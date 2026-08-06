FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY services/nexus_ml/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY config/ /app/config/
COPY services/nexus_ml/ /app/services/nexus_ml/

ENV PYTHONPATH=/app \
    REDIS_URL=redis://redis:6379 \
    MODEL_DIR=/app/models_store \
    MLFLOW_TRACKING_URI=http://mlflow:5000 \
    LOG_LEVEL=INFO

RUN mkdir -p /app/models_store

EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health')"

CMD ["uvicorn", "services.nexus_ml.main:app", "--host", "0.0.0.0", "--port", "8003"]
