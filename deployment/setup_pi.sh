#!/usr/bin/env bash
# Automated Setup & Deployment Script for Insteon Network Management Bridge on Raspberry Pi 4
set -euo pipefail

INSTALL_DIR="/opt/insteon-bridge"
CACHE_DIR="/var/lib/insteon-bridge/cache"
SERVICE_FILE="/etc/systemd/system/insteon-bridge.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=================================================================="
echo " Insteon Network Management Bridge - Raspberry Pi 4 Installer"
echo "=================================================================="

sudo mkdir -p "$INSTALL_DIR" "$CACHE_DIR"
sudo chown -R "${SUDO_USER:-$USER}:${SUDO_USER:-$USER}" "$INSTALL_DIR" "$CACHE_DIR"

echo "[1/5] Copying backend application files to $INSTALL_DIR..."
cp -r "$REPO_ROOT/backend/app" "$INSTALL_DIR/"
cp "$REPO_ROOT/backend/requirements.txt" "$INSTALL_DIR/"
if [ -f "$REPO_ROOT/network_snapshot.json" ]; then
    cp "$REPO_ROOT/network_snapshot.json" "$INSTALL_DIR/network_snapshot.json"
fi
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$REPO_ROOT/backend/.env.example" "$INSTALL_DIR/.env"
    echo "      Created $INSTALL_DIR/.env from template (please configure OPENHAB_TOKEN and INSTEON_PORT)."
fi

echo "[2/5] Creating Python virtual environment in $INSTALL_DIR/.venv..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "[3/5] Ensuring user '${SUDO_USER:-$USER}' is in the 'dialout' group for /dev/ttyUSB0 access..."
sudo usermod -a -G dialout "${SUDO_USER:-$USER}" || true

echo "[4/5] Installing systemd service unit ($SERVICE_FILE)..."
sudo cp "$SCRIPT_DIR/insteon-bridge.service" "$SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable insteon-bridge.service

echo "[5/5] Starting insteon-bridge.service..."
sudo systemctl restart insteon-bridge.service
sudo systemctl status insteon-bridge.service --no-pager

echo "=================================================================="
echo " Deployment Complete! Point Windows GUI Backend URL to:"
echo "   http://$(hostname -I | awk '{print $1}'):8000"
echo "=================================================================="
