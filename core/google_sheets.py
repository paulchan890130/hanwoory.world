# core/google_sheets.py
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from config import OAUTH_CLIENT_SECRET_PATH, OAUTH_TOKEN_PATH, RUN_ENV
import os

from config import (
    KEY_PATH,
    SHEET_KEY,
    DEFAULT_TENANT_ID,
    SESS_TENANT_ID,
    TENANT_MODE,
    PARENT_DRIVE_FOLDER_ID,
    ACCOUNTS_SHEET_NAME,
    CUSTOMER_DATA_TEMPLATE_ID,
    WORK_REFERENCE_TEMPLATE_ID,
)

def debug_print_drive_user():
    svc = get_drive_service()
    about = svc.about().get(fields="user, storageQuota").execute()
    print("현재 API 사용자 이메일:", about["user"]["emailAddress"])
    print("storageQuota:", about["storageQuota"])

def get_user_credentials(scopes):
    """
    로컬: token 없으면 브라우저 띄워서 로그인 (InstalledAppFlow)
    서버(Render): token.json이 반드시 있어야 하고, 없으면 에러.
    """
    creds = None

    # 공통: token.json 있으면 먼저 읽기
    if os.path.exists(OAUTH_TOKEN_PATH):
        creds = UserCredentials.from_authorized_user_file(OAUTH_TOKEN_PATH, scopes)

    # ----- 서버 모드 (Render) -----
    if RUN_ENV == "server":
        if not creds:
            raise RuntimeError(
                "서버 모드인데 token.json이 없습니다. "
                "로컬에서 OAuth 로그인 후 생성된 token.json을 서버로 업로드하세요."
            )
        # 서버에서는 브라우저를 띄울 수 없으므로, 갱신만 허용
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # 갱신된 토큰 저장 (선택)
            with open(OAUTH_TOKEN_PATH, "w", encoding="utf-8") as token:
                token.write(creds.to_json())
        return creds

    # ----- 로컬 모드 -----
    # 여기부터는 RUN_ENV != "server" (즉, local)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # 기존 토큰 갱신
            creds.refresh(Request())
        else:
            # 처음 로그인: 브라우저 띄워서 계정 선택
            flow = InstalledAppFlow.from_client_secrets_file(
                OAUTH_CLIENT_SECRET_PATH,
                scopes=scopes,
            )
            creds = flow.run_local_server(port=0)

        # 갱신/새 토큰 저장
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
    return st.session_state.get(SESS_TENANT_ID, DEFAULT_TENANT_ID)

@st.cache_data(ttl=600)
def _load_tenant_sheet_map():
    """
    Accounts 시트에서 tenant_id -> sheet_key 매핑을 읽어온다.
    TENANT_MODE=True일 때만 사용.
    """
    from core.google_sheets import read_data_from_sheet  # 자기 자신이면 위치 맞춰서 import
    records = read_data_from_sheet(ACCOUNTS_SHEET_NAME, default_if_empty=[])
    mapping = {}
    for r in records:
        tid = str(r.get("tenant_id", "")).strip()
        sk  = str(r.get("sheet_key", "")).strip()
        is_active = str(r.get("is_active", "")).strip().lower()
        if tid and sk and is_active in ("true", "1", "y"):
            mapping[tid] = sk
    return mapping

def get_sheet_key_for_tenant(tenant_id: str) -> str:
    """
    나중에 테넌트별로 다른 스프레드시트를 쓰고 싶으면
    이 함수만 수정하면 된다.
    """
    if not TENANT_MODE:
        return SHEET_KEY  # 기존 단일 모드

    mapping = _load_tenant_sheet_map()
    return mapping.get(tenant_id, SHEET_KEY)

def get_sheet_key_for_tenant(tenant_id: str) -> str:
    """
    나중에 테넌트별로 다른 스프레드시트를 쓰고 싶으면
    이 함수만 수정하면 된다.
    """
    # 예시 (미래):
    # if TENANT_MODE:
    #     mapping = {
    #         "hanwoory": SHEET_KEY_HANWOORY,
    #         "office_b": SHEET_KEY_OFFICE_B,
    #     }
    #     return mapping.get(tenant_id, SHEET_KEY)
    return SHEET_KEY

# ===== Google Sheets / Drive Client =====
@st.cache_resource(ttl=600)
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = get_user_credentials(scopes)
    return gspread.authorize(creds)

def get_drive_service():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = get_user_credentials(scopes)
    return build("drive", "v3", credentials=creds)

def get_worksheet(client, sheet_name: str):
    tenant_id = get_current_tenant_id()
    sheet_key = get_sheet_key_for_tenant(tenant_id)
    sheet = client.open_by_key(sheet_key)
    return sheet.worksheet(sheet_name)

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
