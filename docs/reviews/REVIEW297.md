# v1.0.89 / updater297 — defeat results without a crystal page

Report161 confirms TIPS/OK/HP0 and a delivered defeat acknowledgement. The last
inputs are the defeat OK followed by a result Next. The worker then stops at the
uncredited-replay guard. failure.png still contains an older TIPS image, while
receipt.png/its trace belong to an earlier credited result. Those stale images
cannot establish whether a crystal page followed this particular defeat; the user
did not observe the intermediate screens. Do not invent or backfill their drops.

Handle the explicit defeat → acknowledged white mana result → stable replay
sequence when no gold crystal page appeared. Require current-run defeat proof,
the same session's successful white-result Next, two stable replay captures and
visible paired replay controls. An ordinary result, another session, an unread
lobby or any observed gold page cannot authorize this path. Keep the existing
crystal collection and identity checks when gold appears.

Commit a verified empty receipt through the existing atomic/idempotent credit
function. Preserve stock, gained quantities and saved F7 deficits, retain this
run's evidence and resume the same dungeon. Never reconcile live inventory or
count a failed finite repeat as a completion. Block board/animation preparation
after a confirmed defeat until replay is accepted, preventing residual battle
pixels from opening another reward session or triggering a battle action.

Cache the latest owned worker capture and observed result heading for failures,
while retaining the original defeat OCR proof and last credited receipt separately.
Extract the unchanged mana reader into the embedded battle helper to stay within
the old launcher's2MiB single-source update limit; keep the same callbacks and
white-page/number recognition rules.

Sixteen regressions cover same-session proof, stable controls, missing/weak mana,
seen-gold rejection, stale sessions, changed targets, bounded waits/F10, atomic/idempotent empty
credit, unchanged deficits, no-ledger repeats, full zero-then-real-drop farming,
normal gold collection, no post-defeat board probe and latest-frame ZIP evidence.
The private replay uses actual161 defeat pixels and real Korean OCR. Generated
post-defeat screens reproduce the old guard and verify the corrected flow; the
original missing result/lobby screens and physical MuMu input remain unverified.
Original reports, screenshots, ledgers, local PC paths and input traces are excluded
from publication.
