FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 (config/seoul.yaml 상대경로가 /app 기준으로 잡히도록 WORKDIR=/app)
COPY . .

# Railway가 $PORT를 주입한다. 로컬 실행 대비 8000 fallback.
CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
