# v1.0.114 / updater322 — manual capture wait and cold button

The user reports that v113 remains at “게임 화면 확인 중” for over ten
seconds and that the first calculation button also appears slowly. No new
condition-error ZIP is available. The label alone cannot prove whether their
Windows capture or inference is blocked. This patch fixes reproducible code
defects; physical Windows/MuMu latency and the exact reported runtime stall
remain unverified. Scope is master_multicolor only.

The v113 exact-pixel cache held one shared lock throughout colour and mask
inference. A slow reader of a different frame therefore blocked even an already
cached result for unchanged pixels. A deterministic event-controlled test primes
frame A, blocks inference of B, then requests A. It fails on v113 because A cannot
finish until B is released. The fix holds the lock only around cache metadata;
inference runs outside it. Concurrent cold misses may perform independent
inference instead of waiting. Results arriving after stop/recognition/model
changes are not inserted. Four-entry storage, complete RGB/size/rectangle/epoch/
model keys, copied containers and combo badges are retained. Every observation
still uses a new capture, its real timestamp and the two-matching-capture gate.

Condition progress previously remained at “capture” while diagnostic frame
binding performed full board inference. Master condition preparation now changes
to “퍼즐판 확인 중” immediately after capture returns, before that inference.
Other modes and health-only probes keep their original stages. This improves
diagnosis; it does not add a guessed timer or bypass condition checks.

When no board rectangle existed, the independent presence worker could not start.
Only the whole-window discovery worker, which reads the page heading first,
could supply coordinates. Master manual controls can now test the existing
normalized anchor, or the existing canonical game-layout rectangle scaled to
the current native host, without waiting for heading OCR. A candidate is never
saved or shown by geometry alone. Current ROI pixels must pass the unchanged
strict presence worker, with native foreground/occlusion checks and matching
HWND, host bounds, rectangle, stop epoch, mode and generation. Pixel proof expires
after the unchanged 1.2 seconds. Only a current positive adopts the rectangle and
wakes normal full recognition; presence grants no colour readiness or execution.
An unready F6 ticket follows that verified rectangle without extending its
deadline. F10 manual remains stopped; ordinary stopped, automatic, busy, selection
and capture sessions cannot start cold discovery. Negative or stale proof does
not install guessed geometry. Existing full-window recovery remains available.

Eleven new regressions cover blocked inference versus a cached read, late cache
insertion, capture/recognition progress, real uploaded puzzle pixels without full
colour inference or heading OCR, native scaling and saved anchors, unready F6,
negative pixels, automatic/busy guards, F10 manual, other modes, changed host and
epoch, pixel expiry and occlusion. The prior concurrent-cache regression now
requires consistent independent outputs rather than exactly one cold inference.
The baseline failed seven checks and errored on missing cold discovery; the
patched suite passes. This demonstrates the specific defects, not an end-to-end
timing guarantee on the user's device.

Maximum-combo objectives, solver budgets, input timing, final execution checks,
all-drop models and branch/receipt data are unchanged. The report222 automatic
input interruption remains unresolved and is not claimed fixed by this patch.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43926753524 (success; source dcdf1a10c4ef93c0d5ea648246de9398a03a7843); 1543 tests, 142 suites. Exact package source SHA256 matches.

Publication verified: PR25 merged as 257ed47b072e0b24471a16acebc9b73e120b8edb.
Its tree matches the Windows-tested head. Publisher run 43929742847 succeeded.
The actual stable latest v1.0.114 download passed SHA256, ZIP CRC, version and updater-state checks.
Its source matches the immutable payload and root update.json activates322.
The genuine v1.0.113 launcher installed322 from the live manifest in an isolated installation,
retained its backup, settings, inventory and four separate stage/branch markers,
and skipped a repeat update. No PAD0 or physical game input was executed.
The exact current native stall remains unconfirmed without new runtime evidence;
the cache convoy and cold discovery dependency are reproduced regression fixes.
