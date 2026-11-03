"""Drop/removal event triggers (C22).

* `BagMouthTracker`: polygon ROI (not a single y-line), per-track state, hand/person suppression.
* `EventFuser`: a crossing only becomes an event when a settled scale step of the same sign
  coincides with it, so hands crossing the mouth and ID switches (which add crossings without
  weight) are not counted; `out_of_bag` events subtract from the net count.
"""
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass(frozen=True)
class Detection:
    x: float
    y: float
    label: str = "item"
    confidence: float = 1.0


@dataclass(frozen=True)
class DropCrossing:
    track_id: int
    x: float
    y: float
    direction: str  # "into_bag" | "out_of_bag"


@dataclass(frozen=True)
class ConfirmedDrop:
    track_id: int
    direction: str
    t: float
    delta_g: float


class PolygonROI:
    def __init__(self, points: list[Point]) -> None:
        if len(points) < 3:
            raise ValueError("a polygon ROI needs at least three points")
        self.points = points

    def contains(self, x: float, y: float) -> bool:
        """Ray casting; points exactly on an edge may go either way."""
        inside = False
        n = len(self.points)
        for i in range(n):
            x1, y1 = self.points[i]
            x2, y2 = self.points[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
        return inside


class BagMouthTracker:
    def __init__(
        self,
        roi: PolygonROI,
        cooldown_frames: int = 12,
        ignore_labels: tuple[str, ...] = ("hand", "person", "arm"),
        min_confidence: float = 0.35,
    ) -> None:
        self.roi = roi
        self.cooldown_frames = cooldown_frames
        self.ignore_labels = {label.lower() for label in ignore_labels}
        self.min_confidence = min_confidence
        self._inside: dict[int, bool] = {}
        self._last_trigger: dict[int, int] = {}
        self.frame = 0

    def update(self, detections: dict[int, Detection | Point]) -> list[DropCrossing]:
        self.frame += 1
        events: list[DropCrossing] = []
        for track_id, detection in detections.items():
            if not isinstance(detection, Detection):
                detection = Detection(*detection)
            if detection.label.lower() in self.ignore_labels or detection.confidence < self.min_confidence:
                continue
            inside = self.roi.contains(detection.x, detection.y)
            previous = self._inside.get(track_id)
            self._inside[track_id] = inside
            last = self._last_trigger.get(track_id, -self.cooldown_frames)
            if previous is None or previous == inside or self.frame - last < self.cooldown_frames:
                continue
            events.append(DropCrossing(track_id, detection.x, detection.y, "into_bag" if inside else "out_of_bag"))
            self._last_trigger[track_id] = self.frame
        return events


@dataclass
class EventFuser:
    window_s: float = 1.5
    min_step_g: float = 3.0
    _pending: list[tuple[float, DropCrossing]] = field(default_factory=list)
    unmatched_steps: int = 0
    unconfirmed: int = 0
    into: int = 0
    out: int = 0

    def add_crossing(self, t: float, crossing: DropCrossing) -> None:
        self.expire(t)
        self._pending.append((t, crossing))

    def add_scale_step(self, t: float, delta_g: float) -> list[ConfirmedDrop]:
        """Call on each settled scale step (signed grams since the previous settled reading)."""
        self.expire(t)
        if abs(delta_g) < self.min_step_g:
            return []
        direction = "into_bag" if delta_g > 0 else "out_of_bag"
        for index, (crossed_at, crossing) in enumerate(self._pending):
            if crossing.direction == direction and abs(t - crossed_at) <= self.window_s:
                del self._pending[index]
                if direction == "into_bag":
                    self.into += 1
                else:
                    self.out += 1
                return [ConfirmedDrop(crossing.track_id, direction, t, delta_g)]
        self.unmatched_steps += 1  # weight changed with no visible crossing (occlusion, bag shift)
        return []

    def expire(self, now: float) -> list[DropCrossing]:
        """Drop crossings that never got a scale step (hand passes, duplicate IDs)."""
        stale = [c for crossed_at, c in self._pending if now - crossed_at > self.window_s]
        self._pending = [(t, c) for t, c in self._pending if now - t <= self.window_s]
        self.unconfirmed += len(stale)
        return stale

    @property
    def net_count(self) -> int:
        return self.into - self.out
