# DINAD Codex operating contract

## Source of truth
- The sole authoritative integration branch is `origin/main`. Never establish permanent cloud, Fedora, develop, or alternative mainlines.
- Before starting a task: `git fetch origin --prune`; inspect status and open PRs; create a short-lived task branch **from current `origin/main`**.
- Never begin new work from a stale local branch, or from an existing feature branch without explicit instruction.
- Keep each PR small, focused and testable. Prefer squash merge, then delete branch.
- Before PR: fetch latest main, reconcile/rebase safely, rerun tests and report base SHA, head SHA, tests and merge status.
- Never force-push main, silently discard conflicts, auto-resolve by overwriting unrelated work, or commit generated audio/logs/device-specific configuration.
- On Fedora, update main with `git pull --ff-only origin main`; stop if it fails. Preserve uncommitted work before switching branches.
- Do not merge or publish without user authorization. Do not spend on APIs or paid services without approval.

## Architecture
Read docs/architecture.md, docs/m1.md and docs/roadmap.md before M1 work; consult docs/m0.md for historical M0 requirements. The concept is frozen; propose changes rather than silently redefining it.
- Python for musical and control logic, not real-time sample processing.
- Keep musical objects generic and interpreters separate from object definitions.
- Make all musical and control decisions deterministic/testable where specified.
- Use an audio backend interface; start with a mock backend and a provisional SuperCollider adapter.
- Cloud must run deterministic tests without audio hardware. Fedora is for PipeWire, playback, plugins, latency and listening.
- Do not implement a piano-roll or node-patch editor as the primary UI.
- Preserve a small dependency footprint. Avoid premature C/Rust rewrites and unnecessary framework infrastructure.

## Frozen GUI technology
- **PySide6 / Qt 6 Widgets is the sole DINAD desktop GUI toolkit.** Read docs/gui-decision.md before any GUI work. No QML/Qt Quick, browser/web/Electron, GTK, Dear PyGui or vendor/plugin editor without explicit owner-approved architecture change.
- The DINAD GUI is one window; a minimal performance panel and an **in-window F12 drop-down** editor are M1.4 deliverables. Use Qt text editing and syntax highlighting. Keep final panel visual polish for M3.
- The application model and headless CLI/tests must remain usable without Qt or a display. Qt must not perform audio DSP or block scheduling; all controls go through DINAD's semantic model.

## Mandatory M1 front-end and plugin rules
- DINAD is the **only user-facing interface**. Never launch, embed, require or fall back to a third-party plugin's bespoke GUI. No vendor UI framework is part of DINAD.
- All user-facing plugin configuration, mapping, preset selection, automation and diagnostics are controlled by DINAD and represented in schema-validated, human-readable DINAD config files.
- Plugins are headless execution providers. A DINAD adapter translates resolved musical events, MIDI, timing and semantic parameters to exposed plugin capabilities; plugins need not understand musical objects.
- Discover and validate plugin parameters/capabilities; report inaccessible or GUI-only functionality as unsupported. Do not invent capabilities, plugin compatibility, or semantic parameter meaning.
- Opaque plugin state may be retained only as a supplementary versioned recall asset, never as the sole user-facing configuration mechanism.
- Front-end controls bind to stable DINAD parameter IDs, not directly to vendor-specific plugin APIs.
- Preserve timing, deterministic event history and safe note-off/resource cleanup across adapters. Validate on Fedora before claiming third-party audio works.
- Build small contracts and a single verified integration before attempting a universal plugin host.

## Frozen M2 Lua automation boundary
- Read docs/lua-automation-decision.md before Lua or M2 planning. Lua is **only a programmable source of DINAD semantic controller events**, not a second composition engine, plugin authoring runtime or DSP language.
- Lua and GUI controls use the same stable DINAD parameter registry and Python scheduler. Python retains musical interpretation, plugin/synthesis management and production orchestration; native engines do real-time DSP. Never permit Lua to call plugin-native APIs or run in audio callbacks.
- Preserve deterministic timestamped event history, restrict script capabilities and resources, and explicitly specify manual-vs-automation precedence. Do not add Lua to M1.5/M1.6.

## M1 sequencing and listening gates
- Do not change or interrupt an in-progress original M1.3 continuous-performance build; complete and merge it on its existing scope.
- Next, M1.4 implements the minimal PySide6/Qt Widgets DINAD performance panel and functional F12 DINAD-native config editor with validated Ctrl+S application and safe rollback. See docs/m1.md. Leave advanced visual polish for M3.
- **Pause for a Fedora hands-on listening/usability gate after M1.4 and before M1.5.** Owner has reported M1.4 working and authorized proceeding to M1.5 with M1.4.1 included first; retain M1.5's separate Fedora plugin listening gate.
- M1.4 owner Fedora test: PASS with minor usability follow-ups (editor key and random seed). **Before M1.5 host work**, implement docs/m1-4-1.md as its own small PR from fresh main: backtick editor toggle (F12 retained), keyboard seed editing with explicit active/pending semantics and deterministic tests. Do not hijack typing backticks in the editor. Keep this PR separate from plugin-host code.
- M1.5 is the first headless external instrument integration; M1.6 is the combined playable demo. Follow the revised milestone numbering in docs/m1.md.

## Deliverables per task
Explain files changed, test results, architectural decisions, limitations, and next recommended step. Never claim audio was auditioned in cloud.
