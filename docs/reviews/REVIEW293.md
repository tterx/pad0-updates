# v1.0.85 / updater293 — inspect search portraits for both owner-name hints

Report158 completes one material and moves toward the next, then stops on the
creation search page. Its diagnostics already show both title hints being
searched: the old gate finds no complete result-name token matching either query.
The saved last page is an empty search for the second, misspelled hint. The first
query's result screenshot is absent, so its actual portrait visibility cannot be
established from this archive. Input delivery and visible query checks succeeded.

Keep those query checks, and add creature-frame detection independent of result
name OCR. For creation search, compare the visible card sizes within each result row
below the query, retaining the larger target portraits and excluding smaller
material cards and controls. This supports left and right result panels. Require two fresh frames
with matching geometry and artwork before a visual selection. Retain the full
name/number text result paths. Open the result and verify its current prefixed
No. twice before returning to materials. A confirmed wrong number returns to the
verified search page and tries the other hint; uncertain numbers stop after fresh
read attempts. Empty searches finish after bounded per-hint checks rather than
repeating the final query for the rest of a shared loop allowance.

Apply the same hint/portrait/number approach to training return. Retain the
existing single Lv portrait marker path, add stable visual portraits when that
marker is omitted, and return for the second hint after a confirmed wrong owner.
Never infer a monster identity from portrait presence or OCR confidence alone.
Cache each of the first two candidate search screens, include both in automatic
and manual error ZIPs, and retain the query, portrait and opened-number evidence.
F7-baseline quantities, credited receipts, material order, skills, actual route
execution timing and completion name registration remain unchanged.

Twenty-one generated regression cases cover portrait geometry, left/right result layouts and excluded material
columns, empty/decorated pages, two-frame stability, absent result-name/Lv OCR,
first/second hint selection, wrong-owner recovery, both wrong or empty hints,
query proof, ambiguous results, failure archives with receipt preservation and
F10. A private replay uses actual158 empty-search pixels and Korean OCR, an actual
154 training portrait and an explicitly controlled placement of the actual157
No.380 portrait. No original first158 query image or real MuMu taps are available;
subsequent flow checks are generated/mocked. User screenshots, diagnostic ZIPs,
PC paths and input traces are excluded from publication.
