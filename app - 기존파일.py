# ===== Imports (정리본) =====
import os, platform, io, json, uuid, calendar
import datetime
import streamlit as st
import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import NameObject, BooleanObject, TextStringObject, NumberObject
from PIL import Image, ImageDraw, ImageFont
from PIL import Image as _PILImage, ImageOps, ImageFilter
import shlex

# ==== OCR ====
try:
    import pytesseract
except Exception:
    pytesseract = None

# ==== OCR ====  (위 import 근처에 미리 추가)

TESSERACT_ROOT = r"C:\Program Files\Tesseract-OCR"
TESSERACT_EXE  = os.path.join(TESSERACT_ROOT, "tesseract.exe")
TESSDATA_DIR   = os.path.join(TESSERACT_ROOT, "tessdata")  # 참고용

def _ensure_tesseract():
    """
    Windows: Tesseract 실행 파일 경로 + TESSDATA_PREFIX 고정
    (중요) --tessdata-dir은 쓰지 않고, 환경변수만 사용
    """
    if pytesseract is None:
        return False
    if platform.system() == "Windows":
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
        # Tesseract는 $TESSDATA_PREFIX\tessdata 폴더에서 언어파일을 찾습니다.
        os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR + os.sep  # ← tessdata 폴더를 가리키게!
        # 헷갈리는 커스텀 변수는 제거(실제 Tesseract는 TESSDATA_DIR를 쓰지 않습니다)
        os.environ.pop("TESSDATA_DIR", None)
    return True

# ==== 이미지 열기 헬퍼(교체본) ====
# 업로더(UploadedFile)도, 파일경로(str)도 모두 열 수 있고
# EXIF 회전 보정 + RGB 변환까지 합니다.
from PIL import Image as _PILImage, ImageOps

def _open_image_safe(fileobj_or_path):
    """
    Streamlit 업로더(UploadedFile)나 파일경로 모두 지원.
    EXIF 회전 보정 후 RGB로 반환.
    """
    if hasattr(fileobj_or_path, "read"):   # 업로더 객체
        img = _PILImage.open(fileobj_or_path)
    else:                                   # 경로 문자열
        img = _PILImage.open(str(fileobj_or_path))

    try:
        img = ImageOps.exif_transpose(img)  # 휴대폰 사진 회전 보정
    except Exception:
        pass

    return img.convert("RGB")               # 항상 RGB로

# ==== OCR 전처리 + 베스트 시도(디버그용) ====
# _pre / _binarize_soft / _binarize / ocr_try_all
# - UI 디버그(expander)에서 사용하는 최소 세트만 제공합니다.

from PIL import ImageOps, ImageFilter

def _pre(img):
    """부드러운 전처리: 그레이스케일 + 자동 대비 + 1600px 이상으로 리사이즈 + 샤픈"""
    g = ImageOps.grayscale(img)
    w, h = g.size
    if w < 1600:
        r = 1600 / float(w)
        g = g.resize((int(w * r), int(h * r)), resample=_PILImage.Resampling.BILINEAR)
    g = ImageOps.autocontrast(g)
    g = g.filter(ImageFilter.SHARPEN)
    return g

def _binarize_soft(img):
    """너무 세지 않은 이진화(평균밝기 기준 가변 임계값)"""
    from PIL import ImageStat  # 로컬 import (파일 상단 수정 불필요)
    g = ImageOps.grayscale(img)
    w, h = g.size
    if w < 1600:
        r = 1600 / float(w)
        g = g.resize((int(w * r), int(h * r)), resample=_PILImage.Resampling.BILINEAR)
    g = ImageOps.autocontrast(g)
    m = ImageStat.Stat(g).mean[0]  # 평균 밝기
    thr = int(max(100, min(200, m * 0.9)))
    return g.point(lambda p: 255 if p > thr else 0)

def _binarize(img, thr: int = 160):
    """고정 임계값 이진화(샘플 미리보기용)"""
    g = ImageOps.grayscale(img)
    w, h = g.size
    if w < 1800:
        r = 1800 / float(w)
        g = g.resize((int(w * r), int(h * r)), resample=_PILImage.Resampling.BILINEAR)
    g = ImageOps.autocontrast(g).filter(ImageFilter.SHARPEN)
    return g.point(lambda p: 255 if p > thr else 0)

def ocr_try_all(img, langs=None):
    """
    여러 전처리×PSM 조합을 빠르게 시도해서
    '문자수'가 가장 많은 결과를 반환(디버그용).
    반환: {'score','lang','config','pre','text'}
    """
    import re
    if langs is None:
        langs = ["kor", "eng+kor"]
    preprocesses = [lambda x: x, _pre, _binarize_soft, _binarize]
    cfgs = ["--oem 3 --psm 6", "--oem 3 --psm 3"]

    best = (0, "", "", "", "")
    for pre in preprocesses:
        try:
            im = pre(img)
        except Exception:
            im = img
        for lang in langs:
            for cfg in cfgs:
                try:
                    txt = pytesseract.image_to_string(im, lang=lang, config=cfg)
                except Exception:
                    txt = ""
                score = len(re.sub(r"[^A-Za-z0-9가-힣]", "", txt))
                if score > best[0]:
                    best = (score, lang, cfg, getattr(pre, "__name__", "custom"), txt)

    return {"score": best[0], "lang": best[1], "config": best[2], "pre": best[3], "text": best[4]}

# ---- 호환용 별칭 (반드시 함수 정의 "밖"에 둘 것! 들여쓰기 금지) ----
_open_image = _open_image_safe
open_image_safe = _open_image_safe

def _ocr(img, lang="eng+kor", config="--oem 3 --psm 6"):
    try:
        # (중요) 여기서 더 이상 --tessdata-dir 을 붙이지 않습니다.
        return pytesseract.image_to_string(img, lang=lang, config=config)
    except Exception as e:
        st.error(f"OCR 실행 오류: {e}")
        return ""

PARENT_DRIVE_FOLDER_ID = "1vAT3OvELPhosJ99Zg1fJ5hKJEgx7kNlW"

# --- Session Keys ---
SESS_CURRENT_PAGE = 'current_page'
SESS_DF_CUSTOMER = 'df_customer'
SESS_CUSTOMER_SEARCH_TERM = 'customer_search_term'
SESS_CUSTOMER_SEARCH_MASK_INDICES = 'customer_search_mask_indices'
SESS_CUSTOMER_SELECTED_ROW_IDX = 'customer_selected_row_idx'
SESS_CUSTOMER_AWAITING_DELETE_CONFIRM = 'customer_awaiting_delete_confirm'
SESS_CUSTOMER_PENDING_DELETE_DISPLAY_IDX = 'customer_pending_delete_display_idx'
SESS_CUSTOMER_DELETED_ROWS_STACK = 'customer_deleted_rows_stack'
SESS_CUSTOMER_DATA_EDITOR_KEY = 'customer_data_editor_key'
SESS_DAILY_SELECTED_DATE = 'daily_selected_date'
SESS_DAILY_DATE_INPUT_KEY = 'daily_date_input_key'
SESS_DAILY_TEMP_DATA = 'daily_temp_data'
SESS_ALL_DAILY_ENTRIES_PAGE_LOAD = 'all_daily_entries_page_load'
SESS_EVENTS_DATA_HOME = 'events_data_home'
SESS_HOME_SELECTED_YEAR = 'home_selected_year'
SESS_HOME_SELECTED_MONTH = 'home_selected_month'
SESS_HOME_CALENDAR_SELECTED_DATE = 'home_calendar_selected_date'
SESS_PLANNED_TASKS_TEMP = 'planned_tasks_temp_data'
SESS_ACTIVE_TASKS_TEMP = 'active_tasks_temp_data'
SESS_DOC_SELECTED_CUSTOMER_NAME = 'doc_selected_customer_name'
SESS_DOC_SELECTED_CUSTOMER_DATA = 'doc_selected_customer_data'

# Page Keys
PAGE_HOME = 'home'
PAGE_MEMO = 'memo'
PAGE_REFERENCE = 'reference'
PAGE_CUSTOMER = 'customer'
PAGE_DAILY = 'daily'
PAGE_MONTHLY = 'monthly'
PAGE_MANUAL = 'manual'
PAGE_DOCUMENT = 'document'
PAGE_COMPLETED = 'completed'
PAGE_SCAN = 'scan'

def safe_int(val):
    try: return int(float(val))
    except (TypeError, ValueError): return 0

# ===== Google Sheets 설정 =====
SHEET_KEY = "14pEPo-Q3aFgbS1Gqcamb2lkadq-eFlOrQ-wST3EU1pk"


# ===== Sheet Tab Names (구글 스프레드시트 탭 이름과 정확히 같아야 합니다) =====
CUSTOMER_SHEET_NAME        = "고객 데이터"
DAILY_SUMMARY_SHEET_NAME   = "일일결산"
DAILY_BALANCE_SHEET_NAME   = "잔액"
PLANNED_TASKS_SHEET_NAME   = "예정업무"
ACTIVE_TASKS_SHEET_NAME    = "진행업무"
COMPLETED_TASKS_SHEET_NAME = "완료업무"
EVENTS_SHEET_NAME          = "일정"
MEMO_LONG_SHEET_NAME       = "장기메모"
MEMO_MID_SHEET_NAME        = "중기메모"
MEMO_SHORT_SHEET_NAME      = "단기메모"

if platform.system() == "Windows":
    KEY_PATH = r"C:\Users\윤찬\한우리 현행업무\프로그램\출입국업무관리\hanwoory-9eaa1a4c54d7.json"
else:
    KEY_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/etc/secrets/hanwoory-9eaa1a4c54d7.json")

@st.cache_resource(ttl=600)
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    return gspread.authorize(creds)

def get_drive_service():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    return build("drive", "v3", credentials=creds)


def create_customer_folders(df_customers, worksheet=None):
    drive_svc = get_drive_service()
    parent_id = PARENT_DRIVE_FOLDER_ID

    # 1) 부모 폴더의 하위 폴더 목록(name→id) 한 번만 가져오기
    resp = drive_svc.files().list(
        q=f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder'",
        fields="files(id,name)",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True
    ).execute()
    existing = {f["name"]: f["id"] for f in resp.get("files", [])}

    # 2) 시트에 기록된 고객ID→행 번호, '폴더' 컬럼 위치 찾기
    cust_row_map = {}
    folder_col = None
    if worksheet is not None:
        rows = worksheet.get_all_values()
        hdr = rows[0]
        id_i = hdr.index("고객ID")
        folder_col = hdr.index("폴더") + 1  # update_cell 1-based
        for r, row in enumerate(rows[1:], start=2):
            cid = row[id_i].strip()
            if cid:
                cust_row_map[cid] = r

    # 3) 재매핑이 필요한 행: 비어 있거나, ID가 불일치하는 경우
    def needs_update(r):
        cid = str(r["고객ID"]).strip()
        if not cid:
            return False
        # raw 값을 strip 해서 진짜 빈값("")인지 확인
        raw = str(r.get("폴더","")).strip()
        # “https://…/folders/ID” 꼴이라면 뒤쪽 ID만, 아니면 raw 자체
        cur = raw.rsplit("/", 1)[-1] if raw else ""
        correct = existing.get(cid)
        # (1) cur가 빈 문자열이거나 (2) 이미 생성된 ID가 있는데 값이 다르면 업데이트
        return (cur == "") or (correct is not None and cur != correct)

    mask = df_customers.apply(needs_update, axis=1)

    for idx, row in df_customers[mask].iterrows():
        cid = str(row["고객ID"]).strip()
        if not cid:
            continue

        # 4) 이미 부모 폴더에 cid라는 이름의 폴더가 있으면 재사용, 없으면 생성
        if cid in existing:
            fid = existing[cid]
        else:
            fid = drive_svc.files().create(
                body={"name":cid,
                      "mimeType":"application/vnd.google-apps.folder",
                      "parents":[parent_id]},
                fields="id",
                supportsAllDrives=True
            ).execute()["id"]
            existing[cid] = fid

        # 5) DataFrame에 올바른 URL로 **수정** (폴더 컬럼)
        df_customers.at[idx, "폴더"] = fid

        # 6) 시트에도 **수정**
        if worksheet is not None and cid in cust_row_map:
            worksheet.update_cell(cust_row_map[cid], folder_col, fid)

# ✅ 2. 워크시트 객체 불러오기

def get_worksheet(client, sheet_name):
    sheet = client.open_by_key("14pEPo-Q3aFgbS1Gqcamb2lkadq-eFlOrQ-wST3EU1pk")
    return sheet.worksheet(sheet_name)

# ← 이 줄 바로 아래에 추가합니다.
def write_data_to_sheet(sheet_name: str, records: list[dict], header_list: list[str]) -> bool:
    """
    sheet_name 시트에 records 목록을 header_list 순서대로 덮어씁니다.
    기존 내용을 지우고 전체 데이터를 다시 씁니다.
    """
    try:
        client = get_gspread_client()
        worksheet = get_worksheet(client, sheet_name)
        worksheet.clear()
        rows = [header_list]
        for record in records:
            rows.append([record.get(h, "") for h in header_list])
        worksheet.update(rows)
        return True
    except Exception as e:
        st.error(f"❌ write_data_to_sheet 오류 ({sheet_name}): {e}")
        return False
# ← 여기까지
def append_rows_to_sheet(sheet_name: str, records: list[dict], header_list: list[str]) -> bool:
    """
    신규 레코드만 Google Sheet에 append 합니다.
    기존 내용은 건드리지 않고, 한 번의 API 호출로 여러 줄을 추가할 수 있습니다.
    """
    try:
        client    = get_gspread_client()
        worksheet = get_worksheet(client, sheet_name)
        # header_list 순서대로 rows 생성
        rows = [[record.get(h, "") for h in header_list] for record in records]
        worksheet.append_rows(rows)  # append만 수행
        return True
    except Exception as e:
        st.error(f"❌ append_rows_to_sheet 오류 ({sheet_name}): {e}")
        return False

def upsert_customer_from_scan(passport: dict, arc: dict):
    """
    passport: {'성','명','여권','발급','만기'}
    arc     : {'한글','등록증','번호','발급일','만기일'}
    -> 여권번호 또는 (등록증,번호) 기준으로 업데이트. 없으면 신규 생성.
    """
    client = get_gspread_client()
    ws = get_worksheet(client, CUSTOMER_SHEET_NAME)

    rows = ws.get_all_values()
    if not rows:
        return False, "고객 시트가 비어 있습니다."
    headers = rows[0]
    df = pd.DataFrame(rows[1:], columns=headers)

    def norm(s): return str(s or "").strip()

    key_passport = norm(passport.get("여권"))
    key_reg_front = norm(arc.get("등록증"))
    key_reg_back  = norm(arc.get("번호"))

    hit_idx = None
    if key_passport:
        m = df.index[df.get("여권","").astype(str).str.strip() == key_passport].tolist()
        if m: hit_idx = m[0]
    if hit_idx is None and key_reg_front and key_reg_back:
        m = df.index[
            (df.get("등록증","").astype(str).str.strip() == key_reg_front) &
            (df.get("번호","").astype(str).str.strip()   == key_reg_back)
        ].tolist()
        if m: hit_idx = m[0]

    # 갱신할 값(빈값은 덮어쓰지 않음)
    to_update = {}
    for k in ["성","명","여권","발급","만기"]:
        v = norm(passport.get(k))
        if v: to_update[k] = v
    for k in ["한글","등록증","번호","발급일","만기일","주소"]:
        v = norm(arc.get(k))
        if v: to_update[k] = v

    if hit_idx is not None:
        rownum = hit_idx + 2  # 헤더 포함
        batch = []
        for col_name, val in to_update.items():
            if col_name in headers:
                col_idx = headers.index(col_name) + 1
                cell = f"{col_index_to_letter(col_idx)}{rownum}"
                batch.append({"range": cell, "values": [[val]]})
        if batch:
            ws.batch_update(batch)

        # 세션/캐시 리프레시
        load_customer_df_from_sheet.clear()
        st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet()
        return True, f"기존 고객({df.at[hit_idx,'고객ID']}) 정보가 업데이트되었습니다."

    # 신규 생성: 고객ID = YYYYMMDD + 2자리 시퀀스
    today_str = datetime.date.today().strftime('%Y%m%d')
    col_id = df.get("고객ID", pd.Series(dtype=str)).astype(str)
    next_seq = str(col_id[col_id.str.startswith(today_str)].shape[0] + 1).zfill(2)
    new_id = today_str + next_seq

    base = {h: " " for h in headers}
    base.update({"고객ID": new_id})
    for k, v in to_update.items():
        if k in base:
            base[k] = v

    # 실제 시트 헤더 순서대로 append
    ws.append_row([base.get(h, "") for h in headers])
    # 폴더 생성/연동
    create_customer_folders(pd.DataFrame([base]), ws)

    load_customer_df_from_sheet.clear()
    st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet()
    return True, f"신규 고객이 추가되었습니다 (고객ID: {new_id})."

def get_sheet_and_titles(sheet_key):
    client = get_gspread_client()
    sheet = client.open_by_key(sheet_key)
    titles = [ws.title for ws in sheet.worksheets()]
    return sheet, titles

def load_worksheet_df(sheet, title):
    worksheet = sheet.worksheet(title)
    all_values = worksheet.get_all_values()

    if not all_values:
        return worksheet, pd.DataFrame()

    # 1) 원본 헤더를 문자열 리스트로
    raw_header = [str(h) for h in all_values[0]]

    # 2) 중복 제거 및 고유 이름 생성
    unique_header = deduplicate_headers(raw_header)

    # 3) 데이터 로우
    data_rows = all_values[1:]

    # 4) DataFrame 생성
    df = pd.DataFrame(data_rows, columns=unique_header)
    return worksheet, df

def update_changes_to_sheet(worksheet, original_df, edited_df):
    changes = []
    for i in range(len(edited_df)):
        for j, col in enumerate(edited_df.columns):
            if str(original_df.at[i, col]) != str(edited_df.at[i, col]):
                changes.append((i+2, j+1, edited_df.at[i, col]))
    for row, col, val in changes:
        worksheet.update_cell(row, col, val)
    return len(changes)

# ✅ 3. 기존 데이터프레임 불러오기

def load_original_customer_df(worksheet):
    data = worksheet.get_all_values()
    header = data[0]
    rows = data[1:]
    return pd.DataFrame(rows, columns=header)

def read_data_from_sheet(sheet_name: str, default_if_empty=None):
    client = get_gspread_client()
    worksheet = get_worksheet(client, sheet_name)
    try:
        data = worksheet.get_all_records()
        return data if data else default_if_empty
    except Exception as e:
        st.warning(f"[시트 읽기 실패] {sheet_name}: {e}")
        return default_if_empty


def read_memo_from_sheet(sheet_name):
    client = get_gspread_client()
    if client is None: return " "

    worksheet = get_worksheet(client, sheet_name)
    if worksheet:
        try:
            val = worksheet.acell('A1').value
            return val if val is not None else " "
        except Exception as e:
            st.error(f"'{sheet_name}' 시트 (메모) 읽기 중 오류 발생: {e}")
            return " "
    return " "

def save_memo_to_sheet(sheet_name, content):
    client = get_gspread_client()
    if client is None: return False
    
    worksheet = get_worksheet(client, sheet_name)
    if worksheet:
        try:
            worksheet.update_acell('A1', content)
            st.cache_data.clear() 
            return True
        except Exception as e:
            st.error(f"'{sheet_name}' 시트 (메모) 저장 중 오류 발생: {e}")
            return False
    return False



# — 도장 이미지 생성 함수 (한 번만 정의) —
def create_seal(circle_path, name, font_path, seal_size):
    base = Image.open(circle_path).convert("RGBA")
    base = base.resize((seal_size, seal_size), resample=Image.Resampling.LANCZOS)
    sample_y = int(seal_size * 0.11)
    border_color = tuple(base.getpixel((seal_size//2, sample_y))[:3])

    draw = ImageDraw.Draw(base)
    font_size = int(seal_size * 0.28)
    font = ImageFont.truetype(font_path, font_size)

    # 한/세 글자 배치
    if len(name) == 2:
        positions = {1: name[0], 3: name[1]}
    else:
        positions = {i: ch for i, ch in enumerate(name, 1)}

    spacing = seal_size / 4
    for slot in (1, 2, 3):
        ch = positions.get(slot, "")
        if not ch:
            continue
        bbox = draw.textbbox((0, 0), ch, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = seal_size/2 - w/2
        y = spacing*slot - h/2
        draw.text((x, y), ch, fill=border_color, font=font)

    return base

# — 도장 생성용 설정값 —
circle_path = "templates/원형 배경.png"
font_path   = "Fonts/HJ한전서B.ttf"
seal_size   = 200

def extract_folder_id(val: str) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    # URL 형태면 맨 끝 segment(ID)만 추출
    if "drive.google.com" in s:
        return s.rstrip("/").rsplit("/", 1)[-1]
    return s

# -----------------------------
# ✅ Application Specific Data Load/Save Functions
# -----------------------------

# --- Customer Data Functions ---
@st.cache_data(ttl=300)
def load_customer_df_from_sheet():
    # 1) 시트에서 원본 데이터 읽기
    client = get_gspread_client()
    worksheet = get_worksheet(client, CUSTOMER_SHEET_NAME)
    all_values = worksheet.get_all_values()
    if not all_values:
        return pd.DataFrame().fillna(" ")

    # 2) DataFrame 생성
    headers = all_values[0]
    records = all_values[1:]
    df = pd.DataFrame(records, columns=headers)

    # 3) 빈값 정리 (빈 문자열로)
    df = df.fillna("")

    # 4) 구글 드라이브 폴더 URL 처리
    #    • 시트의 '폴더' 칼럼에는 ID만 들어 있으므로, 전체 URL을 붙여줍니다.
    if '폴더' in df.columns:
        df['folder_url'] = df['폴더'].apply(
            lambda x: f"https://drive.google.com/drive/folders/{x.strip()}" if x and x.strip() != " " else ""
        )
    else:
        df['folder_url'] = ""

    return df

# ────────────────────────────────────────────────────────
# 아래 두 함수도 같은 파일 상단 어딘가에 정의되어 있어야 합니다.
def deduplicate_headers(headers):
    seen = {}
    result = []
    for col in headers:
        if col not in seen:
            seen[col] = 1
            result.append(col)
        else:
            seen[col] += 1
            result.append(f"{col}.{seen[col]-1}")
    return result

def col_index_to_letter(n):
    result = ''
    while n > 0:
        n, rem = divmod(n-1, 26)
        result = chr(65+rem) + result
    return result
# ────────────────────────────────────────────────────────

def save_customer_batch_update(edited_df: pd.DataFrame, worksheet) -> bool:
    """
    UI에 보이는 컬럼만 비교해서 수정/추가를 처리합니다.
    '고객ID' 컬럼은 변경 감지 대상에서 제외해야 합니다.
    """
    print("🚀 [진입] save_customer_batch_update 시작")

    # 1) 시트에서 기존 데이터 읽기
    existing_data = worksheet.get_all_values()
    raw_headers = existing_data[0]
    headers = deduplicate_headers(raw_headers)
    rows = existing_data[1:]
    existing_df = pd.DataFrame(rows, columns=headers)

    # ─── 빈셀("")을 UI 기본 빈값(" ")으로 바꿔서, 비교/업데이트 시 같은 기준으로 처리 ───
    existing_df = existing_df.applymap(lambda x: str(x).strip() or " ")

    # 2) '고객ID'를 인덱스로 설정
    if "고객ID" not in existing_df.columns:
        st.error("❌ '고객ID' 컬럼이 시트에 없습니다.")
        return False
    existing_df.set_index("고객ID", inplace=True)

    batch_updates = []
    new_rows = []
    modified_count = 0
    added_count = 0

    # 3) 에디터에 올라온 모든 컬럼을 비교 대상으로 삼되, '고객ID'만 제외
    compare_cols = [c for c in edited_df.columns if c not in ("고객ID", "폴더")]

    # 4) 각 행 검사
    for _, row in edited_df.iterrows():
        cust_id = str(row["고객ID"]).strip()
        # 시트 컬럼 순서에 맞춘 전체 row_data 준비 (비어 있으면 " "로)
        row_data = [str(row.get(h, "")).strip() or " " for h in headers]
        # ── '폴더' 칼럼이 있으면, URL 전체가 아닌 ID만 남겨둡니다.
        if "폴더" in headers:
            idx_folder = headers.index("폴더")
            raw = row_data[idx_folder]
            # “https://drive.google.com/drive/folders/ID” → “ID”
            if raw.startswith("http"):
                row_data[idx_folder] = raw.rsplit("/", 1)[-1]

        if cust_id in existing_df.index:
            orig = existing_df.loc[cust_id]

            # 변경 여부 판단 (UI 컬럼만, ID 제외)
            def norm(x): return str(x).strip()
            changed = any(norm(orig.get(h, "")) != norm(row[h])
                          for h in compare_cols)

            if changed:
                if modified_count >= 10:
                    st.error("❌ 수정 가능한 행은 최대 10개까지입니다.")
                    return False
                modified_count += 1

                # 실제 구글시트 행 번호 계산 (헤더 포함)
                base_row = existing_df.index.get_loc(cust_id) + 2
                for col_idx, val in enumerate(row_data):
                    # '폴더' 칼럼은 건드리지 않는다
                    if headers[col_idx] == "폴더":
                        continue
                    cell = f"{col_index_to_letter(col_idx+1)}{base_row}"
                    batch_updates.append({
                        "range": cell,
                        "values": [[val]]
                    })
        else:
            # 새로운 행 추가
            if added_count >= 10:
                st.error("❌ 추가 가능한 행은 최대 10개까지입니다.")
                return False
            added_count += 1
            new_rows.append(row_data)

    # 5) 구글시트에 반영
    if batch_updates:
        worksheet.batch_update(batch_updates)
    if new_rows:
        worksheet.append_rows(new_rows)

        # ▶ 신규 고객에 대해 바로 폴더 생성/연동
        create_customer_folders(edited_df, worksheet)

    st.success(f"🟢 저장 완료: 수정 {modified_count}건, 추가 {added_count}건")
    return True


# --- Event (Calendar) Data Functions ---
@st.cache_data(ttl=300) 
def load_events(): 
    records = read_data_from_sheet(EVENTS_SHEET_NAME, default_if_empty=[])
    events = {}
    if not records: # Check if records is None or empty
        return {}
    for record in records:
        date_str = record.get('date_str')
        event_text = record.get('event_text', '') 
        if date_str: 
            if date_str not in events:
                events[date_str] = []
            events[date_str].append(str(event_text)) 
    return events

def save_events(events_dict): 
    data_to_save = []
    for date_str, event_texts_list in events_dict.items():
        for text in event_texts_list:
            data_to_save.append({'date_str': str(date_str), 'event_text': str(text)})
    header = ['date_str', 'event_text']
    if write_data_to_sheet(EVENTS_SHEET_NAME, data_to_save, header_list=header):
        load_events.clear() 
        # Clear home page event data from session if it's separate
        if SESS_EVENTS_DATA_HOME in st.session_state:
            del st.session_state[SESS_EVENTS_DATA_HOME]
        return True
    return False


# --- Daily Summary & Balance Functions ---
@st.cache_data(ttl=300) 
def load_daily(): 
    records = read_data_from_sheet(DAILY_SUMMARY_SHEET_NAME, default_if_empty=[])
    processed_records = []
    for r in records:
        entry = {
            'id'          : r.get('id', str(uuid.uuid4())), # Ensure ID exists
            'date'        : str(r.get('date', '')),
            'time'        : str(r.get('time', '')),
            'category'    : str(r.get('category', '')),
            'name'        : str(r.get('name', '')),
            'task'        : str(r.get('task', '')),
            'income_cash': safe_int(r.get('income_cash')),
            'income_etc' : safe_int(r.get('income_etc')),
            'exp_cash'   : safe_int(r.get('exp_cash')),
            'exp_etc'    : safe_int(r.get('exp_etc')),
            'cash_out'   : safe_int(r.get('cash_out')), 
            'memo'        : str(r.get('memo', ''))
        }
        processed_records.append(entry)
    return processed_records

def save_daily(data_list_of_dicts): 
    header = ['id', 'date', 'time', 'category', 'name', 'task', 'income_cash', 'income_etc', 'exp_cash', 'cash_out', 'exp_etc', 'memo']
    if write_data_to_sheet(DAILY_SUMMARY_SHEET_NAME, data_list_of_dicts, header_list=header):
        load_daily.clear() # Clear cache for load_daily
        load_balance.clear() # Clear cache for load_balance as it might depend on daily data
        
        # Update SESS_ALL_DAILY_ENTRIES_PAGE_LOAD if it's in use and needs to reflect the save
        if SESS_ALL_DAILY_ENTRIES_PAGE_LOAD in st.session_state:
            st.session_state[SESS_ALL_DAILY_ENTRIES_PAGE_LOAD] = data_list_of_dicts.copy()
        return True
    return False

@st.cache_data(ttl=300) 
def load_balance(): 
    records = read_data_from_sheet(DAILY_BALANCE_SHEET_NAME, default_if_empty=[])
    balance = {'cash': 0, 'profit': 0} # Use string keys
    if not records:
        return balance
    for record in records:
        key = record.get('key')
        value_str = str(record.get('value', '0')) 
        if key in balance:
            try:
                balance[key] = int(value_str) if value_str and value_str.strip() else 0
            except ValueError:
                st.warning(f"누적요약 데이터 '{key}'의 값 '{value_str}'을 숫자로 변환할 수 없습니다. 기본값 0으로 설정됩니다.")
                balance[key] = 0  
    return balance

def save_balance(balance_dict): 
    data_to_save = []
    for key, value in balance_dict.items():
        data_to_save.append({'key': str(key), 'value': str(value)}) 
    header = ['key', 'value']
    if write_data_to_sheet(DAILY_BALANCE_SHEET_NAME, data_to_save, header_list=header):
        load_balance.clear() 
        return True
    return False

# --- Memo Functions ---
@st.cache_data(ttl=600)
def load_long_memo(): return read_memo_from_sheet(MEMO_LONG_SHEET_NAME)
def save_long_memo(content): 
    if save_memo_to_sheet(MEMO_LONG_SHEET_NAME, content):
        load_long_memo.clear()
        return True # Indicate success
    return False

@st.cache_data(ttl=600)
def load_mid_memo(): return read_memo_from_sheet(MEMO_MID_SHEET_NAME)
def save_mid_memo(content): 
    if save_memo_to_sheet(MEMO_MID_SHEET_NAME, content):
        load_mid_memo.clear()
        return True
    return False

@st.cache_data(ttl=600)
def load_short_memo(): return read_memo_from_sheet(MEMO_SHORT_SHEET_NAME)
def save_short_memo(content): 
    if save_memo_to_sheet(MEMO_SHORT_SHEET_NAME, content):
        load_short_memo.clear()
        return True
    return False


# --- Planned Task Functions ---
@st.cache_data(ttl=300)
def load_planned_tasks_from_sheet(): 
    records = read_data_from_sheet(PLANNED_TASKS_SHEET_NAME, default_if_empty=[])
    return [{
        'id': r.get('id', str(uuid.uuid4())), 
        'date': str(r.get('date','')),
        'period': str(r.get('period','')),
        'content': str(r.get('content','')),
        'note': str(r.get('note',''))
    } for r in records]

def save_planned_tasks_to_sheet(data_list_of_dicts): 
    header = ['id', 'date', 'period', 'content', 'note']
    if write_data_to_sheet(PLANNED_TASKS_SHEET_NAME, data_list_of_dicts, header_list=header):
        load_planned_tasks_from_sheet.clear()
        return True
    return False

# --- Active Task Functions ---
@st.cache_data(ttl=300)
def load_active_tasks_from_sheet(): 
    records = read_data_from_sheet(ACTIVE_TASKS_SHEET_NAME, default_if_empty=[])
    return [{
        'id': r.get('id', str(uuid.uuid4())), 
        'category': str(r.get('category','')),
        'date': str(r.get('date','')),
        'name': str(r.get('name','')),
        'work': str(r.get('work','')),
        'source_original': str(r.get('source_original', '')), # New field "원본"
        'details': str(r.get('details','')),
        'processed': r.get('processed', False) == True or str(r.get('processed', 'false')).lower() == 'true', # Ensure boolean
        'processed_timestamp': str(r.get('processed_timestamp', '')) # Store as string, parse if needed
    } for r in records]

def save_active_tasks_to_sheet(data_list_of_dicts): 
    # 헤더 정의
    header = ['id', 'category', 'date', 'name', 'work',
              'source_original', 'details', 'processed', 'processed_timestamp']
    # 전역 write_data_to_sheet 함수 호출
    success = write_data_to_sheet(ACTIVE_TASKS_SHEET_NAME, data_list_of_dicts, header)
    if success:
        load_active_tasks_from_sheet.clear()
    return success

# --- Completed Task Functions ---
@st.cache_data(ttl=300) # Added cache
def load_completed_tasks_from_sheet(): # Renamed
    records = read_data_from_sheet(COMPLETED_TASKS_SHEET_NAME, default_if_empty=[])
    # Ensure all fields are strings and have defaults
    return [{
        'id': r.get('id', str(uuid.uuid4())),
        'category': str(r.get('category', '')),
        'date': str(r.get('date', '')),
        'name': str(r.get('name', '')),
        'work': str(r.get('work', '')),
        'source_original': str(r.get('source_original', '')), # Added source_original
        'details': str(r.get('details', '')),
        'complete_date': str(r.get('complete_date', ''))
    } for r in records]


def save_completed_tasks_to_sheet(records): # Renamed
    header = ['id', 'category', 'date', 'name', 'work', 'source_original', 'details', 'complete_date']
    if write_data_to_sheet(COMPLETED_TASKS_SHEET_NAME, records, header_list=header):
        load_completed_tasks_from_sheet.clear()
        return True
    return False

# -----------------------------
# ✅ Streamlit App Logic
# -----------------------------

# --- Font Setup for Matplotlib ---
def setup_matplotlib_font():
    font_path_linux = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
    font_path_windows = "C:/Windows/Fonts/malgun.ttf" # Malgun Gothic for Windows
    font_path_macos = "/System/Library/Fonts/AppleSDGothicNeo.ttc" # Apple SD Gothic Neo for macOS

    font_path = None
    if platform.system() == "Windows":
        if os.path.exists(font_path_windows):
            font_path = font_path_windows
    elif platform.system() == "Darwin": # macOS
        if os.path.exists(font_path_macos): # Check for specific font file if known, or a common one
            font_path = font_path_macos
        else: # Fallback for macOS if specific font not found, try to find any Korean font
            try:
                font_list = fm.findSystemFonts(fontpaths=None, fontext='ttf')
                for f in font_list:
                    if 'apple sd gothic neo' in f.lower() or 'nanumgothic' in f.lower() or 'malgun' in f.lower(): # Common Korean fonts
                        font_path = f
                        break
            except:
                pass # fm.findSystemFonts might not be available or fail
    else: # Linux or other
        if os.path.exists(font_path_linux):
            font_path = font_path_linux
    
    if font_path:
        try:
            font_prop = fm.FontProperties(fname=font_path)
            plt.rcParams['font.family'] = font_prop.get_name()
            plt.rcParams['axes.unicode_minus'] = False # To handle minus sign correctly
        except Exception as e:
            st.warning(f"선택된 한국어 폰트 ({font_path}) 설정 중 오류 발생: {e}. 기본 폰트로 표시됩니다.")
    else:
        st.warning("적절한 한국어 폰트를 찾을 수 없어 그래프의 글자가 깨질 수 있습니다. (NanumGothic, Malgun Gothic, Apple SD Gothic Neo 등 설치 권장)")

if st:
    setup_matplotlib_font() # Setup font once

def search_via_server(question):
    try:
        res = requests.post(
            "https://hanwoory.onrender.com/search", 
            json={"question": question}, # Corrected JSON payload
            timeout=30
        )
        if res.status_code == 200:
            return res.json().get("answer", "답변을 받을 수 없습니다.")
        else:
            error_detail = res.text
            try: 
                error_json = res.json()
                error_detail = error_json.get("detail", res.text)
            except ValueError: # If response is not JSON
                pass
            return f"서버 오류: {res.status_code} - {error_detail}"
    except requests.exceptions.Timeout:
        return "요청 시간 초과: 서버가 응답하지 않습니다."
    except requests.exceptions.RequestException as e: 
        return f"요청 실패 (네트워크 또는 서버 문제): {str(e)}"
    except Exception as e: # Catch any other unexpected errors
        return f"요청 중 알 수 없는 오류: {str(e)}"


if st: 
    st.set_page_config(page_title="출입국 업무관리", layout="wide")

    # Initialize current_page in session state if not present
    if SESS_CURRENT_PAGE not in st.session_state:
        st.session_state[SESS_CURRENT_PAGE] = PAGE_HOME

    # Initialize other session states if needed
    if SESS_DF_CUSTOMER not in st.session_state:
        st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet()
    
    if SESS_PLANNED_TASKS_TEMP not in st.session_state:
        st.session_state[SESS_PLANNED_TASKS_TEMP] = load_planned_tasks_from_sheet() # Load initial data into temp

    if SESS_ACTIVE_TASKS_TEMP not in st.session_state:
        st.session_state[SESS_ACTIVE_TASKS_TEMP] = load_active_tasks_from_sheet() # Load initial data into temp


    st.markdown("""
    <style>
      [data-testid="stVerticalBlock"] > div { margin-bottom: 0px !important; }
      [data-testid="stColumns"] { margin-bottom: 0px !important; }
      /* Attempt to style placeholder text for Korean IME issue - often not effective */
      /* input::placeholder, textarea::placeholder { opacity: 0.7; } */
      /* Forcing font for inputs - might not solve IME composition issue */
      /* input[type="text"], textarea { font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', 'NanumGothic', sans-serif !important; } */
    </style>
    """, unsafe_allow_html=True)

    title_col, toolbar_col = st.columns([2, 3]) 
    with title_col:
        st.title("📋 출입국 업무관리")
        
    with toolbar_col:
        toolbar_options = {
            "🏠 홈으로": PAGE_HOME,
            "🗒 메모장": PAGE_MEMO,
            "📚 업무": PAGE_REFERENCE,
            "👥 고객관리": PAGE_CUSTOMER,
            "📊 결산": PAGE_DAILY, # 일일결산
            "🧭 메뉴얼 검색": PAGE_MANUAL
        }
        num_buttons = len(toolbar_options)
        btn_cols = st.columns(num_buttons)  
        for idx, (label, page_key) in enumerate(toolbar_options.items()):
            if btn_cols[idx].button(label, key=f"nav-{page_key}-{idx}", use_container_width=True):
                st.session_state[SESS_CURRENT_PAGE] = page_key
                st.rerun()
                
    st.markdown("---") 

    current_page_to_display = st.session_state[SESS_CURRENT_PAGE]

    

    # -----------------------------
    # ✅ Customer Management Page
    # -----------------------------
    if current_page_to_display == PAGE_CUSTOMER:
        # (로컬 헬퍼 – 교체본만으로도 동작하도록)
        from googleapiclient.errors import HttpError
        def extract_folder_id(val: str) -> str:
            s = str(val or "").strip()
            if not s:
                return ""
            if "drive.google.com" in s:
                return s.rstrip("/").rsplit("/", 1)[-1]
            return s

        if SESS_CUSTOMER_DATA_EDITOR_KEY not in st.session_state:
            st.session_state[SESS_CUSTOMER_DATA_EDITOR_KEY] = 0

        st.subheader("👥 고객관리")

        # --- 1) 원본 DataFrame 로드 ---
        df_customer_main = st.session_state[SESS_DF_CUSTOMER].copy()
        df_customer_main = df_customer_main.sort_values("고객ID", ascending=False).reset_index(drop=True)

        # --- 2) 컬럼 제한 ---
        cols_to_display = [
            '고객ID', '한글', '성', '명', '연', '락', '처',
            '등록증', '번호', '발급일', 'V', '만기일',
            '여권', '발급', '만기', '주소', '위임내역', '비고', '폴더'
        ]
        cols_to_display = [c for c in cols_to_display if c in df_customer_main.columns]
        df_for_ui = df_customer_main.loc[:, cols_to_display].copy()

        # folder_url 준비
        if "folder_url" not in df_customer_main.columns:
            df_customer_main["folder_url"] = ""
        df_for_ui = df_for_ui.copy()
        df_for_ui["폴더"] = df_customer_main["folder_url"]

        # “폴더 생성” 버튼
        if st.button("📂 폴더 일괄 생성/연동", use_container_width=True):
            st.info("폴더 생성 중…")
            client = get_gspread_client()
            worksheet = get_worksheet(client, CUSTOMER_SHEET_NAME)
            create_customer_folders(df_customer_main, worksheet)
            load_customer_df_from_sheet.clear()
            st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet()
            st.success("✅ 폴더 매핑이 최신화 되었습니다.")

        # --- 3) 툴바 ---
        col_add, col_scan, col_search, col_select, col_delete, col_save, col_undo = st.columns([1,1,1.5,1,1,1,1])

        with col_scan:
            if st.button("📷 스캔(여권/등록증)", use_container_width=True):
                st.session_state[SESS_CURRENT_PAGE] = PAGE_SCAN
                st.rerun()

        # 3-1) 행 추가
        with col_add:
            if st.button("➕ 행 추가", use_container_width=True):
                today_str = datetime.date.today().strftime('%Y%m%d')
                existing_ids = df_customer_main["고객ID"].astype(str)
                today_ids = existing_ids[existing_ids.str.startswith(today_str)]
                next_seq = str(len(today_ids) + 1).zfill(2)
                new_id = today_str + next_seq

                new_row = {col: " " for col in df_customer_main.columns}
                new_row["고객ID"] = new_id
                df_customer_main = pd.concat(
                    [pd.DataFrame([new_row]), df_customer_main],
                    ignore_index=True
                )
                st.session_state[SESS_DF_CUSTOMER] = df_customer_main
                st.rerun()

        # 3-2) 검색 입력창
        with col_search:
            st.text_input("🔍 검색", key=SESS_CUSTOMER_SEARCH_TERM)
            search_term = st.session_state.get(SESS_CUSTOMER_SEARCH_TERM, "")

        # 4) 검색 필터링
        df_display_full = df_for_ui.copy()
        df_for_search = df_display_full.fillna(" ").astype(str)

        if search_term:
            mask = df_for_search.apply(
                lambda row: search_term.lower() in row.str.lower().to_string(), axis=1
            )
            df_display_filtered = df_display_full[mask]
            st.session_state[SESS_CUSTOMER_SEARCH_MASK_INDICES] = df_display_full[mask].index.tolist()
        else:
            df_display_filtered = df_display_full
            st.session_state[SESS_CUSTOMER_SEARCH_MASK_INDICES] = df_display_full.index.tolist()

        # 5) 필터링된 DataFrame (원본 인덱스 유지)
        mask_indices = st.session_state.get(SESS_CUSTOMER_SEARCH_MASK_INDICES, [])
        df_display_for_editor = (
            df_customer_main.loc[mask_indices, cols_to_display]
            .reset_index(drop=True)
            .copy()
        )
        df_display_for_editor["폴더"] = (
            df_customer_main.loc[mask_indices, "folder_url"]
            .reset_index(drop=True)
            .fillna("")
        )

        # 9) 삭제 확인
        if st.session_state.get(SESS_CUSTOMER_AWAITING_DELETE_CONFIRM, False):
            st.warning("🔔 정말 삭제하시겠습니까?")
            confirm_cols = st.columns(2)
            with confirm_cols[0]:
                if st.button("✅ 예, 삭제합니다", key="confirm_delete_customer_yes"):
                    full_df = st.session_state[SESS_DF_CUSTOMER]
                    deleted_stack = st.session_state.setdefault(SESS_CUSTOMER_DELETED_ROWS_STACK, [])

                    # 구글시트 & Drive 클라이언트
                    gs_client = get_gspread_client()
                    worksheet = get_worksheet(gs_client, CUSTOMER_SHEET_NAME)
                    drive_svc = get_drive_service()

                    # 시트의 고객ID → 행 번호 맵
                    rows_all = worksheet.get_all_values()
                    if not rows_all:
                        st.error("시트가 비어 있습니다.")
                        st.stop()
                    hdr = rows_all[0]
                    try:
                        id_col_idx = hdr.index("고객ID")
                    except ValueError:
                        st.error("'고객ID' 컬럼을 시트에서 찾을 수 없습니다.")
                        st.stop()

                    id_to_sheetrow = {}
                    for r_idx, row_vals in enumerate(rows_all[1:], start=2):
                        cid_val = (row_vals[id_col_idx] or "").strip()
                        if cid_val:
                            id_to_sheetrow[cid_val] = r_idx

                    # 선택된 ID들 순회
                    deleted_count = 0
                    for del_id in st.session_state.get("PENDING_DELETE_IDS", []):
                        # 1) DF에서 해당 행 찾기
                        idx_list = full_df.index[full_df["고객ID"].astype(str).str.strip() == str(del_id).strip()].tolist()
                        if not idx_list:
                            continue
                        i = idx_list[0]

                        # 2) 폴더 ID 안전 추출
                        # 변경 코드
                        # 2) 폴더 ID 안전 추출 (폴더 컬럼이 비어있으면 folder_url에서 보조 추출)
                        folder_raw = full_df.at[i, "폴더"] if "폴더" in full_df.columns else ""
                        if (not str(folder_raw).strip()) and ("folder_url" in full_df.columns):
                            folder_raw = full_df.at[i, "folder_url"]
                        folder_id = extract_folder_id(folder_raw)

                        # 3) Drive 폴더 삭제(권한 이슈 시 휴지통으로 이동 폴백)
                        if folder_id:
                            try:
                                # 1차: 삭제 시도
                                drive_svc.files().delete(fileId=folder_id, supportsAllDrives=True).execute()
                            except HttpError as e:
                                code = getattr(e, "resp", None).status if hasattr(e, "resp") else None
                                if code == 404:
                                    # 이미 없어졌으면 통과
                                    st.info(f"폴더(ID={folder_id})는 이미 삭제되었습니다.")
                                elif code == 403:
                                    # 권한 부족 → 휴지통으로 이동(업데이트) 폴백
                                    try:
                                        drive_svc.files().update(
                                            fileId=folder_id,
                                            body={"trashed": True},
                                            supportsAllDrives=True
                                        ).execute()
                                        st.info(f"폴더(ID={folder_id})를 휴지통으로 이동했습니다.")
                                    except HttpError as e2:
                                        st.warning(f"폴더 삭제/휴지통 이동 실패(ID={folder_id}): {e2}")
                                else:
                                    st.warning(f"폴더 삭제 중 오류(ID={folder_id}): {e}")

                        # 4) 시트 행 삭제(정확한 행 번호)
                        sheet_row = id_to_sheetrow.get(str(del_id).strip())

                        if sheet_row:
                            try:
                                worksheet.delete_rows(sheet_row)
                            except Exception as e:
                                st.warning(f"시트 행 삭제 중 오류(ID={del_id}, row={sheet_row}): {e}")
                            # 맵 재생성 (행 당김 반영)
                            rows_all = worksheet.get_all_values()
                            id_to_sheetrow = {}
                            if rows_all:
                                hdr2 = rows_all[0]
                                if "고객ID" in hdr2:
                                    id_col_idx2 = hdr2.index("고객ID")
                                    for r_idx2, row_vals2 in enumerate(rows_all[1:], start=2):
                                        cid2 = (row_vals2[id_col_idx2] or "").strip()
                                        if cid2:
                                            id_to_sheetrow[cid2] = r_idx2

                        # 5) 로컬 DF에서도 제거 + Undo 스택에 보관
                        deleted_stack.append((i, full_df.loc[i].copy()))
                        full_df = full_df.drop(index=i)
                        deleted_count += 1

                    # 6) 인덱스 재정렬 및 세션 반영
                    full_df = full_df.sort_values("고객ID", ascending=False).reset_index(drop=True)
                    st.session_state[SESS_DF_CUSTOMER] = full_df

                    st.success(f"✅ {deleted_count}개의 행이 삭제되었습니다.")
                    st.session_state[SESS_CUSTOMER_AWAITING_DELETE_CONFIRM] = False
                    st.session_state.pop("PENDING_DELETE_IDS", None)
                    st.rerun()

            with confirm_cols[1]:
                if st.button("❌ 아니오, 취소합니다", key="cancel_delete_customer_no"):
                    st.session_state[SESS_CUSTOMER_AWAITING_DELETE_CONFIRM] = False
                    st.session_state.pop("PENDING_DELETE_IDS", None)
                    st.info("삭제가 취소되었습니다.")
                    st.rerun()

        editor_key = st.session_state.get(SESS_CUSTOMER_DATA_EDITOR_KEY, 0)
        edited_df_display = st.data_editor(
            df_display_for_editor.fillna(" "),
            height=600,
            use_container_width=True,
            num_rows="dynamic",
            key=f"data_editor_customer_{editor_key}",
            disabled=["고객ID"],
            column_config={
                "폴더": st.column_config.LinkColumn(
                    "폴더",
                    help="클릭하면 구글 드라이브 폴더가 새 탭에서 열립니다."
                )
            }
        )

        # 6) 삭제할 고객ID 선택
        with col_select:
            options = df_display_for_editor["고객ID"].tolist()
            selected_delete_ids = st.multiselect(
                "삭제할 고객ID 선택",
                options=options,
                key="customer_delete_ids",
                disabled=not options
            )

        # 7) 삭제 요청 버튼
        with col_delete:
            if st.button("🗑️ 삭제 요청", use_container_width=True, disabled=not selected_delete_ids):
                st.session_state["PENDING_DELETE_IDS"] = selected_delete_ids
                st.session_state[SESS_CUSTOMER_AWAITING_DELETE_CONFIRM] = True
                st.rerun()

        # 8) 삭제 취소 버튼
        with col_undo:
            if st.button("↩️ 삭제 취소 (Undo)", use_container_width=True):
                if SESS_CUSTOMER_DELETED_ROWS_STACK in st.session_state and st.session_state[SESS_CUSTOMER_DELETED_ROWS_STACK]:
                    original_idx, row_data_series = st.session_state[SESS_CUSTOMER_DELETED_ROWS_STACK].pop()
                    current_df = st.session_state[SESS_DF_CUSTOMER]

                    part1 = current_df.iloc[:original_idx]
                    row_to_insert_df = pd.DataFrame([row_data_series])
                    row_to_insert_df = row_to_insert_df.reindex(columns=current_df.columns, fill_value=" ")
                    part2 = current_df.iloc[original_idx:]
                    restored_df = pd.concat([part1, row_to_insert_df, part2]).reset_index(drop=True)

                    st.session_state[SESS_DF_CUSTOMER] = restored_df
                    st.success(f"{original_idx}번 행 (원본 기준)이 복구되었습니다. 저장하려면 💾 저장 버튼을 눌러주세요.")
                    st.rerun()

        # 11) 저장
        with col_save:
            if st.button("💾 저장", use_container_width=True):
                st.info("⏳ 저장 중입니다... 잠시만 기다려 주세요.")
                client = get_gspread_client()
                worksheet = get_worksheet(client, CUSTOMER_SHEET_NAME)

                # 1) 시트에 없던 신규 행만 append
                original = load_customer_df_from_sheet()
                orig_ids = set(original["고객ID"].astype(str))
                new_rows = []
                for _, row in edited_df_display.iterrows():
                    cid = str(row["고객ID"]).strip()
                    if cid not in orig_ids:
                        new_rows.append({h: row.get(h, "") for h in original.columns})

                if new_rows and append_rows_to_sheet(CUSTOMER_SHEET_NAME, new_rows, list(original.columns)):
                    st.success(f"✅ 신규 {len(new_rows)}건이 추가되었습니다.")

                    # 2) fresh_df로 폴더 생성/연동
                    load_customer_df_from_sheet.clear()
                    fresh_df = load_customer_df_from_sheet()
                    st.info("📂 신규 고객 폴더 생성 중…")
                    create_customer_folders(fresh_df, worksheet)
                    st.success("✅ 신규 고객 폴더가 생성/연동되었습니다.")
                    st.session_state[SESS_DF_CUSTOMER] = fresh_df

                # 4) 기존 행 변경사항 batch update
                ok = save_customer_batch_update(edited_df_display, worksheet)
                if ok:
                    st.success("🔄 업데이트가 반영되었습니다.")

                # 5) 최종 리프레시
                load_customer_df_from_sheet.clear()
                st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet()
                st.session_state[SESS_CUSTOMER_DATA_EDITOR_KEY] += 1
                st.rerun()

    # -----------------------------
    # ✅ Daily Summary Page
    # -----------------------------
    elif current_page_to_display == PAGE_DAILY:
        col_left, col_right = st.columns([8,1])
        with col_right:
            if st.button("📅 월간결산", use_container_width=True):
                st.session_state["current_page"] = "monthly"
                st.rerun()

        data = load_daily() 
        balance = load_balance()

        # Active Tasks와 동일한 구분 옵션
        구분_옵션 = ["출입국", "전자민원", "공증", "여권", "초청", "영주권", "기타"]

        # -------------------
        # 날짜 선택: Streamlit 기본 달력
        # -------------------
        if "daily_selected_date" not in st.session_state:
            st.session_state["daily_selected_date"] = datetime.date.today()

        # 달력 위젯
        선택날짜 = st.date_input(
            "날짜 선택",
            value=st.session_state["daily_selected_date"],
            key="daily_date_input"
        )

        # 날짜가 바뀌면 다시 렌더링
        if 선택날짜 != st.session_state["daily_selected_date"]:
            st.session_state["daily_selected_date"] = 선택날짜
            st.rerun()

        # 문자열 포맷
        선택날짜_문자열 = 선택날짜.strftime("%Y-%m-%d")
        선택날짜_표시     = 선택날짜.strftime("%Y년 %m월 %d일")

        st.subheader(f"📊 일일결산: {선택날짜_표시}")

        선택날짜_문자열 = 선택날짜.strftime("%Y-%m-%d")
        선택날짜_표시 = 선택날짜.strftime("%Y년 %m월 %d일")
        이번달_str = 선택날짜.strftime("%Y-%m") 

        오늘_데이터 = [row for row in data if row.get("date") == 선택날짜_문자열]
        오늘_데이터.sort(key=lambda x: x.get('time', '00:00:00')) 

        if not 오늘_데이터:
            st.info("선택한 날짜에 등록된 내역이 없습니다.")

        for idx, row_data in enumerate(오늘_데이터): 
            cols = st.columns([0.8, 0.8, 1, 2, 1, 1, 1, 1, 1, 1, 0.7])
            new_time = cols[0].text_input(
                "시간", value=row_data.get("time"," "), key=f"time_disp_{idx}", label_visibility="collapsed"
            )
            prev_category = row_data.get("category","")
            new_category  = cols[1].selectbox(
                "구분", ["현금출금"]+구분_옵션,
                index=(["현금출금"]+구분_옵션).index(prev_category) if prev_category in 구분_옵션 or prev_category=="현금출금" else 0,
                key=f"daily_category_{idx}", label_visibility="collapsed"
            )
            new_name = cols[2].text_input(
                "성명", value=row_data.get("name"," "), key=f"name_{idx}", label_visibility="collapsed"
            )
            new_task = cols[3].text_input(
                "업무", value=row_data.get("task"," "), key=f"task_{idx}", label_visibility="collapsed"
            )
            cols[4].number_input("현금입금", value=row_data.get("income_cash", 0), key=f"inc_cash_{idx}", format="%d", label_visibility="collapsed", help="현금입금")
            cols[5].number_input("현금지출", value=row_data.get("exp_cash", 0), key=f"exp_cash_{idx}", format="%d", label_visibility="collapsed", help="현금지출")
            cols[6].number_input("현금출금", value=row_data.get("cash_out", 0), key=f"cash_out_{idx}", format="%d", label_visibility="collapsed", help="현금출금(개인)")
            cols[7].number_input("기타입금", value=row_data.get("income_etc", 0), key=f"inc_etc_{idx}", format="%d", label_visibility="collapsed", help="기타입금")
            cols[8].number_input("기타지출", value=row_data.get("exp_etc", 0), key=f"exp_etc_{idx}", format="%d", label_visibility="collapsed", help="기타지출")
            cols[9].text_input("비고", value=row_data.get("memo", " "), key=f"memo_{idx}", label_visibility="collapsed", placeholder="비고")

            action_cols_daily = cols[10].columns(2)

            # --- 2-1) 수정 버튼(✏️) 클릭 시: 즉시 저장 로직
            if action_cols_daily[0].button("✏️", key=f"edit_daily_{idx}"):
                new_time = st.session_state.get(f"time_disp_{idx}", row_data.get("time"," "))
                new_name = st.session_state.get(f"name_{idx}", " ")
                new_task = st.session_state.get(f"task_{idx}", " ")
                new_category  = st.session_state.get(f"daily_category_{idx}", "")
                new_inc_cash   = st.session_state.get(f"inc_cash_{idx}", 0)
                new_exp_cash   = st.session_state.get(f"exp_cash_{idx}", 0)
                new_cash_out   = st.session_state.get(f"cash_out_{idx}", 0)
                new_inc_etc    = st.session_state.get(f"inc_etc_{idx}", 0)
                new_exp_etc    = st.session_state.get(f"exp_etc_{idx}", 0)
                new_memo       = st.session_state.get(f"memo_{idx}", " ")

                original_id = 오늘_데이터[idx]["id"]

                for i, row in enumerate(data):
                    if row.get("id") == original_id:
                        data[i]["time"]       = new_time
                        data[i]["name"]       = new_name
                        data[i]["task"]       = new_task
                        # daily summary sheet에는 저장하지 않지만, 메모리상 카테고리 보관
                        data[i]["category"]   = new_category
                        data[i]["income_cash"]= new_inc_cash
                        data[i]["exp_cash"]   = new_exp_cash
                        data[i]["cash_out"]   = new_cash_out
                        data[i]["income_etc"] = new_inc_etc
                        data[i]["exp_etc"]    = new_exp_etc
                        data[i]["memo"]       = new_memo
                        break
        
                # 3) Google Sheet에 즉시 저장
                save_daily(data)
        
                st.success(f"{idx+1}번째 행이 저장되었습니다.")
                st.rerun()  # 화면을 갱신하여 변경된 내용을 반영
        
            # --- 2-2) 삭제 버튼(🗑️) 클릭 시: 기존 로직 유지
            if action_cols_daily[1].button("🗑️", key=f"delete_daily_{idx}", help="삭제"):
                original_row_id = row_data.get("id")
                # 해당 ID가 포함되지 않은 새 리스트로 갱신
                data = [d for d in data if d.get("id") != original_row_id]
                save_daily(data)
                st.success("삭제되었습니다.")
                st.rerun()
        
        st.markdown("#### 새 내역 추가")
        with st.form("add_daily_form", clear_on_submit=True):
            # 0: 구분, 1: 성명, 2: 업무, 나머지는 기존대로
            form_cols = st.columns([1,1.5,2,1,1,1,1,1,1.5,0.5])
            add_category = form_cols[0].selectbox("구분", ["현금출금"] + 구분_옵션, key="add_daily_category", label_visibility="collapsed")
            add_name = form_cols[1].text_input("성명", key="add_daily_name", label_visibility="collapsed")
            add_task = form_cols[2].text_input("업무", key="add_daily_task", label_visibility="collapsed")
            add_income_cash= form_cols[3].number_input("현금입금", value=0, key="add_daily_inc_cash_old", format="%d")
            add_exp_cash   = form_cols[4].number_input("현금지출", value=0, key="add_daily_exp_cash_old", format="%d")
            add_cash_out   = form_cols[5].number_input("현금출금", value=0, key="add_daily_cash_out_old", format="%d") 
            add_income_etc = form_cols[6].number_input("기타입금", value=0, key="add_daily_inc_etc_old", format="%d")
            add_exp_etc    = form_cols[7].number_input("기타지출", value=0, key="add_daily_exp_etc_old", format="%d")
            add_memo       = form_cols[8].text_input("비고", key="add_daily_memo_old")
        
            submitted = form_cols[9].form_submit_button("➕ 추가")
            if submitted:
                if not add_name and not add_task:
                    st.warning("이름 또는 업무 내용을 입력해주세요.")
                else:
                    new_entry_row = { 
                        "id": str(uuid.uuid4()),
                        "date": 선택날짜_문자열,
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        "category": add_category,
                        "name": add_name,
                        "task": add_task,
                        "income_cash": add_income_cash,
                        "income_etc": add_income_etc,
                        "exp_cash": add_exp_cash,
                        "cash_out": add_cash_out,
                        "exp_etc": add_exp_etc,
                        "memo": add_memo
                    }
                    data.append(new_entry_row)
                    save_daily(data)
                    # — 여기서 Active Tasks에도 동기화 —
                    # ‘현금출금’ 이 아니면 Active Tasks에도 동기화
                    if add_category != "현금출금":
                        new_active = {
                            "id": str(uuid.uuid4()),
                            "category": add_category,
                            "date": 선택날짜_문자열,
                            "name": add_name,
                            "work": add_task,
                            "source_original": "",
                            "details": "",
                            "processed": False,
                            "processed_timestamp": ""
                        }
                        st.session_state[SESS_ACTIVE_TASKS_TEMP].append(new_active)
                        save_active_tasks_to_sheet(st.session_state[SESS_ACTIVE_TASKS_TEMP])
                    st.success(f"{선택날짜_표시}에 새 내역이 추가되었습니다.")
                    st.rerun()

        # — 오늘(선택일) 수익·지출 세부 집계 —
        오늘데이터      = 오늘_데이터  # 이미 필터된 리스트 사용
        오늘_현금입금   = sum(r.get("income_cash", 0) for r in 오늘데이터)
        오늘_기타입금   = sum(r.get("income_etc", 0)  for r in 오늘데이터)
        오늘_현금지출   = sum(r.get("exp_cash", 0)    for r in 오늘데이터)
        오늘_기타지출   = sum(r.get("exp_etc", 0)     for r in 오늘데이터)

        오늘_총입금     = 오늘_현금입금 + 오늘_기타입금
        오늘_총지출     = 오늘_현금지출 + 오늘_기타지출
        오늘_순수익     = 오늘_총입금   - 오늘_총지출

       # ─── 사무실현금 누적 계산 ───
       # data 리스트 전체를 날짜순으로 정렬한 뒤
        사무실현금_누적 = 0
        all_data_sorted_for_cash = sorted(data, key=lambda x: (x.get('date',''), x.get('time','00:00:00')))
        for r_calc in all_data_sorted_for_cash:
            # 선택일 이후 기록은 반영하지 않음
            if r_calc.get('date','') > 선택날짜_문자열:
                break
            # 현금입금은 더하고, 현금지출·현금출금은 뺍니다
            사무실현금_누적 += r_calc.get('income_cash', 0)
            사무실현금_누적 -= r_calc.get('exp_cash',   0)
            사무실현금_누적 -= r_calc.get('cash_out',   0)
        
        st.markdown("---")
        st.markdown("#### 요약 정보")
        # — 이번 달(선택일까지) 수익·지출 세부 집계 —
        이번달_데이터  = [
            r for r in data
            if r.get("date","").startswith(이번달_str)
               and r.get("date","") <= 선택날짜_문자열
        ]
        월_현금입금     = sum(r.get("income_cash", 0) for r in 이번달_데이터)
        월_기타입금     = sum(r.get("income_etc", 0)  for r in 이번달_데이터)
        월_현금지출     = sum(r.get("exp_cash", 0)    for r in 이번달_데이터)
        월_기타지출     = sum(r.get("exp_etc", 0)     for r in 이번달_데이터)

        월_총입금       = 월_현금입금 + 월_기타입금
        월_총지출       = 월_현금지출 + 월_기타지출
        월_순수익       = 월_총입금   - 월_총지출
        balance['profit'] = 월_순수익
        save_balance(balance)  # 👉 이 함수가 없다면 만들거나 임시 저장 생략 가능

        # — 화면에 출력 —
        sum_col1, sum_col2 = st.columns(2)

        with sum_col1:
            st.write(f"📅 {선택날짜.month}월 요약")
            st.write(f"• 총 입금: {월_총입금:,} 원")
            st.write(f"- 현금: {월_현금입금:,} 원")
            st.write(f"- 기타: {월_기타입금:,} 원")
            st.write(f"• 총 지출: {월_총지출:,} 원")
            st.write(f"- 현금: {월_현금지출:,} 원")
            st.write(f"- 기타: {월_기타지출:,} 원")
            st.write(f"• 순수익: {월_순수익:,} 원")

            D = 선택날짜.day
            profits = []
            for m in (1, 2, 3):
                # prev = 선택날짜 - relativedelta(months=m)  # 이전 relativedelta 버전
                prev_ts = pd.to_datetime(선택날짜) - pd.DateOffset(months=m)
                prev = prev_ts.date()

                y, mo = prev.year, prev.month
                total = 0
                for d in range(1, D+1):
                    date_str = f"{y}-{mo:02d}-{d:02d}"
                    total += sum(
                        r.get("income_cash", 0)
                        + r.get("income_etc",  0)
                        - r.get("exp_cash",    0)
                        - r.get("exp_etc",     0)
                        for r in data
                        if r.get("date") == date_str
                    )
                profits.append(total)

            avg_profit = sum(profits) // 3 if profits else 0
            st.write(f"(지난 3개월 같은날 평균 순수익 : {avg_profit:,} 원)")

        with sum_col2:
            st.write(f"📅 오늘({선택날짜.day}일) 요약")
            st.write(f"• 총 입금: {오늘_총입금:,} 원")
            st.write(f"- 현금: {오늘_현금입금:,} 원")
            st.write(f"- 기타: {오늘_기타입금:,} 원")
            st.write(f"• 총 지출: {오늘_총지출:,} 원")
            st.write(f"- 현금: {오늘_현금지출:,} 원")
            st.write(f"- 기타: {오늘_기타지출:,} 원")
            st.write(f"• 순수익: {오늘_순수익:,} 원")
            st.write(f"💰 현재 사무실 현금: {int(사무실현금_누적):,} 원")
        st.caption(f"* '{선택날짜.strftime('%Y년 %m월')}' 전체 순수익은 '{balance['profit']:,}' 원 입니다 (Google Sheet '잔액' 기준).")


    # -----------------------------
    # ✅ Monthly Summary Page
    # -----------------------------
    elif current_page_to_display == PAGE_MONTHLY:
        st.subheader("📅 월간결산")

        # 1) 구글 시트 전체 일일결산 데이터 로드
        all_daily = load_daily()
        df = pd.DataFrame(all_daily)
        # 날짜 타입 변환
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date'])

        # 2) ‘수익’·‘매출’ 컬럼 추가
        df['수익'] = (
            df['income_cash'].fillna(0) +
            df['income_etc'].fillna(0) -
            df['exp_cash'].fillna(0) -
            df['exp_etc'].fillna(0)
        )
        df['매출'] = (
            df['income_cash'].fillna(0) +
            df['income_etc'].fillna(0)
        )

        # 3) 월 단위 키(YYYY-MM) 생성
        df['month'] = df['date'].dt.to_period('M').astype(str)

        # 4) 월별 집계 테이블 준비
        monthly_summary = df.groupby('month').agg(
            현금입금=('income_cash','sum'),
            기타입금=('income_etc','sum'),
            현금지출=('exp_cash','sum'),
            기타지출=('exp_etc','sum'),
            매출=('매출','sum'),
            순수익=('수익','sum'),
        ).reset_index().sort_values('month')

        # 5) 분석할 월 선택박스 (기본: 가장 최근 달)
        months = monthly_summary['month'].tolist()
        selected_month = st.selectbox(
            "🔎 분석할 월 선택",
            options=months,
            index=len(months)-1,
            format_func=lambda x: x.replace('-', '년 ') + '월'
        )

        # 6) 선택된 월 데이터만 필터
        df_sel = df[df['month'] == selected_month]

        # 7) 전체 월 요약 테이블 출력
        st.markdown("### 📊 월별 요약")
        st.dataframe(
            monthly_summary.rename(columns={'month':'월'}).style.format({
                "현금입금": "{:,} 원", "기타입금": "{:,} 원",
                "현금지출": "{:,} 원", "기타지출": "{:,} 원",
                "매출":     "{:,} 원", "순수익":   "{:,} 원"
            }),
            use_container_width=True,
            hide_index=True
        )

        # 8) 월별 순수익 추이 (라인 차트)
        fig1, ax1 = plt.subplots(figsize=(10, 4))
        ax1.plot(
            monthly_summary['month'], 
            monthly_summary['순수익'], 
            marker='o', linewidth=2
        )
        ax1.set_title("월별 순수익 추이", fontsize=14)
        ax1.set_xlabel("월", fontsize=12)
        ax1.set_ylabel("순수익 (원)", fontsize=12)
        ax1.grid(True, linestyle='--', alpha=0.5)
        ax1.tick_params(axis='x', rotation=45)
        st.pyplot(fig1)

        # 9) 선택월 요일별 순수익 (바 차트)
        # 요일 순서 고정
        order_en = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
        order_ko = ['월','화','수','목','금','토','일']
        df_sel['weekday'] = df_sel['date'].dt.day_name()
        weekday_sum = df_sel.groupby('weekday')['수익'].sum().reindex(order_en).fillna(0)

        fig2, ax2 = plt.subplots(figsize=(8, 4))
        bars = ax2.bar(order_ko, weekday_sum.values)
        ax2.set_title(f"{selected_month.replace('-', '년 ')}월 요일별 순수익", fontsize=14)
        ax2.set_xlabel("요일", fontsize=12)
        ax2.set_ylabel("순수익 (원)", fontsize=12)
        ax2.grid(axis='y', linestyle='--', alpha=0.5)
        for bar in bars:
            h = bar.get_height()
            ax2.text(
                bar.get_x() + bar.get_width()/2, 
                h * 1.01, 
                f"{int(h):,}", 
                ha='center', va='bottom', fontsize=9
            )
        st.pyplot(fig2)

        # 업무 분류 함수(필요시 수정)
        # daily 페이지의 'category' 값을 분류용 4개 카테고리로 매핑
        mapping = {
            '출입국':    '출입국',
            '등록':      '출입국',
            '연장':      '출입국',
            '변경':      '출입국',
            '전자민원':  '전자민원',
            '공증':      '공증',
            '영주권':    '영주',
            # 기타 모든 값은 '기타'로
        }

        # 2) 매핑 적용
        df_sel['class_cat'] = (
            df_sel['category']
            .fillna('기타')                # NaN → 기타
            .apply(lambda x: mapping.get(x, '기타'))
        )

        # 3) 집계용 카테고리 순서 지정
        categories = ['출입국','전자민원','공증','영주','기타']

        # 4) 순수익 집계
        task_sum = (
            df_sel
            .groupby('class_cat')['수익']
            .sum()
            .reindex(categories, fill_value=0)
        )

        # 5) 차트 그리기 (이전과 동일한 스타일)
        fig3, ax3 = plt.subplots(figsize=(8, 4))
        bars3 = ax3.bar(task_sum.index, task_sum.values)
        ax3.set_title(f"{selected_month.replace('-', '년 ')}월 업무별 순수익", fontsize=14)
        ax3.set_xlabel("업무 분류", fontsize=12)
        ax3.set_ylabel("순수익 (원)", fontsize=12)
        ax3.grid(axis='y', linestyle='--', alpha=0.5)
        for bar in bars3:
            h = bar.get_height()
            ax3.text(
                bar.get_x() + bar.get_width()/2, 
                h * 1.01, 
                f"{int(h):,}", 
                ha='center', va='bottom', fontsize=9
            )
        st.pyplot(fig3)

        # 11) 선택월 시간대별 매출 (바 차트)
        def classify_time(t):
            try:
                h = int(str(t).split(':')[0])
                if h < 11:               
                    return '오전 (00-11시)'
                elif h < 14:
                    return '점심 (11-14시)'
                elif h < 18:
                    return '오후 (14-18시)'
                else:
                    return '저녁 (18-24시)'
            except:
                return '시간정보없음'

        # 반드시 df_sel 사용, 그리고 '수익' 컬럼으로 집계
        df_sel['time_group'] = df_sel['time'].apply(classify_time)
        time_order = ['오전 (00-11시)','점심 (11-14시)','오후 (14-18시)','저녁 (18-24시)','시간정보없음']
        time_profit = df_sel.groupby('time_group')['수익'].sum().reindex(time_order).fillna(0)

        fig4, ax4 = plt.subplots(figsize=(8, 4))
        bars4 = ax4.bar(time_order, time_profit.values)
        ax4.set_title(f"{selected_month.replace('-', '년 ')}월 시간대별 순수익", fontsize=14)
        ax4.set_xlabel("시간대", fontsize=12)
        ax4.set_ylabel("순수익 (원)", fontsize=12)
        ax4.grid(axis='y', linestyle='--', alpha=0.5)
        ax4.tick_params(axis='x', rotation=45)
        for bar, val in zip(bars4, time_profit.values):
            ax4.text(
                bar.get_x() + bar.get_width()/2,
                val * 1.01,
                f"{int(val):,}",
                ha='center', va='bottom', fontsize=9
            )
        st.pyplot(fig4)

    # -----------------------------
    # ✅ Scan Page (여권/등록증 OCR → 고객 자동 추가/수정)
    # -----------------------------
    elif current_page_to_display == PAGE_SCAN:
        st.subheader("📷 스캔으로 고객 추가/수정")
        st.caption("여권 1장만 또는 여권+등록증 2장을 업로드하세요.")
        show_debug = st.checkbox("OCR 디버그 보기(느려짐)", value=False)

        # Tesseract 점검
        if not _ensure_tesseract():
            st.error("pytesseract가 감지되지 않았습니다. `Tesseract-OCR` 설치 및 환경설정을 확인하세요.")
            st.stop()

        # --- OCR 유틸 ---
        # ====== OCR DROP-IN (PAGE_SCAN) START ======
        # ── 속도/옵션 ─────────────────────────────────────────────
        ARC_REMOVE_PAREN = True   # 주소에서 (신길동) 같은 괄호표기 제거
        ARC_FAST_ONLY    = True   # 빠른 모드(필요 최소 조합만 시도)

        # ── 필요한 모듈 ───────────────────────────────────────────
        import re
        from datetime import datetime as _dt, timedelta as _td
        from PIL import ImageOps, ImageFilter, ImageStat

        # ── 공용 OCR 유틸 (가벼움) ────────────────────────────────
        def _pre(img):
            g = ImageOps.grayscale(img)
            w, h = g.size
            if w < 1400:
                r = 1400 / float(w)
                g = g.resize((int(w*r), int(h*r)))
            g = ImageOps.autocontrast(g)
            g = g.filter(ImageFilter.SHARPEN)
            return g

        def _binarize_soft(img):
            g = ImageOps.grayscale(img)
            w, h = g.size
            if w < 1600:
                r = 1600 / float(w)
                g = g.resize((int(w*r), int(h*r)), resample=ImageFilter.BILINEAR)
            m = ImageStat.Stat(g).mean[0]
            thr = int(max(100, min(200, m*0.9)))
            return g.point(lambda p: 255 if p > thr else 0)

        # 숫자 보정(0/O, 1/I, 5/S 등)
        _DIGIT_FIX = str.maketrans({
            'O':'0','o':'0','D':'0','Q':'0',
            'I':'1','l':'1','|':'1','!':'1',
            'Z':'2',
            'S':'5','s':'5',
            'B':'8',
            'g':'9','q':'9'
        })
        def _digits_only(s: str) -> str:
            return re.sub(r'[^0-9]', '', (s or '').translate(_DIGIT_FIX))

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
            if not re.fullmatch(r'\d{6}', _digits_only(L2[13:19])): return False # 생년
            if not re.fullmatch(r'\d{6}', _digits_only(L2[21:27])): return False # 만기
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

            b = _digits_only(L2[13:19])
            if len(b) == 6:
                yy, mm, dd = int(b[:2]), int(b[2:4]), int(b[4:6])
                yy += 2000 if yy < 80 else 1900
                try: out['생년월일'] = _dt(yy,mm,dd).strftime('%Y-%m-%d')
                except: pass

            sx = L2[20:21]
            out['성별'] = '남' if sx == 'M' else ('여' if sx == 'F' else '')

            e = _digits_only(L2[21:27])
            if len(e) == 6:
                yy, mm, dd = int(e[:2]), int(e[2:4]), int(e[4:6])
                yy += 2000 if yy < 80 else 1900
                try: out['만기'] = _dt(yy,mm,dd).strftime('%Y-%m-%d')
                except: pass

            # 발급일: 만기에서 역산(+1일). (기본 10년, 중국 미성년 5년)
            if out.get('만기'):
                try:
                    exp = _dt.strptime(out['만기'], '%Y-%m-%d').date()
                    validity_years = 10
                    if out.get('국가') == 'CHN' and out.get('생년월일'):
                        birth = _dt.strptime(out['생년월일'], '%Y-%m-%d').date()
                        age_at_expiry = (exp - birth).days // 365
                        validity_years = 10 if age_at_expiry >= 21 else 5
                    issued = _minus_years(exp, validity_years) + _td(days=1)
                    out['발급'] = issued.strftime('%Y-%m-%d')
                except:
                    pass
            return out

        def parse_passport(img):
            """
            TD3 여권: 하단 40%에서 MRZ 2줄만 빠르게 인식하여 반환
            {'성','명','여권','발급','만기','생년월일'}
            """
            if img is None: return {}
            w, h = img.size
            band = img.crop((0, int(h*0.58), w, h))
            texts = []
            for pre in (_binarize_soft, _pre, lambda x: x):
                try:
                    im = pre(band)
                except Exception:
                    im = band
                t7 = _ocr(im, lang='ocrb+eng',
                          config='--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ<0123456789')
                t6 = _ocr(im, lang='ocrb+eng',
                          config='--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ<0123456789')
                texts.append((t7 or '') + '\n' + (t6 or ''))
            joined = '\n'.join([t for t in texts if t])
            L1, L2 = find_mrz_pair_from_text(joined)
            if not L1 or not L2:
                return {}
            out = _parse_mrz_pair(L1, L2)
            return {
                '성': out.get('성',''),
                '명': out.get('명',''),
                '여권': out.get('여권',''),
                '발급': out.get('발급',''),
                '만기': out.get('만기',''),
                '생년월일': out.get('생년월일','')
            }

        # ── 등록증(ARC) 보조 ──────────────────────────────────────
        _ADDR_BAN_RE = re.compile(
            r'(유효|취업|가능|확인|민원|국번없이|콜센터|call\s*center|www|http|1345|출입국|immigration|안내|관할|관계자|외|금지)',
            re.I
        )
        _NAME_BAN = {'국내','거소','신고','증','외국','국적','재외','동포','사무소','대한','민국','주소','발급','만기','체류','자격','종류','주민','등록','국내거소신고증'}

        def _kor_count(s: str) -> int:
            return len(re.findall(r'[가-힣]', s or ''))

        def _clean_addr_line(s: str, remove_paren=True) -> str:
            if not s: return ''
            # 맨 앞 날짜 제거 (YYYY.MM.DD / YYYY-MM-DD / YYYY/MM/DD)
            s = re.sub(r'^\s*\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\s*', '', s)
            # 영문자/특수기호 과다 제거 → 한글/숫자/일부기호만 유지
            s = re.sub(r'[^가-힣0-9\s\-\.,#()/~]', ' ', s)
            if remove_paren:
                s = re.sub(r'\([^)]*\)', ' ', s)   # (신길동) 제거
            s = re.sub(r'\s{2,}', ' ', s).strip(' ,')
            return s

        def _is_junk_addr_line(s: str) -> bool:
            s = (s or '').strip()
            if not s: return True
            if _ADDR_BAN_RE.search(s): return True
            # 한글이 거의 없고 숫자/기호 과다
            if _kor_count(s) < 3 and len(re.sub(r'[^\d]', '', s)) >= 6: return True
            # 괄호/기호/점만
            if re.fullmatch(r'[\(\)\.\-/#\s]+', s): return True
            return False

        def _addr_score(s: str) -> float:
            s = _clean_addr_line(s, remove_paren=ARC_REMOVE_PAREN)
            if _is_junk_addr_line(s): return -1.0
            has_lvl  = bool(re.search(r'(도|시|군|구)', s))
            has_road = bool(re.search(r'(로|길|번길|대로)', s))
            has_num  = bool(re.search(r'\d', s))
            has_unit = bool(re.search(r'(동|호|층|호수|#\d+)', s))
            return _kor_count(s)*2 + has_lvl*6 + has_road*8 + has_num*4 + has_unit*2 + min(len(s), 60)/12.0

        def _extract_kor_name_strict(text: str) -> str:
            m = re.search(r'(성명|이름)\s*[:\-]?\s*([가-힣]{2,3})', text)
            if m:
                nm = m.group(2)
                return '' if nm in _NAME_BAN else nm
            toks = re.findall(r'[가-힣]{2,3}', text)
            toks = [t for t in toks if t not in _NAME_BAN]
            if not toks: return ''
            pos_label = min([p for p in [text.find('성명'), text.find('이름')] if p != -1] + [len(text)//2])
            best, best_d = '', 10**9
            for t in toks:
                p = text.find(t)
                if p != -1:
                    d = abs(p - pos_label)
                    if d < best_d:
                        best, best_d = t, d
            return best

        def _parse_en_date(s: str) -> str:
            MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
            m = re.search(r'(\d{1,2})\s*([A-Z]{3})\s*(\d{4})', (s or '').upper())
            if not m: return ''
            d, mon, y = int(m.group(1)), MONTHS.get(m.group(2),0), int(m.group(3))
            if not mon: return ''
            try: return _dt(y,mon,d).strftime('%Y-%m-%d')
            except: return ''

        def _parse_ko_date(s: str) -> str:
            s2 = (s or '').replace('년','.').replace('월','.').replace('일','').replace('-', '.').replace('/', '.')
            m = re.search(r'(\d{4})\.(\d{1,2})\.(\d{1,2})', s2)
            if not m: return ''
            y,mo,d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try: return _dt(y,mo,d).strftime('%Y-%m-%d')
            except: return ''

        def _find_all_dates(text: str):
            cands = set()
            if not text: return []
            for m in re.finditer(r'(\d{4})[.\-\/](\d{1,2})[.\-\/](\d{1,2})', text):
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                try: cands.add(_dt(y, mo, d).strftime('%Y-%m-%d'))
                except: pass
            for m in re.finditer(r'(\d{1,2})\s*([A-Z]{3})\s*(\d{4})', (text or '').upper()):
                MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
                d, mon, y = int(m.group(1)), MONTHS.get(m.group(2),0), int(m.group(3))
                if mon:
                    try: cands.add(_dt(y, mon, d).strftime('%Y-%m-%d'))
                    except: pass
            return sorted(cands)

        def _pick_labeled_date(text: str, labels_regex: str) -> str:
            if not text: return ''
            m1 = re.search(labels_regex + r'[^\d]{0,10}(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})', text, re.I)
            if m1:
                return _parse_ko_date(m1.group(1)) or _parse_en_date(m1.group(1))
            m2 = re.search(labels_regex + r'[^\d]{0,10}(\d{1,2}\s*[A-Z]{3}\s*\d{4})', text, re.I)
            if m2:
                return _parse_en_date(m2.group(1))
            return ''

        def _fast_ocr(im, lang='kor', psm=6, soften=True):
            try:
                proc = _binarize_soft(im) if soften else _pre(im)
            except Exception:
                proc = im
            cfg = f'--oem 3 --psm {psm}'
            try:
                return (_ocr(proc, lang=lang, config=cfg) or '').strip()
            except Exception:
                return ''

        # === FAST & STABLE parse_arc (앞/뒤 ROI + 0/90/270 회전, 주소/만기일 강화) ===
        def parse_arc(img):
            """
            등록증 이미지:
            - 상단 50%: 한글이름(있으면), 등록증 앞6/뒤7, 발급일
            - 하단 50%: 만기일, 주소  ← 앞 날짜 제거, 다음 줄 결합, (신길동) 제거, 띄어쓰기/숫자 사이 공백 보정
            - 속도: 상단은 1회만(ocr_try_all), 하단은 0/90/270 회전 × 가벼운 kor OCR 2셋(psm6+psm4) 중 최고만 사용
            """
            out = {}
            if img is None:
                return out

            import re
            from datetime import datetime as _d
            from PIL import ImageOps, Image as _PILImage, ImageFilter

            # ───────── 기본 ROI ─────────
            w, h = img.size
            top = img.crop((0, 0, w, int(h*0.5)))          # 앞면
            bot = img.crop((0, int(h*0.5), w, h))          # 뒷면

            # ───────── 가벼운 전처리 + 빠른 OCR ─────────
            def _soft(im):
                g = ImageOps.grayscale(im)
                W, H = g.size
                if W < 1500:
                    r = 1500 / float(W)
                    g = g.resize((int(W*r), int(H*r)), resample=_PILImage.Resampling.BILINEAR)
                g = ImageOps.autocontrast(g)
                g = g.filter(ImageFilter.SHARPEN)
                return g

            def _fast_read(im, psm=6, lang="kor"):
                try:
                    return (_ocr(_soft(im), lang=lang, config=f"--oem 3 --psm {psm}") or "")
                except Exception:
                    return ""

            # 숫자 오인식 보정 테이블
            _DIGFIX = str.maketrans({
                'O':'0','o':'0','D':'0','Q':'0',
                'I':'1','l':'1','|':'1','!':'1',
                'Z':'2','S':'5','s':'5','B':'8',
                'g':'9','q':'9'
            })
            def _fix_digits_local(s: str) -> str:
                # 외부 _fix_digits가 있으면 그걸 쓰고, 없으면 로컬 보정 사용
                try:
                    return _fix_digits(s)
                except Exception:
                    return s.translate(_DIGFIX)

            # ───────── 날짜 파서 ─────────
            def _norm_date(s):
                if not s: return ""
                t = (s or "").replace("년",".").replace("월",".").replace("일","")
                t = t.replace("/",".").replace("-",".")
                m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", t)
                if not m:
                    m = re.search(r"(\d{8})", re.sub(r"\s+","", t))
                    if m:
                        y, mo, dd = m.group(1)[:4], m.group(1)[4:6], m.group(1)[6:8]
                    else:
                        return ""
                else:
                    y, mo, dd = m.group(1), m.group(2), m.group(3)
                y = y.translate(_DIGFIX)
                if y.startswith("26"):  # 2622 → 2022 같은 케이스
                    y = "20" + y[2:]
                try:
                    return _d(int(y), int(mo), int(dd)).strftime("%Y-%m-%d")
                except:
                    return ""

            def _find_all_dates(text: str):
                cands = set()
                if not text: return []
                # YYYY.MM.DD / YYYY-MM-DD / YYYY/MM/DD
                for m in re.finditer(r"(\d{4})[.\-\/](\d{1,2})[.\-\/](\d{1,2})", text):
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    try: cands.add(_d(y, mo, d).strftime("%Y-%m-%d"))
                    except: pass
                # 12 OCT 2024
                MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                          "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
                for m in re.finditer(r"(\d{1,2})\s*([A-Z]{3})\s*(\d{4})", (text or "").upper()):
                    d, mon, y = int(m.group(1)), MONTHS.get(m.group(2),0), int(m.group(3))
                    if mon:
                        try: cands.add(_d(y, mon, d).strftime("%Y-%m-%d"))
                        except: pass
                return sorted(cands)

            def _pick_labeled_date(text: str, labels_regex: str):
                if not text: return ""
                m1 = re.search(labels_regex + r"[^\d]{0,10}(\d{4}[.\-/]?\d{1,2}[.\-/]?\d{1,2}|\d{8})", text, re.I)
                return _norm_date(m1.group(1)) if m1 else ""

            # ───────── 주소 정리 ─────────
            BAN = re.compile(r"(유효|취업|가능|확인|민원|국번없이|1345|www|http|출입국|안내|관할|사무소|CHIEF)", re.I)

            def _strip_leading_date(s: str) -> str:
                # 맨 앞의 날짜 "2020.03.16 " 제거
                return re.sub(r"^\s*\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\s*", "", s or "")

            def _clean_addr_line(s: str, drop_paren=True) -> str:
                s = _strip_leading_date(s)
                # 한글/숫자/기본기호만
                s = re.sub(r"[^가-힣0-9\s\-\.,#()/~]", " ", s)
                if drop_paren:
                    s = re.sub(r"\([^)]*\)", " ", s)  # (신길동) 제거
                s = re.sub(r"\s{2,}", " ", s).strip(" ,")
                return s

            def _kor_count(s: str) -> int:
                return len(re.findall(r"[가-힣]", s or ""))

            def _addr_score(s: str) -> int:
                if not s or BAN.search(s): return -1
                sc = _kor_count(s)*2
                if re.search(r"(도|시|군|구|읍|면|동|리)", s): sc += 6
                if re.search(r"(로|길|대로|번길)", s):        sc += 8
                if re.search(r"\d", s):                      sc += 4
                return sc

            def _join_number_spaces(s: str) -> str:
                # "로 1 1, 2 0 1호" → "로 11, 201호"
                return re.sub(r"(?<=\d)\s+(?=\d)", "", s or "")

            def _fix_hangul_spacing(s: str) -> str:
                # '경 기 도 안 산 시' → '경기도 안산시'
                toks = s.split()
                outt = []
                for t in toks:
                    if outt and len(outt[-1])==1 and len(t)==1 and re.fullmatch(r"[가-힣]", outt[-1]) and re.fullmatch(r"[가-힣]", t):
                        outt[-1] = outt[-1] + t
                    else:
                        outt.append(t)
                return " ".join(outt)

            def _best_addr(text: str) -> str:
                lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
                best_i, best, best_sc = -1, "", -1
                for i, l in enumerate(lines):
                    c  = _clean_addr_line(l, drop_paren=True)  # (신길동) 제거
                    if not c or re.fullmatch(r"[\(\)\.\-/#\s]+", c) or len(c) < 6:
                        continue
                    sc = _addr_score(c)
                    if sc > best_sc:
                        best_i, best, best_sc = i, c, sc
                if best_i < 0:
                    return ""

                # 다음 줄 결합 (동/호/층/숫자 있으면)
                addr = best
                if best_i + 1 < len(lines):
                    nxt = _clean_addr_line(lines[best_i+1], drop_paren=True)
                    if nxt and not BAN.search(nxt) and re.search(r"(동|호|층|\d)", nxt):
                        cand = (addr + ", " + nxt).strip(" ,")
                        if _addr_score(cand) >= _addr_score(addr):
                            addr = cand

                # 숫자 사이 공백 제거 + 한글 단독글자 붙이기
                addr = _join_number_spaces(addr)
                addr = _fix_hangul_spacing(addr)
                return addr

            # ───────── 상단 50%: 번호/발급일/이름 ─────────
            try:
                t_top = ocr_try_all(top, langs=("kor", "kor+eng"))["text"]
            except Exception:
                t_top = ""
            tn_top = _fix_digits_local(t_top)

            # --- 등록증 앞6/뒤7 (강화) ---
            # 숫자 사이에 끼어드는 공백을 먼저 제거해서 7자리 매칭 실패를 막는다.
            t_dense = re.sub(r'(?<=\d)\s+(?=\d)', '', tn_top)

            # 6자리 → (비숫자 최대 12자) → 7자리 를 우선적으로 한 번에 잡는다
            pair = re.search(r'(?<!\d)(\d{6})\D{0,12}(\d{7})(?!\d)', t_dense)
            if pair:
                out["등록증"], out["번호"] = pair.group(1), pair.group(2)
            else:
                # 폴백: 6자리와 7자리 후보를 모두 뽑고, 6자리 '뒤쪽 30자 이내'에 있는 7자리를 우선 연결
                six_spans = [(m.group(0), m.start()) for m in re.finditer(r'(?<!\d)\d{6}(?!\d)', t_dense)]
                sev_spans = [(m.group(0), m.start()) for m in re.finditer(r'(?<!\d)\d{7}(?!\d)', t_dense)]

                best = None
                best_dist = 10**9
                for s6, p6 in six_spans:
                    for s7, p7 in sev_spans:
                        if p7 >= p6 and (p7 - p6) <= 30:  # 앞6 바로 뒤쪽(30자 이내)만 허용
                            d = p7 - p6
                            if d < best_dist:
                                best = (s6, s7)
                                best_dist = d

                if best:
                    out["등록증"], out["번호"] = best
                else:
                    # 그래도 없으면 기존 폴백(가급적 5~8로 시작하는 후보 우선)
                    if six_spans:
                        out["등록증"] = six_spans[0][0]
                    if sev_spans:
                        pref = [x for x, _ in sev_spans if x[0] in "5678"]
                        out["번호"] = pref[0] if pref else sev_spans[0][0]

            # 발급일 (라벨 우선 → 가장 이른 날짜)
            issued = _pick_labeled_date(tn_top, r"(발\s*급|발\s*행|issue|issued)")
            if not issued:
                ds_top = _find_all_dates(tn_top)
                if ds_top: issued = ds_top[0]
            if issued:
                out["발급일"] = issued

            # 한글 이름 (라벨 근처 2~3글자 우선, 금지어 제외)
            def _extract_name(text: str) -> str:
                ban = {"외국","국내","거소","신고","증","대한","민국","주소","발급","만기","체류","자격","종류"}
                m = re.search(r"(성명|이름)\s*[:\-]?\s*([가-힣]{2,3})", text)
                if m and m.group(2) not in ban:
                    return m.group(2)
                toks = re.findall(r"[가-힣]{2,3}", text)
                toks = [t for t in toks if t not in ban]
                if not toks: return ""
                # 라벨과의 거리 최소 값 선택
                pos_label = min([p for p in [text.find("성명"), text.find("이름")] if p != -1] + [len(text)//2])
                best, best_d = "", 10**9
                for t in toks:
                    p = text.find(t)
                    if p != -1:
                        d = abs(p - pos_label)
                        if d < best_d:
                            best, best_d = t, d
                return best

            name_ko = _extract_name(t_top)
            if name_ko and name_ko not in ("성명", "이름"):
                out["한글"] = name_ko

            # ───────── 하단 50%: 회전(0/90/270) 중 최고 1개만 사용 ─────────
            best_text, best_sc = "", -1
            for deg in (0, 90, 270):
                im = bot.rotate(deg, expand=True)
                t1 = _fast_read(im, psm=6, lang="kor")
                t2 = _fast_read(im, psm=4, lang="kor")
                t  = (t1 + "\n" + t2)
                t  = _fix_digits_local(t)
                sc = _kor_count(t) + 10*bool(re.search(r"(만기|유효|until|expiry|expiration|valid\s*until|까지)", t, re.I)) \
                     + 5*len(re.findall(r"(\d{4}[.\-/]?\d{1,2}[.\-/]?\d{1,2}|\d{8})", t))
                if sc > best_sc:
                    best_sc, best_text = sc, t

            tn_bot = best_text

            # 만기일: 라벨 우선 → 가장 늦은 날짜 (발급일과 같으면 다음값)
            expiry = _pick_labeled_date(tn_bot, r"(만기|유효|until|expiry|expiration|valid\s*until|까지)")
            if not expiry:
                ds_bot = _find_all_dates(tn_bot)
                if issued and issued in ds_bot:
                    try: ds_bot.remove(issued)
                    except ValueError: pass
                if ds_bot: expiry = ds_bot[-1]
            if expiry:
                out["만기일"] = expiry

            # 주소: 앞 날짜 제거 + (신길동) 제거 + 다음 줄 결합 + 숫자/한글 공백 보정
            addr = _best_addr(tn_bot)
            if addr and _kor_count(addr) >= 3 and len(addr) >= 6:
                out["주소"] = addr

            return out
        # === parse_arc 끝 ===
        
        # 업로드
        cc0, cc1 = st.columns(2)
        with cc0:
            passport_file = st.file_uploader("여권 이미지 (필수)", type=["jpg","jpeg","png","webp"])
        with cc1:
            arc_file = st.file_uploader("등록증/스티커 이미지 (선택)", type=["jpg","jpeg","png","webp"])

        show_debug = st.checkbox(
            "🧪 디버그 패널 보기(느림)", value=False,
             help="체크하면 원문/베스트OCR/파싱결과/테서랙트 진단을 표시합니다. (속도 저하)"
        )

        # >>> 교체: 토글 켜졌을 때만 표시
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
                    langs = pytesseract.get_languages()  # ⬅️ config 없이!
                except Exception as e:
                    langs = f"(에러: {e})"
                st.write(f'탐지된 언어들: {langs}')

        parsed_passport, parsed_arc = {}, {}
        if passport_file:
            img_p = open_image_safe(passport_file)
            st.image(img_p, caption="여권", use_container_width=True)
            parsed_passport = parse_passport(img_p)
        if arc_file:
            img_a = open_image_safe(arc_file)
            st.image(img_a, caption="등록증/스티커", use_container_width=True)
            parsed_arc = parse_arc(img_a)

        try:
            birth = parsed_passport.get("생년월일", "").strip()
            if birth:
                yymmdd = _dt.strptime(birth, "%Y-%m-%d").strftime("%y%m%d")
                st.session_state["scan_등록증"] = yymmdd  # ✅ 항상 덮어씀
        except Exception:
            pass

        # >>> 교체: 토글 켜진 경우에만 ‘베스트 OCR’ 실행(느림)
        if show_debug:
            with st.expander("🧪 OCR 원문(베스트 설정)", expanded=False):
                if passport_file:
                    bp = ocr_try_all(img_p)
                    st.write({"lang": bp["lang"], "config": bp["config"], "pre": bp["pre"], "score": bp["score"]})
                    st.code(bp["text"][:2000])
                if arc_file:
                    ba = ocr_try_all(img_a)
                    st.write({"lang": ba["lang"], "config": ba["config"], "pre": ba["pre"], "score": ba["score"]})
                    st.code(ba["text"][:2000])

        # (미리보기 출력 바로 아래에)
        if show_debug:
            with st.expander("🔎 OCR 원문 보기"):
                if passport_file:
                    st.markdown("**여권 MRZ 크롭(샘플)**")
                    w, h = img_p.size
                    test_mrz = _binarize(img_p.crop((0, int(h*0.6), w, h)))
                    st.image(test_mrz, caption="MRZ(하단부) 샘플", use_container_width=True)
                    st.code(_ocr(
                        test_mrz,
                        "eng",
                        "--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ<0123456789"
                    ))
                if arc_file:
                    st.markdown("**등록증 전체 원문(빠른 이진화 1회)**")
                    st.code(_ocr(_binarize_soft(img_a), "kor", "--oem 3 --psm 6")[:2000])

        # >>> 교체: 토글 켜진 경우에만 표시
        if show_debug:
            with st.expander("🧪 OCR 파싱 결과(디버그)"):
                st.json({"passport": parsed_passport, "arc": parsed_arc})

        # ✅ OCR 결과를 세션에 채워 넣고, 최초 1회만 화면을 새로고침
        def _prefill_from_ocr(p, a):
            changed = False
            def setk(field, val):
                nonlocal changed
                k = f"scan_{field}"
                v = (val or "").strip()
                if v and st.session_state.get(k, "").strip() == "":
                    st.session_state[k] = v
                    changed = True
            setk("한글",    a.get("한글"))
            setk("성",      p.get("성"))
            setk("명",      p.get("명"))
            setk("여권",    p.get("여권"))
            setk("여권발급", p.get("발급"))
            setk("여권만기", p.get("만기"))
            setk("등록증",  a.get("등록증"))
            setk("번호",    a.get("번호"))
            setk("발급일",  a.get("발급일"))
            setk("만기일",  a.get("만기일"))
            setk("주소",    a.get("주소"))
            return changed
            if p.get("생년월일"):
                try:
                    yymmdd = _dt.strptime(p["생년월일"], "%Y-%m-%d").strftime("%y%m%d")
                    # 사용자가 이미 입력해놨으면 건드리지 않음
                    if not str(st.session_state.get("scan_등록증", "")).strip():
                        st.session_state["scan_등록증"] = yymmdd
                        changed = True
                except Exception:
                    pass
            return changed

        # ⬇️ parsed_passport / parsed_arc 계산된 다음, 폼 전에 배치
        if _prefill_from_ocr(parsed_passport, parsed_arc) and not st.session_state.get("_scan_prefilled_once"):
            st.session_state["_scan_prefilled_once"] = True
            st.rerun()

        try:
            birth = (parsed_passport.get("생년월일", "") or "").strip()
            if birth:
                yymmdd = _dt.strptime(birth, "%Y-%m-%d").strftime("%y%m%d")
                st.session_state["scan_등록증"] = yymmdd  # ✅ 최종 우선권
        except Exception:
            pass

        st.markdown("### 🔎 OCR 추출값 (필요 시 수정)")
        with st.form("scan_confirm_form"):
            c1, c2, c3 = st.columns(3)

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
            주소 = c3.text_input("주소", key="scan_주소")  # ✅ 추가

            submitted = st.form_submit_button("💾 고객관리 반영")
            if submitted:
                ok, msg = upsert_customer_from_scan(
                    {"성":성.strip(),"명":명.strip(),"여권":여권.strip(),
                     "발급":여권발급.strip(),"만기":여권만기.strip()},
                    {"한글":한글.strip(),"등록증":등록증.strip(),"번호":번호.strip(),
                     "발급일":발급일.strip(),"만기일":만기일.strip(),"주소":주소.strip()}  # ✅ 주소 전달
                )
                # 변경 코드
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")

                # ↩️ 고객관리로 돌아가기 버튼
                if st.button("👥 고객관리 페이지로 돌아가기", use_container_width=True):
                    st.session_state[SESS_CURRENT_PAGE] = PAGE_CUSTOMER
                    st.rerun()

        if st.button("← 고객관리로 돌아가기", use_container_width=True):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_CUSTOMER
            st.rerun()


    # -----------------------------
    # ✅ Manual Search Page
    # -----------------------------
    elif current_page_to_display == PAGE_MANUAL: 
        st.subheader("🧭 메뉴얼 검색 (GPT 기반)")
        question = st.text_input("궁금한 내용을 입력하세요", placeholder="예: F-4에서 F-5 변경 조건은?") 
        if st.button("🔍 GPT로 검색하기"): 
            if question:
                with st.spinner("답변 생성 중입니다..."): 
                    answer = search_via_server(question) 
                    st.markdown("#### 🧠 GPT 요약 답변")
                    st.write(answer) 
            else:
                st.info("검색할 내용을 입력해주세요.")

    # -----------------------------
    # ✅ Memo Page
    # -----------------------------
    elif current_page_to_display == PAGE_MEMO: 
        st.subheader("🗒️ 메모장")
        
        st.markdown("---")
        col_long, col_mid = st.columns(2)

        with col_long:
            st.markdown("### 📌 장기보존 메모")
            memo_long_content = load_long_memo()
            edited_memo_long = st.text_area("🗂️ 장기보존 내용", value=memo_long_content, height=300, key="memo_long_text_area")
            if st.button("💾 장기메모 저장", key="save_memo_long_btn", use_container_width=True): 
                if save_long_memo(edited_memo_long):
                    st.success("✅ 장기보존 메모가 저장되었습니다.")
                    st.rerun() 
                else:
                    st.error("장기메모 저장에 실패했습니다.")

        with col_mid:
            st.markdown("### 🗓 중기 메모")
            memo_mid_content = load_mid_memo()
            edited_memo_mid = st.text_area("📘 중기메모", value=memo_mid_content, height=300, key="memo_mid_text_area")
            if st.button("💾 중기메모 저장", key="save_memo_mid_btn", use_container_width=True):
                if save_mid_memo(edited_memo_mid):
                    st.success("✅ 중기메모가 저장되었습니다.")
                    st.rerun()
                else:
                    st.error("중기메모 저장에 실패했습니다.")
    
    # -----------------------------
    # ✅ Document Automation Page (수정된 부분)
    # -----------------------------
    elif current_page_to_display == PAGE_DOCUMENT:
        st.subheader("📝 문서작성 자동화")

        # 고객 데이터 로드
        if "df_customer" not in st.session_state:
            st.session_state["df_customer"] = load_customer_df_from_sheet()
        df_cust = st.session_state["df_customer"]

        # 문서 생성 상태 초기화
        if "document_generated" not in st.session_state:
            st.session_state["document_generated"] = False

        # PDF 템플릿 목록
        pdf_templates = {
            f"{업무}_{내용}": f"templates/{업무}_{내용}.pdf"
            for 업무 in ["H2", "F4", "F1", "F3", "F2", "F5", "국적"]
            for 내용 in ["등록", "연장", "연장 전자", "자격변경", "자격변경 전자", "자격부여", "체류지 변경", "등록사항 변경"]
        }

        # 레이아웃: 6개 컬럼 (업무/내용, 숙소제공자, 신원보증인, 신청인, 미성년자 대리인)
        cols = st.columns(6)

        # 1) 업무·내용 선택
        with cols[0]:
            업무 = st.selectbox(
                "업무",
                sorted({k.split('_')[0] for k in pdf_templates.keys()}),
                key="doc_task"
            )
        with cols[1]:
            내용 = st.selectbox(
                "내용",
                sorted({k.split('_')[1] for k in pdf_templates.keys()}),
                key="doc_action"
            )

        # 2) 숙소 제공자 검색·선택
        with cols[2]:
            숙소키워드 = st.text_input("숙소제공자 검색", key="doc_accommodation_search")
        matched_provs = (
            df_cust[df_cust["한글"].str.contains(숙소키워드.strip(), na=False)]
            if 숙소키워드.strip() else pd.DataFrame()
        )
        if not matched_provs.empty:
            st.markdown("👀 **숙소제공자 검색 결과:**")
            for idx2, prov_row in matched_provs.iterrows():
                label2 = f"{prov_row['한글']} / {prov_row['등록증']} / {prov_row['연']}-{prov_row['락']}-{prov_row['처']}"
                if st.button(label2, key=f"accom_{idx2}"):
                    # 신청인·대리인 초기화
                    st.session_state.pop("selected_customer_idx", None)
                    st.session_state.pop("selected_guardian_idx", None)
                    st.session_state["selected_accommodation_idx"] = idx2
                    st.session_state["document_generated"] = False
                    st.rerun()
        prov = None
        if "selected_accommodation_idx" in st.session_state:
            prov = df_cust.loc[st.session_state["selected_accommodation_idx"]]
            st.markdown(f"✅ 선택된 숙소제공자: **{prov['한글']}**")

        # 3) 신원보증인 검색·선택 (F1, F3 선택 시만)
        보증인 = None
        if 업무 in ["F1", "F2", "F3", "F5"]:
            with cols[3]:
                guarantor_kw = st.text_input("신원보증인 검색", key="doc_guarantor_search")
            matched_guars = (
                df_cust[df_cust["한글"].str.contains(guarantor_kw.strip(), na=False)]
                if guarantor_kw.strip() else pd.DataFrame()
            )
            if not matched_guars.empty:
                st.markdown("🔒 **신원보증인 검색 결과:**")
                for _, grow in matched_guars.iterrows():
                    # ① 고객ID 컬럼 이름이 다르면 실제 이름으로 바꿔주세요
                    cust_id = grow["고객ID"]
                    lbl     = f"{grow['한글']} / {grow['등록증']} / {grow['연']}-{grow['락']}-{grow['처']}"
                    # ② key에 고객ID를 사용해서 절대 중복 방지
                    if st.button(lbl, key=f"guarantor_{cust_id}"):
                        # idxg 대신 DataFrame index(번호)를 그대로 써야 할 경우 grow.name 사용
                        st.session_state["selected_guarantor_idx"] = grow.name
                        st.session_state["document_generated"] = False
                        st.rerun()
            if "selected_guarantor_idx" in st.session_state:
                보증인 = df_cust.loc[st.session_state["selected_guarantor_idx"]]
                st.markdown(f"✅ 선택된 신원보증인: **{보증인['한글']}**")

        # 4) 신청인 검색·선택
        with cols[4]:
            신청인_검색어 = st.text_input("신청인 이름 (고객 검색)", key="doc_search")
        matched = (
            df_cust[df_cust["한글"].str.contains(신청인_검색어.strip(), na=False)]
            if 신청인_검색어.strip() else pd.DataFrame()
        )
        if not matched.empty:
            st.markdown("🔎 **신청인 검색 결과:**")
            for idx, row_tmp in matched.iterrows():
                label = f"{row_tmp['한글']} / {row_tmp['등록증']} / {row_tmp['연']}-{row_tmp['락']}-{row_tmp['처']}"
                if st.button(label, key=f"select_{idx}"):
                    st.session_state["selected_customer_idx"] = idx
                    st.session_state["document_generated"] = False
                    st.rerun()

        선택된_고객, row = None, None
        if "selected_customer_idx" in st.session_state:
            row = df_cust.loc[st.session_state["selected_customer_idx"]]
            선택된_고객 = row["한글"]

        # 5) 미성년자 대리인 로직
        import datetime
        is_minor = False
        guardian = None
        if row is not None:
            reg = str(row.get("등록증", "")).replace("-", "")
            # 생년월일 정보가 최소 6자리의 숫자인지 확인
            if len(reg) >= 6 and reg[:6].isdigit():
                yy_int = int(reg[:2])
                current_short = datetime.date.today().year % 100
                century = 2000 if yy_int <= current_short else 1900
                try:
                    birth = datetime.date(century + yy_int, int(reg[2:4]), int(reg[4:6]))
                    age = (datetime.date.today() - birth).days // 365
                    is_minor = age < 18
                except ValueError:
                    is_minor = False
            else:
                is_minor = False

        if is_minor:
            with cols[5]:
                대리인_검색 = st.text_input("대리인 이름 (고객 검색)", key="doc_guardian_search")
            후보 = (
                df_cust[df_cust["한글"].str.contains(대리인_검색.strip(), na=False)]
                if 대리인_검색.strip() else pd.DataFrame()
            )
            if not 후보.empty:
                st.markdown("👤 **대리인 검색 결과:**")
                for _, row2 in 후보.iterrows():
                    cust_id = row2["고객ID"]
                    label3  = f"{row2['한글']} / {row2['등록증']} / {row2['연']}-{row2['락']}-{row2['처']}"
                    if st.button(label3, key=f"guardian_{cust_id}"):
                        st.session_state["selected_guardian_idx"] = row2.name
                        st.session_state["document_generated"] = False
                        st.rerun()
            if "selected_guardian_idx" in st.session_state:
                guardian = df_cust.loc[st.session_state["selected_guardian_idx"]]

        st.markdown("---")

        # 문서 생성
        if 선택된_고객 and 업무 and 내용 and not st.session_state["document_generated"]:
            key = f"{업무}_{내용}"
            template_path = pdf_templates.get(key)
            if not template_path or not os.path.exists(template_path):
                st.error(f"❗️ 템플릿이 없습니다: templates/{key}.pdf")
                st.stop()
            # ── F1, F3, F5 보증인 필수 체크
            if 업무 in ["F1", "F3", "F5"] and 보증인 is None:
                st.error("❗️ 신원보증인을 선택해야 문서를 생성할 수 있습니다.")
                st.stop()

            if is_minor and guardian is None:
                st.error("❗️ 미성년자는 대리인을 선택해야 문서를 생성할 수 있습니다.")
                st.stop()

            # 생년월일 및 성별 표시
            reg = str(row.get("등록증", "")).replace("-", "")
            birth_raw = reg[:6]
            if len(birth_raw) == 6 and birth_raw.isdigit():
                yy = int(birth_raw[:2])
                current_short = datetime.date.today().year % 100
                century = 2000 if yy <= current_short else 1900
                yyyy = str(century + yy)
                mm = birth_raw[2:4]
                dd = birth_raw[4:6]
            else:
                yyyy, mm, dd = "", "", ""

            # 2) '번호' 필드의 첫 글자로 성별 판별
            num = str(row.get("번호", "")).replace("-", "").strip()
            gdigit = num[0] if len(num) >= 1 else ""
            gender = "남" if gdigit in ["5","7"] else "여" if gdigit in ["6","8"] else ""
            man = "V" if gdigit in ["5","7"] else ""
            girl = "V" if gdigit in ["6","8"] else ""

            # 기본 필드 값 세팅
            field_values = {
                "Surname":     row.get("성", ""),
                "Given names": row.get("명", ""),
                "yyyy":        yyyy, "mm": mm, "dd": dd,
                "gender":      gender,
                "man":         man, "girl": girl,
                "fnumber":     row.get("등록증", ""),
                "rnumber":     row.get("번호", ""),
                "passport":    row.get("여권", ""),
                "issue":       row.get("발급", ""),
                "expiry":      row.get("만기", ""),
                "nation":      "중국",
                "adress":      row.get("주소", ""),
                "phone1":      row.get("연", ""), "phone2": row.get("락", ""), "phone3": row.get("처", ""),
                "koreanname":  row.get("한글", ""),
                "bankaccount": row.get("환불계좌", ""),
                "why":         row.get("신청이유", ""),
                "hope":        row.get("희망자격", ""),
                "partner":     row.get("배우자", ""),
                "parents":     guardian.get("한글", "") if is_minor else row.get("부모", ""),
                # 기타 필드 초기화
                "registration": "", "card": "", "extension": "",
                "change": "", "granting": "", "adresscheck": "",
                "partner yin": "", "parents yin": "", "changeregist": "",
            }
            # 번호 자리별 필드
            for i, digit in enumerate(str(row.get("등록증", "")).strip(), 1):
                field_values[f"fnumber{i}"] = digit
            for i, digit in enumerate(str(row.get("번호", "")).strip(), 1):
                field_values[f"rnumber{i}"] = digit

            # 숙소 제공자 필드
            if prov is not None:
                field_values.update({
                    "hsurname": prov.get("성", ""),
                    "hgiven names": prov.get("명", ""),
                    "hfnumber": prov.get("등록증", ""),
                    "hrnumber": prov.get("번호", ""),
                    "hphone1": prov.get("연", ""),
                    "hphone2": prov.get("락", ""),
                    "hphone3": prov.get("처", ""),
                    "hkoreanname": prov.get("한글", ""),
                })
                # 숙소 제공자 인감
                prov_seal = create_seal(circle_path, prov["한글"], font_path, seal_size)
                buf_prov = io.BytesIO()
                prov_seal.save(buf_prov, format="PNG")
                prov_img_bytes = buf_prov.getvalue()

            # 신청인/대리인 인감
            seal_name = guardian["한글"] if is_minor and guardian is not None else 선택된_고객
            seal_img = create_seal(circle_path, seal_name, font_path, seal_size)
            buf = io.BytesIO()
            seal_img.save(buf, format="PNG")
            img_bytes = buf.getvalue()

            # 신원보증인 인감
            if 보증인 is not None:
                # 1) 보증인 인감 생성
                g_seal = create_seal(circle_path, 보증인["한글"], font_path, seal_size)
                buf_g = io.BytesIO()
                g_seal.save(buf_g, format="PNG")
                byin_bytes = buf_g.getvalue()

                # 2) 보증인 등록증 번호로 생년월일/성별 계산
                g_reg = str(보증인["등록증"]).replace("-", "")
                gbirth = g_reg[:6]
                byyyy = "19" + gbirth[:2] if int(gbirth[:2]) > 41 else "20" + gbirth[:2]
                bmm, bdd = gbirth[2:4], gbirth[4:6]
                reg_no = str(보증인["번호"]).replace("-", "").strip()
                gdigit = reg_no[0] if len(reg_no) >= 1 else ""
                if gdigit in ["5", "7"]:
                    bgender = "남"
                    bman = "V"
                    bgirl = ""
                elif gdigit in ["6", "8"]:
                    bgender = "여"
                    bman = ""
                    bgirl = "V"
                else:
                    bgender = ""
                    bman = ""
                    bgirl = ""

                # 3) 보증인 필드값 업데이트
                field_values.update({
                    "bsurname": 보증인.get("성", ""),
                    "bgiven names": 보증인.get("명", ""),
                    "byyyy": byyyy,
                    "bmm": bmm,
                    "bdd": bdd,
                    "bgender": bgender,
                    "bman": bman,
                    "bgirl": bgirl,
                    "bfnumber": 보증인.get("등록증", ""),
                    "brnumber": 보증인.get("번호", ""),
                    "badress": 보증인.get("주소", ""),
                    "bphone1": 보증인.get("연", ""),
                    "bphone2": 보증인.get("락", ""),
                    "bphone3": 보증인.get("처", ""),
                    "bkoreanname": 보증인.get("한글", ""),
                })
                # 4) 자리별 번호
                for i, d in enumerate(g_reg, start=1):
                    field_values[f"bfnumber{i}"] = d

            # PDF 필드 삽입 및 이미지 삽입
            import fitz
            doc = fitz.open(template_path)
            for page in doc:
                for widget in page.widgets():
                    base = widget.field_name.split('#')[0]
                    if base in field_values:
                        widget.field_value = field_values[base]
                        widget.update()
                for widget in page.widgets():
                    base = widget.field_name.split('#')[0]
                    if base == "yin":
                        page.insert_image(widget.rect, stream=img_bytes)
                    if base == "hyin" and prov is not None:
                        page.insert_image(widget.rect, stream=prov_img_bytes)
                    if base == "byin" and 보증인 is not None:
                        page.insert_image(widget.rect, stream=byin_bytes)

            out = io.BytesIO()
            doc.save(out)
            doc.close()
            out.seek(0)

            # 다운로드 버튼
            if st.download_button(
                "📅 자동작성된 PDF 다운로드",
                data=out.read(),
                file_name=f"{선택된_고객}_{업무}_{내용}.pdf",
                mime="application/pdf"
            ):
                st.session_state["document_generated"] = True
                st.rerun()

        # 완료 후 초기화
        if st.session_state["document_generated"]:
            st.success("✅ 문서가 성공적으로 생성되었습니다.")
            if st.button("🔄 다른 고객으로 다시 작성"):
                for k in ["selected_customer_idx", "selected_guardian_idx", "selected_accommodation_idx", "selected_guarantor_idx"]:
                    st.session_state.pop(k, None)
                st.session_state["document_generated"] = False
                st.rerun()


    # -----------------------------
    # ✅ Reference Page
    # -----------------------------
    elif current_page_to_display == PAGE_REFERENCE:
        st.subheader("📚 업무참고")

        # --------------------------------
        # 🔼 상단 아이콘 버튼 2개 (서류작성 / 완료업무)
        # --------------------------------
        col_blank_ref, col_doc_ref, col_done_ref = st.columns([8, 1, 1])

        with col_doc_ref:
            if st.button("📝 서류작성", key="nav_to_document_page_from_ref"):
                st.session_state[SESS_CURRENT_PAGE] = PAGE_DOCUMENT
                st.rerun()

        with col_done_ref:
            if st.button("✅ 완료업무", key="nav_to_completed_from_ref"):
                st.session_state[SESS_CURRENT_PAGE] = PAGE_COMPLETED
                st.rerun()

        # --------------------------------
        # 🟩 구글시트 임베딩
        # --------------------------------
        st.markdown("#### 🗂️ 업무참고 시트 (수정 가능)")
        GOOGLE_SHEET_ID = "1KxZY_VGUfGjo8nWn1d01OVN007uTpbLSnNLX3Jf62nE"
        SHEET_EDIT_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit?rm=demo"

        st.markdown("""
            <style>
                .block-container {
                    padding-bottom: 0rem !important;
                }
                iframe {
                    margin-bottom: -20px !important;
                }
            </style>
        """, unsafe_allow_html=True)

        st.components.v1.iframe(
            src=SHEET_EDIT_URL,
            height=800,   # 충분히 길게 해서 내부 스크롤 줄임
            width=0,       # width=0 + use_container_width=True로 100% 폭
            scrolling=True,
        )

    # -----------------------------
    # ✅ Completed Tasks Page
    # -----------------------------
    elif current_page_to_display == PAGE_COMPLETED:
        st.subheader("✅ 완료업무")

        search_term_completed = st.text_input("🔍 검색", key="completed_tasks_search_term")
        
        completed_tasks_list = load_completed_tasks_from_sheet()
        if not completed_tasks_list:
            st.info("완료된 업무가 없습니다.")
        else:
            df_completed = pd.DataFrame(completed_tasks_list)
            
            # Sort by category then complete_date (descending for recent first)
            if 'category' in df_completed.columns:
                df_completed['category'] = df_completed['category'].fillna('')
            if 'complete_date' in df_completed.columns:
                df_completed['complete_date_dt'] = pd.to_datetime(df_completed['complete_date'], errors='coerce')
                df_completed = df_completed.sort_values(by=['category', 'complete_date_dt'], ascending=[True, False])
                df_completed = df_completed.drop(columns=['complete_date_dt']) # Drop helper column

            # Hide 'id' and 'source_original' if not needed for display, but keep for editing
            columns_to_display = [col for col in df_completed.columns if col not in ['id']] # 'source_original' might be useful

            if search_term_completed:
                df_completed_str = df_completed.astype(str)
                mask_completed = df_completed_str.apply(
                    lambda row: search_term_completed.lower() in row.str.lower().to_string(),
                    axis=1
                )
                df_completed_display = df_completed[mask_completed][columns_to_display]
            else:
                df_completed_display = df_completed[columns_to_display]

            # Use st.dataframe for non-editable display, or st.data_editor if edits are needed
            st.dataframe(df_completed_display.reset_index(drop=True), use_container_width=True, hide_index=True)
            
            # If editing is required for completed tasks:
            # edited_completed_df = st.data_editor(...)
            # if st.button("💾 완료업무 저장"):
            #    # Logic to merge edited_completed_df back and save
            #    # save_completed_tasks_to_sheet(edited_completed_df.to_dict('records')) # Simplified
            #    st.success("완료업무 시트에 저장되었습니다.")

    # -----------------------------
    # ✅ Home Page (Main Dashboard)
    # -----------------------------
    elif current_page_to_display == PAGE_HOME:
        home_col_left, home_col_right = st.columns(2) 

        with home_col_left:
            st.subheader("1. 📅 일정 달력")
            
            google_calendar_embed_code = """
            <iframe src="https://calendar.google.com/calendar/embed?height=600&wkst=1&ctz=Asia%2FSeoul&showPrint=0&src=d2tkd2hmbEBnbWFpbC5jb20&src=ZDEzOGVmN2MzNDVjY2YwNzE5MDBjOGVmMDVlMDlkYzZmZDFkZWVjNzQ5ZjBmNWMwM2I3NGZhY2EyODkwMGI5ZkBncm91cC5jYWxlbmRhci5nb29nbGUuY29t&src=a28uc291dGhfa29yZWEjaG9saWRheUBncm91cC52LmNhbGVuZGFyLmdvb2dsZS5jb20&color=%237986cb&color=%239e69af&color=%230b8043" style="border:solid 1px #777" width="100%" height="600" frameborder="0" scrolling="no"></iframe>
            """

            st.components.v1.html(google_calendar_embed_code, height=630, scrolling=True)

            # 단기 메모
            memo_short_content = load_short_memo()
            edited_memo_short = st.text_area("📗 단기메모", value=memo_short_content, height=200, key="memo_short_text_area")
            if st.button("💾 단기메모 저장", key="save_memo_short_btn", use_container_width=True):
                if save_short_memo(edited_memo_short):
                    st.success("✅ 단기메모가 저장되었습니다.")
                    st.rerun()
                else:
                    st.error("단기메모 저장에 실패했습니다.")            

        with home_col_right:
            st.subheader("2. 🪪 등록증 만기 4개월 전")
            
            df_customers_for_alert_view = st.session_state.get(SESS_DF_CUSTOMER, pd.DataFrame())
            if df_customers_for_alert_view.empty:
                st.write("(표시할 고객 없음)")
            else:
                # 표시용 기본 컬럼 구성
                df_alert_display_prepared_view = pd.DataFrame()
                df_alert_display_prepared_view['한글이름'] = df_customers_for_alert_view.get('한글', pd.Series(dtype='str'))
                df_alert_display_prepared_view['영문이름'] = df_customers_for_alert_view.get('성', pd.Series(dtype='str')).fillna('') + ' ' + df_customers_for_alert_view.get('명', pd.Series(dtype='str')).fillna('')
                df_alert_display_prepared_view['여권번호'] = df_customers_for_alert_view.get('여권', pd.Series(dtype='str')).astype(str).str.strip()
                # 전화번호 포맷
                df_alert_display_prepared_view['전화번호'] = (
                    df_customers_for_alert_view.get('연', pd.Series(dtype='str')).astype(str).apply(lambda x: x.split('.')[0].zfill(3) if pd.notna(x) and x.strip() and x.lower()!='nan' else " ") + ' ' +
                    df_customers_for_alert_view.get('락', pd.Series(dtype='str')).astype(str).apply(lambda x: x.split('.')[0].zfill(4) if pd.notna(x) and x.strip() and x.lower()!='nan' else " ") + ' ' +
                    df_customers_for_alert_view.get('처', pd.Series(dtype='str')).astype(str).apply(lambda x: x.split('.')[0].zfill(4) if pd.notna(x) and x.strip() and x.lower()!='nan' else " ")
                ).str.replace(r'^\s* \s*$', '(정보없음)', regex=True).str.replace(r'^\s*--\s*$', '(정보없음)', regex=True)

                # 생년월일 계산 함수
                def format_birthdate_alert_view(reg_front_val, reg_back_val=None):
                    """
                    reg_front_val: '등록증' 앞 6자리(YYMMDD)
                    reg_back_val : '번호' 뒤 7자리(선택) - 첫 자리가 세기 판단에 도움
                    반환: 'YYYY-MM-DD' 또는 ''
                    """
                    s = str(reg_front_val or "").strip()
                    s = s.split('.')[0]  # '680101.0' 같은 형태 방지
                    if len(s) < 6 or not s[:6].isdigit():
                        return ""
                    yy = int(s[:2]); mm = int(s[2:4]); dd = int(s[4:6])

                    # 세기 판단: '번호' 첫 자리(1,2,5,6=1900 / 3,4,7,8=2000). 없으면 휴리스틱
                    century = None
                    if reg_back_val:
                        rb = str(reg_back_val).strip().split('.')[0]
                        if len(rb) >= 1 and rb[0].isdigit():
                            gd = rb[0]
                            if gd in ("1", "2", "5", "6"):
                                century = 1900
                            elif gd in ("3", "4", "7", "8"):
                                century = 2000
                    if century is None:
                        curr_yy = datetime.date.today().year % 100
                        century = 1900 if yy > curr_yy else 2000

                    try:
                        d = datetime.date(century + yy, mm, dd)
                        return d.strftime("%Y-%m-%d")
                    except ValueError:
                        return ""

                # 생년월일 컬럼 생성
                df_alert_display_prepared_view['생년월일'] = df_customers_for_alert_view.apply(
                    lambda r: format_birthdate_alert_view(r.get('등록증'), r.get('번호')),
                    axis=1
                )

                # 등록증 만기 알림 (오늘 ~ 4개월 이내)
                df_customers_for_alert_view['등록증만기일_dt_alert'] = pd.to_datetime(
                    df_customers_for_alert_view.get('만기일'), errors='coerce'
                )
                today_ts = pd.Timestamp.today().normalize()
                card_alert_limit_date = today_ts + pd.DateOffset(months=4)
                
                card_alerts_df = df_customers_for_alert_view[
                    df_customers_for_alert_view['등록증만기일_dt_alert'].notna() &
                    (df_customers_for_alert_view['등록증만기일_dt_alert'] <= card_alert_limit_date) &
                    (df_customers_for_alert_view['등록증만기일_dt_alert'] >= today_ts)
                ].sort_values(by='등록증만기일_dt_alert')

                if not card_alerts_df.empty:
                    display_df_card_alert_view = df_alert_display_prepared_view.loc[card_alerts_df.index].copy()
                    display_df_card_alert_view['등록증만기일'] = card_alerts_df['등록증만기일_dt_alert'].dt.strftime('%Y-%m-%d')
                    st.dataframe(
                        display_df_card_alert_view[['한글이름', '등록증만기일', '여권번호', '생년월일', '전화번호']],
                        use_container_width=True, hide_index=True
                    )
                else:
                    st.write("(만기 예정 등록증 없음)") 
            
            st.subheader("3. 🛂 여권 만기 6개월 전")
            if df_customers_for_alert_view.empty:
                st.write("(표시할 고객 없음)")
            else:
                df_customers_for_alert_view['여권만기일_dt_alert'] = pd.to_datetime(
                    df_customers_for_alert_view.get('만기').astype(str).str.strip(),
                    errors='coerce'
                )
                today_ts = pd.Timestamp.today().normalize()
                passport_alert_limit_date = today_ts + pd.DateOffset(months=6)
                passport_alerts_df = df_customers_for_alert_view[ 
                    df_customers_for_alert_view['여권만기일_dt_alert'].notna() & 
                    (df_customers_for_alert_view['여권만기일_dt_alert'] <= passport_alert_limit_date) &
                    (df_customers_for_alert_view['여권만기일_dt_alert'] >= today_ts)
                ].sort_values(by='여권만기일_dt_alert')
                
                if not passport_alerts_df.empty:
                    display_df_passport_alert_view = df_alert_display_prepared_view.loc[passport_alerts_df.index].copy()
                    display_df_passport_alert_view['여권만기일'] = passport_alerts_df['여권만기일_dt_alert'].dt.strftime('%Y-%m-%d')
                    st.dataframe(
                        display_df_passport_alert_view[['한글이름', '여권만기일', '여권번호', '생년월일', '전화번호']],
                        use_container_width=True, hide_index=True
                    )
                else:
                    st.write("(만기 예정 여권 없음)") 

        # 4. 📌 예정업무 – ✏️ 버튼으로 저장/수정, 삭제 확인 포함
        st.markdown("---")
        st.subheader("4. 📌 예정업무")
        planned_tasks_editable_list = st.session_state.get(SESS_PLANNED_TASKS_TEMP, [])

        # 삭제 확인 인덱스 상태
        if "confirm_delete_idx" not in st.session_state:
            st.session_state["confirm_delete_idx"] = None

        # 정렬: 기간 → 날짜
        기간_옵션_plan_home_opts = ["장기🟢", "중기🟡", "단기🔴", "완료✅", "보류⏹️"]
        기간_우선순위_plan_home_map = {opt: i for i, opt in enumerate(기간_옵션_plan_home_opts)}
        planned_tasks_editable_list.sort(
            key=lambda x: (
                기간_우선순위_plan_home_map.get(x.get('period', " "), 99),
                pd.to_datetime(x.get('date', "9999-12-31"), errors='coerce')
            )
        )

        # 헤더
        h0, h1, h2, h3, h4, h5 = st.columns([0.8, 1, 4, 2, 0.5, 0.5])
        h0.write("**기간**"); h1.write("**날짜**"); h2.write("**내용**")
        h3.write("**비고**"); h4.write("**✏️ 수정**"); h5.write("**❌ 삭제**")

        # 행 렌더
        for idx_plan, task_item in enumerate(planned_tasks_editable_list):
            uid = task_item.get("id", str(idx_plan))
            cols = st.columns([0.8, 1, 4, 2, 0.5, 0.5])

            prev_p = task_item.get("period", 기간_옵션_plan_home_opts[0])
            new_p = cols[0].selectbox(" ", 기간_옵션_plan_home_opts,
                                      index=기간_옵션_plan_home_opts.index(prev_p) if prev_p in 기간_옵션_plan_home_opts else 0,
                                      key=f"plan_period_{uid}", label_visibility="collapsed")

            try:
                prev_d = datetime.datetime.strptime(task_item.get("date",""), "%Y-%m-%d").date()
            except:
                prev_d = datetime.date.today()
            new_d = cols[1].date_input(" ", value=prev_d,
                                       key=f"plan_date_{uid}", label_visibility="collapsed")

            prev_c = task_item.get("content","")
            new_c = cols[2].text_input(" ", value=prev_c,
                                       key=f"plan_content_{uid}", label_visibility="collapsed")

            prev_n = task_item.get("note","")
            new_n = cols[3].text_input(" ", value=prev_n,
                                       key=f"plan_note_{uid}", label_visibility="collapsed")

            # 수정
            if cols[4].button("✏️", key=f"plan_edit_{uid}", use_container_width=True):
                task_item.update({
                    "period": new_p,
                    "date":   new_d.strftime("%Y-%m-%d"),
                    "content": new_c,
                    "note":    new_n
                })
                st.session_state[SESS_PLANNED_TASKS_TEMP] = planned_tasks_editable_list
                save_planned_tasks_to_sheet(planned_tasks_editable_list)
                st.success(f"예정업무(ID:{uid}) 수정 저장됨")
                st.rerun()

            # 삭제 요청
            if cols[5].button("❌", key=f"plan_delete_{uid}", use_container_width=True):
                st.session_state["confirm_delete_idx"] = idx_plan

        # 삭제 확인 UI
        idx = st.session_state["confirm_delete_idx"]
        if idx is not None and 0 <= idx < len(planned_tasks_editable_list):
            task = planned_tasks_editable_list[idx]
            st.warning(f"예정업무(ID:{task['id']})를 삭제하시겠습니까?")
            c_yes, c_no = st.columns(2, gap="small")
            with c_yes:
                if st.button("✅ 예, 삭제합니다", key="confirm_yes", use_container_width=True):
                    planned_tasks_editable_list.pop(idx)
                    st.session_state[SESS_PLANNED_TASKS_TEMP] = planned_tasks_editable_list
                    save_planned_tasks_to_sheet(planned_tasks_editable_list)
                    st.session_state["confirm_delete_idx"] = None
                    st.rerun()
            with c_no:
                if st.button("❌ 아니오, 취소합니다", key="confirm_no", use_container_width=True):
                    st.session_state["confirm_delete_idx"] = None
                    st.rerun()

        # 추가 폼
        with st.form("add_planned_form_home_new", clear_on_submit=True):
            ac0, ac1, ac2, ac3, ac4 = st.columns([0.8,1,3,2,1])
            ap = ac0.selectbox("기간", 기간_옵션_plan_home_opts, key="add_plan_period_form", label_visibility="collapsed")
            ad = ac1.date_input("날짜", value=datetime.date.today(), key="add_plan_date_form", label_visibility="collapsed")
            ac = ac2.text_input("내용", key="add_plan_content_form", placeholder="업무 내용", label_visibility="collapsed")
            an = ac3.text_input("비고", key="add_plan_note_form", placeholder="참고 사항", label_visibility="collapsed")
            add_btn = ac4.form_submit_button("➕ 추가", use_container_width=True)

            if add_btn:
                if not ac:
                    st.warning("내용을 입력해주세요.")
                else:
                    planned_tasks_editable_list.append({
                        "id":      str(uuid.uuid4()),
                        "date":    ad.strftime("%Y-%m-%d"),
                        "period":  ap,
                        "content": ac,
                        "note":    an
                    })
                    st.session_state[SESS_PLANNED_TASKS_TEMP] = planned_tasks_editable_list
                    save_planned_tasks_to_sheet(planned_tasks_editable_list)
                    st.success("새 예정업무 추가됨")
                    st.rerun()

        st.markdown("---")
        # --- Active Tasks 섹션 ---
        st.subheader("5. 🛠️ 진행업무")
        
        active_tasks = st.session_state.get(SESS_ACTIVE_TASKS_TEMP, [])
        구분_옵션_active_opts = ["출입국", "전자민원", "공증", "여권", "초청", "영주권", "기타"]
        구분_우선순위_map = {opt: i for i, opt in enumerate(구분_옵션_active_opts)}
        
        # 정렬: 미처리 → 처리됨, 구분, 처리시각, 날짜
        active_tasks.sort(key=lambda x: (
            not x.get('processed', False),
            구분_우선순위_map.get(x.get('category', "기타"), 99),
            pd.to_datetime(x.get('processed_timestamp', ''), errors='coerce') if x.get('processed') else pd.Timestamp.min,
            pd.to_datetime(x.get('date', "9999-12-31"), errors='coerce')
        ))
        
        # 헤더
        h1, h2, h3, h4, h5, h6, h7, h8, h9, h10 = st.columns([0.8, 0.8, 0.8, 1, 1, 2.5, 0.5, 0.5, 0.5, 0.5], gap="small")
        h1.markdown("**구분**")
        h2.markdown("**진행일**")
        h3.markdown("**성명**")
        h4.markdown("**업무**")
        h5.markdown("**원본**")
        h6.markdown("**세부내용**")
        h7.markdown("**✏️ 수정**")
        h8.markdown("**🅿️ 처리**")
        h9.markdown("**✅ 완료**")
        h10.markdown("**❌ 삭제**")
        
        # 각 행
        for task in active_tasks:
            uid = task["id"]
            cols = st.columns([0.8, 0.8, 0.8, 1, 1, 2.5, 0.5, 0.5, 0.5, 0.5], gap="small")
        
            prev_category = task.get("category", 구분_옵션_active_opts[0])
            new_category = cols[0].selectbox(
                " ", options=구분_옵션_active_opts,
                index=구분_옵션_active_opts.index(prev_category) if prev_category in 구분_옵션_active_opts else 0,
                key=f"active_category_{uid}", label_visibility="collapsed"
            )
        
            try:
                prev_date = datetime.datetime.strptime(task.get("date", " "), "%Y-%m-%d").date()
            except:
                prev_date = datetime.date.today()
            new_date = cols[1].date_input(
                " ", value=prev_date,
                key=f"active_date_{uid}", label_visibility="collapsed"
            )
        
            prev_name = task.get("name", " ")
            new_name = cols[2].text_input(
                " ", value=prev_name, key=f"active_name_{uid}", label_visibility="collapsed"
            )
        
            prev_work = task.get("work", " ")
            if task.get("processed", False):
                cols[3].markdown(f"<span style='color:blue;'>{prev_work}</span>", unsafe_allow_html=True)
            else:
                new_work = cols[3].text_input(
                    " ", value=prev_work, key=f"active_work_{uid}", label_visibility="collapsed"
                )
        
            prev_src = task.get("source_original", " ")
            new_src = cols[4].text_input(
                " ", value=prev_src, key=f"active_source_{uid}", placeholder="원본 링크/파일", label_visibility="collapsed"
            )
        
            prev_details = task.get("details", " ")
            if task.get("processed", False):
                cols[5].markdown(f"<span style='color:blue;'>{prev_details}</span>", unsafe_allow_html=True)
            else:
                new_details = cols[5].text_input(
                    " ", value=prev_details, key=f"active_details_{uid}", label_visibility="collapsed"
                )
        
            # 수정
            if cols[6].button("✏️", key=f"active_edit_{uid}", use_container_width=True):
                full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
                for i, t in enumerate(full_list):
                    if t["id"] == uid:
                        t["category"]        = new_category
                        t["date"]            = new_date.strftime("%Y-%m-%d")
                        t["name"]            = new_name
                        if not t.get("processed", False):
                            t["work"]        = new_work
                            t["details"]     = new_details
                        t["source_original"] = new_src
                        break
                save_active_tasks_to_sheet(full_list)
                st.success("✅ 진행업무가 수정되어 저장되었습니다.")
                st.rerun()
        
            # 처리 토글
            if cols[7].button("🅿️", key=f"active_proc_{uid}", use_container_width=True, help="처리 상태 변경"):
                full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
                for i, t in enumerate(full_list):
                    if t["id"] == uid:
                        t["processed"] = not t.get("processed", False)
                        t["processed_timestamp"] = datetime.datetime.now().isoformat() if t["processed"] else " "
                        break
                save_active_tasks_to_sheet(full_list)
                st.info(f"진행업무(ID:{uid}) 처리 상태가 {'✅ 처리됨' if t['processed'] else '🕓 미처리'} 으로 변경되었습니다.")
                st.rerun()
        
            # 완료로 이동
            if cols[8].button("✅", key=f"active_complete_{uid}", use_container_width=True, help="완료 처리"):
                full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
                for i, t in enumerate(full_list):
                    if t["id"] == uid:
                        completed_item = full_list.pop(i)
                        completed_item["complete_date"] = datetime.date.today().strftime("%Y-%m-%d")
                        break
                completed_list = load_completed_tasks_from_sheet()
                completed_list.append(completed_item)
                save_completed_tasks_to_sheet(completed_list)
                st.session_state[SESS_ACTIVE_TASKS_TEMP] = full_list
                save_active_tasks_to_sheet(full_list)
                st.success("✅ 업무가 완료처리되어 ‘완료업무’ 페이지로 이동합니다.")
                st.rerun()
        
            # 삭제 요청
            if cols[9].button("❌", key=f"active_request_del_{uid}", use_container_width=True):
                st.session_state["active_delete_uid"] = uid
                st.rerun()

        # 삭제 확인 UI (루프 밖)
        if st.session_state.get("active_delete_uid"):
            del_uid = st.session_state["active_delete_uid"]
            st.warning(f"진행업무(ID:{del_uid})를 정말 삭제하시겠습니까?")
            c1, c2 = st.columns(2, gap="small")
            with c1:
                if st.button("✅ 예, 삭제", key=f"active_confirm_yes_{del_uid}", use_container_width=True):
                    full = st.session_state[SESS_ACTIVE_TASKS_TEMP]
                    new_list = [t for t in full if t["id"] != del_uid]
                    st.session_state[SESS_ACTIVE_TASKS_TEMP] = new_list
                    save_active_tasks_to_sheet(new_list)
                    del st.session_state["active_delete_uid"]
                    st.success("🗑️ 삭제되었습니다.")
                    st.rerun()
            with c2:
                if st.button("❌ 취소", key=f"active_confirm_no_{del_uid}", use_container_width=True):
                    del st.session_state["active_delete_uid"]
                    st.info("삭제가 취소되었습니다.")
                    st.rerun()
        
        # 추가 폼
        with st.form("add_active_form", clear_on_submit=True):
            cols_add = st.columns([0.8, 1, 1, 1, 1, 3, 1])
            add_category = cols_add[0].selectbox("구분", options=구분_옵션_active_opts, key="add_active_category", label_visibility="collapsed")
            add_date    = cols_add[1].date_input("진행일", value=datetime.date.today(), key="add_active_date", label_visibility="collapsed")
            add_name    = cols_add[2].text_input("성명", key="add_active_name", placeholder="성명", label_visibility="collapsed")
            add_work    = cols_add[3].text_input("업무", key="add_active_work", placeholder="업무 종류", label_visibility="collapsed")
            add_source  = cols_add[4].text_input("원본", key="add_active_source", placeholder="원본 링크/파일", label_visibility="collapsed")
            add_details = cols_add[5].text_input("세부내용", key="add_active_details", placeholder="세부 진행사항", label_visibility="collapsed")
            add_btn     = cols_add[6].form_submit_button("➕ 추가", use_container_width=True)
        
            if add_btn:
                if not add_name or not add_work:
                    st.warning("성명과 업무 내용을 입력해주세요.")
                else:
                    new_task = {
                        "id": str(uuid.uuid4()),
                        "category": add_category,
                        "date": add_date.strftime("%Y-%m-%d"),
                        "name": add_name,
                        "work": add_work,
                        "source_original": add_source,
                        "details": add_details,
                        "processed": False,
                        "processed_timestamp": " "
                    }
                    st.session_state[SESS_ACTIVE_TASKS_TEMP].append(new_task)
                    save_active_tasks_to_sheet(st.session_state[SESS_ACTIVE_TASKS_TEMP])
                    st.success("➕ 새 진행업무가 추가되었습니다.")
                    st.rerun()
    
else: 
    print("Streamlit is not available. Cannot run the application.")
    print(f"Key path configured: {KEY_PATH}")
    print("To run, ensure Streamlit is installed ('pip install streamlit') and run 'streamlit run your_script_name.py'")
