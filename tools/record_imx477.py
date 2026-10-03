#!/usr/bin/env python3
"""Record SLAM-friendly video from an IMX477 (Arducam / Pi HQ) on a Pi Zero 2 W.

Runs the 2028x1520 2x2-binned full-field-of-view sensor mode at 40 fps and
encodes in hardware with one of:

* ``--codec mjpeg`` (default): full 2028x1520. Every frame is an independent
  intra frame, so there are no inter-frame motion artifacts.
* ``--codec h264``: the ISP scales to 1440x1080, which keeps 4:3 and the full
  field of view. The hardware H.264 encoder stops at about 1920x1080 (level 4.2),
  so it cannot take 2028x1520. Files are several times smaller.

Auto exposure is kept on but capped: the AGC tuning is rewritten so that the
shutter never exceeds ``--max-exposure-us`` and any extra exposure needed comes
from analogue gain (up to ``--max-gain``). Sharpening is raised, denoise is
turned off by default, and AWB is locked after warm-up for photometric
consistency between frames.

Each session directory contains:

* ``video.mjpeg`` or ``video.h264`` - raw elementary stream
* ``frames.csv``   - one row per encoded frame: sensor timestamp, exposure, gain
* ``session.json`` - settings, camera properties, clock offsets

Usage::

    python3 record_imx477.py                     # record until Ctrl-C
    python3 record_imx477.py --duration 60       # 60 seconds
    python3 record_imx477.py --codec h264 --duration 60
    python3 record_imx477.py --max-exposure-us 2000 --max-gain 12

Requires Raspberry Pi OS (Bookworm or later) with ``python3-picamera2``.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

SENSOR_MODE = (2028, 1520)
SENSOR_BIT_DEPTH = 12
SENSOR_MODE_MAX_FPS = 40.0
MIN_SHUTTER_US = 100

CODECS = {
    "mjpeg": {"size": SENSOR_MODE, "bitrate": 50e6, "ext": "mjpeg"},
    "h264": {"size": (1440, 1080), "bitrate": 15e6, "ext": "h264"},
}
H264_MAX_BITRATE = 25e6

_SENSOR_CLOCK = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)


def cap_agc_exposure(tuning: dict, max_exposure_us: int, max_gain: float) -> int:
    """Rewrite every AGC exposure mode so the shutter never exceeds the cap.

    The AGC walks the ``shutter``/``gain`` lists stage by stage. With
    ``shutter=[min, cap, cap]`` and ``gain=[1, 1, max_gain]`` it first lengthens
    the shutter up to the cap at unity gain, then raises gain to ``max_gain``
    while holding the shutter at the cap. Beyond that the image underexposes
    instead of blurring.

    Handles both the flat (``rpi.agc.exposure_modes``) and multi-channel
    (``rpi.agc.channels[i].exposure_modes``) tuning layouts. Returns the number
    of exposure modes rewritten.
    """
    if max_exposure_us < MIN_SHUTTER_US:
        raise ValueError(f"max exposure must be >= {MIN_SHUTTER_US} us")
    if max_gain < 1.0:
        raise ValueError("max gain must be >= 1.0")

    agc = None
    for algo in tuning.get("algorithms", []):
        if "rpi.agc" in algo:
            agc = algo["rpi.agc"]
            break
    if agc is None:
        raise ValueError("tuning file has no rpi.agc section")

    channels = agc.get("channels", [agc])
    shutter = [MIN_SHUTTER_US, int(max_exposure_us), int(max_exposure_us)]
    gain = [1.0, 1.0, float(max_gain)]
    rewritten = 0
    for channel in channels:
        for mode in channel.get("exposure_modes", {}).values():
            mode["shutter"] = list(shutter)
            mode["gain"] = list(gain)
            rewritten += 1
    if rewritten == 0:
        raise ValueError("rpi.agc has no exposure_modes to cap")
    return rewritten


def frame_stats(timestamps_ns: list[int], fps: float) -> dict:
    """Summarize frame timing; a gap over 1.5 frame periods counts as drops."""
    n = len(timestamps_ns)
    if n < 2:
        return {"frames": n, "measured_fps": None, "dropped_frames": 0, "max_gap_ms": None}
    period_ns = 1e9 / fps
    dropped = 0
    max_gap = 0
    for prev, cur in zip(timestamps_ns, timestamps_ns[1:]):
        gap = cur - prev
        max_gap = max(max_gap, gap)
        if gap > 1.5 * period_ns:
            dropped += round(gap / period_ns) - 1
    span = timestamps_ns[-1] - timestamps_ns[0]
    return {
        "frames": n,
        "measured_fps": (n - 1) * 1e9 / span if span > 0 else None,
        "dropped_frames": dropped,
        "max_gap_ms": max_gap / 1e6,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output", type=Path, default=Path("recordings"), help="parent directory for sessions")
    p.add_argument("--duration", type=float, default=0.0, help="seconds to record; 0 = until Ctrl-C")
    p.add_argument("--camera", type=int, default=0, help="libcamera camera index")
    p.add_argument("--fps", type=float, default=SENSOR_MODE_MAX_FPS)
    p.add_argument("--max-exposure-us", type=int, default=4000,
                   help="auto-exposure shutter cap in microseconds (motion blur limit)")
    p.add_argument("--max-gain", type=float, default=8.0, help="auto-exposure analogue gain cap")
    p.add_argument("--shutter-us", type=int, default=None,
                   help="fixed shutter in microseconds instead of capped auto exposure")
    p.add_argument("--gain", type=float, default=None, help="fixed analogue gain (with --shutter-us: full manual)")
    p.add_argument("--sharpness", type=float, default=3.0,
                   help="libcamera sharpness 0-16, default ISP value is 1; above ~4 halos create false corners")
    p.add_argument("--denoise", choices=["off", "fast", "high_quality"], default="off")
    p.add_argument("--awb-lock-after", type=float, default=2.0,
                   help="seconds of auto white balance before locking colour gains; negative = never lock")
    p.add_argument("--codec", choices=sorted(CODECS), default="mjpeg",
                   help="mjpeg: 2028x1520; h264: scaled to 1440x1080 (hardware limit)")
    p.add_argument("--bitrate", type=float, default=None,
                   help="bits/s; default 50e6 for mjpeg, 15e6 for h264")
    p.add_argument("--iperiod", type=int, default=40,
                   help="h264 only: frames between keyframes (40 = one per second at 40 fps)")
    p.add_argument("--buffer-count", type=int, default=4,
                   help="camera buffers; the Zero 2 W's 512 MB leaves little CMA")
    p.add_argument("--tuning-file", default="imx477.json", help="libcamera tuning file to start from")
    args = p.parse_args(argv)

    if not 0 < args.fps <= SENSOR_MODE_MAX_FPS:
        p.error(f"--fps must be in (0, {SENSOR_MODE_MAX_FPS}] for the 2028x1520 mode")
    frame_period_us = 1e6 / args.fps
    if args.max_exposure_us > frame_period_us:
        p.error(f"--max-exposure-us cannot exceed the frame period ({frame_period_us:.0f} us)")
    if args.shutter_us is not None and args.shutter_us > frame_period_us:
        p.error(f"--shutter-us cannot exceed the frame period ({frame_period_us:.0f} us)")
    if not 0.0 <= args.sharpness <= 16.0:
        p.error("--sharpness must be in [0, 16]")
    if args.bitrate is None:
        args.bitrate = CODECS[args.codec]["bitrate"]
    if args.codec == "h264" and args.bitrate > H264_MAX_BITRATE:
        p.error(f"--bitrate above {H264_MAX_BITRATE:.0f} is not supported by the Pi H.264 encoder")
    if args.iperiod < 1:
        p.error("--iperiod must be >= 1")
    return args


def build_controls(args: argparse.Namespace) -> dict:
    from libcamera import controls as lc

    frame_us = int(round(1e6 / args.fps))
    denoise = {
        "off": lc.draft.NoiseReductionModeEnum.Off,
        "fast": lc.draft.NoiseReductionModeEnum.Fast,
        "high_quality": lc.draft.NoiseReductionModeEnum.HighQuality,
    }[args.denoise]
    ctrls = {
        "FrameDurationLimits": (frame_us, frame_us),
        "Sharpness": float(args.sharpness),
        "NoiseReductionMode": denoise,
        "AwbEnable": True,
    }
    if args.shutter_us is not None:
        ctrls["ExposureTime"] = int(args.shutter_us)
    if args.gain is not None:
        ctrls["AnalogueGain"] = float(args.gain)
    if args.shutter_us is not None and args.gain is not None:
        ctrls["AeEnable"] = False
    return ctrls


def make_logging_encoder(args: argparse.Namespace, writer: csv.writer, timestamps: list[int]):
    """A hardware encoder that logs metadata for exactly the frames it encodes.

    Hooking the encoder (rather than a Picamera2 post_callback) keeps CSV rows
    aligned one-to-one with frames in the stream even if the encoder skips some.
    """
    from picamera2.encoders import H264Encoder, MJPEGEncoder

    if args.codec == "h264":
        # repeat=True puts SPS/PPS before every keyframe so playback can start at any of them.
        base, kwargs = H264Encoder, {"bitrate": int(args.bitrate), "repeat": True, "iperiod": args.iperiod}
    else:
        base, kwargs = MJPEGEncoder, {"bitrate": int(args.bitrate)}

    hook = "_encode" if hasattr(base, "_encode") else "encode"
    parent = getattr(base, hook)
    counter = [0]

    def logged(self, stream, request):
        md = request.get_metadata()
        ts = md.get("SensorTimestamp")
        writer.writerow([
            counter[0],
            ts,
            md.get("ExposureTime"),
            md.get("AnalogueGain"),
            md.get("DigitalGain"),
            md.get("Lux"),
            md.get("ColourTemperature"),
            md.get("FocusFoM"),
        ])
        counter[0] += 1
        if ts is not None:
            timestamps.append(int(ts))
        return parent(self, stream, request)

    cls = type(f"Logging{base.__name__}", (base,), {hook: logged})
    return cls(**kwargs)


def configure_camera(picam2, args: argparse.Namespace):
    main = {"size": CODECS[args.codec]["size"], "format": "YUV420"}
    common = dict(main=main, controls=build_controls(args), buffer_count=args.buffer_count,
                  encode="main", display=None)
    try:
        config = picam2.create_video_configuration(
            sensor={"output_size": SENSOR_MODE, "bit_depth": SENSOR_BIT_DEPTH}, **common)
    except TypeError:
        config = picam2.create_video_configuration(raw={"size": SENSOR_MODE}, **common)
    picam2.configure(config)

    sensor_cfg = picam2.camera_configuration().get("sensor") or {}
    out_size = tuple(sensor_cfg.get("output_size") or picam2.camera_configuration()["raw"]["size"])
    if out_size != SENSOR_MODE:
        modes = [(m["size"], m["fps"]) for m in picam2.sensor_modes]
        raise RuntimeError(f"libcamera picked sensor mode {out_size}, not {SENSOR_MODE}. Available: {modes}")
    return config


def lock_awb_later(picam2, delay_s: float, stop: threading.Event) -> None:
    if stop.wait(delay_s):
        return
    md = picam2.capture_metadata()
    gains = md.get("ColourGains")
    if gains:
        picam2.set_controls({"AwbEnable": False, "ColourGains": tuple(gains)})
        print(f"AWB locked at colour gains {gains[0]:.3f}, {gains[1]:.3f}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        from picamera2 import Picamera2
        from picamera2.outputs import FileOutput
    except ImportError:
        print("picamera2 not found: sudo apt install -y python3-picamera2", file=sys.stderr)
        return 1

    tuning = Picamera2.load_tuning_file(args.tuning_file)
    n_modes = cap_agc_exposure(tuning, args.max_exposure_us, args.max_gain)

    picam2 = Picamera2(camera_num=args.camera, tuning=tuning)
    config = configure_camera(picam2, args)

    session = args.output / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.codec}"
    session.mkdir(parents=True, exist_ok=False)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    timestamps: list[int] = []
    csv_file = open(session / "frames.csv", "w", newline="", buffering=1 << 16)
    writer = csv.writer(csv_file)
    writer.writerow(["frame", "sensor_timestamp_ns", "exposure_us", "analogue_gain",
                     "digital_gain", "lux", "colour_temperature_k", "focus_fom"])
    encoder = make_logging_encoder(args, writer, timestamps)
    out_size = CODECS[args.codec]["size"]
    video_path = session / f"video.{CODECS[args.codec]['ext']}"

    realtime_ns = time.time_ns()
    sensor_clock_ns = time.clock_gettime_ns(_SENSOR_CLOCK)
    session_info = {
        "camera_model": picam2.camera_properties.get("Model"),
        "camera_properties": {k: str(v) for k, v in picam2.camera_properties.items()},
        "sensor_mode": list(SENSOR_MODE),
        "resolution": list(out_size),
        "fps": args.fps,
        "codec": args.codec,
        "bitrate": args.bitrate,
        "iperiod": args.iperiod if args.codec == "h264" else None,
        "max_exposure_us": args.max_exposure_us,
        "max_gain": args.max_gain,
        "shutter_us": args.shutter_us,
        "gain": args.gain,
        "sharpness": args.sharpness,
        "denoise": args.denoise,
        "awb_lock_after_s": args.awb_lock_after,
        "agc_exposure_modes_capped": n_modes,
        "camera_config": str(config),
        "sensor_timestamp_clock": "CLOCK_BOOTTIME" if hasattr(time, "CLOCK_BOOTTIME") else "CLOCK_MONOTONIC",
        "realtime_minus_sensor_clock_ns": realtime_ns - sensor_clock_ns,
        "started_at": datetime.now().isoformat(),
    }

    print(f"Recording {out_size[0]}x{out_size[1]} @ {args.fps:g} fps {args.codec.upper()} "
          f"at {args.bitrate / 1e6:g} Mbit/s to {session}")
    if args.shutter_us is None:
        print(f"Auto exposure capped at {args.max_exposure_us} us, gain <= {args.max_gain:g}")
    else:
        print(f"Fixed shutter {args.shutter_us} us, gain {'auto' if args.gain is None else args.gain}")

    awb_thread = None
    try:
        picam2.start_recording(encoder, FileOutput(str(video_path)))
        if args.awb_lock_after >= 0:
            awb_thread = threading.Thread(target=lock_awb_later, args=(picam2, args.awb_lock_after, stop),
                                          daemon=True)
            awb_thread.start()
        started = time.monotonic()
        while not stop.is_set():
            if args.duration > 0 and time.monotonic() - started >= args.duration:
                break
            stop.wait(1.0)
            elapsed = time.monotonic() - started
            print(f"\r{elapsed:7.1f}s  {len(timestamps):6d} frames", end="", flush=True)
        print()
    finally:
        stop.set()
        picam2.stop_recording()
        picam2.close()
        csv_file.close()

    stats = frame_stats(timestamps, args.fps)
    stats["file_bytes"] = video_path.stat().st_size if video_path.exists() else 0
    duration_s = stats["frames"] / stats["measured_fps"] if stats["measured_fps"] else None
    stats["actual_mbit_per_s"] = stats["file_bytes"] * 8 / duration_s / 1e6 if duration_s else None
    session_info["stats"] = stats
    session_info["stopped_at"] = datetime.now().isoformat()
    (session / "session.json").write_text(json.dumps(session_info, indent=2))

    fps_txt = f"{stats['measured_fps']:.2f}" if stats["measured_fps"] else "n/a"
    rate_txt = f"{stats['actual_mbit_per_s']:.1f} Mbit/s" if stats["actual_mbit_per_s"] else "n/a"
    print(f"{stats['frames']} frames, measured {fps_txt} fps, {stats['dropped_frames']} dropped, "
          f"{stats['file_bytes'] / 1e6:.1f} MB ({rate_txt})")
    print(f"Mux without re-encoding: ffmpeg -framerate {args.fps:g} -i {video_path} "
          f"-c copy {session / 'video.mkv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
