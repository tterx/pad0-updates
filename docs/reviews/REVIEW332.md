# v1.0.123 / updater332 — continue route misses, adjust only after dungeon defeat

The user explicitly replaces stop-on-movement-mistake with: keep playing the
dungeon, and only after a dungeon defeat add10ms and retry. The increment applies
to corner dwell discussed immediately beforehand, not the straight sample gap.
This is master_multicolor automatic farming/repeat using the existing selected
MAA transport (including its MuMu SDK backend). Windows manual movement and other
modes keep their behavior. Existing merge/publication/update authorization applies.
All checks/builds/publication run in Codex; no Actions run is requested.

Report226 has real stationary differences at cells20 and28 before and after UP.
The pre-UP verifier continues to report failure; the global final-board checker
is unchanged. The automatic turn caller now treats a read-only final-layout error
as a nonfatal route miss only with a confirmed original start, the current complete
DOWN/140-MOVE/UP trace for226 (exact positions/contact/transport/order), acknowledged
release and no held/cancelled contact. A controller completion is explicitly not a
game ACK. Dispatch success is not invented: the original false dispatch flags stay
false for this read-only failure. Ordinary successful-dispatch held-layout failures
can also continue with the same trace/release prerequisites. Unconfirmed start,
missing/wrong trace, transport errors, time-limit errors and failed UP still stop.
F10 remains authoritative. If the existing failure cleanup left the original
controller capture cancelled, its normal owned release handoff resumes capture
without clearing device.cancel; it checks F10 before clearing the controller
signal. No rescue DOWN, path replay or corrective move is added.

The miss remains input_game_confirmed=False/input_layout_confirmed=False with an
explicit continued_after_route_miss marker. The last eight lightweight miss records
are retained in run diagnostics. After release cleanup the farmer returns to its
existing fresh stable-board/animation/result loop rather than asserting the route
worked. A missed route need not differ from the original board, so its transition
is not gated by the old three-second original-board-change assertion. Existing
party, stage/branch and navigation checks remain. Wrong-character/input-connection
errors are outside the movement-miss continuation policy.

The existing defeat detector still requires matching high-confidence TIPS, OK and
zero/max-HP readings plus two stable modal frames, then sends one owned OK. Only
that confirmed defeat calls the new hook, once per defeat. Each adds exactly10ms
to the current device's extra corner dwell. Repeated modal frames, repeated route
misses, victory, ambiguous dialogs and cancellation do not increment. Success
retains the learned value; it does not reset or lower it. The extra is keyed by
the existing device/resolution/controller-coordinate identity, published through
a run-owned UI queue, saved in normal user settings and reused on a fresh connection.
A different identity starts at zero. User cadence/calibration settings are intact.

The increment is added AFTER the existing SDK corner minimum: e.g.62.5ms becomes
72.5ms,82.5ms on successive defeats; the straight sample remains unchanged. The
same extra is reserved conservatively per edge by move_cap and applied to the
worker's actual corner dwell/estimate/preflight reserve. The automatic retry policy
keeps at least four seconds even in branches that historically borrowed that
reserve; an unknown or <=4-second timer cannot grant execution. Search fallback
still uses the adjusted budget. A learned wait never compresses samples or borrows
the reserve to force an infeasible route. When no route fits, the existing route
preparation can hold/stop; no claim of always finding a route is made.

New regressions use actual cropped226 baseline/held/released boards and its latest
complete gesture, the real turn caller, fake-clock native input, the real defeat
parser/stable-modal flow and owned persistence. They cover continued mismatch with
failed-success flags retained, incomplete/wrong trace, bad start/release/cancel,
no adjustment on misses, exact repeated10ms increments, duplicate defeat, restart
reuse and identity separation, stale settings/messages, other-mode/manual isolation,
SDK floor/unchanged sampling, increments above the old UI user-setting range, and
pre-DOWN rejection when the adjusted route cannot fit. Related repeat/map/result,
held-feedback, F10, taskbar, level-up and timing regressions remain required.

The new retry module is embedded before input code in the existing source bundle.
Two existing lifecycle/diagnostic methods (stop_playback and
_background_cache_input_failure) move unchanged into the compressed manual mixin
solely to retain the launcher's unchanged2MiB limit; scope checks compare their ASTs.
Solver/model/party/stage/branch data, GUI native policies, installer and launcher
are unchanged. Cropped fixtures omit desktop paths/character UI. Final validation,
actual download hashes and live isolated updater preservation checks are recorded
in release_checks332.json. Physical Windows/MuMu gameplay remains unverified;
controlled tests do not prove that10ms increments eliminate the underlying native
input-consumption issue, and all confirmed dungeon defeats train by user policy,
including defeats that may have causes other than a route miss.

Validation: source 5cfca49231ca9dac03370d997067c575e3854155;349 related tests in26 suites,153 bundled tests, no failures/errors/skips. Twenty-one new regressions. No Actions executed.

Publication verified: PR34 merged as e980bf96209a523989d6ca17ee56adac9fef5c13.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.123 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.123 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates332.
The unchanged launcher installed332 from the live manifest over v1.0.122 in an
isolated installation at state331, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
