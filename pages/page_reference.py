# pages/page_reference.py

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
    get_sheet_column_widths,
)

# 어드민 전용 업무정리 스프레드시트 ID
ADMIN_WORK_REFERENCE_SHEET_KEY = "1TzJtn6at28EHt4FTHdD_rAMkINeXUqcPuQeTOKm192U"


def _get_reference_sheet_key_for_current_user() -> str:
    """
    현재 로그인 사용자가 사용할 업무정리 스프레드시트 ID 반환
    - 어드민: ADMIN_WORK_REFERENCE_SHEET_KEY 고정
    - 일반 테넌트: Accounts 시트에 매핑된 work_sheet_key 사용
    """
    tenant_id = get_current_tenant_id()
    is_admin = st.session_state.get(SESS_IS_ADMIN, False)

    if is_admin:
        return ADMIN_WORK_REFERENCE_SHEET_KEY

    return get_work_sheet_key_for_tenant(tenant_id)


def _values_to_df(values: list[list[str]]) -> pd.DataFrame:
    """get_all_values() 결과를 안전하게 DataFrame으로 변환"""
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


@st.cache_data(ttl=600)
def load_reference_sheet_titles(sheet_key: str) -> list[str]:
    """업무정리 파일의 시트명 목록"""
    client = get_gspread_client()
    if client is None:
        return []

    sh = client.open_by_key(sheet_key)
    return [ws.title for ws in sh.worksheets()]


@st.cache_data(ttl=300)
def load_reference_sheet_df(sheet_key: str, sheet_name: str) -> pd.DataFrame:
    """특정 시트 내용을 DataFrame으로 로드"""
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


def render():
    st.markdown("## 📚 업무정리 / 업무참고")

    # 현재 사용자에게 연결된 업무정리 스프레드시트
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

    # 셀 줄바꿈 / 가독성 CSS
    st.markdown(
        """
        <style>
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

    # ===== 시트 목록 / 선택 =====
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

    # ===== 선택된 시트 데이터 로드 =====
    df = load_reference_sheet_df(sheet_key, selected_sheet)
    if df is None or df.empty:
        st.info(f"시트 '{selected_sheet}' 에 데이터가 없습니다. 원본 구글시트에서 내용을 입력해주세요.")
        df = pd.DataFrame()

    st.caption("※ 각 셀은 자동 줄바꿈 + 내용 길이에 따라 행 높이가 자동 조절됩니다.")

    # 시트 줄 수에 따라 전체 테이블 높이 대략 조정
    row_count = len(df) if not df.empty else 5
    row_height = 28
    base_height = 80
    max_height = 900
    table_height = min(base_height + row_count * row_height, max_height)

    # 구글시트에서 열 너비 읽기 시도
    try:
        col_width_map = get_sheet_column_widths(sheet_key, selected_sheet)
    except Exception:
        col_width_map = {}

    # AgGrid 옵션 설정
    gb = GridOptionsBuilder.from_dataframe(df)

    # 조회 전용 + 줄바꿈 + autoHeight
    gb.configure_default_column(
        editable=False,
        wrapText=True,
        autoHeight=True,
        resizable=True,
    )

    # 각 컬럼에 너비 적용
    for idx, col_name in enumerate(df.columns):
        width = col_width_map.get(idx)

        if not width:
            # 🔹 fallback: 내용 길이 기반으로 자동 폭 계산
            col_series = df[col_name].astype(str)
            sample = col_series.head(100).tolist()
            if sample:
                max_len = max([len(str(col_name))] + [len(v) for v in sample])
            else:
                max_len = len(str(col_name))
            width = max(80, min(max_len * 10 + 40, 600))

        gb.configure_column(col_name, width=width)

    grid_options = gb.build()
    grid_options["domLayout"] = "normal"

    # AgGrid 렌더링 (조회 전용)
    AgGrid(
        df,
        gridOptions=grid_options,
        theme="streamlit",
        height=table_height,
        fit_columns_on_grid_load=False,             # 구글시트 width / fallback width 사용
        data_return_mode=DataReturnMode.AS_INPUT,
        update_mode=GridUpdateMode.NO_UPDATE,      # 편집 없음
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
    )

    # 안내 문구
    st.info("업무정리 편집은 원본시트에서 해주세요.")
