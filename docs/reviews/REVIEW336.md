# v1.0.127 / updater 336 — Prefer earlier stage numbers in master material farming

The previous picker gave priority to non-EX sources, then compared how many
missing materials a stage could drop and its battle count. The final comparison
used the stage ID as text, so an equal-count 15-10 preceded 5-4. A lower battle
count could also put 15-10 first. This reproduces the reported pair in a fixture.
The original run's candidate list is unavailable.

The master material farming picker now compares verified numbered candidates
by chapter and stage as integers. Both 5-4 and 5-10 precede 15-10. Eligibility
still requires the current target, verified preview and full stage identity.
Starred and hidden entries retain their exact IDs and entry verification.
Branch stages participate normally. EX remains the last resort. The previous
ranking resolves ties and selects among special stages when there is no eligible
numbered stage. The new order is used on a fresh automatic selection. A repeat
already in progress continues running its selected stage.

Local tests cover numeric order, battle counts, ineligible stages, EX fallback,
distinct stage identities and the real picker with mode and acquisition proof.
Physical Windows/MuMu navigation was not exercised.

Codex verification recorded for 15bd2e318603ae22b04fee7036bd5758ec166b18. 151 related tests and 32 bundled tests passed without skips. The automatic-picker test exercised the real selection code with simulated device I/O.
