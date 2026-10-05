# IMX477 recorder for Raspberry Pi Zero 2 W

`record_imx477.py` records the Arducam 12.3MP IMX477 (same sensor as the Pi HQ
camera) in its **2028x1520** 2x2-binned full field-of-view mode, at 30 fps by
default, with settings aimed at visual SLAM:

| Setting | Default | Why |
| --- | --- | --- |
| Codec | hardware MJPEG, 50 Mbit/s (`--codec h264` for comparison) | each JPEG is an independent intra frame; the H.264 encoder stops at 1920 px wide |
| Exposure | auto, shutter capped at 4 ms, gain at most 8x | limits motion blur; once the shutter reaches the cap, AE raises gain instead |
| Frame rate | fixed 30 fps (`FrameDurationLimits`) | constant frame interval; MJPEG cannot sustain 40 fps |
| Sharpness | 3.0 (ISP default is 1.0) | crisper corners and edges for feature tracking |
| Denoise | off | spatial denoise smears fine texture |
| White balance | auto for 2 s, then locked | colour stays constant from frame to frame |

## Pi setup

1. Use Raspberry Pi OS Bookworm (or later), Lite is fine. The Zero 2 W needs the
   22-pin to 15-pin camera ribbon.
2. Edit `/boot/firmware/config.txt`:

   ```ini
   camera_auto_detect=0
   dtoverlay=imx477
   # 2028x1520 buffers are ~4.6 MB each; the default CMA pool is too small
   dtoverlay=vc4-kms-v3d,cma-128
   ```

   Reboot, then check that `rpicam-hello --list-cameras` lists `2028x1520 [40.xx fps]`.
3. `sudo apt install -y python3-picamera2 ffmpeg`
4. Use a fast SD card (A1/U3). MJPEG writes about 3.5 MB/s.

## Recording

Stop the collector first (`sudo systemctl stop lumi-collector`); only one process can own the camera.

```bash
python3 record_imx477.py                          # until Ctrl-C
python3 record_imx477.py --duration 120
python3 record_imx477.py --max-exposure-us 2000 --max-gain 12   # fast motion
python3 record_imx477.py --shutter-us 3000 --gain 4             # full manual
python3 record_imx477.py --codec h264 --label h264_tool         # name the session
```

Run `--help` to see every option. Each run creates `recordings/<timestamp>_<codec>[_<label>]/` containing:

- `video.mjpeg` or `video.h264`: the raw stream. MJPEG is 1920x1520: the hardware JPEG
  encoder keeps only the first 1920 columns of the 2028-wide frame (the lens circle fits).
- `frames.csv`: one row per frame **in the stream**, with the libcamera `SensorTimestamp`
  (ns, CLOCK_BOOTTIME), size in bytes, keyframe flag, exposure, gain, lux and colour
  temperature. Rows come from the frames written to the file, so they stay one-to-one with
  the stream even when the encoder takes a frame and never emits it. Use these timestamps
  for SLAM rather than an assumed fixed frame period.
- `refs.npz`: `--refs` (default 8) uncompressed Y planes of the frames handed to the
  encoder, spread over the take, with their sensor timestamps (`sensor_ns`, `y`). Each
  matches a row of `frames.csv`, so the compression loss of exactly that frame can be measured.
- `session.json`: settings, camera properties, the offset from the sensor clock to
  wall-clock time, plus the measured fps, dropped frames, frames lost inside the encoder,
  file size and actual bitrate.

`--rest N` adds a REST/MOVE cue to the progress line: keep the camera still for the first
N seconds (for brightness and noise), then move.

## Comparing settings

`sweep.sh` runs a round of takes back to back on the Pi. It stops `lumi-collector` and
restarts it when it exits. Each take rests for 3 s, then needs the same handheld motion
(3 slow and 3 fast passes over the workspace):

```bash
./sweep.sh                         # every take of round 1
./sweep.sh round2                  # round 2, around round 1's best take
./sweep.sh h264_tool mjpeg_tool    # only some
DURATION=30 ./sweep.sh
```

Then copy the sessions to the Mac and score them there (solarian conda env):

```bash
scp -r pi@solarian-lumi.local:solarian-lumi-collector/tools/recordings/'*' tools/recordings/
python tools/score_recordings.py tools/recordings/20261003_17*
```

`score_recordings.py` prints one row per take and writes `summary.csv`, `contact_sheet.png`,
`bits_vs_motion.png` and a real-speed `video.mp4` per session (frames placed at their sensor
timestamps, so drops show as held frames instead of speeding playback up). Columns:

- integrity: frames in the stream against rows in `frames.csv`, drops, fps, bitrate;
- compression loss against `refs.npz` (PSNR and fine detail kept at the 1440 grid), grouped
  by motion speed;
- bytes per frame against motion speed, as a share of the nominal per-frame budget;
- camera-fixed ORB matches while the camera moves (fake features from codec artifacts) and
  the jump in detail at H.264 keyframes;
- brightness, clipping and temporal noise from the resting segment;
- finger tag detection (ids 0 and 1, `DICT_4X4_50`) at the 1440 grid and at native size;
- ORB features and frame-to-frame inlier matches at 960x720, the size SLAM runs at.

The 1440x1080 H.264 and 1920x1520 MJPEG frames need their own camera calibration, because
scaling and cropping change the intrinsics.

## Notes

- Focus and aperture on the C/CS lens are manual. Set them before you record, then
  lock the rings.
- Under mains-powered lighting, short exposures can pick up flicker banding. A shutter
  that is a multiple of 10 ms (50 Hz mains) or 8.33 ms (60 Hz) avoids it, but neither
  fits under the frame period without blur. Prefer DC or LED lighting.
- At 40 fps MJPEG loses about 16% of its frames (the JPEG block tops out near 34 fps at
  1920x1520), and 1440x1080 H.264 uses the encoder's whole 1080p30 budget.
- `--denoise high_quality` cannot keep up with video: about 22 fps at 30 fps requested.
- The H.264 encoder never goes below QP 20 (the driver's `h264_minimum_qp_value`), so with
  a clean image it settles near 10 Mbit/s whatever `--bitrate` asks for. Only a noisy image
  (denoise off at high gain) reaches the 25 Mbit/s ceiling.
- If `dropped_frames` is non-zero, lower `--bitrate` or use a faster card.
- Calibrate intrinsics at this exact resolution: the 2028x1520 mode is binned, so its
  intrinsics differ from those of the full 4056x3040 mode.
