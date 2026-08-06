FROM python:3.11-slim

# System deps required by DuckDB, numpy wheels, and ib_insync
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY services/nexus_data/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the shared package
COPY shared/ /app/shared/

# Copy service code
COPY services/nexus_data/ /app/services/nexus_data/

ENV PYTHONPATH="/app"
EXPOSE 8001

CMD ["uvicorn", "services.nexus_data.main:app", "--host", "0.0.0.0", "--port", "8001"]
