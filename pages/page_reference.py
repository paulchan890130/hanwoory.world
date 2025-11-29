# pages/page_reference.py

import pandas as pd
import streamlit as st

from config import (
    SESS_CURRENT_PAGE,
    PAGE_DOCUMENT,
    PAGE_COMPLETED,
)

from core.google_sheets import (
    get_gspread_client,
    get_work_sheet_key_for_tenant,   # 🔹 추가
    get_current_tenant_id,           # 🔹 추가 (google_sheets 쪽 함수)
)

# ---------- 공통: values → DataFrame 변환 (헤더 깨져도 안전하게) ----------
def _values_to_df(values: list[list[str]]) -> pd.DataFrame:
    if not values:
        return pd.DataFrame()

    raw_header = values[0]
    data_rows = values[1:]

    header = []
    used = {}
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


# ---------- 1) 모든 시트 로드 (시트명 → DataFrame) ----------
@st.cache_data(ttl=60)
def load_all_reference_sheets() -> dict[str, pd.DataFrame]:
    client = get_gspread_client()
    if client is None:
        return {}

    tenant_id = get_current_tenant_id()
    sheet_key = get_work_sheet_key_for_tenant(tenant_id)

    sh = client.open_by_key(sheet_key)
    result: dict[str, pd.DataFrame] = {}
    for ws in sh.worksheets():
        values = ws.get_all_values()
        df = _values_to_df(values)
        result[ws.title] = df
    return result


# ---------- 2) 특정 시트 저장 ----------
def save_reference_sheet(sheet_name: str, df: pd.DataFrame) -> bool:
    client = get_gspread_client()
    if client is None:
        st.error("Google Sheets 클라이언트를 생성하지 못했습니다.")
        return False

    tenant_id = get_current_tenant_id()
    sheet_key = get_work_sheet_key_for_tenant(tenant_id)

    sh = client.open_by_key(sheet_key)
    try:
        ws = sh.worksheet(sheet_name)
    except Exception as e:
        st.error(f"시트 '{sheet_name}' 를 찾지 못했습니다: {e}")
        return False
    try:
        ws = sh.worksheet(sheet_name)
    except Exception as e:
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
    except Exception as e:
        st.error(f"업무정리 시트 저장 중 오류: {e}")
        return False


# ---------- 3) 메인 렌더 ----------
def render():
    st.markdown("## 📚 업무정리 / 업무참고")

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
        # 현재 테넌트 기준 업무정리 스프레드시트 ID로 링크 생성
        tenant_id = get_current_tenant_id()
        sheet_key = get_work_sheet_key_for_tenant(tenant_id)
        sheet_edit_url = f"https://docs.google.com/spreadsheets/d/{sheet_key}/edit"

        st.link_button(
            "↗ 원본 구글시트 열기",
            sheet_edit_url,
            use_container_width=True,
        )


    st.markdown("---")

    # ===== 1) 셀 여러 줄 표시 + 표 최대 확장 CSS =====
    # ===== 1) 셀 여러 줄 표시 + 표 최대 확장 CSS =====
    st.markdown(
        """
        <style>
        /* 🔹 data_editor 셀 안에서 줄바꿈 허용 + 자동 줄바꿈 */
        div[data-testid="stDataEditor"] div[role="cell"] {
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
        }
        /* 셀 안의 텍스트 컨테이너도 줄바꿈 상속 */
        div[data-testid="stDataEditor"] div[role="cell"] * {
            white-space: inherit !important;
        }

        /* 🔹 data_editor 전체 높이 제한 완화 (스크롤 박스 높이 늘리기) */
        div[data-testid="stDataEditor"] div[role="rowgroup"] {
            max-height: none !important;
        }

        /* 🔹 dataframe(조회 전용 표)도 동일하게 줄바꿈 */
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
    all_sheets = load_all_reference_sheets()
    if not all_sheets:
        st.error("업무정리 시트를 불러오지 못했습니다.")
        return

    sheet_names = list(all_sheets.keys())

    # 보기 범위 선택: 전체(검색용) vs 특정 시트 편집
    view_mode = st.radio(
        "보기 모드 선택",
        ("전체(검색용)", "특정 시트 편집"),
        horizontal=True,
    )

    selected_sheet = None
    if view_mode == "특정 시트 편집":
        selected_sheet = st.selectbox("편집할 시트를 선택하세요", sheet_names)

    # 공통 검색어
    search = st.text_input(
        "검색어 (업무명, 내용, 비고 등)",
        placeholder="예: F4 재발급, 여권번호 변경, 초청장",
    )
    q = search.strip()

    # ===== 3-A) 전체(검색용) 모드: 시트별로 나눠서 검색 / 보기 =====
    if view_mode == "전체(검색용)":
        hit_any = False

        st.caption("※ 전체 모드는 시트별 조회 전용입니다. 편집은 '특정 시트 편집'에서 하세요.")

        for sheet_name, df in all_sheets.items():
            if df is None or df.empty:
                continue

            view_df = df

            # 검색어가 있으면 해당 시트 안에서만 필터링
            if q:
                mask = df.apply(
                    lambda row: row.astype(str).str.contains(q, case=False, na=False).any(),
                    axis=1,
                )
                view_df = df[mask]

            # 이 시트에서 보여줄 행이 없으면 스킵
            if view_df is None or view_df.empty:
                continue

            hit_any = True

            with st.expander(f"📄 {sheet_name} (행 {len(view_df)}개)", expanded=True):
                st.dataframe(
                    view_df,
                    use_container_width=True,
                    height=400,  # 시트별로 적당한 높이
                )

        if not hit_any:
            if q:
                st.info("검색 결과가 없습니다.")
            else:
                st.info("표시할 데이터가 없습니다.")
        return

    # ===== 3-B) 특정 시트 편집 모드 =====
    if not selected_sheet:
        st.info("편집할 시트를 먼저 선택하세요.")
        return

    df = all_sheets.get(selected_sheet, pd.DataFrame())

    if df is None or df.empty:
        st.info(f"시트 '{selected_sheet}' 에 데이터가 없습니다. 아래 표에서 직접 추가 후 저장하세요.")

    # 검색어가 있으면 조회 전용
    if q:
        mask = df.apply(
            lambda row: row.astype(str).str.contains(q, case=False, na=False).any(),
            axis=1,
        )
        view_df = df[mask].copy()

        st.caption("※ 검색 상태에서는 조회용으로만 보여줍니다. 수정/추가는 검색어를 지우고 전체 보기 상태에서 하세요.")
        st.dataframe(
            view_df,
            use_container_width=True,
            height=720,
        )
        return

    # === 편집 가능한 표 (여러 줄 표시 + 화면 꽉 채우기) ===
    st.caption(f"✏ 현재 편집 중인 시트: **{selected_sheet}**")
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",   # 아래에서 행 추가 가능
        height=720,           # 화면을 넉넉하게 사용
    )

    if st.button("💾 변경사항 저장", type="primary"):
        if save_reference_sheet(selected_sheet, edited_df):
            st.success("업무정리 시트가 저장되었습니다.")
            load_all_reference_sheets.clear()
            st.rerun()
        else:
            st.error("저장 중 오류가 발생했습니다.")