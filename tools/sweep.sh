#!/usr/bin/env bash
# Record a round of capture-setting takes back to back on the Lumi.
#
#   ./sweep.sh                       every take of round 1
#   ./sweep.sh round2                every take of round 2
#   ./sweep.sh h264_tool mjpeg_tool  only these
#   DURATION=30 ./sweep.sh
#
# Each take rests for REST seconds (keep the Lumi still on the table, pointed at the
# workspace), then needs the same handheld motion every time: 3 slow and 3 fast passes.
set -euo pipefail
cd "$(dirname "$0")"

DURATION=${DURATION:-20}
REST=${REST:-3}
TUNING=${TUNING:-/home/pi/imx477_fisheye.json}
# Six buffers, the Picamera2 video default the collector runs with, leave slack for the reference copies.
COMMON=(--fps 30 --duration "$DURATION" --rest "$REST" --refs 8 --buffer-count 6
        --tuning-file "$TUNING" --output recordings)
# The fisheye tuning leaves rpi.sdn at libcamera's defaults (deviation 3.2, strength 0.75). Half of
# both smooths only differences near the noise level, so texture just above it survives.
# Absolute: Picamera2.load_tuning_file looks a bare relative path up in libcamera's own directories.
MILD_TUNING=$(pwd)/recordings/imx477_fisheye_sdnmild.json
mkdir -p recordings
python3 - "$TUNING" "$MILD_TUNING" <<'EOF'
import json, sys
tuning = json.load(open(sys.argv[1]))
sdn = [algo for algo in tuning["algorithms"] if "rpi.sdn" in algo]
if not sdn:
    sys.exit(f"{sys.argv[1]} has no rpi.sdn block")
sdn[0]["rpi.sdn"] = {"deviation": 1.6, "strength": 0.375}
json.dump(tuning, open(sys.argv[2], "w"), indent=2)
EOF

declare -A TAKES=(
  # The collector today: 8 ms cap, its tuning-scaled gain ceiling (16 x 33.3/8 ms), ISP defaults, 10 Mbit/s.
  [h264_collector]="--codec h264 --max-exposure-us 8000 --max-gain 66.7 --sharpness 1 --denoise fast --bitrate 10e6 --awb-lock-after -1"
  [h264_tool]="--codec h264 --max-exposure-us 4000 --max-gain 8 --sharpness 3 --denoise off --bitrate 15e6"
  [mjpeg_tool]="--codec mjpeg --max-exposure-us 4000 --max-gain 8 --sharpness 3 --denoise off"
  [h264_tool25]="--codec h264 --max-exposure-us 4000 --max-gain 8 --sharpness 3 --denoise off --bitrate 25e6"
  [h264_s1]="--codec h264 --max-exposure-us 4000 --max-gain 8 --sharpness 1 --denoise off --bitrate 15e6"
  [mjpeg_s1]="--codec mjpeg --max-exposure-us 4000 --max-gain 8 --sharpness 1 --denoise off"
  [h264_e6g16]="--codec h264 --max-exposure-us 6000 --max-gain 16 --sharpness 3 --denoise off --bitrate 25e6"
  [mjpeg_e6g16]="--codec mjpeg --max-exposure-us 6000 --max-gain 16 --sharpness 3 --denoise off"
  [h264_e6g16fast]="--codec h264 --max-exposure-us 6000 --max-gain 16 --sharpness 3 --denoise fast --bitrate 25e6"
  # Round 2, around h264_e6g16. It takes ~23 Mbit/s, near the encoder's 25 Mbit/s ceiling, so most
  # variants try to keep its texture for fewer bits. The repeat measures how much
  # takes differ by hand motion alone.
  [h264_e6g16_15]="--codec h264 --max-exposure-us 6000 --max-gain 16 --sharpness 3 --denoise off --bitrate 15e6"
  [h264_e6g16_s2]="--codec h264 --max-exposure-us 6000 --max-gain 16 --sharpness 2 --denoise off --bitrate 25e6"
  [h264_e6g12]="--codec h264 --max-exposure-us 6000 --max-gain 12 --sharpness 3 --denoise off --bitrate 25e6"
  [h264_e8g16]="--codec h264 --max-exposure-us 8000 --max-gain 16 --sharpness 3 --denoise off --bitrate 25e6"
  [h264_e6g16_sdnmild]="--codec h264 --max-exposure-us 6000 --max-gain 16 --sharpness 3 --denoise fast --bitrate 25e6 --tuning-file $MILD_TUNING"
  [h264_e6g16_40fps]="--codec h264 --max-exposure-us 6000 --max-gain 16 --sharpness 3 --denoise off --bitrate 25e6 --fps 40 --iperiod 40"
  [h264_e6g16_rep]="--codec h264 --max-exposure-us 6000 --max-gain 16 --sharpness 3 --denoise off --bitrate 25e6"
  # What the collector ships after round 2 (config/collector.yaml).
  [h264_e8g16_15]="--codec h264 --max-exposure-us 8000 --max-gain 16 --sharpness 3 --denoise off --bitrate 15e6"
)
ROUND1=(h264_collector h264_tool mjpeg_tool h264_tool25 h264_s1 mjpeg_s1 h264_e6g16 mjpeg_e6g16 h264_e6g16fast)
ROUND2=(h264_e6g16 h264_e6g16_15 h264_e6g16_s2 h264_e6g12 h264_e8g16 h264_e6g16_sdnmild h264_e6g16_40fps
        h264_e6g16_rep)

takes=()
for arg in "${@:-round1}"; do
  case $arg in
    round1) takes+=("${ROUND1[@]}") ;;
    round2) takes+=("${ROUND2[@]}") ;;
    *) [ -n "${TAKES[$arg]+set}" ] || { echo "unknown take: $arg (known: ${!TAKES[*]})" >&2; exit 2; }
       takes+=("$arg") ;;
  esac
done

sudo -n systemctl stop lumi-collector
trap 'sudo -n systemctl start lumi-collector' EXIT

n=${#takes[@]}
for i in "${!takes[@]}"; do
  take=${takes[$i]}
  echo
  echo "=== $((i + 1))/$n  $take: put the Lumi down on the table, pointed at the workspace"
  for s in 5 4 3 2 1; do printf '\r    starting in %d s ' "$s"; sleep 1; done
  echo
  # The take's flags come last so they override COMMON (argparse keeps the last value).
  # shellcheck disable=SC2086  # the take's flags are meant to split
  LIBCAMERA_LOG_LEVELS='*:WARN' python3 record_imx477.py "${COMMON[@]}" ${TAKES[$take]} --label "$take" \
    || echo "    take $take failed"
done
echo
echo "Done. Sessions are in $(pwd)/recordings"
