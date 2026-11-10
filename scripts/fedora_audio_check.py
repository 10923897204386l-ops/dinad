#!/usr/bin/env python3
"""Collect local audio diagnostics without starting audio or changing settings."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess


def probe(arguments, timeout):
    executable = shutil.which(arguments[0])
    if executable is None:
        return {"status": "missing", "command": arguments}
    command = [executable, *arguments[1:]]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                errors="replace", timeout=timeout)
        return {"status": "ok" if result.returncode == 0 else "failed",
                "command": command, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "command": command}
    except OSError as exc:
        return {"status": "failed", "command": command, "error": str(exc)}


def collect(timeout):
    commands = {
        "sclang": ["sclang", "-v"], "scsynth": ["scsynth", "-v"],
        "pulse_server": ["pactl", "info"],
        "sinks": ["pactl", "list", "sinks"],
        "sources": ["pactl", "list", "sources"],
        "routing": ["pw-link", "-l"],
        "clock": ["pw-metadata", "-n", "settings"],
        "graph": ["pw-dump"],
    }
    checks = {name: probe(command, timeout) for name, command in commands.items()}
    players = {tool: shutil.which(tool) for tool in ("pw-play", "pw-top", "pw-jack")}
    ready = all(check["status"] == "ok" for check in checks.values()) and all(players.values())
    return {"schema": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "status": "ready_for_manual_checks" if ready else "incomplete",
            "checks": checks, "tools": players,
            "limits": "Read-only inventory; does not establish sound, routing correctness, dropouts or latency."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new local JSON file; never overwrites")
    parser.add_argument("--timeout", type=float, default=5, help="seconds per probe (default 5)")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite positive number")
    if args.output is not None and args.output.exists():
        parser.error("output already exists")
    report = collect(args.timeout)
    text = json.dumps(report, indent=2) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        try:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
        except OSError as exc:
            parser.error(str(exc))
    return 0 if report["status"] == "ready_for_manual_checks" else 1


if __name__ == "__main__":
    raise SystemExit(main())
