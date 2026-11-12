"""DINAD adapter for the verified MDA ePiano LV2 helper."""

from collections import Counter
from itertools import groupby
import math
import os
from pathlib import Path
import select
import shutil
import subprocess
from tempfile import TemporaryDirectory, TemporaryFile
import time

from dinad.engine import MockBackend
from dinad.events import Event
from dinad.modules import (
    AudioPort, AutomationRate, Capability, Dependency, ModuleDescriptor, ModuleKind,
    ParameterDescriptor, ParameterType, ParameterValue, StagedLifecycle,
)
from dinad.audio import inspect_float_wav
from dinad.objects import _finite

IDENTIFIER = "dinad.lv2_epiano"
STATE_SCHEMA = "dinad.epiano.controls.v1"
PLUGIN_URI = "http://drobilla.net/plugins/mda/EPiano"
PORTS = (
    ("env_decay", "Envelope Decay", .5),
    ("env_release", "Envelope Release", .5),
    ("hardness", "Hardness", .5),
    ("treble_boost", "Treble Boost", .5),
    ("modulation", "Modulation", .5),
    ("lfo_rate", "LFO Rate", .65),
    ("vel_sense", "Velocity Sense", .25),
    ("stereo_width", "Stereo Width", .5),
    ("polyphonic", "Polyphonic", True),
    ("fine_tuning", "Fine Tuning", .5),
    ("random_tuning", "Random Tuning", .146),
    ("overdrive", "Overdrive", 0.),
)
PORT_INDEX = {symbol: index for index, (symbol, _, _) in enumerate(PORTS)}
SETTINGS = frozenset((*PORT_INDEX, "sample_rate", "tail", "helper"))
LIVE_SETTINGS = (SETTINGS - {"tail"}) | {"output_left", "output_right"}
AUDIO_CAPABILITIES = frozenset({Capability.CONTINUOUS_TRANSPORT, Capability.OFFLINE_RENDER})


def _pitch(event: Event) -> int:
    note = int(event.pitch)
    if note != event.pitch or not 0 <= note <= 127:
        raise ValueError("ePiano requires integer MIDI pitches from 0 through 127")
    return note


class MidiGates:
    """MIDI note-offs are per pitch. Hold overlaps until the last gate ends."""

    def __init__(self) -> None:
        self.nodes: dict[int, int] = {}
        self.counts: Counter[int] = Counter()

    def update(self, releases, starts) -> list[tuple[int, int]]:
        commands = []
        for node in releases:
            note = self.nodes.pop(node)
            self.counts[note] -= 1
            if not self.counts[note]:
                del self.counts[note]
                commands.append((128 + note, 0))
        for node, event in starts:
            note = _pitch(event)
            if not self.counts[note]:
                commands.append((note, 80))
            self.counts[note] += 1
            self.nodes[node] = note
        return commands


def _batch(lines: list[str]) -> str:
    return f"{len(lines)}\n" + "".join(f"{line}\n" for line in lines)


def _offline_score(events: tuple[Event, ...], rate: int) -> list[str]:
    boundaries = []
    for node, event in enumerate(events):
        _pitch(event)
        end = event.time + event.duration
        if end > 3600:
            raise ValueError("ePiano rendering is limited to one hour")
        start_frame, end_frame = round(event.time * rate), round(end * rate)
        if start_frame >= end_frame:
            raise ValueError("ePiano note must span at least one audio frame")
        boundaries.extend(((start_frame, 1, node, event), (end_frame, 0, node, event)))
    boundaries.sort(key=lambda item: item[:3])
    gates = MidiGates()
    lines = []
    for frame, group in groupby(boundaries, key=lambda item: item[0]):
        releases, starts = [], []
        for _, on, node, event in group:
            if on:
                starts.append((node, event))
            else:
                releases.append(node)
        lines.extend(f"m {frame} {code} {velocity}" for code, velocity in gates.update(releases, starts))
    return lines


def _probe(helper: str) -> str:
    executable = shutil.which(helper)
    if executable is None:
        raise ValueError("ePiano helper missing. Build tools/lv2/dinad-epiano and set helper in DINAD TOML.")
    try:
        result = subprocess.run([executable, "probe"], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ePiano discovery timed out") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ePiano discovery failed")
    lines = result.stdout.splitlines()
    if not lines or lines[0] != f"{PLUGIN_URI} 2.0" or len(lines) != len(PORTS) + 1:
        raise ValueError("unverified ePiano metadata")
    for line, (symbol, _, default) in zip(lines[1:], PORTS):
        fields = line.split()
        if len(fields) != 4 or fields[:3] != [symbol, "0", "1"]:
            raise ValueError(f"ePiano port metadata mismatch for {symbol}")
        if not math.isclose(float(fields[3]), default, abs_tol=1e-6):
            raise ValueError(f"ePiano default mismatch for {symbol}")
    return executable


def _values(values: dict[str, ParameterValue]) -> list[str]:
    return [str(float(values[symbol])) for symbol, _, _ in PORTS]


class EPianoLive:
    lead = .05

    def __init__(self, values: dict[str, ParameterValue]):
        self.initial = values.copy()
        self.destinations = (str(values.get("output_left", "")), str(values.get("output_right", "")))
        if any(self.destinations) and (not all(self.destinations) or self.destinations[0] == self.destinations[1]):
            raise ValueError("set two distinct ePiano stereo destination ports")
        self.values = values.copy()
        self.automation: list[tuple[float, dict[str, ParameterValue]]] = []
        self.rate = int(values["sample_rate"])
        self.process = None
        self.log = None
        self.gates = MidiGates()
        self.pending: dict[str, ParameterValue] = {}
        self.diagnostics = ""

    def validate(self, events: tuple[Event, ...]) -> None:
        for event in events:
            _pitch(event)

    def _read_ack(self, expected: bytes) -> None:
        assert self.process is not None and self.process.stdout is not None
        deadline = time.monotonic() + 2
        result = b""
        while b"\n" not in result:
            if not select.select([self.process.stdout], [], [], max(0., deadline - time.monotonic()))[0]:
                raise RuntimeError("ePiano helper acknowledgement timed out")
            part = os.read(self.process.stdout.fileno(), 256)
            if not part or len(result) + len(part) > 256:
                raise RuntimeError(self._diagnostics() or "ePiano helper disconnected")
            result += part
        if result != expected + b"\n":
            raise RuntimeError("unexpected ePiano helper response")

    def _diagnostics(self) -> str:
        return os.pread(self.log.fileno(), 8192, 0).decode(errors="replace").strip() if self.log else ""

    def open(self) -> None:
        executable = _probe(str(self.values["helper"]))
        self.gates = MidiGates()
        self.pending.clear()
        self.log = TemporaryFile()
        try:
            self.process = subprocess.Popen(
                [executable, "live", str(self.rate), *_values(self.initial),
                 *(self.destinations if all(self.destinations) else ())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            )
            self._read_ack(b"READY")
            self.values = self.initial.copy()
            self.automation.clear()
            self.diagnostics = ""
        except BaseException:
            self.close(())
            raise

    def set_controls(self, values: dict[str, ParameterValue]) -> None:
        for key, value in values.items():
            if key not in PORT_INDEX:
                raise ValueError(f"unknown ePiano control {key!r}")
            EPianoBackend.descriptor.parameter(key).validate(value)
        self.pending = {key: value for key, value in values.items() if self.values[key] != value}

    def send(self, deadline: float, releases, starts) -> None:
        assert self.process is not None and self.process.stdin is not None
        at = round(deadline * 1_000_000)
        lines = [f"p {at} {PORT_INDEX[key]} {float(value)}" for key, value in self.pending.items()]
        lines.extend(f"m {at} {code} {velocity}" for code, velocity in self.gates.update(releases, starts))
        self.process.stdin.write(_batch(lines).encode("ascii"))
        self.process.stdin.flush()
        self._read_ack(b"OK")
        self.values.update(self.pending)
        if self.pending:
            self.automation.append((deadline, self.pending.copy()))
        self.pending.clear()

    def metadata(self, origin: float, stop: float) -> dict:
        return {"module_diagnostics": self.diagnostics, "module_state": {
            "schema": STATE_SCHEMA,
            "module_id": IDENTIFIER,
            "version": EPianoBackend.version,
            "controls": {key: self.initial[key] for key in PORT_INDEX},
            "stop_seconds": stop,
            "automation": [{"seconds": deadline - origin, "values": values}
                           for deadline, values in self.automation if 0 <= deadline - origin < stop],
        }}

    def close(self, active) -> None:
        process, self.process = self.process, None
        error = None
        try:
            if process is not None:
                assert process.stdin is not None and process.stdout is not None
                try:
                    try:
                        process.stdin.close()  # EOF cancels future events and releases all pitches.
                    except BrokenPipeError:
                        pass
                    if process.wait(timeout=2):
                        error = RuntimeError(self._diagnostics() or "ePiano live host failed")
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    error = RuntimeError("ePiano host timed out while releasing notes")
                finally:
                    process.stdout.close()
        finally:
            if self.log:
                self.diagnostics = self._diagnostics()
                self.log.close()
                self.log = None
        if error:
            raise error


class EPianoBackend(StagedLifecycle):
    version = "mda-epiano-2.0-v1"
    descriptor = ModuleDescriptor(
        identifier=IDENTIFIER, version=version, kind=ModuleKind.INSTRUMENT,
        capabilities=AUDIO_CAPABILITIES | {Capability.STAGED_NOTES,
                                          Capability.PARAMETER_READ, Capability.PARAMETER_WRITE,
                                          Capability.STATE_SAVE, Capability.STATE_RESTORE},
        state_schema=STATE_SCHEMA,
        parameters=(
            ParameterDescriptor("helper", "Headless helper", ParameterType.STRING, "dinad-epiano", readable=True),
            ParameterDescriptor("sample_rate", "Sample rate", ParameterType.INTEGER, 48000, 8000, 192000,
                                "Hz", readable=True),
            ParameterDescriptor("output_left", "Left JACK destination", ParameterType.STRING, "", readable=True),
            ParameterDescriptor("output_right", "Right JACK destination", ParameterType.STRING, "", readable=True),
            ParameterDescriptor("tail", "Render tail", ParameterType.FLOAT, 2., .5, 60., "s", readable=True),
            *(ParameterDescriptor(symbol, label, ParameterType.BOOLEAN if type(default) is bool else ParameterType.FLOAT,
                                  default, **({} if type(default) is bool else {"minimum": 0., "maximum": 1.}),
                                  readable=True, writable=True, automation=AutomationRate.SAMPLE)
              for symbol, label, default in PORTS),
        ),
        dependencies=(Dependency("dinad-epiano and Fedora lv2-mdala-plugins", AUDIO_CAPABILITIES),
                      Dependency("PipeWire JACK", frozenset({Capability.CONTINUOUS_TRANSPORT}))),
        audio_ports=(AudioPort("master", "output", 2),),
        limitations=("One verified LV2 instrument with integer MIDI pitch and fixed velocity.",
                     "DINAD recordings recall control values and automation. Opaque state and vendor presets are unavailable."),
    )

    def __init__(self, settings) -> None:
        super().__init__()
        self.values = {p.native_id: p.default for p in self.descriptor.parameters}
        for key, value in settings.items():
            self.descriptor.parameter(key).validate(value)
            self.values[key] = value
        self.history = MockBackend()

    def submit(self, event: Event) -> None:
        self._require_ready()
        _pitch(event)
        self.history.submit(event)

    def get_parameter(self, native_id: str) -> ParameterValue:
        self.descriptor.parameter(native_id)
        return self.values[native_id]

    def set_parameter(self, native_id: str, value: ParameterValue) -> None:
        self._require_ready()
        parameter = self.descriptor.parameter(native_id)
        if not parameter.writable:
            raise ValueError(f"{native_id} is read-only")
        parameter.validate(value)
        self.values[native_id] = value

    def live_output(self, **options) -> EPianoLive:
        self._require_ready()
        return EPianoLive(self.values)

    @classmethod
    def restore_controls(cls, state: dict, end: float) -> tuple[
            dict[str, ParameterValue], list[tuple[float, dict[str, ParameterValue]]]]:
        if (state.get("schema"), state.get("module_id"), state.get("version")) != (
                STATE_SCHEMA, IDENTIFIER, cls.version):
            raise ValueError("recorded ePiano state is incompatible")
        controls, changes = state.get("controls"), state.get("automation")
        if not isinstance(controls, dict) or controls.keys() != PORT_INDEX.keys() or not isinstance(changes, list):
            raise ValueError("invalid recorded ePiano controls or automation")
        for key, value in controls.items():
            cls.descriptor.parameter(key).validate(value)
        previous = 0.
        automation = []
        for change in changes:
            if not isinstance(change, dict) or not isinstance(change.get("values"), dict):
                raise ValueError("invalid recorded ePiano parameter change")
            seconds = change.get("seconds")
            _finite(seconds, "automation time")
            if not previous <= seconds <= end:
                raise ValueError("ePiano automation time is out of order or outside the recording")
            previous = seconds
            for key, value in change["values"].items():
                if key not in PORT_INDEX:
                    raise ValueError(f"invalid recorded ePiano parameter {key!r}")
                cls.descriptor.parameter(key).validate(value)
            automation.append((seconds, change["values"]))
        return controls, automation

    def render(self, output: Path, *, timeout: float = 60,
               module_state: dict | None = None, **options) -> dict:
        self._require_ready()
        _finite(timeout, "timeout")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if output.exists():
            raise ValueError("output already exists")
        rate = int(self.values["sample_rate"])
        lines = _offline_score(self.history.events, rate)
        end = max((e.time + e.duration for e in self.history.events), default=0.)
        if module_state is not None:
            if not isinstance(module_state, dict):
                raise ValueError("invalid recorded ePiano state")
            # A take can contain automation after its last note ended.
            stop = module_state.get("stop_seconds", end)
            _finite(stop, "stop time")
            if not end - 1e-8 <= stop <= 3600:
                raise ValueError("recorded ePiano stop time is invalid")
            controls, automation = self.restore_controls(module_state, stop)
            self.values.update(controls)
            lines.extend(f"p {round(seconds * rate)} {PORT_INDEX[key]} {float(value)}"
                         for seconds, values in automation for key, value in values.items())
            lines.sort(key=lambda line: (int(line.split()[1]), line[0] != "p"))
            end = stop
        frames = math.ceil((end + float(self.values["tail"])) * rate)
        if frames > min(rate * 3600, (2**32 - 1024) // 8):
            raise ValueError("ePiano render exceeds one hour or the stereo float WAV size limit")
        if len(lines) > 200000:
            raise ValueError("ePiano render exceeds the 200000-event host limit")
        helper = _probe(str(self.values["helper"]))
        with TemporaryDirectory(prefix=".dinad-epiano-", dir=output.parent) as directory:
            wav = Path(directory) / "render.wav"
            try:
                result = subprocess.run([helper, "render", str(rate), str(wav), str(frames), *_values(self.values)],
                                        input=_batch(lines), text=True, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("ePiano render timed out") from exc
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "ePiano render failed")
            metrics = inspect_float_wav(wav, rate, frames / rate,
                                  require_signal=bool(self.history.events))
            os.link(wav, output)
        return {"backend": self.version, "plugin": PLUGIN_URI, "event_count": len(self.history.events),
                "settings": {key: value for key, value in self.values.items() if key in SETTINGS and key != "helper"},
                "audio": metrics}
