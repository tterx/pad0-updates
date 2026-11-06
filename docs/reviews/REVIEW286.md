# v1.0.78 / updater286 — material OCR contention

The user supplied a creation confirmation screenshot for Minaka and a status
showing `RuntimeError: OCR 엔진이 이전 작업을 처리 중입니다.`. This is an
inference-lock rejection, not an instruction to restore a saved farming task.
The F7 worker waited only for the material-button title probe; other OCR users
could hold the shared inference lock or take it between consecutive quantity
reads. The nonblocking acquisition aborted F7. Its failure callback then synced
and opened the previous saved group's management panel.

F7 and its subsequent material-name/stage worker now reserve inference ownership
for their complete read sequence. An already running inference may finish;
subsequent low-priority reads yield. The owner waits in its worker, at most six
seconds per inference lock acquisition, with F10/epoch and F7 generation checks
before and immediately after acquiring the lock. Cancellation releases a newly
acquired lock without running the engine. Owner cleanup cannot erase a newer
worker's reservation. Explicit awakening registration is not preempted by F7.
Engine exceptions also release the inference lock as before.

Failed scans preserve recorded quantities and history, do not sync/reopen an old
task as a new result, and refresh an already open panel with explicit previous
saved data/current scan failed labels. All-materials-complete is suppressed for
that failed scan. Capture failures follow the same non-popup behavior.

Twelve regressions exercise a real contended Python lock, reserved gaps between
reads, all OCR entry points, timeout, stop/generation changes, engine exception,
worker replacement/cleanup, catalog failure, panel labels and awakening exclusion.
A generated five-card panel uses the screenshot's declared numbers (required
2,2,1,1,5; owned20,0,2,43,120), with mocked title/numeric OCR and icon staging;
twenty consecutive numeric reads reject competing background acquisitions and
register the new owner while preserving the old group. This is not a pixel replay
of the original screenshot, a physical MuMu reproduction, or proof which earlier
OCR caller held the user's lock. No original screenshots or input traces publish.
