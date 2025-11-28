# config.py
import os
import platform

# 로컬 / 서버 모드 구분
RUN_ENV = os.getenv("HANWOORY_ENV", "local")  # 기본값: local
TENANT_MODE = (RUN_ENV == "server")

if platform.system() == "Windows":
    # 로컬에서 쓰는 경로
    OAUTH_CLIENT_SECRET_PATH = r"C:\Users\윤찬\K.ID 출입국업무관리\client_secret_desktop.json"
    OAUTH_TOKEN_PATH = r"C:\Users\윤찬\K.ID 출입국업무관리\token.json"
else:
    # Render 같은 리눅스 서버에서 쓸 경로 (Secret Files로 맞춰줄 예정)
    OAUTH_CLIENT_SECRET_PATH = "/etc/secrets/client_secret_desktop.json"
    OAUTH_TOKEN_PATH = "/etc/secrets/token.json"

# ===== 드라이브/도장 등 경로 상수 =====
PARENT_DRIVE_FOLDER_ID = "1OX5tH9MOYz9leeYJ_KIrBHWBZxMXjb16"

circle_path = "templates/원형 배경.png"
font_path   = "Fonts/HJ한전서B.ttf"
seal_size   = 200

# ===== 고객 폴더 기능 옵션 =====
# 기본값: 폴더 기능 비활성화 (Admin이 True로 바꿔서 사용)
ENABLE_CUSTOMER_FOLDERS = False

# ===== 테넌트 / 로그인 기본 설정 =====
# 지금은 단일 사무소만 쓰지만, 나중에 멀티테넌트 확장할 때 쓸 플래그
SESS_LOGGED_IN = "logged_in"
SESS_USERNAME = "username"

TENANT_MODE = False          # 나중에 True로 바꾸면 멀티테넌트 모드
DEFAULT_TENANT_ID = "hanwoory"
SESS_TENANT_ID = "sess_tenant_id"

SESS_LOGGED_IN = "logged_in"
SESS_USERNAME = "username"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===== 구글 서비스 계정 키 경로 =====
if platform.system() == "Windows":
    KEY_PATH = r"C:\Users\윤찬\한우리 현행업무\프로그램\출입국업무관리\hanwoory-9eaa1a4c54d7.json"
else:
    KEY_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/etc/secrets/hanwoory-9eaa1a4c54d7.json")

# ===== 업무정리 / 고객데이터 템플릿 =====
WORK_REFERENCE_TEMPLATE_ID = "1KxZY_VGUfGjo8nWn1d01OVN007uTpbLSnNLX3Jf62nE"
SHEET_KEY = "1W7myK9dOQQnN3BAVLzFR_kjV91LSmKzO_9v7lWS0bI8"

# ===== 테넌트용 템플릿 스프레드시트 ID =====
# 새 사무실 생성 시, 아래 두 파일을 복사해서 사용한다.
CUSTOMER_DATA_TEMPLATE_ID  = "1UhMUpSJif54NqJXapQBe7DxyvbCil3RNiQDPhUgjNec"
WORK_REFERENCE_TEMPLATE_ID = "1p7Xt9x8TxVwQHzfiyTmCppvYSOuLHMTJmwdCErZ8KX4"

# --- 계정/테넌트 관련 ---
ACCOUNTS_SHEET_NAME = "Accounts"

TENANT_MODE = False   # 기본: 단일 테넌트
DEFAULT_TENANT_ID = "hanwoory"

SESS_TENANT_ID  = "sess_tenant_id"
SESS_LOGGED_IN  = "sess_logged_in"
SESS_USERNAME   = "sess_username"
SESS_IS_ADMIN   = "sess_is_admin"

# ===== Sheet Tab Names =====
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

# ===== Session Keys =====
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

# ===== Page Keys =====
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
PAGE_ADMIN_ACCOUNTS = 'admin_accounts'


# ===== 공용 헬퍼 =====
def safe_int(val):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0
