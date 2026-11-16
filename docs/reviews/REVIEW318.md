# v1.0.110 / updater318 — manual button disappears after using a skill

The user reports ordinary manual play, without an automatic farming handoff:
the calculation button was visible, a skill was used, and the button disappeared
even though a mixed locked/jammer puzzle is visible. The inline screenshot has no
local file/Sediment attachment; its exact pixels cannot be replayed. No claim is
made that its original classification output or physical MuMu flow was observed.

The reproducible control-flow defect is that _path_button_may_show returns before
launching its independent presence worker when _live_board_ready is false, and
also requires a recent full-recognition timestamp or retained route. Skill/menu
transitions can invalidate those prerequisites. Current positive puzzle evidence
cannot restore the button, and transient full-recognition misses can withdraw it
again. This affects ordinary manual play as well as the prior F10 recovery path.

For master_multicolor only, the calculation control now depends on current puzzle
presence, independently of solver/readiness state. The existing actual-pixel
capture, exact rect/mode/epoch/generation identity, foreground/occlusion checks,
negative-menu rejection and1.2-second expiry stay in force. The learned eight-drop
model, confidence/texture thresholds and fallback recognition are unchanged.
Transient recognition misses can clear readiness without withdrawing a freshly
proved puzzle button. Explicit nonbattle clearing discards positive/inflight
presence generations; old work cannot restore them. Fresh positive ROI evidence
also protects an unready F10 manual stream from a competing full-frame miss.
The restored unready control is labelled calculation rather than stale execution.

An explicit master click before full recognition cancels old preparation, wakes
reading, and uses the existing bounded12-second preparation wait to await a fresh
complete stable board. Presence is never turned into thirty guessed colours or
route readiness. A missing/old candidate cannot satisfy this stage. F10, area or
mode changes cancel the wait. The original deadline is never renewed. Completing
preparation only reports readiness: another execution click remains necessary.
No solver starts, old route executes or game input occurs merely because a button
is displayed or a wait completes. Existing final fresh-board, condition, timer,
held-layout and release checks remain in force.

Twenty-one new checks cover absent/stale readiness with positive presence, actual
mixed real-art lock/thorn/jammer/poison crops, captured menu negatives, expiry,
transient misses, explicit nonbattle generations, ordinary and F10 manual paths,
F9, pointer-down lifetime, other-mode controls and explicit unready-click waiting.
A repeated skill-menu-to-new-board episode uses real capture workers on controlled
real-game-art boards and checks stable presence for many polls. A real Tk button
is shown, clicked, hidden on a menu and restored on a new puzzle; clicks request
reading only. All Android/controller boundaries remain unexercised. The exact
current inline screenshot and a successful live MuMu episode remain unverified.

Only master display/readiness wiring changes. Other modes keep their prior
readiness gates. Maximum-combo/no-forced-healing policy, frozen all-eight-drop
learning, skill strategy, movement timing/budgets, release guards, branch learning,
EX ranking and separate ordinary/starred/Hidden stage records are unchanged.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43809690416 (success; source 0d1c44c3b778191020c362c23d85c0ef1ce7864f); 1,485 executions, 138 suites. Exact package source SHA256 matches.

[Public replay comparison](../../releases/v1.0.110/replay318.json).
