"""Operating modes (I3): off | shadow | advisory | enforce, per store and SKU, with a kill switch.

Only `enforce` can block a packer, and only for decisions the policy treats as confident holds.
SKU overrides can only make a store more lenient, never stricter.
"""
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import yaml


class Mode(IntEnum):
    OFF = 0
    SHADOW = 1
    ADVISORY = 2
    ENFORCE = 3

    @classmethod
    def parse(cls, value: "str | Mode") -> "Mode":
        if isinstance(value, Mode):
            return value
        try:
            return cls[str(value).strip().upper()]
        except KeyError as error:
            raise ValueError(f"unknown mode {value!r}; expected off|shadow|advisory|enforce") from error

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass
class ModePolicy:
    default_mode: Mode = Mode.SHADOW
    stores: dict[str, Mode] = field(default_factory=dict)
    skus: dict[str, Mode] = field(default_factory=dict)
    # Hard blocks stay disabled until the per-order false-hold gate has been met on real data.
    allow_enforce: bool = False
    kill_switch: bool = False
    version: str = "unversioned"

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    @classmethod
    def from_file(
        cls, path: str | Path | None, *, allow_enforce: bool = False, kill_switch: bool = False,
        default_mode: str | None = None,
    ) -> "ModePolicy":
        data: dict = {}
        if path and Path(path).is_file():
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(
            default_mode=Mode.parse(default_mode or data.get("default_mode", "shadow")),
            stores={str(k): Mode.parse(v) for k, v in (data.get("stores") or {}).items()},
            skus={str(k): Mode.parse(v) for k, v in (data.get("skus") or {}).items()},
            allow_enforce=allow_enforce,
            kill_switch=kill_switch or bool(data.get("kill_switch", False)),
            version=f"v{data.get('version', '?')}",
        )

    def set_kill_switch(self, active: bool) -> None:
        with self._lock:
            self.kill_switch = active

    def resolve(self, store_id: str | None, skus: list[str] | None = None) -> Mode:
        if self.kill_switch:
            return Mode.OFF
        mode = self.stores.get(store_id or "", self.default_mode)
        for sku in skus or []:
            override = self.skus.get(sku)
            if override is not None and override < mode:
                mode = override
        if mode is Mode.ENFORCE and not self.allow_enforce:
            mode = Mode.ADVISORY
        return mode


def apply_mode(decision: str, confident: bool, mode: Mode) -> str:
    """Map a raw decision to what the packer app should do: none | notify | block."""
    if mode in (Mode.OFF, Mode.SHADOW) or decision == "pass":
        return "none"
    if mode is Mode.ENFORCE and decision == "hold" and confident:
        return "block"
    return "notify"
