# v1.0.108 / updater316 — open detail rejected by its small skill label

The user now shows a genuinely open creature detail (No.463, Lv40, turn4), while
registration says "4번 상세 화면을 확인하지 못했습니다" and creates no error file.
That error is the first guard in _party_read_detail: the whole-frame OCR must
return exactly 스킬/액티브스킬/skill at>=.82. A small cyan/graphic heading can miss
that guard although its creature number and skill body are visible. This is a
different path from107's corrected card crop/press geometry; those changes remain.
The current inline screenshot has no local file, so its original OCR tokens have
not been replayed. The exact rejected tokens become available with the new ZIP.

For master registration, retain the existing strong whole-frame label path.
When absent, isolate actual cyan text pixels in the skill-heading area and read
both enlarged colour and black-on-white mask views. Both must recognize the
supported full label at>=.90. Limited graphic dropdown suffixes are allowed;
team/awakening/other words, missing cyan ink, low confidence and conflicting
views fail closed. Both fresh detail frames must satisfy that guard, and their
creature numbers must still agree with the original strict two-read check.
Expected slot or cached character number is never substituted. The canonical
heading token lets the existing bounded body reader continue to exclude the turn
header and stop at stat labels. Existing other-mode recognition stays unchanged.

A real cyan heading cropped from saved211 game artwork was independently read
with the production Korean RapidOCR recognizer after both transformations:
스킬 .99987 and 스킬 .99992. This validates the same game font, not the missing
original current screenshot. Positive full-profile tests use controlled number,
level and body tokens plus actual game heading pixels. No physical MuMu input
or successful live No.463 registration is claimed.

The missing ZIP is a definite wiring defect: the manual worker formats failures
as "파티 정보 읽기 중지", then only archives messages starting with "재료 파밍 중지".
Master manual registration now initializes a fresh operation snapshot, records
current detail frames and all whole/focused OCR evidence, attempts touch/capture
cleanup first, then writes the existing error archive from cached pixels. The
status contains the real ZIP path. An I/O failure explicitly reports "오류 저장
실패". Cleanup failures are recorded as attempted/unsuccessful, never as released.
A connect failure cannot borrow an older run's images. First and independent
second detail PNGs, slot, OCR scores/boxes, controller trace and cleanup result
are available when captured. No extra capture or game input occurs while saving.

The worker carries its initiating app/epoch and restores thread-local state.
Its UI results retain that epoch through consumption, so an old queued profile
or completion cannot overwrite a resumed operation after F10. User cancellation
and superseded operations do not produce false failure archives. Other modes
retain their previous worker/result/archive behavior. Confirmed earlier slots,
user corrections,107 geometry, large portrait identity thresholds, maximum combos,
eight-drop learning, branch memory, EX ranking and separate stage IDs remain.

To fit the launcher's2 MiB source limit, generated packaging additionally removes
blank lines and irrelevant indentation inside implicit bracket continuations.
All string/byte/f-string literal content, license notices and the complete
executable AST must remain identical, enforced at build time. Development files
and release_comments' line-preserving contract remain unchanged. Tests verify
multiline literal values and control flow; the exact final bundle is tested too.

Twenty-seven new tests cover label recovery across all six slots, fresh number
agreement/missing/conflicting IDs, absent/low/disagreeing/incorrect labels, second
frame rejection, original strong labels and other-mode controls; actual manual
worker archives, PNG/JSON evidence, cleanup order, I/O errors, no stale images,
cancellation, stale/current UI delivery owned cancelled cleanup without clearing a newer device, and thread-local restoration; and
semantics-preserving generated compaction. The game-art fixture contains only a
small heading, with no full uploaded desktop. Current screenshot OCR and physical
MuMu registration remain unverified. After updating, return the game to Team or
Entry Confirmation before reopening automatic party registration.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43802417626 (success; source d5f0a694d27e42e169f2366eec2beab592dfb943); 1,437 executions, 136 suites. Exact package source SHA256 matches.

[Public replay comparison](../../releases/v1.0.108/replay316.json).
