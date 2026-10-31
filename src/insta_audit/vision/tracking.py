from dataclasses import dataclass


@dataclass(frozen=True)
class DropCrossing:
    track_id: int
    x: float
    y: float
    direction: str


class CrossingLine:
    """Debounced per-track crossing trigger for the configured bag-mouth ROI."""

    def __init__(self, y_threshold: float, cooldown_frames: int = 12) -> None:
        self.y_threshold = y_threshold
        self.cooldown_frames = cooldown_frames
        self.previous: dict[int, tuple[float, float]] = {}
        self.last_trigger: dict[int, int] = {}
        self.frame = 0

    def update(self, centers: dict[int, tuple[float, float]]) -> list[DropCrossing]:
        self.frame += 1
        events: list[DropCrossing] = []
        for track_id, (x, y) in centers.items():
            previous = self.previous.get(track_id)
            last = self.last_trigger.get(track_id, -self.cooldown_frames)
            if previous and self.frame - last >= self.cooldown_frames:
                old_y = previous[1]
                if old_y < self.y_threshold <= y:
                    events.append(DropCrossing(track_id, x, y, "into_bag"))
                    self.last_trigger[track_id] = self.frame
                elif old_y >= self.y_threshold > y:
                    events.append(DropCrossing(track_id, x, y, "out_of_bag"))
                    self.last_trigger[track_id] = self.frame
            self.previous[track_id] = (x, y)
        return events
