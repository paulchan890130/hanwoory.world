# core/google_sheets.py
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from config import OAUTH_CLIENT_SECRET_PATH, OAUTH_TOKEN_PATH, RUN_ENV
import os

from config import (
    KEY_PATH,
    SHEET_KEY,
    DEFAULT_TENANT_ID,
    SESS_TENANT_ID,
    SESS_USERNAME,
    PARENT_DRIVE_FOLDER_ID,
    CUSTOMER_DATA_TEMPLATE_ID,
    WORK_REFERENCE_TEMPLATE_ID,
    ACCOUNTS_SHEET_NAME,
    TENANT_MODE,
    CUSTOMER_SHEET_NAME,
    DAILY_SUMMARY_SHEET_NAME,
    DAILY_BALANCE_SHEET_NAME,
    PLANNED_TASKS_SHEET_NAME,
    ACTIVE_TASKS_SHEET_NAME,
    COMPLETED_TASKS_SHEET_NAME,
    EVENTS_SHEET_NAME,
    MEMO_LONG_SHEET_NAME,
    MEMO_MID_SHEET_NAME,
    MEMO_SHORT_SHEET_NAME,
)

def debug_print_drive_user():
    svc = get_drive_service()
    about = svc.about().get(fields="user, storageQuota").execute()
    print("현재 API 사용자 이메일:", about["user"]["emailAddress"])
    print("storageQuota:", about["storageQuota"])

def get_user_credentials(scopes):
    """
    로컬: token 없으면 브라우저 띄워서 로그인 (InstalledAppFlow)
    서버(Render): 원칙적으로 사용하지 않음. (서버에서는 서비스계정 사용)
    """
    creds = None

    # 공통: token.json 있으면 먼저 읽기
    if os.path.exists(OAUTH_TOKEN_PATH):
        creds = UserCredentials.from_authorized_user_file(OAUTH_TOKEN_PATH, scopes)

    # ----- 서버 모드 (Render) -----
    # 서버에서는 지금 이 함수를 직접 쓰지 않고, 서비스 계정(KEY_PATH)만 사용한다.
    # 혹시라도 잘못 호출되면 바로 에러를 내서 알 수 있게 한다.
    if RUN_ENV == "server":
        if not creds:
            raise RuntimeError("서버 모드에서는 get_user_credentials() 대신 서비스계정을 써야 합니다.")
        # 이미 token.json 이 있고 유효하면 그대로 사용 (호환용)
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                raise RuntimeError(f"서버 모드에서 OAuth 토큰 갱신 실패: {e}")
        return creds

    # ----- 로컬 모드 (Windows) -----
    # 여기부터는 RUN_ENV != "server" (즉, local)
    if not creds or not creds.valid:
        # 1) 기존 토큰이 있고, 만료 + refresh_token 있으면 먼저 갱신 시도
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                # 토큰이 만료되었거나 취소된 경우 → token.json 삭제 후 재로그인 유도
                try:
                    if os.path.exists(OAUTH_TOKEN_PATH):
                        os.remove(OAUTH_TOKEN_PATH)
                except OSError:
                    pass
                creds = None

        # 2) 여전히 creds 가 없거나 invalid 이면 새로 로그인
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                OAUTH_CLIENT_SECRET_PATH,
                scopes=scopes,
            )
            creds = flow.run_local_server(port=0)

        # 3) 로컬에서는 갱신/새 토큰을 파일에 저장
        with open(OAUTH_TOKEN_PATH, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


def get_drive_service():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    return build("drive", "v3", credentials=creds)

def create_tenant_workspace(tenant_id: str, office_name: str = "") -> dict:
    """
    새 사무실(테넌트)을 위해 Drive에 워크스페이스를 만든다.
    1) PARENT_DRIVE_FOLDER_ID 아래에 tenant_id 이름으로 폴더 생성
    2) 그 폴더 안에
       - CUSTOMER_DATA_TEMPLATE_ID 복사 → '{office_name}_고객 데이터'
       - WORK_REFERENCE_TEMPLATE_ID 복사 → '{office_name}_업무정리'
    3) 생성된 fileId 들을 dict로 반환
    """
    drive = get_drive_service()

    # 1) 폴더 생성
    folder_meta = {
        "name": office_name or tenant_id,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [PARENT_DRIVE_FOLDER_ID],
    }
    folder = drive.files().create(body=folder_meta, fields="id").execute()
    folder_id = folder["id"]

    # 2) 업무정리 파일 복사
    work_body = {
        "name": f"{office_name or tenant_id}_업무정리",
        "parents": [folder_id],
    }
    work_file = drive.files().copy(
        fileId=WORK_REFERENCE_TEMPLATE_ID,
        body=work_body,
        fields="id",
    ).execute()
    work_sheet_key = work_file["id"]

    # 3) 고객 데이터 파일 복사
    cust_body = {
        "name": f"{office_name or tenant_id}_고객 데이터",
        "parents": [folder_id],
    }
    cust_file = drive.files().copy(
        fileId=CUSTOMER_DATA_TEMPLATE_ID,
        body=cust_body,
        fields="id",
    ).execute()
    customer_sheet_key = cust_file["id"]

    return {
        "folder_id": folder_id,
        "work_sheet_key": work_sheet_key,
        "customer_sheet_key": customer_sheet_key,
    }


def update_account_workspace(login_id: str, workspace: dict) -> bool:
    """
    Accounts 시트에서 해당 login_id 행을 찾아
    folder_id / work_sheet_key / customer_sheet_key 를 채우고,
    is_active 도 TRUE 로 바꾼다.
    """
    from core.google_sheets import read_data_from_sheet, write_data_to_sheet  # 자기 자신이지만, 위쪽에 이미 정의돼 있음

    records = read_data_from_sheet(ACCOUNTS_SHEET_NAME, default_if_empty=[]) or []
    if not records:
        st.error("Accounts 시트에 데이터가 없습니다.")
        return False

    # 헤더 리스트 확보
    header_list = list(records[0].keys())

    updated = False
    for r in records:
        if str(r.get("login_id", "")).strip() == login_id.strip():
            # 워크스페이스 정보 채우기
            if "folder_id" in r:
                r["folder_id"] = workspace.get("folder_id", "")
            if "work_sheet_key" in r:
                r["work_sheet_key"] = workspace.get("work_sheet_key", "")
            if "customer_sheet_key" in r:
                r["customer_sheet_key"] = workspace.get("customer_sheet_key", "")
            if "is_active" in r:
                r["is_active"] = "TRUE"
            updated = True
            break

    if not updated:
        st.error(f"Accounts 시트에서 login_id='{login_id}' 를 찾지 못했습니다.")
        return False

    ok = write_data_to_sheet(ACCOUNTS_SHEET_NAME, records, header_list=header_list)

    if ok:
        # Accounts 시트가 바뀌었으니, 테넌트별 sheet_key 캐시 초기화
        try:
            _load_tenant_sheet_keys.clear()
        except Exception:
            # 혹시 모르니까 전체 cache_data라도 비우기 (최후의 안전장치)
            st.cache_data.clear()

    return ok


def create_tenant_spreadsheet(tenant_id: str, office_name: str = "") -> str:
    """
    기존 SHEET_KEY 스프레드시트를 템플릿 삼아서
    새 테넌트용 스프레드시트를 하나 복사 생성한다.
    반환: 새 파일의 sheet_key (스프레드시트 ID)
    """
    drive_svc = get_drive_service()
    name = office_name or f"{tenant_id}_출입국업무관리"

    body = {"name": name}
    # 부모 폴더 지정하고 싶으면 body["parents"] = [어떤_폴더_ID]

    new_file = drive_svc.files().copy(
        fileId=SHEET_KEY,
        body=body
    ).execute()

    new_id = new_file.get("id")
    return new_id


def get_current_tenant_id():
    """현재 세션에서 사용하는 테넌트 ID (없으면 기본 hanwoory)"""
    return st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)


@st.cache_data(ttl=600)
def _load_tenant_sheet_keys():
    """
    Accounts 시트에서 tenant_id 별로
    - customer_sheet_key
    - work_sheet_key
    를 읽어 dict로 반환.

    형식:
    {
      "hanwoory": {"customer": "...", "work": "..."},
      "officeA" : {"customer": "...", "work": "..."},
      ...
    }
    """
    from core.google_sheets import read_data_from_sheet  # 자기 자신 호출

    records = read_data_from_sheet(ACCOUNTS_SHEET_NAME, default_if_empty=[]) or []
    mapping: dict[str, dict[str, str]] = {}

    for r in records:
        # tenant_id가 비어 있으면 login_id라도 사용
        tid = str(r.get("tenant_id") or r.get("login_id") or "").strip()
        if not tid:
            continue

        is_active = str(r.get("is_active", "")).strip().lower()
        if is_active not in ("true", "1", "y"):
            continue  # 비활성 계정은 무시

        cust = str(r.get("customer_sheet_key", "")).strip()
        work = str(r.get("work_sheet_key", "")).strip()

        mapping[tid] = {
            "customer": cust,
            "work": work,
        }

    return mapping


def get_customer_sheet_key_for_tenant(tenant_id: str) -> str:
    """
    테넌트별 고객데이터 스프레드시트 ID 반환.

    - 로컬(TENANT_MODE=False): 기존 SHEET_KEY 사용
    - 서버(TENANT_MODE=True):
      * 해당 테넌트에 customer_sheet_key 가 있으면 그걸 사용
      * 일반 테넌트는 기본 테넌트(hanwoory)로 폴백하지 않는다
      * 기본 테넌트(hanwoory)는 자기 customer_sheet_key 없으면 SHEET_KEY 로 폴백
    """
    if not TENANT_MODE:
        return SHEET_KEY

    mapping = _load_tenant_sheet_keys()

    # 1) 현재 테넌트 우선
    rec = mapping.get(tenant_id)
    if rec and rec.get("customer"):
        return rec["customer"]

    # 2) 일반 테넌트면, 기본 테넌트로 폴백하지 않고 템플릿(또는 공용)으로 처리
    if tenant_id != DEFAULT_TENANT_ID:
        # 최악의 경우라도 admin 고객 데이터(SHEET_KEY)가 아니라,
        # 템플릿 파일(CUSTOMER_DATA_TEMPLATE_ID)만 쓰도록.
        return CUSTOMER_DATA_TEMPLATE_ID

    # 3) 기본 테넌트(hanwoory)의 폴백
    rec = mapping.get(DEFAULT_TENANT_ID)
    if rec and rec.get("customer"):
        return rec["customer"]

    # 4) 그래도 없으면 과거 구조 유지
    return SHEET_KEY

import streamlit as st
from googleapiclient.discovery import build
# ... 기존 import 그대로 두고, 아래 함수만 추가

@st.cache_data(ttl=600)
def get_sheet_column_widths(sheet_key: str, sheet_name: str) -> dict[int, int]:
    """
    구글시트의 열 너비(pixel)를 읽어서
    {컬럼인덱스(0-based): width} 형태로 반환.

    - get_gspread_client() 가 사용하는 동일 OAuth creds 재사용
    - 로컬 / 서버(Render) 공통 사용
    """
    client = get_gspread_client()
    if client is None:
        return {}

    try:
        # gspread Client 안에 들어있는 Credentials 그대로 사용
        creds = getattr(client, "auth", None)
        if creds is None:
            return {}

        # Sheets API 서비스 객체
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)

        resp = (
            service.spreadsheets()
            .get(
                spreadsheetId=sheet_key,
                includeGridData=True,
                fields="sheets(properties(title),data(columnMetadata(pixelSize)))",
            )
            .execute()
        )

        widths: dict[int, int] = {}

        for sheet in resp.get("sheets", []):
            props = sheet.get("properties", {})
            title = props.get("title")
            if title != sheet_name:
                continue

            data_list = sheet.get("data", [])
            if not data_list:
                break

            # 첫 번째 GridData 블록 기준
            col_meta = data_list[0].get("columnMetadata", [])
            for idx, meta in enumerate(col_meta):
                pixel = meta.get("pixelSize")
                if pixel is not None:
                    widths[idx] = pixel

            break  # 원하는 시트 찾았으니 종료

        return widths

    except HttpError as e:
        print(f"[get_sheet_column_widths] HttpError: {e}")
        return {}
    except Exception as e:
        print(f"[get_sheet_column_widths] Unexpected error: {e}")
        return {}


def get_work_sheet_key_for_tenant(tenant_id: str) -> str:
    """
    테넌트별 업무정리 스프레드시트 ID 반환.
    - 로컬(TENANT_MODE=False): WORK_REFERENCE_TEMPLATE_ID 사용
    - 서버(TENANT_MODE=True):
      * 해당 테넌트에 work_sheet_key가 있으면 그걸 사용
      * 일반 테넌트는 기본 테넌트(hanwoory)로 폴백하지 않는다
      * 기본 테넌트(hanwoory)는 자기 work_sheet_key 없으면 템플릿으로 폴백
    """
    if not TENANT_MODE:
        return WORK_REFERENCE_TEMPLATE_ID

    mapping = _load_tenant_sheet_keys()

    # 1) 현재 테넌트 우선
    rec = mapping.get(tenant_id)
    if rec and rec.get("work"):
        return rec["work"]

    # 2) 일반 테넌트면, 한우리로 폴백하지 않고 템플릿 사용
    if tenant_id != DEFAULT_TENANT_ID:
        return WORK_REFERENCE_TEMPLATE_ID

    # 3) 기본 테넌트(hanwoory)의 폴백
    rec = mapping.get(DEFAULT_TENANT_ID)
    if rec and rec.get("work"):
        return rec["work"]

    # 4) 그래도 없으면 템플릿
    return WORK_REFERENCE_TEMPLATE_ID


# ===== Google Sheets / Drive Client =====
@st.cache_resource(ttl=600)
def get_gspread_client():
    """
    gspread Client 생성.
    - 서버(Render): 서비스 계정(KEY_PATH) 사용
    - 로컬: OAuth(user) 사용
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ]

    if RUN_ENV == "server":
        # Render 서버에서는 서비스 계정으로만 접근
        creds = Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    else:
        # 로컬에서는 OAuth(데스크톱 클라이언트) 사용
        creds = get_user_credentials(scopes)

    return gspread.authorize(creds)

def get_drive_service():
    """
    Google Drive API 서비스 객체 생성.
    - 서버(Render): 서비스 계정(KEY_PATH) 사용
    - 로컬: OAuth(user) 사용
    """
    scopes = ["https://www.googleapis.com/auth/drive"]

    if RUN_ENV == "server":
        creds = Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    else:
        creds = get_user_credentials(scopes)

    return build("drive", "v3", credentials=creds)


def get_worksheet(client, sheet_name: str):
    """
    sheet_name 에 따라 적절한 테넌트별 스프레드시트를 선택해서
    해당 워크시트를 열어준다.
    """
    tenant_id = get_current_tenant_id()

    # ─────────────────────
    # 1) 멀티테넌트 모드 (서버: Render)
    # ─────────────────────
    if TENANT_MODE:
        # 1-1) 고객데이터 워크북 안에 들어있는 탭들
        if sheet_name in (
            CUSTOMER_SHEET_NAME,          # "고객 데이터"
            DAILY_SUMMARY_SHEET_NAME,     # "일일결산"
            DAILY_BALANCE_SHEET_NAME,     # "잔액"
            PLANNED_TASKS_SHEET_NAME,     # "예정업무"
            ACTIVE_TASKS_SHEET_NAME,      # "진행업무"
            COMPLETED_TASKS_SHEET_NAME,   # "완료업무"
            EVENTS_SHEET_NAME,            # "일정"
            MEMO_LONG_SHEET_NAME,         # "장기메모"
            MEMO_MID_SHEET_NAME,          # "중기메모"
            MEMO_SHORT_SHEET_NAME,        # "단기메모"
        ):
            sheet_key = get_customer_sheet_key_for_tenant(tenant_id)

        # 1-2) 업무정리 워크북 안에 들어있는 탭들
        elif sheet_name in ("업무참고", "업무정리"):
            sheet_key = get_work_sheet_key_for_tenant(tenant_id)

        # 1-3) 혹시 빠진 탭이 있으면, 일단 기본(admin) 시트로
        else:
            sheet_key = SHEET_KEY

    # ─────────────────────
    # 2) 단일 테넌트 모드 (로컬 개발)
    #    → 예전 동작 최대한 유지
    # ─────────────────────
    else:
        if sheet_name in ("업무참고", "업무정리"):
            sheet_key = get_work_sheet_key_for_tenant(tenant_id)
        else:
            # 기존처럼 하나의 SHEET_KEY 안에 모든 탭이 있는 구조
            sheet_key = SHEET_KEY

    sh = client.open_by_key(sheet_key)
    return sh.worksheet(sheet_name)


def create_office_files_for_tenant(tenant_id: str, office_name: str = "") -> dict:
    """
    새 사무소(tenant)를 위해 구글 드라이브에 폴더 1개 + 시트 2개를 자동 생성.
    - 부모 폴더: PARENT_DRIVE_FOLDER_ID
    - 고객 데이터 시트: CUSTOMER_DATA_TEMPLATE_ID 복사
    - 업무정리 시트: WORK_REFERENCE_TEMPLATE_ID 복사

    반환:
        {
            "folder_id": "<드라이브 폴더 ID>",
            "customer_sheet_key": "<고객 데이터 스프레드시트 ID>",
            "work_sheet_key": "<업무정리 스프레드시트 ID>",
        }
    """
    tenant_id = (tenant_id or "").strip()
    office_name = (office_name or "").strip() or tenant_id

    drive_svc = get_drive_service()

    # 1) 사무소 전용 폴더 생성
    folder_name = f"{tenant_id}_{office_name}"
    folder_meta = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [PARENT_DRIVE_FOLDER_ID],
    }
    folder = drive_svc.files().create(
        body=folder_meta,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    folder_id = folder["id"]

    # 2) 고객 데이터 템플릿 복사
    customer_name = f"{office_name}_고객데이터"
    customer_file = drive_svc.files().copy(
        fileId=CUSTOMER_DATA_TEMPLATE_ID,
        body={
            "name": customer_name,
            "parents": [folder_id],
        },
        fields="id",
        supportsAllDrives=True,
    ).execute()
    customer_sheet_key = customer_file["id"]

    # 3) 업무정리 템플릿 복사
    work_name = f"{office_name}_업무정리"
    work_file = drive_svc.files().copy(
        fileId=WORK_REFERENCE_TEMPLATE_ID,
        body={
            "name": work_name,
            "parents": [folder_id],
        },
        fields="id",
        supportsAllDrives=True,
    ).execute()
    work_sheet_key = work_file["id"]

    return {
        "folder_id": folder_id,
        "customer_sheet_key": customer_sheet_key,
        "work_sheet_key": work_sheet_key,
    }

# ===== 공용 Read/Write =====
def write_data_to_sheet(sheet_name: str, records: list[dict], header_list: list[str]) -> bool:
    """
    sheet_name 시트에 records 목록을 header_list 순서대로 덮어씁니다.
    기존 내용을 지우고 전체 데이터를 다시 씁니다.
    """
    try:
        client = get_gspread_client()
        worksheet = get_worksheet(client, sheet_name)
        worksheet.clear()
        rows = [header_list]
        for record in records:
            rows.append([record.get(h, "") for h in header_list])
        worksheet.update(rows)
        return True
    except Exception as e:
        st.error(f"❌ write_data_to_sheet 오류 ({sheet_name}): {e}")
        return False

def _col_letter(n: int) -> str:
    # 1 -> A, 2 -> B, ... 26 -> Z, 27 -> AA ...
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def upsert_rows_by_id(sheet_name: str,
                      header_list: list[str],
                      records: list[dict],
                      id_field: str = "id") -> bool:
    """
    id_field 기준:
    - 기존 id 있으면 해당 행만 UPDATE
    - 없으면 APPEND
    - 삭제는 하지 않음(삭제는 delete_row_by_id로 별도 처리)
    """
    try:
        client = get_gspread_client()
        ws = get_worksheet(client, sheet_name)

        values = ws.get_all_values()  # [header, row2, row3...]
        last_col = _col_letter(len(header_list))

        # 시트 비어있으면: 헤더+전체 한번에
        if not values:
            rows = [header_list] + [[str(r.get(c, "")) for c in header_list] for r in records]
            ws.update(f"A1:{last_col}{len(rows)}", rows, value_input_option="USER_ENTERED")
            return True

        # 헤더 보정(필요시 1행만 업데이트)
        header = values[0]
        if header != header_list:
            ws.update(f"A1:{last_col}1", [header_list], value_input_option="USER_ENTERED")
            header = header_list  # 이후 로직은 header_list 기준으로

        if id_field not in header:
            raise ValueError(f"시트 헤더에 '{id_field}' 컬럼이 없습니다.")

        id_idx = header.index(id_field)

        # 기존 id -> row_number(시트 행번호) 매핑
        existing = {}
        for r_i, row in enumerate(values[1:], start=2):  # 2행부터 데이터
            if id_idx < len(row):
                rid = str(row[id_idx]).strip()
                if rid:
                    existing[rid] = r_i

        updates = []
        appends = []

        for rec in records:
            rid = str(rec.get(id_field, "")).strip()
            row_vals = [str(rec.get(c, "")) for c in header_list]

            if rid and rid in existing:
                row_no = existing[rid]
                updates.append({
                    "range": f"A{row_no}:{last_col}{row_no}",
                    "values": [row_vals],
                })
            else:
                appends.append(row_vals)

        # 부분 업데이트(1회)
        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")

        # 신규 append(1회)
        if appends:
            ws.append_rows(appends, value_input_option="USER_ENTERED")

        return True

    except Exception as e:
        st.error(f"❌ upsert_rows_by_id 오류 ({sheet_name}): {e}")
        return False


def delete_row_by_id(sheet_name: str, rid: str, id_field: str = "id") -> bool:
    """id_field 값이 rid인 행을 찾아서 1행 삭제"""
    try:
        client = get_gspread_client()
        ws = get_worksheet(client, sheet_name)

        values = ws.get_all_values()
        if not values:
            return True

        header = values[0]
        if id_field not in header:
            raise ValueError(f"시트 헤더에 '{id_field}' 컬럼이 없습니다.")
        id_idx = header.index(id_field)

        target = str(rid).strip()
        for r_i, row in enumerate(values[1:], start=2):
            if id_idx < len(row) and str(row[id_idx]).strip() == target:
                ws.delete_rows(r_i)
                return True

        return True  # 못 찾으면 그냥 True 처리(안전)
    except Exception as e:
        st.error(f"❌ delete_row_by_id 오류 ({sheet_name}): {e}")
        return False



def append_rows_to_sheet(sheet_name: str, records: list[dict], header_list: list[str]) -> bool:
    """
    신규 레코드만 Google Sheet에 append 합니다.
    기존 내용은 건드리지 않고, 한 번의 API 호출로 여러 줄을 추가할 수 있습니다.
    """
    try:
        client    = get_gspread_client()
        worksheet = get_worksheet(client, sheet_name)
        rows = [[record.get(h, "") for h in header_list] for record in records]
        worksheet.append_rows(rows)
        return True
    except Exception as e:
        st.error(f"❌ append_rows_to_sheet 오류 ({sheet_name}): {e}")
        return False

def read_data_from_sheet(sheet_name: str, default_if_empty=None):
    client = get_gspread_client()
    worksheet = get_worksheet(client, sheet_name)
    try:
        data = worksheet.get_all_records()
        return data if data else default_if_empty
    except Exception as e:
        st.warning(f"[시트 읽기 실패] {sheet_name}: {e}")
        return default_if_empty


def read_memo_from_sheet(sheet_name: str):
    client = get_gspread_client()
    if client is None:
        return " "

    worksheet = get_worksheet(client, sheet_name)
    if worksheet:
        try:
            val = worksheet.acell('A1').value
            return val if val is not None else " "
        except Exception as e:
            st.error(f"'{sheet_name}' 시트 (메모) 읽기 중 오류 발생: {e}")
            return " "
    return " "

def save_memo_to_sheet(sheet_name: str, content: str) -> bool:
    client = get_gspread_client()
    if client is None:
        return False
    
    worksheet = get_worksheet(client, sheet_name)
    if worksheet:
        try:
            worksheet.update_acell('A1', content)
            st.cache_data.clear()
            return True
        except Exception as e:
            st.error(f"'{sheet_name}' 시트 (메모) 저장 중 오류 발생: {e}")
            return False
    return False

def get_current_account_row():
    """
    현재 로그인한 계정(세션의 SESS_USERNAME)에 해당하는
    Accounts 시트 한 행(dict)을 리턴.
    없으면 None.
    """
    username = st.session_state.get(SESS_USERNAME, "")
    if not username:
        return None

    records = read_data_from_sheet(ACCOUNTS_SHEET_NAME, default_if_empty=[]) or []
    for r in records:
        if str(r.get("login_id", "")).strip() == username.strip():
            return r
    return None


def get_current_agent_info():
    """
    문서자동작성에서 쓰기 좋은 형태로 행정사 정보를 정리해서 반환.
    - office_name: 사무실명(대행기관명)
    - office_adr : 사무실 주소
    - agent_name : 행정사 성명
    - agent_tel  : 행정사 연락처
    - biz_reg_no : 사업자등록번호
    - agent_rrn  : 행정사 주민등록번호
    """
    acc = get_current_account_row()
    if acc is None:
        return {}

    return {
        "office_name":  acc.get("office_name", ""),
        "office_adr":   acc.get("office_adr", ""),
        "agent_name":   acc.get("contact_name", ""),
        "agent_tel":    acc.get("contact_tel", ""),
        "biz_reg_no":   acc.get("biz_reg_no", ""),
        "agent_rrn":    acc.get("agent_rrn", ""),
    }
