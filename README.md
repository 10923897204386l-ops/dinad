# DINAD — Dinad Is Not A DAW

DINAD is a programmable musical environment combining explicit composition and generative behaviour. Musical objects are reusable, relational, nested and interpreted dynamically; the same structure may become a motif, harmonic progression, arpeggio or large-scale form.

## Status
M0 is integrated. [M1.1 module contracts and adapter foundations](docs/m1-1.md) are implemented; remaining M1 milestones are planned. Architecture concept frozen (2026-11-10). M0.1 has an initial headless object model and note interpreter. M0.2 adds a deterministic scheduler, shared harmonic context, seeded demo and mock recording/replay; see [CLI usage and contracts](docs/m0-2.md). M0.3 adds [validated TOML projects and keyboard command sessions](docs/m0-3.md). M0.4 adds [provisional SuperCollider rendering and live submission](docs/m0-4.md). M0.5 adds [Fedora setup and audio validation](docs/m0-5.md), with real-speaker results and a read-only diagnostic collector. M0.6 adds [timed control capture and performance replay](docs/m0-6.md). See [implementation notes](docs/m0-implementation.md).

## Principles
- Python owns composition, objects, transformations, rules, scheduling intent, configuration and control surfaces.
- Audio DSP runs outside Python's real-time callback. SuperCollider is the provisional primary engine; Csound may be added through an interchangeable backend adapter.
- Absolute and relational pitch/time; shared harmonic contexts; acoustic persistence and controlled dissonance.
- Deterministic seeds, recorded resolved events, versioned state and reproducible renders.
- Fixed modal knob/pad/display interface, keyboard-first, no required piano roll or node-patching GUI.
- Global/project/control/production configuration files, with validated overrides.
- Hierarchical voice, group, track, send/return and master processing; LV2/LADSPA support subject to feasibility testing.
- GitHub origin/main is the only authoritative integration branch.

**M1 architectural rule:** DINAD is the only front end. External instruments and effects operate headlessly, with all configuration and parameter bindings owned by DINAD files and its fixed control surface. Vendor-specific plugin GUIs are never required or embedded.

GUI technology frozen: **PySide6 / Qt 6 Widgets**, one DINAD window with a functional F12 editor in M1.4; see [GUI decision](docs/gui-decision.md).\n\nStart here: [current roadmap](docs/roadmap.md) · [M1 build specification](docs/m1.md) · [architecture](docs/architecture.md) · [Codex rules](AGENTS.md).

M0 reference: [brief](docs/m0.md) · [implementation](docs/m0-implementation.md) · [local validation](docs/m0-local-audio.md). Detailed M0 checkpoint reports remain in `docs/m0-*.md`.

## Development
Python 3.11+ recommended. From repository root:

```sh
python -m pip install -e ".[dev]"
python -m pytest
```

Audio hardware tests belong on Fedora Kinoite; cloud tests must remain headless.
