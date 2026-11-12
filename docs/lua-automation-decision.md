# Frozen decision: Lua is DINAD controller automation

Accepted: 2026-11-12. **Design decision for M2, not an M1.5 implementation request.**

## Boundary
Lua is a programmable virtual DINAD controller. **Every writable DINAD-exposed parameter/action that a physical or Qt control can map to may also be driven by Lua**, subject to the same capability declarations, bounds, permissions, timing, smoothing, validation, and state transitions. Lua never bypasses the stable semantic DINAD parameter registry or directly calls a synthesizer, SuperCollider, LV2/VST plugin, DSP, host, mixer, audio callback, or backend-specific native parameter ID.

TOML describes project configuration and bindings; Lua describes time-varying controller actions and automation rules; Python owns musical objects, interpretation, parameter registry, scheduling, module/plugin adapters, sound-production orchestration, event history and replay. Native engines/plugins perform real-time synthesis and DSP. Lua does **not** become a competing musical-object interpreter, general plugin implementation language, sound-synthesis language, audio processing engine or replacement for Python.

## Contract for M2
- Define a minimal, versioned controller-facing API using stable DINAD semantic IDs, read-only parameter metadata, musical-time scheduling, validated set/ramp operations, and optional event/clock callbacks. The GUI and Lua submit the same semantic control actions to Python.
- Scripts run outside real-time audio callbacks, with bounded CPU/time/event quotas and an isolated/restricted runtime. No unrestricted filesystem, network, process or wall-clock access. Investigate standard Lua runtime/bindings and packaging before selecting one; do not assume Lua embedding is secure by default.
- All generated controller actions must be timestamped, ordered, logged and replayable. Use DINAD-supplied deterministic random state and musical clocks; record script/version/seed and the resolved event history. Replaying resolved events should not require rerunning Lua.
- Manual versus scripted control conflicts need explicit, visible priority rules. Initial proposal: manual input takes effect until the next scheduled automation event; consider touch/latch modes later, but verify this choice with owner before implementation.
- Lua may control production parameters only **through the same exposed DINAD controller bindings** (e.g. a mapped reverb wet level), not through native DSP or plugin interfaces.
- F12 editor may eventually open linked .lua automation files with validation/highlighting; keep Qt/PySide6 sole front end.
- No Lua work is authorized in M1.5 or M1.6 by this decision. Plan incremental M2 PRs: API contract and deterministic tests; restricted runtime prototype; TOML script references and UI editing; automation behaviours and listening tests.

## Status and scope
M1.5 external plugin integration has been reported **built locally** by the owner. GitHub PR #17 was open at the time of this decision; local listening, merge and plugin behaviour are not confirmed by this statement. Do not alter the M1.5 implementation branch for this documentation decision.
