"""``solarian lumi --stream --policy`` — the live stream as the policy would see it.

Each frame is remapped into GoPro Max Lens geometry with the camera's
calibration, as ``umi-slam --camera lumi`` remaps the recordings, then
center-cropped to 224x224 with the gripper and mirror areas blacked out by
the same pipeline the UMI skill feeds its policy. ArUco tags are not
inpainted, as they are in training.

Runs on the workstation; needs ``solarian-umi`` installed alongside.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

WINDOW = "LUMI policy view  [s save  q quit]"
# Integer, so each policy pixel is a square block rather than a blur.
DISPLAY_SCALE = 3


def find_calibration(cameras: Path) -> Path:
    """The single ``<serial>.json`` in *cameras* whose serial matches its name."""
    found = [
        path
        for path in sorted(cameras.glob("*.json"))
        if json.loads(path.read_text(encoding="utf-8")).get("camera_serial") == path.stem
    ]
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemExit(
            f"No Lumi calibration in {cameras}. Run from the project directory, pass "
            "--calibration, or calibrate with solarian umi-calibrate-lumi --serial <serial>."
        )
    names = ", ".join(p.name for p in found)
    raise SystemExit(f"Several calibrations in {cameras} ({names}); pick one with --calibration.")


def policy_pipeline(calibration: Path, size: tuple[int, int]):
    """A function from a native Lumi frame of *size* (width, height) to the policy image."""
    from solarian_umi.slam.camera_calibration import load_native_model
    from solarian_umi.slam.fisheye_model import build_remap
    from solarian_umi.slam.remap_video import gopro_target
    from solarian_umi.slam.run import calibration_dir
    from solarian_umi.umi.policy_image import PolicyImagePipeline

    native = load_native_model(calibration).resized(*size)
    remap = build_remap(native, gopro_target(calibration_dir() / "gopro_intrinsics_2_7k.json"))
    pipe = PolicyImagePipeline()
    return lambda frame: pipe.process_frame(remap.apply(frame))


def run(host: str, port: int, calibration: Path) -> None:
    try:
        from solarian_umi.slam.live_calibration import LumiStream
    except ImportError as exc:
        raise SystemExit(f"--policy needs solarian-umi with its slam extra installed ({exc})") from exc
    import cv2

    stream = LumiStream(host, port)
    print(f"Streaming {stream.size[0]}x{stream.size[1]} from {host}; calibration {calibration}")
    to_policy = policy_pipeline(calibration, stream.size)
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    # Launched from an editor's terminal, the window otherwise opens behind it.
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
    shown = -1
    image = None
    try:
        while stream.alive:
            count, frame = stream.latest()
            if frame is not None and count != shown:
                shown = count
                image = to_policy(frame)
                big = cv2.resize(image, None, fx=DISPLAY_SCALE, fy=DISPLAY_SCALE, interpolation=cv2.INTER_NEAREST)
                cv2.imshow(WINDOW, big)
            key = cv2.waitKey(5) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                return
            if key == ord("s") and image is not None:
                path = Path(f"lumi_policy_{datetime.now():%Y%m%d_%H%M%S}.png")
                cv2.imwrite(str(path), image)
                print(f"Saved {path}")
        raise SystemExit("The Lumi stream ended. Is the collector still on Stream video?")
    finally:
        stream.close()
        cv2.destroyAllWindows()
        cv2.waitKey(1)
