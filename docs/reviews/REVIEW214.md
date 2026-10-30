# v1.0.6 / updater214 — reported088 skipped corner

## Evidence

088 ran v1.0.5, SDK input8 through the existing Maa controller, enhanced capture64. The requested start was visibly lifted and all108 MOVE requests were present. PC request gaps were approximately17.26 /17.41 /30.20ms (minimum/median/maximum); a completed native job does not prove Android/game processing.

Three before-UP captures had exactly the same two differences: cell2 expectedWater/observedHeart and cell9 expectedHeart/observedWater (zero-based cells). Replaying the captured initial board and path, omitting path index6 (cell8, row2/column3), exactly reproduces the actual board. Among single intermediate waypoint omissions this explanation is unique. The requested segment was9→8→14. Final held orb classification cannot explain the two remote cells. The first game frame agrees with the recorded initial board.

The data demonstrate a rendered route mismatch consistent with a missed corner, despite complete host submissions. They do not measure Android event times or establish whether SDK delivery, emulator scheduling, game polling or animation timing lost the intermediate point.

## Changes

- `NativeSDKInputMixin.play_input`: after arriving at a direction-change center, before issuing the next edge, finish its pending MOVE and ask the input owner for read-only game evidence. Keep one DOWN, four linear samples per edge, every exact center and the original UP. Never replay a MOVE, send another DOWN, change backend or burst delayed events.
- `_MaaNativeCapture.confirm_route_corner`: use the same controller's enhanced screenshot and raw-size pixel coordinates to read the vacated previous cell, rather than the held/occluded destination. Two freshly requested frames must agree on the expected vacated colour. Poll only within a120ms allowance, with a cancellable12ms read spacing. Confirmed results continue immediately; there is no fixed120ms dwell.
- Skip only proof that cannot distinguish an exchange: equal colours, cloud-covered cells, nuisance targets unsupported by this fast reader, or downward movement whose floating sprite overlaps the vacated cell. These remain subject to the unchanged strict final board/release verifier. This is not a proof for every intermediate Android event.
- Reading occurs locally on a small native BGR tile; it does not perform a full-frame resize, board AI inference or OCR per corner. A fresh capture request does not guarantee two different Android render timestamps; it proves two observed outputs, and logs them as such.
- An unconfirmed observable swap stops before the next edge and releases the original contact. Capture failure, resolution change and F10 also release normally. This may stop earlier on an ambiguous or slow animation, rather than complete a bad route.
- Include the worst-case120ms per potential corner in gesture preflight and conservatively per edge in solver move capacity, plus the existing20ms margin. Saved speed/corner settings remain untouched. The solver algorithm is unchanged; its time-limited path length can become shorter to reserve feedback time. No performance claim without physical playback.
- Error ZIP diagnostics carry `corner_verification` (cells, expected/old/observed colours, raw pixel box, observations, request times, reason, duration), its reserve, and the retained `last_drag_timing`. Timings are PC-side, never labelled Android execution timestamps.

## Scope / validation

Preserve fixed SDK input8 + existing skill controller, enhanced capture64, the B/J specialist, skill priority/fallback, replay selection, saved preferences, final board proof, F10 and release cleanup. No automatic speed tester, emulator repetition, backend switch, model retraining or full test-suite run was added.

Focused developer regressions use actual088 before/held board pixels and recorded path, plus a scripted controller and fake clock. They check the exact skipped-corner explanation, raw coordinate conversion, two-frame agreement, stale/wrong readings, unobservable cases, capture failure, cancellation, changed resolution, continuous contact/all centers/no bursts, early stop and timing preflight. Windows CI exercises official DLL/controller construction and real Tk; no user MuMu is connected to this Linux environment.

Real-device check: exit PAD0, restart `시작.vbs`, keep the saved speed, and run auto-farming once. Check smoothness and correct turns/skills/replay. If it stops, supply the new ZIP; the recorded corner observations should distinguish a rendered swap from host delivery. It remains unconfirmed whether this prevents all game-side skipped corners; real animation cost and any false timeout need that run.
