# 1.0.20 / updater228 — report104 thorned start and manual risk-mask consistency

## Automatic stop reproduced

Report104 recognizes8 seconds, builds a14-move plan with four direct combos plus two certified existing-orb gravity combos, and stops before any MOVE. The final trace contains one DOWN and one UP; SDK release completion is recorded, but it does not independently establish the game's post-release state. The normal4-second input allowance and selected controller/timing are not the failing prerequisite.

The intended start is cell19, a thorned purple orb. Its small selected centre reads S because the grey X covers the centre and the selected sprite is translucent. The old generic texture/colour proof rejects all four captures. Replaying the supplied before/held board pixels reproduced that rejection; the central reader remains S in the new regression, so the correction does not hide the original mismatch by relabelling the whole board.

A narrow alternative proof applies only after the existing lift checks fail on an originally verified P with physical curved-thorn evidence. It fits the selected sprite over the actual background at the candidate lifted pose: `observed - background = alpha * (source - background)`. Opacity must be .35–1.02, normalized residual at most .10, channel-centred texture correlation at least .85, grayscale texture correlation at least .80, and spatial grayscale variation must remain substantial. Reconstructed artwork must be P with a sufficient purple/pink ratio; physical thorns must independently be present in both source and selected sprites. Source change, changed pixels above the source and remote-board stability still apply. This is a local input-start proof, not a change to poison classification or an assertion based on the planned label alone.

On the actual104 crops the fit has alpha .72952, residual .07920, texture correlation .89045, grayscale correlation .88230 and21 stable remote cells. The current frame can qualify without another DOWN. `THORN_START228.json` records this offline proof and crop hashes. Fake controller regression confirms one DOWN → positive real-pixel proof → MOVE → UP with the recorded .017/.0295 timing configuration. It is a scripted controller test, not an Android/game ACK or a connected physical MuMu trial. Static/brightness-only frames, wrong type/source cell, real skull/heart replacements, solid purple/pointer shapes and changed remote cells remain rejected. Other start alternatives and their thresholds are unchanged.

## Manual path found but rejected

The screenshot shows the same thorn board at full HP. The precomputed search can legitimately relax known-thorn ranking at freshly confirmed high HP, but the click-time cache gate compared the raw physical thorn flags before examining the mask actually used by that search. Desktop/connected capture differences or display-only detector variation therefore rejected a route whose searched risk conditions were unchanged.

Cache validation now checks the current relevant risk mask against the search mask recorded in the plan. It accepts equivalent/subset risk, retains fresh physical flags and updates the per-board fallback binding. A low/stale HP observation, newly relevant thorn or unconfirmed cloud hazard still requires new route preparation. Actual orb changes, party/board geometry, timer/bonus and path replay remain independently checked. The final mouse pre-drag check refreshes HP and applies the same condition instead of silently restoring old display flags.

The preview receiver also validates the explicit execution goals and cap produced by the headless worker. Normal master plans retain certified existing-orb gravity validation. Explicit timer-fallback rainbow/six-only plans can reach the manual button; unrequested weaker targets and invalid paths cannot. The worker certifies its actual expanded search cap rather than its earlier reserve-limited cap.

## Validation and scope

New tests replay anonymized104 board crops and the actual route, exercise the unchanged controller timing and one-DOWN behavior, high-HP raw-flag variation, low/stale HP, unknown hazards, fresh flag rebinding, preview target handling, and the final manual pre-drag gate. Full Linux discovery:266 tests,265 passed,1 official Windows DLL check skipped,0 failures/errors. Windows, package, release-download and live-updater checks are recorded in distribution `release_checks228.json`.

No movement pacing, input backend, generated models, personal-data policy or timer OCR was replaced. Local learning/data recording and the AI button stay removed. Full physical MuMu gameplay remains unverified in this development environment. The purpose is to correct two reproduced gates without bypassing actual input/layout/release verification.
