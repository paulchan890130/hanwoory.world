"""MRZ extraction pipeline using ROI-only OCR (OCR-B + ENG).

This module exposes extract_mrz_fields for TD3 passports. It avoids full-image OCR
and limits OCR attempts to ROI candidates within a time budget.
"""
from __future__ import annotations

import time
from typing import Dict, List, Tuple

import cv2
import numpy as np
import pytesseract
from PIL import Image


_OCR_CONFIG = "--oem 1 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"


def _to_gray(pil_rgb: Image.Image) -> np.ndarray:
    arr = np.array(pil_rgb)
    if arr.ndim == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    return arr


def _rotate(gray: np.ndarray, degrees: int) -> np.ndarray:
    if degrees == 0:
        return gray
    if degrees == 90:
        return cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(gray, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return gray


def _candidate_bands(gray: np.ndarray) -> List[Tuple[int, int, int, int, str]]:
    h, w = gray.shape[:2]
    bands = []
    # Bottom-heavy heuristic bands (MRZ usually near bottom)
    for ratio in (0.35, 0.45):
        y0 = int(h * (1 - ratio))
        bands.append((0, y0, w, h - y0, f"bottom_{int(ratio * 100)}"))

    # Edge-density band detection (hybrid)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    grad_x = cv2.Sobel(blur, cv2.CV_16S, 1, 0, ksize=3)
    grad_y = cv2.Sobel(blur, cv2.CV_16S, 0, 1, ksize=3)
    abs_grad = cv2.convertScaleAbs(cv2.addWeighted(grad_x, 1.0, grad_y, 0.5, 0))
    _, thr = cv2.threshold(abs_grad, 0, 255, cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 5))
    morph = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        best = max(contours, key=cv2.contourArea)
        x, y, bw, bh = cv2.boundingRect(best)
        # Expand a bit to include full MRZ lines
        pad_y = int(bh * 0.3)
        y0 = max(y - pad_y, 0)
        y1 = min(y + bh + pad_y, h)
        bands.append((0, y0, w, y1 - y0, "edge_density"))

    return bands


def _prep_roi(gray: np.ndarray) -> np.ndarray:
    # Normalize contrast and binarize
    norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    thr = cv2.adaptiveThreshold(
        norm,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        31,
        10,
    )
    return thr


def _ocr_roi(roi: np.ndarray) -> str:
    return pytesseract.image_to_string(roi, lang="ocrb+eng", config=_OCR_CONFIG)


def _clean_mrz_lines(raw: str) -> List[str]:
    lines = [line.strip().replace(" ", "") for line in raw.splitlines()]
    lines = [line for line in lines if len(line) >= 20]
    lines = ["".join(ch for ch in line if ch.isalnum() or ch == "<") for line in lines]
    return lines


def _pick_mrz_pair(lines: List[str]) -> Tuple[str, str]:
    best = ("", "")
    best_score = -1
    for i in range(len(lines) - 1):
        l1, l2 = lines[i], lines[i + 1]
        if len(l1) >= 30 and len(l2) >= 30:
            score = l1.count("<") + l2.count("<")
            if score > best_score:
                best = (l1[:44], l2[:44])
                best_score = score
    return best


def _parse_td3(l1: str, l2: str) -> Dict[str, str]:
    def clean_name(block: str) -> str:
        return " ".join([p for p in block.replace("<", " ").split() if p])

    surname = ""
    given_names = ""
    if l1.startswith("P<") and "<<" in l1:
        name_part = l1[5:44]
        parts = name_part.split("<<")
        surname = clean_name(parts[0])
        given_names = clean_name(parts[1]) if len(parts) > 1 else ""

    passport_no = l2[0:9].replace("<", "").strip()
    nationality = l2[10:13].replace("<", "").strip()
    dob = l2[13:19]
    sex = l2[20:21].replace("<", "").strip()
    expiry = l2[21:27]

    return {
        "passport_no": passport_no,
        "nationality": nationality,
        "dob_raw": dob,
        "expiry_raw": expiry,
        "sex": sex,
        "surname": surname,
        "given_names": given_names,
    }


def _format_date_yyMMdd(raw: str, kind: str) -> str:
    if len(raw) != 6 or not raw.isdigit():
        return ""
    yy = int(raw[0:2])
    mm = raw[2:4]
    dd = raw[4:6]
    now = time.gmtime().tm_year % 100
    if kind == "expiry":
        century = 2000 if yy <= now + 10 else 1900
    else:
        century = 1900 if yy > now else 2000
    return f"{century + yy:04d}-{mm}-{dd}"


def extract_mrz_fields(pil_rgb: Image.Image, time_budget_sec: float = 3.5) -> Dict[str, object]:
    """Extract TD3 MRZ fields from a PIL RGB image.

    Returns a dict with required fields plus debug metadata.
    """
    start = time.monotonic()
    debug = {
        "method": "B-hybrid",
        "rotation": None,
        "reason": "",
        "candidates": [],
        "timing": {},
    }

    if pil_rgb is None:
        debug["reason"] = "no-image"
        return {
            "passport_no": "",
            "nationality": "",
            "dob_formatted": "",
            "expiry_formatted": "",
            "sex": "",
            "surname": "",
            "given_names": "",
            "debug": debug,
        }

    gray = _to_gray(pil_rgb)
    ocr_calls = 0
    best_fields: Dict[str, str] = {}

    for rotation in (0, 90, 270, 180):
        if time.monotonic() - start > time_budget_sec:
            debug["reason"] = "time-budget"
            break

        rot_gray = _rotate(gray, rotation)
        candidates = _candidate_bands(rot_gray)
        debug["candidates"].extend(
            {"rotation": rotation, "tag": tag, "bbox": [x, y, w, h]}
            for x, y, w, h, tag in candidates
        )

        for x, y, w, h, tag in candidates:
            if ocr_calls >= 4:
                debug["reason"] = "ocr-limit"
                break
            if time.monotonic() - start > time_budget_sec:
                debug["reason"] = "time-budget"
                break

            roi = rot_gray[y : y + h, x : x + w]
            prep = _prep_roi(roi)
            raw = _ocr_roi(prep)
            ocr_calls += 1

            lines = _clean_mrz_lines(raw)
            l1, l2 = _pick_mrz_pair(lines)
            if l1 and l2:
                fields = _parse_td3(l1, l2)
                fields["dob_formatted"] = _format_date_yyMMdd(fields.pop("dob_raw"), "dob")
                fields["expiry_formatted"] = _format_date_yyMMdd(fields.pop("expiry_raw"), "expiry")
                debug["rotation"] = rotation
                debug["reason"] = f"match:{tag}"
                debug["timing"]["elapsed"] = round(time.monotonic() - start, 4)
                return {**fields, "debug": debug}

            best_fields = best_fields or {}

        if debug.get("reason") == "ocr-limit":
            break

    debug["timing"]["elapsed"] = round(time.monotonic() - start, 4)
    if not debug["reason"]:
        debug["reason"] = "no-match"

    return {
        "passport_no": "",
        "nationality": "",
        "dob_formatted": "",
        "expiry_formatted": "",
        "sex": "",
        "surname": "",
        "given_names": "",
        "debug": debug,
    }
