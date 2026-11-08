# v1.0.83 / updater291 — retain OCR evidence and continue by owner number

Report157 stops before material-name dialogs: the two direct title readings are
`서늘한 밤의 가야금 용악사 미나카` (.97004) and
`서놀한 밤의 가야금 용악사 미나카` (.97639). Both current result-number
reads agree on No.380 (.9792/.98028), but this number has no registered name.
The old fallback requires a known name and therefore discards a usable number.

Save complete direct readings, their preprocessing methods and unrounded scores,
detector tokens, glyph bounds, capture/record times and both number readings with
the F7 group. If direct titles disagree, retain only No.380 as owner identity and
mark the name unconfirmed. Do not promote the higher-scoring OCR candidate into
a canonical name. Two high-confidence, prefixed current number readings are still
required; conflicting/weak numbers and a confirmed other-owner conflict stop.

Continue all material-name and stage catalog work, rebinding the SDK screen to
the same current number and all registered material images before any click.
Pin the SDK title fingerprint for return checks. Preserve owner evidence in later
material-error archives, independently of detail-specific diagnostics. Display
`No.380 · 이름 미확정` in the material panel. A later explicit F7 scan with a
confirmed name updates the same numbered group rather than creating a duplicate.

On creation/training return, unconfirmed title candidates are search hints only.
Verify the visible query and result, then verify the opened creature number
before touching materials or breakthrough controls. An empty first search can
try the second candidate; a mismatched opened number stops. Keep F10 guards and
all existing receipt, F7-baseline quantity and ordered farming rules.

Nineteen new regressions cover evidence fidelity, known/new number handling,
full F7 registration and catalog startup, identity upgrades without duplicate
groups, SDK number binding, diagnostic retention, search hint fallback, strict
opened-number checks, single portrait taps and cancellation. Existing owner tests
are updated to the explicitly requested unknown-number continuation policy.
A private replay runs actual Korean OCR on the original157 title/full-frame
pixels and reproduces the exact readings and number-only decision. Generated
and mocked UI tests cover subsequent navigation; no physical MuMu input is
performed. Original screenshots, archives, PC paths and input traces stay private.
