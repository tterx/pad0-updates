# 1.0.12 / updater220 —096 bound-cloud partial board

096 reached battle and completed earlier drags. At failure its board had18 valid cells and12 None values under the bottom two cloud rows. `cloud_observation` and `cloud_diagnostics` were absent. The solver raised that30 valid drops are required. No stationary cloud probe had run for this board.

When binding/awakening disablement remains, `_background_observed` intentionally supplies a partial `background_skill_board_sample` so ally-clearing skills can run before touching hidden drops. After no usable clearing/haste option was found, the battle path incorrectly treated that skill-only sample as a playable board. Unknown cells also looked like a colour shortage and caused unnecessary board-skill selection.

Keep the initial cleanse/haste priority. After that priority path falls through, resolve any partial cloud sample using the existing cloud memory/stationary per-cell DOWN/UP procedure, and reject incomplete boards before colour counts, combo decisions or solver calls. Existing stable-before-probe, reveal recognition, restored frames, F10, release handling and no-repeat-on-failure checks remain unchanged. This enables the already requested unavailable-skill fallback; it does not pretend binding was cleared.

While binding remains, `_background_observed` may reuse an already complete current cloud memory through a read-only resolution call. It still returns a passive partial sample when memory is incomplete, without touching the board before skill selection. Memory invalidation after skills and scene changes is retained.

Five new regressions use actual096 board pixels to check12 hidden cells, passive sampling before skills, memory reuse without touch, normalization before combo/route math, rejection of still-incomplete data, and usable cleanse before probing. Existing cloud reveal/release/cancellation and skill fallback tests are included. A historical test's resolver stub now permits the read-only lookup while continuing to forbid capture/probe before cleanse.

SDK input, capture64, timing, solver, models, acquisition-list provenance and reward accounting are unchanged. No physical MuMu is connected; stationary-touch performance and the full096 game progression require user-device verification.
