"""DINAD file editing and transport coordination, independent of Qt."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import tomllib
from typing import Callable

from dinad.config import Profile, Project, load_profile, load_project
from dinad.registry import ModuleConfig, load_module_config
from dinad.transport import Transport


LOADERS = {"project": load_project, "profile": load_profile, "modules": load_module_config}


@dataclass(frozen=True)
class Document:
    kind: str
    path: Path
    content: bytes

    @classmethod
    def read(cls, kind: str, path: Path) -> "Document":
        path = path.resolve(strict=True)
        content = path.read_bytes()
        content.decode("utf-8")
        return cls(kind, path, content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8").replace("\r\n", "\n")


class EditError(ValueError):
    def __init__(self, message: str, location: tuple[int, int] | None = None):
        super().__init__(message)
        self.location = location


def _location(error: BaseException, text: str) -> tuple[int, int] | None:
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, tomllib.TOMLDecodeError):
        if hasattr(cause, "lineno"):
            return cause.lineno, cause.colno
        match = re.search(r"line (\d+), column (\d+)", str(cause))
        if match:
            return int(match[1]), int(match[2])
        if "end of document" in str(cause):
            lines = text.split("\n")
            return len(lines), len(lines[-1]) + 1
    # Schema validators report field names. Point at a unique matching key.
    message = str(error)
    matches = []
    for number, line in enumerate(text.splitlines(), 1):
        key = re.match(r'^\s*(?:"([^"]+)"|([\w-]+))\s*=', line)
        if key:
            name = key[1] or key[2]
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", message):
                matches.append((number, len(line) - len(line.lstrip()) + 1))
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class StagedEdit:
    original: Document
    replacement: Document
    temporary: Path
    candidate: Project | Profile | ModuleConfig

    def commit(self) -> None:
        path = self.original.path
        if path.is_symlink() or path.read_bytes() != self.original.content:
            raise EditError("File changed on disk. Reload before saving.")
        os.replace(self.temporary, path)

    def discard(self) -> None:
        self.temporary.unlink(missing_ok=True)


def stage_edit(document: Document, text: str) -> StagedEdit:
    """Write and validate a sibling temporary file on the file I/O worker."""
    if b"\r\n" in document.content and b"\n" not in document.content.replace(b"\r\n", b""):
        content = text.replace("\n", "\r\n").encode("utf-8")
    else:
        content = text.encode("utf-8")
    temporary = None
    try:
        with NamedTemporaryFile(dir=document.path.parent, prefix=".dinad-edit-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), document.path.stat().st_mode & 0o777)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        candidate = LOADERS[document.kind](temporary)
        replacement = Document(document.kind, document.path, content)
        return StagedEdit(document, replacement, temporary, candidate)
    except BaseException as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            message = str(exc).removeprefix(f"{temporary}: ")
            raise EditError(message, _location(exc, text)) from exc
        raise


class EditorSession:
    """Owns the live transport and publishes config only after the file commit."""

    def __init__(self, documents: dict[str, Document],
                 factory: Callable[[Project, Profile, ModuleConfig | None], Transport]):
        self.documents = documents
        self.factory = factory
        loaded = {kind: LOADERS[kind](doc.path) for kind, doc in documents.items()}
        if any(doc.path.read_bytes() != doc.content for doc in documents.values()):
            raise EditError("Configuration changed while opening the editor. Reopen it to load the latest version.")
        self.modules = loaded.get("modules")
        self.transport = factory(loaded["project"], loaded.get("profile", Profile()), self.modules)

    @property
    def live_config(self) -> tuple:
        return (self.modules.operations.get("live"), self.modules.bindings) if self.modules else (None, ())

    def apply(self, edit: StagedEdit) -> str:
        candidate = edit.candidate
        transport = self.transport
        replacement = None
        publish = None
        try:
            if self.documents.get(edit.original.kind) != edit.original:
                raise EditError("Editor snapshot is stale. Reload before saving.")
            if isinstance(candidate, ModuleConfig):
                if (candidate.operations.get("live"), candidate.bindings) != self.live_config:
                    if transport.running:
                        raise EditError("Live provider, settings or bindings changed. Stop playback, then save again to rebuild.")
                    replacement = self.factory(transport.session.project, transport.session.profile, candidate)
            else:
                project = candidate if isinstance(candidate, Project) else transport.session.project
                profile = candidate if isinstance(candidate, Profile) else transport.session.profile
                if (project, profile) != (transport.session.project, transport.session.profile):
                    try:
                        publish = transport.prepare_edit(project, profile)
                    except ValueError as exc:
                        error = EditError(f"composition.anchor: {exc}")
                        raise EditError(str(error), _location(error, edit.replacement.text)) from exc
            edit.commit()
            if publish is not None:
                publish()
            if replacement is not None:
                self.transport = replacement
            if isinstance(candidate, ModuleConfig):
                self.modules = candidate
            self.documents[edit.original.kind] = edit.replacement
            return "Saved and applied" if replacement is None else "Saved. Start to use the rebuilt audio configuration."
        finally:
            edit.discard()
