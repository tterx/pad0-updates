# v1.0.117 / updater326 — manual button display and reader ownership

The user requested an option to keep the master-multicolor calculation button
visible, then fix its preparation stages in order. The new setting is
`master_path_always_visible`, off by default, with a main-window checkbox
“달인다속 계산 버튼 항상 표시 (입력·캡처 중 제외)”. It persists with ordinary
settings. When enabled, presentation does not wait for foreground classification,
orb-grid presence or colour recognition. F9 still hides the button. Input,
automation, route capture and playback still hide it temporarily. Missing or
unreadable game pixels produce a waiting reason when clicked, without game input.
Repeated failed reader ticks cannot withdraw the always-visible button.

This patch restores the v115 strict visibility baseline instead of retaining the
v116 exposed-foreign-focus policy, which was quarantined after a further missing
entry/calculation-button report. The exact physical regression was not established.
The root updater's rollback325 sequence is reserved; the new program uses326.

Controlled inspection reproduced additional preparation defects: orphan reader
busy flags, late inventory results clearing a replacement reader's active flag,
unbounded presence jobs during visibility churn, stale pointer-down state after
a lost release, perpetual suppression of whole-window entry checks by ROI proof,
and a wait-expired message claiming failure while the search remained active.
These are reproducible code paths, not proof that every user-reported symptom had
one physical cause.

Master-only readers now own explicit tickets with stop/mode/recognition epochs.
Inventory, presence and whole-screen jobs use3/2/4-second deadlines, respectively.
Late results cannot mutate replacement work. Each role permits at most two
unfinished native calls, including retired calls; if both remain stuck the UI
reports that the capture connection needs restarting rather than spawning more
threads. Python cannot safely kill a blocked native capture. Completed work frees
its slot. Workers use the mode captured at scheduling and do not read Tk goals.

The native foreground allowlist is consistent for the game and PAD0's own
controls, while actual native ownership/occlusion checks remain required. A stale
pointer capture is released only after native left and right buttons are both up.
Whole-window entry probing resumes at least every3seconds even while positive ROI
proof is present. Fresher entry pixels are not erased by older ROI pixels. Screen
results and root-tree changes are applied on the UI thread only while still owned.
An expired12-second manual wait describes the ongoing stage/search; it grants no
execution and does not automatically move drops.

F12 remains independent of capture/recognition and now records display policy,
read tickets and pipeline delay state. State-only error saving remains available
from the always-visible HUD even before a valid screenshot exists.

Twenty-five new regressions cover opt-in scope, no-game display, F9/capture hides,
real Tk HUD presentation, interleaved failed-reader ticks, guarded click behavior,
mouse release recovery, timeout capacity, epoch changes, stale/orphan readers,
entry probe scheduling and freshness, screen-worker success/error completion,
worker isolation from Tk, and truthful search status. Existing suites exercise
native occlusion, recognition, input ownership and all other modes. At the user’s
request, checks and packaging run in Codex, with no GitHub Actions run. Native
Win32 checks use controlled API fixtures on Linux. Actual release-download and
updater proof are recorded in the update repository.
Physical Windows/MuMu play has not been performed in this environment.

Solver, drop models, maximum-combo objective, search budgets, gesture timing,
final execution checks, reward verification, EX policy and separate normal/starred
branch history remain unchanged. Report222's automatic input interruption remains
unresolved and is outside this manual-control patch. Supporting methods live in
`pad0_manual_controls.py`, embedded by the existing portable-module loader to keep
the program within the launcher's2MiB source limit.

Local validation: source b1852f1bc09115d9618a58f6cbde660aa7f0082b;1,577 tests,144 suites;1 Windows-only DLL test skipped on Linux; all remaining tests passed. Bundle45 passed. No Actions executed.
