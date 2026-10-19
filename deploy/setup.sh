#!/bin/bash
# Raspberry Pi OS Lite 64-bit (Trixie) on a Pi Zero 2 W.
# Flash with Raspberry Pi Imager: enable SSH, set user `pi`, configure Wi-Fi.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root: sudo bash deploy/setup.sh" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
install -d -o pi -g pi /home/pi/lumi /home/pi/lumi/datasets /home/pi/lumi/work

apt-get update
apt-get install -y --no-install-recommends \
  python3-venv python3-pip python3-libcamera python3-picamera2 rpicam-apps \
  python3-numpy python3-pil python3-spidev python3-lgpio \
  ffmpeg i2c-tools git samba

# ionice -c 3 on the save process only takes effect under BFQ.
echo 'ACTION=="add|change", KERNEL=="mmcblk[0-9]", ATTR{queue/scheduler}="bfq"' \
  > /etc/udev/rules.d/60-lumi-bfq.rules
udevadm control --reload
udevadm trigger --action=change --subsystem-match=block

# A take writes to the SD card while earlier ones save to it. The kernel's default ratios let
# tens of MB wait unwritten; reclaiming memory then waits on the card and, at 15 Mbit/s, stalls
# the camera for most of a second.
cat > /etc/sysctl.d/60-lumi-writeback.conf <<'EOF'
vm.dirty_background_bytes = 4194304
vm.dirty_bytes = 16777216
EOF
sysctl -p /etc/sysctl.d/60-lumi-writeback.conf

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
# The KMS overlay reserves 256 MB of the 415 for contiguous buffers by default, and the kernel only
# lends that out reluctantly, so the save swapped the collector out with 80 MB "free". The camera
# needs about 60 MB of it; the LCD is on SPI, so nothing else does. Applies at the next boot.
sed -i 's/^dtoverlay=vc4-kms-v3d$/dtoverlay=vc4-kms-v3d,cma-128/' "$CFG"

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
echo "Reboot, then open http://<pi-ip>:8080 to sign in to Solarian Base."
echo "In Finder: Go → Connect to Server → smb://$(hostname).local"
echo "Home lists datasets: Right creates one, Push opens it, KEY1 records inside it."
echo "KEY3 settings / back, hold 2s shutdown."
