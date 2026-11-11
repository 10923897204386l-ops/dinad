# DINAD front-end technology decision — frozen 2026-11-11

**Status: accepted and frozen for M1.4 and subsequent front-end work.** This is a technology/architecture decision, not a claim that a GUI is already implemented.

## Decision
- **Python PySide6 bindings to Qt 6, using Qt Widgets** are DINAD's sole desktop GUI toolkit.
- Build a **single DINAD application window**, resizable and optionally full-screen. Use custom-painted Qt Widgets for the eventual static hardware-style instrument panel; use Qt's native text editing primitives (QPlainTextEdit, QSyntaxHighlighter or equivalent Qt Widgets facilities) for the configuration editor.
- The final instrument layout remains largely static, with contextual mode-dependent labels, displays, knobs and pads. Mouse optional; keyboard-first navigation and controls remain mandatory.
- **No QML/Qt Quick, web/browser/Electron UI, GTK, Dear PyGui or plugin-specific GUI** in the normal DINAD interface. Revisiting this frozen choice requires explicit owner approval and a documented migration proposal.
- Qt is the **presentation layer**, not the musical or audio engine. The application model, semantic parameter registry, config validator and event scheduler must be independently testable without importing PySide6. The GUI may not directly manipulate SuperCollider or a third-party plugin.
- Preserve headless CLI, mock tests, deterministic replay and existing M0/M1 APIs. Do not require a display or Qt to run core tests/CLI; add PySide6 as an optional GUI dependency where feasible, with a clear installation path for Fedora Kinoite Toolbox.
- The GUI thread must not run audio DSP, block the native audio callback or stall the scheduler. Use bounded asynchronous command/state messages and Qt-safe event delivery; show errors and actual state rather than inventing success.

## M1.4 minimum GUI deliverable
1. A runnable DINAD GUI entry point launching a single Qt Widgets window with a deliberately basic performance panel: playback/transport status, active object/project, a few semantic parameter controls, error/status indication and keyboard operation. This is not the finished M3 panel.
2. **F12** toggles a Yakuake-inspired drop-down editor **within that same DINAD window**, overlaying the performance panel rather than spawning a separate editor or plugin window. The editor shows the actual selected DINAD TOML configuration, current path, unsaved-change indicator and validation diagnostics.
3. **Ctrl+S** validates and applies supported changes transactionally; invalid edits preserve both the last good on-disk configuration and the running instrument state. Clearly indicate edits requiring a stop/restart or audio graph rebuild. Offer reload/discard with explicit unsaved-change handling. Syntax highlighting and line/column error feedback are required.
4. GUI controls and the editor bind through DINAD's application model/parameter registry, never vendor parameter APIs. Existing semantic control bindings remain stable.
5. Include GUI smoke tests (offscreen where feasible), configuration failure/rollback tests and Fedora launch/playback instructions. Do not assert hardware listening from headless tests.

## M1.4 acceptance / owner pause
After M1.4 merges, **stop before M1.5** for a 30–60 minute Fedora hands-on check: launch GUI, keyboard navigation, F12 and Ctrl+S, sustained audio, live parameter updates, safe invalid edits, error feedback, CPU/underruns and usable focus/shortcuts. Owner decides whether to repair or continue.

## Deferred to M3
Finished custom knob/pad visuals, modal colours, refined layout, polished animation, autocomplete, rich parameter browser, contextual help and full accessibility/ergonomics. Do not delay the functional M1.4 editor for visual polish.
