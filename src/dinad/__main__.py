"""Headless composition, validated project files and keyboard command sessions."""

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

from dinad.config import PARAMETERS, Profile, Project, load_profile, load_project
from dinad.controls import Session
from dinad.engine import MockBackend, replay
from dinad.recording import decode


def _save(path: Path, text: str) -> None:
    """Replace a history only after complete generation and successful writing."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".dinad-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _session(session: Session) -> int:
    failed = False
    while True:
        if sys.stdin.isatty():
            print("dinad> ", file=sys.stderr, end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            break
        if not line.strip():
            continue
        try:
            tokens = shlex.split(line)
            if tokens and tokens[0] == "save":
                if len(tokens) != 2:
                    raise ValueError("expected: save PATH")
                _save(Path(tokens[1]), session.recording())
                print(f"Saved resolved history to {tokens[1]}")
                continue
            result = session.execute(line)
            if result == "quit":
                break
            print(result, end="" if result.endswith("\n") else "\n")
        except (ValueError, OSError, OverflowError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="emit a deterministic resolved mock recording")
    demo.add_argument("--project", type=Path)
    demo.add_argument("--seed", type=int)
    demo.add_argument("--anchor", type=float)
    demo.add_argument("--tempo", type=float)
    playback = commands.add_parser("replay", help="replay resolved JSON through the mock backend")
    playback.add_argument("recording", type=Path)
    validate = commands.add_parser("validate", help="validate project and optional control profile")
    validate.add_argument("project", type=Path)
    validate.add_argument("--profile", type=Path)
    interactive = commands.add_parser("session", help="run a mock command session from keyboard or stdin")
    interactive.add_argument("--project", type=Path)
    interactive.add_argument("--profile", type=Path)
    commands.add_parser("parameters", help="list stable parameter IDs and semantics")
    args = parser.parse_args()
    try:
        if args.command == "parameters":
            print(json.dumps({key: asdict(value) for key, value in PARAMETERS.items()}, indent=2))
        elif args.command == "replay":
            backend = MockBackend()
            replay(decode(args.recording.read_text(encoding="utf-8")), backend)
            print(f"Replayed {len(backend.events)} resolved events through {backend.version}.")
        else:
            project = load_project(args.project) if args.project else Project()
            if args.command == "demo":
                overrides = {name: getattr(args, name) for name in ("seed", "anchor", "tempo")
                             if getattr(args, name) is not None}
                project = replace(project, **overrides)
                sys.stdout.write(Session(project).recording())
            else:
                profile = load_profile(args.profile) if args.profile else Profile()
                if args.command == "validate":
                    print("Project and control profile valid (schema 1).")
                else:
                    return _session(Session(project, profile))
    except (ValueError, OSError, OverflowError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
