# v1.0.10 / updater218 — F7 acquisition-stage registration

094 stopped during the stage-discovery phase after one full-screen OCR read returned no usable stage numbers. The material-catalog worker already requested stage inspection; changing its option alone would not fix this failure. Its saved `game.png` was the original final-breakthrough cost dialog, so the ZIP does not show the unreadable acquisition list. We cannot establish the exact OCR miss from that image.

## Changes

- Remove the global shortcut that completed F7 when all names were known and every material occurred in some registered stage. That condition did not prove all currently available stages for each material were registered.
- Both F7 and the material-confirm button reuse existing names, finish missing names first, then inspect each material's acquisition list, including materials already owned in sufficient quantity. Reuse verified stage previews; register missing/deleted previews via the medal, save stage illustrations and drop images, and return to the original material dialog. No battle entry is added to material confirmation.
- When full-screen OCR misses a medal's stage label, read the short label below that portrait at 2× and 3×. Require the same complete stage number at confidence ≥0.90 in both reads. Preserve existing SP/numeric parsing and medal validation; never fill a number from the DB or guess missing digits. If any visible medal remains unresolved, do not declare partial registration complete. Cache only complete recognition of identical list pixels, including the medal region from y220.
- The acquisition page header can appear before the list finishes rendering. If the list cannot be confirmed, permit one fresh capture/read, without repeating the material click. F10 and changed-page checks remain active.
- Failure ZIPs retain `phase=material_name` for compatibility and add `catalog_phase=stage_listing/stage_preview`. Save the latest navigation/list/preview image, fallback label boxes/text/confidence and unread-label count instead of retaining only the starting cost dialog. Previously committed names/stages/stock survive a later read failure.
- Update the in-app instructions and progress text to describe stage registration. Keep SDK input, skill controller, capture64, saved speed, solver, models, reward accounting and completion popup unchanged.

## Verification scope

Ten focused scripted regressions cover existing-name reuse with a partially mapped DB, names-before-stages, all materials including sufficient stock, earlier-save preservation, actual failing list/preview snapshots, two-scale agreement, partial/full OCR, rejection of inconsistent/low-confidence/unrelated digits, exact-pixel cache invalidation and F10. Existing material/navigation/preview-refresh regressions are retained. An obsolete historical test expecting an `auto` input selector now checks the already-fixed `mumu_sdk` setting; no input behavior was changed for that test.

Critical plus related Linux checks and the Windows focused/DLL/Tk/package workflow are required before updater activation. A one-shot static rendered-label check with the installed OCR engine read SP8 at 0.93087 and 6-14 at 0.98591 with two-scale agreement; those are synthetic label pixels, not the missing 094 list or measured game accuracy.

No physical MuMu is connected to this workspace. No repeated emulator test, automatic speed test, new input retry or SDK/backend change is introduced. Real F7 completion on the user's current acquisition list remains a user-device verification item.
