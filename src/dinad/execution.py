"""DINAD application boundary for registered headless adapters."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dinad.engine import replay
from dinad.events import Event
from dinad.modules import Capability, NoteAdapter, OfflineRenderer, ParameterValue, ScheduledPlayer
from dinad.registry import BUILTINS, ModuleSelection


@contextmanager
def _staged(events: tuple[Event, ...], selection: ModuleSelection | None,
            default_id: str, capability: Capability,
            overrides: dict[str, ParameterValue]) -> Iterator[NoteAdapter]:
    selection = selection or ModuleSelection(BUILTINS.require(default_id), {})
    selection.registration.descriptor.require(capability)
    selection.registration.descriptor.require(Capability.STAGED_NOTES)
    adapter = selection.create(**overrides)
    try:
        if not isinstance(adapter, NoteAdapter):
            raise ValueError("registered adapter does not implement note reception")
        if capability == Capability.OFFLINE_RENDER and not isinstance(adapter, OfflineRenderer):
            raise ValueError("registered adapter does not implement offline rendering")
        if capability == Capability.SCHEDULED_PLAYBACK and not isinstance(adapter, ScheduledPlayer):
            raise ValueError("registered adapter does not implement scheduled playback")
        adapter.prepare()
        replay(events, adapter)
        yield adapter
    finally:
        adapter.close()


def replay_history(events: tuple[Event, ...], *, module: ModuleSelection | None = None) -> tuple[int, str]:
    with _staged(events, module, "dinad.mock", Capability.STAGED_NOTES, {}) as adapter:
        return len(events), adapter.descriptor.version


def render_history(events: tuple[Event, ...], output: Path, *, sample_rate: int | None = None,
                   tail: float | None = None, sclang: str = "sclang", scsynth: str = "scsynth",
                   library: Path | None = None, plugins: Path | None = None,
                   timeout: float = 60, module: ModuleSelection | None = None) -> dict:
    overrides = {key: value for key, value in (("sample_rate", sample_rate), ("tail", tail))
                 if value is not None}
    with _staged(events, module, "dinad.supercollider", Capability.OFFLINE_RENDER, overrides) as adapter:
        assert isinstance(adapter, OfflineRenderer)
        return adapter.render(output, sclang=sclang, scsynth=scsynth, library=library,
                              plugins=plugins, timeout=timeout)


def play_history(events: tuple[Event, ...], output: Path, *, sample_rate: int | None = None,
                 tail: float | None = None, sclang: str = "sclang", library: Path | None = None,
                 timeout: float = 60, host: str = "127.0.0.1", port: int = 57110,
                 lookahead: float = 0.2, exclusive: bool = False,
                 module: ModuleSelection | None = None) -> dict:
    overrides = {key: value for key, value in (("sample_rate", sample_rate), ("tail", tail))
                 if value is not None}
    with _staged(events, module, "dinad.supercollider", Capability.SCHEDULED_PLAYBACK, overrides) as adapter:
        assert isinstance(adapter, ScheduledPlayer)
        return adapter.play(output, sclang=sclang, library=library, timeout=timeout,
                            host=host, port=port, lookahead=lookahead, exclusive=exclusive)
