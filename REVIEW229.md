# 1.0.21 / updater229 — clear a disabled or enemy-converted board

The reported screenshot contains only fire and jammer orbs, with skills unavailable. The prior path could reach search after counter fallback but still required six combos. A failed survival/timer fallback therefore stopped the run. Manual best-available selection also optimized combo count and could restore enemy combo requirements during later execution gates.

## Behavior

Usable cleanse/haste/generation counters retain their existing priority. Once there is no usable cast (or the cast limit is exhausted), a deficient five-attribute board or unavailable bind/awoken counter enters explicit board-clear mode. A full six-slot character bind also qualifies. Converted boards qualify through their observed missing attribute inventory, independently of character-bind detection; skill seal is not falsely labelled as a newly detected character bind. This turn has no attribute, heart or enemy/team combo floor. Normal attack boards retain their original five-attribute and six-combo policy.

The beam search ranks directly matched **orb count** first. Hazard/poison estimates and combo count only break ties. It searches under the actual move cap, and independently compares every legal single swap. Existing matches or an expired math budget consequently still produce a real orthogonal edge, rather than a stationary press/release. Automatic mode may execute a zero-match single move to advance the cooldown/debuff turn. Manual mode uses the same clear-count objective but still requires a match before dispatch. No search promises a global optimum; the found maximum clear count is used.

Explicit board-clear goals and fallback metadata survive direct/manual solving, the headless worker, preview acceptance, cached route validation, mouse playback and its final condition/timer recheck. Enemy combo recognition failure or a changed combo floor cannot block this turn-progress objective. An unread timer stays unknown, with the existing bounded five-second input budget; four seconds are not reserved in board-clear mode. Board identity, geometry, relevant thorn risk, orthogonal movement, usable time, start/release proof and F10 cancellation remain enforced. Unknown cloud estimates cannot authorize a drag starting on an unobserved cell.

The manual UI says `판 정리 실행` and describes the absence of attack/combo conditions. Force-search also permits the shortage-board clear route. Selected party settings are preserved and normal attack requirements return on an attack-capable board.

## Evidence and checks

`tests/test_board_clear229.py` has15 regressions covering the visible two-colour board, unavailable counters, cleanse-first ordering, a feasible-rainbow board with all characters bound, unread time and combo condition, no-match automatic turn advance, cancellation, already-matched/zero-math-budget boards, clear count outranking combo count, manual direct/headless/preview/cache/playback propagation, final mouse timer/condition checks, and rejection of stationary/unrequested/invalid manual routes. The cloud-jammer regression now calls the explicit clear solver and still verifies that the start is not an assumed cloud cell.

`BOARD_CLEAR229.json` records one replayable solution of the manually transcribed visible J/R board:30 directly cleared orbs in5 combos,34 orthogonal moves, no required combo floor. This is an offline board/path replay, not an assertion that a pixel classifier or physical MuMu completed the turn. Full Linux and exact-source Windows results, portable ZIP integrity, bundled import, upgrade/backup/corruption rejection, startup AI cleanup, published ZIP download and live updater checks are recorded in distribution `release_checks229.json`.

No model weights, input backend or movement pacing changed. Development-only bundled models, no local learning/data recording, previous AI-store cleanup and removed AI button remain in effect. Physical MuMu gameplay is not available for verification here.
