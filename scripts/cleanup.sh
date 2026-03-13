#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> ParkBot wird gestoppt und bereinigt ..."

cd "$PROJECT_DIR"
docker-compose down -v --rmi all

echo "==> Bereinigung abgeschlossen."
