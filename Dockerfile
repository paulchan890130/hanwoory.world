# 1) Python 베이스 이미지 (슬림으로 시작)
FROM python:3.11-slim

# 2) 시스템 패키지 + Tesseract 설치 + OCR-B 언어팩 추가
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
        wget && \
    mkdir -p /usr/share/tesseract-ocr/4.00/tessdata && \
    wget -O /usr/share/tesseract-ocr/4.00/tessdata/ocrb.traineddata \
        https://github.com/Shreeshrii/tessdata_ocrb/raw/master/ocrb.traineddata && \
    rm -rf /var/lib/apt/lists/*

# 3) 작업 디렉토리 설정
WORKDIR /app

# 4) 파이썬 패키지 먼저 설치 (캐시 활용 위해 requirements만 먼저 복사)
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 5) 나머지 코드 복사
COPY . .

# 6) 환경 변수 설정
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata

# 7) 컨테이너 시작 명령
CMD ["bash", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0"]
