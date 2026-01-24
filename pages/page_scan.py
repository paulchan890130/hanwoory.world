# pages/page_scan.py

import os
import re
import platform
import datetime
from datetime import datetime as _dt, timedelta as _td

import streamlit as st
from PIL import Image, ImageOps, ImageFilter, ImageStat, Image as _PILImage

try:
    import pytesseract
except Exception:
    pytesseract = None

# ==== Tesseract 실행 파일 경로 (로컬/서버 겸용) ====
if platform.system() == "Windows":
    # 로컬 PC (Windows)에서는 기본 설치 경로 사용
    TESSERACT_ROOT = r"C:\Program Files\Tesseract-OCR"
    TESSERACT_EXE  = os.path.join(TESSERACT_ROOT, "tesseract.exe")
else:
    # Render 같은 리눅스 서버에서는 PATH 에 있는 tesseract 사용
    # (예: apt-get install tesseract-ocr 로 설치된 바이너리)
    TESSERACT_ROOT = ""
    TESSERACT_EXE  = "tesseract"


from config import (
    SESS_CURRENT_PAGE,
    PAGE_CUSTOMER,
)

from core.customer_service import (
    upsert_customer_from_scan,
)

# -----------------------------
# 1) Tesseract 기본 유틸 (간단 버전)
# -----------------------------

def _ensure_tesseract() -> bool:
    """Tesseract 실행파일 & pytesseract 연결 확인 (로컬/서버 겸용).

    - Windows: C:\Program Files\Tesseract-OCR\tesseract.exe 사용
    - Linux/서버(Render 등): PATH 에 있는 `tesseract` 사용
    """
    import streamlit as st
    import platform
    global pytesseract

    # 1) 모듈 체크
    if pytesseract is None:
        st.error("❌ pytesseract 모듈이 없습니다. `pip install pytesseract` 후 다시 실행해주세요.")
        return False

    system = platform.system()

    # 2) OS별 실행 파일 확인
    if system == "Windows":
        if not os.path.exists(TESSERACT_EXE):
            st.error(
                "❌ Tesseract 실행파일을 찾을 수 없습니다.\n"
                f"기대 경로: {TESSERACT_EXE}"
            )
            return False
        cmd = TESSERACT_EXE
    else:
        # 리눅스/맥: PATH 에 있는 tesseract 사용
        cmd = TESSERACT_EXE  # 보통 'tesseract'

    # 3) 연결 + 버전 확인
    try:
        pytesseract.pytesseract.tesseract_cmd = cmd
        ver = pytesseract.get_tesseract_version()
        st.info(f"✅ Tesseract 연결 성공: {ver} (cmd={cmd})")
        return True
    except Exception as e:
        if system == "Windows":
            more = "Tesseract-OCR 설치 및 환경변수를 다시 확인해주세요."
        else:
            more = "Render 서버에 `tesseract-ocr` 패키지가 설치되어 있는지 확인해주세요."
        st.error(f"❌ Tesseract 실행 중 오류: {e}\n{more}")
        return False



def _ocr(img, lang="kor", config=""):
    """
    공통 OCR 래퍼.
    """
    if pytesseract is None or img is None:
        return ""
    try:
        return pytesseract.image_to_string(img, lang=lang, config=config) or ""
    except Exception:
        return ""


def _binarize(img):
    """
    단순 이진화(디버그/보조용).
    """
    g = ImageOps.grayscale(img)
    return g.point(lambda p: 255 if p > 128 else 0)


def _binarize_soft(img):
    """
    MRZ용 '부드러운' 이진화:
    - 그레이스케일
    - 약한 노이즈 제거
    - 자동 대비 조정
    """
    g = ImageOps.grayscale(img)
    g = g.filter(ImageFilter.MedianFilter(size=3))
    g = ImageOps.autocontrast(g)
    return g


def _pre(img):
    """
    MRZ용 기본 전처리:
    - 그레이스케일 + 자동 대비
    """
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    return g


def ocr_try_all(
    img,
    langs=("kor", "kor+eng"),
    psms=(6, 7),
    pres=("raw", "binarize"),
    max_tries: int | None = None,
):
    """
    디버그용 ‘베스트 OCR’ 탐색 (간이 버전).
    - text 길이를 score로 사용.
    - max_tries 가 None 이면: langs×psms×pres 모든 조합 시도 (기존과 동일)
    - max_tries 가 1,2,... 이면: 앞에서부터 최대 그 횟수만 시도
      (langs/psms/pres 값 자체는 그대로 유지하고, '조합 수'만 줄인다)
    """
    best = {"text": "", "lang": None, "config": "", "pre": None, "score": -1}
    if pytesseract is None or img is None:
        return best

    tried = 0
    for lang in langs:
        for psm in psms:
            for pre in pres:
                proc = img
                if pre == "binarize":
                    proc = _binarize(img)
                cfg = f"--oem 3 --psm {psm}"
                try:
                    txt = pytesseract.image_to_string(proc, lang=lang, config=cfg) or ""
                except Exception:
                    txt = ""
                score = len(txt.strip())
                if score > best["score"]:
                    best.update(text=txt, lang=lang, config=cfg, pre=pre, score=score)

                tried += 1
                if max_tries is not None and tried >= max_tries:
                    # 빠른 모드일 때: 앞에서부터 max_tries개 조합만 시도
                    return best

    return best



def open_image_safe(uploaded_file):
    """
    업로드된 파일을 안전하게 이미지(RGB)로 여는 함수.
    - 이미지(jpg/png/webp 등): 그대로 PIL로 로드
    - PDF: 1페이지를 렌더링하여 PIL 이미지로 변환
    """
    if uploaded_file is None:
        return None

    name = getattr(uploaded_file, "name", "") or ""
    ext = os.path.splitext(name.lower())[1]

    # PDF 처리: 1페이지 렌더
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
        except Exception:
            return None

        try:
            pdf_bytes = uploaded_file.getvalue()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.page_count < 1:
                return None
            page = doc[0]
            # 속도 유지: 과도한 고해상도 금지 (zoom 2.0 정도면 MRZ 충분)
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return img
        except Exception:
            return None

    # 일반 이미지
    try:
        return Image.open(uploaded_file).convert("RGB")
    except Exception:
        return None


# -----------------------------
# 2) 스캔용 OCR 유틸 (기존 코드 그대로)
# -----------------------------

# ── 속도/옵션 ─────────────────────────────────────────────
ARC_REMOVE_PAREN = True   # 주소에서 (신길동) 같은 괄호표기 제거
ARC_FAST_ONLY    = True   # 빠른 모드(필요 최소 조합만 시도)

# ── MRZ(여권) 보조 ────────────────────────────────────────
_MRZ_CLEAN_TRANS = str.maketrans({'«':'<','‹':'<','>':'<',' ':'', '—':'-', '–':'-'})
def _normalize_mrz_line(s: str) -> str:
    s = (s or '').strip().translate(_MRZ_CLEAN_TRANS).upper()
    if s.startswith('PO'):
        s = 'P<' + s[2:]
    s = re.sub(r'[^A-Z0-9<]', '', s)
    if len(s) < 44: s += '<'*(44-len(s))
    elif len(s) > 44: s = s[:44]
    return s

def _is_td3_candidate(L1: str, L2: str) -> bool:
    if not (re.fullmatch(r'[A-Z0-9<]{44}', L1) and re.fullmatch(r'[A-Z0-9<]{44}', L2)): return False
    if not L1.startswith('P<'): return False
    if (L1+L2).count('<') < 20: return False
    if not re.fullmatch(r'[A-Z]{3}', L2[10:13]): return False   # 국적
    if L2[20] not in 'MF<': return False                        # 성별
    if not re.fullmatch(r'\d{6}', re.sub(r'[^0-9]', '', L2[13:19])): return False # 생년
    if not re.fullmatch(r'\d{6}', re.sub(r'[^0-9]', '', L2[21:27])): return False # 만기
    return True

def find_mrz_pair_from_text(text: str):
    lines = [l for l in (text or '').splitlines() if l.strip()]
    norms = [(i, _normalize_mrz_line(l)) for i, l in enumerate(lines)]
    best = None
    for (i, L1) in norms:
        if i+1 < len(norms):
            _, L2 = norms[i+1]
            if _is_td3_candidate(L1, L2):
                score = (L1+L2).count('<')
                if not best or score > best[0]:
                    best = (score, L1, L2)
    return (best[1], best[2]) if best else (None, None)

def _minus_years(d: _dt.date, years: int) -> _dt.date:
    y = d.year - years
    import calendar
    endday = calendar.monthrange(y, d.month)[1]
    return _dt(y, d.month, min(d.day, endday)).date()

def _parse_mrz_pair(L1: str, L2: str) -> dict:
    out = {}

    # None 방지 + 정규화
    L1 = _normalize_mrz_line(L1) if L1 else ""
    L2 = _normalize_mrz_line(L2) if L2 else ""

    def _fix_mrz_digits(s: str) -> str:
        """MRZ 숫자 영역에서 흔한 OCR 문자 오인식 보정."""
        if not s:
            return s
        trans = str.maketrans({
            "O": "0",
            "Q": "0",
            "D": "0",
            "I": "1",
            "L": "1",
            "Z": "2",
            "S": "5",
            "B": "8",
            "G": "6",
            "T": "7",
        })
        return s.translate(trans)

    # 🔹 이름: "진짜 여권 1줄(P<...)"처럼 생긴 경우에만 파싱
    #   - P<로 시작
    #   - 뒤에 '<<' 구분자가 존재
    if L1.startswith("P<") and "<<" in L1[5:]:
        sur, given = L1[5:].split("<<", 1)
        out["성"] = sur.replace("<", " ").strip()
        out["명"] = given.replace("<", " ").strip()
    # 그렇지 않으면 이름은 아예 채우지 않음 → 나머지 필드는 그대로 진행

    # 여권, 국적, 생년, 성별, 만기 (기존 로직 그대로)
    pn = re.sub(r"[^A-Z0-9]", "", L2[0:9])
    if pn:
        out["여권"] = pn

    nat = re.sub(r"[^A-Z]", "", L2[10:13])
    if nat:
        out["국가"] = nat

    b = _fix_mrz_digits(re.sub(r"[^0-9A-Z]", "", L2[13:19]))
    b = re.sub(r"[^0-9]", "", b)
    if len(b) == 6:
        yy, mm, dd = int(b[:2]), int(b[2:4]), int(b[4:6])
        yy += 2000 if yy < 80 else 1900
        try:
            out["생년월일"] = _dt(yy, mm, dd).strftime("%Y-%m-%d")
        except Exception:
            pass

    sx = L2[20:21]
    out["성별"] = "남" if sx == "M" else ("여" if sx == "F" else "")

    e = _fix_mrz_digits(re.sub(r"[^0-9A-Z]", "", L2[21:27]))
    e = re.sub(r"[^0-9]", "", e)
    if len(e) == 6:
        yy, mm, dd = int(e[:2]), int(e[2:4]), int(e[4:6])
        yy += 2000 if yy < 80 else 1900
        try:
            out["만기"] = _dt(yy, mm, dd).strftime("%Y-%m-%d")
        except Exception:
            pass

    # 👉 발급일: 10년짜리 기준 역산 (+1일) 그대로 유지
    if out.get("만기"):
        try:
            exp = _dt.strptime(out["만기"], "%Y-%m-%d").date()
            issued = _minus_years(exp, 10) + _td(days=1)
            out["발급"] = issued.strftime("%Y-%m-%d")
        except Exception:
            pass

    return out


# ── MRZ(여권) 고정밀/고속 추출 유틸 ────────────────────────
def _edge_density(pil_img: Image.Image) -> float:
    """빠른 엣지(텍스트) 밀도 스코어. (numpy 없이)"""
    if pil_img is None:
        return 0.0
    g = ImageOps.grayscale(pil_img)
    # 속도 위해 축소
    g = g.copy()
    g.thumbnail((320, 320))
    e = g.filter(ImageFilter.FIND_EDGES)
    # 픽셀 중 임계값 초과 비율
    data = list(e.getdata())
    if not data:
        return 0.0
    thr = 40
    cnt = 0
    for v in data:
        if v > thr:
            cnt += 1
    return cnt / float(len(data))


def _crop_to_content_bbox(img: Image.Image, pad: int = 20) -> Image.Image:
    """
    여백이 큰 스캔본에서 '내용 영역'만 남기기 (속도형).
    실패하면 원본 반환.
    """
    if img is None:
        return img

    w, h = img.size
    # 너무 크면 bbox 탐색용으로만 축소
    work = img.copy()
    scale = 1.0
    if max(w, h) > 900:
        scale = 900.0 / float(max(w, h))
        work = work.resize((int(w * scale), int(h * scale)), resample=_PILImage.BILINEAR)

    g = ImageOps.grayscale(work).filter(ImageFilter.FIND_EDGES)
    # 임계값 초과 좌표 찾기
    px = g.load()
    ww, hh = g.size
    thr = 35
    minx, miny = ww, hh
    maxx, maxy = 0, 0
    found = False

    # 샘플링 간격(속도)
    step = 2 if max(ww, hh) <= 600 else 3
    for y in range(0, hh, step):
        for x in range(0, ww, step):
            if px[x, y] > thr:
                found = True
                if x < minx: minx = x
                if y < miny: miny = y
                if x > maxx: maxx = x
                if y > maxy: maxy = y

    if not found:
        return img

    # 너무 작은 bbox면 의미 없음
    if (maxx - minx) < ww * 0.25 or (maxy - miny) < hh * 0.25:
        return img

    # 원본 좌표로 환산
    inv = 1.0 / scale
    x0 = int(minx * inv) - pad
    y0 = int(miny * inv) - pad
    x1 = int(maxx * inv) + pad
    y1 = int(maxy * inv) + pad

    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(w, x1); y1 = min(h, y1)
    return img.crop((x0, y0, x1, y1))


def _split_regions(img: Image.Image):
    """상/하/좌/우/전체 후보 생성"""
    w, h = img.size
    top = img.crop((0, 0, w, h // 2))
    bottom = img.crop((0, h // 2, w, h))
    left = img.crop((0, 0, w // 2, h))
    right = img.crop((w // 2, 0, w, h))
    return {
        "full": img,
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
    }


def _crop_mrz_band(img: Image.Image, band_ratio: float = 0.42) -> Image.Image:
    """MRZ는 여권 하단에 위치하므로, 후보 영역의 '하단 띠'만 잘라 OCR"""
    w, h = img.size
    y0 = int(h * (1.0 - band_ratio))
    return img.crop((0, y0, w, h))


def _prep_mrz(img: Image.Image, target_w: int = 1200) -> Image.Image:
    g = ImageOps.grayscale(img)
    w, h = g.size
    if w > target_w:
        r = target_w / float(w)
        g = g.resize((int(w * r), int(h * r)), resample=_PILImage.BILINEAR)
    g = ImageOps.autocontrast(g)
    g = g.filter(ImageFilter.MedianFilter(size=3))
    g = g.filter(ImageFilter.SHARPEN)
    return g


def _tess_string(img: Image.Image, lang: str, config: str, timeout_s: int = 2) -> str:
    if pytesseract is None:
        return ""
    try:
        return pytesseract.image_to_string(img, lang=lang, config=config, timeout=timeout_s) or ""
    except TypeError:
        # 구버전 pytesseract timeout 미지원
        try:
            return pytesseract.image_to_string(img, lang=lang, config=config) or ""
        except Exception:
            return ""
    except Exception:
        return ""


def _ocr_mrz(img: Image.Image) -> str:
    cfg_common = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
    # ocrb 우선
    for psm in (7, 6):
        cfg = f"--oem 1 --psm {psm} {cfg_common}"
        for lang in ("ocrb", "eng+ocrb", "eng"):
            txt = _tess_string(img, lang=lang, config=cfg, timeout_s=2)
            if txt and len(txt.strip()) >= 10:
                return txt
    return ""


def _extract_mrz_pair(raw: str):
    """
    raw OCR 결과에서 '<'가 충분하고 길이가 긴 라인 2개를 MRZ 후보로 선택.
    (엄격 TD3 검증은 기존 _is_td3_candidate 재사용)
    """
    if not raw:
        return (None, None)

    lines = []
    for ln in raw.splitlines():
        s = (ln or "").strip().replace(" ", "")
        s = re.sub(r"[^A-Z0-9<]", "", s.upper())
        if len(s) >= 25 and "<" in s:
            lines.append(s)

    if len(lines) < 2:
        return (None, None)

    # 기존 정규화/검증 사용
    norms = [_normalize_mrz_line(l) for l in lines]
    best = None
    for i in range(len(norms) - 1):
        L1, L2 = norms[i], norms[i + 1]
        if _is_td3_candidate(L1, L2):
            sc = (L1 + L2).count("<")
            if best is None or sc > best[0]:
                best = (sc, L1, L2)

    if best:
        return (best[1], best[2])

    # fallback: '<' 많은 상위 2개
    scored = sorted(((n.count("<") + len(n), n) for n in norms), reverse=True)
    return (scored[0][1], scored[1][1])


def _extract_name_from_mrz_text(raw: str) -> dict:
    """
    MRZ 블록 텍스트에서 이름 힌트를 추출.
    (P<국가코드성<<명 패턴을 우선 사용)
    """
    if not raw:
        return {}
    joined = re.sub(r"\s+", "", raw.upper())
    m = re.search(r"P<[A-Z0-9]{3}([A-Z<]{2,30})<<([A-Z<]{2,30})", joined)
    if not m:
        return {}

    def _clean(s: str) -> str:
        s = re.sub(r"[^A-Z<]", "", s)
        s = s.replace("<", " ").strip()
        return re.sub(r"\s{2,}", " ", s)

    return {"성": _clean(m.group(1)), "명": _clean(m.group(2))}


def _passport_payload(out: dict) -> dict:
    """여권 OCR 파싱 결과를 공통 포맷으로 정규화."""
    return {
        "성":       out.get("성", ""),
        "명":       out.get("명", ""),
        "여권":     out.get("여권", ""),
        "발급":     out.get("발급", ""),
        "만기":     out.get("만기", ""),
        "국가":     out.get("국가", ""),
        "성별":     out.get("성별", ""),
        "생년월일": out.get("생년월일", ""),
    }


def parse_passport(img):
    """
    TD3 여권: 국가/방향/상하좌우 편차를 감안하여 MRZ 2줄을 우선 추출.
    - 속도 보호: 큰 이미지는 축소 + 시도 예산(회전×상하좌우 후보) 내 조기 종료
    반환:
      {'성','명','여권','발급','만기','생년월일'}
    """
    if img is None:
        return {}

    # 성능 보호: 너무 큰 이미지는 한 변 최대 1600px 로 축소 (기존과 동일)
    max_side = 1600
    w0, h0 = img.size
    scale = max_side / float(max(w0, h0))
    if scale < 1.0:
        img = img.resize((int(w0 * scale), int(h0 * scale)), resample=_PILImage.LANCZOS)

    # 여백이 큰 스캔은 내용 영역을 먼저 추출
    img = _crop_to_content_bbox(img)

    # 회전 우선순위: 0/180 먼저 (대부분 케이스), 그 다음 90/270
    rotations = (0, 180, 90, 270)

    # 최대 시도 예산 (속도 유지)
    tries = 0
    max_tries = 12
    best = {}

    for deg in rotations:
        if tries >= max_tries:
            break

        rot = _crop_to_content_bbox(img.rotate(deg, expand=True))

        # 후보 영역 중 '엣지밀도' 높은 것부터 시도
        regions = _split_regions(rot)
        scored = sorted(
            ((_edge_density(rimg), rkey, rimg) for rkey, rimg in regions.items()),
            key=lambda x: x[0],
            reverse=True,
        )

        # 우선순위: 상/하/좌/우 중 상위 3개 + full
        cand = []
        for _, k, rimg in scored:
            if k == "full":
                continue
            cand.append((k, rimg))
            if len(cand) >= 3:
                break
        cand.append(("full", regions["full"]))

        for _, rimg in cand:
            if tries >= max_tries:
                break

            for band_ratio in (0.45, 0.6):
                if tries >= max_tries:
                    break

                band = _crop_mrz_band(rimg, band_ratio=band_ratio)
                for pre in (_prep_mrz, _binarize_soft):
                    if tries >= max_tries:
                        break
                    tries += 1

                    try:
                        prep = pre(band)
                    except Exception:
                        prep = band

                    raw = _ocr_mrz(prep)
                    if not raw:
                        continue

                    name_hint = _extract_name_from_mrz_text(raw)
                    L1, L2 = _extract_mrz_pair(raw)
                    if not (L1 and L2):
                        continue

                    out = _parse_mrz_pair(L1, L2)
                    if name_hint and (not out.get("성") or not out.get("명")):
                        out["성"] = out.get("성") or name_hint.get("성", "")
                        out["명"] = out.get("명") or name_hint.get("명", "")

                    # 필수값(여권번호/생년/만기) 중 2개 이상 있으면 성공으로 간주
                    have = sum(bool(out.get(k)) for k in ("여권", "생년월일", "만기"))
                    if have >= 2:
                        return _passport_payload(out)

                    if have > sum(bool(best.get(k)) for k in ("여권", "생년월일", "만기")):
                        best = out

    if best:
        return _passport_payload(best)

    return {}


# 등록증(ARC) 관련 보조 정규식/함수들 (사용하던 버전 그대로)
_ADDR_BAN_RE = re.compile(
    r'(유효|취업|가능|확인|민원|국번없이|콜센터|call\s*center|www|http|1345|출입국|immigration|안내|관할|관계자|외|금지)',
    re.I
)
# ── 이름 추출 보조 ─────────────────────────
_NAME_BAN = {
    "외국", "국내", "거소", "신고", "증", "재외동포", "재외동","외동포",
    "재외", "동포", "국적", "주소", "발급", "발급일", "발급일자",
    "만기", "체류", "자격", "종류", "성명", "이름", "사력"
}

def _extract_kor_name_strict(text: str) -> str:
    """
    등록증 앞면 전체 텍스트에서 한글 이름 2~3글자를 최대한 안전하게 추출
    0순위: 괄호 안 한글 이름 2~4글자  (예: LI FENZI(이분자))
    1순위: '성명' / '이름' 뒤의 2~3글자
    2순위: 전체에서 2~3글자 토큰 중 라벨 근처에 있는 것
    """
    if not text:
        return ""

    # 0) 괄호 안 한글 이름 예: LI FENZI(이분자)
    m = re.search(r"\(([가-힣]{2,4})\)", text)
    if m:
        cand = m.group(1)
        if cand not in _NAME_BAN:
            return cand

    # 1) '성명: 이분' / '성명 이분' 패턴
    m = re.search(r"(성명|이름)\s*[:\-]?\s*([가-힣]{2,3})", text)
    if m:
        cand = m.group(2)
        if cand not in _NAME_BAN:
            return cand

    # 2) 전체에서 한글 2~3글자 토큰 후보
    toks = re.findall(r"[가-힣]{2,3}", text)
    toks = [t for t in toks if t not in _NAME_BAN]
    if not toks:
        return ""

    # '성명' / '이름' 라벨 위치 기준으로 가장 가까운 토큰 선택
    label_pos_list = [p for p in (text.find("성명"), text.find("이름")) if p != -1]
    label_pos = min(label_pos_list) if label_pos_list else len(text) // 2

    best, best_d = "", 10**9
    for t in toks:
        p = text.find(t)
        if p == -1:
            continue
        d = abs(p - label_pos)
        if d < best_d:
            best, best_d = t, d

    return best

def _kor_count(s: str) -> int:
    return len(re.findall(r'[가-힣]', s or ''))

def parse_arc(img, fast: bool = False):
    """
    등록증 이미지 파서.
    - fast=True  이면:
        * 등록증 전체 이미지를 한 변 최대 1600px로 리사이즈
        * 상단 OCR 시 ocr_try_all 을 최대 2회까지만 시도
    - fast=False 이면:
        * 리사이즈 없이 원본 크기
        * ocr_try_all 이 langs×psms×pres 전체 조합을 모두 시도 (기존과 동일)
    반환값 예:
    {'한글','등록증','번호','발급일','만기일','주소'}
    """
    out = {}
    if img is None:
        return out

    # 🔹 FAST 모드일 때만: 등록증 이미지 리사이즈 (한 변 최대 1600px)
    if fast:
        max_side = 1600
        w0, h0 = img.size
        scale = max_side / float(max(w0, h0))
        if scale < 1.0:
            img = img.resize(
                (int(w0 * scale), int(h0 * scale)),
                resample=_PILImage.LANCZOS,
            )

    # 리사이즈 반영된 크기로 상·하단 분리
    w, h = img.size
    top = img.crop((0, 0, w, int(h*0.5)))
    bot = img.crop((0, int(h*0.5), w, h))

    # 상단: 기본 OCR
    try:
        # FAST 모드면: 앞 조합 2개까지만 시도, 아니면 전체 조합
        max_tries = 2 if fast else None
        t_top = ocr_try_all(top, langs=("kor","kor+eng"), max_tries=max_tries)["text"]
    except Exception:
        t_top = ""
    tn_top = t_top

    # 등록증 앞6/뒤7
    # 등록증 앞6/뒤7
    # 등록증 앞6/뒤7
    t_dense = re.sub(r'(?<=\d)\s+(?=\d)', '', tn_top)

    # 1차: 6자리 + (기호/공백) + 7자리 패턴
    pair = re.search(r'(?<!\d)(\d{6})\D{0,20}(\d{7})(?!\d)', t_dense)
    if pair:
        out["등록증"], out["번호"] = pair.group(1), pair.group(2)

    # 2차: fallback – 앞 6자리만 잡혔거나, 아직 번호가 비어 있으면
    if not out.get("등록증"):
        m6 = re.search(r'(?<!\d)(\d{6})(?!\d)', t_dense)
        if m6:
            out["등록증"] = m6.group(1)

    if out.get("등록증") and not out.get("번호"):
        idx6 = t_dense.find(out["등록증"])
        candidates7 = list(re.finditer(r'(?<!\d)(\d{7})(?!\d)', t_dense))
        if candidates7:
            if idx6 >= 0:
                # 앞 6자리 위치에서 가장 가까운 7자리 숫자 선택
                best7 = min(candidates7, key=lambda m: abs(m.start() - idx6))
            else:
                best7 = candidates7[0]
            out["번호"] = best7.group(1)

        # 3차: 숫자 덩어리에서 강제 분할 (13자리 → 6+7)
    if not out.get("번호"):
        for m in re.finditer(r'\d{11,14}', t_dense):
            s = m.group(0)
            if len(s) == 13:
                # 6211146101796 같은 경우
                front, back = s[:6], s[6:]
                out.setdefault("등록증", front)
                out["번호"] = back
                break


    # 발급일
    def _find_all_dates(text: str):
        cands = set()
        if not text: return []
        for m in re.finditer(r'(\d{4})[.\-\/](\d{1,2})[.\-\/](\d{1,2})', text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try: cands.add(_dt(y, mo, d).strftime('%Y-%m-%d'))
            except: pass
        MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
        for m in re.finditer(r'(\d{1,2})\s*([A-Z]{3})\s*(\d{4})', (text or '').upper()):
            d, mon, y = int(m.group(1)), MONTHS.get(m.group(2),0), int(m.group(3))
            if mon:
                try: cands.add(_dt(y, mon, d).strftime('%Y-%m-%d'))
                except: pass
        return sorted(cands)

    def _pick_labeled_date(text: str, labels_regex: str):
        if not text: return ''
        m1 = re.search(labels_regex + r'[^\d]{0,10}(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})', text, re.I)
        if m1:
            return m1.group(1).replace('/', '-').replace('.', '-')
        return ''

    issued = _pick_labeled_date(tn_top, r"(발\s*급|발\s*행|issue|issued)")
    if not issued:
        ds = _find_all_dates(tn_top)
        if ds:
            issued = ds[0]
    if issued:
        out["발급일"] = issued

    # ───────── 한글 이름 추출 ─────────
    def _extract_name_from_text(text: str) -> str:
        ban = {
            "외국", "국내", "거소", "신고", "증", "재외동포",
            "재외", "동포", "국적", "주소", "발급", "발급일", "발급일자",
            "만기", "체류", "자격", "종류", "성명", "이름"
        }
        m = re.search(r"(성명|이름)\s*[:\-]?\s*([가-힣]{2,4})", text)
        if m and m.group(2) not in ban:
            return m.group(2)
        toks = re.findall(r"[가-힣]{2,4}", text)
        toks = [t for t in toks if t not in ban]
        if not toks:
            return ""
        pos_label = min(
            [p for p in [text.find("성명"), text.find("이름")] if p != -1] + [len(text)//2]
        )
        best, best_d = "", 10**9
        for t in toks:
            p = text.find(t)
            if p != -1:
                d = abs(p - pos_label)
                if d < best_d:
                    best, best_d = t, d
        return best

    def _extract_name_from_roi(img, text_top: str) -> str:
        """
        등록증 앞면 가운데 이름 박스 쪽을 한 번 더 OCR 해서
        손글씨/스티커 이름(예: 윤원길)을 최대한 잡아낸다.
        """
        try:
            w, h = img.size
            # 카드 기준 대략 이름 박스 위치 (비율 기반이어서 스캔 크기 달라도 유지됨)
            roi = img.crop((
                int(w * 0.30),  # left
                int(h * 0.30),  # top
                int(w * 0.95),  # right
                int(h * 0.70),  # bottom
            ))
            txt = _fast_ocr(roi, lang="kor", psm=7)
            m = re.search(r"[가-힣]{2,4}", txt)
            if m:
                return m.group(0)
        except Exception:
            pass
        # ROI에서 못 찾으면 기존 텍스트 기반으로
        return _extract_name_from_text(text_top)

    # --- 이름 추출 ---
    # --- 이름 추출 (ROI 우선 + 텍스트 보조) ---
    name_ko = _extract_kor_name_strict(t_top)

    if name_ko:
        out["한글"] = name_ko

    # 하단(만기/주소)
    best_text, best_sc = "", -1
    for deg in (0, 90, 270):
        im = bot.rotate(deg, expand=True)
        t1 = _ocr(ImageOps.grayscale(im), lang="kor", config="--oem 3 --psm 6")
        t2 = _ocr(ImageOps.grayscale(im), lang="kor", config="--oem 3 --psm 4")
        t = (t1 + "\n" + t2)
        sc = _kor_count(t)
        if sc > best_sc:
            best_sc, best_text = sc, t
    tn_bot = best_text

    # 🔚 만기일: 하단에서 발견된 "모든 날짜" 중 가장 늦은 날짜를 선택
    expiry = _pick_labeled_date(
        tn_bot,
        r"(만기|유효|until|expiry|expiration|valid\s*until|까지)"
    )
    ds_bot = _find_all_dates(tn_bot)

    # 발급일(issued)과 같은 날짜는 후보에서 제거
    if issued and issued in ds_bot:
        try:
            ds_bot.remove(issued)
        except ValueError:
            pass

    # 라벨로 잡은 만기일이 있으면 후보에 포함
    if expiry:
        ds_bot.append(expiry)

    if ds_bot:
        ds_bot = sorted(set(ds_bot))
        out["만기일"] = ds_bot[-1]   # 👉 가장 늦은 날짜 = 최종 만기일

    # 주소
        # ───────── 주소(국내거소) 추출 ─────────
    def _clean_addr_line(s: str) -> str:
        s = re.sub(r'^\s*\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\s*', '', s or '')
        s = re.sub(r'[^가-힣0-9\s\-\.,#()/~]', ' ', s)
        if ARC_REMOVE_PAREN:
            s = re.sub(r'\([^)]*\)', ' ', s)  # (정왕동) 같은 괄호 제거
        s = re.sub(r'\s{2,}', ' ', s).strip(' ,')
        return s

    def _is_junk_addr_line(s: str) -> bool:
        s = (s or '').strip()
        if not s:
            return True
        if _ADDR_BAN_RE.search(s):
            return True
        if _kor_count(s) < 3 and len(re.sub(r'[^\d]', '', s)) >= 6:
            return True
        if re.fullmatch(r'[\(\)\.\-/#\s]+', s):
            return True
        return False

    def _addr_score(s: str) -> float:
        s = _clean_addr_line(s)
        if _is_junk_addr_line(s):
            return -1.0
        has_lvl  = bool(re.search(r'(도|시|군|구)', s))
        has_road = bool(re.search(r'(로|길|번길|대로)', s))
        has_num  = bool(re.search(r'\d', s))
        has_unit = bool(re.search(r'(동|호|층|호수|#\d+)', s))
        return (
            _kor_count(s)*2 +
            has_lvl*6 + has_road*8 + has_num*4 + has_unit*2 +
            min(len(s), 60)/12.0
        )

    def _best_addr_latest(text: str) -> str:
        """
        국내거소 테이블에서
        'YYYY.MM.DD + 주소' 형식 줄 중,
        날짜가 가장 최근인 줄의 주소를 우선 사용.
        없으면 기존 점수 기반으로 fallback.
        """
        lines = [l for l in (text or '').splitlines() if l.strip()]
        best_addr = ""
        best_date = None
        best_sc   = -1.0

        for l in lines:
            if _ADDR_BAN_RE.search(l):
                continue
            m = re.search(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', l)
            if not m:
                continue
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                dt = _dt(y, mo, d)
            except ValueError:
                continue

            c  = _clean_addr_line(l)
            sc = _addr_score(c)
            if sc < 0:
                continue

            if (best_date is None) or (dt > best_date) or (dt == best_date and sc > best_sc):
                best_date, best_sc, best_addr = dt, sc, c

        # 날짜 있는 주소를 하나도 못 찾았으면,
        # 기존 방식(점수 최고 주소) fallback
        if best_addr:
            return best_addr

        best_i, best_addr2, best_score2 = -1, "", -1
        for i, l in enumerate(lines):
            c = _clean_addr_line(l)
            sc = _addr_score(c)
            if sc > best_score2:
                best_i, best_addr2, best_score2 = i, c, sc
        return best_addr2

    # tn_bot: 하단 OCR 텍스트 (이미 위에서 계산)
    # tn_bot: 하단 OCR 텍스트 (이미 위에서 계산)
    addr = _best_addr_latest(tn_bot)
    if addr and _kor_count(addr) >= 3 and len(addr) >= 6:
        out["주소"] = addr
    else:
        # 👉 하단에서 못 찾았으면 상단+하단 전체를 대상으로 한 번 더 시도
        addr2 = _best_addr_latest(t_top + "\n" + tn_bot)
        if addr2 and _kor_count(addr2) >= 3 and len(addr2) >= 6:
            out["주소"] = addr2
                # 그래도 못 찾으면, '도/시/로/길/번길' 포함 + 한글 많은 줄을 강제로 선택
        if "주소" not in out:
            lines_all = [l.strip() for l in (t_top + "\n" + tn_bot).splitlines() if l.strip()]
            best_line = ""
            best_score = -1
            for l in lines_all:
                if _kor_count(l) < 3:
                    continue
                if not re.search(r'(도|시|군|구|로|길|번길|대로)', l):
                    continue
                sc = _addr_score(l)
                if sc > best_score:
                    best_score = sc
                    best_line = _clean_addr_line(l)
            if best_line:
                out["주소"] = best_line


    return out


# -----------------------------
# 3) 페이지 렌더 함수
# -----------------------------

def render():
    """
    스캔으로 고객 추가/수정 페이지 (기존 PAGE_SCAN 코드 모듈화 버전)
    """

    st.subheader("📷 스캔으로 고객 추가/수정")
    st.caption("여권 1장만 또는 여권+등록증 2장을 업로드하세요.")

    show_debug = st.checkbox(
        "🧪 디버그 패널 보기(느림)", value=False,
        help="체크하면 원문/베스트OCR/파싱결과/테서랙트 진단을 표시합니다. (속도 저하)"
    )

    # 등록증 FAST 모드 (기본 ON)
    fast_arc = st.checkbox(
        "⚡ 등록증 빠른 모드 (리사이즈 + OCR 최대 2회)",
        value=True,
        help=(
            "체크 시: 등록증 이미지를 적당히 줄이고, 상단 OCR 조합을 앞에서부터 최대 2번까지만 시도합니다. "
            "해제 시: 이미지를 원본 크기로 두고, langs/psm/전처리 모든 조합을 시도해 인식률을 최대화합니다."
        ),
    )

    # Tesseract 점검
    if not _ensure_tesseract():
        st.error("pytesseract가 감지되지 않았습니다. `Tesseract-OCR` 설치 및 환경설정을 확인하세요.")
        st.stop()

    # 업로드
    cc0, cc1 = st.columns(2)
    with cc0:
        passport_file = st.file_uploader("여권 이미지 (필수)", type=["jpg", "jpeg", "png", "webp", "pdf"])
    with cc1:
        arc_file = st.file_uploader("등록증/스티커 이미지 (선택)", type=["jpg", "jpeg", "png", "webp", "pdf"])

    # 업로드된 파일이 바뀌면 → 새 스캔으로 판단하고 prefill 플래그 초기화
    prev_pass = st.session_state.get("_scan_prev_passport_name")
    prev_arc  = st.session_state.get("_scan_prev_arc_name")

    cur_pass = passport_file.name if passport_file is not None else None
    cur_arc  = arc_file.name if arc_file is not None else None

    if (cur_pass, cur_arc) != (prev_pass, prev_arc):
        st.session_state["_scan_prefilled_once"] = False
        st.session_state["_scan_prev_passport_name"] = cur_pass
        st.session_state["_scan_prev_arc_name"] = cur_arc

    # Tesseract 디버그
    if show_debug:
        with st.expander("🔧 Tesseract 진단 정보"):
            try:
                ver = pytesseract.get_tesseract_version()
            except Exception as e:
                ver = f"(에러: {e})"
            st.write(f"Tesseract 버전: {ver}")
            st.write(f"tesseract_cmd: {getattr(pytesseract.pytesseract, 'tesseract_cmd', '')}")
            st.write(f"TESSDATA_PREFIX: {os.environ.get('TESSDATA_PREFIX')}")
            try:
                langs = pytesseract.get_languages()
            except Exception as e:
                langs = f"(에러: {e})"
            st.write(f"탐지된 언어들: {langs}")

    parsed_passport, parsed_arc = {}, {}

    # 이미지/미리보기 + 파싱
    if passport_file:
        img_p = open_image_safe(passport_file)
        parsed_passport = parse_passport(img_p)
    else:
        img_p = None

    if arc_file:
        img_a = open_image_safe(arc_file)
        # 🔹 FAST 모드 on/off 에 따라 등록증 파싱 전략 변경
        parsed_arc = parse_arc(img_a, fast=fast_arc)
    else:
        img_a = None



    # 베스트 OCR 원문 디버그
    if show_debug:
        with st.expander("🧪 OCR 원문(베스트 설정)", expanded=False):
            if img_p is not None:
                bp = ocr_try_all(img_p)
                st.write({"lang": bp["lang"], "config": bp["config"], "pre": bp["pre"], "score": bp["score"]})
                st.code(bp["text"][:2000])
            if img_a is not None:
                ba = ocr_try_all(img_a)
                st.write({"lang": ba["lang"], "config": ba["config"], "pre": ba["pre"], "score": ba["score"]})
                st.code(ba["text"][:2000])

    # MRZ/ARC 원문 + 파싱 결과 디버그
    if show_debug:
        if img_p is not None:
            with st.expander("🔎 여권 MRZ 원문 샘플"):
                w, h = img_p.size
                mrz_crop = img_p.crop((0, int(h*0.6), w, h))
                mrz_bin = _binarize(mrz_crop)
                st.image(mrz_bin, caption="MRZ(하단부) 샘플", use_container_width=True)
                st.code(_ocr(
                    mrz_bin,
                    "eng",
                    "--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ<0123456789"
                ))

        if img_a is not None:
            with st.expander("🔎 등록증 전체 OCR(빠른 이진화 1회)"):
                st.code(_ocr(_binarize(img_a), "kor", "--oem 3 --psm 6")[:2000])

        with st.expander("🧪 OCR 파싱 결과(디버그)"):
            st.json({"passport": parsed_passport, "arc": parsed_arc})

    # OCR 결과 → 세션 채우기
    def _prefill_from_ocr(p, a):
        changed = False

        def setk(field, val):
            nonlocal changed
            k = f"scan_{field}"
            v = (val or "").strip()
            if not v:
                return

            # 값이 달라지면 무조건 새 OCR 값으로 덮어쓴다
            # 👇 이걸로 교체
            cur = str(st.session_state.get(k, "")).strip()
            if cur != v:
                st.session_state[k] = v
                changed = True

        setk("한글",     a.get("한글"))
        setk("성",       p.get("성"))
        setk("명",       p.get("명"))
        setk("성별",     p.get("성별"))
        setk("국가",     p.get("국가"))
        setk("여권",     p.get("여권"))
        setk("여권발급", p.get("발급"))
        setk("여권만기", p.get("만기"))
        setk("등록증",   a.get("등록증"))
        setk("번호",     a.get("번호"))
        setk("발급일",   a.get("발급일"))
        setk("만기일",   a.get("만기일"))
        setk("주소",     a.get("주소"))

        # 여권 생년월일로 등록증 앞자리 채우기
        birth = (p.get("생년월일") or "").strip()
        if birth:
            try:
                yymmdd = _dt.strptime(birth, "%Y-%m-%d").strftime("%y%m%d")
                if not str(st.session_state.get("scan_등록증", "")).strip():
                    st.session_state["scan_등록증"] = yymmdd
                    changed = True
            except Exception:
                pass

        return changed

    # 👇 이걸로 교체
    if not st.session_state.get("_scan_prefilled_once"):
        if _prefill_from_ocr(parsed_passport, parsed_arc):
            st.session_state["_scan_prefilled_once"] = True
            st.rerun()

    # -----------------------------
    # 확인/수정 폼 (2 x 2 레이아웃)
    # -----------------------------
    if "scan_연" not in st.session_state or not str(st.session_state["scan_연"]).strip():
        st.session_state["scan_연"] = "010"

    st.markdown("### 🔎 스캔 결과 확인 및 수정")

    with st.form(key="scan_confirm_form_v2"):
        # 1) 첫번째 가로 줄: 여권 (이미지 70% + 정보 30%)
        row1_img_col, row1_info_col = st.columns([7, 3])

        with row1_img_col:
            st.markdown("#### 여권 이미지")
            if img_p is not None:
                st.image(img_p, caption="여권", use_container_width=True)
            else:
                st.info("여권 이미지를 업로드하세요.")

        with row1_info_col:
            # 🔹 여권 이미지 높이에 맞춰 대략 중앙쯤에서 시작하도록 위쪽 여백 추가
            st.markdown("<div style='height: 240px'></div>", unsafe_allow_html=True)

            st.markdown("#### 여권 정보")
            성   = st.text_input("성(영문)", key="scan_성")
            명   = st.text_input("명(영문)", key="scan_명")
            성별 = st.text_input("성별", key="scan_성별")
            국가 = st.text_input("국가(국적)", key="scan_국가")
            여권     = st.text_input("여권번호", key="scan_여권")
            여권발급 = st.text_input("여권 발급일(YYYY-MM-DD)", key="scan_여권발급")
            여권만기 = st.text_input("여권 만기일(YYYY-MM-DD)", key="scan_여권만기")


        # 2) 두번째 가로 줄: 등록증 (이미지 70% + 정보 30%)
        row2_img_col, row2_info_col = st.columns([7, 3])

        with row2_img_col:
            st.markdown("#### 등록증 / 스티커 이미지")
            if img_a is not None:
                st.image(img_a, caption="등록증/스티커", use_container_width=True)
            else:
                st.info("등록증/스티커 이미지를 업로드하지 않아도 됩니다.")

        with row2_info_col:
            # 🔹 등록증 이미지 중앙쯤에서 입력이 시작되도록 위쪽 여백 추가
            st.markdown("<div style='height: 160px'></div>", unsafe_allow_html=True)

            st.markdown("#### 등록증 / 연락처 정보")
            한글   = st.text_input("한글 이름", key="scan_한글")
            등록증 = st.text_input("등록증 앞(YYMMDD)", key="scan_등록증")
            번호   = st.text_input("등록증 뒤 7자리",   key="scan_번호")
            발급일 = st.text_input("등록증 발급일(YYYY-MM-DD)", key="scan_발급일")
            만기일 = st.text_input("등록증 만기일(YYYY-MM-DD)", key="scan_만기일")
            주소   = st.text_input("주소", key="scan_주소")

            p1, p2, p3, p4 = st.columns([1, 1, 1, 0.7])
            연   = p1.text_input("연(앞 3자리)", key="scan_연")
            락   = p2.text_input("락(중간 4자리)", key="scan_락")
            처   = p3.text_input("처(끝 4자리)", key="scan_처")
            V    = p4.text_input("V", key="scan_V")


        submitted = st.form_submit_button("💾 고객관리 반영", use_container_width=True)
        if submitted:
            passport_data = {
                "성":   성.strip(),
                "명":   명.strip(),
                "성별": 성별.strip(),
                "국가": 국가.strip(),
                "여권": 여권.strip(),
                "발급": 여권발급.strip(),
                "만기": 여권만기.strip(),
            }
            arc_data = {
                "한글":   한글.strip(),
                "등록증": 등록증.strip(),
                "번호":   번호.strip(),
                "발급일": 발급일.strip(),
                "만기일": 만기일.strip(),
                "주소":   주소.strip(),
            }
            extra_data = {
                "연": 연.strip(),
                "락": 락.strip(),
                "처": 처.strip(),
                "V":  V.strip(),
            }

            ok, msg = upsert_customer_from_scan(passport_data, arc_data, extra_data)

            if ok:
                st.session_state["scan_saved_ok"] = True
                st.success(f"✅ {msg}")
            else:
                st.error(f"❌ {msg}")

            if st.session_state.get("scan_saved_ok"):
                st.success("✅ 고객관리 데이터에 반영이 완료되었습니다.")


    if st.button("← 고객관리로 돌아가기", use_container_width=True):
        st.session_state[SESS_CURRENT_PAGE] = PAGE_CUSTOMER
        st.rerun()
