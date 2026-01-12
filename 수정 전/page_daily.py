# pages/page_daily.py

import datetime
import uuid

import streamlit as st

from config import (
    ACTIVE_TASKS_SHEET_NAME,
    DAILY_BALANCE_SHEET_NAME,
    DAILY_SUMMARY_SHEET_NAME,
    PAGE_MONTHLY,
    SESS_ACTIVE_TASKS_TEMP,
    SESS_ALL_DAILY_ENTRIES_PAGE_LOAD,
    SESS_CURRENT_PAGE,
    SESS_DAILY_DATE_INPUT_KEY,
    SESS_DAILY_SELECTED_DATE,
)

from core.google_sheets import read_data_from_sheet, write_data_to_sheet


INCOME_METHODS = ["이체", "현금", "카드", "미수"]  # 미수: 수익/매출에 포함하지 않음
EXPENSE_METHODS = ["이체", "현금", "카드", "인지"]


def _now_hhmm() -> str:
    return datetime.datetime.now().strftime("%H:%M")


def safe_int(val) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def _normalize_daily_record(raw: dict) -> dict:
    """시트의 (구/신) 스키마를 모두 받아서, 신 스키마 + 호환(기존 계산 유지) 형태로 정규화."""

    entry = {
        "id": raw.get("id") or str(uuid.uuid4()),
        "date": str(raw.get("date", "")),
        "time": str(raw.get("time", "")),
        "category": str(raw.get("category", "")),
        "name": str(raw.get("name", "")),
        "task": str(raw.get("task", "")),
        "memo": str(raw.get("memo", "")),
    }

    income_type = str(raw.get("income_type", raw.get("income_method", "")) or "").strip()
    expense_type = str(raw.get("expense_type", raw.get("expense_method", "")) or "").strip()

    income_amount = safe_int(raw.get("income_amount"))
    expense_amount = safe_int(raw.get("expense_amount"))
    cash_out = safe_int(raw.get("cash_out"))

    # 구 스키마(이전 데이터) 호환
    old_income_cash = safe_int(raw.get("income_cash"))
    old_income_etc = safe_int(raw.get("income_etc"))
    old_exp_cash = safe_int(raw.get("exp_cash"))
    old_exp_etc = safe_int(raw.get("exp_etc"))

    # 신 스키마가 비어있고 구 스키마 값이 있으면 추정 매핑
    if not income_type and income_amount == 0 and (old_income_cash or old_income_etc):
        income_amount = old_income_cash + old_income_etc
        if old_income_cash and not old_income_etc:
            income_type = "현금"
        elif old_income_etc and not old_income_cash:
            income_type = "이체"
        else:
            income_type = "이체"

    if not expense_type and expense_amount == 0 and (old_exp_cash or old_exp_etc):
        expense_amount = old_exp_cash + old_exp_etc
        if old_exp_cash and not old_exp_etc:
            expense_type = "현금"
        elif old_exp_etc and not old_exp_cash:
            expense_type = "이체"
        else:
            expense_type = "이체"

    entry["income_type"] = income_type
    entry["income_amount"] = income_amount
    entry["expense_type"] = expense_type
    entry["expense_amount"] = expense_amount
    entry["cash_out"] = cash_out

    # ---- 호환 컬럼(월간결산 등 기존 계산 유지용) ----
    income_ar = income_amount if income_type == "미수" else 0

    income_cash = income_amount if income_type == "현금" else 0
    income_etc = income_amount if income_type in ("이체", "카드") else 0
    if income_type == "미수":
        income_cash = 0
        income_etc = 0

    exp_cash = expense_amount if expense_type == "현금" else 0
    exp_etc = expense_amount if expense_type in ("이체", "카드", "인지") else 0

    entry["income_cash"] = income_cash
    entry["income_etc"] = income_etc
    entry["exp_cash"] = exp_cash
    entry["exp_etc"] = exp_etc
    entry["income_ar"] = income_ar

    return entry


def load_daily() -> list[dict]:
    records = read_data_from_sheet(DAILY_SUMMARY_SHEET_NAME, default_if_empty=[])
    return [_normalize_daily_record(r) for r in records]


def save_daily(data_list_of_dicts: list[dict]) -> bool:
    normalized = [_normalize_daily_record(r) for r in data_list_of_dicts]
    header = [
        # 공통
        "id",
        "date",
        "time",
        "category",
        "name",
        "task",
        "memo",
        # 신 스키마
        "income_type",
        "income_amount",
        "expense_type",
        "expense_amount",
        "cash_out",
        # 호환(구 스키마) + 미수 분리
        "income_cash",
        "income_etc",
        "exp_cash",
        "exp_etc",
        "income_ar",
    ]

    ok = write_data_to_sheet(DAILY_SUMMARY_SHEET_NAME, normalized, header_list=header)
    if ok:
        # 캐시/세션 동기화
        if SESS_ALL_DAILY_ENTRIES_PAGE_LOAD in st.session_state:
            st.session_state[SESS_ALL_DAILY_ENTRIES_PAGE_LOAD] = normalized.copy()
        st.cache_data.clear()
        return True
    return False


def load_balance() -> dict:
    records = read_data_from_sheet(DAILY_BALANCE_SHEET_NAME, default_if_empty=[])
    balance = {"cash": 0, "profit": 0}
    if not records:
        return balance

    for record in records:
        key = record.get("key")
        value_str = str(record.get("value", "0"))
        if key in balance:
            try:
                balance[key] = int(value_str) if value_str and value_str.strip() else 0
            except ValueError:
                balance[key] = 0
    return balance


def save_balance(balance_dict: dict) -> bool:
    data_to_save = [{"key": str(k), "value": str(v)} for k, v in balance_dict.items()]
    return bool(write_data_to_sheet(DAILY_BALANCE_SHEET_NAME, data_to_save, header_list=["key", "value"]))


def _save_active_tasks_from_session() -> bool:
    tasks = st.session_state.get(SESS_ACTIVE_TASKS_TEMP, []) or []
    header = [
        "id",
        "category",
        "date",
        "name",
        "work",
        "source_original",
        "details",
        "transfer",
        "cash",
        "card",
        "stamp",
        "receivable",
        "planned_expense",
        "processed",
        "processed_timestamp",
    ]
    ok = write_data_to_sheet(ACTIVE_TASKS_SHEET_NAME, tasks, header_list=header)
    if ok:
        st.cache_data.clear()
        return True
    return False


def render():
    col_left, col_right = st.columns([8, 1])
    with col_right:
        if st.button("📅 월간결산", use_container_width=True):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_MONTHLY
            st.rerun()

    if SESS_ACTIVE_TASKS_TEMP not in st.session_state:
        st.session_state[SESS_ACTIVE_TASKS_TEMP] = []

    # 날짜 선택(앱에서 리셋해주지만, 안전장치)
    if SESS_DAILY_SELECTED_DATE not in st.session_state:
        st.session_state[SESS_DAILY_SELECTED_DATE] = datetime.date.today()
    if SESS_DAILY_DATE_INPUT_KEY not in st.session_state:
        st.session_state[SESS_DAILY_DATE_INPUT_KEY] = "daily_date_input"

    data = load_daily()
    balance = load_balance()

    category_opts = ["출입국", "전자민원", "공증", "여권", "초청", "영주권", "기타"]
    cat_options_for_ui = ["현금출금"] + category_opts

    selected_date = st.date_input(
        "날짜 선택",
        value=st.session_state[SESS_DAILY_SELECTED_DATE],
        key=st.session_state[SESS_DAILY_DATE_INPUT_KEY],
    )
    st.session_state[SESS_DAILY_SELECTED_DATE] = selected_date
    selected_date_str = str(selected_date)

    st.subheader("📌 기존 내역")
    today_records = [r for r in data if r.get("date") == selected_date_str]

    # 삭제 확인(예/아니오)
    if "daily_pending_delete_id" not in st.session_state:
        st.session_state["daily_pending_delete_id"] = None

    if today_records:
        cols = st.columns([1.0, 1.2, 1.5, 1.5, 1.2, 1.4, 1.2, 1.0, 0.7, 0.7], gap="small")
        cols[0].markdown("**시간**")
        cols[1].markdown("**구분**")
        cols[2].markdown("**성명**")
        cols[3].markdown("**업무**")
        cols[4].markdown("**비고**")
        cols[5].markdown("**수입(유형/금액)**")
        cols[6].markdown("**지출(유형/금액)**")
        cols[7].markdown("**현금출금**")
        cols[8].markdown("**저장**")
        cols[9].markdown("**삭제**")

        for r in today_records:
            rid = r["id"]
            row_cols = st.columns([1.0, 1.2, 1.5, 1.5, 1.2, 1.4, 1.2, 1.0, 0.7, 0.7], gap="small")

            time_val = row_cols[0].text_input("", value=r.get("time", ""), key=f"r_{rid}_time", label_visibility="collapsed")
            cat_val = row_cols[1].selectbox(
                "",
                options=cat_options_for_ui,
                index=(cat_options_for_ui.index(r.get("category")) if r.get("category") in cat_options_for_ui else 0),
                key=f"r_{rid}_cat",
                label_visibility="collapsed",
            )
            name_val = row_cols[2].text_input("", value=r.get("name", ""), key=f"r_{rid}_name", label_visibility="collapsed")
            task_val = row_cols[3].text_input("", value=r.get("task", ""), key=f"r_{rid}_task", label_visibility="collapsed")
            memo_val = row_cols[4].text_input("", value=r.get("memo", ""), key=f"r_{rid}_memo", label_visibility="collapsed")

            inc_type = row_cols[5].selectbox(
                "",
                options=[""] + INCOME_METHODS,
                index=([""] + INCOME_METHODS).index(r.get("income_type", "")) if r.get("income_type", "") in ([""] + INCOME_METHODS) else 0,
                key=f"r_{rid}_inc_type",
                label_visibility="collapsed",
            )
            inc_amt = row_cols[5].number_input(
                "",
                min_value=0,
                step=1000,
                value=safe_int(r.get("income_amount", 0)),
                key=f"r_{rid}_inc_amt",
                label_visibility="collapsed",
            )

            exp_type = row_cols[6].selectbox(
                "",
                options=[""] + EXPENSE_METHODS,
                index=([""] + EXPENSE_METHODS).index(r.get("expense_type", "")) if r.get("expense_type", "") in ([""] + EXPENSE_METHODS) else 0,
                key=f"r_{rid}_exp_type",
                label_visibility="collapsed",
            )
            exp_amt = row_cols[6].number_input(
                "",
                min_value=0,
                step=1000,
                value=safe_int(r.get("expense_amount", 0)),
                key=f"r_{rid}_exp_amt",
                label_visibility="collapsed",
            )

            cash_out = row_cols[7].number_input(
                "",
                min_value=0,
                step=1000,
                value=safe_int(r.get("cash_out", 0)),
                key=f"r_{rid}_cash_out",
                label_visibility="collapsed",
            )

            # 저장: 변경된 경우만 저장
            if row_cols[8].button("💾", key=f"btn_save_{rid}", use_container_width=True):
                new_row = dict(r)
                new_row.update(
                    {
                        "time": time_val.strip(),
                        "category": cat_val.strip(),
                        "name": name_val.strip(),
                        "task": task_val.strip(),
                        "memo": memo_val.strip(),
                        "income_type": inc_type.strip(),
                        "income_amount": safe_int(inc_amt),
                        "expense_type": exp_type.strip(),
                        "expense_amount": safe_int(exp_amt),
                        "cash_out": safe_int(cash_out),
                    }
                )

                before_norm = _normalize_daily_record(r)
                after_norm = _normalize_daily_record(new_row)

                # 핵심 필드 비교(불필요한 시트 쓰기 방지)
                keys_to_compare = [
                    "time",
                    "category",
                    "name",
                    "task",
                    "memo",
                    "income_type",
                    "income_amount",
                    "expense_type",
                    "expense_amount",
                    "cash_out",
                ]
                changed = any(before_norm.get(k) != after_norm.get(k) for k in keys_to_compare)

                if not changed:
                    st.info("변경된 내용이 없습니다.")
                else:
                    # data 전체에서 해당 id 교체 후 저장
                    for idx, rr in enumerate(data):
                        if rr.get("id") == rid:
                            data[idx] = after_norm
                            break
                    ok = save_daily(data)
                    if ok:
                        st.success("저장 완료")
                    else:
                        st.error("저장 실패")

            # 삭제: 즉시 삭제 X → 확인창
            if row_cols[9].button("🗑️", key=f"btn_del_{rid}", use_container_width=True):
                st.session_state["daily_pending_delete_id"] = rid
                st.rerun()

        # 삭제 확인 UI(한 번에 1개만)
        pending_id = st.session_state.get("daily_pending_delete_id")
        if pending_id:
            tgt = next((x for x in today_records if x.get("id") == pending_id), None)
            if tgt:
                st.warning(
                    f"삭제하시겠습니까?\n\n"
                    f"- 시간: {tgt.get('time','')}\n"
                    f"- 구분: {tgt.get('category','')}\n"
                    f"- 성명: {tgt.get('name','')}\n"
                    f"- 업무: {tgt.get('task','')}"
                )
            c_yes, c_no = st.columns(2, gap="small")
            with c_yes:
                if st.button("✅ 예, 삭제합니다", key="daily_confirm_delete_yes", use_container_width=True):
                    data = [rr for rr in data if rr.get("id") != pending_id]
                    ok = save_daily(data)
                    st.session_state["daily_pending_delete_id"] = None
                    if ok:
                        st.success("삭제 완료")
                    else:
                        st.error("삭제 실패")
                    st.rerun()
            with c_no:
                if st.button("❌ 아니오, 취소합니다", key="daily_confirm_delete_no", use_container_width=True):
                    st.session_state["daily_pending_delete_id"] = None
                    st.rerun()
    else:
        st.info("선택한 날짜의 내역이 없습니다.")

    st.subheader("➕ 새 내역 추가")
    with st.form("add_daily_form", clear_on_submit=True):
        # 1줄: 구분 / 성명 / 내용 / 수입(방법) / 지출1(방법) / 지출2(방법)
        r1 = st.columns([0.9, 1.2, 1.8, 1.0, 1.0, 1.0], gap="small")

        add_category = r1[0].selectbox(
            "",
            ["구분", "현금출금"] + 구분_옵션,
            index=0,
            key="add_daily_category",
            label_visibility="collapsed",
        )
        add_name = r1[1].text_input("", placeholder="성명", key="add_daily_name", label_visibility="collapsed")
        add_task = r1[2].text_input("", placeholder="내용", key="add_daily_task", label_visibility="collapsed")

        add_income_type = r1[3].selectbox(
            "",
            ["수입"] + INCOME_METHODS,
            index=0,
            key="add_daily_income_type",
            label_visibility="collapsed",
        )
        add_expense_type = r1[4].selectbox(
            "",
            ["지출1"] + EXPENSE_METHODS,
            index=0,
            key="add_daily_expense_type",
            label_visibility="collapsed",
        )
        add_expense2_type = r1[5].selectbox(
            "",
            ["지출2"] + EXPENSE_METHODS,
            index=0,
            key="add_daily_expense2_type",
            label_visibility="collapsed",
        )

        # 2줄: 비고(구분~내용까지 폭) / 수입금액 / 지출1금액 / 지출2금액
        r2 = st.columns([3.9, 1.0, 1.0, 1.0], gap="small")
        add_memo = r2[0].text_input("", placeholder="비고", key="add_daily_memo", label_visibility="collapsed")

        add_income_amount_txt = r2[1].text_input("", placeholder="수입 금액", key="add_daily_income_amount_txt", label_visibility="collapsed")
        add_expense_amount_txt = r2[2].text_input("", placeholder="지출1 금액", key="add_daily_expense_amount_txt", label_visibility="collapsed")
        add_expense2_amount_txt = r2[3].text_input("", placeholder="지출2 금액", key="add_daily_expense2_amount_txt", label_visibility="collapsed")

        # (선택) 현금출금 금액
        add_cash_out_txt = ""
        if add_category == "현금출금":
            add_cash_out_txt = st.text_input("", placeholder="현금출금 금액", key="add_daily_cash_out_txt", label_visibility="collapsed")

        submitted = st.form_submit_button("➕ 추가", use_container_width=True)

        if submitted:
            # placeholder 처리
            if add_category == "구분":
                add_category = ""
            if add_income_type == "수입":
                add_income_type = ""
            if add_expense_type == "지출1":
                add_expense_type = ""
            if add_expense2_type == "지출2":
                add_expense2_type = ""

            income_amount = safe_int(add_income_amount_txt)
            expense_amount = safe_int(add_expense_amount_txt)
            expense2_amount = safe_int(add_expense2_amount_txt)
            cash_out = safe_int(add_cash_out_txt) if add_category == "현금출금" else 0

            if not add_category:
                st.warning("구분을 선택해주세요.")
            elif (not add_name) and (not add_task):
                st.warning("성명 또는 내용(업무)을 입력해주세요.")
            else:
                # ✅ 현금출금이면 수입/지출은 0 처리
                if add_category == "현금출금":
                    income_amount = 0
                    expense_amount = 0
                    expense2_amount = 0
                    add_income_type = ""
                    add_expense_type = ""
                    add_expense2_type = ""

                new_entry = {
                    "id": str(uuid.uuid4()),
                    "date": 선택날짜_문자열,
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    "category": add_category,
                    "name": add_name,
                    "task": add_task,
                    "memo": add_memo,
                    "income_type": add_income_type,
                    "income_amount": income_amount,
                    "expense_type": add_expense_type,
                    "expense_amount": expense_amount,
                    "expense2_type": add_expense2_type,
                    "expense2_amount": expense2_amount,
                    "cash_out": cash_out,
                }
                data.append(new_entry)
                save_daily(data)

                # ✅ 진행업무에도 동기화(현금출금 제외)
                if add_category != "현금출금":
                    receivable = income_amount if (add_income_type or "").strip() == "미수" else 0

                    def _exp_by(method: str) -> int:
                        total = 0
                        if (add_expense_type or "").strip() == method:
                            total += expense_amount
                        if (add_expense2_type or "").strip() == method:
                            total += expense2_amount
                        return total

                    transfer = _exp_by("이체")
                    cash = _exp_by("현금")
                    card = _exp_by("카드")
                    stamp = _exp_by("인지")
                    planned_sum = transfer + cash + card + stamp

                    new_active = {
                        "id": str(uuid.uuid4()),
                        "category": add_category,
                        "date": 선택날짜_문자열,
                        "name": add_name,
                        "work": add_task,
                        "source_original": "",
                        "details": add_memo,
                        "transfer": transfer,
                        "cash": cash,
                        "card": card,
                        "stamp": stamp,
                        "receivable": receivable,
                        "planned_expense": planned_sum,
                        "processed": False,
                        "processed_timestamp": "",
                    }
                    st.session_state[SESS_ACTIVE_TASKS_TEMP].append(new_active)
                    _save_active_tasks_from_session()

                st.success(f"{선택날짜_표시}에 새 내역이 추가되었습니다.")
                st.rerun()

                else:
                    st.error("추가 실패")


    st.subheader("📌 요약")
    today_entries = [r for r in data if r.get("date") == selected_date_str]

    total_cash_in = sum(safe_int(e.get("income_cash", 0)) for e in today_entries)
    total_etc_in = sum(safe_int(e.get("income_etc", 0)) for e in today_entries)
    total_ar = sum(safe_int(e.get("income_ar", 0)) for e in today_entries)  # 미수(수익 아님)
    total_in = total_cash_in + total_etc_in

    total_cash_out = sum(safe_int(e.get("exp_cash", 0)) for e in today_entries)
    total_etc_out = sum(safe_int(e.get("exp_etc", 0)) for e in today_entries)
    total_out = total_cash_out + total_etc_out

    total_cash_withdraw = sum(safe_int(e.get("cash_out", 0)) for e in today_entries)
    total_profit = total_in - total_out

    st.markdown(
        f"""
- 수입합계(미수 제외): **{total_in:,}원**  (현금 {total_cash_in:,} / 기타 {total_etc_in:,})
- 미수(수익 제외): **{total_ar:,}원**
- 지출합계: **{total_out:,}원** (현금 {total_cash_out:,} / 기타 {total_etc_out:,})
- 현금출금: **{total_cash_withdraw:,}원**
- 순이익(수입-지출, 미수 제외): **{total_profit:,}원**
"""
    )
