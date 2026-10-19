import json

import numpy as np
import pytest

from lumi_collector.policy_view import find_calibration


def _camera_file(cameras, name, serial):
    path = cameras / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"camera_serial": serial}))
    return path


def test_finds_the_camera_file_and_skips_copies(tmp_path):
    expected = _camera_file(tmp_path, "LUMI01", "LUMI01")
    _camera_file(tmp_path, "LUMI01_old", "LUMI01")
    assert find_calibration(tmp_path) == expected


def test_no_calibration(tmp_path):
    with pytest.raises(SystemExit, match="No Lumi calibration"):
        find_calibration(tmp_path)


def test_several_cameras_need_a_choice(tmp_path):
    _camera_file(tmp_path, "LUMI01", "LUMI01")
    _camera_file(tmp_path, "LUMI02", "LUMI02")
    with pytest.raises(SystemExit, match="--calibration"):
        find_calibration(tmp_path)


def test_policy_image_is_masked_224(tmp_path):
    pytest.importorskip("solarian_umi_skill.slam.remap_video")
    from solarian_umi_skill.slam.fisheye_model import Fisheye

    from lumi_collector.policy_view import policy_pipeline

    lens = Fisheye(fx=330.0, fy=330.0, cx=719.5, cy=539.5, k=(0.0, 0.0, 0.0, 0.0), width=1440, height=1080)
    calibration = tmp_path / "LUMI01.json"
    calibration.write_text(json.dumps({"camera_serial": "LUMI01", "intrinsics": lens.to_umi_json(rms=0.3, n_views=20, fps=30)}))
    to_policy = policy_pipeline(calibration, (1440, 1080))
    image = to_policy(np.full((1080, 1440, 3), 200, np.uint8))
    assert image.shape == (224, 224, 3)
    assert image[112, 112].tolist() == [200, 200, 200]
    assert image[220, 112].tolist() == [0, 0, 0]
