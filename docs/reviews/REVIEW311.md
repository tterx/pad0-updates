# v1.0.103 / updater311 — locked starts, full status matrix and manual HUD

Report213 v102 differs from the prior search/skill failures. A seven-combo,
48-move route was found on a fully locked board. The planned dark orb at3 was
visibly lifted with a lock and thorn; four visual checks nevertheless rejected
its small texture (MAE.12849/correlation.58324). No MOVE was sent, and the original
controller released after2.022s. The user confirms F10 text appeared, so key
reception succeeded; the post-F10 desktop sequence is not present in the ZIP.

Master-only start proof now treats coloured source texture independently of the
padlock/grey metal, fits its actual observed background, and also requires a real
moving lock with keyhole. Keep .90 texture correlation, .08 residual, bounded
opacity and spatial coverage, source/upper change, and remote-board stability.
Locked jammer/poison additionally require their original independently identified
face/body and reconstructed kind. A blue/yellow core cannot stand in for them.
No extra DOWN, recovery click, input backend/pacing change or timer bypass.

The requested all-block test revealed locked poison read as Light, combined
jammer read as Water, missed thorns and a monochrome-hazard readiness rejection.
Explicit master recognition now confirms a yellow-enclosed keyhole even when the
body merges into Light art, reads poison through two independent face views and
jammer through navy body/dark texture/yellow eye, and retains combo metadata.
For a proven upper-left lock, three unoccluded vines must each contain metal AND
outline at .70 (the ordinary four-arm physical proof is retained on normal kinds).
Refine uncertain thin vines by one pixel at three best poses, preserving the .70
threshold. Hazard artwork cannot use the old false-positive physical exception.
An overlay-covered board needs at least24 independently proved hazard glyphs or
matching normal body views with a real lock/vines, plus texture in both axes; flat
menus do not qualify. Propagate the same mode-specific reading
through initial/skill/manual/final/release checks, without changing default modes.
Drop stale manual-probe results from a different mode and do not reuse an old
background run's mode for subsequent manual recognition.

F10 in master mode explicitly drops stale floating mouse/drag capture and hides
retained route drawings that block fresh recognition. Repeating F10 in manual
mode repeats this restoration. After the usual live-board and cleanup gates, the
manual HUD uses the existing detached-owner/no-activation overlay presenter so
an iconified main window cannot suppress it or steal focus back. Other modes use
the existing reveal path. New Windows Tk coverage checks native owner zero and
window visibility after the main root is iconified, plus the existing button action.
These cover concrete UI blockers; they are not a captured replay of the user's
unrecorded manual screen transition. Physical MuMu remains unverified.

27 new test methods use independent expected labels, actual repository artwork,
hand-traced physical overlays, mixed layouts, resolution changes and negative
controls. Fixtures do not derive answers or overlay masks from a detector/model.
64 boards cover1920 cells (8 kinds, four statuses including plain, two artwork
variants); requested lock/thorn/both cases are1440 cells plus480 plain controls.
The private start sweep covers all30 positions:840 positives including a second
artwork variant, and288 wrong-cell/unchanged negatives; zero false accepts/rejects.
No uploaded213 screenshots are published. Native route mocks use real pixel proof,
one DOWN, all MOVEs and the original release; cancellation after DOWN sends no
MOVEs. Existing test callbacks accept the new optional mode keyword; their
assertions remain intact. Windows/package/public ZIP/updater proofs accompany
this review in the release repository.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43723227498 (success; source a12fd1d20b23d416dc32f056241b05a9660c045f); 1,316 executions, 124 suites. Exact package source SHA256 matches.

[Public matrix image](../../releases/v1.0.103/status_matrix311.png) and [case results](../../releases/v1.0.103/status_matrix311.json).

Publication verified: PR14 merged as 9b72b6786516e0e1f6de9ce15163cf38f1c9cfac;
its tree matches the Windows-tested head. Publisher run 43724241861 succeeded.
The actual public stable latest v1.0.103 ZIP passed SHA256, CRC, version and updater-state
checks. Its source equals the live immutable raw payload. Root update.json points to311.
The genuine v1.0.102 launcher installed311 from the live manifest in an isolated
installation, preserved its backup, settings, inventory and stage/branch-history
markers, then skipped a repeat update. No PAD0 or physical game input was executed.
