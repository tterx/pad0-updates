# v1.0.29 / updater237

108 reports v1.0.27, an executed17-edge path with matched held-board verification, and a stale board-clear fallback despite requested TPA. This is not evidence of failed input delivery. Clear the previous fallback when current party goals are TPA.

A bounded TPA search miss previously latched the board indefinitely. A same-board miss with enough eligible orbs now retries after2 seconds without input. Real inventory shortage remains latched. Successful preview still waits for explicit execution.

Manual passive cloud observation may use provisional J for unknown cells. These values are not certified in cloud memory or training labels. Background skill/probe ordering remains unchanged. Previously a failed reveal invalidated memory, so the next passive observation could return to the pending state indefinitely. Manual presearch and final capture now consistently accept provisional unknowns. Hidden spike evidence stays unknown/conservative.

Observed enemy combo floors are required for TPA metrics, ranking and final manual execution. Optional team6 is still optional when no enemy floor exists. Enemy6-or-less nullification requires7, including observed skill and matched-orb bonuses. Combo skills can be selected for this enemy condition.

Tests cover enemy-floor rejection/additive bonus, uncertified passive cloud J and automatic same-board retry without input. Existing TPA optional-six tests now explicitly use no enemy floor. Original108 board classifier/grid diagnostics passed. Original inline cloud image has no local PNG, so its exact hidden drops and physical MuMu behavior are unverified. Frozen combo badge model is unchanged and its positive original-game validation remains outstanding.
