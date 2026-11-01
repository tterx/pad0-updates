# v1.0.54 / updater262

The reported delays are subjective estimates, not measured client timings. Local profiling of the saved135 frame found avoidable preparation costs:650ms between the first two board samples, up to300ms to drain condition results, duplicate classification of the same capture for cloud memory and failure evidence, repeated OCR after an already confirmed slow master read, and a scenery candidate below the actual timer-bar row.

Unstable-board sampling now uses150ms spacing and100ms queue polling, returning to650ms/250ms after two matching frames. Both matching observations and the full grid/texture/ring checks remain required. Recognition passes its same-capture classification and obscuration mask to cloud memory, and condition preparation reuses the board crop/classification returned by its diagnostic capture. These values are never reused across different captured images. A busy condition probe is polled at100ms instead of300ms.

A known-time, confirmed master preview skips the old second OCR round and directly uses the existing post-OCR current-board verification. That verification still binds board/badges, area and action/recognition epochs, refreshes spikes/bind state and enforces12-second condition/2-second final-board bounds. Unknown conditions and TPA keep their existing fresh-OCR flow. Timer candidates must overlap the detected timer-bar row, excluding lower-edge scenery before OCR while preserving independent views and conflicting aligned candidates. Explicit execution, five-attribute priority, mathematical verification and fresh pre-drag time/enemy/board/spike checks are unchanged.

Seven alternating same-process trials on private135 pixels, with the real Korean OCR model warmed and screen capture mocked, gave these median stage times in seconds:

| Stage | v1.0.53 | v1.0.54 |
| --- | ---: | ---: |
| One board sample |0.4474|0.2316|
| Preview conditions |0.8092|0.6016|
| Clicked condition scan |0.3248|0.2408|

Timer OCR calls during preview dropped from7 to3. These stage measurements exclude real capture, Windows process spawn and emulator input; they are not a claim about the user's end-to-end duration. Original images and diagnostic ZIPs remain private.

Nine focused regressions cover the two-frame gate, adaptive cadence, same-capture reuse, retained failed-condition retry, confirmed read delivery, changed current board rejection and timer candidate conflicts. Existing delivery tests now expect one confirmed OCR pass plus the mandatory fresh board capture. The bundled runtime also replays private135 pixels with actual Korean OCR and verifies a real five-attribute/six-combo route and fresh-execution rejection.
