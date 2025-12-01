# pages/page_customer.py

import datetime
import pandas as pd
import streamlit as st
from googleapiclient.errors import HttpError

from config import (
    # 세션 키
    SESS_CURRENT_PAGE,
    SESS_DF_CUSTOMER,
    SESS_CUSTOMER_DATA_EDITOR_KEY,
    SESS_CUSTOMER_SEARCH_TERM,
    SESS_CUSTOMER_SEARCH_MASK_INDICES,
    SESS_CUSTOMER_AWAITING_DELETE_CONFIRM,
    SESS_CUSTOMER_DELETED_ROWS_STACK,
    SESS_TENANT_ID,
    DEFAULT_TENANT_ID,
    # 페이지 키
    PAGE_SCAN,

    # 시트 이름
    CUSTOMER_SHEET_NAME,
)

from core.google_sheets import (
    get_gspread_client,
    get_worksheet,
    get_drive_service,
    append_rows_to_sheet,
)

from core.customer_service import (
    load_customer_df_from_sheet,
    save_customer_batch_update,
    create_customer_folders,
    extract_folder_id,
    is_customer_folder_enabled,
)


def render():
    """
    고객관리 페이지 렌더링 함수.
    app.py에서 current_page_to_display == PAGE_CUSTOMER 일 때 호출.
    """

    # 초기화 플래그 (필요하면 사용)
    if 'customer_initialized' not in st.session_state:
        st.session_state['customer_initialized'] = True

    if SESS_CUSTOMER_DATA_EDITOR_KEY not in st.session_state:
        st.session_state[SESS_CUSTOMER_DATA_EDITOR_KEY] = 0

    st.subheader("👥 고객관리")

    # --- 1) 원본 DataFrame 로드 ---
    # --- 1) 원본 DataFrame 로드 ---
    df_customer_main = st.session_state[SESS_DF_CUSTOMER].copy()
    df_customer_main = df_customer_main.sort_values("고객ID", ascending=False).reset_index(drop=True)

    # --- 1-1) 폴더 ID → URL 변환 (어드민 전용 폴더 기능용) ---
    if "폴더" in df_customer_main.columns:
        from core.customer_service import extract_folder_id
        def _to_folder_url(val: str) -> str:
            fid = extract_folder_id(val)
            return f"https://drive.google.com/drive/folders/{fid}" if fid else ""
        df_customer_main["folder_url"] = df_customer_main["폴더"].apply(_to_folder_url)
    else:
        df_customer_main["folder_url"] = ""

    # --- 2) 컬럼 제한 ---
    cols_to_display = [
        '고객ID', '한글', '성', '명', '연', '락', '처',
        '등록증', '번호', '발급일', 'V', '만기일',
        '여권', '발급', '만기', '주소', '위임내역', '비고', '폴더'
    ]
    if not is_customer_folder_enabled():
        cols_to_display = [c for c in cols_to_display if c != "폴더"]

    cols_to_display = [c for c in cols_to_display if c in df_customer_main.columns]
    df_for_ui = df_customer_main.loc[:, cols_to_display].copy()

    # folder_url 준비
    if is_customer_folder_enabled():
        # folder_url 준비
        if "folder_url" not in df_customer_main.columns:
            df_customer_main["folder_url"] = ""
        df_for_ui = df_for_ui.copy()
        if "폴더" in df_for_ui.columns:
            df_for_ui["폴더"] = df_customer_main["folder_url"]

        # “폴더 생성” 버튼
        if st.button("📂 폴더 일괄 생성/연동", use_container_width=True):
            st.info("폴더 생성 중…")
            client = get_gspread_client()
            worksheet = get_worksheet(client, CUSTOMER_SHEET_NAME)
            create_customer_folders(df_customer_main, worksheet)
            load_customer_df_from_sheet.clear()
            st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet()
            st.success("✅ 폴더 매핑이 최신화 되었습니다.")
    else:
        # 필요하면 안내 문구 정도만
        st.caption("📂 고객별 폴더 기능은 현재 비활성화된 상태입니다.")

    # --- 3) 툴바 ---
    col_add, col_scan, col_search, col_select, col_delete, col_save, col_undo = st.columns([1, 1, 1.5, 1, 1, 1, 1])

    # 3-1) 스캔 페이지로 이동
    with col_scan:
        if st.button("📷 스캔(여권/등록증)", use_container_width=True):
            st.session_state[SESS_CURRENT_PAGE] = PAGE_SCAN
            st.rerun()

    # 3-2) 행 추가
    with col_add:
        if st.button("➕ 행 추가", use_container_width=True):
            today_str = datetime.date.today().strftime('%Y%m%d')
            existing_ids = df_customer_main["고객ID"].astype(str)
            today_ids = existing_ids[existing_ids.str.startswith(today_str)]
            next_seq = str(len(today_ids) + 1).zfill(2)
            new_id = today_str + next_seq

            new_row = {col: " " for col in df_customer_main.columns}
            new_row["고객ID"] = new_id
            df_customer_main = pd.concat(
                [pd.DataFrame([new_row]), df_customer_main],
                ignore_index=True
            )
            st.session_state[SESS_DF_CUSTOMER] = df_customer_main
            st.rerun()

    # 3-3) 검색 입력창
    with col_search:
        st.text_input("🔍 검색", key=SESS_CUSTOMER_SEARCH_TERM)
        search_term = st.session_state.get(SESS_CUSTOMER_SEARCH_TERM, "")

    # 4) 검색 필터링
    df_display_full = df_for_ui.copy()
    df_for_search = df_display_full.fillna(" ").astype(str)

    if search_term:
        mask = df_for_search.apply(
            lambda row: search_term.lower() in row.str.lower().to_string(), axis=1
        )
        df_display_filtered = df_display_full[mask]
        st.session_state[SESS_CUSTOMER_SEARCH_MASK_INDICES] = df_display_full[mask].index.tolist()
    else:
        df_display_filtered = df_display_full
        st.session_state[SESS_CUSTOMER_SEARCH_MASK_INDICES] = df_display_full.index.tolist()

    # 5) 필터링된 DataFrame (원본 인덱스 유지)
    mask_indices = st.session_state.get(SESS_CUSTOMER_SEARCH_MASK_INDICES, [])
    df_display_for_editor = (
        df_customer_main.loc[mask_indices, cols_to_display]
        .reset_index(drop=True)
        .copy()
    )

    if is_customer_folder_enabled():
        df_display_for_editor["폴더"] = (
            df_customer_main.loc[mask_indices, "folder_url"]
            .reset_index(drop=True)
            .fillna("")
        )

    # 9) 삭제 확인
    if st.session_state.get(SESS_CUSTOMER_AWAITING_DELETE_CONFIRM, False):
        st.warning("🔔 정말 삭제하시겠습니까?")
        confirm_cols = st.columns(2)
        with confirm_cols[0]:
            if st.button("✅ 예, 삭제합니다", key="confirm_delete_customer_yes"):
                full_df = st.session_state[SESS_DF_CUSTOMER]
                deleted_stack = st.session_state.setdefault(SESS_CUSTOMER_DELETED_ROWS_STACK, [])

                # 구글시트 & Drive 클라이언트
                gs_client = get_gspread_client()
                worksheet = get_worksheet(gs_client, CUSTOMER_SHEET_NAME)
                drive_svc = get_drive_service()

                # 시트의 고객ID → 행 번호 맵
                rows_all = worksheet.get_all_values()
                if not rows_all:
                    st.error("시트가 비어 있습니다.")
                    st.stop()
                hdr = rows_all[0]
                try:
                    id_col_idx = hdr.index("고객ID")
                except ValueError:
                    st.error("'고객ID' 컬럼을 시트에서 찾을 수 없습니다.")
                    st.stop()

                id_to_sheetrow = {}
                for r_idx, row_vals in enumerate(rows_all[1:], start=2):
                    cid_val = (row_vals[id_col_idx] or "").strip()
                    if cid_val:
                        id_to_sheetrow[cid_val] = r_idx

                # 선택된 ID들 순회
                deleted_count = 0
                for del_id in st.session_state.get("PENDING_DELETE_IDS", []):
                    # 1) DF에서 해당 행 찾기
                    idx_list = full_df.index[full_df["고객ID"].astype(str).str.strip() == str(del_id).strip()].tolist()
                    if not idx_list:
                        continue
                    i = idx_list[0]

                    # 2) 폴더 ID 안전 추출 (폴더 컬럼이 비어있으면 folder_url에서 보조 추출)
                    folder_id = ""
                    if is_customer_folder_enabled():
                        # 폴더 기능이 켜져 있을 때만 Drive 연동 처리
                        folder_raw = full_df.at[i, "폴더"] if "폴더" in full_df.columns else ""
                        if (not str(folder_raw).strip()) and ("folder_url" in full_df.columns):
                            folder_raw = full_df.at[i, "folder_url"]
                        folder_id = extract_folder_id(folder_raw)

                        # 3) Drive 폴더 삭제(권한 이슈 시 휴지통으로 이동 폴백)
                        if folder_id:
                            try:
                                drive_svc.files().delete(fileId=folder_id, supportsAllDrives=True).execute()
                            except HttpError as e:
                                code = getattr(e, "resp", None).status if hasattr(e, "resp") else None
                                if code == 404:
                                    st.info(f"폴더(ID={folder_id})는 이미 삭제되었습니다.")
                                elif code == 403:
                                    try:
                                        drive_svc.files().update(
                                            fileId=folder_id,
                                            body={"trashed": True},
                                            supportsAllDrives=True
                                        ).execute()
                                        st.info(f"폴더(ID={folder_id})를 휴지통으로 이동했습니다.")
                                    except HttpError as e2:
                                        st.warning(f"폴더 삭제/휴지통 이동 실패(ID={folder_id}): {e2}")
                                else:
                                    st.warning(f"폴더 삭제 중 오류(ID={folder_id}): {e}")

                    # 4) 시트 행 삭제(정확한 행 번호)
                    sheet_row = id_to_sheetrow.get(str(del_id).strip())

                    if sheet_row:
                        try:
                            worksheet.delete_rows(sheet_row)
                        except Exception as e:
                            st.warning(f"시트 행 삭제 중 오류(ID={del_id}, row={sheet_row}): {e}")
                        # 맵 재생성 (행 당김 반영)
                        rows_all = worksheet.get_all_values()
                        id_to_sheetrow = {}
                        if rows_all:
                            hdr2 = rows_all[0]
                            if "고객ID" in hdr2:
                                id_col_idx2 = hdr2.index("고객ID")
                                for r_idx2, row_vals2 in enumerate(rows_all[1:], start=2):
                                    cid2 = (row_vals2[id_col_idx2] or "").strip()
                                    if cid2:
                                        id_to_sheetrow[cid2] = r_idx2

                    # 5) 로컬 DF에서도 제거 + Undo 스택에 보관
                    deleted_stack.append((i, full_df.loc[i].copy()))
                    full_df = full_df.drop(index=i)
                    deleted_count += 1

                # 6) 인덱스 재정렬 및 세션 반영
                full_df = full_df.sort_values("고객ID", ascending=False).reset_index(drop=True)
                st.session_state[SESS_DF_CUSTOMER] = full_df

                st.success(f"✅ {deleted_count}개의 행이 삭제되었습니다.")
                st.session_state[SESS_CUSTOMER_AWAITING_DELETE_CONFIRM] = False
                st.session_state.pop("PENDING_DELETE_IDS", None)
                st.rerun()

        with confirm_cols[1]:
            if st.button("❌ 아니오, 취소합니다", key="cancel_delete_customer_no"):
                st.session_state[SESS_CUSTOMER_AWAITING_DELETE_CONFIRM] = False
                st.session_state.pop("PENDING_DELETE_IDS", None)
                st.info("삭제가 취소되었습니다.")
                st.rerun()

    # 10) 데이터 에디터
    editor_key = st.session_state.get(SESS_CUSTOMER_DATA_EDITOR_KEY, 0)
    edited_df_display = st.data_editor(
        df_display_for_editor.fillna(" "),
        height=600,
        use_container_width=True,
        num_rows="dynamic",
        key=f"data_editor_customer_{editor_key}",
        disabled=["고객ID"],
        column_config={
            "폴더": st.column_config.LinkColumn(
                "폴더",
                help="클릭하면 구글 드라이브 폴더가 새 탭에서 열립니다."
            )
        }
    )

    # 11) 삭제할 고객ID 선택
    with col_select:
        options = df_display_for_editor["고객ID"].tolist()
        selected_delete_ids = st.multiselect(
            "삭제할 고객ID 선택",
            options=options,
            key="customer_delete_ids",
            disabled=not options
        )

    # 12) 삭제 요청 버튼
    with col_delete:
        if st.button("🗑️ 삭제 요청", use_container_width=True, disabled=not selected_delete_ids):
            st.session_state["PENDING_DELETE_IDS"] = selected_delete_ids
            st.session_state[SESS_CUSTOMER_AWAITING_DELETE_CONFIRM] = True
            st.rerun()

    # 13) 삭제 취소 버튼
    with col_undo:
        if st.button("↩️ 삭제 취소 (Undo)", use_container_width=True):
            if SESS_CUSTOMER_DELETED_ROWS_STACK in st.session_state and st.session_state[SESS_CUSTOMER_DELETED_ROWS_STACK]:
                original_idx, row_data_series = st.session_state[SESS_CUSTOMER_DELETED_ROWS_STACK].pop()
                current_df = st.session_state[SESS_DF_CUSTOMER]

                part1 = current_df.iloc[:original_idx]
                row_to_insert_df = pd.DataFrame([row_data_series])
                row_to_insert_df = row_to_insert_df.reindex(columns=current_df.columns, fill_value=" ")
                part2 = current_df.iloc[original_idx:]
                restored_df = pd.concat([part1, row_to_insert_df, part2]).reset_index(drop=True)

                st.session_state[SESS_DF_CUSTOMER] = restored_df
                st.success(f"{original_idx}번 행 (원본 기준)이 복구되었습니다. 저장하려면 💾 저장 버튼을 눌러주세요.")
                st.rerun()

    # 14) 저장
    with col_save:
        if st.button("💾 저장", use_container_width=True):
            st.info("⏳ 저장 중입니다... 잠시만 기다려 주세요.")
            client = get_gspread_client()
            worksheet = get_worksheet(client, CUSTOMER_SHEET_NAME)

            tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)

            # 1) 시트에 없던 신규 행만 append
            original = load_customer_df_from_sheet(tenant_id)
            orig_ids = set(original["고객ID"].astype(str))
            new_rows = []
            for _, row in edited_df_display.iterrows():
                cid = str(row["고객ID"]).strip()
                if cid not in orig_ids:
                    new_rows.append({h: row.get(h, "") for h in original.columns})

            if len(new_rows) > 0 and len(new_rows) <= 1000 and set(new_rows[0].keys()) == set(original.columns):
                st.success(f"✅ 신규 {len(new_rows)}건이 추가되었습니다.")

                # 공통: DF는 새로 다시 읽어와서 세션에 반영
                load_customer_df_from_sheet.clear()
                fresh_df = load_customer_df_from_sheet(tenant_id)
                st.session_state[SESS_DF_CUSTOMER] = fresh_df

                # 👉 폴더 기능이 켜져 있을 때만 실제 폴더 생성 + 메시지 출력
                if is_customer_folder_enabled():
                    st.info("📂 신규 고객 폴더 생성 중…")
                    create_customer_folders(fresh_df, worksheet)
                    st.success("✅ 신규 고객 폴더가 생성/연동되었습니다.")

            # 3) 기존 행 변경사항 batch update
            ok = save_customer_batch_update(edited_df_display, worksheet)
            if ok:
                st.success("🔄 업데이트가 반영되었습니다.")

            # 4) 최종 리프레시
            load_customer_df_from_sheet.clear()
            st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet(tenant_id)
            st.session_state[SESS_CUSTOMER_DATA_EDITOR_KEY] += 1
            st.rerun()
