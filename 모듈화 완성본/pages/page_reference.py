# pages/page_reference.py

import streamlit as st

from config import (
    SESS_CURRENT_PAGE,
    PAGE_DOCUMENT,
    PAGE_COMPLETED,
)


# 업무참고용 구글시트 설정
GOOGLE_SHEET_ID = "1KxZY_VGUfGjo8nWn1d01OVN007uTpbLSnNLX3Jf62nE"
SHEET_EDIT_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit?rm=demo"


def render():
    """
    '업무참고' 페이지 렌더링.
    app.py에서 current_page_to_display == PAGE_REFERENCE 일 때 호출.
    기존 inline 코드와 UI/동작을 그대로 유지한다.
    """

    st.subheader("📚 업무참고")

    # --------------------------------
    # 🔼 상단 아이콘 버튼 2개 (서류작성 / 완료업무)
    # --------------------------------
    col_blank_ref, col_doc_ref, col_done_ref = st.columns([8, 1, 1])

    with col_doc_ref:
        if st.button("📝 서류작성", key="nav_to_document_page_from_ref"):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_DOCUMENT
            st.rerun()

    with col_done_ref:
        if st.button("✅ 완료업무", key="nav_to_completed_from_ref"):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_COMPLETED
            st.rerun()

    # --------------------------------
    # 🟩 구글시트 임베딩
    # --------------------------------
    st.markdown("#### 🗂️ 업무참고 시트 (수정 가능)")

    # 기존 스타일 그대로
    st.markdown(
        """
        <style>
            .block-container {
                padding-bottom: 0rem !important;
            }
            iframe {
                margin-bottom: -20px !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.components.v1.iframe(
        src=SHEET_EDIT_URL,
        height=800,   # 충분히 길게 해서 내부 스크롤 줄임
        width=0,      # width=0 + container_width 로 100% 폭
        scrolling=True,
    )
