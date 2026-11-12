import json
import os
from pathlib import Path
import struct

import pytest

from dinad.config import Profile, Project
from dinad.events import Event
from dinad.execution import live_transport
from dinad.lv2_epiano import EPianoBackend, MidiGates, PORT_INDEX, _offline_score
from dinad.registry import load_module_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/first_environment/epiano.toml"


def test_same_pitch_overlap_releases_only_after_last_gate():
    gates = MidiGates()
    event = Event("pad", 60, 0., 1.)
    assert gates.update((), ((1, event), (2, event))) == [(60, 80)]
    assert gates.update((1,), ()) == []
    assert gates.update((2,), ((3, event),)) == [(188, 0), (60, 80)]
    assert gates.update((3,), ()) == [(188, 0)]


def test_offline_score_preserves_non_block_aligned_note_frames():
    note = Event("pad", 60, 123 / 48000, 149 / 48000)
    assert _offline_score((note,), 48000) == ["m 123 60 80", "m 272 188 0"]
    with pytest.raises(ValueError, match="integer MIDI"):
        _offline_score((Event("pad", 60.25, 0., .1),), 48000)


def test_example_routes_semantic_edits_to_both_epiano_ports():
    config = load_module_config(EXAMPLE)
    transport = live_transport(Project(), Profile(), backend=None, exclusive=False,
                               port=57110, sample_rate=None, sclang="sclang",
                               library=None, module_config=config)
    transport.before_onset(Project(anchor=60))
    assert transport.output.pending == {"hardness": .46875, "treble_boost": .484375}
    assert transport.output.process is None


def test_state_restore_is_versioned_and_validates_all_controls():
    original = EPianoBackend({"hardness": .75})
    output = original.live_output()
    output.automation.append((100.025, {"hardness": .8, "treble_boost": .25}))
    state = json.loads(json.dumps(output.metadata(100., 1.)["module_state"]))
    assert state["controls"].keys() == PORT_INDEX.keys()
    controls, automation = EPianoBackend.restore_controls(state, 1.)
    assert controls["hardness"] == .75
    assert len(automation) == 1
    assert automation[0][0] == pytest.approx(.025)
    assert automation[0][1] == {"hardness": .8, "treble_boost": .25}
    state["controls"]["hardness"] = 2
    with pytest.raises(ValueError, match="hardness"):
        EPianoBackend.restore_controls(state, 1.)
    state["version"] = "other"
    with pytest.raises(ValueError, match="incompatible"):
        EPianoBackend.restore_controls(state, 1.)


def _frames(path):
    data = path.read_bytes()
    cursor = 12
    while cursor < len(data):
        kind, size = struct.unpack_from("<4sI", data, cursor)
        start = cursor + 8
        if kind == b"data":
            return list(struct.iter_unpack("<ff", data[start:start + size]))
        cursor = start + size + size % 2
    raise AssertionError("missing WAV data")


@pytest.mark.skipif(not os.environ.get("DINAD_TEST_EPIANO"), reason="native ePiano helper and LV2 bundle required")
def test_actual_plugin_renders_sample_offset_and_restores_automation(tmp_path):
    helper = os.environ["DINAD_TEST_EPIANO"]
    original = EPianoBackend({"helper": helper, "random_tuning": 0., "hardness": 0.})
    output = original.live_output()
    output.automation.append((.025, {"hardness": 1.}))
    state = output.metadata(0., .22)["module_state"]
    history = (Event("pad", 60, 123 / 48000, .08), Event("pad", 67, .101, .1))
    for e in history:
        original.submit(e)
    baseline = tmp_path / "baseline.wav"
    original.render(baseline)
    restored = EPianoBackend({"helper": helper, "hardness": .5, "random_tuning": .1})
    for e in history:
        restored.submit(e)
    path = tmp_path / "restored.wav"
    manifest = restored.render(path, module_state=state)
    frames, reference = _frames(path), _frames(baseline)
    assert next(i for i, pair in enumerate(frames) if max(map(abs, pair)) > 1e-7) == 123
    assert manifest["settings"]["hardness"] == 0 and manifest["settings"]["random_tuning"] == 0
    assert frames[:1200] == reference[:1200]
    assert frames[4848:6000] != reference[4848:6000]


def test_live_take_replays_resolved_notes_and_controls(monkeypatch):
    from dinad.execution import play_take
    from dinad.lv2_epiano import EPianoLive
    from dinad.transport import Transport

    sent = []
    monkeypatch.setattr(EPianoLive, "open", lambda self: None)
    monkeypatch.setattr(EPianoLive, "send", lambda self, at, off, on:
                        sent.append((at, off, on, self.pending.copy())))
    monkeypatch.setattr(EPianoLive, "close", lambda self, active: sent.append(active))
    original = EPianoBackend({"hardness": .25}).live_output()
    original.automation.append((.125, {"hardness": .75}))
    metadata = original.metadata(0., .5)
    transport = Transport(Project(), Profile(), EPianoBackend({"hardness": .5}).live_output(), lambda: 10.)
    note = Event("arp.fast", 72, .125, .125)
    playback = play_take(transport, ((note,), metadata))
    assert playback.output.initial["hardness"] == .25
    assert playback.deadline == pytest.approx(10.175)
    playback.advance()
    assert sent[0][1:] == ((), ((1002, note),), {"hardness": .75})
    playback.advance()
    assert sent[1][1:3] == ((1002,), ())
    playback.advance()
    assert not playback.running and sent[-1] == ()
    assert transport.output.initial["hardness"] == .5
