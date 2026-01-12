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
    업로드된 이미지를 안전하게 RGB로 여는 함수.
    """
    if uploaded_file is None:
        return None
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
    L1 = _normalize_mrz_line(L1); L2 = _normalize_mrz_line(L2)

    # 이름
    if '<<' in L1[5:]:
        sur, given = L1[5:].split('<<', 1)
        out['성'] = sur.replace('<', ' ').strip()
        out['명'] = given.replace('<', ' ').strip()

    # 여권, 국적, 생년, 성별, 만기
    pn = re.sub(r'[^A-Z0-9]', '', L2[0:9])
    if pn: out['여권'] = pn
    nat = re.sub(r'[^A-Z]', '', L2[10:13])
    if nat: out['국가'] = nat

    b = re.sub(r'[^0-9]', '', L2[13:19])
    if len(b) == 6:
        yy, mm, dd = int(b[:2]), int(b[2:4]), int(b[4:6])
        yy += 2000 if yy < 80 else 1900
        try: out['생년월일'] = _dt(yy,mm,dd).strftime('%Y-%m-%d')
        except: pass

    sx = L2[20:21]
    out['성별'] = '남' if sx == 'M' else ('여' if sx == 'F' else '')

    e = re.sub(r'[^0-9]', '', L2[21:27])
    if len(e) == 6:
        yy, mm, dd = int(e[:2]), int(e[2:4]), int(e[4:6])
        yy += 2000 if yy < 80 else 1900
        try: out['만기'] = _dt(yy,mm,dd).strftime('%Y-%m-%d')
        except: pass

    # 👉 발급일: 실무 편의를 위해 항상 10년짜리 기준으로 역산 (+1일)
    if out.get('만기'):
        try:
            exp = _dt.strptime(out['만기'], '%Y-%m-%d').date()
            issued = _minus_years(exp, 10) + _td(days=1)
            out['발급'] = issued.strftime('%Y-%m-%d')
        except Exception:
            pass

    return out

def parse_passport(img):
    """
    TD3 여권: 하단 40%에서 MRZ 2줄만 인식해서
    {'성','명','여권','발급','만기','생년월일'} 반환.
    일반 텍스트 OCR 결과는 사용하지 않는다.
    """
    if img is None:
        return {}

    # 🔹 성능 보호: 너무 큰 이미지는 한 변 최대 1600px 로 축소
    max_side = 1600
    w0, h0 = img.size
    scale = max_side / float(max(w0, h0))
    if scale < 1.0:
        img = img.resize(
            (int(w0 * scale), int(h0 * scale)),
            resample=_PILImage.LANCZOS,
        )

    w, h = img.size
    band = img.crop((0, int(h * 0.58), w, h))  # 하단 MRZ 영역

    texts = []

    def _ocr_mrz_block(im):
        """
        MRZ 전용 OCR:
        - 1차: ocrb+eng
        - 2차: eng (ocrb 미설치/오류 대비)
        psm 7, 6 두 번 시도
        """
        lines = []
        cfg_common = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ<0123456789"
        for psm in (7, 6):
            # 1차: ocrb+eng
            txt = _ocr(im, lang="ocrb+eng", config=f"--oem 3 --psm {psm} {cfg_common}")
            if txt.strip():
                lines.append(txt)
                continue

            # 2차: eng (ocrb 없을 때용)
            txt = _ocr(im, lang="eng", config=f"--oem 3 --psm {psm} {cfg_common}")
            if txt.strip():
                lines.append(txt)
        return "\n".join(lines)

    # 전처리 3종(부드러운 이진화, 기본 대비, 원본)을 각각 시도
    for pre in (_binarize_soft, _pre, lambda x: x):
        try:
            im = pre(band)
        except Exception:
            im = band
        block_txt = _ocr_mrz_block(im)
        if block_txt.strip():
            texts.append(block_txt)

    joined = "\n".join(t for t in texts if t.strip())
    if not joined:
        # MRZ 후보 자체가 안 나오면 그냥 포기
        return {}

    # 1차: 기존 TD3 검증 로직으로 MRZ 2줄 찾기
    L1, L2 = find_mrz_pair_from_text(joined)

    # 2차: 그래도 못 찾으면 '<'가 많이 들어간 줄 두 개를 강제로 선택
    if not L1 or not L2:
        lines = [l for l in joined.splitlines() if l.strip()]
        scored = []
        for l in lines:
            score = l.count('<') + sum(c.isdigit() for c in l)
            if score >= 10:  # MRZ 느낌 나는 줄만
                scored.append((score, _normalize_mrz_line(l)))
        scored.sort(key=lambda x: x[0])
        if len(scored) >= 2:
            L1 = scored[-2][1]
            L2 = scored[-1][1]
        else:
            return {}


    out = _parse_mrz_pair(L1, L2)
    return {
        "성":       out.get("성", ""),
        "명":       out.get("명", ""),
        "여권":     out.get("여권", ""),
        "발급":     out.get("발급", ""),
        "만기":     out.get("만기", ""),
        "생년월일": out.get("생년월일", ""),
    }


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
        passport_file = st.file_uploader("여권 이미지 (필수)", type=["jpg", "jpeg", "png", "webp"])
    with cc1:
        arc_file = st.file_uploader("등록증/스티커 이미지 (선택)", type=["jpg", "jpeg", "png", "webp"])

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
        st.image(img_p, caption="여권", use_container_width=True)
        parsed_passport = parse_passport(img_p)
    else:
        img_p = None

    if arc_file:
        img_a = open_image_safe(arc_file)
        st.image(img_a, caption="등록증/스티커", use_container_width=True)
        # 🔹 FAST 모드 on/off 에 따라 등록증 파싱 전략 변경
        parsed_arc = parse_arc(img_a, fast=fast_arc)
    else:
        img_a = None


    # 여권 생년월일을 등록증 앞자리(YYMMDD)에 우선 반영
    try:
        birth = parsed_passport.get("생년월일", "").strip()
        if birth:
            yymmdd = _dt.strptime(birth, "%Y-%m-%d").strftime("%y%m%d")
            st.session_state["scan_등록증"] = yymmdd  # 항상 덮어씀
    except Exception:
        pass

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

            cur = str(st.session_state.get(k, "")).strip()
            # 값이 달라지면 무조건 새 OCR 값으로 덮어쓴다
            if cur != v:
                st.session_state[k] = v
                changed = True

        setk("한글",     a.get("한글"))
        setk("성",       p.get("성"))
        setk("명",       p.get("명"))
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

    if _prefill_from_ocr(parsed_passport, parsed_arc) and not st.session_state.get("_scan_prefilled_once"):
        st.session_state["_scan_prefilled_once"] = True
        st.rerun()

    # 최종 한 번 더 여권 생년월일 → 등록증 앞자리 우선권
    try:
        birth = (parsed_passport.get("생년월일", "") or "").strip()
        if birth:
            yymmdd = _dt.strptime(birth, "%Y-%m-%d").strftime("%y%m%d")
            st.session_state["scan_등록증"] = yymmdd
    except Exception:
        pass

    # -----------------------------
    # 확인/수정 폼
    # -----------------------------
    if "scan_연" not in st.session_state or not str(st.session_state["scan_연"]).strip():
        st.session_state["scan_연"] = "010"

    st.markdown("### 🔎 OCR 추출값 (필요 시 수정)")
    with st.form("scan_confirm_form"):
        c1, c2, c3 = st.columns(3)

        # 기본 인적사항
        한글 = c1.text_input("한글", key="scan_한글")
        성   = c1.text_input("성(영문)", key="scan_성")
        명   = c1.text_input("명(영문)", key="scan_명")

        여권     = c2.text_input("여권번호", key="scan_여권")
        여권발급 = c2.text_input("여권 발급일(YYYY-MM-DD)", key="scan_여권발급")
        여권만기 = c2.text_input("여권 만기일(YYYY-MM-DD)", key="scan_여권만기")

        등록증 = c3.text_input("등록증 앞(YYMMDD)", key="scan_등록증")
        번호   = c3.text_input("등록증 뒤 7자리",   key="scan_번호")
        발급일 = c3.text_input("등록증 발급일(YYYY-MM-DD)", key="scan_발급일")
        만기일 = c3.text_input("등록증 만기일(YYYY-MM-DD)", key="scan_만기일")
        주소   = c3.text_input("주소", key="scan_주소")

        # 🔢 전화번호 + V 필드 (사람이 직접 입력/수정)
        p1, p2, p3, p4 = st.columns([1, 1, 1, 0.7])
        연   = p1.text_input("연(앞 3자리)", key="scan_연")
        락   = p2.text_input("락(중간 4자리)", key="scan_락")
        처   = p3.text_input("처(끝 4자리)", key="scan_처")
        V    = p4.text_input("V", key="scan_V")

        submitted = st.form_submit_button("💾 고객관리 반영")
        if submitted:
            passport_data = {
                "성":   성.strip(),
                "명":   명.strip(),
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
