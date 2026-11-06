# v1.0.79 / updater287 — creation owner and accessible diagnostics

The user supplied a material panel showing all five current quantities but an
unresolved owner; the status explicitly says target name/number pending. Earlier
creation pixels show No.376 and the Korean name 서늘한 가야금 용악사 미나카.
The existing known-game-name fallback included only No.343: if the long title
disagreed, even matching No.376 readings had no verified name to bind. Add the
screen-transcribed No.376/name association. Two matching, prefixed, high-confidence
readings from the current creation capture remain mandatory; disagreement with
another known owner title still rejects registration. No old selection is reused.

The supplied status does not prove whether the user's number readings matched,
the title disagreed, or another header/number step failed. The missing association
is a concrete blocked path, not a complete physical reproduction of the case.

The user cannot find the OCR diagnostic or a saved report. An unresolved F7 owner
now saves a diagnostic ZIP with the current cached game/title images, title
readings, owner-number notes and full F7 debug text. Stale name comparisons and
prior timings are cleared; no fresh capture is taken to save the report. Previous
ledger stocks remain unchanged. The crystal panel directly exposes OCR 진단 /
오류 기록, whose window displays the latest ZIP path and opens its folder.

Eleven tests cover current number/name binding, disagreement/weak readings,
conflicting known owner, missing/non-creation context, full F7 callback/catalog
request, current report evidence, no stale pixels, save failure and actual Tk
diagnostic controls/folder callback. OCR and device inputs are declared mocks;
the current screenshot's pixels and physical MuMu are not replayed. Originals,
diagnostic images, local PC paths and input traces are excluded from publication.
