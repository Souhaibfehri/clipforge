FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HF_HOME=/data/models

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core fontconfig libglib2.0-0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to handle YouTube
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
ENTRYPOINT ["./entrypoint.sh"]
