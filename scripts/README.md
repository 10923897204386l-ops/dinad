# Development scripts

`python scripts/fedora_audio_check.py --output local/audio-check.json` captures a
read-only Fedora audio inventory. Create the parent directory first. It does not
start playback, record audio, change routing or install packages. Existing output
files are preserved. Use `--help` for options and [M0.5](../docs/m0-5.md) for setup,
exit codes and the render/listening/live-validation procedure.

Reports are machine-specific local evidence; keep them outside Git. The collector
uses Python's standard library and runs without SuperCollider or audio hardware,
reporting unavailable probes explicitly.
