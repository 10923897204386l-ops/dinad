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

## Mandatory M1 front-end and plugin rules
- DINAD is the **only user-facing interface**. Never launch, embed, require or fall back to a third-party plugin's bespoke GUI. No vendor UI framework is part of DINAD.
- All user-facing plugin configuration, mapping, preset selection, automation and diagnostics are controlled by DINAD and represented in schema-validated, human-readable DINAD config files.
- Plugins are headless execution providers. A DINAD adapter translates resolved musical events, MIDI, timing and semantic parameters to exposed plugin capabilities; plugins need not understand musical objects.
- Discover and validate plugin parameters/capabilities; report inaccessible or GUI-only functionality as unsupported. Do not invent capabilities, plugin compatibility, or semantic parameter meaning.
- Opaque plugin state may be retained only as a supplementary versioned recall asset, never as the sole user-facing configuration mechanism.
- Front-end controls bind to stable DINAD parameter IDs, not directly to vendor-specific plugin APIs.
- Preserve timing, deterministic event history and safe note-off/resource cleanup across adapters. Validate on Fedora before claiming third-party audio works.
- Build small contracts and a single verified integration before attempting a universal plugin host.

## Deliverables per task
Explain files changed, test results, architectural decisions, limitations, and next recommended step. Never claim audio was auditioned in cloud.
