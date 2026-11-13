"""Continuous composition and timed controls, independent of audio I/O."""

from dataclasses import asdict, replace
import math
from typing import Callable, Protocol

from dinad.config import Profile, Project
from dinad.controls import Session
from dinad.demo import compose
from dinad.events import Event
from dinad.recording import encode
from dinad.modules import ParameterValue


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


# Each entry adds parts to the drone and pad for one 16-beat cycle.
FORM_ARPS = (
    frozenset(),
    frozenset({"arp.slow"}),
    frozenset({"arp.slow", "arp.fast"}),
    frozenset({"arp.fast"}),
)


class Transport:
    """One looping take. A batch is committed after the output acknowledges it."""

    def __init__(self, project: Project, profile: Profile, output: LiveOutput,
                 clock: Callable[[], float], lead: float = .05) -> None:
        self.session = Session(project, profile)
        self.output = output
        self.clock = clock
        self.lead = lead
        self.before_onset: Callable[[Project], None] | None = None
        self.output_metadata: Callable[[float, float], dict] | None = None
        self.running = False
        self.active_seed: int | None = None
        self.origin = 0.
        self.tick = 0
        self.onset = 0.
        self.active: dict[int, float] = {}
        self.events: list[Event] = []
        self.actions: list[dict] = []
        self.stop_time = 0.
        self.initial_project = self.take_project = project
        self.pattern = self._pattern(project)

    def _pattern(self, project: Project, tick: int = 0) -> tuple[Event, ...]:
        seed = (project.seed + tick // 32) % 2**63 if project.evolve else project.seed
        pattern = compose(seed, project.anchor, 60, tension=project.tension, motif=project.motif)
        self.output.validate(pattern)
        return pattern

    def start(self) -> None:
        if self.running:
            raise ValueError("transport is already running")
        self.pattern = self._pattern(self.session.project)
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

    def _space_fast_arp(self, notes: list[Event], releases: tuple[int, ...]) -> list[Event]:
        releasing = set(releases)
        sustained = [
            self.events[node - 1002].pitch for node in self.active
            if node not in releasing and self.events[node - 1002].instrument in ("drone", "pad")
        ]
        sustained.extend(note.pitch for note in notes if note.instrument in ("drone", "pad"))
        return [note for note in notes if note.instrument != "arp.fast" or
                all(not math.isclose(abs((note.pitch - pitch + 6) % 12 - 6), 1., abs_tol=1e-8)
                    for pitch in sustained)]

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
            if self.session.project.evolve and self.tick and self.tick % 32 == 0:
                self.pattern = self._pattern(self.session.project, self.tick)
            beat = (self.tick % 32) / 2
            arps = FORM_ARPS[(self.tick // 32) % len(FORM_ARPS)] if self.session.project.form else FORM_ARPS[2]
            notes = [replace(note, time=at, duration=note.duration * seconds_per_beat)
                     for note in self.pattern
                     if note.time == beat and (note.instrument in ("drone", "pad") or note.instrument in arps)]
            if self.session.project.voice_space:
                notes = self._space_fast_arp(notes, releases)
            starts = tuple(enumerate(notes, 1002 + len(self.events)))
        try:
            if at == self.onset and self.before_onset is not None:
                self.before_onset(self.session.project)
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
            self.active_seed = self.session.project.seed
            self.tick += 1
            self.onset += seconds_per_beat / 2

    def control(self, command: str, received: float | None = None) -> str:
        pattern = self.pattern

        def validate(candidate: Project) -> None:
            nonlocal pattern
            pattern = self._pattern(candidate, self.tick if self.running else 0)

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
        pattern = self._pattern(project, self.tick if self.running else 0)
        session = Session(project, profile)
        old = self.session.project.snapshot()
        changes = []
        if self.running:
            received = max(0., self.clock() - self.origin)
            old["generation.evolve"] = self.session.project.evolve
            for identifier, value in {**project.snapshot(), "generation.evolve": project.evolve}.items():
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
            self.active_seed = None
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
        output_metadata = self.output_metadata(self.origin, self.stop_time) if self.output_metadata else {}
        return encode(events, {
            **output_metadata,
            "generator": "m1-continuous-demo-v1",
            "project": self.take_project.snapshot(),
            **({"generation_evolves": True} if self.initial_project.evolve else {}),
            "live_controls": {"schema_version": 1, "stop_seconds": self.stop_time,
                              "initial_project": self.initial_project.snapshot(),
                              "changes": self.actions},
        })


class TakePlayback:
    """Schedule resolved notes and recorded automation through a separate live output."""

    def __init__(self, output: LiveOutput, clock: Callable[[], float],
                 events: tuple[Event, ...], end: float,
                 automation: list[tuple[float, dict[str, ParameterValue]]],
                 set_controls: Callable[[dict[str, ParameterValue]], None]):
        output.validate(events)
        self.output = output
        self.clock = clock
        self.lead = output.lead
        self.set_controls = set_controls
        self.active: set[int] = set()
        batches = {}

        def batch(at):
            return batches.setdefault(at, ([], [], {}))

        for node, event in enumerate(events, 1002):
            batch(event.time)[1].append((node, event))
            batch(event.time + event.duration)[0].append(node)
        for at, values in automation:
            batch(at)[2].update(values)
        self.batches = iter(sorted(batches.items()))
        self.next_batch = next(self.batches, None)
        self.end = end
        self.output.open()
        self.origin = self.clock() + self.lead
        self.running = True

    @property
    def deadline(self) -> float:
        if self.next_batch is None:
            # The worker subtracts lead when waking. Close only after playback.
            return self.origin + self.end + self.lead
        return self.origin + self.next_batch[0]

    def advance(self) -> None:
        if self.next_batch is None:
            self.stop()
            return
        at, (releases, starts, controls) = self.next_batch
        try:
            if controls:
                self.set_controls(controls)
            self.output.send(self.origin + at, tuple(releases), tuple(starts))
        except (ValueError, OSError, RuntimeError, OverflowError):
            self.stop()
            raise
        self.active.difference_update(releases)
        self.active.update(node for node, _ in starts)
        self.next_batch = next(self.batches, None)

    def stop(self) -> None:
        if self.running:
            self.running = False
            self.output.close(tuple(sorted(self.active)))
