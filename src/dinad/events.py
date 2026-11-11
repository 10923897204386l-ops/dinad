"""Resolved adapter input; independent of objects, generators and DSP engines."""

from dataclasses import dataclass

from dinad.objects import _finite


@dataclass(frozen=True)
class Event:
    """Fully resolved note command; seconds relative to transport origin."""

    instrument: str
    pitch: float
    time: float
    duration: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string")
        for name in ("pitch", "time", "duration"):
            _finite(getattr(self, name), f"event {name}")
        _finite(self.time + self.duration, "event end")
        if self.time < 0 or self.duration <= 0:
            raise ValueError("event time must be non-negative and duration positive")
