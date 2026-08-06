FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ../../shared ./shared
COPY . ./services/nexus_features

ENV PYTHONPATH=/app
ENV REDIS_URL=redis://redis:6379

EXPOSE 8002

CMD ["uvicorn", "services.nexus_features.main:app", "--host", "0.0.0.0", "--port", "8002"]
