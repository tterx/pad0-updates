# v1.0.111 / updater319 — ordinary manual startup has no board context

The user confirms the button is absent during ordinary manual play before F10,
and F6 also appears unresponsive. v110 decoupled presence from solver readiness,
but its tests fixed the saved rectangle/window mapping and visibility checks.
They did not cover booting without an assigned rectangle, or the actual window
anchor changing after a move/resize. No current runtime ZIP or locally accessible
inline screenshot is available, so the user's exact native state is not claimed.

Three remaining control-flow defects are reproducible. The full-window periodic
probe published battle samples only after an F10 handoff; ordinary manual play
never received that bootstrap. With no saved board region, the presence worker
cannot start, and the attribute probe asks for a region. A moved/resized anchor
also yields a new relative rectangle while board_rect stays old; the button gate
keeps rejecting their mismatch. Finally, v110's F6 unready wait creates an empty
rectangle ticket then immediately cancels it against board_rect=None. These are
additional paths, not a claim that the exact previous screenshot was replayed.

For master_multicolor ordinary manual play only, the existing full-window probe
now emits its fresh complete/partial battle observation even without F10. The UI
consumer rechecks action epoch, current host HWND+bounds, actual foreground,
minimized/visible state, mouse state and all board hit-test points before adopting
the observed scaled board rectangle. Unknown/menu frames cannot invent a region.
Busy/automatic/capture/route sessions, old frames, changed hosts, stale epochs and
stopped actions reject it. Existing F10 messages and other modes stay unchanged.

A verified geometry change retires old preparation, advances recognition/proof
generations and synchronizes the board anchor and absolute rectangle in current
settings. Repeated same-geometry frames do not keep resetting recognition. F9 and
temporary capture hiding remain untouched. Two matching ordinary observations can
use the original readiness pipeline. No expected colours or route are supplied.
The real foreground gate also explicitly declares pointer-sized HWND results.

F6 before initialization wakes the whole-window probe. A master ticket may wait
while both its region and the current region are absent. Only the verified UI
consumer may attach the resulting new region to an owned board-less request;
its original12-second deadline remains unchanged. A complete/old-route request
cannot follow a different region. Readiness/preparation still requires another
execution click, and all final board/condition/timer/held-layout/release gates
remain unchanged. F12 diagnostics now retain the host-context decision and current
presence proof so a future device-specific block need not be inferred blindly.

Twenty-one new checks execute the actual normalized anchor transformation and
Win32 visibility gate with an emulated API, rather than forcing those helpers true.
They cover cold ordinary startup without F10, stale absolute coordinates, resized
and negative-monitor positions, real full-frame producer and real UI queue
consumer, moved-region ROI capture, occlusion/foreground/minimize/mouse gates,
stale epoch/host/frame, repeated stable readiness, menu and other-mode controls,
F9/capture hiding, F6 key polling, bounded pending requests and pointer-size setup.
A real Tk button boots through that queue without a saved region or F10; its
actual geometry placement is used. Capture and native OS responses are controlled;
no physical MuMu run or current exact screenshot replay is claimed.

Maximum-combo/no-forced-healing policy, frozen eight-drop models/thresholds, skill
strategy, movement timing/budgets, branch learning, EX ranking and separate
ordinary/starred/Hidden records retain their existing behavior.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43814407910 (success; source 772878d80418ca29e2a802696e1c6b219837f380); 1,506 executions, 139 suites. Exact package source SHA256 matches.

[Public replay comparison](../../releases/v1.0.111/replay319.json).

Publication verified: PR22 merged as 2604a2b25bd23204c5fca7bc53e4b74b6f2685f6;
its tree matches the Windows-tested head. Publisher run 43816084780 succeeded.
The actual public stable latest v1.0.111 ZIP passed SHA256, CRC, version and updater-state
checks. Its source equals the live immutable raw payload. Root update.json points to319.
The genuine v1.0.110 launcher installed319 from the live manifest in an isolated
installation, preserved its backup, settings, inventory and stage/branch-history
markers, then skipped a repeat update. No PAD0 or physical game input was executed.
