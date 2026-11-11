"""DINAD application boundary for built-in adapters; no plugin discovery or GUIs."""

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from dinad.engine import MockBackend, replay
from dinad.events import Event
from dinad.modules import Capability

if TYPE_CHECKING:
    from dinad.supercollider import SuperColliderBackend


def replay_history(events: tuple[Event, ...]) -> tuple[int, str]:
    with MockBackend() as backend:
        backend.descriptor.require(Capability.STAGED_NOTES)
        replay(events, backend)
        return len(backend.events), backend.version


@contextmanager
def _supercollider(events: tuple[Event, ...], sample_rate: int, tail: float) -> Iterator["SuperColliderBackend"]:
    # Explicit built-in selection preserves M0. Registry/config selection is M1.2.
    from dinad.supercollider import Settings, SuperColliderBackend

    with SuperColliderBackend(Settings(sample_rate=sample_rate, tail=tail)) as backend:
        replay(events, backend)
        yield backend


def render_history(events: tuple[Event, ...], output: Path, *, sample_rate: int = 48000,
                   tail: float = 2, sclang: str = "sclang", scsynth: str = "scsynth",
                   library: Path | None = None, plugins: Path | None = None,
                   timeout: float = 60) -> dict:
    with _supercollider(events, sample_rate, tail) as backend:
        backend.descriptor.require(Capability.OFFLINE_RENDER)
        return backend.render(output, sclang=sclang, scsynth=scsynth, library=library,
                              plugins=plugins, timeout=timeout)


def play_history(events: tuple[Event, ...], output: Path, *, sample_rate: int = 48000,
                 tail: float = 2, sclang: str = "sclang", library: Path | None = None,
                 timeout: float = 60, host: str = "127.0.0.1", port: int = 57110,
                 lookahead: float = 0.2, exclusive: bool = False) -> dict:
    with _supercollider(events, sample_rate, tail) as backend:
        backend.descriptor.require(Capability.SCHEDULED_PLAYBACK)
        return backend.play(output, sclang=sclang, library=library, timeout=timeout,
                            host=host, port=port, lookahead=lookahead, exclusive=exclusive)
