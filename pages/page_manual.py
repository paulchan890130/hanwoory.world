# pages/page_manual.py

import streamlit as st

from core.manual_search import search_via_server


def render():
    """
    메뉴얼 검색 페이지 렌더링 함수.
    app.py 에서 current_page_to_display == PAGE_MANUAL 일 때 호출.
    기존 inline 코드와 UI/동작을 동일하게 유지.
    """

    st.subheader("🧭 메뉴얼 검색 (GPT 기반)")

    question = st.text_input(
        "궁금한 내용을 입력하세요",
        placeholder="예: F-4에서 F-5 변경 조건은?",
        key="manual_search_input",
    )

    if st.button("🔍 GPT로 검색하기", use_container_width=True):
        if question and question.strip():
            with st.spinner("답변 생성 중입니다..."):
                answer = search_via_server(question.strip())
            st.markdown("#### 🧠 GPT 요약 답변")
            st.write(answer)
        else:
            st.info("검색할 내용을 입력해주세요.")
