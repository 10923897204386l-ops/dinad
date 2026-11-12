"""Versioned project settings and immutable keyboard profiles (TOML)."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
import tomllib

from dinad.objects import _finite


@dataclass(frozen=True)
class Parameter:
    attribute: str
    label: str
    unit: str
    minimum: float
    maximum: float
    integer: bool = False
    applies: str = "next generation"
    smoothing: str = "none (mock)"

    def validate(self, value: float, identifier: str) -> None:
        _finite(value, identifier)
        if self.integer and type(value) is not int:
            raise ValueError(f"{identifier} must be an integer")
        if not self.minimum <= value <= self.maximum:
            raise ValueError(f"{identifier} must be between {self.minimum} and {self.maximum}")


PARAMETERS = MappingProxyType({
    "composition.anchor": Parameter("anchor", "Anchor", "semitones (C4=60)", 0, 127),
    "transport.tempo_bpm": Parameter("tempo", "Tempo", "BPM", 1, 1000),
    "generation.seed": Parameter("seed", "Seed", "integer", 0, 2**63 - 1, integer=True),
})


@dataclass(frozen=True)
class Project:
    anchor: float = 48
    tempo: float = 120
    seed: int = 0
    evolve: bool = False

    def __post_init__(self) -> None:
        if type(self.evolve) is not bool:
            raise ValueError("generation.evolve must be a boolean")
        for identifier, parameter in PARAMETERS.items():
            parameter.validate(getattr(self, parameter.attribute), identifier)
        object.__setattr__(self, "anchor", float(self.anchor))
        object.__setattr__(self, "tempo", float(self.tempo))

    def snapshot(self) -> dict:
        return {identifier: getattr(self, parameter.attribute)
                for identifier, parameter in PARAMETERS.items()}


@dataclass(frozen=True)
class Profile:
    bindings: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        from dinad.controls import parse_action

        bindings = tuple(tuple(pair) for pair in self.bindings)
        seen = set()
        for pair in bindings:
            if len(pair) != 2:
                raise ValueError("profile bindings must be key/command pairs")
            key, command = pair
            if not isinstance(key, str) or not key or any(char.isspace() for char in key):
                raise ValueError("profile keys must be non-empty tokens without whitespace")
            if key in seen:
                raise ValueError(f"duplicate profile key: {key}")
            seen.add(key)
            # File writes and recursive key mappings are not profile actions.
            try:
                parse_action(command)
            except ValueError as exc:
                raise ValueError(f"binding {key!r}: {exc}") from exc
        object.__setattr__(self, "bindings", bindings)

    def command_for(self, key: str) -> str:
        for candidate, command in self.bindings:
            if candidate == key:
                return command
        raise ValueError(f"unmapped profile key: {key}")

    def snapshot(self) -> dict:
        return {"schema_version": 1, "bindings": dict(self.bindings)}


def _table(value: object, allowed: set[str], path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a table")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")
    return value


def _read(path: Path, sections: set[str]) -> dict:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
        _table(document, sections | {"schema_version"}, "document")
        version = document.get("schema_version")
        if type(version) is not int or version != 1:
            raise ValueError("schema_version must be integer 1")
        return document
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def load_project(path: Path) -> Project:
    path = Path(path)
    document = _read(path, {"composition", "transport", "generation"})
    values = {}
    try:
        for section, identifier in (
            ("composition", "composition.anchor"),
            ("transport", "transport.tempo_bpm"),
            ("generation", "generation.seed"),
        ):
            field = identifier.split(".")[1]
            allowed = {field, "evolve"} if section == "generation" else {field}
            table = _table(document.get(section, {}), allowed, section)
            if section == "generation" and "evolve" in table:
                values["evolve"] = table["evolve"]
            if field in table:
                values[PARAMETERS[identifier].attribute] = table[field]
        return Project(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def load_profile(path: Path) -> Profile:
    path = Path(path)
    document = _read(path, {"bindings"})
    try:
        bindings = document.get("bindings", {})
        if not isinstance(bindings, dict):
            raise ValueError("bindings must be a table")
        return Profile(tuple(bindings.items()))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: bindings: {exc}") from exc
