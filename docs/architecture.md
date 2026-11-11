# DINAD frozen concept architecture — v0.1

Frozen: 2026-11-10. This document records **design intent**, not implemented functionality.

## Purpose
DINAD is not a conventional DAW: it is an object-based musical environment for instant generative play, scripted composition, live performance capture and reproducible offline rendering.

## Musical representation
A musical object is a general-purpose, reusable, nestable structure of elements, references, relationships, timing and metadata. It is **not** hard-coded as a chord progression, motif or section. Meaning is assigned by an interpreter at use time (late binding).
- Absolute pitches (C4 G4 E4), anchor-relative intervals (1 P5 maj3), and previous-event-relative intervals are distinct.
- Timing can be absolute, beat-relative, proportional or relative to another event.
- Definitions are immutable/reusable; instances hold context, transformation state and seeded random state.
- Interpreters produce notes, harmonic contexts, control events or structural transitions.
- Deterministic and generative behaviours transform instances; nesting and multiscale reuse are supported.
- Shared harmonic contexts can control pads, drones and arpeggiators together. Harmonic evaluation must consider sustained notes and effect tails, including spectral roughness and intended tension.

## Composition and reproducibility
One composition engine supports instant play, templates/script editing, recorded improvisation, rule/trigger-based pieces and offline export.
- Distinguish project definitions, running state and resolved timestamped event history.
- Schedule events ahead on an audio-derived timeline; Python must not run in the real-time callback.
- Version project schema, seeds, backend/plugin versions, and relevant render settings.
- Replay of resolved musical events is a minimum reproducibility guarantee; bit-identical audio across platforms is not promised.
- Snapshots must distinguish musical state from complete DSP state; seek/freeze with effects requires checkpoints or replay.
- Live and offline quality modes may differ; exact-render mode should retain settings where feasible.

## Frozen desktop GUI technology (2026-11-11)
DINAD's sole GUI toolkit is **PySide6 / Qt 6 Widgets**, with custom-painted widgets for the static instrument panel and Qt text-editing widgets for the F12 drop-down editor. The single DINAD window is keyboard-first, resizable/full-screen capable, and owns all plugin-facing controls; no plugin GUI or browser UI. The Qt presentation layer remains separate from the headless application model and audio scheduler. See [frozen GUI decision](gui-decision.md).

## Controls and configuration
Fixed virtual instrument: knobs, illuminated pads, small displays, modal colours, keyboard-first Vim-like navigation, command bar and drop-down editable text. No mandatory piano roll or visual patch graph.
- A parameter registry defines stable IDs, units, limits, labels, smoothing and semantics.
- Versioned control profiles map object attributes, actions, derived signals, lights, displays and shortcuts to hardware/virtual controls.
- Profiles may be swapped or overlaid; all control changes resolve into logged semantic actions.
- Global settings: keyboard, audio device/backend, default sample rate/buffer, recording format, output, UI, plugin paths.
- Project overrides: rendering, routing, audio policy and selected profiles. Hard overrides must fail clearly if unavailable.
- Config changes are validated and applied transactionally, with undo where feasible.

## M1 binding rule: fixed DINAD interface, headless modules
**Normative product decision (2026-11-11): DINAD is the sole front end.** The physical/visual control layout remains fixed after design; its contextual mappings and displays may change. Plugins may not open, embed or require their bespoke graphical interfaces. All normal instrument selection, configuration, parameter editing, presets, mappings and automation occur in DINAD through its own human-readable validated config files and controls.

A dedicated modular abstraction layer discovers capabilities and translates resolved DINAD object actions to notes/MIDI, timestamped events, plugin parameters, state and audio routing. A plugin does not interpret DINAD objects itself. Semantic DINAD parameter IDs remain independent of plugin-native identifiers. Opaque state may be stored for recall only as a supplementary asset; GUI-only functionality is explicitly unsupported until represented through DINAD. Third-party plugin hosting must be designed independently of the provisional SuperCollider synthesis adapter, without assuming all plugins expose all parameters.

See [M1 contract](m1.md) and [roadmap](roadmap.md).

## Synthesis and production
Python owns objects, behaviours, rules, orchestration and UI; a native audio engine owns synthesis and DSP. SuperCollider is provisional; Csound is a possible optional specialist backend. Avoid implementing two competing production/mixing stacks.
- Standard backend adapter: timestamped events, instrument definitions, parameter updates, outputs, latency and render capabilities.
- Production routing: voice -> instrument/track -> group buses -> master, with parallel aux sends/returns and sidechain signals.
- LV2 preferred on Linux, LADSPA compatibility and VST3 optional; actual hosting method **unresolved** and requires prototype validation.
- Effects: EQ, delay, reverb, dynamics, gate/expander, sidechain ducking, limiting, stereo tools, metering.
- Audio backend on Fedora: PipeWire preferred. Automatic negotiated device/rate/quantum and headroom reporting; optional strict project override.
- Distinguish audio buffer size, algorithmic plugin latency, and musical event timing. Compensate parallel path latency; feedback routing needs explicit handling.
- Native DSP and plugin chains must not force Python into the audio callback.

## Development
Single GitHub mainline; short-lived branches and squash PRs. Codex Cloud handles headless deterministic tests; Fedora Kinoite Toolbox handles real-time/audio-device validation. Keep project compact and portable.

## Explicitly deferred
Final GUI visual design, production-grade plugin host, cross-engine synchronisation, advanced mastering, full DSP snapshots, production-ready latency compensation, instrument library and full offline render equivalence. Validate these before committing implementation architecture.
