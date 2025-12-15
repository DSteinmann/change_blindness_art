# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ENV GIT_HTTP_LOW_SPEED_LIMIT=0 \
    GIT_HTTP_MAX_RETRIES=5 \
    GIT_CURL_VERBOSE=1

WORKDIR /relay

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        cmake \
        build-essential \
        libopus-dev \
        pkg-config \
        libfmt-dev \
        libboost-filesystem-dev \
        libboost-date-time-dev \
        libboost-iostreams-dev \
        liblz4-dev \
        libzstd-dev \
        zlib1g-dev \
        libpng-dev \
        libjpeg-dev \
        libturbojpeg0-dev \
        libavcodec-dev \
        libavformat-dev \
        libavutil-dev \
        libswscale-dev \
        libswresample-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git config --global http.postBuffer 524288000

COPY relay/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY relay ./

EXPOSE 5555

CMD ["python", "aria_stream_relay.py", "--mode", "simulate", "--endpoint", "tcp://0.0.0.0:5555"]
