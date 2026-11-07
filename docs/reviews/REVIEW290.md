# v1.0.82 / updater290 — entry stage label and coloured modifiers

Report156 selected SP14 from the current acquisition list with two agreeing label
reads, reached the entry screen, then stopped before Challenge because the stage
number could not be confirmed. Exact saved pixels reproduce full-scene `8SP14`
and direct-crop `8SP14`: the purple modifier icon becomes a leading8. The gray
entry background also prevents the general modal-title glyph finder from locating
the line. The parser correctly rejects that dirty prefix; it must not be trimmed
into a guessed stage number.

Share the existing acquisition-label ink cleaner with entry header recognition.
Retain complete neutral gray stage glyphs and real gold stars, remove coloured
modifiers/edge shadows, and require two matching full-identifier reads at .90 or
higher. Keep the original title/raw alternatives for other layouts, stage-world
binding tied to current action epoch, and equality with the explicitly selected
stage before Challenge. Save entry label readings, expected/observed identity and
the actual entry failure frame in the diagnostic record. Also read the complete
right-hand battle-count field; the former crop ended before its final digit.

Twelve regressions cover a gray header with coloured modifier, `8SP14` rejection
followed by two actual-label reads, weak/conflicting/invalid text, missing visible
number despite selection history, stale epoch/world binding, multiple identifier
forms, compact entry-token caching, full battle count and mismatch before Challenge.
Existing acquisition/Hidden/star tests run after extracting their unchanged mask
into the shared helper. A private exact156 replay uses actual Korean OCR for the
entry number/Challenge and battle count. No physical emulator or real Challenge
input is performed. Original screenshots, PC paths and input traces do not publish.
