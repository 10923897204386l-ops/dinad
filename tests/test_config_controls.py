from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dinad.config import PARAMETERS, Profile, Project, load_profile, load_project
from dinad.controls import Session
from dinad.demo import compose
from dinad.recording import decode


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "first_environment"


def test_example_project_profile_and_legacy_cli_settings_agree():
    project = load_project(EXAMPLE / "project.toml")
    profile = load_profile(EXAMPLE / "controls.toml")
    assert project == Project(anchor=48, tempo=120, seed=42)
    assert decode(Session(project, profile).recording()) == compose(seed=42, anchor=48, tempo=120)
    assert profile.command_for("+") == "adjust composition.anchor 1"


@pytest.mark.parametrize("content, message", [
    ("", "schema_version"),
    ("schema_version = true", "schema_version"),
    ("schema_version = 2", "schema_version"),
    ('schema_version = 1\n[audio]\nbackend = "auto"', "unknown fields: audio"),
    ('schema_version = 1\n[composition]\nunknown = 4', "composition: unknown fields"),
    ('schema_version = 1\ncomposition = 3', "composition must be a table"),
    ('schema_version = 1\n[composition]\nanchor = true', "composition.anchor"),
    ('schema_version = 1\n[transport]\ntempo_bpm = 0', "transport.tempo_bpm"),
    ('schema_version = 1\n[generation]\nseed = 1.5', "generation.seed"),
    ('schema_version = 1\n[generation]\nseed = -1', "generation.seed"),
    ('schema_version = 1\n[composition]\nanchor = nan', "composition.anchor"),
    ('schema_version = 1\n[transport]\ntempo_bpm = "fast"', "transport.tempo_bpm"),
    ('schema_version = 1\n[composition', ""),
])
def test_project_errors_include_filename_and_field(tmp_path, content, message):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(ValueError) as failure:
        load_project(path)
    assert str(path) in str(failure.value)
    assert message in str(failure.value)


def test_defaults_and_immutable_settings(tmp_path):
    path = tmp_path / "defaults.toml"
    path.write_text("schema_version = 1")
    assert load_project(path) == Project()
    with pytest.raises(FrozenInstanceError):
        Project().seed = 3
    with pytest.raises(TypeError):
        PARAMETERS["new"] = PARAMETERS["composition.anchor"]
    with pytest.raises(ValueError, match="missing.toml"):
        load_project(tmp_path / "missing.toml")


@pytest.mark.parametrize("content", [
    'schema_version = 1\n[bindings]\nx = "set unknown 1"',
    'schema_version = 1\n[bindings]\nx = "set generation.seed 1.5"',
    'schema_version = 1\n[bindings]\nx = "set composition.anchor 999"',
    'schema_version = 1\n[bindings]\nx = "adjust transport.tempo_bpm inf"',
    'schema_version = 1\n[bindings]\nx = "key y"',
    'schema_version = 1\n[bindings]\nx = "save overwrite.json"',
    'schema_version = 1\n[bindings]\nx = 42',
    'schema_version = 1\n[bindings]\n"bad key" = "undo"',
    'schema_version = 1\n[bindings]\nx = "undo"\nx = "quit"',
    'schema_version = 1\nbindings = []',
])
def test_invalid_profile_is_rejected_before_use(tmp_path, content):
    path = tmp_path / "bad-profile.toml"
    path.write_text(content)
    with pytest.raises(ValueError, match="bad-profile.toml"):
        load_profile(path)


def test_profile_defensive_copy_and_duplicate_keys():
    pairs = [["x", "undo"]]
    profile = Profile(pairs)
    pairs[0][1] = "quit"
    assert profile.command_for("x") == "undo"
    with pytest.raises(ValueError, match="duplicate"):
        Profile((("x", "undo"), ("x", "quit")))


@pytest.mark.parametrize("command", [
    "set composition.anchor 128", "adjust composition.anchor -1000",
    "set transport.tempo_bpm 0", "adjust transport.tempo_bpm -120",
    "set generation.seed true", "set generation.seed 1.5",
    "set generation.seed 9223372036854775808", "set composition.anchor nan",
    "set unknown 3", 'set composition.anchor "', "key missing",
    "undo", "set composition.anchor 60 extra", "save history.json",
])
def test_invalid_action_preserves_all_session_state(command):
    session = Session(Project())
    project = session.project
    before = session.recording()
    with pytest.raises(ValueError):
        session.execute(command)
    assert session.project is project
    assert session.changes == ()
    assert session.recording() == before


def test_keyboard_and_text_actions_share_semantics_and_undo():
    profile = Profile((("+", "adjust composition.anchor 1"),))
    keyboard = Session(Project(), profile)
    text = Session(Project(), profile)
    assert keyboard.execute("key +") == text.execute("adjust composition.anchor 1")
    assert keyboard.recording() == text.recording()
    keyboard.execute("set transport.tempo_bpm 60")
    keyboard.execute("set generation.seed 42")
    keyboard.execute("undo")
    keyboard.execute("undo")
    keyboard.execute("undo")
    assert keyboard.project == Project()
    assert decode(keyboard.recording()) == decode(Session(Project()).recording())
    assert [change.kind for change in keyboard.changes] == ["adjust", "set", "set", "undo", "undo", "undo"]
    assert [change.sequence for change in keyboard.changes] == list(range(6))
    with pytest.raises(ValueError, match="nothing to undo"):
        keyboard.execute("undo")


def test_failed_adjustment_does_not_damage_existing_undo_stack():
    session = Session(Project())
    session.execute("set composition.anchor 60")
    with pytest.raises(ValueError):
        session.execute("adjust composition.anchor 1000")
    session.execute("undo")
    assert session.project == Project()
    assert len(session.changes) == 2


def test_capture_links_initial_settings_actions_profile_and_resolved_events():
    session = Session(Project(seed=42), Profile((("x", "set composition.anchor 60"),)))
    before = decode(session.recording())
    session.execute("key x")
    document = json.loads(session.execute("generate"))
    metadata = document["metadata"]
    assert metadata["project_schema_version"] == 1
    assert metadata["project"]["composition.anchor"] == 60
    assert metadata["profile"]["schema_version"] == 1
    history = metadata["semantic_actions"]
    assert history["initial_project"]["composition.anchor"] == 48
    assert history["schema_version"] == 1
    assert history["changes"][0] == dict(sequence=0, kind="set", parameter="composition.anchor", old=48, new=60)
    after = decode(json.dumps(document))
    assert all(b.pitch == a.pitch + 12 for a, b in zip(before, after))
    # Metadata is informational for event replay, including control actions.
    document["metadata"] = {}
    assert decode(json.dumps(document)) == after


def test_session_cli_save_replay_and_error_recovery(tmp_path):
    command = [sys.executable, "-m", "dinad"]
    path = tmp_path / "history with spaces.json"
    inputs = f'inspect\nkey +\nset transport.tempo_bpm 0\nundo\nsave "{path}"\nquit\n'
    result = subprocess.run(command + ["session", "--project", str(EXAMPLE / "project.toml"),
                                      "--profile", str(EXAMPLE / "controls.toml")],
                            input=inputs, capture_output=True, text=True)
    assert result.returncode == 1  # An invalid command is reported even after recovery.
    assert "transport.tempo_bpm" in result.stderr
    assert "Traceback" not in result.stderr
    assert decode(path.read_text()) == compose(seed=42)
    playback = subprocess.run(command + ["replay", str(path)], capture_output=True, text=True)
    assert playback.returncode == 0
    assert "Replayed 61" in playback.stdout


def test_project_cli_override_and_registry():
    command = [sys.executable, "-m", "dinad"]
    result = subprocess.run(command + ["demo", "--project", str(EXAMPLE / "project.toml"), "--anchor", "60"],
                            check=True, capture_output=True, text=True)
    assert decode(result.stdout) == compose(seed=42, anchor=60)
    registry = subprocess.run(command + ["parameters"], check=True, capture_output=True, text=True)
    parameters = json.loads(registry.stdout)
    assert set(parameters) == set(PARAMETERS)
    assert parameters["transport.tempo_bpm"]["applies"] == "next generation"


def test_atomic_save_preserves_existing_file_on_failure(tmp_path, monkeypatch):
    import dinad.__main__ as cli

    path = tmp_path / "history.json"
    path.write_text("existing")
    def fail_replace(*args):
        raise OSError("simulated write failure")
    monkeypatch.setattr(cli.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        cli._save(path, "replacement")
    assert path.read_text() == "existing"
    assert list(tmp_path.iterdir()) == [path]


def test_profile_error_identifies_the_bad_binding(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('schema_version = 1\n[bindings]\nvalid = "inspect"\nbroken = "set missing 42"')
    with pytest.raises(ValueError, match="binding 'broken': unknown parameter"):
        load_profile(path)
