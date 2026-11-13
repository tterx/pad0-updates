# v1.0.93 / updater301

Reports 194 and 196 completed and credited their puzzle moves, then failed while
returning to the breakthrough owner. A short tap on a training portrait opens
the currently selected training operation (the limit-break confirmation in both
reports). Open character detail with one stationary 4.2-second hold instead,
then require the existing exact name/number checks. Both the legacy single-card
and saved-number multi-card routes use the same helper. Search-query proof,
stable portraits, candidate limits and receipt ownership remain required.

SDK stationary UI holds now send one DOWN, wait the requested elapsed time and
release, independently of puzzle sample speed. They do not interpolate duplicate
MOVE events or capture during the hold. Only a single-point UI gesture without
a probe or start-feedback context can hold beyond one second (up to five).
Cancellation still releases the contact; a failed hold is never retried.

Report 195 had one unrecognized held jammer over a yellow destination; the other
29 final cells and input trace agreed. The previous freshness mask treated every
old yellow pixel as a possible eye. Exclude individually eye-shaped old components
instead of a large yellow background. Both complete new eyes must still match
source shape and location with at least 50 percent fresh pixels, and the texture,
navy silhouette, water veto and all other final-cell checks remain required.
The uploaded image now satisfies those existing proof gates. This is a visual
verification correction, not evidence of a newly repaired physical drag.

Also pass the compact OCR reader explicitly into the extracted navigation helper;
the non-training entry previously referenced a missing module global.

Regression scenes are generated. Uploaded screenshots, reports and input traces
stay private and are not included in the source tree or release. Packaging retains
the existing launcher's 2 MiB source limit. Validation is recorded in TESTING.md;
Windows/MuMu hardware operation and live farming completion are not claimed.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43516092888 (success; exact source head 643c39d08e59167ffdc3a57bb3a5812f8c7eb737).
Windows validation: 1,125 test executions in 112 suites, no failures, errors or skipped tests. Bundled source SHA256 matches the portable artifact.

Publication: stable v1.0.93 ZIP downloaded and verified; live updater300 to updater301 preserved backup and personal-data markers and skipped a second update. PAD0 itself was not executed.
