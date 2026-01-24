import os
import time
import sys
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from utils.mrz_pipeline import extract_mrz_fields


SAMPLES = [
    "/mnt/data/여권1.jpg",
    "/mnt/data/3.여권 - 복사본.pdf",
    "/mnt/data/4. 학생여권 - 복사.pdf",
    "/mnt/data/여권2.pdf",
    "/mnt/data/돌아감.pdf",
    "/mnt/data/4. 학생여권.pdf",
    "/mnt/data/03_여권사본.pdf",
    "/mnt/data/03_여권사본 (PASSPORT).pdf",
    "/mnt/data/3.여권 사본 - 복사본.pdf",
]


def _load_image(path: str):
    ext = os.path.splitext(path.lower())[1]
    if ext == ".pdf":
        doc = fitz.open(path)
        if doc.page_count < 1:
            return None
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return Image.open(path).convert("RGB")


def main():
    print("MRZ pipeline sample run")
    for path in SAMPLES:
        print(f"\n=== {path} ===")
        if not os.path.exists(path):
            print("MISSING: file not found")
            continue
        img = _load_image(path)
        start = time.perf_counter()
        result = extract_mrz_fields(img, time_budget_sec=3.5)
        elapsed = time.perf_counter() - start
        print(f"ok={result.get('ok')} time={elapsed:.3f}s")
        print("fields:", result.get("fields", {}))
        print("debug.timing:", result.get("debug", {}).get("timing", {}))


if __name__ == "__main__":
    main()
