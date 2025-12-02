import os
import io
import datetime

import streamlit as st
import pandas as pd
import fitz  # PyMuPDF

from config import (
    SESS_DF_CUSTOMER,
    SESS_TENANT_ID,
    DEFAULT_TENANT_ID,
    ACCOUNTS_SHEET_NAME,
)

from core.customer_service import (
    load_customer_df_from_sheet,
)

from core.google_sheets import (
    read_data_from_sheet,
)

from utils.document import (
    create_seal,
    circle_path,
    font_path,
    seal_size,
)

@st.cache_data(ttl=600)
def get_account_for_tenant(tenant_id: str):
    """
    현재 로그인한 tenant_id 에 해당하는 accounts 시트 행(딕셔너리)을 리턴.
    못 찾으면 None.
    """
    records = read_data_from_sheet(ACCOUNTS_SHEET_NAME, default_if_empty=[]) or []
    norm = (tenant_id or "").strip()

    for r in records:
        # accounts 시트의 tenant_id 와 비교
        if str(r.get("tenant_id", "")).strip() == norm:
            return r

    return None

# ─────────────────────────────────────────────
# 선택 트리 정의 (구분/민원/종류/세부)
# ─────────────────────────────────────────────

CATEGORY_OPTIONS = ["체류", "사증"]

MINWON_OPTIONS = {
    "체류": ["등록", "연장", "변경", "부여", "신고", "기타"],
    "사증": ["준비중"],
}

TYPE_OPTIONS = {
    ("체류", "등록"): ["F", "H2", "E7"],
    ("체류", "연장"): ["F", "H2", "E7"],
    ("체류", "변경"): ["F", "H2", "E7", "국적", "D"],
    ("체류", "부여"): ["F"],
    ("체류", "신고"): ["주소", "등록사항"],
    ("체류", "기타"): ["D"],
    ("사증", "준비중"): ["x"],
}

SUBTYPE_OPTIONS = {
    ("체류", "등록", "F"): ["1", "2", "3", "4", "5", "6"],
    ("체류", "등록", "H2"): [],
    ("체류", "등록", "E7"): [],
    ("체류", "연장", "F"): ["1", "2", "3", "4", "5", "6"],
    ("체류", "연장", "H2"): [],
    ("체류", "연장", "E7"): [],
    ("체류", "변경", "F"): ["1", "2", "3", "4", "5", "6"],
    ("체류", "변경", "H2"): [],
    ("체류", "변경", "E7"): [],
    ("체류", "변경", "국적"): ["일반", "간이", "특별"],
    ("체류", "변경", "D"): ["2", "4", "8", "10"],
    ("체류", "부여", "F"): ["2", "3", "5"],
    ("체류", "신고", "주소"): [],
    ("체류", "신고", "등록사항"): [],
    ("체류", "기타", "D"): ["2", "4", "8", "10"],
    ("사증", "준비중", "x"): [],
}

# ─────────────────────────────────────────────
# (구분,민원,종류,세부) → 필요서류 목록 매핑
# main: 민원 서류, agent: 행정사 서류(위임장 등)
# ─────────────────────────────────────────────

REQUIRED_DOCS = {
    # 체류-등록
    ("체류","등록","F","1"): {
        "main": ["통합신청서", "비취업 서약서", "신원보증서", "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","등록","F","2"): {
        "main": ["통합신청서", "직업신고서", "신원보증서", "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","등록","F","3"): {
        "main": ["통합신청서", "비취업 서약서", "신원보증서", "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","등록","F","4"): {
        "main": ["통합신청서", "직업신고서", "단순노무 비취업 서약서", "한글성명 병기 신청서",
                 "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    # F5는 등록 불가라서 제외
    ("체류","등록","F","6"): {
        "main": ["통합신청서", "직업신고서", "신원보증서", "거주숙소 제공 확인서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","등록","H2",""): {
        "main": ["통합신청서", "직업신고서", "한글성명 병기 신청서",
                 "거주숙소 제공 확인서", "치료예정 서약서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","등록","E7",""): {
        "main": ["준비중"],
        "agent": ["위임장", "대행업무수행확인서"],
    },

    # 체류-연장
    ("체류","연장","F","1"): {
        "main": ["통합신청서", "비취업 서약서", "신원보증서", "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","연장","F","2"): {
        "main": ["통합신청서", "직업신고서", "신원보증서", "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","연장","F","3"): {
        "main": ["통합신청서", "비취업 서약서", "신원보증서", "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","연장","F","4"): {
        "main": ["통합신청서", "직업신고서", "한글성명 병기 신청서",
                 "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","연장","F","6"): {
        "main": ["통합신청서", "직업신고서", "신원보증서", "거주숙소 제공 확인서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","연장","H2",""): {
        "main": ["통합신청서", "직업신고서", "한글성명 병기 신청서",
                 "거주숙소 제공 확인서", "치료예정 서약서", "법령준수 확인서", "비취업 확인서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","연장","E7",""): {
        "main": ["준비중"],
        "agent": ["위임장", "대행업무수행확인서"],
    },

    # 체류-변경
    ("체류","변경","F","1"): {
        "main": ["통합신청서", "비취업 서약서", "신원보증서", "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","F","2"): {
        "main": ["통합신청서", "직업신고서", "결혼배경진술서", "초청장",
                 "직업 및 연간 소득금액 신고서", "신원보증서",
                 "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","F","3"): {
        "main": ["통합신청서", "비취업 서약서", "신원보증서",
                 "거주숙소 제공 확인서", "재학신고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","F","4"): {
        "main": ["통합신청서", "직업신고서", "단순노무 비취업 서약서",
                 "한글성명 병기 신청서", "거주숙소 제공 확인서",
                 "재학신고서", "법령준수 확인서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","F","5"): {
        "main": ["통합신청서", "직업신고서", "한글성명 병기 신청서",
                 "신원보증서", "거주숙소 제공 확인서", "재학신고서",
                 "신청자 기본정보", "심사보고서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","F","6"): {
        "main": ["준비중"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","H2",""): {
        "main": ["통합신청서", "직업신고서", "한글성명 병기 신청서",
                 "거주숙소 제공 확인서", "치료예정 서약서"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","E7",""): {
        "main": ["준비중"],
        "agent": ["위임장", "대행업무수행확인서"],
    },
    ("체류","변경","국적","일반"): {"main": ["준비중"], "agent": []},
    ("체류","변경","국적","간이"): {"main": ["준비중"], "agent": []},
    ("체류","변경","국적","특별"): {"main": ["준비중"], "agent": []},
    ("체류","변경","D","2"): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},
    ("체류","변경","D","4"): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},
    ("체류","변경","D","8"): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},
    ("체류","변경","D","10"): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},

    # 체류-부여
    ("체류","부여","F","2"): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},
    ("체류","부여","F","3"): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},
    ("체류","부여","F","5"): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},

    # 체류-신고
    ("체류","신고","주소",""): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},
    ("체류","신고","등록사항",""): {"main": ["준비중"], "agent": ["위임장", "대행업무수행확인서"]},

    # 사증
    ("사증","준비중","",""): {"main": ["준비중"], "agent": []},
}

# ─────────────────────────────────────────────
# 문서명 → 템플릿 파일 경로 정의
# (실제 파일명은 폴찬이 가지고 있는 PDF 기준으로 맞추면 됨)
# ─────────────────────────────────────────────

DOC_TEMPLATES = {
    "통합신청서": "templates/통합신청서.pdf",
    "비취업 서약서": "templates/비취업서약서.pdf",
    "신원보증서": "templates/신원보증서.pdf",
    "거주숙소 제공 확인서": "templates/거주숙소제공확인서.pdf",
    "재학신고서": "templates/재학신고서.pdf",
    "직업신고서": "templates/직업신고서.pdf",
    "한글성명 병기 신청서": "templates/한글성명병기신청서.pdf",
    "단순노무 비취업 서약서": "templates/단순노무비취업서약서.pdf",
    "치료예정 서약서": "templates/치료예정서약서.pdf",
    "법령준수 확인서": "templates/법령준수확인서.pdf",
    "비취업 확인서": "templates/비취업확인서.pdf",
    "결혼배경진술서": "templates/결혼배경진술서.pdf",
    "초청장": "templates/초청장.pdf",
    "직업 및 연간 소득금액 신고서": "templates/직업및연간소득금액신고서.pdf",
    "신청자 기본정보": "templates/신청자기본정보.pdf",
    "심사보고서": "templates/심사보고서.pdf",
    "위임장": "templates/위임장.pdf",
    "대행업무수행확인서": "templates/대행업무수행확인서.pdf",
    "준비중": "templates/준비중.pdf",  # 나중에 실제 템플릿으로 교체
}

# ─────────────────────────────────────────────
# 역할별 도장 필드 이름
# (PDF에서 필드명을 이 규칙에 맞추면 됨)
# ─────────────────────────────────────────────

ROLE_WIDGETS = {
    "applicant": "yin",       # 신청인/미성년자 대리인
    "accommodation": "hyin",  # 숙소제공자
    "guarantor": "byin",      # 신원보증인
    "guardian": "gyin",       # 법정대리인(필요시 별도 필드)
    "aggregator": "pyin",     # 합산자
    "agent": "ayin",          # 행정사(향후 확장용)
}

def normalize_field_name(name: str) -> str:
    """
    PDF 위젯 이름에서 '#...', ' [숫자]' 같은 꼬리표를 제거해서
    우리가 쓰는 기본 이름만 남긴다.
    예) 'V [518]' -> 'V', 'agent_biz_no [529]' -> 'agent_biz_no'
    """
    if not name:
        return ""
    base = name.split("#")[0]        # 'foo#1' 같은 경우
    if " [" in base:                 # 'foo [123]' 꼬리 제거
        base = base.split(" [", 1)[0]
    return base.strip()

def normalize_step(v: str) -> str:
    v = (v or "").strip()
    return "" if v.lower() == "x" else v

def need_guarantor(category, minwon, kind, detail):
    if category != "체류" or kind != "F":
        return False
    if minwon in ("등록", "연장"):
        return detail in ("1", "2", "3", "6")
    if minwon == "변경":
        return detail in ("1", "2", "3", "5", "6")
    if minwon == "부여":
        return detail in ("2", "3", "5")
    return False

def need_aggregator(category, minwon, kind, detail):
    return (category, minwon, kind, detail) == ("체류", "변경", "F", "5")

def calc_is_minor(reg_no: str) -> bool:
    reg = str(reg_no or "").replace("-", "")
    if len(reg) < 6 or not reg[:6].isdigit():
        return False
    yy = int(reg[:2])
    current_short = datetime.date.today().year % 100
    century = 2000 if yy <= current_short else 1900
    try:
        birth = datetime.date(century + yy, int(reg[2:4]), int(reg[4:6]))
    except ValueError:
        return False
    age = (datetime.date.today() - birth).days // 365
    return age < 18

def build_field_values(
    row,
    prov=None,
    guardian=None,
    guarantor=None,
    aggregator=None,
    is_minor=False,
    account=None,
    category=None,
    minwon=None,
):
    """
    PDF 텍스트 필드에 들어갈 값을 모두 Dict로 만들어서 리턴.
    - row        : 신청인 (고객 데이터 한 줄)
    - prov       : 숙소제공자
    - guardian   : (필요시) 대리인/법정대리인
    - guarantor  : (필요시) 신원보증인
    - aggregator : (필요시) 합산자
    - is_minor   : 신청인 미성년 여부
    - account    : Accounts 시트에서 읽어온 행정사 계정 정보
    """
    field_values = {}

    # ========= 1) 신청인 기본정보 =========
    reg = str(row.get("등록증", "")).replace("-", "")
    birth_raw = reg[:6]
    yyyy = mm = dd = ""
    if len(birth_raw) == 6 and birth_raw.isdigit():
        yy = int(birth_raw[:2])
        current_short = datetime.date.today().year % 100
        century = 2000 if yy <= current_short else 1900
        yyyy = str(century + yy)
        mm = birth_raw[2:4]
        dd = birth_raw[4:6]

    num = str(row.get("번호", "")).replace("-", "").strip()
    gdigit = num[0] if len(num) >= 1 else ""
    if gdigit in ["5", "7"]:
        gender = "남"
        man = "V"
        girl = ""
    elif gdigit in ["6", "8"]:
        gender = "여"
        man = ""
        girl = "V"
    else:
        gender = ""
        man = ""
        girl = ""

    field_values.update(
        {
            "Surname":     row.get("성", ""),
            "Given names": row.get("명", ""),
            "yyyy":        yyyy,
            "mm":          mm,
            "dd":          dd,
            "gender":      gender,
            "man":         man,
            "girl":        girl,
            "V" :          row.get("V", ""),
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
            # 미성년자면 부모 이름 대신 대리인 이름을 parents 필드에 넣는다
            "parents":     guardian.get("한글", "") if is_minor and guardian is not None else row.get("부모", ""),
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
    )

    # 등록증/번호 한 칸씩
    for i, digit in enumerate(str(row.get("등록증", "")).strip(), 1):
        field_values[f"fnumber{i}"] = digit
    for i, digit in enumerate(str(row.get("번호", "")).strip(), 1):
        field_values[f"rnumber{i}"] = digit

    # ========= 2) 숙소제공자(prov) =========
    if prov is not None:
        field_values.update(
            {
                "hsurname":      prov.get("성", ""),
                "hgiven names":  prov.get("명", ""),
                "hfnumber":      prov.get("등록증", ""),
                "hrnumber":      prov.get("번호", ""),
                "hphone1":       prov.get("연", ""),
                "hphone2":       prov.get("락", ""),
                "hphone3":       prov.get("처", ""),
                "hkoreanname":   prov.get("한글", ""),
                "hadress":       prov.get("주소", ""),
            }
        )

    # ========= 3) 신원보증인(guarantor) =========
    if guarantor is not None:
        g = guarantor
        g_reg = str(g.get("등록증", "")).replace("-", "")
        gbirth_raw = g_reg[:6]
        byyyy = bmm = bdd = ""
        if len(gbirth_raw) == 6 and gbirth_raw.isdigit():
            gyy = int(gbirth_raw[:2])
            current_short = datetime.date.today().year % 100
            gcentury = 2000 if gyy <= current_short else 1900
            byyyy = str(gcentury + gyy)
            bmm = gbirth_raw[2:4]
            bdd = gbirth_raw[4:6]

        gnum = str(g.get("번호", "")).replace("-", "").strip()
        ggdigit = gnum[0] if len(gnum) >= 1 else ""
        if ggdigit in ["5", "7"]:
            bgender = "남"
            bman = "V"
            bgirl = ""
        elif ggdigit in ["6", "8"]:
            bgender = "여"
            bman = ""
            bgirl = "V"
        else:
            bgender = ""
            bman = ""
            bgirl = ""

        field_values.update(
            {
                "bsurname":      g.get("성", ""),
                "bgiven names":  g.get("명", ""),
                "byyyy":         byyyy,
                "bmm":           bmm,
                "bdd":           bdd,
                "bgender":       bgender,
                "bman":          bman,
                "bgirl":         bgirl,
                "bfnumber":      g.get("등록증", ""),
                "brnumber":      g.get("번호", ""),
                "badress":       g.get("주소", ""),
                "bphone1":       g.get("연", ""),
                "bphone2":       g.get("락", ""),
                "bphone3":       g.get("처", ""),
                "bkoreanname":   g.get("한글", ""),
            }
        )

        for i, digit in enumerate(g_reg, 1):
            field_values[f"bfnumber{i}"] = digit

    # ========= 4) guardian_row 대리인용 필드 =========
    if guardian is not None:
        d = guardian
        d_reg = str(d.get("등록증", "")).replace("-", "")
        dbirth_raw = d_reg[:6]
        dyyyy = dmm = ddd = ""
        if len(dbirth_raw) == 6 and dbirth_raw.isdigit():
            dyy = int(dbirth_raw[:2])
            current_short = datetime.date.today().year % 100
            dcentury = 2000 if dyy <= current_short else 1900
            dyyyy = str(dcentury + dyy)
            dmm = dbirth_raw[2:4]
            ddd = dbirth_raw[4:6]

        dnum = str(d.get("번호", "")).replace("-", "").strip()
        dgdigit = dnum[0] if len(dnum) >= 1 else ""
        if dgdigit in ["5", "7"]:
            dgender = "남"
            dman = "V"
            dgirl = ""
        elif dgdigit in ["6", "8"]:
            dgender = "여"
            dman = ""
            dgirl = "V"
        else:
            dgender = ""
            dman = ""
            dgirl = ""

        field_values.update(
            {
                "gsurname":      d.get("성", ""),
                "ggiven names":  d.get("명", ""),
                "gyyyy":         dyyyy,
                "gmm":           dmm,
                "gdd":           ddd,
                "ggender":       dgender,
                "gman":          dman,
                "ggirl":         dgirl,
                "gfnumber":      d.get("등록증", ""),
                "grnumber":      d.get("번호", ""),
                "gadress":       d.get("주소", ""),
                "gphone1":       d.get("연", ""),
                "gphone2":       d.get("락", ""),
                "gphone3":       d.get("처", ""),
                "gkoreanname":   d.get("한글", ""),
            }
        )

        for i, digit in enumerate(d_reg, 1):
            field_values[f"gfnumber{i}"] = digit

    # ========= 5) aggregator_row 합산자용 필드 =========
    # 구조는 guardian과 동일하지만, 접두사가 'p'
    if aggregator is not None:
        a = aggregator
        a_reg = str(a.get("등록증", "")).replace("-", "")
        abirth_raw = a_reg[:6]
        ayyyy = amm = addd = ""
        if len(abirth_raw) == 6 and abirth_raw.isdigit():
            ayy = int(abirth_raw[:2])
            current_short = datetime.date.today().year % 100
            acentury = 2000 if ayy <= current_short else 1900
            ayyyy = str(acentury + ayy)
            amm = abirth_raw[2:4]
            addd = abirth_raw[4:6]

        anum = str(a.get("번호", "")).replace("-", "").strip()
        agdigit = anum[0] if len(anum) >= 1 else ""
        if agdigit in ["5", "7"]:
            agender = "남"
            aman = "V"
            agirl = ""
        elif agdigit in ["6", "8"]:
            agender = "여"
            aman = ""
            agirl = "V"
        else:
            agender = ""
            aman = ""
            agirl = ""

        field_values.update(
            {
                "psurname":      a.get("성", ""),
                "pgiven names":  a.get("명", ""),
                "pyyyy":         ayyyy,
                "pmm":           amm,
                "pdd":           addd,
                "pgender":       agender,
                "pman":          aman,
                "pgirl":         agirl,
                "pfnumber":      a.get("등록증", ""),
                "prnumber":      a.get("번호", ""),
                "padress":       a.get("주소", ""),
                "pphone1":       a.get("연", ""),
                "pphone2":       a.get("락", ""),
                "pphone3":       a.get("처", ""),
                "pkoreanname":   a.get("한글", ""),
            }
        )

        for i, digit in enumerate(a_reg, 1):
            field_values[f"pfnumber{i}"] = digit


    # ========= 6) 행정사 계정 정보(account) =========
    if account is not None:
        agency_name = str(account.get("office_name", "") or "").strip()
        agent_name  = str(account.get("contact_name", "") or "").strip()
        agent_rrn   = str(account.get("agent_rrn", "") or "").strip()
        biz_no      = str(account.get("biz_reg_no", "") or "").strip()
        agent_tel   = str(account.get("contact_tel", "") or "").strip()
        office_adr   = str(account.get("office_adr", "") or "").strip()
        field_values.update(
            {
                "agency_name":  agency_name,
                "agent_name":   agent_name,
                "agent_rrn":    agent_rrn,
                "agent_biz_no": biz_no,
                "agent_tel":    agent_tel,
                "office_adr":   office_adr,
            }
        )

    # ========= 6) 민원 종류에 따른 체크박스 값 자동 설정 =========
    # 등록 / 연장 / 변경 / 부여 에 따라 해당 필드에 "V" 값 세팅
    # 기본값은 빈 문자열이거나 None; 존재하면 덮어씌움
    if category == "체류":
        if minwon == "등록":
            field_values["registration"] = "V"
        elif minwon == "연장":
            field_values["extension"] = "V"
        elif minwon == "변경":
            field_values["change"] = "V"
        elif minwon == "부여":
            field_values["granting"] = "V"
    # 필요하면 사증 등 다른 category 도 확장 가능

    return field_values

def make_seal_bytes(name: str = None):
    if not name:
        return None
    seal_img = create_seal(circle_path, name, font_path, seal_size)
    buf = io.BytesIO()
    seal_img.save(buf, format="PNG")
    return buf.getvalue()

def fill_and_append_pdf(template_path: str, field_values: dict,
                        seal_bytes_by_role: dict, merged_doc: fitz.Document):
    if not template_path or not os.path.exists(template_path):
        return

    doc = fitz.open(template_path)

    for page in doc:
        # ✅ 제너레이터를 리스트로 먼저 뽑아둔다
        widgets = list(page.widgets() or [])

        # 1) 텍스트 필드 채우기
        for widget in widgets:
            base = normalize_field_name(widget.field_name)
            if base in field_values:
                widget.field_value = str(field_values[base] or "")
                widget.update()

        # 2) 도장 넣기
        for widget in widgets:
            base = normalize_field_name(widget.field_name)
            for role, widget_name in ROLE_WIDGETS.items():
                if base == widget_name:
                    img_bytes = seal_bytes_by_role.get(role)
                    if img_bytes:
                        page.insert_image(widget.rect, stream=img_bytes)

    merged_doc.insert_pdf(doc)
    doc.close()


def render():
    st.subheader("📝 문서작성 자동화")

    tenant_id = st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)
    # 🔹 현재 로그인된 테넌트의 행정사 계정 정보 읽기
    account = get_account_for_tenant(tenant_id)

    if account:
        st.caption(
            f"대행기관: {account.get('office_name', '')} / "
            f"행정사: {account.get('contact_name', '')}"
        )
    else:
        st.warning("Accounts 시트에서 현재 테넌트 정보를 찾지 못했습니다. "
                   "위임장/대행업무수행확인서의 행정사 정보가 비어 있을 수 있습니다.")

    if SESS_DF_CUSTOMER not in st.session_state:
        st.session_state[SESS_DF_CUSTOMER] = load_customer_df_from_sheet(tenant_id)
    df_cust: pd.DataFrame = st.session_state[SESS_DF_CUSTOMER]

    if "document_generated" not in st.session_state:
        st.session_state["document_generated"] = False
    if "selected_docs_for_generate" not in st.session_state:
        st.session_state["selected_docs_for_generate"] = []

    # ── 1단계: 업무 분류 선택 (구분/민원/종류/세부) ──
    cols_top = st.columns(4)
    with cols_top[0]:
        category = st.selectbox("구분", CATEGORY_OPTIONS, key="doc_category")

    with cols_top[1]:
        minwon_options = MINWON_OPTIONS.get(category, [])
        minwon = st.selectbox("민원", minwon_options, key="doc_minwon")

    with cols_top[2]:
        tkey = (category, minwon)
        type_options = TYPE_OPTIONS.get(tkey, [])
        kind = st.selectbox("종류", type_options, key="doc_kind") if type_options else ""

    with cols_top[3]:
        skey = (category, minwon, kind or "x")
        subtype_options = SUBTYPE_OPTIONS.get(skey, [])
        detail = st.selectbox("추가선택", subtype_options,
                              key="doc_detail") if subtype_options else ""

    key_tuple = (
        normalize_step(category),
        normalize_step(minwon),
        normalize_step(kind),
        normalize_step(detail),
    )

    st.markdown("---")

    # ── 2단계: 사람 선택 (숙소제공자/신청인/대리인/신원보증인/합산자) ──
    cols_people = st.columns(5)

    prov = None
    guardian = None
    guarantor = None
    aggregator = None
    row = None

    # 숙소제공자
    with cols_people[0]:
        숙소키워드 = st.text_input("숙소제공자 검색", key="doc_accommodation_search")
    matched_provs = (
        df_cust[df_cust["한글"].str.contains(숙소키워드.strip(), na=False)]
        if 숙소키워드.strip()
        else pd.DataFrame()
    )
    if not matched_provs.empty:
        st.markdown("👀 **숙소제공자 검색 결과:**")
        for idx2, prov_row in matched_provs.iterrows():
            label2 = f"{prov_row['한글']} / {prov_row['등록증']} / {prov_row['연']}-{prov_row['락']}-{prov_row['처']}"
            if st.button(label2, key=f"accom_{idx2}"):
                st.session_state["selected_accommodation_idx"] = idx2
                st.session_state["document_generated"] = False
                st.rerun()
    if "selected_accommodation_idx" in st.session_state:
        prov = df_cust.loc[st.session_state["selected_accommodation_idx"]]
        st.markdown(f"✅ 선택된 숙소제공자: **{prov['한글']}**")
    apply_prov_seal = st.checkbox("숙소제공자 도장", value=True, key="chk_prov_seal")

    # 신청인
    with cols_people[1]:
        applicant_kw = st.text_input("신청인 이름 (고객 검색)", key="doc_search")
    matched = (
        df_cust[df_cust["한글"].str.contains(applicant_kw.strip(), na=False)]
        if applicant_kw.strip()
        else pd.DataFrame()
    )
    if not matched.empty:
        st.markdown("🔎 **신청인 검색 결과:**")
        for idx, row_tmp in matched.iterrows():
            label = f"{row_tmp['한글']} / {row_tmp['등록증']} / {row_tmp['연']}-{row_tmp['락']}-{row_tmp['처']}"
            if st.button(label, key=f"select_{idx}"):
                st.session_state["selected_customer_idx"] = idx
                st.session_state["document_generated"] = False
                st.rerun()
    선택된_고객 = None
    if "selected_customer_idx" in st.session_state:
        row = df_cust.loc[st.session_state["selected_customer_idx"]]
        선택된_고객 = row["한글"]
        st.markdown(f"✅ 선택된 신청인: **{선택된_고객}**")
    apply_applicant_seal = st.checkbox("신청인 도장", value=True, key="chk_applicant_seal")

    # 미성년자 여부 + 대리인
    is_minor = calc_is_minor(row.get("등록증", "")) if row is not None else False
    if is_minor:
        with cols_people[2]:
            guardian_kw = st.text_input("대리인 이름 (고객 검색)", key="doc_guardian_search")
        후보 = (
            df_cust[df_cust["한글"].str.contains(guardian_kw.strip(), na=False)]
            if guardian_kw.strip()
            else pd.DataFrame()
        )
        if not 후보.empty:
            st.markdown("👤 **대리인 검색 결과:**")
            for _, row2 in 후보.iterrows():
                cust_id = row2["고객ID"]
                label3 = f"{row2['한글']} / {row2['등록증']} / {row2['연']}-{row2['락']}-{row2['처']}"
                if st.button(label3, key=f"guardian_{cust_id}"):
                    st.session_state["selected_guardian_idx"] = row2.name
                    st.session_state["document_generated"] = False
                    st.rerun()
        if "selected_guardian_idx" in st.session_state:
            guardian = df_cust.loc[st.session_state["selected_guardian_idx"]]
            st.markdown(f"✅ 선택된 대리인: **{guardian['한글']}**")
    apply_guardian_seal = st.checkbox("대리인 도장", value=True,
                                      key="chk_guardian_seal") if is_minor else False

    # 신원보증인
    need_g = need_guarantor(*key_tuple)
    if need_g:
        with cols_people[3]:
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
                lbl = f"{grow['한글']} / {grow['등록증']} / {grow['연']}-{grow['락']}-{grow['처']}"
                if st.button(lbl, key=f"guarantor_{cust_id}"):
                    st.session_state["selected_guarantor_idx"] = grow.name
                    st.session_state["document_generated"] = False
                    st.rerun()
        if "selected_guarantor_idx" in st.session_state:
            guarantor = df_cust.loc[st.session_state["selected_guarantor_idx"]]
            st.markdown(f"✅ 선택된 신원보증인: **{guarantor['한글']}**")
    apply_guarantor_seal = st.checkbox("신원보증인 도장", value=True,
                                       key="chk_guarantor_seal") if need_g else False

    # 합산자
    need_a = need_aggregator(*key_tuple)
    if need_a:
        with cols_people[4]:
            agg_kw = st.text_input("합산자 검색", key="doc_agg_search")
        matched_agg = (
            df_cust[df_cust["한글"].str.contains(agg_kw.strip(), na=False)]
            if agg_kw.strip()
            else pd.DataFrame()
        )
        if not matched_agg.empty:
            st.markdown("📊 **합산자 검색 결과:**")
            for _, arow in matched_agg.iterrows():
                cust_id = arow["고객ID"]
                lbl = f"{arow['한글']} / {arow['등록증']} / {arow['연']}-{arow['락']}-{arow['처']}"
                if st.button(lbl, key=f"agg_{cust_id}"):
                    st.session_state["selected_agg_idx"] = arow.name
                    st.session_state["document_generated"] = False
                    st.rerun()
        if "selected_agg_idx" in st.session_state:
            aggregator = df_cust.loc[st.session_state["selected_agg_idx"]]
            st.markdown(f"✅ 선택된 합산자: **{aggregator['한글']}**")
    apply_aggregator_seal = st.checkbox("합산자 도장", value=True,
                                        key="chk_agg_seal") if need_a else False

    st.markdown("---")

    # ── 3단계: 필요서류 목록 표시 + 체크박스 ──
    docs_cfg = REQUIRED_DOCS.get(key_tuple)
    selected_ids = set(st.session_state.get("selected_docs_for_generate", []))

    if not docs_cfg:
        st.info("선택한 조합에 대해 아직 필요서류 설정이 없습니다.")
        docs_list = []
    else:
        docs_list = docs_cfg["main"] + docs_cfg["agent"]
        st.markdown("### 📄 필요서류 목록")
        new_selected = []
        for doc_name in docs_list:
            checked = st.checkbox(
                doc_name,
                key=f"doc_{doc_name}",
                value=(doc_name in selected_ids),
            )
            if checked:
                new_selected.append(doc_name)
        st.session_state["selected_docs_for_generate"] = new_selected
        selected_ids = set(new_selected)

    st.markdown("---")

    # ── 4단계: 최종 서류 작성 ──
    if st.button("🖨 최종 서류 작성", type="primary"):
        if not 선택된_고객 or row is None:
            st.error("신청인을 먼저 선택해 주세요.")
            return
        if not selected_ids:
            st.error("작성할 서류를 선택해 주세요.")
            return
        if is_minor and guardian is None:
            st.error("미성년자는 대리인을 선택해야 합니다.")
            return
        if need_g and guarantor is None:
            st.error("이 조합은 신원보증인이 필요합니다.")
            return
        if need_a and aggregator is None:
            st.error("이 조합은 합산자가 필요합니다.")
            return


        # 도장 이미지 준비 (체크된 사람만)

        # 1) 신청인/대리인 도장 이름 결정
        #    - 성인 : 신청인 이름 → applicant 위치(yin)
        #    - 미성년 : 대리인 이름 → applicant 위치(yin)
        if is_minor:
            applicant_seal_name = guardian["한글"] if guardian is not None else None
        else:
            applicant_seal_name = 선택된_고객  # row["한글"] 과 동일

        # guardian 필드는 별도 도장이 필요한 경우에만 사용
        guardian_seal_name = guardian["한글"] if guardian is not None else None

        # 2) 행정사 도장 이름 (accounts 기준)
        agent_seal_name = None
        if account is not None:
            agent_seal_name = str(account.get("contact_name", "")).strip() or None

        seal_bytes_by_role = {
            # 신청인 위치 도장
            "applicant": make_seal_bytes(applicant_seal_name)
            if (applicant_seal_name and apply_applicant_seal)
            else None,

            # 숙소제공자
            "accommodation": make_seal_bytes(prov["한글"])
            if (prov is not None and apply_prov_seal)
            else None,

            # 신원보증인
            "guarantor": make_seal_bytes(guarantor["한글"])
            if (guarantor is not None and apply_guarantor_seal)
            else None,

            # 대리인/법정대리인 별도 필드가 있을 때
            "guardian": make_seal_bytes(guardian_seal_name)
            if (guardian_seal_name and apply_guardian_seal)
            else None,

            # 합산자
            "aggregator": make_seal_bytes(aggregator["한글"])
            if (aggregator is not None and apply_aggregator_seal)
            else None,

            # 행정사(위임장, 대행업무수행확인서용)
            "agent": make_seal_bytes(agent_seal_name)
            if agent_seal_name
            else None,
        }

        field_values = build_field_values(
            row=row,
            prov=prov,
            guardian=guardian,
            guarantor=guarantor,
            aggregator=aggregator,
            is_minor=is_minor,
            account=account,
            category=category,
            minwon=minwon,
        )

        merged_doc = fitz.open()
        for doc_name in docs_list:
            if doc_name not in selected_ids:
                continue
            template_path = DOC_TEMPLATES.get(doc_name)
            fill_and_append_pdf(template_path, field_values,
                                seal_bytes_by_role, merged_doc)

        if merged_doc.page_count == 0:
            st.error("선택된 서류에 해당하는 템플릿 파일이 없습니다.")
            return

        out = io.BytesIO()
        merged_doc.save(out)
        merged_doc.close()
        out.seek(0)

        if st.download_button(
            "📥 작성된 PDF 다운받기",
            data=out.read(),
            file_name=f"{선택된_고객}_{category}_{minwon}_{kind}_{detail or 'x'}.pdf",
            mime="application/pdf",
        ):
            st.session_state["document_generated"] = True
            st.rerun()

    if st.session_state.get("document_generated", False):
        st.success("✅ 문서가 성공적으로 생성되었습니다.")
        if st.button("🔄 다른 고객으로 다시 작성"):
            for k in [
                "selected_customer_idx",
                "selected_guardian_idx",
                "selected_accommodation_idx",
                "selected_guarantor_idx",
                "selected_agg_idx",
                "selected_docs_for_generate",
            ]:
                st.session_state.pop(k, None)
            st.session_state["document_generated"] = False
            st.rerun()
