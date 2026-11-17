# v1.0.120 / updater329 — battle-lifetime master route overlay

The user refines the presentation rule: keep the route button throughout dungeon
combat and hide it at the result screen. This supersedes v119's two-negative-frame
and2.5-second expiry for an established battle. The no-taskbar tool-window policy,
F9, capture/input hides and the v118 latency improvements remain unchanged.

A fresh, positive puzzle observation scoped to the current game host and master
goals starts a display-only combat phase. A cached route, recognition-ready flag
or old/unscoped presence record cannot start that phase. Once established, missing
puzzle frames, skill/character popups, capture errors, recognition resets and F10
stop-epoch changes do not end it. Native presence/colour readers continue to run;
no pixel timestamp, recognition readiness or execution permission is renewed by
the phase. Foreground gating and temporary hides still apply, and returning from
another app can restore the HUD without waiting for a new positive board sample.
A different/missing game host or a mode change discards the phase; resizing the
same game host does not change input coordinates from display evidence.

The existing passive full-screen worker emits its recognized heading through the
owned UI queue. Only a current, fresh result or definite out-of-dungeon heading
ends the phase and clears the visible HUD, old route and recognition readiness.
Examples are result, repeat/entry confirmation, home, adventure and dungeon lists.
Unknown headings and character/awakening detail popups are not exit evidence.
Current result observations are consumed even after F10 while actions are stopped;
retired jobs, old stop epochs, foreign contexts and stale observations are rejected.
When result/exit OCR takes long enough to age the first screenshot, the existing
v118 recapture mechanism verifies that all actual title pixels still match before
using the newly captured timestamp. This never relabels old pixels as fresh. The
worker does not access Tk or execute game input.

A one-time master preference migration selects combat-conditioned presentation,
including installations that had re-enabled the earlier emergency always-visible
option. The explicit diagnostic always-visible option remains available if the
user turns it on again afterwards. This phase is not persisted across sessions;
a program restart needs a new current puzzle observation. F12 state diagnostics
record the active display phase and last confirmed exit.

Sixteen new regressions cover30seconds of repeated board misses with one mapped
HUD, recognition clearing and F10, real queue delivery of current result evidence,
result detection after stopped actions, popup/unknown headings, stale/retired/foreign
messages, dungeon exit headers, host/geometry/mode changes, focus return, F9/input
hiding, one-time preference migration, no phase from cached/unscoped evidence,
slow-result recapture, changed titles and unchanged other-mode capture behavior.
Two focused cases fail on v119 and pass with this change. Existing v119 display
expectations now reflect the user's battle-lifetime rule. Native tool styles and
main-root isolation continue to run through their original tests.

Checks, packaging and direct publication run in Codex; no Actions run is requested.
Physical Windows shell/MuMu play is unverified. If no current result/exit can be
read because the capture connection remains blocked, display is retained until a
confirmed exit or host/mode change; no unsafe native-thread termination or input
bypass is introduced. The existing bounded-reader restart policy remains. Solver,
models, maximum-combo policy, input modules, stage identities and branch records
are unchanged. Final metadata records tests, hashes and actual download/updater
verification with personal data preserved.

Validation: source 31be508011f210606377f955ceb92faefe4ff48f;313 related tests in26 suites,66 bundled tests, no failures/errors/skips. Sixteen new regressions;two focused cases failed on v119 and passed after fix. No Actions executed.

Publication verified: PR31 merged as 79210614601d32a908edf59383f0d5e407a17108.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.120 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.120 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates329.
The unchanged launcher installed329 from the live manifest over v1.0.119 in an
isolated installation at state328, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
