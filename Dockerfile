# 1) Python 베이스 이미지 (슬림)
FROM python:3.11-slim

# 2) 시스템 패키지 + Tesseract 설치
#   - tesseract-ocr : 엔진 + 영어 기본
#   - tesseract-ocr-eng : 영어 데이터 명시
#   - tesseract-ocr-kor : 한글 데이터
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-kor \
        libtesseract-dev \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# 3) 작업 디렉토리
WORKDIR /app

# 4) 파이썬 패키지 먼저 설치 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5) 나머지 코드 복사
COPY . .

# 6) 환경변수 (⚠ TESSDATA_PREFIX는 아예 지정하지 말자)
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# 7) 컨테이너 시작 명령
CMD ["bash", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0"]
