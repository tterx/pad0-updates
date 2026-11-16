# v1.0.113 / updater321 — manual preparation latency

The user confirms that the button appears but inventory recognition, condition
reading and calculation preparation all feel slow. Scope remains master_multicolor.
This patch reduces competing read-only work. Solver objectives, maximum-combo
ranking, worker/search budgets, touch timing and execution validation are unchanged.

The uploaded actual board requires roughly.22 seconds for colour inference and
.13 seconds for the cloud/dark mask on this Linux machine. The parallel whole-window
bootstrap adds roughly.55 seconds including another board/mask/thorn read. Those
paths continue during ordinary manual play, even after the ROI is already proved.
The condition reader also reclassifies its crop and repeats cloud-mask work; an
unconfirmed slow condition's fresh-capture branch previously classified the same
new crop once for diagnostics and again for validation. These costs compete with
OCR and mathematical workers. They are reproduced costs, not timings measured on
the user's Windows/MuMu device.

Master manual readers now share a four-entry in-memory inference cache keyed by
SHA256 of every RGB pixel, original size, exact rectangle, stop and recognition
epochs, and active model identity. Only identical freshly captured pixels reuse
colour/hue/mask results. A changed pixel, rectangle, epoch or model requires another
full original inference. No approximate scene comparison or guessed colour is used.
Returned containers are independent; combo-badge flags survive. The same lock
coalesces simultaneous identical reads. Original images are not stored in this
cache. Every recognition tick still captures current pixels, keeps its real
capture time, and requires the existing two matching observations for readiness.
The cache supplies neither readiness, timer values, combo conditions nor game input.

A completed positive ROI proof also carries its native HWND and host bounds.
For master manual play only, the existing background scheduler can omit its
parallel full-window menu/bootstrap probe while that proof matches the current
host, rectangle, mode, stop epoch and generation and remains within the unchanged
1.2-second pixel expiry. This also clears old entry permission. Negative, expired,
missing or incomplete proof, changed host/geometry, failure hold, a retained root-tree context and cold startup
keep the original full-window discovery. F10 manual and ordinary manual use the
same rule. Other modes retain their original reads and scheduling.

Condition diagnostics and board validation reuse inference from their own exact
fresh crop; cloud reconstruction receives the already observed raw board and mask.
The old slow-condition fresh capture remains mandatory, and unknown/conflicting
conditions are never guessed. Final execution still captures and validates current
board, spikes, timer, combo conditions and release response independently. F12 adds
cache hit/miss counts for future runtime diagnosis.

Fifteen new regressions cover actual uploaded pixels, single-pixel changes,
stop/recognition/area/model changes, return-value mutation, combo badges, bounded
storage, concurrent readers, two independent captures before readiness, all eight
drop kinds with plain/lock/thorn/both artwork, current/stale/missing/negative ROI
proof, exact host identity, F10 flags, cold/moved geometry, old entry permission
and other-mode behavior. Existing maximum-combo tests remain applicable.

The existing TPA bonus/ranking quality regression now uses fixed clock ticks so
its search work is reproducible on contended CI hosts. Its full-goal, seven-combo,
bonus-mask and continued-optimization assertions remain. Dedicated runtime deadline
tests keep real clocks. Production TPA/solver code is byte/AST-identical to v112.

The read-only benchmark uses the actual report221 image. Four rounds of repeated
colour/mask preparation plus bootstrap scheduling took7.80s on v112 and.41s on
v113 when all captured pixels matched (11 hits,1 miss). Forcing one pixel to change
on every capture gave no hits and required all12 original inferences:6.77s versus
5.51s, with four duplicate full-window board reads removed. All raw colour codes,
cloud/dark masks and combo flags match. This is a serial processing-work benchmark;
it excludes OCR, math search, Tk response, physical capture and live game latency.
It does not establish an end-to-end speedup on the user's device.

Physical Windows/MuMu behavior remains unverified. The earlier report222 automatic
input interruption is outside this latency change and is not claimed resolved.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43919500460 (success; source fcd9e43cd546ca1e09eb56214723487cba507669); 1532 tests, 141 suites. Exact package source SHA256 matches. Final repeated measurements: releases/v1.0.113/latency321.json.

Publication verified: PR24 merged as 89489206674c26d202a8b2e175db87a7ffdc124e.
Its tree matches the Windows-tested head. Publisher run 43922095676 succeeded.
The actual stable latest v1.0.113 download passed SHA256, ZIP CRC, version and updater-state checks.
Its source matches the immutable payload and root update.json activates321.
The genuine v1.0.112 launcher installed321 from the live manifest in an isolated installation,
retained its backup, settings, inventory and four separate stage/branch markers,
and skipped a repeat update. No PAD0 or physical game input was executed.
Report222's exact input interruption cause remains unresolved; its stop is preserved.
