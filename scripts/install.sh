#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> ParkBot Installation"

# Install Docker if not present
if ! command -v docker &>/dev/null; then
  echo "==> Docker wird installiert ..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
else
  echo "==> Docker bereits installiert: $(docker --version)"
fi

# Install Docker Compose if not present
if ! command -v docker-compose &>/dev/null; then
  echo "==> Docker Compose wird installiert ..."
  COMPOSE_VERSION=$(curl -fsSL https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
  curl -L "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" \
    -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose
else
  echo "==> Docker Compose bereits installiert: $(docker-compose --version)"
fi

# Load kernel modules required by redroid
echo "==> Lade Kernel-Module ..."
modprobe binder_linux || echo "WARNUNG: binder_linux konnte nicht geladen werden (ggf. nicht verfügbar)"
modprobe ashmem_linux || echo "WARNUNG: ashmem_linux konnte nicht geladen werden (ggf. nicht verfügbar)"

# Create apk directory
mkdir -p "${PROJECT_DIR}/apk"

# Download APK
APK_FILE="${PROJECT_DIR}/apk/handyparken.apk"
if [ ! -f "$APK_FILE" ] || [ "$(stat -c%s "$APK_FILE" 2>/dev/null || echo 0)" -lt 1000000 ]; then
  echo "==> Lade HANDYPARKEN APK herunter ..."
  curl -L "https://d.apkpure.com/b/APK/at.mobilkom.android.handyparken?version=latest" \
    -H "User-Agent: Mozilla/5.0" \
    -o "$APK_FILE" || echo "WARNUNG: APK-Download fehlgeschlagen. Bitte APK manuell in apk/handyparken.apk ablegen."
else
  echo "==> APK bereits vorhanden."
fi

# Build containers
echo "==> Baue Docker-Container ..."
cd "$PROJECT_DIR"
docker-compose build

echo ""
echo "==> Installation abgeschlossen!"
echo "==> Starten mit: bash scripts/start.sh"
