"""Versioned resolved history. Replay does not depend on generation metadata."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import tempfile

from dinad import __version__
from dinad.engine import Event, MockBackend, replay


SCHEMA_VERSION = 1


def encode(events: tuple[Event, ...], metadata: dict) -> str:
    backend = MockBackend()
    replay(events, backend)
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "dinad_version": __version__,
        "backend": MockBackend.version,
        "python_version": platform.python_version(),
        "metadata": metadata,
        "events": [asdict(event) for event in events],
    }, sort_keys=True, indent=2, allow_nan=False) + "\n"


def decode(text: str) -> tuple[Event, ...]:
    """Validate history before dispatch; retain serialized tie ordering."""
    return decode_take(text)[0]


def decode_take(text: str) -> tuple[tuple[Event, ...], dict]:
    try:
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("recording must be an object")
        version = document.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ValueError("unsupported recording schema_version")
        if not isinstance(document.get("events"), list):
            raise ValueError("recording events must be a list")
        events = tuple(Event(**item) for item in document["events"])
        replay(events, MockBackend())
        metadata = document.get("metadata")
        return events, metadata if isinstance(metadata, dict) else {}
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError(f"invalid recording: {exc}") from exc


def save_take(path: Path, text: str) -> None:
    """Publish a complete new history atomically without replacing prior takes."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".dinad-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
