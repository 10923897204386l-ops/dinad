import json
from pathlib import Path
import subprocess
import sys

import pytest

from dinad.config import Profile, Project
from dinad.demo import compose
from dinad.recording import decode
from dinad.transport import Transport


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
