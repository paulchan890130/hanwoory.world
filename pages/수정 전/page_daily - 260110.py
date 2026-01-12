# pages/page_daily.py

import streamlit as st
import pandas as pd
import datetime
import uuid

from config import (
    SESS_CURRENT_PAGE,
    SESS_ACTIVE_TASKS_TEMP,
    SESS_ALL_DAILY_ENTRIES_PAGE_LOAD,
    SESS_DAILY_SELECTED_DATE,
    DAILY_SUMMARY_SHEET_NAME,
    DAILY_BALANCE_SHEET_NAME,
    ACTIVE_TASKS_SHEET_NAME,
    PAGE_MONTHLY,
)

from core.google_sheets import (
    read_data_from_sheet,
    write_data_to_sheet,
    get_gspread_client,
    get_worksheet,
)

# ✅ 입력용 드롭다운
INCOME_METHODS = ["이체", "현금", "카드", "미수"]  # 미수: 수익/매출(순수익)에 포함하지 않음
EXPENSE_METHODS = ["이체", "현금", "카드", "인지"]


def safe_int(val):
    """숫자 컬럼 안전 변환용"""
    try:
        if val is None:
            return 0
        s = str(val).strip()
        if s == "":
            return 0
        return int(float(s.replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _pack_memo(user_memo: str, income_type: str, exp1_type: str, exp2_type: str) -> str:
    """
    ✅ 시트 컬럼을 늘리지 않고,
    미수(수익 제외) / 지출 수단 정보를 memo 안에 태그로 저장.
    """
    user_memo = (user_memo or "").strip()
    tag = f"[KID]inc={income_type or ''};e1={exp1_type or ''};e2={exp2_type or ''}[/KID]"
    if user_memo:
        return f"{tag} {user_memo}"
    return tag


def _unpack_memo(memo: str) -> tuple[dict, str]:
    """
    memo에서 [KID]...[/KID] 태그를 읽어 meta와 사용자 비고를 분리.
    """
    memo = memo or ""
    meta = {"inc": "", "e1": "", "e2": ""}
    user = memo

    try:
        start = memo.find("[KID]")
        end = memo.find("[/KID]")
        if start != -1 and end != -1 and end > start:
            inner = memo[start + 5:end]
            for part in inner.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k in meta:
                        meta[k] = v
            user = (memo[end + 6:] or "").strip()
    except Exception:
        pass

    return meta, user

def upsert_daily_records(records: list[dict]) -> bool:
    """
    ✅ '일일결산' 시트에 id 기준으로 행을 추가/수정(upsert)합니다.
    - 전체 덮어쓰기 아님
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

    try:
        client = get_gspread_client()
        ws = get_worksheet(client, DAILY_SUMMARY_SHEET_NAME)

        values = ws.get_all_values()
        if not values:
            # 시트가 비어있으면: 헤더 + records 전체
            rows = [header]
            for rec in records:
                rows.append([str(rec.get(h, "")) for h in header])
            ws.update(rows)
            return True

        sheet_header = values[0]
        if "id" not in sheet_header:
            # 헤더가 깨진 경우: 헤더부터 정상화(최소 안전장치)
            ws.update([header])
            sheet_header = header

        id_col = sheet_header.index("id")

        # id -> 시트 row번호(2부터 시작)
        existing = {}
        for r_i, row in enumerate(values[1:], start=2):
            rid = row[id_col].strip() if len(row) > id_col else ""
            if rid:
                existing[rid] = r_i

        # 헤더 길이에 맞춰 A~ 끝열 범위 계산
        def _col_letter(n: int) -> str:
            # 1->A, 26->Z, 27->AA
            s = ""
            while n:
                n, r = divmod(n - 1, 26)
                s = chr(65 + r) + s
            return s

        last_col = _col_letter(len(header))

        for rec in records:
            rid = str(rec.get("id", "")).strip()
            if not rid:
                rid = str(uuid.uuid4())
                rec["id"] = rid

            row_vals = [str(rec.get(h, "")) for h in header]

            if rid in existing:
                row_no = existing[rid]
                ws.update(f"A{row_no}:{last_col}{row_no}", [row_vals])
            else:
                ws.append_row(row_vals)

        return True

    except Exception as e:
        st.error(f"❌ 일일결산 저장 실패: {e}")
        return False

def delete_daily_record_by_id(record_id: str) -> bool:
    """✅ '일일결산' 시트에서 id가 record_id인 행 1개만 삭제"""
    try:
        client = get_gspread_client()
        ws = get_worksheet(client, DAILY_SUMMARY_SHEET_NAME)
        values = ws.get_all_values()
        if not values:
            return False

        header = values[0]
        if "id" not in header:
            return False

        id_col = header.index("id")

        for row_no, row in enumerate(values[1:], start=2):
            rid = row[id_col].strip() if len(row) > id_col else ""
            if rid == str(record_id).strip():
                ws.delete_rows(row_no)
                return True

        return False

    except Exception as e:
        st.error(f"❌ 삭제 실패: {e}")
        return False

# -----------------------------
# 1) 일일결산 / 잔액 로드·저장 함수
# -----------------------------

def load_daily():
    """
    ✅ '일일결산' 시트 헤더(사용자 제공):
    id, date, time, category, name, task, income_cash, income_etc, exp_cash, cash_out, exp_etc, memo
    """
    records = read_data_from_sheet(DAILY_SUMMARY_SHEET_NAME, default_if_empty=[])
    processed_records = []
    for r in records:
        entry = {
            "id": r.get("id", str(uuid.uuid4())),
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
    """일일결산 전체 데이터를 시트에 덮어쓰기."""
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
        if SESS_ALL_DAILY_ENTRIES_PAGE_LOAD in st.session_state:
            st.session_state[SESS_ALL_DAILY_ENTRIES_PAGE_LOAD] = data_list_of_dicts.copy()
        return True
    return False


def load_balance():
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
                st.warning(f"누적요약 데이터 '{key}' 값 '{value_str}' 숫자 변환 실패 → 0으로 처리합니다.")
                balance[key] = 0
    return balance


def save_balance(balance_dict):
    """잔액 시트에 cash / profit 값 저장."""
    data_to_save = [{"key": str(k), "value": str(v)} for k, v in balance_dict.items()]
    header = ["key", "value"]
    ok = write_data_to_sheet(
        DAILY_BALANCE_SHEET_NAME,
        data_to_save,
        header_list=header,
    )
    return bool(ok)


def _save_active_tasks_from_session():
    """
    ✅ 진행업무 시트 전체 덮어쓰기(기존 로직 유지).
    다만, 현재 세션에 'transfer' 키가 있으면(신스키마) 그 헤더로 저장하고,
    없으면(구스키마) 기존 planned_expense만 저장.
    """
    tasks = st.session_state.get(SESS_ACTIVE_TASKS_TEMP, []) or []
    use_new = any(isinstance(t, dict) and ("transfer" in t or "stamp" in t) for t in tasks)

    if use_new:
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
    else:
        header = [
            "id",
            "category",
            "date",
            "name",
            "work",
            "source_original",
            "details",
            "planned_expense",
            "processed",
            "processed_timestamp",
        ]

    ok = write_data_to_sheet(
        ACTIVE_TASKS_SHEET_NAME,
        tasks,
        header_list=header,
    )
    if ok:
        st.cache_data.clear()
        return True
    return False


# -----------------------------
# 2) 메인 렌더 함수
# -----------------------------

def render():
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
    # 날짜 선택
    # -------------------
    if SESS_DAILY_SELECTED_DATE not in st.session_state:
        st.session_state[SESS_DAILY_SELECTED_DATE] = datetime.date.today()

    선택날짜 = st.date_input(
        "날짜 선택",
        value=st.session_state[SESS_DAILY_SELECTED_DATE],
        key="daily_date_input",
    )

    if 선택날짜 != st.session_state[SESS_DAILY_SELECTED_DATE]:
        st.session_state[SESS_DAILY_SELECTED_DATE] = 선택날짜
        st.rerun()

    선택날짜_문자열 = 선택날짜.strftime("%Y-%m-%d")
    선택날짜_표시 = 선택날짜.strftime("%Y년 %m월 %d일")

    st.subheader(f"📊 일일결산: {선택날짜_표시}")

    이번달_str = 선택날짜.strftime("%Y-%m")
    오늘_데이터 = [row for row in data if row.get("date") == 선택날짜_문자열]
    오늘_데이터.sort(key=lambda x: x.get("time", "00:00:00"))

    if not 오늘_데이터:
        st.info("선택한 날짜에 등록된 내역이 없습니다.")

    # -------------------
    # 기존 내역 리스트(수정/삭제) - 기존 안정 로직 유지 (✅ key를 id 기반으로 변경)
    # -------------------

    # ✅ 옵션 중복 방지 ("현금출금"이 구분_옵션에 이미 있으면 2번 들어가는 문제 예방)
    cat_options = ["현금출금"] + [x for x in 구분_옵션 if x != "현금출금"]

    for idx, row_data in enumerate(오늘_데이터):
        # ✅ 핵심: Streamlit key는 idx가 아니라 "id" 기반이어야 삭제/정렬에도 안 꼬임
        rid = str(row_data.get("id", "")).strip()
        if not rid:
            rid = f"idx_{idx}"  # 혹시 id 비어있는 레거시 데이터 대비

        cols = st.columns([0.8, 0.8, 1, 2, 1, 1, 1, 1, 1, 1, 0.7])

        cols[0].text_input(
            "시간",
            value=row_data.get("time", " "),
            key=f"time_disp_{rid}",
            label_visibility="collapsed",
        )

        prev_category = row_data.get("category", "")
        cols[1].selectbox(
            "구분",
            cat_options,
            index=cat_options.index(prev_category) if prev_category in cat_options else 0,
            key=f"daily_category_{rid}",
            label_visibility="collapsed",
        )

        cols[2].text_input(
            "성명",
            value=row_data.get("name", " "),
            key=f"name_{rid}",
            label_visibility="collapsed",
        )
        cols[3].text_input(
            "업무",
            value=row_data.get("task", " "),
            key=f"task_{rid}",
            label_visibility="collapsed",
        )

        cols[4].number_input(
            "현금입금",
            value=safe_int(row_data.get("income_cash", 0)),
            key=f"inc_cash_{rid}",
            format="%d",
            label_visibility="collapsed",
            help="현금입금",
        )
        cols[5].number_input(
            "현금지출",
            value=safe_int(row_data.get("exp_cash", 0)),
            key=f"exp_cash_{rid}",
            format="%d",
            label_visibility="collapsed",
            help="현금지출",
        )
        cols[6].number_input(
            "현금출금",
            value=safe_int(row_data.get("cash_out", 0)),
            key=f"cash_out_{rid}",
            format="%d",
            label_visibility="collapsed",
            help="현금출금(개인)",
        )
        cols[7].number_input(
            "기타입금",
            value=safe_int(row_data.get("income_etc", 0)),
            key=f"inc_etc_{rid}",
            format="%d",
            label_visibility="collapsed",
            help="기타입금(이체/카드/미수 포함 가능)",
        )
        cols[8].number_input(
            "기타지출",
            value=safe_int(row_data.get("exp_etc", 0)),
            key=f"exp_etc_{rid}",
            format="%d",
            label_visibility="collapsed",
            help="기타지출(이체/카드/인지 포함 가능)",
        )

        meta, user_memo = _unpack_memo(row_data.get("memo", ""))
        cols[9].text_input(
            "비고",
            value=user_memo if user_memo else " ",
            key=f"memo_{rid}",
            label_visibility="collapsed",
            placeholder="비고",
        )

        action_cols_daily = cols[10].columns(2)

        # --- 수정(✏️)
        if action_cols_daily[0].button("✏️", key=f"edit_daily_{rid}"):

            new_time = st.session_state.get(f"time_disp_{rid}", row_data.get("time", " "))
            new_name = st.session_state.get(f"name_{rid}", " ")
            new_task = st.session_state.get(f"task_{rid}", " ")
            new_category = st.session_state.get(f"daily_category_{rid}", "")
            new_inc_cash = safe_int(st.session_state.get(f"inc_cash_{rid}", 0))
            new_exp_cash = safe_int(st.session_state.get(f"exp_cash_{rid}", 0))
            new_cash_out = safe_int(st.session_state.get(f"cash_out_{rid}", 0))
            new_inc_etc = safe_int(st.session_state.get(f"inc_etc_{rid}", 0))
            new_exp_etc = safe_int(st.session_state.get(f"exp_etc_{rid}", 0))
            new_memo_user = st.session_state.get(f"memo_{rid}", " ").strip()

            # ✅ 기존 메타 태그는 유지
            new_memo = _pack_memo(new_memo_user, meta.get("inc", ""), meta.get("e1", ""), meta.get("e2", ""))

            updated = {
                "id": row_data.get("id"),
                "date": row_data.get("date"),
                "time": new_time,
                "category": new_category,
                "name": new_name,
                "task": new_task,
                "income_cash": new_inc_cash,
                "income_etc": new_inc_etc,
                "exp_cash": new_exp_cash,
                "cash_out": new_cash_out,
                "exp_etc": new_exp_etc,
                "memo": new_memo,
            }

            ok = upsert_daily_records([updated])
            if ok:
                st.cache_data.clear()
                st.success("저장되었습니다.")
                st.rerun()
            else:
                st.error("저장 실패")

            # --- 삭제(🗑️) : ✅ id 단위 삭제
            if action_cols_daily[1].button("🗑️", key=f"delete_daily_{rid}", help="삭제"):
                original_row_id = row_data.get("id")
                ok = delete_daily_record_by_id(original_row_id)
                if ok:
                    st.cache_data.clear()
                    st.success("삭제되었습니다.")
                    st.rerun()
                else:
                    st.error("삭제 실패")

    # -------------------
    # 새 내역 추가 (✅ 2줄 UI + 지출 2개 합산)
    # -------------------
    st.markdown("#### 새 내역 추가")

    INCOME_METHODS = ["이체", "현금", "카드", "미수"]          # 미수는 수익으로 잡지 말 것(= 요약에서 제외 처리 필요)
    EXPENSE_METHODS = ["이체", "현금", "카드", "인지"]        # 인지는 지출

    def _fmt_cat(x): return "구분" if x == "" else x
    def _fmt_inc(x): return "수입" if x == "" else x
    def _fmt_e1(x):  return "지출1" if x == "" else x
    def _fmt_e2(x):  return "지출2" if x == "" else x

    # ✅ 구분 옵션: 기본은 빈값("") → 사용자가 선택할 때까지 placeholder
    cat_options_for_ui = [""] + [c for c in 구분_옵션 if c]    # 구분_옵션은 기존 변수 그대로 사용
    # 필요하면 현금출금도 구분 옵션에 포함
    if "현금출금" not in cat_options_for_ui:
        cat_options_for_ui.append("현금출금")

    with st.form("add_daily_form", clear_on_submit=True):
        # 1줄: 구분 + 성명 + 내용 + 수입 + 지출1 + 지출2
        r1 = st.columns([1.2, 1.5, 2.3, 1.1, 1.1, 1.1], gap="small")

        add_category = r1[0].selectbox(
            "구분",
            cat_options_for_ui,
            index=0,
            format_func=_fmt_cat,
            key="daily_add_category",
            label_visibility="collapsed",
        )

        add_name = r1[1].text_input(
            "성명",
            placeholder="성명",
            key="daily_add_name",
            label_visibility="collapsed",
        )
        add_task = r1[2].text_input(
            "내용",
            placeholder="내용",
            key="daily_add_task",
            label_visibility="collapsed",
        )

        is_cash_out = (add_category == "현금출금")

        income_type = r1[3].selectbox(
            "수입",
            [""] + INCOME_METHODS,
            index=0,
            format_func=_fmt_inc,
            key="daily_add_income_type",
            label_visibility="collapsed",
            disabled=is_cash_out,
        )
        exp1_type = r1[4].selectbox(
            "지출1",
            [""] + EXPENSE_METHODS,
            index=0,
            format_func=_fmt_e1,
            key="daily_add_exp1_type",
            label_visibility="collapsed",
            disabled=is_cash_out,
        )
        exp2_type = r1[5].selectbox(
            "지출2",
            [""] + EXPENSE_METHODS,
            index=0,
            format_func=_fmt_e2,
            key="daily_add_exp2_type",
            label_visibility="collapsed",
            disabled=is_cash_out,
        )

        # ✅ 현금출금 금액 입력 (현금출금일 때만 활성)
        cash_out_amt = 0
        if is_cash_out:
            cash_out_amt = st.number_input(
                "현금출금금액",
                min_value=0,
                step=1000,
                value=0,
                key="daily_add_cash_out_amt_form",
                label_visibility="collapsed",
            )


        # 2줄: 비고(구분~내용 폭) + 수입금액 + 지출1금액 + 지출2금액
        r2 = st.columns([1.2 + 1.5 + 2.3, 1.1, 1.1, 1.1], gap="small")

        add_memo_user = r2[0].text_input(
            "비고",
            placeholder="비고",
            key="daily_add_memo",
            label_visibility="collapsed",
        )

        income_amt = r2[1].number_input(
            "0",
            min_value=0,
            step=1000,
            value=0,
            key="daily_add_income_amt",
            label_visibility="collapsed",
            disabled=is_cash_out,
        )
        exp1_amt = r2[2].number_input(
            "0",
            min_value=0,
            step=1000,
            value=0,
            key="daily_add_exp1_amt",
            label_visibility="collapsed",
            disabled=is_cash_out,
        )
        exp2_amt = r2[3].number_input(
            "0",
            min_value=0,
            step=1000,
            value=0,
            key="daily_add_exp2_amt",
            label_visibility="collapsed",
            disabled=is_cash_out,
        )

        submitted = st.form_submit_button("➕ 추가", use_container_width=True)

        if submitted:
            # ✅ 빈 구분 방지
            if add_category == "":
                st.error("구분을 선택하세요.")
                st.stop()

            inc_type = "" if income_type == "" else income_type
            e1_type = "" if exp1_type == "" else exp1_type
            e2_type = "" if exp2_type == "" else exp2_type

            inc_amt = safe_int(income_amt)
            e1_amt = safe_int(exp1_amt)
            e2_amt = safe_int(exp2_amt)

            # ✅ 현금출금 금액은 세션에서 확실히 읽기 (입력칸 key와 동일해야 함)
            cash_out_amt = safe_int(st.session_state.get("daily_add_cash_out_amt_form", 0))

            # ✅ 현금출금이면: 성명/내용 없어도 됨. 대신 현금출금 금액은 필수.
            if is_cash_out:
                if cash_out_amt <= 0:
                    st.error("현금출금 금액을 입력하세요.")
                    st.stop()

                # 현금출금이면 수입/지출은 0 처리
                inc_type, e1_type, e2_type = "", "", ""
                inc_amt, e1_amt, e2_amt = 0, 0, 0

            # ✅ 일반 항목이면: 완전 빈 입력 방지(기존 로직)
            else:
                if not add_name.strip() and not add_task.strip() and inc_amt == 0 and e1_amt == 0 and e2_amt == 0:
                    st.error("성명/내용 또는 금액을 입력하세요.")
                    st.stop()
                                                                                

            # -----------------------
            # ✅ 일일결산 시트 컬럼에 맞춰 금액 배분
            # income_cash / income_etc / exp_cash / exp_etc / cash_out
            # -----------------------
            income_cash = 0
            income_etc = 0
            exp_cash = 0
            exp_etc = 0

            # 수입
            if inc_type == "현금":
                income_cash += inc_amt
            elif inc_type in ("이체", "카드"):
                income_etc += inc_amt
            elif inc_type == "미수":
                # ✅ 미수는 수익으로 잡지 말 것 → 수치 컬럼에는 넣지 않고 memo에만 남김(요약에서 제외됨)
                pass

            # 지출1
            if e1_type == "현금":
                exp_cash += e1_amt
            elif e1_type in ("이체", "카드", "인지"):
                exp_etc += e1_amt

            # 지출2 (합산)
            if e2_type == "현금":
                exp_cash += e2_amt
            elif e2_type in ("이체", "카드", "인지"):
                exp_etc += e2_amt

            # ✅ 메모에 타입 메타 저장(기존 함수 사용)
            # (미수금액까지 남기고 싶으면 사용자 메모에 자동으로 한 줄 덧붙임)
            memo_user = (add_memo_user or "").strip()
            if inc_type == "미수" and inc_amt > 0:
                memo_user = (memo_user + f" / 미수 {inc_amt:,}").strip(" /")

            memo_packed = _pack_memo(memo_user, inc_type, e1_type, e2_type)

            new_entry = {
                "id": str(uuid.uuid4()),
                "date": 선택날짜_문자열,
                "time": datetime.datetime.now().strftime("%H:%M"),
                "category": add_category,
                "name": add_name.strip(),
                "task": add_task.strip(),
                "income_cash": income_cash,
                "income_etc": income_etc,
                "exp_cash": exp_cash,
                "cash_out": cash_out_amt,
                "exp_etc": exp_etc,
                "memo": memo_packed,
            }

            # --- new_entry 만들기 끝난 다음 ---

            ok = upsert_daily_records([new_entry])

            if ok:
                # ✅ 화면/요약 즉시 반영용(있으면 유지, 없으면 생략 가능)
                st.cache_data.clear()
                st.success("추가 완료")
                st.rerun()
            else:
                st.error("추가 실패")


    # -------------------
    # 요약 집계 (일간/월간/사무실 현금) - 기존 형식 유지
    # 단, ✅ '미수'는 수익(순수익)에 포함하지 않음
    # -------------------
    오늘데이터 = 오늘_데이터

    def _is_receivable(r: dict) -> bool:
        meta, _ = _unpack_memo(r.get("memo", ""))
        return (meta.get("inc", "") == "미수")

    오늘_현금입금 = sum(r.get("income_cash", 0) for r in 오늘데이터)
    오늘_기타입금 = sum(r.get("income_etc", 0) for r in 오늘데이터 if not _is_receivable(r))
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
        사무실현금_누적 += safe_int(r_calc.get("income_cash", 0))
        사무실현금_누적 -= safe_int(r_calc.get("exp_cash", 0))
        사무실현금_누적 -= safe_int(r_calc.get("cash_out", 0))

    st.markdown("---")
    st.markdown("#### 요약 정보")

    이번달_데이터 = [
        r
        for r in data
        if r.get("date", "").startswith(이번달_str)
        and r.get("date", "") <= 선택날짜_문자열
    ]
    월_현금입금 = sum(r.get("income_cash", 0) for r in 이번달_데이터)
    월_기타입금 = sum(r.get("income_etc", 0) for r in 이번달_데이터 if not _is_receivable(r))
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
                    + (r.get("income_etc", 0) if not _is_receivable(r) else 0)
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
