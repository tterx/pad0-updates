# v1.0.129 / updater 338 — Continue when a crystal receipt has a capacity warning

234 and 235 record the same automatic-farming receipt stop. The captured
result displays the maximum-inventory notice, a red warning triangle on the
first card, a thumb at the top and no Next button. The fresh pre-scroll check
then rejected the view. The original report saved the preceding capture,
so the exact pixels that triggered that rejection are unavailable. The
235 receipt.png is from an earlier successful result.

Master material farming recognizes the notice at two OCR scales and finds
the triangular warning on individual cards. It excludes the marked card
from the credited amount, carries that exclusion across overlapping pages
and continues to read ordinary cards below. The offer shown on the marked
card does not establish a partially accepted amount. Such a card is excluded
without changing its saved inventory. Capped receipts do not add a branch
no-drop miss. Verified drops remain eligible for branch learning.

Before scrolling, the worker reads a fresh frame. It retries brief incomplete
frames, and a known capacity receipt may be identified by the gold rails,
neutral card panel and current result title. Scrolling still requires
overlapping cards and a confirmed end. A visible Next button supplies the
usual permission to advance. F10 prevents further touches. Other modes and
manual collection retain their previous recognition and accounting.

The actual 234 frame produced the notice twice at confidence above .98 and
exactly one marked card out of ten complete cards. The earlier normal
235 receipt had no marked cards. The worker regression uses a representative
scrolled receipt and controlled device input to exercise lower cards,
deduplication and verified onward navigation. Physical MuMu is unverified.

Codex verification recorded for 4d44f59bfacb40dc9a16a7815535c900235bd552. 160 related tests and 75 bundled tests passed without skips. The actual 234 report confirmed the notice and one marked card. The worker test used simulated scroll and device I/O.
