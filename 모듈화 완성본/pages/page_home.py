# pages/page_home.py

import datetime
import uuid

import pandas as pd
import streamlit as st

from config import (
    # 세션 상태 키
    SESS_DF_CUSTOMER,
    SESS_PLANNED_TASKS_TEMP,
    SESS_ACTIVE_TASKS_TEMP,
    # 시트 이름
    MEMO_SHORT_SHEET_NAME,
)

from core.google_sheets import (
    read_memo_from_sheet,
    save_memo_to_sheet,
    read_data_from_sheet,
    write_data_to_sheet,
)

# ─────────────────────────────
# 0) 시트 탭 이름 (홈에서 쓰는 것만 로컬 상수로 정의)
#    → config.py로 옮겨도 되지만, 일단 여기서 확실하게 정의해 두자
# ─────────────────────────────
PLANNED_TASKS_SHEET_NAME = "예정업무"
ACTIVE_TASKS_SHEET_NAME = "진행업무"
COMPLETED_TASKS_SHEET_NAME = "완료업무"


# ─────────────────────────────
# 1) 단기메모 로드/저장
# ─────────────────────────────
@st.cache_data(ttl=600)
def load_short_memo():
    """구글시트 '단기메모' 시트에서 A1 셀 내용을 읽어옵니다."""
    return read_memo_from_sheet(MEMO_SHORT_SHEET_NAME)


def save_short_memo(content: str) -> bool:
    """
    단기메모 저장.
    - 성공하면 True, 실패하면 False
    - 저장 후 load_short_memo 캐시 초기화
    """
    if save_memo_to_sheet(MEMO_SHORT_SHEET_NAME, content):
        load_short_memo.clear()
        return True
    return False


# ─────────────────────────────
# 2) 예정업무 / 진행업무 / 완료업무 저장 함수
# ─────────────────────────────
def save_planned_tasks_to_sheet(data_list_of_dicts):
    """예정업무 전체를 시트에 덮어쓰기 저장"""
    header = ['id', 'date', 'period', 'content', 'note']
    return write_data_to_sheet(PLANNED_TASKS_SHEET_NAME, data_list_of_dicts, header_list=header)


def save_active_tasks_to_sheet(data_list_of_dicts):
    """진행업무 전체를 시트에 덮어쓰기 저장"""
    header = [
        'id', 'category', 'date', 'name', 'work',
        'source_original', 'details', 'processed', 'processed_timestamp'
    ]
    return write_data_to_sheet(ACTIVE_TASKS_SHEET_NAME, data_list_of_dicts, header_list=header)


@st.cache_data(ttl=300)
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
    ok = write_data_to_sheet(COMPLETED_TASKS_SHEET_NAME, records, header_list=header)
    if ok:
        load_completed_tasks_from_sheet.clear()
    return ok


# ─────────────────────────────
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
    with home_col_left:
        st.subheader("1. 📅 일정 달력")

        google_calendar_embed_code = """
        <iframe src="https://calendar.google.com/calendar/embed?height=600&wkst=1&ctz=Asia%2FSeoul&showPrint=0&src=d2tkd2hmbEBnbWFpbC5jb20&src=ZDEzOGVmN2MzNDVjY2YwNzE5MDBjOGVmMDVlMDlkYzZmZDFkZWVjNzQ5ZjBmNWMwM2I3NGZhY2EyODkwMGI5ZkBncm91cC5jYWxlbmRhci5nb29nbGUuY29t&src=a28uc291dGhfa29yZWEjaG9saWRheUBncm91cC52LmNhbGVuZGFyLmdvb2dsZS5jb20&color=%237986cb&color=%239e69af&color=%230b8043"
                style="border:solid 1px #777" width="100%" height="600" frameborder="0" scrolling="no"></iframe>
        """

        st.components.v1.html(google_calendar_embed_code, height=630, scrolling=True)

        # 단기 메모
        memo_short_content = load_short_memo()
        edited_memo_short = st.text_area(
            "📗 단기메모",
            value=memo_short_content,
            height=200,
            key="memo_short_text_area",
        )
        if st.button("💾 단기메모 저장", key="save_memo_short_btn", use_container_width=True):
            if save_short_memo(edited_memo_short):
                st.success("단기메모를 저장했습니다.")
            else:
                st.error("단기메모 저장 중 오류가 발생했습니다.")

    # ── 2·3. 오른쪽: 만기 알림(등록증/여권) ─────────────────
    with home_col_right:
        st.subheader("2. 🪪 등록증 만기 4개월 전")

        df_customers_for_alert_view = st.session_state.get(SESS_DF_CUSTOMER, pd.DataFrame())
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

        # 3. 여권 만기
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
                st.rerun()
        with c_no:
            if st.button("❌ 아니오, 취소합니다", key="confirm_no", use_container_width=True):
                st.session_state["confirm_delete_idx"] = None
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
                st.rerun()

    # ── 5. 🛠️ 진행업무 ─────────────────────────────
    st.markdown("---")
    st.subheader("5. 🛠️ 진행업무")

    active_tasks = st.session_state.get(SESS_ACTIVE_TASKS_TEMP, [])
    구분_옵션_active_opts = ["출입국", "전자민원", "공증", "여권", "초청", "영주권", "기타"]
    구분_우선순위_map = {opt: i for i, opt in enumerate(구분_옵션_active_opts)}

    # 정렬: 미처리 → 처리됨, 구분, 처리시각, 날짜
    active_tasks.sort(key=lambda x: (
        not x.get('processed', False),
        구분_우선순위_map.get(x.get('category', "기타"), 99),
        pd.to_datetime(x.get('processed_timestamp', ''), errors='coerce')
        if x.get('processed') else pd.Timestamp.min,
        pd.to_datetime(x.get('date', "9999-12-31"), errors='coerce'),
    ))

    # 헤더
    h1, h2, h3, h4, h5, h6, h7, h8, h9, h10 = st.columns(
        [0.8, 0.8, 0.8, 1, 1, 2.5, 0.5, 0.5, 0.5, 0.5],
        gap="small",
    )
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

    # 각 행 렌더
    for task in active_tasks:
        uid = task["id"]
        cols = st.columns([0.8, 0.8, 0.8, 1, 1, 2.5, 0.5, 0.5, 0.5, 0.5], gap="small")

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

        # ✏️ 수정
        if cols[6].button("✏️", key=f"active_edit_{uid}", use_container_width=True):
            full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
            for i, t in enumerate(full_list):
                if t["id"] == uid:
                    t["category"] = new_category
                    t["date"] = new_date.strftime("%Y-%m-%d")
                    t["name"] = new_name
                    if not t.get("processed", False):
                        t["work"] = new_work
                        t["details"] = new_details
                    t["source_original"] = new_src
                    break
            save_active_tasks_to_sheet(full_list)
            st.success("✅ 진행업무가 수정되어 저장되었습니다.")
            st.rerun()

        # 🅿️ 처리 토글
        if cols[7].button("🅿️", key=f"active_proc_{uid}", use_container_width=True, help="처리 상태 변경"):
            full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
            for i, t in enumerate(full_list):
                if t["id"] == uid:
                    t["processed"] = not t.get("processed", False)
                    t["processed_timestamp"] = (
                        datetime.datetime.now().isoformat() if t["processed"] else " "
                    )
                    break
            save_active_tasks_to_sheet(full_list)
            st.info(f"진행업무(ID:{uid}) 처리 상태가 {'✅ 처리됨' if t['processed'] else '🕓 미처리'} 으로 변경되었습니다.")
            st.rerun()

        # ✅ 완료로 이동
        if cols[8].button("✅", key=f"active_complete_{uid}", use_container_width=True, help="완료 처리"):
            full_list = st.session_state[SESS_ACTIVE_TASKS_TEMP]
            completed_item = None
            for i, t in enumerate(full_list):
                if t["id"] == uid:
                    completed_item = full_list.pop(i)
                    completed_item["complete_date"] = datetime.date.today().strftime("%Y-%m-%d")
                    break
            if completed_item:
                completed_list = load_completed_tasks_from_sheet()
                completed_list.append(completed_item)
                save_completed_tasks_to_sheet(completed_list)
                st.session_state[SESS_ACTIVE_TASKS_TEMP] = full_list
                save_active_tasks_to_sheet(full_list)
                st.success("✅ 업무가 완료처리되어 ‘완료업무’ 페이지로 이동합니다.")
                st.rerun()

        # ❌ 삭제 요청
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
