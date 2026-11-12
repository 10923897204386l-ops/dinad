"""Bounded command delivery to the transport thread."""

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable

from dinad.config import Profile, Project
from dinad.editor import Document, EditorSession


@dataclass(frozen=True)
class Result:
    running: bool
    project: Project
    profile: Profile
    active_seed: int | None
    documents: dict[str, Document]
    message: str
    error: bool = False
    location: tuple[int, int] | None = None
    response: bool = False
    replaying: bool = False
    can_save: bool = False
    diagnostics: str = ""


class PanelWorker:
    def __init__(self, session: EditorSession):
        self.session = session
        self.commands: Queue[Callable[[EditorSession], str]] = Queue(maxsize=1)
        self.results: Queue[Result] = Queue(maxsize=4)
        self.closing = Event()
        self.initial = self._snapshot("Ready")
        self.thread = Thread(target=self._run, name="dinad-transport")

    def _snapshot(self, message: str, *, error=False, location=None, response=False) -> Result:
        transport = self.session.transport
        playback = self.session.playback
        replaying = playback is not None and playback.running
        output = playback.output if playback is not None else transport.output
        return Result(transport.running or replaying, transport.session.project, transport.session.profile, transport.active_seed,
                      dict(self.session.documents),
                      message, error, location, response, replaying,
                      bool(transport.events and transport.events[0].time < transport.stop_time)
                      and not transport.running, getattr(output, "diagnostics", ""))

    def _run(self) -> None:
        try:
            while not self.closing.is_set():
                playback = self.session.playback
                replaying = playback is not None and playback.running
                transport = playback if replaying else self.session.transport
                timeout = .05
                if transport.running:
                    remaining = transport.deadline - transport.lead - transport.clock()
                    if remaining <= 0:
                        try:
                            previous_seed = getattr(transport, "active_seed", None)
                            transport.advance()
                            if not transport.running:
                                self.results.put_nowait(self._snapshot("Replay finished"))
                            elif previous_seed != getattr(transport, "active_seed", None):
                                self.results.put_nowait(self._snapshot(f"Seed {transport.active_seed} active"))
                        except (ValueError, OSError, RuntimeError, OverflowError) as exc:
                            self.results.put_nowait(self._snapshot(str(exc), error=True))
                        continue
                    timeout = min(timeout, remaining)
                try:
                    command = self.commands.get(timeout=timeout)
                except Empty:
                    continue
                try:
                    message = command(self.session)
                    result = self._snapshot(message, response=True)
                except (ValueError, OSError, RuntimeError, OverflowError) as exc:
                    result = self._snapshot(str(exc), error=True, location=getattr(exc, "location", None),
                                            response=True)
                self.results.put_nowait(result)
        finally:
            try:
                if self.session.playback is not None:
                    self.session.playback.stop()
                self.session.transport.stop()
            except (OSError, RuntimeError) as exc:
                self.results.put_nowait(self._snapshot(str(exc), error=True))
