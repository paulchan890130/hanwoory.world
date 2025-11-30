import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode

from config import (
    SESS_CURRENT_PAGE,
    PAGE_DOCUMENT,
    PAGE_COMPLETED,
    SESS_IS_ADMIN,
)

from core.google_sheets import (
    get_gspread_client,
    get_work_sheet_key_for_tenant,
    get_current_tenant_id,
    get_sheet_column_widths,  # 🔹 구글시트 열 너비 읽기
)

# 🔹 어드민 전용 업무정리 스프레드시트 ID
ADMIN_WORK_REFERENCE_SHEET_KEY = "1TzJtn6at28EHt4FTHdD_rAMkINeXUqcPuQeTOKm192U"


# ---------- 공통: values → DataFrame 변환 (헤더 깨져도 안전하게) ----------
def _values_to_df(values: list[list[str]]) -> pd.DataFrame:
    if not values:
        return pd.DataFrame()

    raw_header = values[0]
    data_rows = values[1:]

    header: list[str] = []
    used: dict[str, int] = {}

    for idx, h in enumerate(raw_header):
        name = (h or "").strip()
        if not name:
            name = f"col_{idx+1}"
        if name in used:
            used[name] += 1
            name = f"{name}_{used[name]}"
        else:
            used[name] = 1
        header.append(name)

    df = pd.DataFrame(data_rows, columns=header)
    return df


def _col_index_to_letter(n: int) -> str:
    """1 → A, 2 → B, ..."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _get_reference_sheet_key_for_current_user() -> str:
    """
    현재 로그인 사용자가 어떤 업무정리 스프레드시트를 써야 하는지 결정.
    - 어드민(SESS_IS_ADMIN=True): 항상 ADMIN_WORK_REFERENCE_SHEET_KEY 사용
    - 일반 계정: 테넌트별 get_work_sheet_key_for_tenant() 사용
    """
    tenant_id = get_current_tenant_id()
    is_admin = st.session_state.get(SESS_IS_ADMIN, False)

    if is_admin:
        return ADMIN_WORK_REFERENCE_SHEET_KEY

    return get_work_sheet_key_for_tenant(tenant_id)


# ---------- 1) 시트 목록 / 단일 시트 로드 ----------
@st.cache_data(ttl=600)
def load_reference_sheet_titles(sheet_key: str) -> list[str]:
    client = get_gspread_client()
    if client is None:
        return []

    sh = client.open_by_key(sheet_key)
    return [ws.title for ws in sh.worksheets()]


@st.cache_data(ttl=300)
def load_reference_sheet_df(sheet_key: str, sheet_name: str) -> pd.DataFrame:
    client = get_gspread_client()
    if client is None:
        return pd.DataFrame()

    sh = client.open_by_key(sheet_key)
    try:
        ws = sh.worksheet(sheet_name)
    except Exception:
        return pd.DataFrame()

    values = ws.get_all_values()
    return _values_to_df(values)


# ---------- 2) 특정 시트 저장 (부분 업데이트) ----------
def save_reference_sheet(sheet_key: str, sheet_name: str, edited_df: pd.DataFrame) -> bool:
    client = get_gspread_client()
    if client is None:
        st.error("Google Sheets 클라이언트를 생성하지 못했습니다.")
        return False

    sh = client.open_by_key(sheet_key)
    try:
        ws = sh.worksheet(sheet_name)
    except Exception as e:  # noqa: BLE001
        st.error(f"시트 '{sheet_name}' 를 찾지 못했습니다: {e}")
        return False

    try:
        values = ws.get_all_values()

        # 1) 완전히 빈 시트라면: 헤더 + 내용 한 번에 채우기 (최초 1회)
        if not values:
            edited_df = (edited_df or pd.DataFrame()).fillna("")
            header = list(edited_df.columns)
            rows = edited_df.astype(str).values.tolist() if not edited_df.empty else []
            if header or rows:
                ws.update([header] + rows)
            return True

        # 2) 기존 시트가 있는 경우: 변경된 부분만 patch
        raw_header = values[0]
        existing_rows = values[1:]

        # 기존 데이터 프레임 (헤더는 구글시트 원본 그대로 사용)
        existing_df = pd.DataFrame(existing_rows, columns=raw_header).astype(str)

        # 편집된 DF 문자열화
        if edited_df is None:
            edited_df = pd.DataFrame()
        edited_df_str = edited_df.fillna("").astype(str)

        header = raw_header
        existing_row_count = len(existing_df)
        edited_row_count = len(edited_df_str)

        # 2-1) 기존 행 ↔ 편집된 행 비교 → 달라진 셀만 ws.update()
        min_row_count = min(existing_row_count, edited_row_count)
        for r_idx in range(min_row_count):
            row_series = edited_df_str.iloc[r_idx]

            for c_idx, col_name in enumerate(header):
                new_val = str(row_series.get(col_name, "")).strip()
                old_val = str(existing_df.iloc[r_idx].get(col_name, "")).strip()

                if new_val != old_val:
                    row_number = r_idx + 2  # 헤더가 1행이므로 +2
                    col_letter = _col_index_to_letter(c_idx + 1)
                    cell_addr = f"{col_letter}{row_number}"
                    # 🔹 셀 단위 patch
                    ws.update(cell_addr, new_val)

        # 2-2) 편집된 쪽에 행이 더 많으면 → 새 행 append
        if edited_row_count > existing_row_count:
            new_rows = []
            for r_idx in range(existing_row_count, edited_row_count):
                row_series = edited_df_str.iloc[r_idx]
                row_values = [
                    str(row_series.get(col, "")).strip()
                    for col in header
                ]
                new_rows.append(row_values)

            if new_rows:
                ws.append_rows(new_rows)

        # 2-3) 기존 시트에 행이 더 많으면 → 아래쪽부터 삭제
        if existing_row_count > edited_row_count:
            # 아래 행부터 삭제해야 인덱스가 안 꼬임
            for r_idx in range(existing_row_count - 1, edited_row_count - 1, -1):
                row_number = r_idx + 2  # 헤더 +1
                ws.delete_rows(row_number)

        return True

    except Exception as e:  # noqa: BLE001
        st.error(f"업무정리 시트 저장 중 오류: {e}")
        return False


# ---------- 3) 메인 렌더 ----------
def render():
    st.markdown("## 📚 업무정리 / 업무참고")

    # 현재 사용자가 사용할 업무정리 스프레드시트 ID 계산
    sheet_key = _get_reference_sheet_key_for_current_user()

    # 상단 빠른 이동 / 원본 시트 열기
    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        if st.button("📄 문서 자동작성", use_container_width=True):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_DOCUMENT
            st.rerun()

    with col2:
        if st.button("✅ 완료업무 조회", use_container_width=True):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_COMPLETED
            st.rerun()

    with col3:
        sheet_edit_url = f"https://docs.google.com/spreadsheets/d/{sheet_key}/edit"
        st.link_button(
            "↗ 원본 구글시트 열기",
            sheet_edit_url,
            use_container_width=True,
        )

    st.markdown("---")

    # ===== 1) (예전 data_editor용 CSS) 줄바꿈만 유지 =====
    st.markdown(
        """
        <style>
        /* dataframe(조회 전용 표)도 동일하게 줄바꿈 */
        div[data-testid="stDataFrame"] td {
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
        }
        div[data-testid="stDataFrame"] td * {
            white-space: inherit !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ===== 2) 시트 목록 / 선택 =====
    sheet_names = load_reference_sheet_titles(sheet_key)
    if not sheet_names:
        st.error("업무정리 시트 목록을 불러오지 못했습니다.")
        return

    prev_selected = st.session_state.get("reference_selected_sheet")
    if prev_selected in sheet_names:
        default_index = sheet_names.index(prev_selected)
    else:
        default_index = 0

    selected_sheet = st.selectbox(
        "📂 조회할 시트를 선택하세요",
        sheet_names,
        index=default_index,
    )
    st.session_state["reference_selected_sheet"] = selected_sheet

    # ===== 3) 선택된 시트 데이터 로드 =====
    df = load_reference_sheet_df(sheet_key, selected_sheet)
    if df is None or df.empty:
        st.info(f"시트 '{selected_sheet}' 에 데이터가 없습니다. 원본 구글시트에서 내용을 입력해주세요.")
        df = pd.DataFrame()  # 빈 DF라도 넘기기

    st.caption("※ 각 셀은 자동 줄바꿈 + 내용 길이에 따라 행 높이가 자동 조절됩니다.")

    # 시트 줄 수에 따라 전체 테이블 높이 대략 조정
    row_count = len(df) if not df.empty else 5
    row_height = 28
    base_height = 80
    max_height = 900
    table_height = min(base_height + row_count * row_height, max_height)

    # 1) 구글시트에서 열 너비 읽어오기
    col_width_map = get_sheet_column_widths(sheet_key, selected_sheet)
    # col_width_map: {0: 120, 1: 200, ...}

    # 2) GridOptionsBuilder 생성
    gb = GridOptionsBuilder.from_dataframe(df)

    # 🔹 기본 옵션: 조회 전용 + 줄바꿈 + autoHeight
    gb.configure_default_column(
        editable=False,     # ✅ 이제 이 화면은 조회만 가능 (편집 불가)
        wrapText=True,      # 텍스트 줄바꿈
        autoHeight=True,    # 내용에 맞춰 행 높이 자동 조정
        resizable=True,     # 칼럼 폭 조정 가능
    )

    # 3) 각 컬럼에 구글시트 너비 적용 (없으면 기본 150)
    for idx, col_name in enumerate(df.columns):
        width = col_width_map.get(idx)
        if width:
            gb.configure_column(col_name, width=width)
        else:
            gb.configure_column(col_name, width=150)

    grid_options = gb.build()
    grid_options["domLayout"] = "normal"

    # 4) AgGrid 렌더링 (조회 전용)
    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        theme="streamlit",
        height=table_height,
        fit_columns_on_grid_load=False,             # 🔹 구글시트 width 그대로 사용
        data_return_mode=DataReturnMode.AS_INPUT,
        update_mode=GridUpdateMode.NO_UPDATE,      # 🔹 편집 안 하니까 NO_UPDATE
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
    )

    # 나중에 다시 살릴 수 있도록 편집/저장 로직은 주석으로 보관
    # edited_df = pd.DataFrame(grid_response["data"])
    #
    # # 5) 저장 버튼 → 기존 저장 로직 (현재는 비활성화)
    # if st.button("💾 변경사항 저장 (AgGrid)", type="primary", use_container_width=True):
    #     if save_reference_sheet(sheet_key, selected_sheet, edited_df):
    #         st.success("업무정리 시트가 저장되었습니다.")
    #         # 캐시 초기화
    #         load_reference_sheet_df.clear()
    #         load_reference_sheet_titles.clear()
    #         st.rerun()
    #     else:
    #         st.error("저장 중 오류가 발생했습니다.")

    # ✅ 요구하신 안내 문구
    st.info("업무정리 편집은 원본시트에서 해주세요.")

