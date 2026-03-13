#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> ParkBot wird gestartet ..."

cd "$PROJECT_DIR"
docker-compose up -d

echo ""
echo "==> ParkBot läuft!"
echo "==> Dashboard: http://localhost:8000"
echo "==> Logs anzeigen: docker-compose logs -f controller"
