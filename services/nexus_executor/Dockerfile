FROM python:3.12-slim AS base

WORKDIR /app

COPY services/nexus_executor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY config/ /app/config/
COPY services/nexus_executor/ /app/services/nexus_executor/

ENV PYTHONPATH=/app
ENV NEXUS_SERVICE_NAME=nexus-executor
ENV NEXUS_SERVICE_PORT=8007

EXPOSE 8007

CMD ["uvicorn", "services.nexus_executor.main:app", "--host", "0.0.0.0", "--port", "8007"]
