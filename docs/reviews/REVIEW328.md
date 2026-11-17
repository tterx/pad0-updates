# v1.0.119 / updater328 — board-only route HUD without taskbar registration

The user confirms that v118 restored the route button and now requests a pure
route overlay: no separate taskbar entry, continuous display while the puzzle is
visible, and automatic disappearance when the puzzle ends. Other modes retain
the existing maintenance scope. Checks, packaging and publication run in Codex,
with no GitHub Actions run.

The ordinary master path previously used Tk deiconify/lift. Only the F10 manual
handoff used the detached no-activate helper, which added TOOLWINDOW but did not
clear APPWINDOW or retain Tk's own tool-window policy. Repeated withdraw/reveal
could therefore expose taskbar registration. All master route presentations now
share the same path. Tk remembers `-toolwindow` before any mapping. Once the route
wrapper exists, native preparation clears APPWINDOW, sets TOOLWINDOW/NOACTIVATE
and detaches its owner before showing it; preparation repeats after mapping and
alpha restoration to cover wrapper recreation. It never changes the main root's
window flags; a matching root handle is rejected. Creation only records the Tk
policy and does not style a not-yet-materialized native wrapper. Live HUD ticks
do not remap the window or repeatedly apply frame styles. Normal and F10 manual
play use the same presentation; other modes retain their prior path.

A master-only, one-time preference migration turns off the earlier emergency
always-visible setting and sets its checkbox accordingly. It runs at master
startup or on later master selection and is persisted through the normal settings
writer. Other modes are not changed. The always-visible option remains available
if explicitly enabled again after this migration, preserving the earlier request
for that diagnostic option; the new default is puzzle-conditioned display.

A visible master HUD tolerates one uncertain/negative sample without withdrawing.
Two distinct current negative captures confirm absence; repeating one result is
not a second capture. A fresh positive clears the negative count. Positive pixels
may reveal a hidden HUD only within the original1.2-second freshness limit. An
already mapped normal HUD can survive a delayed sample for at most2.5seconds from
its last positive capture, without changing that timestamp. Capture errors do not
count as negative frames and cannot extend the grace indefinitely. Native cover,
focus/geometry/epoch/context changes and a confirmed nonbattle transition still
invalidate display immediately. No retained route or readiness flag is used as
proof of a visible puzzle. F9 and capture/input hides remain authoritative; master
playback now withdraws the clickable HUD even with the always-visible option off.
None of this display evidence grants recognition readiness or game input.

Fifteen new regressions test normal/F10 native style ordering over repeated maps,
no APPWINDOW bit at presentation, persistent Tk tool policy, wrapper recreation,
no live remapping, main-root isolation, other-mode styles, one-time preference
migration, forty alternating positive/missed controller ticks with one reveal,
two independent negatives, bounded slow/error grace, hard menu/cover invalidation,
no hidden-HUD revival from uncertainty, F9/input hides, and real Tk hide/restore
and button action. Two focused cases fail on v118 and pass after the change.
Existing real-Tk/Win32-mock fixtures now distinguish two absence captures and
isolate their X-server presentation from simulated Windows ownership checks;
native tool-window behavior is covered by the dedicated API fixtures.

Related suites and the actual bundled program are tested locally. No physical
Windows shell/taskbar or MuMu game session was run in this Linux environment.
The v118 mode-query latency fix and delayed-entry re-capture are unchanged. Solver,
models, input modules, maximum-combo policy, stage identities and branch records
are unchanged. Published metadata records final test counts, hashes, downloaded
ZIP verification and the genuine launcher update with personal data preserved.

Validation: source 8c276fd581437391a322227a1f665e09700ffa69;297 related tests in25 suites,50 bundled tests, no failures/errors/skips. Fifteen new regressions;two focused cases failed on v118 and passed after fix. No Actions executed.

Publication verified: PR30 merged as 6b8b0ac67eef6d9eb30fea610b78f08799c0ec1b.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.119 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.119 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates328.
The unchanged launcher installed328 from the live manifest over v1.0.118 in an
isolated installation at state327, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
