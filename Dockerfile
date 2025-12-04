# 1) Python 베이스 이미지 (슬림으로 시작)
FROM python:3.11-slim

# 2) 시스템 패키지 + Tesseract 설치
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-kor \
        curl \
        libtesseract-dev \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# 2-1) ocrb 언어 데이터 직접 다운로드
RUN mkdir -p /usr/share/tesseract-ocr/4.00/tessdata && \
    curl -L \
      https://github.com/tesseract-ocr/tessdata_best/raw/main/ocrb.traineddata \
      -o /usr/share/tesseract-ocr/4.00/tessdata/ocrb.traineddata

# 3) 작업 디렉토리 설정
WORKDIR /app

# 4) 파이썬 패키지 먼저 설치 (캐시 활용 위해 requirements만 먼저 복사)
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 5) 나머지 코드 복사
COPY . .

# 6) 환경 변수 설정 (필수는 아니지만 권장)
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata

# 7) 컨테이너 시작 명령
#    Render가 PORT 환경변수를 넣어줌 → 그걸 그대로 사용
CMD ["bash", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0"]