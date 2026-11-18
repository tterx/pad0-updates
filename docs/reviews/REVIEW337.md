# v1.0.128 / updater 337 — Wait for MuMu Android services during active farming recovery

Report 233 ran v1.0.126. The last 38-edge drag matched the computed layout.
The game reacted to UP and the hand release was confirmed. The next Android
foreground read could not identify a running activity. The recovery handler
stopped at the initial Android HOME lookup because the package service
answered `cmd: Can't find service: package`. Its action list was empty. The
report does not establish why Android stopped reporting that service.

During an active master material farming run, the recovery handler waits up to
45 seconds when a selected-device read reports a missing Android service or
`error: closed`. It retries fixed read-only queries. Two foreground readings
must agree, and the current home is verified again after resolving the game
launch component. On the confirmed launcher it follows the existing login
and farming re-entry flow. If the game is still open and only the worker's
outer capture failed, it captures the current screen and continues that
same farming session. The run's reward accounting stays attached to that
session. While the game loads, transient read failures clear the previous
screen proof and the next action needs two new readings.

F10 stops the wait. A launch or tap with uncertain delivery is never retried
by this handler. Pending touch ownership continues to block recovery. No
ADB daemon or emulator restart is sent. The setup warmup path remains outside
this change. Automated tests use simulated Android responses and actual worker
logic. Physical MuMu recovery is unverified.

Codex verification recorded for 63d81961e5cd5914070220b0bbd84dc4d7fdb451. 143 related tests and 55 bundled tests passed without skips. The worker recovery test used simulated device I/O without launching Android.
