# DINAD development roadmap

Planning document; dates and milestone scopes are provisional. Completion is defined by evidence, not by labels.

## M0 — Feasibility foundation (integrated)
Object model; deterministic scheduler/mock history; TOML projects and semantic controls; SuperCollider rendering/live submission; Fedora diagnostics and local listening; subsequent timed-control work where documented. See [M0 brief](m0.md) and M0 implementation notes. Open limitations and local validation evidence remain authoritative in their respective reports.

## M1 — Modular, playable instrument (in progress)
M1.1 module contracts and M1.2 registry/config bindings are integrated. **M1.3 continues under its original continuous-performance scope.** M1.4 introduces the minimal **PySide6/Qt 6 Widgets single-window DINAD performance panel** and functional DINAD-native F12 drop-down configuration editor, followed by a **mandatory Fedora hands-on usability/audio gate** before proceeding. M1.5 integrates the first verified headless external instrument and includes a shorter plugin hands-on check. M1.6 delivers the combined playable instrument demonstration. See [M1 plan](m1.md).

## M2 — Musical intelligence and composition
Develop shared harmonic field and adjustable consonance/tension, voice and effect-tail awareness, multiscale generative behaviours, richer transformations, evolving forms, rule triggers, improvisation capture, reproducible event histories and usable composition editing. Verify musical quality through listening rather than relying only on unit tests.

## M3 — Fixed performance interface
Polish the PySide6/Qt Widgets implementation into the predominantly static virtual hardware panel: knobs, pads, displays, modal colour, contextual banks, keyboard-first navigation, command bar and **polished** drop-down project editor (functional F12 editor introduced in M1.4). The front end remains exclusively DINAD; never embed or launch plugin-specific editors. Improve accessibility, control feedback, undo and transactional configuration.

## M4 — Instruments and sound palette
Expand DINAD-native synthesis and compatible headless external instruments; add expressive modulation, granular/sample instruments where supported, curated ambient presets, instrument mapping profiles and project portability. Plugin capability differences must remain explicit.

## M5 — Production and rendering
Mature track/group/aux/master routing, effects, sidechains, plugin-host latency compensation, metering, offline quality modes, live FLAC recording, reproducible WAV export and state/preset portability. Test timing and tails under sustained load.

## M6 — Integration and release quality
Packaging for Fedora/Linux, migration/versioning, robust diagnostics, performance optimisation, crash recovery, regression and listening suites, documentation, and a stable personal-use release.

### Cross-cutting rules
- One GitHub `origin/main`, short-lived PR branches, no divergent development lineages.
- Fixed DINAD-only UI; headless plugins and DINAD-native configuration.
- Musical objects remain generic and interpreters independent.
- Cloud tests are headless; Fedora is the authority for real audio and plugin capability verification.
- Scope may change after listening and integration evidence; avoid building unused framework infrastructure.
