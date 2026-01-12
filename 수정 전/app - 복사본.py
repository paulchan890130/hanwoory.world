# ===== Imports (정리본) =====
import os, platform, io, json, uuid, calendar, pytesseract
import datetime
import streamlit as st
import requests
import pandas as pd
import gspread
import hashlib, os, base64, hmac
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

from pages.page_customer import render as render_customer_page
from pages.page_home import render as render_home_page
from pages.page_daily import render as render_daily_page
from pages.page_monthly import render as render_monthly_page
# from pages.page_manual import render as render_manual_page
from pages.page_memo import render as render_memo_page
from pages.page_reference import render as render_reference_page
from pages.page_document import render as render_document_page
from pages import page_scan
from pages import page_completed

from config import RUN_ENV, TENANT_MODE

# ==== OCR ====
try:
    import pytesseract
except Exception:
    pytesseract = None

from config import (
    # ===== 시트 키 및 시트 이름 =====
    SHEET_KEY,
    CUSTOMER_SHEET_NAME,
    DAILY_SUMMARY_SHEET_NAME,
    DAILY_BALANCE_SHEET_NAME,
    PLANNED_TASKS_SHEET_NAME,
    ACTIVE_TASKS_SHEET_NAME,
    COMPLETED_TASKS_SHEET_NAME,
    EVENTS_SHEET_NAME,
    MEMO_LONG_SHEET_NAME,
    MEMO_MID_SHEET_NAME,
    MEMO_SHORT_SHEET_NAME,

    # ===== 드라이브/도장 관련 상수 =====
    PARENT_DRIVE_FOLDER_ID,

    # ===== 세션 키 =====
    SESS_LOGGED_IN,
    SESS_USERNAME,
    SESS_TENANT_ID,
    DEFAULT_TENANT_ID,
    ACCOUNTS_SHEET_NAME,
    SESS_IS_ADMIN,          # 🔹 추가
    SESS_CURRENT_PAGE,
    SESS_DF_CUSTOMER,
    SESS_CUSTOMER_SEARCH_TERM,
    SESS_CUSTOMER_SEARCH_MASK_INDICES,
    SESS_CUSTOMER_SELECTED_ROW_IDX,
    SESS_CUSTOMER_AWAITING_DELETE_CONFIRM,
    SESS_CUSTOMER_PENDING_DELETE_DISPLAY_IDX,
    SESS_CUSTOMER_DELETED_ROWS_STACK,
    SESS_CUSTOMER_DATA_EDITOR_KEY,
    SESS_DAILY_SELECTED_DATE,
    SESS_DAILY_DATE_INPUT_KEY,
    SESS_DAILY_TEMP_DATA,
    SESS_ALL_DAILY_ENTRIES_PAGE_LOAD,
    SESS_EVENTS_DATA_HOME,
    SESS_HOME_SELECTED_YEAR,
    SESS_HOME_SELECTED_MONTH,
    SESS_HOME_CALENDAR_SELECTED_DATE,
    SESS_PLANNED_TASKS_TEMP,
    SESS_ACTIVE_TASKS_TEMP,
    SESS_DOC_SELECTED_CUSTOMER_NAME,
    SESS_DOC_SELECTED_CUSTOMER_DATA,

    # ===== 페이지 키 =====
    PAGE_HOME,
    PAGE_MEMO,
    PAGE_REFERENCE,
    PAGE_CUSTOMER,
    PAGE_DAILY,
    PAGE_MONTHLY,
    PAGE_MANUAL,
    PAGE_DOCUMENT,
    PAGE_COMPLETED,
    PAGE_SCAN,
    PAGE_ADMIN_ACCOUNTS,
    PAGE_BOARD,

    # ===== 공용 함수 =====
    safe_int,
)

from core.google_sheets import (
    get_gspread_client,
    get_drive_service,
    get_worksheet,
    write_data_to_sheet,
    append_rows_to_sheet,
    read_data_from_sheet,
    read_memo_from_sheet,
    save_memo_to_sheet,
)
from core.customer_service import (
    load_customer_df_from_sheet,
    save_customer_batch_update,
    upsert_customer_from_scan,
    create_customer_folders,
    extract_folder_id,
)

# ==== OCR ====  (위 import 근처에 미리 추가)
if platform.system() == "Windows":
    TESSERACT_ROOT = r"C:\Program Files\Tesseract-OCR"
    TESSERACT_EXE  = os.path.join(TESSERACT_ROOT, "tesseract.exe")
    TESSDATA_DIR   = os.path.join(TESSERACT_ROOT, "tessdata")  # 참고용
else:
    # Linux(Docker)에서는 패키지로 설치된 tesseract 사용
    pytesseract.pytesseract.tesseract_cmd = "tesseract"


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

# -----------------------------
# ✅ Application Specific Data Load/Save Functions
# -----------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return base64.b64encode(salt + dk).decode("ascii")

def verify_password(password: str, hashed: str) -> bool:
    try:
        raw = base64.b64decode(hashed.encode("ascii"))
        salt, dk = raw[:16], raw[16:]
        new_dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(dk, new_dk)
    except Exception:
        return False


def create_office_account_via_signup(
    login_id: str,
    raw_pw: str,
    office_name: str,
    contact_name: str = "",
    contact_tel: str = "",
    biz_reg_no: str = "",
    agent_rrn: str = "",
    office_adr: str = "",
):
    """
    일반 사무실에서 회원가입 탭을 통해 계정 신청할 때 호출.
    - Accounts 시트에 한 줄 추가
    - 기본값:
        is_admin  = FALSE  (전역 관리자 아님)
        is_active = FALSE  (관리자 승인 전까지 로그인 불가)
    """
    login_id = (login_id or "").strip()
    office_name = (office_name or "").strip()
    biz_reg_no = (biz_reg_no or "").strip()
    agent_rrn = (agent_rrn or "").strip()

    if not login_id:
        raise ValueError("로그인 ID가 비어 있습니다.")
    if not raw_pw:
        raise ValueError("비밀번호가 비어 있습니다.")
    if not office_name:
        raise ValueError("사무실 이름이 비어 있습니다.")

    # 1) 기존 계정 목록 읽기
    records = read_data_from_sheet(ACCOUNTS_SHEET_NAME, default_if_empty=[]) or []

    # 2) login_id 중복 체크
    for r in records:
        if str(r.get("login_id", "")).strip() == login_id:
            # ✅ 폴찬이 원하는 문구
            raise ValueError("동일한 ID가 존재합니다. 다른 ID로 가입신청해 주십시오.")
        
    # 3) header_list 결정 (기존 시트가 있으면 그 구조를 따라감)
    if records:
        header_list = list(records[0].keys())
    else:
        # 시트가 비어 있는 경우: 기본 헤더 정의
        header_list = [
            "login_id",
            "password_hash",
            "tenant_id",
            "office_name",
            "contact_name",
            "contact_tel",
            "biz_reg_no",
            "agent_rrn",
            "is_admin",
            "is_active",
            "folder_id",
            "work_sheet_key",
            "customer_sheet_key",
            "created_at",
        ]

    # 4) 기본값 딕셔너리 만들고 필요한 값 채우기
    new_rec = {h: "" for h in header_list}

    new_rec["login_id"] = login_id
    new_rec["password_hash"] = hash_password(raw_pw)
    # 우선은 tenant_id = login_id (나중에 slug 처리 등 가능)
    if "tenant_id" in new_rec:
        new_rec["tenant_id"] = login_id
    if "office_name" in new_rec:
        new_rec["office_name"] = office_name
    if "contact_name" in new_rec:
        new_rec["contact_name"] = contact_name
    if "contact_tel" in new_rec:
        new_rec["contact_tel"] = contact_tel
    if "biz_reg_no" in new_rec:
        new_rec["biz_reg_no"] = biz_reg_no
    if "office_adr" in new_rec:
        new_rec["office_adr"] = office_adr
    if "agent_rrn" in new_rec:
        new_rec["agent_rrn"] = agent_rrn
        
    if "is_admin" in new_rec:
        new_rec["is_admin"] = "FALSE"
    if "is_active" in new_rec:
        new_rec["is_active"] = "FALSE"

    if "folder_id" in new_rec:
        new_rec["folder_id"] = ""
    if "work_sheet_key" in new_rec:
        new_rec["work_sheet_key"] = ""
    if "customer_sheet_key" in new_rec:
        new_rec["customer_sheet_key"] = ""

    if "created_at" in new_rec:
        new_rec["created_at"] = datetime.date.today().isoformat()

    ok = append_rows_to_sheet(
        ACCOUNTS_SHEET_NAME,
        [new_rec],           # dict 1개를 리스트로 감싸서 전달
        header_list=header_list,
    )
    if not ok:
        raise RuntimeError("Accounts 시트에 신규 계정을 추가하지 못했습니다.")

    # Accounts가 바뀌었으니, 테넌트 sheet_key 캐시를 초기화
    try:
        from core.google_sheets import _load_tenant_sheet_keys
        _load_tenant_sheet_keys.clear()
    except Exception:
        st.cache_data.clear()


def find_account(login_id: str):
    records = read_data_from_sheet(ACCOUNTS_SHEET_NAME, default_if_empty=[])
    for r in records:
        if str(r.get("login_id", "")).strip() == login_id.strip():
            return r
    return None

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

def save_planned_tasks_to_sheet(tenant_id, data_list_of_dicts):
    """
    예정업무: 전체 덮어쓰기 대신, id 기준 upsert
    """
    sheet_key = get_sheet_key_for_tenant(tenant_id)
    header = ['id', 'date', 'period', 'content', 'note']

    # string 변환(넣기 전에 정리)
    normalized = []
    for r in data_list_of_dicts:
        rec = {}
        for col in header:
            rec[col] = "" if r.get(col) is None else str(r.get(col))
        normalized.append(rec)

    ok = upsert_rows_by_id(sheet_key, PLANNED_TASKS_SHEET_NAME,
                           header_list=header,
                           records=normalized,
                           id_field="id")
    if ok:
        load_planned_tasks_from_sheet.clear()
    return ok


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

def save_active_tasks_to_sheet(tenant_id, data_list_of_dicts):
    header = [
        'id', 'category', 'date', 'name', 'work',
        'source_original', 'details', 'processed', 'processed_timestamp'
    ]
    sheet_key = get_sheet_key_for_tenant(tenant_id)
    normalized = []
    for r in data_list_of_dicts:
        rec = {}
        for col in header:
            rec[col] = "" if r.get(col) is None else str(r.get(col))
        normalized.append(rec)

    ok = upsert_rows_by_id(sheet_key, ACTIVE_TASKS_SHEET_NAME,
                           header_list=header,
                           records=normalized,
                           id_field="id")
    if ok:
        load_active_tasks_from_sheet.clear()
    return ok

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

def save_completed_tasks_to_sheet(tenant_id, records):
    header = ['id', 'category', 'date', 'name', 'work',
              'source_original', 'details', 'complete_date']
    sheet_key = get_sheet_key_for_tenant(tenant_id)
    normalized = []
    for r in records:
        rec = {}
        for col in header:
            rec[col] = "" if r.get(col) is None else str(r.get(col))
        normalized.append(rec)

    ok = upsert_rows_by_id(sheet_key, COMPLETED_TASKS_SHEET_NAME,
                           header_list=header,
                           records=normalized,
                           id_field="id")
    if ok:
        load_completed_tasks_from_sheet.clear()
    return ok

# -----------------------------
# ✅ Streamlit App Logic
# -----------------------------

# --- Font Setup for Matplotlib ---
def setup_matplotlib_font():
    font_path_linux = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
    font_path_windows = "C:/Windows/Fonts/malgun.ttf"  # Malgun Gothic for Windows
    font_path_macos = "/System/Library/Fonts/AppleSDGothicNeo.ttc"  # Apple SD Gothic Neo for macOS

    font_path = None
    try:
        if platform.system() == "Windows":
            if os.path.exists(font_path_windows):
                font_path = font_path_windows
        elif platform.system() == "Darwin":  # macOS
            if os.path.exists(font_path_macos):
                font_path = font_path_macos
            else:
                # macOS에서 아무 한글폰트나 찾아보기 (없으면 그냥 패스)
                font_list = fm.findSystemFonts(fontpaths=None, fontext='ttf')
                for f in font_list:
                    if "Gothic" in f or "Nanum" in f or "AppleSDGothic" in f:
                        font_path = f
                        break
        else:  # Linux or other
            if os.path.exists(font_path_linux):
                font_path = font_path_linux

        if font_path:
            font_prop = fm.FontProperties(fname=font_path)
            plt.rcParams["font.family"] = font_prop.get_name()
            plt.rcParams["axes.unicode_minus"] = False
        # 폰트를 못 찾으면 그냥 기본 폰트 사용 (아무 메시지도 안 띄움)
    except Exception:
        # 폰트 설정 중 에러 나도 조용히 무시
        pass


if st:
    setup_matplotlib_font()  # Setup font once
    st.set_page_config(
        page_title="출입국 업무관리",
        layout="wide",
        initial_sidebar_state="collapsed",   # ✅ 처음에는 접힌 상태
    )

    # ===== 세션 기본값 설정 (로그인 관련) =====
    if SESS_LOGGED_IN not in st.session_state:
        st.session_state[SESS_LOGGED_IN] = False

    if SESS_USERNAME not in st.session_state:
        st.session_state[SESS_USERNAME] = ""

    if SESS_TENANT_ID not in st.session_state:
        st.session_state[SESS_TENANT_ID] = DEFAULT_TENANT_ID

    if SESS_IS_ADMIN not in st.session_state:
        st.session_state[SESS_IS_ADMIN] = False

    if SESS_CURRENT_PAGE not in st.session_state:
        st.session_state[SESS_CURRENT_PAGE] = PAGE_HOME

    # ===== 로그인 / 회원가입 화면 =====
    if not st.session_state[SESS_LOGGED_IN]:
        st.title("🔐 K.ID 출입국 업무관리")

        if "signup_message" in st.session_state:
            st.success(st.session_state["signup_message"])
            del st.session_state["signup_message"]

        tab_login, tab_signup = st.tabs(["로그인", "사무실 회원가입"])

        # ---------- 탭 1: 로그인 ----------
        with tab_login:
            st.subheader("로그인")

            with st.form("login_form"):
                username = st.text_input("ID")
                password = st.text_input("비밀번호", type="password")
                submitted = st.form_submit_button("로그인")

            if submitted:
                acc = find_account(username)

                if not acc:
                    st.error("계정이 존재하지 않습니다.")
                else:
                    is_active = str(acc.get("is_active", "")).strip().lower() in ("true", "1", "y")
                    if not is_active:
                        st.error("비활성화된 계정입니다. (관리자 승인 전이거나 사용 중지된 계정)")
                    else:
                        hashed = str(acc.get("password_hash", "")).strip()
                        if not hashed or not verify_password(password, hashed):
                            st.error("ID 또는 비밀번호가 올바르지 않습니다.")
                        else:
                            is_admin_flag = str(acc.get("is_admin", "")).strip().lower() in ("true", "1", "y")
                            tenant_id = acc.get("tenant_id") or DEFAULT_TENANT_ID

                            st.session_state[SESS_LOGGED_IN] = True
                            st.session_state[SESS_USERNAME]  = username
                            st.session_state[SESS_TENANT_ID] = tenant_id
                            st.session_state[SESS_IS_ADMIN]  = is_admin_flag
                            st.rerun()

        # ---------- 탭 2: 사무실 회원가입 ----------
        with tab_signup:
            st.subheader("사무실 회원가입")

            st.markdown(
                "- 이 화면은 **새로운 행정사 사무소**가 K.ID 업무관리 시스템을 사용하기 위해 계정을 신청하는 용도입니다.<br>"
                "- 가입 후에는 관리자가 승인을 해야 로그인 가능합니다.<br>"
                "- 사업자등록번호 및 주민등록번호 등 개인정보는 문서작성 자동화 시스템에서 대리인 정보를 자동 기입하기 위해 사용하는 것으로, 해당 기능을 사용하지 않는 경우 입력하지 않으셔도 무방합니다.",
                unsafe_allow_html=True,
            )

            with st.form("signup_form"):
                # 1) 대행기관명 (사무실명)
                office_name  = st.text_input("대행기관명 (사무실명) *")
                office_adr   = st.text_input("사무실 주소")

                # 2) 사업자등록번호 (샘플은 음영으로 보이도록 placeholder)
                biz_reg_no   = st.text_input(
                    "사업자등록번호",
                    placeholder="000-00-00000",
                )

                # 3) 행정사 주민등록번호 (필요시만 입력)
                agent_rrn    = st.text_input(
                    "행정사 주민등록번호",
                    placeholder="000000-0000000",
                )

                # 4) 행정사 성명 / 연락처
                contact_name = st.text_input("행정사 성명", value="")
                contact_tel  = st.text_input(
                    "연락처 (전화번호)",
                    value="",
                    placeholder="010-0000-0000",
                )

                # 5) 로그인 ID / 비밀번호
                login_id_new = st.text_input("로그인 ID (영문/숫자 권장) *")
                pw1 = st.text_input("비밀번호 *", type="password")
                pw2 = st.text_input("비밀번호 확인 *", type="password")

                submitted_signup = st.form_submit_button("회원가입 요청")

            if submitted_signup:
                errors = []
                if not office_name.strip():
                    errors.append("사무실 이름을 입력해주세요.")
                if not login_id_new.strip():
                    errors.append("로그인 ID를 입력해주세요.")
                if not pw1:
                    errors.append("비밀번호를 입력해주세요.")
                if pw1 != pw2:
                    errors.append("비밀번호 확인이 일치하지 않습니다.")

                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    try:
                        create_office_account_via_signup(
                            login_id=login_id_new,
                            raw_pw=pw1,
                            office_name=office_name,
                            contact_name=contact_name,
                            contact_tel=contact_tel,
                            biz_reg_no=biz_reg_no,
                            agent_rrn=agent_rrn,
                            office_adr=office_adr,
                        )
                        st.session_state["signup_message"] = (
                            "가입신청이 완료되었습니다. 본 프로그램은 정식 영업중인 행정사를 위한 프로그램으로 "
                            "사업자등록증, 행정사업무신고확인증, 사업장 사진(3장 이상)을 "
                            "chan@hanwoory.world 로 보내주시면 확인 후 승인해 드리겠습니다."
                        )
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"회원가입 중 오류가 발생했습니다: {e}")

        # 로그인/회원가입 화면에서는 여기서 종료
        st.stop()

    # ===== 여기부터는 '로그인된 상태'에서만 실행 =====
    tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)

    # 테넌트별 데이터 로딩 (고객 / 예정 / 진행)
    if SESS_DF_CUSTOMER not in st.session_state:
        st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet(tenant_id)

    if SESS_PLANNED_TASKS_TEMP not in st.session_state:
        st.session_state[SESS_PLANNED_TASKS_TEMP] = load_planned_tasks_from_sheet()

    if SESS_ACTIVE_TASKS_TEMP not in st.session_state:
        st.session_state[SESS_ACTIVE_TASKS_TEMP] = load_active_tasks_from_sheet()

    # 사이드바 / 로그아웃
    with st.sidebar:
        st.caption(f"👤 {st.session_state.get(SESS_USERNAME, '')}")
        if st.button("로그아웃"):
            for key in [
                SESS_LOGGED_IN,
                SESS_USERNAME,
                SESS_TENANT_ID,
                SESS_IS_ADMIN,
                SESS_DF_CUSTOMER,
                SESS_PLANNED_TASKS_TEMP,
                SESS_ACTIVE_TASKS_TEMP,
            ]:
                st.session_state.pop(key, None)
            st.rerun()

    # 공통 스타일 + 디버그 캡션
    st.markdown("""
    <style>
      [data-testid="stVerticalBlock"] > div { margin-bottom: 0px !important; }
      [data-testid="stColumns"] { margin-bottom: 0px !important; }
    </style>
    """, unsafe_allow_html=True)

    st.sidebar.caption(
        f"ENV={RUN_ENV}, TENANT_MODE={TENANT_MODE}, "
        f"tenant={st.session_state.get(SESS_TENANT_ID, '-')}"
    )

    title_col, toolbar_col = st.columns([2, 3])
    with title_col:
        st.title("📋 출입국 업무관리")

    with toolbar_col:
        toolbar_options = {
            "🏠 홈으로": PAGE_HOME,
            "🗒 메모장": PAGE_MEMO,
            "📚 업무": PAGE_REFERENCE,
            "👥 고객관리": PAGE_CUSTOMER,
            "📊 결산": PAGE_DAILY,
            "🧭 메뉴얼 검색": PAGE_MANUAL,
            "📢 게시판": PAGE_BOARD,
        }

        if st.session_state.get(SESS_IS_ADMIN, False):
            toolbar_options["🧩 계정관리"] = PAGE_ADMIN_ACCOUNTS

        num_buttons = len(toolbar_options)
        btn_cols = st.columns(num_buttons)
        for idx, (label, page_key) in enumerate(toolbar_options.items()):
            col = btn_cols[idx]

            if page_key == PAGE_MANUAL:
                col.link_button(
                    label,
                    "https://www.hikorea.go.kr/board/BoardNtcDetailR.pt?BBS_SEQ=1&BBS_GB_CD=BS10&NTCCTT_SEQ=1062&page=1",
                    use_container_width=True,
                )
            else:
                if col.button(label, key=f"nav-{page_key}-{idx}", use_container_width=True):
                    st.session_state[SESS_CURRENT_PAGE] = page_key
                    st.rerun()

    st.markdown("---") 

    current_page_to_display = st.session_state[SESS_CURRENT_PAGE]

    # -----------------------------
    # ✅ Customer Management Page
    # -----------------------------
    current_page_to_display = st.session_state[SESS_CURRENT_PAGE]

    if current_page_to_display == PAGE_CUSTOMER:
        render_customer_page()

    # -----------------------------
    # ✅ Daily Summary Page
    # -----------------------------
    elif current_page_to_display == PAGE_DAILY:
        render_daily_page()
        
    # -----------------------------
    # ✅ Monthly Summary Page
    # -----------------------------
    elif current_page_to_display == PAGE_MONTHLY:
        render_monthly_page()
        
    # -----------------------------
    # ✅ Scan Page (여권/등록증 OCR → 고객 자동 추가/수정)
    # -----------------------------
    elif current_page_to_display == PAGE_SCAN:
        page_scan.render()

    # -----------------------------
    # ✅ Manual Search Page
    # -----------------------------
    # elif current_page_to_display == PAGE_MANUAL:
    #    render_manual_page()

    # -----------------------------
    # ✅ Memo Page
    # -----------------------------
    elif current_page_to_display == PAGE_MEMO:
        render_memo_page()

    # ✅ Board Page (게시판)
    elif current_page_to_display == PAGE_BOARD:
        from pages import page_board
        page_board.render()

    # -----------------------------
    # ✅ Document Automation Page (수정된 부분)
    # -----------------------------
    elif current_page_to_display == PAGE_DOCUMENT:
        render_document_page()

    # -----------------------------
    # ✅ Reference Page
    # -----------------------------
    elif current_page_to_display == PAGE_REFERENCE:
        render_reference_page()

    # -----------------------------
    # ✅ Completed Tasks Page
    # -----------------------------
    elif current_page_to_display == PAGE_COMPLETED:
        page_completed.render()

    # -----------------------------
    # ✅ admin page
    # -----------------------------
    elif current_page_to_display == PAGE_ADMIN_ACCOUNTS:
        from pages import page_admin_accounts
        page_admin_accounts.render()

    # -----------------------------
    # ✅ Home Page (Main Dashboard)
    # -----------------------------
    elif current_page_to_display == PAGE_HOME:
        render_home_page()

else: 
    print("Streamlit is not available. Cannot run the application.")
    print(f"Key path configured: {KEY_PATH}")
    print("To run, ensure Streamlit is installed ('pip install streamlit') and run 'streamlit run your_script_name.py'")
