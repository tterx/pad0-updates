# 1.0.19 / updater227 — report103 timer recognition and staged execution fallback

## Evidence and recognition correction

Report103 stops after three null timer reads, despite a visible8 at the right endpoint of the cyan bar. All recorded candidate boxes were near the left clock. The large white numeric endpoint merged with the board/frame, so the old white-connected-component detector missed it and attempted OCR on the clock face.

`timer_badge_boxes` retains isolated white-badge recognition and additionally detects outlined teal digits with a dark core, nearby white background and timer-band geometry. In wide timer strips it excludes the left clock. It groups adjacent digit outlines before reading multi-digit seconds; it never votes on isolated digits as whole numbers. White framing no longer has to be disconnected from the badge. Yellow-arrow obstruction and conflicting strong readings remain unavailable rather than guessed. Broad OCR fallback is restricted to a detected numeral target.

The real report103 strip was passed through the application's actual `_restricted_drag_move_cap` and RapidOCR Korean PP-OCRv5 mobile recognizer. It returned8 seconds, move cap37, with two independent readings `(8,.9908)` and `(8,.9196)`. Candidate `[690,23,716,57]` belongs to the right numeral, not the clock. `tools/check_timer_ocr227.py` explicitly reproduces this check with real inference; `TIMER_OCR227.json` records the result. Unit regressions also exercise both image scales, clock-only rejection, connected white backgrounds, adjacent digits and conflicting high-confidence values. The OCR model is the existing packaged/runtime dependency; no personal/local model training or user sample recording was added.

## Requested fallback hierarchy

A readable normal timer still uses the existing four-second reserve when a valid route fits. An unread/short timer first gets the existing finite skill selection, including a currently ready time counter. If no useful counter is available, or a reserve-constrained route cannot be found, the worker drops the four-second reserve and searches for five attributes plus six final combos. If that route cannot be verified, it searches for six final combos only, with optional healing no longer allowed to block the six-combo stage. Confirmed combo bonuses remain applied; the fallback's floor is6 rather than an enemy attack floor. Results are replayed from the actual path, not a solver-supplied board.

Background, manual/headless and cached route execution carry the actual fallback goals. Input checks use those goals and the full readable timer window. When no timer can be read, input uses the existing bounded5-second fallback budget; diagnostics retain `timer_observed_seconds: null` and record the budget separately. Five seconds is an execution budget, not a claimed timer reading or a guarantee of the game's remaining time. Raw unknown timer state is not replaced in OCR caches or observation comparisons. Later real timer/board/status changes trigger recalculation. Search/cast limits, F10, orthogonal-path and physical input/release/layout checks remain in place. Neither unconfirmed conditions unrelated to the timer nor a route below six combos is declared verified.

The fallback does not stop merely because four seconds cannot remain. It can still stop if even a six-combo route cannot be verified, cancellation occurs, the board changes or physical input verification fails. It never silently executes an unverified fewer-than-six route.

## AI control removal

The main AI model/management button is removed. Development-trained embedded models and the v1.0.18 retirement of personal AI stores remain unchanged. No local learning or image/answer recording was restored.

## Validation

Focused timer/fallback tests cover the actual103 board reaching real search/drag with a forced unread timer, a readable short timer, an available time counter, reserve search failure with a known timer, five-colour failure despite sufficient inventory, six-only fallback, rejection when neither goal succeeds, manual/headless cache and playback acceptance, ordinary reserve preservation, and absence of the AI button. Full Linux/Windows/package/published-download/live-update results are recorded in the distribution repository's `release_checks227.json`. Actual MuMu gameplay is not connected in this development environment.
