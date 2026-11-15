# v1.0.98 / updater306

208 reaches EX5 successfully, then stops before skills or route input because
slot4 Ideal's bound/dim portrait with a blue duration badge fails entry-to-battle
identity matching. The saved entry and battle frames reproduce the exact logged
raw differences: slot4 89.303, while the other five slots pass unchanged.

For master_multicolor only, after the unchanged legacy portrait checks fail and
the current frame independently identifies that slot as bound, compare the visible
forehead and both side strips with the source portrait. A bounded affine crop
alignment and one shared dimming/exposure fit must preserve RGB texture, strong
correlation and low error globally and in all three independent regions. The
central/lower duration badge is excluded. Do not accept a bind glyph as identity
or lower the original thresholds. Missing bind evidence and other modes retain
the original rejection. A bound first frame is never cached as the unbound battle
identity reference. Wrong-party checks still stop before automatic skills/moves.

Private208 replay passes all six original portraits with the new master path;
slot4 exposure .50453, correlation .91072, corrected mean difference12.232.
Swapping each of the other five actual entry portraits into that position remains
rejected. No game input is executed by this replay, and live Windows/MuMu farming
completion is not claimed. Uploaded images, reports and private traces are excluded
from source and publication; the regression suite uses generated textures.

User-requested shared selection policy: filter eligible target sources to non-EX
whenever any exist, then use ordinary target-overlap/battle-count ranking for branch and nonbranch sources equally.
Only if every eligible source is EX may an EX be selected. Full identity handles
world prefixes and stars. Unavailable, unverified or wrong-material normal stages
cannot block a valid EX fallback. With equal ordinary battle counts the208 list17-5,18-5,EX5,★7-1 now selects17-5. The follow-up explicitly removes the old branch-avoidance preference.
Branch learning, previously confirmed routes, maximum combo goals, no forced
healing, input pacing and other-mode identity behavior remain unchanged.

Fourteen new tests cover the eligible EX and equal branch policy, positive/negative bound texture,
three-region corruption, missing bind, unchanged other-mode rejection, wrong-party
stop and two-cycle bound/unbound identity caching at a mocked device boundary.
The complete Windows workflow builds the exact tested package; independent ZIP
checks and publication/updater download proofs are recorded in the updates repo.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43671355681 (success; source 76c3cdcc9a6cb9292a7d2a58bfb98869fb89f885); 1,220 executions, 118 suites. Exact package source SHA256 matches.

Publication verified: PR9 merged as 88d8e09c61a2c4c8f58117f1f3b342337dedaa84;
its tree matches the Windows-tested head. Publisher run 43672074546 succeeded.
The actual public stable latest v1.0.98 ZIP passed SHA256, CRC, version and updater-state
checks. Its source equals the live immutable raw payload. Root update.json points to306.
The genuine v1.0.97 launcher installed306 from the live manifest in an isolated
installation, preserved its backup, settings, inventory and stage/branch-history
markers, then skipped a repeat update. No PAD0 or physical game input was executed.
