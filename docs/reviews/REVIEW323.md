# v1.0.115 / updater323 — manual focus, fresh board and diagnostic saving

The user reports that manual buttons require clicking the game again, skill changes
leave old fire/water shortage text, and both condition save and F12 fail. Two supplied
independent diagnostics contain 12 samples each. Game HWND97651002 becomes foreground
at06:49:50/06:50:57; the HUD appears about one second later. These show the focus
dependency, not the exact colour inference or callback failure. No current puzzle
pixels were present. Physical colour recognition remains unverified.

Master mode now permits the PAD0 main window as well as its HUD in its foreground
presentation check. Native ROI occlusion, actual puzzle presence, focus expiry and
F9 remain authoritative. A main window covering the puzzle cannot authorize it.
Other modes retain their foreground policy.

On the first changed master board, old preparation and readiness are immediately
retired. The inventory strip shows new-board confirmation instead of the previous
shortage. The original two matching fresh observations still gate readiness. This
does not change any colour model, thresholds, maximum-combo search or input timing.

Master F12 saves a small control-state ZIP immediately, independently of foreground,
busy/OCR/capture state or fresh recognition. It records epochs, flags, last candidate,
presence tickets, recognition errors and search checks; it performs no screenshot,
inference, iconification, game input or worker wait. Missing condition/board originals
also use this fallback. Current matching error originals retain the original image
save behavior. Fallback reports explicitly label the board as last observed and
contain no old images. They never manufacture current game evidence. Other modes
retain their existing diagnostic flow. No reward capture guard is weakened.

Nine new regressions cover own/foreign focus, native occlusion, other modes,
busy/foreign F12 without capture, missing originals, native/search inspection failures, disk failures, first changed
board invalidation and the real Tk inventory strip. Two old save regressions now
expect state-only fallback and still prohibit attaching previous images.

This fixes reproduced control/state defects. It does not establish the exact
runtime colour-count or solver-failure cause on the user's PC. Report222's earlier
automatic input interruption is outside this change and remains unresolved.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43965148009 (success; source 311e048488b47a8bb428dbcf75843909c6bfdc07); 1552 tests, 143 suites. Exact package source SHA256 matches.

Publication verified: PR26 merged as 1ab50cade583cd5accd3f7bbb98b6f94b20b776d.
Its tree matches the Windows-tested head. Publisher run 43967457714 succeeded.
The actual stable latest v1.0.115 download passed SHA256, ZIP CRC, version and updater-state checks.
Its source matches the immutable payload and root update.json activates323.
The genuine v1.0.114 launcher installed323 from the live manifest in an isolated installation,
retained its backup, settings, inventory and four separate stage/branch markers,
and skipped a repeat update. No PAD0 or physical game input was executed.
The exact colour-count and search failure remains unconfirmed from window-only reports;
focus, stale readiness and gated diagnostic writes are addressed by this patch.
