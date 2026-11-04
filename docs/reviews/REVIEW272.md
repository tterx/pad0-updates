# v1.0.64 / updater272

Automatic repeat now reads Challenge without a stage identifier, skips stage-selection recording and entry metadata, and limits leader-condition OCR to the battle-relevant crop. Input preparation still verifies the entry screen and visible Challenge; party identity, skill and battle execution checks remain.

On verified result screens, repeat reads only the visible Next button, guards duplicate input until the page changes, then uses the existing verified Continue/Quit controls. It does not classify mana receipts, collect material pages, credit stock, or read/write the material ledger at shutdown. Counts advance only after successful lobby input; F10 and unread-button failures retain the existing stop behavior. Completion notifications show repeat count only. Material farming retains its original stage/reward pipeline.

Five regressions cover strict forbidden stage/accounting/ledger calls, absent Next, duplicate result frames, Challenge-only OCR and cropped party-condition coordinates. Existing17 repeat regressions cover finite repeat/quit, mana-page navigation, wrong start, stop and input failure. Physical emulator replay is unverified; tests mock input boundaries and use real Tk where relevant. No original user images or diagnostic archives are published.
