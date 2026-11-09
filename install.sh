#!/usr/bin/env bash
# ClipForge one-shot installer / updater for a fresh Ubuntu/Debian VPS.
# First run: asks for your keys, installs Docker, starts the app.
# Later runs: pulls the latest code and restarts (keeps your .env and data).
set -euo pipefail
cd "$(dirname "$0")"

say() { printf "\n\033[1;33m==> %s\033[0m\n" "$*"; }

if [ "$(id -u)" -ne 0 ]; then echo "Run as root (or with sudo)."; exit 1; fi

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker (2-3 minutes)"
  curl -fsSL https://get.docker.com | sh
fi

if [ -d .git ]; then
  say "Getting the latest code"
  git pull --ff-only || echo "(couldn't pull, using the code already here)"
fi

if [ ! -f .env ]; then
  say "Setup: a few questions"
  cp .env.example .env
  read -rp "Anthropic API key (sk-ant-...): " KEY </dev/tty
  read -rp "Choose a password for the web app: " PASS </dev/tty
  read -rp "Domain for HTTPS, e.g. clips.yourdomain.com (leave empty to skip for now): " DOM </dev/tty
  read -rp "Groq API key for fast transcription (optional, Enter to skip): " GROQ </dev/tty
  read -rp "Upload-Post API key for auto-posting (optional, Enter to skip): " UP </dev/tty
  set_kv() { sed -i "s|^$1=.*|$1=$2|" .env; }
  set_kv ANTHROPIC_API_KEY "$KEY"
  set_kv APP_PASSWORD "$PASS"
  set_kv DOMAIN "$DOM"
  set_kv UPLOADPOST_API_KEY "$UP"
  if [ -n "$GROQ" ]; then set_kv GROQ_API_KEY "$GROQ"; set_kv TRANSCRIBE_PROVIDER groq; fi
  chmod 600 .env
fi

DOMAIN=$(grep -E '^DOMAIN=' .env | cut -d= -f2- || true)
mkdir -p data

if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "clips.example.com" ]; then
  say "Building and starting with HTTPS on $DOMAIN"
  docker compose --profile https up -d --build
  URL="https://$DOMAIN"
else
  say "Building and starting (no domain yet)"
  docker compose up -d --build
  URL="http://$(curl -fs4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'):8000"
fi

say "Waiting for the app to come up"
for i in $(seq 1 60); do
  if curl -fs http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 3
done

if curl -fs http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  say "ClipForge is running"
  echo "Open:      $URL"
  echo "User:      admin"
  echo "Password:  the one you chose (stored in /opt/clipforge/.env)"
  echo
  echo "Logs:      cd /opt/clipforge && docker compose logs -f app"
  echo "Update:    bash /opt/clipforge/install.sh"
  if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "clips.example.com" ]; then
    echo "If HTTPS doesn't load yet: check the DNS A record for $DOMAIN points to this server, then wait a few minutes."
  fi
else
  echo "The app didn't answer yet. Check: cd /opt/clipforge && docker compose logs --tail 50 app"
fi
