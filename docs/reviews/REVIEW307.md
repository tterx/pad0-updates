# v1.0.99 / updater307

209 reaches17-5 in v1.0.98, then stops before skills/movement because slot5
RaDragon's bound portrait fails identity matching (raw difference45.867).
Bind detection is correct. The previous20x16 source comparison cannot separate
rope and badge contamination sufficiently for this different artwork. It only
covered208's Ideal reliably; a different bound character could still stop.

For master_multicolor farming, independently detect bound slots as before and
apply one generic fresh-entry texture comparison to every bound slot. No slot
or character-number exceptions are used. The entry20x16 signature must still
agree with the prepared profile (difference<=8), then compare80x100 samples
from that verified entry frame and the current battle. Use a bounded affine crop
alignment and one scalar dimming/common bias fit. Exclude the duration area,
trim at most25% of otherwise visible pixels for rope contamination and retain
>=70% of the forehead plus>=50% of each separate side. Per-channel-centred
spatial texture, regional agreement, minimum spatial variation and corrected RGB
error are all required. Flat colours cannot establish texture correlation.
Small-crop acceptance alone cannot bypass this proof for a currently bound slot.

Both genuine saved frames pass:208 slot4 spatial correlation .99526, corrected
mean difference3.169;209 slot5 .98202 and6.761. All five wrong actual source
portraits reject in each case. Simulations using the six actual unbound portraits
and209's observed overlay accept each bound slot singly and all six together;
the6x6 source matrix accepts exactly six genuine pairs and rejects30 wrong pairs.
These multi-slot cases are labelled simulations, not captured game runs.

Bound battle frames still cannot become an unbound identity reference. Refresh
the current entry frame after a master farming target/stage change; map-return
reentry already refreshes it. Retain normal portrait checks for unbound slots,
other modes, and existing callers without an entry frame. Save numerical proof
and rejection diagnostics for future reports. Private images and reports are not
published. Branch learning, EX-last selection, equal branch ranking, maximum
combos, skill policy and input timing are unchanged.

Sixteen generated regression methods cover every slot, mixed/all-six binds,
36 source-pair controls, regional corruption, crop variants, flat/nonfinite data,
invalid photometry, missing bind/other-mode gating, stale entry rejection, a real
worker's first turn with all six bound, wrong-party input prevention and bound-
then-unbound reference caching. All device inputs in these tests are mocked.
Full Windows workflow, exact package source hash, standalone ZIP checks and live
updater/public download verification are recorded in the release repository.
Physical MuMu input and farming completion remain unverified.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43674185562 (success; source 155a4b77f15c0fdb41061a95d90c64deb1ef8f83); 1,236 executions, 119 suites. Exact package source SHA256 matches.
