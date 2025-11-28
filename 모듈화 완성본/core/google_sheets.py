# core/google_sheets.py
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import KEY_PATH, SHEET_KEY

# ===== Google Sheets / Drive Client =====
@st.cache_resource(ttl=600)
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    return gspread.authorize(creds)

def get_drive_service():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=scopes)
    return build("drive", "v3", credentials=creds)

def get_worksheet(client, sheet_name: str):
    sheet = client.open_by_key(SHEET_KEY)
    return sheet.worksheet(sheet_name)

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
