# v1.0.32 / updater240

The user reports material2 failing in a creation-confirmation dialog, with red required3 and owned1. No original PNG/ZIP is attached for this case; exact screenshot pixel/crop provenance is unverified.

Development reproduction with real Korean RapidOCR showed small red3 enlarged to64px recognized as Hangul-like 그/크, or a low-confidence3. The old code repeated the same64px single-glyph views; its32px recovery required two existing weak numeric readings and could never run on these nonnumeric results.

On a failed single-digit read, replay the isolated, complete glyph at32px and24px. Two agreeing readings at normal confidence are required. Any contrary high-confidence observation at64px or another scale rejects. The same bounded recovery applies to failed individual digits inside multi-digit quantities. Complete-number digit counts, red/white separation, intact-edge and multiplication-mark guards remain. Registration still enforces required1..999 and owned0..999. No local learning/data store is added.

Unit regressions cover nonnumeric large3 recovery, cross-scale disagreement, two-view confidence,333 digit fallback, and the five-material crop->F7 completion->persistent ledger flow with required3/3/1/1/10 and stock4/1/5/7/82. tools/check_quantity240.py performs actual OCR on rendered numbers and the two small-red3 reproductions. This is not proof of the exact inline screenshot or MuMu interaction. Very thin white glyphs below the supported complete-stroke threshold remain unconfirmed rather than guessed.
