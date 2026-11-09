# v1.0.88 / updater296 — defeat acknowledgement and replay

Report160 shows the defeat TIPS modal over HP0. The background worker has no
handler for this overlay and stops after40 seconds without identifying a battle
or result page. It does not reach the normal result/crystal/replay sequence.

Add a separate battle helper, embedded in the single-file release. Before board
recognition or animation handling, locate the actual pale modal header/body and
separate white button. Require two high-confidence TIPS and OK reads, two matching
numeric HP0/max reads with positive maximum, and two stable modal captures.
Normalize the tight text strokes and lift dim red HP digits behind the scrim;
never infer zero from an empty rail or treat arbitrary OK dialogs as defeat.

Send the visible OK once and wait for the modal to disappear. A stuck modal times
out without repeating input; missing/weak/conflicting proof does not authorize
input. F10 is checked before recognition and again before tapping. Keep full OCR
scores, bounds and acknowledgement state in failure evidence. A defeat before the
first stable board can open this run's uncredited session without changing stock.

Return to the existing mana/result, crystal receipt and replay handlers. Material
farming credits only actual rewards once; zero-drop runs remain valid and repeat
until the saved F7 shortage is filled. No live inventory reconciliation is added.
Finite automatic repeats retry failed attempts without counting them toward the
requested completed runs. Reset defeat state only when replay is accepted; retain
the original completion/quit policy when the material goal is actually fulfilled.

Twenty-two generated regressions cover modal bounds, absent OK/HP, other titles,
nonzero/invalid HP, weak/disagreeing reads, stability, duplicate suppression,
stuck dialogs, F10, session preservation, one/two failures before successful
repeats, immediate defeat before board recognition, and full material zero-drop
then real-drop credit. The private exact160 replay reads TIPS, OK and HP0/134193
with real Korean OCR and verifies a single mocked acknowledgement. The following
result/crystal/replay screens are absent from the supplied failure ZIP and are
covered by generated worker flows. Physical MuMu input is unverified. User images,
reports, ledgers, local PC paths and input traces are excluded from publication.
