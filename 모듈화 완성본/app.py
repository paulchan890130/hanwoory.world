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

from pages.page_customer import render as render_customer_page
from pages.page_home import render as render_home_page
from pages.page_daily import render as render_daily_page
from pages.page_monthly import render as render_monthly_page
from pages.page_manual import render as render_manual_page
from pages.page_memo import render as render_memo_page
from pages.page_reference import render as render_reference_page
from pages.page_document import render as render_document_page
from pages import page_scan
from pages import page_completed

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
    elif current_page_to_display == PAGE_MANUAL:
        render_manual_page()

    # -----------------------------
    # ✅ Memo Page
    # -----------------------------
    elif current_page_to_display == PAGE_MEMO:
        render_memo_page()

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
    # ✅ Home Page (Main Dashboard)
    # -----------------------------
    elif current_page_to_display == PAGE_HOME:
        render_home_page()

else: 
    print("Streamlit is not available. Cannot run the application.")
    print(f"Key path configured: {KEY_PATH}")
    print("To run, ensure Streamlit is installed ('pip install streamlit') and run 'streamlit run your_script_name.py'")
