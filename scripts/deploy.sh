#!/usr/bin/env bash
# Interactive deploy: prompts for bind IP and host port, then builds and starts the stack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi

DCOMPOSE="$ROOT/scripts/dcompose"
if [[ ! -x "$DCOMPOSE" ]]; then
  echo "Error: missing or non-executable $DCOMPOSE" >&2
  exit 1
fi

echo "AI Code Manager — Docker deploy"
echo "--------------------------------"

read -r -p "Bind address (0.0.0.0 = all interfaces, 127.0.0.1 = local only) [0.0.0.0]: " BIND_IP
BIND_IP=${BIND_IP:-0.0.0.0}
if [[ "$BIND_IP" == "localhost" ]]; then
  BIND_IP=127.0.0.1
fi

read -r -p "Host port (mapped to container 8000) [8000]: " HOST_PORT
HOST_PORT=${HOST_PORT:-8000}

if ! [[ "$HOST_PORT" =~ ^[0-9]+$ ]] || [[ "$HOST_PORT" -lt 1 ]] || [[ "$HOST_PORT" -gt 65535 ]]; then
  echo "Error: port must be a number between 1 and 65535." >&2
  exit 1
fi

export BIND_IP HOST_PORT

echo ""
echo "Starting: BIND_IP=$BIND_IP HOST_PORT=$HOST_PORT"
"$DCOMPOSE" up --build -d

echo ""
if [[ "$BIND_IP" == "0.0.0.0" ]]; then
  URL_HOST="localhost"
  echo "Done. API (docs):  http://${URL_HOST}:${HOST_PORT}/docs"
  echo "Health check:     http://${URL_HOST}:${HOST_PORT}/api/v1/health"
  echo "(Bound on all interfaces — from other machines use this host's IP:${HOST_PORT})"
else
  echo "Done. API (docs):  http://${BIND_IP}:${HOST_PORT}/docs"
  echo "Health check:     http://${BIND_IP}:${HOST_PORT}/api/v1/health"
fi
echo ""
echo "View logs:  cd \"$ROOT\" && ./scripts/dcompose logs -f"
echo "Stop:       cd \"$ROOT\" && ./scripts/dcompose down"
