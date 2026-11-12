"""Explicit headless providers and DINAD-owned module configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from dinad.config import PARAMETERS, Project, _read, _table
from dinad.engine import MockBackend
from dinad.modules import (
    BindingTarget, Capability, ModuleAdapter, ModuleDescriptor, ParameterDescriptor, ParameterType,
    ParameterValue, SemanticBinding, _text,
)
from dinad.supercollider import Settings, SuperColliderBackend
from dinad.lv2_epiano import EPianoBackend, LIVE_SETTINGS as EPIANO_LIVE_SETTINGS, SETTINGS as EPIANO_SETTINGS


@dataclass(frozen=True)
class Registration:
    descriptor: ModuleDescriptor
    factory: Callable[[Mapping[str, ParameterValue]], ModuleAdapter]
    settings: frozenset[str] = frozenset()
    live_settings: frozenset[str] = frozenset()

    def validate_settings(self, values: Mapping[str, ParameterValue]) -> None:
        for name, value in values.items():
            if name not in self.settings:
                raise ValueError(f"{self.descriptor.identifier}: unknown constructor setting {name!r}")
            self.descriptor.parameter(name).validate(value)


class Registry:
    def __init__(self, entries: tuple[Registration, ...]) -> None:
        index = {entry.descriptor.identifier: entry for entry in entries}
        if len(index) != len(entries):
            raise ValueError("duplicate module ID")
        self._entries = MappingProxyType(index)

    @property
    def descriptors(self) -> tuple[ModuleDescriptor, ...]:
        return tuple(entry.descriptor for entry in self._entries.values())

    def require(self, identifier: str, version: str | None = None) -> Registration:
        _text(identifier, "module ID")
        if identifier not in self._entries:
            raise ValueError(f"unknown module {identifier!r}")
        entry = self._entries[identifier]
        if version is not None and version != entry.descriptor.version:
            raise ValueError(f"{identifier}: requested version {version!r}, available "
                             f"{entry.descriptor.version!r}; explicit migration required")
        return entry


BUILTINS = Registry((
    Registration(MockBackend.descriptor, lambda settings: MockBackend()),
    Registration(SuperColliderBackend.descriptor,
                 lambda settings: SuperColliderBackend(Settings(**settings)),
                 frozenset({"sample_rate", "tail", "preroll"}), frozenset({"sample_rate"})),
    Registration(EPianoBackend.descriptor, EPianoBackend, EPIANO_SETTINGS, EPIANO_LIVE_SETTINGS),
))

OPERATIONS = MappingProxyType({
    "replay": Capability.STAGED_NOTES,
    "render": Capability.OFFLINE_RENDER,
    "play": Capability.SCHEDULED_PLAYBACK,
    "live": Capability.CONTINUOUS_TRANSPORT,
})


@dataclass(frozen=True)
class ModuleSelection:
    registration: Registration
    settings: Mapping[str, ParameterValue]

    def __post_init__(self) -> None:
        values = dict(self.settings)
        self.registration.validate_settings(values)
        object.__setattr__(self, "settings", MappingProxyType(values))

    def create(self, **overrides: ParameterValue) -> ModuleAdapter:
        values = {**self.settings, **overrides}
        self.registration.validate_settings(values)
        adapter = self.registration.factory(values)
        if adapter.descriptor != self.registration.descriptor:
            adapter.close()
            raise ValueError("adapter descriptor differs from its registration")
        return adapter


@dataclass(frozen=True)
class ModuleConfig:
    operations: Mapping[str, ModuleSelection]
    bindings: tuple[SemanticBinding, ...]
    registry: Registry

    def resolve(self, project: Project) -> tuple[ParameterUpdate, ...]:
        updates = []
        for binding in self.bindings:
            value = getattr(project, PARAMETERS[binding.semantic_id].attribute)
            for target in binding.targets:
                descriptor = self.registry.require(target.module_id, target.module_version).descriptor
                parameter = descriptor.parameter(target.native_id)
                updates.append(ParameterUpdate(binding.semantic_id, target.module_id, target.module_version,
                                               target.native_id, _mapped_value(target, parameter, value)))
        return tuple(updates)


@dataclass(frozen=True)
class ParameterUpdate:
    semantic_id: str
    module_id: str
    module_version: str
    native_id: str
    value: ParameterValue


def _mapped_value(target: BindingTarget, parameter: ParameterDescriptor, value: int | float) -> ParameterValue:
    if parameter.value_type == ParameterType.INTEGER:
        mapped = int(value) * int(target.scale) + int(target.offset)
    else:
        mapped = value * target.scale + target.offset
    parameter.validate(mapped)
    return mapped


def _binding(semantic_id: str, targets: object, registry: Registry) -> SemanticBinding:
    if semantic_id not in PARAMETERS:
        raise ValueError(f"unknown DINAD semantic parameter {semantic_id!r}")
    if not isinstance(targets, list):
        raise ValueError(f"{semantic_id}: expected an array of binding targets")
    source = PARAMETERS[semantic_id]
    resolved = []
    for raw in targets:
        table = _table(raw, {"module_id", "module_version", "native_id", "scale", "offset"},
                       f"bindings.{semantic_id}")
        target = BindingTarget(**table)
        module = registry.require(target.module_id, target.module_version).descriptor
        target.validate_target(module)
        parameter = module.parameter(target.native_id)
        if parameter.value_type not in (ParameterType.FLOAT, ParameterType.INTEGER):
            raise ValueError(f"{semantic_id}: numeric control requires a numeric target")
        if parameter.value_type == ParameterType.INTEGER and (
            not (source.integer or target.scale == 0)
            or target.scale % 1 != 0
            or target.offset % 1 != 0
        ):
            raise ValueError(f"{semantic_id}: mapping must preserve integer values")
        for endpoint in (source.minimum, source.maximum):
            _mapped_value(target, parameter, endpoint)
        resolved.append(target)
    return SemanticBinding(semantic_id, tuple(resolved))


def load_module_config(path: Path, registry: Registry = BUILTINS) -> ModuleConfig:
    path = Path(path)
    document = _read(path, {"operations", "bindings"})
    try:
        operations = {}
        for name, raw in _table(document.get("operations", {}), set(OPERATIONS), "operations").items():
            table = _table(raw, {"module", "version", "settings"}, f"operations.{name}")
            identifier, version = table.get("module"), table.get("version")
            _text(version, f"operations.{name}.version")
            entry = registry.require(identifier, version)
            entry.descriptor.require(OPERATIONS[name])
            entry.descriptor.require(Capability.STAGED_NOTES)
            allowed = entry.live_settings if name == "live" else entry.settings
            settings = _table(table.get("settings", {}), set(allowed), f"operations.{name}.settings")
            operations[name] = ModuleSelection(entry, settings)
        raw_bindings = document.get("bindings", {})
        if not isinstance(raw_bindings, dict):
            raise ValueError("bindings must be a table")
        bindings = tuple(_binding(identifier, targets, registry) for identifier, targets in raw_bindings.items())
        targets = [(t.module_id, t.native_id) for binding in bindings for t in binding.targets]
        if len(set(targets)) != len(targets):
            raise ValueError("multiple semantic bindings write to the same native target")
        return ModuleConfig(MappingProxyType(operations), bindings, registry)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
