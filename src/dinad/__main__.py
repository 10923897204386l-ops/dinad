"""Headless composition, validated project files and keyboard command sessions."""

import argparse
import codecs
from dataclasses import asdict, replace
import json
import os
import queue
from pathlib import Path
import shlex
import sys
import tempfile
import threading
import time

from dinad.config import PARAMETERS, Profile, Project, load_profile, load_project
from dinad.controls import Session
from dinad.execution import play_history, render_history, replay_history
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
        except (ValueError, OSError, OverflowError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def _save_new(path: Path, text: str) -> None:
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


def _capture(performance, output: Path) -> int:
    """Timestamp complete input lines; a daemon reader permits automatic finish."""
    if output.exists() or not output.parent.is_dir():
        raise ValueError("capture requires a new output in an existing directory")
    incoming = queue.Queue()
    origin = time.monotonic()

    def read():
        try:
            decoder = codecs.getincrementaldecoder(sys.stdin.encoding or "utf-8")()
            pending = ""
            while True:
                # Avoid a daemon holding TextIOWrapper's lock at interpreter exit.
                chunk = os.read(sys.stdin.fileno(), 4096)
                pending += decoder.decode(chunk, final=not chunk)
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    incoming.put((time.monotonic() - origin, line + "\n"))
                if not chunk:
                    if pending:
                        incoming.put((time.monotonic() - origin, pending))
                    incoming.put((time.monotonic() - origin, ""))
                    return
        except (OSError, UnicodeError, ValueError) as exc:
            incoming.put((time.monotonic() - origin, exc))

    threading.Thread(target=read, daemon=True).start()
    print("Capturing 16 beats. Enter set/adjust/key/undo/inspect; quit or EOF stops. "
          "Save then render/play the captured history.", file=sys.stderr)
    failed = False
    try:
        while True:
            remaining = performance.end_time - (time.monotonic() - origin)
            try:
                seconds, line = incoming.get(timeout=max(0, min(.05, remaining)))
            except queue.Empty:
                if remaining <= 0:
                    stop = performance.end_time
                    break
                continue
            if seconds >= performance.end_time:
                stop = performance.end_time
                break
            if isinstance(line, Exception):
                raise OSError(f"capture input failed: {line}")
            if not line:
                stop = seconds
                break
            if not line.strip():
                continue
            try:
                result = performance.execute(line, seconds)
                if result == "quit":
                    stop = seconds
                    break
                print(result, flush=True)
            except (ValueError, OverflowError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                failed = True
    except KeyboardInterrupt:
        stop = min(time.monotonic() - origin, performance.end_time)
    _save_new(output, performance.recording(stop))
    print(f"Saved timed performance to {output}", file=sys.stderr)
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
    for name in ("perform", "capture"):
        timed = commands.add_parser(name, help="compile a timed TOML performance" if name == "perform"
                                   else "capture timestamped keyboard controls, then save resolved notes")
        if name == "perform":
            timed.add_argument("timeline", type=Path)
        timed.add_argument("output", type=Path, help="new resolved recording; never overwrites")
        timed.add_argument("--project", type=Path)
        timed.add_argument("--profile", type=Path)
    for name in ("render", "play"):
        audio = commands.add_parser(name, help="SuperCollider offline WAV render" if name == "render"
                                    else "submit to an exclusive running SuperCollider server")
        audio.add_argument("recording", type=Path)
        audio.add_argument("output", type=Path, help="new WAV path" if name == "render" else "new log directory")
        audio.add_argument("--sample-rate", type=int, default=48000)
        audio.add_argument("--tail", type=float, default=2)
        audio.add_argument("--sclang", default="sclang")
        audio.add_argument("--library", type=Path, help="optional sclang library YAML")
        audio.add_argument("--timeout", type=float, default=60)
        if name == "render":
            audio.add_argument("--scsynth", default="scsynth")
            audio.add_argument("--plugins", type=Path)
        else:
            audio.add_argument("--exclusive-server", action="store_true", required=True,
                               help="confirm node group 1000 and bus 16 are reserved for this run")
            audio.add_argument("--host", default="127.0.0.1")
            audio.add_argument("--port", type=int, default=57110)
            audio.add_argument("--lookahead", type=float, default=0.2)
    args = parser.parse_args()
    try:
        if args.command in ("perform", "capture"):
            from dinad.performance import Performance, load_performance

            project = load_project(args.project) if args.project else Project()
            profile = load_profile(args.profile) if args.profile else Profile()
            if args.command == "capture":
                return _capture(Performance(project, profile), args.output)
            performance = load_performance(args.timeline, project, profile)
            _save_new(args.output, performance.recording())
            print(f"Saved timed performance to {args.output}")
        elif args.command in ("render", "play"):
            events = decode(args.recording.read_text(encoding="utf-8"))
            common = dict(sample_rate=args.sample_rate, tail=args.tail, sclang=args.sclang,
                          library=args.library, timeout=args.timeout)
            if args.command == "render":
                result = render_history(events, args.output, scsynth=args.scsynth,
                                        plugins=args.plugins, **common)
            else:
                result = play_history(events, args.output, host=args.host, port=args.port,
                                      lookahead=args.lookahead, exclusive=args.exclusive_server, **common)
            print(json.dumps(result, indent=2))
        elif args.command == "parameters":
            print(json.dumps({key: asdict(value) for key, value in PARAMETERS.items()}, indent=2))
        elif args.command == "replay":
            count, version = replay_history(decode(args.recording.read_text(encoding="utf-8")))
            print(f"Replayed {count} resolved events through {version}.")
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
    except (ValueError, OSError, OverflowError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
