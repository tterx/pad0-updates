# v1.0.116 / updater324 — exposed puzzle reading without activation

The uploaded v115 control-state report has board=null, no active colour reader,
no running/prepared search and status=game-screen wait. Recognition epoch102 and
presence generation203 accompany a completed positive orb-grid presence ticket.
The captured foreground HWND98568626 differs from game HWND97651002. The report
does not identify that foreground window, capture its pixels or explain native
occlusion. It establishes a pre-recognition wait rather than a math timeout;
the positive ticket alone is not proof that every later visibility check passed.

The code can reproduce this wait with a fully exposed puzzle and a foreground
window outside its allowed set. Before, master ROI reading rejects that window
before native cell hit testing. The presentation tick also withdraws proof and
increments its generation on focus loss. Those independent gates can discard a
valid completed presence ticket and repeatedly clear recognition before counting.

For master mode only, read-only visibility now depends on the visible, nonminimized
game window and actual native ownership of all30 cell centers and four corners.
A foreign foreground is permitted only if those points belong to the game.
Any covering point, hidden/minimized game or held mouse still blocks the read.
The HUD tick opts into this exposed-board policy; it must subsequently pass the
unchanged actual-pixel presence worker before showing. Cold candidates are still
adopted only from fresh positive pixels. F9 remains authoritative. Strict default
foreground checks for crystal/title watchers, reward reads, condition captures,
execution and other modes are unchanged. This policy authorizes no game input.

F12 adds its monotonic observation time and last master visibility decision,
including foreground/game handles, focus membership, rectangle, decision time,
pass/fail and the covering point/owner when applicable. These are past observations,
not a new screenshot or current execution proof. Existing state-only save behavior
remains independent of capture and inference.

Seven new regressions exercise the uploaded rectangle/epoch/foreground shape with
actual uploaded puzzle pixels: exposed foreign focus, strict default callers,
one covered cell, minimized game and mouse drag, other modes, F9 and a complete
HUD+colour-reader tick sequence reaching two-observation readiness without a click
or foreground change. The older foreign-window context test now explicitly covers
the puzzle; foreign focus alone intentionally no longer rejects master reads.
No game input is executed. Solver, models, search budgets, gesture timing and final
execution validation are unchanged. Physical Windows/MuMu behavior and the exact
colour-count/solver failure beyond this recorded gate remain unverified.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43969557412 (success; source 1eb7919198bb74acb43d2ca838a234102af68636); 1559 tests, 144 suites. Exact package source SHA256 matches.
