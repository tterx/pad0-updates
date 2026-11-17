# v1.0.121 / updater330 — report223 and visible manual movement feedback

The user reports223 and asks that executing a prepared manual route retain its
route-calculation HUD during automatic mouse movement. Scope is master_multicolor;
existing publication/automatic-update authorization remains. All work runs locally
in Codex, with no Actions. Physical Windows shell/MuMu play is unverified.

Report223 failed before UP, not because its complete path was proved wrong. In
three held frames the worker read stationary cell1 as water instead of jammer;
all other stationary cells matched. The water held at final cell7 floats upward,
covering part of the jammer. The source of that stationary jammer is initial
cell0 after the actual path permutation. Reading with the bootstrap model still
makes this particular occlusion error; simply loading that model is not a fix.
The released screenshot independently reads the expected complete final layout.
This does not turn the original failed dispatch into a successful input record.

A scoped, copied pre-DOWN baseline permits a read-only source-texture fallback
only for mismatching stationary cells adjacent to the held final cell. The
baseline's independent full-board classification must exactly match the gesture
context. A fixed central-disc/held-sprite geometric mask, independent of the
expected and observed colours, leaves25–97% visible; source and current texture
must agree in full, left and right visible regions (at least100 pixels each on
64px tiles, correlation>=.95, RGB MAE<=16, texture deviation>=8). A bounded
plus/minus.06-cell translation accounts for orb wobble. Report223 leaves43.92%
visible, with correlations .97519/.97794/.96757 and MAE9.759/11.833/7.498.
The original observation and raw mismatches are preserved alongside the physical
proof and verified labels. No neighbouring cell is simply ignored. Two fresh
matching stationary observations remain required. The original baseline is
classified once per confirmation, not once per frame; other gestures never reuse
that cache. Missing/foreign baseline, wrong texture, unrelated mismatches, expired
budget, cancellation and game advancement before UP remain rejected.

The downstream partial-held-board path uses the same source proof but still
requires complete recent DOWN/MOVE/UP trace, dispatch and release acknowledgments,
and the physical lifted sprite. It returns the existing lifted-final-orb reason
so the caller still demands post-UP game reaction. Actual223 diagnostics retain
false dispatch and remain rejected; the delivered-input regression is an explicit
counterfactual with acknowledged dispatch, not a claim of original success.
No input scheduling, path, speed, extra move or repress is added. Cloud handling
and nonmaster decisions retain their previous behavior.

The manual HUD stays hidden for preflight captures and validation. Just before
the mouse worker starts, it is positioned outside the board and timer, shown
without taskbar registration or focus activation, and its action/error-save
buttons are disabled. Its grip cannot move it over the input path. F9 and capture
hiding still work. Completion restores the prior widget states through UI-session
teardown; F10 also clears feedback and restores states. The combat display latch
continues until a fresh result/exit. Farming and other modes do not enter this
manual feedback path. If no safe HUD geometry exists it remains hidden rather
than intercepting input or contaminating the board/timer capture.

Eighteen new regressions exercise the actual cropped223 baseline/held/released
board data, two stable proof reads, failed original dispatch, delivered-input
simulation, release/trace failures, seven wrong replacement kinds, unrelated wrong
cells, invalid baselines, worker cache/cancel/budget, report222 early attack,
scoped baseline preparation, real Tk visibility/disabled actions/grip, restoration,
F9/capture, unsafe geometry and mode/farming isolation. Related suite results and
packaged-code tests are recorded in release_checks330.json. Cropped fixtures omit
PC paths and character UI. Six UI lifecycle methods move into the compressed
manual-controls mixin to retain the unchanged launcher2MiB limit; begin/countdown/finish/finish-queue/
restore behavior is unchanged and poll adds only feedback cleanup. Solver, learned
model, party, crystal/stage data, launcher and installer are unchanged. Final
publication verifies the real immutable ZIP and unchanged updater's preservation
of settings, inventory and distinct starred/normal/hidden branch records.

Validation: source e6983f230e08ab5ef9ee2edca907875c117eaf0b;377 related tests in32 suites,100 bundled tests, no failures/errors/skips. Eighteen new regressions. No Actions executed.
