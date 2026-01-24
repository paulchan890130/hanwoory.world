# utils/mrz_pipeline.py
"""MRZ extraction pipeline (ROI-only, B-hybrid, NO checkdigit validation).

- Fast global candidate search via morphology/contours on preview.
- OCR only on candidate ROIs (<= 4 calls).
- Rotation is tried only when no candidates at 0 deg (early-exit).
- NO checkdigit validation (user-requested).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple, Union

import cv2
import numpy as np
import pytesseract
from PIL import Image

_OCR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_OCR_CONFIG = f"--oem 1 --psm 6 -c tessedit_char_whitelist={_OCR_WHITELIST}"


def _to_gray(img: Union[Image.Image, np.ndarray]) -> np.ndarray:
    if isinstance(img, Image.Image):
        arr = np.array(img)  # RGB
    else:
        arr = img
    if arr.ndim == 3:
        # If ndarray is BGR (OpenCV), this still works reasonably for gray conversion
        return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.shape[2] == 3 else arr[:, :, 0]
    return arr


def _resize_preview(gray: np.ndarray, long_side: int = 1100, max_side: int = 1200) -> Tuple[np.ndarray, float]:
    h, w = gray.shape[:2]
    ms = max(h, w)
    if ms <= max_side:
        return gray, 1.0
    scale = min(max_side / float(ms), long_side / float(ms))
    resized = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return resized, scale


def _rotate(gray: np.ndarray, deg: int) -> np.ndarray:
    if deg == 0:
        return gray
    if deg == 90:
        return cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(gray, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return gray


def _mrz_enlarge_bbox(x: int, y: int, w: int, h: int, H: int, W: int) -> Tuple[int, int, int, int]:
    """
    핵심: morphology가 MRZ '한 줄'만 잡는 경우가 많음.
    -> OCR ROI는 2줄 밴드가 되도록 세로로 크게 확장.
    """
    # If very thin (single-line), expand aggressively
    if h < int(H * 0.06):
        pad_y = int(max(h * 1.5, H * 0.04))
        new_h = int(h * 3.2)
    else:
        pad_y = int(max(h * 0.6, H * 0.03))
        new_h = int(h * 2.2)

    y0 = max(0, y - pad_y)
    y1 = min(H, y0 + new_h)

    # Add small x padding
    pad_x = int(max(w * 0.03, W * 0.01))
    x0 = max(0, x - pad_x)
    x1 = min(W, x + w + pad_x)

    return x0, y0, x1 - x0, y1 - y0


def _find_candidates(gray: np.ndarray) -> List[Dict[str, Any]]:
    """
    Fast global MRZ band candidate search on preview.
    Returns up to 3 candidates with bbox + score.
    """
    H, W = gray.shape[:2]
    area_total = float(H * W)

    # blackhat to 강조: 어두운 문자/획
    blackhat = cv2.morphologyEx(
        gray,
        cv2.MORPH_BLACKHAT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)),
        iterations=1,
    )
    blur = cv2.GaussianBlur(blackhat, (3, 3), 0)
    _, thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # only 2 kernels (멀티스케일 과도 금지)
    k1 = cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, int(W * 0.03)), 3))
    k2 = cv2.getStructuringElement(cv2.MORPH_RECT, (max(55, int(W * 0.05)), 5))
    closed = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, k1, iterations=1)
    closed = cv2.morphologyEx(closed, cv2.MORPH_CLOSE, k2, iterations=1)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    cands: List[Dict[str, Any]] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        aspect = w / float(h + 1e-6)
        area = float(w * h)

        if aspect < 6:
            continue
        if h < H * 0.02 or h > H * 0.25:
            continue
        if area < area_total * 0.003:
            continue

        roi = thr[y : y + h, x : x + w]
        ink = float(np.count_nonzero(roi == 0)) / float(area + 1e-6)

        # 약한 위치 패널티만
        y_center = (y + h * 0.5) / float(H)
        pos_penalty = 0.0
        if y_center < 0.12:
            pos_penalty = 0.5
        elif y_center > 0.95:
            pos_penalty = 0.3

        score = (aspect * 1.0) + (ink * 3.0) + (area / area_total * 2.0) - pos_penalty

        x2, y2, w2, h2 = _mrz_enlarge_bbox(x, y, w, h, H, W)
        cands.append(
            {
                "bbox": [int(x2), int(y2), int(w2), int(h2)],
                "score": float(score),
                "aspect": float(aspect),
                "ink": float(ink),
            }
        )

    cands.sort(key=lambda d: d["score"], reverse=True)
    return cands[:3]


def _prep_for_ocr(gray_roi: np.ndarray) -> np.ndarray:
    # upscale to help OCR on thin bands
    h, w = gray_roi.shape[:2]
    if w < 900:
        scale = 2.0
        gray_roi = cv2.resize(gray_roi, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    gray_roi = cv2.normalize(gray_roi, None, 0, 255, cv2.NORM_MINMAX)
    gray_roi = cv2.GaussianBlur(gray_roi, (3, 3), 0)
    _, bw = cv2.threshold(gray_roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # ensure black text on white background
    # if mostly black background -> invert
    if np.mean(bw) < 127:
        bw = 255 - bw
    return bw


def _clean_lines(raw: str) -> List[str]:
    out: List[str] = []
    for line in (raw or "").splitlines():
        s = "".join(ch for ch in line.strip().upper() if ch.isalnum() or ch == "<")
        if len(s) >= 25:
            out.append(s)
    return out


def _pick_best_td3_pair(lines: List[str]) -> Tuple[str, str]:
    """
    No checkdigit validation.
    Choose best adjacent pair by:
    - length closeness to 44
    - count of '<'
    - L1 startswith 'P<'
    """
    best = ("", "")
    best_score = -1.0

    def score_pair(l1: str, l2: str) -> float:
        l1s, l2s = l1[:44], l2[:44]
        sc = 0.0
        sc += (l1s.count("<") + l2s.count("<")) * 1.0
        sc += 6.0 if l1s.startswith("P<") else 0.0
        sc += 2.0 if abs(len(l1) - 44) <= 2 else 0.0
        sc += 2.0 if abs(len(l2) - 44) <= 2 else 0.0
        return sc

    for i in range(len(lines) - 1):
        l1, l2 = lines[i], lines[i + 1]
        if len(l1) < 30 or len(l2) < 30:
            continue
        sc = score_pair(l1, l2)
        if sc > best_score:
            best_score = sc
            best = (l1, l2)

    return best


def _pad44(s: str) -> str:
    s = (s or "")[:44]
    if len(s) < 44:
        s = s.ljust(44, "<")
    return s


def _parse_td3(l1: str, l2: str) -> Dict[str, str]:
    l1 = _pad44(l1)
    l2 = _pad44(l2)
    name_block = l1[5:44]
    parts = name_block.split("<<")
    surname = parts[0].replace("<", " ").strip()
    given = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""
    passport_no = l2[0:9].replace("<", "").strip()
    nationality = l2[10:13].replace("<", "").strip()
    dob_raw = l2[13:19]
    sex = l2[20]
    expiry_raw = l2[21:27]
    return {
        "passport_no": passport_no,
        "nationality": nationality,
        "dob_raw": dob_raw,
        "sex": sex,
        "expiry_raw": expiry_raw,
        "surname": surname,
        "given_names": given,
    }


def extract_mrz_fields(image_bgr_or_rgb: Union[Image.Image, np.ndarray], *, time_budget_sec: float = 3.5) -> Dict[str, Any]:
    t0 = time.perf_counter()
    debug: Dict[str, Any] = {
        "method": "B-hybrid-no-validate",
        "rotation": None,
        "candidates": [],
        "timing": {
            "t_preview_resize": 0.0,
            "t_find_candidates_total": 0.0,
            "t_rotation": 0.0,
            "t_ocr_total": 0.0,
            "t_validate_parse": 0.0,  # kept for compatibility (0)
            "t_total": 0.0,
        },
    }

    gray0 = _to_gray(image_bgr_or_rgb)

    tA = time.perf_counter()
    preview, _ = _resize_preview(gray0)
    debug["timing"]["t_preview_resize"] = round(time.perf_counter() - tA, 4)

    ocr_calls = 0

    def time_left() -> float:
        return time_budget_sec - (time.perf_counter() - t0)

    # rotation: only if 0deg has no candidates
    rotations = [0]
    found_at_0 = False

    tB = time.perf_counter()
    c0 = _find_candidates(preview)
    debug["timing"]["t_find_candidates_total"] += round(time.perf_counter() - tB, 4)
    debug["candidates"].extend(c0)
    if c0:
        found_at_0 = True
    else:
        rotations = [0, 90, 180, 270]

    for rot_deg in rotations:
        if time_left() <= 0:
            break

        if rot_deg != 0:
            tR = time.perf_counter()
            rot = _rotate(preview, rot_deg)
            debug["timing"]["t_rotation"] += round(time.perf_counter() - tR, 4)
            tB = time.perf_counter()
            cands = _find_candidates(rot)
            debug["timing"]["t_find_candidates_total"] += round(time.perf_counter() - tB, 4)
        else:
            rot = preview
            cands = c0

        if not cands:
            continue

        debug["rotation"] = rot_deg

        for c in cands:
            if ocr_calls >= 4 or time_left() <= 0:
                break

            x, y, w, h = c["bbox"]
            roi = rot[y : y + h, x : x + w]

            tO = time.perf_counter()
            bw = _prep_for_ocr(roi)
            raw = ""
            try:
                raw = pytesseract.image_to_string(bw, lang="ocrb", config=_OCR_CONFIG)
            except Exception:
                raw = ""
            debug["timing"]["t_ocr_total"] += round(time.perf_counter() - tO, 4)
            ocr_calls += 1

            lines = _clean_lines(raw)
            l1, l2 = _pick_best_td3_pair(lines)
            if l1 and l2:
                fields = _parse_td3(l1, l2)
                debug["timing"]["t_total"] = round(time.perf_counter() - t0, 4)
                return {
                    "ok": True,
                    "mrz_lines": [_pad44(l1), _pad44(l2)],
                    "fields": fields,
                    "score": float(c.get("score", 0.0)),
                    "debug": debug,
                }

            if ocr_calls >= 4 or time_left() <= 0:
                break

            # fallback 1회: eng (same ROI)
            tO = time.perf_counter()
            try:
                raw = pytesseract.image_to_string(bw, lang="eng", config=_OCR_CONFIG)
            except Exception:
                raw = ""
            debug["timing"]["t_ocr_total"] += round(time.perf_counter() - tO, 4)
            ocr_calls += 1

            lines = _clean_lines(raw)
            l1, l2 = _pick_best_td3_pair(lines)
            if l1 and l2:
                fields = _parse_td3(l1, l2)
                debug["timing"]["t_total"] = round(time.perf_counter() - t0, 4)
                return {
                    "ok": True,
                    "mrz_lines": [_pad44(l1), _pad44(l2)],
                    "fields": fields,
                    "score": float(c.get("score", 0.0)),
                    "debug": debug,
                }

        # early-exit: if 0deg had candidates, don't rotate further
        if found_at_0:
            break

    debug["timing"]["t_total"] = round(time.perf_counter() - t0, 4)
    return {"ok": False, "mrz_lines": [], "fields": {}, "score": 0.0, "debug": debug}
