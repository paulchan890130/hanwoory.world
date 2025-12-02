# utils/document.py

from PIL import Image, ImageDraw, ImageFont

# 도장 생성용 기본 설정값
circle_path = "templates/원형 배경.png"
font_path   = "fonts/HJ한전서B.ttf"
seal_size   = 200


def create_seal(circle_path_path: str, name: str, font_path_path: str, seal_size_px: int):
    """
    원형 도장 이미지 위에 한글 이름을 세로로 배치한 이미지를 생성.
    - circle_path_path : 도장 원형 배경 PNG 경로
    - name             : 도장에 넣을 이름 (2글자/3글자 기준 배치)
    - font_path_path   : TTF 폰트 경로
    - seal_size_px     : 최종 도장 이미지 한 변의 크기(px)
    """
    base = Image.open(circle_path_path).convert("RGBA")
    base = base.resize((seal_size_px, seal_size_px), resample=Image.Resampling.LANCZOS)

    # 테두리 색상 추출 (위쪽 부분 픽셀 샘플링)
    sample_y = int(seal_size_px * 0.11)
    border_color = tuple(base.getpixel((seal_size_px // 2, sample_y))[:3])

    draw = ImageDraw.Draw(base)
    font_size = int(seal_size_px * 0.28)
    font = ImageFont.truetype(font_path_path, font_size)

    # 글자 배치: 2글자면 위·아래, 3글자면 3등분
    if len(name) == 2:
        positions = {1: name[0], 3: name[1]}
    else:
        positions = {i: ch for i, ch in enumerate(name, 1)}

    spacing = seal_size_px / 4
    for slot in (1, 2, 3):
        ch = positions.get(slot, "")
        if not ch:
            continue

        bbox = draw.textbbox((0, 0), ch, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = seal_size_px / 2 - w / 2
        y = spacing * slot - h / 2
        draw.text((x, y), ch, fill=border_color, font=font)

    return base
