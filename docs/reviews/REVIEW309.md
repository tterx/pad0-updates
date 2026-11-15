# v1.0.101 / updater309

211 differs from210: v1.0.100 stops at background_skill_no_effect after one Aten
Use click, not at the path-search ceiling. All30 drops are locked and yellow has
only2 orbs. Unlock is unavailable; the selector spends a five-board skill whose
conversion cannot affect this full locked board. Time nevertheless increases8 to11,
and a new cooldown2 is read at .99178/.99147. Check2 calls this time_increased;
check3 changes the preferred reason to observed_cooldown. The old signature includes
the reason, resets stability, and reaches4.172s without confirming the real effect.
The saved observations reproduce this failure in the original three checks.
Actual image OCR independently confirms8,11 and2; both saved crops match the board.

Master-only policy: after confirming unlock unavailable with30 detected locks,
remove blocked five-board/generation conversion needs. Keep ready unlock first
and independent time, bonus, cleanse and other relevant counters. Partial-lock
and other-mode conversion behavior stays unchanged. With no useful skill, keep
the established maximum-combo turn progress; no forced healing or new turn budget.

Master skill confirmation retains independent evidence across consecutive stable
board/spike/combo-metadata observations. A stable time increase can confirm even
when a newly readable cooldown becomes the preferred label. One time read plus
an unrelated single cooldown, changing grids, conflicting cooldowns, popup-only
or missing evidence still fail; no extra Use click. Keep the existing bounded
confirmation/grace windows. A verified activation with fully locked conversion
can settle without impossible colour changes when no unlock/refresh/generation
is expected. Require the same recent device/effects, current30 locks and stable
board; ordinary changes and other modes keep existing requirements.

F10 previously left the failure window foreground although the route button
requires foreground MuMu. From a master automatic run/failure, F10 minimizes the
PAD0 failure window and requests game-window focus, without mouse/key/route input.
Reset pending display probes and request current recognition. A fresh independent
ROI proof takes priority over the less precise whole-window fallback, preventing
that fallback from erasing/moving a currently verified puzzle. Explicit menus,
stale epochs, occlusion and non-puzzle presence still hide controls.

After master controller release/close, send a run-owned handoff before slow PNG
archive and diagnostic reads. Only this verified release clears running state;
F10's epoch/manual state is retained. Freeze the old failure for background archive
writing so it cannot overwrite a new manual snapshot. Late archive completion
cannot reset the manual epoch, resume automatic input, or stop a newer run. Input
release errors do not claim an early handoff. Defer before-cleanup diagnostic reads
for master failures, preserving the fact that cleanup was prioritized; diagnostic
read failure after release cannot prevent handback or archive. Other modes retain
the original terminal-message and archive behavior.

22 new tests exercise independent positive/negative skill proof, full versus
partial locks, device/effect ownership, unlock priority, focus-only F10, current
ROI precedence, slow archive ordering, stale completion, diagnostic failure and
archive isolation. Existing related tests151 pass. Private comparison uses the
recorded observation sequence and generated frames, actual image OCR, and mocked
controller/UI boundaries; it is not a captured F10 screen sequence. A real solver
on211's actual board reaches a verified combo turn without a skill cast in a
mocked-device flow. Private screenshots/reports are excluded from publication.
Full Windows/package/public ZIP/updater proofs are recorded in the release repo.
Physical MuMu farming/manual transition remain unverified.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43713728382 (success; source 4248dbc17cf66f035d30282aa3fb9f4574819314); 1,275 executions, 121 suites. Exact package source SHA256 matches.
