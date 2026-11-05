#!/usr/bin/env bash
# Install AdHush on a Raspberry Pi (Raspberry Pi OS Bookworm or later) as an
# always-on service that starts at boot and restarts itself if it crashes.
#
#   git clone https://github.com/dmking0728/adhush.git && cd adhush
#   sudo ./scripts/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo ./scripts/install.sh" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-pi}"
APP_DIR=/opt/adhush
CONF_DIR=/etc/adhush

echo "==> Installing system packages (OpenCV, Tesseract OCR)"
# apt's OpenCV is prebuilt for the Pi; building it with pip on a Zero takes hours.
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip python3-opencv python3-numpy python3-yaml \
  tesseract-ocr tesseract-ocr-eng v4l-utils

echo "==> Creating virtualenv in $APP_DIR"
mkdir -p "$APP_DIR"
python3 -m venv --system-site-packages "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/venv/bin/pip" install "$REPO_DIR[samsung]"

echo "==> Config in $CONF_DIR"
mkdir -p "$CONF_DIR"
if [[ ! -f "$CONF_DIR/config.yaml" ]]; then
  cp "$REPO_DIR/config.example.yaml" "$CONF_DIR/config.yaml"
  echo "    Created $CONF_DIR/config.yaml - edit it to set your TV's IP address."
else
  echo "    Keeping existing $CONF_DIR/config.yaml"
fi
chown -R "$RUN_USER" "$CONF_DIR"
usermod -aG video "$RUN_USER"

echo "==> Installing systemd service (runs as $RUN_USER)"
sed "s/__USER__/$RUN_USER/" "$REPO_DIR/scripts/adhush.service" > /etc/systemd/system/adhush.service
systemctl daemon-reload
systemctl enable adhush.service
systemctl restart adhush.service

IP="$(hostname -I | awk '{print $1}')"
cat <<EOF

AdHush is installed and running.

Next:
  1. sudo nano $CONF_DIR/config.yaml     # set tv.host and tv.backend: samsung
  2. sudo -u $RUN_USER $APP_DIR/venv/bin/adhush -c $CONF_DIR/config.yaml test-tv
     (press Allow on the TV if it asks)
  3. sudo systemctl restart adhush
  4. Open http://$IP:8080 on your phone and draw a box around the "Ad" badge.

Logs:   journalctl -u adhush -f
EOF
