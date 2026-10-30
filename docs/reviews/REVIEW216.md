# v1.0.8 / updater216 —090 jammer start and field-first skills

##090 evidence

The run had already earned109,000 automatic mana. This failure occurred before path motion, not while executing the path: input8 SDK/capture64 submitted one DOWN, four start checks and one successful UP, with zero MOVE requests. It used the saved17ms sample/12.5ms extra corner settings. The selected source was cell17 (row3/column6), a jammer. The start reader repeatedly reported baselineJ/floatingB and rejected it. No release failure was recorded.

The saved before/start images show that jammer newly lifted and translucent, with another unchanged jammer immediately above it. In the narrow40×40 texture crop, one resting eye is clipped at x=0 (component7×5, centre x≈2.21), outside the old complete-eye bounds; the other centre is x≈23.84. The matching selected crop is likewise clipped. A large core correlation alone cannot authorize a lift, especially with a same-artwork neighbour above. The old matcher consequently rejected all four observations. This is a start-proof limitation on the supplied pixels, not evidence that MOVE delivery failed.

## Start proof correction

Add `_touch_start_jammer_full_face`, an additional proof inside the existing spatial start matcher only. Keep the original source/upper change, bounded lift pose and remote-board checks. Require core correlation≥.85 and error≤.10. Read a wider64×64 face, require exactly two complete eye components with matching relative centres/areas and shapeIoU≥.75, require at least50% newly yellow AND RGB-changed pixels for each eye versus the same pre-DOWN location, and require navy-body coverage. An unchanged adjacent jammer or a brightness pulse cannot supply newly appearing eyes. This does not lower the general colour/model confidence thresholds or relabel the puzzle board.

On the saved090 pair, the added proof succeeds with core correlation≈.89769, eye shapeIoUs≈.794/.821, newly appearing eye fractions≈.972/.946 and24 stable remote cells. These are results on saved images, not a live emulator timing/accuracy claim. Water substituted at the lift, a missing eye, uniform brightness, the unchanged upper jammer, wrong expected colour and remote board changes remain negative cases for the applicable proof.

Input sequencing, saved speed, all exact centres, final board verification, release checks and F10 remain unchanged. No per-corner capture, new DOWN retry, alternative backend or speed tester is introduced.

## Requested skill order

When there is a current skill need/search failure and an unused registered field-destruction effect exists, set `refresh_first` for that decision. A castable field destruction gets tier1000, ahead of cleanse/time/void/unlock and other counter tiers, as requested. Existing bound-caster, auto-use, confirmation and use-policy restrictions still apply: an unusable caster cannot be forced to act. A preparation already applied/used in the current chain is not repeatedly selected as the first action.

The urgent pre-board counter path accepts this refresh as preparation instead of discarding it as unrelated to the enemy badge. After confirmed field destruction, the existing wait/recapture loop reads the new board and locks; only then does it choose the remaining penetration/unlock/other skills and calculate the route. For a combined destruction+penetration skill, the existing applied-buff tracking avoids spending penetration again. Locks follow the new observation, never the previous unlock state.

Preserve213's finite fallback: if destruction is waiting, disabled, bound, used, unverified or otherwise unusable and no valid observed-cooldown haste can restore it, mark that tier unavailable for this decision and choose the next useful skill. Do not pretend the enemy/ally status is cleared. With no skill, preserve the best-available combo/healing turn. A selected skill whose actual activation fails still stops; it is never automatically cast twice.

## Verification scope

11 focused regressions cover the actual090 pair and negatives, one continuous contact gated by that proof, field→penetration→new-lock unlock→search in the real battle orchestrator with scripted frames/controllers, unavailable/disabled/bound/unverified destruction fallback, first-action priority with other counters and prevention of repeated preparation. The older cleanse-over-lower-counter case now explicitly represents field preparation already completed; it still covers its original lower-tier priority.

Run critical plus affected start/input/skill-chain/model regressions and Windows DLL/Tk/package checks. Preserve actual088 rejection and089 uninterrupted routing checks. No full-suite run or real MuMu connection exists in this Linux workspace. Physical start recognition and skill ordering remain for the user's next auto-farming run. Frozen general/B-J models and training/upload policy are unchanged.
