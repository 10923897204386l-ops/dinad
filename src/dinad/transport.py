"""Continuous composition and timed controls, independent of audio I/O."""

from dataclasses import asdict, replace
import math
from typing import Callable, Protocol

from dinad.config import Profile, Project
from dinad.controls import Session
from dinad.demo import compose
from dinad.events import Event
from dinad.recording import encode


class LiveOutput(Protocol):
    lead: float
    def validate(self, events: tuple[Event, ...]) -> None: ...
    def open(self) -> None: ...
    def send(self, deadline: float, releases: tuple[int, ...],
             starts: tuple[tuple[int, Event], ...]) -> None: ...
    def close(self, active: tuple[int, ...]) -> None: ...


class SilentOutput:
    lead = 0.

    def validate(self, events): pass
    def open(self): pass
    def send(self, deadline, releases, starts): pass
    def close(self, active): pass


class Transport:
    """One looping take. A batch is committed after the output acknowledges it."""

    def __init__(self, project: Project, profile: Profile, output: LiveOutput,
                 clock: Callable[[], float], lead: float = .05) -> None:
        self.session = Session(project, profile)
        self.output = output
        self.clock = clock
        self.lead = lead
        self.running = False
        self.origin = 0.
        self.tick = 0
        self.onset = 0.
        self.active: dict[int, float] = {}
        self.events: list[Event] = []
        self.actions: list[dict] = []
        self.stop_time = 0.
        self.initial_project = self.take_project = project
        self.pattern = self._pattern(project)

    def _pattern(self, project: Project) -> tuple[Event, ...]:
        pattern = compose(project.seed, project.anchor, 60)
        self.output.validate(pattern)
        return pattern

    def start(self) -> None:
        if self.running:
            raise ValueError("transport is already running")
        self.output.open()
        self.origin = self.clock() + self.lead
        self.tick = 0
        self.onset = 0.
        self.active.clear()
        self.events.clear()
        self.actions.clear()
        self.initial_project = self.take_project = self.session.project
        self.stop_time = 0.
        self.running = True

    @property
    def deadline(self) -> float:
        return self.origin + min(self.onset, min(self.active.values(), default=math.inf))

    def advance(self) -> None:
        if not self.running:
            raise ValueError("transport is stopped")
        at = min(self.onset, min(self.active.values(), default=math.inf))
        # Tempo changes and repeated additions can put coincident beat/gate
        # boundaries a few floating-point units apart.
        if math.isclose(at, self.onset, rel_tol=0., abs_tol=1e-8):
            at = self.onset
        releases = tuple(node for node, end in self.active.items() if end <= at + 1e-8)
        starts = ()
        if at == self.onset:
            seconds_per_beat = 60 / self.session.project.tempo
            beat = (self.tick % 32) / 2
            notes = [replace(note, time=at, duration=note.duration * seconds_per_beat)
                     for note in self.pattern if note.time == beat]
            starts = tuple(enumerate(notes, 1002 + len(self.events)))
        try:
            self.output.send(self.origin + at, releases, starts)
        except BaseException as error:
            try:
                self.stop()
            except (OSError, RuntimeError) as cleanup:
                raise RuntimeError(f"{error}. Live cleanup failed: {cleanup}") from error
            raise
        for node in releases:
            del self.active[node]
        for node, event in starts:
            self.active[node] = event.time + event.duration
            self.events.append(event)
        if at == self.onset:
            self.tick += 1
            self.onset += seconds_per_beat / 2

    def control(self, command: str, received: float | None = None) -> str:
        pattern = self.pattern

        def validate(candidate: Project) -> None:
            nonlocal pattern
            pattern = self._pattern(candidate)

        count = len(self.session.changes)
        result = self.session.execute(command, validate=validate)
        self.pattern = pattern
        if self.running and len(self.session.changes) != count:
            self.take_project = self.session.project
            self.actions.append({**asdict(self.session.changes[-1]), "sequence": len(self.actions),
                                 "received_seconds": max(0., (self.clock() if received is None else received) - self.origin),
                                 "effective_seconds": self.onset, "beat": self.tick / 2})
        return result

    def prepare_edit(self, project: Project, profile: Profile) -> Callable[[], None]:
        """Validate a full edit and return its in-memory commit for this scheduler turn."""
        pattern = self._pattern(project)
        session = Session(project, profile)
        old = self.session.project.snapshot()
        changes = []
        if self.running:
            received = max(0., self.clock() - self.origin)
            for identifier, value in project.snapshot().items():
                if value != old[identifier]:
                    changes.append({"sequence": len(self.actions) + len(changes), "kind": "config",
                                    "parameter": identifier, "old": old[identifier], "new": value,
                                    "received_seconds": received, "effective_seconds": self.onset,
                                    "beat": self.tick / 2})

        def apply() -> None:
            self.session = session
            self.pattern = pattern
            if self.running:
                self.take_project = project
                self.actions.extend(changes)

        return apply

    def replace_project(self, project: Project) -> None:
        pattern = self._pattern(project)
        restart = self.running
        self.stop()
        self.session = Session(project, self.session.profile)
        self.pattern = pattern
        if restart:
            self.start()

    def stop(self) -> None:
        if self.running:
            self.running = False
            self.stop_time = max(0., self.clock() - self.origin)
            try:
                self.output.close(tuple(self.active))
            finally:
                self.active.clear()

    def recording(self) -> str:
        if self.running:
            raise ValueError("stop the transport before saving")
        events = tuple(replace(e, duration=min(e.duration, self.stop_time - e.time))
                       for e in self.events if e.time < self.stop_time)
        return encode(events, {
            "generator": "m1-continuous-demo-v1",
            "project": self.take_project.snapshot(),
            "live_controls": {"schema_version": 1, "stop_seconds": self.stop_time,
                              "initial_project": self.initial_project.snapshot(),
                              "changes": self.actions},
        })
