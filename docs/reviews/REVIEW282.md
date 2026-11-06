# v1.0.74 / updater282 — SDK corner consumption

Report151 stops during the held-board gate, after one DOWN and all 92 MOVE
requests. UP cleanup completes. Three held-grid reads disagree at the same two
stationary cells, and the held screenshot visibly agrees with those reads.
This is a partial route execution, not an unsupported menu or a missing solver
result. Either of two omitted turns produces the same grid; the archive cannot
identify which turn the game missed. Native job completion is not a game ACK.

For a feedback-verified MuMu SDK route, effective corner dwell is at least
62.5ms. Straight sample intervals, slower requested turns, MaaTouch cadence,
single-point UI taps/probes, coordinates and controller ownership are preserved.
Planning and pre-DOWN time reservations include the same effective dwell.
The existing fixed capture/start/release reserve remains at least 1.25s. SDK
per-edge host margin is 5ms (report151's observed maximum is below 3ms), rather
than charging another 20ms on every edge. This keeps its valid 23-step route
inside the four-second budget; charging both would reduce the search to 20
steps, where short replay searches failed to meet the combo goal.
Stored user timing is retained, with requested/effective timing in diagnostics.
No mid-path capture or corrective/repeated gesture is added. The exact held
grid proof still rejects a persistent mismatch and releases the original touch.

The minimum is a conservative mitigation for game-side missed corners, not a
proven timing threshold for every emulator frame rate. Private original-pixel
replay verifies the mismatch and both possible skipped turns. Fake-clock tests
verify dispatch intervals, matching budgets and strict rejection, without
claiming physical MuMu playback. Original images, archives, local PC paths and
input traces are excluded from distribution.
