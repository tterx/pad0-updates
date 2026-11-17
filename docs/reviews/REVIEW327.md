# v1.0.118 / updater327 — master UI polling and delayed entry evidence

The user reports that switching from the game back to PAD0 leaves the main window
black while its widgets appear one at a time, and that entry-screen controls were
missing before playing. No new capture or runtime log was supplied. The exact
physical Windows rendering cause remains unconfirmed.

A measured UI polling defect exists in `selected_goals`: even when the user has
explicitly selected master multicolor, every call merges all party profiles and
compares their awakening icons to infer TPA attributes, then discards the result.
Multiple presentation, inventory, presence and screen callbacks call this function
repeatedly on Tk's event thread. With six registered profiles and eighteen
verified awakening icons, twenty queries took0.453seconds in the controlled local
profile. After skipping that unused inference for explicitly selected mode9, the
same queries took0.000120seconds. This is synthetic registered-party evidence,
not a measurement of the user's machine. The result is unchanged and no cached
party inference can become stale: explicit selection simply needs no inference.
Implicit/legacy selection and every other mode keep their original policy.

A separate entry-display failure is reproduced with2.6seconds of title OCR.
The positive result carries pixels captured before OCR, so the2.5-second display
expiry hides the controls immediately after delivery. Master-owned passive entry
probes now take one additional screenshot if the initial pixels are at least
0.75seconds old. They reuse the recognized title only if every RGB pixel of the
actual title rectangle still matches. The timestamp belongs to that new capture;
it is never refreshed on old pixels. Changed titles, failed captures and retired
stop/mode tickets cannot grant entry display. Fast probes still use one capture.
Other modes and legacy direct callers retain their original behavior. All entry
clicks still run the existing stage, party and input validation, including the
separate identities of starred and normal stages.

Ten regressions cover no party scanning during explicit master polling, current
mode/legacy/TPA policy, real Tk restoration and widget mapping, delayed entry
recapture and real queue-to-button delivery, changed titles, fast single capture,
F10 invalidation, and unchanged other-mode/direct-caller paths. Related mode,
manual-control, native ownership, paint, stage and final-input suites run locally
in separate processes. The actual bundled code is also tested. No GitHub Actions
run is requested; push checks remain disabled. Physical Windows/MuMu play remains
unverified. The solver, models, input modules, combo objective and personal data
are unchanged. Native calls that remain blocked are still subject to the existing
bounded job policy; this patch does not kill Python threads or bypass visibility.

Validation: source 243a219fd956d182fe778802b2c98dbfe670f255;228 related tests in20 suites,44 bundled tests, no failures/errors/skips. New10 regressions:4 failed on v117, all passed after fix. No Actions executed.
