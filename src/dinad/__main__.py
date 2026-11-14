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
from dinad.performance import load_controller
from dinad.execution import live_transport, play_history, render_history, replay_history
from dinad.recording import decode, save_take
from dinad.registry import BUILTINS, load_module_config


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


def _read_input(origin: float = 0.) -> queue.Queue:
    incoming = queue.Queue()

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
    return incoming


def _capture(performance, output: Path) -> int:
    """Timestamp complete input lines; a daemon reader permits automatic finish."""
    if output.exists() or not output.parent.is_dir():
        raise ValueError("capture requires a new output in an existing directory")
    origin = time.monotonic()
    incoming = _read_input(origin)
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
    save_take(output, performance.recording(stop))
    print(f"Saved timed performance to {output}", file=sys.stderr)
    return 1 if failed else 0


def _live(transport) -> int:
    incoming = _read_input()
    print("Live session ready. Enter start, stop, replace PATH, save PATH, "
          "set/adjust/key/undo/inspect, or quit.", flush=True)
    failed = False
    try:
        while True:
            try:
                if transport.running and time.monotonic() >= transport.deadline - transport.lead:
                    transport.advance()
                    continue
                timeout = max(0., transport.deadline - transport.lead - time.monotonic()) if transport.running else None
                try:
                    received, line = incoming.get(timeout=timeout)
                except queue.Empty:
                    continue
                if isinstance(line, Exception):
                    raise SystemExit(f"live input failed: {line}")
                if not line:
                    break
                tokens = shlex.split(line)
                if not tokens:
                    continue
                if tokens == ["start"]:
                    transport.start()
                    print("Started", flush=True)
                elif tokens == ["stop"]:
                    transport.stop()
                    print("Stopped", flush=True)
                elif tokens[0] in ("replace", "save"):
                    if len(tokens) != 2:
                        raise ValueError(f"expected {tokens[0]} PATH")
                    path = Path(tokens[1])
                    if tokens[0] == "replace":
                        transport.replace_project(load_project(path))
                        print("Project replaced", flush=True)
                    else:
                        save_take(path, transport.recording())
                        print("Take saved", flush=True)
                else:
                    result = transport.control(line, received)
                    if result == "quit":
                        break
                    print(result, flush=True)
            except (ValueError, OSError, OverflowError, RuntimeError) as exc:
                print(f"error: {exc}", file=sys.stderr, flush=True)
                failed = True
    except KeyboardInterrupt:
        pass
    finally:
        transport.stop()
    return int(failed)


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
    playback.add_argument("--modules", type=Path, help="DINAD module configuration")
    validate = commands.add_parser("validate", help="validate project and optional control profile")
    validate.add_argument("project", type=Path)
    validate.add_argument("--profile", type=Path)
    interactive = commands.add_parser("session", help="run a mock command session from keyboard or stdin")
    interactive.add_argument("--project", type=Path)
    interactive.add_argument("--profile", type=Path)
    commands.add_parser("parameters", help="list stable parameter IDs and semantics")
    commands.add_parser("modules", help="list built-in headless module descriptions")
    module_check = commands.add_parser("validate-modules", help="validate configuration and preview mappings")
    module_check.add_argument("config", type=Path)
    module_check.add_argument("--project", type=Path)
    for name in ("perform", "capture"):
        timed = commands.add_parser(name, help="compile a timed TOML performance" if name == "perform"
                                   else "capture timestamped keyboard controls, then save resolved notes")
        if name == "perform":
            timed.add_argument("timeline", type=Path)
        timed.add_argument("output", type=Path, help="new resolved recording; never overwrites")
        timed.add_argument("--project", type=Path)
        timed.add_argument("--profile", type=Path)
    live = commands.add_parser("live", help="loop the composition with interactive timed controls")
    live.add_argument("--project", type=Path)
    live.add_argument("--profile", type=Path)
    live.add_argument("--backend", choices=("mock", "supercollider", "lv2_epiano"))
    live.add_argument("--modules", type=Path, help="DINAD module configuration")
    live.add_argument("--exclusive-server", action="store_true")
    live.add_argument("--port", type=int, default=57110, help="local SuperCollider TCP port")
    live.add_argument("--sample-rate", type=int)
    live.add_argument("--sclang", default="sclang")
    live.add_argument("--library", type=Path)
    live.add_argument("--controller", type=Path, help="beat-based controller TOML")
    gui = commands.add_parser("gui", help="open the DINAD Qt Widgets performance panel")
    gui.add_argument("--project", type=Path, required=True)
    gui.add_argument("--profile", type=Path)
    gui.add_argument("--modules", type=Path)
    gui.add_argument("--backend", choices=("mock", "supercollider", "lv2_epiano"))
    gui.add_argument("--exclusive-server", action="store_true")
    gui.add_argument("--layered", action="store_true", help="route drone and pad to a dedicated SuperCollider server")
    gui.add_argument("--port", type=int, default=57110)
    gui.add_argument("--sample-rate", type=int)
    gui.add_argument("--sclang", default="sclang")
    gui.add_argument("--library", type=Path)
    gui.add_argument("--controller", type=Path, help="beat-based controller TOML")
    for name in ("render", "play"):
        audio = commands.add_parser(name, help="SuperCollider offline WAV render" if name == "render"
                                    else "submit to an exclusive running SuperCollider server")
        audio.add_argument("recording", type=Path)
        audio.add_argument("output", type=Path, help="new WAV path" if name == "render" else "new log directory")
        audio.add_argument("--modules", type=Path, help="DINAD module configuration")
        audio.add_argument("--sample-rate", type=int)
        audio.add_argument("--tail", type=float)
        audio.add_argument("--sclang", default="sclang")
        audio.add_argument("--library", type=Path, help="optional sclang library YAML")
        audio.add_argument("--timeout", type=float, default=60)
        if name == "render":
            audio.add_argument("--scsynth", default="scsynth")
            audio.add_argument("--plugins", type=Path)
            audio.add_argument("--restore-module-state", action="store_true",
                               help="restore saved DINAD module controls and automation")
        else:
            audio.add_argument("--exclusive-server", action="store_true", required=True,
                               help="confirm node group 1000 and bus 16 are reserved for this run")
            audio.add_argument("--host", default="127.0.0.1")
            audio.add_argument("--port", type=int, default=57110)
            audio.add_argument("--lookahead", type=float, default=0.2)
    args = parser.parse_args()
    try:
        if args.command == "gui":
            try:
                from dinad.gui import launch
            except ModuleNotFoundError as exc:
                if exc.name != "PySide6":
                    raise
                raise ValueError("Qt GUI requires the optional dependency. Install with pip install '.[gui]'.") from exc
            return launch(args)
        module_config = load_module_config(args.modules) if getattr(args, "modules", None) else None
        module = module_config.operations.get(args.command) if module_config else None
        if args.command == "live":
            project = load_project(args.project) if args.project else Project()
            profile = load_profile(args.profile) if args.profile else Profile()
            transport = live_transport(project, profile, backend=args.backend,
                                        exclusive=args.exclusive_server, port=args.port,
                                        sample_rate=args.sample_rate, sclang=args.sclang,
                                        library=args.library, module_config=module_config)
            if args.controller:
                transport.controller = load_controller(args.controller)
            return _live(transport)
        elif args.command == "modules":
            print(json.dumps([asdict(item) for item in BUILTINS.descriptors], default=sorted, indent=2))
        elif args.command == "validate-modules":
            config = load_module_config(args.config)
            project = load_project(args.project) if args.project else Project()
            print(json.dumps({"operations": sorted(config.operations),
                              "updates": [asdict(update) for update in config.resolve(project)]}, indent=2))
        elif args.command in ("perform", "capture"):
            from dinad.performance import Performance, load_performance

            project = load_project(args.project) if args.project else Project()
            profile = load_profile(args.profile) if args.profile else Profile()
            if args.command == "capture":
                return _capture(Performance(project, profile), args.output)
            performance = load_performance(args.timeline, project, profile)
            save_take(args.output, performance.recording())
            print(f"Saved timed performance to {args.output}")
        elif args.command in ("render", "play"):
            recording = args.recording.read_text(encoding="utf-8")
            events = decode(recording)
            common = dict(sample_rate=args.sample_rate, tail=args.tail, sclang=args.sclang,
                          library=args.library, timeout=args.timeout, module=module)
            if args.command == "render":
                state = None
                if args.restore_module_state:
                    metadata = json.loads(recording).get("metadata")
                    state = metadata.get("module_state") if isinstance(metadata, dict) else None
                    if not isinstance(state, dict):
                        raise ValueError("recording has no DINAD module state to restore")
                result = render_history(events, args.output, scsynth=args.scsynth,
                                        plugins=args.plugins, module_state=state, **common)
            else:
                result = play_history(events, args.output, host=args.host, port=args.port,
                                      lookahead=args.lookahead, exclusive=args.exclusive_server, **common)
            print(json.dumps(result, indent=2))
        elif args.command == "parameters":
            print(json.dumps({key: asdict(value) for key, value in PARAMETERS.items()}, indent=2))
        elif args.command == "replay":
            count, version = replay_history(decode(args.recording.read_text(encoding="utf-8")), module=module)
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
