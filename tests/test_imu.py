import random

import pytest

from lumi_collector.config import ImuConfig
from lumi_collector.imu import Mpu6050Imu


class FakeMpu:
    """MPU-6050 FIFO on a simulated clock. Sample k is taken at first + k * period."""

    def __init__(self, period_s: float):
        self.period_s = period_s
        self.now = 0.0
        self.first = 0.0
        self.taken = 0
        self.overflowed = False

    def _produced(self) -> int:
        return max(0, int((self.now - self.first) / self.period_s) + 1)

    def _queued(self) -> int:
        queued = self._produced() - self.taken
        if queued * 12 > 1024:
            self.overflowed = True
        return min(queued, 1024 // 12)

    def write_byte_data(self, _addr, reg, value):
        if reg == Mpu6050Imu.USER_CTRL and value & Mpu6050Imu.USER_FIFO_RESET:
            self.first = self.now + self.period_s / 3
            self.taken = 0
            self.overflowed = False

    def read_byte_data(self, _addr, reg):
        if reg == Mpu6050Imu.WHO_AM_I:
            return 0x68
        if reg == Mpu6050Imu.INT_STATUS:
            self._queued()
            status = Mpu6050Imu.FIFO_OFLOW if self.overflowed else 0
            self.overflowed = False
            return status
        return 0

    def read_i2c_block_data(self, _addr, reg, n):
        if reg == Mpu6050Imu.FIFO_COUNT_H:
            count = self._queued() * 12
            return [count >> 8, count & 0xFF]
        if reg == Mpu6050Imu.FIFO_R_W:
            out = []
            for _ in range(n // 12):
                k = self.taken
                self.taken += 1
                # gyro x carries the sample index (16.4 counts per deg/s)
                out += [0, 0, 0, 0, 0x10, 0x00, (k >> 8) & 0x7F, k & 0xFF, 0, 0, 0, 0]
            return out
        return [0] * n


@pytest.fixture
def clock(monkeypatch):
    state = {"latency": 0.0}
    fake = FakeMpu(period_s=0.00505)
    monkeypatch.setattr("lumi_collector.imu.time.sleep", lambda _s: None)
    monkeypatch.setattr("lumi_collector.imu.time.monotonic", lambda: fake.now + state["latency"])
    imu = Mpu6050Imu(ImuConfig(), bus=fake)
    imu.open()
    return fake, imu, state


def _true_index(sample) -> int:
    # gyr stored ZXY: index 1 is sensor x
    return round(sample.gyr_zxy[1] / 3.141592653589793 * 180 * 16.4)


def _run(fake, imu, state, polls, rng, gap_after=None, gap_s=0.0):
    imu.start_stream()
    stream_start = fake.first
    for i in range(polls):
        fake.now += 0.04 + rng.uniform(-0.01, 0.02)
        if i == gap_after:
            fake.now += gap_s
        state["latency"] = rng.uniform(0.0, 0.004)
        imu.poll()
    return stream_start, imu.finish_stream()


def test_fifo_times_follow_the_chip_clock_not_the_poll_times(clock):
    fake, imu, state = clock
    rng = random.Random(1)
    fake.now = 3.0
    start, samples = _run(fake, imu, state, polls=250, rng=rng)

    assert len(samples) > 1900
    assert [_true_index(s) for s in samples] == list(range(len(samples)))
    for s in samples[::50]:
        truth = start + _true_index(s) * fake.period_s - imu._t0 - Mpu6050Imu.FILTER_DELAY_S
        assert s.t_s == pytest.approx(truth, abs=0.0015)
    info = imu.stream_info()
    assert info["timing"] == "fifo"
    assert info["segments"] == 1
    assert info["sample_period_s"] == pytest.approx(0.00505, rel=1e-3)


def test_an_overflow_starts_a_new_segment_with_correct_times(clock):
    fake, imu, state = clock
    rng = random.Random(2)
    fake.now = 1.0
    _start, samples = _run(fake, imu, state, polls=200, rng=rng, gap_after=100, gap_s=0.6)

    assert imu.stream_info()["segments"] == 2
    times = [s.t_s for s in samples]
    assert all(b > a for a, b in zip(times, times[1:]))
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) > 0.5
    assert sorted(gaps)[len(gaps) // 2] == pytest.approx(0.00505, rel=0.01)
