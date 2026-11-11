#!/usr/bin/env bash
# Turnkey Installer for Insteon Network Management Bridge on Raspberry Pi / Linux Host
set -euo pipefail

INSTALL_DIR="/opt/insteon-bridge"
CACHE_DIR="/var/lib/insteon-bridge/cache"
SERVICE_FILE="/etc/systemd/system/insteon-bridge.service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/backend" ] && [ -d "$SCRIPT_DIR/deployment" ]; then
    REPO_ROOT="$SCRIPT_DIR"
    DEPLOY_DIR="$SCRIPT_DIR/deployment"
else
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    DEPLOY_DIR="$SCRIPT_DIR"
fi

CLEAN_DB=false
SEED_SNAPSHOT=false
NON_INTERACTIVE=false
OH_URL=""
OH_TOKEN=""
OH_THING=""
PLM_PORT=""

for arg in "$@"; do
    case "$arg" in
        --clean-db)
            CLEAN_DB=true
            ;;
        --seed-snapshot)
            SEED_SNAPSHOT=true
            ;;
        --non-interactive)
            NON_INTERACTIVE=true
            ;;
        --openhab-url=*)
            OH_URL="${arg#*=}"
            ;;
        --openhab-token=*)
            OH_TOKEN="${arg#*=}"
            ;;
        --openhab-thing=*)
            OH_THING="${arg#*=}"
            ;;
        --insteon-port=*)
            PLM_PORT="${arg#*=}"
            ;;
    esac
done

echo "=================================================================="
echo " Insteon Network Management Bridge - Raspberry Pi Installer v2.0"
echo "=================================================================="

TARGET_USER="${SUDO_USER:-$USER}"
if [ "$TARGET_USER" = "root" ] && id "openhabian" >/dev/null 2>&1; then
    TARGET_USER="openhabian"
elif [ "$TARGET_USER" = "root" ] && id "pi" >/dev/null 2>&1; then
    TARGET_USER="pi"
fi

echo "[1/6] Checking system prerequisites (Python 3 & venv)..."
if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "      Installing python3-venv and python3-pip via apt..."
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip
fi

sudo mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/snapshots" "$CACHE_DIR"
sudo chown -R "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR" "$CACHE_DIR"

echo "[2/6] Installing backend application files to $INSTALL_DIR..."
rm -rf "$INSTALL_DIR/app" "$INSTALL_DIR/scripts"
cp -r "$REPO_ROOT/backend/app" "$INSTALL_DIR/"
if [ -d "$REPO_ROOT/backend/scripts" ]; then
    cp -r "$REPO_ROOT/backend/scripts" "$INSTALL_DIR/"
fi
cp "$REPO_ROOT/backend/requirements.txt" "$INSTALL_DIR/"

if [ "$CLEAN_DB" = true ]; then
    echo "      [--clean-db] Clearing cached network_snapshot.json and pyinsteon cache..."
    rm -f "$INSTALL_DIR/network_snapshot.json" "$INSTALL_DIR/backend/network_snapshot.json"
    rm -rf "$CACHE_DIR"/* "$INSTALL_DIR/insteon_cache"/* 2>/dev/null || true
elif [ "$SEED_SNAPSHOT" = true ] && [ -f "$REPO_ROOT/network_snapshot.json" ]; then
    echo "      [--seed-snapshot] Copying initial network_snapshot.json to $INSTALL_DIR..."
    cp "$REPO_ROOT/network_snapshot.json" "$INSTALL_DIR/network_snapshot.json"
fi

echo "[3/6] Configuring environment ($INSTALL_DIR/.env)..."
NEW_ENV=false
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$REPO_ROOT/backend/.env.example" "$INSTALL_DIR/.env"
    sed -i 's/\r$//' "$INSTALL_DIR/.env"
    NEW_ENV=true
    echo "      Created new $INSTALL_DIR/.env from template."
else
    echo "      Existing $INSTALL_DIR/.env found (preserving existing configuration)."
fi

# Interactive prompts if creating .env for the first time in an interactive terminal
if [ "$NEW_ENV" = true ] && [ "$NON_INTERACTIVE" = false ] && [ -t 0 ]; then
    echo ""
    echo "  --- Optional Quick Configuration (Press Enter to keep default) ---"
    if [ -z "$OH_URL" ]; then
        read -r -p "  openHAB URL [http://localhost:8080]: " INPUT_OH_URL || true
        OH_URL="${INPUT_OH_URL:-http://localhost:8080}"
    fi
    if [ -z "$OH_TOKEN" ]; then
        read -r -p "  openHAB API Token (oh.insteonbridge....) [configure later]: " INPUT_OH_TOKEN || true
        OH_TOKEN="${INPUT_OH_TOKEN:-}"
    fi
    if [ -z "$OH_THING" ]; then
        read -r -p "  openHAB Insteon Thing UID [insteon:network:local]: " INPUT_OH_THING || true
        OH_THING="${INPUT_OH_THING:-insteon:network:local}"
    fi
    if [ -z "$PLM_PORT" ]; then
        read -r -p "  USB PLM Serial Port [/dev/ttyUSB0]: " INPUT_PLM_PORT || true
        PLM_PORT="${INPUT_PLM_PORT:-/dev/ttyUSB0}"
    fi
    echo ""
fi

if [ -n "$OH_URL" ]; then
    sed -i "s|^OPENHAB_URL=.*|OPENHAB_URL=$OH_URL|" "$INSTALL_DIR/.env"
fi
if [ -n "$OH_TOKEN" ]; then
    sed -i "s|^OPENHAB_TOKEN=.*|OPENHAB_TOKEN=$OH_TOKEN|" "$INSTALL_DIR/.env"
fi
if [ -n "$OH_THING" ]; then
    sed -i "s|^OPENHAB_THING_UID=.*|OPENHAB_THING_UID=$OH_THING|" "$INSTALL_DIR/.env"
fi
if [ -n "$PLM_PORT" ]; then
    sed -i "s|^INSTEON_PORT=.*|INSTEON_PORT=$PLM_PORT|" "$INSTALL_DIR/.env"
fi

sudo chown "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR/.env"

echo "[4/6] Creating/updating Python virtual environment in $INSTALL_DIR/.venv..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
sudo chown -R "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR/.venv"

echo "[5/6] Ensuring user '$TARGET_USER' is in the 'dialout' group for serial PLM access..."
sudo usermod -a -G dialout "$TARGET_USER" || true

echo "[6/6] Installing and starting systemd service ($SERVICE_FILE)..."
sed -e 's/\r$//' -e "s/^User=.*/User=$TARGET_USER/" -e "s/^Group=.*/Group=$TARGET_USER/" "$DEPLOY_DIR/insteon-bridge.service" | sudo tee "$SERVICE_FILE" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable insteon-bridge.service
sudo systemctl restart insteon-bridge.service
sleep 2
sudo systemctl status insteon-bridge.service --no-pager || true

PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -z "$PI_IP" ]; then
    PI_IP="<raspberry-pi-ip>"
fi

echo "=================================================================="
echo " Insteon Bridge Deployment Complete!"
echo "   Service Status : $(systemctl is-active insteon-bridge.service)"
echo "   Config File    : $INSTALL_DIR/.env"
echo "   Bridge URL     : http://${PI_IP}:8000"
echo "=================================================================="
echo " Enter http://${PI_IP}:8000 into the Windows Insteon Network Studio"
echo " top-right 'Bridge' box and click 'Test Link'!"
echo "=================================================================="
