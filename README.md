# DINAD — Dinad Is Not A DAW

DINAD is a programmable musical environment combining explicit composition and generative behaviour. Musical objects are reusable, relational, nested and interpreted dynamically; the same structure may become a motif, harmonic progression, arpeggio or large-scale form.

## Status
Architecture concept frozen (2026-11-10). M0 exploratory prototype has **not** been implemented.

## Principles
- Python owns composition, objects, transformations, rules, scheduling intent, configuration and control surfaces.
- Audio DSP runs outside Python's real-time callback. SuperCollider is the provisional primary engine; Csound may be added through an interchangeable backend adapter.
- Absolute and relational pitch/time; shared harmonic contexts; acoustic persistence and controlled dissonance.
- Deterministic seeds, recorded resolved events, versioned state and reproducible renders.
- Fixed modal knob/pad/display interface, keyboard-first, no required piano roll or node-patching GUI.
- Global/project/control/production configuration files, with validated overrides.
- Hierarchical voice, group, track, send/return and master processing; LV2/LADSPA support subject to feasibility testing.
- GitHub origin/main is the only authoritative integration branch.

See [architecture](docs/architecture.md), [M0 plan](docs/m0.md), and [Codex rules](AGENTS.md).

## Development
Python 3.11+ recommended. From repository root:

```sh
python -m pip install -e ".[dev]"
python -m pytest
```

Audio hardware tests belong on Fedora Kinoite; cloud tests must remain headless.
