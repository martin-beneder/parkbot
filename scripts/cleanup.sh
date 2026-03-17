#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> ParkBot wird gestoppt und bereinigt ..."

# Helper: run docker compose (plugin) or docker-compose (standalone)
docker_compose() {
  if docker compose version &>/dev/null; then
    docker compose "$@"
  elif command -v docker-compose &>/dev/null; then
    docker-compose "$@"
  else
    echo "FEHLER: Weder 'docker compose' noch 'docker-compose' gefunden."
    return 1
  fi
}

cd "$PROJECT_DIR"
docker_compose down -v --rmi all

echo "==> Bereinigung abgeschlossen."
