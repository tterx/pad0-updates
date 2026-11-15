# v1.0.104 / updater312 — starred acquisition label, exact stage identity

Report215 (v103) stops before entering: one acquisition medal is present, but its
stage number cannot be confirmed. The screenshot clearly shows ★7-1. Both whole
label reads return *7-1 with scores .87750/.87701, below the unchanged .90 gate.
Production RapidOCR3.10.0 on the actual saved image reproduces both scores and
the original error. No puzzle board or route is involved; contacts were released.

In an active master farming run only, two matching parseable whole-label readings
with a physically proved five-point star can request a separate number view.
The existing contour/concavity/shape proof exposes the star's actual bounding box;
cut at its exclusive right edge and keep the remaining label ink. No fixed digit
cut, selected stage or database value supplies/replaces a digit. The focused text
must exactly match both original candidates at two scales, each with score>=.90.
There are no confidence upgrades or relaxed final acceptance thresholds. Weak,
conflicting, dirty, absent-star and wrong-number results still stop. Respect F10
before/after each extra read; cache confirmed pixels separately by mode.

The star remains part of the stage ID: ★7-1 and7-1 have separate DB entries,
selection, acquisition links and branch records. The user's explicit clarification
also exposed a previous selection-binding shortcut: a normal entry header could
inherit the selected stage's star. Active master entry binding now requires an
exact base ID, including the star, before retaining the selected world's prefix.
A different observed base ID is returned to the existing picker mismatch check;
it cannot be replaced with the selected ID. Other modes retain their default path.

13 new tests cover crop geometry, genuine versus missing/clipped star, unchanged
high-confidence path, matching strong number reads, weak/conflicting/wrong/dirty
negative controls, no guesses from remembered selections, exact-frame cache with
mode ownership, F10 cancellation, real list inspection/picker and wrong entry,
plus six starred/normal/Hidden binding combinations and the real entry reader.
Existing identity, entry, acquisition and branch policy suites remain intact.

Private production OCR replay of report215 now returns exactly ★7-1 and CLEAR,
using focused scores .99966/.99963, and reuses the confirmed identical frame.
The uploaded screenshots are not published. The original stage database is absent,
so selection integration uses generated artwork and a controlled DB; the screenshot
replay performs recognition only. Actual Windows/MuMu entry remains unverified.
Maximum combos, no forced healing, drop/status/F10 fixes, branch learning and EX
ranking remain unchanged. Windows/package/public ZIP/updater checks accompany
this review in the release repository.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43727377578 (success; source 94167361580579db25eeea112cb99efc3359aca3); 1,329 executions, 125 suites. Exact package source SHA256 matches.

[Public OCR comparison](../../releases/v1.0.104/ocr_replay312.json).

Publication verified: PR15 merged as 5b1f65847cb2d0c6d89a50b8b6dac8f9373f7ad6;
its tree matches the Windows-tested head. Publisher run 43728477321 succeeded.
The actual public stable latest v1.0.104 ZIP passed SHA256, CRC, version and updater-state
checks. Its source equals the live immutable raw payload. Root update.json points to312.
The genuine v1.0.103 launcher installed312 from the live manifest in an isolated
installation, preserved its backup, settings, inventory and stage/branch-history
markers, then skipped a repeat update. No PAD0 or physical game input was executed.
