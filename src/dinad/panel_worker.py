"""Bounded command delivery to the transport thread."""

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable

from dinad.config import Project
from dinad.editor import Document, EditorSession


@dataclass(frozen=True)
class Result:
    running: bool
    project: Project
    documents: dict[str, Document]
    message: str
    error: bool = False
    location: tuple[int, int] | None = None
    response: bool = False


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
        return Result(transport.running, transport.session.project, dict(self.session.documents),
                      message, error, location, response)

    def _run(self) -> None:
        try:
            while not self.closing.is_set():
                transport = self.session.transport
                timeout = .05
                if transport.running:
                    remaining = transport.deadline - transport.lead - transport.clock()
                    if remaining <= 0:
                        try:
                            transport.advance()
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
                self.session.transport.stop()
            except (OSError, RuntimeError) as exc:
                self.results.put_nowait(self._snapshot(str(exc), error=True))
