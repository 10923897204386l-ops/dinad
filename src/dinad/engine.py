"""Ahead-of-time musical scheduling; no wall clock or DSP callbacks."""

from dataclasses import dataclass
from typing import Protocol

from dinad.interpreters import Note, interpret_notes
from dinad.objects import ObjectInstance, _finite


@dataclass(frozen=True)
class HarmonicContext:
    """Resolved, non-overlapping half-open harmonic spans in beats."""

    spans: tuple[Note, ...]

    def __post_init__(self) -> None:
        spans = tuple(sorted(self.spans, key=lambda note: note.start))
        if not spans:
            raise ValueError("harmonic context must contain at least one span")
        for index, span in enumerate(spans):
            _finite(span.start + span.duration, "harmonic span end")
            if index and spans[index - 1].start + spans[index - 1].duration > span.start:
                raise ValueError("harmonic spans must not overlap")
        object.__setattr__(self, "spans", spans)

    @classmethod
    def from_instance(cls, instance: ObjectInstance) -> "HarmonicContext":
        return cls(interpret_notes(instance))

    def pitch_at(self, beat: float) -> float:
        _finite(beat, "harmonic lookup beat")
        for span in self.spans:
            if span.start <= beat < span.start + span.duration:
                return span.pitch
        raise ValueError(f"no harmonic context at beat {beat}")


@dataclass(frozen=True)
class Part:
    instrument: str
    notes: tuple[Note, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string")
        object.__setattr__(self, "notes", tuple(self.notes))
        if any(not isinstance(note, Note) for note in self.notes):
            raise TypeError("part notes must be Note values")


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


def schedule(parts: tuple[Part, ...], tempo: float = 120) -> tuple[Event, ...]:
    """Convert beats to seconds; stable ties follow part then note order."""
    _finite(tempo, "tempo")
    if tempo <= 0:
        raise ValueError("tempo must be positive")
    seconds_per_beat = 60 / tempo
    _finite(seconds_per_beat, "seconds per beat")
    if seconds_per_beat <= 0:
        raise ValueError("tempo produces an unrepresentable beat duration")
    events = [
        Event(part.instrument, note.pitch, note.start * seconds_per_beat,
              note.duration * seconds_per_beat)
        for part in parts for note in part.notes
    ]
    return tuple(sorted(events, key=lambda event: event.time))


class Backend(Protocol):
    """Backend accepts resolved future events, including note lifetimes."""

    def submit(self, event: Event) -> None: ...


class MockBackend:
    version = "mock-v1"

    def __init__(self) -> None:
        self._events: list[Event] = []

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def submit(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise TypeError("backend requires a resolved Event")
        if self._events and event.time < self._events[-1].time:
            raise ValueError("backend events must be submitted in timestamp order")
        self._events.append(event)


def replay(events: tuple[Event, ...], backend: Backend) -> None:
    """Submit existing history without invoking interpreters or generators."""
    # Validate the whole history before dispatch, avoiding partial malformed replay.
    previous = -1.0
    for event in events:
        if not isinstance(event, Event):
            raise TypeError("replay requires resolved Event values")
        if event.time < previous:
            raise ValueError("replay events must be in timestamp order")
        previous = event.time
    for event in events:
        backend.submit(event)
