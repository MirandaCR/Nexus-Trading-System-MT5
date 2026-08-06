FROM python:3.12-slim AS base

WORKDIR /app

COPY services/nexus_monitor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY config/ /app/config/
COPY services/nexus_monitor/ /app/services/nexus_monitor/

ENV PYTHONPATH=/app
ENV NEXUS_SERVICE_NAME=nexus-monitor
ENV NEXUS_SERVICE_PORT=8008

EXPOSE 8008

CMD ["uvicorn", "services.nexus_monitor.main:app", "--host", "0.0.0.0", "--port", "8008"]
