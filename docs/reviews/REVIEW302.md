# v1.0.94 / updater302

Only new reports 197-200 were analyzed. Re-uploaded 194-196 were excluded; their
updater301 fixes remain covered by the existing regression suite.

197: the first confirmed cooldown arrived after OCR at 4.031 seconds, just past
the four-second observation window. It was not yet stable across two frames.
Allow one bounded 0.75-second settle window after the first positive signature,
so a late read can obtain its second agreeing frame. Missing or contradictory
evidence never extends it, unstable boards still fail and cancellation prevents
the next capture. Observe only: never send a second Use click.

198: the actual held jammer's eyes fell between coarse position/scale samples.
The coarse alignment produced one eye shape IoU of .737, below the unchanged .75
gate. Try the original poses first, then refine at most three strong texture
seeds within the original global bounds. Keep the original opaque/translucent
texture gates, both complete eyes, fresh pixels, navy silhouette, water veto,
full trace and the other 29 cells. This changes alignment, not proof thresholds.
The existing texture search lives in the embedded battle module to retain the
launcher's 2 MiB source limit.

199-200: first-clear results returned to the Challenge Dungeon map. Add its exact
heading to the two-read header whitelist and owned repeat-map handler. Entry
OCR read the visible star as an asterisk, while map OCR omitted it; normalize
these only when the existing five-point image detector independently confirms
the star. Require two agreeing stage-number reads and exact starred identity,
two stable CLEAR captures, an owned result/session, the original party and the
same entry number before another Challenge. NEW or unrelated stages are not
counted, and unstarred numbers do not match starred stages.

Private uploaded images, reports and input traces are excluded from the source
and release. Regression scenes are generated. Validation is in TESTING.md;
Windows/MuMu hardware operation and live farming completion are not claimed.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43634010980 (success; exact source head 00dbb24b980c7266c49a4534eca9c15626ec2b75). 1,142 test executions in 113 suites, no failures, errors or skipped tests. Portable source SHA256 matches.

Publication: stable v1.0.94 ZIP downloaded and verified. Live updater301 to updater302 preserved backup and personal-data markers and skipped a second update; PAD0 itself was not executed.
