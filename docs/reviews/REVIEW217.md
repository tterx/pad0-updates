# v1.0.9 / updater217 —091 completion on Quit and useful totals

091 had passed exact puzzle layout and post-UP response verification, credited rewards and reached the replay lobby. `complete=true` selected the confirmed Quit glyph at(600,721), and the SDK click/UP jobs completed. The stop came from the subsequent10-second return-header wait:49 captures did not yield two successive accepted headings. The saved lobby image preceded the click; it does not prove whether the game left the lobby. The user explicitly changed completion semantics to the Quit input itself, with a popup/chime and material/mana totals.

## Behavior

- Keep reward crediting and target completion checks. At a confirmed replay lobby, shortage still chooses OK; completion chooses Quit once. Unknown buttons, F10 before input, SDK input or release errors still fail without reporting completion or retrying a click.
- Remove only Quit's return-screen wait. After the normal SDK tap returns successfully, record `replay_completion_scope=quit_input_acknowledged` and `replay_return_screen_required=false`. This is an input acknowledgement, not proof of the game's next screen.
- The worker immediately queues a completion notice before touch/capture cleanup, then exits the run. The Tk thread displays it independently of that cleanup. The later done event cannot duplicate the popup or sound for the same run. Pending AI-review editing still waits until the worker has finished. A new run can notify again.
- The completion popup shows selected materials in group order with their committed total current stock, explicitly labelled `재료 총수량 (현재 보유)`, and `획득 마나` from this automatic run's accumulated mana receipts. Do not substitute the last reward's amount or add the account balance. Mark unknown mana pages if any. The summary contains copied names/numbers rather than live ledger rows.
- Retain the existing small0.45-second PCM chime, asynchronous Windows sound playback, nonmodal topmost popup,15-second dismissal and notification/sound on/off preferences. Size the popup to its content so the close button remains visible.
- Existing completion history retains the summary. Preserve SDK/capture64, saved speeds, start/held/final board checks, skill priority/fallback, inventory accounting, F10 and error ZIPs. No game movement, solver, AI model or updater selection change.

## Checks

Use actual091 button-panel pixels to exercise Quit with a return wait that must never be called. Update the existing two-run worker regression to keep the game in the replay scene even after Quit, require one success notice before device cleanup/done, and preserve exact two-receipt stock accounting. A failed tap still propagates without completion. Retain the actual086 glyph and button ambiguity/F10 checks under the new requested completion semantics.

Four focused cases check the091 panel, selected current-stock totals versus the last receipt/account balance, actual Tk popup content/close-button visibility, one popup/sound request per run, deferred AI review, a later run and unknown mana display. Existing sound regressions verify the cached quiet WAV amplitude/duration and one asynchronous sound per run. Run critical plus replay/notification/sound cases and Windows DLL/Tk/packaging checks; do not run the entire suite or operate a real emulator.

This Linux workspace is not connected to the user's MuMu. Host input sequencing and scripted UI/worker completion are verified; actual game screen transition and audible output on the user's PC are not claimed. The game returning to a recognized header is intentionally no longer required for completion.
