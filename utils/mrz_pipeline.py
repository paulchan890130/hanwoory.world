import re
import time
from dataclasses import dataclass
from typing import Any

try:
    import cv2
except Exception:
    cv2 = None

import numpy as np
import pytesseract
from PIL import Image as PILImage


_MRZ_WHITELIST_CFG = (
    "--oem 1 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
)


def _now() -> float:
    return time.perf_counter()


def _as_bgr(img: Any) -> np.ndarray:
    if cv2 is None:
        return None
    if img is None:
        return None
    if isinstance(img, np.ndarray):
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 3:
            return img.copy()
        if img.shape[2] == 4:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if isinstance(img, PILImage.Image):
        arr = np.array(img)
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return None


def _resize_preview(img_bgr: np.ndarray, target_long: int = 1000):
    if cv2 is None:
        return img_bgr, 1.0
    h, w = img_bgr.shape[:2]
    long_side = max(h, w)
    if long_side <= target_long:
        return img_bgr, 1.0
    scale = target_long / float(long_side)
    resized = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return resized, scale


def _threshold_blackhat(gray: np.ndarray):
    if cv2 is None:
        return gray
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5)))
    blur = cv2.GaussianBlur(blackhat, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def _find_candidates(preview_bgr: np.ndarray) -> list[dict]:
    if cv2 is None:
        return []
    gray = cv2.cvtColor(preview_bgr, cv2.COLOR_BGR2GRAY)
    thresh = _threshold_blackhat(gray)
    ph, pw = gray.shape[:2]
    preview_area = float(ph * pw)
    kernels = [(35, 3), (55, 5)]
    candidates: list[dict] = []

    for kx, ky in kernels:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if h <= 0 or w <= 0:
                continue
            aspect = w / float(h)
            if aspect < 6.0:
                continue
            if h < ph * 0.025 or h > ph * 0.25:
                continue
            area = w * h
            if area < preview_area * 0.003:
                continue
            roi = thresh[y : y + h, x : x + w]
            ink = float(np.count_nonzero(roi == 0)) / float(area)
            if ink < 0.12:
                continue
            area_ratio = area / preview_area
            center_y = (y + h / 2.0) / ph
            position_penalty = abs(center_y - 0.5) * 0.2
            score = (aspect * 0.12) + (ink * 2.0) + (area_ratio * 6.0) - position_penalty
            candidates.append(
                {
                    "bbox": (x, y, w, h),
                    "score": float(score),
                    "aspect": float(aspect),
                    "ink": float(ink),
                    "area_ratio": float(area_ratio),
                }
            )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:3]


def _rotate_image(img_bgr: np.ndarray, deg: int) -> np.ndarray:
    if cv2 is None:
        return img_bgr
    if deg == 0:
        return img_bgr
    if deg == 90:
        return cv2.rotate(img_bgr, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(img_bgr, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(img_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"Unsupported rotation {deg}")


def _expand_bbox(bbox, img_shape, margin: float = 0.08):
    x, y, w, h = bbox
    ih, iw = img_shape[:2]
    mx = int(w * margin)
    my = int(h * margin)
    x0 = max(0, x - mx)
    y0 = max(0, y - my)
    x1 = min(iw, x + w + mx)
    y1 = min(ih, y + h + my)
    return x0, y0, x1, y1


def _deskew_if_needed(img_bgr: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return img_bgr
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    thresh = _threshold_blackhat(gray)
    coords = cv2.findNonZero(thresh)
    if coords is None:
        return img_bgr
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 1.2:
        return img_bgr
    h, w = img_bgr.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(img_bgr, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _normalize_ocr_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = line.strip().replace(" ", "").upper()
        line = re.sub(r"[^A-Z0-9<]", "", line)
        if line:
            lines.append(line)
    return lines


def _char_value(ch: str) -> int:
    if "0" <= ch <= "9":
        return ord(ch) - ord("0")
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    return 0


def _check_digit(data: str) -> int:
    weights = [7, 3, 1]
    total = 0
    for i, ch in enumerate(data):
        total += _char_value(ch) * weights[i % 3]
    return total % 10


@dataclass
class _MrzParseResult:
    ok: bool
    score: float
    lines: list[str]
    fields: dict[str, str]


def _parse_td3(lines: list[str]) -> _MrzParseResult:
    best = _MrzParseResult(False, 0.0, [], {})
    if len(lines) < 2:
        return best

    for i in range(len(lines) - 1):
        l1 = lines[i][:44].ljust(44, "<")
        l2 = lines[i + 1][:44].ljust(44, "<")
        if not l1.startswith("P<"):
            continue

        passport_no = l2[0:9]
        passport_cd = l2[9]
        nationality = l2[10:13]
        dob = l2[13:19]
        dob_cd = l2[19]
        sex = l2[20]
        expiry = l2[21:27]
        expiry_cd = l2[27]
        personal = l2[28:42]
        personal_cd = l2[42]
        composite_cd = l2[43]

        checks = {
            "passport": _check_digit(passport_no) == _char_value(passport_cd),
            "dob": _check_digit(dob) == _char_value(dob_cd),
            "expiry": _check_digit(expiry) == _char_value(expiry_cd),
        }
        composite_data = passport_no + passport_cd + dob + dob_cd + expiry + expiry_cd + personal + personal_cd
        checks["composite"] = _check_digit(composite_data) == _char_value(composite_cd)
        score = sum(1 for ok in checks.values() if ok)
        if score < 2:
            continue

        name_field = l1[5:]
        if "<<" in name_field:
            surname, given = name_field.split("<<", 1)
        else:
            surname, given = name_field, ""
        surname = surname.replace("<", " ").strip()
        given = given.replace("<", " ").strip()

        fields = {
            "passport_no": passport_no.replace("<", "").strip(),
            "nationality": nationality.replace("<", "").strip(),
            "dob": dob,
            "sex": "" if sex == "<" else sex,
            "expiry": expiry,
            "surname": surname,
            "given_names": given,
        }
        best = _MrzParseResult(True, float(score), [l1, l2], fields)
        if score >= 3:
            return best
    return best


def _format_date_yyMMdd(raw: str) -> str:
    if not raw or len(raw) != 6 or not raw.isdigit():
        return ""
    yy = int(raw[:2])
    mm = raw[2:4]
    dd = raw[4:6]
    year = 2000 + yy if yy < 80 else 1900 + yy
    return f"{year:04d}-{mm}-{dd}"


def _ocr_roi(img_bgr: np.ndarray, lang: str) -> str:
    if cv2 is None:
        return ""
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    return pytesseract.image_to_string(rgb, lang=lang, config=_MRZ_WHITELIST_CFG) or ""


def extract_mrz_fields(image_bgr_or_rgb, *, time_budget_sec: float = 3.5) -> dict:
    start = _now()
    debug: dict[str, Any] = {"timing": {}, "rotation": 0, "candidates": [], "method": "B-hybrid"}
    out = {"ok": False, "mrz_lines": [], "fields": {}, "score": 0.0, "debug": debug}

    if cv2 is None:
        debug["reason"] = "opencv-not-available"
        debug["timing"]["t_total"] = _now() - start
        return out

    img_bgr = _as_bgr(image_bgr_or_rgb)
    if img_bgr is None:
        return out

    t0 = _now()
    preview, scale = _resize_preview(img_bgr)
    debug["timing"]["t_preview_resize"] = _now() - t0

    rotation_used = 0
    candidate_sets: list[dict] = []
    t_find = _now()
    candidates = _find_candidates(preview)
    if not candidates:
        for deg in (90, 180, 270):
            if _now() - start > time_budget_sec:
                break
            rotated_preview = _rotate_image(preview, deg)
            candidates = _find_candidates(rotated_preview)
            if candidates:
                rotation_used = deg
                preview = rotated_preview
                break
    debug["timing"]["t_find_candidates_total"] = _now() - t_find
    debug["rotation"] = rotation_used

    if candidates:
        candidate_sets = candidates
        for cand in candidates:
            debug["candidates"].append(cand.copy())
    else:
        debug["timing"]["t_total"] = _now() - start
        return out

    work_img = _rotate_image(img_bgr, rotation_used)
    ph, pw = preview.shape[:2]
    wh, ww = work_img.shape[:2]
    scale_x = ww / float(pw)
    scale_y = wh / float(ph)

    ocr_attempts = 0
    t_ocr = 0.0
    t_validate = 0.0

    for idx, cand in enumerate(candidate_sets):
        if _now() - start > time_budget_sec:
            break
        x, y, w, h = cand["bbox"]
        x0 = int(x * scale_x)
        y0 = int(y * scale_y)
        w0 = int(w * scale_x)
        h0 = int(h * scale_y)
        x1, y1, x2, y2 = _expand_bbox((x0, y0, w0, h0), work_img.shape)
        roi = work_img[y1:y2, x1:x2]
        if roi.size == 0:
            continue

        for lang in ("ocrb", "eng"):
            if _now() - start > time_budget_sec:
                break
            ocr_attempts += 1
            t_ocr_start = _now()
            text = _ocr_roi(roi, lang)
            t_ocr += _now() - t_ocr_start

            lines = _normalize_ocr_lines(text)
            t_val_start = _now()
            parsed = _parse_td3(lines)
            t_validate += _now() - t_val_start

            if parsed.ok:
                fields = parsed.fields.copy()
                fields["dob_formatted"] = _format_date_yyMMdd(fields.get("dob", ""))
                fields["expiry_formatted"] = _format_date_yyMMdd(fields.get("expiry", ""))
                out.update(
                    ok=True,
                    mrz_lines=parsed.lines,
                    fields=fields,
                    score=parsed.score,
                )
                debug["timing"]["t_ocr_total"] = t_ocr
                debug["timing"]["t_validate_parse"] = t_validate
                debug["timing"]["t_total"] = _now() - start
                return out

            if lang == "ocrb" and idx >= 1:
                roi = _deskew_if_needed(roi)

            if ocr_attempts >= 4:
                break
        if ocr_attempts >= 4:
            break

    debug["timing"]["t_ocr_total"] = t_ocr
    debug["timing"]["t_validate_parse"] = t_validate
    debug["timing"]["t_total"] = _now() - start
    return out
