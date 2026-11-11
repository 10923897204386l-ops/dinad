"""Validated semantic commands and transactional mock composition sessions."""

from dataclasses import asdict, dataclass, replace
import json
import shlex
from typing import Callable

from dinad.config import PARAMETERS, Profile, Project
from dinad.demo import compose
from dinad.engine import MockBackend, replay
from dinad.objects import _finite
from dinad.recording import encode


@dataclass(frozen=True)
class Action:
    kind: str
    parameter: str | None = None
    value: float | int | None = None


def parse_action(command: str) -> Action:
    """Parse commands without eval, shell expansion or arbitrary execution."""
    if not isinstance(command, str):
        raise ValueError("command must be text")
    tokens = shlex.split(command)
    if len(tokens) == 1 and tokens[0] in ("inspect", "undo", "generate", "quit"):
        return Action(tokens[0])
    if len(tokens) != 3 or tokens[0] not in ("set", "adjust"):
        raise ValueError("expected: inspect, set ID VALUE, adjust ID DELTA, undo, generate, or quit")
    kind, identifier, raw = tokens
    if identifier not in PARAMETERS:
        raise ValueError(f"unknown parameter: {identifier}")
    parameter = PARAMETERS[identifier]
    try:
        value = int(raw) if parameter.integer else float(raw)
    except ValueError as exc:
        raise ValueError(f"{identifier}: invalid numeric value {raw!r}") from exc
    _finite(value, identifier)
    if kind == "set":
        parameter.validate(value, identifier)
    return Action(kind, identifier, value)


@dataclass(frozen=True)
class Change:
    sequence: int
    kind: str
    parameter: str
    old: float | int
    new: float | int


class Session:
    """Validated state changes; no running audio transport is implied."""

    def __init__(self, project: Project, profile: Profile | None = None) -> None:
        profile = Profile() if profile is None else profile
        if not isinstance(project, Project) or not isinstance(profile, Profile):
            raise TypeError("session requires a Project and Profile")
        self._project = project
        self._initial = project
        self.profile = profile
        self._changes: list[Change] = []
        self._undo: list[Change] = []

    @property
    def project(self) -> Project:
        return self._project

    @property
    def changes(self) -> tuple[Change, ...]:
        return tuple(self._changes)

    def execute(self, command: str, *, validate: Callable[[Project], None] | None = None) -> str:
        tokens = shlex.split(command)
        if tokens and tokens[0] == "key":
            if len(tokens) != 2:
                raise ValueError("expected: key TOKEN")
            command = self.profile.command_for(tokens[1])
        action = parse_action(command)
        if action.kind == "inspect":
            return json.dumps(self.project.snapshot(), sort_keys=True)
        if action.kind == "quit":
            return "quit"
        if action.kind == "generate":
            return self.recording()
        if action.kind == "undo":
            if not self._undo:
                raise ValueError("nothing to undo")
            original = self._undo[-1]
            parameter = PARAMETERS[original.parameter]
            candidate = replace(self.project, **{parameter.attribute: original.old})
            change = Change(len(self._changes), "undo", original.parameter, original.new, original.old)
            if validate is not None:
                validate(candidate)
            self._undo.pop()
        else:
            parameter = PARAMETERS[action.parameter]
            old = getattr(self.project, parameter.attribute)
            value = action.value if action.kind == "set" else old + action.value
            # Validate the entire candidate before mutating state or either history.
            candidate = replace(self.project, **{parameter.attribute: value})
            change = Change(len(self._changes), action.kind, action.parameter, old, value)
            if validate is not None:
                validate(candidate)
            self._undo.append(change)
        self._project = candidate
        self._changes.append(change)
        return json.dumps(asdict(change), sort_keys=True)

    def recording(self) -> str:
        project = self.project
        backend = MockBackend()
        replay(compose(project.seed, project.anchor, project.tempo), backend)
        return encode(backend.events, {
            "generator": "m0-demo-v1",
            "project_schema_version": 1,
            "project": project.snapshot(),
            "profile": self.profile.snapshot(),
            "semantic_actions": {
                "schema_version": 1,
                "initial_project": self._initial.snapshot(),
                "changes": [asdict(change) for change in self.changes],
                "applies": "next generation",
            },
        })
