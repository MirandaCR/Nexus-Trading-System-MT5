FROM python:3.12-slim AS base

WORKDIR /app

COPY services/nexus_risk/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY config/ /app/config/
COPY services/nexus_risk/ /app/services/nexus_risk/

ENV PYTHONPATH=/app
ENV NEXUS_SERVICE_NAME=nexus-risk
ENV NEXUS_SERVICE_PORT=8006

EXPOSE 8006

CMD ["uvicorn", "services.nexus_risk.main:app", "--host", "0.0.0.0", "--port", "8006"]
