from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess

import pytest

from dinad.demo import compose
from dinad.engine import Backend, Event, MockBackend, replay
from dinad.events import Event as ResolvedEvent
from dinad.execution import play_history, render_history, replay_history
from dinad.modules import (
    AudioPort, AutomationRate, BindingTarget, Capability, Dependency, EventReceiver,
    LifecycleState, ModuleAdapter, ModuleDescriptor, ModuleKind, NoteAdapter, ParameterDescriptor,
    ParameterReader, ParameterType, SemanticBinding, StagedLifecycle, UnsupportedCapability,
)
from dinad.recording import decode, encode
from dinad.supercollider import Settings, SuperColliderBackend, compile_events


def writable_module():
    # Contract fixture only: this does not represent a discovered third-party plugin.
    return ModuleDescriptor("test.native", "1", ModuleKind.INSTRUMENT,
                            frozenset({Capability.PARAMETER_READ, Capability.PARAMETER_WRITE}),
                            parameters=(ParameterDescriptor("cutoff", "Cutoff", ParameterType.FLOAT,
                                        440, 20, 20000, "Hz", readable=True, writable=True),))


def test_legacy_event_and_receiver_contracts_remain_usable():
    assert Event is ResolvedEvent
    assert Backend is EventReceiver
    class LegacyReceiver:
        def __init__(self): self.events = []
        def submit(self, event): self.events.append(event)
    receiver = LegacyReceiver()
    events = compose(seed=42)
    replay(events, receiver)
    assert tuple(receiver.events) == events
    assert isinstance(receiver, EventReceiver)
    assert not isinstance(receiver, ModuleAdapter)
    assert replay_history(events) == (61, "mock-v1")
    assert decode(encode(events, {})) == events


def test_builtins_declare_only_actual_support_without_starting_processes(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("contract access started a process"))
    for backend in (MockBackend(), SuperColliderBackend()):
        assert isinstance(backend, ModuleAdapter)
        assert isinstance(backend, NoteAdapter)
        backend.prepare()
        replay(compose(seed=42), backend)
        assert backend.events == compose(seed=42)
        assert backend.descriptor.headless
        assert backend.descriptor.state_schema is None
        with pytest.raises(UnsupportedCapability, match="unsupported capability"):
            backend.descriptor.require(Capability.STATE_RESTORE)
    mock = MockBackend().descriptor
    assert mock.capabilities == {Capability.STAGED_NOTES, Capability.CONTINUOUS_TRANSPORT}
    assert mock.audio_ports == mock.parameters == ()
    sc = SuperColliderBackend().descriptor
    assert sc.audio_ports == (AudioPort("master", "output", 2),)
    assert {p.native_id for p in sc.parameters} == {"sample_rate", "tail", "preroll"}
    assert Capability.PARAMETER_WRITE not in sc.capabilities
    assert all(p.automation == AutomationRate.NONE and not p.writable for p in sc.parameters)


def test_parameter_reads_reflect_actual_settings_not_descriptor_defaults():
    backend = SuperColliderBackend(Settings(sample_rate=44100, tail=3, preroll=.2))
    assert isinstance(backend, ParameterReader)
    assert not isinstance(MockBackend(), ParameterReader)
    assert backend.get_parameter("sample_rate") == 44100
    assert backend.get_parameter("tail") == 3
    assert backend.descriptor.parameter("sample_rate").default == 48000
    with pytest.raises(UnsupportedCapability, match="unknown native parameter"):
        backend.get_parameter("vendor.gui.only")


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "440", 0, 20001])
def test_numeric_parameter_types_and_bounds_reject_invalid_values(value):
    with pytest.raises(ValueError):
        writable_module().parameter("cutoff").validate(value)


def test_other_parameter_types_unknown_defaults_and_immutable_values():
    integer = ParameterDescriptor("voices", "Voices", ParameterType.INTEGER, 8, 1, 32)
    integer.validate(4)
    for value in (4.0, False):
        with pytest.raises(ValueError): integer.validate(value)
    boolean = ParameterDescriptor("enabled", "Enabled", ParameterType.BOOLEAN, True)
    boolean.validate(False)
    with pytest.raises(ValueError): boolean.validate(1)
    enum = ParameterDescriptor("mode", "Mode", ParameterType.ENUM, "pad", enum_values=["pad", "drone"])
    with pytest.raises(ValueError): enum.validate("unknown")
    assert enum.enum_values == ("pad", "drone")
    assert ParameterDescriptor("unknown", "Unknown", ParameterType.FLOAT).default is None
    with pytest.raises(FrozenInstanceError): integer.minimum = 0


@pytest.mark.parametrize("kwargs", [
    {"value_type": "float"}, {"minimum": 5, "maximum": 1}, {"default": float("nan")},
    {"readable": 1}, {"writable": "yes"}, {"automation": "sample"},
    {"automation": AutomationRate.SAMPLE}, {"smoothing_seconds": .1}, {"max_update_hz": 0},
    {"value_type": ParameterType.BOOLEAN, "minimum": 0},
    {"value_type": ParameterType.INTEGER, "minimum": .5},
    {"value_type": ParameterType.ENUM}, {"enum_values": ("a",)},
    {"value_type": ParameterType.ENUM, "enum_values": ("a", "a")},
])
def test_invalid_parameter_descriptions_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ParameterDescriptor(**{"native_id": "x", "label": "X", "value_type": ParameterType.FLOAT, **kwargs})


@pytest.mark.parametrize("kwargs", [
    {"headless": False}, {"headless": 1}, {"contract_version": True}, {"contract_version": 2},
    {"kind": "instrument"}, {"capabilities": {"staged_notes"}},
    {"parameters": ("not a description",)}, {"state_schema": "state-v1"},
    {"capabilities": {Capability.STATE_SAVE}},
    {"dependencies": (Dependency("tool", frozenset({Capability.OFFLINE_RENDER})),)},
    {"parameters": (ParameterDescriptor("x", "X", ParameterType.FLOAT, readable=True),)},
    {"audio_ports": (AudioPort("out", "output", 2), AudioPort("out", "output", 2))},
])
def test_inconsistent_or_gui_requiring_module_descriptions_fail(kwargs):
    with pytest.raises(ValueError):
        ModuleDescriptor(**{"identifier": "test", "version": "1", "kind": ModuleKind.PERFORMANCE,
                            "capabilities": frozenset(), **kwargs})


def test_collections_are_copied_and_duplicate_parameter_ids_are_rejected():
    caps = {Capability.PARAMETER_READ, Capability.PARAMETER_WRITE}
    parameters = list(writable_module().parameters)
    module = ModuleDescriptor("test", "1", ModuleKind.INSTRUMENT, caps, parameters=parameters)
    caps.clear(); parameters.clear()
    assert module.parameters and module.capabilities
    with pytest.raises(ValueError, match="duplicate"):
        replace(module, parameters=module.parameters * 2)


def test_semantic_bindings_are_declarative_one_to_many_and_exactly_versioned():
    module = writable_module()
    targets = [BindingTarget("test.native", "1", "cutoff", scale=100, offset=20),
               BindingTarget("test.other", "2", "brightness")]
    binding = SemanticBinding("instrument.tone", targets)
    targets.clear()
    assert len(binding.targets) == 2
    binding.targets[0].validate_target(module)
    with pytest.raises(ValueError, match="migration"):
        binding.targets[0].validate_target(replace(module, version="2"))
    with pytest.raises(UnsupportedCapability, match="unknown native parameter"):
        BindingTarget("test.native", "1", "renamed").validate_target(module)
    with pytest.raises(ValueError, match="duplicate"):
        SemanticBinding("instrument.tone", binding.targets * 2)
    with pytest.raises(ValueError): SemanticBinding("", binding.targets)
    with pytest.raises(ValueError): SemanticBinding("instrument.tone", (), schema_version=2)
    with pytest.raises(ValueError): BindingTarget("test.native", "1", "cutoff", scale=float("nan"))


def test_bindings_reject_read_only_or_non_numeric_transforms():
    backend = SuperColliderBackend()
    with pytest.raises(UnsupportedCapability):
        BindingTarget("dinad.supercollider", backend.version, "tail").validate_target(backend.descriptor)
    module = writable_module()
    readonly = replace(module, parameters=(replace(module.parameters[0], writable=False),))
    with pytest.raises(UnsupportedCapability, match="read-only"):
        BindingTarget(module.identifier, module.version, "cutoff").validate_target(readonly)
    enum = replace(module, parameters=(ParameterDescriptor("mode", "Mode", ParameterType.ENUM,
                    "a", enum_values=("a", "b"), writable=True),))
    with pytest.raises(ValueError, match="non-numeric"):
        BindingTarget(module.identifier, module.version, "mode", scale=2).validate_target(enum)


@pytest.mark.parametrize("factory", [MockBackend, SuperColliderBackend])
def test_lifecycle_is_idempotent_and_close_preserves_history_but_blocks_execution(factory, tmp_path):
    backend = factory()
    backend.prepare(); backend.prepare()
    assert backend.state == LifecycleState.READY
    events = compose()
    replay(events, backend)
    backend.close(); backend.close()
    assert backend.state == LifecycleState.CLOSED
    assert backend.events == events
    for operation in (backend.prepare, lambda: backend.submit(Event("pad", 60, 20, 1))):
        with pytest.raises(RuntimeError, match="closed"): operation()
    if isinstance(backend, SuperColliderBackend):
        for operation in (lambda: backend.render(tmp_path / "out.wav"), lambda: backend.play(tmp_path / "logs")):
            with pytest.raises(RuntimeError, match="closed"): operation()
        assert list(tmp_path.iterdir()) == []


def test_application_boundary_closes_adapter_after_success_or_failure(monkeypatch, tmp_path):
    import dinad.supercollider as sc
    calls = []
    created = []
    import dinad.execution as execution
    from dinad.registry import BUILTINS, Registry

    def factory(settings):
        backend = sc.SuperColliderBackend(Settings(**settings))
        created.append(backend)
        return backend
    entry = BUILTINS.require("dinad.supercollider")
    monkeypatch.setattr(execution, "BUILTINS", Registry((replace(entry, factory=factory),)))
    def rendered(events, output, settings, **options):
        calls.append((events, settings, options))
        return {"ok": True}
    monkeypatch.setattr(sc, "render", rendered)
    events = compose(seed=42)
    assert render_history(events, tmp_path / "audio.wav", sample_rate=44100, tail=3) == {"ok": True}
    assert calls[0][0] == events and calls[0][1] == Settings(sample_rate=44100, tail=3)
    assert all(b.state == LifecycleState.CLOSED for b in created)
    def failed(*a, **k): raise RuntimeError("engine failed")
    monkeypatch.setattr(sc, "play", failed)
    with pytest.raises(RuntimeError, match="engine failed"):
        play_history(events, tmp_path / "logs", exclusive=True)
    assert created[-1].state == LifecycleState.CLOSED
    with pytest.raises(ValueError, match="unsupported"):
        render_history((Event("missing", 60, 0, 1),), tmp_path / "invalid.wav")
    assert created[-1].state == LifecycleState.CLOSED
    assert list(tmp_path.iterdir()) == []


def test_frontend_uses_dinad_execution_boundary_not_concrete_audio_provider():
    import ast
    import dinad.__main__ as cli
    tree = ast.parse(Path(cli.__file__).read_text())
    assert not any(isinstance(node, ast.ImportFrom) and node.module == "dinad.supercollider"
                   for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Name) and node.id == "SuperColliderBackend" for node in ast.walk(tree))


def test_stage_refactor_preserves_exact_note_release_and_order_contract():
    events = compose(seed=42)
    with SuperColliderBackend() as backend:
        replay(events, backend)
        assert compile_events(backend.events, backend.settings) == compile_events(events, Settings())
        assert json.loads(encode(backend.events, {}))["events"] == json.loads(encode(events, {}))["events"]


def test_base_module_contract_does_not_require_note_reception():
    class ProductionModule(StagedLifecycle):
        descriptor = ModuleDescriptor("test.effect", "1", ModuleKind.PRODUCTION, frozenset())
    effect = ProductionModule()
    assert isinstance(effect, ModuleAdapter)
    assert not isinstance(effect, NoteAdapter)
    assert not isinstance(effect, EventReceiver)
    with pytest.raises(UnsupportedCapability):
        effect.descriptor.require(Capability.STAGED_NOTES)
