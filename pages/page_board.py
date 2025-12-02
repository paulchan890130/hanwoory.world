import datetime
import uuid
from typing import List, Dict, Optional
import html

import streamlit as st

from config import (
    SESS_USERNAME,
    SESS_IS_ADMIN,
    SESS_TENANT_ID,
    DEFAULT_TENANT_ID,
    BOARD_SHEET_NAME,
    BOARD_COMMENT_SHEET_NAME,
)

from core.google_sheets import (
    read_data_from_sheet,
    append_rows_to_sheet,
    get_gspread_client,
    get_worksheet,
    get_current_agent_info,
)

# ===== 상수 =====

BOARD_HEADERS = [
    "id",
    "tenant_id",
    "author_login",
    "office_name",
    "is_notice",   # "Y" or ""
    "category",
    "title",
    "content",
    "created_at",
    "updated_at",
]

COMMENT_HEADERS = [
    "id",
    "post_id",
    "tenant_id",
    "author_login",
    "office_name",
    "content",
    "created_at",
    "updated_at",
]

SESS_BOARD_SELECTED_ID     = "board_selected_id"
SESS_BOARD_EDIT_MODE       = "board_edit_mode"
SESS_BOARD_COMMENT_EDIT_ID = "board_comment_edit_id"


# ===== 유틸 =====

def _normalize_records(records: List[Dict], header_list: List[str]) -> List[Dict]:
    norm: List[Dict] = []
    for r in records or []:
        item = {}
        for h in header_list:
            item[h] = r.get(h, "")
        norm.append(item)
    return norm


@st.cache_data(ttl=10)
def load_board_posts() -> List[Dict]:
    """게시판 전체 글 목록 (공지 + 일반)"""
    records = read_data_from_sheet(BOARD_SHEET_NAME, default_if_empty=[]) or []
    records = _normalize_records(records, BOARD_HEADERS)
    # 작성일 기준 최신순
    records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    return records


def load_comments_for_post(post_id: str) -> List[Dict]:
    records = read_data_from_sheet(BOARD_COMMENT_SHEET_NAME, default_if_empty=[]) or []
    records = _normalize_records(records, COMMENT_HEADERS)
    filtered = [r for r in records if str(r.get("post_id", "")).strip() == str(post_id).strip()]
    filtered.sort(key=lambda r: str(r.get("created_at", "")))
    return filtered


def _append_board_post(rec: Dict) -> bool:
    """게시글 append (전체 덮어쓰기 X)"""
    ok = append_rows_to_sheet(BOARD_SHEET_NAME, [rec], BOARD_HEADERS)
    if ok:
        try:
            load_board_posts.clear()
        except Exception:
            pass
    return ok


def _update_row_by_id(sheet_name: str, header_list: List[str], record: Dict, id_field: str = "id") -> bool:
    """id 기준으로 한 행만 부분 수정"""
    try:
        client = get_gspread_client()
        ws = get_worksheet(client, sheet_name)
        values = ws.get_all_values()
        if not values:
            return False

        header = values[0]
        if id_field not in header:
            st.error(f"{sheet_name} 시트에 {id_field} 컬럼이 없습니다.")
            return False

        id_idx = header.index(id_field)
        target_row = None
        for idx, row in enumerate(values[1:], start=2):
            if len(row) > id_idx and str(row[id_idx]).strip() == str(record.get(id_field, "")).strip():
                target_row = idx
                break

        if not target_row:
            return False

        row_values = [str(record.get(col, "")) for col in header_list]
        ws.update(f"A{target_row}", [row_values])
        return True
    except Exception as e:
        st.error(f"❌ _update_row_by_id 오류 ({sheet_name}): {e}")
        return False


def _delete_rows_by_field(sheet_name: str, field_name: str, field_value: str) -> int:
    """field_name == field_value 인 행들 모두 삭제"""
    try:
        client = get_gspread_client()
        ws = get_worksheet(client, sheet_name)
        values = ws.get_all_values()
        if not values:
            return 0

        header = values[0]
        if field_name not in header:
            return 0

        idx_field = header.index(field_name)
        targets = []
        for idx, row in enumerate(values[1:], start=2):
            if len(row) > idx_field and str(row[idx_field]).strip() == str(field_value).strip():
                targets.append(idx)

        deleted = 0
        for r in reversed(targets):
            ws.delete_rows(r)
            deleted += 1
        return deleted
    except Exception as e:
        st.error(f"❌ _delete_rows_by_field 오류 ({sheet_name}): {e}")
        return 0


def _ensure_session_defaults():
    if SESS_BOARD_SELECTED_ID not in st.session_state:
        st.session_state[SESS_BOARD_SELECTED_ID] = None
    if SESS_BOARD_EDIT_MODE not in st.session_state:
        st.session_state[SESS_BOARD_EDIT_MODE] = False
    if SESS_BOARD_COMMENT_EDIT_ID not in st.session_state:
        st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None


# ===== 댓글 관련 함수 =====

def add_comment(post_id: str, tenant_id: str, username: str, office_name: str, content: str) -> bool:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rec = {
        "id": str(uuid.uuid4()),
        "post_id": post_id,
        "tenant_id": tenant_id,
        "author_login": username,
        "office_name": office_name,
        "content": content.strip(),
        "created_at": now,
        "updated_at": now,
    }
    return append_rows_to_sheet(BOARD_COMMENT_SHEET_NAME, [rec], COMMENT_HEADERS)


def update_comment_content(comment_id: str, new_content: str) -> bool:
    records = read_data_from_sheet(BOARD_COMMENT_SHEET_NAME, default_if_empty=[]) or []
    records = _normalize_records(records, COMMENT_HEADERS)
    target = None
    for r in records:
        if str(r.get("id", "")).strip() == str(comment_id).strip():
            target = r
            break
    if not target:
        return False

    target["content"] = new_content.strip()
    target["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return _update_row_by_id(BOARD_COMMENT_SHEET_NAME, COMMENT_HEADERS, target, id_field="id")


def delete_comment(comment_id: str) -> bool:
    deleted = _delete_rows_by_field(BOARD_COMMENT_SHEET_NAME, "id", comment_id)
    return deleted > 0


# ===== 메인 렌더 =====

def render():
    _ensure_session_defaults()

    username  = st.session_state.get(SESS_USERNAME, "")
    tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
    is_admin  = bool(st.session_state.get(SESS_IS_ADMIN, False))

    agent_info  = get_current_agent_info() or {}
    office_name = agent_info.get("office_name", "") or tenant_id

    st.markdown("### 📢 게시판")

    posts = load_board_posts()
    notices = [p for p in posts if str(p.get("is_notice", "")).strip().upper() == "Y"]
    normal_posts = [p for p in posts if str(p.get("is_notice", "")).strip().upper() != "Y"]

    # ─────────────────────
    # 1) 새 글 작성
    # ─────────────────────
    with st.expander("✏️ 새 글 작성", expanded=(len(posts) == 0)):
        col1, col2 = st.columns([3, 1])
        with col1:
            # key 제거
            new_title = st.text_input("제목")
        with col2:
            # key 제거
            new_category = st.text_input("분류", value="자유")
            if is_admin:
                new_is_notice = st.checkbox("🔔 공지로 등록", key="board_new_is_notice")
            else:
                new_is_notice = False

        # key 제거
        new_content = st.text_area("내용", height=200)

        if st.button("등록", key="board_new_submit", use_container_width=True):
            if not username:
                st.error("로그인 정보가 없습니다. 다시 로그인해주세요.")
            elif not new_title.strip():
                st.warning("제목을 입력해주세요.")
            elif not new_content.strip():
                st.warning("내용을 입력해주세요.")
            else:
                now = datetime.datetime.now().isoformat(timespec="seconds")
                rec = {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "author_login": username,
                    "office_name": office_name,
                    "is_notice": "Y" if new_is_notice else "",
                    "category": new_category.strip() or "자유",
                    "title": new_title.strip(),
                    "content": new_content.strip(),
                    "created_at": now,
                    "updated_at": now,
                }
                if _append_board_post(rec):
                    st.success("게시글이 등록되었습니다.")
                    # 🔻 여기서 session_state 초기화 코드 전체 삭제
                    # st.session_state["board_new_title"] = ""
                    # st.session_state["board_new_category"] = "자유"
                    # st.session_state["board_new_content"] = ""

                    st.session_state[SESS_BOARD_SELECTED_ID] = rec["id"]
                    st.session_state[SESS_BOARD_EDIT_MODE] = False
                    st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                    st.rerun()
                else:
                    st.error("게시글 저장 중 오류가 발생했습니다.")


    st.markdown("---")

    # ─────────────────────
    # 2) 공지사항 목록 (제목 클릭 → 내용 보기)
    # ─────────────────────
    st.markdown("#### 📌 공지사항")

    if not notices:
        st.info("등록된 공지사항이 없습니다.")
    else:
        header_cols = st.columns([2, 5, 3])
        header_cols[0].markdown("**작성일**")
        header_cols[1].markdown("**제목**")
        header_cols[2].markdown("**작성자(사무소)**")

        for n in notices:
            cols = st.columns([2, 5, 3])
            created = (n.get("created_at") or "")[:10]
            title = str(n.get("title") or "(제목 없음)")
            office = n.get("office_name") or n.get("tenant_id") or "-"

            with cols[0]:
                st.write(created or "-")
            with cols[1]:
                if st.button(
                    str(f"[공지] {title}"),
                    key=f"board_notice_{n.get('id','')}",
                    use_container_width=True,
                ):
                    st.session_state[SESS_BOARD_SELECTED_ID] = n.get("id")
                    st.session_state[SESS_BOARD_EDIT_MODE] = False
                    st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                    st.rerun()
            with cols[2]:
                st.write(office)

    st.markdown("---")

    # ─────────────────────
    # 3) 일반 게시글 목록
    # ─────────────────────
    st.markdown("#### 📂 게시글")

    if not normal_posts:
        st.info("아직 작성된 게시글이 없습니다.")
    else:
        header_cols = st.columns([2, 5, 3])
        header_cols[0].markdown("**작성일**")
        header_cols[1].markdown("**제목**")
        header_cols[2].markdown("**작성자(사무소)**")

        for p in normal_posts:
            cols = st.columns([2, 5, 3])
            created = (p.get("created_at") or "")[:10]
            title = str(p.get("title") or "(제목 없음)")
            office = p.get("office_name") or p.get("tenant_id") or "-"

            with cols[0]:
                st.write(created or "-")
            with cols[1]:
                if st.button(
                    title,
                    key=f"board_post_{p.get('id','')}",
                    use_container_width=True,
                ):
                    st.session_state[SESS_BOARD_SELECTED_ID] = p.get("id")
                    st.session_state[SESS_BOARD_EDIT_MODE] = False
                    st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                    st.rerun()
            with cols[2]:
                st.write(office)

    st.markdown("---")

    # ─────────────────────
    # 4) 선택 글 상세 / 수정 / 삭제
    # ─────────────────────
    posts_latest = load_board_posts()  # 최신 데이터
    selected_id = st.session_state.get(SESS_BOARD_SELECTED_ID)
    selected_post: Optional[Dict] = None
    for r in posts_latest:
        if str(r.get("id", "")) == str(selected_id):
            selected_post = r
            break

    if not selected_post:
        st.info("확인할 게시글을 선택해주세요.")
        return

    can_edit_post = is_admin or (selected_post.get("author_login") == username)

    st.markdown("#### 📄 선택한 글")

    if not st.session_state.get(SESS_BOARD_EDIT_MODE, False):
        # 보기 모드
        if selected_post.get("is_notice", "").upper() == "Y":
            st.markdown("**[공지사항]**")

        st.markdown(f"**제목:** {selected_post.get('title', '(제목 없음)')}")
        st.markdown(f"**작성자(사무소):** {selected_post.get('office_name') or selected_post.get('tenant_id')}")
        st.markdown(f"**작성일:** {selected_post.get('created_at')}")
        if selected_post.get("updated_at") and selected_post.get("updated_at") != selected_post.get("created_at"):
            st.markdown(f"**수정일:** {selected_post.get('updated_at')}")

        st.markdown("---")
        # 내용이 숫자여도 항상 문자열로 취급
        content = selected_post.get("content", "")
        content = "" if content is None else str(content)

        st.markdown("**내용**")

        safe_content = html.escape(content)
        st.markdown(
            f"""
            <div style="
                border: 1px solid #666;
                border-radius: 4px;
                padding: 8px;
                white-space: pre-wrap;
                font-size: 0.95rem;
            ">
                {safe_content}
            </div>
            """,
            unsafe_allow_html=True,
        )


        btn_cols = st.columns(3)
        with btn_cols[0]:
            if can_edit_post and st.button("✏️ 수정", key="board_edit_btn", use_container_width=True):
                st.session_state[SESS_BOARD_EDIT_MODE] = True
                st.rerun()
        with btn_cols[1]:
            if can_edit_post and st.button("🗑 삭제", key="board_delete_btn", use_container_width=True):
                _delete_rows_by_field(BOARD_SHEET_NAME, "id", selected_post["id"])
                _delete_rows_by_field(BOARD_COMMENT_SHEET_NAME, "post_id", selected_post["id"])
                try:
                    load_board_posts.clear()
                    load_comments_for_post.clear()
                except Exception:
                    pass
                st.success("게시글이 삭제되었습니다.")
                st.session_state[SESS_BOARD_SELECTED_ID] = None
                st.session_state[SESS_BOARD_EDIT_MODE] = False
                st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                st.rerun()
        with btn_cols[2]:
            if st.button("목록으로", key="board_back_btn", use_container_width=True):
                st.session_state[SESS_BOARD_SELECTED_ID] = None
                st.session_state[SESS_BOARD_EDIT_MODE] = False
                st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                st.rerun()

    else:
        # 수정 모드
        edit_title = st.text_input(
            "제목",
            value=selected_post.get("title", ""),
            key="board_edit_title",
        )
        edit_category = st.text_input(
            "분류",
            value=selected_post.get("category", "자유"),
            key="board_edit_category",
        )
        edit_content = st.text_area(
            "내용",
            value=selected_post.get("content", ""),
            height=250,
            key="board_edit_content",
        )

        edit_cols = st.columns(3)
        with edit_cols[0]:
            if st.button("💾 수정 저장", key="board_save_edit_btn", use_container_width=True):
                if not can_edit_post:
                    st.error("이 글을 수정할 권한이 없습니다.")
                elif not edit_title.strip():
                    st.warning("제목을 입력해주세요.")
                elif not edit_content.strip():
                    st.warning("내용을 입력해주세요.")
                else:
                    now = datetime.datetime.now().isoformat(timespec="seconds")
                    updated = dict(selected_post)
                    updated["title"] = edit_title.strip()
                    updated["category"] = edit_category.strip() or "자유"
                    updated["content"] = edit_content.strip()
                    updated["updated_at"] = now
                    if _update_row_by_id(BOARD_SHEET_NAME, BOARD_HEADERS, updated, id_field="id"):
                        try:
                            load_board_posts.clear()
                        except Exception:
                            pass
                        st.success("게시글이 수정되었습니다.")
                        st.session_state[SESS_BOARD_EDIT_MODE] = False
                        st.rerun()
                    else:
                        st.error("게시글 수정 중 오류가 발생했습니다.")
        with edit_cols[1]:
            if st.button("수정 취소", key="board_cancel_edit_btn", use_container_width=True):
                st.session_state[SESS_BOARD_EDIT_MODE] = False
                st.rerun()
        with edit_cols[2]:
            if can_edit_post and st.button("🗑 삭제", key="board_delete_edit_btn", use_container_width=True):
                _delete_rows_by_field(BOARD_SHEET_NAME, "id", selected_post["id"])
                _delete_rows_by_field(BOARD_COMMENT_SHEET_NAME, "post_id", selected_post["id"])
                try:
                    load_board_posts.clear()
                    load_comments_for_post.clear()
                except Exception:
                    pass
                st.success("게시글이 삭제되었습니다.")
                st.session_state[SESS_BOARD_SELECTED_ID] = None
                st.session_state[SESS_BOARD_EDIT_MODE] = False
                st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                st.rerun()

    # ─────────────────────
    # 5) 댓글 영역
    # ─────────────────────
    st.markdown("#### 💬 댓글")

    comments = load_comments_for_post(selected_post["id"])
    edit_comment_id = st.session_state.get(SESS_BOARD_COMMENT_EDIT_ID)

    if not comments:
        st.info("등록된 댓글이 없습니다.")
    else:
        for c in comments:
            can_edit_comment = is_admin or (c.get("author_login") == username)
            box = st.container()
            created = (c.get("created_at") or "")[:16]
            office = c.get("office_name") or c.get("tenant_id") or "-"

            with box:
                st.markdown(f"**[{created}] {office}**")

                if edit_comment_id == c["id"]:
                    # 댓글 수정 모드
                    new_content = st.text_area(
                        "댓글 내용 수정",
                        value=c.get("content", ""),
                        height=120,
                        key=f"comment_edit_content_{c['id']}",
                    )
                    btn_cols = st.columns(3)
                    with btn_cols[0]:
                        if st.button("💾 댓글 수정 저장", key=f"comment_save_{c['id']}", use_container_width=True):
                            if not can_edit_comment:
                                st.error("이 댓글을 수정할 권한이 없습니다.")
                            elif not new_content.strip():
                                st.warning("내용을 입력해주세요.")
                            else:
                                if update_comment_content(c["id"], new_content):
                                    st.success("댓글이 수정되었습니다.")
                                    st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                                    st.rerun()
                                else:
                                    st.error("댓글 수정 중 오류가 발생했습니다.")
                    with btn_cols[1]:
                        if st.button("수정 취소", key=f"comment_cancel_{c['id']}", use_container_width=True):
                            st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                            st.rerun()
                    with btn_cols[2]:
                        if can_edit_comment and st.button("🗑 삭제", key=f"comment_del_{c['id']}", use_container_width=True):
                            if delete_comment(c["id"]):
                                st.success("댓글이 삭제되었습니다.")
                                st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                                st.rerun()
                            else:
                                st.error("댓글 삭제 중 오류가 발생했습니다.")
                else:
                    # 댓글 보기 모드
                    st.markdown(c.get("content", ""))
                    btn_cols = st.columns(3)
                    with btn_cols[0]:
                        if can_edit_comment and st.button("✏️ 댓글 수정", key=f"comment_edit_btn_{c['id']}", use_container_width=True):
                            st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = c["id"]
                            st.rerun()
                    with btn_cols[1]:
                        if can_edit_comment and st.button("🗑 댓글 삭제", key=f"comment_delete_btn_{c['id']}", use_container_width=True):
                            if delete_comment(c["id"]):
                                st.success("댓글이 삭제되었습니다.")
                                st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                                st.rerun()
                            else:
                                st.error("댓글 삭제 중 오류가 발생했습니다.")

    st.markdown("---")

    # 새 댓글 작성
    new_comment = st.text_area(
        "새 댓글",
        height=120,
        # key 제거
    )
    if st.button("💬 댓글 등록", key="board_new_comment_submit", use_container_width=True):
        if not username:
            st.error("로그인 정보가 없습니다. 다시 로그인해주세요.")
        elif not new_comment.strip():
            st.warning("내용을 입력해주세요.")
        else:
            if add_comment(selected_post["id"], tenant_id, username, office_name, new_comment):
                st.success("댓글이 등록되었습니다.")
                # 🔻 세션 값 직접 초기화하는 코드 제거
                # st.session_state["board_new_comment"] = ""
                st.session_state[SESS_BOARD_COMMENT_EDIT_ID] = None
                st.rerun()
            else:
                st.error("댓글 저장 중 오류가 발생했습니다.")
