#!/usr/bin/env bash
# Regenerate everything derived from the Onshape export in ./cad:
#   STL/Individual + STL/Plates (print-oriented enclosure), docs/media stills and assembly videos.
# Needs: python3 + numpy + pillow, node + playwright (Chromium/Chrome with WebGL), ffmpeg.
set -euo pipefail
cd "$(dirname "$0")"
python3 prep.py data
python3 fasteners.py
python3 orient.py front_body=Front_body_Native rear_lid=Rear_lid_Native
python3 plates.py ../../STL/Plates
python3 anim.py
for s in $(python3 -c "import json;print(' '.join(x['name'] for x in json.load(open('steps.json'))))"); do
  node render.mjs "shots_$s.json" "frames/$s" 1280 720
done
python3 encode.py frames ../../docs/media/assembly
python3 stills.py && node render.mjs stills.json stills 1600 1000
cp stills/*.png ../../docs/media/
