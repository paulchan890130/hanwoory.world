# google_drive_service.py

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SERVICE_ACCOUNT_PATH = ".streamlit/service_account.json"

# 템플릿 시트 ID
TEMPLATE_SHEET_TASKS = "1p7Xt9x8TxVwQHzfiyTmCppvYSOuLHMTJmwdCErZ8KX4"
TEMPLATE_SHEET_CUSTOMER = "1UhMUpSJif54NqJXapQBe7DxyvbCil3RNiQDPhUgjNec"

# 가입자 폴더들이 생기는 상위 폴더
PARENT_FOLDER_ID = "1vqkdbFM7rImOAFmPyh0-ngN36X-MspvY"

def get_drive_service():
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def create_user_folder(email: str):
    drive = get_drive_service()

    # 1. 사용자 전용 폴더 생성
    folder_metadata = {
        "name": email,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [PARENT_FOLDER_ID]
    }
    folder = drive.files().create(body=folder_metadata, fields="id").execute()
    user_folder_id = folder["id"]

    # 2. 사용자 이메일에 편집자 권한 부여
    drive.permissions().create(
        fileId=user_folder_id,
        body={
            "type": "user",
            "role": "writer",
            "emailAddress": email
        },
        sendNotificationEmail=True
    ).execute()

    # 5. 고객관리 폴더 생성
    drive.files().create(
        body={
            "name": "고객관리",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [user_folder_id]
        },
        fields="id"
    ).execute()

    return {
        "user_folder_id": user_folder_id
    }

import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SERVICE_ACCOUNT_PATH = ".streamlit/service_account.json"
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]

def get_services():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=SCOPES)
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets

def create_user_folder(email: str):
    drive, _ = get_services()
    folder_metadata = {
        "name": email,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["1vqkdbFM7rImOAFmPyh0-ngN36X-MspvY"]  # 가입자 폴더의 상위 폴더 ID
    }
    folder = drive.files().create(body=folder_metadata, fields="id").execute()
    folder_id = folder["id"]

    # 권한 부여
    drive.permissions().create(
        fileId=folder_id,
        body={"type": "user", "role": "writer", "emailAddress": email},
        sendNotificationEmail=True
    ).execute()

    # 하위 폴더 고객관리/
    drive.files().create(body={
        "name": "고객관리",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [folder_id]
    }, fields="id").execute()

    return {"user_folder_id": folder_id}

def create_google_sheet_from_excel(xlsx_path, sheet_title, folder_id):
    drive, sheets = get_services()
    xls = pd.ExcelFile(xlsx_path)

    spreadsheet = sheets.spreadsheets().create(
        body={"properties": {"title": sheet_title}},
        fields="spreadsheetId"
    ).execute()
    sheet_id = spreadsheet["spreadsheetId"]

    drive.files().update(
        fileId=sheet_id,
        addParents=folder_id,
        fields="id, parents"
    ).execute()

    # 기본 시트 삭제
    default_sheet_id = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()["sheets"][0]["properties"]["sheetId"]
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"deleteSheet": {"sheetId": default_sheet_id}}]}
    ).execute()

    # 시트 복사
    for sheet_name in xls.sheet_names:
        df = xls.parse(sheet_name)
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name[:99]}}}]}
        ).execute()

        values = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{sheet_name[:99]}!A1",
            valueInputOption="RAW",
            body={"values": values}
        ).execute()

    return sheet_id

def create_user_assets(email: str):
    info = create_user_folder(email)
    folder_id = info["user_folder_id"]
    create_google_sheet_from_excel("업무정리.xlsx", f"업무정리 - {email}", folder_id)
    create_google_sheet_from_excel("고객 데이터.xlsx", f"고객 데이터 - {email}", folder_id)
    return {
        "user_folder_id": folder_id,
        "sheet_customer_id": customer_id,
        "sheet_tasks_id": task_id
    }
