FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCMAGLEV_RENDER_LITE=1 \
    SCMAGLEV_MAX_TRACKED_TRAINS=30 \
    TOSS_PAYMENTS_MOCK_ONLY=1 \
    CONGESTION_LAZY_LOAD=1 \
    CONGESTION_DISABLE_DL=1 \
    CONGESTION_WARMUP=0 \
    CONGESTION_AUTO_RETRAIN=0 \
    CONGESTION_RETRAIN_HOURS=24 \
    CONGESTION_MIN_NEW_SAMPLES=300

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"10000\")}/api/health', timeout=10)" || exit 1

CMD ["sh", "-c", "gunicorn --worker-class gthread --threads 4 -w 1 --bind 0.0.0.0:${PORT:-10000} --timeout 120 --graceful-timeout 30 --keep-alive 5 app:app"]
