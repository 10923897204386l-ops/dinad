import json
from pathlib import Path
import subprocess
import sys

import pytest

from dinad.config import Profile, Project
from dinad.demo import compose
from dinad.events import Event
from dinad.recording import decode
from dinad.transport import SilentOutput, Transport


class Clock:
    now = 100.

    def __call__(self):
        return self.now


class Output:
    def __init__(self):
        self.batches = []
        self.closed = []
        self.opened = 0
        self.fail = False

    def validate(self, events):
        if any(event.pitch > 100 for event in events):
            raise ValueError("pitch unsupported")

    def open(self):
        self.opened += 1

    def send(self, deadline, releases, starts):
        if self.fail:
            raise ConnectionError("engine disconnected")
        self.batches.append((deadline, releases, starts))

    def close(self, active):
        self.closed.append(active)


def setup():
    clock = Clock()
    output = Output()
    transport = Transport(Project(), Profile(), output, clock, lead=0.)
    transport.start()
    return transport, output, clock


def until(transport, clock, end):
    while transport.deadline < transport.origin + end:
        clock.now = transport.deadline
        transport.advance()
    clock.now = transport.origin + end


def test_repeated_cycles_preserve_m0_event_order_and_release_nodes():
    transport, output, clock = setup()
    until(transport, clock, 800.)
    original = compose()
    assert len(transport.events) == 100 * len(original)
    assert tuple(transport.events[:61]) == original
    assert len(transport.active) <= 6
    started = [node for _, _, starts in output.batches for node, _ in starts]
    released = [node for _, releases, _ in output.batches for node in releases]
    assert len(set(started)) == len(started)
    assert len(set(released)) == len(released)
    assert set(released) | set(transport.active) == set(started)
    remaining = tuple(transport.active)
    transport.stop()
    assert output.closed == [remaining]
    assert not transport.active
    assert len(decode(transport.recording())) == len(started)


def test_controls_apply_to_next_onset_without_retiming_active_notes():
    transport, output, clock = setup()
    until(transport, clock, .1)
    assert transport.active[1002] == 8.
    transport.control("set composition.anchor 60")
    transport.control("set transport.tempo_bpm 60")
    until(transport, clock, .26)
    event = transport.events[-1]
    assert event.time == .25 and event.duration == pytest.approx(.4)
    assert event.pitch == compose(anchor=60)[6].pitch
    assert transport.active[1002] == 8.
    assert transport.onset == .75
    transport.stop()
    metadata = json.loads(transport.recording())["metadata"]
    assert [item["effective_seconds"] for item in metadata["live_controls"]["changes"]] == [.25, .25]
    assert decode(transport.recording())[0].duration == pytest.approx(.26)


def test_invalid_control_and_replacement_leave_playing_state_intact():
    transport, output, clock = setup()
    until(transport, clock, .1)
    with pytest.raises(ValueError, match="pitch unsupported"):
        transport.control("set composition.anchor 127")
    with pytest.raises(ValueError, match="pitch unsupported"):
        transport.replace_project(Project(anchor=127))
    assert transport.session.project == Project()
    assert transport.session.changes == ()
    assert transport.running and output.closed == []


def test_replace_releases_old_take_and_restarts_at_zero():
    transport, output, clock = setup()
    until(transport, clock, .1)
    active = tuple(transport.active)
    transport.replace_project(Project(anchor=52))
    assert output.closed == [active]
    assert transport.running and output.opened == 2
    assert transport.events == [] and transport.actions == []
    transport.advance()
    assert transport.events[0].pitch == 40
    assert transport.events[0].time == 0.


def test_failed_dispatch_closes_take_and_can_restart():
    transport, output, clock = setup()
    until(transport, clock, .1)
    sent = tuple(transport.events)
    active = tuple(transport.active)
    output.fail = True
    clock.now = transport.deadline
    with pytest.raises(ConnectionError, match="disconnected"):
        transport.advance()
    assert not transport.running and output.closed == [active]
    assert tuple(transport.events) == sent
    output.fail = False
    transport.start()
    transport.advance()
    assert transport.running and transport.events[0].time == 0.


def test_cli_live_controls_and_save(tmp_path: Path):
    path = tmp_path / "take.json"
    run = subprocess.run([sys.executable, "-m", "dinad", "live"], text=True, capture_output=True,
                         input=f"start\nset composition.anchor 50\nstop\nsave {path}\nquit\n", timeout=5)
    assert run.returncode == 0, run.stderr
    assert "Started" in run.stdout and "Take saved" in run.stdout
    assert json.loads(path.read_text())["metadata"]["project"]["composition.anchor"] == 50


def test_numerically_coincident_release_and_onset_share_one_batch():
    import math

    transport, output, clock = setup()
    until(transport, clock, .21)
    transport.active[1002] = math.nextafter(.25, 0)
    clock.now = transport.deadline
    transport.advance()
    deadline, releases, starts = output.batches[-1]
    assert deadline == transport.origin + .25
    assert releases == (1002,) and starts


def test_live_config_selects_provider_and_rejects_staged_only_settings(tmp_path):
    from dinad.execution import live_transport
    from dinad.registry import load_module_config

    path = tmp_path / "modules.toml"
    config = """
schema_version = 1
[operations.live]
module = "dinad.supercollider"
version = "supercollider-v1"
[operations.live.settings]
sample_rate = 44100
"""
    path.write_text(config)
    selection = load_module_config(path).operations["live"]
    options = dict(backend=None, port=57110, sample_rate=None, sclang="sclang", library=None, module=selection)
    with pytest.raises(ValueError, match="exclusive-server"):
        live_transport(Project(), Profile(), exclusive=False, **options)
    transport = live_transport(Project(), Profile(), exclusive=True, **options)
    assert transport.output.settings.sample_rate == 44100 and not transport.running
    path.write_text(config + "tail = 3\n")
    with pytest.raises(ValueError, match="unknown fields: tail"):
        load_module_config(path)


def test_interrupt_during_release_still_frees_reserved_group(monkeypatch):
    import dinad.live_sc as sc

    class Connection:
        def close(self):
            pass

    driver = sc.SuperColliderLive(port=57110)
    driver._connection = Connection()
    driver._owns_group = True
    commands = []
    monkeypatch.setattr(driver, "_exchange", commands.extend)

    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(sc.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        driver.close((1002,))
    assert commands == [("/n_set", 1002, "gate", 0), ("/n_free", 1000)]
    assert driver._connection is None and not driver._owns_group


def test_seed_is_pending_until_next_onset_and_history_is_repeatable():
    histories = []
    for _ in range(2):
        transport, output, clock = setup()
        until(transport, clock, .1)
        assert transport.active_seed == 0
        previous = tuple(transport.events)
        active = dict(transport.active)
        for value in ("-1", "1.5"):
            with pytest.raises(ValueError):
                transport.control(f"set generation.seed {value}")
        transport.control("set generation.seed 42")
        assert transport.session.project.seed == 42 and transport.active_seed == 0
        assert tuple(transport.events) == previous and transport.active == active
        until(transport, clock, .21)  # Release only.
        assert transport.active_seed == 0
        until(transport, clock, 2.)
        assert transport.active_seed == 42
        assert tuple(transport.events[:len(previous)]) == previous
        assert output.opened == 1
        transport.stop()
        histories.append(transport.recording())
    assert histories[0] == histories[1]


def test_evolving_demo_varies_fast_arp_by_cycle():
    from dinad.demo import compose
    from dinad.transport import SilentOutput

    transport = Transport(Project(seed=42, evolve=True), Profile(), SilentOutput(), lambda: 0.)
    transport.start()
    while transport.tick < 64:
        transport.advance()
    first, second = [tuple(event.pitch for event in transport.events
                           if event.instrument == "arp.fast" and start <= event.time < start + 8)
                     for start in (0, 8)]
    assert first != second
    assert second == tuple(event.pitch for event in compose(seed=43) if event.instrument == "arp.fast")


def test_tension_changes_next_pad_and_arp_without_releasing_drone():
    from dinad.transport import SilentOutput

    transport = Transport(Project(seed=42), Profile(), SilentOutput(), lambda: 0.)
    transport.start()
    transport.advance()
    drone_end = transport.active[1002]
    transport.control("set composition.tension 2")
    while transport.tick <= 8:
        transport.advance()
    assert transport.active[1002] == drone_end
    pad = [event for event in transport.events if event.instrument == "pad" and event.time == 2.]
    assert [event.pitch for event in pad] == [53, 57, 59, 63]
    assert transport.actions[-1]["parameter"] == "composition.tension"
    assert transport.actions[-1]["effective_seconds"] == .25


def test_tension_changes_shared_pitch_set_deterministically():
    for level, tones in ((0, {0, 4, 7}), (1, {0, 4, 7, 10}), (2, {0, 4, 6, 10})):
        events = compose(seed=42, tension=level)
        assert events == compose(seed=42, tension=level)
        assert {event.pitch - 48 for event in events if event.instrument == "pad" and event.time == 0} == tones
        assert {(event.pitch - 48) % 12 for event in events
                if event.instrument.startswith("arp.") and event.time < 2} <= tones


@pytest.mark.parametrize("mode", [1, 2])
def test_fast_arp_reuses_motif_across_harmony(mode):
    from dinad.recording import decode

    def phrases(events):
        roots = (48, 53, 57, 55)
        return [tuple(e.pitch - root - 12 for e in events
                      if e.instrument == "arp.fast" and 2 * bar <= e.time < 2 * (bar + 1))
                for bar, root in enumerate(roots)]

    project = Project(seed=42, motif=mode, tension=2)
    transport = Transport(project, Profile(), SilentOutput(), lambda: 100.)
    transport.start()
    while transport.tick < 32:
        transport.advance()
    transport.clock = lambda: transport.origin + 8.
    transport.stop()
    pattern = phrases(decode(transport.recording()))
    assert pattern == phrases(compose(seed=42, tension=2, motif=mode))
    assert len(pattern[0]) == 8
    assert pattern[0] == pattern[1] == pattern[2]
    assert pattern[3] == (pattern[0] if mode == 1 else pattern[0][::-1])
    assert set(pattern[0]) <= {0, 4, 6, 10, 12}


def test_evolving_form_layers_and_capture():
    from dinad.recording import decode

    transport = Transport(Project(seed=42, form=1), Profile(), SilentOutput(), lambda: 100.)
    transport.start()
    while transport.tick < 160:
        transport.advance()
    transport.clock = lambda: transport.origin + 40.
    transport.stop()

    events = decode(transport.recording())
    sustained = {"drone", "pad"}
    expected = [sustained, sustained | {"arp.slow"}, sustained | {"arp.slow", "arp.fast"},
                sustained | {"arp.fast"}, sustained]
    assert [{event.instrument for event in events if cycle * 8 <= event.time < (cycle + 1) * 8}
            for cycle in range(5)] == expected


def test_turning_form_off_brings_arps_back_at_next_unscheduled_onset():
    transport = Transport(Project(form=1), Profile(), SilentOutput(), lambda: 100.)
    transport.start()
    transport.advance()
    transport.control("set generation.form 0")
    transport.advance()
    assert [event.instrument for event in transport.events if event.time == .25] == ["arp.fast"]
    assert transport.actions[-1]["effective_seconds"] == .25


def test_voice_spacing_uses_sounding_and_starting_sustained_notes():
    transport = Transport(Project(), Profile(), SilentOutput(), lambda: 100.)
    drone = Event("drone", 36, 0, 1)
    pad = Event("pad", 52, .5, 1)
    transport.events.append(drone)
    transport.active[1002] = 1.
    clashes_with_drone = Event("arp.fast", 61, .5, .2)
    clashes_with_pad = Event("arp.fast", 65, .5, .2)
    slow = Event("arp.slow", 65, .5, .4)
    notes = [pad, clashes_with_drone, clashes_with_pad, slow]
    assert transport._space_fast_arp(notes, ()) == [pad, slow]
    assert transport._space_fast_arp(notes, (1002,)) == [pad, clashes_with_drone, slow]


def test_voice_spacing_includes_releasing_voices_only_in_tail_mode():
    transport = Transport(Project(voice_space=2), Profile(), SilentOutput(), lambda: 100.)
    transport.events.append(Event("pad", 60, 0., 1.))
    transport.active[1002] = 1.
    arp = Event("arp.fast", 73, 1., .2)
    transport.onset = 1.
    assert transport._space_fast_arp([arp], (1002,)) == []
    transport.active.clear()
    transport.release_tails.append((60, 1.4))
    transport.onset = 1.25
    assert transport._space_fast_arp([arp], ()) == []
    transport.control("set composition.voice_space 1")
    assert transport._space_fast_arp([arp], ()) == [arp]
    transport.control("set composition.voice_space 2")
    transport.onset = 1.4
    assert transport._space_fast_arp([arp], ()) == [arp]
    transport.start()
    assert transport.release_tails == []
