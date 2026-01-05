# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY generation/requirements.txt ./
# Install system dependencies if any
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt
# Add fastapi uvicorn to run the server
RUN pip install --no-cache-dir fastapi uvicorn

COPY generation ./generation
COPY assets ./assets

EXPOSE 8001

CMD ["python", "generation/server.py"]
