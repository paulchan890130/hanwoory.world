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
    - name             : 도장에 넣을 이름 (1~4글자)
    - font_path_path   : TTF 폰트 경로
    - seal_size_px     : 최종 출력 이미지 한 변 길이
    """

    # ── 0) 이름 정리 ───────────────────────────────────────────────
    if not name:
        name = ""
    name = str(name).strip()

    # 한글만 추출 (안전용)
    hangul_only = "".join(ch for ch in name if "가" <= ch <= "힣")
    if hangul_only:
        name = hangul_only

    if not name:
        name = ""

    # 최대 4글자 제한
    if len(name) > 4:
        name = name[:4]

    n_chars = len(name)

    # ── 1) 기본 캔버스 생성 ───────────────────────────────────────
    canvas_size = seal_size_px
    base = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    # ── 2) 원형 배경 로드 + 5% 확대 후 중앙 배치 ─────────────────
    try:
        circle_img = Image.open(circle_path_path).convert("RGBA")
    except Exception:
        # 파일 없으면 간단한 빨간 원 직접 그림
        circle_img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        d = ImageDraw.Draw(circle_img)
        margin = int(canvas_size * 0.08)
        d.ellipse(
            (margin, margin, canvas_size - margin, canvas_size - margin),
            outline=(180, 0, 0, 255),
            width=int(canvas_size * 0.05),
        )

    scale = 1.05  # 원 크기 5% 확대
    circle_size = int(canvas_size * scale)
    circle_img = circle_img.resize((circle_size, circle_size), Image.LANCZOS)

    offset_x = (canvas_size - circle_size) // 2
    offset_y = (canvas_size - circle_size) // 2
    base.alpha_composite(circle_img, dest=(offset_x, offset_y))

    # ── 3) 테두리 색상 샘플링 (폰트색 = 테두리색) ─────────────────
    border_color = (180, 0, 0, 255)  # 실패 시 기본값
    cx = canvas_size // 2
    for y in range(offset_y, offset_y + canvas_size // 2):
        r, g, b, a = base.getpixel((cx, y))
        if a > 0:
            border_color = (r, g, b, a)
            break

    draw = ImageDraw.Draw(base)

    # ── 4) 글자 폰트 크기 및 배치 계산 ────────────────────────────
    if n_chars > 0:
        # 글자 수별 세로 사용 비율 / 줄 간격 비율
        if n_chars == 1:
            cover_ratio = 0.70
            line_gap_ratio = 0.0
        elif n_chars == 2:
            cover_ratio = 0.80
            line_gap_ratio = 0.25
        elif n_chars == 3:
            # ⬅ 3글자만 높이 살짝 줄이고, 간격을 더 좁게
            cover_ratio = 0.90   # 0.95 → 0.90 (이제 원 밖으로 거의 안 나감)
            line_gap_ratio = 0.12  # 0.18 → 0.12 (글자 사이 간격 좁게)
        else:  # 4글자
            cover_ratio = 0.98
            line_gap_ratio = 0.15

        max_inner_height = canvas_size * cover_ratio

        denom = (n_chars + (n_chars - 1) * line_gap_ratio)
        if denom <= 0:
            denom = 1.0
        font_size = int(max_inner_height / denom)
        if font_size < 10:
            font_size = 10

        try:
            font = ImageFont.truetype(font_path_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        # 각 글자 크기 측정
        char_sizes = []
        for ch in name:
            bbox = draw.textbbox((0, 0), ch, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            char_sizes.append((w, h))

        # 실제 총 높이 + 줄 간격
        total_h = 0
        for _, h in char_sizes:
            total_h += h
        if n_chars > 1:
            line_gap = int(font_size * line_gap_ratio)
            total_h += line_gap * (n_chars - 1)
        else:
            line_gap = 0

        current_y = (canvas_size - total_h) / 2

        # 세로 가운데 정렬로 글자 그리기
        for idx, ch in enumerate(name):
            w, h = char_sizes[idx]
            x = (canvas_size - w) / 2
            y = current_y
            draw.text((x, y), ch, fill=border_color, font=font)
            current_y += h + line_gap

    # ── 5) 투명화(그라디언트) 없이 바로 회전만 적용 ───────────────
    rotated = base.rotate(5, resample=Image.BICUBIC, expand=True)

    # 회전 후 캔버스 중앙에 다시 맞추기
    final_img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    rw, rh = rotated.size
    fx = (canvas_size - rw) // 2
    fy = (canvas_size - rh) // 2
    final_img.alpha_composite(rotated, dest=(fx, fy))

    return final_img
