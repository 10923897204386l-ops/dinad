"""DINAD application boundary for registered headless adapters."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import time

from dinad.config import Profile, Project

from dinad.engine import replay
from dinad.events import Event
from dinad.modules import Capability, LiveProvider, NoteAdapter, OfflineRenderer, ParameterValue, ScheduledPlayer
from dinad.registry import BUILTINS, ModuleConfig, ModuleSelection
from dinad.lv2_epiano import EPianoBackend, EPianoLive
from dinad.objects import _finite
from dinad.recording import decode_take
from dinad.transport import TakePlayback, Transport


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
                   timeout: float = 60, module: ModuleSelection | None = None,
                   module_state: dict | None = None) -> dict:
    overrides = {key: value for key, value in (("sample_rate", sample_rate), ("tail", tail))
                 if value is not None}
    with _staged(events, module, "dinad.supercollider", Capability.OFFLINE_RENDER, overrides) as adapter:
        assert isinstance(adapter, OfflineRenderer)
        options = {}
        if module_state is not None:
            adapter.descriptor.require(Capability.STATE_RESTORE)
            options["module_state"] = module_state
        return adapter.render(output, sclang=sclang, scsynth=scsynth, library=library,
                              plugins=plugins, timeout=timeout, **options)


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


def live_transport(project: Project, profile: Profile, *, backend: str | None,
                   exclusive: bool, port: int, sample_rate: int | None,
                   sclang: str, library: Path | None, module: ModuleSelection | None = None,
                   module_config: ModuleConfig | None = None) -> Transport:
    if module_config is not None:
        module = module_config.operations.get("live")
    if module is not None and backend is not None:
        raise ValueError("select the live backend in TOML or with --backend")
    selection = module or ModuleSelection(BUILTINS.require(f"dinad.{backend or 'mock'}"), {})
    selection.registration.descriptor.require(Capability.CONTINUOUS_TRANSPORT)
    overrides = {"sample_rate": sample_rate} if sample_rate is not None else {}
    unsupported = (selection.settings.keys() | overrides.keys()) - selection.registration.live_settings
    if unsupported:
        raise ValueError(f"unsupported live settings: {', '.join(sorted(unsupported))}")
    adapter = selection.create(**overrides)
    try:
        if not isinstance(adapter, LiveProvider):
            raise ValueError("registered adapter does not implement continuous transport")
        output = adapter.live_output(exclusive=exclusive, port=port, sclang=sclang, library=library)
    finally:
        adapter.close()
    transport = Transport(project, profile, output, time.monotonic, lead=output.lead)
    if isinstance(output, EPianoLive):
        transport.output_metadata = output.metadata
    if module_config is not None and module_config.bindings:
        if not isinstance(output, EPianoLive):
            raise ValueError("selected live provider cannot apply parameter bindings")
        if any(target.module_id != selection.registration.descriptor.identifier
               for binding in module_config.bindings for target in binding.targets):
            raise ValueError("all live binding targets must belong to the selected provider")

        def configure(project: Project) -> None:
            output.set_controls({update.native_id: update.value for update in module_config.resolve(project)})

        transport.before_onset = configure
    return transport


def play_take(transport: Transport, take: tuple[tuple[Event, ...], dict]) -> TakePlayback:
    if transport.running:
        raise ValueError("stop the performance before replaying a take")
    output = transport.output
    if not isinstance(output, EPianoLive):
        raise ValueError("select the DINAD ePiano live module to replay a take")
    events, metadata = take
    if len(events) > 100000:
        raise ValueError("live take exceeds the 100000-note limit")
    state = metadata.get("module_state")
    if not events or not isinstance(state, dict):
        raise ValueError("select a nonempty DINAD ePiano take with recorded module state")
    last_release = max(event.time + event.duration for event in events)
    stop = state.get("stop_seconds", last_release)
    _finite(stop, "take stop time")
    if not last_release - 1e-8 <= stop <= 3600:
        raise ValueError("recorded ePiano stop time is invalid")
    controls, automation = EPianoBackend.restore_controls(state, stop)
    playback = EPianoLive({**output.initial, **controls})
    return TakePlayback(playback, transport.clock, events, max(stop, last_release) + .25,
                        automation, playback.set_controls)


def load_take(path: Path) -> tuple[tuple[Event, ...], dict]:
    return decode_take(path.expanduser().read_text(encoding="utf-8"))
