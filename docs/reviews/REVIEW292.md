# v1.0.84 / updater292 — register an unresolved owner name after farming

A number-only farming target can finish successfully while its full creature
name remains unconfirmed. The completion window now shows `이름 등록 필요 · No.380`,
a blank entry and an `이름 등록` button for this case. Enter also submits the name.
The window stays open until closed, so typing is not interrupted by the normal
15-second toast timeout. The user may close it without entering a name. Named
targets, automatic repeat and notification/sound preferences retain their behavior.

Pin the completed group key, revision and number in the completion snapshot.
Save the manual name to that exact numbered group and its canonical number/name
mapping, record the manual origin separately from the original OCR observations,
and refresh the active material panel. Retain group identity, material requirements,
stock, reward receipts, selection and creation-to-breakthrough route. Never run
F7 or reset inventory to change a name. An intervening F7 snapshot, a changed
owner number or a new running farm blocks an old window's write.

Twelve regressions use real Tk windows and real temporary ledger files to verify
visible inputs, no timeout while typing, complete save behavior, unchanged counts
and OCR evidence, validation, skip/close, named and repeat completion, stale-window
rejection, active-group switching, new-run protection, disk-failure retry and
notification deduplication. Existing completion, number-only owner, farming and
navigation checks remain. No physical emulator input is involved in name entry.
Original user screenshots, archives and local PC paths are not published.
