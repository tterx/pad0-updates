# v1.0.95 / updater303

Six combos are a minimum attack/shield floor, not a stopping target. The attack
optimizer keeps its best replay-verified route while asking feasible-search seeds
for successively higher combo totals. The five-attribute and general engines also
continue after the first valid route; rankings distinguish extra combos instead
of clamping them at six. Stop early only at a known-orb inventory ceiling, or an
explicit diagnostic first-result request. Unknown skyfall is never assumed.
Search remains bounded by time, moves, cancellation and restart limits; the
selected result is the highest verified total found, not a global optimality proof.

Remove HP-driven heart requirements, the separate healing recalculation button
and its dedicated search branch. Recalculation now prepares maximum-combo routes.
Short reduced-time turns also optimize combos rather than searching for hearts
first. Legacy healing-request flags cannot restore the retired product behavior.
HP observations and natural heart-match statistics remain truthful diagnostics.

Compare both master workers before publishing a result, retaining the strongest
valid plan if another worker fails or times out. A speculative minimum route can
bypass optimization only at the proven inventory ceiling. TPA quick candidates
are seeds too: optimize under the selected shape/combo constraints before emitting
the final plan. Readiness never grants automatic input or resumes an old click.

201: the EX4 entry label's binary crop included tiny curved-rim fragments. After
two agreeing but weak complete identifiers, retry two focused RGB views that keep
antialiased letter pixels and omit rim noise. Both reads must still agree at .90;
conflicting confident digits are never overridden. The map still needs the exact
original stage, two stable CLEAR observations and original party before reentry.

202: a translucent Dark sprite over a warm background was classified as Heart.
When ordinary source-texture proof fails, fit the existing alpha-compositing model
against the observed background, then require the restored source color and the
same texture/freshness thresholds. Full input trace and all other final cells
remain mandatory. Wrong-cell and unchanged-frame controls still reject the move.

203: a parent could terminate precomputation after five seconds although a valid
bounded attack/fallback search could need up to seven. Allow an eight-second math
window after worker start; startup is separately bounded. Waiting for both workers
and removing forced healing do not relax fresh-board, time, shape or enemy checks.

Regression fixtures are generated. Private uploaded frames, reports and traces
are excluded. Validation is recorded in TESTING.md. Windows/MuMu hardware input
and live farming completion are not claimed by offline replays or unit tests.

Windows CI: https://github.com/tterx/pad0-all-in-one/actions/runs/43643301916 (success; exact source head bd692c7ea5abc192e016e365d3f28c51483c8b8b). 1,164 test executions in 114 suites, no skipped tests. Portable source SHA256 matches.

Future scope: after this release, only 달인다속 (master multicolor / mode9) may be patched unless the user explicitly expands the scope.
