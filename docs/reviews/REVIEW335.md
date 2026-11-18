# v1.0.126 / updater 335 — Resume material farming after the game exits

Report 231 stopped before the first touch of the computed route. The Android
foreground check could not identify an activity. The saved battle pixels predate
that check. The user later saw the MuMu launcher and demonstrated the startup
sequence. The available evidence does not determine why the game exited.

During an active 달인다속 material farming run, a foreground failure starts a
bounded recovery check on the same Android device. It must identify the resolved
Android HOME package twice. A different app, uncertain touch ownership or F10
prevents a relaunch. PAD0 resolves and starts the launch activity of the exact
game package registered by the run. It sends each startup touch once after two
matching observations. It waits for the GungHo splash, taps once, then taps
시작 when the title button is confirmed. It taps the center of the populated
first save card and taps 아니요 only on the specific interrupted-dungeon prompt.
If the prompt is absent, it waits for the game menu. A slow launch has up to
three minutes to reach verified game menu controls.

The interrupted attempt's reward session closes without a new result. Committed
inventory, mana and branch receipts remain available. The worker locates the
current registered shortage, uses the existing entry and party checks, and starts
a fresh run. For a registered breakthrough target it enters 육성 and searches with
spaces removed, using the saved monster number to verify the result. Registered
creation targets follow their existing creation path with the compact query.
A crash does not count as a dungeon defeat. A failure before the first fresh
battle after relaunch stops the recovery sequence. Automatic repeat and other
modes do not enter this recovery path.

Regression checks exercise the active worker, the foreground error, launcher
gating, slow startup, saved slot and prompt recognition, F10, compact Korean
search and actual award accounting after re-entry. Startup views in the automated
tests are simulated. Physical Windows/MuMu relaunch and login are unverified.
Builds and tests run in Codex. The package generator shortens only the generated
statement indentation and checks AST equality to fit the existing updater limit.

Codex verification recorded for 10f7486679aebb08247889c4a7c6d3f60b3bf340. 461 related tests and 222 bundled tests passed without skips. End-to-end work used simulated device I/O and real OCR on synthetic startup screens.

Publication verified: PR37 merged as a639799e8acd3134ff5bd59c856998f6839ace94.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.126 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.126 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates335.
The unchanged launcher installed335 from the live manifest over v1.0.125 in an
isolated installation at state334, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
