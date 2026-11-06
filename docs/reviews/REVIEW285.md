# v1.0.77 / updater285 — first material-name startup

The user reports fresh registration stopping at provisional crystal1–4 labels
without opening any detail dialog. No capture/error log accompanies this report,
so the original startup branch or connection failure cannot be uniquely proved.
Two concrete skip paths exist: the F7 completion callback and catalog entry both
require a desktop title image, even though the SDK can rebind current material
pixels; a busy calculation also rejects startup rather than continuing later.
Connection setup failures leave stored provisional quantities visible.

Windows F7 completion now requests the catalog even without desktop context.
The existing SDK binding verifies all current material images/count/positions.
When the desktop title is absent, two high-confidence SDK owner-name readings
must match the requested owner before any material click. Its title fingerprint
is pinned during return checks. Wrong/uncertain owner or materials stop; a title
is never guessed from an arbitrary window. Binding may retry up to three fresh
read-only captures before any click, with cancellation checks.

An existing calculation can defer name startup for at most15seconds. The same
action epoch, F7 generation and group revision must still own the request.
F10, replacement requests, target changes, new input work or timeout cancel it;
actual input/farming/catalog work is never queued for later surprise clicks.
An older timer cannot clear a newly started catalog's OCR ownership.

Startup/connection errors are explicitly labeled name-registration pending and
save a diagnostic ZIP with cached source images and startup reason. Provisional
quantities are labeled waiting for names; success follows worker completion.
Normalized placeholder names remain unnamed, including suffixed legacy labels.
Existing actual names are reused, all missing names precede stage inspection,
and F7 never starts a battle.

Validation uses declared mocked UI/ADB/thread boundaries, a four-material first
registration, missing-title/connection failure, deferred cancellation/revision/
timeout/replacement, owner/material mismatch and prior material/farming checks.
It is not a physical first-registration replay or a reproduction of the user's
unrecorded exact failure. Original user images/archives/traces are not published.
Existing quantity-only fixtures isolate the new catalog-start boundary and
assert names-pending rather than success; their numeric and ledger checks remain.
