#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> ParkBot Installation"

# Detect WSL
IS_WSL=0
if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
  IS_WSL=1
  echo "==> WSL-Umgebung erkannt."
fi

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

# Helper: wait for Docker daemon to become ready
wait_for_docker() {
  local max_wait=30
  local waited=0
  while ! docker info &>/dev/null; do
    if [ "$waited" -ge "$max_wait" ]; then
      return 1
    fi
    echo "  Warte auf Docker-Daemon ... (${waited}s/${max_wait}s)"
    sleep 3
    waited=$((waited + 3))
  done
  return 0
}

# Install Docker if not present
if ! command -v docker &>/dev/null; then
  echo "==> Docker wird installiert ..."
  curl -fsSL https://get.docker.com | sh
  # get.docker.com already starts docker via systemctl if available.
  # We just need to wait for it to be ready.
  echo "==> Warte auf Docker-Daemon ..."
  if ! wait_for_docker; then
    # Docker didn't start automatically — try manual methods
    if command -v systemctl &>/dev/null; then
      systemctl enable docker 2>/dev/null || true
      systemctl start docker 2>/dev/null || true
    fi
    if ! docker info &>/dev/null; then
      service docker start 2>/dev/null || true
    fi
    if ! docker info &>/dev/null; then
      echo "==> Starte dockerd manuell im Hintergrund ..."
      nohup dockerd > /var/log/dockerd.log 2>&1 &
      disown
    fi
    if ! wait_for_docker; then
      echo ""
      echo "FEHLER: Docker-Daemon konnte nach Installation nicht gestartet werden."
      echo "  Prüfe /var/log/dockerd.log für Details."
      echo ""
      exit 1
    fi
  fi
else
  echo "==> Docker bereits installiert: $(docker --version)"
fi

# If Docker was already installed but daemon isn't running, try to start it
if ! docker info &>/dev/null; then
  echo "==> Docker-Daemon wird gestartet ..."

  # On WSL: kill any Docker Desktop socket remnants that block native docker
  if [ "$IS_WSL" -eq 1 ]; then
    # Remove stale Docker Desktop socket if it exists and is broken
    if [ -S /var/run/docker.sock ] && ! docker info &>/dev/null 2>&1; then
      rm -f /var/run/docker.sock 2>/dev/null || true
    fi
    # Also clean up Docker Desktop context if set
    docker context use default &>/dev/null 2>&1 || true
  fi

  # Try systemctl first, then service, then direct dockerd
  if command -v systemctl &>/dev/null; then
    systemctl start docker 2>/dev/null || true
  fi
  if ! docker info &>/dev/null; then
    service docker start 2>/dev/null || true
  fi
  if ! docker info &>/dev/null; then
    # Last resort: start dockerd directly in background
    echo "==> Starte dockerd manuell im Hintergrund ..."
    nohup dockerd > /var/log/dockerd.log 2>&1 &
    disown
  fi

  if ! wait_for_docker; then
    echo ""
    echo "FEHLER: Docker-Daemon konnte nicht gestartet werden."
    if [ "$IS_WSL" -eq 1 ]; then
      echo "  Prüfe /var/log/dockerd.log für Details."
      echo "  Ggf. Docker Desktop deinstallieren, da es Konflikte verursacht."
    else
      echo "  Starte den Docker-Daemon: sudo systemctl start docker"
    fi
    echo ""
    exit 1
  fi
fi

# Install Docker Compose if not present
if ! docker compose version &>/dev/null 2>&1 && ! command -v docker-compose &>/dev/null; then
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
if [ -e /dev/binder ] || [ -e /dev/binderfs/binder ]; then
  echo "==> /dev/binder gefunden."
  BINDER_OK=1
elif lsmod 2>/dev/null | grep -q binder_linux; then
  echo "==> binder_linux Modul aktiv."
  BINDER_OK=1
elif grep -qsE "^CONFIG_ANDROID_BINDER_IPC=y" "/boot/config-$(uname -r)" 2>/dev/null; then
  echo "==> binder ist im Kernel integriert (CONFIG_ANDROID_BINDER_IPC=y)."
  BINDER_OK=1
elif zgrep -qsE "^CONFIG_ANDROID_BINDER_IPC=y" "/proc/config.gz" 2>/dev/null; then
  echo "==> binder ist im Kernel integriert (via /proc/config.gz)."
  BINDER_OK=1
else
  modprobe binder_linux 2>/dev/null && BINDER_OK=1 || true
fi

if [ "$BINDER_OK" -eq 0 ]; then
  echo ""
  echo "==> Kein Android Binder-Support gefunden."
  echo "    ReDroid benötigt CONFIG_ANDROID_BINDER_IPC im Kernel."
  if [ "$IS_WSL" -eq 1 ]; then
    echo "==> Starte automatischen WSL2-Kernel-Build mit Binder-Support ..."
    bash "${SCRIPT_DIR}/build_wsl_kernel.sh"
    echo ""
    echo "==> Bitte WSL neu starten (wsl --shutdown) und dann install.sh erneut ausführen."
    exit 0
  else
    echo "  Auf CachyOS/Arch: yay -S linux-cachyos (binder ist eingebaut)"
    echo "  Oder: sudo modprobe binder_linux"
    echo ""
  fi
fi

# ashmem is not required for Android 10+
modprobe ashmem_linux 2>/dev/null || true

# APK download
mkdir -p "${PROJECT_DIR}/apk"
APK_FILE="${PROJECT_DIR}/apk/handyparken.apk"
if [ -f "$APK_FILE" ] && [ "$(stat -c%s "$APK_FILE" 2>/dev/null || echo 0)" -gt 1000000 ]; then
  echo "==> APK bereits vorhanden ($(du -h "$APK_FILE" | cut -f1))."
else
  echo "==> Lade HANDYPARKEN APK herunter ..."
  APK_PATH="$APK_FILE" python3 "${PROJECT_DIR}/scripts/download_apk.py" || true
  if [ -f "$APK_FILE" ] && [ "$(stat -c%s "$APK_FILE" 2>/dev/null || echo 0)" -gt 1000000 ]; then
    echo "==> APK erfolgreich heruntergeladen."
  else
    echo "==> APK wird beim ersten Container-Start automatisch heruntergeladen."
  fi
fi

# Build containers
echo ""
echo "==> Baue Docker-Container ..."
cd "$PROJECT_DIR"
docker_compose build

echo ""
echo "==> Installation abgeschlossen!"
echo "==> Starten mit: bash scripts/start.sh"
