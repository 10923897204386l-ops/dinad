import json
from pathlib import Path
import subprocess
import sys

import pytest

from dinad.config import Profile, Project
from dinad.demo import compose
from dinad.engine import MockBackend, replay
from dinad.performance import Performance, load_performance
from dinad.recording import decode


ROOT = Path(__file__).parents[1]


def test_unchanged_performance_preserves_existing_demo_and_ties():
    for project in (Project(), Project(anchor=50.5, tempo=137, seed=42)):
        performance = Performance(project)
        assert performance.events() == compose(project.seed, project.anchor, project.tempo)
        assert decode(performance.recording()) == performance.events()
        assert len(performance.events()) == 61


def test_anchor_change_applies_at_boundary_to_new_onsets_not_sustained_notes():
    original = compose()
    performance = Performance(Project())
    performance.execute("set composition.anchor 60", 1)
    events = performance.events()
    for before, after in zip(original, events):
        assert after.time == before.time
        assert after.duration == before.duration
        assert after.pitch == before.pitch + (12 if before.time >= 1 else 0)
    assert events[0].instrument == "drone"
    assert events[0].pitch == 36


def test_tempo_changes_integrate_beats_but_preserve_existing_gate_durations():
    performance = Performance(Project())
    performance.execute("set transport.tempo_bpm 60", 1)
    assert performance.end_time == 15
    assert performance.actions[0]["beat"] == 2
    events = performance.events()
    assert events[0].duration == 8  # Drone was started at the old tempo.
    boundary = next(e for e in events if e.instrument == "arp.slow" and e.time == 1)
    assert boundary.duration == pytest.approx(.8)
    assert next(e for e in events if e.instrument == "pad" and e.time > 0).time == 3
    performance.execute("set transport.tempo_bpm 120", 3)
    assert performance.actions[-1]["beat"] == 4
    assert performance.end_time == 9


def test_equal_time_controls_are_ordered_and_undo_uses_semantic_history():
    performance = Performance(Project(), Profile((("up", "adjust composition.anchor 12"),)))
    performance.execute("key up", 1)
    performance.execute("set transport.tempo_bpm 60", 1)
    performance.execute("undo", 1)
    assert performance.end_time == 8
    assert [a["sequence"] for a in performance.actions] == [0, 1, 2]
    assert [a["beat"] for a in performance.actions] == [2, 2, 2]
    baseline = compose(anchor=60)
    assert [e for e in performance.events() if e.time >= 1] == [e for e in baseline if e.time >= 1]
    performance.execute("undo", 2)
    assert performance.session.project == Project()


def test_seed_change_selects_future_tick_values_without_rewriting_past():
    performance = Performance(Project(seed=42))
    performance.execute("set generation.seed 7", 2)
    expected = tuple(e for e in compose(seed=42) if e.time < 2) + tuple(e for e in compose(seed=7) if e.time >= 2)
    assert performance.events() == expected
    assert performance.events() == performance.events()  # No persistent RNG consumption.


@pytest.mark.parametrize("seconds", [-1, True, float("nan"), float("inf"), "1"])
def test_invalid_time_does_not_change_state(seconds):
    performance = Performance(Project())
    before = performance.recording()
    with pytest.raises(ValueError):
        performance.execute("set composition.anchor 60", seconds)
    assert performance.recording() == before


@pytest.mark.parametrize("command", ["set composition.anchor 999", "adjust transport.tempo_bpm -120",
                                      "undo", "generate", "key missing", "key a b", "save file"])
def test_failed_control_does_not_advance_time_or_change_history(command):
    performance = Performance(Project())
    before = performance.recording()
    with pytest.raises(ValueError):
        performance.execute(command, 2)
    assert performance.recording() == before
    performance.execute("set composition.anchor 60", 1)  # Failure did not advance time.
    performance.execute("undo", 1)
    assert performance.session.project == Project()


def test_control_timestamps_are_ordered_and_bounded_by_dynamic_end():
    performance = Performance(Project())
    performance.execute("inspect", 2)
    for when in (1, 8, 9):
        with pytest.raises(ValueError):
            performance.execute("set composition.anchor 60", when)
    performance.execute("set transport.tempo_bpm 1000", 2)
    assert performance.end_time == pytest.approx(2.72)
    with pytest.raises(ValueError):
        performance.execute("set composition.anchor 60", 3)


def test_stop_truncates_outstanding_notes_and_forbids_later_actions():
    performance = Performance(Project())
    performance.execute("quit", 1.1)
    events = performance.events()
    assert events
    assert all(e.time < 1.1 and e.time + e.duration <= 1.1 for e in events)
    assert events[0].duration == 1.1
    with pytest.raises(ValueError, match="stopped"):
        performance.execute("inspect", 1.1)
    with pytest.raises(ValueError):
        performance.recording(2)
    empty = Performance(Project())
    empty.execute("quit", 0)
    assert empty.events() == ()


def test_capture_metadata_is_informational_for_event_replay(monkeypatch):
    performance = Performance(Project())
    performance.execute("set composition.anchor 60", 1)
    text = performance.recording()
    document = json.loads(text)
    assert document["metadata"]["timed_controls"]["changes"][0]["seconds"] == 1
    document["metadata"] = {"unavailable": "generator"}
    monkeypatch.setattr("dinad.performance.compose", lambda *args: pytest.fail("replay regenerated notes"))
    backend = MockBackend()
    replay(decode(json.dumps(document)), backend)
    assert backend.events == decode(text)


def test_actions_property_does_not_expose_internal_history():
    performance = Performance(Project())
    performance.execute("set composition.anchor 60", 1)
    performance.actions[0]["new"] = 90
    assert performance.actions[0]["new"] == 60


@pytest.mark.parametrize("content", [
    "schema_version=2", "schema_version=1\nactions=3",
    'schema_version=1\n[[actions]]\nseconds=1',
    'schema_version=1\n[[actions]]\nseconds=1\ncommand=3',
    'schema_version=1\n[[actions]]\nseconds=1\ncommand="inspect"\nextra=1',
    'schema_version=1\n[[actions]]\nseconds=8\ncommand="set composition.anchor 60"',
    'schema_version=1\n[[actions]]\nseconds=2\ncommand="inspect"\n[[actions]]\nseconds=1\ncommand="inspect"',
])
def test_invalid_timeline_includes_filename(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content)
    with pytest.raises(ValueError, match="bad.toml"):
        load_performance(path, Project())


def cli(*args, **kwargs):
    return subprocess.run([sys.executable, "-m", "dinad", *map(str, args)],
                          capture_output=True, text=True, timeout=15, **kwargs)


def test_example_cli_is_deterministic_renderable_and_no_clobber(tmp_path):
    timeline = ROOT / "examples/first_environment/performance.toml"
    paths = [tmp_path / "first.json", tmp_path / "second.json"]
    for path in paths:
        result = cli("perform", timeline, path)
        assert result.returncode == 0, result.stderr
    assert paths[0].read_bytes() == paths[1].read_bytes()
    text = paths[0].read_text()
    assert len(decode(text)) == 61
    from dinad.supercollider import Settings, compile_events
    assert compile_events(decode(text), Settings())
    assert cli("replay", paths[0]).returncode == 0
    assert cli("perform", timeline, paths[0]).returncode == 2
    assert paths[0].read_text() == text


def test_capture_handles_piped_input_eof_and_preserves_existing_takes(tmp_path):
    path = tmp_path / "take.json"
    result = cli("capture", path, input="set composition.anchor 60\nundo\nquit\n")
    assert result.returncode == 0, result.stderr
    document = json.loads(path.read_text())
    changes = document["metadata"]["timed_controls"]["changes"]
    assert [c["kind"] for c in changes] == ["set", "undo"]
    assert changes[0]["seconds"] <= changes[1]["seconds"]
    assert decode(path.read_text())
    before = path.read_text()
    assert cli("capture", path, input="quit\n").returncode == 2
    assert path.read_text() == before
    eof = tmp_path / "eof.json"
    assert cli("capture", eof, input="").returncode == 0
    assert eof.exists()


def test_capture_auto_finishes_even_if_stdin_remains_open(tmp_path):
    project = tmp_path / "quick.toml"
    project.write_text("schema_version=1\n[transport]\ntempo_bpm=1000")
    path = tmp_path / "complete.json"
    process = subprocess.Popen([sys.executable, "-m", "dinad", "capture", str(path), "--project", str(project)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        process.wait(timeout=5)  # Keep stdin open: no EOF to release a blocking input reader.
        assert process.returncode == 0, process.stderr.read()
        assert len(decode(path.read_text())) == 61
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_capture_retains_valid_controls_after_a_rejected_command(tmp_path):
    path = tmp_path / "partial.json"
    result = cli("capture", path, input="set composition.anchor 999\nset composition.anchor 60\nquit")
    assert result.returncode == 1
    assert "error:" in result.stderr
    changes = json.loads(path.read_text())["metadata"]["timed_controls"]["changes"]
    assert len(changes) == 1 and changes[0]["new"] == 60


def test_nontext_control_is_rejected_without_reading_stdin():
    with pytest.raises(ValueError, match="text"):
        Performance(Project()).execute(None, 0)
