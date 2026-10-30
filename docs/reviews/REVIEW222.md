# 1.0.14 / updater222 —098 ready triangle with cooldown1

098 selected Aten(slot2) because the readiness list was[0,2,3,4,5]. The pre-click image clearly has both its white/orange triangle and an outlined1 at the card's upper right. Opening that card yielded `앞으로1턴`, with `skill_use_requested=false` and `skill_use_dispatched=false`; the program stopped after five popup checks because no usable Use button existed. This is a readiness classification error, not failed Use input.

Add `background_cooldown_badge`: detect an outlined pale glyph only in the canonical35×35 upper-right cooldown region, on the expected baseline, with bounded dimensions/area and a dark surrounding outline. Its presence vetoes readiness; it does not infer a cooldown value or authorize haste. This avoids scanning the entire portrait or interpreting central bind counts/clothing as cooldowns. The ROI is cropped before conversion, so no repeated full-frame array allocation or OCR is introduced.

`background_ready_slots` now requires both the triangle and absence of that glyph. This common predicate governs candidate selection, the immediate click preflight and post-use observations. The existing finite priority fallback then chooses another usable skill or a combo turn when no useful skill remains. Add the vetoed slot list to readiness diagnostics. Keep the existing observed-count requirement for haste, single Use request, actual activation verification, F10 and safety stop for unrecognized popups.

Actual098 now yields cooldown slots[1,2] and ready[0,3,4,5]. Actual055 yields cooldown slots[1,3] and ready[0,2], preserving ready Aten despite its white portrait artwork and ignoring large central bind counts. Five regressions cover both real strips, candidate fallback without portrait OCR, click rejection before touch/capture, and negative glyph location/outline cases. Critical/related Linux and Windows/package checks precede updater activation.

No SDK input, capture64, speed, solver, model weights or saved data change. Physical MuMu is not connected; full user-device gameplay is not claimed.
