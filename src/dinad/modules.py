"""M1.1 headless module contracts; no discovery, hosting or binding execution."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, Self, TypeAlias, runtime_checkable

from dinad.events import Event
from dinad.objects import _finite

if TYPE_CHECKING:
    from dinad.transport import LiveOutput


class ModuleKind(str, Enum):
    MUSICAL = "musical"
    PERFORMANCE = "performance"
    INSTRUMENT = "instrument"
    PRODUCTION = "production"


class Capability(str, Enum):
    STAGED_NOTES = "staged_notes"
    OFFLINE_RENDER = "offline_render"
    SCHEDULED_PLAYBACK = "scheduled_playback"
    CONTINUOUS_TRANSPORT = "continuous_transport"
    PARAMETER_READ = "parameter_read"
    PARAMETER_WRITE = "parameter_write"
    STATE_SAVE = "state_save"
    STATE_RESTORE = "state_restore"


class ParameterType(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"
    STRING = "string"


class AutomationRate(str, Enum):
    NONE = "none"
    BLOCK = "block"
    SAMPLE = "sample"


ParameterValue: TypeAlias = float | int | bool | str


def _text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")


@dataclass(frozen=True)
class ParameterDescriptor:
    native_id: str
    label: str
    value_type: ParameterType
    default: ParameterValue | None = None  # None explicitly means unknown.
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None
    enum_values: tuple[str, ...] = ()
    readable: bool = False
    writable: bool = False
    automation: AutomationRate = AutomationRate.NONE
    smoothing_seconds: float | None = None
    max_update_hz: float | None = None

    def __post_init__(self) -> None:
        _text(self.native_id, "native parameter ID")
        _text(self.label, "parameter label")
        if not isinstance(self.value_type, ParameterType) or not isinstance(self.automation, AutomationRate):
            raise ValueError("parameter type and automation rate must be typed enum values")
        if type(self.readable) is not bool or type(self.writable) is not bool:
            raise ValueError("parameter access flags must be booleans")
        object.__setattr__(self, "enum_values", tuple(self.enum_values))
        if self.unit is not None:
            _text(self.unit, "parameter unit")
        numeric = self.value_type in (ParameterType.FLOAT, ParameterType.INTEGER)
        if not numeric and (self.minimum is not None or self.maximum is not None):
            raise ValueError("only numeric parameters have numeric bounds")
        for bound in (self.minimum, self.maximum):
            if bound is not None:
                _finite(bound, "parameter bound")
                if self.value_type == ParameterType.INTEGER and type(bound) is not int:
                    raise ValueError("integer parameter bounds must be integers")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum exceeds maximum")
        if self.value_type == ParameterType.ENUM:
            if not self.enum_values or any(not isinstance(v, str) or not v for v in self.enum_values):
                raise ValueError("enum parameters require non-empty text choices")
            if len(set(self.enum_values)) != len(self.enum_values):
                raise ValueError("enum choices must be unique")
        elif self.enum_values:
            raise ValueError("enum choices require an enum parameter")
        if self.automation != AutomationRate.NONE and not self.writable:
            raise ValueError("automation requires a writable parameter")
        for name in ("smoothing_seconds", "max_update_hz"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)
                if not self.writable or not numeric or value < 0 or (name == "max_update_hz" and value == 0):
                    raise ValueError(f"{name} requires writable numeric parameters and a valid bound")
        if self.default is not None:
            self.validate(self.default)

    def validate(self, value: ParameterValue) -> None:
        if self.value_type in (ParameterType.FLOAT, ParameterType.INTEGER):
            _finite(value, self.native_id)
            if self.value_type == ParameterType.INTEGER and type(value) is not int:
                raise ValueError(f"{self.native_id} requires an integer")
            if ((self.minimum is not None and value < self.minimum) or
                    (self.maximum is not None and value > self.maximum)):
                raise ValueError(f"{self.native_id} is outside its bounds")
        elif self.value_type == ParameterType.BOOLEAN:
            if type(value) is not bool:
                raise ValueError(f"{self.native_id} requires a boolean")
        elif not isinstance(value, str) or (self.value_type == ParameterType.ENUM and value not in self.enum_values):
            raise ValueError(f"{self.native_id} requires a supported text value")


@dataclass(frozen=True)
class Dependency:
    name: str
    required_for: frozenset[Capability]

    def __post_init__(self) -> None:
        _text(self.name, "dependency")
        object.__setattr__(self, "required_for", frozenset(self.required_for))
        if not self.required_for or any(not isinstance(c, Capability) for c in self.required_for):
            raise ValueError("dependencies require typed capabilities")


@dataclass(frozen=True)
class AudioPort:
    identifier: str
    direction: Literal["input", "output"]  # 'input' or 'output'; no device or graph discovery here.
    channels: int

    def __post_init__(self) -> None:
        _text(self.identifier, "audio port ID")
        if self.direction not in ("input", "output") or type(self.channels) is not int or self.channels <= 0:
            raise ValueError("audio ports require input/output direction and positive channel count")


class UnsupportedCapability(ValueError):
    """An explicit unsupported operation; never a request to launch a vendor GUI."""


@dataclass(frozen=True)
class ModuleDescriptor:
    identifier: str
    version: str
    kind: ModuleKind
    capabilities: frozenset[Capability]
    parameters: tuple[ParameterDescriptor, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    audio_ports: tuple[AudioPort, ...] = ()
    limitations: tuple[str, ...] = ()
    state_schema: str | None = None  # Versioned state compatibility ID, if supported.
    contract_version: int = 1
    headless: bool = True

    def __post_init__(self) -> None:
        _text(self.identifier, "module ID")
        _text(self.version, "module version")
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("unsupported module contract version")
        if self.headless is not True:
            raise ValueError("DINAD modules must be headless; vendor GUIs are unsupported")
        if not isinstance(self.kind, ModuleKind):
            raise ValueError("module kind must be typed")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        if any(not isinstance(c, Capability) for c in self.capabilities):
            raise ValueError("module capabilities must be typed")
        for name, value_type in (("parameters", ParameterDescriptor), ("dependencies", Dependency), ("audio_ports", AudioPort)):
            values = tuple(getattr(self, name))
            if any(not isinstance(v, value_type) for v in values):
                raise ValueError(f"invalid module {name}")
            object.__setattr__(self, name, values)
        object.__setattr__(self, "limitations", tuple(self.limitations))
        for item in self.limitations:
            _text(item, "module limitation")
        for values in ([p.native_id for p in self.parameters], [p.identifier for p in self.audio_ports]):
            if len(set(values)) != len(values):
                raise ValueError("duplicate module parameter or port ID")
        for parameter in self.parameters:
            if parameter.readable:
                self.require(Capability.PARAMETER_READ)
            if parameter.writable:
                self.require(Capability.PARAMETER_WRITE)
        if any(not dependency.required_for <= self.capabilities for dependency in self.dependencies):
            raise ValueError("dependency references an undeclared capability")
        stateful = bool(self.capabilities & {Capability.STATE_SAVE, Capability.STATE_RESTORE})
        if stateful != (self.state_schema is not None):
            raise ValueError("state capabilities require an explicit versioned state schema")
        if self.state_schema is not None:
            _text(self.state_schema, "state schema")

    def require(self, capability: Capability) -> None:
        if not isinstance(capability, Capability):
            raise ValueError("capability must be typed")
        if capability not in self.capabilities:
            raise UnsupportedCapability(f"{self.identifier}@{self.version}: unsupported capability {capability.value}")

    def parameter(self, native_id: str) -> ParameterDescriptor:
        for parameter in self.parameters:
            if parameter.native_id == native_id:
                return parameter
        raise UnsupportedCapability(f"{self.identifier}@{self.version}: unknown native parameter {native_id!r}")


@dataclass(frozen=True)
class BindingTarget:
    module_id: str
    module_version: str
    native_id: str
    scale: float = 1
    offset: float = 0

    def __post_init__(self) -> None:
        for name in ("module_id", "module_version", "native_id"):
            _text(getattr(self, name), name)
        _finite(self.scale, "binding scale")
        _finite(self.offset, "binding offset")

    def validate_target(self, module: ModuleDescriptor) -> None:
        if (module.identifier, module.version) != (self.module_id, self.module_version):
            raise ValueError("binding module identity/version mismatch; explicit migration required")
        parameter = module.parameter(self.native_id)
        module.require(Capability.PARAMETER_WRITE)
        if not parameter.writable:
            raise UnsupportedCapability(f"{self.module_id}: parameter {self.native_id!r} is read-only")
        if parameter.value_type not in (ParameterType.FLOAT, ParameterType.INTEGER) and (self.scale, self.offset) != (1, 0):
            raise ValueError("non-numeric bindings cannot declare scale/offset transforms")


@dataclass(frozen=True)
class SemanticBinding:
    semantic_id: str
    targets: tuple[BindingTarget, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _text(self.semantic_id, "DINAD semantic parameter ID")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported semantic binding schema version")
        object.__setattr__(self, "targets", tuple(self.targets))
        if not self.targets or any(not isinstance(t, BindingTarget) for t in self.targets):
            raise ValueError("semantic binding requires typed targets")
        keys = [(t.module_id, t.native_id) for t in self.targets]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate semantic binding target")


class LifecycleState(str, Enum):
    READY = "ready"
    CLOSED = "closed"


class StagedLifecycle:
    """Built-in staging lifecycle: no process/device is owned between calls."""

    def __init__(self) -> None:
        self._state = LifecycleState.READY

    @property
    def state(self) -> LifecycleState:
        return self._state

    def _require_ready(self) -> None:
        if self.state != LifecycleState.READY:
            raise RuntimeError("module is closed; create a new adapter")

    def prepare(self) -> None:
        self._require_ready()  # Idempotent; no implicit engine boot or discovery.

    def close(self) -> None:
        self._state = LifecycleState.CLOSED

    def __enter__(self) -> Self:
        self.prepare()
        return self

    def __exit__(self, *args) -> None:
        self.close()


@runtime_checkable
class EventReceiver(Protocol):
    def submit(self, event: Event) -> None: ...


@runtime_checkable
class ModuleAdapter(Protocol):
    @property
    def descriptor(self) -> ModuleDescriptor: ...
    @property
    def state(self) -> LifecycleState: ...
    def prepare(self) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class NoteAdapter(ModuleAdapter, EventReceiver, Protocol):
    """Optional resolved-note receiver; production modules need not accept notes."""


@runtime_checkable
class ParameterReader(ModuleAdapter, Protocol):
    def get_parameter(self, native_id: str) -> ParameterValue: ...


class ParameterWriter(ModuleAdapter, Protocol):
    def set_parameter(self, native_id: str, value: ParameterValue) -> None: ...


@runtime_checkable
class OfflineRenderer(ModuleAdapter, Protocol):
    def render(self, output: Path, **options) -> dict: ...


@runtime_checkable
class ScheduledPlayer(ModuleAdapter, Protocol):
    def play(self, work: Path, **options) -> dict: ...


@runtime_checkable
class LiveProvider(ModuleAdapter, Protocol):
    def live_output(self, *, exclusive: bool, port: int, sclang: str,
                    library: Path | None) -> "LiveOutput": ...
