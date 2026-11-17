# v1.0.122 / updater331 — hide master route HUD on level-up confirmation

The supplied screenshot visibly shows the game page title `레벨 업 확인` while
master route controls remain visible. Code inspection found two omissions: the
existing two-reading page-title allowlist did not accept compact `레벨업확인`, and
the combat-display exit list did not include that page. A confirmed combat phase
therefore survived this definite noncombat screen. This is a display correction
within the user's master_multicolor scope, with existing merge/publication and
automatic-update authorization. All work runs in Codex; no Actions are started.

The passive master display worker explicitly enables the level-up title in the
existing reader. Two normalized readings must match with confidence at least.82;
unknown, inconsistent, low-confidence or single readings are not accepted. The
reader uses a separate master-display cache so this extra title can neither leak
into ordinary navigation/other modes nor be shadowed by their cached readings.
Ordinary callers keep their previous title results. No Tk state is read by the
worker to select this policy; it uses the existing captured master flag.

The existing owned queue carries the heading, actual capture time, game context
and stop epoch. A current fresh level-up confirmation ends the combat display,
invalidates the old route/board readiness and hides the HUD. The unchanged slow-OCR
recapture rule requires identical actual title pixels in a new capture. Stale,
foreign, retired and old-epoch results cannot end a current battle. Fresh title
results also work after F10 without resuming actions. A fresh positive puzzle
can start the next combat display normally. This does not add a timeout or hide
buttons merely because a battle frame/skill animation cannot be read.

Ten regressions use the real title reader, controlled OCR readings on synthetic
header strokes, the real passive worker and owned UI queue. They cover normalized
two-reading acceptance, ordinary-mode rejection, weak/disagreeing/single/unknown
readings, cache separation both ways, level-up exit, F10, slow recapture with equal
or changed title pixels, invalid message ownership and fresh puzzle return.
The supplied screenshot itself is visually inspected; it is not available as a
local image file, and these tests are not a physical Windows OCR/play claim.
Existing combat, taskbar, master presence/context, manual workflow and report223
playback feedback regressions remain in the related checks. Existing worker mocks
accept the new explicit display-policy keyword; their assertions are unchanged.

No changes to solver, maximum-combo policy, input delivery or occlusion proof,
models, party, stage identities, branch data, installer or launcher. The v121
visible disabled HUD during actual manual mouse movement remains. F9, capture
hiding and native taskbar exclusion are unchanged. An explicitly enabled diagnostic
always-visible setting remains the existing user-controlled exception. Local and
bundled-source results, hashes and actual download/updater preservation checks
are recorded in release_checks331.json. Windows/MuMu hardware is unverified.

Validation: source 7362938cf34034752fa60a79d4795a2ab2349438;173 related tests in11 suites,94 bundled tests, no failures/errors/skips. Ten new regressions. No Actions executed.
