# v1.0.125 / updater334 — report228: recover a cancelled start without moving blindly

228 runs v124 and stops at background_before_drag with an eleven-second timer
(seven usable, four reserved). It never sends a MOVE: the latest owned gesture is
DOWN(1820,601) then UP at the same point with finger0 on the selected MAA SDK.
Both releases are confirmed; dispatch is false. The start is cell17, a jammer.
The two start checks take approximately3.08s including2.79s of capture/recognition.
The lifted sprite is translucent and its colour/eye evidence is insufficient;
strict start proof remains false. After the second check the conservative route
reserve no longer fits. v124 correctly cancels movement, but propagates the start
budget error and stops farming. Its continued-route policy requires a complete
MOVE trace, so this zero-MOVE case cannot use that policy.

Keep that strict lifted-start proof. In master automatic MAA play only, use the
measured last probe cost before scheduling another start probe: stop checking
when another probe plus the existing whole-route reserve cannot fit. No MOVE is
sent for an unconfirmed or wrong start. This is an early cancellation, not a
relaxation of identity/colour/eye thresholds, or permission to spend the four-second
reserve. A positively confirmed but excessively slow start can also cancel before
MOVE and qualify for the same recovery checks.

The parent recognizes only the soft start-confirmation errors with a latest
owned DOWN/UP pair, correct native coordinates, finger and transport, positive
release acknowledgements and no active contact. Wrong observed start, hard
transport/capture errors, partial movement, failed release and F10 cannot qualify.
After release it captures twice and passively reads the current board. Both must
match the planned board, combo flags and geometry with no unresolved cloud; a
separate textured central-tile comparison must prove that the old lifted start
has returned to its baseline pose. The actual228 released crop gives correlation
0.99993 and mean RGB difference2.172; the held crop fails this resting test.
No second DOWN occurs inside the failed gesture; the ordinary next-turn pipeline
reads timer, board, spikes and statuses again before granting another gesture.
A possible defeat panel is handed back without a tap or correction increment;
only the existing stable TIPS/OK/zero-HP detector may confirm defeat and add10ms.

A failed start is temporarily excluded for that board geometry and current device
identity. If an adjacent equal-colour cell produces the exact same final colours,
combo flags and thorn layout by prefixing one edge to the old route, use that
prefix only within the current cap. For228, cell16 can precede cell17, preserving
the original final board with36 edges instead of35. Otherwise compare bounded
available-progress candidates excluding the failed/unknown starts. The result
remains the best found bounded route, not a guaranteed global maximum or clear.
No confidence, successful dispatch or goal result is fabricated. Start misses do
not increment corner dwell; confirmed dungeon defeat remains the only10ms trigger.

Other modes and Windows manual behavior retain their prior branches. Solver,
recognition/model, defeat verifier/learner, input coordinate rules, HUD/taskbar,
party, stage/branch, launcher and installer algorithms remain unchanged. To retain
the launcher's existing2MiB source limit, three manual window/timer helpers move
unchanged into the existing compressed ManualControlsMixin. AST identity and
module global dependencies are checked, alongside manual/UI regressions.

Thirteen new regressions use the actual228 board crops and DOWN/UP trace, reject
held/changed/cloud/different-geometry frames, partial MOVE/wrong pointer/transport/
start/release and F10, verify two passive reads and board/device scoping, replay
the equivalent prefix including combo/thorn negative controls, test the full
failed-turn to next-turn caller, confirmed-slow starts, defeat-only learning and
cost-aware native scheduling without a blind MOVE. Related tests and tests of the
exact bundled source run locally in Codex; Actions is not used. Final counts,
hashes, public ZIP/source checks and live isolated updater preservation are in
release_checks334.json. Physical Windows/MuMu play is unverified.

Validation: source 39ce5046308f885c8c7134cf3a56f715dfcb3ece;375 related tests in28 suites,179 bundled tests, no failures/errors/skips. Thirteen new regressions. No Actions executed.

Publication verified: PR36 merged as 1c50cdf657eaccafa27e7e47ed6db7192764eaf8.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.125 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.125 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates334.
The unchanged launcher installed334 from the live manifest over v1.0.124 in an
isolated installation at state333, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
