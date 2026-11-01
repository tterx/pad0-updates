"""Shared version, board/goal constants and atomic JSON I/O; no GUI or models."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


APP_VERSION = "v1.0.50"
APP_RELEASE = 258  # Monotonic updater sequence, independent of display version.

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
    (10, "지우기&콤보", "같은 드롭 4개 이상 연결·6콤보 이상·조작시간 2초 남기기"),
    (1, "드롭 8개 연결", "같은 종류의 드롭 8개 이상 연결"),
    (4, "TPA · 2체 공격", "TPA 각성을 가진 파티원의 속성 드롭을 정확히 4개 매치"),
    (5, "십자팟", "같은 공격 속성 드롭 5개를 십자로 매치"),
    (6, "L자팟", "같은 공격 속성 드롭 5개를 L자로 매치"),
)

SKILL_TACTIC_STATES = (
    ('bound','바인드',('bound',),('bind_clear','cleanse')),
    ('awoken_void','각성 무효',('awoken_void',),('awoken_clear','cleanse')),
    ('time','시간 부족',('time_critical','short_time'),('time',)),
    ('locked','드롭 잠금',('locked',),('unlock',)),
    ('drop_generation','드롭 생성',('drop_generation',),('generation','five_board')),
    ('shortage','5속성 부족',('shortage',),('five_board','refresh')),
    ('combo','콤보 부족',('combo_short',),('bonus',)),
    ('void','데미지 무효',('void_shield',),('void_pierce',)),
    ('absorb','데미지 흡수',('damage_absorb',),('absorb',)),
    ('attribute_absorb','속성 흡수',('attribute_absorb',),('attribute_absorb',)),
    ('spikes','가시 다수',('spikes_many',),('refresh',)),
    ('low_hp','체력 부족',('low_hp',),('heal','shield')),
    ('attack','공격력 감소',('attack_down',),('attack',)),
    ('recovery','회복력 감소',('recovery_down',),('recovery',)),
    ('poison','독 드롭',('poison',),('poison_clear',)),
)


def skill_tactics_party(profiles):
    return [str(profiles[i].get('number') or '') if i<len(profiles) and isinstance(profiles[i],dict) else '' for i in range(6)]


def normalize_skill_tactics(config,profiles):
    """Validate a plan against the ordered current party, including duplicate No.s."""
    if not isinstance(config,dict):raise ValueError('스킬 택틱 설정을 확인해 주세요.')
    party=skill_tactics_party(profiles)
    if config.get('party')!=party:raise ValueError('파티가 바뀌었습니다. 현재 파티를 다시 불러온 뒤 저장해 주세요.')
    orders=config.get('orders',{})
    if not isinstance(orders,dict):raise ValueError('상태별 순서를 확인해 주세요.')
    result={}
    for key,_,_,_ in SKILL_TACTIC_STATES:
        order=orders.get(key,[])
        if not isinstance(order,list) or len(order)>6 or any(type(i) is not int or not 0<=i<6 for i in order) or len(set(order))!=len(order):
            raise ValueError('각 상태에는 서로 다른 파티원 1~6번만 넣을 수 있습니다.')
        if any(not party[i] for i in order):raise ValueError('미등록 파티원은 택틱에 넣을 수 없습니다. 파티 정보를 먼저 등록해 주세요.')
        if order:result[key]=list(order)
    return dict(version=1,enabled=config.get('enabled') is True,party=party,orders=result)


ROWS, COLS = 5, 6

COLOR_ORDER = ["R", "B", "G", "Y", "P", "H", "J", "S"]

PRIMARY_ATTRIBUTES = {"R", "B", "G", "Y", "P"}

# Approximate hue prototypes in degrees; per-cell colors are classified by nearest circular hue.

APP_TITLE = f"퍼드0 경로 오버레이 {APP_VERSION}"

AI_GITHUB_DEFAULT_REPO = "tterx/pad0-learning-data"


class ComboOrb(str):
    """A visible +1 combo badge travels with its orb during swaps."""
    combo_plus = True


def combo_signature(board):
    return sum(1 << i for i,c in enumerate(board) if getattr(c,'combo_plus',False))
