"""Immutable, interpretation-neutral definitions and per-use transform context.

Pitch numbers and intervals use semitones; timing in this first increment uses
beats. No tempo, scheduler, instrument, or synthesis backend is implied.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


def _finite(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number")


@dataclass(frozen=True)
class AbsolutePitch:
    semitones: float

    def __post_init__(self) -> None:
        _finite(self.semitones, "absolute pitch")


@dataclass(frozen=True)
class AnchorPitch:
    semitones: float = 0

    def __post_init__(self) -> None:
        _finite(self.semitones, "anchor interval")


@dataclass(frozen=True)
class PreviousPitch:
    semitones: float = 0

    def __post_init__(self) -> None:
        _finite(self.semitones, "previous interval")


Pitch = AbsolutePitch | AnchorPitch | PreviousPitch


@dataclass(frozen=True)
class Element:
    """A pitch relationship with explicit local beat onset and duration."""

    pitch: Pitch
    start: float = 0
    duration: float = 1

    def __post_init__(self) -> None:
        if not isinstance(self.pitch, (AbsolutePitch, AnchorPitch, PreviousPitch)):
            raise TypeError("pitch must be an explicit pitch relationship")
        _finite(self.start, "start")
        _finite(self.duration, "duration")
        if self.start < 0 or self.duration <= 0:
            raise ValueError("start must be non-negative and duration must be positive")


@dataclass(frozen=True)
class MusicalObject:
    """Reusable ordered structure. Tuple order determines relational traversal."""

    elements: tuple[Element | "ObjectReference", ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "elements", tuple(self.elements))
        if any(not isinstance(item, (Element, ObjectReference)) for item in self.elements):
            raise TypeError("object elements must be Element or ObjectReference")


@dataclass(frozen=True)
class ObjectReference:
    """Place a reusable child at a local beat offset."""

    target: MusicalObject
    start: float = 0

    def __post_init__(self) -> None:
        if not isinstance(self.target, MusicalObject):
            raise TypeError("reference target must be a MusicalObject")
        _finite(self.start, "reference start")
        if self.start < 0:
            raise ValueError("reference start must be non-negative")


@dataclass(frozen=True)
class ObjectInstance:
    """Per-use context; transformations never mutate the shared definition."""

    definition: MusicalObject
    anchor: float = 60
    transpose: float = 0
    time_scale: float = 1

    def __post_init__(self) -> None:
        if not isinstance(self.definition, MusicalObject):
            raise TypeError("definition must be a MusicalObject")
        _finite(self.anchor, "anchor")
        _finite(self.transpose, "transpose")
        _finite(self.time_scale, "time scale")
        if self.time_scale <= 0:
            raise ValueError("time scale must be positive")
