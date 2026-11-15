"""Timed control capture and resolved replay; no audio clock or DSP callback."""

from dataclasses import asdict, dataclass
import shlex
from pathlib import Path
import tomllib

from dinad.config import PARAMETERS, Profile, Project, _read, _table
from dinad.controls import Session, parse_action
from dinad.demo import compose
from dinad.engine import Event
from dinad.objects import _finite
from dinad.recording import encode


@dataclass(frozen=True)
class Segment:
    seconds: float
    beat: float
    project: Project


class Performance:
    """A finite 16-beat demo whose successful controls take effect at timestamps.

    New onsets use the current anchor/seed/tempo. Existing notes retain their
    onset pitch and seconds-duration; stopping clips outstanding note gates.
    """

    def __init__(self, project: Project, profile: Profile | None = None):
        self.session = Session(project, profile)
        self.initial = project
        self._segments = [Segment(0.0, 0.0, project)]
        self._actions: list[dict] = []
        self._last_time = 0.0
        self._stopped: float | None = None

    @property
    def end_time(self) -> float:
        segment = self._segments[-1]
        return segment.seconds + (16 - segment.beat) * (60 / segment.project.tempo)

    @property
    def actions(self) -> tuple[dict, ...]:
        return tuple(dict(action) for action in self._actions)

    def execute(self, command: str, seconds: float) -> str:
        _finite(seconds, "control timestamp")
        if seconds < self._last_time:
            raise ValueError("control timestamps must be non-negative and ordered")
        if self._stopped is not None:
            raise ValueError("performance has stopped")
        if not isinstance(command, str):
            raise ValueError("command must be text")
        tokens = shlex.split(command)
        if tokens and tokens[0] == "key":
            if len(tokens) != 2:
                raise ValueError("expected: key TOKEN")
            command = self.session.profile.command_for(tokens[1])
        action = parse_action(command)
        if action.kind == "generate":
            raise ValueError("generate is unavailable during capture; finish to save the performance")
        if seconds > self.end_time or (seconds == self.end_time and action.kind not in ("inspect", "quit")):
            raise ValueError("control timestamp is at or after the performance end")
        segment = self._segments[-1]
        beat = segment.beat + (seconds - segment.seconds) * segment.project.tempo / 60
        # Session validates the whole candidate before changing its state/history.
        result = self.session.execute(command)
        if action.kind in ("set", "adjust", "undo"):
            change = asdict(self.session.changes[-1])
            self._actions.append({**change, "seconds": float(seconds), "beat": beat})
            self._segments.append(Segment(float(seconds), beat, self.session.project))
        elif action.kind == "quit":
            self._stopped = float(seconds)
        self._last_time = float(seconds)
        return result

    def set_at_beat(self, beat: float, parameter: str, value: float) -> str:
        """Schedule a semantic set against musical time and the current tempo."""
        _finite(beat, "controller beat")
        if not isinstance(parameter, str) or parameter not in PARAMETERS:
            raise ValueError(f"unknown semantic parameter {parameter!r}")
        PARAMETERS[parameter].validate(value, parameter)
        segment = self._segments[-1]
        if not segment.beat <= beat < 16:
            raise ValueError("controller beats must be ordered and in the 16-beat performance")
        seconds = segment.seconds + (beat - segment.beat) * (60 / segment.project.tempo)
        result = self.execute(f"set {parameter} {value}", seconds)
        # Keep the requested beat exact when converting back from seconds.
        self._segments[-1] = Segment(seconds, float(beat), self.session.project)
        self._actions[-1]["beat"] = float(beat)
        return result

    def events(self, stop: float | None = None) -> tuple[Event, ...]:
        if stop is None:
            stop = self._stopped if self._stopped is not None else self.end_time
        _finite(stop, "stop timestamp")
        if not self._last_time <= stop <= self.end_time:
            raise ValueError("stop must follow accepted controls and not exceed the performance end")
        if self._stopped is not None and stop != self._stopped:
            raise ValueError("stop differs from the captured quit timestamp")
        resolved = []
        for index, segment in enumerate(self._segments):
            if segment.seconds >= stop:
                break
            next_segment = self._segments[index + 1] if index + 1 < len(self._segments) else None
            if next_segment is not None and next_segment.seconds <= stop:
                end_beat = next_segment.beat
            else:
                end_beat = segment.beat + (stop - segment.seconds) * segment.project.tempo / 60
            # At 60 BPM this existing composition's timestamps are beat values.
            for event in compose(segment.project.seed, segment.project.anchor, 60,
                                 tension=segment.project.tension, motif=segment.project.motif):
                if segment.beat <= event.time < end_beat:
                    time = segment.seconds + (event.time - segment.beat) * (60 / segment.project.tempo)
                    duration = event.duration * (60 / segment.project.tempo)
                    if time + duration > stop:
                        duration = stop - time
                    if duration > 0:
                        resolved.append(Event(event.instrument, event.pitch, time, duration))
        return tuple(sorted(resolved, key=lambda event: event.time))

    def recording(self, stop: float | None = None) -> str:
        if stop is None:
            stop = self._stopped if self._stopped is not None else self.end_time
        events = self.events(stop)
        return encode(events, {
            "generator": "m0-timed-demo-v1", "project_schema_version": 1,
            "project": self.session.project.snapshot(),
            "profile": self.session.profile.snapshot(),
            "timed_controls": {
                "schema_version": 1, "time_unit": "seconds",
                "initial_project": self.initial.snapshot(), "stop_seconds": stop,
                "changes": list(self.actions),
                "application": "future onsets; sustained notes retain pitch and gate duration; stop clips gates",
                "clock": "relative capture or scripted time; not audio-clock synchronized",
            },
        })


def load_performance(path: Path, project: Project, profile: Profile | None = None) -> Performance:
    """Validate a complete TOML control timeline before producing any output."""
    path = Path(path)
    document = _read(path, {"actions", "controller"})
    try:
        if "controller" in document:
            if "actions" in document:
                raise ValueError("choose either actions or controller")
            performance = Performance(project, profile)
            for beat, parameter, value in expand_controller(document["controller"]):
                performance.set_at_beat(beat, parameter, value)
            return performance
        actions = document.get("actions", [])
        if not isinstance(actions, list):
            raise ValueError("actions must be an array of tables")
        performance = Performance(project, profile)
        for index, raw in enumerate(actions):
            item = _table(raw, {"seconds", "command"}, f"actions[{index}]")
            if set(item) != {"seconds", "command"}:
                raise ValueError(f"actions[{index}] requires seconds and command")
            if not isinstance(item["command"], str):
                raise ValueError(f"actions[{index}].command must be text")
            performance.execute(item["command"], item["seconds"])
        return performance
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def expand_controller(entries: object) -> tuple[tuple[float, str, float], ...]:
    if not isinstance(entries, list) or len(entries) > 4096:
        raise ValueError("controller must be an array of at most 4096 tables")
    schedule = []
    for index, raw in enumerate(entries):
        item = _table(raw, {"beat", "parameter", "value", "end_beat", "end_value", "steps"},
                      f"controller[{index}]")
        if not {"beat", "parameter", "value"} <= item.keys():
            raise ValueError(f"controller[{index}] requires beat, parameter and value")
        beat, parameter, value = item["beat"], item["parameter"], item["value"]
        _finite(beat, "controller beat")
        if not 0 <= beat < 16 or (index and beat < previous_beat):
            raise ValueError("controller beats must be ordered and in the 16-beat performance")
        previous_beat = beat
        ramp_fields = item.keys() & {"end_beat", "end_value", "steps"}
        if not ramp_fields:
            schedule.append((beat, parameter, value))
        else:
            if ramp_fields != {"end_beat", "end_value", "steps"}:
                raise ValueError("ramp requires end_beat, end_value and steps")
            end, target, steps = item["end_beat"], item["end_value"], item["steps"]
            _finite(end, "ramp end beat")
            if not beat < end < 16 or type(steps) is not int or not 1 <= steps <= 256:
                raise ValueError("ramp needs a later beat before 16 and 1 to 256 steps")
            descriptor = PARAMETERS.get(parameter) if isinstance(parameter, str) else None
            if descriptor is None or descriptor.integer:
                raise ValueError("ramp requires a continuous semantic parameter")
            descriptor.validate(value, parameter)
            descriptor.validate(target, parameter)
            schedule.extend((beat + (end - beat) * (step / steps), parameter,
                             value + (target - value) * (step / steps))
                            for step in range(steps))
            schedule.append((end, parameter, target))
        if len(schedule) > 4096:
            raise ValueError("controller schedule exceeds 4096 resolved operations")
    for _, parameter, value in schedule:
        if not isinstance(parameter, str) or parameter not in PARAMETERS:
            raise ValueError(f"unknown semantic parameter {parameter!r}")
        PARAMETERS[parameter].validate(value, parameter)
    return tuple(sorted(schedule, key=lambda action: action[0]))


def load_controller(path: Path) -> tuple[tuple[float, str, float], ...]:
    document = _read(path, {"controller"})
    try:
        return expand_controller(document.get("controller"))
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
