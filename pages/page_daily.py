# pages/page_daily.py

import streamlit as st
import pandas as pd
import datetime
import uuid

from config import (
    SESS_CURRENT_PAGE,
    SESS_ACTIVE_TASKS_TEMP,
    SESS_ALL_DAILY_ENTRIES_PAGE_LOAD,
    DAILY_SUMMARY_SHEET_NAME,
    DAILY_BALANCE_SHEET_NAME,
    ACTIVE_TASKS_SHEET_NAME,
    PAGE_MONTHLY,
)

from core.google_sheets import (
    read_data_from_sheet,
    write_data_to_sheet,
)


def safe_int(val):
    """숫자 컬럼 안전 변환용"""
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


# -----------------------------
# 1) 일일결산 / 잔액 로드·저장 함수
# -----------------------------

def load_daily():
    records = read_data_from_sheet(DAILY_SUMMARY_SHEET_NAME, default_if_empty=[])
    processed_records = []
    for r in records:
        entry = {
            "id": r.get("id", str(uuid.uuid4())),  # ID 없으면 생성
            "date": str(r.get("date", "")),
            "time": str(r.get("time", "")),
            "category": str(r.get("category", "")),
            "name": str(r.get("name", "")),
            "task": str(r.get("task", "")),
            "income_cash": safe_int(r.get("income_cash")),
            "income_etc": safe_int(r.get("income_etc")),
            "exp_cash": safe_int(r.get("exp_cash")),
            "cash_out": safe_int(r.get("cash_out")),
            "exp_etc": safe_int(r.get("exp_etc")),
            "memo": str(r.get("memo", "")),
        }
        processed_records.append(entry)
    return processed_records


def save_daily(data_list_of_dicts):
    """
    일일결산 전체 데이터를 시트에 덮어쓰기.
    - data_list_of_dicts: load_daily() 형태의 dict 리스트
    """
    header = [
        "id",
        "date",
        "time",
        "category",
        "name",
        "task",
        "income_cash",
        "income_etc",
        "exp_cash",
        "cash_out",
        "exp_etc",
        "memo",
    ]
    ok = write_data_to_sheet(
        DAILY_SUMMARY_SHEET_NAME,
        data_list_of_dicts,
        header_list=header,
    )
    if ok:
        # 캐시 및 세션 동기화
        load_daily.clear()
        if SESS_ALL_DAILY_ENTRIES_PAGE_LOAD in st.session_state:
            st.session_state[SESS_ALL_DAILY_ENTRIES_PAGE_LOAD] = data_list_of_dicts.copy()
        return True
    return False


def load_balance():
    records = read_data_from_sheet(DAILY_BALANCE_SHEET_NAME, default_if_empty=[])    balance = {"cash": 0, "profit": 0}
    if not records:
        return balance

    for record in records:
        key = record.get("key")
        value_str = str(record.get("value", "0"))
        if key in balance:
            try:
                balance[key] = int(value_str) if value_str and value_str.strip() else 0
            except ValueError:
                st.warning(
                    f"누적요약 데이터 '{key}' 값 '{value_str}' 숫자 변환 실패 → 0으로 처리합니다."
                )
                balance[key] = 0
    return balance


def save_balance(balance_dict):
    """잔액 시트에 cash / profit 값 저장."""
    data_to_save = [
        {"key": str(k), "value": str(v)} for k, v in balance_dict.items()
    ]
    header = ["key", "value"]
    ok = write_data_to_sheet(
        DAILY_BALANCE_SHEET_NAME,
        data_to_save,
        header_list=header,
    )
    if ok:
        load_balance.clear()
        return True
    return False


def _save_active_tasks_from_session():
    """
    SESS_ACTIVE_TASKS_TEMP 에 들어있는 진행업무 리스트를
    ACTIVE_TASKS_SHEET_NAME 시트에 전체 덮어쓰기.
    (원래 save_active_tasks_to_sheet 역할을 이 페이지 안에서 최소 구현)
    """
    tasks = st.session_state.get(SESS_ACTIVE_TASKS_TEMP, [])

    header = [
        "id",
        "category",
        "date",
        "name",
        "work",
        "source_original",
        "details",
        "processed",
        "processed_timestamp",
    ]

    ok = write_data_to_sheet(
        ACTIVE_TASKS_SHEET_NAME,
        tasks,
        header_list=header,
    )
    if ok:
        # 진행업무 관련 캐시 전부 비우기 (load_active_tasks_from_sheet 등)
        st.cache_data.clear()
        return True
    return False


# -----------------------------
# 2) 메인 렌더 함수
# -----------------------------

def render():
    """
    일일결산 페이지 렌더링.
    app.py 에서 current_page_to_display == PAGE_DAILY 일 때 호출.
    """
    col_left, col_right = st.columns([8, 1])
    with col_right:
        if st.button("📅 월간결산", use_container_width=True):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_MONTHLY
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

    선택날짜 = st.date_input(
        "날짜 선택",
        value=st.session_state["daily_selected_date"],
        key="daily_date_input",
    )

    # 날짜가 바뀌면 다시 렌더링
    if 선택날짜 != st.session_state["daily_selected_date"]:
        st.session_state["daily_selected_date"] = 선택날짜
        st.rerun()

    # 문자열 포맷
    선택날짜_문자열 = 선택날짜.strftime("%Y-%m-%d")
    선택날짜_표시 = 선택날짜.strftime("%Y년 %m월 %d일")

    st.subheader(f"📊 일일결산: {선택날짜_표시}")

    이번달_str = 선택날짜.strftime("%Y-%m")

    오늘_데이터 = [row for row in data if row.get("date") == 선택날짜_문자열]
    오늘_데이터.sort(key=lambda x: x.get("time", "00:00:00"))

    if not 오늘_데이터:
        st.info("선택한 날짜에 등록된 내역이 없습니다.")

    # -------------------
    # 기존 내역 리스트(수정/삭제)
    # -------------------
    for idx, row_data in enumerate(오늘_데이터):
        cols = st.columns([0.8, 0.8, 1, 2, 1, 1, 1, 1, 1, 1, 0.7])

        cols[0].text_input(
            "시간",
            value=row_data.get("time", " "),
            key=f"time_disp_{idx}",
            label_visibility="collapsed",
        )
        prev_category = row_data.get("category", "")
        cols[1].selectbox(
            "구분",
            ["현금출금"] + 구분_옵션,
            index=(["현금출금"] + 구분_옵션).index(prev_category)
            if prev_category in 구분_옵션 or prev_category == "현금출금"
            else 0,
            key=f"daily_category_{idx}",
            label_visibility="collapsed",
        )
        cols[2].text_input(
            "성명",
            value=row_data.get("name", " "),
            key=f"name_{idx}",
            label_visibility="collapsed",
        )
        cols[3].text_input(
            "업무",
            value=row_data.get("task", " "),
            key=f"task_{idx}",
            label_visibility="collapsed",
        )
        cols[4].number_input(
            "현금입금",
            value=row_data.get("income_cash", 0),
            key=f"inc_cash_{idx}",
            format="%d",
            label_visibility="collapsed",
            help="현금입금",
        )
        cols[5].number_input(
            "현금지출",
            value=row_data.get("exp_cash", 0),
            key=f"exp_cash_{idx}",
            format="%d",
            label_visibility="collapsed",
            help="현금지출",
        )
        cols[6].number_input(
            "현금출금",
            value=row_data.get("cash_out", 0),
            key=f"cash_out_{idx}",
            format="%d",
            label_visibility="collapsed",
            help="현금출금(개인)",
        )
        cols[7].number_input(
            "기타입금",
            value=row_data.get("income_etc", 0),
            key=f"inc_etc_{idx}",
            format="%d",
            label_visibility="collapsed",
            help="기타입금",
        )
        cols[8].number_input(
            "기타지출",
            value=row_data.get("exp_etc", 0),
            key=f"exp_etc_{idx}",
            format="%d",
            label_visibility="collapsed",
            help="기타지출",
        )
        cols[9].text_input(
            "비고",
            value=row_data.get("memo", " "),
            key=f"memo_{idx}",
            label_visibility="collapsed",
            placeholder="비고",
        )

        action_cols_daily = cols[10].columns(2)

        # --- 2-1) 수정 버튼(✏️) 클릭 시: 즉시 저장 로직
        if action_cols_daily[0].button("✏️", key=f"edit_daily_{idx}"):
            new_time = st.session_state.get(f"time_disp_{idx}", row_data.get("time", " "))
            new_name = st.session_state.get(f"name_{idx}", " ")
            new_task = st.session_state.get(f"task_{idx}", " ")
            new_category = st.session_state.get(f"daily_category_{idx}", "")
            new_inc_cash = st.session_state.get(f"inc_cash_{idx}", 0)
            new_exp_cash = st.session_state.get(f"exp_cash_{idx}", 0)
            new_cash_out = st.session_state.get(f"cash_out_{idx}", 0)
            new_inc_etc = st.session_state.get(f"inc_etc_{idx}", 0)
            new_exp_etc = st.session_state.get(f"exp_etc_{idx}", 0)
            new_memo = st.session_state.get(f"memo_{idx}", " ")

            original_id = 오늘_데이터[idx]["id"]

            for row in data:
                if row.get("id") == original_id:
                    row["time"] = new_time
                    row["name"] = new_name
                    row["task"] = new_task
                    row["category"] = new_category  # 시트에는 안 쓰지만 메모리 유지
                    row["income_cash"] = new_inc_cash
                    row["exp_cash"] = new_exp_cash
                    row["cash_out"] = new_cash_out
                    row["income_etc"] = new_inc_etc
                    row["exp_etc"] = new_exp_etc
                    row["memo"] = new_memo
                    break

            save_daily(data)
            st.success(f"{idx + 1}번째 행이 저장되었습니다.")
            st.rerun()

        # --- 2-2) 삭제 버튼(🗑️)
        if action_cols_daily[1].button("🗑️", key=f"delete_daily_{idx}", help="삭제"):
            original_row_id = row_data.get("id")
            data = [d for d in data if d.get("id") != original_row_id]
            save_daily(data)
            st.success("삭제되었습니다.")
            st.rerun()

    # -------------------
    # 새 내역 추가
    # -------------------
    st.markdown("#### 새 내역 추가")
    with st.form("add_daily_form", clear_on_submit=True):
        form_cols = st.columns([1, 1.5, 2, 1, 1, 1, 1, 1, 1.5, 0.5])
        add_category = form_cols[0].selectbox(
            "구분",
            ["현금출금"] + 구분_옵션,
            key="add_daily_category",
            label_visibility="collapsed",
        )
        add_name = form_cols[1].text_input(
            "성명", key="add_daily_name", label_visibility="collapsed"
        )
        add_task = form_cols[2].text_input(
            "업무", key="add_daily_task", label_visibility="collapsed"
        )
        add_income_cash = form_cols[3].number_input(
            "현금입금", value=0, key="add_daily_inc_cash_old", format="%d"
        )
        add_exp_cash = form_cols[4].number_input(
            "현금지출", value=0, key="add_daily_exp_cash_old", format="%d"
        )
        add_cash_out = form_cols[5].number_input(
            "현금출금", value=0, key="add_daily_cash_out_old", format="%d"
        )
        add_income_etc = form_cols[6].number_input(
            "기타입금", value=0, key="add_daily_inc_etc_old", format="%d"
        )
        add_exp_etc = form_cols[7].number_input(
            "기타지출", value=0, key="add_daily_exp_etc_old", format="%d"
        )
        add_memo = form_cols[8].text_input("비고", key="add_daily_memo_old")

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
                    "memo": add_memo,
                }
                data.append(new_entry_row)
                save_daily(data)

                # Active Tasks에도 동기화
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
                        "processed_timestamp": "",
                    }
                    st.session_state[SESS_ACTIVE_TASKS_TEMP].append(new_active)
                    _save_active_tasks_from_session()

                st.success(f"{선택날짜_표시}에 새 내역이 추가되었습니다.")
                st.rerun()

    # -------------------
    # 요약 집계 (일간/월간/사무실 현금)
    # -------------------
    오늘데이터 = 오늘_데이터
    오늘_현금입금 = sum(r.get("income_cash", 0) for r in 오늘데이터)
    오늘_기타입금 = sum(r.get("income_etc", 0) for r in 오늘데이터)
    오늘_현금지출 = sum(r.get("exp_cash", 0) for r in 오늘데이터)
    오늘_기타지출 = sum(r.get("exp_etc", 0) for r in 오늘데이터)

    오늘_총입금 = 오늘_현금입금 + 오늘_기타입금
    오늘_총지출 = 오늘_현금지출 + 오늘_기타지출
    오늘_순수익 = 오늘_총입금 - 오늘_총지출

    # ─── 사무실현금 누적 계산 ───
    사무실현금_누적 = 0
    all_data_sorted_for_cash = sorted(
        data, key=lambda x: (x.get("date", ""), x.get("time", "00:00:00"))
    )
    for r_calc in all_data_sorted_for_cash:
        if r_calc.get("date", "") > 선택날짜_문자열:
            break
        사무실현금_누적 += r_calc.get("income_cash", 0)
        사무실현금_누적 -= r_calc.get("exp_cash", 0)
        사무실현금_누적 -= r_calc.get("cash_out", 0)

    st.markdown("---")
    st.markdown("#### 요약 정보")

    # 이번 달(선택일까지) 수익·지출 세부 집계
    이번달_데이터 = [
        r
        for r in data
        if r.get("date", "").startswith(이번달_str)
        and r.get("date", "") <= 선택날짜_문자열
    ]
    월_현금입금 = sum(r.get("income_cash", 0) for r in 이번달_데이터)
    월_기타입금 = sum(r.get("income_etc", 0) for r in 이번달_데이터)
    월_현금지출 = sum(r.get("exp_cash", 0) for r in 이번달_데이터)
    월_기타지출 = sum(r.get("exp_etc", 0) for r in 이번달_데이터)

    월_총입금 = 월_현금입금 + 월_기타입금
    월_총지출 = 월_현금지출 + 월_기타지출
    월_순수익 = 월_총입금 - 월_총지출
    balance["profit"] = 월_순수익
    save_balance(balance)

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
            prev_ts = pd.to_datetime(선택날짜) - pd.DateOffset(months=m)
            prev = prev_ts.date()

            y, mo = prev.year, prev.month
            total = 0
            for d in range(1, D + 1):
                date_str = f"{y}-{mo:02d}-{d:02d}"
                total += sum(
                    r.get("income_cash", 0)
                    + r.get("income_etc", 0)
                    - r.get("exp_cash", 0)
                    - r.get("exp_etc", 0)
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

    st.caption(
        f"* '{선택날짜.strftime('%Y년 %m월')}' 전체 순수익은 '{balance['profit']:,}' 원 입니다 (Google Sheet '잔액' 기준)."
    )
