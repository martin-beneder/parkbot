#!/bin/bash
# Builds a WSL2 kernel with Android Binder support (required for ReDroid).
# This script is called automatically by install.sh when running on WSL2
# without binder support. It can also be run standalone.
#
# After building, the user must restart WSL: wsl --shutdown && wsl
set -e

echo "==> WSL2 Kernel mit Binder-Support wird gebaut ..."
echo "    (Dies dauert ca. 10-20 Minuten beim ersten Mal)"
echo ""

# ── 1. Install build dependencies ────────────────────────────────────────────
echo "==> Installiere Build-Abhängigkeiten ..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    build-essential flex bison libssl-dev libelf-dev \
    bc dwarves python3 git wget ca-certificates \
    2>&1 | tail -1

# ── 2. Determine kernel version and get source ──────────────────────────────
KVER=$(uname -r)
# WSL kernel versions look like: 5.15.167.4-microsoft-standard-WSL2
# The upstream tag is: linux-msft-wsl-<major>.<minor>.<patch>.<rev>
KVER_BASE="${KVER%%-*}"  # e.g. 5.15.167.4

echo "==> Aktueller Kernel: $KVER"

BUILD_DIR="/usr/src/wsl2-kernel"
BZIMAGE_NAME="bzImage-wsl2-binder"

# Detect Windows user profile path for .wslconfig
WIN_PROFILE=""
if [ -d "/mnt/c/Users" ]; then
    # Try to find the right Windows user by checking who owns the current mount
    for d in /mnt/c/Users/*/; do
        user_dir="$(basename "$d")"
        # Skip system accounts
        case "$user_dir" in
            Public|Default|"Default User"|"All Users") continue ;;
        esac
        # Check if .wslconfig already exists or if this is the likely user
        if [ -f "${d}.wslconfig" ] || [ -d "${d}Desktop" ]; then
            WIN_PROFILE="$d"
            break
        fi
    done
fi

if [ -z "$WIN_PROFILE" ]; then
    echo "WARNUNG: Konnte Windows-Benutzerprofil nicht finden."
    echo "         .wslconfig muss manuell konfiguriert werden."
fi

# ── 3. Clone or update kernel source ────────────────────────────────────────
if [ -d "$BUILD_DIR/.git" ]; then
    echo "==> Kernel-Source bereits vorhanden, aktualisiere ..."
    cd "$BUILD_DIR"
    git fetch --depth=1 origin 2>/dev/null || true
else
    echo "==> Lade WSL2 Kernel-Source herunter ..."
    rm -rf "$BUILD_DIR"
    # Determine the right branch based on kernel version
    MAJOR_MINOR=$(echo "$KVER_BASE" | cut -d. -f1-2)

    # Try specific version tag first, then fall back to rolling branch
    BRANCH="linux-msft-wsl-${KVER_BASE}"
    if ! git ls-remote --tags https://github.com/microsoft/WSL2-Linux-Kernel "refs/tags/${BRANCH}" 2>/dev/null | grep -q .; then
        BRANCH="linux-msft-wsl-${MAJOR_MINOR}.y"
    fi

    echo "==> Branch/Tag: $BRANCH"
    git clone --depth=1 --branch "$BRANCH" \
        https://github.com/microsoft/WSL2-Linux-Kernel.git "$BUILD_DIR" 2>&1 | tail -3
    cd "$BUILD_DIR"
fi

# ── 4. Configure kernel with binder support ─────────────────────────────────
echo "==> Konfiguriere Kernel mit Binder-Support ..."

# Start from the Microsoft WSL2 config
if [ -f "Microsoft/config-wsl" ]; then
    cp Microsoft/config-wsl .config
elif [ -f "/proc/config.gz" ]; then
    zcat /proc/config.gz > .config
else
    # Fall back to current running config
    make KCONFIG_CONFIG=.config defconfig
fi

# Enable Android binder + ensure netfilter/iptables modules stay enabled
# (Docker needs iptables NAT support)
declare -A EXTRA_CONFIGS=(
    # Android binder (for ReDroid)
    ["CONFIG_ANDROID"]="y"
    ["CONFIG_ANDROID_BINDER_IPC"]="y"
    ["CONFIG_ANDROID_BINDERFS"]="y"
    ["CONFIG_ANDROID_BINDER_DEVICES"]='""'
    ["CONFIG_ANDROID_BINDER_IPC_SELFTEST"]="n"
    # Netfilter / iptables (for Docker networking)
    ["CONFIG_NETFILTER"]="y"
    ["CONFIG_NETFILTER_ADVANCED"]="y"
    ["CONFIG_NETFILTER_XTABLES"]="y"
    ["CONFIG_NETFILTER_XT_MATCH_ADDRTYPE"]="y"
    ["CONFIG_NETFILTER_XT_MATCH_CONNTRACK"]="y"
    ["CONFIG_NETFILTER_XT_MATCH_IPVS"]="y"
    ["CONFIG_NF_CONNTRACK"]="y"
    ["CONFIG_NF_NAT"]="y"
    ["CONFIG_NF_TABLES"]="y"
    ["CONFIG_NF_TABLES_INET"]="y"
    ["CONFIG_NF_TABLES_IPV4"]="y"
    ["CONFIG_NF_TABLES_IPV6"]="y"
    ["CONFIG_NFT_COMPAT"]="y"
    ["CONFIG_NFT_NAT"]="y"
    ["CONFIG_NFT_MASQ"]="y"
    ["CONFIG_IP_NF_IPTABLES"]="y"
    ["CONFIG_IP_NF_FILTER"]="y"
    ["CONFIG_IP_NF_NAT"]="y"
    ["CONFIG_IP_NF_TARGET_MASQUERADE"]="y"
    ["CONFIG_IP_NF_TARGET_REJECT"]="y"
    ["CONFIG_IP_NF_RAW"]="y"
    ["CONFIG_IP_NF_MANGLE"]="y"
    ["CONFIG_IP_NF_SECURITY"]="y"
    ["CONFIG_IP6_NF_IPTABLES"]="y"
    ["CONFIG_IP6_NF_FILTER"]="y"
    ["CONFIG_IP6_NF_NAT"]="y"
    ["CONFIG_IP6_NF_TARGET_MASQUERADE"]="y"
    ["CONFIG_IP6_NF_RAW"]="y"
    ["CONFIG_IP6_NF_MANGLE"]="y"
    ["CONFIG_IP6_NF_SECURITY"]="y"
    ["CONFIG_NF_NAT_MASQUERADE"]="y"
    ["CONFIG_NF_NAT_REDIRECT"]="y"
    ["CONFIG_NETFILTER_XT_NAT"]="y"
    ["CONFIG_NETFILTER_XT_MATCH_COMMENT"]="y"
    ["CONFIG_NETFILTER_XT_MATCH_MULTIPORT"]="y"
    ["CONFIG_NETFILTER_XT_MATCH_STATISTIC"]="y"
    ["CONFIG_NETFILTER_XT_MATCH_LIMIT"]="y"
    ["CONFIG_NETFILTER_XT_TARGET_REDIRECT"]="y"
    ["CONFIG_NETFILTER_XT_TARGET_MASQUERADE"]="y"
    ["CONFIG_BRIDGE"]="y"
    ["CONFIG_BRIDGE_NETFILTER"]="y"
    ["CONFIG_VETH"]="y"
    ["CONFIG_VXLAN"]="y"
    ["CONFIG_IPVLAN"]="y"
    ["CONFIG_MACVLAN"]="y"
    ["CONFIG_DUMMY"]="y"
    # cgroup / namespace (for containers)
    ["CONFIG_CGROUPS"]="y"
    ["CONFIG_CGROUP_DEVICE"]="y"
    ["CONFIG_CGROUP_FREEZER"]="y"
    ["CONFIG_CGROUP_PIDS"]="y"
    ["CONFIG_CGROUP_HUGETLB"]="y"
    ["CONFIG_CGROUP_CPUACCT"]="y"
    ["CONFIG_CGROUP_PERF"]="y"
    ["CONFIG_MEMCG"]="y"
    ["CONFIG_NAMESPACES"]="y"
    ["CONFIG_NET_NS"]="y"
    ["CONFIG_PID_NS"]="y"
    ["CONFIG_IPC_NS"]="y"
    ["CONFIG_UTS_NS"]="y"
    ["CONFIG_USER_NS"]="y"
    # Overlay filesystem (Docker storage driver)
    ["CONFIG_OVERLAY_FS"]="y"
)

for key in "${!EXTRA_CONFIGS[@]}"; do
    val="${EXTRA_CONFIGS[$key]}"
    # Remove any existing line (set or not set)
    sed -i "/^${key}[= ]/d; /^# ${key} is not set/d" .config
    if [ "$val" = "n" ]; then
        echo "# ${key} is not set" >> .config
    else
        echo "${key}=${val}" >> .config
    fi
done

# Resolve config dependencies
make olddefconfig 2>&1 | tail -5

# Verify binder is enabled
if grep -q "^CONFIG_ANDROID_BINDER_IPC=y" .config; then
    echo "==> CONFIG_ANDROID_BINDER_IPC=y aktiviert."
else
    echo "FEHLER: Konnte CONFIG_ANDROID_BINDER_IPC nicht aktivieren."
    echo "        Kernel-Version ist möglicherweise nicht kompatibel."
    exit 1
fi

# ── 5. Build the kernel ─────────────────────────────────────────────────────
echo "==> Baue Kernel ($(nproc) CPU-Kerne) ..."
make -j"$(nproc)" bzImage 2>&1 | tail -5

BZIMAGE_PATH="$(pwd)/arch/x86/boot/bzImage"
if [ ! -f "$BZIMAGE_PATH" ]; then
    echo "FEHLER: bzImage wurde nicht erstellt."
    exit 1
fi

echo "==> Kernel erfolgreich gebaut: $BZIMAGE_PATH"

# ── 6. Install kernel and configure .wslconfig ──────────────────────────────
if [ -n "$WIN_PROFILE" ]; then
    DEST="${WIN_PROFILE}${BZIMAGE_NAME}"
    cp "$BZIMAGE_PATH" "$DEST"
    echo "==> Kernel kopiert nach: $DEST"

    # Convert Linux path to Windows path for .wslconfig
    # /mnt/c/Users/foo/bzImage-wsl2-binder -> C:\\Users\\foo\\bzImage-wsl2-binder
    WIN_PATH=$(echo "$DEST" | sed 's|^/mnt/\([a-z]\)/|\U\1:\\\\|; s|/|\\\\|g')

    WSLCONFIG="${WIN_PROFILE}.wslconfig"

    if [ -f "$WSLCONFIG" ]; then
        # Backup existing config
        cp "$WSLCONFIG" "${WSLCONFIG}.bak"
        if grep -q "^\[wsl2\]" "$WSLCONFIG"; then
            # Replace or add kernel line under [wsl2]
            if grep -q "^kernel=" "$WSLCONFIG"; then
                sed -i "s|^kernel=.*|kernel=${WIN_PATH}|" "$WSLCONFIG"
            else
                sed -i "/^\[wsl2\]/a kernel=${WIN_PATH}" "$WSLCONFIG"
            fi
        else
            # Append [wsl2] section
            printf '\n[wsl2]\nkernel=%s\n' "$WIN_PATH" >> "$WSLCONFIG"
        fi
    else
        printf '[wsl2]\nkernel=%s\n' "$WIN_PATH" > "$WSLCONFIG"
    fi

    echo "==> .wslconfig aktualisiert: $WSLCONFIG"
    echo ""
    echo "=============================================="
    echo "  KERNEL ERFOLGREICH GEBAUT UND INSTALLIERT!"
    echo "=============================================="
    echo ""
    echo "  Nächster Schritt: WSL neu starten!"
    echo "  Öffne PowerShell/CMD und führe aus:"
    echo ""
    echo "    wsl --shutdown"
    echo ""
    echo "  Dann WSL wieder öffnen und install.sh erneut starten."
    echo ""
else
    echo "==> Kernel gebaut: $BZIMAGE_PATH"
    echo ""
    echo "  Manuell installieren:"
    echo "  1) Kopiere $BZIMAGE_PATH nach Windows (z.B. C:\\bzImage-wsl2-binder)"
    echo "  2) Erstelle/bearbeite %USERPROFILE%\\.wslconfig:"
    echo "     [wsl2]"
    echo "     kernel=C:\\\\bzImage-wsl2-binder"
    echo "  3) wsl --shutdown"
    echo ""
fi
