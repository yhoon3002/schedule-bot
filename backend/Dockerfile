FROM python:3.12-slim

# (빌드 도구 필요시) gcc 등 설치
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 설치
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 앱 복사
COPY backend /app

# Render가 자동으로 PORT 주입(없으면 8000 기본값 사용)
ENV PORT=8000
EXPOSE 8000

# FastAPI 실행
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]