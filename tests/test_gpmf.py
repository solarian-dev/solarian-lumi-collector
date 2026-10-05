import numpy as np

from lumi_collector.gpmf import (
    ACCL_SCALE,
    GYRO_SCALE,
    build_payloads,
    extract_stream_samples,
    iter_klv,
)


def _samples(n=200, acc=(9.8, 0.1, -0.2), gyr=(0.01, 0.02, 0.03)):
    return np.array([[i / 200.0, *acc, *gyr, 35.0] for i in range(n)])


def test_payload_contains_device_and_streams():
    blob = b"".join(build_payloads(_samples(), serial="UMI00ABCDEF"))
    keys = [key for key, *_ in iter_klv(blob)]
    assert keys[0] == "DEVC"
    nested = next(iter_klv(blob))[4]
    inner = [key for key, *_ in iter_klv(nested)]
    assert "DVID" in inner
    assert "CASN" in inner
    assert inner.count("STRM") == 2


def test_roundtrip_accl_gyro():
    samples = _samples()
    blob = b"".join(build_payloads(samples, serial="UMI1"))
    acc = extract_stream_samples(blob, "ACCL", ACCL_SCALE)
    gyr = extract_stream_samples(blob, "GYRO", GYRO_SCALE)
    assert len(acc) == 200
    assert len(gyr) == 200
    z, x, y = acc[0]
    assert abs(z - 9.8) < 0.02
    assert abs(x - 0.1) < 0.02
    assert abs(y + 0.2) < 0.02
    gz, gx, gy = gyr[0]
    assert abs(gz - 0.01) < 0.002
    assert abs(gx - 0.02) < 0.002
    assert abs(gy - 0.03) < 0.002


def test_two_second_windows():
    samples = _samples(400, acc=(9.8, 0.0, 0.0), gyr=(0.0, 0.0, 0.0))
    payloads = build_payloads(samples, serial="UMI1", window_s=1.0)
    assert len(payloads) == 2


def test_accl_saturates_at_int16_instead_of_wrapping():
    blob = b"".join(build_payloads(_samples(acc=(100.0, -100.0, 0.0)), serial="UMI1"))
    z, x, _ = extract_stream_samples(blob, "ACCL", ACCL_SCALE)[0]
    assert (z, x) == (32767 / ACCL_SCALE, -32768 / ACCL_SCALE)
