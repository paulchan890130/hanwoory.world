# pages/page_completed.py

import streamlit as st
import pandas as pd

from config import COMPLETED_TASKS_SHEET_NAME
from core.google_sheets import read_data_from_sheet


# --- 완료업무 로드 함수 (이 파일 안에서 자체 정의) ---

@st.cache_data(ttl=60)
def load_completed_tasks_from_sheet():
    """
    구글시트 '완료업무' 시트에서 데이터 읽어오기.
    - config.COMPLETED_TASKS_SHEET_NAME 기준
    - 빈 경우 [] 반환
    """
    records = read_data_from_sheet(
        COMPLETED_TASKS_SHEET_NAME,
        default_if_empty=[]
    )
    if not records:
        return []
    return records


def render():
    """완료업무 페이지 렌더링"""

    st.subheader("✅ 완료업무")

    # 검색창
    search_term_completed = st.text_input(
        "🔍 검색",
        key="completed_tasks_search_term"
    )

    # 구글시트에서 완료업무 불러오기
    completed_tasks_list = load_completed_tasks_from_sheet()

    if not completed_tasks_list:
        st.info("완료된 업무가 없습니다.")
        return

    df_completed = pd.DataFrame(completed_tasks_list)

    # category 정리
    if "category" in df_completed.columns:
        df_completed["category"] = df_completed["category"].fillna("")

    # 완료일 기준 정렬
    if "complete_date" in df_completed.columns:
        df_completed["complete_date_dt"] = pd.to_datetime(
            df_completed["complete_date"],
            errors="coerce",
        )
        df_completed = df_completed.sort_values(
            by=["category", "complete_date_dt"],
            ascending=[True, False],
        )
        df_completed = df_completed.drop(columns=["complete_date_dt"])

    # 화면에서 숨길 컬럼 (id 등)
    columns_to_display = [
        col for col in df_completed.columns
        if col not in ["id"]   # 필요하면 여기서 더 빼면 됨
    ]

    # 검색어 필터
    if search_term_completed:
        df_completed_str = df_completed.astype(str)
        mask_completed = df_completed_str.apply(
            lambda row: search_term_completed.lower()
                        in row.str.lower().to_string(),
            axis=1,
        )
        df_completed_display = df_completed[mask_completed][columns_to_display]
    else:
        df_completed_display = df_completed[columns_to_display]

    # 표 출력 (읽기 전용)
    st.dataframe(
        df_completed_display.reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
    )

    # 나중에 수정 기능 붙이고 싶으면 여기 아래에
    # st.data_editor + 저장 로직 추가하면 됨
