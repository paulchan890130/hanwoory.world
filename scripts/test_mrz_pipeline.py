import os
from pathlib import Path

import fitz
from PIL import Image

from utils.mrz_pipeline import extract_mrz_fields


SAMPLES = [
    "/mnt/data/여권1.jpg",
    "/mnt/data/3.여권 - 복사본.pdf",
    "/mnt/data/4. 학생여권 - 복사.pdf",
    "/mnt/data/여권2.pdf",
    "/mnt/data/돌아감.pdf",
    "/mnt/data/4. 학생여권.pdf",
]


def _load_image(path: str) -> Image.Image:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        doc = fitz.open(path)
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200)
        mode = "RGB" if pix.alpha == 0 else "RGBA"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        if mode == "RGBA":
            img = img.convert("RGB")
        return img
    return Image.open(path).convert("RGB")


def main() -> None:
    for sample in SAMPLES:
        if not os.path.exists(sample):
            print(f"MISSING: {sample}")
            continue
        img = _load_image(sample)
        result = extract_mrz_fields(img, time_budget_sec=3.5)
        fields = result.get("fields", {})
        debug = result.get("debug", {})
        timing = debug.get("timing", {})
        print("=")
        print(f"sample: {sample}")
        print(f"ok={result.get('ok')} rotation={debug.get('rotation')} t_total={timing.get('t_total')}")
        print(
            "passport_no={passport_no} dob={dob_raw} expiry={expiry_raw}".format(
                passport_no=fields.get("passport_no"),
                dob_raw=fields.get("dob_raw"),
                expiry_raw=fields.get("expiry_raw"),
            )
        )


if __name__ == "__main__":
    main()
