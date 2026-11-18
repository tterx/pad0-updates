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

Publication verified: PR38 merged as ac562c736d1cdd1565b414f74d510569f9e0050a.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.127 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.127 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates336.
The unchanged launcher installed336 from the live manifest over v1.0.126 in an
isolated installation at state335, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
