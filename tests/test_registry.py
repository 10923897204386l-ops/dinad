from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dinad.config import Project
from dinad.engine import Event, MockBackend
from dinad.execution import render_history, replay_history
from dinad.modules import Capability, ModuleDescriptor, ModuleKind, ParameterDescriptor, ParameterType
from dinad.registry import BUILTINS, Registration, Registry, load_module_config


def load(tmp_path, content, registry=BUILTINS):
    path = tmp_path / "modules.toml"
    path.write_text("schema_version = 1\n" + content)
    return load_module_config(path, registry)


def test_config_selects_backend_and_explicit_flags_override_settings(tmp_path, monkeypatch):
    import dinad.supercollider as sc

    config = load(tmp_path, """
[operations.render]
module = "dinad.supercollider"
version = "supercollider-v1"
[operations.render.settings]
sample_rate = 44100
tail = 3.0
""")
    monkeypatch.setattr(sc, "render", lambda events, output, settings, **opts: settings)
    result = render_history((), tmp_path / "out.wav", module=config.operations["render"], tail=4)
    assert result == sc.Settings(sample_rate=44100, tail=4)


def test_config_can_change_replay_receiver_without_changing_event(tmp_path):
    config = load(tmp_path, """
[operations.replay]
module = "dinad.supercollider"
version = "supercollider-v1"
""")
    history = (Event("pad", 60, 0, 1),)
    assert replay_history(history) == (1, "mock-v1")
    assert replay_history(history, module=config.operations["replay"]) == (1, "supercollider-v1")


@pytest.mark.parametrize(("content", "error"), [
    ('[operations.render]\nmodule="dinad.mock"\nversion="mock-v1"', "offline_render"),
    ('[operations.replay]\nmodule="vendor.editor"\nversion="1"', "unknown module"),
    ('[operations.replay]\nmodule="dinad.mock"\nversion="old"', "migration"),
    ('[operations.replay]\nmodule="dinad.mock"', "version"),
    ('[operations.play]\nmodule="dinad.supercollider"\nversion="supercollider-v1"\n'
     '[operations.play.settings]\neditor=true', "unknown fields: editor"),
    ('[operations.render]\nmodule="dinad.supercollider"\nversion="supercollider-v1"\n'
     '[operations.render.settings]\nsample_rate=true', "finite"),
    ('[bindings]\n"unknown.control"=[]', "unknown DINAD"),
    ('[bindings]\n"composition.anchor"=[{module_id="dinad.supercollider",'
     'module_version="supercollider-v1",native_id="tail"}]', "parameter_write"),
])
def test_invalid_config_fails_with_filename(tmp_path, content, error):
    with pytest.raises(ValueError, match=f"modules.toml: .*{error}"):
        load(tmp_path, content)


def fixture_provider(identifier, names, *, value_type=ParameterType.FLOAT, maximum=127):
    descriptor = ModuleDescriptor(identifier, "1", ModuleKind.INSTRUMENT,
                                  frozenset({Capability.PARAMETER_WRITE}),
                                  parameters=tuple(ParameterDescriptor(
                                      name, name, value_type, minimum=0, maximum=maximum,
                                      writable=True) for name in names))

    def factory(settings):
        pytest.fail("binding validation or preview instantiated an adapter")

    return Registration(descriptor, factory)


def test_semantic_mapping_survives_module_swap_and_fans_out(tmp_path):
    registry = Registry((fixture_provider("test.a", ("first", "second")),
                         fixture_provider("test.b", ("pitch",))))
    source = '''
[[bindings."composition.anchor"]]
module_id = "{module}"
module_version = "1"
native_id = "{parameter}"
scale = {scale}
'''
    first = load(tmp_path, source.format(module="test.a", parameter="first", scale=1)
                 + source.format(module="test.a", parameter="second", scale=0.5), registry)
    second = load(tmp_path, source.format(module="test.b", parameter="pitch", scale=1), registry)
    project = Project(anchor=60)
    assert [(u.semantic_id, u.native_id, u.value) for u in first.resolve(project)] == [
        ("composition.anchor", "first", 60), ("composition.anchor", "second", 30)]
    assert [(u.semantic_id, u.native_id, u.value) for u in second.resolve(project)] == [
        ("composition.anchor", "pitch", 60)]
    with pytest.raises(ValueError, match="outside its bounds"):
        load(tmp_path, source.format(module="test.a", parameter="first", scale=2), registry)


def test_integer_mapping_keeps_large_seed_exact(tmp_path):
    maximum = 2**63 - 1
    registry = Registry((fixture_provider("test.seed", ("seed",),
                                          value_type=ParameterType.INTEGER, maximum=maximum),))
    config = load(tmp_path, '''
[[bindings."generation.seed"]]
module_id = "test.seed"
module_version = "1"
native_id = "seed"
scale = 1.0
''', registry)
    update, = config.resolve(Project(seed=maximum))
    assert type(update.value) is int and update.value == maximum


def test_mismatched_factory_descriptor_is_rejected_and_closed(tmp_path):
    adapter = MockBackend()
    registration = Registration(replace(adapter.descriptor, version="other"), lambda settings: adapter)
    config = load(tmp_path, '[operations.replay]\nmodule="dinad.mock"\nversion="other"',
                  Registry((registration,)))
    with pytest.raises(ValueError, match="descriptor differs"):
        replay_history((), module=config.operations["replay"])
    assert adapter.state.value == "closed"


def test_catalog_and_config_validation_cli():
    def cli(*arguments):
        return json.loads(subprocess.check_output([sys.executable, "-m", "dinad", *arguments], text=True))

    catalog = cli("modules")
    assert [item["identifier"] for item in catalog] == ["dinad.mock", "dinad.supercollider"]
    assert all(item["headless"] for item in catalog)
    example = Path(__file__).resolve().parents[1] / "examples/first_environment/modules.toml"
    assert cli("validate-modules", str(example)) == {"operations": ["play", "render", "replay"], "updates": []}
