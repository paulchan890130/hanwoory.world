# pages/page_document.py

import os
import io
import datetime

import streamlit as st
import pandas as pd
import fitz  # PyMuPDF

from config import (
    SESS_DF_CUSTOMER,
)

from core.customer_service import (
    load_customer_df_from_sheet,
)

from utils.document import (
    create_seal,
    circle_path,
    font_path,
    seal_size,
)


def render():
    """
    📝 문서작성 자동화 페이지 렌더링 함수.
    app.py에서 current_page_to_display == PAGE_DOCUMENT 일 때 호출.
    기존 코드의 UI/동작을 그대로 옮겨온 버전.
    """
    st.subheader("📝 문서작성 자동화")

    # ─────────────────────
    # 1) 고객 데이터 로드
    # ─────────────────────
    if SESS_DF_CUSTOMER not in st.session_state:
        st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet()
    df_cust: pd.DataFrame = st.session_state[SESS_DF_CUSTOMER]

    # 문서 생성 상태 플래그
    if "document_generated" not in st.session_state:
        st.session_state["document_generated"] = False

    # ─────────────────────
    # 2) PDF 템플릿 목록 정의
    # ─────────────────────
    pdf_templates = {
        f"{업무}_{내용}": f"templates/{업무}_{내용}.pdf"
        for 업무 in ["H2", "F4", "F1", "F3", "F2", "F5", "국적"]
        for 내용 in [
            "등록", "연장", "연장 전자",
            "자격변경", "자격변경 전자",
            "자격부여", "체류지 변경", "등록사항 변경",
        ]
    }

    # ─────────────────────
    # 3) 상단 6컬럼 레이아웃
    # ─────────────────────
    cols = st.columns(6)

    # 3-1) 업무 / 내용 선택
    with cols[0]:
        업무 = st.selectbox(
            "업무",
            sorted({k.split('_')[0] for k in pdf_templates.keys()}),
            key="doc_task",
        )

    with cols[1]:
        내용 = st.selectbox(
            "내용",
            sorted({k.split('_')[1] for k in pdf_templates.keys()}),
            key="doc_action",
        )

    # 3-2) 숙소 제공자 검색·선택
    with cols[2]:
        숙소키워드 = st.text_input("숙소제공자 검색", key="doc_accommodation_search")

    matched_provs = (
        df_cust[df_cust["한글"].str.contains(숙소키워드.strip(), na=False)]
        if 숙소키워드.strip()
        else pd.DataFrame()
    )

    if not matched_provs.empty:
        st.markdown("👀 **숙소제공자 검색 결과:**")
        for idx2, prov_row in matched_provs.iterrows():
            label2 = (
                f"{prov_row['한글']} / {prov_row['등록증']} / "
                f"{prov_row['연']}-{prov_row['락']}-{prov_row['처']}"
            )
            if st.button(label2, key=f"accom_{idx2}"):
                # 신청인·대리인 초기화
                st.session_state.pop("selected_customer_idx", None)
                st.session_state.pop("selected_guardian_idx", None)
                st.session_state["selected_accommodation_idx"] = idx2
                st.session_state["document_generated"] = False
                st.rerun()

    prov = None
    if "selected_accommodation_idx" in st.session_state:
        prov = df_cust.loc[st.session_state["selected_accommodation_idx"]]
        st.markdown(f"✅ 선택된 숙소제공자: **{prov['한글']}**")

    # 3-3) 신원보증인 (F1, F2, F3, F5일 때만)
    보증인 = None
    if 업무 in ["F1", "F2", "F3", "F5"]:
        with cols[3]:
            guarantor_kw = st.text_input("신원보증인 검색", key="doc_guarantor_search")

        matched_guars = (
            df_cust[df_cust["한글"].str.contains(guarantor_kw.strip(), na=False)]
            if guarantor_kw.strip()
            else pd.DataFrame()
        )

        if not matched_guars.empty:
            st.markdown("🔒 **신원보증인 검색 결과:**")
            for _, grow in matched_guars.iterrows():
                cust_id = grow["고객ID"]
                lbl = (
                    f"{grow['한글']} / {grow['등록증']} / "
                    f"{grow['연']}-{grow['락']}-{grow['처']}"
                )
                if st.button(lbl, key=f"guarantor_{cust_id}"):
                    st.session_state["selected_guarantor_idx"] = grow.name
                    st.session_state["document_generated"] = False
                    st.rerun()

        if "selected_guarantor_idx" in st.session_state:
            보증인 = df_cust.loc[st.session_state["selected_guarantor_idx"]]
            st.markdown(f"✅ 선택된 신원보증인: **{보증인['한글']}**")

    # 3-4) 신청인 검색·선택
    with cols[4]:
        신청인_검색어 = st.text_input("신청인 이름 (고객 검색)", key="doc_search")

    matched = (
        df_cust[df_cust["한글"].str.contains(신청인_검색어.strip(), na=False)]
        if 신청인_검색어.strip()
        else pd.DataFrame()
    )

    if not matched.empty:
        st.markdown("🔎 **신청인 검색 결과:**")
        for idx, row_tmp in matched.iterrows():
            label = (
                f"{row_tmp['한글']} / {row_tmp['등록증']} / "
                f"{row_tmp['연']}-{row_tmp['락']}-{row_tmp['처']}"
            )
            if st.button(label, key=f"select_{idx}"):
                st.session_state["selected_customer_idx"] = idx
                st.session_state["document_generated"] = False
                st.rerun()

    선택된_고객, row = None, None
    if "selected_customer_idx" in st.session_state:
        row = df_cust.loc[st.session_state["selected_customer_idx"]]
        선택된_고객 = row["한글"]

    # 3-5) 미성년자 여부 + 대리인 선택
    is_minor = False
    guardian = None
    if row is not None:
        reg = str(row.get("등록증", "")).replace("-", "")
        if len(reg) >= 6 and reg[:6].isdigit():
            yy_int = int(reg[:2])
            current_short = datetime.date.today().year % 100
            century = 2000 if yy_int <= current_short else 1900
            try:
                birth = datetime.date(century + yy_int, int(reg[2:4]), int(reg[4:6]))
                age = (datetime.date.today() - birth).days // 365
                is_minor = age < 18
            except ValueError:
                is_minor = False

    if is_minor:
        with cols[5]:
            대리인_검색 = st.text_input("대리인 이름 (고객 검색)", key="doc_guardian_search")

        후보 = (
            df_cust[df_cust["한글"].str.contains(대리인_검색.strip(), na=False)]
            if 대리인_검색.strip()
            else pd.DataFrame()
        )

        if not 후보.empty:
            st.markdown("👤 **대리인 검색 결과:**")
            for _, row2 in 후보.iterrows():
                cust_id = row2["고객ID"]
                label3 = (
                    f"{row2['한글']} / {row2['등록증']} / "
                    f"{row2['연']}-{row2['락']}-{row2['처']}"
                )
                if st.button(label3, key=f"guardian_{cust_id}"):
                    st.session_state["selected_guardian_idx"] = row2.name
                    st.session_state["document_generated"] = False
                    st.rerun()

        if "selected_guardian_idx" in st.session_state:
            guardian = df_cust.loc[st.session_state["selected_guardian_idx"]]

    st.markdown("---")

    # ─────────────────────
    # 4) 문서 생성 로직
    # ─────────────────────
    if 선택된_고객 and 업무 and 내용 and not st.session_state["document_generated"]:
        key = f"{업무}_{내용}"
        template_path = pdf_templates.get(key)

        if not template_path or not os.path.exists(template_path):
            st.error(f"❗️ 템플릿이 없습니다: templates/{key}.pdf")
            return

        # F1, F3, F5는 보증인 필수
        if 업무 in ["F1", "F3", "F5"] and 보증인 is None:
            st.error("❗️ 신원보증인을 선택해야 문서를 생성할 수 있습니다.")
            return

        if is_minor and guardian is None:
            st.error("❗️ 미성년자는 대리인을 선택해야 문서를 생성할 수 있습니다.")
            return

        # ── 신청인 기본 인적사항 (생년월일/성별) ──
        reg = str(row.get("등록증", "")).replace("-", "")
        birth_raw = reg[:6]
        if len(birth_raw) == 6 and birth_raw.isdigit():
            yy = int(birth_raw[:2])
            current_short = datetime.date.today().year % 100
            century = 2000 if yy <= current_short else 1900
            yyyy = str(century + yy)
            mm = birth_raw[2:4]
            dd = birth_raw[4:6]
        else:
            yyyy, mm, dd = "", "", ""

        num = str(row.get("번호", "")).replace("-", "").strip()
        gdigit = num[0] if len(num) >= 1 else ""
        gender = "남" if gdigit in ["5", "7"] else "여" if gdigit in ["6", "8"] else ""
        man = "V" if gdigit in ["5", "7"] else ""
        girl = "V" if gdigit in ["6", "8"] else ""

        # ── PDF 필드값 기본 세팅 ──
        field_values = {
            "Surname":     row.get("성", ""),
            "Given names": row.get("명", ""),
            "yyyy":        yyyy,
            "mm":          mm,
            "dd":          dd,
            "gender":      gender,
            "man":         man,
            "girl":        girl,
            "fnumber":     row.get("등록증", ""),
            "rnumber":     row.get("번호", ""),
            "passport":    row.get("여권", ""),
            "issue":       row.get("발급", ""),
            "expiry":      row.get("만기", ""),
            "nation":      "중국",
            "adress":      row.get("주소", ""),
            "phone1":      row.get("연", ""),
            "phone2":      row.get("락", ""),
            "phone3":      row.get("처", ""),
            "koreanname":  row.get("한글", ""),
            "bankaccount": row.get("환불계좌", ""),
            "why":         row.get("신청이유", ""),
            "hope":        row.get("희망자격", ""),
            "partner":     row.get("배우자", ""),
            "parents":     guardian.get("한글", "") if is_minor and guardian is not None else row.get("부모", ""),
            # 기타 체크박스/항목 초기화
            "registration": "",
            "card": "",
            "extension": "",
            "change": "",
            "granting": "",
            "adresscheck": "",
            "partner yin": "",
            "parents yin": "",
            "changeregist": "",
        }

        # 등록증/번호 자리별
        for i, digit in enumerate(str(row.get("등록증", "")).strip(), 1):
            field_values[f"fnumber{i}"] = digit
        for i, digit in enumerate(str(row.get("번호", "")).strip(), 1):
            field_values[f"rnumber{i}"] = digit

        # ── 숙소 제공자 필드 + 도장 ──
        if prov is not None:
            field_values.update({
                "hsurname":      prov.get("성", ""),
                "hgiven names":  prov.get("명", ""),
                "hfnumber":      prov.get("등록증", ""),
                "hrnumber":      prov.get("번호", ""),
                "hphone1":       prov.get("연", ""),
                "hphone2":       prov.get("락", ""),
                "hphone3":       prov.get("처", ""),
                "hkoreanname":   prov.get("한글", ""),
            })
            prov_seal = create_seal(circle_path, prov["한글"], font_path, seal_size)
            buf_prov = io.BytesIO()
            prov_seal.save(buf_prov, format="PNG")
            prov_img_bytes = buf_prov.getvalue()
        else:
            prov_img_bytes = None

        # ── 신청인/대리인 도장 ──
        seal_name = guardian["한글"] if is_minor and guardian is not None else 선택된_고객
        seal_img = create_seal(circle_path, seal_name, font_path, seal_size)
        buf = io.BytesIO()
        seal_img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        # ── 신원보증인 도장 + 필드 ──
        if 보증인 is not None:
            g_seal = create_seal(circle_path, 보증인["한글"], font_path, seal_size)
            buf_g = io.BytesIO()
            g_seal.save(buf_g, format="PNG")
            byin_bytes = buf_g.getvalue()

            g_reg = str(보증인["등록증"]).replace("-", "")
            gbirth = g_reg[:6]
            byyyy = "19" + gbirth[:2] if int(gbirth[:2]) > 41 else "20" + gbirth[:2]
            bmm, bdd = gbirth[2:4], gbirth[4:6]

            reg_no = str(보증인["번호"]).replace("-", "").strip()
            gdigit2 = reg_no[0] if len(reg_no) >= 1 else ""
            if gdigit2 in ["5", "7"]:
                bgender = "남"
                bman = "V"
                bgirl = ""
            elif gdigit2 in ["6", "8"]:
                bgender = "여"
                bman = ""
                bgirl = "V"
            else:
                bgender = ""
                bman = ""
                bgirl = ""

            field_values.update({
                "bsurname":     보증인.get("성", ""),
                "bgiven names": 보증인.get("명", ""),
                "byyyy":        byyyy,
                "bmm":          bmm,
                "bdd":          bdd,
                "bgender":      bgender,
                "bman":         bman,
                "bgirl":        bgirl,
                "bfnumber":     보증인.get("등록증", ""),
                "brnumber":     보증인.get("번호", ""),
                "badress":      보증인.get("주소", ""),
                "bphone1":      보증인.get("연", ""),
                "bphone2":      보증인.get("락", ""),
                "bphone3":      보증인.get("처", ""),
                "bkoreanname":  보증인.get("한글", ""),
            })

            for i, d in enumerate(g_reg, start=1):
                field_values[f"bfnumber{i}"] = d
        else:
            byin_bytes = None

        # ─────────────────────
        # 5) PDF에 필드/이미지 삽입 (PyMuPDF)
        # ─────────────────────
        doc = fitz.open(template_path)
        for page in doc:
            # 텍스트 필드 채우기
            for widget in page.widgets():
                base = widget.field_name.split('#')[0]
                if base in field_values:
                    widget.field_value = field_values[base]
                    widget.update()

            # 도장 이미지 삽입
            for widget in page.widgets():
                base = widget.field_name.split('#')[0]
                if base == "yin":
                    page.insert_image(widget.rect, stream=img_bytes)
                if base == "hyin" and prov_img_bytes is not None:
                    page.insert_image(widget.rect, stream=prov_img_bytes)
                if base == "byin" and byin_bytes is not None:
                    page.insert_image(widget.rect, stream=byin_bytes)

        out = io.BytesIO()
        doc.save(out)
        doc.close()
        out.seek(0)

        # ─────────────────────
        # 6) 다운로드 버튼
        # ─────────────────────
        if st.download_button(
            "📅 자동작성된 PDF 다운로드",
            data=out.read(),
            file_name=f"{선택된_고객}_{업무}_{내용}.pdf",
            mime="application/pdf",
        ):
            st.session_state["document_generated"] = True
            st.rerun()

    # ─────────────────────
    # 6) 완료 후 초기화 버튼
    # ─────────────────────
    if st.session_state.get("document_generated", False):
        st.success("✅ 문서가 성공적으로 생성되었습니다.")
        if st.button("🔄 다른 고객으로 다시 작성"):
            for k in [
                "selected_customer_idx",
                "selected_guardian_idx",
                "selected_accommodation_idx",
                "selected_guarantor_idx",
            ]:
                st.session_state.pop(k, None)
            st.session_state["document_generated"] = False
            st.rerun()
