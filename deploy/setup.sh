#!/bin/bash
# Raspberry Pi OS Lite 64-bit (Trixie) on a Pi Zero 2 W.
# Flash with Raspberry Pi Imager: enable SSH, set user `pi`, configure Wi-Fi.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root: sudo bash deploy/setup.sh" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
install -d -o pi -g pi /home/pi/lumi /home/pi/lumi/raw_videos /home/pi/lumi/work

apt-get update
apt-get install -y --no-install-recommends \
  python3-venv python3-pip python3-libcamera python3-picamera2 \
  python3-numpy python3-pil python3-spidev python3-lgpio \
  ffmpeg libimage-exiftool-perl i2c-tools git

if [[ -f /boot/firmware/config.txt ]]; then
  CFG=/boot/firmware/config.txt
else
  CFG=/boot/config.txt
fi
if ! grep -q 'dtoverlay=imx477' "$CFG"; then
  cat "$SRC/deploy/config.txt.snippet" >> "$CFG"
fi

sudo -u pi python3 -m venv --system-site-packages /home/pi/lumi/venv
sudo -u pi /home/pi/lumi/venv/bin/pip install -U pip
sudo -u pi /home/pi/lumi/venv/bin/pip install -e "$SRC[pi]"

install -m 0644 -o pi -g pi "$SRC/config/collector.yaml" /home/pi/lumi/collector.yaml
install -m 0644 "$SRC/deploy/lumi-collector.service" /etc/systemd/system/lumi-collector.service

systemctl daemon-reload
systemctl enable lumi-collector.service

echo
echo "Reboot, then open http://<pi-ip>:8080 to sign in to PrimaryKey."
echo "KEY1 record, KEY2 delete last, KEY3 settings / hold 2s shutdown."
