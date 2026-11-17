# v1.0.124 / updater333 — report227 search-goal miss must not stop auto play

The user reports that v123 still stopped after asking to continue mistakes until
actual dungeon defeat. Report227 is a different remaining stop: background_search,
no current gesture/path, timer8s, usable4s, move cap23. The saved board crop reads
exactly as the report board. Its primary20-edge route replays to4 attributes and
5 combos; a subsequent fallback also missed the requested attack target. The final
error reports3/5 attributes and5/6 combos. This is not an input-release failure.
The full failure image is explicitly last_worker_capture (before the board-changing
skill); analysis uses the current saved board crop, not that older outer frame.

v123 continued final movement mismatches but left search-goal rejection in place.
When a master automatic selected-MAA run exhausts its attack search, this patch
compares the retained initial candidate and fallback candidate with an optional
unconstrained combo search bounded to.75s and the remaining overall search budget.
It also enumerates every actual legal one-edge move, so search timeout or a zero-
combo best candidate does not become a stationary DOWN/UP or a six-combo exception.
The bounded extra search is included in math_spent. Once the search allowance is
exhausted, the existing bounded planners and replayed candidates can still yield
an actual turn rather than return to the eight-second same-board stop on a goal
miss. No unlimited search or additional per-cell capture is introduced.

Each candidate is replayed from the current board, must be orthogonal, have at
least one edge, fit the current move cap and start on an observed cell. Existing
thorn/poison preferences precede damage ranking; candidates then rank by known
combos, attributes, matched count and shorter route. Proven existing-drop cascades
can contribute to ranking; unknown refill is never invented. A below-six route
gets explicit available-combo execution goals, not a fabricated fulfilled5-colour/
6-combo target. With no matching single-step candidate, a genuine board-progress
move is permitted. Requested goals remain in diagnostics alongside
continued_after_search_miss=True and the actual requested_attack_goal_met result; the runtime
status states the best currently found combo count.

Fresh board, timer, spikes/locks/status and move cap are still checked before
input. A changed board is replanned before any drag. No time can be borrowed from
the four-second reserve; an impossible move cap or no observed starting drop cannot
grant execution. F10 and transport/release handling remain intact. The defeat-only
10ms learning/persistence logic is unchanged: search misses do not increment it.
Other modes, Windows manual play and ordinary strict search branches retain their
behavior. No solver, model, global layout verifier, native input/timing, party,
stage/branch, HUD/native-window, launcher or installer implementation changes.

Thirteen regressions cover the actual227 crop/route metrics, the real bounded combo search, full automatic turn
caller dispatching a below-target progress route within4s/cap23, short-timer
fallback retaining four seconds, exhausted zero-combo search making a real move,
F10, fresh-board replanning, cap/orthogonality/observed-start filters, zero-combo
board progress, hazard preference and extra-search budget. Existing v123 movement
continuation, stable defeat, timing, profile persistence and capture/F10 handoff
checks remain required. Final local and exact-bundle checks, download hashes and
live isolated updater preservation results are recorded in release_checks333.json.
All work runs in Codex without Actions. Physical Windows/MuMu play is unverified;
no globally optimal route or successful dungeon clear is claimed by these fixtures.

Validation: source 8d84ce3898818c2d9021f04e897054edc833ec57;362 related tests in27 suites,166 bundled tests, no failures/errors/skips. Thirteen new regressions. No Actions executed.

Publication verified: PR35 merged as 48380ea94940caf1173eccd52b4b6e6692e1faae.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.124 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.124 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates333.
The unchanged launcher installed333 from the live manifest over v1.0.123 in an
isolated installation at state332, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
