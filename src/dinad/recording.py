"""Versioned resolved history. Replay does not depend on generation metadata."""

from dataclasses import asdict
import json
import platform

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
        return events
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError(f"invalid recording: {exc}") from exc
