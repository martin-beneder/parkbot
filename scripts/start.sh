#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> ParkBot wird gestartet ..."

cd "$PROJECT_DIR"
docker compose up -d 2>/dev/null || docker-compose up -d

echo ""
echo "==> ParkBot läuft!"
echo "==> Dashboard:  http://localhost:8000"
echo "==> Logs:       docker compose logs -f controller"
echo ""
echo "HINWEIS: Der Android-Emulator benötigt 2-5 Minuten zum Starten."
echo "         Das Dashboard ist sofort erreichbar, ADB-Verbindung"
echo "         wird automatisch hergestellt sobald der Emulator bereit ist."
