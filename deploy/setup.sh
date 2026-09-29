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
  python3-venv python3-pip python3-libcamera python3-picamera2 rpicam-apps \
  python3-numpy python3-pil python3-spidev python3-lgpio \
  ffmpeg libimage-exiftool-perl i2c-tools git samba

if [[ -f /boot/firmware/config.txt ]]; then
  CFG=/boot/firmware/config.txt
else
  CFG=/boot/config.txt
fi
# imx477 is often already present from camera bring-up. Still add I2C/SPI.
while IFS= read -r line; do
  [[ -z "$line" || "$line" =~ ^# ]] && continue
  grep -qxF "$line" "$CFG" || echo "$line" >> "$CFG"
done < "$SRC/deploy/config.txt.snippet"

# dtparam=i2c_arm=on enables the controller. /dev/i2c-1 needs this module.
echo i2c-dev > /etc/modules-load.d/i2c-dev.conf

sudo -u pi python3 -m venv --system-site-packages /home/pi/lumi/venv
sudo -u pi /home/pi/lumi/venv/bin/pip install -U pip
sudo -u pi /home/pi/lumi/venv/bin/pip install -e "$SRC[pi]"

install -m 0644 -o pi -g pi "$SRC/config/collector.yaml" /home/pi/lumi/collector.yaml
install -m 0644 "$SRC/deploy/lumi-collector.service" /etc/systemd/system/lumi-collector.service
install -m 0644 "$SRC/deploy/lumi-splash.service" /etc/systemd/system/lumi-splash.service

systemctl daemon-reload
systemctl enable lumi-splash.service lumi-collector.service

# Finder connects with smb://, not SSH. The password is separate from the login one.
install -m 0644 "$SRC/deploy/samba-lumi.conf" /etc/samba/lumi.conf
grep -qxF "include = /etc/samba/lumi.conf" /etc/samba/smb.conf \
  || echo "include = /etc/samba/lumi.conf" >> /etc/samba/smb.conf
systemctl enable --now smbd

echo
echo "Set the password Finder uses for smb://$(hostname).local (user pi)."
smbpasswd -a pi

echo
echo "Reboot, then open http://<pi-ip>:8080 to sign in to PrimaryKey."
echo "In Finder: Go → Connect to Server → smb://$(hostname).local"
echo "KEY1 record, KEY2 delete last, KEY3 settings / hold 2s shutdown."
