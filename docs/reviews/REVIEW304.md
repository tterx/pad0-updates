# v1.0.96 / updater304

The user's report concerns screens inside the game where the puzzle is absent.
The old button gate retained a recent board for four seconds and a matching ready
route indefinitely. Board sampling pauses during calculation and route display,
so a cached route alone could continue to permit the HUD on a menu/result screen.
The existing ring-colour detector also accepts a generated gold menu with flat
horizontal rows; this is a reproducible negative control, not the user's screenshot.

Only master multicolor (달인다속 / mode9) now requires independent, current puzzle
presence evidence for its button. A single background capture worker uses the
existing full-grid/cloud visual proof plus texture in both axes of visible cells.
It runs independently of route calculation, OCR and route display; it performs no
Tk calls, input, solver work or route-cache mutation. A negative or expired sample,
occlusion, changed coordinates or stop epoch suppresses presentation. Late results
from before a focus change cannot re-show the button. A generation counter rather
than clock resolution guards that transition. Polling is bounded;
this is not instantaneous screen transition detection. A stalled worker cannot multiply
captures, and its positive proof expires after 1.2 seconds from capture start.

Prepared routes remain cached. Visibility does not grant input or automatic
execution. Existing pointer-down/drag protection and F9 hiding remain. Other modes
keep the previous button behavior and do not run the new presence worker. Shared
grid classifiers, solver objectives, input timing and farming are unchanged.

Regression checks cover cached-route/menu transitions during idle, calculation and
playback, asynchronous publication, stale/late/wrong-context proofs, capture/start
failures, single-worker bounds, capture restoration, F9, pointer protection and
other-mode isolation. Existing normal, spike, badge and cloud board fixtures are
positive controls; generated neutral/dark/gold menu panels are negative controls.
Physical MuMu transitions and the user's exact unseen screen are not verified.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43646177821 (success, source head 3e436a8e3017ffacaeaebc3181bbbe13d1a2a45c); 1,178 test executions, 115 suites. The portable source SHA256 matches.

Publication verified: PR7 merged as 8ee808178a3bd101c4ffcd2fb62be40b86aa1c0c;
its tree matches the Windows-tested head. Publisher run 43646999889 succeeded.
The public stable latest v1.0.96 ZIP passed SHA256, CRC, version and updater-state
checks. The live raw source matches the ZIP. Root update.json now points to304.
The genuine v1.0.95 launcher installed304 from the live manifest in an isolated
installation, preserved the source backup, configuration and personal-data markers,
and skipped a repeated update. PAD0/game input was not executed by this check.
Two earlier-upload entry screens were rejected and two puzzle crops accepted by
the new presence worker. Those private images are excluded from publication.
