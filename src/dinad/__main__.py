"""Generate or replay resolved mock histories: python -m dinad --help."""

import argparse
import sys
from pathlib import Path

from dinad.demo import compose
from dinad.engine import MockBackend, replay
from dinad.recording import decode, encode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="emit a deterministic mock recording as JSON")
    demo.add_argument("--seed", type=int, default=0)
    demo.add_argument("--anchor", type=float, default=48)
    demo.add_argument("--tempo", type=float, default=120)
    playback = commands.add_parser("replay", help="validate and replay JSON through the mock backend")
    playback.add_argument("recording", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "demo":
            backend = MockBackend()
            replay(compose(args.seed, args.anchor, args.tempo), backend)
            sys.stdout.write(encode(backend.events, {
                "generator": "m0-demo-v1", "seed": args.seed,
                "anchor": args.anchor, "tempo_bpm": args.tempo,
            }))
        else:
            backend = MockBackend()
            replay(decode(args.recording.read_text()), backend)
            print(f"Replayed {len(backend.events)} resolved events through {backend.version}.")
    except (ValueError, OSError, OverflowError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
