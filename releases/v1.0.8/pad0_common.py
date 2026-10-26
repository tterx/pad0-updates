"""Shared version, board/goal constants and atomic JSON I/O; no GUI or models."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


APP_VERSION = "v1.0.8"
APP_RELEASE = 216  # Monotonic updater sequence, independent of display version.

COLOR_INFO = {
    "R": ("불", "#ee5547", 5),
    "B": ("물", "#269de5", 210),
    "G": ("나무", "#42bd65", 135),
    "Y": ("빛", "#f4c94d", 45),
    "P": ("어둠", "#7650b9", 285),
    "H": ("회복", "#f08ac9", 340),
    # All jammer designs (including the dark-blue spiky icon) are grouped as J.
    # They can be matchable for combo counting but are not one of the five attributes.
    "J": ("방해", "#b2a08b", 25),
    # Skull/poison hazard: still counts as a match/combo, but the solver can
    # prefer routes that do not clear it because matching it harms the player.
    "S": ("독", "#79639f", 285),
}

COLOR_HUE_ORDER = ["R", "B", "G", "Y", "P", "H"]

def _atomic_json_write(path: Path, data: dict) -> None:
    """Write JSON atomically so an interrupted update does not corrupt user data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _load_json_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}

ROUTE_GOAL_OPTIONS = (
    (0, "10콤보", "드롭을 나눠 10콤보 달성"),
    (2, "다색팟 · 5속성", "불·물·나무·빛·어둠을 모두 매치"),
    (9, "달인 다색", "5속성·6콤보 이상 · 조작시간 4초 남기기"),
    (1, "드롭 8개 연결", "같은 종류의 드롭 8개 이상 연결"),
    (4, "TPA · 2체 공격", "같은 공격 속성 드롭을 정확히 4개 매치"),
    (5, "십자팟", "같은 공격 속성 드롭 5개를 십자로 매치"),
    (6, "L자팟", "같은 공격 속성 드롭 5개를 L자로 매치"),
)

ROWS, COLS = 5, 6

COLOR_ORDER = ["R", "B", "G", "Y", "P", "H", "J", "S"]

PRIMARY_ATTRIBUTES = {"R", "B", "G", "Y", "P"}

# Approximate hue prototypes in degrees; per-cell colors are classified by nearest circular hue.

APP_TITLE = f"퍼드0 경로 오버레이 {APP_VERSION}"

AI_GITHUB_DEFAULT_REPO = "tterx/pad0-learning-data"
