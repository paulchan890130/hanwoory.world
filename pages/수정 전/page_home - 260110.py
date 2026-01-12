# pages/page_home.py

import datetime
import uuid
import calendar as pycal

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar as st_calendar  # 👈 추가

from config import (
    # 세션 상태 키
    SESS_DF_CUSTOMER,
    SESS_TENANT_ID,
    DEFAULT_TENANT_ID,
    SESS_PLANNED_TASKS_TEMP,
    SESS_ACTIVE_TASKS_TEMP,
    SESS_EVENTS_DATA_HOME,          
    SESS_HOME_SELECTED_YEAR,        
    SESS_HOME_SELECTED_MONTH,       
    SESS_HOME_CALENDAR_SELECTED_DATE,  
    # 시트 이름
    MEMO_SHORT_SHEET_NAME,
    EVENTS_SHEET_NAME,
    MEMO_SHORT_SHEET_NAME,
    EVENTS_SHEET_NAME,
    PLANNED_TASKS_SHEET_NAME,
    ACTIVE_TASKS_SHEET_NAME,
    COMPLETED_TASKS_SHEET_NAME,        
)

from core.google_sheets import (
    read_memo_from_sheet,
    save_memo_to_sheet,
    read_data_from_sheet,
    upsert_rows_by_id,   
    append_rows_to_sheet,  
    get_gspread_client,    
    get_worksheet,         
    upsert_rows_by_id,  
    delete_row_by_id, 
)

from core.customer_service import (
    load_customer_df_from_sheet,
)

def _extract_selected_date(date_raw) -> str | None:
    """
    캘린더 콜백에서 넘어온 dateStr / startStr 등을
    한국 시간(KST, UTC+9) 기준 YYYY-MM-DD 문자열로 맞춰준다.
    """
    if not date_raw:
        return None

    s = str(date_raw)

    # 이미 'YYYY-MM-DD' 형태면 그대로 사용
    if len(s) >= 10 and s[4] == "-" and s[7] == "-" and "T" not in s:
        return s[:10]

    try:
        # ...Z 로 끝나면 ISO 포맷으로 바꿔줌
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        dt = datetime.datetime.fromisoformat(s)  # ✅ 모듈.datetime

        # timezone 정보가 없으면 그냥 date 기준
        if dt.tzinfo is None:
            return dt.date().isoformat()

        # 한국(KST, UTC+9) 기준 날짜로 변환
        kst = datetime.timezone(datetime.timedelta(hours=9))  # ✅ 모듈.timezone/timedelta
        local_dt = dt.astimezone(kst)
        return local_dt.date().isoformat()

    except Exception:
        # 이상하면 일단 앞 10글자만 사용
        return s[:10]

# ─────────────────────────────
# 0-1) 일정(달력) 관련 상수 / 헬퍼
# ─────────────────────────────

SESS_HOME_CAL_YEAR = "home_calendar_year"
SESS_HOME_CAL_MONTH = "home_calendar_month"
SESS_HOME_CAL_SELECTED_DATE = "home_calendar_selected_date"


# ─────────────────────────────
# 0-1) 달력용 일정 로딩/저장 헬퍼 (Google Sheets '일정' 시트 사용)
# ─────────────────────────────
from streamlit_calendar import calendar

try:
    import holidays as _holidays
    KR_HOLIDAYS = _holidays.KR()
    CN_HOLIDAYS = _holidays.China()
except Exception:
    KR_HOLIDAYS = None
    CN_HOLIDAYS = None

st.session_state.setdefault("home_calendar_nonce", 0)

@st.cache_data(ttl=300)
def load_calendar_events_for_tenant(tenant_id: str) -> dict:
    """현재 테넌트의 '일정' 시트를 읽어서 { 'YYYY-MM-DD': [메모1, 메모2, ...] } 형태로 반환."""
    rows = read_data_from_sheet(EVENTS_SHEET_NAME, default_if_empty=[])
    events_by_date: dict[str, list[str]] = {}
    if not rows:
        return {}

    for r in rows:
        # 날짜 컬럼: 옛날/새 이름 모두 대응
        raw_date = str(
            r.get("date")
            or r.get("date_str")
            or r.get("날짜")
            or r.get("일자")
            or ""
        ).strip()
        if not raw_date:
            continue
        date_str = raw_date[:10]

        # 메모 컬럼: 옛날/새 이름 모두 대응
        memo_raw = str(
            r.get("memo")
            or r.get("event_text")
            or r.get("메모")
            or r.get("내용")
            or ""
        ).strip()
        if not memo_raw:
            continue

        # 여러 줄 메모 → 줄 단위로 쪼개기
        lines = [ln.strip() for ln in memo_raw.splitlines() if ln.strip()]
        if not lines:
            continue

        events_by_date.setdefault(date_str, []).extend(lines)

    return events_by_date


def _ensure_events_header(ws):
    """'일정' 시트에 헤더(date, memo)가 없으면 A1:B1 에만 헤더를 세팅 (기존 데이터는 건드리지 않음)."""
    try:
        values = ws.get_values("A1:B1")
    except Exception:
        values = []
    if not values or not values[0]:
        ws.update("A1:B1", [["date", "memo"]])


def save_calendar_events_for_date(date_str: str, lines: list[str]) -> bool:
    """특정 날짜의 메모 전체를 교체 저장.
    - lines 에 내용이 있으면 해당 날짜 1줄만 남기고 내용 갱신
    - lines 가 비어 있으면 해당 날짜 행 전체 삭제
    절대 전체 시트를 clear 하지 않고, 해당 날짜 row 만 건드린다.
    """
    client = get_gspread_client()
    if client is None:
        return False
    ws = get_worksheet(client, EVENTS_SHEET_NAME)
    if ws is None:
        return False

    _ensure_events_header(ws)

    try:
        # 1) 이 날짜에 해당하는 기존 row 들 찾기 (A열 기준)
        found = ws.findall(date_str)
        target_rows = [c.row for c in found if c.col == 1]

        if lines:
            memo_text = "\n".join(lines)

            if target_rows:
                # 첫 번째 row는 내용만 갱신
                first_row = min(target_rows)
                ws.update_cell(first_row, 1, date_str)
                ws.update_cell(first_row, 2, memo_text)
                # 나머지 중복 row 는 모두 삭제 (아래에서 위 순서로)
                for row_idx in sorted(target_rows[1:], reverse=True):
                    ws.delete_rows(row_idx)
            else:
                # 기존 row 가 없으면 새로 추가 (append)
                ws.append_row([date_str, memo_text])
        else:
            # lines 가 비어 있으면 해당 날짜의 row 모두 삭제
            for row_idx in sorted(target_rows, reverse=True):
                ws.delete_rows(row_idx)

        # 캐시 비우기 (이 테넌트 일정 다시 로드되도록)
        load_calendar_events_for_tenant.clear()
        return True

    except Exception as e:
        st.error(f"'일정' 시트 저장 중 오류: {e}")
        return False


def _get_day_text_color(dt: datetime.date):
    """공휴일에 따른 날짜 글자색 결정 (주말은 CSS에서 따로 처리)."""
    is_kr_holiday = (KR_HOLIDAYS is not None and dt in KR_HOLIDAYS)
    is_cn_holiday = (CN_HOLIDAYS is not None and dt in CN_HOLIDAYS)

    # 1) 한국 공휴일 우선 (파란색)
    if is_kr_holiday:
        return "#1565c0"

    # 2) 중국 공휴일 (빨간색)
    if is_cn_holiday:
        return "#d32f2f"

    # 나머지는 기본 색상
    return None


# ─────────────────────────────
# 0-2) 일정 팝업 다이얼로그 (저장 전 확인 한 번 더)
# ─────────────────────────────
if hasattr(st, "dialog"):

    @st.dialog("📌 일정 메모")
    def show_calendar_dialog(date_str: str):
        """특정 날짜에 대한 메모를 팝업으로 입력/수정/삭제."""
        tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
        events_by_date = load_calendar_events_for_tenant(tenant_id)
        existing_lines = events_by_date.get(date_str, [])
        default_text = "\n".join(existing_lines)

        # 날짜가 바뀌면 확인 상태 초기화
        if st.session_state.get("calendar_confirm_date") != date_str:
            st.session_state["calendar_confirm"] = False
            st.session_state["calendar_confirm_date"] = date_str
            st.session_state["calendar_memo_buffer"] = default_text

        # 현재 memo 값 (buffer 기준)
        current_text = st.session_state.get("calendar_memo_buffer", default_text)

        st.markdown(f"**{date_str} 일정 메모**")
        memo_text = st.text_area(
            "한 줄 = 한 일정입니다.",
            value=current_text,
            height=150,
            key="calendar_memo_text",
        )

        # 항상 최신 입력 내용을 버퍼에 반영
        st.session_state["calendar_memo_buffer"] = memo_text

        if not st.session_state.get("calendar_confirm", False):
            # 1단계: 저장 버튼 → "정말 저장하시겠습니까?" 단계로 전환
            col_save, col_close = st.columns(2)
            with col_save:
                if st.button("💾 저장", use_container_width=True):
                    st.session_state["calendar_confirm"] = True
                    st.rerun()

            with col_close:
                if st.button("닫기", use_container_width=True):
                    st.session_state["calendar_confirm"] = False
                    st.session_state["calendar_memo_buffer"] = ""
                    st.session_state["home_calendar_dialog_open"] = False
                    st.session_state[SESS_HOME_CALENDAR_SELECTED_DATE] = None

                    st.session_state["suppress_calendar_callback"] = True
                    st.session_state["home_calendar_nonce"] = st.session_state.get("home_calendar_nonce", 0) + 1  # ✅ 추가

                    st.rerun()

        else:
            # 2단계: 정말 저장하시겠습니까?
            st.info("정말 저장하시겠습니까?")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("예", use_container_width=True):
                    buffer_text = st.session_state.get("calendar_memo_buffer", "")
                    new_lines = [ln.strip() for ln in buffer_text.splitlines() if ln.strip()]
                    save_calendar_events_for_date(date_str, new_lines)

                    # 상태 초기화 + 팝업 종료
                    st.session_state["calendar_confirm"] = False
                    st.session_state["calendar_memo_buffer"] = ""
                    st.session_state[SESS_HOME_CALENDAR_SELECTED_DATE] = None
                    st.session_state["home_calendar_dialog_open"] = False
                    # ▶ 다음 한 번은 캘린더 콜백 무시
                    st.session_state["suppress_calendar_callback"] = True
                    st.session_state["home_calendar_nonce"] = st.session_state.get("home_calendar_nonce", 0) + 1  # ✅ 추가
                    st.success("저장되었습니다.")
                    st.rerun()


            with col_no:
                if st.button("아니오", use_container_width=True):
                    # 확인만 취소하고, 팝업/내용은 그대로 유지
                    st.session_state["calendar_confirm"] = False
                    st.rerun()

    @st.dialog("📆 년/월 선택")
    def show_month_picker_dialog():
        today = datetime.date.today()
        cur_year = st.session_state.get(SESS_HOME_SELECTED_YEAR, today.year)
        cur_month = st.session_state.get(SESS_HOME_SELECTED_MONTH, today.month)

        # 연도 범위는 현재 기준 ±5년 정도
        years = list(range(cur_year - 5, cur_year + 6))
        if cur_year not in years:
            years.append(cur_year)
            years.sort()

        months = list(range(1, 13))

        year_idx = years.index(cur_year)
        month_idx = cur_month - 1 if 1 <= cur_month <= 12 else 0

        sel_year = st.selectbox("년도", years, index=year_idx)
        sel_month = st.selectbox("월", months, index=month_idx)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("확인", use_container_width=True):
                st.session_state[SESS_HOME_SELECTED_YEAR] = sel_year
                st.session_state[SESS_HOME_SELECTED_MONTH] = sel_month
                st.session_state["home_month_picker_open"] = False
                st.rerun()
        with c2:
            if st.button("취소", use_container_width=True):
                st.session_state["home_month_picker_open"] = False
                st.rerun()


else:
    # Streamlit 버전이 낮아 experimental_dialog 가 없는 경우:
    # 달력 아래에 카드 형식으로 노출하는 fallback
    def show_calendar_dialog(date_str: str):
        tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
        events_by_date = load_calendar_events_for_tenant(tenant_id)
        existing_lines = events_by_date.get(date_str, [])
        default_text = "\n".join(existing_lines)

        st.markdown(f"#### 📌 {date_str} 일정 메모")
        memo_text = st.text_area(
            "한 줄 = 한 일정입니다.",
            value=default_text,
            height=150,
            key="calendar_memo_text_inline",
        )
        col_save, col_close = st.columns(2)
        with col_save:
            if st.button("💾 저장", use_container_width=True):
                new_lines = [ln.strip() for ln in memo_text.splitlines() if ln.strip()]
                save_calendar_events_for_date(date_str, new_lines)
                st.session_state[SESS_HOME_CALENDAR_SELECTED_DATE] = None
                st.success("저장되었습니다.")
                st.rerun()
        with col_close:
            if st.button("닫기", use_container_width=True):
                st.session_state[SESS_HOME_CALENDAR_SELECTED_DATE] = None
    
    def show_month_picker_dialog():
        today = datetime.date.today()
        cur_year = st.session_state.get(SESS_HOME_SELECTED_YEAR, today.year)
        cur_month = st.session_state.get(SESS_HOME_SELECTED_MONTH, today.month)

        st.markdown("#### 📆 년/월 선택")
        sel_year = st.number_input("년도", value=cur_year, step=1)
        sel_month = st.number_input("월", value=cur_month, min_value=1, max_value=12, step=1)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("확인", use_container_width=True):
                st.session_state[SESS_HOME_SELECTED_YEAR] = int(sel_year)
                st.session_state[SESS_HOME_SELECTED_MONTH] = int(sel_month)
                st.session_state["home_month_picker_open"] = False
                st.rerun()
        with c2:
            if st.button("취소", use_container_width=True):
                st.session_state["home_month_picker_open"] = False

# ─────────────────────────────
# 1) 단기메모 로드/저장
# ─────────────────────────────
@st.cache_data(ttl=60)   # ✅ 캐시 적용 (60초 정도만 캐시)
def load_short_memo(tenant_id: str | None = None):
    """
    구글시트 '단기메모' 시트에서 A1 셀 내용을 읽어옵니다.
    tenant_id 인자는 캐시 키를 다르게 하기 위한 용도 (내부에서 직접 쓰진 않음).
    """
    return read_memo_from_sheet(MEMO_SHORT_SHEET_NAME)


def save_short_memo(content: str) -> bool:
    tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
    if save_memo_to_sheet(MEMO_SHORT_SHEET_NAME, content):
        # ✅ 캐시 비우기 → 다음에 다시 읽을 때 실제 시트에서 재로드
        load_short_memo.clear()
        # 필요하면 여기서 load_short_memo(tenant_id) 로 재캐시
        return True
    return False


# ─────────────────────────────
# 2) 예정업무 / 진행업무 / 완료업무 저장 함수
# ─────────────────────────────
def save_planned_tasks_to_sheet(data_list_of_dicts):
    """예정업무 전체를 시트에 덮어쓰기 저장"""
    header = ['id', 'date', 'period', 'content', 'note']
    return upsert_rows_by_id(PLANNED_TASKS_SHEET_NAME, data_list_of_dicts, header_list=header)


def save_active_tasks_to_sheet(data_list_of_dicts):
    """진행업무 전체를 시트에 덮어쓰기 저장"""
    header = [
        'id', 'category', 'date', 'name', 'work',
        'source_original', 'details', 'planned_expense', 'processed', 'processed_timestamp'
    ]
    ok = upsert_rows_by_id(ACTIVE_TASKS_SHEET_NAME, header_list=header, records=data_list_of_dicts, id_field="id")
    return ok

@st.cache_data(ttl=60)
def load_completed_tasks_from_sheet():
    """완료업무 시트 전체 로드"""
    records = read_data_from_sheet(COMPLETED_TASKS_SHEET_NAME, default_if_empty=[])
    return [{
        'id': r.get('id', str(uuid.uuid4())),
        'category': str(r.get('category', '')),
        'date': str(r.get('date', '')),
        'name': str(r.get('name', '')),
        'work': str(r.get('work', '')),
        'source_original': str(r.get('source_original', '')),
        'details': str(r.get('details', '')),
        'complete_date': str(r.get('complete_date', '')),
    } for r in records]


def save_completed_tasks_to_sheet(records):
    """완료업무 전체를 시트에 덮어쓰기 저장"""
    header = ['id', 'category', 'date', 'name', 'work', 'source_original', 'details', 'complete_date']
    ok = upsert_rows_by_id(COMPLETED_TASKS_SHEET_NAME, records, header_list=header)
    if ok:
        load_completed_tasks_from_sheet.clear()
    return ok


# load_events_from_sheet
# 3) 홈 페이지 렌더
# ─────────────────────────────
def render():
    """
    HOME 페이지 렌더링 함수.
    기존 app.py 의 PAGE_HOME 블럭과 UI/동작을 동일하게 유지.
    """

    # 좌/우 두 칼럼
    home_col_left, home_col_right = st.columns(2)

    # ── 1. 왼쪽: 구글 캘린더 + 단기메모 ─────────────────
    # ── 1. 왼쪽: 월간 일정 달력 + 단기메모 ─────────────────
    # ── 1. 왼쪽: 월간 달력 + 날짜별 메모 + 단기메모 ─────────────────
    # ── 1. 왼쪽: 월간 달력 + 단기메모 ─────────────────
    with home_col_left:
        st.subheader("1. 📅 일정 달력")

        # 세션에 현재 보고 있는 년/월 없으면 오늘 기준으로 초기화
        today = datetime.date.today()
        if SESS_HOME_SELECTED_YEAR not in st.session_state:
            st.session_state[SESS_HOME_SELECTED_YEAR] = today.year
        if SESS_HOME_SELECTED_MONTH not in st.session_state:
            st.session_state[SESS_HOME_SELECTED_MONTH] = today.month

        year = st.session_state[SESS_HOME_SELECTED_YEAR]
        month = st.session_state[SESS_HOME_SELECTED_MONTH]

        # 상단: 이전/다음 달 이동 + '2025년 8월' 텍스트
        nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])

        with nav_col1:
            prev_clicked = st.button("◀", key="home_cal_prev_month", use_container_width=True)
        with nav_col3:
            next_clicked = st.button("▶", key="home_cal_next_month", use_container_width=True)

        # 먼저 클릭 처리해서 year/month 값을 갱신
        if prev_clicked:
            if month == 1:
                month = 12
                year -= 1
            else:
                month -= 1
            st.session_state[SESS_HOME_SELECTED_YEAR] = year
            st.session_state[SESS_HOME_SELECTED_MONTH] = month
            st.session_state[SESS_HOME_CALENDAR_SELECTED_DATE] = None
            st.session_state["home_calendar_dialog_open"] = False
            st.session_state["suppress_calendar_callback"] = True

        elif next_clicked:
            if month == 12:
                month = 1
                year += 1
            else:
                month += 1
            st.session_state[SESS_HOME_SELECTED_YEAR] = year
            st.session_state[SESS_HOME_SELECTED_MONTH] = month
            st.session_state[SESS_HOME_CALENDAR_SELECTED_DATE] = None
            st.session_state["home_calendar_dialog_open"] = False
            st.session_state["suppress_calendar_callback"] = True  # ✅ 추가


        # 갱신된 year/month 기준으로 중앙 버튼 표시
        with nav_col2:
            if st.button(f"{year}년 {month}월", key="home_cal_month_label", use_container_width=True):
                st.session_state["home_month_picker_open"] = True


        tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
        events_by_date = load_calendar_events_for_tenant(tenant_id)

        # FullCalendar 에 넘길 events 리스트 구성
        calendar_events = []
        for date_str, lines in events_by_date.items():
            for line in lines:
                event = {
                    "title": line,
                    "start": date_str,   # "YYYY-MM-DD"
                    "allDay": True,
                }
                calendar_events.append(event)


        # 주말/공휴일 색상, 이벤트 있는 날짜 하이라이트, 마우스 포인터 처리용 CSS
        base_css = '''
        .fc .fc-col-header-cell.fc-day-sun { color: red; }
        .fc .fc-col-header-cell.fc-day-sat { color: blue; }

        /* 주말 날짜 숫자 색상 */
        .fc .fc-day-sun .fc-daygrid-day-number { color: red; }
        .fc .fc-day-sat .fc-daygrid-day-number { color: blue; }

        .fc .fc-daygrid-day:hover { cursor: pointer; }

        /* 날짜 칸 안의 일정 텍스트를 작게 여러 줄로 보여주기 */
        .fc .fc-daygrid-day .fc-daygrid-event {
            font-size: 0.70rem;
            line-height: 1.1;
            margin-top: 2px;
            padding: 0 2px;
            white-space: normal;
        }
        /* 점(dot) 스타일 숨기기 */
        .fc .fc-daygrid-day .fc-daygrid-event-dot {
            display: none;
        }
        '''

        # 현재 월의 날짜별 색상을 동적으로 생성
        date_css_parts = []
        last_day = pycal.monthrange(year, month)[1]
        for day in range(1, last_day + 1):
            dt = datetime.date(year, month, day)
            color = _get_day_text_color(dt)
            if color:
                date_css_parts.append(
                    f'.fc .fc-daygrid-day[data-date="{dt.isoformat()}"] .fc-daygrid-day-number {{ color: {color}; }}'
                )

        custom_css = base_css + "\n".join(date_css_parts)

        options = {
            "initialView": "dayGridMonth",
            "initialDate": datetime.date(year, month, 1).isoformat(),
            "locale": "ko",
            "height": 600,
            "headerToolbar": { "left": "", "center": "", "right": "" },  # 상단 헤더는 숨기고, 우리가 만든 상단 네비만 사용
        }

        st.markdown(f"<style>{custom_css}</style>", unsafe_allow_html=True)

        cal_state = calendar(
            events=calendar_events,
            options=options,
            custom_css=custom_css,
            key=f"home_calendar_{year}_{month}_{st.session_state.get('home_calendar_nonce', 0)}",
            callbacks=["dateClick", "eventClick"],
        )

        if "home_calendar_nonce" not in st.session_state:
            st.session_state["home_calendar_nonce"] = 0

        # 날짜 클릭 / 이벤트 클릭 → 선택된 날짜 계산
        selected_date_str = None
        suppress = st.session_state.get("suppress_calendar_callback", False)

        # ✅ suppress가 켜져 있으면 1회만 무시하고 바로 해제
        if suppress:
            st.session_state["suppress_calendar_callback"] = False
        else:
            if cal_state:
                cb = cal_state.get("callback")

                # dateClick
                if cb == "dateClick":
                    dc = cal_state.get("dateClick", {})
                    date_raw = dc.get("dateStr") or dc.get("date")
                    selected_date_str = _extract_selected_date(date_raw)

                # eventClick
                elif cb == "eventClick":
                    ev = cal_state.get("eventClick", {}).get("event", {})
                    date_raw = ev.get("startStr") or ev.get("start")
                    selected_date_str = _extract_selected_date(date_raw)

                if selected_date_str:
                    st.session_state[SESS_HOME_CALENDAR_SELECTED_DATE] = selected_date_str
                    st.session_state["home_calendar_dialog_open"] = True

                    # ✅ 다음 rerun(예정/진행업무 수정 등)에서 달력 콜백이 재처리되지 않게 1회 무시 플래그 ON
                    st.session_state["suppress_calendar_callback"] = True


        # 팝업(또는 fallback 카드) 띄우기
        sel_date = st.session_state.get(SESS_HOME_CALENDAR_SELECTED_DATE)
        if st.session_state.get("home_calendar_dialog_open") and sel_date:
            show_calendar_dialog(sel_date)

        # 6) 기존 단기메모는 아래에 그대로 유지
        tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
        memo_short_content = load_short_memo(tenant_id)
        edited_memo_short = st.text_area(
            "📝 단기메모",
            value=memo_short_content,
            height=200,
            key="memo_short_text_area",
        )
        if st.button("💾 단기메모 저장", key="save_memo_short_btn", use_container_width=True):
            if save_short_memo(edited_memo_short):
                st.success("단기메모를 저장했습니다.")
            else:
                st.error("단기메모 저장에 실패했습니다.")


    # ── 2·3. 오른쪽: 만기 알림(등록증/여권) ─────────────────
    with home_col_right:
        st.subheader("2. 🪪 등록증 만기 4개월 전")

        # 👉 홈 들어올 때마다, 현재 테넌트 기준으로 고객 DF 다시 로딩
        tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
        df_customers_for_alert_view = load_customer_df_from_sheet(tenant_id)
        st.session_state[SESS_DF_CUSTOMER] = df_customers_for_alert_view.copy()

        if df_customers_for_alert_view.empty:
            st.write("(표시할 고객 없음)")
        else:
            # 표시용 기본 컬럼 구성
            df_alert_display_prepared_view = pd.DataFrame()
            df_alert_display_prepared_view['한글이름'] = df_customers_for_alert_view.get('한글', pd.Series(dtype='str'))
            df_alert_display_prepared_view['영문이름'] = (
                df_customers_for_alert_view.get('성', pd.Series(dtype='str')).fillna('') + ' ' +
                df_customers_for_alert_view.get('명', pd.Series(dtype='str')).fillna('')
            )
            df_alert_display_prepared_view['여권번호'] = (
                df_customers_for_alert_view.get('여권', pd.Series(dtype='str'))
                .astype(str).str.strip()
            )

            # 전화번호 포맷
            def _fmt_part(x, width):
                x = str(x)
                x = x.split('.')[0]
                if x.strip() and x.lower() != 'nan':
                    return x.zfill(width)
                return " "

            df_alert_display_prepared_view['전화번호'] = (
                df_customers_for_alert_view.get('연', pd.Series(dtype='str')).apply(lambda x: _fmt_part(x, 3)) + ' ' +
                df_customers_for_alert_view.get('락', pd.Series(dtype='str')).apply(lambda x: _fmt_part(x, 4)) + ' ' +
                df_customers_for_alert_view.get('처', pd.Series(dtype='str')).apply(lambda x: _fmt_part(x, 4))
            ).str.replace(r'^\s* \s*$', '(정보없음)', regex=True).str.replace(
                r'^\s*--\s*$', '(정보없음)', regex=True
            )

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
                df_customers_for_alert_view.get('만기일')
                    .astype(str)
                    .str.replace(".", "-")
                    .str.slice(0, 10),
                format="%Y-%m-%d",
                errors="coerce",
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

        # 3. 여권 만기
        st.subheader("3. 🛂 여권 만기 6개월 전")
        if df_customers_for_alert_view.empty:
            st.write("(표시할 고객 없음)")
        else:
            df_customers_for_alert_view['여권만기일_dt_alert'] = pd.to_datetime(
                df_customers_for_alert_view.get('만기')
                    .astype(str)
                    .str.replace(".", "-")
                    .str.slice(0, 10),
                format="%Y-%m-%d",
                errors="coerce",
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

    # ── 4. 📌 예정업무 ─────────────────────────────
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
        new_p = cols[0].selectbox(
            " ", 기간_옵션_plan_home_opts,
            index=기간_옵션_plan_home_opts.index(prev_p) if prev_p in 기간_옵션_plan_home_opts else 0,
            key=f"plan_period_{uid}", label_visibility="collapsed"
        )

        try:
            prev_d = datetime.datetime.strptime(task_item.get("date", ""), "%Y-%m-%d").date()
        except Exception:
            prev_d = datetime.date.today()
        new_d = cols[1].date_input(
            " ", value=prev_d,
            key=f"plan_date_{uid}", label_visibility="collapsed"
        )

        prev_c = task_item.get("content", "")
        new_c = cols[2].text_input(
            " ", value=prev_c,
            key=f"plan_content_{uid}", label_visibility="collapsed"
        )

        prev_n = task_item.get("note", "")
        new_n = cols[3].text_input(
            " ", value=prev_n,
            key=f"plan_note_{uid}", label_visibility="collapsed"
        )

        # 수정 버튼
        if cols[4].button("✏️", key=f"plan_edit_{uid}", use_container_width=True):
            task_item.update({
                "period": new_p,
                "date":   new_d.strftime("%Y-%m-%d"),
                "content": new_c,
                "note":    new_n,
            })
            st.session_state[SESS_PLANNED_TASKS_TEMP] = planned_tasks_editable_list
            save_planned_tasks_to_sheet(planned_tasks_editable_list)
            st.success(f"예정업무(ID:{uid}) 수정 저장됨")
            st.session_state["suppress_calendar_callback"] = True  # ✅ 추
            st.rerun()

        # 삭제 요청 버튼
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
                st.session_state["suppress_calendar_callback"] = True  # ✅ 추가
                st.rerun()
        with c_no:
            if st.button("❌ 아니오, 취소합니다", key="confirm_no", use_container_width=True):
                st.session_state["confirm_delete_idx"] = None
                st.session_state["suppress_calendar_callback"] = True  # ✅ 추가
                st.rerun()

    # 예정업무 추가 폼
    with st.form("add_planned_form_home_new", clear_on_submit=True):
        ac0, ac1, ac2, ac3, ac4 = st.columns([0.8, 1, 3, 2, 1])
        ap = ac0.selectbox("기간", 기간_옵션_plan_home_opts,
                           key="add_plan_period_form", label_visibility="collapsed")
        ad = ac1.date_input("날짜", value=datetime.date.today(),
                            key="add_plan_date_form", label_visibility="collapsed")
        ac = ac2.text_input("내용", key="add_plan_content_form",
                            placeholder="업무 내용", label_visibility="collapsed")
        an = ac3.text_input("비고", key="add_plan_note_form",
                            placeholder="참고 사항", label_visibility="collapsed")
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
                    "note":    an,
                })
                st.session_state[SESS_PLANNED_TASKS_TEMP] = planned_tasks_editable_list
                save_planned_tasks_to_sheet(planned_tasks_editable_list)
                st.success("새 예정업무 추가됨")
                st.session_state["suppress_calendar_callback"] = True  # ✅ 추가
                st.rerun()

    # ── 5. 🛠️ 진행업무 ─────────────────────────────
    def upsert_one_active_task(task: dict) -> bool:
        header = [
            'id', 'category', 'date', 'name', 'work',
            'source_original', 'details', 'planned_expense', 'processed', 'processed_timestamp'
        ]
        return upsert_rows_by_id(ACTIVE_TASKS_SHEET_NAME, header_list=header, records=[task], id_field="id")


    def upsert_one_completed_task(task: dict) -> bool:
        header = ['id', 'category', 'date', 'name', 'work', 'source_original', 'details', 'complete_date']
        return upsert_rows_by_id(COMPLETED_TASKS_SHEET_NAME, header_list=header, records=[task], id_field="id")


    st.markdown("---")
    title_l, title_r = st.columns([3, 1])
    with title_l:
        st.subheader("5. 🛠️ 진행업무")

    active_tasks = st.session_state.get(SESS_ACTIVE_TASKS_TEMP, [])
    구분_옵션_active_opts = ["출입국", "전자민원", "공증", "여권", "초청", "영주권", "기타"]
    구분_우선순위_map = {opt: i for i, opt in enumerate(구분_옵션_active_opts)}

    with title_r:
        total = sum(int(t.get("planned_expense") or 0) for t in active_tasks)
        st.markdown(f"#### 지출예정 금액 : {total:,} 원")

    # 정렬: 미처리 → 처리됨, 구분, 처리시각, 날짜
    active_tasks.sort(key=lambda x: (
        not x.get('processed', False),
        구분_우선순위_map.get(x.get('category', "기타"), 99),
        pd.to_datetime(x.get('processed_timestamp', ''), errors='coerce')
        if x.get('processed') else pd.Timestamp.min,
        pd.to_datetime(x.get('date', "9999-12-31"), errors='coerce'),
    ))

    # 헤더
    h1, h2, h3, h4, h5, h6, h7, h8, h9, h10, h11 = st.columns(
        [0.8, 0.7, 0.7, 1, 1, 2, 0.7, 0.5, 0.5, 0.5, 0.5],
        gap="small",
    )
    h1.markdown("**구분**")
    h2.markdown("**진행일**")
    h3.markdown("**성명**")
    h4.markdown("**업무**")
    h5.markdown("**원본**")
    h6.markdown("**세부내용**")
    h7.markdown("**지출예정**")
    h8.markdown("**✏️ 수정**")
    h9.markdown("**🅿️ 처리**")
    h10.markdown("**✅ 완료**")
    h11.markdown("**❌ 삭제**")

    # 각 행 렌더
    for task in active_tasks:
        uid = task["id"]
        cols = st.columns([0.8, 0.7, 0.7, 1, 1, 2, 0.7, 0.5, 0.5, 0.5, 0.5], gap="small")

        prev_category = task.get("category", 구분_옵션_active_opts[0])
        new_category = cols[0].selectbox(
            " ", options=구분_옵션_active_opts,
            index=구분_옵션_active_opts.index(prev_category)
            if prev_category in 구분_옵션_active_opts else 0,
            key=f"active_category_{uid}", label_visibility="collapsed",
        )

        try:
            prev_date = datetime.datetime.strptime(task.get("date", " "), "%Y-%m-%d").date()
        except Exception:
            prev_date = datetime.date.today()
        new_date = cols[1].date_input(
            " ", value=prev_date, key=f"active_date_{uid}", label_visibility="collapsed"
        )

        prev_name = task.get("name", " ")
        new_name = cols[2].text_input(
            " ", value=prev_name, key=f"active_name_{uid}", label_visibility="collapsed"
        )

        prev_work = task.get("work", " ")
        if task.get("processed", False):
            cols[3].markdown(f"<span style='color:blue;'>{prev_work}</span>", unsafe_allow_html=True)
            new_work = prev_work  # 처리됨이면 실제로는 수정하지 않음
        else:
            new_work = cols[3].text_input(
                " ", value=prev_work, key=f"active_work_{uid}", label_visibility="collapsed"
            )

        prev_src = task.get("source_original", " ")
        new_src = cols[4].text_input(
            " ", value=prev_src, key=f"active_source_{uid}",
            placeholder="원본 링크/파일", label_visibility="collapsed",
        )

        prev_details = task.get("details", " ")
        if task.get("processed", False):
            cols[5].markdown(f"<span style='color:blue;'>{prev_details}</span>", unsafe_allow_html=True)
            new_details = prev_details
        else:
            new_details = cols[5].text_input(
                " ", value=prev_details, key=f"active_details_{uid}",
                label_visibility="collapsed",
            )

        prev_planned = int(task.get("planned_expense") or 0)

        if task.get("processed", False):
            cols[6].markdown(f"<span style='color:blue;'>{prev_planned:,}</span>", unsafe_allow_html=True)
            new_planned = prev_planned
        else:
            new_planned = cols[6].number_input(
                " ", value=prev_planned, step=1000,
                key=f"active_planned_{uid}",
                label_visibility="collapsed",
            )
                

        # ✏️ 수정
        if cols[7].button("✏️", key=f"active_edit_{uid}", use_container_width=True):
            full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
            updated_task = None

            for t in full_list:
                if t["id"] == uid:
                    t["category"] = new_category
                    t["date"] = new_date.strftime("%Y-%m-%d")
                    t["name"] = new_name
                    if not t.get("processed", False):
                        t["work"] = new_work
                        t["details"] = new_details
                    t["source_original"] = new_src
                    t["planned_expense"] = new_planned
                    updated_task = t
                    break

            if updated_task:
                upsert_one_active_task(updated_task)

            st.success("✅ 진행업무가 수정되어 저장되었습니다.")
            st.session_state["suppress_calendar_callback"] = True
            st.rerun()

        # 🅿️ 처리 토글
        if cols[8].button("🅿️", key=f"active_proc_{uid}", use_container_width=True, help="처리 상태 변경"):
            full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
            updated_task = None

            for t in full_list:
                if t["id"] == uid:
                    t["processed"] = not t.get("processed", False)
                    t["processed_timestamp"] = datetime.datetime.now().isoformat() if t["processed"] else " "
                    updated_task = t
                    break

            if updated_task:
                upsert_one_active_task(updated_task)

            st.info(f"진행업무(ID:{uid}) 처리 상태가 {'✅ 처리됨' if updated_task['processed'] else '🕓 미처리'} 으로 변경되었습니다.")
            st.rerun()


        # ✅ 완료로 이동
        if cols[9].button("✅", key=f"active_complete_{uid}", use_container_width=True, help="완료 처리"):
            full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
            completed_item = None

            for i, t in enumerate(full_list):
                if t["id"] == uid:
                    completed_item = full_list.pop(i)
                    completed_item["complete_date"] = datetime.date.today().strftime("%Y-%m-%d")
                    break

            if completed_item:
                # 1) 완료업무에 1행 upsert
                upsert_one_completed_task(completed_item)
                # 2) 진행업무 시트에서 해당 id 행 삭제
                delete_row_by_id(ACTIVE_TASKS_SHEET_NAME, uid, id_field="id")
                # 3) 세션 갱신
                st.session_state[SESS_ACTIVE_TASKS_TEMP] = full_list

            st.success("✅ 업무가 완료처리되어 ‘완료업무’ 페이지로 이동합니다.")
            st.session_state["suppress_calendar_callback"] = True
            st.rerun()


        # ❌ 삭제 요청
        if cols[10].button("❌", key=f"active_request_del_{uid}", use_container_width=True):
            st.session_state["active_delete_uid"] = uid
            st.session_state["suppress_calendar_callback"] = True  # ✅ 추가
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

                delete_row_by_id(ACTIVE_TASKS_SHEET_NAME, del_uid, id_field="id")  # ✅ 시트에서 1행 삭제

                del st.session_state["active_delete_uid"]
                st.success("🗑️ 삭제되었습니다.")
                st.session_state["suppress_calendar_callback"] = True
                st.rerun()
        with c2:
            if st.button("❌ 취소", key=f"active_confirm_no_{del_uid}", use_container_width=True):
                del st.session_state["active_delete_uid"]
                st.info("삭제가 취소되었습니다.")
                st.session_state["suppress_calendar_callback"] = True  # ✅ 추가
                st.rerun()
