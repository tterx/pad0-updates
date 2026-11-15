# v1.0.100 / updater308

210 shows30 locked drops, no usable unlock, eleven seconds, enemy floor9 and
bonus2. It fails in background_search with the calculation-wait limit, rather
than refusing locked movement. Both saved crops classify to the reported board,
pass the real puzzle-presence checks and detect30 locks. A real search finds a
verified five-attribute7+2-combo route within48 moves. Locked drops can still move.
The original intervening revalidation frames and F10/manual button sequence were
not included, so the exact original retry trigger is not claimed as reproduced.

Master-only turn-local plan reuse prevents repeated final recaptures from burning
the search ceiling for an identical verified plan. Key the board and combo-orb
metadata, search hazards, goals, move/time limits, lock presence and fallback/start
cell policy. Store only a replay-verified immutable path and its actual execution
goals; reuse still replays it and passes the existing complete fresh gate. A new
board, timer, enemy floor or other search input cannot use an old entry. Skill
casts invalidate entries. Do not reduce combos or force healing. A new input with
no remaining search budget yields without input to the existing bounded outer
worker/stall checks. Other modes retain their existing search and lock-count guard.

When no unlock effect is registered or this decision confirms it unavailable, a positive-to-positive lock
count change alone cannot invalidate a master swap route: locks block conversion,
not movement. Full lock presence changes and every board, spike, timer, combo,
status, cancellation, direction, gesture-budget and release check remain. Ready
unlock still takes priority before path search. Save final revalidation retry
reasons and plan-reuse counts in reports instead of losing that history when the
next search snapshot replaces the previous one.

The master button probe now dates its evidence at actual pixel capture, so thread
startup/capture waiting is not mistaken for old pixels. Completion time does not
make an old frame fresh; keep the1.2s image-age limit. Supersede an unfinished
probe whose epoch, mode, area or focus-generation is obsolete, allowing F10's new
manual state to start a current probe. Late old-ticket results cannot replace it.
Menu negatives, missing captures, focus/occlusion, F9 and pointer guards remain.
The saved locked crops already pass presence classification; do not loosen the
puzzle/menu thresholds on that basis.

Seventeen new regressions cover unavailable/available unlock, lock presence,
six transient final reads with one search and a fresh final verification,
changed board/floor/time, cancellation and unchanged other-mode behavior; delayed
capture versus stale pixels, obsolete inflight probes and real F10 state handoff,
and immediate menu hiding. Device inputs are mocked. A controlled replay uses
the actual210 board and a real verified route, injecting transient final reads:
v1.0.99 reaches its search ceiling; the fixed path searches once and reuses the
route. This is a labelled simulation, not the missing original frame sequence.
Private images, traces and reports are excluded from public source/releases.
Local checks, exact ZIP/Windows source hashes and live updater verification are
recorded in the release repository. Physical MuMu farming completion and the
original unrecorded manual flicker sequence remain unverified.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43691300686 (success; source cc91b71c53886d9e46a88114f08d452ef1a64b58); 1,253 executions, 120 suites. Exact package source SHA256 matches.
