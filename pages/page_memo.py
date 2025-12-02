# pages/page_memo.py

import streamlit as st

from config import (
    MEMO_LONG_SHEET_NAME,
    MEMO_MID_SHEET_NAME,
)

from core.google_sheets import (
    read_memo_from_sheet,
    save_memo_to_sheet,
)


# --- 메모 로드/저장 래퍼 함수들 ---
@st.cache_data(ttl=60)     # ✅ 이 줄 추가
def load_long_memo() -> str:
    """구글시트 '장기메모' 시트에서 A1 내용 읽기"""
    return read_memo_from_sheet(MEMO_LONG_SHEET_NAME)

def save_long_memo(content: str) -> bool:
    """장기 메모 저장 후 캐시 초기화"""
    ok = save_memo_to_sheet(MEMO_LONG_SHEET_NAME, content)
    if ok:
        load_long_memo.clear()   # 이제 정상 동작 (캐시 클리어)
    return ok


@st.cache_data(ttl=60)     # ✅ 이 줄 추가
def load_mid_memo() -> str:
    """구글시트 '중기메모' 시트에서 A1 내용 읽기"""
    return read_memo_from_sheet(MEMO_MID_SHEET_NAME)

def save_mid_memo(content: str) -> bool:
    """중기 메모 저장 후 캐시 초기화"""
    ok = save_memo_to_sheet(MEMO_MID_SHEET_NAME, content)
    if ok:
        load_mid_memo.clear()    # 여기도 정상 동작
    return ok


# --- 렌더 함수 ---

def render():
    """
    메모장 페이지 렌더링 함수.
    app.py 에서 current_page_to_display == PAGE_MEMO 일 때 호출.
    기존 UI/동작과 동일하게 유지.
    """
    st.subheader("🗒️ 메모장")

    st.markdown("---")
    col_long, col_mid = st.columns(2)

    # ----- 왼쪽: 장기보존 메모 -----
    with col_long:
        st.markdown("### 📌 장기보존 메모")
        memo_long_content = load_long_memo()
        edited_memo_long = st.text_area(
            "🗂️ 장기보존 내용",
            value=memo_long_content,
            height=300,
            key="memo_long_text_area",
        )
        if st.button("💾 장기메모 저장", key="save_memo_long_btn", use_container_width=True):
            if save_long_memo(edited_memo_long):
                st.success("✅ 장기보존 메모가 저장되었습니다.")
                st.rerun()
            else:
                st.error("장기메모 저장에 실패했습니다.")

    # ----- 오른쪽: 중기 메모 -----
    with col_mid:
        st.markdown("### 🗓 중기 메모")
        memo_mid_content = load_mid_memo()
        edited_memo_mid = st.text_area(
            "📘 중기메모",
            value=memo_mid_content,
            height=300,
            key="memo_mid_text_area",
        )
        if st.button("💾 중기메모 저장", key="save_memo_mid_btn", use_container_width=True):
            if save_mid_memo(edited_memo_mid):
                st.success("✅ 중기메모가 저장되었습니다.")
                st.rerun()
            else:
                st.error("중기메모 저장에 실패했습니다.")
