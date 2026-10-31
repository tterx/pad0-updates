# v1.0.43 / updater251

Fresh material observations must replace old quantities after crafting or selecting a higher breakthrough requirement. Three red requirements of six with zero stock must not keep older 2/2, 2/2 and 2/0 values.

The selected row's current requirement and stock now reach target activation without being replaced by historical per-icon manual fields. The database table prioritizes the active farming group's requirement and canonical inventory, retaining manual fallback only for entries without a current observation. A complete F7 snapshot also updates an existing target's requirement, stock, shortage and completion flag atomically with the inventory.

When a freshly scanned draft icon is subsequently linked to an existing material name, its observed inventory, including zero, transfers to the canonical ID. Previously an existing image caused the older canonical stock to survive. Scan provenance is retained on the group row; legacy drafts without this observation retain the previous fallback. Canonical names, acquisition stages and accumulated reward history are preserved.

Eight regressions cover the four-card 6/0,6/0,6/0,15/46 panel, persistent replacement of older values, target activation, completed-target reset, actual database table values despite stale candidates/manual fields, canonical draft naming, legacy fallback and failed-read preservation. A generated four-card panel also passes the real Korean RapidOCR path and persistent registration. The inline user screenshot was visually inspected; its exact pixels and physical MuMu gameplay were not replayed. No training store or new model is introduced.
