#!/bin/sh
set -e
# YouTube changes often; a fresh yt-dlp on every start avoids most download errors.
if [ "${AUTO_UPDATE_YTDLP:-1}" = "1" ]; then
  pip install -q -U "yt-dlp[default]" || echo "yt-dlp update skipped"
fi
mkdir -p /data/jobs /data/models
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
