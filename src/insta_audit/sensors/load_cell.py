"""Load-cell handling (C21): signed readings, multi-point calibration, per-bag tare, settle
detection and drift checks. Hardware transport stays in a site adapter (`LoadCell`)."""
import statistics
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoadCellCalibration:
    grams_per_count: float
    zero_offset: float
    # Worst absolute error (g) of the fit over the calibration points; 0 for a single point.
    max_residual_g: float = 0.0
    points: int = 1

    def to_grams(self, raw_count: float) -> float:
        """Signed: a reading below zero is a real signal (an item lifted out), not noise to clamp."""
        return (raw_count - self.zero_offset) * self.grams_per_count

    @classmethod
    def from_points(cls, points: list[tuple[float, float]]) -> "LoadCellCalibration":
        """Least-squares fit of grams = slope * (count - offset) from (raw_count, known_grams) pairs.
        Include an empty-platform point (0 g) and at least two loads across the operating range."""
        if len(points) < 2:
            raise ValueError("need at least two calibration points (include an empty-platform point)")
        counts = [p[0] for p in points]
        grams = [p[1] for p in points]
        if len(set(counts)) < 2:
            raise ValueError("calibration points need distinct raw counts")
        mean_c, mean_g = statistics.fmean(counts), statistics.fmean(grams)
        sxx = sum((c - mean_c) ** 2 for c in counts)
        slope = sum((c - mean_c) * (g - mean_g) for c, g in zip(counts, grams, strict=True)) / sxx
        if slope <= 0:
            raise ValueError("calibration slope must be positive (check the known weights and raw counts)")
        intercept = mean_g - slope * mean_c
        offset = -intercept / slope
        residual = max(abs(slope * (c - offset) - g) for c, g in zip(counts, grams, strict=True))
        return cls(grams_per_count=slope, zero_offset=offset, max_residual_g=residual, points=len(points))


def drift_check(calibration: LoadCellCalibration, raw_count: float, known_g: float, tolerance_g: float) -> dict:
    """Shift-start check with a known weight (and an empty platform for known_g=0)."""
    error = calibration.to_grams(raw_count) - known_g
    return {"known_g": known_g, "measured_g": round(calibration.to_grams(raw_count), 2),
            "error_g": round(error, 2), "ok": abs(error) <= tolerance_g}


class StabilityDetector:
    """Settled when the last `window_s` seconds of readings span at least `min_samples` samples and
    have a standard deviation below `sigma_g` (e.g. < 1 g over 300 ms)."""

    def __init__(self, window_s: float = 0.3, sigma_g: float = 1.0, min_samples: int = 5) -> None:
        self.window_s = window_s
        self.sigma_g = sigma_g
        self.min_samples = min_samples
        self._samples: deque[tuple[float, float]] = deque()

    def push(self, t: float, grams: float) -> None:
        self._samples.append((t, grams))
        while self._samples and t - self._samples[0][0] > self.window_s:
            self._samples.popleft()

    @property
    def settled(self) -> bool:
        if len(self._samples) < self.min_samples:
            return False
        if self._samples[-1][0] - self._samples[0][0] < self.window_s * 0.9:
            return False
        return statistics.pstdev(g for _, g in self._samples) < self.sigma_g

    @property
    def value(self) -> float | None:
        """Mean over the window once settled, else None."""
        return statistics.fmean(g for _, g in self._samples) if self.settled else None

    def reset(self) -> None:
        self._samples.clear()


@dataclass
class BagScale:
    """Tracks one bag: tare when the empty bag is placed, then signed net deltas on settled steps."""

    detector: StabilityDetector = field(default_factory=StabilityDetector)
    tare_g: float | None = None
    last_settled_g: float | None = None

    def push(self, t: float, grams: float) -> None:
        self.detector.push(t, grams)

    def tare(self) -> float:
        value = self.detector.value
        if value is None:
            raise ValueError("cannot tare: scale is not settled")
        self.tare_g, self.last_settled_g = value, value
        return value

    @property
    def net_g(self) -> float | None:
        value = self.detector.value
        return None if value is None or self.tare_g is None else value - self.tare_g

    def step(self) -> float | None:
        """Signed change since the previous settled reading (negative = an item was removed),
        or None if the scale is not settled / not tared."""
        value = self.detector.value
        if value is None or self.last_settled_g is None:
            return None
        delta = value - self.last_settled_g
        self.last_settled_g = value
        return delta


class LoadCell:
    """Hardware-neutral contract. Implement serial/I2C transport in a site adapter."""

    def read_grams(self) -> float:
        raise NotImplementedError("connect a calibrated store-specific load-cell adapter")
