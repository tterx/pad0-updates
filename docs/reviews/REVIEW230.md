# 1.0.22 / updater230 — awakening identity, linked-clear/TPA modes and current-party skill tactics

## Awakening registration

The previous explicit registration required exact agreement between two direct OCR views of the creature name in the awakening modal and then a unique name-to-number lookup. The creature's already visible number in its detail view was not used. A spelling discrepancy or unread title therefore prevented registration even when the user was looking at No.243.

Registration now captures the current creature detail twice and confirms its number, opens the awakening modal, confirms all awakening names/descriptions on two of at most three captured frames, closes the modal and confirms the same creature number again before queueing a save. Starting with an already open modal closes it first to obtain the current detail number; no selected party slot, old overlay label or old number cache is used as the identity without fresh matching captured pixels. Desktop context/device configuration and request cancellation checks remain.

The modal name reader still performs its two direct OCR views. A known different creature name remains a conflict. When the views disagree or are unread, the confirmed number supplies the association and an existing name supplies display text; that text is explicitly not marked as newly recognized. Unconfirmed spelling never overwrites the saved Korean name. The save routine checks all three number observations before mutating settings. Missing/different numbers, changed screens and differing awakening effects do not save.

`OCR230.json` records real RapidOCR inference on a **rendered** No.243 badge and the Korean name 미마룡 다이긴죠: both name views agree at .9372/.9342. It is not an inference result from the user's original screenshot, which is available only inline. Worker tests replay capture/tap transitions with blank and conflicting owner text; they establish control flow, not physical MuMu operation.

## 연결&지우기

A new saved mode ID10 is displayed as `연결&지우기`. The supplied team-skill screenshot is represented by these joint route requirements:

- A matched group of at least four identical orbs; the condition is not exact-four TPA.
- At least six combos, or a stronger current enemy floor, including only actually confirmed combo bonuses.
- A two-second reserve from the observed movement timer. For example,8 observed seconds permits a6-second input budget. Manual move-cap calculation, paced mouse input and background dispatch share that budget.

Any orb type allowed by the screenshot can satisfy the connected group; no five-attribute or mandatory heart-match condition is added. Random combo-drop appearance contributes no invented bonus or future skyfall. An unread/insufficient timer cannot certify the new mode's two-second reserve. Weak routes are not silently presented as satisfying the new mode. Existing mode IDs and master four-second/fallback policy remain. The explicit disabled-board turn-progress policy from1.0.21 remains available when appropriate.

## TPA

TPA goals now carry an explicit attribute set derived from party members with the TPA awakening (2마리 공격/2체 공격/2体攻撃/TPA), including their main/subattributes and repeated attributes only once. Other members' attributes do not qualify merely because they are present in the party. Saved observed/corrected character details and awakening registrations are used through the usual profile merge. No eligible TPA member prompts the user to register party/awakening information rather than guessing all colours.

The solver and shared goal replay count exact-four shapes only in eligible attack attributes. A four-orb group of another attribute cannot satisfy TPA. Exactly four remains required; five connected does not satisfy TPA. Ordinary matches of other attributes remain usable. The attribute set survives enemy combo augmentation, direct solving, background search, cached goals and final execution validation. A changed party attribute set can invalidate an old route.

## Current-party skill tactics UI

The main window removes `현재 상세의 크리처 1기 등록` and `등록한 크리처·각성 확인`, and exposes a separate `스킬 택틱 · 상태별 우선순위` window. Existing in-game/party awakening registration is retained. The user selected **state-based priorities**, not floor/turn scheduling.

The standalone editor displays the six current members, available portraits, main/subattributes, cooldowns and skill summaries. Each of14 supported states has an ordered list of party slots with add, move up/down, remove and reset-to-default controls. Editing is a draft until Save; saving does not start registration or game input. An enable checkbox controls use of the saved plan. Plans are bound to the ordered six creature numbers, not names or arbitrary list indices alone; changing the party requires reloading and saving. The editor protects against a party change while a draft is open. The saved configuration is copied into the next automatic farming run; it does not mutate a gesture or an active run's settings.

The actual skill selector consults the saved order among relevant, ready counters at the current urgency tier. It skips cooldown, bound, disabled, used and unverified restricted skills. Unlisted eligible skills retain the standard fallback ordering. The plan cannot push colour generation ahead of required bind/awoken cleansing or unlocking. If the counter tier is unavailable, the existing fallback re-evaluates the next state's saved order. A malformed or different-party plan falls back to normal selection without applying old slot priorities to different creatures.

`test_skill_tactics230.py` adds12 checks for state ordering, unavailable/irrelevant skills, party changes, plan validation, actual context-to-selector propagation and unavailable-cleanse fallback. Real Tk tests edit/reorder/save/reload/reset drafts, reject stale-party saves, confirm that editing does not start game actions, and check a700px-high layout with long Korean names, skill text and portrait images. The main-button removal and tactics entry are also checked.

## Verification and limits

New suites cover12 awakening cases and15 route cases: name OCR disagreement/unread, stable number save, pre/post creature changes, description disagreement, modal-start recovery, cancellation, old name-only behavior, linked four/six joint proof, no speculative bonus, two-second reserve, direct/headless goal handling, automatic and final manual budgets, party-specific exact-four scoring and actual automatic TPA dispatch. All existing regressions remain, including disabled-board clearance, cloud behavior, input start/release and local AI cleanup. Full Linux, exact-source Windows, package and live-updater results are recorded in distribution `release_checks230.json`.

No runtime model training, AI dataset recording, model weights, input backend or general movement pacing changed. Actual user-screenshot OCR and physical MuMu UI/turn execution are not verified here. The OCR diagnostic is explicitly rendered input; route and worker verification is offline/scripted.
