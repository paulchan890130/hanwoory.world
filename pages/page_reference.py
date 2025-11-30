# pages/page_reference.py

import pandas as pd
import streamlit as st

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
)

# 🔹 어드민 전용 업무정리 스프레드시트 ID
#   (https://docs.google.com/spreadsheets/d/<이 부분>/edit)
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


# ---------- 1) 시트 로드 (sheet_key를 인자로 받아 캐시) ----------
@st.cache_data(ttl=60)
def load_all_reference_sheets(sheet_key: str) -> dict[str, pd.DataFrame]:
    client = get_gspread_client()
    if client is None:
        return {}

    sh = client.open_by_key(sheet_key)
    result: dict[str, pd.DataFrame] = {}
    for ws in sh.worksheets():
        values = ws.get_all_values()
        df = _values_to_df(values)
        result[ws.title] = df
    return result


# ---------- 2) 특정 시트 저장 ----------
def save_reference_sheet(sheet_key: str, sheet_name: str, df: pd.DataFrame) -> bool:
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
        # 원래 헤더는 그대로 유지, 데이터만 교체
        values = ws.get_all_values()
        if values:
            raw_header = values[0]
        else:
            raw_header = list(df.columns)

        df = df.fillna("")
        rows = df.astype(str).values.tolist()

        ws.clear()
        ws.update([raw_header] + rows)
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

    # ===== 1) 셀 여러 줄 표시 + 표 최대 확장 CSS =====
    st.markdown(
        """
        <style>
        /* data_editor 셀 안에서 줄바꿈 허용 + 자동 줄바꿈 */
        div[data-testid="stDataEditor"] div[role="cell"] {
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
        }
        div[data-testid="stDataEditor"] div[role="cell"] * {
            white-space: inherit !important;
        }

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

    # ===== 2) 시트 전체 로드 =====
    all_sheets = load_all_reference_sheets(sheet_key)
    if not all_sheets:
        st.error("업무정리 시트를 불러오지 못했습니다.")
        return

    sheet_names = list(all_sheets.keys())

    # ===== 3) 드롭다운으로 한 번에 한 시트만 선택 =====
    prev_selected = st.session_state.get("reference_selected_sheet")
    if prev_selected in sheet_names:
        default_index = sheet_names.index(prev_selected)
    else:
        default_index = 0

    selected_sheet = st.selectbox(
        "📂 편집할 시트를 선택하세요",
        sheet_names,
        index=default_index,
    )
    st.session_state["reference_selected_sheet"] = selected_sheet

    df = all_sheets.get(selected_sheet, pd.DataFrame())
    if df is None or df.empty:
        st.info(f"시트 '{selected_sheet}' 에 데이터가 없습니다. 아래 표에서 직접 추가 후 저장하세요.")

    st.caption("※ 각 셀은 자동 줄바꿈됩니다. 글이 길어도 셀 안에서 모두 보입니다.")

    # ===== 4) 시트 줄 수에 따라 전체 테이블 높이 자동 조정 =====
    row_count = len(df) if not df.empty else 5
    row_height = 28   # 대략적인 한 줄 높이(px)
    base_height = 80
    max_height = 1000
    table_height = min(base_height + row_count * row_height, max_height)

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        height=table_height,
    )

    if st.button("💾 현재 시트 저장", type="primary"):
        if save_reference_sheet(sheet_key, selected_sheet, edited_df):
            st.success("업무정리 시트가 저장되었습니다.")
            load_all_reference_sheets.clear()  # 캐시 비우기
            st.rerun()
        else:
            st.error("저장 중 오류가 발생했습니다.")
