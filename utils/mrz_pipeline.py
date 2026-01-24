"""MRZ extraction pipeline (ROI-only, B-hybrid)."""
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
        arr = np.array(img)
    else:
        arr = img
    if arr.ndim == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    return arr


def _resize_preview(gray: np.ndarray) -> Tuple[np.ndarray, float]:
    h, w = gray.shape[:2]
    max_side = max(h, w)
    target = 1100
    max_target = 1200
    if max_side <= max_target:
        return gray, 1.0
    scale = min(max_target / float(max_side), target / float(max_side))
    resized = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return resized, scale


def _rotate(gray: np.ndarray, rotation: int) -> np.ndarray:
    if rotation == 0:
        return gray
    if rotation == 90:
        return cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
    if rotation == 180:
        return cv2.rotate(gray, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return gray


def _find_candidates(gray: np.ndarray) -> Tuple[List[Tuple[int, int, int, int, float, float]], Dict[str, float]]:
    t0 = time.monotonic()
    h, w = gray.shape[:2]
    blackhat = cv2.morphologyEx(
        gray,
        cv2.MORPH_BLACKHAT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)),
        iterations=1,
    )
    blur = cv2.GaussianBlur(blackhat, (3, 3), 0)
    _, thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    k1_w = max(15, int(w * 0.03))
    k2_w = max(20, int(w * 0.05))
    k1 = cv2.getStructuringElement(cv2.MORPH_RECT, (k1_w, 3))
    k2 = cv2.getStructuringElement(cv2.MORPH_RECT, (k2_w, 5))
    closed = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, k1, iterations=1)
    closed = cv2.morphologyEx(closed, cv2.MORPH_CLOSE, k2, iterations=1)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: List[Tuple[int, int, int, int, float, float]] = []
    area_total = float(h * w)
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        aspect = bw / float(bh + 1e-6)
        area = bw * bh
        if aspect < 6:
            continue
        if bh < h * 0.02 or bh > h * 0.25:
            continue
        if area < area_total * 0.003:
            continue
        roi = thr[y : y + bh, x : x + bw]
        ink = float(np.count_nonzero(roi == 0)) / float(area + 1e-6)
        score = aspect + (ink * 3.0) + (area / area_total)
        candidates.append((x, y, bw, bh, score, ink))

    candidates.sort(key=lambda c: c[4], reverse=True)
    candidates = candidates[:3]
    timing = {"t_find": round(time.monotonic() - t0, 4)}
    return candidates, timing


def _clean_lines(raw: str) -> List[str]:
    lines = []
    for line in raw.splitlines():
        line = "".join(ch for ch in line.strip().upper() if ch.isalnum() or ch == "<")
        if len(line) >= 30:
            lines.append(line)
    return lines


def _score_pair(l1: str, l2: str) -> float:
    if not (40 <= len(l1) <= 50 and 40 <= len(l2) <= 50):
        return -1.0
    score = l1.count("<") + l2.count("<")
    if l1.startswith("P<"):
        score += 5
    return float(score)


def _pick_mrz(lines: List[str]) -> Tuple[str, str]:
    best_score = -1.0
    best = ("", "")
    for i in range(len(lines) - 1):
        l1, l2 = lines[i], lines[i + 1]
        score = _score_pair(l1, l2)
        if score > best_score:
            best_score = score
            best = (l1, l2)
    if best_score < 0:
        return ("", "")
    return best


def _pad_44(line: str) -> str:
    if len(line) >= 44:
        return line[:44]
    return line.ljust(44, "<")


def _parse_td3(l1: str, l2: str) -> Dict[str, str]:
    l1 = _pad_44(l1)
    l2 = _pad_44(l2)
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


def _valid_date(raw: str) -> bool:
    return len(raw) == 6 and raw.isdigit()


def extract_mrz_fields(pil_or_ndarray: Union[Image.Image, np.ndarray], time_budget_sec: float = 3.5) -> Dict[str, Any]:
    """Extract MRZ fields from a PIL image or ndarray.

    Returns: {ok, mrz_lines, fields, debug}
    """
    t0 = time.monotonic()
    debug = {
        "method": "B-hybrid",
        "rotation": None,
        "candidates": [],
        "timing": {"t_total": 0.0, "t_find": 0.0, "t_ocr": 0.0},
    }
    fields: Dict[str, str] = {}

    gray = _to_gray(pil_or_ndarray)
    preview, scale = _resize_preview(gray)

    ocr_calls = 0
    for rotation in (0, 90, 180, 270):
        if time.monotonic() - t0 > time_budget_sec:
            break
        rot = _rotate(preview, rotation)
        candidates, timing = _find_candidates(rot)
        debug["timing"]["t_find"] += timing["t_find"]

        for x, y, w, h, score, ink in candidates:
            debug["candidates"].append(
                {"bbox": [int(x), int(y), int(w), int(h)], "score": score, "aspect": w / float(h), "ink": ink}
            )

        if rotation == 0 and candidates:
            pass
        elif rotation == 0 and not candidates:
            continue

        if not candidates:
            continue

        debug["rotation"] = rotation
        for x, y, w, h, score, ink in candidates:
            if ocr_calls >= 4 or time.monotonic() - t0 > time_budget_sec:
                break

            roi = rot[y : y + h, x : x + w]
            t_ocr = time.monotonic()
            try:
                raw = pytesseract.image_to_string(
                    roi,
                    lang="ocrb",
                    config=_OCR_CONFIG,
                    timeout=1.2,
                )
            except Exception:
                try:
                    raw = pytesseract.image_to_string(
                        roi,
                        lang="ocrb+eng",
                        config=_OCR_CONFIG,
                        timeout=1.2,
                    )
                except Exception:
                    raw = ""
            debug["timing"]["t_ocr"] += round(time.monotonic() - t_ocr, 4)
            ocr_calls += 1

            lines = _clean_lines(raw)
            l1, l2 = _pick_mrz(lines)
            if l1 and l2:
                l1 = _pad_44(l1)
                l2 = _pad_44(l2)
                fields = _parse_td3(l1, l2)
                ok = (
                    len(fields.get("passport_no", "")) >= 7
                    and _valid_date(fields.get("dob_raw", ""))
                    and _valid_date(fields.get("expiry_raw", ""))
                )
                debug["timing"]["t_total"] = round(time.monotonic() - t0, 4)
                return {
                    "ok": ok,
                    "mrz_lines": [l1, l2],
                    "fields": fields,
                    "debug": debug,
                }

            if ocr_calls >= 4 or time.monotonic() - t0 > time_budget_sec:
                break

            t_ocr = time.monotonic()
            try:
                raw = pytesseract.image_to_string(
                    roi,
                    lang="eng",
                    config=_OCR_CONFIG,
                    timeout=1.2,
                )
            except Exception:
                raw = ""
            debug["timing"]["t_ocr"] += round(time.monotonic() - t_ocr, 4)
            ocr_calls += 1

            lines = _clean_lines(raw)
            l1, l2 = _pick_mrz(lines)
            if l1 and l2:
                l1 = _pad_44(l1)
                l2 = _pad_44(l2)
                fields = _parse_td3(l1, l2)
                ok = (
                    len(fields.get("passport_no", "")) >= 7
                    and _valid_date(fields.get("dob_raw", ""))
                    and _valid_date(fields.get("expiry_raw", ""))
                )
                debug["timing"]["t_total"] = round(time.monotonic() - t0, 4)
                return {
                    "ok": ok,
                    "mrz_lines": [l1, l2],
                    "fields": fields,
                    "debug": debug,
                }

        if rotation == 0 and candidates:
            break

    debug["timing"]["t_total"] = round(time.monotonic() - t0, 4)
    return {"ok": False, "mrz_lines": [], "fields": {}, "debug": debug}
