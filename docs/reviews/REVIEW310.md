# v1.0.102 / updater310 — acquisition-backed branch continuation

Report212 stops in background_stage_selection with “분기 선택용 목표 재료의
출현 위치를 확인하지 못했습니다.” The saved game image is the acquisition
list for the next shortage; the saved failure image is a map. Neither image is
an actual two-button branch dialog. After crediting the previous material, the
next target C0131 has six current acquisition candidates hidden:3-2 through3-7.
Their preview drop IDs remain partly unresolved; hidden:3-5 contains no C0131.
It is the only -5 candidate, so the pre-entry branch planner's strict missing
position check explains the stop before a branch choice. The full stage DB and
its battle-count ranking are not attached; exact original ranking is not replayed.

Preserve the complete-preview last-four classification whenever the target is
identified, and prioritize existing trial/receipt-confirmed learning. If the
current freshly matched material's acquisition list attests a stage, its preview
scan is complete and trusted, but its target position is unresolved, start an
explicit option1 trial instead of rejecting entry. Do not append a fake preview
identity/position, change the drop DB, guess crystal identity, or credit a reward.
The existing verified receipt learner switches after ten no-drop results and
retains receipt-confirmed routes. Unknown-position trials use that same learner.

Keep acquisition provenance local to the current selected stage and material.
Clear it before every new selection and automatic run; publish it only after
entry confirms the entire stage identity, including Hidden/Normal/star world.
At an actual branch dialog, require the same stage and current target before
reusing this provenance. Unexpected branch stages use the same path after their
button pair is actually recognized. Saved acquisition IDs alone, a different
world/target, an incomplete or stale preview, or an unproved button pair do not
authorize this fallback. Duplicate input guards and receipt ownership are kept.
Fix the stale “nonbranch preferred” status text to match the existing common
non-EX-first, branch-inclusive ranking; do not change ranking or EX policy.

This modifies shared branch paths authorized by the user's all-stage branch
learning instructions. Other gameplay modes, skill/F10 behavior, maximum combo
objectives, no-forced-healing policy and input pacing are unchanged.

14 new methods cover positive and negative provenance, complete-preview order,
entry mismatch, old-source invalidation, and the actual picker/worker/receipt
flow through ten misses, opposite choice, success and restart reuse. Generated
UI fixtures contain no user screenshots. A private replay uses actual212 images
and drop order with controlled selected-stage ranking and mocked entry: v101
raises before input; v102 sends one stage tap and preserves original drop IDs.
Full Windows/package/public ZIP/updater proofs are recorded in the release repo.
Physical MuMu and the original unrecorded navigation sequence remain unverified.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43716327041 (success; source 29d2b73d8582dc08f0ac6f623c5233d6ed65e2f9); 1,289 executions, 122 suites. Exact package source SHA256 matches.

Publication verified: PR13 merged as fb2b3d871b653840a2e5ddcb472fa6f0c5e1523e;
its tree matches the Windows-tested head. Publisher run 43717381106 succeeded.
The actual public stable latest v1.0.102 ZIP passed SHA256, CRC, version and updater-state
checks. Its source equals the live immutable raw payload. Root update.json points to310.
The genuine v1.0.101 launcher installed310 from the live manifest in an isolated
installation, preserved its backup, settings, inventory and stage/branch-history
markers, then skipped a repeat update. No PAD0 or physical game input was executed.
