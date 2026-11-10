# DINAD M0 local audio validation — 11 November 2026

Tested source: PR #4, commit `156323a3e1e56aeab10ed67d18786c21447e075c`.
The tests preceded integration. PR #4 was subsequently squash merged as
`48a71341598392e12d1ff90ac09ccd7b9768ba13` on 11 November 2026 (Melbourne).

## Results

- PASS: all 138 M0.4 tests, including actual SuperCollider offline rendering, on Python 3.14.8 / SuperCollider 3.13.0.
- PASS: example project rendered 61 events to 48 kHz stereo float32 WAV, 10.201333 s, peak 0.139662, RMS 0.036196; no nonfinite samples or clipping.
- PASS: WAV playback routed to this PC's real laptop speakers. User initially heard nothing, then confirmed “i heard it then”; described the level as very quiet. A later WAV replay used stream volume 1.0 instead of 0.5 (+6.02 dB), without changing system volume. User subsequently confirmed the louder playback and live tests were “Clear, adequate level, no obvious glitches”.
- PASS: four live CLI submissions to two exclusively owned SC server instances on localhost port 57120. Each completed all 61 expected voice starts and ends, reporting DINAD_LIVE_OK. No server node failures; servers stopped after tests.
- PASS: explicit stereo PipeWire/JACK routing from SuperCollider out_1/out_2 to the real Speaker playback_FL/playback_FR ports.
- PASS: two speaker-monitor recordings captured live output. Both peak at 0.152476; no nonfinite samples or clipping. Monitor capture verifies the digital output path, not acoustic quality.
- PASS: SuperCollider reported zero PipeWire errors throughout captured monitoring. Speaker error count was already 1 at the first monitored sample and stayed 1 throughout both captures (13 and 24 samples). No increases were observed; this is a short smoke test, not an extended dropout certification.

## Actual device/settings

Fedora 43 Toolbox, host PipeWire 1.6.8. Lenovo IdeaPad S540-13ITL, sof-hda-dsp / Realtek ALC257 internal Speaker output. Actual SC rate 48000 Hz; JACK buffer/graph quantum 256 frames (5.333 ms); synthesis block 64 frames. Recorded graph snapshots and live server logs are retained locally. A 256-frame buffer is not a measurement of full output or round-trip latency. Language startup warned that real-time scheduling priority was unavailable; live server itself reported no startup errors. Headless Qt also emitted graphics warnings.

## Setup and retained failures

No system packages or global audio settings were changed by this session. Temporary Fedora RPM tools were extracted under the chat's work/audio-tools directory because sudo is blocked by the session's no-new-privileges restriction. Toolbox installation is authorized by the user but not completed by this session. The first extraction raced an incomplete download: sclang initially crashed, then could not compile its missing class library. Re-extraction after download completion and local Qt path configuration resolved startup; all 138 tests then passed. Failure logs are retained under work/audio-tools. No DINAD source changes were required.

## Listening confirmation

PASS: listener confirmed clear playback, adequate level and no obvious glitches in the louder playback/live tests. Individual voice and effect-tail characteristics were not separately scored.

## Still open
- Calibrated electrical output/round-trip latency requires wired loopback. An acoustic estimate is now available below.
- Sustained-load stability is outside these short demos.
- Timed live control/rolling transport is not implemented in PR #4 and cannot be validated as an existing feature.


## Acoustic latency estimate — 11 November 2026

Five 80 ms swept chirps were played through the real speakers at 48 kHz. A single two-channel PipeWire recorder simultaneously captured the speaker digital monitor and built-in Mic1; cross-correlation compared the two channels. Median lag: **12.90 ms**; range **12.60–13.23 ms** (individual trials 13.23, 12.94, 12.90, 12.65, 12.60 ms). Normalized correlation 0.79–0.91 supports identification of the chirps in the microphone signal. No clipping occurred in the correlated microphone segments.

This is an approximate monitor-to-acoustic-input lag, including output path, sound travel, microphone/input processing and graph alignment. It is not isolated output latency, electrical round-trip latency, or keypress-to-sound latency. Application scheduling before the digital monitor is excluded; DINAD's default 200 ms scheduling lookahead is separate. The small trial spread is not a calibrated accuracy bound. Playback and capture requested 256-frame buffers; no system volume or microphone settings were changed. A wired loopback remains necessary for a calibrated electrical measurement.

Numerical results: acoustic-latency.json. Raw microphone recording is retained only in the chat work directory and is not included in exported outputs. Test recorder and player have stopped.
