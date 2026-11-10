import json
import random
import subprocess
import sys

import pytest

from dinad.demo import compose
from dinad.engine import Event, HarmonicContext, MockBackend, Part, replay, schedule
from dinad.interpreters import Note
from dinad.recording import decode, encode


def test_schedule_converts_time_and_duration_and_preserves_stable_ties():
    parts = (
        Part("first", (Note(60, 2, 2), Note(62, 0, 1), Note(64, 2, 1))),
        Part("second", (Note(67, 2, 1),)),
    )
    assert schedule(parts, tempo=60) == (
        Event("first", 62, 0, 1), Event("first", 60, 2, 2),
        Event("first", 64, 2, 1), Event("second", 67, 2, 1),
    )
    assert schedule(parts, tempo=120)[1] == Event("first", 60, 1, 1)


def test_context_boundaries_gaps_and_sustained_pitch():
    context = HarmonicContext((Note(65, 4, 4), Note(60, 0, 3)))
    assert context.pitch_at(0) == 60
    assert context.pitch_at(4) == 65
    for beat in (-1, 3, 8, float("nan")):
        with pytest.raises(ValueError):
            context.pitch_at(beat)
    # Lifetime crosses a context boundary without retuning the held note.
    assert schedule((Part("held", (Note(context.pitch_at(2), 2, 5),)),))[0].pitch == 60


@pytest.mark.parametrize("spans", [(), (Note(60, 0, 4), Note(65, 3, 4))])
def test_context_rejects_empty_and_overlapping_spans(spans):
    with pytest.raises(ValueError):
        HarmonicContext(spans)


def test_demo_shared_anchor_transposes_every_part():
    low, high = compose(seed=42, anchor=48), compose(seed=42, anchor=60)
    assert len(low) == len(high) == 61
    assert {event.instrument for event in low} == {"drone", "pad", "arp.slow", "arp.fast"}
    for original, shifted in zip(low, high):
        assert shifted.pitch == original.pitch + 12
        assert (original.time, original.duration, original.instrument) == (
            shifted.time, shifted.duration, shifted.instrument,
        )
    boundary = [event for event in low if event.time == 2]
    assert [event.pitch for event in boundary if event.instrument == "pad"] == [53, 57, 60]
    assert [event.pitch for event in boundary if event.instrument == "arp.slow"] == [65]
    assert next(event for event in low if event.instrument == "drone").duration == 8


def test_seed_is_repeatable_isolated_and_changes_only_generated_part():
    before = random.getstate()
    original = compose(seed=42)
    assert compose(seed=42) == original
    assert random.getstate() == before
    changed = compose(seed=43)
    assert [e for e in original if e.instrument != "arp.fast"] == [
        e for e in changed if e.instrument != "arp.fast"
    ]
    assert [e for e in original if e.instrument == "arp.fast"] != [
        e for e in changed if e.instrument == "arp.fast"
    ]


def test_recording_roundtrip_and_replay_do_not_generate_again(monkeypatch):
    events = compose(seed=42)
    text = encode(events, {"seed": 42})
    monkeypatch.setattr("dinad.demo.compose", lambda *a, **k: pytest.fail("replay regenerated"))
    backend = MockBackend()
    replay(decode(text), backend)
    assert backend.events == events
    assert encode(events, {"seed": 42}) == text
    assert json.loads(text)["backend"] == "mock-v1"


@pytest.mark.parametrize("text", [
    "{", "[]", '{}', '{"schema_version":true,"events":[]}',
    '{"schema_version":2,"events":[]}', '{"schema_version":1,"events":{}}',
    '{"schema_version":1,"events":[{}]}',
    '{"schema_version":1,"events":[{"instrument":"p","pitch":NaN,"time":0,"duration":1}]}',
])
def test_invalid_recordings_fail_clearly(text):
    with pytest.raises(ValueError, match="invalid recording"):
        decode(text)


def test_out_of_order_replay_fails_before_dispatch():
    backend = MockBackend()
    with pytest.raises(ValueError, match="timestamp order"):
        replay((Event("p", 60, 1, 1), Event("p", 62, 0, 1)), backend)
    assert backend.events == ()
    backend.submit(Event("p", 60, 1, 1))
    with pytest.raises(ValueError, match="timestamp order"):
        backend.submit(Event("p", 62, 0, 1))


@pytest.mark.parametrize("tempo", [0, -1, True, float("nan"), float("inf"), 1e-320])
def test_invalid_tempo(tempo):
    with pytest.raises(ValueError):
        schedule((Part("p", (Note(60, 1, 1),)),), tempo)


@pytest.mark.parametrize("kwargs", [
    {"instrument": ""}, {"pitch": float("inf")}, {"time": -1},
    {"duration": 0}, {"time": 1e308, "duration": 1e308},
])
def test_invalid_event_values(kwargs):
    values = dict(instrument="p", pitch=60, time=0, duration=1)
    values.update(kwargs)
    with pytest.raises(ValueError):
        Event(**values)


def test_cli_repeatability_replay_and_errors(tmp_path):
    command = [sys.executable, "-m", "dinad"]
    first = subprocess.run(command + ["demo", "--seed", "42"], check=True, capture_output=True, text=True)
    second = subprocess.run(command + ["demo", "--seed", "42"], check=True, capture_output=True, text=True)
    assert first.stdout == second.stdout
    path = tmp_path / "history.json"
    path.write_text(first.stdout)
    result = subprocess.run(command + ["replay", str(path)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Replayed 61" in result.stdout
    for args in (["demo", "--tempo", "0"], ["replay", str(tmp_path / "missing.json")]):
        failure = subprocess.run(command + args, capture_output=True, text=True)
        assert failure.returncode == 2
        assert "error:" in failure.stderr
        assert "Traceback" not in failure.stderr


def test_unrepresentable_tempo_rejected_even_for_empty_schedule():
    with pytest.raises(ValueError, match="seconds per beat"):
        schedule((), tempo=1e-320)


def test_recording_rejects_reversed_history_and_unknown_event_fields():
    events = compose(seed=0)
    document = json.loads(encode(events, {}))
    document["events"].reverse()
    with pytest.raises(ValueError, match="timestamp order"):
        decode(json.dumps(document))
    document = json.loads(encode(events, {}))
    document["events"][0]["unknown"] = 1
    with pytest.raises(ValueError, match="invalid recording"):
        decode(json.dumps(document))
