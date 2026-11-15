# v1.0.106 / updater314 — learned correction for every drop kind

The v105 report218 start is a visibly lifted thorned Green at cell0, not an input
budget failure. Four saved checks reject it; that contact sends one DOWN, no
route MOVEs, then UP. The previous special thorn correction only handled Purple
and Yellow. The general spatial check also fails on the translucent background.

A frozen, genuinely trained appearance model now covers Fire, Water, Wood, Light,
Dark, Heart, Jammer and Poison, with plain, lock, thorn and lock+thorn artwork.
It is a small 148-feature, 24-hidden-unit, nine-class tanh model (including unknown),
trained offline with Adam for900 epochs on each of three fixed seeds. Selection
uses validation performance; shipping stores float16 weights, not an online
self-labeling process. `tools/train_all_drops314.py` reproduces the model from
reviewed real orb crops and independently hand-traced physical overlay artwork.
The new poison strip contains only cropped orb art, with per-tile hashes/labels;
no full uploaded desktop or report archive is committed. Augmentation varies
background, scale, shift, brightness, saturation, resolution, blur and inner crop.

All augmentations of an original source tile stay in one split, checked by SHA256.
Some normal source screens contribute different tiles to multiple splits, so this
is not a whole-screen-independent dataset. Earlier poison test results informed
augmentation work; the public test set is a regression set, not a pristine blind
trial. Report218 is excluded from fitting and calibration. Training has18,520
examples; validation1,776/1,776 correct and1,424 accepted; regression test1,398/1,400
correct and1,219 accepted. Two uncertain jammer classifications abstain. Neither
validation nor test has an incorrect accepted classification at .995. Source,
augmentation and split metadata, validation/test feature tensors and frozen model
hash are supplied in DROP_LEARNING314.json and tests/fixtures/drop314_*.
These figures describe this dataset, not an accuracy guarantee on unseen gameplay.

Only master_multicolor uses the learned corrections. Two differently sized views
of the actual tile must agree at >=.995 before correcting its kind; uncertainty
retains the existing reader. ComboOrb attributes survive correction. The code
never uses an expected route label as a training or classification input.

The common start proof identifies the source using both learned views, then
requires new pixels above the intended cell and a bounded source-conditioned
alpha/texture match: correlation>=.90, residual<=.08, coverage>=.25, and texture
in at least three half-planes. A confident reconstructed kind may not contradict
the source; an uncertain reconstruction additionally needs the independent
visible-art reader. It does not lower the learned source threshold to accept a
translucent composite. Moving locks must be present in both raw selected art and
the reconstruction; thorns must remain physically visible; jammers retain the
existing two complete newly appearing eyes. At least20 remote cells must remain
stable. Existing start methods remain available when the new proof abstains.
Synthetic positive tests cover the complete product path for8 kinds x4 states x3
positions; they do not claim the new method alone handles every overlay/pose.

The actual saved218 pixels now pass: learned source G .99919/.99826, correlation
.90587, residual.05122, alpha.72719, coverage.51210, physical selected thorn and26
stable remote cells. Selected reconstruction confidence .88341 abstains; independent
visible-art recognition supplies G instead. This distinction is retained in proof
metadata. No extra DOWN, recovery click, input speed, usable budget, final-board
check, release condition or maximum-combo objective changes.

For the F10 display path, fresh captures of a learned puzzle first use the small
model, avoiding the full board recognizer in the serial presence worker. At least
27 of30 tiles need agreeing strong full/inner readings and two-axis texture.
Uncertain/cloud frames retain the previous fallback. Capture timestamps are not
renewed on completion; the1.2-second stale-frame gate, bounded worker ownership,
mode/epoch/area/focus/occlusion/menu/F9 guards remain. A deferred worker test
repeatedly collects real fast proofs; an expensive legacy classifier is forbidden
in the positive path. A deliberately1.4-second processing delay still expires.
Noise, colour noise, simple discs, squares, rows and menus fail the learned gate.
This reduces a reproduced source of display delays; physical MuMu F10 behavior
and the original post-F10 desktop sequence are not verified by these tests.

New checks:39 executions across three suites (25 new methods plus14 inherited
presence-gate cases). They verify frozen fit/splits, all kinds and overlays,
resampling, wrong kinds/sprites, absent moving locks, no lift, brightness,
neighbours, remote changes, one DOWN / MOVEs / owned UP, and cancellation/budget
expiry with zero MOVEs. Initial local failures were test fixture mistakes
(negative sample number218 mistaken for report218, a legacy positive bypassing the
intended new-proof control, and asserting a non-mocked action function). Corrected
fixtures test the intended boundaries; failed intermediate runs are not pass proof.

Other modes, branch receipt learning, non-EX preference, starred/normal/Hidden
stage identities, farming/name data, settings and inventory remain unchanged.
Windows and exact-bundle checks and actual-pixel/mock-controller results are
recorded in the release verification metadata. No physical game input is executed
by the validation or updater installation checks.

The first full Windows run43774511494 reached the old timer-fallback suite and
failed its manual-condition test: it mocked classify_board to a synthetic ATTACK
board while supplying an unrelated reported_0 desktop. The new real-pixel
correction properly notices the discrepancy, so the worker rejects that board.
That test isolates condition/spike propagation, not visual recognition; its
fixture now supplies the intended observation at classify_mode_board and asserts
the master flag. All real-pixel model and stale-board rejection tests remain.
The product source and frozen model are unchanged by this fixture correction.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43775287737 (success; source e6b22b8f3b902928fceef45b4446dbdcd9798b73); 1,389 executions, 130 suites. Exact package source SHA256 matches.

[Public replay comparison](../../releases/v1.0.106/replay314.json).

Publication verified: PR17 merged as 652baa23a72795c64958b0ac4ae770c1e66e1bb4;
its tree matches the Windows-tested head. Publisher run 43776557118 succeeded.
The actual public stable latest v1.0.106 ZIP passed SHA256, CRC, version and updater-state
checks. Its source equals the live immutable raw payload. Root update.json points to314.
The genuine v1.0.105 launcher installed314 from the live manifest in an isolated
installation, preserved its backup, settings, inventory and stage/branch-history
markers, then skipped a repeat update. No PAD0 or physical game input was executed.
