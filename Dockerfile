FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed to build some Python wheels (e.g. for
# serial communication libraries) and clean up afterwards to keep the image
# small - important on resource constrained devices like a Raspberry Pi.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/app \
    RPI_SEMIAUTOMATOR_CONFIG=/app/config/config.yaml

EXPOSE 8080

CMD ["python", "app/main.py"]
