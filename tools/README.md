# IMX477 recorder for Raspberry Pi Zero 2 W

`record_imx477.py` records the Arducam 12.3MP IMX477 (same sensor as the Pi HQ
camera) at **2028x1520 @ 40 fps**, the sensor's 2x2-binned full field-of-view
mode, with settings aimed at visual SLAM:

| Setting | Default | Why |
| --- | --- | --- |
| Codec | hardware MJPEG, 50 Mbit/s (`--codec h264` for comparison) | each JPEG is an independent intra frame; the H.264 encoder stops at 1920 px wide |
| Exposure | auto, shutter capped at 4 ms, gain at most 8x | limits motion blur; once the shutter reaches the cap, AE raises gain instead |
| Frame rate | fixed 40 fps (`FrameDurationLimits`) | constant frame interval |
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
4. Use a fast SD card (A1/U3). At 50 Mbit/s the recorder writes about 6 MB/s.

## Recording

```bash
python3 record_imx477.py                          # until Ctrl-C
python3 record_imx477.py --duration 120
python3 record_imx477.py --max-exposure-us 2000 --max-gain 12   # fast motion
python3 record_imx477.py --shutter-us 3000 --gain 4             # full manual
```

Run `--help` to see every option. Each run creates `recordings/<timestamp>_<codec>/` containing:

- `video.mjpeg` or `video.h264`: the raw stream. To put it in a container without
  re-encoding, run `ffmpeg -framerate 40 -i video.mjpeg -c copy video.mkv`.
- `frames.csv`: one row per frame in the stream, with the libcamera
  `SensorTimestamp` (ns, CLOCK_BOOTTIME), exposure, gain, lux and colour temperature.
  Use these timestamps for SLAM rather than an assumed fixed 25 ms.
- `session.json`: settings, camera properties, the offset from the sensor clock to
  wall-clock time, plus the measured fps, dropped-frame count, file size and actual bitrate.

## Comparing MJPEG and H.264

```bash
python3 record_imx477.py --duration 60                                 # 2028x1520 MJPEG
python3 record_imx477.py --duration 60 --codec h264                    # 1440x1080 H.264, 15 Mbit/s
python3 record_imx477.py --duration 60 --codec h264 --bitrate 25e6 --iperiod 10
```

Both codecs use the same 2028x1520 @ 40 fps sensor mode. For H.264 the ISP scales the
image to 1440x1080, which keeps 4:3 and the full field of view, because the hardware H.264
encoder can't take frames above about 1920x1080. H.264 bitrate is capped at 25 Mbit/s.
`--iperiod` is the number of frames between keyframes: lower values hold up better under
fast motion but make files bigger.

To compare fairly, record the same motion with each codec (for example, the same handheld
sweep), run your SLAM pipeline on both, and compare tracking loss and trajectory error
alongside `file_bytes`, `actual_mbit_per_s` and `dropped_frames` from `session.json`.
To get frames out for SLAM, run:

```bash
ffmpeg -i video.mjpeg -fps_mode passthrough -c copy frame_%06d.jpg   # lossless copy
ffmpeg -i video.h264 -fps_mode passthrough frame_%06d.png             # full decode
```

`-fps_mode passthrough` stops ffmpeg from duplicating or dropping frames, so
`frame_000001` matches row 0 of `frames.csv`.

The 1440x1080 H.264 frames need their own camera calibration, because scaling changes the
intrinsics.

## Notes

- Focus and aperture on the C/CS lens are manual. Set them before you record, then
  lock the rings.
- Under mains-powered lighting, short exposures can pick up flicker banding. A shutter
  that is a multiple of 10 ms (50 Hz mains) or 8.33 ms (60 Hz) avoids it, but neither
  fits under the 25 ms frame period without blur. Prefer DC or LED lighting.
- If `dropped_frames` is non-zero, lower `--bitrate` or use a faster card.
- Calibrate intrinsics at this exact resolution: the 2028x1520 mode is binned, so its
  intrinsics differ from those of the full 4056x3040 mode.
