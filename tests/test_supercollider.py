import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from dinad.demo import compose
from dinad.engine import Event
from dinad.supercollider import (
    Settings, _run, compile_events, inspect_wav, live_source, play, render, score_source,
)


def test_score_lifetimes_setup_order_and_determinism(tmp_path):
    events = (Event("pad", 60, 0, 1), Event("arp.fast", 72, 1, 0.5))
    commands = compile_events(events, Settings())
    assert commands[:2][0].arguments == ("/g_new", 1000, 0, 0)
    assert commands[:2][1].arguments == ("/s_new", "dinad_effects", 1001, 1, 1000)
    at_boundary = [c for c in commands if c.time == 1.1]
    assert [c.arguments[0] for c in at_boundary] == ["/n_set", "/s_new"]
    starts = [c for c in commands if c.arguments[0] == "/s_new" and "freq" in c.arguments]
    assert starts[0].arguments[2:5] == (1002, 2, 1001)
    assert starts[0].arguments[6] == pytest.approx(261.625565)
    assert starts[1].arguments[6] == pytest.approx(starts[0].arguments[6] * 2)
    assert commands[-1].time == 3.6
    assert commands[-1].arguments == ("/n_free", 1000)
    assert score_source(events, Settings(), tmp_path / 'score "é".osc') == score_source(events, Settings(), tmp_path / 'score "é".osc')


@pytest.mark.parametrize("events", [
    (Event("unknown", 60, 0, 1),),
    (Event("pad", 10000, 0, 1),),
    (Event("pad", -100000, 0, 1),),
    (Event("pad", 60, 1, 1), Event("pad", 60, 0, 1)),
    (Event("pad", 60, 1e20, 1),),
    (Event("pad", 60, 0, 1e-30),),
])
def test_invalid_event_histories_fail_before_creating_files(tmp_path, events):
    with pytest.raises(ValueError):
        render(events, tmp_path / "output.wav")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kwargs", [
    {"sample_rate": True}, {"sample_rate": 7999}, {"sample_rate": 200000},
    {"tail": 0}, {"tail": float("nan")}, {"preroll": 0},
])
def test_invalid_render_settings(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_assets_are_in_generated_source_and_paths_are_escaped(tmp_path):
    source = score_source(compose(), Settings(), tmp_path / 'with "quotes" é.osc')
    for name in ("dinad_drone", "dinad_pad", "dinad_arp", "dinad_effects"):
        assert name in source
    assert "FreeVerb2.ar" in source and "DelayC.ar" in source
    assert '\\"quotes\\"' in source
    assert "é.osc" in source


def test_missing_executable_has_clear_error_and_no_artifacts(tmp_path):
    with pytest.raises(ValueError, match="required executable unavailable"):
        render(compose(), tmp_path / "audio.wav", sclang="dinad-no-such-executable")
    assert list(tmp_path.iterdir()) == []


def test_subprocess_errors_and_timeouts_keep_diagnostics(tmp_path):
    log = tmp_path / "process.log"
    with pytest.raises(RuntimeError, match="exited 7"):
        _run([sys.executable, "-c", 'import sys; print("FAIL marker"); sys.exit(7)'], log, 5)
    assert "FAIL marker" in log.read_text()
    with pytest.raises(RuntimeError, match="timed out"):
        _run([sys.executable, "-c", 'import time; print("before timeout", flush=True); time.sleep(5)'], log, 0.2)
    assert "before timeout" in log.read_text()
    with pytest.raises(RuntimeError, match="engine error"):
        _run([sys.executable, "-c", 'print("FAILURE IN SERVER /s_new")'], log, 5)


def make_wav(path, samples, rate=48000, channels=2):
    audio = struct.pack('<' + 'f' * len(samples), *samples)
    fmt = struct.pack('<HHIIHH', 3, channels, rate, rate * channels * 4, channels * 4, 32)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(audio)) + audio
    path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)


def test_audio_validation_checks_real_samples_and_format(tmp_path):
    path = tmp_path / "audio.wav"
    make_wav(path, [0.1, -0.1] * 480)
    metrics = inspect_wav(path, Settings(), 0.01)
    assert metrics["peak"] == pytest.approx(0.1)
    assert metrics["rms"] == pytest.approx(0.1)
    assert metrics["duration_seconds"] == 0.01
    for samples, expected in (([0, 0] * 480, "silent"), ([float("nan"), 0] * 480, "non-finite"),
                              ([1.1, 0] * 480, "full scale")):
        make_wav(path, samples)
        with pytest.raises(ValueError, match=expected):
            inspect_wav(path, Settings(), 0.01)
    make_wav(path, [0.1, -0.1] * 480, rate=44100)
    with pytest.raises(ValueError, match="requested sample rate"):
        inspect_wav(path, Settings(), 0.01)
    path.write_bytes(b"not a WAV")
    with pytest.raises(ValueError, match="RIFF WAV"):
        inspect_wav(path, Settings(), 0.01)


def test_existing_output_is_preserved(tmp_path):
    output = tmp_path / "existing.wav"
    output.write_bytes(b"keep")
    with pytest.raises(ValueError, match="already exists"):
        render(compose(), output, sclang=sys.executable, scsynth=sys.executable)
    assert output.read_bytes() == b"keep"


def test_invalid_compiler_output_is_not_published(tmp_path):
    # Python accepts -v for version reporting, but cannot compile the SC source.
    with pytest.raises(RuntimeError):
        render(compose(), tmp_path / "failed.wav", sclang=sys.executable, scsynth=sys.executable)
    assert not (tmp_path / "failed.wav").exists()
    assert list(tmp_path.glob("dinad-render-*/sclang.log"))


def test_live_source_synchronizes_definitions_and_checks_sample_rate():
    source = live_source(compose(), Settings(), "127.0.0.1", 57110)
    assert source.index('"/status"') < source.index('"/d_recv"') < source.index("sendBundle")
    assert "server.sync" in source
    assert "message[8] != 48000" in source
    assert "Main.elapsedTime" in source
    assert "server.boot" not in source
    assert "DINAD_LIVE_OK" in source
    with pytest.raises(ValueError):
        live_source(compose(), Settings(), "127.0.0.1", 0)
    with pytest.raises(ValueError):
        live_source(compose(), Settings(), "127.0.0.1", 57110, lookahead=0)


def test_live_requires_explicit_exclusive_server_and_sufficient_timeout(tmp_path):
    with pytest.raises(ValueError, match="exclusive server"):
        play(compose(), tmp_path / "live")
    with pytest.raises(ValueError, match="timeout must exceed"):
        play(compose(), tmp_path / "live", exclusive=True, timeout=1)
    assert list(tmp_path.iterdir()) == []


def test_live_timeout_keeps_logs_and_attempts_group_cleanup(tmp_path, monkeypatch):
    import dinad.supercollider as sc
    sent = []
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, timeout): pass
        def sendto(self, packet, address): sent.append((packet, address))
    def fail(arguments, log, timeout):
        log.write_text("simulated server readiness timeout")
        raise RuntimeError("timed out")
    monkeypatch.setattr(sc, "_run", fail)
    monkeypatch.setattr("socket.socket", lambda *args: Socket())
    with pytest.raises(RuntimeError, match="timed out"):
        play(compose(), tmp_path / "live", sclang=sys.executable, exclusive=True)
    assert (tmp_path / "live/sclang-live.log").exists()
    assert sent[0] == (b'/n_free\0,i\0\0' + struct.pack('>i', 1000), ('127.0.0.1', 57110))


@pytest.mark.skipif(not os.environ.get("DINAD_TEST_SCLANG"), reason="optional actual SuperCollider NRT test")
def test_actual_supercollider_render(tmp_path, monkeypatch):
    import dinad.supercollider as sc
    original = sc.score_source
    # A user's language startup may change the default musical clock. Resolved
    # event times must still be seconds and render the same duration.
    def changed_default_clock(*args):
        return original(*args).replace('var defs, score;',
                                      'var defs, score;\nTempoClock.default.tempo = 2;')
    monkeypatch.setattr(sc, 'score_source', changed_default_clock)
    directory = tmp_path / "render é with spaces"
    directory.mkdir()
    result = render(compose(seed=42), directory / 'demo é.wav',
                    sclang=os.environ["DINAD_TEST_SCLANG"],
                    scsynth=os.environ["DINAD_TEST_SCSYNTH"],
                    library=Path(os.environ["DINAD_TEST_LIBRARY"]) if os.environ.get("DINAD_TEST_LIBRARY") else None,
                    plugins=Path(os.environ["DINAD_TEST_PLUGINS"]) if os.environ.get("DINAD_TEST_PLUGINS") else None)
    assert result["event_count"] == 61
    assert result["audio"]["peak"] > 0
    assert result["audio"]["peak"] < 1
    assert result["audio"]["duration_seconds"] == pytest.approx(10.2, abs=0.003)
    assert (Path(result["logs"]) / "manifest.json").exists()


def test_live_simultaneous_setup_is_one_ordered_bundle_and_fail_replies_are_fatal():
    source = live_source(compose(), Settings(), "127.0.0.1", 57110)
    bundles = [line for line in source.splitlines() if 'server.addr.sendBundle' in line]
    assert '["/g_new", 1000, 0, 0], ["/s_new", "dinad_effects", 1001, 1, 1000]' in bundles[0]
    assert "'/fail'" in source
    assert 'ERROR: server failure' in source
    assert '1.exit' in source


def test_live_success_requires_all_voice_start_and_end_notifications():
    source = live_source(compose(), Settings(), "127.0.0.1", 57110)
    assert "'/n_go'" in source and "'/n_end'" in source
    assert '(starts.size != 61)' in source and '(ends.size != 61)' in source
    assert 'incomplete voice lifecycle' in source


def test_render_rejects_corrupt_successful_server_output_without_publishing(tmp_path, monkeypatch):
    import dinad.supercollider as sc
    def fake_run(args, log, timeout):
        log.write_text("fake subprocess success")
        if '-v' in args:
            return "test-version"
        if '-D' in args:
            (log.parent / "score.osc").write_bytes(b"score")
            return "DINAD_SCORE_OK"
        if '-N' in args:
            Path(args[4]).write_bytes(b"invalid WAV data" * 10)
        return ""
    monkeypatch.setattr(sc, '_run', fake_run)
    with pytest.raises(RuntimeError, match="audio validation failed"):
        render(compose(), tmp_path / 'output.wav', sclang=sys.executable, scsynth=sys.executable)
    assert not (tmp_path / 'output.wav').exists()
    assert list(tmp_path.glob('dinad-render-*/scsynth.log'))


def test_truncated_wav_format_fails_cleanly(tmp_path):
    path = tmp_path / 'truncated.wav'
    path.write_bytes(b'RIFF' + struct.pack('<I', 100) + b'WAVEfmt ' + struct.pack('<I', 16) + b'bad')
    with pytest.raises(ValueError, match='format chunk is truncated'):
        inspect_wav(path, Settings(), 1)


def test_backend_protocol_stages_without_launching_audio_and_rejects_bad_events(monkeypatch):
    from dinad.engine import replay
    from dinad.supercollider import SuperColliderBackend
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('submit launched audio'))
    backend = SuperColliderBackend()
    events = compose()
    replay(events, backend)
    assert backend.events == events
    with pytest.raises(ValueError, match='unsupported'):
        backend.submit(Event('missing', 60, 20, 1))
    with pytest.raises(ValueError, match='timestamp order'):
        backend.submit(Event('pad', 60, 0, 1))
    assert backend.events == events
