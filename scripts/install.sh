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
if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
  echo "==> Docker Compose wird installiert ..."
  COMPOSE_VERSION=$(curl -fsSL https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
  curl -L "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" \
    -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose
else
  echo "==> Docker Compose bereits installiert."
fi

# Fix Docker credential store if docker-credential-desktop is missing
DOCKER_CFG="${HOME}/.docker/config.json"
if [ -f "$DOCKER_CFG" ] && grep -q "docker-credential-desktop" "$DOCKER_CFG" 2>/dev/null; then
  echo "==> Behebe Docker Credential Store Problem ..."
  cp "$DOCKER_CFG" "${DOCKER_CFG}.bak"
  sed -i 's/"credsStore"[[:space:]]*:[[:space:]]*"desktop"/"credsStore": ""/g' "$DOCKER_CFG"
  echo "==> Erledigt (Backup: ${DOCKER_CFG}.bak)"
fi

# Check for Android binder support (required by ReDroid)
echo "==> Prüfe Android Binder-Unterstützung ..."
BINDER_OK=0
if [ -e /dev/binder ]; then
  echo "==> /dev/binder gefunden."
  BINDER_OK=1
elif lsmod 2>/dev/null | grep -q binder_linux; then
  echo "==> binder_linux Modul aktiv."
  BINDER_OK=1
elif grep -qsE "^CONFIG_ANDROID_BINDER_IPC=y" "/boot/config-$(uname -r)" 2>/dev/null; then
  echo "==> binder ist im Kernel integriert (CONFIG_ANDROID_BINDER_IPC=y)."
  BINDER_OK=1
else
  modprobe binder_linux 2>/dev/null && BINDER_OK=1 || true
fi

if [ "$BINDER_OK" -eq 0 ]; then
  echo "WARNUNG: Kein Android Binder-Support gefunden."
  echo "         ReDroid benötigt CONFIG_ANDROID_BINDER_IPC im Kernel."
  echo "         Auf CachyOS/Arch: yay -S linux-cachyos (binder ist eingebaut)"
fi

# ashmem is not required for Android 10+
modprobe ashmem_linux 2>/dev/null || true

# APK setup
mkdir -p "${PROJECT_DIR}/apk"
APK_FILE="${PROJECT_DIR}/apk/handyparken.apk"
if [ -f "$APK_FILE" ] && [ "$(stat -c%s "$APK_FILE" 2>/dev/null || echo 0)" -gt 1000000 ]; then
  echo "==> APK bereits vorhanden ($(du -h "$APK_FILE" | cut -f1))."
else
  echo ""
  echo "==> HANDYPARKEN APK nicht gefunden!"
  echo "    Die APK kann nicht automatisch heruntergeladen werden."
  echo "    Bitte manuell herunterladen und ablegen unter:"
  echo "      ${APK_FILE}"
  echo ""
  echo "    Download-Quellen:"
  echo "      - Google Play (via ADB/APK-Extractor auf eigenem Gerät)"
  echo "      - https://apkpure.com/handyparken/at.mobilkom.android.handyparken"
  echo ""
  echo "    Alternativ: Der Controller installiert die App beim ersten Start"
  echo "    automatisch, sobald die APK an obigem Pfad bereitgestellt wird."
fi

# Build containers
echo ""
echo "==> Baue Docker-Container ..."
cd "$PROJECT_DIR"
docker compose build 2>/dev/null || docker-compose build

echo ""
echo "==> Installation abgeschlossen!"
echo "==> Starten mit: bash scripts/start.sh"
