# config.py
import os
import platform

# ===== 드라이브/도장 등 경로 상수 =====
PARENT_DRIVE_FOLDER_ID = "1vAT3OvELPhosJ99Zg1fJ5hKJEgx7kNlW"

circle_path = "templates/원형 배경.png"
font_path   = "Fonts/HJ한전서B.ttf"
seal_size   = 200

# ===== 구글 서비스 계정 키 경로 =====
if platform.system() == "Windows":
    KEY_PATH = r"C:\Users\윤찬\한우리 현행업무\프로그램\출입국업무관리\hanwoory-9eaa1a4c54d7.json"
else:
    KEY_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/etc/secrets/hanwoory-9eaa1a4c54d7.json")

# ===== 메인 스프레드시트 키 =====
SHEET_KEY = "14pEPo-Q3aFgbS1Gqcamb2lkadq-eFlOrQ-wST3EU1pk"

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

# ===== 공용 헬퍼 =====
def safe_int(val):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0
