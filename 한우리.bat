@echo off
cd /d "C:\Users\윤찬\내 드라이브\한우리 현행업무\프로그램\출입국업무관리"

:: 파이썬 설치 여부 확인
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo [!] Python이 설치되어 있지 않습니다.
    echo    Python 다운로드 페이지: https://www.python.org/downloads/
    pause
    exit
)

:: streamlit 설치 여부 확인
pip show streamlit >nul 2>&1
IF ERRORLEVEL 1 (
    echo [i] Streamlit이 설치되어 있지 않아 설치를 시작합니다...
    pip install streamlit
)

:: 실행
streamlit run app.py

pause