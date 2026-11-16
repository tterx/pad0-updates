# v1.0.109 / updater317 — material farming returned to the challenge map

Report220 is v1.0.108. Its latest failure pixels show the challenge-dungeon map
with EX5 visible. The recorded turn passed held-layout and release checks, then
one defeat was positively acknowledged. Mana total97,460 and a real crystal-page
receipt were recorded; the target C0127 remained0/7. Crystal Next was positively
read twice and sent. The worker then raised unexpected screen 챌린지던전.
Result-to-map recovery existed only for finite automatic repeats. Material farming
had no corresponding map branch. This was a navigation-state omission after the
recorded defeat and result, not a touch-release failure or a crystal acquisition.

Only master_multicolor material farming gains this path. A nonempty current
session, committed crystal receipt and acknowledged Next are mandatory. The
ledger must still contain that exact credited session and unchanged target group.
The currently farmed material must remain selected and deficient. No map pixel
can manufacture a receipt, award, clear or branch miss. Completed goals finish
without another stage click or Challenge. An absent/uncommitted/stale receipt
continues to fail closed. Mana-only exits without a committed crystal receipt do
not gain guessed zero credit from this change.

Use actual medal/label geometry and two high-confidence label views to locate a
unique exact stage. Ordinary and starred stage numbers, plus normal/manual and
Hidden worlds, remain distinct. A direct manual entry with no attested world
is not silently treated as a normal-world map stage. Two fresh positive map captures must agree in
position and portrait pixels; missing identity resets that proof. A victory needs
a real CLEAR marker; an already proven defeat may retry an uncleared stage. The
map heading must remain the same. Ambiguous labels, duplicates, map changes,
stale sessions, target changes, cancellation and F10 prevent input. Waiting is
bounded and never scrolls or guesses a neighbouring stage.

Real report220 EX5 pixels exposed a second problem: its purple glow splits the
grey E/X/5 strokes, and existing modifier clipping/black-white masking cannot
read it reliably. Farming-map OCR first uses the existing complete label mask;
if that fails, retain all observed neutral-ink component bounds and read the
original RGB crop at two scales. This preserves coloured gaps in the glyphs,
without inserting an expected letter, digit, prefix or stage number. Production
Korean RapidOCR on the actual uploaded map independently read EX5 at .99028 and
.99073 and located (1193.80,425.69). Other visible starred numbers remained separate.
Only a small game-art crop is committed as a test fixture; no full uploaded desktop
or report with private device paths is published.

After the verified stage portrait is clicked, require the exact same entry stage,
the existing high-speed-input preflight and fresh stage check, and all six original
entry-party comparisons before one Challenge. Revalidate the current ledger and
cancellation before actions. The worker resets the old defeat, result and branch
context for the next battle, retaining learned branch plans. Existing stock and
receipt updates, combo policy, all-eight-drop learning, skill/route timing, input
budgets, held-layout/release gates, EX ranking and separate stage histories remain.
The existing finite-repeat implementation and every other mode stay unchanged.

Twenty-seven new checks cover real EX5 art geometry, complete RGB glyph bounds,
strong/weak/disagreeing OCR, duplicate labels, star/world separation, stale/unowned
receipts, unchanged accounting, completed goals, changed groups/selection, fresh
map proof, wrong entries/parties, preflight stage changes and F10 before/after the
portrait. Full worker tests cover material continuation, completion at the map,
other-mode stopping, and defeat → mana → crystals → map → another battle with only
the actual target drop credited. Android input boundaries are mocked. Private
replay additionally uses uploaded map/entry pixels and production OCR. Actual live
Windows/MuMu clicks and a completed physical farming run remain unverified.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43806884968 (success; source 5b1b15251246bbd6c43aa819445207b9d5d34d45); 1,464 executions, 137 suites. Exact package source SHA256 matches.

[Public replay comparison](../../releases/v1.0.109/replay317.json).

Publication verified: PR20 merged as 9628f1357b5247b4db093d4f26f7905eb8a428ce;
its tree matches the Windows-tested head. Publisher run 43808137677 succeeded.
The actual public stable latest v1.0.109 ZIP passed SHA256, CRC, version and updater-state
checks. Its source equals the live immutable raw payload. Root update.json points to317.
The genuine v1.0.108 launcher installed317 from the live manifest in an isolated
installation, preserved its backup, settings, inventory and stage/branch-history
markers, then skipped a repeat update. No PAD0 or physical game input was executed.
