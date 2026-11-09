# v1.0.90 / updater298 — warm battle scenery is not a reward dialog

Both162 and163 show a live Halloween battle with a complete readable puzzle
board. The previous reward candidate checks only long gold horizontal spans
near the top and bottom. The orange scenery satisfies both checks, so the worker
suppresses board recognition. Report162 reaches the20-second entry guard without
accepting its board;163 reaches the40-second nonbattle/result guard at the boss.
Independent unchanged board recognition reads all30 cells in both actual captures,
including163's locked purple orbs. This is a scene classification error; no
stage-clear status check is involved in this failure.

Require a bounded, connected gold dialog frame enclosing a large inner content
area. Validate normalized position/size, rectangular outer contour area and the
inner hole's width, height and area. Close tiny scaling gaps; reject scenery,
solid orange blocks, disconnected bands, small card frames and oval decorations.
Do not depend on reward text, card count or whether a target crystal dropped.
The helper remains a candidate gate: existing result, identity and quantity
checks still control accounting and inputs.

Move the detector into the existing embedded battle helper and retain the App
method as its wrapper, respecting the older launcher's2MiB source limit. Preserve
entry and stall deadlines, skill/board proofs, input timing, reward collection,
failed-repeat rules, saved F7 quantities and no-material accounting in repeat mode.
Do not add stage OCR or blindly dismiss unrelated first-entry dialogs.

Sixteen generated regressions cover full/empty/dense reward panels, scales,
warm backgrounds, disconnected bands, solid/oval/card decorations, wrong colours,
missing edges, small gaps and complete entry/two-wave repeat flows. A private
replay uses exact162/163 pixels to reproduce both false reward classifications
and the20/40-second worker guards. The corrected mocked worker accepts the two
actual boards and completes its repeat. Five earlier actual gold receipts remain
positive; a real reward Next is read with Korean OCR. Physical MuMu input and
actual first-clear result transitions are unverified. Original captures/reports,
local PC paths and input traces are excluded from publication.
