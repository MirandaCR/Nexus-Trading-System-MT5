FROM python:3.12-slim

WORKDIR /app

COPY services/nexus_api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY services/nexus_api/ /app/services/nexus_api/

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "services.nexus_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
