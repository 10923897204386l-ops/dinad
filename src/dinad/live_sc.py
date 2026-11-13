"""Persistent, acknowledged SuperCollider output for DINAD's live transport."""

from pathlib import Path
import socket
import struct
from tempfile import TemporaryDirectory
import time

from dinad.supercollider import (
    INSTRUMENTS, Settings, _assets, _run, _sc_string, _sclang_arguments, voice_arguments,
)


def _text(value: str) -> bytes:
    data = value.encode("utf-8") + b"\0"
    return data + b"\0" * (-len(data) % 4)


def _message(address, *values) -> bytes:
    tags = [","]
    parts = []
    for value in values:
        if isinstance(value, str):
            tags.append("s")
            parts.append(_text(value))
        elif isinstance(value, bytes):
            tags.append("b")
            parts.append(struct.pack(">i", len(value)) + value + b"\0" * (-len(value) % 4))
        elif isinstance(value, int):
            tags.append("i")
            parts.append(struct.pack(">i", value))
        else:
            tags.append("f")
            parts.append(struct.pack(">f", value))
    return _text(address) + _text("".join(tags)) + b"".join(parts)


def _bundle(messages, timestamp: float | None) -> bytes:
    if timestamp is None:
        timetag = 1
    else:
        # OSC's 32-bit seconds field wraps at the NTP era boundary.
        ntp = timestamp + 2_208_988_800
        timetag = int(ntp * 2**32) % 2**64
    return b"#bundle\0" + struct.pack(">Q", timetag) + b"".join(
        struct.pack(">I", len(message)) + message for message in messages)


class SuperColliderLive:
    """Use a dedicated local TCP-enabled scsynth and DINAD's reserved group/bus."""

    lead = .05

    def __init__(self, *, port: int, settings: Settings = Settings(),
                 sclang: str = "sclang", library: Path | None = None,
                 audible_drone: bool = False) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("TCP port must be between 1 and 65535")
        self.port = port
        self.settings = settings
        self.sclang = sclang
        self.library = library
        self.audible_drone = audible_drone
        self._connection: socket.socket | None = None
        self._definitions: tuple[bytes, ...] = ()
        self._serial = 0
        self._owns_group = False
        self._wall_offset = 0.

    def validate(self, events) -> None:
        for event in events:
            voice_arguments(event, self.settings, 1002)

    def _compile(self) -> tuple[bytes, ...]:
        with TemporaryDirectory(prefix="dinad-live-") as directory:
            work = Path(directory)
            script = work / "defs.scd"
            assets = _assets()
            if self.audible_drone:
                assets = assets.replace(
                    "Env.asr(0.02, 1, 0.3)", "Env.asr(0.12, 1, 0.3)", 1
                ).replace(
                    "SinOsc.ar(freq) * amp * env) ! 2",
                    "Mix(SinOsc.ar(freq * [1, 2, 4]) * [0.55, 0.9, 0.7]) * amp * env) ! 2",
                    1,
                ).replace("Env.asr(0.02, 1, 0.4)", "Env.asr(0.10, 1, 0.4)", 1)
            script.write_text(f"({assets}.do {{ |def| def.writeDefFile({_sc_string(directory)}) }}; 0.exit;)\n")
            log = work / "compile.log"
            try:
                _run([*_sclang_arguments(self.sclang, self.library), str(script)], log, 15)
                names = sorted(set(INSTRUMENTS.values()) | {"dinad_effects"})
                return tuple((work / f"{name}.scsyndef").read_bytes() for name in names)
            except (ValueError, OSError, RuntimeError) as exc:
                detail = log.read_text(errors="replace")[-2000:] if log.exists() else ""
                raise RuntimeError(f"live SynthDef compilation failed: {exc}\n{detail}") from exc

    def _connect(self) -> socket.socket:
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return connection

    def _write(self, packet: bytes) -> None:
        assert self._connection is not None
        self._connection.settimeout(2)
        self._connection.sendall(struct.pack(">I", len(packet)) + packet)

    def _response(self, deadline: float) -> bytes:
        assert self._connection is not None

        def read(size: int) -> bytes:
            data = bytearray()
            while len(data) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("SuperCollider acknowledgement timed out")
                self._connection.settimeout(remaining)
                chunk = self._connection.recv(size - len(data))
                if not chunk:
                    raise ConnectionError("SuperCollider closed the live connection")
                data.extend(chunk)
            return bytes(data)

        size, = struct.unpack(">I", read(4))
        if not 0 < size <= 65536:
            raise RuntimeError("invalid SuperCollider response size")
        return read(size)

    def _exchange(self, commands, timestamp: float | None = None) -> None:
        self._serial = self._serial % (2**31 - 1) + 1
        messages = [_message(*command) for command in commands]
        messages.append(_message("/sync", self._serial))
        self._write(_bundle(messages, timestamp))
        deadline = time.monotonic() + 2
        failure = None
        while True:
            reply = self._response(deadline)
            if reply.startswith(b"/fail\0"):
                failure = " ".join(part.decode("utf-8", "replace") for part in reply.split(b"\0") if part)
            if reply == _message("/synced", self._serial):
                if failure:
                    raise RuntimeError(f"SuperCollider {failure}")
                return

    def open(self) -> None:
        if not self._definitions:
            self._definitions = self._compile()
        self._connection = self._connect()
        try:
            self._write(_message("/status"))
            reply = self._response(time.monotonic() + 2)
            if not reply.startswith(b"/status.reply\0") or len(reply) < 56:
                raise RuntimeError("invalid SuperCollider status reply")
            nominal, _ = struct.unpack(">dd", reply[-16:])
            if nominal != self.settings.sample_rate:
                raise ValueError(f"SuperCollider rate {nominal:g} does not match {self.settings.sample_rate}")
            self._exchange([("/d_recv", definition) for definition in self._definitions])
            # The exclusive-server contract reserves these resources before open().
            self._owns_group = True
            self._exchange([("/g_new", 1000, 0, 0), ("/s_new", "dinad_effects", 1001, 1, 1000)])
            self._wall_offset = time.time() - time.monotonic()
        except BaseException:
            self.close(())
            raise

    def send(self, deadline, releases, starts) -> None:
        if time.monotonic() - deadline > .25:
            raise RuntimeError("live transport missed its scheduling deadline")
        commands = [("/n_set", node, "gate", 0) for node in releases]
        commands.extend(voice_arguments(event, self.settings, node) for node, event in starts)
        self._exchange(commands, deadline + self._wall_offset)

    def close(self, active) -> None:
        try:
            if self._owns_group:
                try:
                    if active:
                        self._exchange([("/n_set", node, "gate", 0) for node in active])
                    # Previously released voices can still be in their envelopes.
                    time.sleep(.42)
                except (OSError, RuntimeError):
                    # A partial batch may already have released some voices.
                    # Proceed with group cleanup even when gating fails.
                    pass
                finally:
                    try:
                        self._exchange([("/n_free", 1000)])
                    except (OSError, RuntimeError):
                        # A lost acknowledgement can leave the original stream
                        # unusable. Retry group cleanup over a fresh stream.
                        assert self._connection is not None
                        self._connection.close()
                        self._connection = self._connect()
                        self._exchange([("/n_free", 1000)])
        finally:
            self._owns_group = False
            if self._connection is not None:
                self._connection.close()
                self._connection = None
