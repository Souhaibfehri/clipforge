#!/usr/bin/env bash
# ClipForge one-shot installer / updater for an Ubuntu/Debian VPS.
# Safe on a server that already runs other apps (e.g. a trading bot):
#  - picks a free port, never takes 80/443 if something else uses them
#  - caps CPU and memory (APP_CPUS / APP_MEM in .env)
# First run asks for your keys. Later runs pull the latest code and restart (keeps .env and data).
set -euo pipefail
cd "$(dirname "$0")"

say() { printf "\n\033[1;33m==> %s\033[0m\n" "$*"; }
port_busy() { ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q . ; }
set_kv() { if grep -qE "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
get_kv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

if [ "$(id -u)" -ne 0 ]; then echo "Run as root (or with sudo)."; exit 1; fi

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker (2-3 minutes)"
  curl -fsSL https://get.docker.com | sh
fi

if [ -d .git ]; then
  say "Getting the latest code"
  git pull --ff-only || echo "(couldn't pull, using the code already here)"
fi

FIRST=0
if [ ! -f .env ]; then
  FIRST=1
  say "Setup: a few questions (paste with right-click or Ctrl+Shift+V)"
  cp .env.example .env
  read -rp "Anthropic API key (sk-ant-..., the trading bot's key works): " KEY </dev/tty
  read -rp "Choose a password for the ClipForge web app: " PASS </dev/tty
  read -rp "Groq API key (free at console.groq.com, strongly recommended on a small server): " GROQ </dev/tty
  read -rp "Domain for HTTPS, e.g. clips.yourdomain.com (Enter to skip for now): " DOM </dev/tty
  read -rp "Upload-Post API key for auto-posting (Enter to skip): " UP </dev/tty
  set_kv ANTHROPIC_API_KEY "$KEY"
  set_kv APP_PASSWORD "$PASS"
  set_kv DOMAIN "$DOM"
  set_kv UPLOADPOST_API_KEY "$UP"
  if [ -n "$GROQ" ]; then
    set_kv GROQ_API_KEY "$GROQ"; set_kv TRANSCRIBE_PROVIDER groq
  else
    set_kv WHISPER_MODEL base   # lighter local model for a small CPU
  fi
  chmod 600 .env
fi

# ---- pick a free port for the app (the bot may already use 8000)
PORT=$(get_kv APP_PORT)
if [ -z "$PORT" ]; then
  PORT=8000
  while port_busy "$PORT"; do PORT=$((PORT + 10)); done
  set_kv APP_PORT "$PORT"
fi

# ---- size the resource cap to this machine
if [ -z "$(get_kv APP_CPUS)" ]; then
  CORES=$(nproc)
  if [ "$CORES" -le 1 ]; then CPUS=0.75; elif [ "$CORES" -le 2 ]; then CPUS=1.5; else CPUS=$((CORES - 1)); fi
  set_kv APP_CPUS "$CPUS"
fi
[ -z "$(get_kv APP_MEM)" ] && set_kv APP_MEM 2g

DOMAIN=$(get_kv DOMAIN)
USE_HTTPS=0
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "clips.example.com" ]; then
  if docker ps --format '{{.Names}}' | grep -q caddy || ! { port_busy 80 || port_busy 443; }; then
    USE_HTTPS=1
  else
    echo "Ports 80/443 are already used by another app on this server, so ClipForge won't start its own HTTPS."
    echo "Point that web server at http://127.0.0.1:$PORT for $DOMAIN, or use the IP link below for now."
  fi
fi

mkdir -p data
if [ "$USE_HTTPS" = 1 ]; then
  say "Building and starting with HTTPS on $DOMAIN (first build ~5 minutes)"
  docker compose --profile https up -d --build
  URL="https://$DOMAIN"
else
  say "Building and starting (first build ~5 minutes)"
  docker compose up -d --build
  IP=$(curl -fs4 --max-time 5 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
  URL="http://$IP:$PORT"
  if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow "$PORT"/tcp >/dev/null; fi
fi

say "Waiting for the app to come up"
for i in $(seq 1 80); do
  curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 3
done

if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  say "ClipForge is running"
  echo "Open:      $URL"
  echo "User:      admin"
  echo "Password:  the one you chose (stored in $(pwd)/.env)"
  echo "Limits:    $(get_kv APP_CPUS) CPU, $(get_kv APP_MEM) RAM, so your other apps keep running smoothly"
  echo
  echo "Logs:      cd $(pwd) && docker compose logs -f app"
  echo "Update:    bash $(pwd)/install.sh"
  [ "$USE_HTTPS" = 1 ] && echo "If HTTPS doesn't load yet: check the DNS A record for $DOMAIN points to this server, wait a few minutes."
else
  echo "The app didn't answer yet. Check: cd $(pwd) && docker compose logs --tail 50 app"
fi
