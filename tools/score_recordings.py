#!/usr/bin/env python3
"""Score capture takes recorded by record_imx477.py. Runs on the Mac (solarian env).

    python tools/score_recordings.py tools/recordings/20261003_17*

Every frame is mapped onto the sensor's 2028x1520 grid, so H.264 (the whole
sensor scaled to 1440x1080) and MJPEG (sensor columns 0-1919 at full scale)
are scored over the same scene: at 1440x1080, the size the pipeline remaps to;
at 960x720, the size SLAM tracks at; and at native size for finger tags only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import warnings
from multiprocessing import Pool
from pathlib import Path

import av
import cv2
import numpy as np

from lumi_collector.mux import slot_timestamps

GRID_1440 = (1440, 1080)
GRID_960 = (960, 720)
MASK_GRID = (240, 180)
FLOW_GRID = (480, 360)
REST_PX, FAST_PX = 0.5, 5.0   # motion speed bounds, px per frame at the 960 grid (5 px = ~35 deg/s at 30 fps)
MOVING_PX = 2.0               # where the scene flows this fast, a match that moved under FIXED_PX is fixed to the camera
FIXED_PX = 0.5
WEAK_INLIERS = 30
MATCHED_PX = (2.0, 4.0)       # speed band for comparing inliers between takes moved by hand
LIT_Y = 25                    # the ISP's video output is limited range: black is 16, white 235
CLIP_Y = 233
NOISE_CLIP_Y = 12             # larger frame-to-frame differences at rest are flicker or a nudge, not noise
TAG_IDS = (0, 1)
TAG_WINDOW = (160, 120)       # contact-sheet crops at the 1440 grid
TEXTURE_WINDOW = (280, 180)
STREAM_TIMEBASE = 1_200_000   # ffmpeg's raw h264 and mjpeg demuxers

COLUMNS = [
    # (section, key, header, format)
    ("integrity", "frames", "frames", "{:.0f}"),
    ("integrity", "packets_match_csv", "csv=stream", "{}"),
    ("integrity", "dropped", "dropped", "{:.0f}"),
    ("integrity", "lost_in_encoder", "lost", "{:.0f}"),
    ("integrity", "fps", "fps", "{:.2f}"),
    ("integrity", "mbps", "Mbit/s", "{:.1f}"),
    ("resting image", "exposure_us", "exp us", "{:.0f}"),
    ("resting image", "gain", "gain", "{:.1f}"),
    ("resting image", "brightness", "bright", "{:.0f}"),
    ("resting image", "clipped_pct", "clip%", "{:.2f}"),
    ("resting image", "noise", "noise", "{:.2f}"),
    ("resting image", "texture", "texture", "{:.2f}"),
    ("compression loss vs refs (1440 grid)", "psnr_rest", "dB rest", "{:.1f}"),
    ("compression loss vs refs (1440 grid)", "psnr_moving", "dB moving", "{:.1f}"),
    ("compression loss vs refs (1440 grid)", "psnr_fast", "dB fast", "{:.1f}"),
    ("compression loss vs refs (1440 grid)", "kept_moving", "kept moving", "{:.2f}"),
    ("compression loss vs refs (1440 grid)", "kept_fast", "kept fast", "{:.2f}"),
    ("compression loss vs refs (1440 grid)", "corr_moving", "corr moving", "{:.2f}"),
    ("bytes per frame (P-frames for H.264)", "kb_slow", "KB slow", "{:.0f}"),
    ("bytes per frame (P-frames for H.264)", "kb_fast", "KB fast", "{:.0f}"),
    ("bytes per frame (P-frames for H.264)", "budget_fast_pct", "%budget fast", "{:.0f}"),
    ("artifacts", "fixed_pct", "fixed%", "{:.2f}"),
    ("artifacts", "kf_detail_jump_pct", "kf detail%", "{:+.1f}"),
    ("artifacts", "kf_feature_jump_pct", "kf feat%", "{:+.1f}"),
    ("finger tags", "tag0_1440", "id0 1440", "{:.2f}"),
    ("finger tags", "tag1_1440", "id1 1440", "{:.2f}"),
    ("finger tags", "tag0_native", "id0 native", "{:.2f}"),
    ("finger tags", "tag1_native", "id1 native", "{:.2f}"),
    ("ORB at 960x720 while moving", "orb_features", "features", "{:.0f}"),
    ("ORB at 960x720 while moving", "orb_inliers", "inliers", "{:.0f}"),
    ("ORB at 960x720 while moving", "orb_inliers_matched", "inl {:.0f}-{:.0f}px".format(*MATCHED_PX), "{:.0f}"),
    ("ORB at 960x720 while moving", "orb_weak_pct", f"<{WEAK_INLIERS} inl%", "{:.1f}"),
    ("motion", "slow_frames", "slow", "{:.0f}"),
    ("motion", "fast_frames", "fast", "{:.0f}"),
    ("motion", "speed_median", "px/frame p50", "{:.1f}"),
    ("motion", "speed_p90", "px/frame p90", "{:.1f}"),
]


def to_grid(y: np.ndarray, cover: float, grid: tuple[int, int]) -> np.ndarray:
    """Map a Y plane covering the first ``cover`` of the sensor width onto a grid of the whole sensor."""
    w = round(grid[0] * cover)
    out = y if (y.shape[1], y.shape[0]) == (w, grid[1]) else cv2.resize(y, (w, grid[1]), interpolation=cv2.INTER_AREA)
    if w == grid[0]:
        return out
    return cv2.copyMakeBorder(out, 0, 0, 0, grid[0] - w, cv2.BORDER_CONSTANT, value=0)


def y_plane(frame) -> np.ndarray:
    """The decoded luma exactly as stored, without a range conversion (H.264 and MJPEG carry the same values)."""
    plane = frame.planes[0]
    return np.frombuffer(plane, np.uint8).reshape(frame.height, plane.line_size)[:, : frame.width]


def band(img: np.ndarray) -> np.ndarray:
    """Fine detail at the 1440 grid: the band between Gaussian sigmas 0.7 and 1.6 px."""
    f = img.astype(np.float32)
    return cv2.GaussianBlur(f, (0, 0), 0.7) - cv2.GaussianBlur(f, (0, 0), 1.6)


def masks(small: np.ndarray, moving: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lit lens circle, and the part of it that is not fixed to the camera.

    The gripper and the lens edge keep their pattern at the same pixels while
    the camera moves, even as their brightness drifts, so the local correlation
    between frames five apart stays high there. ORB, flow and detail only look
    at the rest.
    """
    lit = small.mean(axis=0) > LIT_Y
    frames = small[moving] if moving.sum() > 30 else small

    def box(x):
        return cv2.blur(x, (5, 5))

    ncc = []
    for a, b in zip(frames[:-5:5], frames[5::5]):
        ma, mb = box(a), box(b)
        va, vb = box(a * a) - ma * ma, box(b * b) - mb * mb
        textured = (va > 4) & (vb > 4)
        ncc.append(np.where(textured, (box(a * b) - ma * mb) / np.sqrt(np.maximum(va * vb, 1e-6)), np.nan))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # untextured pixels are NaN in every pair
        fixed = np.nan_to_num(np.nanmean(np.stack(ncc), axis=0), nan=0.0) > 0.7
    kernel = np.ones((5, 5), np.uint8)
    fixed = cv2.dilate(cv2.morphologyEx(fixed.astype(np.uint8), cv2.MORPH_CLOSE, kernel), kernel) > 0
    lit = cv2.erode(lit.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2) > 0
    scene = cv2.morphologyEx((lit & ~fixed).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    if scene.sum() < 0.2 * lit.sum():
        scene = lit
    return lit, scene


def resized(mask: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    return cv2.resize(mask.astype(np.uint8), grid, interpolation=cv2.INTER_NEAREST) > 0


def texture_centre(img: np.ndarray, scene: np.ndarray) -> np.ndarray:
    """Centre (x, y) of the TEXTURE_WINDOW of the scene with the most fine detail."""
    energy = cv2.boxFilter(band(img) ** 2 * scene, -1, TEXTURE_WINDOW)
    y, x = np.unravel_index(np.argmax(energy), energy.shape)
    return np.array([x, y], dtype=float)


def tag_hits(detector, img: np.ndarray) -> dict[int, np.ndarray]:
    corners, ids, _ = detector.detectMarkers(img)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).mean(axis=0) for i, c in zip(ids.ravel(), corners) if int(i) in TAG_IDS}


def score_take(session: Path) -> dict:
    cv2.setNumThreads(1)
    info = json.loads((session / "session.json").read_text())
    with open(session / "frames.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    video = session / f"video.{info['codec']}"
    cover = info["resolution"][0] / info["main_size"][0]
    ts = np.array([int(r["sensor_timestamp_ns"]) for r in rows], dtype=np.int64)
    t = (ts - ts[0]) / 1e9
    nbytes = np.array([int(r["bytes"]) for r in rows])
    keyframe = np.array([r["keyframe"] == "1" for r in rows])
    # Metadata columns are empty for frames whose request was not seen by the encoder hook.
    exposure = np.array([float(r["exposure_us"] or "nan") for r in rows])
    gain = np.array([float(r["analogue_gain"] or "nan") * float(r["digital_gain"] or "nan") for r in rows])
    # The take's clock starts about a second before the first frame arrives.
    rest_end = info["rest_s"] - 1.0

    packet_sizes, small = [], []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        for packet in container.demux(stream):
            if packet.size:
                packet_sizes.append(packet.size)
            for frame in packet.decode():
                small.append(to_grid(y_plane(frame), cover, MASK_GRID).astype(np.float32))
    n = min(len(small), len(rows))
    lit_small, scene_small = masks(np.stack(small[:n]), t[:n] > info["rest_s"])
    del small
    lit14 = resized(lit_small, GRID_1440)
    scene14 = resized(scene_small, GRID_1440)
    scene96 = resized(scene_small, GRID_960).astype(np.uint8) * 255
    scene_flow = resized(scene_small, FLOW_GRID)

    refs = np.load(session / "refs.npz") if (session / "refs.npz").is_file() else None
    ref_at = {int(ns): k for k, ns in enumerate(refs["sensor_ns"])} if refs is not None else {}
    orb = cv2.ORB_create(nfeatures=1250, scaleFactor=1.2, nlevels=8, fastThreshold=20)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), params)

    speed = np.full(n, np.nan)
    feats = np.zeros(n)
    inliers = np.full(n, np.nan)
    matched = np.zeros(n)
    fixed = np.zeros(n)
    detail = np.zeros(n)
    tags14 = np.zeros((n, len(TAG_IDS)), bool)
    tags_native = np.zeros((n, len(TAG_IDS)), bool)
    tag_centres: dict[int, list] = {i: [] for i in TAG_IDS}
    noise, brightness, clipped, ref_scores = [], [], [], []
    rest_sum, rest_count = np.zeros(GRID_1440[::-1]), 0
    rest_index = int(np.argmin(np.abs(t[:n] - rest_end / 2)))
    rest_frame = None
    prev = None
    with av.open(str(video)) as container:
        for i, frame in enumerate(container.decode(video=0)):
            if i >= n:
                break
            y = y_plane(frame)
            g14 = to_grid(y, cover, GRID_1440)
            g96 = cv2.resize(g14, GRID_960, interpolation=cv2.INTER_AREA)
            gflow = cv2.resize(g96, FLOW_GRID, interpolation=cv2.INTER_AREA)
            kp, des = orb.detectAndCompute(g96, scene96)
            feats[i] = len(kp)
            detail[i] = band(g14)[scene14].std()

            hits = tag_hits(detector, g14)
            for j, tag in enumerate(TAG_IDS):
                tags14[i, j] = tag in hits
                if tag in hits:
                    tag_centres[tag].append(hits[tag])
            native_hits = tag_hits(detector, y) if cover < 1 else hits
            tags_native[i] = [tag in native_hits for tag in TAG_IDS]
            if i == rest_index:
                rest_frame = g14.copy()

            if prev is not None:
                p14, p96flow, pkp, pdes = prev
                flow_scale = GRID_960[0] / FLOW_GRID[0]
                flow = np.linalg.norm(cv2.calcOpticalFlowFarneback(p96flow, gflow, None, 0.5, 3, 15, 3, 5, 1.2, 0),
                                      axis=2) * flow_scale
                speed[i] = float(np.median(flow[scene_flow]))
                if des is not None and pdes is not None and len(kp) >= 8 and len(pkp) >= 8:
                    pairs = matcher.match(pdes, des)
                    p0 = np.float32([pkp[m.queryIdx].pt for m in pairs])
                    p1 = np.float32([kp[m.trainIdx].pt for m in pairs])
                    still = np.linalg.norm(p1 - p0, axis=1) < FIXED_PX
                    at = np.minimum((p0 / flow_scale).astype(int), [FLOW_GRID[0] - 1, FLOW_GRID[1] - 1])
                    scene_moves = flow[at[:, 1], at[:, 0]] >= MOVING_PX
                    matched[i] = scene_moves.sum()
                    fixed[i] = (still & scene_moves).sum()
                    if (~still).sum() >= 8:
                        _, inl = cv2.findFundamentalMat(p0[~still], p1[~still], cv2.FM_RANSAC, 2.0, 0.99)
                        inliers[i] = 0 if inl is None else int(inl.sum())
                    else:
                        inliers[i] = 0
                else:
                    inliers[i] = 0
                if t[i] < rest_end and speed[i] < REST_PX:
                    diff = (g14.astype(np.float32) - p14.astype(np.float32))[lit14]
                    diff = diff[np.abs(diff) <= NOISE_CLIP_Y]
                    noise.append(float(np.sqrt(np.mean(diff ** 2) / 2)))
                    brightness.append(float(g14[lit14].mean()))
                    clipped.append(float((g14[lit14] >= CLIP_Y).mean() * 100))
                    rest_sum += g14
                    rest_count += 1

            ref = ref_at.get(int(ts[i]))
            if ref is not None:
                r14 = to_grid(refs["y"][ref][:, : y.shape[1]], cover, GRID_1440)
                mse = float(np.mean((g14[lit14].astype(np.float32) - r14[lit14].astype(np.float32)) ** 2))
                be, br = band(g14)[scene14], band(r14)[scene14]
                ref_scores.append((0.0 if np.isnan(speed[i]) else speed[i], 10 * np.log10(255 ** 2 / max(mse, 1e-6)),
                                   be.std() / br.std(), float(np.corrcoef(be, br)[0, 1])))
            prev = (g14, gflow, kp, des)

    rest = speed < REST_PX
    slow = (speed >= REST_PX) & (speed < FAST_PX)
    fast = speed >= FAST_PX
    moving = speed >= REST_PX
    matched_speed = (speed >= MATCHED_PX[0]) & (speed < MATCHED_PX[1])
    is_h264 = info["codec"] == "h264"
    considered = ~keyframe[:n] if is_h264 else np.ones(n, bool)
    budget = info["bitrate"] / info["fps"] / 8
    stats = info["stats"]

    def mean_where(values, sel):
        return float(np.mean(values[sel])) if sel.any() else float("nan")

    scores = np.array(ref_scores) if ref_scores else np.zeros((0, 4))
    ref_speed = scores[:, 0]
    ref_groups = {"rest": ref_speed < REST_PX, "moving": ref_speed >= REST_PX, "fast": ref_speed >= FAST_PX}
    kf = [k for k in np.flatnonzero(keyframe[:n]) if k >= 3] if is_h264 else []

    result = {
        "label": info.get("label") or session.name,
        "session": session.name,
        "codec": info["codec"],
        "frames": len(rows),
        "packets_match_csv": len(packet_sizes) == len(rows) and packet_sizes == nbytes.tolist(),
        "dropped": stats["dropped_frames"],
        "lost_in_encoder": stats["frames_to_encoder"] - stats["frames"],
        "fps": stats["measured_fps"],
        "mbps": stats["actual_mbit_per_s"],
        "exposure_us": float(np.nanmedian(exposure[t < rest_end])),
        "gain": float(np.nanmedian(gain[t < rest_end])),
        "brightness": float(np.mean(brightness)) if brightness else float("nan"),
        "clipped_pct": float(np.mean(clipped)) if clipped else float("nan"),
        "noise": float(np.median(noise)) if noise else float("nan"),
        # Fine detail of the averaged resting frames: noise averages out, texture stays. Only
        # comparable between takes that rest at the same spot.
        "texture": float(band((rest_sum / rest_count).astype(np.float32))[scene14].std())
        if rest_count else float("nan"),
        "psnr_rest": mean_where(scores[:, 1], ref_groups["rest"]),
        "psnr_moving": mean_where(scores[:, 1], ref_groups["moving"]),
        "psnr_fast": mean_where(scores[:, 1], ref_groups["fast"]),
        "kept_moving": mean_where(scores[:, 2], ref_groups["moving"]),
        "kept_fast": mean_where(scores[:, 2], ref_groups["fast"]),
        "corr_moving": mean_where(scores[:, 3], ref_groups["moving"]),
        "refs_rest_moving_fast": "/".join(str(int(g.sum())) for g in ref_groups.values()),
        "kb_slow": float(np.median(nbytes[:n][slow & considered])) / 1e3 if (slow & considered).any() else float("nan"),
        "kb_fast": float(np.median(nbytes[:n][fast & considered])) / 1e3 if (fast & considered).any() else float("nan"),
        "budget_fast_pct": float(np.median(nbytes[:n][fast & considered])) / budget * 100
        if (fast & considered).any() else float("nan"),
        "fixed_pct": float(fixed.sum() / max(matched.sum(), 1) * 100),
        "kf_detail_jump_pct": float(np.median([detail[k] / detail[k - 3:k].mean() - 1 for k in kf]) * 100)
        if kf else float("nan"),
        "kf_feature_jump_pct": float(np.median([feats[k] / max(feats[k - 3:k].mean(), 1) - 1 for k in kf]) * 100)
        if kf else float("nan"),
        "tag0_1440": float(tags14[:, 0].mean()),
        "tag1_1440": float(tags14[:, 1].mean()),
        "tag0_native": float(tags_native[:, 0].mean()),
        "tag1_native": float(tags_native[:, 1].mean()),
        "orb_features": float(np.median(feats[moving])) if moving.any() else float("nan"),
        "orb_inliers": float(np.nanmedian(inliers[moving])) if moving.any() else float("nan"),
        "orb_weak_pct": float(np.mean(inliers[moving] < WEAK_INLIERS) * 100) if moving.any() else float("nan"),
        "orb_inliers_matched": float(np.nanmedian(inliers[matched_speed])) if matched_speed.any() else float("nan"),
        "rest_frames": int(rest.sum()),
        "slow_frames": int(slow.sum()),
        "fast_frames": int(fast.sum()),
        "speed_median": float(np.nanmedian(speed[moving])) if moving.any() else float("nan"),
        "speed_p90": float(np.nanpercentile(speed[moving], 90)) if moving.any() else float("nan"),
        # Not in the summary: plot and contact-sheet inputs.
        "_speed": speed,
        "_bytes": nbytes[:n],
        "_considered": considered,
        "_rest_frame": rest_frame,
        "_tag_centres": {k: np.median(v, axis=0) for k, v in tag_centres.items() if v},
        "_texture_centre": texture_centre(rest_frame, scene14),
        "_ts": ts,
        "_fps": info["fps"],
        "_video": video,
    }
    return result


def write_real_speed_mp4(result: dict) -> None:
    """Remux with each frame at its sensor-timestamp slot, so drops show as held frames."""
    video = result["_video"]
    fps = result["_fps"]
    slots = np.round((result["_ts"] - result["_ts"][0]) / (1e9 / fps)).astype(int).tolist()
    tick = round(STREAM_TIMEBASE / fps)
    pts = slot_timestamps(slots, tick)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", video.suffix[1:], "-i", str(video),
                    "-c:v", "copy", "-bsf:v", f"setts=pts={pts}:dts={pts}:duration={tick}",
                    str(video.with_suffix(".mp4"))], check=True)


def contact_sheet(results: list[dict], path: Path) -> None:
    """The same crops of every take's resting frame: both finger tags and a patch of the scene, 2x nearest."""
    centres = next((r["_tag_centres"] for r in results if len(r["_tag_centres"]) == len(TAG_IDS)), {})
    windows = [(centres.get(tag, np.array([400 + 640 * j, 900])), TAG_WINDOW) for j, tag in enumerate(TAG_IDS)]
    windows.append((results[0]["_texture_centre"], TEXTURE_WINDOW))
    rows = []
    for r in results:
        img = r["_rest_frame"]
        tiles = []
        for (cx, cy), (w, h) in windows:
            x0 = int(np.clip(cx - w / 2, 0, GRID_1440[0] - w))
            y0 = int(np.clip(cy - h / 2, 0, GRID_1440[1] - h))
            crop = cv2.resize(img[y0:y0 + h, x0:x0 + w], (2 * w, 2 * h), interpolation=cv2.INTER_NEAREST)
            tiles.append(crop)
        tall = max(tile.shape[0] for tile in tiles)
        row = np.hstack([cv2.copyMakeBorder(tile, 2, 2 + tall - tile.shape[0], 2, 2, cv2.BORDER_CONSTANT, value=60)
                         for tile in tiles])
        label = np.zeros((26, row.shape[1]), np.uint8)
        cv2.putText(label, r["label"], (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 1, cv2.LINE_AA)
        rows.append(np.vstack([label, row]))
    cv2.imwrite(str(path), np.vstack(rows))


def bits_plot(results: list[dict], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for r in results:
        sel = r["_considered"] & ~np.isnan(r["_speed"])
        ax.scatter(r["_speed"][sel], r["_bytes"][sel] / 1e3, s=5, alpha=0.5, label=r["label"])
    ax.axvline(REST_PX, color="grey", lw=0.5)
    ax.axvline(FAST_PX, color="grey", lw=0.5)
    ax.set_xlabel("motion speed, px per frame at 960x720")
    ax.set_ylabel("KB per frame (H.264: P-frames only)")
    ax.legend(markerscale=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def print_table(results: list[dict]) -> None:
    width = max(len(r["label"]) for r in results) + 2
    section = None
    group: list[tuple] = []

    def flush():
        if not group:
            return
        print(f"\n{section}")
        print(" " * width + "".join(f"{h:>13}" for _, h, _ in group))
        for r in results:
            cells = []
            for key, _, fmt in group:
                v = r[key]
                cells.append(f"{'-' if isinstance(v, float) and np.isnan(v) else fmt.format(v):>13}")
            print(f"{r['label']:<{width}}" + "".join(cells))

    for sec, key, header, fmt in COLUMNS:
        if sec != section:
            flush()
            section, group = sec, []
        group.append((key, header, fmt))
    flush()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sessions", nargs="+", type=Path, help="session directories from record_imx477.py")
    p.add_argument("--out", type=Path, default=None, help="where summary and images go; default <parent>/scores")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    p.add_argument("--no-mp4", action="store_true", help="skip the real-speed video.mp4 per session")
    args = p.parse_args()

    sessions = [s for s in args.sessions if (s / "frames.csv").is_file() and (s / "session.json").is_file()]
    if not sessions:
        p.error("no session directories given")
    with Pool(min(args.jobs, len(sessions))) as pool:
        results = pool.map(score_take, sessions)

    out = args.out or sessions[0].parent / "scores"
    out.mkdir(parents=True, exist_ok=True)
    keys = ["label", "session", "codec", *[k for _, k, _, _ in COLUMNS], "refs_rest_moving_fast", "rest_frames"]
    with open(out / "summary.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(keys)
        writer.writerows([[r[k] for k in keys] for r in results])
    contact_sheet(results, out / "contact_sheet.png")
    bits_plot(results, out / "bits_vs_motion.png")
    if not args.no_mp4:
        for r in results:
            write_real_speed_mp4(r)
    print_table(results)
    print("\nrefs per motion group (rest/moving/fast): "
          + ", ".join(f"{r['label']} {r['refs_rest_moving_fast']}" for r in results))
    print(f"Wrote {out / 'summary.csv'}, {out / 'contact_sheet.png'}, {out / 'bits_vs_motion.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
