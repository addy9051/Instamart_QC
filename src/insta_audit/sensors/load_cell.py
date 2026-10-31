from dataclasses import dataclass


@dataclass(frozen=True)
class LoadCellCalibration:
    grams_per_count: float
    zero_offset: float

    def to_grams(self, raw_count: float) -> float:
        return max(0.0, (raw_count - self.zero_offset) * self.grams_per_count)


class LoadCell:
    """Hardware-neutral contract. Implement serial/I2C transport in a site adapter."""

    def read_grams(self) -> float:
        raise NotImplementedError("connect a calibrated store-specific load-cell adapter")
