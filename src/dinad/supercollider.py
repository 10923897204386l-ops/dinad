"""Provisional SuperCollider score compiler and explicit offline/live execution."""

from dataclasses import asdict, dataclass
import hashlib
from itertools import groupby
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile

from dinad import __version__
from dinad.engine import Event, MockBackend, replay
from dinad.objects import _finite


INSTRUMENTS = {"drone": "dinad_drone", "pad": "dinad_pad",
               "arp.slow": "dinad_arp", "arp.fast": "dinad_arp"}


@dataclass(frozen=True)
class Settings:
    sample_rate: int = 48000
    tail: float = 2
    # Offline score setup precedes the musical transport origin.
    preroll: float = 0.1

    def __post_init__(self) -> None:
        if type(self.sample_rate) is not int or not 8000 <= self.sample_rate <= 192000:
            raise ValueError("sample_rate must be an integer from 8000 to 192000")
        _finite(self.tail, "effect tail")
        _finite(self.preroll, "preroll")
        if not 0.5 <= self.tail <= 60 or not 0.05 <= self.preroll <= 5:
            raise ValueError("tail must be 0.5–60 seconds and preroll 0.05–5 seconds")


@dataclass(frozen=True)
class Command:
    time: float
    arguments: tuple


def compile_events(events: tuple[Event, ...], settings: Settings) -> tuple[Command, ...]:
    """Validate complete history, then produce ordered setup/on/off/cleanup commands.

    Node 1000 owns the run, node 1001 is the effects return; private stereo bus
    16 feeds it. Live use requires an exclusive server with these resources free.
    At equal times, setup precedes releases, then starts in input history order.
    """
    replay(events, MockBackend())
    commands = [Command(0, ("/g_new", 1000, 0, 0)),
                Command(0, ("/s_new", "dinad_effects", 1001, 1, 1000))]
    for index, event in enumerate(events):
        if event.instrument not in INSTRUMENTS:
            raise ValueError(f"unsupported SuperCollider instrument: {event.instrument}")
        try:
            frequency = 440 * 2 ** ((event.pitch - 69) / 12)
        except OverflowError as exc:
            raise ValueError("pitch cannot be represented as a frequency") from exc
        if not math.isfinite(frequency) or not 0 < frequency < settings.sample_rate / 2:
            raise ValueError("pitch frequency must be positive and below sample-rate Nyquist")
        start = event.time + settings.preroll
        end = event.time + event.duration + settings.preroll
        _finite(start, "scheduled start")
        _finite(end, "scheduled release")
        # SC controls and OSC timestamps must preserve a positive lifetime.
        if end >= 2**31:
            raise ValueError("SuperCollider score timestamps must be below 2**31 seconds")
        if end <= start:
            raise ValueError("event duration is too small for its scheduled timestamp")
        node = 1002 + index
        if node >= 2**31:
            raise ValueError("too many voices for SuperCollider node IDs")
        commands.extend((
            Command(start, ("/s_new", INSTRUMENTS[event.instrument], node, 2, 1001,
                            "freq", frequency, "out", 16)),
            Command(end, ("/n_set", node, "gate", 0)),
        ))
    end = max((event.time + event.duration for event in events), default=0)
    finish = settings.preroll + end + settings.tail
    _finite(finish, "score end")
    if finish + settings.preroll >= 2**31:
        raise ValueError("SuperCollider score timestamps must be below 2**31 seconds")
    if finish <= settings.preroll + end:
        raise ValueError("effect tail is too small for the score timestamp")
    commands.append(Command(finish, ("/n_free", 1000)))
    def order(command: Command):
        priority = {"/g_new": 0, "/n_set": 1, "/s_new": 2, "/n_free": 3}
        return command.time, priority[command.arguments[0]]
    return tuple(sorted(commands, key=order))


def _sc_string(value: str) -> str:
    # SC double-quoted strings interpolate neither Python nor shell syntax.
    return json.dumps(value, ensure_ascii=False)


def _sc_value(value) -> str:
    return _sc_string(value) if isinstance(value, str) else repr(value)


def _literal(command: Command, offset: float = 0) -> str:
    return f'[{command.time + offset!r}, [{", ".join(_sc_value(v) for v in command.arguments)}]]'


def _assets() -> str:
    return files("dinad").joinpath("synthesis/voices.scd").read_text(encoding="utf-8")


def score_source(events: tuple[Event, ...], settings: Settings, score_path: Path) -> str:
    commands = compile_events(events, settings)
    # d_recv completes before later timestamped setup commands; compile assets
    # into bytecode within sclang, so no machine-specific SynthDef paths persist.
    lines = ",\n".join(_literal(command, settings.preroll) for command in commands)
    return f'''(
var defs, score;
try {{
    defs = {_assets()};
    score = defs.collect {{ |definition| [0.0, ["/d_recv", definition.asBytes]] }};
    score = score ++ [\n{lines}\n];
    Score.write(score, {_sc_string(str(score_path))}, TempoClock(1));
    "DINAD_SCORE_OK".postln;
    0.exit;
}} {{ |error| error.reportError; 1.exit; }};
)
'''


def _run(arguments: list[str], log_path: Path, timeout: float) -> str:
    """Run without a shell; always preserve output, including on timeout."""
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        log_path.write_bytes((exc.stdout or b"") + (exc.stderr or b""))
        raise RuntimeError(f"{arguments[0]} timed out; see {log_path}") from exc
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} exited {result.returncode}; see {log_path}")
    if "FAILURE IN SERVER" in result.stdout + result.stderr or "ERROR:" in result.stdout + result.stderr:
        raise RuntimeError(f"{arguments[0]} reported an engine error; see {log_path}")
    return result.stdout + result.stderr


def _executable(value: str) -> str:
    resolved = shutil.which(value)
    if resolved is None:
        raise ValueError(f"required executable unavailable: {value}")
    return resolved


def _sclang_arguments(sclang: str, library: Path | None) -> list[str]:
    args = [_executable(sclang), "-D"]
    if library is not None:
        args.extend(("-l", str(library.resolve())))
    return args


def inspect_wav(path: Path, settings: Settings, expected_duration: float, *,
                require_signal: bool = True) -> dict:
    """Check actual float32 WAV output without requiring ffmpeg or an audio device."""
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("render did not produce a RIFF WAV file")
        audio_format = None
        while True:
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise ValueError("rendered WAV has no complete audio data")
            name, size = struct.unpack("<4sI", chunk)
            if name == b"fmt ":
                if size < 16:
                    raise ValueError("rendered WAV has an invalid format chunk")
                raw_format = stream.read(16)
                if len(raw_format) != 16:
                    raise ValueError("rendered WAV format chunk is truncated")
                audio_format = struct.unpack("<HHIIHH", raw_format)
                stream.seek(size - 16 + size % 2, 1)
            elif name == b"data":
                break
            else:
                stream.seek(size + size % 2, 1)
        if audio_format != (3, 2, settings.sample_rate, settings.sample_rate * 8, 8, 32):
            raise ValueError("rendered WAV must be stereo float32 at the requested sample rate")
        if not size or size % 8:
            raise ValueError("rendered WAV has invalid audio data length")
        duration = size / (settings.sample_rate * 8)
        if abs(duration - expected_duration) > 128 / settings.sample_rate:
            raise ValueError("rendered WAV duration differs from the compiled schedule")
        remaining, peak, energy = size, 0.0, 0.0
        while remaining:
            data = stream.read(min(65536, remaining))
            if not data or len(data) % 4:
                raise ValueError("rendered WAV audio is truncated")
            remaining -= len(data)
            for (sample,) in struct.iter_unpack("<f", data):
                if not math.isfinite(sample):
                    raise ValueError("rendered WAV contains non-finite samples")
                peak = max(peak, abs(sample))
                energy += sample * sample
        if require_signal and peak == 0:
            raise ValueError("rendered WAV is silent despite non-empty event history")
        if peak >= 1:
            raise ValueError("rendered WAV reaches or exceeds full scale")
        return {"duration_seconds": duration, "peak": peak,
                "rms": math.sqrt(energy / (size / 4))}


def render(events: tuple[Event, ...], output: Path, settings: Settings = Settings(), *,
           sclang: str = "sclang", scsynth: str = "scsynth", library: Path | None = None,
           plugins: Path | None = None, timeout: float = 60) -> dict:
    """Render stereo float WAV via NRT; no sound device or server boot needed.

    The output must not exist. Logs, generated script and score remain in an
    adjacent unique work directory. Publish output only after successful rendering.
    """
    _finite(timeout, "timeout")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    commands = compile_events(events, settings)
    language_args = _sclang_arguments(sclang, library)
    server = _executable(scsynth)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    if output.suffix.lower() != ".wav":
        raise ValueError("render output must end in .wav")
    work = Path(tempfile.mkdtemp(prefix="dinad-render-", dir=output.parent))
    score, script, temporary = work / "score.osc", work / "score.scd", work / "render.wav"
    script.write_text(score_source(events, settings, score), encoding="utf-8")
    versions = {
        "sclang": _run([language_args[0], "-v"], work / "sclang-version.log", timeout).strip(),
        "scsynth": _run([server, "-v"], work / "scsynth-version.log", timeout).strip(),
    }
    compiled = _run([*language_args, str(script)], work / "sclang.log", timeout)
    if "DINAD_SCORE_OK" not in compiled or not score.is_file() or score.stat().st_size == 0:
        raise RuntimeError(f"sclang did not create a complete score; see {work}")
    args = [server, "-N", str(score), "_", str(temporary), str(settings.sample_rate),
            "WAV", "float", "-o", "2", "-i", "0"]
    if plugins is not None:
        args.extend(("-U", str(plugins.resolve())))
    _run(args, work / "scsynth.log", timeout)
    if not temporary.is_file() or temporary.stat().st_size <= 44:
        raise RuntimeError(f"scsynth did not create audio; see {work}")
    try:
        metrics = inspect_wav(temporary, settings, commands[-1].time + settings.preroll,
                              require_signal=bool(events))
    except ValueError as exc:
        raise RuntimeError(f"rendered audio validation failed: {exc}; see {work}") from exc
    manifest = {
        "dinad_version": __version__, "backend": "supercollider-nrt-v1",
        "settings": asdict(settings), "channels": 2, "format": "WAV/float32",
        "synthdefs_sha256": hashlib.sha256(_assets().encode()).hexdigest(),
        "transport_offset_seconds": 2 * settings.preroll,
        "expected_duration_seconds": commands[-1].time + settings.preroll,
        "events_sha256": hashlib.sha256(json.dumps([asdict(e) for e in events],
            sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "event_count": len(events), "versions": versions, "logs": str(work),
        "audio": metrics,
    }
    # A hard link is an atomic no-clobber publication on the same filesystem.
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    os.link(temporary, output)
    return manifest


def live_source(events: tuple[Event, ...], settings: Settings, host: str, port: int,
                lookahead: float = 0.2) -> str:
    """Exclusive externally booted server; synchronize definitions before scheduling."""
    _finite(lookahead, "lookahead")
    if not 0.05 <= lookahead <= 5:
        raise ValueError("lookahead must be 0.05–5 seconds")
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(host, str) or not host:
        raise ValueError("live server requires a non-empty host and port 1–65535")
    commands = compile_events(events, settings)
    # All messages are sent ahead as absolute OSC timetags from one fixed origin.
    bundles = []
    for time, simultaneous in groupby(commands, key=lambda command: command.time):
        messages = ", ".join('[' + ", ".join(_sc_value(v) for v in command.arguments) + ']'
                             for command in simultaneous)
        bundles.append(f'    server.addr.sendBundle(origin + {time!r} - Main.elapsedTime, {messages});')
    sends = "\n".join(bundles)
    return f'''(
var server, defs, failures, starts, ends, onStart, onEnd;
server = Server(\\dinad, NetAddr({_sc_string(host)}, {port}));
defs = {_assets()};
starts = Set.new; ends = Set.new;
onStart = OSCFunc({{ |message|
    if((message[1] >= 1002) && (message[1] < {1002 + len(events)})) {{ starts.add(message[1]) }};
}}, '/n_go', server.addr);
onEnd = OSCFunc({{ |message|
    if((message[1] >= 1002) && (message[1] < {1002 + len(events)})) {{ ends.add(message[1]) }};
}}, '/n_end', server.addr);
failures = OSCFunc({{ |message|
    ("ERROR: server failure " ++ message.asString).postln;
    server.addr.sendMsg("/n_free", 1000);
    server.addr.sendMsg("/notify", 0);
    1.exit;
}}, '/fail', server.addr);
Routine {{
    var origin, ready, responder;
    ready = Condition(false);
    responder = OSCFunc({{ |message|
        if(message[8] != {settings.sample_rate}) {{
            "ERROR: server sample rate does not match requested sample rate".postln;
            1.exit;
        }};
        ready.test = true; ready.signal;
    }}, '/status.reply', server.addr);
    server.addr.sendMsg("/status");
    ready.wait;
    responder.free;
    server.addr.sendMsg("/notify", 1);
    server.sync;
    defs.do {{ |definition| server.sendMsg("/d_recv", definition.asBytes) }};
    server.sync;
    origin = Main.elapsedTime + {lookahead!r};
{sends}
    ({commands[-1].time!r} + {lookahead!r} + 0.1).wait;
    server.sync;
    if((starts.size != {len(events)}) || (ends.size != {len(events)})) {{
        ("ERROR: incomplete voice lifecycle: " ++ starts.size ++ " starts, " ++ ends.size ++ " ends").postln;
        server.addr.sendMsg("/notify", 0);
        1.exit;
    }};
    server.addr.sendMsg("/notify", 0);
    server.sync;
    failures.free; onStart.free; onEnd.free;
    "DINAD_LIVE_OK".postln;
    0.exit;
}}.play(SystemClock);
)
'''


def play(events: tuple[Event, ...], work: Path, settings: Settings = Settings(), *,
         host: str = "127.0.0.1", port: int = 57110, lookahead: float = 0.2,
         sclang: str = "sclang", library: Path | None = None, timeout: float = 60,
         exclusive: bool = False) -> dict:
    """Submit to an exclusively owned, running server. Never boots audio implicitly.

    On failure, attempt to free only DINAD's group, not other server resources.
    Server readiness/definition synchronization runs in the language process;
    its entire lifetime is bounded by the subprocess timeout.
    """
    import socket

    if exclusive is not True:
        raise ValueError("live playback requires explicit exclusive server ownership")
    source = live_source(events, settings, host, port, lookahead)
    _finite(timeout, "timeout")
    finish = compile_events(events, settings)[-1].time + lookahead + 0.1
    if timeout <= finish:
        raise ValueError("live timeout must exceed the complete score duration and lookahead")
    arguments = _sclang_arguments(sclang, library)
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    script = work / "live.scd"
    script.write_text(source, encoding="utf-8")
    try:
        result = _run([*arguments, str(script)], work / "sclang-live.log", timeout)
        if "DINAD_LIVE_OK" not in result:
            raise RuntimeError(f"live run did not complete; see {work}")
    except (RuntimeError, OSError):
        # /n_free with one signed int32, using the same exclusive group contract.
        # Best effort only: UDP submission does not establish server cleanup.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.settimeout(1)
                connection.sendto(b"/n_free\0" + b",i\0\0" + struct.pack(">i", 1000), (host, port))
        except OSError:
            pass
        raise
    return {"backend": "supercollider-live-v1", "host": host, "port": port,
            "event_count": len(events), "logs": str(work),
            "note": "voice lifecycle completed; listening/device quality not established"}


class SuperColliderBackend:
    """Backend protocol adapter: stage events, then explicitly render or play.

    submit never starts a process or touches audio; this preserves ahead-of-time
    validation and makes it substitutable for the mock event receiver.
    """

    version = "supercollider-v1"

    def __init__(self, settings: Settings = Settings()) -> None:
        self.settings = settings
        self._history = MockBackend()

    @property
    def events(self) -> tuple[Event, ...]:
        return self._history.events

    def submit(self, event: Event) -> None:
        compile_events((event,), self.settings)
        self._history.submit(event)

    def render(self, output: Path, **kwargs) -> dict:
        return render(self.events, output, self.settings, **kwargs)

    def play(self, work: Path, **kwargs) -> dict:
        return play(self.events, work, self.settings, **kwargs)
