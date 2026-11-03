# v1.0.61 / updater269

Increase the four entry controls from 9pt/8px/4px to 11pt/10px/5px (font/horizontal/vertical padding), retaining the two-by-two requested-size layout, anchor, geometry cache and all callbacks. Dimensions follow platform fonts and DPI.

On the second matching board sample, cancel the pending presearch timer and schedule a zero-delay Tk callback. This removes up to 300ms of idle scheduling delay after stability confirmation without skipping the two samples or any current visibility/board/condition checks. Wake once per candidate, not on every subsequent sample. A stopped scheduler stays stopped.

After receiving a worker notification, yield to Tk and process the next notification without the former 75ms interval while the worker remains active. Empty queues retain the existing 75ms polling interval. This helps queued intermediate results or multiple workers; it does not shorten mathematical search or OCR. No thread calls Tk, no input is automatic, no search budgets or execution proof rules change.

Four regressions cover second-sample wake/coalescing, stopped lifecycle, queued message delivery returning to normal polling, and current visibility gates after waking. Existing actual Tk tests cover all actions, clipping, fixed layout and geometry caching. Timing savings above describe removed scheduler waits, not measured end-to-end MuMu speed. Original screenshots and diagnostic archives remain private.
