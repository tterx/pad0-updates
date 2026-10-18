# -*- coding: utf-8 -*-
"""퍼드0 퍼즐 경로 오버레이 - Windows prototype."""
from __future__ import annotations

import ctypes
import hashlib
import uuid
import math
import random
import re
import threading
import queue
import time
import tkinter as tk
import json
import os
import sys
import subprocess
import shutil
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import mss
import numpy as np
from PIL import Image, ImageDraw

APP_VERSION = "v101.74"
APP_TITLE = f"퍼드0 경로 오버레이 {APP_VERSION}"
ROWS, COLS = 5, 6
# Approximate hue prototypes in degrees; per-cell colors are classified by nearest circular hue.
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
# COLOR_ORDER includes every class available in the manual correction UI.
COLOR_ORDER = ["R", "B", "G", "Y", "P", "H", "J", "S"]
# Automatic region detection should use only the six normal colour families.
COLOR_HUE_ORDER = ["R", "B", "G", "Y", "P", "H"]
PRIMARY_ATTRIBUTES = {"R", "B", "G", "Y", "P"}
# No fixed path-length cap: search depth is limited by the time budget and beam width.
SOLVER_MAX_MOVES = None
SOLVER_TIME_BUDGET = 5.0
SOLVER_BEAM_WIDTH = 850
# Dedicated attribute search; separate from normal combo-search limits.
FIVE_ATTR_TIME_BUDGET = 3.0  # Fail promptly; enemy requirements remain mandatory.
FIVE_ATTR_BEAM_WIDTH = 180
FIVE_ATTR_DEFAULT_MOVE_CAP = 78
PRESEARCH_WAIT_LIMIT = 3.0  # Total waiting, including pauses; parent enforces the same ceiling.

def five_attribute_inventory(board):
    """Count normal attack orbs before launching expensive 5-attribute search."""
    return {key: sum(orb == key for orb in board) for key in ("R", "B", "G", "Y", "P")}

def five_attribute_possible_by_count(board):
    return all(count >= 3 for count in five_attribute_inventory(board).values())


def inventory_route_goals(board, requested):
    """Explicit calculation on an impossible five-color inventory uses combos.

    No hard 10-combo target is added; preserve every other mode unchanged.
    """
    goals=tuple(bool(v) for v in requested[:7])+tuple(requested[7:])
    fallback=(len(board)==ROWS*COLS and all(v in COLOR_ORDER for v in board)
              and len(goals)>2 and goals[2] and sum(goals[:7])==1 and not master_multicolor(goals)
              and not five_attribute_possible_by_count(board))
    return ((False,)*len(goals) if fallback else goals),bool(fallback)


def five_attribute_inventory_status(board):
    """Inventory is a necessary condition, not proof that a route was found."""
    counts = five_attribute_inventory(board)
    missing = [f"{COLOR_INFO[color][0]} {count}/3" for color, count in counts.items() if count < 3]
    if missing:
        return "5속성 수량 부족 · " + ", ".join(missing), "#ffb2a9"
    return "5속성 수량 충족 · 경로 미확인", "#84edbc"



def _user_data_paths() -> dict[str, Path]:
    """Persistent per-user storage; independent of the replaceable program folder."""
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    root = base / "Pad0Overlay"
    db_dir = root / "database"
    icons_dir = db_dir / "crystal_icons"
    root.mkdir(parents=True, exist_ok=True)
    db_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "crystal_db.json"
    records_path = db_dir / "user_records.json"
    ledger_path = db_dir / "crystal_ledger.json"
    board_history_path = root / "board_history.json"
    settings_path = root / "settings.json"
    # Create starter files only if absent; never overwrite a database the user has built.
    if not db_path.exists():
        starter = {
            "schema_version": 1,
            "description": "퍼드0 크리스탈 종류/아이콘 데이터베이스. 새 버전은 기존 파일을 덮어쓰지 않고 항목을 병합해야 합니다.",
            "crystals": []
        }
        _atomic_json_write(db_path, starter)
    if not records_path.exists():
        _atomic_json_write(records_path, {"schema_version": 1, "records": []})
    if not ledger_path.exists():
        _atomic_json_write(ledger_path, {"schema_version": 1, "counts": {}, "events": []})
    if not board_history_path.exists():
        _atomic_json_write(board_history_path, {"schema_version": 1, "boards": []})
    return {"root": root, "database_dir": db_dir, "icons_dir": icons_dir,
            "database": db_path, "records": records_path, "ledger": ledger_path,
            "board_history": board_history_path, "settings": settings_path}


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


def exclude_own_window_from_capture(window) -> bool:
    """Best-effort Windows DWM screenshot exclusion for the 12-o'clock HUD.

    Does not hide it from the user. Unsupported Windows versions return False;
    reward OCR is still restricted to the central game result dialog.
    """
    if sys.platform != 'win32':
        return False
    try:
        user32 = ctypes.windll.user32
        from ctypes import wintypes
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        top = user32.GetAncestor(int(window.winfo_id()), 2) or int(window.winfo_id())
        # WDA_EXCLUDEFROMCAPTURE, Windows 10 2004+; never alter global capture.
        return bool(user32.SetWindowDisplayAffinity(top, 0x11))
    except (OSError, ValueError, tk.TclError, AttributeError):
        return False


def fit_image_preserve_aspect(image: Image.Image, size: tuple[int, int], bg=(255, 255, 255)) -> Image.Image:
    """Fit a thumbnail inside a fixed canvas without stretching the original aspect ratio."""
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    source = image.convert("RGB")
    source.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), bg)
    canvas.paste(source, ((width - source.width) // 2, (height - source.height) // 2))
    return canvas

# Use per-monitor DPI awareness so Tk mouse coordinates and desktop screenshots
# line up on Windows displays using scaling. Fall back on older Windows APIs.
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def circular_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def rgb_to_hsv_np(rgb: np.ndarray):
    arr = rgb.astype(np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.max(arr, axis=-1)
    mn = np.min(arr, axis=-1)
    delta = mx - mn
    hue = np.zeros_like(mx)
    nz = delta > 1e-5
    mr = nz & (mx == r)
    mg = nz & (mx == g)
    mb = nz & (mx == b)
    hue[mr] = (60.0 * ((g[mr] - b[mr]) / delta[mr]) % 360.0)
    hue[mg] = 60.0 * ((b[mg] - r[mg]) / delta[mg] + 2.0)
    hue[mb] = 60.0 * ((r[mb] - g[mb]) / delta[mb] + 4.0)
    sat = np.zeros_like(mx)
    valid = mx > 1e-5
    sat[valid] = delta[valid] / mx[valid]
    return hue, sat, mx


def dominant_hue(cell_rgb: np.ndarray) -> float:
    """Estimate orb hue from the saturated central area, rejecting pale grid/glare pixels."""
    h, s, v = rgb_to_hsv_np(cell_rgb)
    yy, xx = np.ogrid[:cell_rgb.shape[0], :cell_rgb.shape[1]]
    cx, cy = (cell_rgb.shape[1] - 1) / 2.0, (cell_rgb.shape[0] - 1) / 2.0
    radius = min(cell_rgb.shape[0], cell_rgb.shape[1]) * 0.44
    d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) ** 0.5
    mask = (d2 >= radius * 0.28) & (d2 <= radius) & (s >= 0.32) & (v >= 0.24) & (v <= 0.98)
    values = h[mask]
    if values.size < 10:
        mask = (s >= 0.22) & (v >= 0.2) & (v <= 0.99)
        values = h[mask]
    if values.size == 0:
        return 0.0
    hist, edges = np.histogram(values, bins=np.arange(0, 371, 10))
    idx = int(np.argmax(hist))
    return float((edges[idx] + edges[idx + 1]) / 2.0) % 360.0


def hue_to_color(hue: float) -> str:
    """Map a chromatic hue to one of the six normal drop types (never jammer)."""
    return min(COLOR_HUE_ORDER, key=lambda code: circular_distance(hue, COLOR_INFO[code][2]))


def classify_cell(cell_rgb: np.ndarray) -> tuple[str, float]:
    """Classify a board cell using hue, saturation, brightness and purple/pink RGB balance.

    Dark (purple) and recovery (pink) drops can share bright magenta highlights.
    Their red/blue balance over many saturated pixels is a stronger cue than a
    single dominant hue. Low-chroma/brown cells are classified as nuisance drops.
    """
    h, s, v = rgb_to_hsv_np(cell_rgb)
    yy, xx = np.ogrid[:cell_rgb.shape[0], :cell_rgb.shape[1]]
    cx, cy = (cell_rgb.shape[1] - 1) / 2.0, (cell_rgb.shape[0] - 1) / 2.0
    radius = min(cell_rgb.shape[0], cell_rgb.shape[1]) * 0.46
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    disc = (dist <= radius) & (v >= 0.16) & (v <= 0.995)
    if int(disc.sum()) < 16:
        disc = (v >= 0.16) & (v <= 0.995)
    sat_values = s[disc]
    val_values = v[disc]
    if sat_values.size == 0:
        return "J", 0.0

    median_sat = float(np.median(sat_values))
    chromatic_fraction = float(np.mean(sat_values >= 0.30))
    hue = dominant_hue(cell_rgb)

    # Detect the ordinary dark-blue spiky jammer BEFORE hue classification.
    # In the user's screenshot this icon has a dark navy body, blue/cyan edges,
    # very few bright pixels and many low-value blue pixels. A normal Water orb
    # is also blue, but its central area is bright and has almost no deep-dark
    # pixels, so it does not satisfy this combined signature.
    disc_h = h[disc]
    disc_s = s[disc]
    disc_v = v[disc]
    dark_blue_fraction = float(np.mean(
        (disc_h >= 185.0) & (disc_h <= 255.0) & (disc_s >= 0.28) & (disc_v <= 0.62)
    ))
    deep_dark_fraction = float(np.mean(disc_v < 0.28))
    bright_fraction = float(np.mean(disc_v > 0.70))
    median_value = float(np.median(val_values))
    if ((dark_blue_fraction >= 0.28
            and deep_dark_fraction >= 0.22
            and bright_fraction <= 0.22
            and median_value <= 0.62) or
        (dark_blue_fraction >= .55 and deep_dark_fraction >= .20
            and bright_fraction <= .35 and median_value <= .58)):
        # A glow behind the jammer can add bright edge pixels. Its large dark
        # navy face still differs from Water, which lacks the deep-dark core.
        return "J", hue

    # Detect the skull/poison tile by its icon shape, not hue alone. Its purple
    # frame can look like a Dark orb to a hue classifier, but the skull face has
    # a large pale/white area inside a mostly purple ring. Normal Dark orbs in
    # the supplied screenshot have very little pale area and a much larger
    # fraction of saturated purple pixels. Use the central disc to avoid grid
    # lines and bright effects outside the orb.
    pale_fraction = float(np.mean((s[disc] < 0.30) & (v[disc] > 0.52)))
    purple_fraction = float(np.mean(
        (h[disc] >= 250.0) & (h[disc] <= 322.0) & (s[disc] >= 0.25)
    ))
    central_face = (np.abs(xx - cx) <= radius * 0.56) & (np.abs(yy - cy) <= radius * 0.60)
    face_mask = central_face & disc
    face_white = float(np.mean((s[face_mask] < 0.24) & (v[face_mask] >= 0.66))) if face_mask.any() else 0.0
    face_black = float(np.mean(v[face_mask] <= 0.19)) if face_mask.any() else 0.0
    # Locked skulls have a yellow lock in the upper-left, which can shift the
    # dominant hue to light (Y). The pale skull with two dark eyes is stronger
    # evidence than that incidental hue; normal light orbs have no dark eye pair.
    if ((245.0 <= hue <= 320.0 and pale_fraction >= 0.27
         and 0.16 <= purple_fraction <= 0.72) or
        (face_white >= 0.35 and face_black >= 0.07 and purple_fraction >= 0.09)):
        return "S", hue

    # A true neutral/grey block has very little saturated material. A muted
    # brown jammer tends to sit in the orange-yellow hue range with lower
    # saturation than the regular fire/light orbs.
    if (chromatic_fraction < 0.16 and median_sat < 0.23) or (
        18.0 <= hue <= 39.0 and median_sat < 0.58 and float(np.median(val_values)) < 0.93
    ):
        return "J", hue

    # Disambiguate purple Dark vs pink Recovery. In purple pixels blue tends to
    # exceed red; in pink/heal pixels red tends to exceed blue. Use many pixels
    # rather than the single brightest icon/highlight. Exclude near-red hues so
    # Fire is not accidentally interpreted as Recovery.
    if 270.0 <= hue <= 352.0:
        rgb = cell_rgb.astype(np.float32) / 255.0
        red, blue = rgb[..., 0], rgb[..., 2]
        purple_pink_band = disc & (s >= 0.24) & (v >= 0.20) & (h >= 255.0) & (h <= 359.0)
        if int(purple_pink_band.sum()) >= 6:
            weights = s[purple_pink_band] * (0.55 + 0.45 * v[purple_pink_band])
            # Positive means blue-dominant (Dark); negative means red-dominant (Recovery).
            blue_minus_red = (blue[purple_pink_band] - red[purple_pink_band])
            balance = float(np.average(blue_minus_red, weights=np.maximum(weights, 1e-4)))
            if balance >= 0.035:
                return "P", hue
            if balance <= -0.035:
                return "H", hue

    return hue_to_color(hue), hue


def classify_board(image: Image.Image) -> tuple[list[str], list[list[float]]]:
    arr = np.asarray(image.convert("RGB"))
    h, w, _ = arr.shape
    cw, ch = w / COLS, h / ROWS
    result: list[str] = []
    hues: list[list[float]] = []
    for r in range(ROWS):
        row_codes, row_hues = [], []
        for c in range(COLS):
            x0 = int(c * cw + cw * 0.20)
            x1 = int((c + 1) * cw - cw * 0.20)
            y0 = int(r * ch + ch * 0.20)
            y1 = int((r + 1) * ch - ch * 0.20)
            crop = arr[y0:y1, x0:x1]
            code, hue = classify_cell(crop)
            # A yellow padlock can win the center hue histogram (the uploaded
            # locked Wood at row 2/column 1 was read as Light). Require two
            # independent badge-free areas to agree before replacing yellow.
            if code == 'Y' and min(crop.shape[:2]) >= 12:
                right_code,right_hue=classify_cell(crop[:,int(crop.shape[1]*.23):])
                bottom_code,bottom_hue=classify_cell(crop[int(crop.shape[0]*.23):,:])
                if right_code==bottom_code and right_code in ('R','B','G','P','H'):
                    code,hue=right_code,bottom_hue
            # v72: re-evaluate suspicious blue/spike-like cores using a broader
            # cell crop. Dust-shaped jammer graphics are dark in the centre and
            # can resemble a blue orb with crossed blades at certain frames.
            if code == "B":
                wide = arr[int(r*ch+ch*0.10):int((r+1)*ch-ch*0.10),
                           int(c*cw+cw*0.10):int((c+1)*cw-cw*0.10)]
                if wide.size:
                    wide_code, _ = classify_cell(wide)
                    if wide_code == "J":
                        code = "J"
            row_hues.append(hue)
            row_codes.append(code)
        result.extend(row_codes)
        hues.append(row_hues)
    return result, hues



def detect_spike_cells(image: Image.Image) -> list[bool]:
    """Detect the metallic crossed-thorn overlay per cell without replacing its orb colour.

    The user's MuMu screenshot shows two diagonal silver blades with dark outlines.
    We measure low-saturation bright metal and dark outline pixels along both diagonals.
    This is a visual heuristic, not a game-data API; ambiguous cells remain unmarked.
    """
    arr = np.asarray(image.convert("RGB"))
    h, w, _ = arr.shape
    cw, ch = w / COLS, h / ROWS
    result: list[bool] = []
    for r in range(ROWS):
        for c in range(COLS):
            x0 = int(c * cw + cw * 0.20)
            x1 = int((c + 1) * cw - cw * 0.20)
            y0 = int(r * ch + ch * 0.20)
            y1 = int((r + 1) * ch - ch * 0.20)
            tile = arr[y0:y1, x0:x1]
            if tile.size == 0:
                result.append(False)
                continue
            _, sat, val = rgb_to_hsv_np(tile)
            hh, ww = sat.shape
            yy, xx = np.mgrid[0:hh, 0:ww]
            xn = (xx - (ww - 1) / 2.0) / max(1.0, ww / 2.0)
            yn = (yy - (hh - 1) / 2.0) / max(1.0, hh / 2.0)
            # A yellow lock badge is drawn on the orb's upper-left quadrant;
            # never treat its padlock edges as one arm of the X-shaped thorns.
            lock_corner = (xn < -0.18) & (yn < -0.18)
            band_a = (np.abs(yn - xn) < 0.16) & ~lock_corner
            band_b = (np.abs(yn + xn) < 0.16) & ~lock_corner
            metal = (sat < 0.32) & (val > 0.35) & (val < 0.96)
            outline = val < 0.30
            metal_a = float(metal[band_a].mean()) if band_a.any() else 0.0
            metal_b = float(metal[band_b].mean()) if band_b.any() else 0.0
            dark_a = float(outline[band_a].mean()) if band_a.any() else 0.0
            dark_b = float(outline[band_b].mean()) if band_b.any() else 0.0
            # Pointy wood/light artwork alone is not sufficient: require both
            # crossed metallic diagonals passing close to the orb center.
            center = (np.abs(xn) < 0.48) & (np.abs(yn) < 0.48)
            center_a = band_a & center
            center_b = band_b & center
            center_metal_a = float(metal[center_a].mean()) if center_a.any() else 0.0
            center_metal_b = float(metal[center_b].mean()) if center_b.any() else 0.0
            center_dark_a = float(outline[center_a].mean()) if center_a.any() else 0.0
            center_dark_b = float(outline[center_b].mean()) if center_b.any() else 0.0
            result.append(min(dark_a, dark_b) >= 0.11
                          and min(metal_a, metal_b) >= 0.075
                          and max(dark_a, dark_b) >= 0.165
                          and min(center_metal_a, center_metal_b) >= 0.055
                          and min(center_dark_a, center_dark_b) >= 0.085)
    return result


def apply_path_to_spikes(spikes: list[bool], path: list[int]) -> list[bool]:
    """Move thorn flags with their underlying orb along the same swap path."""
    out = list(spikes or [False] * (ROWS * COLS))
    if len(out) != ROWS * COLS:
        out = (out + [False] * (ROWS * COLS))[:ROWS * COLS]
    for cur, nxt in zip(path, path[1:]):
        out[cur], out[nxt] = out[nxt], out[cur]
    return out


def spike_route_metrics(board: list[str], spikes: list[bool], path: list[int]) -> tuple[int, int]:
    """Return (spiked orbs removed by matches, estimated thorn cells crossed)."""
    current_board = list(board)
    current_spikes = list(spikes or [False] * (ROWS * COLS))
    if len(current_spikes) != ROWS * COLS:
        current_spikes = (current_spikes + [False] * (ROWS * COLS))[:ROWS * COLS]
    crossings = 0
    for cur, nxt in zip(path, path[1:]):
        if current_spikes[nxt]:
            crossings += 1
        current_board[cur], current_board[nxt] = current_board[nxt], current_board[cur]
        current_spikes[cur], current_spikes[nxt] = current_spikes[nxt], current_spikes[cur]
    _, _, groups = combo_details(current_board)
    matched_positions = set().union(*groups) if groups else set()
    cleared = sum(1 for i in matched_positions if current_spikes[i])
    return int(cleared), int(crossings)


def _score_region_candidates(hue: np.ndarray, sat: np.ndarray, val: np.ndarray,
                              candidates: list[tuple[int, int, int, float]],
                              batch_size: int = 2500) -> np.ndarray:
    """Score candidate 6x5 grid rectangles by colour consistency around orb rings."""
    screen_h, screen_w = hue.shape
    prototypes = np.array([COLOR_INFO[k][2] for k in COLOR_HUE_ORDER], dtype=np.float32)
    offsets = [(-0.29, 0.0), (0.29, 0.0), (0.0, -0.29), (0.0, 0.29),
               (-0.205, -0.205), (0.205, -0.205), (-0.205, 0.205), (0.205, 0.205)]
    output: list[float] = []
    for batch_start in range(0, len(candidates), batch_size):
        batch = np.asarray(candidates[batch_start:batch_start + batch_size], dtype=np.float32)
        xs, ys, heights, ratios = batch.T
        widths = heights * ratios
        cw, ch = widths / COLS, heights / ROWS
        n = len(batch)
        classes = np.full((n, ROWS * COLS, len(offsets)), -1, dtype=np.int8)
        valid = np.zeros((n, ROWS * COLS, len(offsets)), dtype=bool)
        for row in range(ROWS):
            for col in range(COLS):
                cell = row * COLS + col
                cx = xs + (col + 0.5) * cw
                cy = ys + (row + 0.5) * ch
                for oi, (ox, oy) in enumerate(offsets):
                    px = np.rint(cx + ox * cw).astype(np.int32)
                    py = np.rint(cy + oy * ch).astype(np.int32)
                    inside = (px >= 0) & (px < screen_w) & (py >= 0) & (py < screen_h)
                    indices = np.flatnonzero(inside)
                    if not len(indices):
                        continue
                    hs = hue[py[indices], px[indices]]
                    ss = sat[py[indices], px[indices]]
                    vv = val[py[indices], px[indices]]
                    good = (ss >= 0.32) & (vv >= 0.24)
                    chosen = indices[good]
                    if len(chosen):
                        hh = hs[good]
                        distances = np.abs(hh[:, None] - prototypes[None, :])
                        distances = np.minimum(distances, 360.0 - distances)
                        classes[chosen, cell, oi] = np.argmin(distances, axis=1).astype(np.int8)
                        valid[chosen, cell, oi] = True
        valid_per_cell = valid.sum(axis=2)
        votes = np.stack([((classes == colour_id) & valid).sum(axis=2)
                          for colour_id in range(len(COLOR_HUE_ORDER))], axis=2)
        best_votes = votes.max(axis=2)
        consistency = (best_votes / float(len(offsets))).mean(axis=1)
        valid_fraction = valid.mean(axis=(1, 2))
        cell_coverage = (valid_per_cell >= 5).mean(axis=1)
        dominant = votes.argmax(axis=2)
        diversity = np.array([len(np.unique(row)) for row in dominant], dtype=np.float32)
        ratio_penalty = np.abs(ratios - 1.20) * 0.12  # prefer nearly square cells
        scores = (consistency * 0.75 + valid_fraction * 0.15 + cell_coverage * 0.10
                  - ratio_penalty - np.maximum(0.0, 3.0 - diversity) * 0.02)
        output.extend(np.clip(scores, 0.0, 1.0).tolist())
    return np.asarray(output, dtype=np.float32)


def detect_puzzle_area(screen_image: Image.Image) -> tuple[tuple[int, int, int, int], float]:
    """Find a likely 6-column by 5-row orb board in a full desktop screenshot.

    The detector searches uniform-grid candidates and scores the saturated hues
    around 8 points near every orb centre. It returns coordinates relative to the
    supplied screenshot and a 0..1 confidence score. Manual selection remains a
    fallback because unusual layouts or animations can fool any visual detector.
    """
    original_w, original_h = screen_image.size
    if original_w < 200 or original_h < 160:
        raise RuntimeError("화면 캡처 크기가 너무 작습니다.")
    scale = min(0.5, 800.0 / max(original_w, original_h))
    small_w = max(120, int(round(original_w * scale)))
    small_h = max(100, int(round(original_h * scale)))
    small = screen_image.resize((small_w, small_h), Image.Resampling.BILINEAR)
    hue, sat, val = rgb_to_hsv_np(np.asarray(small.convert("RGB")))
    sh, sw = hue.shape
    min_h = max(44, int(sh * 0.25))
    max_h = min(sh - 8, int(sh * 0.93))
    coarse: list[tuple[int, int, int, float]] = []
    for height in range(min_h, max_h + 1, 8):
        ratio = 1.20
        width = int(round(height * ratio))
        if width > sw - 2:
            continue
        for y in range(0, sh - height + 1, 6):
            for x in range(0, sw - width + 1, 6):
                coarse.append((x, y, height, ratio))
    if not coarse:
        raise RuntimeError("자동 탐지에 사용할 화면 후보가 없습니다.")
    coarse_scores = _score_region_candidates(hue, sat, val, coarse)
    # A slightly shifted crop can score worse if the coarse scan lands between
    # orb centres. Refine several spatially distinct candidates so a tempting
    # partial-board match does not prevent the real 5x6 grid from being found.
    coarse_order = np.argsort(coarse_scores)[::-1]
    anchors: list[tuple[int, int, int, float]] = []
    for candidate_index in coarse_order:
        candidate = coarse[int(candidate_index)]
        ax, ay, ah, ar = candidate
        acx, acy = ax + ah * ar / 2.0, ay + ah / 2.0
        duplicate = False
        for ox, oy, oh, oor in anchors:
            ocx, ocy = ox + oh * oor / 2.0, oy + oh / 2.0
            distance = math.hypot(acx - ocx, acy - ocy)
            if distance < max(ah, oh) * 0.13 and abs(ah - oh) < max(ah, oh) * 0.12:
                duplicate = True
                break
        if not duplicate:
            anchors.append(candidate)
        if len(anchors) >= 4:
            break

    best_fine_score = -1.0
    best_fine = None
    for bx, by, bh, _ in anchors:
        # Fine pass around each promising rectangle; slightly varying aspect
        # ratios accommodates different window sizes and display scaling.
        fine: list[tuple[int, int, int, float]] = []
        fine_h_min, fine_h_max = max(min_h, bh - 8), min(max_h, bh + 8)
        for height in range(fine_h_min, fine_h_max + 1):
            for ratio in np.arange(1.16, 1.251, 0.015):
                width = int(round(height * float(ratio)))
                if width > sw:
                    continue
                for y in range(max(0, by - 6), min(sh - height, by + 6) + 1):
                    for x in range(max(0, bx - 6), min(sw - width, bx + 6) + 1):
                        fine.append((x, y, height, float(ratio)))
        if not fine:
            continue
        fine_scores = _score_region_candidates(hue, sat, val, fine)
        fine_idx = int(np.argmax(fine_scores))
        if float(fine_scores[fine_idx]) > best_fine_score:
            best_fine_score = float(fine_scores[fine_idx])
            best_fine = fine[fine_idx]
    if best_fine is None:
        raise RuntimeError("퍼즐판 후보 영역을 세밀하게 확인하지 못했습니다.")
    x, y, height, ratio = best_fine
    confidence = best_fine_score
    width = int(round(height * ratio))
    # Convert the resized image's pixel coordinates back to original screenshot
    # coordinates, accounting for integer rounding of resized dimensions.
    sx, sy = small_w / original_w, small_h / original_h
    rect = (int(round(x / sx)), int(round(y / sy)),
            int(round(width / sx)), int(round(height / sy)))
    if confidence < 0.62:
        raise RuntimeError(f"퍼즐판을 확실하게 찾지 못했습니다 (탐지 신뢰도 {confidence:.0%}). 직접 영역 지정을 사용해 주세요.")
    return rect, confidence

def neighbors(idx: int) -> list[int]:
    r, c = divmod(idx, COLS)
    out = []
    if r > 0: out.append(idx - COLS)
    if r < ROWS - 1: out.append(idx + COLS)
    if c > 0: out.append(idx - 1)
    if c < COLS - 1: out.append(idx + 1)
    return out


def board_after_drag(board: list[str], path: list[int]) -> list[str]:
    """Simulate a drag: the selected orb is carried, and each crossed orb shifts back."""
    out = list(board)
    if not path:
        return out
    held = board[path[0]]
    for i in range(1, len(path)):
        cur, nxt = path[i - 1], path[i]
        displaced = out[nxt]
        out[cur] = displaced
        out[nxt] = held
    return out


def combo_details(board: list[str]) -> tuple[int, int, list[set[int]]]:
    """Return combo count, matched-cell count and groups on a 5x6 board.

    All recognized classes are eligible for 3+ matches, including Recovery (H),
    Jammer (J), and Skull/Poison (S). S still counts as a combo if matched, but
    its matched-cell count is exposed to the solver as a hazard cost.
    """
    marked: set[int] = set()
    for r in range(ROWS):
        c = 0
        while c < COLS:
            start = c
            color = board[r * COLS + c]
            while c < COLS and board[r * COLS + c] == color:
                c += 1
            if c - start >= 3:
                for cc in range(start, c): marked.add(r * COLS + cc)
    for c in range(COLS):
        r = 0
        while r < ROWS:
            start = r
            color = board[r * COLS + c]
            while r < ROWS and board[r * COLS + c] == color:
                r += 1
            if r - start >= 3:
                for rr in range(start, r): marked.add(rr * COLS + c)
    # Merge orthogonally adjacent matched cells of the same color into a single combo.
    groups: list[set[int]] = []
    unseen = set(marked)
    while unseen:
        seed = unseen.pop()
        group = {seed}
        stack = [seed]
        while stack:
            cur = stack.pop()
            for nxt in neighbors(cur):
                if nxt in unseen and board[nxt] == board[cur]:
                    unseen.remove(nxt)
                    group.add(nxt)
                    stack.append(nxt)
        groups.append(group)
    groups.sort(key=lambda g: min(g))
    return len(groups), len(marked), groups


def near_match_bonus(board: list[str]) -> int:
    """Small tie-breaker: reward lines of two identical orbs with a matching third nearby."""
    bonus = 0
    # Reward pairs that can be completed by the third cell in a straight 3-cell window.
    for r in range(ROWS):
        for c in range(COLS - 2):
            trip = [board[r * COLS + c + k] for k in range(3)]
            if trip[0] == trip[1] == trip[2]: bonus += 2
            elif trip[0] == trip[1] or trip[1] == trip[2] or trip[0] == trip[2]: bonus += 1
    for c in range(COLS):
        for r in range(ROWS - 2):
            trip = [board[(r + k) * COLS + c] for k in range(3)]
            if trip[0] == trip[1] == trip[2]: bonus += 2
            elif trip[0] == trip[1] or trip[1] == trip[2] or trip[0] == trip[2]: bonus += 1
    return bonus


def score_path(board: list[str], path: list[int]) -> tuple[int, int, int, list[str]]:
    after = board_after_drag(board, path)
    combos, matched, _ = combo_details(after)
    score = combos * 1000 + matched * 18 + near_match_bonus(after)
    return score, combos, matched, after


def random_path(moves: int) -> list[int]:
    """Generate a self-avoiding path so the on-screen instructions stay easy to follow."""
    for _attempt in range(80):
        p = [random.randrange(ROWS * COLS)]
        for _ in range(moves):
            opts = [n for n in neighbors(p[-1]) if n not in p]
            if not opts:
                break
            p.append(random.choice(opts))
        if len(p) == moves + 1:
            return p
    # A legal fallback; most boards obtain a simple path long before this is needed.
    p = [random.randrange(ROWS * COLS)]
    for _ in range(moves):
        p.append(random.choice(neighbors(p[-1])))
    return p


def mutate_path(path: list[int]) -> list[int]:
    new = list(path)
    if len(new) < 2:
        return random_path(12)
    moves = len(new) - 1
    for _attempt in range(5):
        candidate = list(new)
        k = random.randrange(0, len(candidate) - 1)
        if k == 0:
            candidate[0] = random.randrange(ROWS * COLS)
        else:
            opts = [n for n in neighbors(candidate[k - 1]) if n not in candidate[:k]]
            if not opts:
                continue
            candidate[k] = random.choice(opts)
        valid = True
        for i in range(k + 1, len(candidate)):
            opts = [n for n in neighbors(candidate[i - 1]) if n not in candidate[:i]]
            if not opts:
                valid = False
                break
            candidate[i] = random.choice(opts)
        if valid:
            return candidate
    return random_path(moves)


# Keep indices 0..3 compatible with existing settings and saved diagnostics.
# Index 3 is the retired recovery mode; the six visible modes are listed below.
ROUTE_GOAL_OPTIONS = (
    (0, "10콤보", "드롭을 나눠 10콤보 달성"),
    (2, "다색팟 · 5속성", "불·물·나무·빛·어둠을 모두 매치"),
    (9, "달인 다색", "5속성·6콤보 이상 · 조작시간 4초 남기기"),
    (1, "드롭 8개 연결", "같은 종류의 드롭 8개 이상 연결"),
    (4, "TPA · 2체 공격", "같은 공격 속성 드롭을 정확히 4개 매치"),
    (5, "십자팟", "같은 공격 속성 드롭 5개를 십자로 매치"),
    (6, "L자팟", "같은 공격 속성 드롭 5개를 L자로 매치"),
)


@lru_cache(maxsize=3)
def shape_match_templates(mode):
    """Exact same-colour components; border orbs must not join the shape."""
    shapes = set()
    if mode == 4:
        triples = [frozenset(r*6+c+k for k in range(3)) for r in range(5) for c in range(4)]
        triples += [frozenset((r+k)*6+c for k in range(3)) for r in range(3) for c in range(6)]
        for triple in triples:
            for cell in triple:
                for extra in neighbors(cell):
                    if extra not in triple:
                        shapes.add(triple | {extra})
    elif mode == 5:
        for r in range(1, 4):
            for c in range(1, 5):
                i = r*6+c
                shapes.add(frozenset((i, i-1, i+1, i-6, i+6)))
    elif mode == 6:
        for r in range(5):
            for c in range(6):
                for dr in (-1, 1):
                    for dc in (-1, 1):
                        if 0 <= r+2*dr < 5 and 0 <= c+2*dc < 6:
                            shapes.add(frozenset((r*6+c, (r+dr)*6+c, (r+2*dr)*6+c,
                                                  r*6+c+dc, r*6+c+2*dc)))
    result = []
    for cells in sorted(shapes, key=lambda cells: sorted(cells)):
        border = {n for i in cells for n in neighbors(i)} - cells
        result.append((sum(1 << i for i in cells), sum(1 << i for i in border)))
    return tuple(result)


def shape_goal_metrics(board, mode):
    templates = shape_match_templates(mode)
    count, progress = 0, 0.0
    for color in PRIMARY_ATTRIBUTES:
        bits = sum(1 << i for i, value in enumerate(board) if value == color)
        for mask, border in templates:
            filled = (bits & mask).bit_count()
            extra = (bits & border).bit_count()
            complete = filled == mask.bit_count() and extra == 0
            count += int(complete)
            # A correct shape beats a nearly formed shape with extra touching orbs.
            progress = max(progress, 1.0 if complete else
                           max(0.0, (filled - .25*extra) / mask.bit_count()) * .99)
    return count, progress


def master_multicolor(goals):
    return len(goals)>9 and bool(goals[9])


def master_survival(goals):
    return len(goals)>10 and bool(goals[10])


def master_survival_goals(required=6,bonus=0):
    return (False,)*7+(6,max(0,int(bonus)),False,True)


def master_multicolor_goals():
    return (False,False,True,False,False,False,False,6,0,True)


def route_operation_seconds(seconds,goals):
    if not master_multicolor(goals):return seconds
    if seconds is None:raise RuntimeError('달인 다색 · 조작시간 미확인 · 4초 잔여 조건을 확인할 수 없습니다.')
    usable=float(seconds)-4.0
    if usable<=1.0:raise RuntimeError('달인 다색 · 시간부족 · 조작시간 4초와 입력 여유를 확보할 수 없습니다.')
    return usable


def combo_condition_goals(goals, required_total=0, combo_bonus=0):
    """Enemy and team requirements combine; never replace the master's six."""
    master=master_multicolor(goals)
    survival=master_survival(goals)
    return (tuple(goals[:7]) + (False,)*max(0,7-len(goals))
            + (6 if survival else max(6 if master else 0,int(required_total)),max(0,int(combo_bonus)))
            + ((False,True) if survival else (True,) if master else ()))


@lru_cache(maxsize=30000)
def _matched_bit_combo_count(bits):
    marked = _attribute_bit_features(bits)[2]
    count = 0
    while marked:
        frontier = marked & -marked
        marked ^= frontier
        while frontier:
            spread = 0
            while frontier:
                bit = frontier & -frontier
                frontier ^= bit
                for nxt in _FIVE_ATTRIBUTE_NEIGHBORS[bit.bit_length()-1]:
                    spread |= 1 << nxt
            frontier = spread & marked
            marked ^= frontier
        count += 1
    return count


def enemy_attack_anchors(frame):
    """Red fist anchors attack countdown rows; its digit is not a combo floor."""
    import cv2
    rgb=np.asarray(frame.convert('RGB'));height=rgb.shape[0]
    if height<300 or rgb.shape[1]<100:return []
    fist_rows = (
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000111100000000000',
        '00000000000111101111111111000000',
        '00000000001111111111111111100000',
        '00000000001111111111111111100000',
        '00000111111111111111111111100000',
        '00000111111111111111111111100000',
        '00000111111111111111111111100000',
        '00000111111111101111111111100000',
        '00000111111111101111101111100000',
        '00000111111111101111101111100000',
        '00000111101111101111101111100000',
        '00000111101111101111101111100000',
        '00000111100111100111100111000000',
        '00000000000111000010000000000000',
        '00000000000000000000000000000000',
        '00000000000000000111111100000000',
        '00000000000000001111111111000000',
        '00000000000000001111111111000000',
        '00000000000000001111111111000000',
        '00000000000000001111111111000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
    )
    template=np.array([[int(c) for c in row] for row in fist_rows],dtype=np.float32)
    red=((rgb[...,0]>150)&(rgb[...,1]<75)&(rgb[...,2]<120)).astype(np.float32)
    search=red[round(height*.12):round(height*.54),:820]
    scores=cv2.matchTemplate(search,template,cv2.TM_CCOEFF_NORMED)
    ay,ax=np.where(scores>=.78)
    anchors=[]
    for y,x in sorted(zip(ay,ax),key=lambda pos:-scores[pos]):
        point=(int(x)+16,int(y)+round(height*.12)+15)
        if not any(abs(point[0]-a)<12 and abs(point[1]-b)<12 for a,b in anchors):anchors.append(point)
    return anchors


def combo_status_candidates(image):
    """Locate orb-ring enemy badges and the three-bead addition badge.

    Work at a common scale. A shield or an isolated number is never an enemy
    combo candidate. Crop coordinates exclude the larger duration on the right.
    Returns a normalized frame and tightly bounded digit crops, not OCR guesses.
    """
    import cv2
    frame=image.convert('RGB').resize((1600,round(image.height*1600/image.width)),Image.Resampling.LANCZOS)
    rgb=np.asarray(frame); height=rgb.shape[0]
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    white=((rgb.min(axis=2)>180)&(rgb.max(axis=2).astype(int)-rgb.min(axis=2)<65)).astype(np.uint8)
    def components(mask):
        n,_,stats,centers=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
        return [(tuple(map(int,stats[i])),centers[i]) for i in range(1,n)]
    # Anchor status rows to the red enemy attack badge. This small binary
    # silhouette is from the supplied screenshot; artwork cannot supply an anchor.
    anchors=enemy_attack_anchors(frame)
    enemy=[]
    # Status durations are outlined white, 20-32 px tall at the reference size.
    region=white.copy();region[:round(height*.12)]=0;region[round(height*.58):]=0;region[:,830:]=0
    for (x,y,w,h,area),_ in components(region):
        if not (17<=h<=34 and 2<=w<=30 and area>=18): continue
        # The orb-ring centre is 21-29 px left of the duration glyph.
        for cx,cy in ((ax,ay+offset) for ax,ay in anchors for offset in (38,76,114)
                      if 10<=x-ax<=48 and abs(y+h//2-ay-offset)<=7):
            if cx<16 or cy<16: continue
            tile=hsv[cy-15:cy+16,cx-15:cx+16]
            if tile.shape[:2]!=(31,31): continue
            yy,xx=np.ogrid[-15:16,-15:16];ring=(xx*xx+yy*yy>=6**2)&(xx*xx+yy*yy<=15**2)
            hue,sat,val=tile[...,0],tile[...,1],tile[...,2]
            # Four independently coloured orb dots around a neutral white digit.
            coloured=ring&(sat>85)&(val>110)
            ranges=((0,12),(14,38),(39,88),(89,128),(129,179))
            counts=[int(np.sum(coloured&(hue>=lo)&(hue<=hi))) for lo,hi in ranges]
            if sum(c>=4 for c in counts)<4 or sum(counts)<35: continue
            centre=white[cy-8:cy+9,cx-5:cx+6]
            if np.sum(centre)<8: continue
            # Reject bright multicoloured character art: UI badge has dark backing.
            outer=rgb[cy-15:cy+16,cx-15:cx+16]
            if np.mean(outer.max(axis=2)<140)<.20: continue
            box=(cx-6,cy-10,cx+7,cy+10)
            if any(abs(cx-(b[0]+b[2])/2)<18 and abs(cy-(b[1]+b[3])/2)<18 for b in enemy): break
            enemy.append(box);break
    # Buff icons lie below the board. Require THREE similar white beads first.
    buff=white.copy();buff[:round(height*.895)]=0;buff[round(height*.985):]=0;buff[:,:870]=0
    beads=[s for s,_ in components(buff) if 7<=s[2]<=18 and 7<=s[3]<=18 and 30<=s[4]<=260 and .6<=s[2]/s[3]<=1.5]
    bonus=[]
    for a in beads:
        x,y,w,h,_=a
        for b in beads:
            pitch=b[0]-x
            if not (9<=pitch<=20 and abs(b[1]-y)<=3 and abs(b[3]-h)<=3):continue
            # The third bead sometimes touches the outlined duration digit.
            # Verify its pixels at the expected pitch instead of merging the turn.
            tx=b[0]+pitch
            third=white[y:y+h,tx:tx+w]
            if third.size==0 or third.mean()<.5:continue
            box=(x-3,y-25,tx+w+2,y-2)
            tile=rgb[box[1]:box[3],box[0]:box[2]]
            yellow=(tile[...,0]>150)&(tile[...,1]>150)&(tile[...,2]<150)
            parts=sorted([s for s,_ in components(yellow) if s[4]>=4],key=lambda s:s[0])
            if len(parts)<2:continue
            for px,py,pw,ph,pa in parts:
                cross=yellow[py:py+ph,px:px+pw]
                if not (5<=pw<=12 and 5<=ph<=12 and .7<=pw/ph<=1.4 and .25<=pa/(pw*ph)<=.65
                        and cross.sum(axis=0).max()>=ph*.85 and cross.sum(axis=1).max()>=pw*.85):continue
                digits=[v for v in parts if px+pw<=v[0]<=px+pw+6 and v[3]>=ph and v[1]<=py+3]
                if not digits:continue
                dx,dy,dw,dh,_=min(digits,key=lambda v:v[0])
                tight=(box[0]+px,box[1]+min(py,dy),box[0]+dx+dw,box[1]+max(py+ph,dy+dh))
                if not any(abs(tight[0]-old[0])<12 for old in bonus):bonus.append(tight)
    return frame,enemy,bonus


def combo_digit_views(image, yellow=False):
    """Two isolated magnifications; addition sign is validated geometrically."""
    import cv2
    rgb=np.asarray(image.convert('RGB'))
    if yellow:
        mask=(rgb[...,0]>150)&(rgb[...,1]>150)&(rgb[...,2]<150)
        n,_,stats,_=cv2.connectedComponentsWithStats(mask.astype('uint8'),8)
        parts=sorted([v for v in stats[1:] if v[4]>=4],key=lambda v:v[0])
        if len(parts)<2:return []
        end=int(parts[0][0]+parts[0][2])
        mask[:,:end+1]=False
    else:
        mask=(rgb.min(axis=2)>175)&(rgb.max(axis=2).astype(int)-rgb.min(axis=2)<65)
    ys,xs=np.where(mask)
    if not len(xs):return []
    box=(max(0,int(xs.min())-1),max(0,int(ys.min())-1),min(image.width,int(xs.max())+2),min(image.height,int(ys.max())+2))
    bw=Image.fromarray(np.where(mask,0,255).astype('uint8')).crop(box).convert('RGB')
    def padded(im,scale):
        im=im.resize((im.width*scale,im.height*scale),Image.Resampling.LANCZOS)
        out=Image.new('RGB',(im.width+24,im.height+24),'white');out.paste(im,(12,12));return out
    first=padded(bw,5)
    return [first,first.resize((round(first.width*1.3),round(first.height*1.3)),Image.Resampling.LANCZOS)]


def board_goal_metrics(board: list[str], goals: tuple[bool, ...]) -> dict:
    """Measure direct matches: 10 combos, 8 connected, five colours, legacy
    recovery, exact four-orb TPA, five-orb cross, and five-orb L respectively.
    Shape goals accept attack attributes only, with no adjoining same-colour orb.
    """
    combos, matched, groups = combo_details(board)
    required_total = max(6 if master_multicolor(goals) or master_survival(goals) else 0,int(goals[7]) if len(goals) > 7 else 0)
    combo_bonus = int(goals[8]) if len(goals) > 8 else 0
    final_combos = combos + combo_bonus
    combo_met = final_combos >= required_total
    largest_group = max((len(group) for group in groups), default=0)
    matched_colors = {board[idx] for group in groups for idx in group}
    attr_count = len(matched_colors & PRIMARY_ATTRIBUTES)
    recovery_matched = sum(len(group) for group in groups if board[min(group)] == "H")
    recovery_combos = sum(1 for group in groups if board[min(group)] == "H")
    skull_matched = sum(len(group) for group in groups if board[min(group)] == "S")
    skull_combos = sum(1 for group in groups if board[min(group)] == "S")
    enabled = [bool(x) for x in goals[:7]] + [False] * max(0, 7 - len(goals))
    shapes = {i: shape_goal_metrics(board, i) for i in (4, 5, 6) if enabled[i]}
    achieved = [
        final_combos >= 10 if enabled[0] else True,
        largest_group >= 8 if enabled[1] else True,
        attr_count >= 5 if enabled[2] else True,
        recovery_matched >= 3 if enabled[3] else True,
        *[shapes[i][0] >= 1 if enabled[i] else True for i in (4, 5, 6)],
    ]
    progress = []
    if enabled[0]: progress.append(min(final_combos / 10.0, 1.0))
    if enabled[1]: progress.append(min(largest_group / 8.0, 1.0))
    if enabled[2]: progress.append(min(attr_count / 5.0, 1.0))
    if enabled[3]: progress.append(min(recovery_matched / 3.0, 1.0))
    progress.extend(shapes[i][1] for i in (4, 5, 6) if enabled[i])
    if required_total:
        progress.append(min(final_combos / required_total, 1.0))
    selected_count = sum(enabled) + bool(required_total)
    achieved_count = sum(1 for i, flag in enumerate(enabled) if flag and achieved[i]) + int(bool(required_total) and combo_met)
    progress_total = sum(progress)
    return {
        "combos": combos,
        "final_combos": final_combos,
        "required_total": required_total,
        "combo_bonus": combo_bonus,
        "combo_met": combo_met,
        "matched": matched,
        "groups": groups,
        "largest_group": largest_group,
        "shape_count": sum(value[0] for value in shapes.values()),
        "shape_counts": {i: value[0] for i, value in shapes.items()},
        "attr_count": attr_count,
        "recovery_matched": recovery_matched,
        "recovery_combos": recovery_combos,
        "skull_matched": skull_matched,
        "skull_combos": skull_combos,
        "matched_colors": matched_colors,
        "enabled": enabled,
        "achieved": achieved,
        "all_met": achieved_count == selected_count,
        "achieved_count": achieved_count,
        "selected_count": selected_count,
        "progress": progress_total,
    }


def timer_badge_boxes(image):
    """Locate the white circular time badge, excluding text over the scenery."""
    import cv2
    rgb=np.asarray(image.convert('RGB'),dtype=np.int16)
    height,width=rgb.shape[:2]
    white=((rgb.min(axis=2)>180)&(rgb.max(axis=2)-rgb.min(axis=2)<60)).astype(np.uint8)
    count,labels,stats,_=cv2.connectedComponentsWithStats(white,8)
    boxes=[]
    for x,y,w,h,area in stats[1:]:
        x,y,w,h,area=map(int,(x,y,w,h,area))
        if not (max(15,height*.25)<=h<=height*.90 and .65<=w/h<=1.5 and area>=.30*w*h): continue
        patch=rgb[y:y+h,x:x+w]
        teal=(patch[...,1]>patch[...,0]+25)&(patch[...,2]>patch[...,0]+20)
        if float(teal.mean())<.04: continue
        boxes.append((max(0,x-2),max(0,y-2),min(width,x+w+2),min(height,y+h+2)))
    return boxes


def route_search_budget(seconds, default_budget):
    """Short drag windows get bounded fast failure, including diagnosis."""
    return min(float(default_budget),3.0)


def goal_label(index: int) -> str:
    return ("10콤보", "동일 색상 8개 연결", "5속성 공격", "회복 우선",
            "TPA · 4개 매치", "십자 매치", "L자 매치")[index]


_HORIZONTAL_PAIR_STARTS = sum(1 << (r * COLS + c) for r in range(ROWS) for c in range(COLS - 1))
_HORIZONTAL_TRIPLE_STARTS = sum(1 << (r * COLS + c) for r in range(ROWS) for c in range(COLS - 2))


def five_attribute_near_completion(board: tuple[str, ...], matched_colors: set[str]) -> int:
    """Reward *missing* colors that have two orbs in a 3-cell match window.

    A four-color candidate with an almost completed fifth color must not be
    eliminated merely because another four-color candidate has more combos.
    """
    missing = PRIMARY_ATTRIBUTES - matched_colors
    if not missing:
        return 0
    masks = {color: 0 for color in missing}
    for idx, color in enumerate(board):
        if color in masks:
            masks[color] |= 1 << idx
    score = 0
    for color in missing:
        bits = masks[color]
        # Same 3-cell windows as before, evaluated with bit operations. Masks
        # prevent horizontal pairs/triples from wrapping across row boundaries.
        if ((bits & (bits >> 1) & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS)
                or (bits & (bits >> COLS) & (bits >> (2 * COLS)))):
            best = 3
        elif ((bits & (bits >> 1) & _HORIZONTAL_PAIR_STARTS)
                or (bits & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS)
                or (bits & (bits >> COLS)) or (bits & (bits >> (2 * COLS)))):
            best = 2
        else:
            best = int(bool(bits))
        score += best * best
    return score


def shortened_timer_bar(image):
    """Cheap visual gate: a cyan bar followed by a substantial red time loss.

    Normal full bars never start OCR or a solver. Isolated red scenery below
    the bar cannot turn a normal timer into a shortened timer.
    """
    rgb = np.asarray(image.convert('RGB'))
    h, w = rgb.shape[:2]
    region = rgb[int(h*.19):int(h*.30), int(w*.53):int(w*.99)]
    if not region.size:
        return False
    r, g, b = (region[...,i].astype(np.int16) for i in range(3))
    cyan = (g>145)&(b>145)&(r<135)
    red = (r>170)&(g<140)&(b<135)
    row = int(np.argmax(np.sum(cyan|red, axis=1)))
    cx, rx = np.flatnonzero(cyan[row]), np.flatnonzero(red[row])
    return bool(len(cx)>w*.10 and len(rx)>w*.08
                and len(cx)+len(rx)>w*.25 and np.median(rx)>np.median(cx))


def verified_prefind_route(board, path, goals, cap):
    """Validate a cached/IPC path before trusting it, including every edge."""
    if not path or len(path)-1>cap:
        return None
    if any(type(i) is not int or not 0<=i<30 for i in path):
        return None
    if any(b not in neighbors(a) for a,b in zip(path,path[1:])):
        return None
    after = board_after_drag(list(board),list(path))
    details = board_goal_metrics(after,goals)
    if not details['all_met']:
        return None
    return list(path), details['combos'], details['matched'], after


def shortened_route_search_process(board, spikes, goals, cap, avoid_skull, output, total_budget=PRESEARCH_WAIT_LIMIT, required_total=0, combo_bonus=0):
    """One low-priority math process, bounded chunks and rest, no GUI or input.

    Fresh seeds explore different beams. Only a verified success crosses IPC;
    unsuccessful routes and screenshots are neither retained nor written.
    """
    try:
        if sys.platform=='win32':
            kernel=ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype=ctypes.c_void_p
            kernel.SetPriorityClass.argtypes=(ctypes.c_void_p,ctypes.c_uint)
            kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x00004000)
        else:
            os.nice(5)
        goals=combo_condition_goals(goals,required_total,combo_bonus)
        required_total=goals[7];combo_bonus=goals[8]
        attempt=0
        deadline=time.monotonic()+max(0.0,float(total_budget))
        while time.monotonic()<deadline:
            kwargs=dict(time_budget=min(1.2,max(0.0,deadline-time.monotonic())),max_moves=cap,spikes=spikes,
                        avoid_skull=avoid_skull,seed_offset=attempt)
            if goals[2]:
                path,_,_,_=solve_five_attributes_v101(board,required_total=required_total,combo_bonus=combo_bonus,**kwargs)
            else:
                path,_,_,_=solve_path(board,goals=goals,**kwargs)
            result=verified_prefind_route(board,path,goals,cap)
            attempt+=1
            if result is not None:
                output.put(('success',result[0],attempt))
                return
            time.sleep(min(.8,max(0.0,deadline-time.monotonic())))
        output.put(('timeout',None,attempt))
    except Exception as exc:
        output.put(('error',str(exc),0))


def solve_path(
    board: list[str],
    time_budget: float = SOLVER_TIME_BUDGET,
    max_moves: Optional[int] = SOLVER_MAX_MOVES,
    beam_width: int = SOLVER_BEAM_WIDTH,
    goals: tuple[bool, bool, bool, bool] = (True, False, False, False),
    avoid_skull: bool = True,
    spikes: Optional[list[bool]] = None,
    seed_offset: int = 0,
    cancel_event=None,
) -> tuple[list[int], int, int, list[str]]:
    """Search routes with hazard safety first; five-color mode ranks distinct attributes before combos.

    The silver crossed blades are metadata carried with their orb. For each path,
    the solver estimates how many thorned cells are entered during movement and how
    many thorned orbs are cleared by the final matches. It favours an overall positive
    thorn-safety score (2 * cleared - crossed), then fewer crossings, then more cleared
    thorns, then poison safety and combo count. Damage is not simulated exactly because
    the game's HP/damage formula is unknown.
    """
    if len(board) != ROWS * COLS:
        raise ValueError(f"퍼즐판에는 {ROWS * COLS}칸이 필요합니다.")
    if master_survival(goals):
        return solve_five_attributes_v101(board,time_budget=time_budget,max_moves=max_moves,
            spikes=spikes,avoid_skull=avoid_skull,seed_offset=seed_offset,
            required_total=6,combo_bonus=goals[8] if len(goals)>8 else 0,require_five=False,cancel_event=cancel_event)
    start_board = tuple(board)
    start_spikes = tuple((list(spikes) + [False] * (ROWS * COLS))[:ROWS * COLS]) if spikes else tuple([False] * (ROWS * COLS))
    enabled_goals = tuple(bool(v) for v in goals[:7]) + (False,) * max(0, 7-len(goals)) + tuple(goals[7:11])
    avoid_skull = bool(avoid_skull)
    eval_cache: dict[tuple[tuple[str, ...], tuple[bool, ...]], tuple] = {}

    def evaluate(bd: tuple[str, ...], sp: tuple[bool, ...], crossings: int) -> tuple:
        cache_key = (bd, sp)
        details = eval_cache.get(cache_key)
        if details is None:
            raw = board_goal_metrics(list(bd), enabled_goals)
            marked = set().union(*raw["groups"]) if raw["groups"] else set()
            cleared_spikes = sum(1 for i in marked if sp[i])
            potential = near_match_bonus(list(bd))
            details = (raw, int(cleared_spikes), int(potential),
                       five_attribute_near_completion(bd, raw["matched_colors"]) if enabled_goals[2] else 0)
            eval_cache[cache_key] = details
        raw, cleared_spikes, potential, attribute_potential = details
        skull_matched = int(raw["skull_matched"])
        recovery_score = raw["recovery_matched"] if enabled_goals[3] else 0
        recovery_combo_score = raw["recovery_combos"] if enabled_goals[3] else 0
        thorn_score = 2 * cleared_spikes - crossings
        has_spikes = any(start_spikes)
        # The selected five-attribute team skill is activated by DISTINCT matched
        # attack colors, not by the total number of combos. Rank 5 > 4 > 3 > 2 > 1
        # even when a lower-attribute candidate would produce more combos.
        # Poison/spike precautions remain safety constraints ahead of damage goals.
        combo_tier = 2 if raw["final_combos"] >= 10 else (1 if raw["final_combos"] >= 8 else 0)
        if enabled_goals[2]:
            # Attribute count is the hard primary objective (before hazards
            # and combos). Next retain near-complete missing colors in the beam.
            objective_rank = (
                raw["attr_count"],
                attribute_potential,
                raw["combos"],
                raw["matched"],
                potential,
            )
        else:
            objective_rank = (
                combo_tier,
                raw["combos"],
                recovery_score,
                recovery_combo_score,
                raw["achieved_count"],
                round(raw["progress"], 4),
                raw["largest_group"] if enabled_goals[1] else 0,
                raw["matched"],
                potential,
            )
        safety_rank = (
            -crossings if has_spikes else 0,
            cleared_spikes if has_spikes else 0,
            thorn_score if has_spikes else 0,
            -skull_matched if avoid_skull else 0,
            int(skull_matched == 0) if avoid_skull else 0,
        )
        if enabled_goals[2]:
            # In 5-attribute mode, safety is a tie-breaker, not a reason to
            # sacrifice the team's activation condition.
            rank = (raw["attr_count"], attribute_potential, *safety_rank,
                    raw["combos"], raw["matched"], potential)
        elif enabled_goals[1] or any(enabled_goals[4:7]):
            rank = (raw["achieved_count"], round(raw["progress"], 4),
                    raw["shape_count"], raw["largest_group"] if enabled_goals[1] else 0,
                    *safety_rank, raw["combos"], raw["matched"], potential)
        else:
            rank = (*safety_rank, *objective_rank)
        if raw['required_total']:
            # Joint progress retains near-complete colours AND extra combos;
            # damage-nullified routes must never outrank a valid route.
            rank = (int(raw['all_met']), round(raw['progress'], 4), *rank)
        return rank, raw["combos"], raw["matched"], potential, cleared_spikes, skull_matched, raw["all_met"]

    states = []
    e0 = evaluate(start_board, start_spikes, 0)
    start_indices = list(range(ROWS * COLS))
    if seed_offset:
        random.Random(seed_offset).shuffle(start_indices)
    for start_idx in start_indices:
        states.append((e0[0], start_board, start_spikes, start_idx, -1, (start_idx,),
                       e0[1], e0[2], e0[3], 0, 1, 0))

    best_path = [0]
    best_rank = (*e0[0], -1, 0)
    deadline = time.perf_counter() + max(0.0, time_budget)
    depth = 1
    while time.perf_counter() < deadline and not (cancel_event is not None and cancel_event.is_set()) and (max_moves is None or depth <= max_moves):
        candidates: dict[tuple, tuple] = {}
        for state in states:
            if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                break
            rank, bd, sp, current, previous, path, _co, _ma, _pot, turns, unique_count, crossings = state
            for nxt in neighbors(current):
                if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                    break
                # Immediate reversal is redundant and can make the drag bounce in place.
                if nxt == previous:
                    continue
                next_crossings = crossings + (1 if sp[nxt] else 0)
                moved = list(bd)
                moved[current], moved[nxt] = moved[nxt], moved[current]
                moved_spikes = list(sp)
                moved_spikes[current], moved_spikes[nxt] = moved_spikes[nxt], moved_spikes[current]
                nboard, nsp = tuple(moved), tuple(moved_spikes)
                ev = evaluate(nboard, nsp, next_crossings)
                npath = path + (nxt,)
                if (enabled_goals[2] or master_survival(enabled_goals)) and ev[6]:
                    # Stop only after the selected colour/combo conditions
                    # have both been independently verified.
                    return list(npath), ev[1], ev[2], list(nboard)
                nturns = turns
                if previous >= 0 and (current - previous) != (nxt - current):
                    nturns += 1
                nuniq = len(set(npath))
                candidate = (ev[0], nboard, nsp, nxt, current, npath, ev[1], ev[2],
                             ev[3], nturns, nuniq, next_crossings)
                key = (nboard, nsp, nxt, current)
                old = candidates.get(key)
                if old is None or (candidate[0], -candidate[9], candidate[10], -len(candidate[5])) > (
                    old[0], -old[9], old[10], -len(old[5])
                ):
                    candidates[key] = candidate
                candidate_rank = (*ev[0], -len(npath), -nturns)
                if candidate_rank > best_rank:
                    best_rank = candidate_rank
                    best_path = list(npath)
        if not candidates:
            break
        states = sorted(
            candidates.values(),
            key=lambda st: (st[0], -st[9], st[10], -len(st[5])),
            reverse=True,
        )[:max(100, beam_width)]
        depth += 1
        # v90: 10-combo aspiration; never terminate on 8 merely because of
        # a fragile tuple-index test. Stop only on a verified 10-combo result
        # when there are no poison/spike hazards, and prefer shorter paths.
        if enabled_goals[2] and board_goal_metrics(board_after_drag(list(start_board), best_path), enabled_goals)["all_met"]:
            break
        if (not enabled_goals[2] and not any(start_spikes) and all(x != 'S' for x in start_board)
                and len(best_path) - 1 <= 36):
            early_details = board_goal_metrics(board_after_drag(list(start_board), best_path), enabled_goals)
            # In five-attribute mode, 10 combos alone are NOT a valid early exit:
            # keep searching until all five attack colors are also matched.
            if early_details["final_combos"] >= 10 and early_details["all_met"]:
                break

    final_board = board_after_drag(list(start_board), best_path)
    final_combos, final_matched, _ = combo_details(final_board)
    return best_path, final_combos, final_matched, final_board


@lru_cache(maxsize=4096)
def _three_orb_match_distance(bits):
    """Minimum Manhattan transport to a straight triple, not drag path length.

    There are only 4060 three-cell subsets on a 6x5 board. Cache them across
    searches. For a horizontal target, rows share a median and sorted columns
    optimally map to three consecutive columns; vertical targets are symmetric.
    """
    if bits.bit_count() != 3:
        raise ValueError("Exactly three orb positions are required.")
    cells = []
    while bits:
        low = bits & -bits
        cells.append(low.bit_length() - 1)
        bits ^= low
    r0, r1, r2 = (cell // COLS for cell in cells)
    c0, c1, c2 = sorted(cell % COLS for cell in cells)
    a, b, c = c0, c1 - 1, c2 - 2
    start = max(0, min(COLS - 3, a + b + c - min(a, b, c) - max(a, b, c)))
    horizontal = r2 - r0 + abs(c0 - start) + abs(c1 - start - 1) + abs(c2 - start - 2)
    a, b, c = r0, r1 - 1, r2 - 2
    start = max(0, min(ROWS - 3, a + b + c - min(a, b, c) - max(a, b, c)))
    vertical = c2 - c0 + abs(r0 - start) + abs(r1 - start - 1) + abs(r2 - start - 2)
    return min(horizontal, vertical)


def _attribute_bit_features(bits):
    """Return (has_match, completion_potential, matched_cells) without flood fills."""
    horizontal = bits & (bits >> 1) & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS
    vertical = bits & (bits >> COLS) & (bits >> (2 * COLS))
    marked = (horizontal | (horizontal << 1) | (horizontal << 2)
              | vertical | (vertical << COLS) | (vertical << (2 * COLS)))
    if marked:
        return 1, 0, marked
    if bits.bit_count() == 3:
        # A nearby pair is insufficient when the only third orb is far away.
        # Reward bringing all three toward a legal triple, including gaps that
        # do not change the old pair-count score at all.
        return 0, 12 - 2 * _three_orb_match_distance(bits), 0
    pairs = ((bits & (bits >> 1) & _HORIZONTAL_PAIR_STARTS).bit_count()
             + (bits & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS).bit_count()
             + (bits & (bits >> COLS)).bit_count()
             + (bits & (bits >> (2 * COLS))).bit_count())
    return 0, pairs, 0


_FIVE_ATTRIBUTE_NEIGHBORS = tuple(tuple(neighbors(i)) for i in range(ROWS * COLS))


def solve_five_attributes_v101(board, time_budget=FIVE_ATTR_TIME_BUDGET, max_moves=65, spikes=None, avoid_skull=True, seed_offset=0, required_total=0, combo_bonus=0, require_five=True, cancel_event=None):
    """Find the first five-attribute route with incremental, diverse beam search.

    Keep bitboards per color, updating only the two swapped colors. Cache color
    features, not complete combo calculations. Reserve half the beam for different
    matched-color masks so one four-color arrangement cannot crowd out every
    alternative. Restart with deterministic color weights if a beam is exhausted.
    Full combo/group evaluation is used to verify the returned route.
    require_five=False reuses the engine for the master's six-combo shield,
    including heart/jammer matches and repeated matches of the same colour.
    """
    deadline = time.perf_counter() + max(0.0, float(time_budget))
    board0 = list(board)
    if len(board0) != ROWS * COLS or any(color not in COLOR_ORDER for color in board0):
        raise ValueError("퍼즐판에는 유효한 드롭 30칸이 필요합니다.")
    cap = max(0, int(max_moves)) if max_moves is not None else FIVE_ATTR_DEFAULT_MOVE_CAP
    goal = (combo_condition_goals((False, False, True, False), required_total, combo_bonus)
            if require_five else master_survival_goals(bonus=combo_bonus))
    needed = max(0, goal[7]-combo_bonus)
    initial = board_goal_metrics(board0, goal)
    if (initial['all_met'] or (require_five and not five_attribute_possible_by_count(board0))
            or cap == 0 or (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set()))):
        return [0], initial['combos'], initial['matched'], board0

    encoded = bytes(COLOR_ORDER.index(color) for color in board0)
    masks0 = tuple(sum(1 << i for i, color in enumerate(encoded) if color == k)
                   for k in range(len(COLOR_ORDER)))
    thorn0 = sum(1 << i for i, flag in enumerate((list(spikes or []) + [False] * 30)[:30]) if flag)
    has_thorns = bool(thorn0)
    has_poison = bool(masks0[COLOR_ORDER.index('S')]) and avoid_skull
    poison_index = COLOR_ORDER.index('S')
    feature = lru_cache(maxsize=100000)(_attribute_bit_features)
    rng = random.Random(''.join(board0) + (f':{seed_offset}' if seed_offset else ''))
    width = max(30, FIVE_ATTR_BEAM_WIDTH)
    best_path = [0]
    best_rank = None

    def evaluate(masks, weights, thorn_bits, crossings):
        matched = 0
        potential = 0.0
        cleared = 0
        for color in range(5 if require_five else len(COLOR_ORDER)):
            found, pairs, marked = feature(masks[color])
            matched |= found << color
            if not found or not require_five:
                potential += min(pairs, 12) * weights[color % 5]
            if has_thorns:
                cleared += (marked & thorn_bits).bit_count()
        if has_thorns and require_five:
            for color in range(5, len(COLOR_ORDER)):
                cleared += (feature(masks[color])[2] & thorn_bits).bit_count()
        poison = feature(masks[poison_index])[2].bit_count() if has_poison else 0
        count = sum(_matched_bit_combo_count(bits) for bits in masks) if needed > 5 or not require_five else 0
        rank = (matched.bit_count(), -crossings if has_thorns else 0,
                cleared if has_thorns else 0, -poison, potential)
        if not require_five:
            rank = (min(count,needed), *rank[1:-1], potential)
        elif needed > 5:
            rank = (matched.bit_count()+min(count,needed), matched.bit_count(), min(count,needed), *rank[1:])
        return matched, rank

    while time.perf_counter() < deadline and not (cancel_event is not None and cancel_event.is_set()):
        weights = tuple(rng.uniform(.7, 1.4) for _ in range(5))
        matched, initial_rank = evaluate(masks0, weights, thorn0, 0)
        states = [(initial_rank, encoded, masks0, i, -1, (i,), matched, thorn0, 0)
                  for i in range(ROWS * COLS)]
        # Lower crossing counts dominate repeated board/cursor/thorn states.
        seen = {}
        for _depth in range(cap):
            candidates = {}
            for _, current_board, masks, cursor, previous, path, _, thorn_bits, crossings in states:
                if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                    break
                held = current_board[cursor]
                for nxt in _FIVE_ATTRIBUTE_NEIGHBORS[cursor]:
                    if nxt == previous:
                        continue
                    if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                        break
                    displaced = current_board[nxt]
                    moved = bytearray(current_board)
                    moved[cursor], moved[nxt] = displaced, held
                    moved = bytes(moved)
                    swap = (1 << cursor) | (1 << nxt)
                    next_thorns = thorn_bits
                    next_crossings = crossings
                    if has_thorns:
                        next_crossings += (thorn_bits >> nxt) & 1
                        if ((thorn_bits >> cursor) ^ (thorn_bits >> nxt)) & 1:
                            next_thorns ^= swap
                    key = (moved, nxt, cursor, next_thorns)
                    if seen.get(key, next_crossings + 1) <= next_crossings:
                        continue
                    seen[key] = next_crossings
                    next_masks = list(masks)
                    if held != displaced:
                        next_masks[held] ^= swap
                        next_masks[displaced] ^= swap
                    next_masks = tuple(next_masks)
                    matched, rank = evaluate(next_masks, weights, next_thorns, next_crossings)
                    route = path + (nxt,)
                    if (matched == 31 if require_five else rank[0]>=needed):
                        # Independent replay/group calculation is authoritative.
                        after = board_after_drag(board0, route)
                        details = board_goal_metrics(after, goal)
                        if details['all_met']:
                            return list(route), details['combos'], details['matched'], after
                    if best_rank is None or (rank, -len(route)) > best_rank:
                        best_rank = (rank, -len(route))
                        best_path = list(route)
                    candidates[key] = (rank, moved, next_masks, nxt, cursor, route,
                                       matched, next_thorns, next_crossings)
            if not candidates or (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                break
            ordered = sorted(candidates.values(), key=lambda state: state[0], reverse=True)
            states = ordered[:width // 2]
            selected = {id(state) for state in states}
            buckets = {}
            for state in ordered:
                if id(state) not in selected:
                    buckets.setdefault((state[6],state[0][2]) if needed>5 else state[6], []).append(state)
            # Iterate each bucket in rank order without repeatedly shifting lists.
            positions = {mask: 0 for mask in buckets}
            while len(states) < width and positions:
                for mask in list(positions):
                    index = positions[mask]
                    states.append(buckets[mask][index])
                    if index + 1 == len(buckets[mask]):
                        del positions[mask]
                    else:
                        positions[mask] = index + 1
                    if len(states) >= width:
                        break
        if cap == 1:
            break  # All legal one-move routes have already been examined.
    after = board_after_drag(board0, best_path)
    combos, matched, _ = combo_details(after)
    return best_path, combos, matched, after


def _red_text_fraction(image: Image.Image, token: OCRToken, start: int, end: int) -> tuple[float, int]:
    """Measure red glyph pixels for one numeric span inside an OCR box.

    The crystal shortage UI marks the required count in red. Looking only at the
    OCR string cannot distinguish the red required number from white required
    numbers, so verify the actual screenshot pixels at that number's position.
    """
    W, H = image.size
    raw = str(token.text or "")
    if not raw or end <= start:
        return 0.0, 0
    box_w = max(1.0, token.x2 - token.x1)
    char_w = box_w / max(1, len(raw))
    pad_x = max(1.5, char_w * 0.18)
    x0 = max(0, int(round(token.x1 + char_w * start - pad_x)))
    x1 = min(W, int(round(token.x1 + char_w * end + pad_x)))
    pad_y = max(2, int(round((token.y2 - token.y1) * 0.12)))
    y0 = max(0, int(round(token.y1 - pad_y)))
    y1 = min(H, int(round(token.y2 + pad_y)))
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0
    arr = np.asarray(image.convert("RGB"))[y0:y1, x0:x1].astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    # Saturated red digits on the dark capsule; tolerate anti-aliased edges.
    mask = (r >= 135) & (r >= g * 1.25) & (r >= b * 1.18) & (g <= 175) & (b <= 190)
    n = int(mask.sum())
    return float(mask.mean()) if mask.size else 0.0, n



# Game UI-specific red numeral shapes, derived from the sample screenshots.
# OCR remains a fallback for unseen glyphs. No cloud service is involved.
_RED_DIGIT_PATTERNS = {'6': ['000000000111100000000000000001111000000000000111111111111000000011111111111111000001111111111111111000011111100001111110000111111000011111100011111100000011111100111110000000011110111111100000000000001111100000000000000011111000000000000000111111111111111000001111111111111111110011111111111111111110111111111000011111101111111110000111111011111110000000111111111111100000000111111111100000000001111111111000000000011111111110000000000111111111111000000001111100111110000000011111001111110000001111110011111111001111111000111111110011111110000111111111111111000000111111111111110000000111111111100000'], '3': ['000000001110000000000000000011100000000000001111111111100000000111111111111111000011111111111111110011111111000011111110111111110000111111101111111000000011111011111100000000111111001100000000001111110000000000000011111000000000000000111110000000000001111111100000000111111111110000000011111111110000000000011111111111000000000111111111110000000000111111111110000000000000001111110000000000000001111100000000000000011111000000000000000111111111000000000001111111111100000000011111111111100000001111111111111100011111111011111111000111111110001111111111111111100001111111111111110000001111111111110000'], '1': ['000000000011111111000000000000111111110000000000111111111111000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111100'], '7': ['111111111111111111001111111111111111110011111111111111111110111111111111111111100011111111111111111100111111111111111111000000000000001111100000000000000011111000000000000001111110000000000000011111100000000000001111110000000000000011111000000000000011111110000000000000111111100000000000011111100000000000000111111000000000000011111100000000000001111110000000000000011111100000000000000111110000000000000111111100000000000001111111000000000000011111110000000000000111110000000000000001111100000000000000111111000000000000001111110000000000000011111100000000000000111110000000000000000111100000000000']}

# v84: red single-digit 2 captured from the user's 1600x900 four-material screen.
_RED_DIGIT_PATTERNS.setdefault('2', []).append('000000000111100000000000000001111000000000000111111111111000000011111111111111000001111111111111111000111111100001111110001111111000011111100011111000000011111100111110000000011111001111100000000111110011100000000001111100111000000000011111000000000000001111110000000000000111111000000000000011111110000000000111111111000000000001111111110000000000111111111000000001111111110000000000111111110000000000011111110000000000000111111100000000000011111100000000000011111110000000000000111111110000000000001111111111111111111111111111111111111111111111111111111111111111111111111111111100011111111111111110')

# v76: additional local pixel templates for narrow 1 and open-top 4 glyphs.
_RED_DIGIT_PATTERNS.setdefault('1', []).append('000000000011111111000000000000111111110000000111111111111111000111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000001111111111000000000011111111110000000000111111111100000000000001111100')
_RED_DIGIT_PATTERNS.setdefault('4', []).append('000000000001111100000000000000011111000000000000000111111000000000000011111110000000000001111111100000000000011111111000000000000111111110000000000011111111100000000011111111111000000000111111111110000000001111101111100000000111110011111000000011111100111110000000111111001111100000001111100011111000000111111000111110000001111100001111100000111100000011111000001111000000111110001111111111111111111011111111111111111111111111111111111111111111111111111111111100111111111111111111000000000001111111100000000000001111100000000000000011111000000000000000111110000000000000000111100000000000000001111000')

def _red_digit_from_patch(component):
    import cv2
    yy, xx = np.where(component > 0)
    if len(xx) < 8:
        return None, 0.0
    patch = component[yy.min():yy.max()+1, xx.min():xx.max()+1].astype(np.uint8)
    patch = cv2.resize(patch, (20, 30), interpolation=cv2.INTER_NEAREST)
    best_digit, best_dist = None, 999.0
    for digit, patterns in _RED_DIGIT_PATTERNS.items():
        for bits in patterns:
            ref = np.fromiter((int(c) for c in bits), dtype=np.uint8).reshape(30,20)
            # Allow a pixel shift from changing MuMu antialiasing.
            dist = float(np.mean(ref != patch))
            if dist < best_dist:
                best_dist, best_digit = dist, int(digit)
    return (best_digit, 1.0-best_dist) if best_dist < .24 else (None, 1.0-best_dist)

def requirement_badge_views(badge):
    """Separate red OR white numerals from a dark pill, independent of OCR detection."""
    import cv2
    rgb = np.asarray(badge.convert('RGB'), dtype=np.int16)
    if badge.width < 20 or badge.height < 12:
        raise ValueError('필요 수량 표시가 너무 작습니다.')
    interior = rgb[2:-2, 7:-7]
    red = ((interior[...,0] >= 125) & (interior[...,0] > interior[...,1]+40)
           & (interior[...,0] > interior[...,2]+30))
    white = (interior.min(axis=2) >= 155) & (interior.max(axis=2)-interior.min(axis=2) < 85)
    mask = (red | white).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    components = []
    cleaned = np.zeros_like(mask)
    for index in range(1,count):
        x,y,w,h,area = (int(v) for v in stats[index])
        if h >= max(7, mask.shape[0]*.32) and w >= 2 and area >= 8:
            component = (labels[y:y+h,x:x+w] == index).astype(np.uint8)
            components.append((x,component))
            cleaned[labels == index] = 1
    components.sort(key=lambda pair:pair[0])
    if not 1 <= len(components) <= 4:
        return [], []
    ys,xs = np.where(cleaned)
    glyphs = cleaned[ys.min():ys.max()+1,xs.min():xs.max()+1]
    scale = 64/glyphs.shape[0]
    glyphs = cv2.resize(255-glyphs*255, (max(1,round(glyphs.shape[1]*scale)),64),
                        interpolation=cv2.INTER_NEAREST)
    padded = cv2.copyMakeBorder(glyphs,12,12,12,12,cv2.BORDER_CONSTANT,value=255)
    smooth = cv2.GaussianBlur(padded,(3,3),.6)
    return [component for _,component in components], [Image.fromarray(padded).convert('RGB'),
                                                      Image.fromarray(smooth).convert('RGB')]


def read_requirement_badge(badge, read_tokens, recognize_number):
    """Restore exact game-digit matching, then fall back to recognition without detection."""
    components, views = requirement_badge_views(badge)
    if not components:
        raise ValueError('수량 표시 안에서 빨간색/흰색 숫자 획을 확인하지 못했습니다.')
    pixel_digits = []
    for component in components:
        digit,confidence = _red_digit_from_patch(component)
        # The partial game-font bank is only authoritative for near-exact shapes.
        # Unseen digits/fonts use OCR; never coerce an unknown glyph into 6 or 3.
        if digit is None or confidence < .90:
            pixel_digits = []
            break
        pixel_digits.append(str(digit))
    if pixel_digits:
        return int(''.join(pixel_digits)), '게임 숫자 모양 일치'
    enlarged = badge.resize((badge.width*4,badge.height*4),Image.Resampling.LANCZOS)
    tokens = read_tokens(enlarged)
    numbers = {int(t.text.strip()) for t in tokens if t.score >= .85
               and re.fullmatch(r'\s*\d{1,4}\s*',t.text)}
    if len(numbers) > 1:
        raise ValueError('필요 수량 OCR 결과가 서로 다릅니다.')
    if len(numbers) == 1:
        return numbers.pop(), '필요 수량 영역 OCR'
    votes = []
    for view in views:
        number,confidence,text = recognize_number(view)
        if number is not None and confidence >= .85:
            votes.append(number)
    if len(votes) == 2 and len(set(votes)) == 1:
        return votes[0], '숫자만 직접 인식 · 두 영상 일치'
    observed = ', '.join(repr(t.text) for t in tokens) or '검출 없음'
    raise ValueError(f'필요 숫자를 읽지 못했습니다 (영역 OCR: {observed}, 직접 인식: {votes}).')


def read_owned_digit_line(image, cx, cy, recognize_quantity):
    """Recognition-only fallback for the red/white ×N at the card's lower right."""
    import cv2
    box=(max(0,cx-40),max(0,cy+110),min(image.width,cx+50),min(image.height,cy+155))
    rgb=np.asarray(image.crop(box).convert('RGB'),dtype=np.int16)
    red=(rgb[...,0]>=125)&(rgb[...,0]>rgb[...,1]+40)&(rgb[...,0]>rgb[...,2]+30)
    white=(rgb.min(axis=2)>=155)&(rgb.max(axis=2)-rgb.min(axis=2)<85)
    count,labels,stats,_=cv2.connectedComponentsWithStats((red|white).astype(np.uint8),connectivity=8)
    mask=np.zeros(labels.shape,dtype=np.uint8)
    for index in range(1,count):
        x,y,w,h,area=(int(v) for v in stats[index])
        # Border highlights touch the edge; quantity glyphs sit near cy+137.
        if (h>=9 and w>=2 and area>=10 and 0<x and x+w<rgb.shape[1]
                and 0<y and y+h<rgb.shape[0] and abs((box[1]+y+h/2)-(cy+137))<=13):
            mask[labels==index]=1
    ys,xs=np.where(mask)
    if len(xs)<15:
        return None
    glyph=mask[ys.min():ys.max()+1,xs.min():xs.max()+1]
    scale=64/glyph.shape[0]
    binary=cv2.resize(255-glyph*255,(max(1,round(glyph.shape[1]*scale)),64),interpolation=cv2.INTER_NEAREST)
    binary=cv2.copyMakeBorder(binary,12,12,12,12,cv2.BORDER_CONSTANT,value=255)
    votes=[]
    for data in (binary,cv2.GaussianBlur(binary,(3,3),.6)):
        value,confidence,_=recognize_quantity(Image.fromarray(data).convert('RGB'))
        if value is not None and confidence>=.85:
            votes.append(value)
    return votes[0] if len(votes)==2 and len(set(votes))==1 else None


def _locate_red_required_badges(image):
    """Find whole red requirement *badges*, including multi-digit counts.

    v75 discarded the second glyph of 14 as a duplicate and tested the dark
    background on top of that second glyph. Both problems are addressed by
    grouping neighboring characters before checking the charcoal pill.
    """
    import cv2
    rgb = np.asarray(image.convert('RGB'), dtype=np.int16)
    h, w = rgb.shape[:2]
    red = ((rgb[:, :, 0] >= 125) & (rgb[:, :, 0] > rgb[:, :, 1] + 45) &
           (rgb[:, :, 0] > rgb[:, :, 2] + 36)).astype(np.uint8)
    y0, y1 = max(0, int(h * .035)), min(h, int(h * .39))
    n, labels, stats, centers = cv2.connectedComponentsWithStats(red[y0:y1], 8)
    glyphs = []
    for i in range(1, n):
        x, y, bw, bh, area = map(int, stats[i])
        if 8 <= bh <= 36 and 2 <= bw <= 28 and area >= 10 and 18 < x + bw / 2 < w - 18:
            glyphs.append((x, y + y0, bw, bh, area, i))
    glyphs.sort(key=lambda g: (g[1] // 15, g[0]))
    groups = []
    for g in sorted(glyphs, key=lambda g: g[0]):
        x, y, bw, bh, area, component_id = g
        matching = next((group for group in groups if
                         abs((y + bh / 2) - group['cy']) <= 8 and
                         0 <= x - group['right'] <= 13 and len(group['glyphs']) < 4), None)
        if matching is None:
            groups.append({'glyphs': [g], 'left': x, 'right': x + bw,
                           'top': y, 'bottom': y + bh, 'cy': y + bh / 2})
        else:
            matching['glyphs'].append(g)
            matching['right'] = max(matching['right'], x + bw)
            matching['top'] = min(matching['top'], y)
            matching['bottom'] = max(matching['bottom'], y + bh)
            matching['cy'] = (matching['top'] + matching['bottom']) / 2
    result = []
    for group in groups:
        left, right = group['left'], group['right']
        cy = int(round(group['cy']))
        cx = (left + right) / 2
        # Test background *outside the entire multi-digit number*, not at ±27
        # from the first individual character as v75 did.
        if left < 10 or right + 10 >= w or cy - 3 < 0 or cy + 4 >= h:
            continue
        samples = [rgb[cy-3:cy+4, max(0,left-10):left-4],
                   rgb[cy-3:cy+4, right+4:min(w,right+10)]]
        if any(v.size == 0 for v in samples):
            continue
        if any(not (27 <= float(np.median(v.mean(axis=2))) <= 125 and
                    float(np.max(np.median(v,axis=(0,1)))-np.min(np.median(v,axis=(0,1)))) < 24)
               for v in samples):
            continue
        digits, conf = [], []
        for x, y, bw, bh, area, component_id in sorted(group['glyphs'],key=lambda q:q[0]):
            # component labels are local to the red detection lane
            component=(labels[y-y0:y-y0+bh,x:x+bw] == component_id).astype(np.uint8)
            d, score = _red_digit_from_patch(component)
            digits.append(d);conf.append(score)
        known = all(d is not None for d in digits)
        number = int(''.join(str(d) for d in digits)) if known else None
        result.append((float(cx), float(group['cy']), number,
                       float(min(conf) if conf else 0), sum(g[4] for g in group['glyphs'])))
    return sorted(result, key=lambda item:item[0])


def _owned_number_mask(image, xcenter, ycenter):
    """Isolate red stock-count glyphs from the lower-right of an F7 card.

    Also returns a geometry-stable 32x32 binary descriptor for user corrections.
    """
    import cv2
    h,w=image.height,image.width
    x0,x1=max(0,int(xcenter)+15),min(w,int(xcenter)+53)
    y0,y1=max(0,int(ycenter)+112),min(h,int(ycenter)+160)
    if x1<=x0 or y1<=y0: return None
    rgb=np.asarray(image.crop((x0,y0,x1,y1)).convert('RGB'),dtype=np.int16)
    mask=((rgb[:,:,0]>=110)&(rgb[:,:,0]>rgb[:,:,1]+35)&
          (rgb[:,:,0]>rgb[:,:,2]+25)).astype(np.uint8)
    count,lab,stats,_=cv2.connectedComponentsWithStats(mask,8)
    valid=[]
    for i in range(1,count):
        x,y,cw,ch,area=map(int,stats[i])
        if area>=14 and ch>=10 and cw>=2: valid.append((x,y,cw,ch,i))
    if not 1<=len(valid)<=2: return None
    out=np.zeros_like(mask)
    for x,y,cw,ch,i in valid: out[lab==i]=1
    ys,xs=np.where(out)
    if len(xs)<15:return None
    cropped=out[ys.min():ys.max()+1,xs.min():xs.max()+1]
    # nearest-neighbour so the resulting bit vector can be stored in JSON
    return cv2.resize(cropped,(32,32),interpolation=cv2.INTER_NEAREST)


def _owned_template_vote(profile, path):
    """Use only manually verified examples, with an ambiguity threshold."""
    records=_load_json_dict(path).get('samples',[])
    if profile is None or not records:return None,None
    ranked=[]
    for rec in records:
        try:
            bits=np.fromiter((int(c) for c in rec['bits']),dtype=np.uint8).reshape(32,32)
            ranked.append((float(np.mean(bits!=profile)),int(rec['value'])))
        except (KeyError,ValueError,TypeError): pass
    if not ranked:return None,None
    ranked.sort()
    distance,value=ranked[0]
    # Do not accept numerals near a different label's template.
    next_different=min((d for d,v in ranked if v!=value),default=1.0)
    if distance<=0.14 and next_different-distance>=0.055:
        return value,distance
    return None,distance

def _read_owned_digits_from_card(image: Image.Image, xcenter: int, ycenter: int, learned_path=None):
    """Read just the owned digits at the lower RIGHT of a crystal card.

    Requirement badges are about 135 pixels above ×N on the fixed MuMu capture.
    The × is intentionally excluded: requiring OCR to recognize that glyph caused
    valid 5-card scans to fail. Red digits are isolated before local Tesseract OCR.
    Returns (owned_count, diagnostic), never guesses a count on uncertainty.
    """
    import cv2
    import tempfile
    if learned_path is not None:
        profile = _owned_number_mask(image, xcenter, ycenter)
        learned, distance = _owned_template_vote(profile, learned_path)
        if learned is not None:
            return learned, f"사용자 정정 이미지 템플릿={learned} (거리 {distance:.3f})"
    h, w = image.height, image.width
    x0, x1 = max(0, xcenter + 15), min(w, xcenter + 53)
    y0, y1 = max(0, ycenter + 112), min(h, ycenter + 160)
    if x1 - x0 < 18 or y1 - y0 < 20:
        return None, "보유 숫자 영역이 캡처 경계 밖"
    rgb = np.asarray(image.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.int16)
    # The deficit owner's numerals are red, including the zero in ×0.
    red = ((rgb[:,:,0] >= 110) & (rgb[:,:,0] > rgb[:,:,1] + 35)
           & (rgb[:,:,0] > rgb[:,:,2] + 25)).astype(np.uint8) * 255
    if np.count_nonzero(red) < 15:
        return None, "카드 하단에서 빨간 보유 숫자를 찾지 못함"
    # Remove tiny anti-aliasing artifacts; preserve thin handwritten-like strokes.
    n, lab, stats, _ = cv2.connectedComponentsWithStats(red, 8)
    cleaned = np.zeros_like(red)
    for i in range(1, n):
        xx, yy, cw, ch, pixels = map(int, stats[i])
        if pixels >= 7 and ch >= 7 and cw >= 2:
            cleaned[lab == i] = 255
    if not np.any(cleaned):
        return None, "보유 숫자 색상 마스크 비어 있음"
    # Tesseract is already installed for Korean title OCR; no cloud API used.
    # Use one cheap numeral-only read per missing material, not whole-row OCR.
    if not shutil.which("tesseract"):
        return None, "Tesseract 실행 파일을 PATH에서 찾지 못함"
    up = cv2.resize(cleaned, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
    padded = cv2.copyMakeBorder(up, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            temp_path = tf.name
        Image.fromarray(255-padded).save(temp_path)
        proc = subprocess.run(
            ["tesseract", temp_path, "stdout", "--psm", "10", "-l", "eng",
             "-c", "tessedit_char_whitelist=0123456789"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        digits = re.sub(r"\D", "", proc.stdout or "")
        if proc.returncode == 0 and digits and len(digits) <= 4:
            if int(digits) != 0:
                return int(digits), f"빨간 숫자 전용 OCR={digits}"
            return None, "OCR 0은 검증 전 DB 보호"
        # A red zero has an enclosed hole; Tesseract can return nothing for
        # this small outlined glyph. Only accept this strong geometric fallback.
        count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, 8)
        glyphs = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 45
                  and stats[i, cv2.CC_STAT_HEIGHT] >= 13]
        if len(glyphs) == 1:
            component = (labels == glyphs[0]).astype(np.uint8) * 255
            contours, hierarchy = cv2.findContours(component, cv2.RETR_TREE,
                                                    cv2.CHAIN_APPROX_SIMPLE)
            if hierarchy is not None and any(item[3] >= 0 for item in hierarchy[0]):
                return None, "닫힌 고리는 4/6/8/9 가능성 때문에 미확정"
        return None, f"숫자 전용 OCR 판독 실패 ({digits or '빈 결과'})"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"숫자 전용 OCR 실행 오류: {type(exc).__name__}"
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def extract_crystal_cost_targets(image: Image.Image, tokens: list[OCRToken]):
    """Return every red-marked deficient crystal, paired with the ×N value below it.

    The card title is captured separately from the emulator title strip. This OCR
    region only needs to include each material's required-count badge, artwork and
    owned ×N badge. Only red required numbers are treated as shortage candidates.
    """
    W, H = image.size
    number_re = re.compile(r"(?i)(?:×|x|✕|✖|╳|\*)\s*(\d{1,5})")
    marked = []
    for token in tokens:
        raw = token.text.strip().replace("х", "x").replace("Х", "X")
        match = number_re.search(raw)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        number_x = token.x1 + ((match.start(1) + match.end(1)) / (2 * max(1, len(raw)))) * (token.x2-token.x1)
        marked.append({"current": value, "x": number_x, "token": token, "raw": raw,
                       "digit_start": match.start(1), "digit_end": match.end(1)})

    red_required = []
    for token in tokens:
        raw = token.text.strip().replace("х", "x").replace("Х", "X")
        if not raw or number_re.search(raw):
            continue
        for match in OCR_NUMBER_RE.finditer(raw):
            digits = re.sub(r"\D", "", match.group(0))
            if not digits or len(digits) > 4:
                continue
            try:
                value = int(digits)
            except ValueError:
                continue
            cx = token.x1 + ((match.start() + match.end()) / (2 * max(1, len(raw)))) * (token.x2-token.x1)
            red_fraction, red_pixels = _red_text_fraction(image, token, match.start(), match.end())
            # OCR can hallucinate a digit at x=0 from a clipped neighboring card.
            # Such a token is not a reliable required-count badge.
            if token.x1 <= 2 or token.x2 >= W - 2:
                continue
            if red_pixels < 2 or red_fraction < 0.035:
                continue
            red_required.append({"required": value, "x": cx, "y": token.cy,
                                 "token": token, "raw": raw, "red_fraction": red_fraction,
                                 "red_pixels": red_pixels})

    # Single-material tight crop: OCR may read both numbers perfectly while
    # red-pixel classification rejects the required number.  When exactly one
    # ×N and one plain number exist, use their vertical ordering instead.
    if len(marked) == 1 and not red_required:
        owned = marked[0]
        candidates = []
        for token in tokens:
            raw = token.text.strip()
            if number_re.search(raw):
                continue
            if (re.fullmatch(r"\d{1,4}", raw) and token.cy < owned["token"].cy
                    and token.x1 > 2 and token.x2 < W - 2):
                candidates.append((token, int(raw)))
        if len(candidates) == 1:
            t, required = candidates[0]
            if required > owned["current"]:
                red_required.append({"required": required, "x": t.cx,
                    "y": t.cy, "token": t, "raw": t.text,
                    "red_fraction": 0.0, "red_pixels": 0})
    if not marked or not red_required:
        return []

    sorted_x = sorted(item["x"] for item in marked)
    diffs = [b-a for a, b in zip(sorted_x, sorted_x[1:]) if b-a >= 18]
    card_spacing = float(np.median(diffs)) if diffs else None
    paired = []
    used_required = set()
    # Pair each owned ×N badge with its horizontally aligned red need value.
    for item in marked:
        candidates = []
        t = item["token"]
        for ri, n in enumerate(red_required):
            gap = t.cy - n["y"]
            dx = abs(item["x"] - n["x"])
            if ri in used_required:
                continue
            if gap <= max(18.0, t.height * 1.4, n["token"].height * 1.4):
                continue
            if gap > H * (0.98 if len(marked) == 1 and len(red_required) == 1 else 0.78):
                continue
            x_limit = max(20.0, W * 0.055, (card_spacing or 60.0) * 0.40)
            if dx > x_limit:
                continue
            score = dx + gap * 0.025
            candidates.append((score, ri, n, gap))
        if not candidates:
            continue
        candidates.sort(key=lambda row: row[0])
        score, ri, n, gap = candidates[0]
        used_required.add(ri)
        crop_w = int(round(min((card_spacing * 0.84 if card_spacing else gap * 0.78), gap * 0.84)))
        # A wide multi-card row can have just ONE deficient (red) card.
        # Without neighboring OCR tokens the old gap-based width included
        # slivers of adjacent cards and created a different icon ID.
        if W >= 300 and W >= gap * 1.9:
            crop_w = min(crop_w, int(W / 3 * 0.82))
        crop_w = max(46, min(crop_w, max(46, W - 2)))
        crop_h = max(64, int(round(gap * 1.00)))
        center_x = (item["x"] + n["x"]) / 2.0
        top_y = n["y"] + gap * 0.14
        x0 = int(round(center_x - crop_w / 2))
        y0 = int(round(top_y))
        x0 = max(0, min(W-crop_w, x0))
        y0 = max(0, min(H-1, y0))
        crop_h = max(1, min(crop_h, H-y0))
        crop = image.crop((x0, y0, x0+crop_w, y0+crop_h)).convert("RGB")
        deficit = int(n["required"]) - int(item["current"])
        if deficit <= 0:
            continue
        paired.append({"crop": crop, "required": int(n["required"]),
                       "current": int(item["current"]), "deficit": deficit,
                       "x": item["x"], "score": score, "marker": item["raw"],
                       "number": str(n["required"]), "red_fraction": n["red_fraction"],
                       "red_pixels": n["red_pixels"], "crop_box": (x0, y0, x0+crop_w, y0+crop_h)})

    # Strict red-color/geometry checks can reject a perfectly legible single
    # material (e.g. 7 at y=31, ×6 at y=167). Use OCR ordering for a tight crop.
    if not paired and len(marked) == 1:
        owned = marked[0]
        plain = []
        for token in tokens:
            raw = token.text.strip()
            if (re.fullmatch(r"[0-9]{1,4}", raw) and token.cy < owned["token"].cy
                    and token.x1 > 2 and token.x2 < W - 2):
                plain.append((token, int(raw)))
        if len(plain) == 1:
            top_token, required = plain[0]
            current = int(owned["current"])
            if required > current:
                gap = owned["token"].cy - top_token.cy
                # Keep the icon between the two badges; never include badge text
                # in the saved icon crop when there is enough room.
                xcenter = (top_token.cx + owned["x"]) / 2.0
                crop_width = max(1, min(W, int(min(W * .92, max(36, gap * .78)))))
                x0 = max(0, min(W - crop_width, round(xcenter - crop_width / 2)))
                y0 = max(0, min(H - 1, round(top_token.y2 + max(1, gap * .06))))
                y1 = min(H, max(y0 + 1, round(owned["token"].y1 - max(1, gap * .04))))
                if y1 <= y0 + 10:
                    y0 = max(0, min(H - 1, round(top_token.cy + gap * .16)))
                    y1 = min(H, max(y0 + 1, round(top_token.cy + gap * .83)))
                crop = image.crop((int(x0), int(y0), int(x0 + crop_width), int(y1))).convert("RGB")
                paired.append({"crop": crop, "required": required,
                               "current": current, "deficit": required-current,
                               "x": owned["x"], "score": 0.0,
                               "marker": owned["raw"], "number": str(required),
                               "red_fraction": 0.0, "red_pixels": 0,
                               "crop_box": (int(x0), int(y0), int(x0+crop_width), int(y1))})

    # A tight single-material selection contains the full icon frame.  The old
    # geometry-based crop cut away its top/bottom and created duplicate templates.
    if len(marked) == 1 and len(paired) == 1 and W < 300:
        owned_token = marked[0]["token"]
        bottom = max(1, min(H, int(owned_token.y1)))
        if bottom < H * 0.52:
            bottom = H
        # Save the entire selected card, including its bottom badge and frame.
        # Previous versions truncated at the owned-count text and made duplicates.
        paired[0]["crop"] = image.convert("RGB").copy()
        paired[0]["crop_box"] = (0, 0, W, H)
    paired.sort(key=lambda item: item["x"])
    for item in paired:
        item["multiple_shortages"] = len(paired) > 1
        item["shortage_count"] = len(paired)
        item["selection_rule"] = "빨간 필요 수량 + 같은 열의 아래 ×N 보유량"
    return paired


def extract_crystal_cost_target(image: Image.Image, tokens: list[OCRToken]):
    """Backward-compatible helper: return the most deficient target, if any."""
    targets = extract_crystal_cost_targets(image, tokens)
    if not targets:
        return None
    return max(targets, key=lambda item: (int(item.get("deficit", 0)), -float(item.get("score", 9999))))


def material_title_line_views(image):
    """Locate only the centered gray text inside the already captured white header."""
    import cv2
    rgb=np.asarray(image.convert('RGB'),dtype=np.int16)
    height,width=rgb.shape[:2]
    mask=((rgb.max(axis=2)-rgb.min(axis=2)<=65)&(rgb.max(axis=2)<215)).astype(np.uint8)
    margin=max(2,round(height*.08))
    mask[:margin]=0;mask[-margin:]=0
    mask[:,:max(2,round(width*.01))]=0;mask[:,-max(2,round(width*.01)):]=0
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
    boxes=[]
    for index in range(1,count):
        x,y,w,h,area=(int(v) for v in stats[index])
        # A shifted detail modal can leave a dark horizontal background strip
        # at the top. It is not part of the title glyphs.
        if h>=3 and 2<=w<width*.8 and h<height*.75 and area>=4 and area<width*height*.15:
            boxes.append((x,y,x+w,y+h))
    if not boxes: return None,[]
    x0=min(b[0] for b in boxes);y0=min(b[1] for b in boxes)
    x1=max(b[2] for b in boxes);y1=max(b[3] for b in boxes)
    if not width*.25<=(x0+x1)/2<=width*.75 or y1-y0>height*.75 or y1-y0<6:
        return None,[]
    box=(max(0,x0-3),max(0,y0-3),min(width,x1+3),min(height,y1+3))
    gray=cv2.cvtColor(np.asarray(image.crop(box).convert('RGB')),cv2.COLOR_RGB2GRAY)
    normalized=cv2.normalize(gray,None,0,255,cv2.NORM_MINMAX)
    _,binary=cv2.threshold(normalized,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    target_width=max(1,round(gray.shape[1]*64/gray.shape[0]))
    views=[]
    for pixels in (normalized,binary):
        pixels=cv2.resize(pixels,(target_width,64),interpolation=cv2.INTER_CUBIC)
        pixels=cv2.copyMakeBorder(pixels,8,8,12,12,cv2.BORDER_CONSTANT,value=255)
        views.append(Image.fromarray(pixels).convert('RGB'))
    return box,views


def locate_material_modal_title(image):
    """Find a centered gray title on a white modal band in the upper game area.

    This uses pixels only. It never supplies a material name or trusts the
    acquisition-stage portrait behind/below the title.
    """
    rgb=np.asarray(image.convert('RGB'),dtype=np.int16)
    height,width=rgb.shape[:2]
    if width<80 or height<15: return None
    neutral=rgb.max(axis=2)-rgb.min(axis=2)<18
    white=neutral & (rgb.min(axis=2)>235)
    flank=max(1,width//6)
    rows=(white[:,:flank].mean(axis=1)>.90) & (white[:,-flank:].mean(axis=1)>.90)
    # Join short anti-aliased interruptions without joining separate white panels.
    import cv2
    rows=cv2.morphologyEx(rows.astype(np.uint8).reshape(-1,1),cv2.MORPH_CLOSE,
                          np.ones((3,1),np.uint8)).ravel().astype(bool)
    edges=np.diff(np.r_[False,rows,False].astype(np.int8))
    for top,bottom in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
        if not 14<=bottom-top<=max(110,round(height*.45)): continue
        crop=image.crop((0,int(top),width,int(bottom)))
        box,views=material_title_line_views(crop)
        if not views: continue
        x0,y0,x1,y1=box
        # Exclude long paragraphs, dark scenery and titles clipped by a window.
        if not .025*width<=x1-x0<=.65*width: continue
        return (0,int(top),width,int(bottom)),crop
    return None


def material_title_fingerprint(image):
    """Scale-independent glyph snapshot, used to avoid OCR on unchanged screens."""
    box,views=material_title_line_views(image)
    if not views: return None
    view=views[1].convert('L').resize((160,32),Image.Resampling.NEAREST)
    # Keep the text width: short owner names and longer crystal titles must differ.
    return hashlib.sha256(view.tobytes()+str(round((box[2]-box[0])/(box[3]-box[1]),1)).encode()).hexdigest()


def extract_card_name_from_title(tokens: list[OCRToken], image_size: tuple[int, int]) -> Optional[str]:
    """Extract the monster/card name from a narrow title strip.

    Titles commonly look like ``용제왕 세리아스의 크리스탈``. OCR can emit the
    complete line as one token or split it into several boxes, so we inspect both
    y-grouped rows and the whole sequence. If the suffix is imperfectly recognised,
    fall back to a compact non-generic line rather than silently dropping the name.
    """
    width, height = image_size
    generic_compact = {
        "생성확인", "확인", "크리스탈", "크리스탈획득", "크리스털", "부족", "필요",
        "한계돌파", "초절", "돌파", "강화", "각성", "보유", "생성하기", "생성에필요한",
        "초절한계돌파확인", "결과", "메뉴", "다음", "취소", "닫기", "퍼즐드래곤",
        "puzzledragons", "대상카드", "카드이름", "라", "no", "lv", "hp", "공격력", "회복력"
    }
    eligible = [t for t in tokens if t.text.strip() and t.score >= 0.08]
    if not eligible:
        return None
    eligible.sort(key=lambda t: (t.cy, t.x1))
    rows: list[list[OCRToken]] = []
    for token in eligible:
        best_row = None
        best_distance = 1e9
        for row in rows[-5:]:
            row_y = sum(t.cy for t in row) / len(row)
            row_height = sum(t.height for t in row) / len(row)
            tolerance = max(10.0, min(30.0, max(token.height, row_height) * 1.05))
            distance = abs(token.cy - row_y)
            if distance <= tolerance and distance < best_distance:
                best_row, best_distance = row, distance
        if best_row is None:
            rows.append([token])
        else:
            best_row.append(token)

    lines = []
    for row in rows:
        line = " ".join(t.text.strip() for t in sorted(row, key=lambda t: t.x1))
        if line:
            lines.append((line, max(t.score for t in row), sum(t.cx for t in row) / len(row)))
    # The narrow user-selected region is usually one line; this recovers the line
    # even when OCR split words into different y rows due to character accents.
    all_line = " ".join(t.text.strip() for t in sorted(eligible, key=lambda t: (t.cy, t.x1)))
    if all_line:
        lines.append((all_line, sum(t.score for t in eligible) / len(eligible), width / 2))

    candidates = []
    for raw_line, score, center_x in lines:
        raw_line = re.sub(r"\s+", " ", raw_line).strip(" :：-—|·")
        compact = _compact_ocr_text(raw_line)
        if not compact:
            continue
        # First and strongest rule: title names preceding "의 크리스탈" or
        # "의 생성". Optional particle tolerates OCR dropping the particle.
        suffix_patterns = (
            r"^(.+?)\s*(?:의\s*)?크리스탈(?:획득)?$",
            r"^(.+?)\s*(?:의\s*)?크리스털(?:획득)?$",
            r"^(.+?)\s*의\s*(?:생성|한계돌파)$",
        )
        extracted = None
        for pat in suffix_patterns:
            m = re.search(pat, raw_line, flags=re.IGNORECASE)
            if m:
                extracted = m.group(1).strip(" :：-—|·")
                break
        if extracted:
            extracted_compact = _compact_ocr_text(extracted)
            if len(extracted_compact) >= 2 and _compact_text_name(extracted):
                candidates.append((0, -score, abs(center_x - width / 2), -len(extracted_compact), extracted))
                continue
        # If the recognized string is a standalone name rather than a title with
        # suffix, allow it as a fallback; reject UI labels and pure statistics.
        compact_line = _compact_ocr_text(raw_line)
        stripped = re.sub(r"(?:의)?크리스탈(?:획득)?$", "", raw_line, flags=re.IGNORECASE).strip()
        stripped_compact = _compact_ocr_text(stripped)
        if stripped and len(stripped_compact) >= 2 and _compact_text_name(stripped):
            if stripped_compact not in generic_compact and not any(g in stripped_compact for g in (
                "생성확인", "한계돌파확인", "크리스탈획득", "생성에필요", "결과화면"
            )):
                if len(stripped_compact) <= 40:
                    candidates.append((1, -score, abs(center_x - width / 2), -len(stripped_compact), stripped))
        elif compact_line not in generic_compact and len(compact_line) <= 40 and _compact_text_name(raw_line):
            if not any(g in compact_line for g in ("생성확인", "한계돌파확인", "크리스탈획득", "생성에필요")):
                candidates.append((2, -score, abs(center_x - width / 2), -len(compact_line), raw_line))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    result = re.sub(r"\s+", " ", candidates[0][4]).strip()
    return result[:60] if result else None

def extract_card_name_from_raw_text(raw_text: str) -> Optional[str]:
    """Extract a card name from OCR text, tolerating split tokens/spaces and imperfect suffixes."""
    if not raw_text:
        return None
    generic = {
        "크리스탈", "크리스털", "크리스탈획득", "생성", "생성확인", "확인",
        "생성하기", "한계돌파", "한계돌파확인", "초절한계돌파확인", "결과",
        "다음", "취소", "닫기", "부족", "필요", "보유", "라",
    }
    # Prefer explicit title endings. Handle minor OCR variation in the particle and suffix.
    for line in [re.sub(r"\s+", " ", part).strip(" :：-—|·") for part in raw_text.splitlines() if part.strip()]:
        patterns = (
            r"^(.+?)\s*(?:의\s*)?크리스탈(?:\s*획득)?$",
            r"^(.+?)\s*(?:의\s*)?크리스털(?:\s*획득)?$",
            r"^(.+?)\s*의\s*(?:생성|한계돌파)$",
        )
        for pat in patterns:
            m = re.search(pat, line, flags=re.IGNORECASE)
            if m:
                candidate = re.sub(r"\s+", " ", m.group(1)).strip(" :：-—|·")
                compact = _compact_ocr_text(candidate)
                if 2 <= len(compact) <= 40 and _compact_text_name(candidate) and compact not in generic:
                    return candidate[:60]
        # If OCR inserts spaces inside the suffix, use a compact fallback but
        # reconstruct a readable name from the original string when possible.
        compact_line = _compact_ocr_text(line)
        for suffix in ("크리스탈획득", "크리스탈", "크리스털", "한계돌파", "생성"):
            if compact_line.endswith(suffix) and len(compact_line) > len(suffix):
                compact_prefix = compact_line[:-len(suffix)]
                if compact_prefix.endswith("의"):
                    compact_prefix = compact_prefix[:-1]
                # Use original spacing unless OCR split every character.
                candidate = line
                candidate = re.sub(r"(?:의\s*)?크리스탈(?:\s*획득)?$", "", candidate).strip(" :：-—|·")
                candidate = re.sub(r"(?:의\s*)?크리스털$", "", candidate).strip(" :：-—|·")
                if len(_compact_ocr_text(candidate)) < 2:
                    candidate = compact_prefix
                if 2 <= len(_compact_ocr_text(candidate)) <= 40 and _compact_text_name(candidate):
                    return candidate[:60]
    # Last resort: one reasonable non-generic OCR line may itself be the name.
    for line in [re.sub(r"\s+", " ", part).strip(" :：-—|·") for part in raw_text.splitlines() if part.strip()]:
        compact = _compact_ocr_text(line)
        if (2 <= len(compact) <= 40 and compact not in generic
                and _compact_text_name(line)
                and not re.search(r"(?:생성확인|한계돌파확인|크리스탈획득|생성에필요|HP|공격력|회복력)", compact, re.I)):
            return line[:60]
    return None

def _compact_text_name(text: str) -> bool:
    compact = _compact_ocr_text(text)
    # Keep meaningful Korean/Latin/card-name candidates and reject numeric UI labels.
    return len(compact) >= 1 and bool(re.search(r"[가-힣A-Za-zぁ-んァ-ヶ一-龯]", compact))


def set_clickthrough(widget) -> bool:
    """Apply transparent, click-through, no-activate styles to the real HWND.

    Tk's winfo_id() may return the client HWND rather than the outer toplevel
    HWND. The earlier prototype applied WS_EX_TRANSPARENT to that inner handle,
    which can leave the full-screen overlay intercepting all mouse clicks.
    """
    if __import__("sys").platform != "win32":
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        raw = wintypes.HWND(widget.winfo_id())
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(raw, 2) or raw  # GA_ROOT

        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        style = get_long(hwnd, GWL_EXSTYLE)
        set_long(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)

        # COLORREF for magenta #FF00FF, with LWA_COLORKEY.
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
        user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        if not user32.SetLayeredWindowAttributes(hwnd, 0x00FF00FF, 0, 0x00000001):
            return False

        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        HWND_TOPMOST = wintypes.HWND(-1)
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0010, 0x0020
        if not user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                   SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED):
            return False
        return True
    except Exception:
        return False


def find_emulator_window_info(point=None):
    """Locate a likely MuMu/Android game host window and return screen bounds.

    Title matching is preferred; if a board point is supplied, a visible non-own
    window containing it can be used as a fallback. This makes saved board regions
    reusable relative to the emulator rather than to the desktop's absolute pixels.
    """
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        current_pid = os.getpid()
        windows = []
        keywords = {
            "mumu": 120, "memu": 115, "puzzle dragons 0": 110,
            "puzzle dragons": 105, "android device": 85,
            "google play": 75, "emulator": 70, "nox": 60,
            "bluestacks": 60,
        }
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, lparam):
            try:
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    return True
                title_len = user32.GetWindowTextLengthW(hwnd)
                if title_len <= 0:
                    return True
                title_buf = ctypes.create_unicode_buffer(title_len + 1)
                user32.GetWindowTextW(hwnd, title_buf, title_len + 1)
                title = title_buf.value.strip()
                if not title:
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == current_pid:
                    return True
                rect = wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                x, y = int(rect.left), int(rect.top)
                w, h = int(rect.right - rect.left), int(rect.bottom - rect.top)
                if w < 500 or h < 350 or w * h < 220000:
                    return True
                norm = title.lower()
                score = max((weight for word, weight in keywords.items() if word in norm), default=0)
                contains_point = False
                if point is not None:
                    px, py = point
                    contains_point = x <= px < x+w and y <= py < y+h
                if score > 0 or contains_point:
                    # Specific emulator/game titles outrank generic parent windows;
                    # among matching windows prefer the larger outer host window.
                    score = score * 1_000_000 + min(w*h, 4_000_000)
                    if contains_point:
                        score += 500_000_000
                    windows.append({"hwnd": int(hwnd), "title": title,
                                    "rect": (x, y, w, h), "score": score,
                                    "title_score": max((weight for word, weight in keywords.items() if word in norm), default=0)})
            except Exception:
                pass
            return True
        user32.EnumWindows(EnumWindowsProc(callback), 0)
        if not windows:
            return None
        # MuMu 12 exposes two separate top-level windows. Only Android Device
        # contains the game; MuMuPlayer is the launcher/manager, never F7's source.
        game_windows = [w for w in windows if
                        ("mumu android device" in w["title"].lower() or
                         w["title"].strip().lower() == "android device" or
                         "puzzle dragons 0" in w["title"].lower())]
        if game_windows:
            if point is not None:
                containing = [w for w in game_windows if
                              w["rect"][0] <= point[0] < w["rect"][0] + w["rect"][2] and
                              w["rect"][1] <= point[1] < w["rect"][1] + w["rect"][3]]
                if containing:
                    return max(containing, key=lambda w: w["rect"][2]*w["rect"][3])
            return max(game_windows, key=lambda w: w["rect"][2]*w["rect"][3])
        # A MuMuPlayer manager window is never a safe material-scan target.
        windows = [w for w in windows if "mumuplayer" not in w["title"].lower()]
        if not windows:
            return None
        # If selection point given, don't let a random small overlay beat a known emulator.
        titled = [w for w in windows if w["title_score"] > 0]
        if titled:
            return max(titled, key=lambda item: item["score"] - (500_000_000 if point and not (
                item["rect"][0] <= point[0] < item["rect"][0]+item["rect"][2] and
                item["rect"][1] <= point[1] < item["rect"][1]+item["rect"][3]) else 0))
        if point is not None:
            return min(windows, key=lambda item: item["rect"][2] * item["rect"][3])
        return max(windows, key=lambda item: item["score"])
    except Exception:
        return None


def _board_image_is_plausible(image: Image.Image, board=None) -> bool:
    """Quick reject for stale saved coordinates or a non-board MuMu screen."""
    try:
        w, h = image.size
        ratio = w / max(1, h)
        if not (0.88 <= ratio <= 1.52) or w < 240 or h < 190:
            return False
        if board is None:
            board, _ = classify_board(image)
        unique = set(board)
        normal_unique = unique.intersection(set(COLOR_HUE_ORDER))
        if len(unique) < 3 or len(normal_unique) < 2:
            return False
        # Sample central pixels in cells; a genuine board has saturated orb centers.
        arr = np.asarray(image.convert("RGB"))
        samples = []
        for r in range(ROWS):
            for c in range(COLS):
                cx = int((c + 0.5) * w / COLS)
                cy = int((r + 0.5) * h / ROWS)
                radius_x = max(2, int(w / COLS * 0.12))
                radius_y = max(2, int(h / ROWS * 0.12))
                tile = arr[max(0, cy-radius_y):min(h, cy+radius_y), max(0, cx-radius_x):min(w, cx+radius_x)]
                if tile.size:
                    samples.append(tile.reshape(-1, 3).mean(axis=0))
        if len(samples) < 20:
            return False
        sample_arr = np.asarray(samples, dtype=np.float32) / 255.0
        saturation_proxy = (sample_arr.max(axis=1) - sample_arr.min(axis=1)) / np.maximum(sample_arr.max(axis=1), 0.01)
        colorful = float(np.mean(saturation_proxy > 0.22))
        return colorful >= 0.32
    except Exception:
        return False


def live_board_grid_diagnostics(image, board):
    """Stricter display gate: 30 textured cells with consistent orb colors.

    A few colorful team portraits must not make a party/menu screen look like
    a board. Do not change the existing manual capture classifier here.
    """
    details={'passed':False,'plausible':_board_image_is_plausible(image,board)}
    if not details['plausible']:
        return dict(details,reason='board_size_colors_or_saturation')
    small = np.asarray(image.convert('RGB').resize((120, 100), Image.Resampling.BILINEAR))
    cells = small.reshape(ROWS, 20, COLS, 20, 3).transpose(0, 2, 1, 3, 4)
    textured = cells.astype(np.float32).std(axis=(2, 3)).mean(axis=2) / 255.0
    details['textured_fraction']=float(np.mean(textured >= .045))
    details['required_textured_fraction']=.75
    if details['textured_fraction'] < .75:
        return dict(details,reason='too_few_textured_cells')
    hue, saturation, value = rgb_to_hsv_np(small)
    prototypes = np.asarray([COLOR_INFO[color][2] for color in COLOR_HUE_ORDER])
    offsets = ((-.29, 0), (.29, 0), (0, -.29), (0, .29),
               (-.205, -.205), (.205, -.205), (-.205, .205), (.205, .205))
    consistency = []
    visible_consistency = []
    visible_samples = []
    for index, color in enumerate(board):
        if color in ('J', 'S'):
            continue  # Dark hazard art is not a normal colored orb ring.
        row, col = divmod(index, COLS)
        votes = [0] * len(prototypes)
        for dx, dy in offsets:
            x, y = round((col+.5+dx)*20), round((row+.5+dy)*20)
            if saturation[y, x] < .32 or value[y, x] < .24:
                continue
            distance = np.abs(prototypes-hue[y, x])
            chosen = int(np.argmin(np.minimum(distance, 360-distance)))
            votes[chosen] += 1
        valid = sum(votes)
        consistency.append(max(votes) / len(offsets))
        visible_samples.append(valid / len(offsets))
        visible_consistency.append(max(votes)/valid if valid >= 3 else 0.0)
    details.update(normal_cell_count=len(consistency),required_normal_cells=8,
                   color_consistency=float(np.mean(consistency)) if consistency else 0.0,
                   required_color_consistency=.65)
    # Pale highlights and the yellow lock hide some samples. Missing samples
    # are not conflicting colors: normalize by usable pixels, with coverage
    # and stronger agreement requirements so sparse menu art cannot pass.
    details.update(visible_color_consistency=float(np.mean(visible_consistency)) if visible_consistency else 0.0,
                   ring_sample_coverage=float(np.mean(visible_samples)) if visible_samples else 0.0)
    passed=len(consistency)>=8 and (details['color_consistency']>=.65 or
        (details['visible_color_consistency']>=.85 and details['ring_sample_coverage']>=.45))
    return dict(details,passed=passed,reason='accepted' if passed else 'orb_ring_color_consistency')


def _live_board_grid_is_visible(image, board):
    return live_board_grid_diagnostics(image,board)['passed']


def _find_reward_amount_tokens(tokens: list[OCRToken], image_size: tuple[int, int]) -> list[tuple[int, OCRToken]]:
    """Find card-local ×N quantity labels, ignoring the global orange ×2 badge."""
    width, height = image_size
    found = []
    for token in tokens:
        raw = token.text.strip().replace("х", "x").replace("Х", "X")
        match = re.search(r"(?i)(?:×|x|✕|✖|✕|╳)\s*(\d{1,5})", raw)
        if not match:
            continue
        amount = int(match.group(1))
        # Reward cards live in the body of the centered modal. The global multiplier
        # badge is near the header, so its y-coordinate is intentionally excluded.
        if not (0.30 * height <= token.cy <= 0.94 * height):
            continue
        if not (0.16 * width <= token.cx <= 0.88 * width):
            continue
        if amount >= 0:
            found.append((amount, token))
    # One OCR region can occasionally be returned more than once; merge near-identical boxes.
    unique = []
    for item in sorted(found, key=lambda pair: (pair[1].cy, pair[1].cx)):
        amount, token = item
        if any(amount == old_amount and abs(token.cx-old_token.cx) < 12 and abs(token.cy-old_token.cy) < 10
               for old_amount, old_token in unique):
            continue
        unique.append(item)
    return unique


def extract_reward_item_crops(image: Image.Image, tokens: list[OCRToken]) -> list[dict]:
    """Extract reward cards using their OCR ×N badge as an anchor.

    The card geometry is scaled from screenshot dimensions. It is designed around
    the game's reward result layout and fails closed if no quantity label is found.
    """
    W, H = image.size
    card_w = max(60, int(W * 0.072))
    card_h = max(80, int(H * 0.168))
    items = []
    for amount, token in _find_reward_amount_tokens(tokens, image.size):
        x0 = int(round(token.x1 - card_w * 0.52))
        y0 = int(round(token.y1 - card_h * 0.81))
        x1, y1 = x0 + card_w, y0 + card_h
        # Require most of the card to be on screen; otherwise mark it unhandled.
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            continue
        crop = image.crop((x0, y0, x1, y1)).convert("RGB")
        if crop.width < 48 or crop.height < 64:
            continue
        items.append({"amount": int(amount), "crop": crop,
                      "screen_box": (x0, y0, x1, y1), "x": token.cx, "y": token.cy})
    return sorted(items, key=lambda item: (item["y"], item["x"]))



def detect_reward_card_rects(image: Image.Image, viewport=None) -> list[tuple[int, int, int, int]]:
    """Find individually rendered crystal cards by their saturated rectangular art.

    Unlike fixed slots, this also works when the number of rewards changes and the
    game resizes the cards. This is only a crop detector, never a quantity guess.
    """
    try:
        import cv2
        arr = np.asarray(image.convert("RGB"))
        H, W = arr.shape[:2]
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
        # Inside reward dialog; exclude orange frame/header and bottom navigation.
        ratios=viewport or (.23,.77,.215,.77)
        left,right=int(W*ratios[0]),int(W*ratios[1])
        top,bottom=int(H*ratios[2]),int(H*ratios[3])
        patch = hsv[top:bottom, left:right]
        mask = (np.asarray((patch[...,1] > 85) & (patch[...,2] > 85), dtype=np.uint8) * 255)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
        n, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
        boxes = []
        for x,y,w,h,area in stats[1:]:
            x,y,w,h,area = map(int,(x,y,w,h,area))
            if not (.035*W <= w <= .14*W and .09*H <= h <= .29*H):
                continue
            if area < max(250, .12*w*h):
                continue
            # Some frames lose a few bright pixels at the top or sides.
            pad_x = max(2,int(w*.025)); pad_y = max(2,int(h*.025))
            bx0=max(0,left+x-pad_x); by0=max(0,top+y-pad_y)
            bx1=min(W,left+x+w+pad_x); by1=min(H,top+y+h+pad_y)
            if any(abs((bx0+bx1)-(a+c)) < .04*W and abs((by0+by1)-(b+d)) < .04*H for a,b,c,d in boxes):
                continue
            boxes.append((bx0,by0,bx1,by1))
        return sorted(boxes, key=lambda b:(round((b[1]+b[3])/(.12*H)),b[0]))
    except Exception:
        return []


def extract_visual_reward_items(image: Image.Image, tokens: list[OCRToken],
                                read_amount=None, boxes=None) -> list[dict]:
    """Match quantity labels with independently detected cards; never infer ×2 globally."""
    if boxes is None: boxes=detect_reward_card_rects(image)
    amounts=_find_reward_amount_tokens(tokens,image.size)
    items=[]
    for x0,y0,x1,y1 in boxes:
        cx=(x0+x1)/2
        # A local ×N must be near the bottom of this particular card.
        candidates=[(amount,t) for amount,t in amounts
                    if x0-8 <= t.cx <= x1+9 and y0+.58*(y1-y0) <= t.cy <= y1+17]
        quantity=None
        if candidates:
            quantity=min(candidates,key=lambda v:abs(v[1].cx-cx)+abs(v[1].cy-(y1-15)))[0]
        if quantity is None and read_amount is not None:
            # OCR only the lower half where the orange quantity digits occur.
            zone=image.crop((max(0,x0),int(y0+.65*(y1-y0)),min(image.width,x1),min(image.height,y1+7)))
            try:
                amount_tokens=read_amount(zone.resize((zone.width*3,zone.height*3),Image.Resampling.LANCZOS))
                matches=[]
                for t in amount_tokens:
                    match=re.search(r'(?i)(?:×|x|✕|✖|╳)\s*(\d{1,4})',t.text.replace('х','x'))
                    if match:
                        matches.append(int(match.group(1)))
                    elif re.fullmatch(r'\d{1,3}',t.text.strip()) and t.cx > zone.width*1.5:
                        # Lower-right digit only; validate nearby orange lettering.
                        a=np.asarray(zone.convert('RGB'), dtype=np.int16)
                        h,w=a.shape[:2]
                        badge=a[max(0,int(h*.20)):h, int(w*.48):w]
                        if badge.size:
                            R,G,B=badge[...,0],badge[...,1],badge[...,2]
                            orange=(R>=155)&(R>G+25)&(G>40)&(G<180)&(B<130)
                            if float(orange.mean()) >= 0.01:
                                matches.append(int(t.text.strip()))
                if len(set(matches))==1: quantity=matches[0]
            except Exception:
                pass
        if quantity is None or quantity<=0:
            continue
        items.append({'amount':int(quantity),'crop':image.crop((x0,y0,x1,y1)).convert('RGB'),
                      'screen_box':(x0,y0,x1,y1),'x':cx,'y':(y0+y1)/2})
    return items


def extract_partially_visible_reward_items(image: Image.Image, tokens: list[OCRToken],
                                           complete_boxes: list[tuple[int,int,int,int]]) -> list[dict]:
    """Rebuild top-clipped reward cards from their visible local ×N labels.

    A card must have a readable per-card quantity and enough *visible artwork*.
    Return crop + alignment metadata; matching is performed on the visible part,
    never by stretching the clipped portion to a full-sized portrait.
    """
    W,H=image.size
    quantities=_find_reward_amount_tokens(tokens,image.size)
    valid=[b for b in complete_boxes if (b[3]-b[1]) >= .12*H]
    # The known game layout uses ~118x152px cards at 1608x944; use actual
    # visible complete cards when possible to accommodate scaling.
    card_w=int(np.median([b[2]-b[0] for b in valid])) if valid else round(W*.073)
    card_h=int(np.median([b[3]-b[1] for b in valid])) if valid else round(H*.16)
    list_top=round(H*.214)  # grey reward-list clipping boundary
    list_bottom=round(H*.761)
    result=[]
    for amount,tok in quantities:
        # Cards already handled by the regular extractor are not included.
        if any(b[0]-12 <= tok.cx <= b[2]+12 and b[1] <= tok.cy <= b[3]+18
               for b in complete_boxes):
            continue
        if not (list_top+10 < tok.cy < list_bottom-5):
            continue
        # Quantity is located at the bottom right corner of the card.
        x1=int(round(tok.cx + .20*card_w))
        y1=int(round(tok.cy + .08*card_h))
        x0=x1-card_w; y0=y1-card_h
        if x0 < W*.25 or x1 > W*.73 or y0 >= list_top or y1 <= list_top+25:
            continue
        visible_y0=max(y0,list_top)
        fraction=(y1-visible_y0)/max(1,card_h)
        if fraction < .38 or fraction > .95:
            continue
        # Exclude badge itself from the artwork comparison: the lower fraction
        # is included to align the card but the matcher masks the quantity.
        box=(x0,visible_y0,x1,y1)
        if any(abs(x0-b[0]) < 18 and abs(y1-b[3])<25 for b in complete_boxes):
            continue
        result.append({'amount':int(amount),
                       'crop':image.crop(box).convert('RGB'),
                       'screen_box':box,'x':(x0+x1)/2,'y':(visible_y0+y1)/2,
                       'partial_top_ratio':(visible_y0-y0)/max(1,card_h)})
    return result


def extract_limit_break_icon_crop(image: Image.Image, tokens: list[OCRToken]):
    """Estimate the crystal card crop on the game's limit-break shortage screen."""
    text = _compact_ocr_text(" ".join(t.text for t in tokens))
    if not ("크리스탈" in text and ("부족" in text or "돌파" in text or "최대lv" in text)):
        return None
    W, H = image.size
    candidates = []
    for token in tokens:
        raw = token.text.strip().replace("х", "x").replace("Х", "X")
        if not re.search(r"(?i)(?:×|x|✕|✖|╳)\s*\d{1,5}", raw):
            continue
        if not (0.20*H <= token.cy <= 0.72*H):
            continue
        if not (0.15*W <= token.cx <= 0.72*W):
            continue
        candidates.append(token)
    if not candidates:
        return None
    # The ×N stock badge sits on the lower-right of the crystal card. The level
    # values in the right panel (40 -> 50) are plain numbers, not ×N badges.
    token = min(candidates, key=lambda t: abs(t.cx-W*0.35) + abs(t.cy-H*0.48)*0.5)
    card_w = max(55, int(W*0.061))
    card_h = max(75, int(H*0.145))
    x0 = int(round(token.cx - card_w*0.50))
    y0 = int(round(token.y1 - card_h*0.78))
    x1, y1 = x0+card_w, y0+card_h
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        return None
    crop = image.crop((x0,y0,x1,y1)).convert("RGB")
    return crop if crop.width >= 45 and crop.height >= 60 else None


def _quick_icon_signature(crop: Image.Image) -> bytes:
    arr = np.asarray(crop.convert("RGB").resize((10, 14), Image.Resampling.BILINEAR), dtype=np.uint8)
    # Coarse quantization smooths small anti-aliasing differences between captures.
    return (arr // 16).astype(np.uint8).tobytes()


class BoardSelector:
    def __init__(self, app):
        self.app = app
        self.start = None
        self.rect = None
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{sw}x{sh}+0+0")
        self.win.configure(bg="#111111", cursor="crosshair")
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.28)
        self.canvas = tk.Canvas(self.win, bg="#111111", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(sw//2, 28, text="퍼즐판 격자 전체의 왼쪽 위부터 오른쪽 아래까지 드래그하세요 (6열 × 5행) · 취소: Esc",
                               fill="white", font=("Segoe UI", 16, "bold"))
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.win.bind("<Escape>", lambda e: self.cancel())
        # This was hidden while the selector was built; show it after handlers
        # are registered so the selection canvas receives mouse events.
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.win.focus_force()

    def on_down(self, event):
        self.start = (event.x_root, event.y_root)
        if self.rect: self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                outline="#00ffff", width=4, fill="", stipple="gray50")

    def on_move(self, event):
        if self.start and self.rect:
            x0, y0 = self.start
            self.canvas.coords(self.rect, x0, y0, event.x_root, event.y_root)

    def on_up(self, event):
        if not self.start: return
        x0, y0 = self.start
        x1, y1 = event.x_root, event.y_root
        left, top = min(x0, x1), min(y0, y1)
        right, bottom = max(x0, x1), max(y0, y1)
        if right - left < 120 or bottom - top < 100:
            messagebox.showwarning("영역이 너무 작아요", "퍼즐 격자 전체를 포함하도록 다시 지정해 주세요.", parent=self.win)
            self.start = None
            if self.rect: self.canvas.delete(self.rect)
            self.rect = None
            return
        self.app.board_rect = (int(left), int(top), int(right - left), int(bottom - top))
        self.win.destroy()
        self.app.on_area_selected()

    def cancel(self):
        self.win.destroy()
        self.app._restore_main_after_board_select()
        self.app.set_status("영역 지정이 취소됐어요.")


class ResourceRegionSelector:
    """Let the user select only the material row on a crystal-shortage screen.

    Required: quantity badges above the icons, crystal artwork, and owned ×N below.
    Card title and shortage sentence are intentionally not required.
    """
    def __init__(self, app):
        self.app = app
        self.start = None
        self.rect = None
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{sw}x{sh}+0+0")
        self.win.configure(bg="#111111", cursor="crosshair")
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.24)
        self.canvas = tk.Canvas(self.win, bg="#111111", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            sw//2, 30,
            text="부족 화면의 크리스탈 재료 줄만 드래그하세요: 위쪽 수량 + 아이콘 + 아래 ×N 포함 (이름/안내문 불필요) · 취소: Esc",
            fill="white", font=("Segoe UI", 15, "bold")
        )
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.win.focus_force()

    def on_down(self, event):
        self.start = (event.x_root, event.y_root)
        self.start_local = (event.x, event.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                outline="#ffcc40", width=4, fill="", stipple="gray50")

    def on_move(self, event):
        if self.start and self.rect:
            # Use canvas-local coordinates for drawing the selection rectangle.
            self.canvas.coords(self.rect, self.start_local[0], self.start_local[1], event.x, event.y)

    def on_up(self, event):
        if not self.start:
            return
        x0, y0 = self.start
        x1, y1 = event.x_root, event.y_root
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1-x0), abs(y1-y0)
        if width < 20 or height < 25:
            messagebox.showwarning(
                "영역이 너무 작아요",
                "빨간 필요 수량, 크리스탈 아이콘, 아래쪽 ×N 보유량이 함께 보이도록 부족 재료 영역을 지정해 주세요.",
                parent=self.win,
            )
            self.start = None
            if self.rect:
                self.canvas.delete(self.rect)
            self.rect = None
            return
        selected = (int(left), int(top), int(width), int(height))
        self.win.destroy()
        self.app.on_resource_area_selected(selected)

    def cancel(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.app.resource_selector_active = False
        self.app.set_status("크리스탈 정보 영역 지정이 취소됐어요.")
        try:
            self.app.toolbar._temporarily_hidden = False
            self.app.toolbar.show_crystal_window()
        except Exception:
            pass


class CardTitleRegionSelector:
    """Select the narrow on-screen strip that shows the card/material name."""
    def __init__(self, app):
        self.app = app
        self.start = None
        self.rect = None
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{sw}x{sh}+0+0")
        self.win.configure(bg="#111111", cursor="crosshair")
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.24)
        self.canvas = tk.Canvas(self.win, bg="#111111", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            sw // 2, 30,
            text="카드 제목 글자 전체와 위아래 여백을 포함해 드래그하세요 · 취소: Esc",
            fill="white", font=("Segoe UI", 15, "bold")
        )
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.win.bind("<Escape>", lambda _e: self.cancel())
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.win.focus_force()

    def on_down(self, event):
        self.start = (event.x_root, event.y_root)
        self.start_local = (event.x, event.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                 outline="#68e1ff", width=4, fill="", stipple="gray50")

    def on_move(self, event):
        if self.start and self.rect:
            self.canvas.coords(self.rect, self.start_local[0], self.start_local[1], event.x, event.y)

    def on_up(self, event):
        if not self.start:
            return
        x0, y0 = self.start
        x1, y1 = event.x_root, event.y_root
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1-x0), abs(y1-y0)
        if width < 80 or height < 16:
            messagebox.showwarning(
                "영역이 너무 작아요",
                "카드 이름이 표시된 제목 문구를 포함하도록 조금 더 넓게 지정해 주세요.",
                parent=self.win,
            )
            self.start = None
            if self.rect:
                self.canvas.delete(self.rect)
            self.rect = None
            return
        selected = (int(left), int(top), int(width), int(height))
        self.win.destroy()
        self.app.on_card_title_area_selected(selected)

    def cancel(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.app.cancel_card_title_area_selection()


@dataclass
class OCRToken:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def height(self) -> float:
        return max(1.0, self.y2 - self.y1)


def _ocr_box_to_bounds(box):
    arr = np.asarray(box, dtype=float)
    if arr.ndim == 2 and arr.shape[1] >= 2:
        return float(arr[:, 0].min()), float(arr[:, 1].min()), float(arr[:, 0].max()), float(arr[:, 1].max())
    if arr.size >= 4:
        vals = arr.reshape(-1)
        return float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
    return 0.0, 0.0, 0.0, 0.0


def rapidocr_result_to_tokens(result) -> list[OCRToken]:
    """Accept both the current RapidOCR result object and the older list format."""
    if isinstance(result, tuple) and result:
        result = result[0]
    tokens: list[OCRToken] = []
    if result is None:
        return tokens
    if hasattr(result, "txts"):
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        boxes = [] if boxes is None else boxes
        texts = [] if texts is None else texts
        scores = [] if scores is None else scores
        for i, text in enumerate(texts):
            try:
                box = boxes[i]
                x1, y1, x2, y2 = _ocr_box_to_bounds(box)
                score = float(scores[i]) if i < len(scores) else 1.0
                tokens.append(OCRToken(str(text), x1, y1, x2, y2, score))
            except Exception:
                continue
        return tokens
    if isinstance(result, (list, tuple)):
        for item in result:
            try:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                box, text = item[0], item[1]
                score = float(item[2]) if len(item) > 2 else 1.0
                x1, y1, x2, y2 = _ocr_box_to_bounds(box)
                tokens.append(OCRToken(str(text), x1, y1, x2, y2, score))
            except Exception:
                continue
    return tokens


OCR_RESOURCE_ALIASES = {
    "crystal": ("크리스탈", "크리스털", "crystal", "クリスタル"),
}
OCR_REQUIRED_CUES = ("필요", "필요량", "필요수", "소모", "소비", "비용", "required", "cost", "consume", "必要", "消費", "費用")
OCR_CURRENT_CUES = ("현재", "보유", "소지", "잔량", "owned", "current", "have", "所持", "保有")
OCR_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d[\d,，]{0,14}\d|(?<![A-Za-z])\d+(?![A-Za-z])")


def _compact_ocr_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower().replace("：", ":")


def _contains_resource(text: str, kind: str) -> bool:
    compact = _compact_ocr_text(text)
    return any(alias in compact for alias in OCR_RESOURCE_ALIASES[kind])


def _number_occurrences(token: OCRToken):
    """Yield (integer value, estimated x-center) for numbers inside one OCR token."""
    raw = token.text
    for match in OCR_NUMBER_RE.finditer(raw):
        digits = re.sub(r"\D", "", match.group(0))
        if not digits or len(digits) > 12:
            continue
        try:
            value = int(digits)
        except ValueError:
            continue
        # Approximate the number's position within the OCR word/line box.
        fraction = ((match.start() + match.end()) / 2.0) / max(1, len(raw))
        nx = token.x1 + (token.x2 - token.x1) * fraction
        yield value, nx


def extract_resource_values(tokens: list[OCRToken]) -> tuple[dict[str, Optional[int]], str]:
    """Extract visible crystal required/current counts from OCR with conservative cues.

    The nearest resource label to the left of a number wins (UI convention: label/icon,
    then value); labels to the right are considered only when no label precedes the number.
    A value is set only when it has enough local text context to associate it safely.
    """
    values: dict[str, Optional[int]] = {
        "crystal_required": None, "crystal_current": None,
    }
    tokens = [t for t in tokens if t.text.strip() and t.score >= 0.15]
    tokens.sort(key=lambda t: (t.cy, t.x1))

    # Group OCR tokens into display rows for the raw-text inspection dialog.
    rows: list[list[OCRToken]] = []
    for token in tokens:
        best_row = None
        best_distance = 1e9
        for row in rows[-4:]:
            row_cy = sum(t.cy for t in row) / len(row)
            tolerance = max(10.0, token.height * 0.65, sum(t.height for t in row) / len(row) * 0.65)
            distance = abs(token.cy - row_cy)
            if distance <= tolerance and distance < best_distance:
                best_row, best_distance = row, distance
        if best_row is None:
            rows.append([token])
        else:
            best_row.append(token)
    rows.sort(key=lambda row: sum(t.cy for t in row) / len(row))

    labels: list[tuple[str, OCRToken]] = []
    for token in tokens:
        raw = token.text.lower()
        for kind, aliases in OCR_RESOURCE_ALIASES.items():
            for alias in aliases:
                pos = raw.find(alias.lower())
                if pos >= 0:
                    # When OCR returns a whole line as one box, narrow the resource
                    # label's box to the word itself instead of using the entire line.
                    width = max(1.0, token.x2 - token.x1)
                    left = token.x1 + width * (pos / max(1, len(raw)))
                    right = token.x1 + width * ((pos + len(alias)) / max(1, len(raw)))
                    labels.append((kind, OCRToken(alias, left, token.y1, right, token.y2, token.score)))
                    break
    for row in rows:
        row_text = " ".join(t.text for t in sorted(row, key=lambda t: t.x1))
        for kind in OCR_RESOURCE_ALIASES:
            # `labels` may contain narrow synthetic boxes for words embedded in a larger
            # OCR token. Check the original row tokens, not object identity of those boxes.
            if _contains_resource(row_text, kind) and not any(_contains_resource(t.text, kind) for t in row):
                labels.append((kind, OCRToken(row_text, min(t.x1 for t in row), min(t.y1 for t in row),
                                              max(t.x2 for t in row), max(t.y2 for t in row), 0.5)))

    number_items: list[tuple[int, float, float, OCRToken]] = []
    for token in tokens:
        for value, xcenter in _number_occurrences(token):
            number_items.append((value, xcenter, token.cy, token))

    candidates: dict[str, list[tuple[float, int]]] = {key: [] for key in values}
    for value, nx, ny, number_token in number_items:
        nearby_labels = []
        for kind, label in labels:
            dy = abs(ny - label.cy)
            dx = abs(nx - label.cx)
            if dy <= max(55.0, label.height * 2.3, number_token.height * 2.3) and dx <= 420.0:
                # In one row, prefer the nearest label before the number. Only fall back
                # to a following label when this number has no plausible preceding label.
                side_penalty = 0 if label.cx <= nx else 600
                nearby_labels.append((side_penalty + dx + dy * 2.5, kind, label))
        if not nearby_labels:
            continue
        nearby_labels.sort(key=lambda item: item[0])
        distance, kind, label = nearby_labels[0]
        # Get immediate reading context around this number. This prevents unrelated
        # 'need/current' captions elsewhere on the screen from changing its meaning.
        num_local = [t for t in tokens if abs(t.cy - ny) <= max(30.0, number_token.height * 1.5)
                     and abs(t.cx - nx) <= 230.0]
        compact = _compact_ocr_text(" ".join(t.text for t in num_local))
        has_required = any(cue in compact for cue in OCR_REQUIRED_CUES)
        has_current = any(cue in compact for cue in OCR_CURRENT_CUES)
        if has_required and has_current:
            # If a single OCR box contains both captions and multiple numbers, bind each
            # number to the latest required/current cue that appears before that number.
            # This is more reliable than nearest-center distance for strings such as
            # A single line can contain both required and current captions, so the closest cue matters.
            # equally far from both captions.
            raw_number_text = number_token.text.lower()
            raw_width = max(1.0, number_token.x2 - number_token.x1)
            number_char_pos = ((nx - number_token.x1) / raw_width) * max(1, len(raw_number_text))
            prior_cues = []
            for cue_list, cue_kind in ((OCR_REQUIRED_CUES, "required"), (OCR_CURRENT_CUES, "current")):
                for cue in cue_list:
                    pos = raw_number_text.find(cue.lower())
                    if pos >= 0 and (pos + len(cue) / 2.0) <= number_char_pos:
                        prior_cues.append((pos + len(cue) / 2.0, cue_kind))
            if prior_cues:
                latest_kind = max(prior_cues, key=lambda item: item[0])[1]
                has_required = latest_kind == "required"
                has_current = latest_kind == "current"
            else:
                # If cue words were recognized in separate boxes, associate the number
                # with the closest caption on the same reading row.
                def cue_distance(cues):
                    distances = []
                    for item in num_local:
                        raw = item.text.lower()
                        for cue in cues:
                            search = cue.lower()
                            pos = raw.find(search)
                            if pos >= 0:
                                phrase_x = item.x1 + ((pos + len(search) / 2.0) / max(1, len(raw))) * (item.x2 - item.x1)
                                distances.append(abs(phrase_x - nx))
                            elif search in _compact_ocr_text(raw):
                                distances.append(abs(item.cx - nx))
                    return min(distances, default=9999)
                req_distance = cue_distance(OCR_REQUIRED_CUES)
                cur_distance = cue_distance(OCR_CURRENT_CUES)
                has_required = req_distance <= cur_distance
                has_current = cur_distance < req_distance
        # Avoid treating arbitrary numbers on a level-up screen as the current balance.
        # Only explicit local captions (required/consume or current/owned) assign a role.
        if has_required and not has_current:
            target = f"{kind}_required"
            candidates[target].append((distance, value))
        elif has_current and not has_required:
            target = f"{kind}_current"
            candidates[target].append((distance, value))

    for key, found in candidates.items():
        if found:
            # The first, closest numeric label is the strongest association.
            found.sort(key=lambda item: item[0])
            values[key] = found[0][1]

    # Limit-break confirmation used in Puzzle & Dragons 0 has a specific layout:
    # the plain number in the small badge ABOVE the crystal card is the required
    # amount, while the marked number (e.g. "×4") near the bottom OF the card is
    # the currently owned count. The nearby 40 -> 50 level values belong to the
    # character level panel and must never be used for these counts.
    all_text = " ".join(t.text for t in tokens)
    compact_all = _compact_ocr_text(all_text)
    shortage_cues = ("부족", "모자라", "足りません", "不足", "not enough", "insufficient")
    is_crystal_shortage = _contains_resource(all_text, "crystal") and any(
        cue in compact_all for cue in shortage_cues
    )
    if is_crystal_shortage:
        marked_entries: list[tuple[int, float, float, OCRToken]] = []
        for token in tokens:
            raw = token.text
            for match in re.finditer(r"(?i)[x×✕*]\s*(\d{1,3})", raw):
                try:
                    marked_value = int(match.group(1))
                except ValueError:
                    continue
                # Locate the number's center within this OCR token.
                raw_width = max(1.0, token.x2 - token.x1)
                fraction = ((match.start(1) + match.end(1)) / 2.0) / max(1, len(raw))
                marked_x = token.x1 + raw_width * fraction
                marked_entries.append((marked_value, marked_x, token.cy, token))

        if marked_entries:
            # User-confirmed UI meaning: ×N beneath/on the material card is the
            # amount owned, not the amount required.
            marked_entries.sort(key=lambda item: (item[3].y1, item[3].x1))
            owned_value, owned_x, owned_y, marked_token = marked_entries[0]
            values["crystal_current"] = owned_value

            # Required amount is a standalone number above the same card badge.
            # Find a plain number above ×N, horizontally aligned with that card.
            # This spatial rule intentionally ignores remote level numbers such
            # as 40 and 50 on the right-hand side of the confirmation dialog.
            required_candidates = []
            for value, nx, ny, number_token in number_items:
                if re.search(r"(?i)[x×✕*]\s*\d", number_token.text):
                    continue
                vertical_gap = owned_y - ny
                horizontal_gap = abs(owned_x - nx)
                if 18.0 <= vertical_gap <= 230.0 and horizontal_gap <= 105.0:
                    score = horizontal_gap + vertical_gap * 0.18
                    required_candidates.append((score, value, number_token))
            if required_candidates:
                required_candidates.sort(key=lambda item: item[0])
                values["crystal_required"] = required_candidates[0][1]

    debug_lines = []
    for row in rows:
        row.sort(key=lambda t: t.x1)
        debug_lines.append("  ".join(f"{t.text} [{t.score:.2f}]" for t in row))
    return values, "\n".join(debug_lines)


def extract_card_name(tokens: list[OCRToken], image_size: tuple[int, int]) -> Optional[str]:
    """Read the centered modal title as the character/card name when possible."""
    width, height = image_size
    generic = (
        "생성확인", "확인", "크리스탈", "부족", "필요", "한계돌파", "초절", "돌파",
        "강화", "각성", "보유", "생성하기", "생성에필요한", "초절한계돌파확인",
        "크리스탈획득", "결과", "메뉴", "다음", "취소", "닫기",
    )
    candidates = []
    for token in tokens:
        raw = re.sub(r"\s+", " ", token.text).strip(" :：-—")
        compact = _compact_ocr_text(raw)
        if not raw or token.score < 0.18:
            continue
        # The card name is displayed in the centered white title strip near the top
        # of the creation/limit-break dialog.
        if token.cy > height * 0.23 or not (width * 0.27 <= token.cx <= width * 0.74):
            continue
        if any(term in compact for term in generic):
            # Some versions display a longer header such as "...의 크리스탈".
            if "의크리스탈" in compact:
                before = re.split(r"의\s*크리스탈|의크리스탈", raw, maxsplit=1)[0].strip()
                before_compact = _compact_ocr_text(before)
                if before and not any(term in before_compact for term in generic):
                    candidates.append((abs(token.cx-width*0.5) + token.cy*0.35, before, token.score))
            continue
        if len(compact) > 24:
            continue
        candidates.append((abs(token.cx-width*0.5) + token.cy*0.35, raw, token.score))
    if not candidates:
        return None
    # Prefer a compact, centered title and then a higher OCR confidence.
    candidates.sort(key=lambda item: (item[0], -item[2], len(item[1])))
    result = candidates[0][1].strip()
    return result[:60] if result else None


class Overlay:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.screen_w, self.screen_h = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
        self.win.configure(bg="#ff00ff")
        self.win.attributes("-topmost", True)
        try: self.win.attributes("-transparentcolor", "#ff00ff")
        except tk.TclError: pass
        self.canvas = tk.Canvas(self.win, bg="#ff00ff", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.update_idletasks()
        # Apply once while hidden and again every time it is shown; Windows/Tk
        # may recreate or restyle the native toplevel on map/unmap.
        self.clickthrough_ok = set_clickthrough(self.win)
        self.visible = False
        self.route_drawings_visible = False
        self.hud_visible = False
        self.hud_status = "대기 중 · F6으로 경로 계산"
        self.hud_resources = "크리스탈 필요 ? / 보유 ?   ·   F7로 재료 OCR"
        self.hud_anchor_rect = None
        self._last_hud_draw = None
        self._last_route_signature = None
        self.hide()

    def _draw_hud(self):
        if not self.win.winfo_exists():
            return
        signature = (self.hud_status, self.hud_resources, self.hud_visible,
                     self.hud_anchor_rect)
        # Avoid needless redraws (notably during the playback countdown).
        if signature == self._last_hud_draw:
            return
        self._last_hud_draw = signature
        self.canvas.delete("hud")
        if not self.hud_visible:
            return
        rect = self.hud_anchor_rect or self.app.board_rect
        if rect:
            bx, by, bw, bh = rect
            bar_w = min(max(420, int(bw)), max(300, self.screen_w - 24))
            center_x = max(bar_w / 2 + 12, min(self.screen_w - bar_w / 2 - 12, bx + bw / 2))
            # When the board has enough headroom, the HUD sits above it, like a game status ribbon.
            if by >= 82:
                y0 = by - 76
            elif self.screen_h - (by + bh) >= 82:
                y0 = by + bh + 5
            else:
                y0 = 5
        else:
            bar_w = min(720, self.screen_w - 24)
            center_x = self.screen_w / 2
            y0 = 8
        bar_h = 68
        x0 = int(max(6, min(self.screen_w - bar_w - 6, center_x - bar_w / 2)))
        x1 = int(x0 + bar_w)
        y0 = int(max(4, min(self.screen_h - bar_h - 4, y0)))
        y1 = y0 + bar_h
        self.canvas.create_rectangle(x0, y0, x1, y1, fill="#17212d", outline="#8bb9d9",
                                     width=2, tags=("hud",))
        self.canvas.create_text((x0+x1)//2, y0+13, text="",
                                fill="#eaf4ff", font=("Segoe UI", 9, "bold"), tags=("hud",))
        status = self.hud_status.replace("\n", " ")
        if len(status) > 92:
            status = status[:89] + "..."
        self.canvas.create_text(x0+12, y0+34, anchor="w", text=status,
                                fill="#ffe08a", font=("Segoe UI", 10, "bold"), tags=("hud",))
        resources = self.hud_resources.replace("\n", " ")
        if len(resources) > 92:
            resources = resources[:89] + "..."
        self.canvas.create_text(x0+12, y0+53, anchor="w", text=resources,
                                fill="#c4f3e0", font=("Segoe UI", 9), tags=("hud",))

    def update_hud(self, status=None, resources=None, anchor_rect=None):
        if status is not None:
            self.hud_status = str(status)
        if resources is not None:
            self.hud_resources = str(resources)
        if anchor_rect is not None:
            self.hud_anchor_rect = tuple(int(v) for v in anchor_rect)
        self._draw_hud()
        try:
            self.win.update_idletasks()
        except tk.TclError:
            pass
        if hasattr(self.app, "toolbar"):
            self.app.toolbar.refresh()

    def set_hud_visible(self, visible: bool):
        self.hud_visible = bool(visible)
        self._last_hud_draw = None
        self._draw_hud()

    def show_hud(self):
        # The full-screen transparent layer is only for route strokes.
        # It is kept withdrawn until there is a route to display, so it does
        # not occupy the topmost z-order when the user is not using the overlay.
        self.hud_visible = False
        self._last_hud_draw = None
        self._draw_hud()
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.clickthrough_ok = set_clickthrough(self.win)
        self.visible = True
        if hasattr(self.app, "toolbar"):
            self.app.root.after(30, self.app.toolbar.raise_to_top)

    def show_route(self, rect, path, spikes=None):
        # Countdown/safety rechecks must not erase and redraw identical arrows.
        if not rect or not path:
            self.hide(); return
        signature = (tuple(rect), tuple(path), tuple(spikes or []))
        if self.visible and self.route_drawings_visible and signature == self._last_route_signature:
            return
        self._last_route_signature = signature
        self.canvas.delete("all")
        x, y, w, h = rect
        cw, ch = w / COLS, h / ROWS
        pts = [(x + (idx % COLS + 0.5) * cw, y + (idx // COLS + 0.5) * ch) for idx in path]

        # Give every straight run its own color, and change color at turns.
        # If the route enters an edge that was already used, force a new color
        # there and keep that color along the following straight run. Repeated
        # edge traversals are also shifted into parallel lanes so both directions
        # remain visible instead of drawing on top of one another.
        edge_counts: dict[tuple[int, int], int] = {}
        lane_order = [0, 1, -1, 2, -2, 3, -3, 4, -4]
        lane_gap = max(5.0, min(9.0, min(cw, ch) * 0.085))
        segment_colors = [
            "#00eaff", "#ffd447", "#ff65c9", "#8dff69", "#ff965e",
            "#72a7ff", "#ff4646", "#d8ff38", "#bd8cff", "#ffffff",
        ]
        run_index = 0
        previous_direction = None
        node_gap = min(cw, ch) * 0.16
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]; x1, y1 = pts[i + 1]
            cur, nxt = path[i], path[i + 1]
            edge = (min(cur, nxt), max(cur, nxt))
            occurrence = edge_counts.get(edge, 0)
            edge_counts[edge] = occurrence + 1
            direction = (nxt // COLS - cur // COLS, nxt % COLS - cur % COLS)
            if i > 0 and (direction != previous_direction or occurrence > 0):
                run_index += 1
            previous_direction = direction

            lane = lane_order[occurrence % len(lane_order)]
            dx, dy = x1 - x0, y1 - y0
            length = max(1.0, math.hypot(dx, dy))
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux
            # Orient the offset consistently for the same undirected edge.
            if (cur, nxt) != edge:
                lane = -lane
            off_x, off_y = nx * lane * lane_gap, ny * lane * lane_gap
            # Leave a small gap around each orb center so turning arrows and
            # the numbered visit badges do not collapse into one dark knot.
            pad = min(node_gap, length * 0.24)
            sx0 = x0 + ux * pad + off_x
            sy0 = y0 + uy * pad + off_y
            sx1 = x1 - ux * pad + off_x
            sy1 = y1 - uy * pad + off_y
            color = segment_colors[run_index % len(segment_colors)]
            # Draw a dark outline first for contrast against bright orb artwork.
            self.canvas.create_line(sx0, sy0, sx1, sy1, fill="#101820", width=9,
                                    capstyle=tk.ROUND, tags=("route",))
            self.canvas.create_line(sx0, sy0, sx1, sy1, fill=color, width=4,
                                    arrow=tk.LAST, arrowshape=(13, 15, 6),
                                    capstyle=tk.ROUND, tags=("route",))
            # A tiny colored node at each turn marks the point where the route
            # changes direction/color. It is behind the numbered badge visually.
            is_turn = i < len(pts) - 2 and direction != (
                path[i + 2] // COLS - nxt // COLS,
                path[i + 2] % COLS - nxt % COLS,
            )
            if is_turn:
                rr = max(3, min(5, int(min(cw, ch) * 0.055)))
                self.canvas.create_oval(x1-rr, y1-rr, x1+rr, y1+rr,
                                        fill=color, outline="#101820", width=1,
                                        tags=("route",))

        # Thorned cells are outlined in red so users can distinguish orb colour from hazard.
        spike_flags = list(spikes or [False] * (ROWS * COLS))
        for idx, is_spike in enumerate(spike_flags[:ROWS * COLS]):
            if not is_spike:
                continue
            rr = min(cw, ch) * 0.43
            px = x + (idx % COLS + 0.5) * cw
            py = y + (idx // COLS + 0.5) * ch
            self.canvas.create_oval(px-rr, py-rr, px+rr, py+rr,
                                    outline="#ff3030", width=3, dash=(5, 3), tags=("route",))

        # Number badges mark the exact sequence; revisits to a cell are placed
        # around a 12-position ring rather than all being stacked together.
        visits: dict[int, int] = {}
        # A 4x4 badge grid gives up to 16 distinct visit markers per cell.
        # The markers are deliberately small enough not to cover neighbouring cells.
        offsets = [-0.32, -0.11, 0.11, 0.32]
        badge_slots = [(ox, oy) for oy in offsets for ox in offsets]
        for i, (px, py) in enumerate(pts):
            cell = path[i]
            visit_num = visits.get(cell, 0)
            visits[cell] = visit_num + 1
            if i == 0:
                label, fill, fg = "S", "#31dc82", "#102016"
            elif i == len(pts) - 1:
                label, fill, fg = "E", "#ff6b58", "#26100d"
            else:
                label, fill, fg = str(i), ("#fff1a6" if visit_num == 0 else "#ffb8ec"), "#171717"
            ox, oy = badge_slots[visit_num % len(badge_slots)]
            tx, ty = px + cw * ox, py + ch * oy
            rad = max(8, min(9, int(min(cw, ch) * 0.10)))
            self.canvas.create_oval(tx-rad, ty-rad, tx+rad, ty+rad,
                                    fill=fill, outline="#111111", width=2, tags=("route",))
            font_size = 7 if len(label) > 1 else 8
            self.canvas.create_text(tx, ty, text=label, fill=fg,
                                    font=("Segoe UI", font_size, "bold"), tags=("route",))

        try:
            self.win.attributes("-transparentcolor", "#ff00ff")
        except tk.TclError:
            pass
        self.hud_anchor_rect = tuple(int(v) for v in rect)
        self._last_hud_draw = None
        self._draw_hud()
        self.canvas.itemconfigure("route", state=tk.NORMAL)
        self.win.deiconify()
        self.win.update_idletasks()
        self.clickthrough_ok = set_clickthrough(self.win)
        self.visible = True
        self.route_drawings_visible = True
        if hasattr(self.app, "toolbar"):
            self.app.root.after(30, self.app.toolbar.raise_to_top)

    def set_route_drawings_visible(self, visible: bool):
        """Hide/show route canvas items without withdrawing the whole window."""
        if not self.win.winfo_exists():
            return
        try:
            self.canvas.itemconfigure("route", state=(tk.NORMAL if visible else tk.HIDDEN))
            self.route_drawings_visible = bool(visible)
            self.win.update_idletasks()
        except tk.TclError:
            pass

    def hide(self):
        # This is called in the F6 playback lifecycle. Never rebuild the
        # crystal candidate list from this window-management operation.
        self.win.withdraw()
        self.visible = False

    def toggle(self):
        """Toggle route drawing visibility; do not map an empty topmost window."""
        if self.visible:
            self.hide()
        elif self.app.last_path and self.app.board_rect:
            self.show_route(self.app.board_rect, self.app.last_path, getattr(self.app, "spikes", None))
        else:
            self.app.set_status("표시할 경로가 없습니다. 먼저 F6 키를 눌러 주세요.")


def path_hud_geometry(game_rect,board_rect=None,preferred=None,width=230,height=76,screen=(1920,1080)):
    gx,gy,gw,gh=map(int,game_rect)
    edge=gx+round(gw*.55)
    if isinstance(board_rect,(tuple,list)) and len(board_rect)==4:edge=min(edge,int(board_rect[0]))
    x,y=preferred if preferred is not None else (edge-width-16,gy+round(gh*.22))
    x=max(0,min(int(x),edge-width-12,screen[0]-width))
    y=max(0,min(int(y),screen[1]-height))
    return x,y,width,height


class FloatingPathExecuteButton:
    """A draggable path-execution button; the crystal panel is opened from the main app."""
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg="#10202d")
        self.win.attributes("-topmost", True)  # draggable execute button stays reachable over the game
        self._drag_origin = None
        self._temporarily_hidden = False
        self._user_hidden = False  # Show the inventory indicator before the first F6; F9 can hide it.
        self._capture_panel_was_visible = False
        self._capture_button_was_visible = False
        self._playback_button_was_visible = False
        self._playback_panel_was_visible = False
        self.crystal_window = None
        self.farming_strip = None
        self.farming_strip_label = None
        self._strip_drag_origin = None
        self._capture_strip_was_visible = False
        self.required_label = None
        self.current_label = None
        self.shortage_label = None
        self.pin_button = None

        outer = tk.Frame(self.win, bg="#172432", bd=1, relief="ridge",
                         highlightbackground="#6ba6ca", highlightthickness=1)
        outer.pack(fill="both", expand=True)
        # A dedicated left-button drag grip is more reliable than dragging the
        # whole small button with the right mouse button. The execution button
        # remains a normal left-click action; right-drag remains as a fallback.
        self.grip = tk.Label(
            outer, text="⠿", bg="#172432", fg="#a9c7da",
            font=("Segoe UI Symbol", 11, "bold"), padx=3, pady=3,
            cursor="fleur", takefocus=False,
        )
        self.grip.pack(side="left", fill="y")
        self.grip.bind("<ButtonPress-1>", self._start_drag)
        self.grip.bind("<B1-Motion>", self._drag)
        self.grip.bind("<ButtonRelease-1>", self._end_drag)

        self.path_body=tk.Frame(outer,bg="#172432")
        self.path_body.pack(side="left",fill="both",expand=True)
        self.button = tk.Button(
            self.path_body, text="▶ 경로 계산", command=self.app.trigger_route,
            bg="#203d52", fg="#ffffff", activebackground="#315875",
            activeforeground="#ffffff", relief="flat", bd=0,
            font=("Segoe UI Emoji", 10, "bold"), padx=6, pady=5,wraplength=185,
            cursor="hand2", takefocus=False,
        )
        self.button.pack(fill="x")
        self.button.bind("<ButtonPress-1>", lambda _event: setattr(self.app, '_path_button_pointer_down', True), add='+')
        self.button.bind("<ButtonRelease-1>", lambda _event: setattr(self.app, '_path_button_pointer_down', False), add='+')
        self.attr_state_var = tk.StringVar(value="속성 확인 전")
        self.attr_state_label = tk.Label(self.path_body, textvariable=self.attr_state_var,
            bg="#172432", fg="#f8d67b", font=("Malgun Gothic", 9), padx=7, pady=7,
            anchor="w",justify="left",wraplength=185)
        self.attr_state_label.pack(fill="x")
        self.refresh_attribute_state()
        self.button.bind("<ButtonPress-3>", self._start_drag)
        self.button.bind("<B3-Motion>", self._drag)
        self.button.bind("<ButtonRelease-3>", self._end_drag)

        self.win.update_idletasks()
        screen_w, screen_h = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        saved_xy=self.app.user_settings.get('path_execute_button_xy')
        moved=bool(self.app.user_settings.get('path_button_user_moved',False))
        preferred=tuple(saved_xy) if moved and isinstance(saved_xy,list) and len(saved_xy)==2 else None
        info=find_emulator_window_info()
        game=info['rect'] if info else (0,0,screen_w,screen_h)
        width,height=230,max(38,self.win.winfo_reqheight())
        x,y,width,height=path_hud_geometry(game,getattr(self.app,'board_rect',None),preferred,width,height,(screen_w,screen_h))
        self.win.geometry(f'{width}x{height}+{x}+{y}')
        self.app.user_settings['path_button_position_version']=169
        # The foreground visibility tick shows it after construction.
        self.win.withdraw()
        self.win.attributes("-topmost", True)
        self.win.lift()

    def _start_drag(self, event):
        self._drag_origin = {
            "sx": event.x_root,
            "sy": event.y_root,
            "wx": self.win.winfo_x(),
            "wy": self.win.winfo_y(),
            "moved": False,
            "widget": getattr(event, "widget", None),
        }
        # Never call grab_set() on this topmost floating window. If it becomes
        # withdrawn during F6, an outstanding Tk grab can starve main-window input.

    def _drag(self, event):
        if not self._drag_origin:
            return
        origin = self._drag_origin
        dx, dy = event.x_root - origin["sx"], event.y_root - origin["sy"]
        # Ignore hand jitter until the pointer has moved a few pixels.
        if not origin["moved"] and abs(dx) <= 2 and abs(dy) <= 2:
            return
        origin["moved"] = True
        x, y = origin["wx"] + dx, origin["wy"] + dy
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x = max(0, min(sw - self.win.winfo_width(), x))
        y = max(0, min(sh - self.win.winfo_height(), y))
        self.win.geometry(f"+{x}+{y}")

    def _end_drag(self, event=None):
        origin = self._drag_origin
        self._drag_origin = None
        if origin:
            try:
                widget = origin.get("widget")
                if widget is not None and str(widget.grab_current()) == str(widget):
                    widget.grab_release()
            except (AttributeError, tk.TclError):
                pass
        # Preserve manual placement independently of the farming strip toggle.
        if origin and origin.get("moved"):
            self._fit_attribute_indicator()
            self.win.update_idletasks()
            self.app.user_settings["path_button_user_moved"] = True
            info = find_emulator_window_info()
            if info:
                gx, gy, gw, gh = (int(v) for v in info["rect"])
                self.app.user_settings["path_execute_button_relative"] = [
                    round((self.win.winfo_x() - gx) / max(1, gw), 6),
                    round((self.win.winfo_y() - gy) / max(1, gh), 6)]
        self.app.save_user_settings()

    def refresh_attribute_state(self):
        """Only show the five-color inventory widget when that goal is selected."""
        try:
            mode=int(self.app.goal_var.get()) if hasattr(self.app, 'goal_var') else int(self.app.user_settings.get('goal_index',0))
            if mode not in (2,9) and not isinstance(getattr(self.app,"_route_result_notice",None),dict):
                self.attr_state_label.pack_forget()
                self._fit_attribute_indicator()
                return
            if not self.attr_state_label.winfo_manager():
                self.attr_state_label.pack(fill="x")
            text, color = self.app._attribute_status
            self.attr_state_var.set(text)
            self.attr_state_label.configure(fg=color)
            self._fit_attribute_indicator()
        except (tk.TclError, ValueError):
            pass

    def _fit_attribute_indicator(self):
        if getattr(self.app,'toolbar',None) is not self or self._drag_origin is not None:return
        self.win.update_idletasks()
        width,height=230,max(38,self.win.winfo_reqheight())
        info=find_emulator_window_info();sw,sh=self.win.winfo_screenwidth(),self.win.winfo_screenheight()
        game=info['rect'] if info else (0,0,sw,sh)
        x,y,width,height=path_hud_geometry(game,getattr(self.app,'board_rect',None),
            (self.win.winfo_x(),self.win.winfo_y()),width,height,(sw,sh))
        self.win.geometry(f'{width}x{height}+{x}+{y}')

    def _position_path_button_beside_strip(self):
        """Keep the compact two-row control on the left of the puzzle column."""
        if self._drag_origin is not None:return
        try:
            info=find_emulator_window_info()
            if not info:return
            gx,gy,gw,gh=map(int,info['rect'])
            self.win.update_idletasks()
            relative=self.app.user_settings.get('path_execute_button_relative')
            preferred=None
            if self.app.user_settings.get('path_button_user_moved') and isinstance(relative,list) and len(relative)==2:
                preferred=(gx+int(gw*float(relative[0])),gy+int(gh*float(relative[1])))
            x,y,width,height=path_hud_geometry((gx,gy,gw,gh),getattr(self.app,'board_rect',None),preferred,
                230,max(38,self.win.winfo_reqheight()),(self.win.winfo_screenwidth(),self.win.winfo_screenheight()))
            self.win.geometry(f'{width}x{height}+{x}+{y}')
        except (tk.TclError,ValueError,TypeError):pass

    def show_crystal_window(self):
        """Ensure the crystal information window is visible; do not toggle it off."""
        self._create_crystal_window()
        # F7 and the main-window command must open the management panel even
        # when the independent 12-o'clock farming HUD is enabled.
        try:
            if self.crystal_window and self.crystal_window.winfo_exists():
                self.crystal_window.deiconify()
                self.crystal_window.attributes("-topmost", bool(self.app.crystal_panel_pin_var.get()))
                self.crystal_window.lift()
                self.refresh()
                self.app._fit_material_panel()
                self._present_crystal_window()
        except tk.TclError:
            pass

    def toggle_crystal_window(self):
        if self.crystal_window is not None:
            try:
                if self.crystal_window.winfo_exists() and self.crystal_window.state() != "withdrawn":
                    self._save_crystal_window_xy()
                    self._end_crystal_presentation()
                    self.crystal_window.withdraw()
                    return
            except tk.TclError:
                self.crystal_window = None
        # Opening from the main-window crystal button means management mode:
        # clear even a previously persisted/stale 12-o'clock HUD preference.
        self.app.toolbar_topmost_var.set(False)
        try:
            if self.farming_strip is not None and self.farming_strip.winfo_exists():
                self.farming_strip.withdraw()
        except tk.TclError:
            pass
        self._create_crystal_window()
        try:
            self.crystal_window.deiconify()
            self.crystal_window.attributes("-topmost", bool(self.app.crystal_panel_pin_var.get()))
            self.crystal_window.lift()
            self.refresh()
            self.app._fit_material_panel()
            self._present_crystal_window()
        except tk.TclError:
            pass
        self.app.root.after_idle(self.app.save_user_settings)

    def _present_crystal_window(self):
        """Explicit F7/open: present above a foreground emulator, without pinning."""
        panel=self.crystal_window
        self._crystal_presentation_active=True
        panel.update_idletasks()
        panel.attributes('-topmost',True)
        panel.lift()
        panel.focus_force()

    def _end_crystal_presentation(self,clicked_owner=None):
        if not getattr(self,'_crystal_presentation_active',False): return
        panel=getattr(self,'crystal_window',None)
        if panel is None or not panel.winfo_exists():
            self._crystal_presentation_active=False
            return
        if clicked_owner is not None:
            own=int(panel.winfo_id())
            if sys.platform=='win32':
                from ctypes import wintypes
                api=ctypes.windll.user32
                api.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT)
                api.GetAncestor.restype=wintypes.HWND
                own=int(api.GetAncestor(own,2) or own)
            if own==clicked_owner: return
        self._crystal_presentation_active=False
        panel.attributes('-topmost',bool(self.app.crystal_panel_pin_var.get()))

    def _crystal_focus_out(self,event=None):
        def check():
            panel=getattr(self,'crystal_window',None)
            if panel is None or not panel.winfo_exists(): return
            focus=panel.focus_displayof()
            if focus is None or focus.winfo_toplevel()!=panel:
                self._end_crystal_presentation()
        self.app.root.after_idle(check)

    def minimize_crystal_window(self):
        """Background name reading keeps the management window minimised."""
        self._end_crystal_presentation()
        panel=getattr(self,'crystal_window',None)
        if panel is not None and panel.winfo_exists() and panel.state()=='normal':
            self._save_crystal_window_xy()
            panel.iconify()

    def _create_crystal_window(self):
        if self.crystal_window is not None:
            try:
                if self.crystal_window.winfo_exists():
                    return
            except tk.TclError:
                pass
        win = tk.Toplevel(self.app.root)
        self.crystal_window = win
        self._crystal_presentation_active=False
        win.title("퍼드0 크리스탈")
        win.bind("<FocusOut>",self._crystal_focus_out,add="+")
        win.configure(bg="#f1f4f7")
        win.resizable(True, True)
        win.attributes("-topmost", bool(self.app.crystal_panel_pin_var.get()))
        # The panel can grow when the card title/list changes. Put all content in
        # a scrollable canvas so the buttons below the candidate list never vanish
        # beyond the bottom of a small display.
        scroll_shell = tk.Frame(win, bg="#f1f4f7")
        scroll_shell.pack(fill="both", expand=True)
        crystal_canvas = tk.Canvas(scroll_shell, bg="#f1f4f7", highlightthickness=0, bd=0)
        crystal_scrollbar = ttk.Scrollbar(scroll_shell, orient="vertical", command=crystal_canvas.yview)
        crystal_canvas.configure(yscrollcommand=crystal_scrollbar.set)
        crystal_canvas.pack(side="left", fill="both", expand=True)
        crystal_scrollbar.pack(side="right", fill="y")
        outer = tk.Frame(crystal_canvas, bg="#f1f4f7", padx=14, pady=12)
        self._crystal_content_frame=outer
        outer_window_id = crystal_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _e: crystal_canvas.configure(scrollregion=crystal_canvas.bbox("all")))
        crystal_canvas.bind("<Configure>", lambda e: crystal_canvas.itemconfigure(outer_window_id, width=e.width))
        self.crystal_canvas = crystal_canvas
        header = tk.Frame(outer, bg="#f1f4f7")
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="💎 크리스탈 보유량", bg="#f1f4f7", fg="#1c2f3e",
                 font=("Segoe UI", 13, "bold")).pack(side="left", anchor="w")
        self.breakthrough_label = tk.Label(
            header, text="", bg="#f1f4f7", fg="#168451",
            font=("Malgun Gothic", 11, "bold"))
        self.breakthrough_label.pack(side="left", padx=(24, 0))
        self.pin_button = tk.Button(
            header, text=("📌 관리창 고정: 켬" if self.app.crystal_panel_pin_var.get() else "📌 관리창 고정: 꺼짐"), command=self.app.toggle_crystal_panel_pin,
            bg="#50697c", fg="white", activebackground="#67879c",
            activeforeground="white", relief="flat", bd=0,
            font=("Segoe UI Emoji", 9, "bold"), padx=8, pady=4, cursor="hand2"
        )
        self.pin_button.pack(side="right", padx=(10, 0))
        tk.Button(header, text="위치 초기화", command=self.reset_crystal_window_position,
                  bg="#50697c", fg="white", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), padx=7, pady=4).pack(side="right", padx=(4, 0))
        self.farming_button = None  # Result statistics replace the battle HUD.
        owner_row=tk.Frame(outer,bg="#f1f4f7",height=54)
        owner_row.pack(fill="x",pady=(0,8))
        owner_row.pack_propagate(False)
        self.card_name_label = tk.Label(
            owner_row, text=f"대상 카드: {self.app.card_name_detected or '이름 확인 필요'}",
            bg="#f1f4f7", fg="#243f56", font=("Segoe UI", 12, "bold"), anchor="w", compound="left"
        )
        self.card_name_label.pack(fill="both",expand=True)
        self._owner_photo_signature=None
        self._owner_photo=None
        self.read_stamp_label = tk.Label(outer, text="돌파 확인 화면에서 F7을 누르면 이름과 전체 재료를 등록합니다.",
                                         bg="#f1f4f7", fg="#657988", font=("Segoe UI", 8), anchor="w",
                                         wraplength=470, justify="left")
        self.read_stamp_label.pack(fill="x", pady=(0, 8))
        # All deficient red-marked materials are shown here; the user can choose
        # which single material to track for the currently selected dungeon.
        candidate_box = tk.LabelFrame(outer, text="전체 재료 · 필요/보유/부족 수량", bg="#f1f4f7", fg="#334b5b")
        candidate_box.pack(fill="x", pady=(0, 8))
        tree_frame = tk.Frame(candidate_box, bg="#f1f4f7")
        tree_frame.pack(fill="x", padx=4, pady=4)
        columns = ("required", "current", "deficit")
        # Each row contains a card portrait. Default Treeview rows (and the DB's
        # unrelated styling) are too short and make adjacent thumbnails overlap.
        candidate_style = ttk.Style(win)
        candidate_style.configure("CrystalCandidate.Treeview", rowheight=64, font=("Segoe UI", 10),
                                  background="#ffffff", fieldbackground="#ffffff")
        candidate_style.configure("CrystalCandidate.Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.candidate_tree = ttk.Treeview(
            tree_frame, columns=columns, show="tree headings", height=max(1,min(5,len(self.app.crystal_candidates))),
            selectmode="browse", style="CrystalCandidate.Treeview")
        self.candidate_tree.heading("#0", text="크리스탈 이미지 / 이름")
        self.candidate_tree.column("#0", width=345, minwidth=280, stretch=True, anchor="w")
        for key, heading, width in (("required", "목표", 88), ("current", "보유", 88), ("deficit", "부족", 88)):
            self.candidate_tree.heading(key, text=heading)
            self.candidate_tree.column(key, width=width, minwidth=70, stretch=False, anchor="center")
        candidate_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=candidate_scroll.set)
        self.candidate_tree.pack(side="left", fill="x", expand=True)
        candidate_scroll.pack(side="right", fill="y")
        self._candidate_photo_refs = []
        candidate_actions = tk.Frame(candidate_box, bg='#f1f4f7')
        candidate_actions.pack(fill='x', padx=4, pady=(0, 4))
        tk.Button(candidate_actions, text="필요/보유 수정",
                  command=lambda: self.app.edit_selected_crystal_quantities(from_candidates=True),
                  bg="#50697c", fg="white", relief="flat", font=("Segoe UI", 9, "bold"),
                  padx=8, pady=5).pack(side='left', padx=(5,0))
        rename_row = tk.Frame(outer, bg="#f1f4f7")
        rename_row.pack(fill="x", pady=(0, 6))
        tk.Button(rename_row, text="돌파 이름 먼저 등록 / 수정", command=self.app._edit_farming_group_name,
                  bg="#50697c", fg="white", relief="flat", padx=8, pady=5).pack(side="left")
        actions = tk.Frame(outer, bg="#f1f4f7")
        actions.pack(fill="x")
        tk.Button(actions, text="영역 지정",
                  command=self.app.select_resource_area,
                  bg="#2f6d90", fg="white", activebackground="#3d83a9",
                  activeforeground="white", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), padx=8, pady=6).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(actions, text="다시 읽기 (F7)",
                  command=lambda: self.app.request_resource_ocr(manual=False, fallback_on_failure=True),
                  bg="#287a58", fg="white", activebackground="#35966c",
                  activeforeground="white", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), padx=8, pady=6).pack(side="left", fill="x", expand=True, padx=4)
        tk.Button(actions, text="DB 관리", command=self.app.show_crystal_ledger,
                  bg="#50697c", fg="white", activebackground="#67879c",
                  activeforeground="white", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), padx=8, pady=6).pack(side="left", fill="x", expand=True, padx=4)
        tk.Button(actions, text="OCR 원문", command=self.app.show_ocr_debug,
                  bg="#50697c", fg="white", activebackground="#67879c",
                  activeforeground="white", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), padx=8, pady=6).pack(side="left", fill="x", expand=True, padx=(4, 0))
        screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
        saved_xy = self.app.user_settings.get("crystal_window_xy")
        if isinstance(saved_xy, list) and len(saved_xy) == 2:
            try:
                x, y = int(saved_xy[0]), int(saved_xy[1])
            except (TypeError, ValueError):
                x, y = max(10, screen_w - 770), 72
        else:
            x, y = max(10, screen_w - 770), 72
        win.update_idletasks()
        width = min(max(760, outer.winfo_reqwidth() + 36), max(420, screen_w - 40))
        # Cap the window height below the desktop height and let the inner canvas
        # scroll instead of pushing the name/target/action buttons off-screen.
        height = min(max(320,outer.winfo_reqheight()+35),max(260,screen_h-90))
        x = max(0, min(screen_w - width, x))
        y = max(0, min(screen_h - height, y))
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.protocol("WM_DELETE_WINDOW", self._hide_crystal_window)
        # Dragging is allowed; the chosen position is persisted when the panel closes.
        # Avoid writing the settings JSON on every pixel of a window drag.
        self.refresh()

    def reset_crystal_window_position(self):
        """Restore a usable right-side location; keep pin/HUD settings untouched."""
        self.app.user_settings.pop("crystal_window_xy", None)
        if self.crystal_window is None or not self.crystal_window.winfo_exists():
            return
        w = self.crystal_window.winfo_width()
        h = self.crystal_window.winfo_height()
        sw = self.crystal_window.winfo_screenwidth()
        sh = self.crystal_window.winfo_screenheight()
        x = max(0, min(sw-w, sw-w-20))
        y = max(0, min(sh-h, 72))
        self.crystal_window.geometry(f"+{x}+{y}")
        self._save_crystal_window_xy()

    def _hide_crystal_window(self):
        self._end_crystal_presentation()
        self._save_crystal_window_xy()
        try:
            if self.crystal_window and self.crystal_window.winfo_exists():
                self.crystal_window.withdraw()
        except tk.TclError:
            pass

    def _save_crystal_window_xy(self):
        try:
            if self.crystal_window and self.crystal_window.winfo_exists():
                self.app.user_settings["crystal_window_xy"] = [
                    int(self.crystal_window.winfo_x()), int(self.crystal_window.winfo_y())
                ]
                self.app.save_user_settings()
        except tk.TclError:
            pass

    def refresh(self):
        if not self.win.winfo_exists():
            return
        if self.crystal_window is None or not self.crystal_window.winfo_exists():
            return
        ledger = self.app._load_crystal_ledger()
        group = ledger.get('farming_groups', {}).get(ledger.get('active_farming_group'), {})
        pending = bool(getattr(self.app, '_pending_farming_materials', None))
        name = (self.app.card_name_detected or '이름 확인 필요') if pending else group.get('name', '등록 대기')
        self.card_name_label.configure(text=f'돌파 대상: {name}')
        self.refresh_owner_image(group,name,pending)
        pinned=bool(self.app.crystal_panel_pin_var.get())
        self.pin_button.configure(text=('📌 관리창 고정: 켬' if pinned else '📌 관리창 고정: 꺼짐'),
                                  bg=('#177c71' if pinned else '#50697c'))
        self.crystal_window.attributes('-topmost',pinned or getattr(self,'_crystal_presentation_active',False))
        self.breakthrough_label.configure(text=('모든 재료 충족' if not pending and self.app.all_crystal_requirements_met() else ''))
        self.read_stamp_label.configure(text='F7: 전체 재료 확인 · 게임에서 재료 클릭: 관리창 최소화 후 이름 자동 등록 · 목록은 메인창에서 다시 열 수 있습니다.')
        self.refresh_candidate_list()

    def refresh_owner_image(self,group,name,pending=False):
        """Show only the current owner's saved image, keeping one Tk photo alive."""
        filenames=[]
        if not pending and group.get('name')==name and group.get('image_file'):
            filenames.append(group['image_file'])
        # Older profiles may predate the owner/image link. A unique registered
        # material with the same name can supply its existing image as well.
        if not filenames:
            db=self.app._load_crystal_db()
            filenames=list(dict.fromkeys(row['image_file'] for row in db.get('crystals',[])
                if row.get('image_file') and known_crystal_name(row)
                and crystal_name_key(known_crystal_name(row))==crystal_name_key(name)))
        signature=None;path=None
        if len(filenames)==1:
            path=self.app.data_paths['icons_dir']/filenames[0]
            try:
                stat=path.stat()
                if path.is_file(): signature=(name,str(path),stat.st_mtime_ns,stat.st_size)
            except OSError:
                pass
        if signature==getattr(self,'_owner_photo_signature',None): return
        photo=None
        if signature is not None:
            try:
                from PIL import ImageTk
                with Image.open(path) as source:
                    thumb=fit_image_preserve_aspect(source,(42,54),bg=(241,244,247))
                    photo=ImageTk.PhotoImage(thumb,master=self.card_name_label)
            except (OSError,ValueError,tk.TclError):
                signature=None
        self._owner_photo_signature=signature
        self._owner_photo=photo
        self.card_name_label.configure(image=photo if photo is not None else '',compound='left')

    def refresh_candidate_list(self):
        tree = getattr(self, "candidate_tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
            old_selection = tree.selection()
            for iid in tree.get_children(""):
                tree.delete(iid)
            self._candidate_photo_refs = []
            candidates = list(getattr(self.app, "crystal_candidates", []) or [])
            db = self.app._load_crystal_db()
            entries_by_id = {str(row.get("id")): row for row in db.get("crystals", [])}
            for index, candidate in enumerate(candidates):
                cid = str(candidate.get("id", f"candidate-{index}"))
                entry = entries_by_id.get(cid, {})
                # A saved title scan belongs to the target crystal icon. Keep it on
                # the corresponding row and in the database so it persists across F7.
                label = crystal_display_name(entry,candidate,index+1)
                photo = None
                filename = entry.get("image_file") or candidate.get("image_file")
                if filename:
                    path = self.app.data_paths["icons_dir"] / str(filename)
                    try:
                        if path.exists():
                            from PIL import ImageTk
                            with Image.open(path) as source:
                                thumb = fit_image_preserve_aspect(source, (42, 54), bg=(255, 255, 255))
                                photo = ImageTk.PhotoImage(thumb)
                                self._candidate_photo_refs.append(photo)
                    except Exception:
                        photo = None
                iid = str(index)
                required = int(candidate.get("required", 0))
                current = int(candidate.get("current", 0))
                deficit = max(0, int(candidate.get("deficit", required-current)))
                insert_kwargs = {'image': photo} if photo is not None else {}
                tree.insert("", "end", iid=iid, text=label,
                            values=(required, current, deficit), **insert_kwargs)
            for selected in old_selection:
                if tree.exists(selected): tree.selection_set(selected)
            tree.configure(height=max(1,min(5,len(candidates))))
            if not candidates:
                tree.insert("", "end", iid="empty", text="미등록", values=("?", "?", "?"))
        except tk.TclError:
            pass

    def is_visible(self):
        """True when the floating execute button or crystal panel could contaminate a capture."""
        try:
            button_visible = bool(self.win.winfo_exists() and self.win.state() != "withdrawn"
                                  and not self._temporarily_hidden and not self._user_hidden)
        except tk.TclError:
            button_visible = False
        try:
            panel_visible = bool(self.crystal_window and self.crystal_window.winfo_exists()
                                 and self.crystal_window.state() != "withdrawn")
        except tk.TclError:
            panel_visible = False
        return button_visible or panel_visible

    def hide_for_capture(self):
        self._end_crystal_presentation()
        if not self.win.winfo_exists():
            return
        self._capture_button_was_visible = bool(self.win.state() != "withdrawn" and not self._user_hidden)
        self._temporarily_hidden = True
        self._capture_panel_was_visible = False
        self._capture_strip_was_visible = bool(self.farming_strip is not None and
                                               self.farming_strip.winfo_exists() and
                                               self.farming_strip.state() != "withdrawn")
        if self._capture_strip_was_visible:
            self.farming_strip.withdraw()
        self.win.withdraw()
        try:
            if self.crystal_window and self.crystal_window.winfo_exists() and self.crystal_window.state() == "normal":
                self._capture_panel_was_visible = True
                self.crystal_window.withdraw()
        except tk.TclError:
            pass
        self.app.root.update_idletasks()

    def restore_after_capture(self):
        if not self.win.winfo_exists():
            self._temporarily_hidden = False
            return
        self._temporarily_hidden = False
        if self._capture_button_was_visible and not self._user_hidden and self.app._path_button_may_show():
            self.win.deiconify()
            self.win.attributes("-topmost", True)
            self.win.lift()
        if self._capture_panel_was_visible and not getattr(self.app,'_route_ui_active',False):
            try:
                if self.crystal_window and self.crystal_window.winfo_exists():
                    self.crystal_window.deiconify()
                    self.crystal_window.attributes("-topmost", bool(self.app.crystal_panel_pin_var.get()))
                    if self.app.crystal_panel_pin_var.get():
                        self.crystal_window.lift()
            except tk.TclError:
                pass
        if self._capture_strip_was_visible and self.app.toolbar_topmost_var.get():
            if self.farming_strip is not None and self.farming_strip.winfo_exists():
                self.farming_strip.deiconify()
        self._capture_strip_was_visible = False
        self._capture_panel_was_visible = False
        self._capture_button_was_visible = False

    def release_floating_pointer_capture(self):
        """Safely drop any stale floating-button mouse grab before window changes."""
        self._drag_origin = None
        self.app._path_button_pointer_down = False
        try:
            current = self.win.grab_current()
            if current is not None and (current is self.win or
                    str(current).startswith(str(self.win) + ".")):
                current.grab_release()
        except tk.TclError:
            pass

    def hide_for_playback(self):
        """Temporarily hide our clickable button/panel so the game receives mouse input."""
        self.release_floating_pointer_capture()
        try:
            self._playback_button_was_visible = bool(
                self.win.winfo_exists() and self.win.state() != "withdrawn" and not self._user_hidden
            )
        except tk.TclError:
            self._playback_button_was_visible = False
        self._playback_panel_was_visible = False
        self._playback_strip_was_visible = bool(self.farming_strip is not None and
                                                self.farming_strip.winfo_exists() and
                                                self.farming_strip.state() != "withdrawn")
        self._temporarily_hidden = True
        # A route request is independent of F7. Keep the crystal manager in
        # the background and never create a later "restore to normal" request.
        self.minimize_crystal_window()

    def restore_after_playback(self):
        """Restore the route button only; the crystal manager stays in the background."""
        self.release_floating_pointer_capture()
        self._temporarily_hidden = False
        try:
            if (getattr(self, "_playback_button_was_visible", False) and not self._user_hidden
                    and self.app._route_button_game_in_foreground() and self.app._path_button_may_show()):
                self.win.deiconify()
                self._position_path_button_beside_strip()
                self.win.attributes("-topmost", True)
                self.win.lift()
        except tk.TclError:
            pass
        self._playback_button_was_visible = False
        # Farming HUD was deliberately left on screen during playback.
        # Do not rebuild/re-show it in the mouse-drag completion callback.
        self._playback_strip_was_visible = False
        self._playback_panel_was_visible = False

    def toggle_visibility(self):
        """Toggle the floating path-execution button; the crystal panel is in the main app."""
        if not self.win.winfo_exists():
            return
        if not self._user_hidden:
            self.release_floating_pointer_capture()
            self._user_hidden = True
            self.win.withdraw()
            self.app.set_status("경로 계산 버튼을 숨겼습니다. F9으로 다시 표시할 수 있어요.")
        else:
            self._user_hidden = False
            self._temporarily_hidden = False
            if self.app._route_button_game_in_foreground() and self.app._path_button_may_show():
                self.win.deiconify()
                self._position_path_button_beside_strip()
                self.win.attributes("-topmost", True)
                self.win.lift()
            self.refresh()
            self.app.set_status("퍼즐판을 인식하면 경로 계산 버튼이 표시됩니다.")

    def raise_to_top(self):
        if not self.win.winfo_exists() or self._temporarily_hidden or self._user_hidden:
            return
        # Do not bring a topmost game button over another application.
        if not self.app._route_button_game_in_foreground() or not self.app._path_button_may_show():
            return
        try:
            self.win.deiconify()
            self._position_path_button_beside_strip()
            self.win.attributes("-topmost", True)
            self.win.lift()
        except tk.TclError:
            pass


def breakthrough_button_candidate(image):
    """Locate a breakthrough confirmation layout without running OCR.

    Require the top page ribbon, modal header, pale material panel and 1–5
    complete quantity pills. Detail/reward/battle pages fail these gates.
    """
    small=image.convert('RGB').resize((1602,941),Image.Resampling.BILINEAR)
    rgb=np.asarray(small,dtype=np.int16)
    def white(box,threshold=230):
        x0,y0,x1,y1=box
        patch=rgb[y0:y1,x0:x1]
        return float(np.mean((patch.min(axis=2)>threshold)&(np.ptp(patch,axis=2)<25)))
    if white((230,45,460,96))<.60 or white((170,154,1400,205))<.78:
        return None
    material=small.crop((145,240,880,530))
    panel=np.asarray(material,dtype=np.int16)[23:52,30:698]
    if float(np.mean((panel.mean(axis=2)>170)&(np.ptp(panel,axis=2)<23)))<.65:
        return None
    try:
        locate_material_badges(material)
    except ValueError:
        return None
    # Keep only the page title, not the modal owner, X button or our overlay.
    header=small.crop((160,45,470,103))
    pixels=np.asarray(header,dtype=np.int16)
    mask=(pixels.max(axis=2)<215)&(np.ptp(pixels,axis=2)<65)
    mask[:4]=False;mask[-4:]=False;mask[:,:3]=False;mask[:,-3:]=False
    ys,xs=np.where(mask)
    if not len(xs): return None
    x0,x1,y0,y1=int(xs.min()),int(xs.max()+1),int(ys.min()),int(ys.max()+1)
    if y1-y0<8 or y1-y0>header.height*.75 or x1-x0<45: return None
    # Page titles are left aligned; tight cropping centers their glyphs for
    # the shared two-view recognizer (modal titles are normally centered).
    return header.crop((max(0,x0-6),max(0,y0-6),min(header.width,x1+6),min(header.height,y1+6)))


def lobby_confirmation_title(image):
    """Pixel gate for the party + white cancel + orange OK pre-battle dialog."""
    small=image.convert('RGB').resize((1602,941),Image.Resampling.BILINEAR)
    rgb=np.asarray(small,dtype=np.int16)
    orange=rgb[680:760,840:1170]
    if float(np.mean((orange[...,0]>210)&(orange[...,1]>95)&(orange[...,1]<210)&(orange[...,2]<85)))<.50:
        return None
    cancel=rgb[680:760,440:760]
    if float(np.mean(cancel.min(axis=2)>230))<.65: return None
    ribbon=rgb[45:96,300:600]
    if float(np.mean(ribbon.min(axis=2)>230))<.80: return None
    band=rgb[112:165,440:1150]
    if float(np.mean(band.min(axis=2)>235))<.80: return None
    header=small.crop((160,45,470,103))
    pixels=np.asarray(header,dtype=np.int16)
    mask=(pixels.max(axis=2)<215)&(np.ptp(pixels,axis=2)<65)
    mask[:4]=False;mask[-4:]=False;mask[:,:3]=False;mask[:,-3:]=False
    ys,xs=np.where(mask)
    if not len(xs): return None
    x0,x1,y0,y1=int(xs.min()),int(xs.max()+1),int(ys.min()),int(ys.max()+1)
    if y1-y0<8 or y1-y0>header.height*.75 or x1-x0<35: return None
    return header.crop((max(0,x0-6),max(0,y0-6),min(header.width,x1+6),min(header.height,y1+6)))


def confirmed_breakthrough_heading(readings):
    if len(readings)!=2 or min(score for _,score in readings)<.85:
        return None
    titles=[re.sub(r'[\s·:：]','',str(text)) for text,_ in readings]
    if titles[0]!=titles[1]:
        return None
    return bool(re.fullmatch(r'(?:(?:초절|초|최종)?한계|중간|최종)?돌파확인',titles[0]))


def locate_material_badges(image):
    """Find dark requirement pills regardless of red/white digit color."""
    import cv2
    rgb = np.asarray(image.convert('RGB'), dtype=np.int16)
    height, width = rgb.shape[:2]
    lane = rgb[:round(height*.40)]
    mask = ((lane.max(axis=2)-lane.min(axis=2) < 28)
            & (lane.mean(axis=2) >= 32) & (lane.mean(axis=2) <= 125)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for x, y, w, h, area in stats[1:]:
        if 45 <= w <= 155 and 18 <= h <= 52 and area >= w*h*.62:
            if x >= 4 and x+w < width-4 and y+h < lane.shape[0]-1:
                boxes.append(tuple(int(v) for v in (x, y, x+w, y+h)))
    if not 1 <= len(boxes) <= 5:
        raise ValueError('필요 수량 표시 1~5개를 온전히 확인하지 못했습니다.')
    centers = [(b[1]+b[3])/2 for b in boxes]
    if max(centers)-min(centers) > 12:
        raise ValueError('재료 수량 표시가 같은 줄에 있지 않습니다.')
    return sorted(boxes)


def crystal_detail_name(tokens):
    """Only accept an explicit '…의 크리스탈' title, never a breakthrough owner."""
    rows=[]
    for token in sorted((t for t in tokens if t.score >= .60),key=lambda t:(t.cy,t.x1)):
        row=next((r for r in rows if abs(r[0].cy-token.cy)<max(10,token.height*.6)),None)
        if row is None: rows.append([token])
        else: row.append(token)
    for row in rows:
        text=' '.join(t.text.strip() for t in sorted(row,key=lambda t:t.x1)).strip()
        match=re.fullmatch(r'(.+?)\s*(?:의\s*)?크\s*리\s*스\s*[탈털]',text)
        if match:
            owner=re.sub(r'\s+',' ',match.group(1)).strip()
            if owner and _compact_ocr_text(owner) not in {'의','크리스탈','획득','결과'}:
                return owner+'의 크리스탈'
    return None


def crystal_material_name(name):
    """Database/display identity is the character name, without the crystal suffix."""
    name=re.sub(r'\s+',' ',str(name or '')).strip()
    return re.sub(r'\s*(?:의\s*)?크\s*리\s*스\s*[탈털]$','',name).strip()


def confirmed_detail_readings(readings,known_names=()):
    """Accept exact agreement, or a registered owner's 의/외 particle OCR error.

    Never repair the owner's letters. One explicit correct reading must be
    >=.97 and the corroborating reading >=.85, both with the same full owner
    and exact crystal suffix. An unregistered name cannot use this correction.
    """
    if len(readings)!=2 or min(score for _,score in readings)<.85: return None
    texts=[_compact_ocr_text(text) for text,_ in readings]
    if texts[0]==texts[1]:
        return crystal_detail_name([OCRToken(readings[0][0],0,0,200,30,min(s for _,s in readings))])
    matches=[]
    for name in known_names:
        owner=crystal_material_name(name);key=_compact_ocr_text(owner)
        if not key: continue
        normal=key+'의크리스탈'
        supported={normal,key+'외크리스탈'}
        if (all(text in supported for text in texts)
                and any(text==normal and score>=.97 for text,(_,score) in zip(texts,readings))):
            matches.append(owner)
    matches=list(dict.fromkeys(matches))
    return matches[0]+'의 크리스탈' if len(matches)==1 else None


def single_owner_material_binding(ledger, title, previous, current, now):
    """One confirmed owner + one material + matching explicit title is unambiguous."""
    if not previous or not current: return None
    if not _compact_ocr_text(title).endswith('크리스탈'): return None
    if not previous.get('hwnd') or len(previous.get('game_rect',()))!=4: return None
    if not 0<=now-previous.get('at',-1000)<=300: return None
    key=previous.get('key')
    if ledger.get('active_farming_group')!=key: return None
    group=ledger.get('farming_groups',{}).get(key,{})
    rows=group.get('materials',[])
    if len(rows)!=1 or group.get('revision')!=previous.get('revision'): return None
    if crystal_name_key(title)!=crystal_name_key(group.get('name','')): return None
    if (previous.get('hwnd')!=current.get('hwnd') or
            tuple(previous.get('game_rect',()))!=tuple(current.get('game_rect',()))): return None
    return key,0,rows[0]['id'],group['revision']


def known_crystal_name(entry):
    name=str(entry.get('crystal_name') or entry.get('label') or '').strip()
    if not name or re.fullmatch(r'(?:크리스탈\s*\d+|C\d+)',name): return ''
    return crystal_material_name(name)


def crystal_name_key(name):
    return _compact_ocr_text(crystal_material_name(name))


def link_crystal_monsters(database,settings):
    """Exact names connect identities; crystal IDs, artwork and counts never change."""
    result=json.loads(json.dumps(database));index={};display={}
    def add(number,name):
        key=crystal_name_key(name)
        if key and key not in ('미확인','확인필요') and not re.fullmatch(r'파티원\d+',key) and str(number).isdigit():index.setdefault(key,set()).add(int(number));display[int(number)]=crystal_material_name(name)
    for row in monster_catalog()['monsters']:add(row['number'],row.get('name_ko',''))
    for number,name in settings.get('monster_korean_names',{}).items():add(number,name)
    for number,entry in settings.get('monster_overrides',{}).items():add(number,entry.get('name',''))
    for entry in settings.get('background_party_profiles',[]):
        if entry.get('number'):add(entry['number'],entry.get('name',''))
    for entry in result.get('crystals',[]):
        if entry.get('monster_number') and entry.get('monster_link_source')!='name_match':continue
        name=known_crystal_name(entry);matches=index.get(crystal_name_key(name),set()) if name else set()
        if len(matches)==1:
            number=next(iter(matches));entry.update(monster_number=number,monster_name_ko=name,monster_link_source='name_match',monster_link_status='linked',monster_link_name_key=crystal_name_key(name))
        else:
            if entry.get('monster_link_source')=='name_match':
                for key in ('monster_number','monster_name_ko','monster_link_source','monster_link_name_key'):entry.pop(key,None)
            entry['monster_link_status']='ambiguous' if len(matches)>1 else 'unmatched' if name else 'unnamed'
    return result


def crystal_display_name(entry, row, index):
    return crystal_material_name(entry.get('crystal_name') or row.get('crystal_name') or entry.get('label')
            or f'크리스탈 {index}')


def register_farming_owner(ledger, name):
    name=str(name or '').strip()[:60]
    if not name: raise ValueError('돌파 대상 이름이 필요합니다.')
    updated=json.loads(json.dumps(ledger))
    groups=updated.setdefault('farming_groups',{})
    key=next((k for k,g in groups.items() if _compact_ocr_text(g['name'])==_compact_ocr_text(name)),
             hashlib.sha256(name.encode('utf-8')).hexdigest()[:24])
    groups.setdefault(key,{'name':name,'revision':uuid.uuid4().hex,'materials':[]})
    updated['active_farming_group']=key
    return updated,key


def name_crystal_material(ledger, database, binding, name, image_file):
    """Reuse the selected icon ID; a verified material title must not rename its owner."""
    name=crystal_material_name(name)
    if not name: raise ValueError('재료 이름을 확인하지 못했습니다.')
    key,index,expected_id,revision=binding
    group=ledger.get('farming_groups',{}).get(key)
    if not group or group['revision']!=revision or not 0<=index<len(group['materials']):
        raise ValueError('재료 구성이 바뀌어 이름 연결을 보류했습니다. 재료를 다시 클릭해 주세요.')
    source=group['materials'][index]
    if source['id']!=expected_id: raise ValueError('선택한 재료가 바뀌었습니다.')
    entries=database.get('crystals',[])
    entry=next((r for r in entries if str(r['id'])==expected_id),None)
    matching=[r for r in entries if crystal_name_key(r.get('crystal_name') or r.get('label',''))==crystal_name_key(name)]
    if entry is not None and not known_crystal_name(entry) and len(matching)==1:
        canonical=str(matching[0]['id'])
    elif entry is not None:
        if matching and any(r['id']!=entry['id'] for r in matching):
            raise ValueError('같은 이름의 다른 이미지가 이미 있습니다. 중복 등록하지 않았습니다.')
        canonical=str(entry['id'])
    elif len(matching)==1:
        entry=matching[0]; canonical=str(entry['id'])
    elif matching:
        raise ValueError('같은 이름의 기존 이미지가 여러 개라 등록을 보류했습니다.')
    else:
        numbers=[int(m.group(1)) for row in entries if (m:=re.search(r'(\d+)$',str(row['id'])))]
        canonical=f'C{max(numbers,default=0)+1:04d}'
    updated=json.loads(json.dumps(ledger)); db=json.loads(json.dumps(database))
    target=next((r for r in db.get('crystals',[]) if str(r['id'])==canonical),None)
    if target is None:
        target={'id':canonical,'image_file':image_file,'first_seen':time.strftime('%Y-%m-%d %H:%M:%S')}
        db.setdefault('crystals',[]).append(target)
    had_image=bool(target.get('image_file'))
    if not had_image: target['image_file']=image_file
    target.update(crystal_name=name,label=name,card_name=name)
    if canonical!=expected_id:
        db['crystals']=[row for row in db['crystals'] if str(row['id'])!=expected_id]
    stocks=updated.setdefault('stocks',{})
    if canonical not in stocks or not had_image:
        stocks[canonical]=int(stocks.get(expected_id,stocks.get(canonical,0)))
    for other in updated['farming_groups'].values():
        changed=False
        for row in other['materials']:
            if row['id']==expected_id or row['id']==canonical:
                row.update(id=canonical,confirmed=True,crystal_name=name,image_file=target['image_file'])
                changed=True
        if crystal_name_key(other['name'])==crystal_name_key(name):
            other.update(crystal_id=canonical,image_file=target['image_file'])
            changed=True
        if changed:
            ids=[r['id'] for r in other['materials']]
            if len(ids)!=len(set(ids)): raise ValueError('한 대상의 서로 다른 재료를 같은 이미지로 합칠 수 없습니다.')
            other['revision']=uuid.uuid4().hex
    if canonical!=expected_id:
        stocks.pop(expected_id,None)
        counts=updated.setdefault('counts',{})
        if expected_id in counts:
            counts[canonical]=max(int(counts.get(canonical,0)),int(counts.pop(expected_id)))
        receipts=[updated.get('reward_session',{})]+updated.get('events',[])
        for receipt in receipts:
            amounts=receipt.get('amounts',{})
            if expected_id in amounts:
                amounts[canonical]=max(int(amounts.get(canonical,0)),int(amounts.pop(expected_id)))
        active=updated.get('active_target')
        if active and str(active.get('id'))==expected_id:
            active.update(id=canonical,label=name)
    return updated,db,canonical


def register_farming_group(ledger, name, materials):
    """One complete F7 snapshot; stocks and group definition share one commit."""
    name = str(name or '').strip()
    if not name or len(name) > 60:
        raise ValueError('돌파 대상 이름을 확인해 주세요.')
    if not 1 <= len(materials) <= 5:
        raise ValueError('재료 전체를 읽어야 등록할 수 있습니다.')
    ids = [str(row['id']) for row in materials]
    if len(set(ids)) != len(ids):
        raise ValueError('서로 다른 재료가 같은 아이콘으로 인식되어 등록을 보류했습니다.')
    updated = json.loads(json.dumps(ledger))
    stocks = updated.setdefault('stocks', {})
    rows = []
    for index, row in enumerate(materials, 1):
        required, owned = int(row['required']), int(row['current'])
        if not (0 <= required <= 999999 and 0 <= owned <= 999999):
            raise ValueError('재료 수량이 올바르지 않습니다.')
        cid = str(row['id'])
        stocks[cid] = owned
        rows.append(dict({'id': cid, 'index': index, 'required': required},
                         **{k:row[k] for k in ('confirmed','crystal_name','image_file') if k in row}))
    groups = updated.setdefault('farming_groups', {})
    key = hashlib.sha256(name.encode('utf-8')).hexdigest()[:24]
    owner_link={k:v for k,v in groups.get(key,{}).items() if k in ('crystal_id','image_file')}
    groups[key] = dict(owner_link,name=name,revision=uuid.uuid4().hex,materials=rows)
    updated['active_farming_group'] = key
    updated['last_confirmed_farming_group'] = key
    return updated, key


def farming_group_rows(ledger, key):
    group = ledger.get('farming_groups', {}).get(key, {})
    stocks = ledger.get('stocks', {})
    awarded = ledger.get('reward_session', {}).get('amounts', {})
    return [dict(row, owned=int(stocks.get(row['id'], 0)),
                 gained=int(awarded.get(row['id'], 0)),
                 deficit=max(0, int(row['required'])-int(stocks.get(row['id'], 0))))
            for row in group.get('materials', [])]


def credit_farming_result(ledger, session_id, amounts, groups_revision):
    """Idempotent receipt and all inventory changes in one atomic JSON document."""
    session = ledger.get('reward_session', {})
    if session.get('id') != session_id or not session.get('open'):
        raise ValueError('전투 결과가 바뀌어 이번 인식을 저장하지 않았습니다.')
    if session.get('credited'):
        return ledger, False
    if ledger.get('farming_groups', {}) != groups_revision:
        raise ValueError('인식 중 재료 등록이 바뀌어 집계를 보류했습니다.')
    known = {row['id'] for group in groups_revision.values() for row in group['materials'] if row.get('confirmed',True)}
    if any(cid not in known or type(amount) is not int or not 0 <= amount <= 999999
           for cid, amount in amounts.items()):
        raise ValueError('확인되지 않은 재료 또는 수량입니다.')
    updated = json.loads(json.dumps(ledger))
    for cid, amount in amounts.items():
        updated.setdefault('stocks', {})[cid] = int(updated.get('stocks', {}).get(cid, 0))+amount
        updated.setdefault('counts', {})[cid] = int(updated.get('counts', {}).get(cid, 0))+amount
    updated['reward_session'].update(credited=True, amounts=amounts,
                                     time=time.strftime('%Y-%m-%d %H:%M:%S'))
    updated.setdefault('events', []).append({'kind': 'farming_result', 'session_id': session_id,
                                            'amounts': amounts, 'time': updated['reward_session']['time']})
    updated['events'] = updated['events'][-300:]
    return updated, True


def reward_viewport(image):
    return image.crop((round(image.width*.29), round(image.height*.245),
                       round(image.width*.705), round(image.height*.763))).convert('L')


def reward_scrollbar_position(image):
    """Return (at top, at bottom) for the orange thumb, or None for no thumb."""
    rgb = np.asarray(image.convert('RGB'), dtype=np.int16)
    h, w = rgb.shape[:2]
    lane = rgb[round(h*.244):round(h*.740), round(w*.749):round(w*.759)]
    orange = ((lane[...,0] > 195) & (lane[...,1] >= 65)
              & (lane[...,1] < 190) & (lane[...,2] < 85))
    occupied = np.where(np.mean(orange, axis=1) >= .35)[0]
    if len(occupied) < h*.025:
        return None
    tolerance = max(6, h*.014)
    return bool(occupied[0] <= tolerance), bool(occupied[-1] >= lane.shape[0]-1-tolerance)


def crystal_art_signatures(image):
    """Compare artwork and colored borders, excluding each card's amount row."""
    import cv2
    rgb = np.asarray(image.convert('RGB'))
    h, w = rgb.shape[:2]
    signatures = []
    for left, top, right, bottom in ((.08,.06,.92,.76), (.13,.10,.87,.74), (.04,.03,.96,.80)):
        patch = rgb[round(h*top):round(h*bottom), round(w*left):round(w*right)]
        if min(patch.shape[:2]) < 5:
            raise ValueError('재료 아이콘이 너무 작습니다.')
        signatures.append(cv2.cvtColor(cv2.resize(patch, (48,64), interpolation=cv2.INTER_AREA),
                                       cv2.COLOR_RGB2LAB).astype(np.int16))
    return signatures


def reward_template_scroll_delta(previous,current):
    """Measure scroll by unique artwork strips when sparse optical flow fails.

    Multiple independent strips must agree on one displacement; repeated rows
    or a gray viewport never supply an invented movement amount.
    """
    import cv2
    a,b=np.asarray(reward_viewport(previous)),np.asarray(reward_viewport(current))
    if a.shape!=b.shape: return None
    height,width=a.shape
    strip=max(32,round(height*.18))
    estimates=[]
    for top in np.linspace(0,height-strip,7).round().astype(int):
        patch=a[top:top+strip]
        if float(patch.std())<10: continue
        scores=cv2.matchTemplate(b,patch,cv2.TM_CCOEFF_NORMED).ravel()
        if not len(scores): continue
        at=int(np.argmax(scores));score=float(scores[at])
        competitors=scores.copy();competitors[max(0,at-5):min(len(scores),at+6)]=-1
        if score<.94 or score-float(competitors.max())<.025: continue
        delta=float(top-at)
        if abs(delta)<=height*.65: estimates.append((delta,score))
    if len(estimates)<2: return None
    cluster=max(([item for item in estimates if abs(item[0]-delta)<=2] for delta,_ in estimates),key=len)
    if len(cluster)<2 or len(cluster)<len(estimates)*.75: return None
    return float(np.median([delta for delta,_ in cluster]))


def reward_scroll_delta(previous, current):
    """Measure actual content translation; never invent a scroll distance."""
    import cv2
    a, b = np.asarray(reward_viewport(previous)), np.asarray(reward_viewport(current))
    if a.shape != b.shape:
        raise ValueError('집계 중 게임 창 크기가 바뀌었습니다.')
    if float(np.abs(a.astype(np.int16)-b.astype(np.int16)).mean()) < .7:
        return 0.0
    template_delta=reward_template_scroll_delta(previous,current)
    if template_delta is not None: return template_delta
    points = cv2.goodFeaturesToTrack(a, maxCorners=240, qualityLevel=.03, minDistance=7)
    if points is None or len(points) < 12:
        raise ValueError('스크롤 전후의 겹친 카드를 확인할 수 없습니다.')
    moved, valid, _ = cv2.calcOpticalFlowPyrLK(a, b, points, None, winSize=(25, 25), maxLevel=3)
    if moved is None:
        raise ValueError('스크롤 이동량을 확인할 수 없습니다.')
    returned, reverse_valid, _ = cv2.calcOpticalFlowPyrLK(b, a, moved, None, winSize=(25, 25), maxLevel=3)
    if returned is None:
        raise ValueError('스크롤 겹침 재확인 실패')
    shift = (moved-points).reshape(-1, 2)
    back_error = np.linalg.norm((returned-points).reshape(-1, 2), axis=1)
    good = ((valid.ravel() != 0) & (reverse_valid.ravel() != 0) & (back_error < 1.8)
            & (np.abs(shift[:, 0]) < 2.5) & (np.abs(shift[:, 1]) < previous.height*.28))
    dy = shift[good, 1]
    if len(dy) < 12:
        raise ValueError('스크롤 중 카드 겹침이 부족해 저장하지 않았습니다.')
    median = float(np.median(dy))
    if float(np.mean(np.abs(dy-median) < 2.5)) < .72:
        raise ValueError('카드 이동이 불안정해 집계를 보류했습니다.')
    return -median


def merge_reward_page(aggregate, page, offset, image_height):
    """Physical card identity is column + measured virtual row, not just artwork."""
    result = list(aggregate)
    for item in page:
        candidate = dict(item, virtual_y=float(item['y'])+offset)
        duplicate = next((old for old in result if abs(old['x']-candidate['x']) < image_height*.035
                          and abs(old['virtual_y']-candidate['virtual_y']) < image_height*.035), None)
        if duplicate is not None:
            if int(duplicate['amount']) != int(candidate['amount']):
                raise ValueError('같은 카드의 획득 수량이 다르게 읽혀 집계를 보류했습니다.')
        else:
            result.append(candidate)
    return result


class WindowsDragInput:
    """Mouse-only hook: allow tagged playback input, keep the keyboard usable."""

    INPUT_TAG = 0x50414430

    @classmethod
    def blocks_event(cls, code, flags, extra_info):
        return code >= 0 and not (flags & 1 and extra_info == cls.INPUT_TAG)

    @staticmethod
    def absolute_position(x, y, origin_x, origin_y, width, height):
        if width < 2 or height < 2:
            raise RuntimeError("마우스 화면 크기를 확인할 수 없습니다.")
        return (round(max(0, min(width-1, x-origin_x))*65535/(width-1)),
                round(max(0, min(height-1, y-origin_y))*65535/(height-1)))

    def __init__(self):
        if sys.platform != 'win32':
            raise RuntimeError("자동 이동은 Windows에서만 지원합니다.")
        from ctypes import wintypes as w
        self._api = ctypes.WinDLL('user32', use_last_error=True)
        self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        pointer_uint = ctypes.c_size_t

        class MouseInput(ctypes.Structure):
            _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('mouseData', w.DWORD),
                        ('dwFlags', w.DWORD), ('time', w.DWORD), ('dwExtraInfo', pointer_uint)]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [('wVk', w.WORD), ('wScan', w.WORD), ('dwFlags', w.DWORD),
                        ('time', w.DWORD), ('dwExtraInfo', pointer_uint)]

        class HardwareInput(ctypes.Structure):
            _fields_ = [('uMsg', w.DWORD), ('wParamL', w.WORD), ('wParamH', w.WORD)]

        class InputUnion(ctypes.Union):
            _fields_ = [('mi', MouseInput), ('ki', KeyboardInput), ('hi', HardwareInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ('data',)
            _fields_ = [('type', w.DWORD), ('data', InputUnion)]

        class MouseHookInfo(ctypes.Structure):
            _fields_ = [('pt', w.POINT), ('mouseData', w.DWORD), ('flags', w.DWORD),
                        ('time', w.DWORD), ('dwExtraInfo', pointer_uint)]

        self._Input, self._MouseInput = Input, MouseInput
        self._HookInfo, self._Message = MouseHookInfo, w.MSG
        self._Callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                          ctypes.c_size_t, ctypes.c_ssize_t)
        api = self._api
        api.SetWindowsHookExW.argtypes = (ctypes.c_int, self._Callback, w.HINSTANCE, w.DWORD)
        api.SetWindowsHookExW.restype = w.HANDLE
        api.CallNextHookEx.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        api.CallNextHookEx.restype = ctypes.c_ssize_t
        api.UnhookWindowsHookEx.argtypes = (w.HANDLE,)
        api.UnhookWindowsHookEx.restype = w.BOOL
        api.PeekMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
        api.PeekMessageW.restype = w.BOOL
        api.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
        api.GetMessageW.restype = w.BOOL
        api.TranslateMessage.argtypes = (ctypes.POINTER(w.MSG),)
        api.DispatchMessageW.argtypes = (ctypes.POINTER(w.MSG),)
        api.DispatchMessageW.restype = ctypes.c_ssize_t
        api.PostThreadMessageW.argtypes = (w.DWORD, w.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
        api.PostThreadMessageW.restype = w.BOOL
        api.SendInput.argtypes = (w.UINT, ctypes.POINTER(Input), ctypes.c_int)
        api.SendInput.restype = w.UINT
        api.mouse_event.argtypes = (w.DWORD, w.DWORD, w.DWORD, w.DWORD, pointer_uint)
        api.mouse_event.restype = None
        api.GetSystemMetrics.argtypes = (ctypes.c_int,)
        api.GetSystemMetrics.restype = ctypes.c_int
        self._kernel.GetCurrentThreadId.restype = w.DWORD
        self._kernel.GetModuleHandleW.argtypes = (w.LPCWSTR,)
        self._kernel.GetModuleHandleW.restype = w.HMODULE
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._closed = False
        self._pressed = False
        self._thread_id = None
        self._startup_error = None
        self._thread = threading.Thread(target=self._hook_loop, daemon=True)

    def _hook_loop(self):
        hook = None
        try:
            message = self._Message()
            self._api.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self._thread_id = self._kernel.GetCurrentThreadId()

            def callback(code, message_id, pointer):
                if code >= 0:
                    event = ctypes.cast(pointer, ctypes.POINTER(self._HookInfo)).contents
                    if self.blocks_event(code, event.flags, event.dwExtraInfo):
                        return 1
                return self._api.CallNextHookEx(None, code, message_id, pointer)

            self._callback = self._Callback(callback)  # Keep the native callback alive.
            hook = self._api.SetWindowsHookExW(
                14, self._callback, self._kernel.GetModuleHandleW(None), 0)
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            while not self._stop.is_set():
                result = self._api.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                self._api.TranslateMessage(ctypes.byref(message))
                self._api.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._startup_error = exc
        finally:
            if hook:
                self._api.UnhookWindowsHookEx(hook)
            self._ready.set()

    def start(self):
        self._thread.start()
        if not self._ready.wait(1.0) or self._startup_error or not self._thread.is_alive():
            self.close()
            raise RuntimeError("마우스 보호를 시작하지 못해 자동 이동을 중단했습니다.") from self._startup_error
        return self

    def _send(self, flags, dx=0, dy=0, mouse_data=0):
        if self._closed or not self._thread.is_alive():
            raise RuntimeError("마우스 보호가 종료되어 자동 이동을 중단했습니다.")
        event = self._Input(type=0)
        event.mi = self._MouseInput(dx=dx, dy=dy, mouseData=mouse_data,
                                   dwFlags=flags, dwExtraInfo=self.INPUT_TAG)
        if self._api.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise RuntimeError("Windows에서 자동 마우스 입력을 허용하지 않았습니다.")

    def move(self, x, y):
        with self._lock:
            dx, dy = self.absolute_position(x, y, *(self._api.GetSystemMetrics(i)
                                                   for i in (76, 77, 78, 79)))
            self._send(0x0001 | 0x8000 | 0x4000 | 0x2000, dx, dy)

    def down(self):
        with self._lock:
            self._send(0x0002)
            self._pressed = True

    def wheel(self, delta):
        with self._lock:
            self._send(0x0800, mouse_data=delta & 0xffffffff)

    def close(self):
        try:
            with self._lock:
                if self._closed:
                    return
                try:
                    if self._pressed:
                        try:
                            self._send(0x0004)
                        except Exception:
                            self._api.mouse_event(0x0004, 0, 0, 0, self.INPUT_TAG)
                finally:
                    self._pressed = False
                    self._closed = True
        finally:
            # Release the drag before removing the mouse-only hook, even on F10,
            # shutdown, startup timeout or an exception during playback.
            self._stop.set()
            if self._thread_id is not None:
                self._api.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            if self._thread.ident is not None:
                self._thread.join(timeout=1.0)


class WindowsMaterialClickObserver(WindowsDragInput):
    """Observe physical clicks without blocking or changing any mouse event."""
    def __init__(self,on_click):
        self.on_click=on_click
        super().__init__()
        from ctypes import wintypes as w
        self._api.WindowFromPoint.argtypes=(w.POINT,)
        self._api.WindowFromPoint.restype=w.HWND
        self._api.GetAncestor.argtypes=(w.HWND,w.UINT)
        self._api.GetAncestor.restype=w.HWND

    def _hook_loop(self):
        hook=None
        try:
            message=self._Message()
            self._api.PeekMessageW(ctypes.byref(message),None,0,0,0)
            self._thread_id=self._kernel.GetCurrentThreadId()
            def callback(code,message_id,pointer):
                try:
                    if code>=0 and message_id==0x0201:
                        event=ctypes.cast(pointer,ctypes.POINTER(self._HookInfo)).contents
                        if not event.flags&1:
                            owner=self._api.GetAncestor(self._api.WindowFromPoint(event.pt),2)
                            self.on_click(event.pt.x,event.pt.y,int(owner or 0))
                except Exception:
                    pass
                return self._api.CallNextHookEx(None,code,message_id,pointer)
            self._callback=self._Callback(callback)
            hook=self._api.SetWindowsHookExW(14,self._callback,self._kernel.GetModuleHandleW(None),0)
            if not hook: raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            while not self._stop.is_set():
                if self._api.GetMessageW(ctypes.byref(message),None,0,0)<=0: break
                self._api.TranslateMessage(ctypes.byref(message))
                self._api.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._startup_error=exc
        finally:
            if hook: self._api.UnhookWindowsHookEx(hook)
            self._ready.set()


class RouteDebugWriter:
    """Serialize diagnostic writes away from Tk and the timed mouse-input loop."""
    def __init__(self, root, keep=20):
        self.root=Path(root)
        self.keep=keep
        self.queue=queue.Queue()
        self.errors=[]
        self.thread=threading.Thread(target=self._work,daemon=True,name='route-debug-writer')
        self.thread.start()

    def begin(self, metadata, images=None, kind='route'):
        ticket=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:10]
        self.update(ticket,dict(metadata,kind=kind,game_match_outcome='unverified'),images)
        return ticket

    def update(self, ticket, metadata, images=None, texts=None):
        if not re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{10}',str(ticket or '')):
            return
        self.queue.put((ticket,json.loads(json.dumps(metadata)),
                        {k:v.copy() for k,v in (images or {}).items() if v is not None},dict(texts or {})))

    def _work(self):
        while True:
            item=self.queue.get()
            try:
                if item is None: return
                ticket,metadata,images,texts=item
                directory=self.root/ticket
                directory.mkdir(parents=True,exist_ok=True)
                report=_load_json_dict(directory/'report.json')
                report.update(metadata)
                _atomic_json_write(directory/'report.json',report)
                for filename,picture in images.items():
                    if Path(filename).name!=filename or not filename.endswith('.png'): continue
                    temp=directory/(filename+'.tmp')
                    picture.save(temp,format='PNG');temp.replace(directory/filename)
                for filename,content in texts.items():
                    if Path(filename).name==filename and filename.endswith('.txt'):
                        (directory/filename).write_text(content,encoding='utf-8')
                self._prune()
            except Exception as exc:
                self.errors.append(f'{type(exc).__name__}: {exc}')
                self.errors=self.errors[-5:]
            finally:
                self.queue.task_done()

    def _prune(self):
        import shutil
        runs=sorted((p for p in self.root.iterdir() if p.is_dir()
                     and re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{10}',p.name)),
                    key=lambda p:p.stat().st_mtime,reverse=True)
        for kind in ('route','button_missing'):
            ordinary=[p for p in runs if (report:=_load_json_dict(p/'report.json')).get('kind')==kind
                      and not report.get('user_reported_failure')]
            for old in ordinary[self.keep:]: shutil.rmtree(old)

    def close(self):
        self.queue.put(None)
        self.thread.join(timeout=2)


def route_debug_board_picture(board, path=None):
    image=Image.new('RGB',(COLS*64,ROWS*64),'#eeeeee')
    draw=ImageDraw.Draw(image)
    for index,code in enumerate(board):
        row,col=divmod(index,COLS);x,y=col*64,row*64
        draw.rectangle((x+2,y+2,x+61,y+61),fill=COLOR_INFO[code][1],outline='#555555')
        draw.text((x+5,y+5),code,fill='black')
    if path:
        points=[((i%COLS+.5)*64,(i//COLS+.5)*64) for i in path]
        if len(points)>1: draw.line(points,fill='white',width=3)
        for n,(x,y) in enumerate(points):
            draw.text((x-4,y-4),str(n),fill='black',stroke_width=1,stroke_fill='white')
    return image


COMMUNITY_ADB_HELP_URL = 'https://arca.live/b/arknights/154754122'


def background_connection_help(path='',serial=''):
    """Local instructions remain available without a browser or network."""
    return '\n'.join([
        'MuMu 연결 설정 순서', '',
        '그림으로 보는 ADB·포트 설정 (사용자 공유 참고 글):', COMMUNITY_ADB_HELP_URL, '',
        '1. MuMu에서 ADB 연결 준비',
        'MuMu와 게임을 실행합니다. MuMu 설정 창 아래쪽 [ADB 포트]를 펼치면 해당 기기의 포트를 볼 수 있습니다. 별도로 ADB 허용 항목이 있는 버전만 로컬 연결을 허용합니다.',
        '처음 연결한다면 [설정 마법사 시작]에서 설치 위치 찾기 → 기기 연결 → 게임 화면 확인 → 저장 순서로 진행하세요.',
        '설정 이름과 위치, 실제 연결 포트는 MuMu 버전에 따라 다를 수 있습니다.', '',
        '2. Google 공식 ADB 또는 MuMu ADB 파일',
        r'[Google 공식 ADB로 강제 교체]를 누르면 프로그램 폴더의 platform-tools\adb.exe에 설치하고 .\platform-tools\adb.exe로 저장합니다.',
        'Google 공식 파일의 체크섬과 실행 버전을 확인한 뒤 적용합니다. 프로그램 업데이트는 platform-tools 폴더를 교체하지 않습니다. 다른 폴더로 옮길 때 함께 복사하세요. [이전 ADB로 되돌리기]도 사용할 수 있습니다.',
        '연결 설정의 [찾기]를 눌러 MuMu에 포함된 adb.exe 파일을 선택합니다. 폴더만 입력하는 것이 아니라 실행 파일까지 선택해야 합니다.',
        r'현재 확인한 경로: C:\Program Files\Netease\MuMuPlayer\nx_device\15.0\shell\adb.exe',
        r'다른 버전의 예: C:\Program Files\Netease\MuMuPlayer\nx_device\12.0\shell\adb.exe',
        'nx_device 다음 폴더는 설치 버전에 따라 12.0, 15.0 등으로 다릅니다. 마법사는 설치된 버전 폴더를 탐색합니다. 직접 설치 위치를 바꿨다면 해당 MuMu 폴더에서 adb.exe를 찾아 선택합니다. 두 입력란을 비우면 프로그램이 자동 탐색·연결을 시도합니다.',
        '현재 입력한 ADB 파일: '+(path.strip() or '(비어 있음 · 자동 탐색)'), '',
        '3. 기기 주소 확인',
        'MuMu의 ADB 포트에 5555,16384처럼 두 숫자가 함께 표시되면 해당 기기에 두 포트가 제공되는 것입니다. 예: 127.0.0.1:5555 또는 127.0.0.1:16384. 마법사는 먼저 연결된 주소만 표시할 수 있으며 다른 포트는 직접 입력 후 [이 주소로 연결]로 확인합니다.',
        'adb.exe가 있는 폴더를 탐색기에서 열고, 주소창에 cmd를 입력해 Enter를 누릅니다. 열린 명령창에서 다음을 실행합니다:',
        '    adb.exe devices', '',
        '    emulator-5554    device',
        '위처럼 나오면 기기 주소에 emulator-5554를 그대로 입력합니다.',
        '    127.0.0.1:16384  device',
        '이렇게 나오면 127.0.0.1:16384를 입력합니다. 실제 목록의 왼쪽 값을 사용하고, 예시 포트 번호를 무조건 입력하지 마세요.',
        '기기가 한 대면 주소를 비워 자동 선택할 수도 있습니다. 여러 대면 사용할 MuMu의 주소를 직접 지정하세요. USB 휴대폰은 자동 선택하지 않습니다.',
        '현재 입력한 기기 주소: '+(serial.strip() or '(비어 있음 · 자동 선택)'), '',
        '4. offline 상태일 때',
        'emulator-5554 offline은 기기는 발견했지만 캡처·입력을 사용할 수 없는 상태입니다. device로 바뀐 뒤 사용하세요.',
        'MuMu의 ADB 허용을 확인하고 게임을 켜둔 상태에서 다음을 실행합니다:',
        '    adb.exe reconnect offline',
        '    adb.exe devices',
        '계속 offline이면 MuMu를 완전히 종료 후 다시 실행합니다. 필요한 경우 아래 명령으로 ADB 서버를 재시작할 수 있습니다. 다른 ADB 연결도 잠시 끊깁니다.',
        '    adb.exe kill-server',
        '    adb.exe start-server',
        '    adb.exe devices',
        '목록이 비어 있으면 아직 연결된 기기가 없는 상태입니다. MuMu 실행·ADB 허용·실제 연결 포트를 확인하세요. 이 안내창에서는 위 명령을 자동 실행하지 않습니다.', '',
        '5. MuMu 고속 화면 캡처',
        r'ADB 파일과 설치 루트는 다릅니다. 설치 루트 예: C:\Program Files\Netease\MuMuPlayer',
        '파밍 연결 설정의 [화면 캡처]에서 자동 또는 MuMu 고속을 선택합니다. 설치 루트는 nx_main 폴더의 상위 폴더이며, ADB 경로로 자동 탐색할 수도 있습니다.',
        '기기 번호는 MuMu의 #0, #1 뒤 숫자입니다. 표준 포트 16384 → #0, 16416 → #1입니다. 5555나 emulator-5554 주소에서는 번호를 추측하지 않으니 직접 확인해 입력하세요.',
        '[스크린샷 테스트]를 누르면 연결 확인 후 한 장만 찍고 캡쳐 속도(ms)와 MumuExtras/ADB 방식을 표시합니다. 연결 준비와 게임 확인은 속도에서 제외하고, 이미지 변환 시간은 포함합니다.',
        '참고: https://arca.live/b/hx3n/148912243',
        '참고: https://docs.maa.plus/ko-kr/manual/connection.html', '',
        '6. 저장하고 확인',
        '연결 설정에서 [저장]을 누른 뒤 게임을 잠입 확인 화면으로 옮깁니다.',
        '[파티 설정]을 누르면 현재 파티를 확인하고 등록·갱신이 필요한 캐릭터만 자동으로 읽습니다. 잠입 확인 화면에서 사용하세요.',
        '연결만 성공해도 게임이 다른 화면에 있으면 파티 읽기는 시작되지 않을 수 있습니다.',
        '백그라운드·최소화 파밍은 MuMu 엔진이 실행 중이어야 하며, MuMu의 백그라운드 일시정지 설정은 꺼주세요.',
    ])


BACKGROUND_SKILL_SOURCE = 'https://gamerch.com/puzdra0/924684'


def mumu_versioned_adb(root):
    """Search installed nx_device versions numerically; never hard-code 12/15."""
    root=Path(root);found=[]
    bases=[root/'Netease/MuMuPlayer/nx_device',root/'MuMuPlayer/nx_device',
           root/'Program Files/Netease/MuMuPlayer/nx_device',root/'nx_device']
    if root.name.lower()=='nx_device':bases.insert(0,root)
    for base in bases:
        try:versions=list(base.iterdir())
        except OSError:continue
        for version in versions:
            if not re.fullmatch(r'\d+(?:\.\d+)*',version.name):continue
            candidate=version/'shell/adb.exe'
            if candidate.is_file():found.append((tuple(map(int,version.name.split('.'))),candidate))
    return [file for _,file in sorted(found,key=lambda item:(item[0],str(item[1])),reverse=True)]


GOOGLE_ADB_REPOSITORY='https://dl.google.com/android/repository/repository2-1.xml'
GOOGLE_ADB_TERMS='https://developer.android.com/studio/releases/platform-tools'


def program_directory():return Path(__file__).resolve().parent


def resolve_background_adb_path(value):
    value=str(value).strip().strip('"')
    if os.name!='nt':value=value.replace('\\',os.sep)
    path=Path(value).expanduser()
    return (path if path.is_absolute() else program_directory()/path).resolve()


def portable_background_adb_path(path):
    path=resolve_background_adb_path(path)
    try:relative=path.relative_to(program_directory())
    except ValueError:return str(path)
    return '.\\'+str(relative).replace('/', '\\')


def google_adb_release(xml_data):
    import xml.etree.ElementTree as ET
    if len(xml_data)>12*1024*1024:raise ValueError('Google 도구 목록이 너무 큽니다.')
    root=ET.fromstring(xml_data)
    def children(element,name):return [v for v in element if v.tag.split('}')[-1]==name]
    def one(element,name):
        found=children(element,name)
        if len(found)!=1:raise ValueError('Google Platform-Tools 정보를 확인하지 못했습니다: '+name)
        return found[0]
    def text(element,name):return (one(element,name).text or '').strip()
    for package in root:
        if package.tag.split('}')[-1]!='remotePackage' or package.get('path')!='platform-tools':continue
        channels=children(package,'channelRef')
        if channels and channels[0].get('ref')!='channel-0':continue
        revision=one(package,'revision');version='.'.join(text(revision,key) for key in ('major','minor','micro'))
        if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Google 도구 버전이 올바르지 않습니다.')
        for archive in one(package,'archives'):
            if text(archive,'host-os')!='windows':continue
            complete=one(archive,'complete');filename=text(complete,'url');size=int(text(complete,'size'));checksum=one(complete,'checksum')
            digest=(checksum.text or '').strip().lower();algorithm=checksum.get('type','sha1').lower()
            if algorithm not in ('sha1','sha256') or not re.fullmatch('[0-9a-f]{'+str(40 if algorithm=='sha1' else 64)+'}',digest):raise ValueError('공식 체크섬 형식을 확인하지 못했습니다.')
            if not re.fullmatch(r'platform-tools_r[\d.]+-win(?:dows)?\.zip',filename) or not 0<size<=64*1024*1024:raise ValueError('Google Windows 도구 다운로드 정보가 올바르지 않습니다.')
            return dict(version=version,url='https://dl.google.com/android/repository/'+filename,size=size,algorithm=algorithm,checksum=digest)
    raise ValueError('Google 공식 Windows Platform-Tools를 찾지 못했습니다.')


def _google_adb_download(url,limit,cancel):
    from urllib.request import Request,build_opener,HTTPRedirectHandler
    from urllib.parse import urlsplit
    def validate(value):
        u=urlsplit(value)
        if u.scheme!='https' or u.hostname!='dl.google.com' or not u.path.startswith('/android/repository/') or u.username or u.password:raise ValueError('Google 공식 HTTPS 다운로드 주소만 사용합니다.')
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            validate(newurl);return super().redirect_request(req,fp,code,msg,headers,newurl)
    validate(url);deadline=time.monotonic()+100;chunks=[];total=0
    with build_opener(Redirect()).open(Request(url,headers={'User-Agent':'PAD0-GoogleADB/1'}),timeout=12) as response:
        validate(response.geturl())
        while True:
            if cancel.is_set():raise RuntimeError('ADB 설치를 취소했습니다.')
            if time.monotonic()>deadline:raise RuntimeError('Google ADB 다운로드 시간 초과')
            block=response.read(128*1024)
            if not block:break
            total+=len(block)
            if total>limit:raise ValueError('Google 도구 다운로드 크기 제한을 넘었습니다.')
            chunks.append(block)
    return b''.join(chunks)


def install_google_adb(base_dir=None,cancel=None,progress=None,fetch=None,verify=None):
    """Install verified official tools beside the app; never touch emulator files."""
    import io,tempfile,zipfile
    base=Path(base_dir or program_directory()).resolve();base.mkdir(parents=True,exist_ok=True)
    cancel=cancel or threading.Event();fetch=fetch or _google_adb_download
    def tell(message):
        if cancel.is_set():raise RuntimeError('ADB 설치를 취소했습니다.')
        if progress:progress(message)
    def check(executable):
        if verify is not None:return verify(executable)
        data=BackgroundADB(str(executable),cancel=cancel)._run(['version'],selected=False,timeout=8).decode('utf-8','replace')
        if 'Android Debug Bridge version' not in data:raise RuntimeError('설치한 Google ADB의 실행을 확인하지 못했습니다.')
        return data.strip()
    tell('Google 공식 Platform-Tools 버전을 확인합니다…')
    release=google_adb_release(fetch(GOOGLE_ADB_REPOSITORY,12*1024*1024,cancel));target=base/'platform-tools'
    manifest=_load_json_dict(target/'pad0-google-adb.json')
    required=('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','source.properties','NOTICE.txt')
    files=manifest.get('files',{})
    if not isinstance(files,dict):files={}
    if manifest.get('release')==release and all(name in files for name in required):
        valid=all(Path(name).name==name and (target/name).is_file() and hashlib.sha256((target/name).read_bytes()).hexdigest()==digest for name,digest in files.items())
        if valid:
            tell('설치된 공식 ADB의 무결성과 버전을 확인합니다…');version=check(target/'adb.exe');tell('공식 ADB 재사용 준비 완료')
            return dict(path=str(target/'adb.exe'),configured=r'.\platform-tools\adb.exe',version=version,release=release,reused=True)
    tell('Google 공식 ADB를 다운로드합니다… ('+release['version']+')')
    data=fetch(release['url'],release['size'],cancel)
    if len(data)!=release['size'] or hashlib.new(release['algorithm'],data).hexdigest()!=release['checksum']:raise ValueError('Google 공식 파일의 크기/체크섬이 일치하지 않습니다. 기존 ADB를 유지합니다.')
    tell('공식 체크섬 확인 완료 · 프로그램 폴더에 설치를 준비합니다…')
    backup=None
    with tempfile.TemporaryDirectory(dir=base,prefix='.pad0-adb-install-') as tmp:
        staging=Path(tmp)/'platform-tools';staging.mkdir()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names=archive.namelist()
            if len(names)!=len(set(names)) or any(name.startswith(('/', '\\')) or '..' in name.replace('\\','/').split('/') for name in names):raise ValueError('안전하지 않은 Platform-Tools ZIP 경로입니다.')
            selected=set(required)|{'libwinpthread-1.dll'}
            extracted={}
            for name in selected:
                path='platform-tools/'+name
                if path not in names:
                    if name in required:raise ValueError('공식 ADB 구성 파일이 없습니다: '+name)
                    continue
                item=archive.getinfo(path)
                if item.file_size>32*1024*1024 or item.flag_bits&1:raise ValueError('ADB 구성 파일의 크기/형식이 올바르지 않습니다.')
                content=archive.read(item)
                if name.lower().endswith(('.exe','.dll')) and not content.startswith(b'MZ'):raise ValueError('Windows ADB 실행 파일 형식이 올바르지 않습니다.')
                (staging/name).write_bytes(content);extracted[name]=hashlib.sha256(content).hexdigest()
        tell('Google ADB 실행 버전을 확인합니다…');version=check(staging/'adb.exe')
        if not re.search(r'Pkg\.Revision\s*=\s*'+re.escape(release['version'])+r'(?:\s|$)',(staging/'source.properties').read_text(errors='replace')):raise ValueError('공식 도구 목록과 파일 버전이 다릅니다.')
        _atomic_json_write(staging/'pad0-google-adb.json',dict(release=release,files=extracted))
        tell('프로그램의 platform-tools 폴더에 적용합니다…')
        try:
            if target.exists():backup=base/('platform-tools.backup-'+uuid.uuid4().hex[:10]);target.rename(backup)
            staging.rename(target)
        except OSError:
            if backup is not None and not target.exists():backup.rename(target)
            raise RuntimeError('platform-tools 폴더를 바꾸지 못했습니다. 실행 중인 ADB 또는 폴더 권한을 확인하세요. 기존 설정을 유지합니다.')
    return dict(path=str(target/'adb.exe'),configured=r'.\platform-tools\adb.exe',version=version,release=release,reused=False,backup=str(backup) if backup else '')


def find_background_adb(configured=''):
    """Resolve explicit portable paths relative to this program, then auto-detect."""
    if str(configured).strip():
        candidate=resolve_background_adb_path(configured)
        if candidate.is_file():return str(candidate)
        raise RuntimeError('지정한 ADB 파일이 없습니다: '+str(candidate))
    candidates=[os.environ.get('PAD0_ADB','')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():return str(Path(candidate).resolve())
    official=program_directory()/'platform-tools/adb.exe'
    if official.is_file():return str(official.resolve())
    roots=[Path(os.environ.get(name,'')) for name in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA') if os.environ.get(name)]
    roots += [Path(drive+':/') for drive in ('C','D')]
    versioned=[file for root in roots for file in mumu_versioned_adb(root)]
    if versioned:return str(versioned[0].resolve())
    for root in roots:
        for relative in ('Netease/MuMuPlayer/nx_main/adb.exe','MuMuPlayer/nx_main/adb.exe',
                         'Program Files/Netease/MuMuPlayer/nx_main/adb.exe','nx_main/adb.exe',
                         'Netease/MuMuPlayer-12.0/shell/adb.exe','MuMuPlayer-12.0/shell/adb.exe',
                         'Netease/MuMuPlayerGlobal-12.0/shell/adb.exe','MuMuPlayerGlobal-12.0/shell/adb.exe',
                         'Program Files/Netease/MuMuPlayer-12.0/shell/adb.exe'):
            candidate=root/relative
            candidates.append(str(candidate))
    if sys.platform=='win32':
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',0,winreg.KEY_READ|view) as uninstall:
                            for index in range(winreg.QueryInfoKey(uninstall)[0]):
                                try:
                                    with winreg.OpenKey(uninstall,winreg.EnumKey(uninstall,index)) as entry:
                                        name=str(winreg.QueryValueEx(entry,'DisplayName')[0])
                                        if 'mumu' not in name.lower():continue
                                        location=str(winreg.QueryValueEx(entry,'InstallLocation')[0]).strip('"')
                                        roots.append(Path(location))
                                        candidates.extend(str(Path(location)/relative) for relative in ('nx_main/adb.exe','shell/adb.exe','adb.exe','nemu/vmonitor/bin/adb_server.exe'))
                                except OSError:continue
                    except OSError:continue
        except ImportError:pass
    versioned=[file for root in roots for file in mumu_versioned_adb(root)]
    if versioned:return str(versioned[0].resolve())
    candidates.append(shutil.which('adb'))
    for root in roots:
        for relative in ('Netease/MuMuPlayer/nx_main/adb.exe','MuMuPlayer/nx_main/adb.exe',
                         'Program Files/Netease/MuMuPlayer/nx_main/adb.exe','nx_main/adb.exe',
                         'Netease/MuMuPlayer-12.0/shell/adb.exe','MuMuPlayer-12.0/shell/adb.exe',
                         'Program Files/Netease/MuMuPlayer-12.0/shell/adb.exe',
                         'Netease/MuMu/nemu/vmonitor/bin/adb_server.exe',
                         'Netease/MuMu/emulator/nemu/vmonitor/bin/adb_server.exe'):
            candidates.append(str(root/relative))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():return str(Path(candidate).resolve())
    raise RuntimeError('MuMu ADB를 찾지 못했습니다. [파밍 연결 설정]에서 MuMu의 adb.exe를 지정하세요.')


# MuMu's installed SDK is loaded only in a disposable child process. No SDK DLL
# or third-party implementation is redistributed. ABI reference: EmulatorExtras
# Mumu/external_renderer_ipc/external_renderer_ipc.h (upstream SDK declarations).
def find_mumu_capture_install(adb_path,install_root=''):
    selected=Path(str(install_root or adb_path).strip().strip('"')).expanduser().resolve()
    start=selected.parent if selected.is_file() or selected.suffix.lower()=='.exe' else selected
    ancestors=[start]+list(start.parents)[:5]
    roots=[]
    for folder in ancestors:
        if (folder/'nx_main').is_dir() or (folder/'nx_device').is_dir():roots.append(folder)
    roots += [folder.parent for folder in ancestors if folder.name.lower() in ('shell','nx_main')]
    roots+=ancestors[:1] if install_root else ancestors
    for root in dict.fromkeys(roots):
        candidates=[root/'nx_main/sdk/external_renderer_ipc.dll',root/'shell/sdk/external_renderer_ipc.dll',root/'sdk/external_renderer_ipc.dll']
        # Modern engines may carry their own SDK under nx_device/<version>/shell.
        try:
            adb=Path(str(adb_path).strip().strip('"')).resolve()
            if adb.is_relative_to(root):candidates.append(adb.parent/'sdk/external_renderer_ipc.dll')
        except (OSError,ValueError):pass
        for dll in candidates:
            if dll.is_file() and dll.resolve().is_relative_to(root.resolve()):return str(root.resolve()),str(dll.resolve())
    raise RuntimeError('MuMu 캡처 SDK를 찾지 못했습니다. MuMu 설치 루트 폴더를 확인하세요. 기본 ADB 캡처를 사용합니다.')


def mumu_instance_index(serial,explicit=''):
    value=str(explicit).strip()
    match=re.fullmatch(r'(?:127\.0\.0\.1|localhost):(\d+)',str(serial).strip())
    port=int(match[1]) if match else None
    inferred=(port-16384)//32 if port is not None and 16384<=port<=49120 and (port-16384)%32==0 else None
    if value:
        if not value.isdecimal() or not 0<=int(value)<=1023:raise ValueError('MuMu 기기 번호는 # 표시 뒤의 숫자(0~1023)입니다. 포트 번호를 입력하지 마세요.')
        index=int(value)
        if inferred is not None and inferred!=index:raise ValueError('ADB 포트와 MuMu 기기 번호가 일치하지 않습니다. 다른 기기의 캡처를 사용하지 않았습니다.')
        return index
    if inferred is None:raise ValueError('이 ADB 주소로 MuMu 기기 번호를 확정할 수 없습니다. MuMu의 #0 같은 기기 번호를 입력하세요.')
    return inferred


class _MuMuNativeCapture:
    """Independent ctypes ABI adapter, instantiated in the SDK child only."""
    def __init__(self,root,dll,index,package,size,shared_buffer):
        if sys.platform!='win32' or ctypes.sizeof(ctypes.c_void_p)!=8:raise RuntimeError('MuMu 고속 캡처는 64비트 Windows Python에서 지원됩니다.')
        self.handle=0;self.directories=[];self.buffer=None
        self.size=tuple(size);self.nbytes=self.size[0]*self.size[1]*4
        if not 0<self.nbytes<=64*1024*1024:raise RuntimeError('MuMu 캡처 해상도가 지원 범위를 넘었습니다.')
        try:
            if hasattr(os,'add_dll_directory'):
                for folder in (Path(dll).parent,Path(dll).parent.parent):
                    if folder.is_dir():self.directories.append(os.add_dll_directory(str(folder)))
            self.dll=ctypes.CDLL(dll)
            self.connect=self.dll.nemu_connect;self.connect.argtypes=[ctypes.c_wchar_p,ctypes.c_int];self.connect.restype=ctypes.c_int
            self.disconnect=self.dll.nemu_disconnect;self.disconnect.argtypes=[ctypes.c_int];self.disconnect.restype=None
            self.display=self.dll.nemu_get_display_id;self.display.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int];self.display.restype=ctypes.c_int
            self.grab=self.dll.nemu_capture_display
            self.grab.argtypes=[ctypes.c_int,ctypes.c_uint,ctypes.c_int,ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_ubyte)]
            self.grab.restype=ctypes.c_int
            self.handle=self.connect(root,int(index))
            if self.handle<=0:raise RuntimeError('MuMu SDK가 선택한 기기에 연결하지 못했습니다.')
            self.display_id=self.display(self.handle,package.encode('utf-8'),0)
            if self.display_id<0:raise RuntimeError('MuMu SDK에서 게임 화면을 찾지 못했습니다.')
            width=ctypes.c_int();height=ctypes.c_int()
            if self.grab(self.handle,self.display_id,0,ctypes.byref(width),ctypes.byref(height),None)!=0:raise RuntimeError('MuMu SDK 화면 크기 확인 실패')
            if (width.value,height.value)!=self.size:raise RuntimeError('MuMu SDK와 ADB 화면 크기가 다릅니다. 기기 번호를 확인하세요.')
            self.buffer=(ctypes.c_ubyte*self.nbytes).from_buffer(shared_buffer)
        except Exception:self.close();raise

    def capture(self):
        width=ctypes.c_int(self.size[0]);height=ctypes.c_int(self.size[1])
        if self.grab(self.handle,self.display_id,self.nbytes,ctypes.byref(width),ctypes.byref(height),self.buffer)!=0:raise RuntimeError('MuMu SDK 캡처 실패')
        if (width.value,height.value)!=self.size:raise RuntimeError('MuMu 해상도가 바뀌었습니다.')

    def close(self):
        self.buffer=None
        if self.handle:
            handle=self.handle;self.handle=0
            self.disconnect(handle)
        for directory in self.directories:directory.close()
        self.directories=[]


def _mumu_capture_worker(connection,root,dll,index,package,size,shared_name):
    from multiprocessing import shared_memory
    memory=None;native=None
    try:
        memory=shared_memory.SharedMemory(name=shared_name)
        native=_MuMuNativeCapture(root,dll,index,package,size,memory.buf)
        connection.send(dict(ready=True))
        while True:
            command=connection.recv()
            if command=='close':break
            if command!='capture':raise RuntimeError('지원하지 않는 캡처 요청')
            started=time.perf_counter();native.capture()
            connection.send(dict(ready=True,sdk_ms=(time.perf_counter()-started)*1000))
    except (EOFError,BrokenPipeError):pass
    except Exception as error:
        try:connection.send(dict(error=str(error)))
        except (EOFError,BrokenPipeError,OSError):pass
    finally:
        if native is not None:
            try:native.close()
            except Exception:pass
        if memory is not None:
            try:memory.close()
            except BufferError:pass
        connection.close()


class MuMuCaptureClient:
    def __init__(self,root,dll,index,package,size,cancel=None,timeout=3,worker=None):
        import multiprocessing
        from multiprocessing import shared_memory
        self.cancel=cancel or threading.Event();self.timeout=float(timeout);self.size=tuple(size)
        self.process=None;self.connection=None;self.memory=None;self.closed=False;self.sdk_ms=None;self.last_timings={}
        width,height=self.size
        if not (isinstance(width,int) and isinstance(height,int) and width>0 and height>0 and width*height<=16000000):raise ValueError('MuMu 화면 크기가 올바르지 않습니다.')
        try:
            self.memory=shared_memory.SharedMemory(create=True,size=width*height*4)
            context=multiprocessing.get_context('spawn')
            self.connection,child=context.Pipe()
            self.process=context.Process(target=worker or _mumu_capture_worker,args=(child,root,dll,index,package,self.size,self.memory.name),daemon=True)
            self.process.start();child.close();self._receive()
        except Exception:self.close();raise

    def _receive(self):
        deadline=time.monotonic()+self.timeout
        while True:
            if self.cancel.is_set():raise RuntimeError('MuMu 캡처를 중지했습니다.')
            if self.closed:raise RuntimeError('MuMu 캡처 연결이 닫혔습니다.')
            if self.connection.poll(.04):
                try:result=self.connection.recv()
                except (EOFError,OSError):raise RuntimeError('MuMu 캡처 SDK 프로세스가 종료되었습니다.')
                if result.get('error'):raise RuntimeError(result['error'])
                return result
            if not self.process.is_alive():raise RuntimeError('MuMu 캡처 SDK 프로세스가 종료되었습니다.')
            if time.monotonic()>=deadline:raise RuntimeError('MuMu 고속 캡처 응답 시간 초과 · ADB로 전환합니다.')

    def capture(self):
        if self.closed:raise RuntimeError('MuMu 캡처 연결이 닫혔습니다.')
        try:
            started=time.perf_counter()
            self.connection.send('capture');result=self._receive();received=time.perf_counter();self.sdk_ms=result.get('sdk_ms')
            # Read shared RGBA directly, then detach into owned RGB pixels before
            # another SDK request or close. Avoid two full-size RGBA copies.
            image=Image.frombuffer('RGBA',self.size,self.memory.buf,'raw','RGBA',0,-1).convert('RGB')
            self.last_timings=dict(sdk_ms=self.sdk_ms,ipc_ms=max(0,(received-started)*1000-float(self.sdk_ms or 0)),rgba_ms=(time.perf_counter()-received)*1000)
            return image
        except Exception:self.close();raise

    def close(self):
        if self.closed:return
        self.closed=True
        if self.connection is not None:
            try:self.connection.send('close')
            except (OSError,EOFError,BrokenPipeError):pass
        if self.process is not None and self.process.pid is not None:
            self.process.join(.15)
            if self.process.is_alive():self.process.terminate();self.process.join(.5)
            if not self.process.is_alive():self.process.close()
        if self.connection is not None:self.connection.close();self.connection=None
        if self.memory is not None:
            self.memory.close()
            try:self.memory.unlink()
            except FileNotFoundError:pass
            self.memory=None


def mumu_capture_matches(adb_image,sdk_image):
    if adb_image.size!=sdk_image.size:return False
    left=np.asarray(adb_image.resize((80,45)).convert('RGB'),dtype=np.int16)
    right=np.asarray(sdk_image.resize((80,45)).convert('RGB'),dtype=np.int16)
    if float(left.std())<5 or float(right.std())<5:return False
    difference=np.abs(left-right).mean(axis=2)
    return float(difference.mean())<18 and float((difference<35).mean())>.85


def capture_background_screenshot(executable,serial,settings,cancel=None):
    """Prepare a verified connection, then measure one screenshot, without input."""
    cancel=cancel or threading.Event();device=BackgroundADB(executable,serial,cancel,capture_settings=settings)
    try:
        started=time.perf_counter();device.connect();preparation=(time.perf_counter()-started)*1000
        if cancel.is_set():raise RuntimeError('스크린샷 테스트를 중지했습니다.')
        started=time.perf_counter();frame=device.capture();elapsed=(time.perf_counter()-started)*1000
        timing=dict(device.last_capture_timings)
        screenshot_ms=float(timing.get('read_ms',0))+float(timing.get('normalize_ms',0)) if timing else elapsed
        return dict(version=APP_VERSION,serial=device.serial,adb_path=device.executable,install_root=settings.get('background_mumu_root',''),
                    instance=device.capture_instance if device.capture_instance is not None else settings.get('background_mumu_index',''),
                    sdk_path=device.capture_sdk_path,detected_root=device.capture_install_root,native_size=device.native_size,
                    method='MumuExtras' if device.last_capture_method=='MuMu 고속' else 'ADB',samples=1,capture_ms=round(screenshot_ms,3),
                    preparation_ms=round(preparation,3),elapsed_ms=round(elapsed,3),timings_ms={k:round(v,3) for k,v in timing.items() if isinstance(v,(int,float))},
                    fallback_reason=device.capture_fallback_reason,frame=frame,
                    clock=dict(name='perf_counter',implementation=time.get_clock_info('perf_counter').implementation,resolution_seconds=time.get_clock_info('perf_counter').resolution))
    finally:device.close_capture()


class BackgroundADB:
    """All pixels and input stay inside one selected Android emulator.

    No Win32 mouse/focus calls and no root/helper APK. Continuous DOWN/MOVE/UP
    uses one remote shell, with an UP trap and an additional final release.
    """
    def __init__(self, executable, serial='', cancel=None, capture_settings=None):
        self.executable=str(executable);self.serial=str(serial).strip()
        self.cancel=cancel or threading.Event();self.package=None
        self.input_command='';self.step_seconds=.20;self.native_size=None
        self.capture_settings=dict(capture_settings) if capture_settings is not None else {'background_capture_mode':'adb'}
        self._fast=None;self._fast_attempted=False;self.last_capture_method='ADB';self.last_capture_ms=None;self.capture_fallback_reason='';self.last_capture_timings={}
        self.capture_install_root='';self.capture_sdk_path='';self.capture_instance=None
        self._process=None;self._process_lock=threading.Lock();self._last_focus_check=0.0;self._touch_up=None

    def _run(self, args, data=None, timeout=8, selected=True, allow_cancel=True):
        if allow_cancel and self.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
        command=[self.executable]+(['-s',self.serial] if selected and self.serial else [])+list(args)
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with self._process_lock:self._process=process
        deadline=time.monotonic()+timeout;payload=data
        try:
            while True:
                if (allow_cancel and self.cancel.is_set()) or time.monotonic()>=deadline:
                    process.kill();process.communicate()
                    raise RuntimeError('재료 파밍을 중지했습니다.' if self.cancel.is_set() else 'MuMu ADB 응답 시간 초과')
                try:
                    out,err=process.communicate(payload,timeout=.15)
                    if process.returncode:raise RuntimeError('MuMu ADB 오류: '+err.decode('utf-8','replace').strip()[:180])
                    return out
                except subprocess.TimeoutExpired:payload=None
        finally:
            if process.poll() is None:process.kill();process.communicate()
            with self._process_lock:
                if self._process is process:self._process=None

    def abort(self):
        self.cancel.set();self.close_capture()
        with self._process_lock:process=self._process
        if process is not None:
            try:process.kill()
            except OSError:pass
        up=self._touch_up
        if up:
            try:
                subprocess.Popen([self.executable,'-s',self.serial,'shell',*up],stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except OSError:pass

    def _shell(self,args,**kwargs):return self._run(['shell',*args],**kwargs).decode('utf-8','replace')

    def connect(self):
        if not self.serial:
            text=self._run(['devices'],selected=False).decode('utf-8','replace')
            devices=[line.split()[0] for line in text.splitlines() if len(line.split())>=2 and line.split()[1]=='device'
                     and (line.startswith(('127.0.0.1:','localhost:','emulator-')))]
            if not devices:
                for port in (16384,7555,16416,16448):
                    self._run(['connect',f'127.0.0.1:{port}'],selected=False,timeout=3)
                text=self._run(['devices'],selected=False).decode('utf-8','replace')
                devices=[line.split()[0] for line in text.splitlines() if len(line.split())>=2 and line.split()[1]=='device'
                         and line.startswith(('127.0.0.1:','localhost:','emulator-'))]
            if len(devices)!=1:
                raise RuntimeError('연결된 MuMu를 하나로 지정해 주세요. [파밍 연결 설정]의 기기 주소에 127.0.0.1:16384 등을 입력합니다.' if devices else
                                   'MuMu ADB 연결 실패 · MuMu의 ADB 디버깅/연결 포트를 확인하세요.')
            self.serial=devices[0]
        if not re.fullmatch(r'(?:127\.0\.0\.1|localhost):\d{2,5}|emulator-\d+',self.serial):
            raise RuntimeError('백그라운드 파밍은 로컬 에뮬레이터 주소만 사용합니다.')
        self._run(['connect',self.serial],selected=False,timeout=3) if ':' in self.serial else None
        focus=self._foreground_package()
        if not re.search(r'gungho|puzdra|padzero|padragon',focus,re.I):
            raise RuntimeError('선택한 MuMu에서 퍼즐앤드래곤 제로를 열고 잠입 확인 화면으로 이동해 주세요.')
        self.package=focus
        for command in (['cmd','input'],['input']):
            started=time.monotonic()
            try:help_text=self._shell(command+['help'],timeout=5)
            except RuntimeError:continue
            elapsed=time.monotonic()-started
            if 'motionevent' in help_text.lower():
                self.input_command=' '.join(command)
                # Reserve measured shell overhead and touch-event scheduling.
                self.step_seconds=max(.16,min(.65,elapsed*1.5+.04))
                break
        if not self.input_command:raise RuntimeError('이 MuMu Android 버전에서 연속 터치 입력을 확인하지 못했습니다. Android 12 이상의 MuMu에서 확인해 주세요.')
        frame=self.capture()
        return frame

    def _foreground_package(self):
        text=self._shell(['dumpsys','window','windows'],timeout=5)
        match=re.search(r'mCurrentFocus=.*?\b([A-Za-z][A-Za-z0-9_.]+)/(?:[A-Za-z0-9_.$]+)',text)
        if not match:
            text=self._shell(['dumpsys','activity','activities'],timeout=5)
            match=re.search(r'(?:mResumedActivity|topResumedActivity).*?\b([A-Za-z][A-Za-z0-9_.]+)/',text)
        if not match:raise RuntimeError('Android에서 실행 중인 게임을 확인하지 못했습니다.')
        return match.group(1)

    def guard(self,force=False):
        if self.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
        if force or time.monotonic()-self._last_focus_check>2:
            if self._foreground_package()!=self.package:raise RuntimeError('MuMu 안에서 다른 앱/창이 열려 파밍을 중지했습니다.')
            self._last_focus_check=time.monotonic()

    def close_capture(self):
        fast=self._fast;self._fast=None
        if fast is not None:fast.close()

    def capture(self):
        started=time.perf_counter();self.guard();guard_finished=time.perf_counter()
        mode=self.capture_settings.get('background_capture_mode','auto')
        image=None
        if mode!='adb' and not self._fast_attempted:
            self._fast_attempted=True
            baseline=self._capture_adb_native()
            try:
                root,dll=find_mumu_capture_install(self.executable,self.capture_settings.get('background_mumu_root',''))
                index=mumu_instance_index(self.serial,self.capture_settings.get('background_mumu_index',''))
                self.capture_install_root=root;self.capture_sdk_path=dll;self.capture_instance=index
                self._fast=MuMuCaptureClient(root,dll,index,self.package,baseline.size,self.cancel)
                candidate=self._fast.capture()
                if not mumu_capture_matches(baseline,candidate):raise RuntimeError('MuMu SDK와 선택한 ADB 기기의 화면이 일치하지 않습니다. 기본 ADB를 사용합니다.')
                image=candidate;self.capture_fallback_reason=''
            except Exception as error:
                self.close_capture();self.capture_fallback_reason=str(error);image=baseline
        if self._fast is not None and image is None:
            try:image=self._fast.capture()
            except Exception as error:self.close_capture();self.capture_fallback_reason=str(error)
        if self.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
        if image is None:image=self._capture_adb_native()
        self.last_capture_method='MuMu 고속' if self._fast is not None else 'ADB'
        read_finished=time.perf_counter();frame=self._normalize_capture(image);finished=time.perf_counter()
        self.last_capture_ms=round((finished-started)*1000,3)
        self.last_capture_timings=dict(guard_ms=(guard_finished-started)*1000,read_ms=(read_finished-guard_finished)*1000,normalize_ms=(finished-read_finished)*1000,total_ms=self.last_capture_ms)
        if self._fast is not None and isinstance(getattr(self._fast,'last_timings',None),dict):self.last_capture_timings.update(self._fast.last_timings)
        return frame

    def _capture_adb_native(self):
        import io
        data=self._run(['exec-out','screencap','-p'],timeout=6)
        if len(data)>32*1024*1024 or not data.startswith(b'\x89PNG\r\n\x1a\n'):raise RuntimeError('ADB 화면 캡처가 올바른 PNG가 아닙니다.')
        with Image.open(io.BytesIO(data)) as source:
            if source.width*source.height>16000000:raise RuntimeError('MuMu 화면 해상도가 너무 큽니다.')
            image=source.convert('RGB')
        return image

    def _normalize_capture(self,image):
        if not 1.65<=image.width/image.height<=1.9:raise RuntimeError('MuMu 게임을 가로 화면으로 열어 주세요.')
        size=image.size
        if self.native_size and self.native_size!=size:raise RuntimeError('MuMu 해상도가 바뀌어 파밍을 중지했습니다.')
        self.native_size=size
        frame=Image.new('RGB',(1600,940),'#dedede')
        if image.size==(1600,900):scaled=image
        else:
            import cv2
            if cv2.getNumThreads()>2:cv2.setNumThreads(2)
            interpolation=cv2.INTER_AREA if image.width>=1600 and image.height>=900 else cv2.INTER_CUBIC
            scaled=Image.fromarray(cv2.resize(np.asarray(image),(1600,900),interpolation=interpolation))
        frame.paste(scaled,(0,40))
        return frame

    def point(self,x,y):
        if not self.native_size:raise RuntimeError('ADB 화면 크기를 확인하지 못했습니다.')
        if not (0<=x<1600 and 40<=y<940):raise ValueError('게임 밖 입력을 차단했습니다.')
        w,h=self.native_size
        return min(w-1,round(x*w/1600)),min(h-1,round((y-40)*h/900))

    def tap(self,x,y):
        self.guard(force=True);x,y=self.point(x,y)
        self._shell(['input','tap',str(x),str(y)],timeout=4)

    def swipe(self,x0,y0,x1,y1,duration=400):
        self.guard(force=True);x0,y0=self.point(x0,y0);x1,y1=self.point(x1,y1)
        self._shell(['input','swipe',str(x0),str(y0),str(x1),str(y1),str(int(duration))],timeout=max(5,float(duration)/1000+3))

    def back(self):
        self.guard(force=True);self._shell(['input','keyevent','4'],timeout=4)

    def search_text(self,text):
        self.guard(force=True)
        text=str(text)
        if not text or len(text)>60 or any(ord(c)<32 for c in text):raise ValueError('검색 이름이 올바르지 않습니다.')
        # AOSP input text cannot type Hangul. Use the emulator clipboard bridge.
        if sys.platform!='win32':raise RuntimeError('한글 이름 검색은 Windows MuMu에서만 지원합니다.')
        import ctypes.wintypes as wt
        api=ctypes.windll.user32;kernel=ctypes.windll.kernel32
        api.GetForegroundWindow.restype=wt.HWND
        info=find_emulator_window_info()
        if not info or int(api.GetForegroundWindow())!=int(info['hwnd']):raise RuntimeError('한글 검색 입력을 위해 MuMu 창이 전면에 있어야 합니다.')
        kernel.GlobalAlloc.argtypes=(wt.UINT,ctypes.c_size_t);kernel.GlobalAlloc.restype=ctypes.c_void_p
        kernel.GlobalLock.argtypes=(ctypes.c_void_p,);kernel.GlobalLock.restype=ctypes.c_void_p
        kernel.GlobalUnlock.argtypes=(ctypes.c_void_p,)
        api.SetClipboardData.argtypes=(wt.UINT,ctypes.c_void_p);api.SetClipboardData.restype=ctypes.c_void_p
        data=(text+'\0').encode('utf-16-le');handle=kernel.GlobalAlloc(0x42,len(data))
        if not handle:raise RuntimeError('검색어 클립보드 할당 실패')
        ptr=kernel.GlobalLock(handle)
        if not ptr:raise RuntimeError('검색어 클립보드 잠금 실패')
        ctypes.memmove(ptr,data,len(data));kernel.GlobalUnlock(handle)
        if not api.OpenClipboard(None):raise RuntimeError('검색어 클립보드 사용 실패')
        try:
            api.EmptyClipboard()
            if not api.SetClipboardData(13,handle):raise RuntimeError('검색어 복사 실패')
        finally:api.CloseClipboard()
        self.guard(force=True)
        # MuMu handles Ctrl+V and transfers Unicode to the Android edit field.
        if int(api.GetForegroundWindow())!=int(info['hwnd']):raise RuntimeError('MuMu가 전면에서 벗어나 이름 입력을 중지했습니다.')
        try:
            api.keybd_event(0x11,0,0,0);api.keybd_event(0x41,0,0,0);api.keybd_event(0x41,0,2,0);api.keybd_event(0x11,0,2,0)
            self.guard(force=True)
            if int(api.GetForegroundWindow())!=int(info['hwnd']):raise RuntimeError('MuMu가 전면에서 벗어나 이름 입력을 중지했습니다.')
            api.keybd_event(0x11,0,0,0);api.keybd_event(0x56,0,0,0)
        finally:api.keybd_event(0x56,0,2,0);api.keybd_event(0x11,0,2,0)
        self._shell(['input','keyevent','66'],timeout=4)

    def move_cap(self,seconds):return max(1,min(65,int((max(1,float(seconds or 5))-1)/self.step_seconds)))

    def drag(self,rect,path,seconds):
        if not path or any(type(i) is not int or not 0<=i<30 for i in path) or any(b not in neighbors(a) for a,b in zip(path,path[1:])):
            raise ValueError('유효하지 않은 ADB 경로입니다.')
        if len(path)-1>self.move_cap(seconds):raise RuntimeError('제한시간부족 · 백그라운드 입력 시간 상한을 넘었습니다.')
        self.guard(force=True)
        x,y,w,h=rect
        points=[self.point(x+(i%6+.5)*w/6,y+(i//6+.5)*h/5) for i in path]
        prefix=self.input_command+' touchscreen motionevent '
        if self.input_command not in ('cmd input','input'):raise RuntimeError('검증한 ADB 연속 입력 방식이 없습니다.')
        last=points[-1];up=prefix+f'UP {last[0]} {last[1]}'
        lines=['set -e',"trap '"+up+"' EXIT HUP INT TERM",prefix+f'DOWN {points[0][0]} {points[0][1]}']
        lines += [prefix+f'MOVE {px} {py}' for px,py in points[1:]]
        lines += [up,"trap - EXIT HUP INT TERM","echo PAD0_INPUT_OK"]
        self._touch_up=self.input_command.split()+['touchscreen','motionevent','UP',str(last[0]),str(last[1])]
        started=time.monotonic()
        try:
            out=self._run(['shell','sh'],data=('\n'.join(lines)+'\n').encode('ascii'),timeout=max(3,float(seconds or 5)-.25))
            if b'PAD0_INPUT_OK' not in out:raise RuntimeError('ADB 연속 입력 완료를 확인하지 못했습니다.')
            if len(path)>3:self.step_seconds=max(self.step_seconds,(time.monotonic()-started)/(len(path)-1)*1.2)
        finally:
            try:self._run(['shell',*self._touch_up],timeout=2,allow_cancel=False)
            except Exception:pass
            self._touch_up=None


def setup_adb_devices(text):
    rows=[]
    for line in text.splitlines()[:100]:
        parts=line.strip().split()
        if len(parts)<2 or not re.fullmatch(r'(?:127\.0\.0\.1|localhost):\d{2,5}|emulator-\d+',parts[0]):continue
        if ':' in parts[0] and not 1<=int(parts[0].rsplit(':',1)[1])<=65535:continue
        if any(r['serial']==parts[0] for r in rows):continue
        rows.append(dict(serial=parts[0],state=parts[1],description=' '.join(parts[2:])))
    return rows


class BackgroundSetupProbe:
    """Read-only setup checks and local connection attempts. Never touch the game."""
    def __init__(self,path='',cancel=None,capture_settings=None):
        self.cancel=cancel or threading.Event();self.device=None;self.path=path;self.capture_settings=capture_settings

    def locate(self):
        candidate=resolve_background_adb_path(self.path) if self.path.strip() else None
        if candidate is not None and candidate.is_dir():
            choices=[candidate/'adb.exe']+mumu_versioned_adb(candidate)+[candidate/relative for relative in ('shell/adb.exe','nx_main/adb.exe','nemu/vmonitor/bin/adb_server.exe')]
            candidate=next((file for file in choices if file.is_file()),None)
            if candidate is None:raise RuntimeError('선택한 폴더에서 ADB를 찾지 못했습니다. MuMuPlayer, nx_device, 설치 버전 또는 shell 폴더를 선택하세요.')
        if candidate is not None:
            if not candidate.is_file():raise RuntimeError('입력한 파일이 없습니다. 자동 찾기 또는 MuMu 폴더 선택을 눌러주세요.')
            self.path=str(candidate.resolve())
        else:self.path=find_background_adb()
        self.device=BackgroundADB(self.path,cancel=self.cancel,capture_settings=self.capture_settings)
        version=self.device._run(['version'],selected=False,timeout=5).decode('utf-8','replace')
        if 'android debug bridge' not in version.lower():raise RuntimeError('선택한 파일을 ADB로 확인하지 못했습니다. Google 공식 도구 또는 MuMu의 adb.exe를 선택하세요.')
        return dict(path=self.path,version=version.splitlines()[0])

    def discover(self,reconnect='',manual='',progress=None):
        if self.device is None:raise RuntimeError('먼저 1단계에서 ADB 파일을 확인하세요.')
        device=self.device
        if reconnect:
            if not setup_adb_devices(reconnect+' offline'):raise RuntimeError('로컬 MuMu 기기만 다시 연결할 수 있습니다.')
            device.serial=reconnect
            try:device._run(['reconnect'],timeout=4)
            except RuntimeError:
                if self.cancel.is_set():raise
            finally:device.serial=''
        text=device._run(['devices','-l'],selected=False,timeout=8).decode('utf-8','replace')
        rows=setup_adb_devices(text);attempts=[]
        if manual:
            if not setup_adb_devices(manual+' device'):raise RuntimeError('주소는 emulator-5554 또는 127.0.0.1:포트 형식으로 입력하세요.')
            addresses=[manual] if ':' in manual else []
        elif not any(r['state']=='device' for r in rows):
            ports=[]
            for r in rows:
                if r['serial'].startswith('emulator-'):
                    port=int(r['serial'].split('-')[-1])+1
                    if 1<=port<=65535:ports.append(port)
            ports.extend([16384,7555,16416,16448])
            addresses=[f'127.0.0.1:{port}' for port in dict.fromkeys(ports)]
        else:addresses=[]
        for i,address in enumerate(addresses):
            if self.cancel.is_set():raise RuntimeError('설정을 취소했습니다.')
            if progress:progress(f'기기 연결 확인 {i+1}/{len(addresses)} · {address}')
            try:result=device._run(['connect',address],selected=False,timeout=2).decode('utf-8','replace').strip()
            except RuntimeError as exc:
                if self.cancel.is_set():raise
                result=str(exc)
            attempts.append((address,result[:180]))
            if re.search(r'(?:already )?connected to ',result,re.I):break
        if addresses or reconnect:
            text=device._run(['devices','-l'],selected=False,timeout=8).decode('utf-8','replace')
            rows=setup_adb_devices(text)
        return dict(rows=rows,attempts=attempts)

    def preview(self,serial):
        if self.device is None:raise RuntimeError('먼저 ADB 파일을 확인하세요.')
        if not setup_adb_devices(serial+' device'):raise RuntimeError('로컬 MuMu 주소를 선택하세요.')
        rows=setup_adb_devices(self.device._run(['devices','-l'],selected=False,timeout=8).decode('utf-8','replace'))
        match=next((r for r in rows if r['serial']==serial),None)
        if not match or match['state']!='device':raise RuntimeError('선택한 기기가 아직 연결되지 않았습니다. 2단계에서 다시 확인하세요.')
        self.device.close_capture();self.device._fast_attempted=False
        self.device.serial=serial
        self.device.native_size=None;self.device.package=None;self.device.input_command='';self.device._last_focus_check=0.0
        frame=self.device.connect()
        return dict(frame=frame,path=self.path,serial=serial,capture_method=self.device.last_capture_method,capture_ms=self.device.last_capture_ms,fallback_reason=self.device.capture_fallback_reason)

    def restart_service(self):
        if self.device is None:raise RuntimeError('먼저 ADB 파일을 확인하세요.')
        self.device._run(['kill-server'],selected=False,timeout=5)
        self.device._run(['start-server'],selected=False,timeout=10)
        return self.discover()

    def cancel_job(self):
        self.cancel.set()
        if self.device is not None:self.device.abort()



class BackgroundSetupWizard:
    """Step-by-step local setup; worker threads never access Tk variables."""
    def __init__(self,app,on_saved=None,path='',serial=''):
        self.app=app;self.on_saved=on_saved;self.closed=False;self.busy=False;self.step=0
        self.events=queue.SimpleQueue();self.probe=None;self.verified_path=None;self.preview_key=None
        self.rows=[];self.attempts=[];self.frame=None;self.photo=None;self.saved=False
        self.win=tk.Toplevel(app.root);self.win.title('MuMu 파밍 연결 설정 마법사');self.win.geometry('790x780')
        self.win.protocol('WM_DELETE_WINDOW',self.close)
        self.path=tk.StringVar(value=path);self.serial=tk.StringVar(value=serial)
        outer=ttk.Frame(self.win,padding=18);outer.pack(fill='both',expand=True)
        heading=ttk.Frame(outer);heading.pack(fill='x')
        ttk.Label(heading,text='MuMu 연결을 함께 설정합니다',font=('Segoe UI',16,'bold')).pack(side='left')
        ttk.Button(heading,text='그림으로 보는 ADB·포트 설정',command=self.app.open_community_adb_help).pack(side='right')
        self.steps=ttk.Label(outer,text='',foreground='#245f73');self.steps.pack(anchor='w',pady=(8,12))
        ttk.Separator(outer).pack(fill='x')
        self.body=ttk.Frame(outer);self.body.pack(fill='both',expand=True,pady=12)
        self.status=ScrolledText(outer,height=5,wrap='word',font=('Segoe UI',10),padx=8,pady=8)
        self.status.pack(fill='x');self.status.configure(state='disabled')
        nav=ttk.Frame(outer);nav.pack(fill='x',pady=(12,0))
        self.back=ttk.Button(nav,text='이전',command=self.go_back);self.back.pack(side='left')
        self.next=ttk.Button(nav,text='다음',command=self.go_next);self.next.pack(side='right')
        ttk.Button(nav,text='취소',command=self.close).pack(side='right',padx=8)
        self.job_buttons=[];self.path.trace_add('write',self.invalidate_path);self.serial.trace_add('write',self.invalidate_preview)
        self.render();self.app._party_bring_forward(self.win)
        self.after=self.win.after(100,self.poll)
        self.initial_after=self.win.after(180,self.locate)

    def tell(self,text):
        self.status.configure(state='normal');self.status.delete('1.0','end');self.status.insert('1.0',text);self.status.configure(state='disabled')

    def invalidate_path(self,*_):
        self.verified_path=None;self.preview_key=None;self.frame=None
        if hasattr(self,'next'):self.update_navigation()

    def invalidate_preview(self,*_):
        self.preview_key=None;self.frame=None
        if hasattr(self,'next'):self.update_navigation()

    def button(self,parent,text,command):
        button=ttk.Button(parent,text=text,command=command);button.pack(side='left',padx=(0,8));self.job_buttons.append(button)
        return button

    def render(self):
        for child in self.body.winfo_children():child.destroy()
        self.job_buttons=[]
        labels=['① MuMu 찾기','② 기기 연결','③ 화면 확인','④ 저장']
        self.steps.configure(text='   →   '.join(('['+s+']') if i==self.step else s for i,s in enumerate(labels)))
        if self.saved:
            ttk.Label(self.body,text='✓ 연결 설정을 저장했습니다',font=('Segoe UI',14,'bold')).pack(anchor='w',pady=12)
            ttk.Label(self.body,text='이제 잠입 확인 화면에서 파티 정보를 읽을 수 있습니다.\n이 창에서 파밍이나 게임 터치를 시작하지는 않습니다.',wraplength=720).pack(anchor='w')
            ttk.Button(self.body,text='파티 설정 열기',command=self.open_party).pack(anchor='w',pady=16)
        elif self.step==0:
            ttk.Label(self.body,text='1. MuMu와 게임을 켜주세요',font=('Segoe UI',13,'bold')).pack(anchor='w')
            ttk.Label(self.body,text='설치 위치를 자동으로 찾습니다. 주소나 명령어를 먼저 입력할 필요는 없습니다.',wraplength=720).pack(anchor='w',pady=8)
            ttk.Label(self.body,text='찾은 ADB 파일 / 설치 폴더').pack(anchor='w',pady=(12,4))
            self.path_entry=ttk.Entry(self.body,textvariable=self.path);self.path_entry.pack(fill='x')
            actions=ttk.Frame(self.body);actions.pack(fill='x',pady=12)
            self.button(actions,'자동으로 찾기',self.auto_locate)
            self.button(actions,'MuMu 폴더 선택',self.choose_folder)
            self.button(actions,'파일 직접 선택',self.choose_file)
            ttk.Label(self.body,text='MuMuPlayer 또는 nx_device 폴더를 선택하면 설치된 버전을 찾습니다.\n예: C:\\Program Files\\Netease\\MuMuPlayer\\nx_device\\15.0\\shell',wraplength=720).pack(anchor='w',pady=8)
            ttk.Label(self.body,text='파일을 확인하면 다음 단계로 넘어갈 수 있습니다.',foreground='#526776').pack(anchor='w',pady=8)
        elif self.step==1:
            ttk.Label(self.body,text='2. 사용할 MuMu를 선택해주세요',font=('Segoe UI',13,'bold')).pack(anchor='w')
            ttk.Label(self.body,text='한 대만 연결되면 자동 선택합니다. offline은 아직 연결되지 않은 상태입니다.',wraplength=720).pack(anchor='w',pady=8)
            self.tree=ttk.Treeview(self.body,columns=('serial','state'),show='headings',height=4,selectmode='browse')
            self.tree.heading('serial',text='발견한 기기 주소');self.tree.heading('state',text='연결 상태')
            self.tree.column('serial',width=390);self.tree.column('state',width=240);self.tree.pack(fill='x')
            for row in self.rows:
                names={'device':'연결됨','offline':'연결 안 됨 · offline','unauthorized':'기기에서 허용 필요'}
                self.tree.insert('', 'end',iid=row['serial'],values=(row['serial'],names.get(row['state'],row['state'])))
            if self.tree.exists(self.serial.get()):self.tree.selection_set(self.serial.get())
            self.tree.bind('<<TreeviewSelect>>',self.select_device)
            actions=ttk.Frame(self.body);actions.pack(fill='x',pady=10)
            self.button(actions,'기기 다시 찾기',self.discover)
            self.button(actions,'선택 기기 재연결',self.reconnect)
            self.button(actions,'연결 서비스 재시작',self.restart_service)
            ttk.Label(self.body,text='목록에 없을 때만 주소 직접 입력 (선택 사항)').pack(anchor='w',pady=(4,2))
            row=ttk.Frame(self.body);row.pack(fill='x');self.serial_entry=ttk.Entry(row,textvariable=self.serial)
            self.serial_entry.pack(side='left',fill='x',expand=True)
            self.button(row,'이 주소로 연결',self.manual_connect)
            ttk.Label(self.body,text='MuMu 설정 창 아래쪽 ‘ADB 포트’를 펼쳐 표시된 숫자를 확인하세요.\n5555,16384가 함께 보이면 127.0.0.1:16384를 입력하고 ‘이 주소로 연결’로 확인할 수 있습니다.\n‘연결 서비스 재시작’은 다른 ADB 연결도 잠시 끊습니다. 자동으로 실행하지 않습니다.',wraplength=720).pack(anchor='w',pady=12)
        elif self.step==2:
            ttk.Label(self.body,text='3. 이 화면이 사용하실 게임인가요?',font=('Segoe UI',13,'bold')).pack(anchor='w')
            ttk.Label(self.body,text='선택한 기기의 화면을 읽습니다. 캐릭터를 누르거나 게임을 움직이지 않습니다.',wraplength=720).pack(anchor='w',pady=6)
            self.preview_label=ttk.Label(self.body,text='MuMu에서 게임을 켠 뒤 화면 확인을 눌러주세요.',anchor='center')
            self.preview_label.pack(fill='both',expand=True)
            if self.frame is not None:
                from PIL import ImageTk
                self.photo=ImageTk.PhotoImage(self.frame.resize((480,282),Image.Resampling.LANCZOS))
                self.preview_label.configure(image=self.photo,text='')
            ttk.Label(self.body,text='선택 기기: '+self.serial.get()).pack(anchor='w')
            row=ttk.Frame(self.body);row.pack(fill='x',pady=8);self.button(row,'화면 다시 확인',self.preview)
            self.button(row,'스크린샷 테스트',lambda:self.app.show_background_capture_test(dict(self.app.user_settings,background_adb_path=self.path.get(),background_adb_serial=self.serial.get()),self.body))
        else:
            ttk.Label(self.body,text='4. 확인한 연결을 저장합니다',font=('Segoe UI',13,'bold')).pack(anchor='w',pady=8)
            ttk.Label(self.body,text='MuMu ADB 파일',foreground='#526776').pack(anchor='w',pady=(16,4))
            ttk.Label(self.body,text=self.path.get(),wraplength=720).pack(anchor='w')
            ttk.Label(self.body,text='사용할 기기 주소',foreground='#526776').pack(anchor='w',pady=(16,4))
            ttk.Label(self.body,text=self.serial.get(),font=('Segoe UI',12,'bold')).pack(anchor='w')
            ttk.Label(self.body,text='ADB 실행과 게임 화면 읽기를 확인했습니다.\n아래 ‘설정 저장’을 누를 때만 프로그램 설정에 반영합니다.\n최소화해서 파밍하려면 MuMu의 백그라운드 일시정지를 꺼주세요.',wraplength=720).pack(anchor='w',pady=20)
        self.update_navigation()

    def update_navigation(self):
        self.back.configure(state='disabled' if self.busy or self.step==0 or self.saved else 'normal')
        key=(self.path.get().strip(),self.serial.get().strip())
        if self.saved:text='닫기';ready=True
        elif self.step==0:text='다음: 기기 연결' if self.verified_path==key[0] else '이 경로 확인';ready=True
        elif self.step==1:
            text='다음: 화면 확인';ready=any(r['serial']==key[1] and r['state']=='device' for r in self.rows)
        elif self.step==2:text='이 화면이 맞아요';ready=self.preview_key==key
        else:text='설정 저장';ready=self.preview_key==key
        self.next.configure(text=text,state='normal' if ready and not self.busy else 'disabled')
        for button in self.job_buttons:button.configure(state='disabled' if self.busy else 'normal')
        for name in ('path_entry','serial_entry'):
            entry=getattr(self,name,None)
            if entry is not None and entry.winfo_exists():entry.configure(state='disabled' if self.busy else 'normal')
        tree=getattr(self,'tree',None)
        if tree is not None and tree.winfo_exists():tree.configure(selectmode='none' if self.busy else 'browse')

    def job(self,kind,work):
        if self.busy or self.closed:return
        self.busy=True;self.update_navigation()
        def worker():
            try:self.events.put(('result',kind,work()))
            except Exception as exc:self.events.put(('error',kind,str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def locate(self):
        if getattr(self,'initial_after',None):
            self.win.after_cancel(self.initial_after);self.initial_after=None
        if self.closed or self.busy or self.step!=0:return
        if self.probe is not None:self.probe.cancel_job()
        path=self.path.get();self.probe=BackgroundSetupProbe(path,capture_settings=dict(self.app.user_settings))
        self.tell('MuMu 설치 위치와 ADB 파일을 확인하는 중입니다…')
        self.job('locate',self.probe.locate)

    def auto_locate(self):
        if self.busy:return
        self.path.set('');self.locate()

    def choose_folder(self):
        from tkinter import filedialog
        folder=filedialog.askdirectory(parent=self.win,title='MuMuPlayer 또는 nx_device 폴더 선택')
        if folder:self.path.set(folder);self.locate()

    def choose_file(self):
        from tkinter import filedialog
        file=filedialog.askopenfilename(parent=self.win,title='MuMu의 adb.exe 선택',filetypes=[('ADB','*.exe'),('모든 파일','*')])
        if file:self.path.set(file);self.locate()

    def discover(self,reconnect='',manual=''):
        if self.probe is None or self.verified_path!=self.path.get().strip():return
        self.preview_key=None;self.frame=None
        self.tell('실행 중인 MuMu와 연결 상태를 확인합니다. 잠시 기다려주세요…')
        probe=self.probe
        self.job('discover',lambda:probe.discover(reconnect=reconnect,manual=manual,progress=lambda s:self.events.put(('progress',s))))

    def reconnect(self):
        selected=self.serial.get().strip()
        if not selected:self.tell('목록에서 연결할 기기를 먼저 선택해주세요.');return
        self.discover(reconnect=selected)

    def manual_connect(self):
        selected=self.serial.get().strip()
        if not selected:self.tell('MuMu에 표시된 실제 주소를 입력해주세요.');return
        self.discover(manual=selected,reconnect=selected if selected.startswith('emulator-') else '')

    def restart_service(self):
        if self.probe is None:return
        self.preview_key=None;self.frame=None;probe=self.probe
        self.tell('ADB 연결 서비스를 다시 시작합니다. 다른 ADB 연결도 잠시 끊깁니다…')
        self.job('discover',probe.restart_service)

    def select_device(self,event=None):
        if self.busy:return
        selection=self.tree.selection()
        if selection and self.serial.get()!=selection[0]:self.serial.set(selection[0])
        self.update_navigation()

    def preview(self):
        selected=self.serial.get().strip();probe=self.probe
        self.tell('선택한 MuMu에서 게임 화면과 입력 지원 여부를 확인합니다. 게임을 터치하지 않습니다…')
        self.job('preview',lambda:probe.preview(selected))

    def go_back(self):
        if not self.busy and self.step>0:self.step-=1;self.render()

    def go_next(self):
        if self.busy:return
        if self.saved:self.close();return
        key=(self.path.get().strip(),self.serial.get().strip())
        if self.step==0:
            if self.verified_path!=key[0]:self.locate();return
            self.step=1;self.render();self.discover()
        elif self.step==1:
            if not any(r['serial']==key[1] and r['state']=='device' for r in self.rows):return
            self.step=2;self.render();self.preview()
        elif self.step==2:
            if self.preview_key!=key:return
            self.step=3;self.render();self.tell('연결 정보를 확인한 뒤 설정 저장을 눌러주세요.')
        else:
            if self.preview_key!=key:return
            if getattr(self.app,'_background_running',False):self.tell('파밍을 중지한 뒤 연결 설정을 저장해주세요.');return
            self.app.user_settings.update(background_adb_path=portable_background_adb_path(key[0]),background_adb_serial=key[1])
            self.app.save_user_settings()
            saved=_load_json_dict(self.app.data_paths['settings'])
            if any(saved.get(k)!=v for k,v in zip(('background_adb_path','background_adb_serial'),(portable_background_adb_path(key[0]),key[1]))):
                self.tell('설정 파일 저장을 확인하지 못했습니다. 저장 폴더 권한을 확인해주세요.');return
            if self.on_saved:self.on_saved(portable_background_adb_path(key[0]),key[1])
            self.saved=True;self.render();self.tell('설정 완료 · '+key[1]+'\n다음부터 저장한 연결 정보를 사용합니다.')
            self.app.set_status('MuMu 설정 마법사 완료 · 게임 화면 읽기 확인 · '+key[1])

    def poll(self):
        if self.closed:return
        try:
            while True:
                try:event=self.events.get_nowait()
                except queue.Empty:break
                if event[0]=='progress':self.tell(event[1]);continue
                self.busy=False
                if event[0]=='error':
                    self.preview_key=None;self.frame=None
                    if event[1]=='locate':self.verified_path=None
                    self.tell('아직 완료되지 않았습니다.\n'+event[2]+'\nMuMu와 게임을 켠 상태에서 해당 단계를 다시 확인해주세요.')
                    self.render();continue
                _,kind,result=event
                if kind=='locate':
                    self.path.set(result['path']);self.verified_path=result['path']
                    self.tell('✓ ADB 파일을 확인했습니다.\n'+result['path']+'\n‘다음: 기기 연결’을 눌러주세요.')
                elif kind=='discover':
                    self.rows=result['rows'];self.attempts=result['attempts']
                    online=[r['serial'] for r in self.rows if r['state']=='device']
                    if len(online)==1:self.serial.set(online[0])
                    elif len(self.rows)==1:self.serial.set(self.rows[0]['serial'])
                    self.tell('✓ 연결된 기기를 선택하고 다음을 누르세요.\n목록은 먼저 연결된 주소입니다. MuMu에 표시된 다른 포트는 주소 입력 후 ‘이 주소로 연결’로 확인할 수 있습니다.' if online else
                        '아직 연결되지 않았습니다.\nMuMu 설정 아래쪽 ADB 포트를 확인하세요. offline 기기는 선택 후 ‘선택 기기 재연결’을 누르세요.\n계속 안 되면 MuMu를 완전히 종료·재실행한 뒤 ‘기기 다시 찾기’를 누르세요.')
                elif kind=='preview':
                    self.frame=result['frame'];self.preview_key=(result['path'],result['serial'])
                    self.tell('✓ 게임 화면을 읽었습니다. '+str(result.get('capture_method','ADB'))+' / '+str(result.get('capture_ms','?'))+' ms\n'+str(result.get('fallback_reason',''))+'\n화면이 맞으면 ‘이 화면이 맞아요’를 눌러주세요.')
                self.render()
        finally:
            if not self.closed:self.after=self.win.after(100,self.poll)

    def open_party(self):
        self.close();self.app.show_party_settings()

    def close(self):
        if self.closed:return
        self.closed=True
        if self.probe is not None:self.probe.cancel_job()
        for name in ('after','initial_after'):
            handle=getattr(self,name,None)
            if handle:
                try:self.win.after_cancel(handle)
                except tk.TclError:pass
        self.win.destroy()



def background_heading_views(header):
    """The game page title is left-aligned, unlike the centred modal title."""
    import cv2
    rgb=np.asarray(header.convert('RGB'),dtype=np.int16)
    mask=((rgb.max(axis=2)<215)&(rgb.max(axis=2)-rgb.min(axis=2)<65)).astype('uint8')
    mask[:4]=0;mask[-4:]=0;mask[:,:3]=0;mask[:,-3:]=0
    n,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    boxes=[v for v in stats[1:] if 2<=v[2]<header.width*.8 and 3<=v[3]<header.height*.75 and 4<=v[4]<header.width*header.height*.15]
    if not boxes:return None,[]
    x0=min(v[0] for v in boxes);y0=min(v[1] for v in boxes)
    x1=max(v[0]+v[2] for v in boxes);y1=max(v[1]+v[3] for v in boxes)
    if y1-y0<6 or y1-y0>header.height*.75:return None,[]
    tight=header.crop((max(0,x0-8),max(0,y0-8),min(header.width,x1+8),min(header.height,y1+8)))
    return material_title_fingerprint(tight),material_title_line_views(tight)[1]


def background_board(frame):
    rect=(882,255,692,572)
    crop=frame.crop((rect[0],rect[1],rect[0]+rect[2],rect[1]+rect[3]))
    board,_=classify_board(crop)
    if not _live_board_grid_is_visible(crop,board):return None
    spikes=[flag and code not in ('J','S') for flag,code in zip(detect_spike_cells(crop),board)]
    return dict(board=board,spikes=spikes,rect=rect,crop=crop)


def background_entry_card_rects(frame):
    """Resolve each card separately; the entry row uses a narrower pitch than battle."""
    rgb=np.asarray(frame.convert('RGB'));band=rgb[240:380,70:930].astype('int16')
    fallback=[(94+136*i,228,226+136*i,450) for i in range(6)]
    if band.shape[:2]!=(140,860):return fallback
    white=(band.min(axis=2)>210)&(band.max(axis=2)-band.min(axis=2)<45)
    ink=(1-white.mean(axis=0))>.25
    # Close only tiny holes inside artwork, never the gap between cards.
    for i in range(1,len(ink)-2):
        if ink[i-1] and ink[i+1]:ink[i]=True
    edges=np.diff(np.r_[False,ink,False].astype('int8'));runs=list(zip(np.where(edges==1)[0]+70,np.where(edges==-1)[0]+70))
    result=[]
    for slot in range(6):
        center=158+136*slot
        candidates=[(x0,x1) for x0,x1 in runs if x0<=center<x1 and 105<=x1-x0<=140]
        if len(candidates)!=1:return fallback
        x0,x1=candidates[0];result.append((max(0,int(x0)-3),228,min(frame.width,int(x1)+3),450))
    if any(result[i][2]>=result[i+1][0] for i in range(5)):return fallback
    return result


def show_game_overlay_without_focus(window):
    """An independent top-level remains visible while the main Tk window is minimized."""
    if sys.platform!='win32':
        if window.state()=='withdrawn':window.deiconify()
        window.attributes('-topmost',True);window.lift();return
    from ctypes import wintypes
    api=ctypes.windll.user32
    api.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT);api.GetAncestor.restype=wintypes.HWND
    hwnd=api.GetAncestor(window.winfo_id(),2) or window.winfo_id()
    get_long=getattr(api,'GetWindowLongPtrW',api.GetWindowLongW)
    set_long=getattr(api,'SetWindowLongPtrW',api.SetWindowLongW)
    get_long.argtypes=(wintypes.HWND,ctypes.c_int);get_long.restype=ctypes.c_ssize_t
    set_long.argtypes=(wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t);set_long.restype=ctypes.c_ssize_t
    # No owner: iconifying the main window must not suppress the farming buttons.
    set_long(hwnd,-8,0)
    set_long(hwnd,-20,get_long(hwnd,-20)|0x08000000|0x00000080)
    if window.state()=='withdrawn':window.deiconify()
    hwnd=api.GetAncestor(window.winfo_id(),2) or window.winfo_id()
    set_long(hwnd,-8,0)
    set_long(hwnd,-20,get_long(hwnd,-20)|0x08000000|0x00000080)
    api.SetWindowPos.argtypes=(wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT)
    api.SetWindowPos.restype=wintypes.BOOL
    if not api.SetWindowPos(hwnd,wintypes.HWND(-1),0,0,0,0,0x53):raise OSError('오버레이 표시 실패')


def background_portrait(frame,slot,entry=False):
    if entry:
        x0,_,x1,_=background_entry_card_rects(frame)[slot]
        return np.asarray(frame.crop((x0+18,260,x1-18,350)).resize((16,20),Image.Resampling.BILINEAR),dtype=np.int16)
    x=.018+.0875*slot;y=.700
    # Compare portrait centres; omit border, elements, level and cooldown overlays.
    crop=frame.crop((round((x+.016)*1600),round((y+.032)*940),round((x+.063)*1600),round((y+.126)*940)))
    return np.asarray(crop.resize((16,20),Image.Resampling.BILINEAR),dtype=np.int16)


def background_ready_slots(frame):
    import cv2
    rgb=np.asarray(frame.convert('RGB'))
    result=[]
    for slot in range(6):
        cx=round((.057+.0875*slot)*1600)
        tile=rgb[round(.670*940):round(.703*940),cx-25:cx+26]
        orange=(tile[...,0]>185)&(tile[...,1]>100)&(tile[...,1]<235)&(tile[...,2]<100)
        white=tile.min(axis=2)>225
        # A ready skill has the orange-bordered WHITE triangle above its card.
        n,_,stats,_=cv2.connectedComponentsWithStats(white.astype('uint8'),8)
        triangle=any(9<=w<=45 and 4<=h<=20 and 20<=area<=450 and .20<=area/(w*h)<=.80
                     for x,y,w,h,area in stats[1:])
        if orange.sum()>=50 and triangle:result.append(slot)
    return result


def background_skill_effects(text):
    """Conservative active-skill categories; never infer skills from a portrait."""
    t=_compact_ocr_text(text).lower();effects=[]
    all_orbs=any(s in t for s in ('모든드롭','전체드롭','전드롭','全ドロップ'))
    if all_orbs and any(s in t for s in ('변화','변환','바꿉','変化')):
        colors=sum(any(word in t for word in words) for words in (('불','화속성','火'),('물','수속성','水'),('나무','목속성','木'),('빛','광속성','光'),('어둠','암속성','闇')))
        if colors==5:effects.append('five_board')
    if all_orbs and any(s in t for s in ('파괴','破壊')):effects.append('refresh')
    if any(s in t for s in ('바인드','바인딩','각성무효','バインド','覚醒無効')) and any(s in t for s in ('회복','해제','回復')):effects.append('cleanse')
    if any(s in t for s in ('조작시간','操作時間')) and any(s in t for s in ('연장','늘','延長')):effects.append('time')
    if '콤보' in t and any(s in t for s in ('가산','추가')) or 'コンボ加算' in t:effects.append('bonus')
    if any(s in t for s in ('hp를','체력을','hpを')) and any(s in t for s in ('회복','回復')):effects.append('heal')
    return effects


def background_skill_choice(profiles,ready,shortage=False,short_time=False,combo_short=False,bound=False,low_hp=False,used=()):
    priorities=[]
    if bound:priorities.append('cleanse')
    if low_hp:priorities+=['heal','five_board']
    if shortage:priorities+=['five_board','refresh']
    if short_time:priorities.append('time')
    if combo_short:priorities+=['bonus','five_board','refresh']
    for effect in priorities:
        for slot in ready:
            if slot not in used and effect in profiles[slot].get('effects',[]):return slot
    return None


MONSTER_CATALOG_B85 = 'c-q{(TW?!QmL>dGhy?P@)5UY+rGb90hkB^@@1arCT{BbD-Bp9C>KWi0e1Jq!HYG}yWRntQT})Zjow8|(vM8Iff&MEpsmnP;{e>?g4k<D&=bYGag0ixKMs=oB>CEI_+_5iful0X_`pcjG{m=jO`=9>)r~mQSzy7bk{Qa+f_1}N@Z@>TZKmCut`=>ws+pqrDzyH7g{O8{#f0Z?D!^!^i?598f{;z-j!|(t1@BbP74QA3z{TCzoUyL;R3;*_~KmO&fzyI@Je){|W=l}ln$AAB~|N8qs|Md69v!DKmez$-8pT9#tew{zQ&cD9SA6@73*ZCs)du0F7X4*tQ{ky-SfAoL-_rLxg{WE^LnLft9!N2_BU;owr-Hq$qx9fc8I)8RO{r+y{_4U+rul%L=D&L)3zn-2q{?6=9EnH9K@Ne{ge)liG|KlJ2_}~9B_!pZ#x}IJ|f8n40^rwIQ=Rf_QKmPRh*5F_KU;c#ty1R4WUKZTFZ$CZ!>OcPFufO~2zvF+AzyA4mfBeh8{_d~8|L1>1|GNL}uMhuL<Zm_lg;v5{T=Oc$#QoX%{pq1w+x0eE3H0ydHk*G(|J#ti#an&uZO&8wy@P*^PsN}9zh|Ql*Q7q&-1&9Ba-G{&`+P0-^X)ZT(Vy?@h3{7zy|o<v)7&fPX8%?9%cr}_h1$pL<r@9@`Pd(`x83x%mc7!(eY4<g77~uvLch3*esLG}aW^{D$8FIc_rY~u@w2wM&wAq^^0PLtb7%O^^Bdi{+Rgh_#gBM($vrH`{)kQQs_ve(68Ft1F9!s1uIe^g)CX(RNuqe2Usn5IQ|t%ZotcgNV7u#E-LHqkB;hD0iT?AN4)pfl%)4jj4@trSCyC2->ce$t)V@<hZ917|Kis?37m*+Cb$*Kc13TBbHp&oI^5@$xSH1SSB6=$|_d`4ON9-;%(Qo7JPr19D1o~G>+@D_EH<v&kv$xW6muu7~ouXm9ew}}LotwMPy#`@89si*V#<2PQ>b3u~)Q4<SIG&$J&~x{vwLW%1I5yq=qPy1^{J6xUm*(*NLE%%*C_Q%?KV|o{;I`+V&Zo@0br~AV?{Lb1f67^vhfe3G?7eBauQ#5~r_8^7S^9&1b)7qgf6$p^<ilr2R&Q2B^(OUs=j*Du!w#w7tndmh`g3RAL?cOm>K|}t0{zsjY%20ae4I}mzh|gDd1{}!;E6Ptey~C6kXa0R{3$Kr4=RVF0YQH<6ZvHR`nTw#wv#{mwVwEs9@K%Ii|+hN$Y)(EqKuO8E?e&DT7S9)|Hyy+TU>Uaf0;q4C;Dvtzk<r^N`I{^@hBQ}E0|v)t#&8o=bDXtBsG4n?K0}JfB5IhJc9<)^EFB7Ouo;tK3XFg`7-MJXzwpj$=v+mA1(j(8Fb^9tCObsBsOp}M&xsde{1y5!~OK^&D^KnObO@Whiv`up(h4^gmM)A7emQv75`v&s&rF++bb>w^f6<<`|Z?HZ)!i}$*eB9+p!Xg+bnyVZIYGp)BXGg>1uFZ9|UXg{}Z?p+CCwf{l1BRc+)F<6oqp18@jm``WcxQm2NX}1K+`)6@FJ^gwo1pBH!4j6UzQVr(d4`rG--TOs4{#=?n6iq7U#8l<h2)o|YLM*vY?jW<rMtxy$R^33>Qd$lrD8PdI{Rx`T(jw1Gd&&5p*Af*n3mn5*}5jfDGY3jfhuTjqIB2R!eJ@vv38-$}<~Pyjs1c(7$bCb%q642h%CDo;2h!@}W7hD1<KWP{5IwWwrFm4Px76?1InnTRugh6Qg}S4<#y<|udxmC?0CbeZzPOa_-1N};urriO9$4+`zD&iF-!R#0x3%v7L)D~?<}ah<OXR+{*`j6YmRgw0fB)zQ7!yE*<~bc?tWM1Pg44X2O0^;y(di9`0A?#XKJQ}KFwRWO~Qqg9?qwT8D<NcfZWexV5un77vPj;<2!^k%~F>SR2FiiBYuxY5C1b@yzlzg886<u~5eQUXQTd(*scH|1yTrpXT5Q2~!f?#e+i%*fJ%3JUtG6A6k}DR|#%=znzwrP0wI6sGtvEp7wiXAI?_SkZHOIw%_llL^X73#AR+5g~<w_Mq_PhiP;BzYnB!EXD|pla8!FsL-IlI+-N4R>m=zYwM2A<+}FJu(gB3HBX25)4{U{<+$*C8LoVw;)4F_MB;+GrmZ#2LGhnx-Ek2;{M4KQKfu%Pzqd=>Zwnx1LN@ZDXERplYoAbO-vC?QPepIDn&?-m_wCt)Tbf6oq{7UIN^l{CK?2C&fzK&SYEFUQZk2cnhU09Fz(y~$P<Xs}Haa@uISOA$K{0_~vULVE<iOt`rC=J7wFMix&;o-GCW6AG;z_<)Izl=MVt7R|fv5@!6>3g`D+Z;&M$bmrM=!KIdAx7w<+_HD(r;u)B{PALG6jxB4M*^Yt`tu@x}ssC$CouI!VmHMTW9%^BI?IN$M<v;!|*ale^3S0k;RNfrqHL6Q0LI6`57~MM)^|d!z;E_s<pa1a{*sbd5ZnqUGC`aq%2sX(O=JnGAe2lF(~)$;>=hSQE7bNYS9ssB;vcx!+xa|vIIph^{*0a5oDR7u*G5LrwtKZkt>Rzk%N-=VF8Rm=8p~_km4U1Nc2AcD#7vnEny&4dH-ocG-lzeAy^b;&jNtKQ$G`k4qkM=jEt9GLH#NrsBSD<m^D?83eu*CTgjC=z^KXJ9Khlh;|3u*Zjw8I$l#$z_E!n6h;f8pHsx~#J1Zz>xQYfCHt012@JpwD-4Go-z|BKc00(u^uM(n~g`Vi-Ufz7`+<jfR&VNA}YFN-6qJj<(OT#haZgTKfZt-kvOv+P%B3cncvTz;#Py{E>TkUw8=kCrnswg}75MZ-|O2bL+g`Oq6YTey!vp-HVih`?LBf82R{(vND)uN|G7ax1)7elPPxX$g6FXY8_&ZkyDXk$hyn&Hyhm_kib@BMl7rb!(SWCT_xXHBpCfo=#SN)7b2BakTXqSA(hONB$We}Ga+!QHAxZl!<$#=i|JvhGitqj|5)-6S<gx?P&TIe0&aGhW{N-&OQwJOf2InlRIy5e$8C;k<D$sqepaJ9|3lh@$w;`<LFk860h|RL8N|no<b$(SiHAMvs2~;{tvD=zmCM^dA@czTTFXpB;2gAeOJi&~i=)O{$qty^u4qGHq-`t7^tOk;`2sCUGWb#yFA_bfR`L;FDLpnlqC!ZRo^5`AMA4nK2IM1g)cO1Yut7lFl?_TE~fh@{>5HGvgi8iJL&%4Ep>P59}<NCeVfrSrU^z<i!!2%t7Axyd^P_OG#mqXj?&HRl4A_leSEg_yof`p^LwXhc&Eqv~7`AOw3at)08O|2Des!>&%R<MTBQ1CVy5E^Jc`Kb89`$N{FsV%&{vH#giX#jp2wzXfUbfUCh{7GUdh(jGaH?Mqdep=3hhv#kERGq-%3HXw2e8(3UA%eq;pwIrmmU_f7AlA{bWlq(w41D1UyAc};1{)IRa9@Eo02cw`3d?G+#Q5YN$ZQz@!>G6De~JbuPI@mP_ylPQ_<=jlY&ACTkG7}=u#(&8Ih{A7bqAaX78NjK0RL%z3;6FD`vKr4=fr9Kvq;&Y_L7Zdkq2X3tj53eP$`AAr>={B4W=_I-Hi8}cxlmcq;@@8By^h5)SuPrtSxBnLwP^MIJ4DsoO;x{xEtpXtv(D9?s#|!DoFEO5c)#Na2oh`DHI8*(OX6;B;Wc=?a%^omgoE1_bbq~<nT6?^ZGJFD4-?^y6Gj}@VbWJI!b~0Lo;^_vpRPRqWeu+U9R7s94od1nxMgArchZ(X=OsAx#L{w$#{{hC-tK0pZyR~{?1q{0@C8#;Wkjd}+Nt^t4&H0pxpzAC=67&a2WR+OGKU3fm__c1r+p68SQ6MWVueKp5gaLP_?n2e8p}*s-<ra!Qzd>%Q<T&C$Gvg(-aN9!t3$B(@3^(jzRXN<$?CBALE`Qf56LjGq%1H$Tot{W32HlpLMX3fIbP=l_^zm`kBLp3EIx7?Rknxq14v4=0X2%J=BRRoR3O(%NRyp+e0e_sJ%U|TmL|i!2axww$_%^AFjuiuKWi7dZmQtW$C%o$K_+h!9FFrz`VOPEqu@0MWIU(~c{Grrg^j=$2#U?8NqRzYzJ>|B=Q!kVq{bPGwu?MJ!0Gav#Wf4J{mr4fci>M&NQZDMmaG1zNu{<0`3MG#M2R&!T08JA?0hkIHuc^QxZD=X?WOYv-1rh$p?NQ*M?L-+Ss3I;6Q)aNTiUKs8jFxgxR(Iu5-i`-(mPbK{o>OOlpo^e}Oj*IQE^ts-nY5PDQC9TjF+efUhItG`=sT5$smX|o&6Ld?A>ohsq}-j|>F$6Q<feqO3@S4CBQns)kLcb6ABl)0W8D4X{VDng8%kOf|8YUzi$NxOpNdi^AZ(%M(84emsu3|PiNl@)J|eKGT*TQ9BR<gaHs|98WpJ&*#04;PQ#;K`Q6snR>7i;|>}Ux&e0;;l1vEG#A~dq!lxEr6D8!8mxu>Cn0vn3?KP?j)LEV$)g<6!N<78#DTKMP$RW%|j0{m|$8ya7MB}QEQq|b#$T$hN#f__paCTz`-;gwcY0%B#P#8&u#z-Cb7Ur;)Y4K-Bad|fWr*_gnlOoN}62#x634?Hw`POeE06Xf3P62xGYIyU3w$Wr69H}zsKqBP)-(n?Ktv*XURm5zyN<+0Ql;SH+aAYCO0hC=CNz;Jk#%nZpvFf53cAtj%}$&hIZBIF2|1koP^k_%w9I8t(BapEXlRS2dzX(J$xz5>9BLL5yg0ToW`QCA$ps}KH2hn)5Ib9Lo7vdWHeLJ2HH#FLmbGl?OR4!Muy<9w;OwJ|MACdZNjV<4yzk6{s_(Lk3eXC1FIh0o_^+OhUA<W(GqYN#|_62c0iJjxu)`7#P8WK5zMC@J1VNT7h+2-!ekrG#xPgg=2O$+uvUQ4(iKJ7DIm7$Qd|MTUh4#$=EV(O`D1JwBjrEHXr4sn6kt(4C>}i`uq2laiT3lOkRmD<JhiHdg($DSX5kBUTUX85b^jazSLQgj3-(<6QNE5Cc^S1-9wPOoc<fPC(Lt^wWgzf9Lgooq*k1M2Ny>wCOZ=H+qQK*Z4nw3MR&GrKA1DDD|hXMZd{chun|P-!5HUPj6r63fI$X*VBu*B+Qjm>AH-8?vneq;qD)}3uT4xolxN^aMf>)lY1Y2DB*qD=vNDg{#wP|MZNgmCaNkmUBsjr;t%oe!Ldgl_!|;?(9$ODM7xQM2R>}2F9<yM<C6@0303xmjsD9GrA<vh<b$^QROBCiNNeQTI)@<uJ>=sBxVB#^6RrJ3g*{|%FA6*R;}Z<~FoW0J!iBb|hb-@@z>oi&*4XpaQ-&Jq+^k*QeO&-C`=ZsxX@S3xeY_y#+>cK(<js%n&bGE-2kqjiNI(3L)-W@*SBBB6ULx$rBl7Wl00lc4JslqpcG#X>5Nz(pCmQTB)_2=$;7@V7p)2ZP`}Xm`Q$MFO_JWEo!_0=}$j8$Foh+LgjAD%>eGw`6I~=KbU!c3Tkz`yV(C4VpgAe@&h)wvM$SEJxH$G#r*N^yvBIh;vSy1FG1<#gn6-G!zEar~UJ;Zz{1pBy4kP!Jq4fgUGn=6WruJd(&rU3f*Z58|2@sq>B!C~Td@6FxwS2u<2?%ZXV+Jky@&_{{VVWprHoj^Tma%hM?`Dw545tX#9{+ojUX$L*t^%;lf7>!-{AAysfof0)OP+iLhaj`_0`svIp_^TJHZ0ZOoN~4>9^n~4iY-*GY90-slB;-%Ym;?4DmeV=)hq-JzrKV54gG&O6b8_TxNT8vB3*mYMs{JU13se|Q5*H6{N?xvk{s>E6qhS6>bPVT|lK_anlMy5iYVdV?UPtlj?yU^I=&~&^?+eL~ytq()|FYYyuUt=~ls?P9#&*<}RMxaW@*Vgz>upXYyf<b40|bVjKWD0Kcx!EJ0CZMM(2uwsv^JG=GyqpsG)-vpl~Rb)(KdMDA?67Uj~wFuavSw7lL|3Ka+r9C*~*O?XWd*ZtA#k)94{io9M|EIL+rJR?)K)SLd^0XCLU!zU1w=?wJ!y=Kue6TLIcfPuRV6Uo@u*V6HC{W@7lzp%*?H_)ZFT(b*vU*2});Zh<Pu)#}4rsuE{18V$Lsd(oq&nsIvSW*-Ns6S|XDVSh!O%ry-F<CBf`jVJ>p}?W#Lhi}diWwlOobwBa_(Sg3jDAhwffkkt(Rg@%>a-ly_?b1L+dn2stl!+J{;PZgt@iLAN!uPMJ(6fBJ=7F9eq+$+Y5DsN^gUK~D4#F>so29~L@vdnbMzYbRmY$hpT7c(2lg%WHJCKuSF1AG7;@9Ro^4}%wj&-nbTIktkziXl|_&J2p-H6rUPJ{$8pLc#KPa@lyR=v}t7W@GUx@EKo6YmO~jPca;<I?2h3fsL-J_)N?10|jJ`i3QfHbbOWKIDw_oZ1A@48Pf*q4XmhyqNZ2mJmvlO_6g8yIul(yMP6F-U(|`{xAZ=|inMPW47}^hvAm~+x7OTOSCeiFInoModZ2@zp|5vaYC%s&)>e^0=fCR{QE9nbKaC7}qcgdnQ{R3~(k%Dq4R@~)Y7RjMotkiw+ZUCfM^m+;zv*1&o`?{Oyj|;Pkuj0z(~r6~B^EhTWeNjEGRa>cD}W&S{5TQ>g0g!Ok}JJ8tLUE;i2(nAc_QKGdoev~D_*7C-&_uLpI~Bd@W;;Z*k_H%0xnuqh3m{FqO8(wPIniYQL!H#N>Aq7PjzhibXDBethd>YGN)h=z|6~*2`}j6;i>|(urw5<R37}e`L{pHTLg4H?b~_c&zIa*Xc1N!8gbJPdX$rhn#78cv^a^ylYvW&Qou|#rIk^@_;))hV4zFsasfqa-~^)_GokLvL)fU7I4W#v3M@vFIRN~2o!c8mP9<!Xnh?ua*to_jGHj5;=sf;x{K%<?jhAJ4h#A{3MTISnK#P%NW>|~l;VBcj36<d4YC0{W;PFpoRPZ8%Tj=0bs{L|DIc#FpmWRkuFJDyH)C63NWJ;_Whd(owfH`V9E~9|)b!}9@B2-;C3AncnA{?#yRpTXHh5_R$)rgqI5_d6@X}TZ%c>8=26?ixM)!WYI?fM$%fG8b*{)GMxp-i-e!goFW28Aemb^CD(m6_e8c6ae51_UA8##WQ2Ps(;W_h-JO&t;*%QT5iQG)^d3)*Onzh2rm(4~1J@@A(2|=Jq$Yy<&Ah(Cx34)m<YPbWkIz+gF_!=-}0|;DajkcRY1;G_NLx4qOTUNOW*D7B$Q2UVcGMta9!!G)%NM4xTtVP=|@y#mS&!P;>uCfCx%CgC%;!d0(9P11~X2|7l_5aAFyRr|my3K-jv`U^!xO(2<Ql5))JPpVa%ZwKOSoh-v$e3J_6+YH(J9*gt!Bpb^==>YglJ=f98}5P<!hV`ZYRB6}NC-`l0$`*R6}i-Fzt&q}9Uba$~|M4$aYGtxb+;FAj?-&|F3<lR5OH$(1LHCniWv)k4{+BXble&$R2Okd~r*$56sYkQy|$_N+JK@3}M3eWWHcxQTaZ1)=8X^W377AM@^K%JYO2nX#pgAV!Zn1_69WoR(dw+@~MuwU3q%snG17!>j7n@ld^A$v_rC{!Whu*^Ta7z1QX?+*^#r}yx6d(dgS957G@CYM{>mwv8}{z&P@T?CAfwWq~tSTIk4|6a6)ut9&TfBCNIB)~8nPP?c<LbJd@=ZxI@eFEVo70qR1a1*u)W&Rp<R3NG#?5KI697=Kxw^noubpcSW%4+*E0ZM{aqs$g<oQTd94R-j{3VKao7|e*Vg<lZ-UcBSbadDmBxz2qC6pxu?^u&=x2XI*`xC&Q)MNSX!Yf&i(V34nSdqpiUklm8<2jhC~3Jil0Giwg32UIjSa1DXNOcakmH~rCrhP2>Kc)jrbYNNN7!+(nUft&qT-7lZ+Di@?`cyZJ%J|Cul_ub{Q-pb`j(PVml1c%<|jqB;J!7?b4EkSx54fhvqcdv<EEYTmu5wA;+*Y(>?Z)@2rZQM5r7%Z<v_NU&~F6tDvmawHYK15$@W7yoEHxs_R3ArvIzfbAF`&7|6N#I4!jtTj-qmG8Ogw&ZSsqpX^AV~Zw5v`PyrUlUI?rA{-ILvT~2)&TN;)ZZBG{az|>c>1z)gdCGk!6Of1Uzww3_MWvLWI%|_<;~%%LtQ}!GEwuje!qaM$FU#O^Y56DmsBQ@K}uxBuiqzq#sUd2n$nVHB4r{Idk)^bN6+DOgo6kJGNclQ4l5)G5z@+dAJQLh9D*G7kao6g8@v-?-E=E3-U6v@C9%4+}+tmQR(!{9VKxid7IW}39&9*$o&VDE;_F5i$No$K`(?BH5ToCe%m|07(fdb%7;pp9**dkAbP$siDLntw1z$i2$Rh{n(0?s7(2K?-t5G2{s4^F;#-0Ea`pg+5J9nQQd8I4k8{0)%ON`a!Ws3?+aPF&P7Zjvg&6W!>R4gblF)||DxgH?m{$wvWx+VXwQ^J8L|=no77#YPRucYCmJ(2U(BkJRBii0EU%WZ|jCXOZ83df}%A4-aF@ew3b-r|+dk4Z!Qs%jm;Rf-%u8d$cM|Lp|)~gJg*z@mF1hbja-SSpj=(ENH82m`RImD}dR4RDS=iO2fB@3;bAdVB3VODbf>c$CO-LP*D$WBW51UZZ#lMpEXK)hocFvwVGktqrsGXCEr51F4~fQfjIXuzDF2w;-vPq(lw)k;e%O=vQJfxJpWz@!*?rvSkCRdMbJWcwkKB2WMf`I{yOm~c)vt?V~*DFjTpgX$RFw4uoW2JVg}giV?`l<i&}b}zr&l)rS_K62iXbBrU9d8g3H9?VD#FL)xQA4U5|L#pPn`I^W4e)U>#Mo(v<V%7VI#j2|)MEFb{L8@QE&NSK(C0Pwgs6<jbNh7i<vPD&qiT5LWjVKKbfrpe~W?0u7tb~onMHKolGGOEr+ZL)2!r9V-!vv@qm|((#K8sL0boywsTt;8)exW^vDSSk<*O|=i9>h9~ncm?4E=eDBu@J3cvW!=Rg74qn_fB?(nW#RLr2Pb0KN^ONXO$q=JRCe2kvl7Md}k$+;rKMezgL0U94xIBf7x(usZ~f)+3ff_v}!A9HiHaLBaDy*l{vPc5@7-VRd)Cit-vB`l&mCYhLtC5g)=%WydXtP8|J!XC$lnjj3C?=|7-|Ui52yTgYpEVp1~zdqdJx*{L~Roqu8Qf1;OhROO#lULNpaMmcg-I&d8#QSQX}rG?qwC0Uc4<mS{%I<rFK9F;~L>9Rmq3OwBC775mOeryMUok4ciKc$pi2c}Ujj#M?MTd3Fs78X=e3rmJQFqJ%GyOfvG7ueoGIje)zJJ=9Y5cCQ946}IU1DH9;*@bV&J>)3duZ}gMpL_AqRS;>|k{#6U8$MPyOxX0?x%#-<lYjPt%nW8uBm2)CqIX+$409kdm+_9)B^376<2G{Y>0)7rO*~!Y--A0$b#$!0k4OgTC9~^&2dwY6(H}!@Bl}Rns+r<&6+?@k#AD^r0Du)fdCUa!*Ik3qY5`*tsQ~l@X(q_AcMU9TTyNfhV-jXB)aR^OiZ7m@2-zR{!m8rhu3_wrFs?%Z4O2<Hh!cjfYIzpD~q9~gyiFGCqCN;li!=jB}-g6MboCL@tmJ$l1DV&^gx(z~MTBzKx2x*MtmYw|h!EP&pDvCbQK$#sHWpAe`l%c%jUA87Cs~0GEE30Ho8}5z!dD$a7uPDAL23&+tPY^8WiFIS0f@SW2jH<Umb81V*N<4b*2w-m5Fg|O9L?`b0g5<oIlZG*@d8<pPi`RCl2BvA)lHArPJx`qBq9ngU?9L5~R{DIuP3O5S;_P?;tJkfp>i9^0Hy@p@Ob3##<V$o^jEto$GR*Z>t3PH-ADE2Le>@-=$<^cm(<j;mXZ;HYAR_k+<NerK832XTTn~h{32{#9M*EE5<pz^+3N%!9u@bZr<wwY6y+7*+eH2vh>ge5s)UqM~6!ctr>5>LTh1|-#gw^0yCM!d1g`CC2a6#ndd1PBN8X*@4WBvZisyow}_^phhdt!jD52#-YC8yfd9J_ZueN!moFLnb{RJ*&&!y<?HE<(<7QIDV2vfc`MvEVyZ=B={AOKTBhHHrzHqF}aB^H#|<=lunKz3$HnqzIa;V+x|ul`nMHD!W#to-r%5rsno1QYRXs!IDjjpniFESHijvy_FM+5rWVA&{MV#%36lZ{zRa7EpnxRs@umGg#f>Ks1)s2m&qYu>0rbc!S{i)OK~Og!>lc&CGu7EA{1W7u!W0lGD>H6>r?o&Vt(cJvyZmMV0{aECT*eW8C;kqz(JJM<}r*zaqbFH;AWN0a9>0yjb(gWfr)9GEq7~b!ue`ceyf{>;xrQ_#VuZ;*cMM?q=1n-CIk-<Dok63Nw0#62UFR2sF3X8Pfy&P?J-Drmo0a1g6|jaBO416k-d5#gWjg;&@T(UlRY|J_<rvAe<8py94$OW@oL}SyTt=W;2@D*oDJ0ahq=P{hA00!Ie759w0IdKYAP7|pwJ`hwX55;mo!2aLE0whCCLanE+tl&xNeT0G=#P-^w~}GW53!S14h46^}f{}q2kqnhcN$^hh~Z|XBlmZ5@``WU+1eNI8D&z%aOU2AYJ#IoHA962~5yd3nBsUmav=^!o>YWTd~*PC`5}_DUM)7>(ID&5jLY+@$ouexXx{p$=unHtPCP#*rwpo;1Yu=#U+*)<wmu~0b`eFJ{VnFbf&y!6YJIx3Ua&|9m5U|p45i7xB;UTHQE=SCm0EfPvqN5)XhN;g`E`He~9&r=E6pEMD0pm^hkgl?)TI|xqxVKds|CbKV4ZMYb>{f8o~i&lE$$|{ho118c>;UVW&fliSekVy?OJpG`I@IGvXtho<O?;izka=ZFY9g9+NC4cK{*jB>3i#NLN)7-b@p3lPU$Zt6+VcFWF-TNyaj}#deacp91b&UX!oiG5+7__r2;{6o=bIP`#QJjXt<0mt0C&W0z71EZc+s4G7_N`XI(yVElLTD^q#W`c~1s#F6*D;Hz+AJyiI3-&$6(>Gsc~tkHQ?!fm2fsEtXgYg4K_fQR2>P?gH_L<JqK!!VBDk;ncVe|~E5w=<5+kmHHQ-z!${&rkl-ggtPO;n4cI_&fjEpK_{(-AKt$SO#GqKf-<@f!~~Rn}^<;_Wf1<KTY_<I42Gx!8<j0=M}@BOp6|TID<)aH9)zvJPo+GrN*F0h=&#NJcGL+7<YU5VQBKp2oaQQW^s4BQodcm9)@?hqwdv57?iB6NOto-FO=?2r%>`i5ZJ{{CWce4;Z>*qv%_iBkcbfF@YdHir(f?j<_4X~K$2_=w39Pr4p<C1*}nKf{~Vn_ZpJ=zz^+x?+9mE+ur%3zG2X$PK$uT)Kg(VFM-9`(M<z~+x51_WfX5`u!21DlWy&_Ck^nHZUz%{T5E66sfiw0481Ud@en~qR%$VmTEGR-9(Z!@|C(V0g(<$Ngb$-`BlLTE^OHr@Yvx3~Ei15x@Y>jRQ_+yU}!bQ{P-ApbaNF0F(POBqr(L?Li3~!H3XM_{-$sd9g7g915SIOW<GwO}V(?yPACQH*8+GIbVpr5O|Do3voC<vp>8xfRwHVZ^CGsLu3C(C<f(~02SbspBcSTdYeNfd<WG00_oISR(e8GH1-KRQq{{)vcqknn;G7chco5z0hOB?Ura4+K=qz45dC0%$my)S@6JB;Z6JH)|B|4U%36*B(pr+G7J2s=Hft3-!rTcx3LJ;|chR7PoQr$x?<upKL^geh|*w0aYE2jBut34rT6k9s!5pE8jFPX*Og-m+J0i;c>V^kBdWvRWbYYr5M*U6y6UcOcgw~EA9<3Njx#I(ESliXC`Av?QQ}|snBg5nAeP_6zlW-GWMAva3MAqjr+5jnpRmT^Axt4Qy?Y>E@e^{UF*L5{u!&T_D=ThR;%6jpGHbTM3)^DVLd_%e5l9Gj(CQ=Ma|s|g_|+m;tbUGxBt9GrWB1<zt|Y45NT9r;0AD%OA^hQx5j642IS!Ek#rqWjE&pDK(c=N{r9VHw^K7i?ZqP?GEGC~Nfa!QAkJ=@y!@n&f9QgJwMx<!dOon!Po;oTgA&37+a;K#n-04_$vdCmhm4x<_Eu+Zj!VQe?|t|AtGl(&0~n$2F&?UesMJYEv85NW;tUB^9D*YBY%Ey)gXW~HHu@{GB%UiZ{9(?OaYfT#t&$W~s^jWJMO4}d;m8@9&cLD+Kg7*_D#fmnws(9hx87L=<(KQ}!cb$O0jAk!Oj}!{AF4_f#}-3DdQ-Sgs$PtI;09U?*d<VriXS>7cowQi1sxo!>vM*W6R7#|-N8##EDtA#@5l#UBvtbsjOms!9W9q$yuP&1n?=X%p#aSBzzI>vM*+ldAYN6VCu_HbDlfc9f74WB+l6>m(`CKRb49@yKnd3%PC1_{GxUn=;%@zDI01b-RE(SgF}IR3%q?i?pNcB|`G=QHuC}7&AIKTza?LB$$s83uc?g>&1w29u{Zvq)Prt}~a<##7f3cloQnZxB-pbACS2}qv!8dPaTFpEra?&7i%-}?qZ83UF`t1Kr*{fCjj5qI|7L;~#frBh1VHzjAl#A6g2G;y(YMy^Py>ff@Ziu1tG0(F@5QaoNb0rfQ!OI$Q^q{@KQ&24_e8fVf7+1Llwn5wM=+y?KvQ^++L+t7ffGyH9h>E*3HKXsIl<(H3hHv2>DeJKPGXz+cDRXrxUik=hjsxywgr@Quu4MoX!`V50UNDX}NG+CEc=*Jt9bWrba0bH}%B!ElfExvlca)|k>~|Y;^wRNgV2TN{EwYWIOxr2bA_&2j5`b--kEnyn7;$_>%Sl$J$M%Yw9dD<m(fTS7X(<M2BTiy08YBF|N|>N%If?0Scex{fqCY$fpC<1gP*Eadi8Ph?8fx706R30>o7E_3UVnw#!cD?1^d%*#N|F*HUr!lX+ZYtbC>Q%dS<Tf$v_A6tZ@mJRJL=Xdbk1pzfV9XZ9!xG-haap)Y`~TKifafFL^U8kCYXiCSf}+#TD~enLv;x#Oc~USyu0wGS6IjRxH_Z_(&}Ut_zreFp0Tnrh4Tc|`~}^9+!Q129PBnLUd3lkeJqUGsBN}T5sJFArz7#t%>dNXliVa;h(Kx>-z~kk$zd&%yNgSId`jTgAS2U#zhr<Qke4qIaSbfs1}5Re@h+M0AmDJI|L5-46aFBS1bF=bRUI+yNdh)C2?yX|qP<@WBsipL!Q+LU0l{HJVQKhVWe}ir(l|x0-54z)?I6%0#C4m?>W`htdhl(wA9yXzDLfo67bZ&Or&t7O=Vyc%@A<r*hb_DurpesvTRs2qbS>B2J-gZ68an7Tpad7kFj87<g+qk*qMyAOh7r<UYgEgH0*RU&^01LHG3r+uL}sZxkE<npOqNKkKDnn3Xea@-XbY~Xs5$oSmv<=R;F_v^OiPTmh%!&ZKZymPPoTkq00X=+qxes%wDKV1ktouN!%An@3d5Vkk&I|_1EY%EziP@)9H$A+Jr}5>NKjOK8Ui~8$|PwOw;T-*Un-*kDtHh!M26?mnkWhyvpkGqm(vcr*?0g2R&+ykbWLv&En6@1*wIlL`}GS;fXmRs<`;$A*-to$Tz<LTem$&ThR%JEb%tYQvoa8Y-JV8K0KyoS0<}LY%%g#(FP&Bg^)7};u|`_0TocDK?qO0;I~2U859UY;HUBJtK^lh-let~JUHpLYv9d8LZcxvJh2M@r;7H5duEcHGColpGg$!#%jRhhxWHuXv8peCm#Fh=0KiGgz)38tuSb@hV8ETGvd$x7=V)^Evb3IKO^SS)!ItuH#G)R1lkn%a5l{tqMui(Y2h=`XdEdB6TBtSJmydcd_;{p~XA2+{y|Fx6wTAQSZLFLO{(XFj3z)HA#7px!xU#Q@06mTY@ct9d(y)!l=P>bHJyOZ*8lz0sCEO!ikPbw;SB|Z71cd1cLd<dq5m)4Tf3(b!x6`;nKObmBh__NjQ#wW2s4esxQ&j19+*pb2v0hO`Cu+F+K-{Q`FyV!j`gQqzD${oLNM<8v6q@KH!@J$!aJphk1Q?;muI2b!D3vGRgbX=7pjtw3OZPf4^sC3{YUZG0w$et;fs+rKTT-p3se4mm@arY@_LnX^I!t$LZi~JV((>ap$il90yBW2b%yxi}7copd?0`WhNvgLzM9#;|I+TKhRcg;AYTh;rsI(|G*)YRB&<gy<nG_3O>;TsZeZye#o7sC-m_&%mt_~3!bEbN#mE!@4z%z>EYhLIdoT^8jdC+<3pIcwsdM+!Jbl0goZnLq(mnJ@-kn&AKFhXuxXI&svuV%+%>`y|S16J;{2U~VzrJA6xCz9mvY`KOt%;gBgawwA4X5=0+mkborb3ivz)a|VE~_um|%yn|Cd9w?TpI#(S73qDd&4RuDO&H>n@1@jNkx&P15IwzlB-F<#bJJ6it;s<gahsyPoj4pGpa*qB<zy7V6{JC3vj{cC!N|M5Pjyl`SmL3=&^tgM?aVwf!!~0hADhdN8uQr|d^>6X-h5ozU{rUcMXOx#Db@CiA!3<*i$(A}<dQ83X_NFz1i}Ay^H@jOyZgq2@>*{AE$Z?Y+P{t8GV-E+of9otq^N8e5FXblH;6rb3ztCS>ARc_}Cc+D*y<!c4ttq!$Ry#owT;B$<ibC!#W%QDui+A?n%F<fT`32HNO%H{1K<o`8qd`k=WC+XMa0inVCn^Ns7S8)~HH|OP&<s`tlK@`gBi-?5sWCPFc<P7#8UvLy!{pH+A?U#DYV36S$Eg1c))k>oiAifIw!G7g!MXzbn_)(L4TWbz&7)?bMgYR4MXaZU@w;2Q-NWD6o5Mv`b_)+&_CURmH9<y({9?n2?5rKdGsDnZ3ud8S!OrD)yi+|GW3=v(qUBL6V8M$IC!{rZz^90^CmO+Zah4ijfA4JeW>H+fB(zU@Qc27Fp_3j*ZPF95!Z%rl$J}M&sgHo1SR){rRe#t>C`+qRw`Oi%zaQ*$ch9=*%^~{WgCfUBJ=UNoHSs}F*dB$R8=(^1tsM+Dy?D)A+eke2EGad?S(3W$b(S#=JS6?w95KEA@DJk&4FWQO@2rZw=ntnB*wOW!{4#H|gBw5eb>H6TPd7$c?<u~)isS=Zu1bvSd74c#>Hxj%jhoZ2q+To$p34!*Q_$VA<H!)Yt@wAflOVk)>A7P@UF95Ai3zu~<W=k5=R&-@0Kkc@E`|$8>^+nvp3ShhI=G~8g?YM%P@_c05bT--Wu2)YQWzgd;%Ak9dgV?kF_wze0hUlil-v+ahybB7f&}j;`^H8dS%wyH+3>+vLh)s_Go~dNgNX;Eb5heSUD}wu^o>vtC&rL0){Ds+_y(Qm#udO6Dc=ML;;78jnDWg`CVAf)ni}(#*#4eC_y#FcZU~QyFunzisL6yv0k-<;M)KTyU}E#Yals#ZyVlRu)!BIdu8(Rr!%8ZVJb(?}-bAJ)VVQ3x4c^Rz!U?XGUV=bkNbxp_IKVFjBqMK=5f|#R<<6*#BS_i(BoPwwaG6Py_qw2Q@_|Hb0Rj~#D^phcjLcsMRL1NuMo{@yL(x<-HF#iSJ~gz2)yrnm;!U%<&BeP$2b4){#mycku{Ji43ev98v*(jBO7fku?mG?4-*p!Y_m>AsMgxX~s9LZ{O}w37q=Axf(`P1Wf5!C-C0sZeMPX$XT!d(^X~xBA5%a5`0yF{?)-*vwsJCn;snPb`TJvu0>Skja#qM^c(QOYEM({Do90)8@ZorWfM``}Rgq=JA8@|nXZ)F+9z<-Rc2KplS)I{GEb~M{yGM=8&%e&B;CdHDL%FXU&ySKRv0+=JC9uf&KUiDE(k7U!4itKVceaHx2E?o`fu(g0&s^ilEyi{qlbc#dLx{!ff%27H4NGmHTNCLRLB0SPsBnPM6eb8B^W5^*<DM2N)dF=@ETA<k+lha!0gX?c$^>~#{FfbP-blUQWB>f^>=|Z4F52R-oQ0nT<eCjo}L4dJjtkPwKkH!iqm_CGHYbM@K(8o#pMeHJg^Gat5KWh6^6H+Y#=st+oS)m5AK}P0glA06uzU85*ASojn8)wg|wq%vMS%cpC)6ke{E9VE*?J0C|Ds_yet_Bb&Dr*|lRJiwHy<0r&&g8qzkD$IhC9`T!23HI*^|bSIn3TGB>kwjWOS=OiAnY}W@6B^!f}`&aG7w`xId$~kA0nq7v}=fRzZGv74BCzIxO6-B5nuo3UUwJr-@oiY5M^g&Xu2_JA@D*OhAzQV7KSZaHY-1cb!MB`>n(BLZlOQ%-jpAk03ur(1289k6y^Mu+3Ar%%b8SP_SQb*8~^<&5Y|$Hrx9+GlVIC3(UqElPY$t_bCN%YO$Jmw+Pz8v*Grex;g$<MlnYIb$^la6Y7ny<-($`hdCX}tcpp8H>>SFVM%OoQ&roIky*=A2E`dNcDW02D<rSUEQpulTR&kKIQ_CxD;M&{=*`!}eKn)g{!%`qeIq>jFv?4KR^^-#R<}()EzG=;nJKm|GeAhcNgaNJ9Qc~JoKZ0Z#GJpq6pyZD-QA)bM$`hC@FS)zz1U|WYbLcMm4$qYWo=Fv+Jkc_qWsa?`AZ8jg;r#IP?M@Dl<X?4Yl%5bT3YgRi0;-f}fWqiB+T@qMZ!jY68w4sUf!to|s-a)0x^q<w1z$%O8)tgWt1%3IE9jL}k@WCo?;4EoyM`cTlt{L&&~d;tXg%+bAA2h&IQf06(XjH*yg-u=TSPyBH>d2q%oD<tmur&Ta#|y3x;Pj^QvsWht0~lp(L|leJOw~KEOfYjG|lXmyRRCUq*8s!rm7q$Nm&`0A*sY`I{Q=s<-VA|KPOU8?$OK$Oz3U&s`}nWY76tjVH7xdHE2zh_0wJj`~Kqn1O=lya1U*X+D>>;#=EmVoxJp>MN$*qHkZe864}{I%u)46BWcu0a<x;IIo2BiX!`y4_EfL+bx53RT;~@7U@}uF8Lh)`e^fpp(f+8h4+x0z4?At;e=%ezHdd??SZ#Lt^YesGS1Y2*O1mp6m7*wDRK<G&{5y&!u7Y2d7Cto1Btx$2zB}k%ez_@s8Pa5q2~*|*Ull+O<PokMnb{aW`Qk}ZIPY*JREUg|x)_U$+LOgd`?`&DtgV2ehIRc=y@ZDrb44Y7W;Ai?8DB<)h&chZSz`EMaAmnkjr8x{9nk(x$K<g61qe{KjI(Z#h_6U+Rj`c?AR*}R)(OB9ou*zbj5#<VEUhl7O;v-$d|BO9YH=<4o8p-205l6Z_|Q5oyIXfFQ@E=ut>4Twy890;-5GF9kWR{+ohVe`VgWiXZ7CSr(<Rs6I^{?~4{5s<dZJ&dxs5g<jh@voD*_c;#R3kVx6?uGU`OX<fqEW;=~is6lby&y-Nlu>Soqh}qE$9OkcV@QOe$Yp=eI%UoKmMR(bM@%go1)4^)SM85$GBIw;G<|6Y8ESn<8-7o(a%Fu%a1vD)I(l3{cai&oNO`w?#hgm*i3$ecPu&DlLYLFT#U;Q;1y417#|D5BUS!Fe@FDYWn#pcczV1nYTB*%c|;a3w1Lmlxi6;t`g#`^mzW5K~@N+=?GAiFXFi5J_6k^lXANo3FPqanDDvd2MP+$9dBDF=g2Fik1>GT^BOT<?M%5l2f9>%9-zg(S%I$`H-C_Q-FO2$8KFfv?G|?OU^2qvs2Ti3WT)OkUNbu_Gh{H~4r1n0&_6>B-_{a}yWe_GM2vv9IebLbvVhqEqbYt#{Gt5U?j^nn$KO$qO^YQ}^F(uq?eUuD_Qg@R_<T66df#0>>#balf<8SzN<zKQ8`slc!Hr!Y-<+cSp^qJ(F!;~hOdHoQS7nrmG?I<&@r$gjC88XRu({U8>*@RR=D<6S+|-cYqjcbXs>t3WL=ntGi=zyUm3JguFNw?UXHaEqXACKeT~pGaN9xFG@~6I)0xFC#INmhzRh3GSY)1I<$lXs(Of5i!3R=MgC5{|X;>0>Q^Qd<MNzR!v<3>rJTq7>sM444r#d|V5l`a)g^Nq5*cB@=Nkqn}h8D}Nw$nTN&;+)9W_>I#!s9&-(mQcEx%?d=kMC>3S^wq(8HK<sCM$^jB8iXE1zaf!C$^8=V%y5<K|A}fZ#n}r4dg%-ZNb;fru`Mp83m2a;BFH=zBO3mSJVwVvm#_dtUNWOa3D~DAMYdTGPMj5<q`=KO9^arR>avBt_+GoFhOQu{5$W5b+oljUn-!YCG`d)rmPx~>Kwd4>BfkYH0cN$Z$7L%P;kpCn!UVI`r4tmw3s!?Jqj>2;jZ6(gZ8(4?Oi*jxkoV5w>ff#F{Bxqtb~!A3j)C~GGcrvzcLeZsD&GBKe`em@?4TYaF{pelTT1xgxcxt@*cJE6md!}0-F}(F&1h%wW_RoR)wk>E!eFBXBm<%6$JWBo4)o*pSwS*hm5-K7u*P!pfY3Nfo7nGUV8dK!>m>*UId%`ILP7qO6V5ULgF1_v9N(TF-+f&WKrDacQ#o**kOR3a*{MmJqDlhIR5aF+ucrIeY3!B30?gg5qRt#)>=6u7!vJQ2U3OxQ`m9utpicMw>i71Sfe0inpie;+P)0;#<b9cGq0unvkF8-I49QD&uuRUo8N4^g<tZwdPv_(7iG~q<>==__<tj(iSkbE4FFxI!e->q-A{qT1gQmmD3F^H|-XSS3tON(Wa^AW9%~?DMT3zC@QU5tMSJ6q(4^=3UzBU4Hs)TpFkra%ugE)&CH{a}6Z#$c}>ubIdRQKBgf?#)M>2Cev`z5I2HnVyTLbxNNFiRC%$Wl}1rwM11Vb|rfwA^K(zfna+^}th#h*F~d8&&a}EhUFSDM6+Xixr!ruz{&-A}&bOU3uW~h(%jm&Q0B|AK}QYA9Z)nhEC$JJ4`!cIa-{=gG&6A96QofCku&(-^?C$$}7}Ko-5SdnW>@Bm&T(?VJ)8u;Y6BBL?T5=OPiXCclS#-yLe@J*XU4oWdnKK;&uKt2tp|tMv+{-9Ze<je{WeTZfwdKJ!>j$;2H=s884<rhGf2WE*4ygtAoj)A`ybNKxIsZiK~mE7RW5JX+rkqD_q8ZU7&hG+!me@3*I`2nQiGxP7G4Ak@rYk@x_+=W<<FUau1MP;+>)Z&!YwsH;m+xsiCuHq#nj>(8&^4c|GI$0yDfXfY}Me$a*F{xTvG%asbT1E%+xDR9YEB3)P#yP<aGYl0BtnRMw_ZbBa-k@5Hytno&s|uS^n^pNg7MDduw9w06tKvzwiRVY-|mG`RN!m2HgBd<T>+O_^0SY`r(>q5e3>N!DJ4?i)U|@xBcw#S*IVT3DEBrxuUp60Ed1nc+W0fl_f+5e!UhS{374?G|MQa^G_I?%-y33&a^uPDrmn^EGFnvo&AuElh_EZRrGeaezYI-)toC6;-J=G2H?k*PJ4P9Xp3Boq(08xhzHxZod>p=vu!3p$omINx}S|>mTAO{X|f&d}Lv?l6<4nLY7%E9S*?czL-}7lSTm_A!mzDIumk`CHnwjJ3$S4(eu#zda0{dO=XgfVy|jF&0aNZ+)KBvm9krEO|W&fo<{2`GvN(xWf?Z{F>xTPk$2=ZB&B6-Ez-u!LW)vG5P`~}p3@1tJn&DbJQxWZ1KU;2)s=uJsk=cbCva$U#-G~>c|ZibwGt=|7D%?UV$M6p>Kp+rjN_b2nWmOGi$DpIa1X=wsuIHO&fhvSFd;~AL+c1R^BAMKDh8_}V_J3{_F%Ubwuudvf_EUoklM#A4(sB0lY4oy;BRA>N!mbhe?emW4(7ydI$0?%$FU+%=IAi-y?xTHzwaF!4fg&aWLVh|3jd2CY}d82x7zWlyZE#Z6N_+=QB;l^s!n~PvM>CY(5o+dl{F&xhyLjg#-ah#SirbM@XPYE5r;N<Y+l@)FAbqoA+GM1q~udWQz|Kw<;IuxN@#+av`IS8hV4z)(5Fq%R4(8%XTR-J22|i`UI|e025tcFSwL>0)wmBXd*>GeK;gsZy&<L)(^E#~tZ+bpi~*?N9li)q*dCPw6!(iE<Bnn#zf>4ddHjL<n2HG~=7m#4Q}D801StQfMVJ!*73l4uLrroq_pp9A%vOs8Q042~jxxAXG6sU<Rwqi}f>{Tu$fZ%>x^FFeryB%Zm1@7-(E%$)Tw4iQ@@|4!<kDCgzhg7bwcU;85K1eU%#VXJs|+Pa(!%zkl2U?FNSQBy5&<j=CH@z~i2h~#Nh2t^?Xvr}LHaIjXp+$S5JM@Xgcnuk{!a@474TP}F1HS~2I$uJQBW~m2vaBDc2AZF#HO!v`($bkgO!x^Erc0lYM9ZEPaw!(R<IHX5o4d9!lTR?!a~F21Khlywy-L8si;_<NAiM9wJ9t$LBQc>M*n~^DJ2Dx{0?cgA>>-q&d6Dts`>rt0E}?Br2{t21Mh0%F&qFD@go{nNkAyHBuYyFX_iE64uqn#Ofe*<LZ$^Y!a_&`Xxznu(w$L=Mwv1^#D$IwL;{K&nmnC*dH0eCr#zqpgJTQhBex3zha)p3lH2KMu%!u}JxWSRt#-T@1)_951V{q^zt<aj7c`1SGr{viNg*h1=#yoJKDUb>I9ME!nBY1ERH002t_@o~TXA^}f+LSWETx#?5S}*DSlVl#ismWW$yjQX2eEydoaqN!38iSJ^yy)*bjE@)ha!0hN>G+e4HP6WoRT|Ul+@d#t79oYw(|X_c^X0}@@f{@LF&P&kfTHi$xWsZj$CAfl`tu427TMv^aX-x1e_B&{0_|X%H(ZdL{*FgjcRbVXhx23bc&SWckO|fh;inZap*UbDt$843S^@?kOUqBRj%RIiaL)n24{|D#E>VZq?x-=^=jyMKWn*#BA%4SsgY9Dz#1QwEnjhuVT-iuC)c@mAoL|=ra+SS{P9HdC1s1^R#GUq*er&A{Z0??K)X`Veao*PoG+zOq+V}@&V^DIeR;j`y*>NAz0#|`ym?uIz{<*MQ7IDESP|TTUgaD?>es(DlRu**hyIYN)WuWB;e$7135YXV$k}Y=#LIQChZREBT4lmzR1_z`t4$|<{acK1^clGO^Zn`0sFoL@iFz6d8-Pw;Ahn_tHMPF|vT(c5=@t(aFeIh1reb$16kiLOZcF&yrkzmVTM?mM0%Y`-<h<#Y>ZnlH3^LjXBuIEIVCo&|fQwC*LmY59j(arn0?F+uYO4Lcz0fUH6kwOqLTmrtF8Q216V+rl>+ew_ir(C+OXefc#!F$SEI|*DUT9&?)K(R%2^2QaQ+8VP_E9BOCu0nW^(|0ipCA(gq(B~fisrGuSnkfQck3J7#aS?%jBHAeK|IhAD+9V38_Ls)W|}mwSP5GQ6Q~9EDjlzRpci2fTda)SnV2tME5aAUD3*bk*bY9$4#c|$)7`IU3c1SBqUje7E?#F}8@L!92<6;0KZRO4ccJo_;Np12vGFyo0U?L4U<tq!Z3c<j#OBN1dfV3|A#$|7_?u5=p95(hixfyX&~a5!{DEiCAy=RTAPo}K>Ma(~OEi!YL4Qsh9(P+lqgPc1aIC;lomC3Xvm|A%xv<kL@cc6Qz>mpvU>i$7V(L)`<P79YNQ5S0JRY8t_@hDU$^kYvc&MF1h6eNV7%0thzto(}X?<8F9r4Ps7lY#`46xb3Lj?sYm(oerhzfgO6B9>RW=UHzJ3B!h;kep?3Pp+N$8hoN=J<mW2|Or4@dsNWNSkipUCdljDJ4<o01u6wSehu{7z-Ol?6$q5tAsnfsX_u59h<TTTJnp)G^3r3_rUlQ&NYB{@TMiB{ufcqjEo$jB}wfF)dj)+j{T+JV<~re2_+;HMvaR7L+6GV)^@_J8~=+?oxe1rfPFZ0yW1t@3kV06t!1pl3mr309PBPn)On4SH6YEj%`2Qf^a)=J<c{gdw)8=r05MddBcp^CIQ~g9s@Et%ERZaWaIyoEp3Q36$Vsq9Yzfv#@ODkIRig3+=A{<uUS7CQ!ym{${?niS_}~8U=YRYCKg&SFkT3_5+m6Y1(`?O~rmYZhxN)@$GWoF;$wE<vVbKsk?E~1_MO?5tj3^nZRy92i0sdft!B0C-Z<p}OGoUhH%nMt%xfwhP^Pm<dCC5gE6pp|ludv3@VWQz)7Vu+G)OSuTE)ONAxT1PPk9$TKdn3)c3i_O47-7du%1m-ikeO4$RNaG9NQh`K7~M<E?p0#ym=b1A2@|@@=4C=;x&`rMHJxU20wxI-fswY7GOfZm-7oASMu*0X%3TcIFSO^;XN<ZrHHuciC5-R_Mo~US>(Pra3vjN0xLyC|E5?A{3rj{Cd_akjxtTI2K!yPwcUB6xRnOy-C7+n2j*rmeC{rWP1%9q6Vk@MXf$19I@D)MXLZnA3n2;(d=85#U4LL^&@db<I7|K1ZxwWDi7@%@^l%}>3z2_BNs6@RMe$&WSkeMW>nF76{v`l&V0I6IcIe?O+1%xFCfSdqc<j2<y2u%$UQ>kY%1c2aV5lb&1V{kd*h=2uTD3Ow)#6`d)#Gp?{nE}vJr>l$813a{@%&W7%#pnpM<x?#(ROHhGscso&EV%?mg_)FMfBTYgs`*0?3<7<pX(XWJtOSE`2{`#o5i|6WF4N+PQ-+x|Hc8R&&Ob;3wUnkzqtU(C8x0nC>MO1m$43tli&ZkXP9;O>ioXG9xX~QNi#Dd~AK`QW2Z6WNVFwGrJlAIdqljoqVk#L%N&HQ0X*NOck)dalgURhT@{k<CN)98P)MddZZHs1VHRq6nl3P0H=kZ{`s~%{j<wu&InXw%?BE=@frg|BNWpCgD9$1nZ*{qCmt_1g9!2ELLzyag+2sI<+NEw*Gqg0rR;lVTO2MeqqctA)#fufJ$!|y;%+zdU{%+JI6Rx2%1y;UF&j=xSJGG`JS^Hh{s9!D1pEK2B9QY@%-vf>Fi|4IcZnuV^?QxVn!3>`4CR`Ht`u2x|tqKjl4x(;k!vW}LVLIe?m&n&`D7De+CM@pwwm|i4qT(K6_^P0P>*?&{q=LB6L2F*{LaM9V|WE0u#1m_!Oe(H0)j{J4AeAdZm;S<tXCuG^C?C44@SR`H}>?d8?Al$9`BvVburVZxW%I#eg>3<n!gt|`XSujv!Whljl)F?EXmtksfllBk2X9>}-K@3_x1ZEYJHv4V6I|oV_F@ndW0bD*)-6t9oMevBVJ&zhXRA%+38}jxx>?4#-vI|(0IzSLHWUCc5$tVM4e<MF_7r-P$0DZHCz2ubA2Us4;kZ`t<8@>yWB43_l4Q_;XiFxY46^tRn;4olr&00ojYU5P&A<xzOGxJJSt08I5tUO4A&QHUr8Cj0m2|G{7CN(v?5n?`p^@TIB$0V?~q0d@2J3mIXM_ht9x?dc*5v{0R&05@c54FgzVegJ<X0!-ogzt5LN*UIqXK#4lS1b_jSY5BNRZs&YtBzq!WF8kPijlSHYt45z3p<?pN`pAU?1C18Y0(^)8~zI=Fu<-><9w1Zf?=Rpnv4Y>otTcUykeEnS*aEA3!ur}O4h@QVwwb#4h45B0LwkU@D;sq)KItg`E76Nh=b2d(ldR6*zXD4;ROY;*nI^IAGwxU-g6}kHZ%9F76Y4^w!7Q%ww81thZo<C(?m-esQ?OMYO34Yd4IdjWgD_(CT+<+sD(-ks$>bo$d*FfNsNZ0C;C~+br3KZfiUz`?cx_^(w=!?8$~B{qo{rqH7cDsn4}q-ftzm~E~`}V(JxNuuvxDu;yFtg#xiwpnrfyd>=+ASKx4as&2t(?c$YL+_byxUSkY|Bn;!4CCgl?=PnQ-wPt;WRw((L_-7KI22UIktM7#!eMJ;P7pq2k1ORiRUxlrEc3Z6n8)jSHRNy2Dg!ej{$MO*&a8_^`#?RPdSyyHszgMX@Vooj&>;daJIX|ZHaJV<LQ9))v75%((*Mqws@*+oLORlPscE{v&i@y)hdY*7@)|I11yIseU$0@cb9Q?0aH3QyQzlQ9^E4VW)G0lcix%TJAB&J!}!<W^a7GiiphHR}JVAN`-d`^P{1`49j74}bjKzm601#(v@fgv`fC!<d?M-;}>#t^0ux&$}^=mJ)O^V#s8<kTC-L^hUITlGg{)rt1-vo5h<X66K!CJi>unDymJWLgoq7dmG#uM&BvOn64}9VpvI&fz$0~g@O~_umP!sRyL*Q@|{N;{(byZH>~(#J)r=iI_I)k+8h7~FWXR6q)}U%gB8w;53m{#dKBMO73R8-zHB9Fi@N$@Zx>7E-hC-xGxhf7Snt>X0VrkJ&SWiFq=n_Am3X2v5apfVoo?J;T>27XhwjcciaV*un}x~Qvq<X4$>{FD+C>gH^dx*H-`y{9z2YIk&JI`z-^!XY_UM9#EbNYwA6%;JaK&H}%(9{jGJ2r3zJ!wy-7;==`sGeQRsz*&qN_`KqWKK8_vP^=oNPgBFYaYySkTA5f(uAXER`}dGQ&;{lmPxxSHU<+L{Vg1IVA6Ib`HFJT}eAki8sJq@xmY#NK@o&?4s7dL6cG|nc^7+sxt-0;w9>o<a`q`yG*&sI+$5tG0cMSDFa+CNMkE0J7<r>Dqu%cRx-^|3Z6qtY|+7=T)QTKM^rH_LAsBmr${r~q~1T1;TiO2mL6vy5n6zU67{VpqP`3zPM#>|LyvnEIrR$s@tzRZp)>N}bWj}z(~q`z_LS(6oVXPlTv+RItF#IV*eoiS+=VhO)F$unp)^|qNOw)Vx(j)ZsVV=>bmwlifTG?ze0#IIHE2HZLG2-d^$ViubqQ)gEiLDfWawX#8u~v5VM2~LiqqC<4fhkMnDR<B+@0c++^s3MT-M3u!n*=u?hv^ym<*pno{G*s;RfyHcXim^Te&&?iUNlfG4P*Xez_@s>9&uDqqO36{s=OVHAOUwC*dzC3rg|Z9w0YIi;I0McyG#HtwP*ui&gaJSZf`{5?Ay1xi3vAorXWI&tx=C#V<TSrjEm<K@a8L-0r`+`?`Q}Nz!1Og!7m}Cq~ZEb-o5!iVEd#74EM%#}VM^r?x2!t5p!v(3I-;&1pQe!vhzT5cjA0h4vUe`{jbW*U@-Q9Br(m&cf&4YoT(1v>K9S&>MxdtM1+!hMy0)@890{PIiWAu}+%jI*7997;@8L;DnE9XUvFp2F8kJ&N4o_jq1QY0zXeU!PP1xvu$=zyNnNbw2%}<lRLI6F~hqOC0PRT*w>(40cY25PM#w~3{w-%4Y&!RQ<Go0wSngx!l@m%qL<qrs0?BJe;Ro95|o=FMEK~ZbG6w=@1t+b>^+}HP#a54e!9}TV~~AQ2QR7GP)5Y6)*{_!8Pqo7W@i4TmLr%Pw8XQddP68F(#SmYjc)*tW;!c%pE_Y<3jZG&C?(Ixa_Cg`>8~An*!33SGCz;W8x^dlFpf@~`#L|wu3}_~DO8>x6YkKc^&>uWwG3)=(S3JtGrxYj_Hx);ygQ(Ki{rr|Y<~ATw+G3RPFlwHi}$_c1s3r8$yw<DuaOka=^>mCw{h<7U-ql*K}&)De7vQ&Y~fdTuBaqV7_GqYjJMa3JylY;z?a$026}#32DRz9J#F2z&PV~**+ZQ~Z}+OZ<7dF<gc;I-Cc*UNJbeLutqEJ%#h4U^$(&8$=?bm{tEk~U^eQtbK_>b$^Mq#GdW!w6DY@QLoee^i6D@;Ur1aV|cb``=RBx7AG*pj=re$-GCRcbH1y}UvP0P$k=R>v&4t(%+AK%f7mD3ZqR`&;i#SuW=or8X<sk9xI@h~%k91ur8CVtzsU*iRt#8?KkM(M4Vx?f8qNA51tz<Ud2&)FGXHeRF!ELrCPrYp*(!8-2syh6>}I3REHPG<~hC{UVp`y}TnFw@}J_j}wNkJ@i^Kdj%a&ydXao&y-R>Ny6bHIdk~+AX?-AvzC2_{+@t9fh0SFpgK_*27`fTvJ%T9s}_0GzCEP{Z~5Z3cFvIvP!3bD*GnW_DP`*I-F<LxHEAeR7LNwNpC|6M>k(dyK>W5r_h=^@EM_?S&pf!^d<JxiGZISyD*exT+!R%HzaNtf3-3Ed8<qP`T5DZ?u0?hj#2I@bFbKuvS~17UiVwxPlnrcGTb6UatA<FLMtsp#tLsZV&}97VJIf20gjzdPrMQty5I~q%4?)$^j52ryLVQ~SmCl!Y{rsnAH*aqliE|<tzUFIWPN+L{)Ge8F<fZOPRbCpOnJ)6$TKvCh$Y<k>&1LO?>~#R0u7&CWtlNkiefVK5mnT#R51S%Q>ILMZ?U=7yI3Z*{?a846!fy~6AccK&a#t~nZmI=lCa@g41B2HFw;q=R`H5!1T_~2V}p&CRd+^ZG0O8q!lq}cVy9ZqlG&-6Nv|0S<(v6+e=*-H?hVVMCV9VF(6dxKlT0d$0O9uw%&P;h-NA{69OzvRw(L0fApBqs(q7~Ktft2O5Wic4Crg$R2iUDEWS}7VAIqfWf0`G);(*uDJK4Kit#;pk8tqGoe=DdXOle_rh&6%4UI2!|%cpzRMK1_0*Y3{>Ls|!lZLu-fnCcyLNjL`>koR#yLoe<Xqy>czE_)k;{B^VT3hx4EOGBpH5fFX0AwwS~vOxBjED-x7hRHX_(1~IFvqgXlNn^#TyT3V1W0zHLqej{~^qTE8lz`<+1=-^^+5#G7h`h6+WDHw-vmgV}cTH+=e>=T$+u8Kd)GeV_d4}Y&DY9X&gIYOTrs|g4KE^lR2DmQM{M*9E{zi2Onm31};x5(khO$zKcag<x6IGqJ;pWQ$wKX+I#{xcIW_%prKR&B`zxsAPT^Npz8u+u%fdJvyGSzj2Ti)Ov4|>ukI2wgHnkaD<>Glm39Ea`UqlW(Ev@)0~VGYH@GJ=#QbMMjNp7hZldqfIxK~bkIhLq^k%>DLg7CmufB5?2$73eoFOJgD%MN+BnfrbKhI-8Lp07ceWyrB+%Rv=g+Mvgw^z6NqjAzq6pTA+yD2vv*d<s2R%<Qm>uWuVfB?KJipD9$=95?H&QfJ205%&roANi+JMNH|dn1dqTvNG!@NdRG7Z;&$=1Pc6E*TR$409PcfC*4q?lb!li>nDa3G{$;mYU%8$}W!o$d>r~V_ofn&7lerx+)8}nYK{mL!x9E@@e7WWo>KM!TsYP%uX@fA=R-j+?*4q6_6?M!EY*Rric7x5yOft74ju-_Fsq)6Ug1*<H<&HKEdwGBOM$T>MoV?`IG(pRy)Pyb<qo*p-D*-3UD<bg5OInAdUoG^jmwuT^?rlhtop(=lI$Z@)ft5A{PPnl|;GtI}1_PTFi`o?29<SUSe!>{UZOhraAqLOK1WOJ<n*vLhDVE^D5Anj1wTL$3T0~14HO_SI&wX|<YJaK)0>xYdi<oHiCK#-$WIVGXt)$HuR?>1e6rx?bMXe)lYAr0hhBM+x`EGq`_<rw^`V<SEfVw3^MmtBQJ{8UZZ8_V=0P-e;0;kSVpPxs4EU7xr&hg8KQMf_s&AjGfQ!og$!#pBIf0Hy1F1{Tt+qzrSLZerB+xs@<_wC*GRrh3xB*jbOJDT_J;^oy*MAFgW`>?P?7vqFg+<z0rVaYZIy;JCu9kt*1H6*c^tD`^i&)(y&!vd2S>>O1zI7Yv-OtZl~nvDF?z?uL>z@O<LoPczgEJ0@xc{d}}c}v{BIO-Ok4_k`&-Q~01%H=55=tm!Zr{3p{>*=q7X-3!;@V`i*!!YSrZ{SHl9zN9NuAU&A#5;iz3B4uoKca*(#AXZi1Q<T|=gq+km;4y{`%4GjrwX917yYuhyl22cEfcyY_3l{{MdEhuWA}KPBo1H^Wu*-nwfUgKr<B~oBscjO=NkrU1qMAvbnZB-5l$XLMyZCrl(?f)D~5!JO{tLAMq)TI&K3+336#4O74?1ef}pc`S6UoLNN?o?t4yxEA*=Hf!q3?UB@{iKAAcRiuc1^%ak1U7!3+2PMzvpE9s?WB25%10D?-)}+Z*2OlEyNeYX{>_+Z6q@6_)hDjberwt#yl2R9Zu2d2SD+1CmM#ZkZ?GuMKlQ5W;g@UMXZ_&#+^K>5*~h4rv<mv+iPHJVS2OFq2lFHi*1Uw}LqW1zK8PDR{Zh(rQJkxN*~OY11Ifq81?EKiBWxejCRx&3IFxyP>>mYngViW(t!s{pKotVfn8_IJrMx>aXmMV^<4TPR^A<vr)LHO9*HlU}TiQF^EmAF?ut_-uVTcYvE>SDbtZTz0zccX6e`V5_1dawc6Ga2F2Jj|D#;dER8B&;oJl9iIq*S7FuP!C1yIqJ2R>t^2wZ9O{#VjKhPM1XGEA~-~UTK>CoSY2Xdh{9<#z6ayy~desPLN;bXi|^=jy!eb#ad1D^?5gwj$<8*gt~GcoH)gvyl6{k;j+(wvlRx{Ro`8XWfv{j~)>wWBF7nyF1kU0U>727rMl_BQSEuu$&qE^4m{r5q(i^S!{^t6+hg5M8jq-%hx?02BTsy+u%p-0A2p3n_`5z$>jF$($fzv5<Q&ewzWN5rRzN*<Qw`*Ao}Z1PUmIM`P)85ww^<xzmwh6DraL6je)LcZ5u5Eq|U$E_(WvjUg)gm8$oxHgSr8k}e70qLAv8C5}{s*@HH<@VGhr*6n;9P+8~l!xJ%-ggz4<YaT>Y$yynq8Z2CjL`X{!55T2NDui1RPOeVvi0pKupC2A%c&$xRB<?l5y`o!N*KH>sIUEmls}LefEKdh(e;?ADU_@?HOO(6$4|iv~0}lP=m)q^v!=tjHj2&d5f&iD2(Z4o8t%k0nA4^4xoo3iK554W?09&{LDf(v_xh6({%8JF1=pbSr_E<5b)Wtg1HnnF#Ps2?qH#qAl<;E@(8)OS!fjpLJkr5NuXc)3v+lY~K+W8qhdfn!-`Z|P`KFT%jTgwB4V5{fB2_pKicU%2(K__i~u%Znd2BZHr^n7{G=k+Ww9^Novy-#0cJa6Ak)dq%u`42+}5A=-5^NP@K$wNW1=M3)_#r8KU%B$kSJ*YCG2Wl(JIF(>8z_lst;1Rcf4C(;`WZV?>R7Km`D1naPCXseyD0J>V<F1fo4r026MX;u^LM0Aw1q2;S<}}reBcUcYoYf~a4l$WWU;6v=6O@KfAyW6Mb=2D{B_9(YqZTryjqCI#<r*kHKy|2r|J}^1?)%ow?iNW*OQh0023_Nz5+|dD0sxyDB#g(fqYSC4cRHB9U#bxk<u7%eb{>#vK*E@eH6>_B;7}`oyU%;Kb611Bm47|FfSH9%vv9_;W#oh+x~pvVZ=IQkf+oR>sAb1*UVzsDA=`DDoVLG<d4?$YxR(W-_ZN%t${wn!$!3RD4I_1y9Xn0|k*Xut-eM&nhaR&ZPTicX3}3H3-#LhU?LbPm7zk3+BEeWw!kbNLz9~ChmoxUeKihUom*i>MDEME^m~+WUw>98=5;iqgp8Pl&hLMjx?IC_p3faUj6Bb2S1-(z{bSkaIMqB8jtu$)nNEQ%EzNBX|g#F`(nIN!I=r1<B%Z|HurSnA-I&p)?kq_BTUba@oaalg&afqeWMc)^1PcFs~+rp;ZILiLqXf#cchwdcr>O!bK%A{T4%u;=j=`b1~{c3vvQ@4qL;6rL`S~Kw>x(<=skcp<&StpCu6jk}(p4TxgV|RIMH08U*>_PLZspT49IE2u#&50wdC9ASUv_|jG9%ftRvfjJ;0MV^jTb<a-TL3984vTch@Z}YS#ev9t<vMo=T7e@hX0%-11&NeC4i*JZdDO3jJnoyzBWU(Z&HiHZ5&RG)2ukif5Bqb_DPD?dV6dhE1<JP|J=}DL$D-(#M#kGuI26e!3`K+jJef#?4i<coT%wRn=}RCyAQB6MYjo%kj!ZJrBNL(UP9{R1nZ=(Rp$K%wlVY6CkNh<fsV|S9)(9!fwl#G`vBq^eSZ&~En_gcMdl-DHyw}j_j{qLr#o({$c%#MLO^Wt7$JODbI_L!2s-iun)}#0*2|5&8Jmt|1I_YEhr-QrD1!{`wt$Qb5y7j}c-nxTffjjJd;CX^&Bu#Bd3>zk^J;s)jVJc|tYBBqH?{s50`<ZUIrw3{`403W{*&x9riFepiuvk@C&xWEhO{;z_UyY%%Ly-7^40_%H@Ms!R&oL2hb&`MU%sgCLLGG~ec5gsCn7y7p5FT@-q>j19f2WA^Y`{XzJ(?K;u~x@GtQD11I(=PNXI$4+>S`OW@iyDoPIGN4@O&#3alSSDJtEGzWtMH?H#too+Z49Hf1bzU)tz^D)2|T<6C^pl1FhN&D<x%>H8`LdPL;7<P7nb+LR!w0%8Yhg!6dNQoI^WZ5-&?kY^!Xziv==uJZdO8TaH<JkV!N8|CpZ56TySk<;?J8X~z&zil;Wmt^w0@7yWsvm)qk6ZG1M%XTTcJ5&F5YYBmQjuQsVCT3*Lc<$T87pZB&u3WRO)b#G6_N0lE|%lh4Ou!2aKNL^<!NTF_GZeMl$+f$5`-7_+BnFVU_W0;sScm6=z(gggD3!;N08mEM9?@}}e_iAv7iUBl(zTS%kB}H^uOVR}3N=uS`5;!tZyueRWS1aB6;?3^Kb#4y7lxsNzt|8%Rl^hX(AtuG8lRH%wrXZkCT8!#{mD~k+pDI}7l-x?;h@(h1Ftw>)#$0G6Foj$wQ#l7}hNi-|6cgdnv=zwt#_iY7nDk%YA4@=fgLwif=xs-+HKODU`AE-M$jcS}u0`u~(aVTR3q3p}SItii)79w#zFAhO^&|OnlX(G|r%v$tfi%5>TFn%FSHIBv)X`#HYNc#`0IVd2BY9HqYf<WVFGa0z*o|KBm@d%Dl&;Kjhwj_iN3b%5+}J<kXDSo0G>sna$(fw;4>RpFe)jI}&AKiNN}PDJdPJV$cvM;%Ho!K2nI-ro37HZU2K?&kw5^1$f~CzYEwz2HEjs2B{-TV!2?tnOi>$t(v69RtvuT-wo|r=a{%@%!)d&+jiDbdbo$kBM`XowHCAt66X^^DiBgB10jzlYSzaMx~l&04xAE$clH`)>|0&qsrC`!-@MZ#Q^T7mu(fj)PlyVpRpTI@-GO>2q`>z%|Pwn93p2&)M}J1)3{mBFCcnktiI5-2HSziF#vMaKc$U=Wtsw2-)%w8yB~s*MVpb+=h|x8`-$q%yfnnX1L4F;?qVVrV#GYq8VA3{4@NI>o`8et&yj|NiCu-6o)6cML;hC>5E*^jtEAWEQNfRQBy|4)7a@znF(HoyKAu9*Q3xCMu>dDtDW&;88J+$cg_CjSA*)Y)w7IsBqsEOBj`#i)DqVBn3AKPbezrxpTKSpJG%5#J-Fz{<?fAC<Mlew0romNo=P5M87U<)0c6?UzgUgLR3V~nNKMy=<(g`Ji(}lx|s|;6V1KEAK1b<j?JgF-t4!5<t;XVZvYvJI8;t#6(BxR3A->2pJ@V2p(LtBdi3+DNYdeI%X?8vV7m3(C2lvd`#UP@m6(UZDUa~PG-M7b<M$+^w8%gs%{aS@p@F|tC7^DYowOV?vJ`>nzDHcGhdQ-OVuAj;uXMc4c_AF!!?If{D%sS*b=m`u*n_p2REPx`YG~Knc^{A_-d*Qw<fM23wBSRTA{|)+$?&A2Cmzw;;V8nW8eWR0La<;8RHKD0*9&#Eja@kXNjTgHTNg)gLS?`ss><l0S@mr|E~yOY$B>yB0#8<}EtjGBz+4G1?pZ+y7_76pbAbMDvi4j?yfbkbn6NcsmKM(6C<b6;Z@^Ut+|(kBSjot$k=Q2$<`{P3AAuNVw`xZ1R@FdKYKXJSMy)^HP`j4JOC@nqp{MpCco|=dnohVYs^q+ifr>#N(yU`?XB+}hb!wSv)oHMwN0fh?A?x23xKB5dv?OyI^?-dMT&AJ&6o)p#pv{OdXp35Fvt?)2q+|OQE7QVbYe$!tGZ62BM~^N33hfm&#WsklGvU~>Wn|W(WBZQead2#%$XZdT&+&v}dvxG^t7(dDph?-p!z(ECvNj#zS48~|9N|=CK_?UsdP13btLR;}G(|Y5NSknYxdM%GPOX#V432GDq7XX(6km@XTd&eV$yrZq!)mI@hF4fu(bK@`4yuIx61-b_5pNV|Wn?sMVod`b)oEZXCn3hfYMBV`l7gI+=5G#(g3XT@5jrr7WkrV|u^de{&0eB3FIXh0Ob&6kpOWE%LxzWz`tUFobQtCLEBA7Zht8vp6D{~SF_yEGFbUDK#_99BcL!V&8Vnl$)_JZpZnUz35|URn(lT+hVox0PfvjKM=)YRvLH00)#J9SSJdL!}r;)1Idr)-anon9Ud}h8^eaodSwIQXQlu_S>%qcC+bIM@*P%zBa4ly5zgV`euFRhrv%OG=5nVF`5SY;>a$>y8Y-bRr_mD*U-HbiVafxW!qWYda2*~GuS9$0ymvbSCqWUogbcUo%W&cSb~2CfltkMFzfBs~!&=8I7BF+!7QBB~<xHyb%MRXJf&H+~v(x|6gOBYulI?>Xq(Iq|R+c#8zLej2y1lXKNzpGZ4N&u1~sdqoXwwiXv!nS;(p1)IRhPIy*C;SIBe+Vc~IoR;%9IOG@~I|V~zJ85w%fnx={Ia-|R1bE{{%Rd-zlGm8%(YkC+4sU^T=|_j0)EqnLVPyu|hXcVrIQ&wIhdoTam8R+~EteG!H#uS0p{a129n{ptzQv(Rr}U_FQaz8LE^1^VFTAj^lgyy|n4}zVz%yDz@j^~#!wPqSxLNc~jm#+_>fcZ_9`(>QtP<zc$t133q7JXZ1ncZ1H6!n>Vr$Fp%A29+`!VTe05_|KozBRp)!9-KUtvdB`W7WYM}L8}b(*TJH#?|t!{(N_ce`vW-JaSJQinl@SMioABB`CIGFqrRXD4ZYOVpR2Rj#KGdaE;dP?IYS#{%T*jn%z6AVJQQj8e8OQ*uOE9X<l8gynOZ*8IXsUsy58TW$1LX8V<@J6BE+I__cttD%%O;$e)dJc6}d^<yl&Xbuv&wv&wBaEq7#jK(a|Pnna|B5zG5L4jkaN!7>WGV17q`VDhXAHuG?EIbTjv?>Y*Ov!cDz?3pN{;w9@#RIZcmTvmr4J~+1NNSjZfJv;4slxVVr%C~>Ki{$GfZBx0Y<db+DH1qrkR@PU`#xqgg$f%G(0!dUOrK4(lJ_SEf+T1p+2KKcfxI!;ObUcsL(6b<JP880r7~*6mb%)o_>Hxgd4XHN^8RkKJeiCr!$y@-6}G^t!r?EhcoJ$bEaYXlpFJ+soC&pw(bhWpN;lz-hyTUk0<J&5gbOtlgL}!gQpP|hX5kjN-3j<B(ALvnM(<w;6YCOicIVh>8UH&nJg}(Yfm8x=)DzI)fsHZ=E)X7-N=fDq3aoJ%bD31)fK$l;U~t!8+hFG0Z;1Z=7odS0j<U!=n$IZ`C02r09v6<g`BDsQbx#QjMhM;0Y=f6o^eQ1}C_BUfB*eqU+H^ey^S$Tv06_-Q!fZiLuJQcx2#TYu7V|ekYMC36TEga*GHJ$EPczaMuHNbr-o|M(9K-~cjlu(aC2XiEm6>dKkrSc)9BAZb8;&?L<u_3^4MCKXl;NvHlj?$uIzpf&HxUn%6c1|H#Hpyih&gnV^}mWe78eYfq)6jIomPqlNtJwx+rXUTm|7G^BYeukkG({cP4>sZ7r+6Y_cE@%G{#m*6Lwghz+_5HD-XLMqnJ<+KLj`w4)oBFH|P}#joJon@t0pD5q$xLqb1Xx^(vUn_v_!9&d;c$LVrjtZ4si<JfaNVIE~>2n-RQV8K*S5%c@j;vor-Rd2Jdc6->HA|Bdece1Ez#3en#s(qk|%7!Nd-)N6*5diC0#*+zyNT|%2C$JQb$i#x5*loU4q5u_yU$A4H#!o=K~&Pn-j^BbIxg62b1K6?0}$IVAU+Of95kI|2@H#4Rezz~^|YVc&_=CRp8U>_KEHET`A3le6&;jl2=ZS`m7aiBDUaYRTEHi}^vxRxGoNF|q!u;$QAa!A}HVLk)FFfGGrg1Fk6k8;xx(gd0ep!$m~ce|!YERVDfH79ea5asbBrahU=JiFrSHlz2T4gy6+OKuvymeD%vj=Y&vs1jHZ0Rfxz1rM^R9Mmrte2r(X(wWR_7$TeQNcl*G-0}%JLO+Yh7_Wrp8)kG}$EqIrGkm`{_+yaLKZaRWk0lUlE}sg_!hL_}r*5<5zOE6%3;LtwC5`(1<fZ!|P>X5sW1E&Rfk9BklN0<J30_z|6;$Ca0i3+FmQFteOP`8TSn@Z!?7@L)i-}XYPY@0;hLa(6XB^Ltgm;XJw9U37XAdbE6~q|*qs=m{N_eXENFF4tHB_L9wjD9bNJXh7YUn0|j+Jeb8Okq_FaInEqDGd#AQjyu1CQ7PZj(hj!TATo5B59L`-A#^dJh-(L6yA}b)6=#G&gX_RZu2-qt2clGs(|~X=}K|L4BfM>FXe^2izW;%uxOWJrW>zh~}SC?yo^^uERHM()1IlG(@wAw>SE^dOXLE5c^jD{hAobL_VdAwm9s}++=3vMe^~(@`){zPvi{Ce>n9#aO+2p)t}dtvPy&l`6Ken<?f`o+`^33FLBvI-XIWQ94S69oB%iQ7mQ_#DK)bE4e!ww$}*=rxw*Rc?Et+GlRKyyzoi0uTSgBje-q%jCe1ky<nEeTt`6TO%!3o6`qKfyEcF;S3@}+~L&Llre2c}_MCi$L_Zp9L;~=FsZN!+9YrD^~Xw8-{Grif;(3<icVNx}r08A?ys|g-Pk?*-+ju1crRxP>1dcKg6I(<2O*!GH5{Cwd&wb!`6Xls8sS&P5%`GSxJ45`aQdOB%(75k7{2BW(;2DU8{wM7upGBW#WTgF53$CqHyK0FylfpLhh{UYl3<6Zq(lBs9{XQfhBaRGx+i<-9IJpbCQAAZ03MxM@#fq(%lc?o%YynN@y-bi$jr{duu06>;e+1*Fznmc|57()BJta_&#M0auL0QLC&1%)k+x7J}r7lXNa&k}H%Mwt0eh%4^+l_025w5U;g_wB{)_WVfJ6UVIxT_+=u89(e4#)|9I9GJdh&PnNBVSl;5GCMFC>gT6WUQk<3>pPjT6rGv{O{j)pX($uSvT5Vo{hO;J{ASIs+<x}O8(JiOMBfo_B>^|mhLtj9`it;MK7Xm2j+*4Ft%^7?QSMiG;zURjF+b()5W5;|fmgy#BnSPMRs0yLuy7)V_tZ6NI%smQv`1nYHmtO$fNlZkC>*Av`2KlY0KeQvg1sXc{E``|+!+o(UbE_l!7t)=&|+E&cYl+HpVyhf@T=A*fIv>Y>QO<c>6Xw+aRjjEFx?s}<;Y|_fe;!uBoXlIBdfy+9}3BEt62^#W*(~WFgQR3j7gp`w`gJS@`O%^;N<L}Y?$VbMGir=q7C8YTgd=k{)3iQxJ2&5J*4{WGxQ*OwXSpxJ>u!3|7M=2&x>n?DBun*Aa$HNYfN0s(xC%9kHv4LEt9w&uoHCC@x8r0>}3h1CuI05%S@I0MG7`qDhr{9>P_4W4@-36;jJu>`qT1Z;3yYBQ<eW^KwinR9I%`(Fnt7)??bPf$oyNO8I3IX1CBu~0aeUUxgt_>7ZCoUm}6nm2ap@sKH$-qJfq|__9GU<FEaqgNeYewqF<9^Yl!ljO)Va}8_nK_h2bo?LST#_)&wEhkkNclCS*GTpjvW3Wv@#h%zk;;N@&|)3DTnj+&f?jq~cZrNZxiBlv-gcuyMwk^m%|RSi{9{0_7@fMsxmXcwo5$@`f_(Oi~V@N&+OJq$${Q@Jlv%O@V;XpM;t!9{rY8nA=e45-JC>TMcf7CfN)KhIo;AFBnbK&f<1^{pR(jLEg(<kcr$okWrLjIU=Jd1@(%63jtg1fd=0627yVYAEj@XHVW?9GFeK^F5%{Mu07cc0XyW?1)cy4fThrQ`m_?3HEUCwi`$*)oBjF_X9widKPF4pBhV9@mRXO<JhA$GTDTtvod*ZyG=;vFerdNF-sK5_<?0gVK3%r_K<a*P<C(fpKPUg-3ih}<`(Zw5QY#Gv<L)eOTJP46@4hbhsWahoeiKAUs!j^XUDPrI&}}4bEz~KYDQ^HPT;82eRgE){@+n##^S?+TJVhbARP&lw#Ga@&?e1(RP}=jE#R;rYwb^oaCn%miKGjij`U6XKia;N{aTfm-`TpJ(Z&7*&hZrugw<xAm9@pXv7_n_pT%3fdrkUcc$^EB!+y}(ony8rnh3-;Rwj&XWX(6dJ71GTl{?^&_r9BD0&Oq7I68X;XCxy}7O-j(+*pm>J5w<y!4R=(tLt?#Ey+5l@sO?FOw>`E#T^dmXB-3Drc0nu?&}PP$&k6oOg0hAsVE-|rZEo2XCOu=vHZTn($4IyUQZVY5nrb?+w2ottfQ%i(j#NZyk#SI_G2k$Y)vM%;8TG(BAQ*`3)mXF91Ar*nQ-eWy9^Wdf_&RX+pMmOTj}HbmAvlaE#|p8a%Hz)whzF7V8I1>lo_>h_&lAG~w$Iz_DBbTdI`|=SK97$F!BvbyYiZ<PLbdD>7*aAvdNN*k(klTuQw1P`<d>*wn3`#86uTEo0y!aLGeKhpL>+MTs%&xW(9#>INX8bl&tau<(n#yk8|KOzAs3y0{@}hKg4-M*gyrcYN)Wz3tMzA=G){h{hH1}ujRjfX5RsV|Fw-iKQ$YU9T){>Yi>DWRg>}sAeDR6)hO3i)@*RwbkTkMdX3HTyf<Cd~5e{YT+u75>H)b@iXprA^mec?ZGPU^t06VQ;^Rn$#h%<P9b9T&QZnK4DPb>3}=vhdTy$pd~@#xm}^0ULS+K6?~oFe^cd)}Av=}$MH*k;Efj>E3y%5R5sgjZyg{GRMhP}tr9P;K%Rh)8G&Pf8&EPgWFPS;K53sA!e9;br%9`02;ggRLS;MtCiP)#D1PeT|&klNH2SjyWX}mAOzqUa=^XLk;L|r?-36mqR3O4<;p`X|HKy<dg@Lh{i%GOC5WQQ2VAp+5vq>)Y^29X2_A`VYy#Yb<F@WjHy%?Z|4x|;3Pvs2$URfC|>gIT>EzW^$=xmalXTm`V2yul;sXo;>!VCW+-DVfOG(Op6T6hwdfUV1a|N1-d<7rX%Jg;I+2GNO6ZH=B0xHO;gd@-oO>rby_XM9E>tTT(_k{1wzsi*eLaUl;^nNAHHTNL;Qzz#tagEDaAU9KFeq@zDk<l~;V#RN2~aU}^RlFRHE!9CX=*y?ot5h6p(jL!hL%Prjlg1P1SqR`Yn3>c=z(~Z?c$~HjSRImW<unF&<m0jE$#BA_5JcArV#9&_i{%liFKE+&{H?y5@1EE1{Qk(3G`cXmJ!09Jh~N+c<n94*Pd63Rowd0b~TG9?$#9H8C+a?l^L85aq)SIQrTGzb*OG_RxK9`n{b(yJFpV4z;&oVW;V`VI8f0eMXiCl#p2yb4h8@A?Zxd@u6OvB6bHWH(iVvev>n2jwX`T|vu9CZJ4JIc9ug9QY#=9aRJiu59gJbObm?v_<0$u68~8{{wRs9)!!S2iL3>G4kF5kqxecu7Cd4tdC%CyR^+@QuSBE-ArCCSDkK#v+O3m13T7iSofL|A1y)e6h0_xNWQ}JWr7dS38<K1$Jr=<Zi)X)<dv7bho+S7J-U)-L&LEughE6%gQRRhLEahzI4n&>*@)WJ5Bf(sfI)4VVzppYycef8i(fLktM2TB@J167on+)|6s%QxHJOchs^51cbRBfvq`cnl#HIt<F(VOJ4OI9{RII#T&i?3<USQuN)6<?i#{+r<w!OYfd_+nYlO<F02GWZGdS(^;9L+K9dk;mJrQf4=>4)oZT@C?8rAbD|oXv<40yj3f=nA}V;XHVMwOs|^Gjj83e(i4dY97$I!7@VFI!E{XfotNZ2>EqiF`^`4@Jt#acAQ8kdJ&lkSc?n)ipi5=p3mk%|fvw28@@B&)WBD`g^_%9Fx<I>2$IE)r0b#5pWwC7xggw{jRenj+o>EZxIjkI6wCO%C-;Jd$Qqn6Aqmv!44R|+W0J}B6Q5vhXD5ru7**n}w9o=DBOh=0N9V}smLuZZ&?dLX;)tM17X!ROI+z9x@J+Yl=1vujTpZ()~TQUvfA#FG#{_tvhP+qnPQcHcJeXO4&9rxjf7kq4=&q5;68DC}Y@nBA>~7ZNyFVo*XYCVMa6-5y|FxtsZ843yn7(wi@!+FTwbj+IVFqJ$V)r;<O1o;qtAXh!R>(qo_e5~_?y-Z`tGz7~&HFhFL|i&Lnh(i#p9-_trZ^0d}Dbc~i;6^5QxF~XnEK4HFL0DS-Q{&r{Pdb)<MET_MMqMwP3eg>pi87<UnN}FvA_sf7aph3S$-%z*NboUx$qVlPL5=p`>H4r@DpvXw*Pe!Tpn&Ila)G`^ur(v){3m4^=$p^Md0&qr#cPO{dCg6%jb}C~?$@8Y5=|LlE0{0~TE|4E08;q{Ze7{`i=MEBB7z_31F@%m%5?LcAWCR&zFw{`EOiO2Jt=*TXHo(EC?~lDNTev{T?LrEXR7!?zYf9C#LbeM`KsQ(@cf99P;sxUGtzTA<j59wqe2LKiQ!(B!gR56+Km`!Q4N<?PdaXD-)eN=x@}H-h-IGB@I{*?&2|Fad#~>_;<oGx$l@r<6o6g@lGo#L4_6#x>PfyNV8pn5Q7pENzAs;_p^T4}Oz>gEuznjjeeV4g!R3qR;4TeSt5)h;^^d_n?JLriT9X!F`T^>&GFUj@w9t1>AN@i)QHolJ-*{6WW)LJ-G@&WzL2KqmLUyAyWLp-F%L;1NnzRP~ooa|6OZ1kRzK<y)#8)O)vp!u5@MZ5}qMF3cw8Uk=si$f~!Oj6OMg@KDbKA?>KI0AId=p)IMS@-ovGIwlVAuzd%KA46Dc)X{N0)ag?c~pD@!P;cOjw)H^M(!>7+7a<Qh=^1+nTej(M6L@1v(ALwQ$m~$3IEOA`=*O_5wy$`XA-?`DU$tHmT`whU)PmH9AwxNa;tdy=vgSc?Q$Xxh&1epe<-v9-li<8$8@t_^(R4ho9{s2lG0Y^u@h!MHyMwU5G$rq&AlYH5O}A4*wT?}g8bt&*^tPSWwVU=@@#^vsX7S|Wd#K~9j(@(;ZAkJF7mXp5MH(yG{`c@a`+y@CZ35tBNlXlo8|!v=MWVBOr%N&f93Z0;m7O`>5XHa?6s97TBsAXM+kqTBNcwGy5ww<31f#u=MobBY%)@mLkFupcK87c=l=5=CfGdxsJ2{IBAmxS0B}pp;2<Lsn?JhF9f3e#2;TZg6O~5?L4*wfGp?`V^-i^}!3^aQKp^cnz=WR>>namwAM^>x%5<2sKRo;q%m6z4<&L+yr2Gm{=P8p9f2{4llT5NX0F!XvB?g>M90X87m=z5{RVYi7DH<ioCE#_RDbV&>@ryyFS~5f%SP!_K$uLgfYrw(rJ5z8vNk#^2o6x>bh9Co|>|~9U@a!Ed{xqq&^idGz9%AQH6mEC1;BHN61DLJ?c2el_DZflT>m*ICj`1^BF`-`E8mX*E_>`Ad0INCuk}}z114kxiQ7oaw^aQc?K&#{{XD7+%g&ho*`osDen9|ZoEjR9yDzhI2YTjzutJO)_eW33kO<yrSDVPc8b57D`=5yr0W^ar;(jiU{fO~dqRWqS`_FvM=NveA9O^ckbe5p_7DE;Z-ENt%YB>(^Bu6?JC?2NyPwSRY4%+0{VR252B+em3swcAQ<wGtScI9vimII+n%7=lR*xFL>VO=5?KVQph(tiD3ecg~FMGjnF<oS8FGq`gSdg240JZ@$a#LT!WlYxNA=qB~V=pWp`AKw&!)dxvJ3yc*v$kW{d!@CX)JP}2Af-R^FB=sQIzZ9y5n-@vXerHoy`B+*h{qCPyH>QD((9kS4+@q4q`SI~!Y7#a8{0-AMRZw_<M(9_F|%yo*017!~vJQ0631iS?)V<?+>lY5?JxW=TM=*zS>;NyPTFAMfdtpB#lD^pGm)WJ8kcvfve;{(G;ChIYhNh#Vl$jcx~MHn!!QY-R7{?%X&;5m5Fq=ep(O#{VI0GhgP@Q4T*<EfO~X+Ckc*kLuqXNzVlhKO-WHT$GWEDGLDwcMzP*-v*8TVf~AqYl+=^B-(EthZ*#jv{JAV~*P9_uGxZ=K}C5Y9+d$lJ{GisJDe`YL+n&$7mmaJ#h^-!lj6+6gd!=2Nwqy&AKXCjSe<vU7aB|qPL;dlINgN>Ds5gsfk8}3^Atps(=+2SF|P*jb1k{C@kpxKCI;E3c8J7b?2g3oR<=g7}R-+7g4`AX50v*z)sWf#&qQ_@H>5fZ(1<NCGZEZYG1?rg127!yfR}YBSmzY?6^cjq0Sq_%d7rz1B&AHXM{p`yy@bn+!e|tUYyr7o@qLT*=E)(Yce~FQlVtHv3Lv$i*^gE3lIRTH-~rvszG#IyfosJ#vjTs%bmCd-r&+)S3(DJMez*egBNe1{cN6$UY7fBN##5dq=zHS&MinuD4<^9Of$p&_Q&aek+7PETzmP9SNJL_n%!ZcQY8Q=f{Y<!o3*%1bM6;>n1LZ=j@3KfuZUN818>U0#w@XieuX;J3|G3uFk@J2qXxmXfoovTjCDL)y^kGSx0qdE1&MtV!2`_Yr&s|Oh$0BIdGWX*>^epp<IPRfTwq6pY8SjVP`N!cp`s;;zWb`qTFkYoK#><oC*X5~_wM%<>VP!p6b4`NP~b0rMNJ8K*W&!!3^rbPiB*kt0-{Wk|05JBu}Rq8V|{Ax5k}e;uu=}I^MXN13>|Jr<I-x{Zwc}dEGyu#46(OX{&KutO9AuX5n|@2^IZ@poX>DMxU7q2Co#uI?g%AAp|u0;!l-)=opyH{!TGn|&FxCwu|~{&60zNajTtj+L--QU<ucE=$2H>baT)zd;N^-=hpB8qI|38m1_W+^7d^2%v`>n$e7M*E4K<qQT;CeP>)W7agOhN?xX~+g^vmvLW_0w9w&^^>m-{C|iwA>8Db8*SNJO&tZoqIr3g{{}?{<2(D~Go^^s1=bu53f;6E&;|*wu!>ZnuM4(9HPx){MpGbsGqhTNiQpv5Ygpyyr}y`{L|9Uz8-)K4K3mC@MD;>X-$!;${^d=JGQ3+9F^hwy+|FFT1GnP~H4DfXH+W8Gf!Z!@SSi_68P>kDs)`HXblW!scVuHEOLNn4*!;e>G{AcL149q3u#`R`KEQzoW{<sM2$=1f(`J{htY~tLD;kCz#YLukotvz}0BXg}P$X6VJxEYie2D1dA_NpRU2YkIGxR6AoR^DDzc$fvpkn<Tk?&axySfYPZ^No(*yb!^S6}0;ls3->_5M!rcG5g-N$XW^kShDJK3qS4go_sXT$Q{G3-x&}^^m*yUsN1ml|0EzFqGxg$8w{nW*=d$i;_r6!X&r<Dln9ne*31AB+w<gdUK5B<}18alx^*b-X5E~c7W%wnZzabEsWA%dK#8i9uc`jWN3XXlS`9nK$PTOzXt=EC`u1CG4B6iuKth)NOU3DDrQW%u?6D(xThygM+9*iOyt7eCK#j`BGC1--2g66}oTHC;-o2K%O(IDShNir-2WqH)o)Uy*j9)P=d1tcmPS&AvfR!=ClNlftsde`k#y&82cjbETG_VW`yFb7}zyd!hr!kr=!H1yyIdx*#P$z~9`GZzF!AHIj0wgzUJYv$^O?m+K&i(Ofg(vBDqJQ*3+jPTyaLgIAtLe3li5WtUJK7dvUdP^pdQs9Hfa7@WLLU*G1|2T!Ne=?u0<ZJ=j&MJ_Z#&{lZpg3-{%2U#_GW1j{=FbK4w1=;W>B9;iN6#|F5=n>w3=>qE-JXWh+^l)RjV+I^<sY|RHwY-<a7i7RTad8eL5UL(em={=^St12l2(l^<Ig2j_F3c2~@f~yt!5P7&S1?G9F1npf1j=!BCzeliMCMzdb@c4l=?OwK809lgKpdN-rP6jZaPut1NW@0K(|LTP`460Gbbf)#$Is$UFktMscrKbW)bhvRHZ3*V%{^PH9JvN+INwSz+`v0CaUUuJEvXHaV)u`Z-t`OT!{Q~Go5%v@CXSFZ>-R!o1-1Zngf$H5e8Z5!nBToLYOzyM0#Yiy$G_OKUT>1rgiACq!yae6T8u5~bT}Zs0Z0!SfD~_)o8?dQ-;9#dQG-HR40XXn?Q>&_0eidnMsy@Nqt9h|Oka6sbiaa_HK41ha8kpG`C#GVCO3qT^iK;;>M*dFs%`7^T<R$96BK(#aaAJKl!ulNyrkKD2N9Q9L!~+TM=*`MWe_-c=NO8oqTL@WhgF=d!JNgz<RV%sa*;$Miq+}?98n<z9$l>nW3fCgX~2*jq8HKR=tbo0hggSn&ErP&4&e}%m$@Fc@aT&6gK~Tom_DWAJ0YD*lgvH~N$3jg{XK+2RDd?&%DJ8*iBn|FqerKwu}?ek`@(>Wk49a5hFkezevgoo`)}4~7VF@a*}lykCfKU$%Ayn=zL$v^HmJl7_T#+th#0Kd<+>wCMz87ncH7;hZ<wfqOrLTYHoJ%=`|%ukME%)VO`}F<@jl7->mLy$2_DbzV-<Z(D=dY)uW3xmeor9*g+YCfSbo#kYn36y6fV%$-FR<fiL7;bDJe|Oy1<T`@F=}4qNaJLmCw&z;1R><_*GC-3@PiHOhS+ftpEjdb0Kr`X<=O51q@o%?l&!~;KLrbC{Acgqfu)?1iaBx&`6Bdmj{glm%{trHAW)wx<V!)^K(-0m$E{L@Y2P(a)JuaASV$zkOiT(ML6iyWk}=&&fH2HJ1Sj3BCkX<k*}c*Qprp%#;g;OfbwTm`OrU4pUNBkBj6MdE4`6<Jv=i>34TU;Q6bdpl1NChZsoIJ*O*{8BEils*PJ}15RPceyl?feCiLP1O!FWNDs)hn`VC#mDMV{bL>m!&CB}J&hr-0qTGG5*Pl9IXCf2@8CaX5C<ZFUU_jx(PR1mW{t~6gRlw)oHyxUO+%}~d$L8TAz#^b`y*PuHKYV)N5S;GCuH;z$@(?PTSTjvEPhn^8A!z&f*mzx>q9Sh@GT_jHsWN#Ydm?YkwFsagEi`3YwD}T;H2uyhq>@f*#VqFN(mR9h};HC!R<lJ$FymhU5S*n|vhX@_ATNPYtv4b0e@hP_9np~f}U4>3__G=}4Cq&Qx;BMo?p#03WMCoB@9El2y4IWDrBEz4M^1Yf7RCn>pjTqW;6P-m^qr&L{aT*Cvp8T>^?Tt$&Snz>MB)Frj?SX@LtP>T+CNv3zdQVA6fPatIYCmKG)ppr<6jFgyf|&h7s4a?4+oBYj#LZ_=J{Xy%Q+ij+@C0+0IEY;Ie!sAoQg6%~il@e*>+2Cc1;gf)*FPep6q|>R-wy(iy^5>x_+=eUgup13=iw|PRZkV5_Uqvt2Gbms*Dr#<U)H@&DFd@e=%o|702T?I;q}|O`|S7=AoV$h5oNsf2dU7(<vf+E1fTOy=&4rm_@4m1pAbqN4uSo)2yF#=kmI$QEIdy%f6T%y?=&Im>ce}y4c=Jj)UJ5gxB@7UaQ5cO-EskA_8h-cn*uo141;IB3tTMtG^v5ysLZ59M>ZsWlxzN;M93;N(58oiDUdz33l2udr3ySWz`*)3LP|1Psb7rBW=mzfwKuZw@Pb^Un~!)tsS&<A!NZ$Q8<kW(4L%+YTVEWuJ_I46+OzM_{{;93fYdAx-+;C6^%C+o`%e%$w*!&ttR^^m=peD6!G%`|WtLt+T$tl+)FGhax_Ui{S&PL+pv+GvDD+^~+FTxQH>c2@RbPj<&TP@35eeP2>h26{;rHlsc<`qm|Ge~vfByKlzdw9TTC+!wA1pn3`0u5k@gMx>yGKiZ`RN}^KLKvhH~8Ime?%W7d~9=S_IqXSdu87J$p8BI(ZdJ-g5uKJv|qzVw`VoL;KR<F@Hv82%Z>>u3+p5gGlA7AqCp(J*z%}ta{e4X94Jm#`EjXXZ4|AIV^juEGq~TtzhJF|{=x$V{_f8AoqRDGh4(M)d~N#0lZQR|@$72X`q+V3E&J7ubxzg*#j<@}M$dHhDe6JdzYj0&KeX|i)U2UT6(%n9_kCv>eguTJA2QBwi7E$XscuxA-v|F@8^>^c1!|uCx@fOox<AY>zx*GQw>HW'

SKILL_EFFECT_LABELS = {
 'five_board':'5속성 판 변환','refresh':'전체 드롭 교체','unlock':'잠금 해제',
 'bind_clear':'바인드 회복','awoken_clear':'각성 무효 회복','time':'조작시간 연장',
 'bonus':'콤보 가산','void_pierce':'데미지 무효 관통','attack':'공격력 증가',
 'heal':'HP 회복','shield':'피해 경감','recovery':'회복력 증가',
 'absorb':'데미지 흡수 무효','attribute_absorb':'속성 흡수 무효','poison_clear':'독 제거','haste':'아군 스킬 턴 단축'}


def background_skill_details(text):
    import unicodedata
    t=_compact_ocr_text(unicodedata.normalize('NFKC',text)).lower()
    effects=[]
    def has(*words):return any(word in t for word in words)
    all_orbs=has('모든드롭','전체드롭','전드롭','全ドロップ')
    if all_orbs and has('변화','변환','変化'):
        colors=sum(has(*words) for words in (('불','화속성','火'),('물','수속성','水'),('나무','목속성','木'),('빛','광속성','光'),('어둠','암속성','闇')))
        if colors==5:effects.append('five_board')
    if all_orbs and has('파괴','破壊'):effects.append('refresh')
    if has('잠금','ロック') and has('해제','解除'):effects.append('unlock')
    if has('회복','해제','回復'):
        if has('바인드','바인딩','バインド'):effects.append('bind_clear')
        if has('각성무효','覚醒無効'):effects.append('awoken_clear')
    if has('조작시간','操作時間') and has('연장','늘','延長'):effects.append('time')
    if has('콤보','コンボ') and has('가산','추가','加算'):effects.append('bonus')
    if has('데미지무효','대미지무효','ダメージ無効') and has('관통','貫通'):effects.append('void_pierce')
    attack=re.search(r'(?:공격력|攻撃力).{0,12}?(\d+(?:\.\d+)?)(?:배|倍)',t)
    if attack and float(attack[1])>1 or re.search(r'공격력.{0,6}증가',t):effects.append('attack')
    if has('hp를','체력을','hpを') and has('회복','回復'):effects.append('heal')
    if has('경감','軽減'):effects.append('shield')
    recovery=re.search(r'(?:회복력|回復力).{0,8}?(\d+(?:\.\d+)?)(?:배|倍)',t)
    if recovery and float(recovery[1])>1 or re.search(r'회복력.{0,6}증가',t):effects.append('recovery')
    if has('속성흡수','属性吸収') and has('무효','無効'):effects.append('attribute_absorb')
    if has('데미지흡수','대미지흡수','ダメージ吸収') and has('무효','無効'):effects.append('absorb')
    if has('독드롭','毒ドロップ') and has('파괴','제거','破壊'):effects.append('poison_clear')
    haste=re.search(r'(?:아군스킬(?:이|을)|味方スキルが)(\d{1,2})(?:턴(?:단축|충전)|ターン溜まる)',t)
    if haste:effects.append('haste')
    def amount(pattern,default=0):
        m=re.search(pattern,t);return int(m[1]) if m else default
    return dict(effects=effects,haste_turns=int(haste[1]) if haste else 0,bonus=amount(r'(\d{1,2})(?:콤보|コンボ)(?:가산|추가|加算)'),
        time_seconds=amount(r'(?:조작시간|操作時間)(?:을|を)?(\d{1,2})(?:초|秒)'),
        recovery_turns=amount(r'(?:상태를|状態を)(\d{1,2})(?:턴|ターン)(?:회복|回復)'),
        duration=amount(r'(\d{1,2})(?:턴동안|ターンの間)'),needs_review=not bool(effects),
        requires_confirmation=bool(re.search(r'hp.{0,12}(?:소모|消費|감소|減少|が1になる|が半分)',t)))


@lru_cache(maxsize=1)
def monster_catalog():
    import base64,zlib
    return json.loads(zlib.decompress(base64.b85decode(MONSTER_CATALOG_B85)).decode('utf-8'))


def background_skill_korean(text):
    """Render catalog facts without discarding the Japanese source."""
    import unicodedata
    text=unicodedata.normalize('NFKC',text or '')
    replacements=[('ドロップロックを解除','드롭 잠금 해제'),('ドロップロック解除','드롭 잠금 해제'),('バインド状態を','바인드 상태를 '),('覚醒無効状態を','각성 무효 상태를 '),('バインドと覚醒無効状態を','바인드와 각성 무효 상태를 '),
        ('自分以外の味方スキルが','자신 이외 아군 스킬이 '),('全ドロップを','모든 드롭을 '),
        ('ダメージ無効を貫通','데미지 무효 관통'),('属性吸収を無効化','속성 흡수 무효화'),('ダメージ吸収を無効化','데미지 흡수 무효화'),
        ('操作時間を','조작시간을 '),('攻撃力と回復力が','공격력과 회복력이 '),('攻撃力が','공격력이 '),('回復力が','회복력이 '),
        ('ダメージを','데미지를 '),('ターンの間に、','턴 동안 '),('ターンの間、','턴 동안 '),('ターン回復','턴 회복'),('ターン溜まる','턴 단축'),
        ('コンボ加算','콤보 가산'),('秒延長','초 연장'),('個ずつ生成','개씩 생성'),('ずつ生成','씩 생성'),('個生成','개 생성'),
        ('凄く落ちやすくなる','출현 확률 대폭 증가'),('落ちやすくなる','출현 확률 증가'),('ロック状態で落ちてくる','잠긴 상태로 출현'),('ドロップ以外から',' 드롭을 제외하고 '),('ドロップに、',' 드롭으로 변환, '),('ドロップを変化',' 드롭으로 변환'),('ドロップを',' 드롭을 '),('ドロップが',' 드롭 '),('ドロップに変化',' 드롭으로 변환'),
        ('お邪魔','방해·'),('猛毒','맹독·'),('回復','회복·'),('火','불·'),('水','물·'),('木','나무·'),('光','빛·'),('闇','어둠·'),('毒','독·'),
        ('HPを','HP를 '),('敵のHP','적 HP의 '),('分のダメージ','만큼 데미지'),('バインド','바인드'),('ドロップロック','드롭 잠금'),('下から','아래에서 '),('段目の','번째 줄의 '),('両端','양 끝 '),('左端','왼쪽 끝 '),('右端','오른쪽 끝 '),('中央','중앙 '),('列ずつ','줄씩 '),('減少','감소'),('個','개'),('変化','변환'),('破壊','파괴'),('軽減','경감'),('倍','배'),('最上段','맨 위 '),('最下段','맨 아래 '),('列を','줄을 '),('横','가로 '),('縦','세로 '),
        ('解除','해제'),('ターン','턴'),('、',', '),('。','. ')]
    for a,b in sorted(replacements,key=lambda pair:len(pair[0]),reverse=True):text=text.replace(a,b)
    text=re.sub(r'·(?=[ ,.]|$)','',text)
    text=re.sub(r'(?<=%)회복', ' 회복',text)
    text=re.sub(r'(?<=%)경감', ' 경감',text)
    return re.sub(r' +',' ',text).strip()


def background_detail_attributes(frame):
    """Read ordered coloured header badges. Repeated attributes are significant."""
    import cv2
    area=np.asarray(frame.convert('RGB'))[240:285,830:935]
    if area.shape[:2]!=(45,105):return []
    hsv=cv2.cvtColor(area,cv2.COLOR_RGB2HSV)
    mask=((hsv[...,1]>85)&(hsv[...,2]>110)).astype('uint8')
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((3,3),dtype='uint8'))
    n,_,stats,_=cv2.connectedComponentsWithStats(mask,8);values=[]
    for x,y,w,h,area_count in stats[1:]:
        if not (11<=w<=32 and 11<=h<=32 and 70<=area_count<=900 and .6<=w/h<=1.6):continue
        patch=hsv[y:y+h,x:x+w];sat=(patch[...,1]>85)&(patch[...,2]>110)
        hue=patch[...,0][sat]
        if not len(hue):continue
        votes={'불':int(((hue<13)|(hue>=170)).sum()),'빛':int(((hue>=14)&(hue<=38)).sum()),
               '나무':int(((hue>=39)&(hue<=88)).sum()),'물':int(((hue>=89)&(hue<=128)).sum()),'어둠':int(((hue>=129)&(hue<=169)).sum())}
        color=max(votes,key=votes.get)
        if votes[color]/len(hue)>=.70:values.append((int(x),color))
    values.sort()
    return [color for _,color in values] if 1<=len(values)<=3 else []


def background_detail_cooldown(tokens):
    # Only the skill header's right edge: never the recovery duration in the body.
    parts=sorted((t for t in tokens if t.score>=.85 and t.x1>=1380 and 340<t.cy<390),key=lambda t:t.x1)
    text=' '.join(t.text for t in parts)
    match=re.search(r'(?:턴|ターン)\s*[:：]?\s*(\d{1,3})(?!\d)',text)
    return int(match[1]) if match else None


AWAKENING_EFFECTS_KO = {
 '없음':'각성 스킬이 없습니다.',
 '2마리 공격':'같은 색 드롭 4개를 연결해 지울 때 적용되는 공격 각성입니다.',
 '3색 공격 강화':'3속성 이상으로 동시에 공격하면 공격력이 증가합니다.',
 '4색 공격 강화':'4속성 이상으로 동시에 공격하면 공격력이 증가합니다.',
 'L자 지우기 공격':'같은 색 드롭 5개를 L자로 지우면 적용되는 공격 각성입니다.',
 '방해 내성':'방해 드롭 생성에 대한 내성을 높입니다.',
 '가드 브레이크':'5속성으로 동시에 공격하면 적의 방어력을 무시합니다.',
 '콤보 강화':'7콤보 이상일 때 공격력이 증가합니다.',
 '스킬 부스트':'던전 시작 시 아군 스킬의 남은 턴을 단축합니다.',
 '데미지 무효 관통':'데미지 무효에 대응하는 각성입니다. 발동 조건은 게임 상세에서 확인하세요.',
 '던전 보너스':'던전 보상에 영향을 주는 각성입니다. 적용 항목은 게임 상세에서 확인하세요.',
 '바인드 내성':'이 캐릭터의 바인드에 대한 내성을 높입니다.',
 '빛 연결 지우기':'빛 드롭을 연결해 지울 때 적용되는 각성입니다. 필요 개수는 게임 상세에서 확인하세요.',
 '십자 지우기 공격':'같은 색 드롭 5개를 십자로 지우면 적용되는 공격 각성입니다.',
 '회복 강화':'회복 성능을 높이는 각성입니다. 정확한 적용 조건은 게임 상세에서 확인하세요.',
 '봉인 내성':'스킬 봉인에 대한 내성을 높입니다.',
 '암흑 내성':'암흑에 대한 내성을 높입니다.',
 '나무 연결 지우기':'나무 드롭을 연결해 지울 때 적용되는 각성입니다. 필요 개수는 게임 상세에서 확인하세요.',
 '독 내성':'독 드롭 생성에 대한 내성을 높입니다.',
 '물 연결 지우기':'물 드롭을 연결해 지울 때 적용되는 각성입니다. 필요 개수는 게임 상세에서 확인하세요.',
 '불 연결 지우기':'불 드롭을 연결해 지울 때 적용되는 각성입니다. 필요 개수는 게임 상세에서 확인하세요.',
 '초 콤보 강화':'10콤보 이상일 때 공격력이 증가합니다.',
 '추가 공격':'추가 공격에 관한 각성입니다. 발동 조건은 게임 상세에서 확인하세요.',
 '어둠 연결 지우기':'어둠 드롭을 연결해 지울 때 적용되는 각성입니다. 필요 개수는 게임 상세에서 확인하세요.',
 '구름 내성':'구름 방해에 대한 내성을 높입니다.'}


def party_screen_offset(heading):
    return (158,26) if heading=='팀' else (0,0)


def party_card_frame(frame,heading):
    """Map the team's shifted six-card strip to the entry-screen read geometry."""
    dx,dy=party_screen_offset(heading)
    if not (dx or dy):return frame
    normalized=frame.copy()
    normalized.paste(frame.crop((70+dx,220+dy,930+dx,455+dy)),(70,220))
    return normalized


def party_registration_targets(frame,profiles):
    """Only reuse cards whose portrait, level and saved detail match this entry."""
    targets=[]
    for slot in range(6):
        item=profiles[slot] if slot<len(profiles) and isinstance(profiles[slot],dict) else {}
        portrait=np.asarray(item.get('portrait',[]),dtype=np.int16)
        current=np.asarray(background_portrait(frame,slot,entry=True),dtype=np.int16)
        level=np.asarray(item.get('entry_level_signature',[]),dtype=np.int16)
        observed=np.asarray(background_entry_level_signature(frame,slot),dtype=np.int16)
        if (not item.get('number') or item.get('portrait_image_version')!=4
            or not item.get('skill_panel_png') or not item.get('cooldown')
            or not item.get('attributes_ko') or not item.get('awakening_icons_png')
            or level.shape!=(10,40) or observed.shape!=level.shape
            or float(np.abs(level-observed).mean())>1.8
            or portrait.shape!=current.shape or float(np.abs(portrait-current).mean())>=8):
            targets.append(slot)
    return targets


def party_image_png(image):
    import base64,io
    buffer=io.BytesIO();image.convert('RGB').save(buffer,format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def background_portrait_image(frame,slot):
    # Include the complete exterior, level/attributes and the awakening footer.
    image=frame.crop(background_entry_card_rects(frame)[slot])
    image.thumbnail((104,178),Image.Resampling.LANCZOS)
    return party_image_png(image)


def background_entry_level_signature(frame,slot):
    x0,_,x1,_=background_entry_card_rects(frame)[slot]
    return np.asarray(frame.crop((x0+17,357,x1-22,388)).convert('L').resize((40,10)),dtype=np.int16).tolist()


@lru_cache(maxsize=512)
def awakening_icon_pixels(encoded):
    import base64,io
    try:
        if not isinstance(encoded,str) or len(encoded)>100000:return None
        image=Image.open(io.BytesIO(base64.b64decode(encoded,validate=True))).convert('RGB')
        if min(image.size)<12:return None
        image=image.resize((32,32),Image.Resampling.LANCZOS)
        pixels=np.asarray(image,dtype=np.int16)[2:-2,2:-2]
        if float(pixels.reshape(-1,3).std(axis=0).mean())<10:return None
        return pixels
    except Exception:return None


def confirmed_awakening_icon(encoded,settings):
    pixels=awakening_icon_pixels(encoded)
    if pixels is None:return None
    shared=settings.get('shared_awakening_descriptions',{})
    candidates=[]
    for name,entry in shared.items():
        if isinstance(entry,dict) and entry.get('verified') and entry.get('description'):
            candidates.append((name,entry,entry.get('icon_png','')))
    for profile in settings.get('background_party_profiles',[]):
        icons=profile.get('awakening_icons_png',[])
        for index,item in enumerate(profile.get('awakening_effects',[])):
            if not isinstance(item,dict):continue
            name=item.get('name','');entry=shared.get(name,item)
            if (entry.get('verified') and entry.get('description') and name not in ('없음','확인 필요','')):
                candidates.append((name,entry,icons[index] if index<len(icons) else item.get('icon_png','')))
    matches={}
    for name,entry,icon in candidates:
        other=awakening_icon_pixels(icon)
        if other is None:continue
        delta=np.abs(pixels-other)
        if (float(delta.mean())<=6 and float(np.percentile(delta,95))<=24
                and float((delta.max(axis=2)>70).mean())<=.005):
            matches[(name,_compact_ocr_text(entry['description']))]=entry
    if len(matches)!=1:return None
    (name,_),entry=next(iter(matches.items()))
    return dict(entry,name=name,verified=True,known=True,icon_png=encoded,source='확인된 동일 각성 아이콘')


def party_awakening_effects(profile,settings=None):
    saved=profile.get('awakening_effects')
    if isinstance(saved,list):items=[dict(item) for item in saved if isinstance(item,dict) and item.get('name')!='없음'][:3]
    else:
        names=[name.strip() for name in profile.get('awakenings','').split('/') if name.strip() and name.strip()!='없음']
        items=[dict(name=name,description=AWAKENING_EFFECTS_KO.get(name,'효과 확인 필요'),
                 known=name in AWAKENING_EFFECTS_KO,source='DB 목록 · 아이콘 대응 확인 필요') for name in names[:3]]
    shared=(settings or {}).get('shared_awakening_descriptions',{})
    for item in items:
        entry=shared.get(item.get('name',''))
        if isinstance(entry,dict) and entry.get('description'):
            item.update(description=entry['description'],verified=bool(entry.get('verified')),known=bool(entry.get('verified')) or item.get('known',False),source='공유 각성 설명')
    icons=profile.get('awakening_icons_png',[])
    for index,icon in enumerate(icons[:3]):
        if awakening_icon_pixels(icon) is None:continue
        while len(items)<=index:items.append(dict(name='확인 필요',description='효과 확인 필요',verified=False,known=False))
        match=confirmed_awakening_icon(icon,settings or {})
        if match:items[index]=match
    return items


def awakening_registration_needed(settings,number,icons=None):
    visible=None
    if icons is not None:
        visible=[icon for icon in icons if awakening_icon_pixels(icon) is not None]
        if not visible:return True
        if all(confirmed_awakening_icon(icon,settings) for icon in visible):return False
    if not number:return True
    saved=next((p for p in settings.get('background_party_profiles',[]) if str(p.get('number'))==str(number)),{'number':number})
    profile=background_profile_merge(saved,settings)
    items=party_awakening_effects(profile,settings)
    complete=bool(items) and profile.get('awakening_registered',False) and all(item.get('verified') for item in items)
    return not (complete and (visible is None or len(items)==len(visible)))


def record_party_description_review(settings,profile):
    number=str(profile.get('number') or '')
    if not number:return
    data={key:profile.get(key) for key in ('number','name','name_ja','text','skill_ja','skill_screen_text','awakenings','awakenings_ja','awakening_effects','source')}
    data=json.loads(json.dumps(data));data['review_status']='pending'
    review=settings.setdefault('description_review_data',{})
    monsters=review.setdefault('monsters',{});old=monsters.get(number)
    if old is not None and old!=data:review.setdefault('history',[]).append(dict(kind='monster',key=number,data=old))
    monsters[number]=data


def share_awakening_description(settings,name,description,verified=True):
    name=str(name or '').strip();description=str(description or '').strip()
    if not name or name in ('확인 필요','없음') or not description:return False
    catalog=settings.setdefault('shared_awakening_descriptions',{})
    entry=dict(name=name,description=description,verified=bool(verified),source='사용자 확인',review_status='pending')
    old=catalog.get(name)
    if old is not None and old!=entry:settings.setdefault('description_review_data',{}).setdefault('history',[]).append(dict(kind='awakening',key=name,data=dict(old)))
    catalog[name]=entry
    return True


def confirm_party_awakenings(settings,slot,indices,confirmed=True):
    profiles=settings.get('background_party_profiles',[])
    if slot not in range(len(profiles)) or not profiles[slot].get('number'):
        raise ValueError('캐릭터를 먼저 등록해 주세요.')
    profile=background_profile_merge(profiles[slot],settings)
    items=party_awakening_effects(profile,settings)
    indices=list(indices)
    if not indices:raise ValueError('확인할 각성 정보가 없습니다.')
    for index in indices:
        if index not in range(len(items)):raise ValueError('각성 정보를 다시 확인해 주세요.')
        item=items[index]
        if item.get('name') in ('',None,'없음','확인 필요') or not item.get('description') or '확인 필요' in item['description']:
            raise ValueError('이름·효과를 읽지 못한 각성이 있습니다. 게임 각성 상세에서 먼저 등록해 주세요.')
    for index in indices:
        item=items[index];name=item['name']
        previous=dict(settings.get('shared_awakening_descriptions',{}).get(name,{}))
        share_awakening_description(settings,name,item['description'],confirmed)
        previous.update(settings['shared_awakening_descriptions'][name])
        previous['review_status']='confirmed' if confirmed else 'pending'
        settings['shared_awakening_descriptions'][name]=previous
    # Duplicate awakenings share their description and confirmation state.
    for item in items:
        shared=settings.get('shared_awakening_descriptions',{}).get(item.get('name'),{})
        if shared:item.update(description=shared['description'],verified=bool(shared.get('verified')),known=bool(shared.get('verified')) or item.get('known',False))
    observation=dict(awakening_effects=items,awakenings=' / '.join(item['name'] for item in items),
                     awakening_registered=bool(items) and all(item.get('verified') and item.get('name') not in ('없음','확인 필요') for item in items))
    number=str(profile['number'])
    settings.setdefault('monster_awakening_observations',{}).setdefault(number,{}).update(observation)
    override=settings.get('monster_overrides',{}).get(number)
    if override is not None:
        for key in ('awakening_effects','awakenings'):override[key]=observation[key]
    for member in profiles:
        if str(member.get('number'))==number:member.update(observation)
    record_party_description_review(settings,profiles[slot])
    return items


def awakening_panel_regions(frame):
    """Find 1–3 grey title bars inside light awakening cards, excluding gaps."""
    import cv2
    rgb=np.asarray(frame.convert('RGB'),dtype=np.int16)
    core=rgb[160:875,500:1140];flank=rgb[160:875,430:442]
    gray=(core.min(axis=2)>=70)&(core.max(axis=2)<=205)&(core.max(axis=2)-core.min(axis=2)<55)
    light=flank.min(axis=2)>205
    rows=((gray.mean(axis=1)>.70)&(light.mean(axis=1)>.60)).astype(np.uint8)
    rows=cv2.morphologyEx(rows.reshape(-1,1),cv2.MORPH_CLOSE,np.ones((3,1),np.uint8)).ravel()
    edges=np.diff(np.r_[False,rows.astype(bool),False].astype(np.int8))
    bars=[(a+160,b+160) for a,b in zip(np.where(edges==1)[0],np.where(edges==-1)[0]) if 22<=b-a<=75]
    if not 1<=len(bars)<=3:return ((206,402),(431,628),(655,853))
    regions=[]
    for index,(start,end) in enumerate(bars):
        limit=min(875,end+190,bars[index+1][0]-15 if index+1<len(bars) else 875)
        gap=gray[max(0,end-160+15):max(0,limit-160)].mean(axis=1)>.75
        bottom=limit
        for row in range(max(0,len(gap)-4)):
            if gap[row:row+5].all():bottom=end+15+row;break
        regions.append((max(170,start-14),bottom))
    return tuple(regions)


def awakening_dialog_geometry(frame):
    """The whole dialog shifts vertically when one or two panels are shown."""
    first_top=int(awakening_panel_regions(frame)[0][0])
    return (430,max(110,first_top-82),1155,first_top-32),(1204,first_top-56)


def awakening_readings_error(first,second):
    if not 1<=len(first)<=3 or len(first)!=len(second):
        return f'각성 개수 확인 필요 · 1차 {len(first)}개 / 2차 {len(second)}개'
    for index,(a,b) in enumerate(zip(first,second),1):
        if _compact_ocr_text(a.get('name',''))!=_compact_ocr_text(b.get('name','')):
            return f'{index}번 각성 이름 불일치 · {a.get("name","미인식")} / {b.get("name","미인식")}'
        one=_compact_ocr_text(a.get('description','')).rstrip('.。．')
        two=_compact_ocr_text(b.get('description','')).rstrip('.。．')
        if not one or one!=two:
            return f'{index}번 {a.get("name","각성")} 설명 불일치\n1차: {a.get("description","")[:110]}\n2차: {b.get("description","")[:110]}'
    return ''


def awakening_detail_rows(tokens,frame):
    """Read only the three labelled panels of the game's awakening-detail dialog."""
    heading=''.join(t.text for t in sorted(tokens,key=lambda t:t.x1) if t.score>=.85 and 40<t.cy<110 and 150<t.x1<700)
    if _compact_ocr_text(heading)!='각성스킬상세':return []
    aliases={_compact_ocr_text(name):name for name in AWAKENING_EFFECTS_KO}
    result=[]
    for top,bottom in awakening_panel_regions(frame):
        raw_title=[t for t in tokens if 493<=t.x1<1160 and top+12<t.cy<top+70]
        raw_body=[t for t in tokens if 493<=t.x1<1160 and top+75<t.cy<bottom-8]
        if any(t.score<.88 for t in raw_title) or any(t.score<.85 for t in raw_body):continue
        title=sorted(raw_title,key=lambda t:t.x1)
        body=sorted(raw_body,key=lambda t:(round(t.cy/20),t.x1))
        if not title or not body:continue
        name=' '.join(t.text.strip() for t in title);name=aliases.get(_compact_ocr_text(name),name)
        description=' '.join(t.text.strip() for t in body)
        if not name or len(name)>40 or len(description)<5:continue
        result.append(dict(name=name,description=description,verified=False,known=True,source='게임 각성 상세 OCR',
            icon_png=party_image_png(frame.crop((447,top+20,491,top+65))),
            description_png=party_image_png(frame.crop((428,top,1174,bottom)))))
    return result


def apply_awakening_detail_readings(settings,first,second):
    """Two matching readings fill pending descriptions; confirmed corrections win."""
    accepted=[];catalog=settings.setdefault('shared_awakening_descriptions',{})
    candidates=settings.setdefault('awakening_description_candidates',{})
    for item in first:
        other=next((v for v in second if _compact_ocr_text(v['name'])==_compact_ocr_text(item['name']) and _compact_ocr_text(v['description'])==_compact_ocr_text(item['description'])),None)
        if other is None:continue
        name=item['name'];entry=dict(item,verified=False,known=True,source='게임 각성 상세 OCR',review_status='pending')
        if candidates.get(name)!=entry:
            old=candidates.get(name)
            if old:settings.setdefault('description_review_data',{}).setdefault('history',[]).append(dict(kind='awakening_ocr',key=name,data=old))
            candidates[name]=entry
        current=catalog.get(name,{})
        if not current.get('verified'):
            if current and current.get('description')!=entry['description']:settings.setdefault('description_review_data',{}).setdefault('history',[]).append(dict(kind='awakening',key=name,data=dict(current)))
            catalog[name]=entry
        else:
            current.update(icon_png=entry['icon_png'],description_png=entry['description_png'])
        accepted.append(name)
    return accepted


def awakening_owner_numbers(settings,owner):
    key=_compact_ocr_text(owner)
    if not key:return set()
    matches=set()
    for row in monster_catalog()['monsters']:
        if key==_compact_ocr_text(row.get('name_ko','')):matches.add(str(row['number']))
    for number,name in settings.get('monster_korean_names',{}).items():
        if key==_compact_ocr_text(name):matches.add(str(number))
    for number,row in settings.get('monster_overrides',{}).items():
        if key==_compact_ocr_text(row.get('name','')) and str(number).isdigit():matches.add(str(number))
    for row in settings.get('background_party_profiles',[]):
        if row.get('number') and key==_compact_ocr_text(row.get('name','')):matches.add(str(row['number']))
    return matches


def register_character_awakenings(settings,owner,repeat_owner,first,second):
    """Explicit registration confirms stable readings, linked by a unique owner name."""
    key=_compact_ocr_text(owner)
    if not key or key!=_compact_ocr_text(repeat_owner):raise ValueError('캐릭터 이름 판독이 일치하지 않습니다. 다시 등록해 주세요.')
    matches=awakening_owner_numbers(settings,owner)
    if len(matches)!=1:raise ValueError(owner+' · 몬스터 번호를 하나로 확인할 수 없습니다. 파티 읽기로 이름과 번호를 먼저 등록해 주세요.')
    problem=awakening_readings_error(first,second)
    if problem:raise ValueError(problem+'\n파티창의 게임 각성 등록/다시 읽기로 재시도해 주세요.')
    number=matches.pop()
    apply_awakening_detail_readings(settings,first,first)
    catalog=settings['shared_awakening_descriptions'];items=[]
    for row in first:
        name=row['name'];entry=catalog[name]
        if not entry.get('verified'):entry.update(verified=True,source='각성 스킬 등록',review_status='confirmed')
        items.append(dict(row,description=entry['description'],verified=True,known=True,source=entry['source']))
    observation=dict(name=owner,awakenings=' / '.join(r['name'] for r in items),awakening_effects=items,
                     awakening_icons_png=[r['icon_png'] for r in first],awakening_registered=True)
    settings.setdefault('monster_awakening_observations',{})[number]=observation
    settings.setdefault('monster_korean_names',{})[number]=owner
    for profile in settings.get('background_party_profiles',[]):
        if str(profile.get('number'))==number:
            profile.update(observation);record_party_description_review(settings,profile)
    return number


def background_party_update(previous,current):
    """A confirmed higher level replaces the same slot's prior observation."""
    result=dict(previous,**current) if previous.get('number')==current.get('number') else dict(current)
    if previous.get('number')==current.get('number'):
        if previous.get('observed_attributes') and not current.get('observed_attributes'):
            for key in ('observed_attributes','attributes_ko','attributes_source','attributes_read_version'):result[key]=previous.get(key)
        old=previous.get('level');new=current.get('level')
        if isinstance(old,int) and (not isinstance(new,int) or new<old):
            result['level']=old
            for key in ('portrait_png','portrait_image_version','awakening_icons_png','skill_panel_png','entry_level_signature'):
                if previous.get(key):result[key]=previous[key]
    return result


def background_profile_merge(profile,settings):
    """Identity is the game monster number. Human corrections always win."""
    profile=dict(profile);number=str(profile.get('number') or '')
    observed_attributes=profile.get('observed_attributes') or []
    row=next((r for r in monster_catalog()['monsters'] if str(r['number'])==number),None)
    if row:
        profile.update(name_ja=row['name_ja'],name_ko=row.get('name_ko',''),name_ko_status=row.get('name_ko_status','translated_draft'),attributes_ko=row.get('attributes_ko',''),source=row['source'],skill_ja=row['skill_ja'],
                       awakenings_ja=row.get('awakenings_ja',''),attributes=row.get('attributes',''))
        if not profile.get('verified') or not profile.get('text') or re.search('[ぁ-ヿ]',profile['text']):
            if profile.get('text') and 'skill_screen_text' not in profile:profile['skill_screen_text']=profile['text']
            profile['text']=row.get('skill_ko') or background_skill_korean(row['skill_ja'])
        if profile.get('cooldown') is None:
            profile['cooldown']=row.get('cooldown');profile['cooldown_source']='DB'
        if not profile.get('awakenings') or re.search('[ぁ-ヿ一-鿿]',profile['awakenings']):profile['awakenings']=row.get('awakenings_ko','')
        if not profile.get('name_confirmed') or not profile.get('name') or re.search('[ぁ-ヿ一-鿿]',profile['name']):
            profile['name']=row.get('name_ko') or profile.get('name','미확인')
        # Icon-only colour/lock descriptions cannot be recovered by text OCR.
        details=background_skill_details(row['skill_ja'])
        details['effects']=sorted(set(details['effects']+background_skill_details(profile.get('text',''))['effects']))
    else:details=background_skill_details(profile.get('text',''))
    profile.update(details)
    override=settings.get('monster_overrides',{}).get(number or 'slot:'+str(profile.get('slot',0)),{})
    override=dict(override)
    if observed_attributes and override.get('attributes_source')!='user_corrected':
        override.pop('attributes_ko',None);override.pop('attributes_source',None)
    if number=='300' and _compact_ocr_text(override.get('attributes_ko',''))=='어둠/나무':override['attributes_ko']='어둠 / 나무 / 어둠'
    if override.get('cooldown') is None:override.pop('cooldown',None)
    if re.search('[ぁ-ヿ]',override.get('text','')):override.pop('text',None)
    for key in ('name','awakenings'):
        if re.search('[ぁ-ヿ一-鿿]',override.get(key,'')):override.pop(key,None)
    if observed_attributes:profile.update(attributes_ko=' / '.join(observed_attributes),attributes_source='game_confirmed')
    observation=settings.get('monster_awakening_observations',{}).get(number,{})
    profile.update({k:v for k,v in observation.items() if k!='name'})
    profile.update(override)
    profile.update({k:v for k,v in observation.items() if k!='name'})
    corrected={'307':['빛','불','빛'],'304':['어둠','불','어둠']}
    saved_attributes=settings.get('monster_attribute_observations',{}).get(number)
    latest=saved_attributes or (observed_attributes if profile.get('attributes_read_version')==2 and observed_attributes else corrected.get(number) if not profile.get('attributes_read_version') else None)
    if latest and override.get('attributes_source')!='user_corrected':
        profile.update(observed_attributes=latest,attributes_ko=' / '.join(latest),attributes_source='game_confirmed')
    if 'cooldown' in override:profile['cooldown_source']='사용자 정정'
    if not override.get('name') and number in settings.get('monster_korean_names',{}):
        profile['name']=settings['monster_korean_names'][number];profile['name_ko_status']='game_confirmed'
    if override.get('name'):profile['name_ko_status']='user_corrected'
    elif profile.get('name_confirmed') and re.search('[가-힣]',profile.get('name','')):profile['name_ko_status']='game_confirmed'
    profile.setdefault('verified',False)
    profile.setdefault('use_policy','locked_only' if number=='273' else 'auto')
    profile['awakening_effects']=party_awakening_effects(profile,settings)
    items=profile['awakening_effects']
    if profile.get('awakening_icons_png'):
        profile['awakening_registered']=bool(items) and all(item.get('verified') and item.get('name') not in ('없음','확인 필요') for item in items)
        if any(item.get('source')=='확인된 동일 각성 아이콘' for item in items):
            profile['awakenings']=' / '.join(item['name'] for item in items)
    return profile


def background_lock_count(frame):
    import cv2
    rgb=np.asarray(frame.convert('RGB'));count=0
    for row in range(5):
        for col in range(6):
            x=round(882+col*692/6);y=round(255+row*572/5)
            patch=rgb[y+10:y+62,x+10:x+65]
            mask=((patch[...,0]>140)&(patch[...,1]>140)&(patch[...,2]<105)).astype('uint8')
            n,_,stats,_=cv2.connectedComponentsWithStats(mask)
            if any(90<=area<=1600 and w>=10 and h>=15 and .35<w/h<1.6 for _,_,w,h,area in stats[1:]):count+=1
    return count


def background_skill_decision(profiles,ready,state,used=()):
    """Score only effects needed now; unneeded skills remain available."""
    needs={}
    for condition,effect,weight in (
        ('bound','bind_clear',120),('awoken_void','awoken_clear',120),('short_time','time',100),
        ('void_shield','void_pierce',100),('attack_down','attack',85),('shortage','five_board',90),
        ('locked','unlock',65),('combo_short','bonus',110),('low_hp','heal',100),
        ('low_hp','shield',45),('recovery_down','recovery',65),('damage_absorb','absorb',100),
        ('attribute_absorb','attribute_absorb',100),('poison','poison_clear',50)):
        if state.get(condition):needs[effect]=weight
    candidates=[]
    for slot in ready:
        if slot in used or not 0<=slot<len(profiles):continue
        p=profiles[slot]
        if p.get('auto_use',True) is False or p.get('requires_confirmation') and not p.get('verified'):continue
        policy=p.get('use_policy','auto')
        if policy=='locked_only' and not state.get('locked'):continue
        if policy=='shortage_only' and not state.get('shortage'):continue
        if policy=='time_only' and not state.get('short_time'):continue
        if policy=='cleanse_only' and not (state.get('bound') or state.get('awoken_void')):continue
        effects=set(p.get('effects',[]))
        score=sum(weight for effect,weight in needs.items() if effect in effects)
        if 'cleanse' in effects and (state.get('bound') or state.get('awoken_void')):score+=120
        if state.get('shortage') and 'refresh' in effects and 'five_board' not in effects:score+=35
        # Resolve an obstructed deficient board before spending a board-only skill.
        if state.get('shortage'):
            if state.get('locked') and 'unlock' in effects:score+=1000
            elif not state.get('locked') and 'five_board' in effects:score+=1000
        # Haste is a last resort after every directly useful skill.
        if score<=0 and 'haste' in effects and (needs or state.get('search_failed')):score=1
        # A rainbow party benefits more from unlock+five colours than unlock alone.
        if state.get('rainbow') and state.get('locked') and 'five_board' in effects and 'unlock' in effects:score+=30
        if score<=0:continue
        candidates.append((score,int(p.get('priority',0)),-int(p.get('cooldown') or 99),-slot,slot))
    if not candidates:return None,''
    slot=max(candidates)[-1]
    why=[SKILL_EFFECT_LABELS[e] for e in needs if e in profiles[slot].get('effects',[])]
    return slot,' · '.join(why) or '판 교체'


ENEMY_VOID_SHIELD_PNG = 'iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAIAAAD9b0jDAAAGJklEQVR4nH1VbW8bxxGe2dt7P96RFGXLkiwpVhAkrm0gLtrf0p+cwv3Qxk5lK5VFmbVJkRTJe9+7vZ3ph5OFFA06Hw4Y4PbZmWeeeRYv//KdLSUiGGJgRgRAsSDnFxP/3Qw27GgQDL8TCCCBHlP9jcledJuAOwsAAAlYSsuSlgAAJgIBiq0dyQsTf6Boxa4B/D1AAAAG0CDW6IIVW0DPqBxzI5mZQSIiCgQGBDBg7di56MJ/UHzLdsf92f8XCuUCRGFJC4VNMCQlAKQxZFlCIALAmuwLCv9Kw5SlZmRmAEC8L7ZP73tHfEg1YIb2B5EYhJegAzCSCExHSlhfIPyldS+Nt0HpBuHA972vIb8GIhIREXVdp7VumkYppZSq6npTw6U2iul7KCQA14bvSP6svXl0BOHeqe9HURSGoe/7g8EgCALP82zbtiyrL9AY0yNWVVUURVmWVVWVVaXLYlml0W4qBeKWncvO/6m0//TnH1/++DpOEgBo21Zr/QD3v1336EopIvI8j5mzLJvdTK9/Ijnr3AsYvKcoJ2JL2o5j2zYzl2W5Xq+JaG9vbzQaCSF6cvuvMaZpmvl8bowJwzAMQyGE4ziW7WzRle8pusTBnF0NNTHT1+FYluU4TpZl6/VaKRWGYRAE/X1VVdV1XRRFnue+7/dcCyEAoOs6xUL+DMkGXAMAiG3bNkpBkgBAFEX9iBaLxWq1Gg6Hk8kkiiJmXi6Xu92ubdu9vb3JZBLH8QNiVVVEJHfoGkAGRkStddu2zKy1zvO8rusoik5PT/M8n8/naZr2h5VScRyfnJxEUVTX9XK5HI/HjuM8gIoWBMM9Wb1QAEAIIYTQWu92O6WU4zjD4dCyrH56cRzHcQwAm82mLEsiQkRENMbUdU1E8rd6fgC1bdv3/bZtV6sVM7uuOxgMhBBN0wBAkiQAkKZpVVVJkkRR5DiOEKIHNcbIB8TfggKA67rj8TgMw9lsNp/Piejw8HB/f5+IPn36pJQaDAbPnj0Lw1BK2dPSK4yZ/6tSIjLG9AIsyzLPc0SM49i27d1ul6Zpmqa9pEaj0XA41FpvNhsp5Wg0klISkdaamSV+9QwhRNd1Xdf1wu66rm3btm0dx/F9Xwix3W7LskTEvb29MAwR8e7uTkoZRRERMTMRtW1LRNKHrgbJAP30lVIA0F8ehuHHjx+/fPmCiGdnZ2EYGmOMMZ7n7Xa7xWIhpTw+Pt7f37dt+16kffvfYXkDfgoOI5ZlmWVZ0zRSyv6PyWTiOE6e57PZrJctEc3ncyHEZDIZjUa2bdd1jYh1XW82m6IoLMuSLyGzAK4AdyiVUkVRVFUVRVHTND2D/fr36um6rl/HKIqSJOltpedntVqt12syxhJCPoccARRgA0HdUpplq9WqN4iqqtI03d/fn0wmvu/f3NzkeW5Z1tOnT+M4bppmNpu5rjuZTBDx9vZ2sVi4juMjS2I8whqRLYApuOl2++bNG9d1Dw4Ozs7Ottvtdru9vLx0XTdJkvF4zMxFUdzd3THzo0ePhsOh67qbzeb29rZM0wDMK04lMYdgjlFZsLMgvq7Fl8/ddDr1PO/g4KB3CsuysizTWgshmFkp5XneaDQaj8e+7+d5fnFxsVzMPZWfcfkcUgkMCByh+QHzFrA1cFmb6dVVGIZJkgRBEATBYDCYTqe9XQkhgiAYDodHR0ee5+V5vlgs3r19i9ndCRV/xN0Jl5IBiIGJAcUZl4DUIS2Wn68/hmEUfXN25vu+7/vn5+eDwSDPc2Y+PDyM41hKqbW+vr7+57t3TbZ7ZbavMPuWCwtYGiYLEQEJ2Ec4oppa9RZ4Pbv+W12T1scnJ3Ecu67bMwgAQRAgYlEUs9ns6vJD+Xn6h+7uBeQnULtABNg/1MAICIDAEZhT02hjv0vNdVG89/yqaQ6fPEmSpPfpfoPTNL1dLq+u/qX+PX2UL1+L3VNQA9D9csr7p73PEJgBQHxLhW0LYfk3H39d3q0fHzw5Pz9//Pix7/ta6/l8Pp1Ob6ZTXaQvKX0ts3NTCOYOQAAwsiRiEPeWInpgAAF0TKWPJIHmBX7+1C4WC9u2e9Ppuo7axmmrF5A9h/yIFFDvycCAzPwfHHYdVLCfj/wAAAAASUVORK5CYII='


@lru_cache(maxsize=1)
def _enemy_void_template():
    import base64,io,cv2
    return cv2.cvtColor(np.asarray(Image.open(io.BytesIO(base64.b64decode(ENEMY_VOID_SHIELD_PNG))).convert('RGB')),cv2.COLOR_RGB2GRAY)


def background_enemy_void(frame):
    """Match stacked chevrons below a red fist, excluding countdown digits."""
    import cv2
    frame=frame.convert('RGB').resize((1600,round(frame.height*1600/frame.width)),Image.Resampling.LANCZOS)
    template=_enemy_void_template();rgb=np.asarray(frame);h,w=template.shape
    for cx,cy in enemy_attack_anchors(frame):
        for offset in (38,76,114):
            x0=max(0,cx-22);y0=max(0,cy+offset-22)
            area=rgb[y0:cy+offset+23,x0:cx+23]
            if area.shape[0]<h or area.shape[1]<w:continue
            gray=cv2.cvtColor(area,cv2.COLOR_RGB2GRAY)
            _,score,_,where=cv2.minMaxLoc(cv2.matchTemplate(gray,template,cv2.TM_CCOEFF_NORMED))
            patch=area[where[1]:where[1]+h,where[0]:where[0]+w].astype('int16')
            # The actual symbol is neutral gray/white, not the coloured combo ring.
            core=patch[5:-3,5:-5]
            if score>=.92 and np.mean(core.max(axis=2)-core.min(axis=2)<40)>.95:return True
    return False


def background_status_text(tokens):
    # Never treat a percentage shield or an unlabelled number as a void shield.
    t=_compact_ocr_text(' '.join(x.text for x in tokens if x.score>=.85)).lower()
    return {
        'awoken_void':any(w in t for w in ('각성무효','覚醒無効')),
        'bound':any(w in t for w in ('바인드상태','바인딩상태','バインド状態')),
        'void_shield':any(w in t for w in ('데미지무효','대미지무효','ダメージ無効')),
        'attack_down':bool(re.search(r'(?:공격력|攻撃力).{0,12}(?:감소|저하|低下|減少)',t)),
        'recovery_down':bool(re.search(r'(?:회복력|回復力).{0,12}(?:감소|저하|低下|減少)',t)),
        'damage_absorb':any(w in t for w in ('데미지흡수','대미지흡수','ダメージ吸収')),
        'attribute_absorb':any(w in t for w in ('속성흡수','属性吸収'))}


def background_template_states(frame,settings):
    import base64,io,cv2
    states={};image=np.asarray(frame.convert('RGB'))
    for key,entry in settings.get('background_status_templates',{}).items():
        if key not in ('void_shield','attack_down','awoken_void','bound','recovery_down','damage_absorb','attribute_absorb'):continue
        try:
            raw=base64.b64decode(entry['png'],validate=True)
            if len(raw)>50000:continue
            template=np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'))
            h,w=template.shape[:2]
            if not 10<=h<=100 or not 10<=w<=100:continue
            area=image[120:570,:870] if entry.get('area')=='enemy' else image[825:940,:]
            # Colour matching prevents visually similar shield shapes changing category.
            score=cv2.matchTemplate(area,template,cv2.TM_SQDIFF_NORMED)
            value,_,where,_=cv2.minMaxLoc(score)
            patch=area[where[1]:where[1]+h,where[0]:where[0]+w]
            if value<.025 and np.abs(patch.astype('int16')-template.astype('int16')).mean()<18:states[key]=True
        except (ValueError,KeyError,OSError,cv2.error):continue
    return states


def background_target_key(ledger):
    """A statistics dropdown must not replace the last complete F7 snapshot."""
    return ledger.get('last_confirmed_farming_group') or ledger.get('active_farming_group')


def background_goal_complete(ledger,key,revision=None,material_ids=None):
    group=ledger.get('farming_groups',{}).get(key)
    if not group or not group.get('materials'):raise RuntimeError('마지막으로 확인한 재료 목록이 없습니다. F7 또는 재료확인을 먼저 사용하세요.')
    if revision is not None and group.get('revision')!=revision:raise RuntimeError('파밍 중 목표 재료가 바뀌었습니다.')
    rows=farming_group_rows(ledger,key)
    if material_ids is not None:
        chosen=set(material_ids)
        if not chosen or not chosen.issubset({row['id'] for row in rows}):raise RuntimeError('파밍할 재료를 다시 선택해 주세요.')
        rows=[row for row in rows if row['id'] in chosen]
    if any(not row.get('confirmed',True) for row in rows):raise RuntimeError('마지막 재료 목록의 이름/이미지 등록을 완료한 뒤 파밍을 시작해 주세요.')
    return all(row['deficit']==0 for row in rows)


def prepare_farming_selection(ledger,key,requirements,selected):
    updated=json.loads(json.dumps(ledger));group=updated.get('farming_groups',{}).get(key)
    if not group or background_target_key(updated)!=key:raise ValueError('돌파 대상이 바뀌었습니다. 창을 다시 열어주세요.')
    ids={row['id'] for row in group['materials']}
    if set(requirements)!=ids or not set(selected).issubset(ids):raise ValueError('재료 목록이 바뀌었습니다.')
    changed=False
    for row in group['materials']:
        value=str(requirements[row['id']]).strip()
        if not re.fullmatch(r'\d{1,6}',value):raise ValueError('필요 수량은 0~999999의 정수로 입력하세요.')
        number=int(value);changed=changed or row['required']!=number;row['required']=number
    chosen=[row['id'] for row in farming_group_rows(updated,key) if row['id'] in selected and row['deficit']>0]
    if not chosen:raise ValueError('부족한 재료를 하나 이상 선택해 주세요.')
    if changed:group['revision']=uuid.uuid4().hex
    return updated,chosen


def stage_list_entries(frame,tokens,database=None):
    import cv2
    entries=[];rgb=np.asarray(frame.convert('RGB'))
    for token in tokens:
        match=re.fullmatch(r'[★☆]?\s*(\d{1,2})\s*[-－–ー]\s*(\d{1,2})',token.text)
        if not match or token.score<.9 or not 300<token.cy<850:continue
        x0=max(0,int(token.cx-100));x1=min(frame.width,int(token.cx+100))
        y0=max(40,int(token.cy-260));y1=max(y0+1,int(token.cy-150))
        patch=rgb[y0:y1,x0:x1]
        mask=((patch[...,0]>205)&(patch[...,1]>125)&(patch[...,1]<225)&(patch[...,2]<100)).astype(np.uint8)
        count,labels,stats,centers=cv2.connectedComponentsWithStats(mask,8)
        medals=[]
        for index,(x,y,w,h,area) in enumerate(stats[1:],1):
            if 45<=w<=140 and 15<=h<=55 and 1.7<w/h<6 and area>350:
                cx,cy=centers[index];medals.append((abs(x0+cx-token.cx),x0+cx,y0+cy))
        registered=bool((database or {}).get('stages',{}).get('-'.join(match.groups()),{}).get('preview_verified'))
        if not medals and not registered:raise RuntimeError('스테이지 '+ '-'.join(match.groups())+'의 훈장 버튼을 확인하지 못했습니다.')
        medal=min(medals) if medals else None
        entries.append(dict(number='-'.join(match.groups()),token=token,medal=(medal[1],medal[2]) if medal else None))
    if len({entry['number'] for entry in entries})!=len(entries):raise RuntimeError('스테이지 번호가 중복 판독되었습니다.')
    return entries


def stage_has_shortage(database,number,deficits):
    row=database.get('stages',{}).get(number,{})
    return bool(row.get('preview_verified') and any(drop.get('id') is not None and deficits.get(str(drop['id']),0)>0 for drop in row.get('drops',[])))


def choose_farming_stage(database,deficits,available=None):
    allowed=set(available) if available is not None else None
    ranks=[]
    for number,row in database.get('stages',{}).items():
        if allowed is not None and number not in allowed:continue
        if not row.get('preview_verified'):continue
        ids={str(drop.get('id')) for drop in row.get('drops',[]) if drop.get('id') is not None}
        wanted={cid for cid,amount in deficits.items() if amount>0}
        overlap=ids&wanted
        if overlap:ranks.append((-len(overlap),int(row.get('battles') or 999),number))
    return min(ranks)[2] if ranks else None


def background_cached_party(frame,settings):
    saved=settings.get('background_party_profiles',[]);profiles=[]
    for slot in range(6):
        portrait=background_portrait(frame,slot,True)
        item=saved[slot] if slot<len(saved) and isinstance(saved[slot],dict) else {}
        old=np.asarray(item.get('portrait',[]),dtype=np.int16)
        same=bool(item.get('number')) and old.shape==portrait.shape and float(np.abs(old-portrait).mean())<=20
        profile=background_profile_merge(item,settings) if same else dict(slot=slot,name=f'파티원 {slot+1}',effects=[],auto_use=False)
        profile.update(slot=slot,portrait=portrait.tolist(),cached_party_match=same)
        if not settings.get('background_auto_skills',True):profile['effects']=[]
        profiles.append(profile)
    return profiles


ACTION_LOCAL=threading.local()


def assert_action_active(app):
    if getattr(app,'_all_actions_stopped',False) is True or (getattr(ACTION_LOCAL,'app',None) is app and getattr(ACTION_LOCAL,'epoch',0)!=getattr(app,'_stop_epoch',0)):
        raise RuntimeError('F10으로 작업을 중지했습니다.')


def resume_actions(app):
    app._all_actions_stopped=False
    if threading.current_thread() is threading.main_thread() and getattr(ACTION_LOCAL,'app',None) is app:
        ACTION_LOCAL.epoch=getattr(app,'_stop_epoch',0)


def action_request_current(app,epoch):
    return getattr(app,'_all_actions_stopped',False) is not True and epoch==getattr(app,'_stop_epoch',0)


def post_action_result(app,epoch,callback):
    app.root.after(0,lambda:callback() if action_request_current(app,epoch) else None)


class App:
    def __init__(self):
        self.data_paths = _user_data_paths()
        self.user_settings = _load_json_dict(self.data_paths["settings"])
        self._ocr_init_lock = threading.Lock()
        self._ocr_infer_lock = threading.Lock()
        self._crystal_db_lock = threading.RLock()
        self.reward_poll_busy = False
        self._reward_monitor_armed = threading.Event()
        self._reward_monitor_stop = threading.Event()
        self._reward_monitor_queue = __import__("queue").SimpleQueue()
        self._reward_monitor_worker_busy = threading.Event()
        self._auto_reward_enabled = False  # Totals change only on a Calculate button click.
        # v79: watch only for a bounded time after F6; never poll forever.
        self._reward_watch_deadline = 0.0
        self._reward_header_first_seen = 0.0
        self._reward_scan_attempts = 0
        # A confirmed target hit must get a fresh repeat-check window even
        # when previous OCR failures have exhausted the probe budget.
        self._reward_confirm_attempts = 0
        self._reward_probe_written = False
        self._reward_button_hidden_for_result = False
        self.reward_candidate_signature = None
        self.reward_candidate_count = 0
        self.reward_processed_this_visit = False
        self.reward_no_header_count = 0
        self.reward_last_scan_debug = "크리스탈 획득 화면 자동 감지를 기다리는 중입니다."
        self.board_history = []
        self._board_history_last_signature = None
        self.spikes: list[bool] = [False] * (ROWS * COLS)
        self.last_solved_spikes = None
        self.resource_crystal_id = self.user_settings.get("target_crystal_id")
        self.crystal_candidates = []
        ledger_initial = _load_json_dict(self.data_paths["ledger"])
        self.active_target = ledger_initial.get("active_target") if isinstance(ledger_initial.get("active_target"), dict) else None
        if self.active_target and self.active_target.get("id"):
            self.resource_crystal_id = str(self.active_target.get("id"))
        self.target_last_message = "목표 크리스탈 미지정 · 부족 화면의 빨간 필요 수량/아래 ×N 영역을 지정한 뒤 F7"
        self.last_result_summary = "결과: 아직 계산 전"
        self.last_solved_goals = None
        self.last_solved_avoid_skull = None
        self.last_solved_signature = None
        self.last_solved_elapsed = None
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1030x860")
        self.root.minsize(900, 680)
        self.root.configure(bg="#f3f5f7")
        # Keep the control panel out of the game's way. The route overlay is
        # topmost and click-through only while a route is actually displayed.
        saved_rect = self.user_settings.get("board_rect") if isinstance(self.user_settings, dict) else None
        try:
            self.board_rect: Optional[tuple[int, int, int, int]] = tuple(int(v) for v in saved_rect) if saved_rect and len(saved_rect) == 4 else None
        except (TypeError, ValueError):
            self.board_rect = None
        saved_resource_rect = self.user_settings.get("resource_rect") if isinstance(self.user_settings, dict) else None
        try:
            self.resource_rect: Optional[tuple[int, int, int, int]] = (
                tuple(int(v) for v in saved_resource_rect)
                if saved_resource_rect and len(saved_resource_rect) == 4 else None
            )
        except (TypeError, ValueError):
            self.resource_rect = None
        saved_card_title_rect = self.user_settings.get("card_title_rect") if isinstance(self.user_settings, dict) else None
        try:
            self.card_title_rect: Optional[tuple[int, int, int, int]] = (
                tuple(int(v) for v in saved_card_title_rect)
                if saved_card_title_rect and len(saved_card_title_rect) == 4 else None
            )
        except (TypeError, ValueError):
            self.card_title_rect = None
        self.card_name_detected = str((self.active_target or {}).get("card_name", "") or self.user_settings.get("last_card_name", "") or "")
        self.detecting_area = False
        self.detect_then_calculate = False
        self.board: Optional[list[str]] = None
        self.last_path: Optional[list[int]] = None
        self.last_hues = None
        self.busy = False
        self._attribute_status = ("5속성: 확인 대기", "#f8d67b")
        self._attribute_probe_queue = queue.SimpleQueue()
        self._attribute_probe_active = False
        self._attribute_probe_epoch = 0
        self._attribute_probe_candidate = None
        self._attribute_probe_repeats = 0
        self._attribute_probe_last_valid = 0.0
        self._attribute_probe_rect = None
        self._attribute_probe_next = 0.0
        self._attribute_probe_after = None
        self._attribute_probe_stopped = False
        self._live_board_ready = False
        self._live_board_seen_at = 0.0
        self._path_button_pointer_down = False
        self.overlay = Overlay(self)
        self._ocr_engine = None
        self.ocr_busy = False
        self.resource_selector_active = False
        self.last_ocr_debug = "OCR 실행 전입니다. 돌파 확인 화면에서 F7로 대상과 재료를 읽고, 재료를 클릭하면 상세 이름을 자동 등록합니다."
        self.resource_values = {
            "crystal_required": None, "crystal_current": None,
        }
        self.resource_last_read = None
        self._card_title_capture_session = None
        # Snapshot the currently selected farming target when title scanning starts.
        # This prevents an OCR result from being assigned to a different target later.
        self._card_title_target_id_snapshot = None
        self._card_title_candidate_ref = None
        self._active_target_candidate_ref = None
        # Keep exactly one OCR diagnostic window; repeated requests refresh it.
        self._ocr_debug_window = None
        self._ocr_debug_text_widget = None
        self._ocr_debug_preview_label = None
        self._ocr_debug_path_label = None
        self._ocr_debug_preview_photo = None
        self._ocr_debug_popup_scheduled = False
        self.last_card_title_crop_path = self.data_paths["root"] / "debug" / "last_card_title_crop.png"
        self.last_f7_capture_path = self.data_paths["root"] / "debug" / "last_f7_material.png"
        self._recent_material_scan_status = "대기"
        self.user_settings["farming_strip_enabled"] = False
        # Replace the old forced-pin default once; later manual pin choices remain valid.
        if not self.user_settings.get('crystal_background_mode_v1'):
            self.user_settings.update(crystal_background_mode_v1=True,crystal_panel_pin=False)
        self._build_ui()
        # Restore non-destructive preferences from the persistent settings file.
        try:
            saved_goal = int(self.user_settings.get("goal_index", 0))
            self.goal_var.set(saved_goal if saved_goal in {i for i, _, _ in ROUTE_GOAL_OPTIONS} else 0)
            self.toolbar_topmost_var.set(False)  # v75: a new session always starts with the strip OFF
            self.crystal_panel_pin_var.set(bool(self.user_settings.get("crystal_panel_pin", False)))
        except Exception:
            pass
        self._sync_attribute_indicator_visibility()
        self._f6_down = False
        self._f7_down = False
        self._f8_down = False
        self._f9_down = False
        self._f10_down = False
        self._v66_debug_init()
        self._playback_finish_queue = __import__("queue").SimpleQueue()
        self.root.after(100, self._poll_playback_finish_queue)
        self.play_cancel = threading.Event()
        self.playback_active = False
        self.drag_in_progress = False
        self.playback_after = None
        self.playback_countdown_left = 0
        self.playback_countdown_deadline = 0.0
        self.playback_path = None
        self.playback_signature = None
        self.set_status("F6=경로 계산 후 자동 실행 · Shift+F6=강제 재탐색 · F7=크리스탈 필요/보유 읽기 · F8=경로 표시 · F9=계산 버튼 표시/숨김 · F10=중지")
        self.overlay.update_hud(resources=self.format_resource_hud())
        # Start with the click-through full-screen route layer withdrawn.
        # It is raised only after a route has been calculated or shown with F8.
        self.overlay.hide()
        self.toolbar = FloatingPathExecuteButton(self)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.poll_global_hotkeys)
        self.root.after(250, self.toolbar.raise_to_top)
        self._route_button_foreign_ticks = 0
        self.root.after(400, self._v83_route_button_focus_tick)
        self._farming_init()
        self._route_debug_init()
        self._crystal_click_init()
        self._prefind_init()
        self._route_combo_fallback=None
        self._route_combo_offer=None
        self._material_button_init()
        self._background_init()
        self._attribute_probe_after = self.root.after(250, self._attribute_probe_tick)
        # Only a stable, verified result screen can update material inventory.
        self.load_board_history()

    def _route_button_game_in_foreground(self):
        """Check emulator foreground only; game options are still the emulator.

        Unlike board matching this check has no screenshots, so an in-game
        options/menu overlay cannot hide the button just by masking the board.
        """
        if sys.platform != 'win32':
            return True
        try:
            info = find_emulator_window_info()
            if not info:
                return False
            user32 = ctypes.windll.user32
            foreground = int(user32.GetForegroundWindow())
            game_hwnd = int(info['hwnd'])
            # Accept child game dialogs owned by the emulator host.
            root = int(user32.GetAncestor(foreground, 2) or foreground)
            if foreground == game_hwnd or root == game_hwnd:
                return True
            # Dragging/pressing our own floating button may activate its Tk hwnd;
            # avoid hiding it halfway through a click.
            toolbar = getattr(self, 'toolbar', None)
            if toolbar and toolbar.win.winfo_exists():
                own = int(toolbar.win.winfo_id())
                if foreground == own or root == int(user32.GetAncestor(own, 2) or own):
                    return True
            return False
        except Exception:
            return False

    def _v83_route_button_focus_tick(self):
        """Show the path button only for a confirmed board in foreground MuMu.

        F9's explicit hidden state is never changed. Crystal controls have an
        independent lifetime.
        """
        try:
            toolbar = getattr(self, 'toolbar', None)
            if toolbar is not None and toolbar.win.winfo_exists():
                if self._route_button_game_in_foreground() and self._path_button_may_show():
                    self._route_button_foreign_ticks = 0
                    if (not toolbar._user_hidden and not toolbar._temporarily_hidden
                            and toolbar.win.state() == 'withdrawn'):
                        toolbar.win.deiconify()
                        toolbar._position_path_button_beside_strip()
                        toolbar.win.attributes('-topmost', True)
                else:
                    self._route_button_foreign_ticks += 1
                    if self._route_button_foreign_ticks >= 2 and toolbar.win.state() != 'withdrawn':
                        toolbar.win.withdraw()
        except (tk.TclError, AttributeError):
            pass
        finally:
            try:
                if self.root.winfo_exists():
                    self.root.after(400, self._v83_route_button_focus_tick)
            except tk.TclError:
                pass

    def _calculate_from_main_window(self):
        self.trigger_route()

    def _remember_manual_error_board(self, image, board, spikes):
        """Keep only the displayed board's source pixels in RAM until explicitly saved."""
        self._manual_error_snapshot=dict(board=list(board),spikes=list(spikes),
            board_rect=list(self.board_rect),captured_local=time.strftime('%Y-%m-%d %H:%M:%S'),
            board_image=image.copy(),game_image=None,path=[])

    def save_manual_board_error(self):
        snapshot=getattr(self,'_manual_error_snapshot',None)
        if not snapshot or tuple(snapshot['board'])!=getattr(self,'_displayed_board_signature',None):
            self.set_status('현재 표시된 퍼즐판의 원본이 없습니다. 퍼즐판을 인식한 뒤 오류 저장을 눌러 주세요.')
            return
        if self.busy or self.playback_active:
            self.set_status('계산과 이동이 끝난 뒤 오류 저장을 눌러 주세요. 이동 전 원본은 보관 중입니다.')
            return
        directory=None
        try:
            import shutil
            root=self.data_paths['root']/'debug'/'manual_errors'
            name=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:10]
            directory=root/(name+'.pending')
            directory.mkdir(parents=True,exist_ok=False)
            snapshot['board_image'].save(directory/'board_original.png',format='PNG')
            if snapshot['game_image'] is not None:
                snapshot['game_image'].save(directory/'game_original.png',format='PNG')
            route_debug_board_picture(snapshot['board']).save(directory/'recognized_board.png')
            if snapshot['path']:
                route_debug_board_picture(snapshot['board'],snapshot['path']).save(directory/'path.png')
            report={k:v for k,v in snapshot.items() if k not in ('board_image','game_image')}
            report.update(version=APP_VERSION,user_reported_failure=True,
                saved_local=time.strftime('%Y-%m-%d %H:%M:%S'),
                game_original_available=snapshot['game_image'] is not None,
                source='cached pixels of displayed board; no screenshot taken on save')
            _atomic_json_write(directory/'report.json',report)
            destination=root/name
            directory.rename(destination)
            self.set_status(f'오류 원본 저장 완료: {destination}')
        except Exception as exc:
            if directory is not None and directory.exists():
                shutil.rmtree(directory,ignore_errors=True)
            self.set_status(f'오류 저장 실패: {exc} · 원본은 메모리에 유지되어 다시 저장할 수 있습니다.')

    def _route_debug_init(self):
        # Diagnostics stay in one RAM snapshot; create no archive or writer on startup.
        self._route_debug_probe_state=None
        self._route_debug_button_pending=False

    def _route_debug_update(self, values, images=None, ticket=None, texts=None):
        snapshot=getattr(self,'_manual_error_snapshot',None)
        if snapshot is not None:
            snapshot.update(values)

    def _route_debug_solution(self, board, path, elapsed, goals=None):
        snapshot=getattr(self,'_manual_error_snapshot',None)
        if not snapshot or snapshot['board']!=list(board): return
        try:
            final=board_after_drag(board,path)
            metrics=board_goal_metrics(final,App._combo_goals(self,board,goals if goals is not None else self.selected_goals()))
            snapshot.update(path=list(path),predicted_final_board=list(final),
                predicted_combos=metrics['combos'],predicted_final_combos=metrics['final_combos'],required_final_combos=metrics['required_total'],predicted_attributes=metrics['attr_count'],
                goals=list(goals if goals is not None else self.selected_goals()),solver_seconds=elapsed)
        except Exception as exc:
            self._v66_log(f'manual error snapshot metadata unavailable: {exc}')

    def _route_debug_before_drag(self):
        snapshot=getattr(self,'_manual_error_snapshot',None)
        if not snapshot or snapshot['board']!=list(self.board): return
        x,y,w,h=self.board_rect
        snapshot.update(execution_goals=list(self._route_playback_goals(self.board,self.playback_path)),
            input_points=[dict(cell=i,x=int(x+(i%COLS+.5)*w/COLS),
                               y=int(y+(i//COLS+.5)*h/ROWS)) for i in self.playback_path])
        try:
            # Transfer ownership of the fresh RGB image; no duplicate full-screen copy.
            snapshot['game_image']=self._capture_route_timer_image()
        except Exception as exc:
            snapshot['game_original_unavailable']=str(exc)

    def _route_debug_finish(self, error, cancelled):
        self._route_debug_update(dict(input_error=error,input_cancelled=bool(cancelled),
                                     input_completed=not error and not cancelled))

    def save_button_detection_debug(self):
        """One-shot diagnostic for the game in front, independent of button visibility."""
        if (self.busy or self.playback_active or self.ocr_busy or self.resource_selector_active
                or self._route_debug_button_pending):
            self.set_status('현재 작업이 끝난 뒤 버튼 미표시 진단을 저장해 주세요.')
            return
        rect,reason=self._attribute_probe_ready_rect()
        self._route_debug_button_context=dict(reason=reason,board_rect=list(rect) if rect else None,
            user_hidden=bool(self.toolbar._user_hidden),live_board_ready=bool(self._live_board_ready),
            state=self._route_debug_probe_state,
            probe_repeats=getattr(self,'_attribute_probe_repeats',0),
            probe_candidate=getattr(self,'_attribute_probe_candidate',None),
            probe_epoch=getattr(self,'_attribute_probe_epoch',0),
            probe_last_valid_age=(time.monotonic()-self._attribute_probe_last_valid
                if getattr(self,'_attribute_probe_last_valid',0) else None))
        self._route_debug_button_pending=True
        self.toolbar.hide_for_capture()
        self.root.iconify();self.root.update_idletasks()
        self.root.after(200,self._save_button_detection_debug_ready)

    def _save_button_detection_debug_ready(self):
        try:
            image=self._capture_reward_monitor_image()
            images={'game.png':image}
            context=dict(self._route_debug_button_context,version=APP_VERSION)
            rect=context['board_rect'] or self.board_rect
            if rect:
                gx,gy,gw,gh=self._last_reward_monitor_rect
                x,y,w,h=rect
                if gx<=x and gy<=y and x+w<=gx+gw and y+h<=gy+gh:
                    board_image=image.crop((x-gx,y-gy,x-gx+w,y-gy+h))
                    board,_=classify_board(board_image)
                    images['board_detection.png']=board_image
                    context.update(board=board,checks=live_board_grid_diagnostics(board_image,board))
            if not hasattr(self,'_route_debug_writer'):
                self._route_debug_writer=RouteDebugWriter(self.data_paths['root']/'debug'/'manual_errors')
            context['user_reported_failure']=True
            ticket=self._route_debug_writer.begin(context,images,kind='button_missing')
            self.set_status(f'버튼 미표시 진단을 저장했습니다: debug/manual_errors/{ticket}')
        except Exception as exc:
            self.set_status(f'버튼 미표시 진단 저장 보류: {exc} · 게임 창을 앞으로 둔 상태에서 다시 시도해 주세요.')
        finally:
            self._route_debug_button_pending=False
            self.toolbar.restore_after_capture()

    def _begin_route_ui_session(self):
        """Keep controls out of every capture and verification in one route run."""
        if getattr(self, '_route_ui_active', False):
            return False
        self._remember_route_timer_windows()
        self._prefind_cancel(keep_result=True)
        self._route_ui_active = True
        self._route_ui_waiting_capture = False
        self._route_ui_waiting_playback = False
        self.toolbar.hide_for_playback()
        self.root.iconify()
        self.root.update_idletasks()
        self.root.after(400, self._poll_route_ui_session)
        return True

    def _run_route_capture(self, use_current):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self._route_ui_waiting_capture = False
        try:
            self.calculate_clicked(use_current=use_current)
        except Exception as exc:
            self.busy = False
            self._route_execution_notice(f'자동 이동 시작 실패: {exc}')

    def _poll_route_ui_session(self):
        if not getattr(self, '_route_ui_active', False):
            return
        if (self.busy or self.playback_active or self.detecting_area
                or self._route_ui_waiting_capture or self._route_ui_waiting_playback):
            self.root.after(200, self._poll_route_ui_session)
            return
        self._route_ui_active = False
        self.toolbar.restore_after_playback()
        # Keep the main controls minimized; restoring them would cover the game.

    def trigger_route(self, force=False):
        """One entry point for F6; Shift+F6 forces a fresh search."""
        resume_actions(self)
        self._last_manual_route_request_at = time.monotonic()
        self._v66_log("F6 route triggered")
        self._force_next_search = bool(force) if not (self.busy or self.playback_active) else False
        self.calculate_clicked()

    def toggle_crystal_panel_pin(self):
        """Pin the management window independently of the farming HUD."""
        pinned = not bool(self.crystal_panel_pin_var.get())
        self.crystal_panel_pin_var.set(pinned)
        panel = getattr(self.toolbar, "crystal_window", None) if hasattr(self, "toolbar") else None
        if panel is not None and panel.winfo_exists():
            panel.attributes("-topmost", pinned)
            if pinned:
                panel.lift()
        if hasattr(self, "toolbar"):
            button = getattr(self.toolbar, "pin_button", None)
            if button is not None:
                button.configure(text=("📌 관리창 고정: 켬" if pinned else "📌 관리창 고정: 꺼짐"),
                                 bg=("#177c71" if pinned else "#50697c"))
        self.root.after_idle(self.save_user_settings)

    def open_data_folder(self):
        """Open persistent app data folder outside the replaceable ZIP directory."""
        try:
            self.data_paths["root"].mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(self.data_paths["root"]))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.data_paths["root"])])
            else:
                subprocess.Popen(["xdg-open", str(self.data_paths["root"])])
            self.set_status(f"사용자 데이터 폴더: {self.data_paths['root']}")
        except Exception as exc:
            self.set_status(f"데이터 폴더 열기 실패: {exc}")

    def open_crystal_icons_folder(self):
        """Open the icon image directory without altering the database."""
        try:
            folder = self.data_paths["icons_dir"]
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            self.set_status(f"크리스탈 이미지 폴더 열기 실패: {exc}")

    def show_crystal_window_from_db(self):
        if hasattr(self, "toolbar"):
            self.toolbar.show_crystal_window()

    def save_user_settings(self):
        """Persist UI preferences in LocalAppData, never alongside replaceable code."""
        try:
            settings = _load_json_dict(self.data_paths["settings"])
            # Carry forward persisted app state (notably the selected board area),
            # while preserving any keys written by a newer database updater.
            settings.update(self.user_settings)
            if hasattr(self, "goal_var"):
                settings["goal_index"] = int(self.goal_var.get())
            settings.pop("avoid_skull", None)
            for obsolete in ("auto_play", "auto_play_default_version", "auto_reward_collection", "auto_interval"):
                settings.pop(obsolete, None)
            if hasattr(self, "toolbar_topmost_var"):
                settings["farming_strip_enabled"] = False  # v75: never persist a live HUD between sessions
                settings["crystal_panel_pin"] = bool(self.crystal_panel_pin_var.get())
            if getattr(self, "resource_crystal_id", None):
                settings["target_crystal_id"] = str(self.resource_crystal_id)
            if hasattr(self, "toolbar") and self.toolbar.win.winfo_exists():
                settings["path_execute_button_xy"] = [int(self.toolbar.win.winfo_x()), int(self.toolbar.win.winfo_y())]
                panel = getattr(self.toolbar, "crystal_window", None)
                if panel is not None and panel.winfo_exists() and panel.state() != "withdrawn":
                    settings["crystal_window_xy"] = [int(panel.winfo_x()), int(panel.winfo_y())]
            _atomic_json_write(self.data_paths["settings"], settings)
            self._sync_crystal_monster_links()
        except Exception:
            pass

    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", font=("Segoe UI", 10), padding=7)
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=9)
        style.configure("TLabel", background="#f3f5f7", font=("Segoe UI", 10))
        style.configure("Header.TLabel", background="#f3f5f7", font=("Segoe UI", 12, "bold"))
        shell = ttk.Frame(self.root, padding=10)
        shell.pack(fill="both", expand=True)
        content = ttk.Frame(shell)
        content.pack(fill="both", expand=True)

        # Left controls are scrollable, so long help/options can never hide below the window.
        left_holder = ttk.Frame(content)
        left_holder.pack(side="left", fill="both", expand=True)
        left_canvas = tk.Canvas(left_holder, highlightthickness=0, width=600, bg="#f3f5f7")
        left_scroll = ttk.Scrollbar(left_holder, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)
        outer = ttk.Frame(left_canvas, padding=(2, 2, 10, 8))
        outer_window = left_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>", lambda e: left_canvas.itemconfigure(outer_window, width=max(480, e.width)))
        def _scroll_left_if_hovered(event):
            try:
                target = self.root.winfo_containing(event.x_root, event.y_root)
                if target is None or target.winfo_toplevel() is not self.root:
                    return None
                widget = target
                while widget is not None and widget is not self.root:
                    if widget is left_canvas or widget is outer:
                        delta = (-1 if event.delta > 0 else 1) * max(1, abs(int(event.delta / 120)))
                        left_canvas.yview_scroll(delta, "units")
                        return "break"
                    widget = getattr(widget, "master", None)
            except tk.TclError:
                pass
            return None
        # Bind only the application's Toplevel tag, NOT the global 'all' tag.
        self.root.bind("<MouseWheel>", _scroll_left_if_hovered, add="+")

        right = ttk.Frame(content, width=350, padding=(8, 2, 2, 2))
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        ttk.Label(outer, text=APP_TITLE, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(outer,
                  text="F6 또는 게임 위 [경로 계산] 버튼으로 계산하고, 선택 조건을 충족한 경로만 자동 실행합니다. 목표 미달이면 실행을 보류합니다.",
                  wraplength=555, justify="left").pack(anchor="w", pady=(4, 12))

        area_row = ttk.Frame(outer); area_row.pack(fill="x", pady=3)
        ttk.Button(area_row, text="1. 영역 자동 탐지", command=self.auto_detect_area, style="Accent.TButton").pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(area_row, text="직접 지정", command=self.select_area).pack(side="left", fill="x", expand=True, padx=(4, 0))
        action_row = ttk.Frame(outer); action_row.pack(fill="x", pady=3)
        ttk.Button(action_row, text="경로 계산 (F6 · 계산 후 자동 실행)", command=self._calculate_from_main_window, style="Accent.TButton").pack(side="left", fill="x", expand=True)
        ttk.Button(action_row, text="경로 오버레이 표시 / 숨김 (F8)", command=self.overlay.toggle).pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Button(outer, text="경로 계산 버튼 표시 / 숨김 (F9)", command=lambda: getattr(self, "toolbar", None) and self.toolbar.toggle_visibility()).pack(fill="x", pady=3)
        farm_row=ttk.Frame(outer);farm_row.pack(fill='x',pady=3)
        ttk.Button(farm_row,text='재료 파밍 시작 / 중지',command=self.start_background_farming).pack(side='left',fill='x',expand=True)
        ttk.Button(farm_row,text='파티 설정',command=self.show_party_settings).pack(side='left',padx=(6,0))
        ttk.Button(farm_row,text='파밍 연결 설정',command=self.background_settings).pack(side='left',padx=(6,0))
        attribute_host = ttk.Frame(outer)
        attribute_host.pack(fill="x")
        self.attribute_status_frame = ttk.Frame(attribute_host)
        self.attribute_status_var = tk.StringVar(value=self._attribute_status[0])
        self.attribute_status_label = ttk.Label(self.attribute_status_frame, textvariable=self.attribute_status_var,
                                               wraplength=535, justify="left")
        self.attribute_status_label.pack(anchor="w", fill="x", pady=(3, 4))
        ttk.Label(self.attribute_status_frame, text="5속성 모드에서는 누르기 전에 수량을 확인합니다. 수량 충족은 경로 발견을 뜻하지 않습니다.",
                  wraplength=535).pack(anchor="w", pady=(0, 4))

        # Crystal controls are grouped and intentionally kept compact. Reading still
        # happens with the global F7 hotkey; the main window only exposes the panel,
        # database/ledger manager and persistent data folder.
        crystal_box = ttk.LabelFrame(outer, text="크리스탈", padding=(8, 6))
        crystal_box.pack(fill="x", pady=(8, 4))
        ttk.Button(crystal_box, text="크리스탈 정보창 열기 / 숨김",
                   command=lambda: self.toolbar.toggle_crystal_window() if hasattr(self, "toolbar") else None).pack(fill="x", pady=2)
        crystal_row = ttk.Frame(crystal_box); crystal_row.pack(fill="x", pady=2)
        ttk.Button(crystal_row, text="획득 기록 / DB 관리", command=self.show_crystal_ledger).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(crystal_row, text="데이터 / DB 폴더 열기", command=self.open_data_folder).pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.toolbar_topmost_var = tk.BooleanVar(value=False)
        self.crystal_panel_pin_var = tk.BooleanVar(value=bool(self.user_settings.get("crystal_panel_pin", False)))

        play_row = ttk.Frame(outer); play_row.pack(fill="x", pady=3)
        ttk.Button(play_row, text="긴급 정지 (F10)", command=self.stop_playback).pack(side="left", fill="x", expand=True)

        goals_frame = ttk.LabelFrame(outer, text="경로 조건 (택 1)", padding=(10, 7))
        goals_frame.pack(fill="x", pady=(7, 2))
        self.goal_var = tk.IntVar(value=int(self.user_settings.get("goal_index", 0)))
        for column in range(4):goals_frame.columnconfigure(column,weight=1,uniform='route_goal')
        for position,(index,title,_) in enumerate(ROUTE_GOAL_OPTIONS):
            ttk.Radiobutton(goals_frame,text=title,variable=self.goal_var,value=index,
                            command=self._on_goal_mode_changed).grid(row=position//4,column=position%4,sticky='w',padx=(0,8),pady=5)
        debug_row=ttk.Frame(outer);debug_row.pack(fill='x',pady=3)
        ttk.Button(debug_row,text='버튼 미표시 진단 (F12)',command=self.save_button_detection_debug).pack(side='left',fill='x',expand=True)
        ttk.Separator(outer).pack(fill="x", pady=10)
        ttk.Label(outer, text="상태", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.status_var = tk.StringVar(value="대기 중")
        self.status_label = ttk.Label(outer, textvariable=self.status_var, wraplength=535, justify="left")
        self.status_label.pack(anchor="w", fill="x", pady=(4, 8))
        tips = ("• 첫 사용 때는 저장 좌표/뮤뮤 창을 먼저 확인하고, 실패하면 전체 화면에서 탐색합니다. 직접 지정한 영역은 뮤뮤 창 기준으로 저장합니다.\n"
                "• 게임 위 [경로 계산] 버튼 또는 F6은 같은 작업입니다. 선택 조건을 충족하면 자동 실행하고, 미달이면 실행을 보류합니다. Shift+F6은 강제 재탐색입니다.\n"
                "• F7은 필요/보유 수량 OCR 후 크리스탈 정보창 열기, F8은 경로 표시, F9은 게임 위 계산 버튼 표시/숨김, F10은 실행 중지입니다.\n"
                "• 돌파 확인 화면에서 F7을 누르면 대상 이름과 필요/보유 수량이 있는 재료 전체를 등록합니다.\n"
                "• 전투 결과창을 인식하면 등록 재료를 자동 집계하고 결과창 옆에 통계를 표시합니다.\n"
                "• 보상 목록을 스크롤해 아래쪽 수량까지 확인합니다. 카드 밑 숫자만 세며 상단 ×2 배율은 사용하지 않습니다. F10으로 인식을 중지할 수 있습니다.\n"
                "• 사용자 설정·아이콘 DB·획득 누계·최근 판독 기록은 프로그램 폴더와 분리된 LocalAppData에 보관합니다.")
        ttk.Label(outer, text=tips, wraplength=535, justify="left").pack(anchor="w", pady=(2, 4))

        # Right-side, fixed-height board preview and scrollable history panel.
        board_header=ttk.Frame(right)
        board_header.pack(fill='x',pady=(2,7))
        ttk.Label(board_header,text='현재 인식한 퍼즐판',style='Header.TLabel').pack(side='left')
        self.board_error_save_button=ttk.Button(board_header,text='오류 저장',command=self.save_manual_board_error)
        self.board_error_save_button.pack(side='right')
        self.board_view = ttk.Frame(right, padding=4)
        self.board_view.pack(fill="x", anchor="n")
        self.board_cells = []
        for r in range(ROWS):
            roww = ttk.Frame(self.board_view); roww.pack(anchor="center")
            for c in range(COLS):
                lab = tk.Label(roww, text="?", width=5, height=2, relief="ridge", bg="#dddddd",
                               fg="#222222", font=("Segoe UI", 9, "bold"))
                lab.pack(side="left", padx=1, pady=1)
                self.board_cells.append(lab)
        ttk.Separator(right).pack(fill="x", pady=10)
        ttk.Label(right, text="최근 인식 기록", style="Header.TLabel").pack(anchor="w")
        ttk.Label(right, text="최대 30개 · 클릭하면 해당 판의 간단한 정보가 표시됩니다.", wraplength=320).pack(anchor="w", pady=(2, 5))
        history_frame = ttk.Frame(right); history_frame.pack(fill="both", expand=True)
        history_scroll = ttk.Scrollbar(history_frame, orient="vertical")
        self.history_listbox = tk.Listbox(history_frame, height=18, exportselection=False,
                                          yscrollcommand=history_scroll.set, font=("Segoe UI", 9))
        history_scroll.configure(command=self.history_listbox.yview)
        self.history_listbox.pack(side="left", fill="both", expand=True)
        history_scroll.pack(side="right", fill="y")
        self.history_info_var = tk.StringVar(value="아직 판독 기록이 없습니다.")
        ttk.Label(right, textvariable=self.history_info_var, wraplength=320, justify="left").pack(anchor="w", pady=(6, 0))
        self.history_listbox.bind("<<ListboxSelect>>", self.on_history_selected)

    def set_status(self, text: str):
        self.status_var.set(text)
        try:
            if hasattr(self, "overlay"):
                self.overlay.update_hud(status=text)
            if hasattr(self, "toolbar"):
                self.toolbar.refresh()
        except Exception:
            pass
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

    @staticmethod
    def _format_resource_value(value):
        return "?" if value is None else f"{int(value):,}"

    def all_crystal_requirements_met(self):
        """Use all registered materials and their canonical inventory."""
        ledger = self._load_crystal_ledger()
        key = ledger.get('active_farming_group')
        if key in ledger.get('farming_groups', {}):
            rows = farming_group_rows(ledger, key)
            return bool(rows) and all(row['deficit'] == 0 for row in rows)
        candidates = getattr(self, 'crystal_candidates', None) or []
        if not candidates or getattr(self, '_recent_material_scan_status', '') != '성공':
            return False
        active = self.active_target or {}
        ledger = self._load_crystal_ledger()
        counts = ledger.get('counts', {})
        for candidate in candidates:
            cid = str(candidate.get('id') or '')
            if not cid:
                return False
            required = int(candidate.get('required', 0))
            original = int(candidate.get('current', 0))
            # Candidate's original stock plus verified rewards since F7; the ledger
            # is a lifetime tally, so do not add it as if it were a delta.
            credited = int(active.get('collected', 0)) if cid == str(active.get('id')) else 0
            if original + credited < required:
                return False
        return True

    def format_resource_hud(self):
        if self._load_crystal_ledger().get('farming_groups'):
            return ''  # Material statistics belong to the result screen.
        v = self.resource_values
        read_stamp = f" · OCR {self.resource_last_read}" if self.resource_last_read else " · OCR 대기"
        target = self.active_target or {}
        current = v["crystal_current"]
        if target.get("id"):
            goal = int(target.get("goal_amount", 0) or 0)
            collected = int(target.get("collected", 0) or 0)
            state = "달성" if target.get("complete") else f"진행 {collected}/{goal}"
            # Display the known original stock plus this target's confirmed rewards.
            # Other crystal types are intentionally not summed into this target.
            base_owned = target.get("owned_before", current)
            if base_owned is not None:
                current = int(base_owned) + collected
            target_text = f" · 목표 {target.get('label', target.get('id'))}: {state}"
        else:
            target_text = " · 목표 크리스탈 미지정"
        return (f"크리스탈 필요 {self._format_resource_value(v['crystal_required'])} / 보유 {self._format_resource_value(current)}{target_text}{read_stamp}")

    def _material_button_init(self):
        win=tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes('-topmost',True)
        self._material_button_win=win
        self._material_confirm_button=tk.Button(win,text='재료확인',command=self._material_button_clicked,
            bg='#174c69',fg='white',activebackground='#216784',activeforeground='white',
            font=('맑은 고딕',10,'bold'),relief='solid',bd=1,padx=12,pady=4,takefocus=False)
        self._material_confirm_button.pack(fill='both',expand=True)
        self._material_button_pressed=False
        self._material_confirm_button.bind('<ButtonPress-1>',lambda _e:setattr(self,'_material_button_pressed',True),add='+')
        self._material_confirm_button.bind('<ButtonRelease-1>',lambda _e:setattr(self,'_material_button_pressed',False),add='+')
        self._material_button_queue=queue.SimpleQueue()
        self._material_button_probe_busy=False
        self._material_button_probe_next=0.0
        self._material_button_context=None
        self._material_button_valid_at=0.0
        self._material_button_header_cache=None
        self._material_button_ocr_retry_at=0.0
        self._material_button_ocr_gate=threading.Lock()
        self._material_button_stopped=False
        self._material_button_after=self.root.after(100,self._material_button_tick)

    def _material_button_game_visible(self, info):
        if not info or sys.platform!='win32': return False
        from ctypes import wintypes
        api=ctypes.windll.user32
        api.GetForegroundWindow.restype=wintypes.HWND
        api.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT)
        api.GetAncestor.restype=wintypes.HWND
        api.IsIconic.argtypes=(wintypes.HWND,);api.IsIconic.restype=wintypes.BOOL
        api.IsWindowVisible.argtypes=(wintypes.HWND,);api.IsWindowVisible.restype=wintypes.BOOL
        api.WindowFromPoint.argtypes=(wintypes.POINT,);api.WindowFromPoint.restype=wintypes.HWND
        game=int(info['hwnd'])
        if api.IsIconic(game) or not api.IsWindowVisible(game): return False
        foreground=int(api.GetForegroundWindow() or 0)
        foreground=int(api.GetAncestor(foreground,2) or foreground)
        own=int(api.GetAncestor(self._material_button_win.winfo_id(),2) or self._material_button_win.winfo_id())
        allowed={game,own}
        for field in ('_background_button_win','_awakening_register_win','_team_party_win'):
            background=getattr(self,field,None)
            if background is not None and background.winfo_exists():
                handle=background.winfo_id();allowed.add(int(api.GetAncestor(handle,2) or handle))
        if foreground not in allowed: return False
        x,y,w,h=info['rect']
        for fx,fy in ((.20,.08),(.35,.20),(.65,.20),(.25,.28),(.50,.50),(.80,.48)):
            owner=api.WindowFromPoint(wintypes.POINT(round(x+w*fx),round(y+h*fy)))
            if not owner or int(api.GetAncestor(owner,2) or owner)!=game: return False
        return True

    def _material_button_probe_worker(self, context):
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=getattr(self,'_stop_epoch',0);ACTION_LOCAL.passive_display=True
        sampled=time.monotonic()
        valid=False
        try:
            _,(x,y,w,h)=context
            with mss.mss() as capture:
                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                image=Image.frombytes('RGB',shot.size,shot.rgb)
            header=breakthrough_button_candidate(image)
            if header is not None:
                fingerprint=material_title_fingerprint(header)
                cache=self._material_button_header_cache
                if fingerprint and cache and fingerprint==cache[0]:
                    valid=cache[1]
                elif (fingerprint and time.monotonic()>=getattr(self,'_material_button_ocr_retry_at',0)
                      and not (self.ocr_busy or self.busy or self.playback_active)):
                    with self._material_button_ocr_gate:
                        if not self.ocr_busy:
                            self._material_button_ocr_retry_at=time.monotonic()+2.0
                            _,views=material_title_line_views(header)
                            readings=[self._recognize_title_line(view) for view in views]
                            result=confirmed_breakthrough_heading(readings)
                            if result is not None:
                                self._material_button_header_cache=(fingerprint,result)
                                valid=result
        except Exception:
            pass
        self._material_button_queue.put((context,valid,sampled))

    def _material_button_hide(self):
        self._material_button_valid_at=0.0
        self._material_button_win.withdraw()

    def _material_button_tick(self):
        self._material_button_after=None
        if self._material_button_stopped: return
        try:
            if getattr(self,'_background_running',False):
                self._material_button_win.withdraw()
                return
            # Keep the Tk press/release/command sequence intact.
            if self._material_button_pressed: return
            info=find_emulator_window_info()
            visible=self._material_button_game_visible(info)
            context=(int(info['hwnd']),tuple(info['rect'])) if visible else None
            if context!=self._material_button_context:
                self._material_button_context=context
                self._material_button_hide()
            now=time.monotonic()
            while True:
                try: sampled,valid,at=self._material_button_queue.get_nowait()
                except queue.Empty: break
                self._material_button_probe_busy=False
                if context is None or sampled!=context or now-at>1.2: continue
                if valid:
                    self._material_button_valid_at=at
                else:
                    self._material_button_hide()
            blocked=(self.busy or self.playback_active or self.resource_selector_active
                     or getattr(self,'_manual_reward_busy',False) or getattr(self,'_route_ui_active',False))
            if (context is None or blocked or not self._material_button_valid_at
                    or now-self._material_button_valid_at>1.2):
                self._material_button_hide()
            else:
                x,y,w,h=context[1]
                self._material_button_win.geometry(f'+{round(x+w*.59)}+{round(y+h*.055)}')
                self._material_confirm_button.configure(state='disabled' if self.ocr_busy else 'normal',
                    text='확인 중…' if self.ocr_busy else '재료확인')
                if self._material_button_win.state()=='withdrawn':
                    self._material_button_win.deiconify()
                    self._material_button_win.attributes('-topmost',True)
            if context is not None and not blocked and not self._material_button_probe_busy and now>=self._material_button_probe_next:
                self._material_button_probe_busy=True
                self._material_button_probe_next=now+.30
                try:
                    threading.Thread(target=self._material_button_probe_worker,args=(context,),daemon=True).start()
                except RuntimeError:
                    self._material_button_probe_busy=False
        except (tk.TclError,OSError,ValueError,AttributeError):
            self._material_button_hide()
        finally:
            if not self._material_button_stopped and self.root.winfo_exists():
                self._material_button_after=self.root.after(100,self._material_button_tick)

    def _material_button_clicked(self):
        self._material_button_pressed=False
        info=find_emulator_window_info()
        context=(int(info['hwnd']),tuple(info['rect'])) if info else None
        if (context!=self._material_button_context or not self._material_button_game_visible(info)
                or time.monotonic()-self._material_button_valid_at>1.2):
            self._material_button_hide()
            return
        if self.ocr_busy: return
        # Paint acknowledgement before the common capture/OCR path starts.
        self._material_confirm_button.configure(text='확인 중…',state='disabled')
        self.set_status('재료확인을 시작합니다…')
        self.root.update_idletasks()
        resume_actions(self)
        epoch=getattr(self,'_stop_epoch',0)
        self.root.after(1,lambda:action_request_current(self,epoch) and self._material_button_run_scan())

    def _material_button_run_scan(self):
        # Fresh visual validation also rejects a page changed after mouse-down.
        info=find_emulator_window_info()
        if not info or not self._material_button_game_visible(info):
            self._material_button_hide();return
        context=(int(info['hwnd']),tuple(info['rect']))
        if context!=self._material_button_context:
            self._material_button_hide();return
        try:
            x,y,w,h=info['rect']
            with mss.mss() as capture:
                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                image=Image.frombytes('RGB',shot.size,shot.rgb)
            header=breakthrough_button_candidate(image)
            cache=self._material_button_header_cache
            fingerprint=material_title_fingerprint(header) if header is not None else None
            if not fingerprint or not cache or cache!=(fingerprint,True):
                self._material_button_hide();return
            self.request_resource_ocr(manual=False,fallback_on_failure=True)
        except Exception as exc:
            self._material_button_hide()
            self.set_status(f'재료확인 캡처 보류: {exc}')

    def _auto_material_rect(self):
        """MuMu-relative F7 capture; never reuse a stale manual desktop rectangle.

        Based on the user's fixed 1602x941 MuMu window. A screenshot is only
        captured when F7 is pressed; no continuous screen polling is involved.
        """
        info = find_emulator_window_info()
        if not info:
            raise RuntimeError("게임 창(MuMu Android Device)을 찾지 못했습니다. MuMuPlayer 관리창이 아닌 게임 창을 열어 주세요.")
        if not ("android device" in info["title"].lower() or "puzzle dragons 0" in info["title"].lower()):
            raise RuntimeError(f"게임 창을 찾지 못했습니다 (발견한 창: {info['title']}). MuMu Android Device가 실행 중인지 확인하세요.")
        wx, wy, ww, wh = info["rect"]
        self._last_f7_window_info = f"창: {info['title']} / HWND {info['hwnd']} / 위치 ({wx},{wy}) / 크기 {ww}x{wh}"
        if ww < 1000 or wh < 650:
            raise RuntimeError(f"게임 창 크기를 확인해 주세요: {self._last_f7_window_info}")
        # Full card badges and icons in BOTH 1-card and 3-card material dialogs.
        # Coordinates are scaled relative to the emulator outer frame.
        return (wx + round(145 * ww / 1602), wy + round(240 * wh / 941),
                round(735 * ww / 1602), round(290 * wh / 941))

    def _auto_material_screen_valid(self, image):
        """Reject battle/board captures rather than adding false crystal IDs."""
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        h, w = arr.shape[:2]
        # Both creation dialogs have a large pale-gray material panel background.
        # Battle backgrounds are predominantly colorful/dark in this location.
        patch = arr[int(h*.08):int(h*.18), int(w*.04):int(w*.95)].astype(np.int16)
        neutral = (patch.max(axis=2) - patch.min(axis=2) < 23)
        bright = (patch.mean(axis=2) > 170)
        return float(np.mean(neutral & bright)) > .36

    def _capture_screen_for_ocr(self, rect_override=None) -> tuple[Image.Image, Optional[Image.Image]]:
        """Capture material region without altering the user's saved manual ROI."""
        if rect_override is None:
            if not self.resource_rect:
                raise RuntimeError("크리스탈 정보 영역을 먼저 지정하세요.")
            self._refresh_resource_rect_from_emulator()
            if not self.resource_rect:
                raise RuntimeError("저장된 크리스탈 정보 영역이 없습니다. 다시 지정하세요.")
        left, top, width, height = [int(v) for v in (rect_override or self.resource_rect)]
        if width < 20 or height < 25:
            raise RuntimeError("저장된 크리스탈 정보 영역이 너무 작습니다. 빨간 필요 수량·아이콘·아래 ×N이 모두 들어가도록 다시 지정하세요.")

        point = (left + width // 2, top + height // 2)
        window_info = find_emulator_window_info(point=point)
        # Read the owning monster only from the current breakthrough modal header.
        route_was_visible = bool(self.overlay.visible)
        root_was_visible = self.root.state() not in ("iconic", "withdrawn") and bool(self.root.winfo_viewable())
        toolbar_was_visible = self.toolbar.is_visible() if hasattr(self, "toolbar") else False
        try:
            if toolbar_was_visible:
                self.toolbar.hide_for_capture()
            self.overlay.hide()
            if root_was_visible:
                self.root.iconify()
            time.sleep(0.18)
            with mss.mss() as sct:
                shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                title_image = None
                if window_info and rect_override is not None:
                    wx, wy, ww, wh = window_info['rect']
                    title_shot = sct.grab({'left':wx+round(ww*.25), 'top':wy+round(wh*.163),
                                           'width':round(ww*.50), 'height':round(wh*.060)})
                    title_image = Image.frombytes('RGB', title_shot.size, title_shot.rgb)
            if window_info and rect_override is not None:
                self._material_capture_context=dict(roi=(left,top,width,height),image=image.copy(),
                    title_image=title_image.copy() if title_image else None,
                    title_roi=(wx+round(ww*.25),wy+round(wh*.163),round(ww*.50),round(wh*.060)),
                    game_rect=tuple(window_info['rect']),hwnd=int(window_info['hwnd']))
            return image, title_image
        finally:
            # Leave the main window minimized (taskbar-accessible), never withdrawn.
            # Only the crystal panel is raised when OCR completes.
            if toolbar_was_visible:
                try:
                    self.toolbar._temporarily_hidden = False
                except Exception:
                    pass

    def request_resource_ocr(self, manual=False, fallback_on_failure=True):
        resume_actions(self)
        if getattr(self,'_background_running',False):
            self.set_status('파밍 중 목표 재료를 바꿀 수 없습니다. F10으로 먼저 중지해 주세요.');return
        if hasattr(self, '_crystal_click_queue'):
            self._drain_crystal_clicks()
            pending=self._crystal_pending_click
            self._crystal_detail_binding_snapshot=pending[0] if pending and time.monotonic()-pending[1]<300 else None
        stats = getattr(self, '_farming_stats', None)
        if stats is not None:
            stats.withdraw()
            self._farming_result_visible = False
            self._farming_positive = 0
        if self.resource_selector_active:
            self.set_status("영역 지정 중입니다. 선택을 완료하거나 Esc로 취소해 주세요.")
            return
        if getattr(self, '_manual_reward_busy', False):
            self.set_status('결과창 집계 중입니다. F10으로 중지한 뒤 재료를 다시 등록할 수 있습니다.')
            return
        if self.ocr_busy:
            if manual:
                self.set_status("크리스탈 OCR이 이미 진행 중이에요. 잠시 기다려 주세요.")
            return
        self._auto_title_serial=getattr(self,'_auto_title_serial',0)+1
        self._auto_title_hidden=False
        self._material_detail_context=None
        # F7 always uses MuMu-relative coordinates. The area selector remains a
        # manual fallback and is intentionally independent of the F7 rectangle.
        self._last_diagnostic_source = "f7"
        self._last_f7_window_info = "수동 재료 영역" if manual else "게임 창 탐색 전"
        self.last_f7_capture_path = None
        try:
            if not manual:
                auto_rect = self._auto_material_rect()
                self._last_f7_window_info += f" / 캡처 {auto_rect}"
                image, title_image = self._capture_screen_for_ocr(rect_override=auto_rect)
                # Save one screenshot per F7 attempt, including failed recognitions.
                try:
                    debug_path = self.data_paths["root"] / "debug" / "last_f7_material.png"
                    debug_path.parent.mkdir(parents=True, exist_ok=True)
                    image.save(debug_path)
                except (OSError, ValueError):
                    pass
                self.last_f7_capture_path = debug_path
            else:
                if not self.resource_rect:
                    self.select_resource_area()
                    return
                image, title_image = self._capture_screen_for_ocr()
        except Exception as exc:
            self._recent_material_scan_status = "실패"
            self.crystal_candidates = []
            self.resource_values["crystal_required"] = None
            self.resource_values["crystal_current"] = None
            self.last_f7_capture_path = None
            self.last_ocr_debug = f"[F7 캡처 진단]\n{self._last_f7_window_info}\n화면 캡처 실패: {exc}"
            if manual:
                self.set_status(self.last_ocr_debug)
            if hasattr(self, "toolbar"):
                self.toolbar.show_crystal_window()
                self.toolbar.refresh()
            self.set_status("F7 자동 스캔 실패. 영역 변경은 크리스탈 창의 영역 지정 버튼을 사용하세요.")
            return
        # Keep the last registered materials until this capture is classified.
        # The new screen may be a name-only detail dialog, not another material scan.
        self._recent_material_scan_status = "스캔 중"
        if hasattr(self, "toolbar"):
            self.toolbar.refresh()
        self.ocr_busy = True
        self._resource_scan_generation = getattr(self, "_resource_scan_generation", 0) + 1
        generation = self._resource_scan_generation
        self.root.after(20000, lambda g=generation: self._resource_scan_watchdog(g))
        self.set_status("크리스탈 스캔 중… 결과를 기다려 주세요. F7 중복 입력은 무시됩니다.")
        self.overlay.update_hud(resources="크리스탈 재료 인식 중…")
        if manual:
            self.set_status("지정한 재료 영역과 별도 지정한 카드 제목 영역을 읽는 중…")
        threading.Thread(target=self._resource_ocr_worker,
                         args=(image, title_image, manual, fallback_on_failure, generation), daemon=True).start()

    def _resource_scan_watchdog(self, generation):
        if generation != getattr(self, "_resource_scan_generation", 0) or not self.ocr_busy:
            return
        # Do not release the scan lock while the worker still owns OCR; doing
        # so caused overlapping scans and blank diagnostic windows.
        self.set_status("OCR이 예상보다 오래 걸리고 있습니다. 현재 작업이 끝날 때까지 F7 중복 실행을 차단합니다.")
        if hasattr(self, "toolbar"):
            self.toolbar.refresh()

    def _get_ocr_engine(self):
        """Create the OCR engine only once; shared by balance OCR and reward monitoring."""
        with self._ocr_init_lock:
            if self._ocr_engine is None:
                from rapidocr import EngineType, LangDet, ModelType, OCRVersion, RapidOCR
                self._ocr_engine = RapidOCR(params={
                    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    "Det.engine_type": EngineType.ONNXRUNTIME,
                    "Det.lang_type": LangDet.CH,
                    "Det.model_type": ModelType.MOBILE,
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.lang_type": "korean",
                    "Rec.model_type": ModelType.MOBILE,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                })
            return self._ocr_engine

    def _acquire_ocr_inference(self):
        """Explicit awakening registration reserves the next inference slot."""
        if getattr(ACTION_LOCAL,'passive_display',False) and getattr(ACTION_LOCAL,'app',None) is self:
            if ACTION_LOCAL.epoch!=getattr(self,'_stop_epoch',0):raise RuntimeError('이전 화면 확인을 취소했습니다.')
        else:assert_action_active(self)
        request=getattr(self,'_awakening_register_request',None)
        owner=getattr(self,'_ocr_priority_owner',None)
        if isinstance(owner,tuple) and owner[0]==threading.get_ident() and owner[1] is not None and owner[1]!=request:
            raise RuntimeError('각성 등록이 취소되었습니다.')
        if isinstance(request,str) and request:
            if owner!=(threading.get_ident(),request):return False
            deadline=getattr(self,'_awakening_register_deadline',time.monotonic()+6.0)
            announced=False
            while time.monotonic()<deadline:
                if getattr(self,'_awakening_register_request',None)!=request:
                    raise RuntimeError('각성 등록이 취소되었습니다.')
                if self._ocr_infer_lock.acquire(timeout=min(.1,max(0.0,deadline-time.monotonic()))):
                    if getattr(self,'_awakening_register_request',None)==request:return True
                    self._ocr_infer_lock.release();raise RuntimeError('각성 등록이 취소되었습니다.')
                if not announced:
                    self._background_queue.put(('awakening_register_progress',request,'이전 OCR 완료 대기 중… 곧 각성 등록을 시작합니다.'))
                    announced=True
            raise RuntimeError('각성 등록 대기시간 초과 · 이전 OCR 작업이 끝나지 않았습니다. 잠시 후 다시 등록해 주세요.')
        return self._ocr_infer_lock.acquire(blocking=False)

    def _run_ocr_tokens(self, image: Image.Image):
        engine = self._get_ocr_engine()
        rgb = np.asarray(image.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        if not self._acquire_ocr_inference():
            raise RuntimeError("OCR 엔진이 이전 작업을 처리 중입니다. 잠시 후 다시 시도하세요.")
        try:
            raw_result = engine(bgr, use_det=True, use_cls=True, use_rec=True)
        finally:
            self._ocr_infer_lock.release()
        return rapidocr_result_to_tokens(raw_result)

    def _recognize_title_line(self, image):
        engine=self._get_ocr_engine()
        pixels=np.asarray(image.convert('RGB'))[:,:,::-1].copy()
        if not self._acquire_ocr_inference():
            raise RuntimeError('OCR 엔진이 이전 작업을 처리 중입니다.')
        try:
            result=engine(pixels,use_det=False,use_cls=False,use_rec=True)
        finally:
            self._ocr_infer_lock.release()
        texts=getattr(result,'txts',None);scores=getattr(result,'scores',None)
        if texts is None or scores is None or len(texts)!=1 or len(scores)!=1: return '',0.0
        return str(texts[0]).strip(),float(scores[0])

    def _read_material_title_tokens(self, image):
        """Recover small gray modal titles without guessing from a previously selected owner."""
        enlarged=image.resize((image.width*3,image.height*3),Image.Resampling.LANCZOS)
        raw=self._run_ocr_tokens(enlarged)
        tokens=[t for t in raw if t.score>=.60]
        self._material_title_notes='제목 영역 OCR: '+repr([(t.text,round(t.score,3)) for t in raw])
        primary=crystal_detail_name(tokens) or extract_card_name_from_title(tokens,enlarged.size)
        if primary and tokens and min(t.score for t in tokens)>=.85: return tokens
        box,views=material_title_line_views(image)
        readings=[]
        for view in views:
            text,score=self._recognize_title_line(view)
            readings.append((text,score))
        self._material_title_notes+='\n제목 글자 직접 인식: '+repr(readings)
        if (len(readings)==2 and min(score for _,score in readings)>=.85
                and _compact_ocr_text(readings[0][0])==_compact_ocr_text(readings[1][0])):
            x0,y0,x1,y1=box
            recovered=[OCRToken(readings[0][0],x0*3,y0*3,x1*3,y1*3,min(score for _,score in readings))]
            if crystal_detail_name(recovered) or extract_card_name_from_title(recovered,enlarged.size):
                return recovered
        return []

    def _read_material_detail_tokens(self,image):
        # Known single-line detail header: try recognition directly before the
        # much larger full detector pass. Require agreement from two views.
        box,views=material_title_line_views(image)
        readings=[self._recognize_title_line(view) for view in views]
        self._material_title_notes='상세 제목 직접 인식: '+repr(readings)
        confirmed=confirmed_detail_readings(readings,getattr(self,'_material_title_known_names',()))
        if confirmed:
            x0,y0,x1,y1=box
            self._material_title_notes+='\n검증된 상세 이름: '+confirmed
            return [OCRToken(confirmed,x0*3,y0*3,x1*3,y1*3,min(score for _,score in readings))]
        return self._read_material_title_tokens(image)

    def _recognize_number_crop(self, image, allow_multiplier=False):
        """Read an already isolated number without the text detector or rotation model."""
        engine = self._get_ocr_engine()
        bgr = np.asarray(image.convert('RGB'))[:,:,::-1].copy()
        if not self._acquire_ocr_inference():
            raise RuntimeError('OCR 엔진이 이전 작업을 처리 중입니다.')
        try:
            result = engine(bgr, use_det=False, use_cls=False, use_rec=True)
        finally:
            self._ocr_infer_lock.release()
        texts = getattr(result,'txts',None)
        scores = getattr(result,'scores',None)
        if texts is None or len(texts) != 1 or scores is None or len(scores) != 1:
            return None,0.0,''
        text = str(texts[0]).strip()
        match = re.fullmatch(r'(?:[×xXхХ✕✖╳*]\s*)?(\d{1,5})' if allow_multiplier else r'(\d{1,4})',text)
        number = int(match.group(1)) if match else None
        return number,float(scores[0]),text

    def _auto_material_targets(self, image, original_tokens):
        """Read every requirement pill, including white counts already satisfied."""
        image = image.resize((735,290), Image.Resampling.LANCZOS)
        badges = locate_material_badges(image)
        found = []
        for x0,y0,x1,y1 in badges:
            cx, cy = round((x0+x1)/2), round((y0+y1)/2)
            badge = image.crop((x0,y0,x1,y1))
            try:
                required, required_method = read_requirement_badge(
                    badge,self._run_ocr_tokens,self._recognize_number_crop)
            except ValueError as exc:
                raise ValueError(f'{len(found)+1}번째 필요 수량 판독 실패: {exc}') from exc
            owner_box = (max(0,cx-52), max(0,cy+104), min(735,cx+55), min(290,cy+170))
            owner = image.crop(owner_box)
            tokens = self._run_ocr_tokens(owner.resize((owner.width*3,owner.height*3), Image.Resampling.LANCZOS))
            numbers = set()
            for token in tokens:
                match = re.fullmatch(r'\s*[×xXхХ✕✖╳*]\s*(\d{1,5})\s*', token.text)
                if match and token.score >= .60:
                    numbers.add(int(match.group(1)))
            if len(numbers) > 1:
                raise ValueError(f'{len(found)+1}번째 보유 수량이 서로 다르게 읽혔습니다.')
            if not numbers:
                plain = {int(t.text.strip()) for t in tokens if t.score >= .85
                         and re.fullmatch(r'\s*\d{1,5}\s*', t.text)
                         and t.cx > owner.width*3*.42 and t.cy > owner.height*3*.30}
                if len(plain) == 1:
                    digit_box = (max(0,cx-15),cy+112,min(735,cx+53),min(290,cy+160))
                    digits = image.crop(digit_box)
                    check = self._run_ocr_tokens(digits.resize((digits.width*4,digits.height*4), Image.Resampling.LANCZOS))
                    confirmed = {int(t.text.strip()) for t in check if t.score >= .85
                                 and re.fullmatch(r'\s*\d{1,5}\s*', t.text)}
                    if confirmed == plain:
                        numbers = plain
            if len(numbers) == 1:
                current = numbers.pop()
            else:
                current, _ = _read_owned_digits_from_card(image,cx,cy,
                    self.data_paths['database_dir']/'verified_stock_digits.json')
                if current is None:
                    current = read_owned_digit_line(image,cx,cy,
                        lambda crop:self._recognize_number_crop(crop,allow_multiplier=True))
                if current is None:
                    raise ValueError(f'{len(found)+1}번째 보유 수량을 확정하지 못했습니다.')
            box = (max(0,cx-53),max(0,cy+18),min(735,cx+53),min(290,cy+153))
            if box[2]-box[0] < 95 or box[3]-box[1] < 120:
                raise ValueError('재료 아이콘이 잘려 등록하지 않았습니다.')
            found.append({'crop':image.crop(box),'crop_box':box,'required':required,'current':current,
                          'deficit':max(0,required-current),'x':float(cx),'scan_time':time.time(),
                          'required_method':required_method})
        self._auto_material_notes = f'전체 재료 {len(found)}종 · 흰색/빨간색 필요 수량 모두 확인'
        return found

    def _resource_ocr_worker(self, image: Image.Image, title_image: Optional[Image.Image],
                             manual: bool, fallback_on_failure: bool, generation=None):
        action_epoch=getattr(self,'_stop_epoch',0)
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=action_epoch
        if getattr(self,'_all_actions_stopped',False) is True:return
        try:
            # If F7 arrives during a page-title read, wait in this worker, not
            # on the Tk thread. ocr_busy prevents a new page read from starting.
            gate=getattr(self,'_material_button_ocr_gate',None)
            if gate is not None:
                with gate:
                    pass
            tokens = self._run_ocr_tokens(image) if manual else []
            values, debug_text = extract_resource_values(tokens)
            if not manual:
                debug_text += ("\n[F7 선택 창] " + str(getattr(self, "_last_f7_window_info", "미확인")))
                debug_text += ("\n자동 모드: 빨간 필요 숫자 이미지 판독 + 해당 카드 ×N 부분 OCR"
                               "\n캡처 파일: " + str(self.data_paths["root"] / "debug" / "last_f7_material.png"))
            title_tokens = []
            card_name = None
            if title_image is not None:
                try:
                    title_size=(title_image.width*3,title_image.height*3)
                    title_tokens = self._read_material_title_tokens(title_image)
                    debug_text += '\n'+self._material_title_notes
                    detail_name=crystal_detail_name(title_tokens)
                    if detail_name:
                        binding=getattr(self,'_crystal_detail_binding_snapshot',None)
                        if generation == getattr(self,'_resource_scan_generation',0):
                            post_action_result(self,action_epoch,lambda name=detail_name,b=binding:self._on_crystal_detail_done(name,b))
                        return
                    card_name = extract_card_name_from_title(title_tokens,title_size)
                except Exception as title_exc:
                    debug_text += f"\n제목 OCR 경고: {type(title_exc).__name__}: {title_exc}"
            # The current breakthrough header supplies the owning monster name.
            raw_targets = self._auto_material_targets(image, tokens)
            if not raw_targets and manual:
                enlarged = image.resize((image.width * 3, image.height * 3), Image.Resampling.LANCZOS)
                enlarged_tokens = self._run_ocr_tokens(enlarged)
                raw_targets = extract_crystal_cost_targets(enlarged, enlarged_tokens)
                if raw_targets:
                    for target in raw_targets:
                        x0, y0, x1, y1 = target["crop_box"]
                        box = (max(0, x0//3), max(0, y0//3),
                               min(image.width, (x1+2)//3), min(image.height, (y1+2)//3))
                        target["crop"] = image.crop(box).convert("RGB")
                        target["crop_box"] = box
                    debug_text += "\\n3배 확대 재판독으로 재료 후보 인식"
                else:
                    debug_text += "\\n원본/3배 확대에서 빨간 필요량과 보유량을 연결하지 못함"
            if generation != getattr(self, "_resource_scan_generation", 0):
                return
            targets_meta = []
            for raw_target in raw_targets:
                try:
                    staged=self._stage_crystal_icon(raw_target['crop'])
                    crystal_id=staged['id']
                    meta = {key: value for key, value in raw_target.items() if key != "crop"}
                    meta.update(staged)
                    # Card names are saved per crystal icon when the user scans the
                    # title for the selected target. Reuse that mapping on later F7 reads.
                    saved_db = self._load_crystal_db()
                    saved_entry = next((row for row in saved_db.get("crystals", [])
                                        if str(row.get("id")) == str(crystal_id)), {})
                    meta["card_name"] = str(saved_entry.get("card_name") or "")
                    meta["crop_size"] = tuple(raw_target["crop"].size)
                    meta['db_message']='기존 이미지 재사용' if staged['confirmed'] else '이름 확인 후 등록 대기'
                    targets_meta.append(meta)
                except Exception as exc:
                    debug_text += f"\n목표 아이콘 저장 실패: {exc}"
            if len(targets_meta) != len(raw_targets):
                raise ValueError('일부 재료 아이콘을 등록하지 못해 전체 등록을 보류했습니다.')
            if not manual:
                try:
                    self._v66_log(f"v76 F7 badges: {getattr(self, '_auto_material_notes', 'no notes')}; targets={len(targets_meta)}")
                    self._v66_log("v76 F7 matches: " + repr([(t.get('id'), t.get('db_message'), t.get('required'), t.get('current')) for t in targets_meta]))
                except Exception:
                    pass
            if not targets_meta:
                if not manual and getattr(self, "_auto_material_notes", ""):
                    debug_text += "\n자동 카드 탐색: " + self._auto_material_notes
                values["crystal_required"] = None
                values["crystal_current"] = None
                debug_text += "\n확인된 부족 카드 없음: 영역에 빨간 필요 숫자와 아래 ×N이 온전히 포함되도록 다시 지정하세요."
            if targets_meta:
                values["crystal_required"] = int(targets_meta[0]["required"])
                values["crystal_current"] = int(targets_meta[0]["current"])
                debug_text += "\n부족 재료 후보: " + str(len(targets_meta))
                for index, meta in enumerate(targets_meta, 1):
                    debug_text += (f"\n  {index}. {meta['id']} · 필요 {meta['required']} / 보유 {meta['current']}"
                                   f" / 부족 {meta['deficit']} · {meta['db_message']}")
            elapsed = "\n".join(f"{t.text}  (신뢰도 {t.score:.2f}, x={int(t.x1)}, y={int(t.y1)})" for t in tokens)
            if not elapsed:
                elapsed = "재료 영역에서 텍스트를 인식하지 못했습니다."
            title_debug = "\n".join(f"{t.text}  (신뢰도 {t.score:.2f}, x={int(t.x1)}, y={int(t.y1)})" for t in title_tokens)
            debug = f"[재료 영역 OCR 원문]\n{elapsed}\n\n[재료 숫자 판독]\n" + "\n".join(
                f"{k}: {v if v is not None else '?'}" for k, v in values.items())
            debug += "\n\n[카드 제목 영역 OCR]\n" + (title_debug or "제목 텍스트를 인식하지 못했습니다.")
            debug += f"\n\n[카드 이름 후보]\n{card_name or '미확인'}"
            if debug_text:
                debug += "\n\n[재료/아이콘 판독]\n" + debug_text
            if generation != getattr(self, "_resource_scan_generation", 0):
                return
            source=getattr(self,'_material_capture_context',None)
            if source and title_tokens:
                source=dict(source)
                source['title_box']=(max(0,int(min(t.x1 for t in title_tokens)/3)-5),
                    max(0,int(min(t.y1 for t in title_tokens)/3)-3),
                    min(source['title_image'].width,int(max(t.x2 for t in title_tokens)/3)+5),
                    min(source['title_image'].height,int(max(t.y2 for t in title_tokens)/3)+3))
            post_action_result(self,action_epoch, lambda v=values,d=debug,m=manual,targets=targets_meta,cn=card_name,
                fb=fallback_on_failure,ctx=source:self._on_resource_ocr_done(v,d,m,None,target_metas=targets,
                    card_name=cn,fallback_on_failure=fb,source_context=ctx))
        except Exception as exc:
            if isinstance(exc, ValueError):
                message = f'재료 판독 보류: {exc}\n캡처는 완료됐지만 숫자 또는 아이콘을 확정하지 못했습니다. 기존 등록 수량은 유지합니다.'
            else:
                message = (f'OCR 실행 오류: {type(exc).__name__}: {exc}\n'
                           '모델 초기화나 실행 실패의 상세 내용은 디버그실행.bat 또는 startup.log에서 확인할 수 있습니다.')
            if generation != getattr(self, "_resource_scan_generation", 0):
                return
            post_action_result(self,action_epoch, lambda d=message, m=manual, fb=fallback_on_failure:
                            self._on_resource_ocr_done({}, d, m, message, fallback_on_failure=fb))

    def _clear_active_crystal_target(self):
        """Clear only the active selection, preserving all already recorded events/counts."""
        try:
            with self._crystal_db_lock:
                ledger = self._load_crystal_ledger()
                ledger["active_target"] = None
                _atomic_json_write(self.data_paths["ledger"], ledger)
        except Exception:
            pass
        self.active_target = None
        self._active_target_candidate_ref = None
        self.resource_crystal_id = None
        self.user_settings.pop("target_crystal_id", None)
        self.save_user_settings()

    def _on_resource_ocr_done(self, values, debug, manual, error, crystal_id=None, target_meta=None,
                              card_name=None, target_metas=None, fallback_on_failure=True,source_context=None):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self.ocr_busy = False
        self.last_ocr_debug = debug
        self._last_diagnostic_source = 'f7'
        targets = list(target_metas or ([] if target_meta is None else [target_meta]))
        if error or not targets:
            self._pending_farming_materials = None
            self._farming_sync_views(self._load_crystal_ledger())
            self._recent_material_scan_status = '실패'
            self.set_status('재료 등록 보류 · '+(str(error).splitlines()[0] if error else '전체 재료를 읽지 못했습니다. 돌파 확인 화면에서 F7을 누르세요.'))
            if hasattr(self, 'toolbar'):
                self.toolbar.show_crystal_window()
            return
        self.crystal_candidates = targets
        self._pending_farming_materials = targets
        self.resource_last_read = time.strftime('%H:%M:%S')
        self._recent_material_scan_status = '성공'
        if not card_name:
            self.card_name_detected = ''
            self.set_status('재료는 읽었지만 돌파 대상 이름을 확인하지 못했습니다. [돌파 이름 먼저 등록 / 수정]으로 등록을 완료하세요.')
            if hasattr(self, 'toolbar'):
                self.toolbar.show_crystal_window()
                self.toolbar.refresh()
            return
        self.card_name_detected=card_name
        try:
            key=self._register_farming_profile(card_name, targets)
            self._arm_crystal_clicks(key,targets,source_context)
        except (ValueError, OSError) as exc:
            self.set_status(f'재료 등록 보류: {exc}')
            if hasattr(self,'toolbar'): self.toolbar.refresh()

    def choose_crystal_candidate(self):
        """Set the selected deficient crystal as the one to farm in the current dungeon."""
        if not hasattr(self, "toolbar"):
            return
        tree = getattr(self.toolbar, "candidate_tree", None)
        if tree is None:
            self.set_status("크리스탈 정보창이 없습니다.")
            return
        selection = tree.selection()
        if not selection:
            self.set_status("파밍 목표로 지정할 크리스탈을 목록에서 선택하세요.")
            return
        try:
            index = int(selection[0])
            candidate = self.crystal_candidates[index]
        except (ValueError, IndexError):
            self.set_status("선택한 크리스탈 항목을 확인할 수 없습니다. F7으로 다시 읽어 주세요.")
            return
        candidate_name = str(candidate.get("card_name") or "").strip()
        self._active_target_candidate_ref = candidate
        active = self.activate_crystal_target(candidate["id"], candidate["required"], candidate["current"],
                                              card_name=candidate_name)
        candidate.update(required=active['required'], current=active['owned_before'],
                         deficit=max(0, active['required']-active['owned_before']))
        # Selecting a new target must not inherit the previous target's name.
        self.card_name_detected = candidate_name
        if candidate_name:
            self.user_settings["last_card_name"] = candidate_name
        else:
            self.user_settings.pop("last_card_name", None)
        self.save_user_settings()
        self.set_status(f"파밍 목표 지정: {active.get('card_name') or '카드 이름 미확인'} · 필요 {active['required']} / 보유 {active['owned_before']} / 부족 {active['goal_amount']}")
        try:
            self.root.iconify()
        except tk.TclError:
            pass
        self.toolbar.show_crystal_window()
        self.toolbar.refresh()

    def _schedule_ocr_debug(self):
        """Schedule at most one automatic diagnostic popup for a failed OCR run."""
        if getattr(self, "_ocr_debug_popup_scheduled", False):
            return
        self._ocr_debug_popup_scheduled = True
        def open_once():
            self._ocr_debug_popup_scheduled = False
            self.show_ocr_debug()
        try:
            self.root.after(180, open_once)
        except tk.TclError:
            self._ocr_debug_popup_scheduled = False

    def _refresh_ocr_debug_window(self):
        win = getattr(self, "_ocr_debug_window", None)
        if not win:
            return
        try:
            if not win.winfo_exists():
                return
            widget = getattr(self, "_ocr_debug_text_widget", None)
            if widget is not None and widget.winfo_exists():
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.insert("1.0", self.last_ocr_debug)
                widget.configure(state="disabled")
            crop_path = (getattr(self, "last_f7_capture_path", None)
                         if getattr(self, "_last_diagnostic_source", "f7") == "f7"
                         else getattr(self, "last_card_title_crop_path", None))
            preview_label = getattr(self, "_ocr_debug_preview_label", None)
            path_label = getattr(self, "_ocr_debug_path_label", None)
            if preview_label is not None and preview_label.winfo_exists():
                if crop_path and Path(crop_path).exists():
                    from PIL import ImageTk
                    with Image.open(crop_path) as source:
                        preview = source.convert("RGB")
                    preview.thumbnail((760, 120), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(preview)
                    preview_label.configure(image=photo, text="")
                    preview_label.image = photo
                    self._ocr_debug_preview_photo = photo
                else:
                    preview_label.configure(image="", text="캡처 이미지 없음")
                    preview_label.image = None
                    self._ocr_debug_preview_photo = None
            if path_label is not None and path_label.winfo_exists():
                path_label.configure(text=f"실제 캡처 파일: {crop_path}" if crop_path else "캡처 파일 없음")
        except Exception:
            pass

    def _close_ocr_debug_window(self):
        win = getattr(self, "_ocr_debug_window", None)
        self._ocr_debug_window = None
        self._ocr_debug_text_widget = None
        self._ocr_debug_preview_label = None
        self._ocr_debug_path_label = None
        self._ocr_debug_preview_photo = None
        try:
            if win is not None and win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass

    def show_ocr_debug(self):
        # Reuse one window instead of opening a new diagnostic window per request.
        existing = getattr(self, "_ocr_debug_window", None)
        try:
            if existing is not None and existing.winfo_exists():
                self._refresh_ocr_debug_window()
                existing.deiconify()
                existing.lift()
                return
        except tk.TclError:
            pass
        win = tk.Toplevel(self.root)
        self._ocr_debug_window = win
        win.title(f"퍼드0 OCR 인식 진단 · {APP_TITLE.rsplit(' ', 1)[-1]}")
        win.geometry("820x720")
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="제목 영역과 OCR 원문을 확인하는 진단 창입니다. 반복 호출해도 이 창 하나만 갱신됩니다.",
                  wraplength=780).pack(anchor="w", pady=(0, 8))
        self._ocr_debug_preview_label = ttk.Label(frame, text="캡처 이미지 없음")
        self._ocr_debug_preview_label.pack(anchor="w", pady=(0, 6))
        self._ocr_debug_path_label = ttk.Label(frame, text="")
        self._ocr_debug_path_label.pack(anchor="w", pady=(0, 8))
        self._ocr_debug_text_widget = ScrolledText(frame, wrap="word", font=("Consolas", 10))
        self._ocr_debug_text_widget.pack(fill="both", expand=True)
        self._ocr_debug_text_widget.configure(state="disabled")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="캡처 이미지 폴더 열기", command=self._open_title_debug_folder).pack(side="left")
        ttk.Button(buttons, text="닫기", command=self._close_ocr_debug_window).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", self._close_ocr_debug_window)
        win.bind("<Escape>", lambda _e: self._close_ocr_debug_window())
        # Render the window immediately; load the optional preview on the next idle tick.
        win.after_idle(self._refresh_ocr_debug_window)

    def _open_title_debug_folder(self):
        try:
            path = getattr(self, "last_card_title_crop_path", None)
            folder = Path(path).parent if path else self.data_paths["root"] / "debug"
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            self.set_status(f"캡처 폴더를 열지 못했습니다: {exc}")

    def poll_global_hotkeys(self):
        """Global hotkeys. F6 calculates a route; Shift+F6 forces a fresh search."""
        if __import__("sys").platform == "win32":
            try:
                user32 = ctypes.windll.user32
                f6 = bool(user32.GetAsyncKeyState(0x75) & 0x8000)
                shift = bool(user32.GetAsyncKeyState(0x10) & 0x8000)
                f7 = bool(user32.GetAsyncKeyState(0x76) & 0x8000)
                f8 = bool(user32.GetAsyncKeyState(0x77) & 0x8000)
                f9 = bool(user32.GetAsyncKeyState(0x78) & 0x8000)
                f10 = bool(user32.GetAsyncKeyState(0x79) & 0x8000)
                f12 = bool(user32.GetAsyncKeyState(0x7B) & 0x8000)
                if f12 and not getattr(self,'_f12_down',False): self.save_button_detection_debug()
                self._f12_down=f12
                if f6 and not self._f6_down:
                    self.trigger_route(force=shift)
                if f7 and not self._f7_down:
                    self.request_resource_ocr(manual=False, fallback_on_failure=True)
                if f8 and not self._f8_down:
                    self.overlay.toggle()
                if f9 and not self._f9_down:
                    if hasattr(self, "toolbar"):
                        self.toolbar.toggle_visibility()
                if f10 and not self._f10_down:
                    self.stop_playback()
                self._f6_down, self._f7_down, self._f8_down, self._f9_down, self._f10_down = f6, f7, f8, f9, f10
            except Exception:
                pass
        if self.root.winfo_exists():
            self.root.after(100, self.poll_global_hotkeys)

    def restore_control_panel(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _relative_resource_rect_for_window(self, window_info):
        anchor = self.user_settings.get("resource_window_anchor") if isinstance(self.user_settings, dict) else None
        norm = anchor.get("normalized") if isinstance(anchor, dict) else None
        if not isinstance(norm, list) or len(norm) != 4 or not window_info:
            return None
        try:
            wx, wy, ww, wh = window_info["rect"]
            fx, fy, fw, fh = [float(v) for v in norm]
            return (int(round(wx + fx*ww)), int(round(wy + fy*wh)),
                    max(1, int(round(fw*ww))), max(1, int(round(fh*wh))))
        except Exception:
            return None

    def _remember_resource_anchor(self, rect):
        rect = tuple(int(v) for v in rect)
        self.resource_rect = rect
        self.user_settings["resource_rect"] = list(rect)
        x, y, w, h = rect
        info = find_emulator_window_info(point=(x+w//2, y+h//2))
        if info:
            wx, wy, ww, wh = info["rect"]
            if ww > 400 and wh > 300 and wx <= x+w//2 < wx+ww and wy <= y+h//2 < wy+wh:
                self.user_settings["resource_window_anchor"] = {
                    "title": info["title"],
                    "normalized": [(x-wx)/ww, (y-wy)/wh, w/ww, h/wh],
                    "window_size": [ww, wh],
                }
        self.save_user_settings()

    def _refresh_resource_rect_from_emulator(self):
        point = None
        if self.resource_rect:
            x, y, w, h = self.resource_rect
            point = (x+w//2, y+h//2)
        info = find_emulator_window_info(point=point)
        relative = self._relative_resource_rect_for_window(info)
        if relative:
            if tuple(relative) != tuple(self.resource_rect or ()):
                self.resource_rect = tuple(relative)
                self.user_settings["resource_rect"] = list(relative)
                self.save_user_settings()
        return info

    def select_resource_area(self):
        if self.resource_selector_active:
            self.set_status("이미 영역 지정 창이 열려 있습니다.")
            return
        if self.ocr_busy:
            self.set_status("크리스탈 OCR 중에는 영역을 다시 지정할 수 없어요.")
            return
        if hasattr(self, "toolbar"):
            self.toolbar.hide_for_capture()
        try:
            self.root.iconify()
            self.overlay.hide()
        except Exception:
            pass
        self.set_status("부족 화면에서 빨간 필요 수량·크리스탈 아이콘·아래 ×N이 포함된 영역을 지정하세요. 여러 부족 재료가 있으면 모두 포함해도 됩니다.")
        self.resource_selector_active = True
        try:
            self._resource_selector=ResourceRegionSelector(self)
        except Exception:
            self.resource_selector_active = False
            raise

    def on_resource_area_selected(self, rect):
        self.resource_selector_active = False
        self._remember_resource_anchor(rect)
        self.set_status(f"크리스탈 재료 영역 저장 완료 ({rect[2]}×{rect[3]}). 빨간 필요 수량과 아래 ×N을 판독합니다…")
        # Show the crystal panel only after OCR completes.
        self.root.after(100, lambda epoch=getattr(self,'_stop_epoch',0): self.request_resource_ocr(manual=True, fallback_on_failure=False) if action_request_current(self,epoch) else None)

    def _relative_card_title_rect_for_window(self, window_info):
        anchor = self.user_settings.get("card_title_window_anchor") if isinstance(self.user_settings, dict) else None
        norm = anchor.get("normalized") if isinstance(anchor, dict) else None
        if not isinstance(norm, list) or len(norm) != 4 or not window_info:
            return None
        try:
            wx, wy, ww, wh = window_info["rect"]
            fx, fy, fw, fh = [float(v) for v in norm]
            return (int(round(wx + fx * ww)), int(round(wy + fy * wh)),
                    max(1, int(round(fw * ww))), max(1, int(round(fh * wh))))
        except Exception:
            return None

    def _remember_card_title_anchor(self, rect):
        rect = tuple(int(v) for v in rect)
        self.card_title_rect = rect
        self.user_settings["card_title_rect"] = list(rect)
        x, y, w, h = rect
        point = (x + w // 2, y + h // 2)
        info = find_emulator_window_info(point=point)
        if info:
            wx, wy, ww, wh = info["rect"]
            if ww > 400 and wh > 300 and wx <= point[0] < wx + ww and wy <= point[1] < wy + wh:
                self.user_settings["card_title_window_anchor"] = {
                    "title": info["title"],
                    "normalized": [(x - wx) / ww, (y - wy) / wh, w / ww, h / wh],
                    "window_size": [ww, wh],
                }
        self.save_user_settings()

    def _refresh_card_title_rect_from_emulator(self):
        if not self.card_title_rect:
            return None
        x, y, w, h = self.card_title_rect
        info = find_emulator_window_info(point=(x + w // 2, y + h // 2))
        relative = self._relative_card_title_rect_for_window(info)
        if relative:
            if tuple(relative) != tuple(self.card_title_rect):
                self.card_title_rect = tuple(relative)
                self.user_settings["card_title_rect"] = list(relative)
                self.save_user_settings()
        return info

    def _begin_card_title_capture_session(self):
        if self._card_title_capture_session is not None:
            return
        try:
            root_state = self.root.state()
            root_visible = root_state not in ("iconic", "withdrawn") and bool(self.root.winfo_viewable())
        except tk.TclError:
            root_state, root_visible = "withdrawn", False
        toolbar_button = getattr(self.toolbar, "win", None) if hasattr(self, "toolbar") else None
        try:
            button_visible = bool(
                toolbar_button is not None and toolbar_button.winfo_exists()
                and toolbar_button.state() != "withdrawn"
                and not getattr(self.toolbar, "_user_hidden", False)
            )
            button_topmost = bool(toolbar_button.attributes("-topmost")) if button_visible else False
        except tk.TclError:
            button_visible, button_topmost = False, False
        state = {
            "root_state": root_state,
            "root_visible": root_visible,
            "button_visible": button_visible,
            "button_topmost": button_topmost,
            "button_was_temporarily_hidden": bool(getattr(self.toolbar, "_temporarily_hidden", False)) if hasattr(self, "toolbar") else False,
            "overlay_visible": bool(self.overlay.visible),
            "route_drawings_visible": bool(self.overlay.route_drawings_visible),
            "hud_visible": bool(self.overlay.hud_visible),
        }
        # Save the window visibility before selecting a title region.  The main
        # window stays on the taskbar; the auxiliary Toplevels are temporarily
        # hidden because they would otherwise cover the emulator or OCR crop.
        panel = getattr(self.toolbar, "crystal_window", None) if hasattr(self, "toolbar") else None
        strip = getattr(self.toolbar, "farming_strip", None) if hasattr(self, "toolbar") else None
        for key, win in (("crystal_panel", panel), ("farming_strip", strip)):
            try:
                state[key + "_visible"] = bool(win and win.winfo_exists() and win.state() != "withdrawn")
            except tk.TclError:
                state[key + "_visible"] = False
        self._card_title_capture_session = state
        # Card naming permanently releases the management-window pin.
        self.crystal_panel_pin_var.set(False)
        if panel is not None and panel.winfo_exists():
            try:
                panel.attributes("-topmost", False)
            except tk.TclError:
                pass
        for key, win in (("crystal_panel", panel), ("farming_strip", strip)):
            if state[key + "_visible"]:
                try:
                    win.withdraw()
                except tk.TclError:
                    pass
        # Hide the floating execute control and the route layer too.
        try:
            if toolbar_button is not None and button_visible:
                self.toolbar._temporarily_hidden = True
                toolbar_button.withdraw()
        except Exception:
            pass
        try:
            self.overlay.hide()
        except Exception:
            pass
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

    def _lower_root_without_activation(self):
        """Send the main control window behind other apps without changing foreground focus."""
        try:
            if self.root.state() != "normal":
                return
            if sys.platform == "win32":
                from ctypes import wintypes
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                raw_hwnd = wintypes.HWND(self.root.winfo_id())
                user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
                user32.GetAncestor.restype = wintypes.HWND
                hwnd = user32.GetAncestor(raw_hwnd, 2) or raw_hwnd  # GA_ROOT
                user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
                user32.SetWindowPos.restype = wintypes.BOOL
                flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
                user32.SetWindowPos(hwnd, wintypes.HWND(1), 0, 0, 0, 0, flags)  # HWND_BOTTOM
            else:
                self.root.lower()
        except Exception:
            try:
                self.root.lower()
            except Exception:
                pass

    def _restore_root_without_activation(self, prior_state):
        """Restore the main window behind the emulator without stealing focus."""
        try:
            if prior_state == "normal":
                self.root.deiconify()
                self.root.update_idletasks()
                if sys.platform == "win32":
                    from ctypes import wintypes
                    user32 = ctypes.WinDLL("user32", use_last_error=True)
                    raw_hwnd = wintypes.HWND(self.root.winfo_id())
                    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
                    user32.GetAncestor.restype = wintypes.HWND
                    hwnd = user32.GetAncestor(raw_hwnd, 2) or raw_hwnd  # GA_ROOT
                    # SW_SHOWNOACTIVATE + move to bottom: visible but won't jump
                    # in front of the MuMu game or become the active window.
                    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                    user32.ShowWindow.restype = wintypes.BOOL
                    user32.ShowWindow(hwnd, 4)
                    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
                    user32.SetWindowPos.restype = wintypes.BOOL
                    flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
                    user32.SetWindowPos(hwnd, wintypes.HWND(1), 0, 0, 0, 0, flags)  # HWND_BOTTOM
                else:
                    self.root.lower()
            elif prior_state == "iconic":
                self.root.iconify()
            else:
                self.root.withdraw()
        except Exception:
            try:
                if prior_state == "normal":
                    self.root.deiconify()
                    self.root.lower()
                elif prior_state == "iconic":
                    self.root.iconify()
                else:
                    self.root.withdraw()
            except Exception:
                pass

    def _finish_card_title_capture_session(self, restore_route=True):
        state = self._card_title_capture_session
        self._card_title_capture_session = None
        if not state:
            return
        if state.get("root_visible"):
            try:
                self.root.after(40, self._lower_root_without_activation)
            except tk.TclError:
                pass
        # Restore only the crystal panel (or the compact strip if pinned). Do not
        # bring the main window forward; it remains available via the taskbar.
        try:
            if state.get("crystal_panel_visible") and hasattr(self, "toolbar"):
                panel = getattr(self.toolbar, "crystal_window", None)
                if panel is not None and panel.winfo_exists():
                    panel.deiconify()
                    panel.lift()
                    panel.focus_set()
            elif state.get("farming_strip_visible") and hasattr(self, "toolbar"):
                strip = getattr(self.toolbar, "farming_strip", None)
                if strip is not None and strip.winfo_exists():
                    strip.deiconify()
                    strip.lift()
        except tk.TclError:
            pass
        try:
            toolbar_button = getattr(self.toolbar, "win", None) if hasattr(self, "toolbar") else None
            if toolbar_button is not None and toolbar_button.winfo_exists():
                self.toolbar._temporarily_hidden = bool(state.get("button_was_temporarily_hidden", False))
                if (state.get("button_visible") and not getattr(self.toolbar, "_user_hidden", False)
                        and self._path_button_may_show()):
                    toolbar_button.deiconify()
                    toolbar_button.attributes("-topmost", bool(state.get("button_topmost", False)))
                    # No lift(): preserve the current foreground application.
                elif not state.get("button_visible"):
                    toolbar_button.withdraw()
        except Exception:
            pass
        try:
            if restore_route and state.get("overlay_visible"):
                self.overlay.win.deiconify()
                self.overlay.win.attributes("-topmost", True)
                self.overlay.clickthrough_ok = set_clickthrough(self.overlay.win)
                self.overlay.visible = True
                self.overlay.route_drawings_visible = bool(state.get("route_drawings_visible"))
                self.overlay.canvas.itemconfigure(
                    "route", state=(tk.NORMAL if state.get("route_drawings_visible") else tk.HIDDEN)
                )
                self.overlay.hud_visible = bool(state.get("hud_visible"))
                self.overlay._last_hud_draw = None
                self.overlay._draw_hud()
            else:
                self.overlay.hide()
        except Exception:
            pass

    def cancel_card_title_area_selection(self):
        self._finish_card_title_capture_session(restore_route=True)
        self._card_title_target_id_snapshot = None
        self._card_title_candidate_ref = None
        self.set_status("카드 이름 영역 지정이 취소됐어요. 크리스탈 수량 판독은 그대로 유지됩니다.")
        if hasattr(self, "toolbar"):
            self.toolbar.refresh()

    def select_card_title_area(self):
        if self.ocr_busy:
            self.set_status("OCR 진행 중에는 카드 이름 영역을 지정할 수 없어요. 잠시 기다려 주세요.")
            return
        if not self.active_target:
            self.set_status("먼저 부족 재료 목록에서 이번 파밍 목표를 지정하세요. 카드 이름은 선택된 목표에 저장됩니다.")
            try:
                messagebox.showinfo("파밍 목표 먼저 지정", "먼저 크리스탈 정보창에서 '선택 항목을 파밍 목표로 지정'을 누른 다음, 카드 이름 영역을 지정해 주세요.", parent=self.root)
            except Exception:
                pass
            return
        # Keep the selected card target fixed for this scan. The resulting title
        # must rename this candidate/target, not whichever target might be active later.
        self._card_title_target_id_snapshot = str((self.active_target or {}).get("id") or "")
        self._card_title_candidate_ref = self._active_target_candidate_ref
        if self._card_title_candidate_ref is None:
            active_id = self._card_title_target_id_snapshot
            matching = [c for c in self.crystal_candidates if str(c.get("id", "")) == active_id]
            if len(matching) == 1:
                self._card_title_candidate_ref = matching[0]
        # Hide the crystal panel, compact strip and floating controls while selecting.
        self._begin_card_title_capture_session()
        try:
            self.root.iconify()
        except tk.TclError:
            pass
        self.set_status("선택한 파밍 목표의 카드 제목 전체를 드래그하세요. 이름은 이 목표에 저장됩니다.")
        try:
            self._card_title_selector=CardTitleRegionSelector(self)
        except Exception:
            self._finish_card_title_capture_session(restore_route=True)
            self._card_title_target_id_snapshot = None
            self._card_title_candidate_ref = None
            raise

    def on_card_title_area_selected(self, rect):
        self._remember_card_title_anchor(rect)
        self.set_status(f"카드 이름 영역 저장 완료 ({rect[2]}×{rect[3]}). 제목을 읽는 중…")
        # The selector is a child of the main window; Windows may otherwise return
        # focus to that parent when the selector closes. Lower it without activation.
        self.root.after_idle(self._lower_root_without_activation)
        self.root.after(100, self.request_card_name_ocr)

    def _capture_card_title_area_for_ocr(self):
        self._begin_card_title_capture_session()
        self._refresh_card_title_rect_from_emulator()
        if not self.card_title_rect:
            raise RuntimeError("카드 이름 영역을 먼저 지정하세요.")
        left, top, width, height = [int(v) for v in self.card_title_rect]
        if width < 80 or height < 16:
            raise RuntimeError("카드 이름 영역이 너무 작습니다. 제목 글자가 모두 들어가도록 다시 지정하세요.")
        try:
            prior_root_state = self.root.state()
            root_was_visible = prior_root_state not in ("iconic", "withdrawn") and bool(self.root.winfo_viewable())
        except tk.TclError:
            prior_root_state, root_was_visible = "withdrawn", False
        try:
            if root_was_visible:
                self.root.withdraw()
            self.root.update_idletasks()
            time.sleep(0.18)
            with mss.mss() as sct:
                shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                img = Image.frombytes("RGB", shot.size, shot.rgb)
            # Save the exact crop used by OCR. This lets us distinguish a coordinate/capture
            # problem from an OCR model problem instead of guessing.
            try:
                self.last_card_title_crop_path.parent.mkdir(parents=True, exist_ok=True)
                img.save(self.last_card_title_crop_path)
            except Exception as save_exc:
                self.last_ocr_debug = f"제목 캡처 저장 경고: {type(save_exc).__name__}: {save_exc}"
            return img
        finally:
            if root_was_visible:
                self._restore_root_without_activation(prior_root_state)

    def request_card_name_ocr(self):
        if self.ocr_busy:
            self.set_status("OCR 진행 중입니다. 잠시 기다려 주세요.")
            return
        if not self.card_title_rect:
            self.select_card_title_area()
            return
        try:
            image = self._capture_card_title_area_for_ocr()
        except Exception as exc:
            self.last_ocr_debug = f"카드 이름 영역 캡처 실패: {exc}"
            self._finish_card_title_capture_session(restore_route=True)
            self._card_title_target_id_snapshot = None
            self._card_title_candidate_ref = None
            self.set_status(self.last_ocr_debug)
            self.set_status("인식 진단은 크리스탈 창의 원문 버튼으로 확인할 수 있습니다.")
            if hasattr(self, "toolbar"):
                self.toolbar.refresh()
            return
        self.ocr_busy = True
        self.set_status("카드 제목 인식 중…")
        threading.Thread(target=self._card_name_ocr_worker, args=(image,), daemon=True).start()

    def _card_name_ocr_worker(self, image: Image.Image):
        """Use padded/high-contrast RapidOCR variants and optional Tesseract fallback for Korean titles."""
        action_epoch=getattr(self,'_stop_epoch',0)
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=action_epoch
        if getattr(self,'_all_actions_stopped',False) is True:return
        try:
            from PIL import ImageOps, ImageEnhance, ImageFilter

            # Fast path: run Tesseract directly on the original selected title crop
            # before expensive RapidOCR variants. The title strip is legible at native
            # resolution, so try the original first and stop when the full heading is found.
            fast_tesseract_debug = []
            tess_path = shutil.which("tesseract")
            if not tess_path:
                for candidate_tess in (
                    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
                    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
                ):
                    if candidate_tess.exists():
                        tess_path = str(candidate_tess)
                        break
            if tess_path:
                try:
                    debug_dir = Path(getattr(self, "last_card_title_crop_path", Path.cwd())).parent
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    direct_path = debug_dir / "tesseract_title_direct_input.png"
                    image.convert("RGB").save(direct_path)
                    for psm in (7, 6):
                        try:
                            proc = subprocess.run(
                                [tess_path, str(direct_path), "stdout", "-l", "kor+eng", "--oem", "1",
                                 "--psm", str(psm), "--dpi", "300", "-c", "preserve_interword_spaces=1"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                            )
                            raw = (proc.stdout or "").strip()
                            compact = _compact_ocr_text(raw)
                            parsed = extract_card_name_from_raw_text(raw)
                            stderr = (proc.stderr or "").strip().replace("\r", " ").replace("\n", " | ")
                            fast_tesseract_debug.append(
                                f"[Tesseract 원본/psm{psm}] {raw or '텍스트 없음'}"
                                f"\n판정: {parsed or '미확인'} · 종료코드 {proc.returncode}"
                                + (f"\n메시지: {stderr[:240]}" if stderr else "")
                            )
                            has_title_suffix = any(suffix in compact for suffix in ("크리스탈", "크리스털", "생성", "한계돌파"))
                            if proc.returncode == 0 and parsed and has_title_suffix:
                                # Full title matched: skip the costly multi-variant OCR cascade.
                                debug = (
                                    "[카드 이름 OCR 원문 · Tesseract 빠른 판독]\n"
                                    + "\n\n".join(fast_tesseract_debug)
                                    + f"\n\n[최종 판정]\n{parsed}\n선택한 엔진: Tesseract/원본/psm{psm}"
                                    + f"\n캡처 이미지: {self.last_card_title_crop_path}"
                                )
                                post_action_result(self,action_epoch, lambda n=parsed, d=debug: self._on_card_name_ocr_done(n, d, None))
                                return
                        except subprocess.TimeoutExpired:
                            fast_tesseract_debug.append(f"[Tesseract 원본/psm{psm}] 제한 시간 초과")
                except Exception as direct_exc:
                    fast_tesseract_debug.append(f"[Tesseract 원본 빠른 판독 오류] {type(direct_exc).__name__}: {direct_exc}")
            else:
                fast_tesseract_debug.append("[Tesseract 원본 빠른 판독] 실행 파일을 찾지 못했습니다.")

            base = image.convert("RGB")
            debug_parts = list(fast_tesseract_debug)
            tries = []

            # Title letters in the game are dark gray on a pale strip. The extra
            # border gives the OCR detector margin instead of putting the text at
            # the very edge of a narrow manually selected crop.
            padded = ImageOps.expand(base, border=(16, 18, 16, 18), fill=(245, 245, 245))
            variants = []
            variants.append(("여백 포함 원본 3배", padded.resize((padded.width * 3, padded.height * 3), Image.Resampling.LANCZOS)))
            gray = ImageOps.autocontrast(padded.convert("L"))
            gray = ImageEnhance.Contrast(gray).enhance(2.0).filter(ImageFilter.SHARPEN)
            variants.append(("고대비 회색 4배", gray.resize((gray.width * 4, gray.height * 4), Image.Resampling.LANCZOS).convert("RGB")))
            variants.append(("이진화 4배", gray.point(lambda px: 255 if px >= 160 else 0).resize((gray.width * 4, gray.height * 4), Image.Resampling.LANCZOS).convert("RGB")))
            variants.append(("반전 대비 4배", ImageOps.invert(gray).resize((gray.width * 4, gray.height * 4), Image.Resampling.LANCZOS).convert("RGB")))
            median = gray.filter(ImageFilter.MedianFilter(size=3))
            variants.append(("노이즈 완화 4배", median.resize((median.width * 4, median.height * 4), Image.Resampling.LANCZOS).convert("RGB")))

            for variant_name, variant in variants:
                tokens = self._run_ocr_tokens(variant)
                ordered = sorted((t for t in tokens if t.text.strip()), key=lambda t: (t.cy, t.x1))
                lines = " ".join(t.text.strip() for t in ordered)
                token_texts = "\n".join(f"{t.text.strip()} (신뢰도 {t.score:.2f})" for t in ordered)
                name = extract_card_name_from_title(tokens, variant.size)
                if not name:
                    name = extract_card_name_from_raw_text(lines)
                avg = (sum(t.score for t in ordered) / len(ordered)) if ordered else 0.0
                debug_parts.append(f"[{variant_name}] {token_texts or '텍스트 없음'}\n판정: {name or '미확인'} · 평균 신뢰도 {avg:.2f}")
                if name:
                    compact_line = _compact_ocr_text(lines)
                    # Prefer a complete heading ending in "의 크리스탈" over a
                    # short partial word such as "용제왕" or a noisy suffix like "외 크리스탈".
                    title_compact = _compact_ocr_text(lines)
                    title_hint = (2.0 if re.search(r"의(?:크리스탈|크리스털)(?:획득)?$", title_compact)
                                  else 1.0 if any(k in title_compact for k in ("크리스탈", "크리스털", "생성", "한계돌파"))
                                  else 0.0)
                    tries.append(((title_hint, avg, len(_compact_ocr_text(name))), name, f"RapidOCR/{variant_name}"))

            # Tesseract fallback: unlike the previous release, never pass the tiny
            # original crop directly. Create several high-resolution, high-contrast
            # variants from the in-memory image that RapidOCR just processed. Save
            # each variant for diagnostics and report stderr/return code when Tesseract
            # cannot read it, rather than silently presenting an empty result.
            tess = shutil.which("tesseract")
            if not tess:
                for candidate_tess in (
                    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
                    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
                ):
                    if candidate_tess.exists():
                        tess = str(candidate_tess)
                        break
            if tess:
                try:
                    debug_dir = Path(getattr(self, "last_card_title_crop_path", Path.cwd())).parent
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    tpad = ImageOps.expand(base, border=(28, 24, 28, 24), fill=(255, 255, 255))
                    tgray = ImageOps.autocontrast(tpad.convert("L"))
                    tgray = ImageEnhance.Contrast(tgray).enhance(2.3).filter(ImageFilter.SHARPEN)
                    scale = 4
                    tesseract_variants = [
                        ("원본 여백 4배", tpad.resize((tpad.width * scale, tpad.height * scale), Image.Resampling.LANCZOS).convert("RGB")),
                        ("고대비 회색 4배", tgray.resize((tgray.width * scale, tgray.height * scale), Image.Resampling.LANCZOS).convert("RGB")),
                        ("이진화 4배", tgray.point(lambda px: 255 if px >= 160 else 0).resize((tgray.width * scale, tgray.height * scale), Image.Resampling.LANCZOS).convert("RGB")),
                        ("반전 회색 4배", ImageOps.invert(tgray).resize((tgray.width * scale, tgray.height * scale), Image.Resampling.LANCZOS).convert("RGB")),
                    ]
                    for variant_index, (variant_name, tess_image) in enumerate(tesseract_variants, start=1):
                        tess_image_path = debug_dir / f"tesseract_title_variant_{variant_index}.png"
                        tess_image.save(tess_image_path)
                        for psm in (7, 6, 11, 13):
                            try:
                                proc = subprocess.run(
                                    [tess, str(tess_image_path), "stdout", "-l", "kor+eng", "--oem", "1",
                                     "--psm", str(psm), "--dpi", "300", "-c", "preserve_interword_spaces=1"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                                )
                                raw = (proc.stdout or "").strip()
                                stderr = (proc.stderr or "").strip().replace("\r", " ").replace("\n", " | ")
                                parsed = extract_card_name_from_raw_text(raw)
                                debug_parts.append(
                                    f"[Tesseract/{variant_name}/psm{psm}] {raw or '텍스트 없음'}"
                                    f"\n판정: {parsed or '미확인'} · 종료코드 {proc.returncode}"
                                    + (f"\nTesseract 메시지: {stderr[:500]}" if stderr else "")
                                )
                                if parsed:
                                    compact_line = _compact_ocr_text(raw)
                                    # Exact suffix recognition outranks noisy title lines;
                                    # all OCR engines still compete on confidence and name length.
                                    title_hint = (2.0 if re.search(r"의(?:크리스탈|크리스털)(?:획득)?$", compact_line)
                                                  else 1.0 if any(k in compact_line for k in ("크리스탈", "크리스털", "생성", "한계돌파"))
                                                  else 0.0)
                                    tries.append(((title_hint, 0.98, len(_compact_ocr_text(parsed))), parsed,
                                                  f"Tesseract/{variant_name}/psm{psm}"))
                            except subprocess.TimeoutExpired:
                                debug_parts.append(f"[Tesseract/{variant_name}/psm{psm}] 제한 시간 초과")
                except Exception as tess_exc:
                    debug_parts.append(f"[Tesseract 대체 OCR 오류] {type(tess_exc).__name__}: {tess_exc}")
            else:
                debug_parts.append("[대체 OCR] Tesseract 실행 파일을 찾지 못함. PATH 또는 설치 경로를 확인하세요.")

            if tries:
                # Prefer a line where the expected title suffix was actually seen,
                # then confidence; do not choose a one-word fragment over a full title.
                tries.sort(key=lambda item: item[0], reverse=True)
                name = tries[0][1]
                chosen_source = tries[0][2]
            else:
                name = None
                chosen_source = "없음"
            crop_info = f"\n캡처 이미지: {self.last_card_title_crop_path}" if getattr(self, "last_card_title_crop_path", None) else ""
            debug = "[카드 이름 OCR 원문 · 다중 판독]\n" + "\n\n".join(debug_parts)
            debug += f"\n\n[최종 판정]\n{name or '미확인'}\n선택한 엔진: {chosen_source}{crop_info}"
            post_action_result(self,action_epoch, lambda n=name, d=debug: self._on_card_name_ocr_done(n, d, None if n else "카드 이름을 읽지 못했습니다."))
        except Exception as exc:
            debug = f"카드 이름 OCR 오류: {type(exc).__name__}: {exc}\n캡처 이미지: {getattr(self, 'last_card_title_crop_path', '')}"
            try:
                post_action_result(self,action_epoch, lambda d=debug: self._on_card_name_ocr_done(None, d, d))
            except tk.TclError:
                pass

    def _on_card_name_ocr_done(self, name, debug, error):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self.ocr_busy = False
        self.last_ocr_debug = debug
        captured_target_id = str(self._card_title_target_id_snapshot or "")
        candidate_ref = self._card_title_candidate_ref
        self._finish_card_title_capture_session(restore_route=False)
        if name:
            resolved_name = str(name).strip()[:60]
            # Assign the name only to the target explicitly selected before the scan.
            target_matches = bool(
                captured_target_id and self.active_target
                and str(self.active_target.get("id", "")) == captured_target_id
            )
            if target_matches and candidate_ref is not None and any(c is candidate_ref for c in self.crystal_candidates):
                candidate_ref["card_name"] = resolved_name
            else:
                matches = [c for c in self.crystal_candidates
                           if str(c.get("id", "")) == captured_target_id]
                if target_matches and len(matches) == 1:
                    matches[0]["card_name"] = resolved_name
            if target_matches:
                self.card_name_detected = resolved_name
                self.active_target["card_name"] = resolved_name
                self.active_target["label"] = resolved_name
                self.user_settings["last_card_name"] = resolved_name
                try:
                    # Rename only the icon entry attached to the selected target.
                    # The saved image remains unchanged; this is its persistent display name.
                    with self._crystal_db_lock:
                        db = self._load_crystal_db()
                        entry = next((row for row in db.get("crystals", [])
                                      if str(row.get("id", "")) == captured_target_id), None)
                        if entry is not None:
                            entry["card_name"] = resolved_name
                            entry["label"] = resolved_name
                            _atomic_json_write(self.data_paths["database"], db)
                    ledger = self._load_crystal_ledger()
                    saved_target = ledger.get("active_target") if isinstance(ledger.get("active_target"), dict) else None
                    if saved_target and str(saved_target.get("id", "")) == captured_target_id:
                        saved_target["card_name"] = resolved_name
                        saved_target["label"] = resolved_name
                        ledger["active_target"] = saved_target
                        _atomic_json_write(self.data_paths["ledger"], ledger)
                except Exception:
                    pass
                self.save_user_settings()
                self.set_status(f"파밍 목표 이름 저장 완료: {resolved_name}")
            else:
                self.set_status(f"카드 이름은 읽었지만 대상이 바뀌어 저장하지 않았습니다: {resolved_name}")
        else:
            self.set_status("카드 이름을 읽지 못했습니다. OCR 원문에서 캡처와 판독 결과를 확인하세요.")
        # Refresh existing crystal UI in place. Do not deiconify/lift it; this avoids
        # the previous close/reopen flash and keeps the game in the foreground.
        if hasattr(self, "toolbar"):
            try:
                self.toolbar.refresh()
            except Exception:
                pass
        self.overlay.update_hud(resources=self.format_resource_hud())
        self._card_title_target_id_snapshot = None
        self._card_title_candidate_ref = None
        if error or not name:
            self.set_status("인식 진단은 크리스탈 창의 원문 버튼으로 확인할 수 있습니다.")

    def edit_target_card_name(self):
        initial = self.card_name_detected or ((self.active_target or {}).get("card_name", "") if self.active_target else "")
        name = simpledialog.askstring("카드 이름", "상단 제목을 읽지 못했다면 카드 이름을 입력하세요.",
                                      initialvalue=initial, parent=self.root)
        if name is None:
            return
        name = name.strip()[:60]
        self.card_name_detected = name
        active_id = str((self.active_target or {}).get("id", ""))
        for candidate in getattr(self, "crystal_candidates", []):
            if active_id and str(candidate.get("id", "")) == active_id:
                candidate["card_name"] = name
        if self.active_target:
            self.active_target["card_name"] = name
            try:
                ledger = self._load_crystal_ledger()
                ledger["active_target"] = self.active_target
                _atomic_json_write(self.data_paths["ledger"], ledger)
            except Exception:
                pass
        self.user_settings["last_card_name"] = name
        self.save_user_settings()
        if hasattr(self, "toolbar"):
            self.toolbar.refresh()
        self.set_status(f"카드 이름 저장: {name or '미지정'}")

    def auto_detect_area(self, start_calculation=False):
        resume_actions(self)
        if self.detecting_area:
            return
        if self.busy or self.playback_active:
            self.set_status("경로 계산 또는 실행 중에는 영역을 탐지할 수 없어요.")
            return
        self.detecting_area = True
        self.detect_then_calculate = bool(start_calculation)
        self.set_status("뮤뮤 창/저장 좌표를 먼저 확인하고, 필요할 때만 전체 화면을 탐색합니다…")
        restore_route = bool(self.overlay.visible and self.last_path and self.overlay.route_drawings_visible)
        restore_hud = bool(self.overlay.hud_visible)
        toolbar_was_visible = self.toolbar.is_visible() if hasattr(self, "toolbar") else False
        try:
            if toolbar_was_visible: self.toolbar.hide_for_capture()
            if self.overlay.route_drawings_visible:
                self.overlay.set_route_drawings_visible(False)
            if restore_hud:
                self.overlay.set_hud_visible(False)
            self.root.update_idletasks()
            time.sleep(0.03)
            with mss.mss() as sct:
                monitor = dict(sct.monitors[0])  # full virtual desktop, including all monitors
                shot = sct.grab(monitor)
                screen_image = Image.frombytes("RGB", shot.size, shot.rgb)
            window_info = find_emulator_window_info()
            saved_rect = self.board_rect
            if saved_rect is None:
                try:
                    vals = self.user_settings.get("board_rect")
                    saved_rect = tuple(int(v) for v in vals) if vals and len(vals) == 4 else None
                except Exception:
                    saved_rect = None
            if toolbar_was_visible: self.toolbar.restore_after_capture()
            if restore_route and self.overlay.visible:
                self.overlay.set_route_drawings_visible(True)
            if restore_hud:
                self.overlay.set_hud_visible(True)
            threading.Thread(target=self._detect_area_worker,
                             args=(screen_image, monitor, self.detect_then_calculate, window_info, saved_rect),
                             daemon=True).start()
        except Exception as exc:
            if restore_route and self.overlay.visible:
                self.overlay.set_route_drawings_visible(True)
            if restore_hud:
                self.overlay.set_hud_visible(True)
            if toolbar_was_visible: self.toolbar.restore_after_capture()
            self.detecting_area = False
            self.set_status(f"화면 캡처 오류: {exc}")

    @staticmethod
    def _crop_rect_from_desktop(image, monitor, rect, margin=0):
        if not rect:
            return None, None
        x, y, w, h = [int(v) for v in rect]
        x -= int(monitor.get("left", 0))
        y -= int(monitor.get("top", 0))
        x0, y0 = max(0, x-margin), max(0, y-margin)
        x1, y1 = min(image.width, x+w+margin), min(image.height, y+h+margin)
        if x1 <= x0 or y1 <= y0:
            return None, None
        return image.crop((x0, y0, x1, y1)), (x0 + int(monitor.get("left", 0)), y0 + int(monitor.get("top", 0)))

    def _relative_rect_for_window(self, window_info):
        anchor = self.user_settings.get("board_window_anchor") if isinstance(self.user_settings, dict) else None
        if not isinstance(anchor, dict):
            return None
        norm = anchor.get("normalized")
        if not isinstance(norm, list) or len(norm) != 4 or not window_info:
            return None
        try:
            wx, wy, ww, wh = window_info["rect"]
            fx, fy, fw, fh = [float(v) for v in norm]
            return (int(round(wx + fx*ww)), int(round(wy + fy*wh)),
                    int(round(fw*ww)), int(round(fh*wh)))
        except Exception:
            return None

    def _remember_board_anchor(self, rect):
        """Save board position as normalized coordinates in the MuMu host window."""
        rect = tuple(int(v) for v in rect)
        self.user_settings["board_rect"] = list(rect)
        x, y, w, h = rect
        info = find_emulator_window_info(point=(x + w//2, y + h//2))
        if info:
            wx, wy, ww, wh = info["rect"]
            if ww > 400 and wh > 300 and wx <= x+w//2 < wx+ww and wy <= y+h//2 < wy+wh:
                self.user_settings["board_window_anchor"] = {
                    "title": info["title"],
                    "normalized": [(x-wx)/ww, (y-wy)/wh, w/ww, h/wh],
                    "window_size": [ww, wh],
                }
        self.save_user_settings()

    def _refresh_board_rect_from_emulator(self):
        point = None
        if self.board_rect:
            bx, by, bw, bh = self.board_rect
            point = (bx + bw//2, by + bh//2)
        info = find_emulator_window_info(point=point)
        rel = self._relative_rect_for_window(info)
        if rel:
            current = tuple(int(v) for v in self.board_rect) if self.board_rect else None
            if current != rel:
                self.board_rect = rel
                self.user_settings["board_rect"] = list(rel)
                self.save_user_settings()
        return info

    def _detect_area_worker(self, screen_image, monitor, then_calculate, window_info=None, saved_rect=None):
        action_epoch=getattr(self,'_stop_epoch',0)
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=action_epoch
        if getattr(self,'_all_actions_stopped',False) is True:return
        try:
            monitor_origin = (int(monitor.get("left", 0)), int(monitor.get("top", 0)))
            # 1) Prefer the saved normalized location in the current MuMu window. This is fast
            # and tracks moving/resized emulator windows without asking the user again.
            rel_rect = self._relative_rect_for_window(window_info)
            if rel_rect:
                exact, _ = self._crop_rect_from_desktop(screen_image, monitor, rel_rect, margin=0)
                if exact is not None and _board_image_is_plausible(exact):
                    post_action_result(self,action_epoch, lambda r=rel_rect: self._on_auto_area_detected(r, 0.99, None, then_calculate))
                    return
            # 2) Validate the last absolute rect (older settings may not have a MuMu anchor).
            if saved_rect:
                exact, _ = self._crop_rect_from_desktop(screen_image, monitor, saved_rect, margin=0)
                if exact is not None and _board_image_is_plausible(exact):
                    post_action_result(self,action_epoch, lambda r=tuple(saved_rect): self._on_auto_area_detected(r, 0.98, None, then_calculate))
                    return
                # 3) Search a small neighborhood around the saved location before scanning the desktop.
                margin = max(120, int(max(saved_rect[2], saved_rect[3]) * 0.55))
                local, local_origin = self._crop_rect_from_desktop(screen_image, monitor, saved_rect, margin=margin)
                if local is not None:
                    try:
                        (lx, ly, lw, lh), confidence = detect_puzzle_area(local)
                        candidate = (local_origin[0]+lx, local_origin[1]+ly, lw, lh)
                        region, _ = self._crop_rect_from_desktop(screen_image, monitor, candidate, margin=0)
                        if region is not None and _board_image_is_plausible(region):
                            post_action_result(self,action_epoch, lambda r=candidate, c=confidence: self._on_auto_area_detected(r, c, None, then_calculate))
                            return
                    except Exception:
                        pass
            # 4) Detect within the MuMu/Android window first, reducing the search area a lot.
            if window_info:
                wx, wy, ww, wh = window_info["rect"]
                crop, origin = self._crop_rect_from_desktop(screen_image, monitor, (wx, wy, ww, wh), margin=0)
                if crop is not None and crop.width >= 500 and crop.height >= 350:
                    try:
                        (lx, ly, lw, lh), confidence = detect_puzzle_area(crop)
                        candidate = (origin[0]+lx, origin[1]+ly, lw, lh)
                        region, _ = self._crop_rect_from_desktop(screen_image, monitor, candidate, margin=0)
                        if region is not None and _board_image_is_plausible(region):
                            post_action_result(self,action_epoch, lambda r=candidate, c=confidence: self._on_auto_area_detected(r, c, None, then_calculate))
                            return
                    except Exception:
                        pass
            # 5) Last resort: full virtual desktop search.
            rect, confidence = detect_puzzle_area(screen_image)
            left, top, width, height = rect
            rect = (left + monitor_origin[0], top + monitor_origin[1], width, height)
            region, _ = self._crop_rect_from_desktop(screen_image, monitor, rect, margin=0)
            if region is not None and not _board_image_is_plausible(region):
                raise RuntimeError("탐지 후보가 6×5 퍼즐판처럼 보이지 않습니다")
            post_action_result(self,action_epoch, lambda: self._on_auto_area_detected(rect, confidence, None, then_calculate))
        except Exception as exc:
            message = str(exc)
            post_action_result(self,action_epoch, lambda msg=message: self._on_auto_area_detected(None, 0.0, msg, then_calculate))

    def _on_auto_area_detected(self, rect, confidence, error, then_calculate):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self.detecting_area = False
        if error or not rect:
            self.set_status(f"자동 탐지 실패: {error or '퍼즐판 영역을 찾지 못했습니다.'}")
            messagebox.showwarning("퍼즐판 자동 탐지 실패",
                                   "뮤뮤 창과 저장 좌표를 먼저 확인했지만 퍼즐판을 확실하게 찾지 못했습니다.\n"
                                   "게임의 퍼즐판이 모두 보이도록 한 뒤 다시 시도하거나 '직접 지정'을 사용해 주세요.",
                                   parent=self.root)
            return
        self.board_rect = tuple(int(v) for v in rect)
        self._remember_board_anchor(self.board_rect)
        self.overlay.update_hud(anchor_rect=self.board_rect)
        self.last_path = None
        self._last_solved_signature = None
        self._last_solved_spike_signature = None
        self.last_solved_goals = None
        self.last_solved_avoid_skull = None
        try:
            self.capture_and_classify(show_preview=True)
        except Exception as exc:
            self.set_status(f"영역은 찾았지만 색상 판독에 실패했어요: {exc}")
            return
        x, y, w, h = self.board_rect
        self.set_status(f"퍼즐판 확인 완료 ({confidence:.0%}) · {w}×{h}. F6으로 경로 계산")
        if then_calculate:
            self.calculate_clicked()
    def _restore_main_after_board_select(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def select_area(self):
        self.overlay.hide()
        self.set_status("게임의 퍼즐판 격자 전체를 드래그로 지정하세요.")
        try:
            self._board_selector=BoardSelector(self)
            self.root.iconify()
        except Exception:
            self._restore_main_after_board_select()
            raise

    def on_area_selected(self):
        self._restore_main_after_board_select()
        self._remember_board_anchor(self.board_rect)
        self.overlay.update_hud(anchor_rect=self.board_rect)
        self.overlay.hide()
        self.set_status(f"영역 저장됨: x={self.board_rect[0]}, y={self.board_rect[1]}, 폭={self.board_rect[2]}, 높이={self.board_rect[3]}. F6으로 계산하세요.")
        self.capture_and_classify(show_preview=True)

    def grab_board(self) -> Image.Image:
        if not self.board_rect:
            raise RuntimeError("퍼즐판 영역을 먼저 자동 탐지하거나 직접 지정하세요.")
        # Hide only the route strokes for the screenshot. The full-screen
        # window stays mapped, so Windows does not flash/unmap it every tick.
        restore_route = bool(
            self.overlay.visible and self.last_path and self.overlay.route_drawings_visible
        )
        restore_hud = bool(self.overlay.hud_visible)
        left, top, width, height = self.board_rect
        toolbar_was_visible = self.toolbar.is_visible() if hasattr(self, "toolbar") else False
        try:
            if toolbar_was_visible: self.toolbar.hide_for_capture()
            if restore_route:
                self.overlay.set_route_drawings_visible(False)
            if restore_hud:
                self.overlay.set_hud_visible(False)
            self.root.update_idletasks()
            with mss.mss() as sct:
                shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                img = Image.frombytes("RGB", shot.size, shot.rgb)
        finally:
            if toolbar_was_visible: self.toolbar.restore_after_capture()
            if restore_route and self.overlay.visible:
                # Restore the exact existing canvas items; don't redraw/re-solve
                # the path unless the board signature actually changes.
                self.overlay.set_route_drawings_visible(True)
            if restore_hud:
                self.overlay.set_hud_visible(True)
        return img

    def on_history_selected(self, _event=None):
        if getattr(self, "history_listbox", None) is None:
            return
        selection = self.history_listbox.curselection()
        if not selection:
            return
        rev_index = selection[0]
        idx = len(self.board_history) - 1 - rev_index
        if idx < 0 or idx >= len(self.board_history):
            return
        rec = self.board_history[idx]
        board = rec.get("board", [])
        spikes = list(rec.get("spikes", [False] * len(board)))
        rows = []
        for r in range(ROWS):
            row_tokens = []
            for idx in range(r*COLS, min((r+1)*COLS, len(board))):
                label = COLOR_INFO.get(board[idx], ("?", "#ccc", 0))[0]
                row_tokens.append(label + ("*" if idx < len(spikes) and spikes[idx] else ""))
            rows.append(" ".join(row_tokens))
        combo = rec.get("combos")
        tail = f" · {combo}콤보" if combo is not None else ""
        spike_tail = f" · 가시 {sum(bool(x) for x in spikes)}개"
        self.history_info_var.set(f"{rec.get('time','--:--:--')}{tail}{spike_tail}\n" + "\n".join(rows))

    def _sync_crystal_monster_links(self):
        if not getattr(self,'data_paths',{}).get('database'):return
        with self._crystal_db_lock:
            data=_load_json_dict(self.data_paths['database'])
            if not isinstance(data.get('crystals'),list):return
            linked=link_crystal_monsters(data,getattr(self,'user_settings',{}))
            if linked!=data:_atomic_json_write(self.data_paths['database'],linked)

    def _load_crystal_db(self):
        data = _load_json_dict(self.data_paths["database"])
        if not isinstance(data.get("crystals"), list):
            data["crystals"] = []
        stocks = _load_json_dict(self.data_paths['ledger']).get('stocks', {})
        for row in data['crystals']:
            if str(row.get('id')) in stocks:
                row['manual_owned'] = int(stocks[str(row['id'])])
        data.setdefault("schema_version", 1)
        return data

    def _load_crystal_ledger(self):
        data = _load_json_dict(self.data_paths["ledger"])
        if not isinstance(data.get("counts"), dict):
            data["counts"] = {}
        if not isinstance(data.get("events"), list):
            data["events"] = []
        data.setdefault("schema_version", 1)
        return data

    def _normalise_crystal_crop(self, crop):
        # Amount badges (×0/×2) vary between cost and reward screens. Compare the
        # upper 80% of the card: frame colour and illustration remain, digits are excluded.
        crop = crop.convert("RGB")
        # Find the actual card bounds inside the user-selected rectangle. This
        # reduces duplicate IDs when one scan includes more surrounding margin.
        arr = np.asarray(crop, dtype=np.uint8)
        rgb = arr.astype(np.int16)
        hi = rgb.max(axis=2); lo = rgb.min(axis=2)
        saturated = (hi - lo > 35) & (hi > 70)
        # Ignore red count digits; prefer purple frame and illustration pixels.
        purple = (rgb[:, :, 2] > rgb[:, :, 1] + 12) & (rgb[:, :, 0] > rgb[:, :, 1] + 8)
        mask = saturated & purple
        ys, xs = np.where(mask)
        if len(xs) > 50:
            x0, x1 = int(np.percentile(xs, 2)), int(np.percentile(xs, 98)) + 1
            y0, y1 = int(np.percentile(ys, 2)), int(np.percentile(ys, 98)) + 1
            if x1 - x0 > 20 and y1 - y0 > 30:
                crop = crop.crop((max(0, x0-2), max(0, y0-2), min(crop.width, x1+2), min(crop.height, y1+2)))
        upper_h = max(1, int(crop.height * 0.76))
        crop = crop.crop((0, 0, crop.width, upper_h))
        # Normalize to a fixed canvas with padding instead of stretching, preserving icon shape.
        fitted = fit_image_preserve_aspect(crop, (40, 54), bg=(245, 245, 245))
        return np.asarray(fitted, dtype=np.int16)

    def _find_or_register_crystal(self, crop: Image.Image):
        """Match a seen crystal card to persistent image templates; unknowns get stable IDs."""
        with self._crystal_db_lock:
            db = self._load_crystal_db()
            incoming = self._normalise_crystal_crop(crop)
            best_id, best_score = None, 9999.0
            for entry in db["crystals"]:
                filename = entry.get("image_file")
                if not filename:
                    continue
                path = self.data_paths["icons_dir"] / filename
                if not path.exists():
                    continue
                try:
                    saved = self._normalise_crystal_crop(Image.open(path).convert("RGB"))
                    score = float(np.abs(incoming - saved).mean())
                    if score < best_score:
                        best_id, best_score = entry.get("id"), score
                except Exception:
                    continue
            # Conservative threshold avoids merging visually similar variants.
            if best_id is not None and best_score <= 17.5:
                return str(best_id), False, best_score
            # A second pass compares only the central artwork, avoiding top/bottom
            # badges and different padding in manually selected rectangles.
            # Require a strong agreement to avoid merging distinct crystals.
            try:
                import cv2
                def center_signature(im):
                    arr = self._normalise_crystal_crop(im).astype(np.uint8)
                    h, w = arr.shape[:2]
                    patch = arr[int(h*.22):int(h*.70), int(w*.16):int(w*.84)]
                    gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
                    return cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
                incoming_center = center_signature(crop)
                for entry in db["crystals"]:
                    path = self.data_paths["icons_dir"] / str(entry.get("image_file") or "")
                    if not path.is_file():
                        continue
                    with Image.open(path) as saved_image:
                        other = center_signature(saved_image.convert("RGB"))
                    center_error = float(np.abs(incoming_center.astype(np.int16) - other.astype(np.int16)).mean())
                    if center_error <= 8.0:
                        return str(entry["id"]), False, center_error
            except Exception:
                pass
            # v75: size- and margin-tolerant secondary match. The automatic F7 crop
            # can include more padding than the manual selection even for one card.
            # Test multiple inner artwork windows, but require a unique confident
            # match; never merge different crystal IDs on ambiguous evidence.
            try:
                import cv2
                def artwork_signatures(im):
                    a = np.asarray(im.convert("RGB"), dtype=np.uint8)
                    h, w = a.shape[:2]
                    patches = []
                    for x0,y0,x1,y1 in ((.17,.17,.83,.68),(.23,.22,.77,.62),(.12,.10,.88,.76)):
                        tile=a[int(h*y0):max(int(h*y0)+1,int(h*y1)), int(w*x0):max(int(w*x0)+1,int(w*x1))]
                        if min(tile.shape[:2]) < 6: continue
                        tile=cv2.resize(tile,(40,40),interpolation=cv2.INTER_AREA)
                        tile=cv2.cvtColor(tile,cv2.COLOR_RGB2LAB)
                        patches.append(tile.astype(np.int16))
                    return patches
                probe=artwork_signatures(crop)
                rankings=[]
                for entry in db["crystals"]:
                    path=self.data_paths["icons_dir"] / str(entry.get("image_file") or "")
                    if not path.is_file(): continue
                    with Image.open(path) as old:
                        refs=artwork_signatures(old)
                    if not probe or not refs: continue
                    score=min(float(np.abs(a-b).mean()) for a,b in zip(probe,refs))
                    rankings.append((score,str(entry["id"])))
                rankings.sort()
                # Ambiguity guard prevents accidental attribution to an unrelated card.
                if rankings and rankings[0][0] < 11.0 and (len(rankings)==1 or rankings[1][0]-rankings[0][0] > 3.5):
                    self._v66_log(f"v76 artwork crop match {rankings[0][1]} score={rankings[0][0]:.2f}")
                    return rankings[0][1], False, rankings[0][0]
                if rankings:
                    self._v66_log(f"v76 artwork crop ambiguous best={rankings[:2]}")
            except Exception as exc:
                self._v66_log(f"v76 artwork crop comparison unavailable: {exc}")
            numeric_ids = []
            for entry in db["crystals"]:
                match = re.search(r"(\d+)$", str(entry.get("id", "")))
                if match:
                    numeric_ids.append(int(match.group(1)))
            next_id = max(numeric_ids, default=0) + 1
            crystal_id = f"C{next_id:04d}"
            filename = f"{crystal_id}.png"
            crop.save(self.data_paths["icons_dir"] / filename, format="PNG")
            db["crystals"].append({
                "id": crystal_id,
                "label": f"크리스탈 {next_id:03d}",
                "image_file": filename,
                "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                "match_threshold": 17.5,
            })
            _atomic_json_write(self.data_paths["database"], db)
            return crystal_id, True, None

    def activate_crystal_target(self, crystal_id, required, current, card_name=None):
        """Arm one target crystal; reward monitoring only counts its matching icon."""
        crystal_id = str(crystal_id)
        required, current = int(required), int(current)
        if card_name is not None:
            resolved_card_name = str(card_name or "")
        else:
            resolved_card_name = str(self.card_name_detected or self.user_settings.get("last_card_name", "") or "")
        if resolved_card_name:
            self.card_name_detected = resolved_card_name
        goal_amount = max(0, required - current)
        db = self._load_crystal_db()
        entry = next((row for row in db.get("crystals", []) if str(row.get("id")) == crystal_id), None)
        label = entry.get("label", crystal_id) if entry else crystal_id
        if entry:
            try:
                required = max(0, int(entry.get('manual_required', required)))
                current = max(0, int(entry.get('manual_owned', current)))
            except (ValueError, TypeError):
                pass
        goal_amount = max(0, required - current)
        active = {
            "id": crystal_id, "label": str(label), "card_name": resolved_card_name,
            "required": required, "owned_before": current,
            "goal_amount": goal_amount, "collected": 0, "complete": goal_amount <= 0,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._crystal_db_lock:
            ledger = self._load_crystal_ledger()
            ledger["active_target"] = active
            _atomic_json_write(self.data_paths["ledger"], ledger)
        self.active_target = active
        self.resource_crystal_id = crystal_id
        self.resource_values["crystal_required"] = required
        self.resource_values["crystal_current"] = current
        self.user_settings["target_crystal_id"] = crystal_id
        self.save_user_settings()
        self.overlay.update_hud(resources=self.format_resource_hud())
        if hasattr(self, "toolbar"):
            self.toolbar.refresh()
        if goal_amount <= 0:
            self.target_last_message = f"{label}: 이미 필요량 충족"
        else:
            self.target_last_message = f"목표 {label}: {goal_amount}개 더 필요"
        return active

    def _target_crystal_template(self):
        target = self.active_target or {}
        target_id = str(target.get("id") or self.resource_crystal_id or "")
        if not target_id:
            return None, None
        db = self._load_crystal_db()
        entry = next((row for row in db.get("crystals", []) if str(row.get("id")) == target_id), None)
        if not entry:
            return None, None
        try:
            path = self.data_paths["icons_dir"] / str(entry.get("image_file", ""))
            if not path.exists():
                return None, entry
            return Image.open(path).convert("RGB"), entry
        except Exception:
            return None, entry

    def _match_active_target_items(self, items):
        template, entry = self._target_crystal_template()
        if template is None:
            return [], []
        target_signature = self._normalise_crystal_crop(template)
        matched_items, scores = [], []
        for item in items:
            try:
                sample = self._normalise_crystal_crop(item["crop"])
                distance = float(np.abs(sample - target_signature).mean())
                # Alternative interior comparison is less sensitive to frames changing red/white.
                # Require both intensity and silhouette consistency; do not auto-register.
                middle_a = sample[11:42, 8:32].astype(np.float32)
                middle_b = target_signature[11:42, 8:32].astype(np.float32)
                gray_a = middle_a.mean(axis=2)
                gray_b = middle_b.mean(axis=2)
                center_error = float(np.abs(gray_a - gray_b).mean())
                yy, xx = np.mgrid[:sample.shape[0], :sample.shape[1]]
                frame = ((xx <= 5) | (xx >= sample.shape[1]-6)) & (yy >= 10) & (yy < sample.shape[0]-17)
                frame_error = float(np.abs(sample[frame] - target_signature[frame]).mean())
                # Same portrait but a white limit-break border may be accepted
                # only with extraordinarily close artwork, never on hue alone.
                trustworthy = (distance <= 19.0 or
                               (center_error <= 12.0 and distance <= 46.0 and frame_error <= 35.0) or
                               (center_error <= 6.0 and distance <= 55.0))
                if not trustworthy:
                    # F7 icon art can be vertically aligned differently from reward
                    # cards. Check illustration inside the border at a few shifts.
                    # Never compare the orange quantity badge or frame alone.
                    try:
                        import cv2
                        ref=np.asarray(template.convert('RGB'))
                        obs=np.asarray(item['crop'].convert('RGB'))
                        best=100.0
                        for a_top,a_bottom in ((.17,.81),(.21,.77),(.24,.74)):
                            ar=ref[round(ref.shape[0]*a_top):round(ref.shape[0]*a_bottom), round(ref.shape[1]*.16):round(ref.shape[1]*.84)]
                            for b_top,b_bottom in ((.17,.81),(.21,.77),(.24,.74)):
                                br=obs[round(obs.shape[0]*b_top):round(obs.shape[0]*b_bottom), round(obs.shape[1]*.16):round(obs.shape[1]*.84)]
                                if min(*ar.shape[:2], *br.shape[:2])<10: continue
                                ar2=cv2.resize(ar,(48,48),interpolation=cv2.INTER_AREA)
                                br2=cv2.resize(br,(48,48),interpolation=cv2.INTER_AREA)
                                diff=cv2.absdiff(cv2.cvtColor(ar2,cv2.COLOR_RGB2LAB), cv2.cvtColor(br2,cv2.COLOR_RGB2LAB)).astype(np.float32)
                                best=min(best,float(diff.mean()))
                        trustworthy = best < 12.0
                    except Exception:
                        pass
                if trustworthy:
                    matched_items.append(item)
                    scores.append(round(distance, 2))
            except Exception:
                continue
        return matched_items, scores

    def _match_partially_visible_target_items(self, items):
        """Compare artwork only where a top-clipped reward card is actually shown.

        Require agreement across multiple regions and reject weak matches. This
        conservative matcher deliberately prefers a missed reward to false credit.
        """
        template, _ = self._target_crystal_template()
        if template is None:
            return [], []
        try:
            import cv2
            ref=np.asarray(template.convert('RGB'))
            matches=[]; scores=[]
            for item in items:
                ratio=float(item.get('partial_top_ratio',0))
                if not (.05 <= ratio <= .65):
                    continue
                raw=np.asarray(item['crop'].convert('RGB'))
                h,w=raw.shape[:2]
                total_h=round(h/(1-ratio))
                if total_h < 70 or w < 65:
                    continue
                # Compare only visible upper-to-middle artwork, avoiding the
                # local ×N at bottom and border overlay as far as possible.
                norm=cv2.resize(ref,(w,total_h),interpolation=cv2.INTER_AREA)
                skip=round(ratio*total_h)
                ystart=max(skip, round(total_h*.12))
                yend=min(total_h-round(total_h*.18), total_h-10)
                if yend-ystart < 23:
                    continue
                obs=raw[ystart-skip:yend-skip]
                ref_part=norm[ystart:yend]
                # Artwork colors may shift slightly; LAB distance is robust to
                # brightness but still rejects other card illustrations.
                obs_lab=cv2.cvtColor(obs,cv2.COLOR_RGB2LAB).astype(np.float32)
                ref_lab=cv2.cvtColor(ref_part,cv2.COLOR_RGB2LAB).astype(np.float32)
                center=(slice(None),slice(round(w*.18),round(w*.82)))
                diff=np.abs(obs_lab[center]-ref_lab[center])
                score=float(diff.mean())
                # With only a partial portrait, be stricter than full-card match.
                if score <= 15.0:
                    matches.append(item); scores.append(round(score,2))
            return matches,scores
        except Exception:
            return [],[]

    def _append_target_reward_event(self, items):
        """Count only the active target crystal; never auto-register other rewards."""
        active = dict(self.active_target or {})
        target_id = str(active.get("id") or self.resource_crystal_id or "")
        if not target_id:
            return 0, 0, "목표 크리스탈 미지정 · 부족한 재료 화면에서 F7로 지정하세요."
        if active.get("complete") or int(active.get("goal_amount", 0) or 0) <= 0:
            return 0, 0, f"목표 크리스탈 {active.get('label', target_id)}는 이미 목표량을 채웠습니다."
        full_items=[i for i in items if not i.get('partial_top_ratio')]
        partial_items=[i for i in items if i.get('partial_top_ratio')]
        matched_items, scores = self._match_active_target_items(full_items)
        partial_matches, partial_scores = self._match_partially_visible_target_items(partial_items)
        matched_items += partial_matches
        scores += partial_scores
        if not matched_items:
            return 0, 0, f"획득창 감지 · 목표 크리스탈 {active.get('label', target_id)}와 일치하는 아이콘이 안 보여 집계하지 않았습니다."
        total = sum(int(item["amount"]) for item in matched_items)
        with self._crystal_db_lock:
            ledger = self._load_crystal_ledger()
            counts = ledger["counts"]
            saved_active = ledger.get("active_target") if isinstance(ledger.get("active_target"), dict) else active
            # If the active target changed while scanning, do not alter any count.
            if str(saved_active.get("id", "")) != target_id:
                return 0, 0, "목표 크리스탈이 바뀌어 이번 결과는 집계하지 않았습니다."
            self._v66_log(f"v78 reward database commit: target={target_id}, old={counts.get(target_id, 0)}, +{total}")
            counts[target_id] = int(counts.get(target_id, 0)) + total
            db = self._load_crystal_db()
            manual_entry = next((r for r in db['crystals'] if str(r.get('id')) == target_id), None)
            if manual_entry is not None and manual_entry.get('manual_owned') is not None:
                manual_entry['manual_owned'] = int(manual_entry['manual_owned']) + total
                _atomic_json_write(self.data_paths['database'], db)
            saved_active["collected"] = int(saved_active.get("collected", 0) or 0) + total
            goal = int(saved_active.get("goal_amount", 0) or 0)
            saved_active["complete"] = saved_active["collected"] >= goal
            ledger["active_target"] = saved_active
            ledger["events"].append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "target_id": target_id,
                "items": [{"id": target_id, "amount": int(item["amount"]), "match_score": score}
                          for item, score in zip(matched_items, scores)],
            })
            ledger["events"] = ledger["events"][-300:]
            _atomic_json_write(self.data_paths["ledger"], ledger)
        self.active_target = saved_active
        self.refresh_crystal_ledger()
        progress = int(saved_active["collected"]); goal = int(saved_active["goal_amount"])
        if saved_active["complete"]:
            return len(matched_items), total, f"목표 달성: {saved_active.get('label', target_id)} {progress}/{goal}개 · 이후 결과는 이 목표에 추가하지 않습니다."
        return len(matched_items), total, f"목표 크리스탈 집계: {saved_active.get('label', target_id)} {progress}/{goal}개"

    def edit_selected_crystal_quantities(self, from_candidates=False):
        """Edit one crystal's stock and requirement; all live views share it."""
        parent = getattr(self, 'crystal_ledger_window', self.root)
        if from_candidates:
            tree = getattr(self.toolbar, 'candidate_tree', None)
            parent = getattr(self.toolbar, 'crystal_window', self.root)
            if tree is None or not tree.selection():
                messagebox.showinfo('선택 필요', '수정할 부족 재료를 먼저 선택해 주세요.', parent=parent)
                return
            try:
                cid = str(self.crystal_candidates[int(tree.selection()[0])]['id'])
            except (ValueError, IndexError, KeyError):
                return
        else:
            tree = getattr(self, 'crystal_ledger_tree', None)
            if tree is None or not tree.selection():
                messagebox.showinfo('선택 필요', '수정할 DB 항목을 먼저 선택해 주세요.', parent=parent)
                return
            cid = str(tree.selection()[0])
        with self._crystal_db_lock:
            db = self._load_crystal_db()
            entry = next((r for r in db['crystals'] if str(r.get('id')) == cid), None)
            if entry is None:
                return
            active = self.active_target or {}
            candidate = next((r for r in self.crystal_candidates if str(r.get('id')) == cid), {})
            need = candidate.get('required', entry.get('manual_required', active.get('required', 0)))
            owned = entry.get('manual_owned', candidate.get('current',
                (int(active.get('owned_before',0))+int(active.get('collected',0)) if str(active.get('id'))==cid else 0)))
            ledger = self._load_crystal_ledger()
            groups = ledger.get('farming_groups', {})
            active_key = ledger.get('active_farming_group')
            containing = [key for key,group in groups.items() if any(r['id'] == cid for r in group['materials'])]
            quantity_group_key = active_key if active_key in containing else next(iter(containing), None)
            if quantity_group_key and not (from_candidates and getattr(self, '_pending_farming_materials', None)):
                material = next(r for r in groups[quantity_group_key]['materials'] if r['id'] == cid)
                need, owned = material['required'], ledger['stocks'][cid]
        name = str(entry.get('label') or cid)
        dialog = tk.Toplevel(parent)
        dialog.title(f"{name} 수량 수정")
        dialog.transient(parent)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"{name} · 변경할 항목만 수정하세요.").grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,10))
        required_var=tk.StringVar(value=str(int(need)))
        owned_var=tk.StringVar(value=str(int(owned)))
        ttk.Label(body,text="필요 수량").grid(row=1,column=0,sticky="w",padx=(0,12),pady=5)
        ttk.Entry(body,textvariable=required_var,width=16).grid(row=1,column=1,pady=5)
        ttk.Label(body,text="보유 수량").grid(row=2,column=0,sticky="w",padx=(0,12),pady=5)
        entry_owned=ttk.Entry(body,textvariable=owned_var,width=16)
        entry_owned.grid(row=2,column=1,pady=5)
        result={}
        def accept():
            try:
                n,o=int(required_var.get()),int(owned_var.get())
                if not (0<=n<=999999 and 0<=o<=999999): raise ValueError()
            except ValueError:
                messagebox.showwarning("입력 확인","0~999999의 숫자를 입력해 주세요.",parent=dialog)
                return
            result['values']=(n,o)
            dialog.destroy()
        actions=ttk.Frame(body)
        actions.grid(row=3,column=0,columnspan=2,sticky="e",pady=(12,0))
        ttk.Button(actions,text="취소",command=dialog.destroy).pack(side="right",padx=(6,0))
        ttk.Button(actions,text="저장",command=accept).pack(side="right")
        dialog.bind("<Return>",lambda e:accept())
        dialog.bind("<Escape>",lambda e:dialog.destroy())
        dialog.grab_set()
        entry_owned.focus_set()
        parent.wait_window(dialog)
        if 'values' not in result:return
        new_need,new_owned=result['values']
        changed_owned=(new_owned!=int(owned))
        need,owned=new_need,new_owned
        if from_candidates and getattr(self, '_pending_farming_materials', None):
            for row in self._pending_farming_materials:
                if row['id'] == cid:
                    row.update(required=need, current=owned, deficit=max(0,need-owned))
            self.toolbar.refresh()
            self.set_status('수량 수정 완료 · 돌파 이름을 지정하면 전체 재료를 저장합니다.')
            return
        with self._crystal_db_lock:
            ledger = self._load_crystal_ledger()
            key = quantity_group_key
            group = ledger.get('farming_groups', {}).get(key, {})
            matching = next((row for row in group.get('materials', []) if row['id'] == cid), None)
            if matching is not None:
                ledger.setdefault('stocks', {})[cid] = owned
                matching['required'] = need
                group['revision'] = uuid.uuid4().hex
                _atomic_json_write(self.data_paths['ledger'], ledger)
        if matching is not None:
            self._farming_sync_views(ledger)
            self.set_status(f'{name}: 필요 {need} · 보유 {owned} · 부족 {max(0,need-owned)} (수정 저장)')
            return
        # Only a correction to the most recent matching F7 scan may teach the
        # decoder; arbitrary stock changes are not evidence of OCR mistakes.
        if changed_owned:
            candidate_scan=next((r for r in self.crystal_candidates if str(r.get('id'))==cid),None)
            if candidate_scan is not None and candidate_scan.get('owned_digit_bits'):
                scan_age=time.time()-float(candidate_scan.get('scan_time') or 0)
                scan_value=int(candidate_scan.get('current',-1))
                if 0<=scan_age<=180 and scan_value==int(entry.get('manual_owned',scan_value)):
                    path=self.data_paths['database_dir']/"verified_stock_digits.json"
                    learned=_load_json_dict(path)
                    samples=learned.setdefault('samples',[])
                    bits=candidate_scan['owned_digit_bits']
                    if not any(r.get('bits')==bits and r.get('value')==owned for r in samples):
                        samples.append({'bits':bits,'value':owned,'time':time.strftime('%Y-%m-%d %H:%M:%S')})
                        learned['samples']=samples[-300:]
                        _atomic_json_write(path,learned)
                        self._v66_log(f"v95 verified owned count learned: id={cid} from={scan_value} to={owned}")
        with self._crystal_db_lock:
            db = self._load_crystal_db()
            entry = next((r for r in db['crystals'] if str(r.get('id')) == cid), None)
            if entry is None:
                return
            entry['manual_required'], entry['manual_owned'] = need, owned
            _atomic_json_write(self.data_paths['database'], db)
            if str((self.active_target or {}).get('id')) == cid:
                ledger = self._load_crystal_ledger()
                saved = ledger.get('active_target') or dict(self.active_target)
                saved.update(required=need, owned_before=owned, goal_amount=max(0,need-owned),
                             collected=0, complete=owned>=need)
                ledger['active_target'] = saved
                _atomic_json_write(self.data_paths['ledger'], ledger)
                self.active_target = saved
                self.resource_values['crystal_required'] = need
                self.resource_values['crystal_current'] = owned
        for candidate in self.crystal_candidates:
            if str(candidate.get('id')) == cid:
                candidate.update(required=need, current=owned, deficit=max(0,need-owned))
        self.refresh_crystal_ledger()
        self.overlay.update_hud(resources=self.format_resource_hud())
        if hasattr(self, 'toolbar'):
            self.toolbar.refresh()
        self.set_status(f'{name}: 필요 {need} · 보유 {owned} · 부족 {max(0,need-owned)} (수동 수정 저장)')

    def show_crystal_ledger(self):
        for attr in ("crystal_ledger_window",):
            existing = getattr(self, attr, None)
            try:
                if existing is not None and existing.winfo_exists():
                    existing.deiconify(); existing.lift(); existing.focus_force(); self.refresh_crystal_ledger(); return
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        win.title("퍼드0 크리스탈 획득 기록 / DB 관리")
        win.geometry("980x480")
        win.minsize(700, 340)
        self.crystal_ledger_window = win
        frame = ttk.Frame(win, padding=10); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="목표로 지정한 크리스탈만 집계합니다. 이름 변경 후에도 아이콘 데이터는 사용자 데이터 폴더에 유지됩니다.",
                  wraplength=620).pack(anchor="w", pady=(0, 8))
        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        table_frame = ttk.Frame(frame); table_frame.pack(fill="both", expand=True)
        columns = ("label", "required", "owned", "short", "count", "monster")
        ttk.Style(win).configure("CrystalLedger.Treeview", rowheight=54)
        self.crystal_ledger_tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", height=5,
                                                style="CrystalLedger.Treeview")
        self.crystal_ledger_tree.heading("#0", text="아이콘")
        self.crystal_ledger_tree.column("#0", width=64, stretch=False, anchor="center")
        self._crystal_photo_refs = []
        for key, title, width in (("label", "크리스탈 이름", 330), ("required", "필요", 78),
                                  ("owned", "보유", 78), ("short", "부족", 78),
                                  ("count", "자동 획득 누계", 105), ("monster", "몬스터 번호", 128)):
            self.crystal_ledger_tree.heading(key, text=title)
            self.crystal_ledger_tree.column(key, width=width, stretch=(key == 'label'),
                                            anchor=('w' if key == 'label' else 'center'), minwidth=width)
        vbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.crystal_ledger_tree.yview)
        self.crystal_ledger_tree.configure(yscrollcommand=vbar.set)
        self.crystal_ledger_tree.pack(side="left", fill="both", expand=True); vbar.pack(side="right", fill="y")
        # Long names remain complete in the model; horizontal scrolling avoids
        # forcing the compact DB window wider than the display.
        hbar = ttk.Scrollbar(frame, orient='horizontal', command=self.crystal_ledger_tree.xview)
        self.crystal_ledger_tree.configure(xscrollcommand=hbar.set)
        hbar.pack(side='bottom', fill='x')
        # Context menu on a DB row: rename or delete without hunting for buttons.
        self.crystal_db_context_menu = tk.Menu(win, tearoff=0)
        self.crystal_db_context_menu.add_command(label="필요/보유 수정", command=self.edit_selected_crystal_quantities)
        self.crystal_db_context_menu.add_command(label="이름 변경", command=self.rename_selected_crystal)
        self.crystal_db_context_menu.add_command(label="삭제", command=self.delete_selected_crystal)
        def _db_right_click(event):
            row_id = self.crystal_ledger_tree.identify_row(event.y)
            if not row_id:
                return
            self.crystal_ledger_tree.selection_set(row_id)
            self.crystal_ledger_tree.focus(row_id)
            try:
                self.crystal_db_context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.crystal_db_context_menu.grab_release()
        self.crystal_ledger_tree.bind("<Button-3>", _db_right_click)
        # Delete only affects the selected DB row, with the existing confirmation dialog.
        self.crystal_ledger_tree.bind("<Delete>", lambda event: self.delete_selected_crystal())
        # Keep destructive and navigation actions visible at the bottom of the DB window.
        ttk.Button(buttons, text="필요/보유 수정", command=self.edit_selected_crystal_quantities).pack(side="left", padx=(0, 4))
        ttk.Button(buttons, text="이름 변경", command=self.rename_selected_crystal).pack(side="left", padx=(0, 4))
        ttk.Button(buttons, text="선택 삭제", command=self.delete_selected_crystal).pack(side="left", padx=4)
        ttk.Button(buttons, text="크리스탈 창", command=self.show_crystal_window_from_db).pack(side="left", padx=4)
        ttk.Button(buttons, text="이미지 폴더", command=self.open_crystal_icons_folder).pack(side="left", padx=4)
        ttk.Button(buttons, text="새로고침", command=self.refresh_crystal_ledger).pack(side="left", padx=4)
        ttk.Button(buttons, text="닫기", command=win.withdraw).pack(side="right")
        self.refresh_crystal_ledger()

    def refresh_crystal_ledger(self):
        tree = getattr(self, "crystal_ledger_tree", None)
        if tree is None:
            return
        try:
            for item in tree.get_children(): tree.delete(item)
            self._sync_crystal_monster_links()
            db = self._load_crystal_db()
            ledger = self._load_crystal_ledger()
            counts = ledger.get("counts", {})
            self._crystal_photo_refs = []
            from PIL import ImageTk
            try:
                from tkinter import font as tkfont
                name_font = tkfont.nametofont('TkDefaultFont')
                longest = max([name_font.measure(str(x.get('label',''))) + 50
                               for x in db.get('crystals', [])] or [330])
                tree.column('label', width=max(330,min(1400,longest)))
            except Exception:
                pass
            for crystal in db.get("crystals", []):
                cid = str(crystal.get("id", ""))
                photo = None
                try:
                    path = self.data_paths["icons_dir"] / str(crystal.get("image_file", ""))
                    if path.exists():
                        thumb = Image.open(path).convert("RGB")
                        thumb.thumbnail((44, 48), Image.Resampling.LANCZOS)
                        photo = ImageTk.PhotoImage(thumb)
                        self._crystal_photo_refs.append(photo)
                except Exception:
                    photo = None
                active = self.active_target or {}
                candidate = next((r for r in self.crystal_candidates if str(r.get('id')) == cid), {})
                req = crystal.get('manual_required', candidate.get('required',
                    active.get('required') if str(active.get('id')) == cid else None))
                owned = crystal.get('manual_owned', candidate.get('current',
                    (int(active.get('owned_before',0)) + int(active.get('collected',0)))
                    if str(active.get('id')) == cid else None))
                shortage = max(0,int(req)-int(owned)) if req is not None and owned is not None else '—'
                insert_options = {'image': photo} if photo is not None else {}
                tree.insert('', 'end', iid=cid,
                            values=(crystal.get('label',cid), req if req is not None else '—',
                                    owned if owned is not None else '—', shortage, counts.get(cid,0),f"No.{crystal['monster_number']}" if crystal.get('monster_number') else '연결 확인 필요'),
                            **insert_options)
        except Exception as exc:
            self._v66_log(f'v79 DB table refresh failed: {type(exc).__name__}: {exc}')

    def delete_selected_crystal(self):
        tree = getattr(self, "crystal_ledger_tree", None)
        if tree is None or not tree.selection():
            messagebox.showinfo("선택 필요", "삭제할 크리스탈을 선택해 주세요.", parent=self.crystal_ledger_window)
            return
        cid = str(tree.selection()[0])
        if not messagebox.askyesno("크리스탈 삭제", f"{cid}의 이미지, 이름, 획득 기록을 삭제할까요?\n이 작업은 되돌릴 수 없습니다.",
                                   parent=self.crystal_ledger_window):
            return
        with self._crystal_db_lock:
            db = self._load_crystal_db()
            entry = next((r for r in db.get("crystals", []) if str(r.get("id")) == cid), None)
            if entry is None:
                return
            db["crystals"] = [r for r in db["crystals"] if str(r.get("id")) != cid]
            _atomic_json_write(self.data_paths["database"], db)
            ledger = self._load_crystal_ledger()
            ledger.get("counts", {}).pop(cid, None)
            ledger["events"] = [ev for ev in ledger.get("events", []) if str(ev.get("crystal_id", ev.get("id", ""))) != cid]
            _atomic_json_write(self.data_paths["ledger"], ledger)
            icon_file = str(entry.get("image_file", ""))
            if icon_file and Path(icon_file).name == icon_file:
                try:
                    (self.data_paths["icons_dir"] / icon_file).unlink(missing_ok=True)
                except OSError:
                    pass
        self.crystal_candidates = [r for r in self.crystal_candidates if str(r.get("id")) != cid]
        if str((self.active_target or {}).get("id")) == cid:
            self._clear_active_crystal_target()
        self.refresh_crystal_ledger()
        if hasattr(self, "toolbar"):
            self.toolbar.refresh()
        self.set_status(f"{cid} 삭제 완료")

    def rename_selected_crystal(self):
        tree = getattr(self, "crystal_ledger_tree", None)
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("선택 필요", "이름을 바꿀 크리스탈을 선택해 주세요.", parent=self.crystal_ledger_window)
            return
        cid = selection[0]
        db = self._load_crystal_db()
        entry = next((row for row in db.get("crystals", []) if str(row.get("id")) == cid), None)
        if not entry:
            return
        new_label = simpledialog.askstring("크리스탈 이름", "구분하기 쉬운 이름을 입력하세요.",
                                           initialvalue=entry.get("label", cid), parent=self.crystal_ledger_window)
        if new_label and new_label.strip():
            entry["label"] = new_label.strip()[:80]
            _atomic_json_write(self.data_paths["database"], db)
            if self.active_target and str(self.active_target.get("id")) == cid:
                self.active_target["label"] = entry["label"]
                self.active_target["card_name"] = entry["label"]
                self.card_name_detected = entry["label"]
                with self._crystal_db_lock:
                    ledger = self._load_crystal_ledger()
                    if isinstance(ledger.get("active_target"), dict) and str(ledger["active_target"].get("id")) == cid:
                        ledger["active_target"].update(label=entry["label"], card_name=entry["label"])
                        _atomic_json_write(self.data_paths["ledger"], ledger)
            for candidate in getattr(self, "crystal_candidates", []):
                if str(candidate.get("id")) == cid:
                    candidate["card_name"] = entry["label"]
            self.refresh_crystal_ledger()
            if getattr(self, "crystal_ledger_panel", None):
                try: self.crystal_ledger_panel.refresh()
                except Exception: pass
            self.overlay.update_hud(resources=self.format_resource_hud())
            if hasattr(self, "toolbar"): self.toolbar.refresh()

    def manual_reward_calculate(self):
        if getattr(self,'_background_running',False):
            self.set_status('백그라운드 파밍 중입니다. F10으로 중지한 뒤 수동 집계를 사용할 수 있습니다.');return
        """One-click user-initiated reward recognition from the *visible* MuMu screen.

        Does not depend on F6, watcher being armed, or overlay foreground status.
        Never guesses a clipped card's amount; user can scroll the game first.
        """
        if getattr(self, '_manual_reward_busy', False):
            self.set_status('보상 계산이 이미 진행 중입니다.')
            return
        if getattr(self, '_manual_reward_committed', False) or getattr(self, 'reward_processed_this_visit', False):
            self.set_status('이 전투 보상은 이미 합산했습니다. 다음 전투에서 F6 실행 후 다시 계산하세요.')
            return
        if not (self.active_target or {}).get('id'):
            self.set_status('파밍 목표가 없습니다. 먼저 F7에서 목표 크리스탈을 지정하세요.')
            return
        rect = self._reward_game_capture_rect()
        if rect is None:
            self.set_status('MuMu 게임 창 위치를 확인할 수 없습니다.')
            return
        self._manual_reward_busy = True
        self.set_status('현재 보상창에서 목표 카드와 수량 확인 중…')
        threading.Thread(target=self._manual_reward_scan_worker, args=(rect,), daemon=True).start()

    def _manual_reward_scan_worker(self, rect):
        action_epoch=getattr(self,'_stop_epoch',0)
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=action_epoch
        if getattr(self,'_all_actions_stopped',False) is True:return
        try:
            # Foreground may be the farming strip after pressing its button;
            # unlike the background watcher, a direct user click permits capture.
            with mss.mss() as sct:
                shot = sct.grab(rect)
                image = Image.frombytes('RGB', shot.size, shot.rgb)
            debug_path = self.data_paths['root'] / 'debug' / 'last_manual_reward.png'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(debug_path)
            if not self._farming_result_header_confirmed(image):raise ValueError('전투 결과 화면에서만 획득량을 집계합니다.')
            if not (self._looks_like_reward_frame(image) or self._looks_like_reward_header(image)):
                raise ValueError('크리스탈 획득 보상창이 확인되지 않습니다.')
            boxes = detect_reward_card_rects(image)
            if not boxes:
                raise ValueError('현재 보이는 보상 카드를 찾지 못했습니다.')
            tokens = self._run_ocr_tokens(image)
            # Local enlarged OCR improves the ×N label on each fully visible card.
            extra=[]
            for x0,y0,x1,y1 in boxes:
                if y1 > image.height*.83:
                    continue
                zone=image.crop((max(0,x0-6),max(0,y0),min(image.width,x1+6),min(image.height,y1+8)))
                scaled=zone.resize((zone.width*3,zone.height*3),Image.Resampling.LANCZOS)
                try:
                    for t in self._run_ocr_tokens(scaled):
                        extra.append(OCRToken(t.text,max(0,x0-6)+t.x1/3,y0+t.y1/3,
                                              max(0,x0-6)+t.x2/3,y0+t.y2/3,t.score))
                except Exception:
                    continue
            items=extract_visual_reward_items(image,tokens+extra,self._run_ocr_tokens)
            # Exclude cards whose quantity row isn't reliably visible, never infer from x2.
            items=[i for i in items if i['screen_box'][3] < image.height*.83]
            hits, scores=self._match_active_target_items(items)
            if items and not hits:
                self._v66_log(f'{APP_VERSION} manual reward: target match 0/{len(items)}. Verify target icon crop and card layout.')
            # Try OCR-anchored partial cards that the color-connected-component
            # detector cannot see when the list clips off their upper halves.
            partial=extract_partially_visible_reward_items(image,tokens+extra,boxes)
            partial_hits,partial_scores=self._match_partially_visible_target_items(partial)
            # Avoid counting the same physical card twice, even with mixed boxes.
            for candidate,score in zip(partial_hits,partial_scores):
                if not any(abs(candidate['x']-item['x'])<35 and
                           abs(candidate['screen_box'][3]-item['screen_box'][3])<35
                           for item in hits):
                    hits.append(candidate); scores.append(score)
            count=sum(int(i['amount']) for i in hits)
            clipped = bool(partial) or any(b[3]>=image.height*.75 for b in boxes)
            self._v66_log(f'{APP_VERSION} manual reward: boxes={len(boxes)} parsed={len(items)} partial={len(partial)} partial_hits={len(partial_hits)} target_hits={len(hits)} scores={scores} amounts={[i["amount"] for i in hits]} clipped={clipped}')
            post_action_result(self,action_epoch, lambda: self._finish_manual_reward_scan(items,hits,count,clipped,None))
        except Exception as exc:
            self._v66_log(f'{APP_VERSION} manual reward error: {type(exc).__name__}: {exc}')
            post_action_result(self,action_epoch, lambda error=str(exc): self._finish_manual_reward_scan([],[],0,False,error))

    def _finish_manual_reward_scan(self, items, hits, count, clipped, error):
        if getattr(self,'_root_tree_active',False) is True:return
        if getattr(self,'_all_actions_stopped',False) is True:return
        self._manual_reward_busy = False
        if error:
            self.set_status('수동 계산 실패: '+error)
            return
        if not hits or count<=0:
            text=('현재 화면의 목표 카드 수량을 읽지 못했습니다. '
                  '아래쪽 카드가 잘렸다면 게임 보상 목록을 내려 ×N이 보이게 한 뒤 계산을 다시 누르세요.'
                  if clipped else '현재 화면에서 수량이 확인된 목표 카드를 찾지 못했습니다.')
            self.set_status(text)
            return
        # Direct user action is consent; conservative card + quantity gates already ran.
        # No modal confirmation: apply immediately and refresh every inventory surface.
        label=str((self.active_target or {}).get('label') or '목표 크리스탈')
        try:
            kinds,total,message=self._append_target_reward_event(hits)
            if kinds>0 and total>0:
                self._manual_reward_committed=True
                self.reward_processed_this_visit=True
                self._disarm_reward_watch('manual reward credited')
                self.overlay.update_hud(resources=self.format_resource_hud())
                if hasattr(self,'toolbar'):
                    self.toolbar.refresh()
                    try: self.toolbar._refresh_farming_strip()
                    except Exception: pass
                try: self.refresh_crystal_ledger()
                except Exception: pass
                self._v66_log(f'{APP_VERSION} manual reward credited: count={total} target={label}')
            self.set_status('수동 계산 · '+message)
        except Exception as exc:
            self.set_status(f'수동 보상 DB 저장 오류: {exc}')

    def _reward_game_capture_rect(self):
        """Return only the emulator's visible screen rectangle, never the desktop.

        Reject ambiguous windows rather than OCR another app or credit false rewards.
        """
        point = None
        if self.board_rect:
            bx, by, bw, bh = (int(v) for v in self.board_rect)
            point = (bx+bw//2, by+bh//2)
        info = find_emulator_window_info(point=point)
        if not info:
            return None
        x, y, w, h = (int(v) for v in info['rect'])
        title = str(info.get('title', '')).lower()
        if not any(k in title for k in ('android device', 'puzzle dragons 0', 'mumu')):
            return None
        if w < 500 or h < 350:
            return None
        return {'left': x, 'top': y, 'width': w, 'height': h}

    def _emulator_is_foreground_for_reward(self):
        """mss captures *visible desktop pixels*, not pixels behind Chrome.

        Only inspect the game's content when its own host is the foreground
        window. GUI overlays belong to this app and never authorize captures.
        """
        if sys.platform != 'win32':
            return True
        try:
            user32 = ctypes.windll.user32
            info = self._reward_game_window_info()
            if not info:
                return False
            active = int(user32.GetForegroundWindow())
            return active == int(info['hwnd'])
        except Exception:
            return False

    def _reward_game_window_info(self):
        if not self.board_rect:
            return None
        bx, by, bw, bh = (int(v) for v in self.board_rect)
        info = find_emulator_window_info(point=(bx + bw // 2, by + bh // 2))
        if not info:
            return None
        name = str(info.get('title', '')).lower()
        return info if ('android device' in name or 'puzzle dragons 0' in name) else None

    def _disarm_reward_watch(self, reason):
        self._reward_monitor_armed.clear()
        self._v66_log('v79 reward watch ended: ' + str(reason))

    def _capture_reward_monitor_image(self):
        """Capture the game window only; worker-safe (does not query Tk widgets)."""
        if not self._emulator_is_foreground_for_reward():
            raise RuntimeError('MuMu 창이 전면에 없어 다른 프로그램 캡처를 건너뜁니다')
        rect = self._reward_game_capture_rect()
        if rect is None:
            raise RuntimeError('게임 창 영역을 찾지 못해 보상 OCR을 중단했습니다')
        with mss.mss() as sct:
            shot = sct.grab(rect)
            image = Image.frombytes('RGB', shot.size, shot.rgb)
        self._last_reward_monitor_rect = (rect['left'], rect['top'], rect['width'], rect['height'])
        return image

    @staticmethod
    def _looks_like_reward_header(image):
        arr = np.asarray(image.convert("RGB"))
        h, w, _ = arr.shape
        x0, x1 = int(w*0.20), int(w*0.82)
        y0, y1 = int(h*0.13), int(h*0.25)
        sample = arr[max(0,y0):min(h,y1), max(0,x0):min(w,x1)].astype(np.int16)
        if sample.size == 0:
            return False
        red, green, blue = sample[...,0], sample[...,1], sample[...,2]
        yellow = (red > 175) & (green > 105) & (blue < 135) & (red > green + 20)
        return float(yellow.mean()) >= 0.035

    @staticmethod
    def _looks_like_reward_frame(image):
        """Detect the large gold reward dialog, not the thin toolbar/7-combo bar.

        A long gold horizontal rim near the dialog's top AND one near its
        bottom are required. This is only a candidate gate; OCR/card matching
        must still confirm before changing any stored quantity.
        """
        try:
            import cv2
            arr = np.asarray(image.convert('RGB'))
            h,w = arr.shape[:2]
            if w < 500 or h < 350:
                return False
            hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
            # Gold/yellow frame on the 1600x900 result modal; generous size tolerance.
            mask = cv2.inRange(hsv, (10, 105, 125), (43, 255, 255)) > 0
            x0,x1=int(w*.24),int(w*.76)
            top=mask[int(h*.12):int(h*.27),x0:x1]
            bottom=mask[int(h*.68):int(h*.94),x0:x1]
            def wide_line(region):
                if not region.size: return False
                return bool(np.any(np.mean(region,axis=1) >= .54))
            return wide_line(top) and wide_line(bottom)
        except Exception:
            return False

    def _v67_poll_reward_watch_queue(self):
        """Tk-only queue consumer; no screenshot or image processing on the GUI."""
        try:
            for _ in range(4):
                try:
                    kind, payload = self._reward_monitor_queue.get_nowait()
                except __import__("queue").Empty:
                    break
                if kind == "reward":
                    if getattr(self,'_all_actions_stopped',False) is True or getattr(self,'_root_tree_active',False) is True:continue
                    if (self._auto_reward_enabled and self._reward_monitor_armed.is_set()
                            and not self.reward_poll_busy and not self.reward_processed_this_visit):
                        self.reward_poll_busy = True
                        self._reward_scan_attempts += 1
                        if not self._reward_header_first_seen:
                            self._reward_header_first_seen = time.monotonic()
                        self._v66_log(f'{APP_VERSION} reward OCR probe={self._reward_scan_attempts}; confirmation={self._reward_confirm_attempts}/3')
                        threading.Thread(target=self._reward_scan_worker, args=(payload,), daemon=True).start()
                elif kind == 'hide_button':
                    # Only the puzzle route button belongs to the reward watcher.
                    # The user's 12-o'clock farming HUD has an independent lifetime.
                    if hasattr(self, 'toolbar') and not self.toolbar._user_hidden:
                        try:
                            self.toolbar.win.withdraw()
                        except tk.TclError:
                            pass
                elif kind == 'deadline':
                    self.set_status('보상 감시 종료 · 미검출/시간 초과 (다음 F6에서 다시 시작)')
                elif kind == "rearmed":
                    self.reward_candidate_signature = None
                    self.reward_candidate_count = 0
                    self._reward_confirm_attempts = 0
                    self.reward_processed_this_visit = False
                elif kind == "error":
                    self._v66_log("v67 reward watch error: " + str(payload))
        finally:
            try:
                if self.root.winfo_exists():
                    self.root.after(250, self._v67_poll_reward_watch_queue)
            except tk.TclError:
                pass

    def _reward_title_confirmed(self, image):
        """OCR the tiny fixed reward heading, not the complete emulator window.

        A missing/ambiguous heading is not evidence that a stage ended: the
        watcher remains armed for the next battle/reward screen.
        """
        w, h = image.size
        # 1600x900: heading centred near x=800, y=255 on the gold modal.
        box = (int(w * .39), int(h * .205), int(w * .64), int(h * .345))
        region = image.crop(box)
        if region.width <= 0 or region.height <= 0:
            return False
        scaled = region.resize((region.width * 2, region.height * 2), Image.Resampling.LANCZOS)
        try:
            tokens = self._run_ocr_tokens(scaled)
            compact = ''.join(_compact_ocr_text(t.text) for t in tokens)
            found = ('크리스탈획득' in compact or 'クリスタル獲得' in compact
                     or ('크리스탈' in compact and '획득' in compact))
            if found:
                self._v66_log(f'{APP_VERSION} reward heading OCR confirmed: {compact[:90]}')
            return bool(found)
        except Exception as exc:
            self._v66_log(f'{APP_VERSION} reward heading OCR unavailable: {type(exc).__name__}: {exc}')
            return False

    def _v67_reward_watch_worker(self):
        """v79: bounded, foreground-only reward watch with no GUI-thread capture.

        Screen sampling lasts at most 150 seconds from the last F6. It stops
        immediately on confirmed reward, or after a small number of failed
        OCR checks; when idle it does not capture anything. Avoid frequent PNG
        writes: save at most one diagnostic probe per watch, on timeout.
        """
        missing = 0
        missing_since = 0.0
        last_reward_request = 0.0
        foreground_reported = False
        hidden_button = False
        last_probe_note = 0.0
        last_title_check = 0.0
        last_title_debug = 0.0
        while not self._reward_monitor_stop.wait(1.8):
            if getattr(self,'_all_actions_stopped',False) is True:continue
            if not self._reward_monitor_armed.is_set():
                missing = 0
                missing_since = 0.0
                hidden_button = False
                foreground_reported = False
                continue
            now = time.monotonic()
            if now >= self._reward_watch_deadline:
                self._disarm_reward_watch('timeout (no reward within 480 seconds after last F6)')
                self._reward_monitor_queue.put(('deadline', None))
                missing = 0
                missing_since = 0.0
                continue
            if not getattr(self, '_auto_reward_enabled', True) or not self.board_rect:
                self._disarm_reward_watch('autocount disabled / board unconfigured')
                continue
            if self.playback_active or self.busy or self.detecting_area or self.ocr_busy:
                continue
            # Foreground gating prevents reading Chrome/Explorer over the emulator.
            # A hidden or alt-tabbed game must not be treated as a reward screen.
            if not self._emulator_is_foreground_for_reward():
                if not foreground_reported:
                    foreground_reported = True
                    self._v66_log('v79 watch paused: MuMu is not foreground; no screenshot taken')
                missing = 0
                missing_since = 0.0
                continue
            foreground_reported = False
            try:
                left, top, width, height = (int(v) for v in self.board_rect)
                if width < 240 or height < 190:
                    self._disarm_reward_watch('invalid puzzle board rectangle')
                    continue
                with mss.mss() as sct:
                    shot = sct.grab({'left': left, 'top': top, 'width': width, 'height': height})
                    board_img = Image.frombytes('RGB', shot.size, shot.rgb)
                if _board_image_is_plausible(board_img):
                    missing = 0
                    missing_since = 0.0
                    continue
                missing += 1
                if not missing_since:
                    missing_since = now
                if now - missing_since > 240.0:
                    if not self._reward_probe_written and self._emulator_is_foreground_for_reward():
                        self._reward_probe_written = True
                        try:
                            path = self.data_paths['root'] / 'debug' / 'last_reward_probe.png'
                            path.parent.mkdir(parents=True, exist_ok=True)
                            self._capture_reward_monitor_image().save(path)
                            self._v66_log('v79 saved a single probe on reward watch timeout')
                        except Exception as exc:
                            self._v66_log('v79 single probe unavailable: ' + str(exc))
                    self._disarm_reward_watch('board absent for 240 seconds without confirmed reward (route choice supported)')
                    self._reward_monitor_queue.put(('deadline', None))
                    continue
                if missing < 2:
                    continue
                if missing == 2:
                    self._v66_log('v80 reward watch: board disappeared; examining game screen')
                # v82: board absence alone is NOT a reward screen. Game options,
                # menus, dialogs and stage transitions all obscure the board.
                # Keep the route button visible until a reward modal is verified.
                if self.reward_poll_busy or now - last_reward_request < 3.0:
                    continue
                image = self._capture_reward_monitor_image()
                if self._update_root_tree_context(image.resize((1600,940),Image.Resampling.LANCZOS)):
                    self._farming_queue.put(('root_tree',))
                    missing=0;missing_since=0.0
                    continue
                if not self._emulator_is_foreground_for_reward():
                    continue
                # v87: inspect only the fixed title area first. Do not run
                # full-screen OCR/card detection on stage-transition frames.
                if now - last_title_check < 2.0:
                    continue
                last_title_check = now
                if not self._farming_result_header_confirmed(image):continue
                visual_frame = self._looks_like_reward_frame(image)
                if visual_frame:
                    self._v66_log(f'{APP_VERSION} reward golden dialog candidate detected')
                heading_found = self._reward_title_confirmed(image)
                # v89: Korean title OCR can miss the modal entirely. Use the
                # distinctive full gold frame AND multiple visual card boxes
                # as a fallback, never one criterion alone.
                if not heading_found and visual_frame:
                    try:
                        candidate_boxes = detect_reward_card_rects(image)
                        if len(candidate_boxes) >= 2:
                            heading_found = True
                            self._v66_log(f'{APP_VERSION} reward visual gate: gold frame + {len(candidate_boxes)} cards')
                    except Exception as gate_exc:
                        self._v66_log(f'{APP_VERSION} visual reward gate error: {gate_exc}')
                if now - last_title_debug >= 15.0:
                    last_title_debug = now
                    self._v66_log(f"{APP_VERSION} reward probe: board_missing={missing} heading={heading_found} size={image.size}")
                if heading_found:
                    self._v66_log(f'{APP_VERSION} reward title gate passed; dispatching full card OCR')
                    try:
                        dp = self.data_paths['root'] / 'debug' / 'last_reward_detected.png'
                        dp.parent.mkdir(parents=True, exist_ok=True)
                        image.save(dp)
                    except Exception as exc:
                        self._v66_log(f'{APP_VERSION} detected reward screenshot failure: {exc}')
                    self._reward_monitor_queue.put(('reward', image))
                    last_reward_request = now
                elif now - last_probe_note >= 12.0:
                    last_probe_note = now
                    self._v66_log(f'{APP_VERSION} reward title not found; board_missing={missing}; watch continues')
                    # Keep exactly one diagnostic image per F6 reward session.
                    if not self._reward_probe_written:
                        self._reward_probe_written = True
                        try:
                            path = self.data_paths['root'] / 'debug' / 'last_reward_probe.png'
                            path.parent.mkdir(parents=True, exist_ok=True)
                            image.save(path)
                        except Exception as exc:
                            self._v66_log(f'{APP_VERSION} probe save failed: {exc}')
            except RuntimeError as exc:
                self._v66_log('v79 reward watch capture paused: ' + str(exc))
            except Exception as exc:
                self._v66_log(f'v79 reward watch error: {type(exc).__name__}: {exc}')

    def reward_monitor_tick(self):
        """Legacy entry point, intentionally no-op: v67 uses background monitoring."""
        return

    def _similar_crystal_crops(self, a: Image.Image, b: Image.Image, threshold=21.0):
        try:
            aa = self._normalise_crystal_crop(a)
            bb = self._normalise_crystal_crop(b)
            return float(np.abs(aa - bb).mean()) <= threshold
        except Exception:
            return False

    def _scan_reward_overflow(self, image, tokens, first_items):
        """Best-effort scroll capture if reward rows approach the lower visible edge.

        It scrolls in small increments inside the reward modal, merges overlapping cards
        by image/amount/column/estimated virtual row, then restores the original scroll
        position in a finally block. If the result UI is not scrollable, the unchanged
        frame ends the attempt and no repeated rows are added.
        """
        if not first_items or sys.platform != "win32":
            return first_items
        H, W = image.height, image.width
        lowest = max(item["screen_box"][3] for item in first_items) / max(1, H)
        found_amount_tokens = len(_find_reward_amount_tokens(tokens, image.size))
        # A normal one-row screen (the supplied example has five complete cards around
        # 60% screen height) should never be scrolled. Try overflow only if multiple rows
        # are visible, a quantity token could not be cropped, or a card reaches the lower edge.
        should_probe_scroll = (len(first_items) > 5 or found_amount_tokens > len(first_items)
                               or lowest >= 0.84)
        if not should_probe_scroll:
            return first_items
        try:
            user32 = ctypes.windll.user32
            from ctypes import wintypes
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            saved_cursor = (int(pt.x), int(pt.y))
            mon = getattr(self, "_last_reward_monitor_rect", (0, 0, W, H))
            mx, my = int(mon[0] + W*0.50), int(mon[1] + min(H*0.70, H-60))
            wheel_event = 0x0800
            aggregate = []
            virtual_offset = 0.0
            for item in first_items:
                copied = dict(item); copied["virtual_y"] = float(item["y"])
                aggregate.append(copied)
            previous_page = first_items
            down_steps = 0
            card_height = H * 0.168
            try:
                for page_no in range(5):
                    user32.SetCursorPos(mx, my)
                    user32.mouse_event(wheel_event, 0, 0, ctypes.c_ulong(-120).value, 0)
                    down_steps += 1
                    time.sleep(0.30)
                    new_img = self._capture_reward_monitor_image()
                    if not self._looks_like_reward_header(new_img):
                        break
                    new_tokens = self._run_ocr_tokens(new_img)
                    joined = "".join(_compact_ocr_text(t.text) for t in new_tokens)
                    if "크리스탈획득" not in joined and not ("크리스탈" in joined and "획득" in joined):
                        break
                    new_items = extract_reward_item_crops(new_img, new_tokens)
                    if not new_items:
                        break
                    # If the wheel did not move the visible list, stop immediately;
                    # otherwise identical visible items could be counted as another row.
                    same_view = len(new_items) == len(previous_page)
                    if same_view:
                        used_same = set()
                        for current in new_items:
                            match_j = None
                            for j, prev in enumerate(previous_page):
                                if j in used_same or current["amount"] != prev["amount"]:
                                    continue
                                if abs(current["x"]-prev["x"]) <= W*0.025 and abs(current["y"]-prev["y"]) <= 10:
                                    if self._similar_crystal_crops(current["crop"], prev["crop"], threshold=12.0):
                                        match_j = j; break
                            if match_j is None:
                                same_view = False; break
                            used_same.add(match_j)
                    if same_view:
                        break
                    shifts = []
                    used_prev = set()
                    for current in new_items:
                        possibilities = []
                        for j, prev in enumerate(previous_page):
                            if j in used_prev or current["amount"] != prev["amount"]:
                                continue
                            if abs(current["x"] - prev["x"]) > W*0.045:
                                continue
                            if self._similar_crystal_crops(current["crop"], prev["crop"], threshold=25.0):
                                dy = float(prev["y"] - current["y"])
                                if 0 < dy < H*0.42:
                                    possibilities.append((abs(dy-card_height*0.8), j, dy))
                        if possibilities:
                            _, j, dy = min(possibilities, key=lambda z: z[0])
                            used_prev.add(j); shifts.append(dy)
                    if shifts:
                        virtual_offset += float(np.median(shifts))
                    elif page_no == 0:
                        virtual_offset += card_height * 0.8
                    added = 0
                    for current in new_items:
                        copied = dict(current); copied["virtual_y"] = float(current["y"] + virtual_offset)
                        duplicate = False
                        for prev in aggregate:
                            if copied["amount"] != prev["amount"]:
                                continue
                            if abs(copied["x"]-prev["x"]) > W*0.045:
                                continue
                            if abs(copied["virtual_y"]-prev["virtual_y"]) > card_height*0.42:
                                continue
                            if self._similar_crystal_crops(copied["crop"], prev["crop"], threshold=25.0):
                                duplicate = True; break
                        if not duplicate:
                            aggregate.append(copied); added += 1
                    if added == 0 and not shifts:
                        break
                    previous_page = new_items
            finally:
                # Restore the original view and cursor even if OCR or capture fails.
                for _ in range(down_steps):
                    try:
                        user32.SetCursorPos(mx, my)
                        user32.mouse_event(wheel_event, 0, 0, 120, 0)
                        time.sleep(0.08)
                    except Exception:
                        break
                try:
                    user32.SetCursorPos(saved_cursor[0], saved_cursor[1])
                except Exception:
                    pass
            aggregate.sort(key=lambda item: (item.get("virtual_y", item["y"]), item["x"]))
            return aggregate
        except Exception:
            return first_items

    def _reward_scan_worker(self, image):
        action_epoch=getattr(self,'_stop_epoch',0)
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=action_epoch
        if getattr(self,'_all_actions_stopped',False) is True:return
        try:
            if not self._farming_result_header_confirmed(image):raise ValueError('전투 결과 화면이 아닙니다.')
            # Preserve exactly the pixels used by the reward parser in user data.
            # No personal DB content is bundled into the program ZIP.
            try:
                debug_path = self.data_paths["root"] / "debug" / "last_reward_screen.png"
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                # Save once per candidate cycle; later retries do not hammer disk.
                if self._reward_scan_attempts == 1:
                    image.save(debug_path)
            except Exception as save_exc:
                self._v66_log(f"v78 reward screenshot save failed: {save_exc}")
            tokens = self._run_ocr_tokens(image)
            joined = "".join(_compact_ocr_text(t.text) for t in tokens)
            self._v66_log(f"v70 reward OCR image={image.width}x{image.height}: " + joined[:260])
            has_reward_title = ("크리스탈획득" in joined or ("크리스탈" in joined and "획득" in joined)
                                or ("クリスタル獲得" in joined))
            if not has_reward_title:
                # Dedicated title OCR. Full-screen recognition was polluted by desktop
                # applications and missed the reward heading even with ×2 visible.
                w, h = image.size
                title_box = (int(w*0.12), int(h*0.05), int(w*0.90), int(h*0.43))
                title_crop = image.crop(title_box)
                try:
                    title_tokens = self._run_ocr_tokens(title_crop)
                    title_text = "".join(_compact_ocr_text(t.text) for t in title_tokens)
                    self._v66_log("v74 reward title-region OCR: " + title_text[:160])
                    has_reward_title = ("크리스탈획득" in title_text or
                                        ("크리스탈" in title_text and "획득" in title_text) or
                                        "クリスタル獲得" in title_text)
                except Exception as exc:
                    self._v66_log(f"v74 reward title-region OCR failed: {exc}")
            visual_boxes = detect_reward_card_rects(image)
            # Korean title OCR is unreliable on small type. The yellow-framed
            # reward panel + >=2 individually identifiable cards is an alternate
            # confirmation; never accept the orange x2 badge as a card quantity.
            if not has_reward_title and (self._looks_like_reward_header(image) or self._looks_like_reward_frame(image)) and len(visual_boxes) >= 2:
                self._v66_log(f"v78 reward header OCR missed; candidate visual cards={len(visual_boxes)}")
                has_reward_title = True
            self._v66_log(f"{APP_VERSION} reward heading confirmed={has_reward_title}; visual_boxes={len(visual_boxes)}; frame={self._looks_like_reward_frame(image)}")
            items = []
            if has_reward_title:
                self._v66_log(f"v77 reward visual cards={len(visual_boxes)} boxes={visual_boxes[:12]}")
                # OCR on the illustrated card row at higher resolution; crop-local
                # tokens are remapped to whole-window coordinates.
                extra_tokens=[]
                if visual_boxes:
                    min_x=min(b[0] for b in visual_boxes); max_x=max(b[2] for b in visual_boxes)
                    min_y=min(b[1] for b in visual_boxes); max_y=max(b[3] for b in visual_boxes)
                    x0=max(0,min_x-18); y0=max(0,min_y-15)
                    x1=min(image.width,max_x+18); y1=min(image.height,max_y+18)
                    region=image.crop((x0,y0,x1,y1))
                    try:
                        scaled=region.resize((region.width*2,region.height*2),Image.Resampling.LANCZOS)
                        for t in self._run_ocr_tokens(scaled):
                            extra_tokens.append(OCRToken(t.text,
                                x0+t.x1/2,y0+t.y1/2,x0+t.x2/2,y0+t.y2/2,t.score))
                    except Exception as exc:
                        self._v66_log(f"v77 reward enlarged OCR failed: {exc}")
                self._v66_log("v77 reward quantity OCR: " + repr([(t.text,round(t.cx),round(t.cy)) for t in tokens+extra_tokens if '2' in t.text or '×' in t.text][:18]))
                items = extract_visual_reward_items(image,tokens+extra_tokens,self._run_ocr_tokens)
                if not items:
                    # Legacy OCR-anchored crops are useful on unusual layouts.
                    items = extract_reward_item_crops(image,tokens+extra_tokens)
                self._v66_log(f"v77 reward recognized cards={len(items)} amounts={[i['amount'] for i in items]}")
            if has_reward_title and items:
                items = self._scan_reward_overflow(image, tokens, items)
            self._v66_log(f"v75 reward parse title={has_reward_title} cards={len(items)} tokens={len(tokens)} amounts={len(_find_reward_amount_tokens(tokens, image.size))}")
            sig = None
            if has_reward_title and items:
                # Frame animation and anti-aliasing make pixel-byte signatures
                # differ every poll. Compare stable recognized item positions and
                # quantities across two checks instead. DB matching remains strict.
                hits, scores = self._match_active_target_items(items)
                self._v66_log(f"{APP_VERSION} reward target hits={len(hits)} scores={scores} "
                              f"quantities={[i['amount'] for i in hits]}")
                if hits:
                    sig = tuple(sorted((round(item['x'] / image.width, 2),
                                        int(item['amount'])) for item in hits))
            debug = "\n".join(f"{t.text} ({t.score:.2f}) @ {int(t.x1)},{int(t.y1)}" for t in tokens)
            post_action_result(self,action_epoch, lambda: self._on_reward_scan_done(has_reward_title, items, sig, debug))
        except Exception as exc:
            message = f"획득 결과 OCR 오류: {type(exc).__name__}: {exc}"
            post_action_result(self,action_epoch, lambda msg=message: self._on_reward_scan_done(False, [], None, msg))

    def _on_reward_scan_done(self, has_title, items, signature, debug):
        if getattr(self,'_root_tree_active',False) is True:return
        if getattr(self,'_all_actions_stopped',False) is True:return
        self.reward_poll_busy = False
        self.reward_last_scan_debug = debug
        if not has_title:
            # A transient OCR miss must not kill a watch during stage changes.
            # The overall 480-second / board-absent deadline still applies.
            self._v66_log(f'{APP_VERSION} reward title missed; watcher remains active')
            self.reward_no_header_count += 1
            if self.reward_no_header_count >= 2:
                self.reward_candidate_signature = None
                self.reward_candidate_count = 0
                self.reward_processed_this_visit = False
                self.reward_no_header_count = 0
            return
        self.reward_no_header_count = 0
        if not items or signature is None:
            # Fail closed. Stop after three attempts rather than sampling forever.
            self._v66_log(f"v79 reward no match: item_count={len(items)}; stable signature unavailable")
            self.set_status("크리스탈 획득 감지 · 목표 아이콘/수량 확인 실패. debug/last_reward_screen.png 확인")
            # No target card found: keep bounded lightweight monitoring;
            # the next reward screen may contain the registered material.
            self._reward_confirm_attempts = 0
            return
        if signature == self.reward_candidate_signature:
            self.reward_candidate_count += 1
        else:
            self.reward_candidate_signature = signature
            self.reward_candidate_count = 1
            self._reward_confirm_attempts = 0
        self._reward_confirm_attempts += 1
        if self.reward_candidate_count < 2 or self.reward_processed_this_visit:
            self._v66_log(f"{APP_VERSION} reward waiting for repeated confirmation: {self.reward_candidate_count}/2; retry={self._reward_confirm_attempts}/3")
            if self._reward_confirm_attempts >= 3 and not self.reward_processed_this_visit:
                # Do not count an unstable reward. Stay armed until a later
                # stable screen or the bounded watch timeout.
                self._v66_log(f'{APP_VERSION} reward confirmation failed; clear candidate and continue watch')
                self.reward_candidate_signature = None
                self.reward_candidate_count = 0
                self._reward_confirm_attempts = 0
            return
        # When many cards reach the lower viewport edge, report that only visible cards are read.
        clipped_warning = any(item["screen_box"][3] >= 0.88 * self._last_monitor_height_guess() for item in items)
        try:
            kinds, total, message = self._append_target_reward_event(items)
            self._v66_log(f"v70 reward match: cards={kinds}, amount={total}, message={message}")
            self.reward_processed_this_visit = bool(kinds > 0 and total > 0)
            if self.reward_processed_this_visit:
                self._disarm_reward_watch('reward counted and saved')
            else:
                self._v66_log(f'{APP_VERSION} reward DB yielded no increment; watching for next confirmed reward')
            self.overlay.update_hud(resources=self.format_resource_hud())
            if hasattr(self, "toolbar"):
                self.toolbar.refresh()
            if clipped_warning:
                message += " · 화면 하단 잘림 가능성: 확인된 항목만 집계"
            self.set_status(message)
        except Exception as exc:
            self._disarm_reward_watch('DB write error')
            self.set_status(f"목표 크리스탈 자동 집계 저장 실패: {exc}")

    def _last_monitor_height_guess(self):
        try:
            with mss.mss() as sct:
                return int(sct.monitors[1 if len(sct.monitors)>1 else 0]["height"])
        except Exception:
            return 900

    def capture_and_classify(self, show_preview=True) -> list[str]:
        img = self.grab_board()
        board, hues = classify_board(img)
        spikes = [flag and code not in ("J", "S") for code, flag in zip(board, detect_spike_cells(img))]
        if not _board_image_is_plausible(img, board):
            raise RuntimeError("저장된 영역에서 6×5 퍼즐판을 확인하지 못했습니다. MuMu 위치를 다시 찾습니다.")
        if hasattr(self, '_farming_queue') and _live_board_grid_is_visible(img, board):
            self._farming_observe_board()
        if show_preview:
            self._remember_manual_error_board(img,board,spikes)
        self.board = board
        self.spikes = spikes
        self.last_hues = hues
        self.record_board_history(board, spikes)
        if show_preview: self.update_board_view()
        return board

    def update_board_view(self):
        if not self.board or not getattr(self, "board_cells", None):
            return
        self._displayed_board_signature=tuple(self.board)
        spikes = list(getattr(self, "spikes", [False] * len(self.board)))
        for i, code in enumerate(self.board):
            name, color, _ = COLOR_INFO[code]
            short_name = {"방해": "방해", "회복": "회복", "나무": "나무", "어둠": "어둠"}.get(name, name)
            fg = "#ffffff" if code in ("P", "S", "J") else "#171717"
            label = f"{short_name}\n가시" if code not in ("J", "S") and i < len(spikes) and spikes[i] else short_name
            if i < len(spikes) and spikes[i]:
                fg = "#ffefbd" if code in ("P", "S", "J") else "#7b1515"
            self.board_cells[i].configure(text=label, bg=color, fg=fg)

    def load_board_history(self):
        data = _load_json_dict(self.data_paths["board_history"])
        records = data.get("boards", [])
        self.board_history = records[-30:] if isinstance(records, list) else []
        self._board_history_last_signature = (tuple(self.board_history[-1].get("board", [])),
                                               tuple(self.board_history[-1].get("spikes", []))) if self.board_history else None
        self.refresh_board_history_list()

    def record_board_history(self, board, spikes=None):
        spikes = list(spikes or [False] * len(board))
        signature = (tuple(board), tuple(spikes))
        if self.board_history:
            last = self.board_history[-1]
            if (tuple(last.get("board", [])), tuple(last.get("spikes", [False] * len(board)))) == signature:
                return
        record = {"time": time.strftime("%H:%M:%S"), "board": list(board), "spikes": spikes,
                  "combos": None, "display": "".join(board)}
        self.board_history.append(record)
        self.board_history = self.board_history[-30:]
        self._board_history_last_signature = signature
        self.refresh_board_history_list()
        try:
            _atomic_json_write(self.data_paths["board_history"], {"schema_version": 1, "boards": self.board_history})
        except Exception:
            pass

    def refresh_board_history_list(self):
        if not hasattr(self, "history_listbox") or self.history_listbox is None:
            return
        try:
            selected = self.history_listbox.curselection()
            self.history_listbox.delete(0, tk.END)
            for rec in reversed(self.board_history):
                combo_text = f" · {rec['combos']}콤보" if rec.get("combos") is not None else " · 계산 전"
                self.history_listbox.insert(tk.END, f"{rec.get('time','--:--:--')}{combo_text} · 30칸")
            if selected and selected[0] < self.history_listbox.size():
                self.history_listbox.selection_set(selected[0])
        except tk.TclError:
            pass

    def _path_button_may_show(self):
        if getattr(self,'_background_running',False):return False
        if not self._live_board_ready:
            return False
        if self._path_button_pointer_down or self.toolbar._drag_origin is not None:
            return True  # Never withdraw a button between mouse-down and release.
        return (self.busy or self.playback_active
                or time.monotonic()-self._live_board_seen_at <= 2.0)

    def _clear_live_board(self):
        self._live_board_ready = False
        if hasattr(self, 'toolbar') and self.toolbar.win.winfo_exists():
            self.toolbar.win.withdraw()

    def _set_attribute_status(self, text, color="#f8d67b"):
        self._attribute_status = (text, color)
        self.attribute_status_var.set(text)
        foreground = {"#84edbc": "#176b42", "#ffb2a9": "#a12622"}.get(color, "#8a6300")
        self.attribute_status_label.configure(foreground=foreground)
        self._sync_attribute_indicator_visibility()
        if hasattr(self, 'toolbar'):
            self.toolbar.refresh_attribute_state()

    def _sync_attribute_indicator_visibility(self):
        if hasattr(self, 'attribute_status_frame'):
            if self.goal_var.get() in (2,9):
                self.attribute_status_frame.pack(fill="x")
            else:
                self.attribute_status_frame.pack_forget()

    def _reset_attribute_probe(self, reason):
        self._attribute_probe_epoch += 1
        self._attribute_probe_candidate = None
        self._attribute_probe_repeats = 0
        self._attribute_probe_last_valid = 0.0
        self._set_attribute_status(reason)

    def _attribute_probe_game_visible(self, rect, info):
        """Only inspect a visible MuMu board while MuMu or our controls have focus."""
        if sys.platform != 'win32' or not info:
            return False
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsIconic.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        game = int(info['hwnd'])
        if user32.IsIconic(game) or not user32.IsWindowVisible(game):
            return False
        foreground = int(user32.GetForegroundWindow())
        foreground = int(user32.GetAncestor(foreground, 2) or foreground)
        allowed = {game}
        for window in (self.root, self.toolbar.win):
            hwnd = int(window.winfo_id())
            allowed.add(int(user32.GetAncestor(hwnd, 2) or hwnd))
        if foreground not in allowed or user32.GetAsyncKeyState(0x01) & 0x8000:
            return False
        x, y, width, height = rect
        points = [((c+.5)/COLS, (r+.5)/ROWS) for r in range(ROWS) for c in range(COLS)]
        points += [(.02, .02), (.98, .02), (.02, .98), (.98, .98)]
        for fx, fy in points:
            hwnd = user32.WindowFromPoint(wintypes.POINT(int(x+width*fx), int(y+height*fy)))
            if not hwnd or int(user32.GetAncestor(hwnd, 2) or hwnd) != game:
                return False
        return True

    def _attribute_probe_ready_rect(self):
        if (self.busy or self.playback_active or self.detecting_area or self.ocr_busy
                or self.resource_selector_active or self._card_title_capture_session):
            return None, "5속성: 작업 종료 후 확인"
        if self.overlay.visible and self.overlay.route_drawings_visible:
            return None, "5속성: 경로 표시를 숨긴 뒤 확인"
        point = None
        if self.board_rect:
            x, y, width, height = self.board_rect
            point = (x+width//2, y+height//2)
        info = find_emulator_window_info(point=point)
        current_rect = self._relative_rect_for_window(info) or self.board_rect
        if not current_rect:
            return None, "5속성: 퍼즐 영역 지정 필요"
        rect = tuple(int(value) for value in current_rect)
        x, y, width, height = rect
        if width <= 0 or height <= 0:
            return None, "5속성: 퍼즐 영역 확인 필요"
        # Native hit testing sees occluding windows in front of MuMu; checking
        # Tk bounding boxes would incorrectly reject windows behind the game.
        # Never flash/hide controls for the periodic screenshot.
        if not self._attribute_probe_game_visible(rect, info):
            return None, "5속성: 게임 화면 확인 대기"
        return rect, ""

    def _attribute_probe_worker(self, epoch, rect):
        """No Tk calls, spike detection, history writes, solver or mouse actions."""
        board = None
        captured_at = time.monotonic()
        try:
            x, y, width, height = rect
            with mss.mss() as capture:
                shot = capture.grab(dict(left=x, top=y, width=width, height=height))
                image = Image.frombytes('RGB', shot.size, shot.rgb)
            captured_at = time.monotonic()
            result, _ = classify_board(image)
            self._route_debug_probe_state=live_board_grid_diagnostics(image,result)
            if self._route_debug_probe_state['passed']:
                board = tuple(result)
        except Exception:
            pass
        self._attribute_probe_queue.put((epoch, rect, board, captured_at))

    def _accept_attribute_probe(self, board):
        self._attribute_probe_last_valid = time.monotonic()
        self._live_board_seen_at = self._attribute_probe_last_valid
        key=getattr(self,'_prefind_key',None)
        if key is not None and key[0]!=tuple(board):
            self._prefind_cancel()
        notice=getattr(self,'_route_result_notice',None)
        if isinstance(notice,dict) and (tuple(board)!=notice['board'] or self.selected_goals()!=notice['goals']):
            self._clear_route_result_notice()
            notice=None
        if board != self._attribute_probe_candidate:
            self._attribute_probe_candidate = board
            self._attribute_probe_repeats = 1
            self._set_attribute_status("5속성: 판 안정 확인 중")
        else:
            self._attribute_probe_repeats += 1
            if self._attribute_probe_repeats >= 2:
                self._live_board_ready = True
                if hasattr(self, '_farming_queue'):
                    self._farming_observe_board()
                if self._prefind_feedback(board):
                    pass
                elif isinstance(notice,dict):
                    self._set_attribute_status(notice['text'],'#ffb2a9')
                    if notice.get('button_text'): self.toolbar.button.configure(text=notice['button_text'])
                elif int(self.goal_var.get()) in (2,9):
                    self._set_attribute_status(*five_attribute_inventory_status(board))
                    self.toolbar.button.configure(text="▶ 콤보 우선 실행" if not five_attribute_possible_by_count(board) else "▶ 경로 계산")
                else:
                    self._set_attribute_status("퍼즐판 인식됨 · 계산 버튼/F6", "#84edbc")
                self.toolbar.raise_to_top()

    def _prefind_init(self):
        self._prefind_stopped = False
        self._prefind_after = None
        self._prefind_process = None
        self._prefind_output = None
        self._prefind_result = None
        self._prefind_key = None
        self._prefind_paused_key = None
        self._prefind_failed_key = None
        self._prefind_started_at = None
        self._prefind_probe_busy = False
        self._prefind_probe_next = 0.0
        self._prefind_probe_queue = queue.SimpleQueue()
        self._prefind_after = self.root.after(300,self._prefind_tick)

    def _prefind_cancel(self, keep_result=False):
        had_prefind=getattr(self,'_prefind_key',None) is not None
        process=getattr(self,'_prefind_process',None)
        self._prefind_process=None
        if process is not None and process.pid is not None:
            if process.is_alive(): process.terminate()
            process.join(timeout=.1)
        output=getattr(self,'_prefind_output',None)
        self._prefind_output=None
        if output is not None:
            output.cancel_join_thread()
            output.close()
        if not keep_result or getattr(self,"_prefind_result",None) is None:
            self._prefind_result=None
            self._prefind_key=None
            toolbar=getattr(self,'toolbar',None)
            if had_prefind and toolbar is not None and getattr(toolbar,'button',None) is not None:
                toolbar.button.configure(text='▶ 경로 계산')

    def _prefind_feedback(self, board):
        key=getattr(self,'_prefind_key',None)
        if (key is None or key[0]!=tuple(board) or key[2]!=self.selected_goals()
                or not (getattr(self,'_prefind_process',None) or getattr(self,'_prefind_result',None))):
            return False
        ready=self._prefind_result is not None
        self.toolbar.button.configure(text='▶ 탐색성공 · 실행' if ready else '⌛ 사전 탐색 중')
        phase='탐색성공 · 버튼을 눌러 실행' if ready else '단축 시간 · 사전 탐색 중'
        if key[2][2]:
            # Keep the inventory result visible while route progress changes.
            # A five-color presearch starts only when every color has >=3 orbs.
            phase='5속성 수량 충족 · '+phase
        if len(key)>7 and key[7]:phase+=f' · 적 {key[7]}콤보 이상 / 가산 +{key[8]}'
        self._set_attribute_status(phase,'#84edbc' if ready else '#f8d67b')
        return True

    def _prefind_probe_worker(self, board, rect, goals, avoid):
        captured_at=time.monotonic()
        try:
            image=self._capture_route_timer_image(cache_for_error=False)
            if not shortened_timer_bar(image):
                data=None
            else:
                cap,seconds=self._restricted_drag_move_cap(image=image,report=False)
                info=self._reward_game_window_info()
                gx,gy,_,_=info['rect']
                x,y,w,h=rect
                crop=image.crop((x-gx,y-gy,x-gx+w,y-gy+h))
                actual,_=classify_board(crop)
                if tuple(actual)!=board or seconds is None:
                    data=None
                else:
                    spikes=tuple(flag and code not in ('J','S')
                                 for flag,code in zip(detect_spike_cells(crop),actual))
                    condition=App._read_combo_condition(self,image,board,store=False)
                    data=((board,spikes,goals,avoid,seconds,cap,rect,
                           condition['required_total'],condition['combo_bonus']) if condition['confirmed'] else None)
            self._prefind_probe_queue.put((board,rect,goals,captured_at,data))
        except Exception:
            self._prefind_probe_queue.put((board,rect,goals,captured_at,None))

    def _prefind_cached_route(self, board, spikes, goals, avoid, seconds, cap):
        key=getattr(self,'_prefind_key',None)
        path=getattr(self,'_prefind_result',None)
        if (key is None or path is None or seconds is None
                or key[:6]!=(tuple(board),tuple(spikes),tuple(goals),bool(avoid),seconds,cap)
                or key[6]!=tuple(self.board_rect)
                or getattr(self,'_force_prefind_bypass',False)):
            return None
        effective=App._combo_goals(self,board,goals)
        current=tuple(effective[7:9]) if len(effective)>7 else (0,0)
        if current!=(tuple(key[7:9]) if len(key)>7 else (0,0)):return None
        return verified_prefind_route(board,path,effective,cap)

    def _combo_offer_matches(self, board, spikes=None, goals=None):
        offer=getattr(self,'_route_combo_offer',None)
        if not isinstance(offer,dict): return False
        requested=tuple(goals) if goals is not None else self.selected_goals()
        return (tuple(board)==offer['board'] and requested==offer['goals']
                and tuple(getattr(self,'board_rect',()) or ())==offer['rect']
                and (spikes is None or tuple(spikes)==offer['spikes']))

    def _offer_combo_after_limit(self, board, spikes, goals, seconds=None, cap=None, attrs=None):
        """An expired search offers a separate clicked action, never auto input."""
        if not (len(goals)>2 and goals[2] and sum(goals)==1): return False
        if not five_attribute_possible_by_count(board): return False
        self._route_combo_offer=dict(board=tuple(board),spikes=tuple(spikes),goals=tuple(goals),
            rect=tuple(getattr(self,'board_rect',()) or ()),seconds=seconds,cap=cap)
        context=getattr(self,'_route_search_context',None)
        limited=isinstance(context,dict) and context.get('board')==tuple(board) and context.get('failure_kind')=='time_limit_insufficient'
        reason='제한시간부족' if limited else '탐색 실패(3초 한도)'
        text='5속성 수량 충족 · '+reason
        if attrs is not None: text+=f' · 경로 미발견 {attrs}/5'
        effective=App._combo_goals(self,board,goals)
        if len(effective)>7 and effective[7]:text+=f' · 적 {effective[7]}콤보 이상 (가산 +{effective[8]})'
        text+=' · 콤보 전환 가능'
        button='▶ 콤보로 실행 · '+('제한시간부족' if limited else '탐색 실패')
        self._route_result_notice=dict(board=tuple(board),goals=tuple(goals),text=text,button_text=button)
        self._set_attribute_status(text,'#ffb2a9')
        if hasattr(self,'toolbar'): self.toolbar.button.configure(text=button)
        self.set_status(text+' · 버튼을 누르면 이번 실행만 콤보 우선으로 계산합니다.')
        snapshot=getattr(self,'_manual_error_snapshot',None)
        if not isinstance(snapshot,dict) or snapshot.get('board')==list(board):
            self._route_debug_update(dict(combo_fallback_offered=True,combo_offer_reason='search_limit'))
        return True

    def _prefind_expired(self):
        key=self._prefind_key
        if key is None: return
        self._prefind_cancel()
        self._prefind_failed_key=key
        if not self._offer_combo_after_limit(key[0],key[1],key[2],key[4],key[5]):
            self._prefind_paused_key=key
            text='탐색 실패(3초 한도) · 사전 탐색 종료'
            self._route_result_notice=dict(board=key[0],goals=key[2],text=text,
                button_text='↻ 다시 계산 · 탐색 실패')
            self._set_attribute_status(text,'#ffb2a9')
            self.toolbar.button.configure(text='↻ 다시 계산 · 탐색 실패')

    def _prefind_start(self, key):
        if key==getattr(self,'_prefind_failed_key',None): return
        if key==getattr(self,'_prefind_paused_key',None): return
        if self._combo_offer_matches(key[0],key[1],key[2]):
            offer=self._route_combo_offer
            if offer['seconds']==key[4] and offer['cap']==key[5]:
                self._prefind_failed_key=key
                return
            self._clear_route_result_notice()
        self._prefind_failed_key=None
        self._prefind_paused_key=None
        if key==getattr(self,'_prefind_key',None): return
        self._prefind_cancel()
        if key[2][2] and not five_attribute_possible_by_count(key[0]): return
        import multiprocessing
        context=multiprocessing.get_context('spawn')
        self._prefind_key=key
        if len(key)>7:
            self._route_combo_condition=dict(board=key[0],required_total=key[7],combo_bonus=key[8],confirmed=True,source='presearch_icons')
        self._prefind_started_at=time.monotonic()
        self._prefind_output=context.Queue(maxsize=1)
        self._prefind_process=context.Process(target=shortened_route_search_process,
            args=(key[0],key[1],key[2],key[5],key[3],self._prefind_output),
            kwargs=dict(required_total=key[7],combo_bonus=key[8]) if len(key)>7 else {},daemon=True)
        try:
            self._prefind_process.start()
        except Exception:
            self._prefind_cancel()
            self._prefind_paused_key=key

    def _prefind_tick(self):
        self._prefind_after=None
        if self._prefind_stopped: return
        try:
            if getattr(self,'_all_actions_stopped',False) is True:return
            if getattr(self,'_background_running',False):
                return
            if (getattr(self,'_path_button_pointer_down',False)
                    or getattr(self.toolbar,'_drag_origin',None) is not None):
                return
            rect,_=self._attribute_probe_ready_rect()
            board=getattr(self,'_attribute_probe_candidate',None)
            eligible=(rect is not None and self._live_board_ready and board is not None
                      and not getattr(self,'_route_ui_active',False))
            if not eligible:
                # A clicked ready route is retained only until its fresh validation.
                self._prefind_cancel(keep_result=bool(getattr(self,'_route_ui_active',False)))
            else:
                board,rect=tuple(board),tuple(rect)
                goals=self.selected_goals()
                if goals[2] and not five_attribute_possible_by_count(board):
                    self._prefind_cancel()
                    return
                key=self._prefind_key
                if key and (key[0]!=board or key[2]!=goals or key[6]!=rect):
                    self._prefind_cancel()
                while True:
                    try: sampled,area,mode,at,data=self._prefind_probe_queue.get_nowait()
                    except queue.Empty: break
                    self._prefind_probe_busy=False
                    if sampled!=board or area!=rect or mode!=goals or time.monotonic()-at>2:
                        continue
                    if data is None: self._prefind_cancel()
                    else: self._prefind_start(data)
                output=self._prefind_output
                if output is not None:
                    try: kind,path,attempts=output.get_nowait()
                    except queue.Empty:
                        started=getattr(self,'_prefind_started_at',None)
                        if (started is not None and time.monotonic()-started>=PRESEARCH_WAIT_LIMIT):
                            self._prefind_expired()
                        elif self._prefind_process is not None and not self._prefind_process.is_alive():
                            self._prefind_paused_key=self._prefind_key
                            self._prefind_cancel()
                            self.set_status('사전 탐색 작업이 종료되었습니다. 버튼으로 다시 계산할 수 있습니다.')
                    else:
                        key=self._prefind_key
                        valid=(kind=='success' and verified_prefind_route(board,path,combo_condition_goals(goals,*key[7:9]) if len(key)>7 else goals,key[5]))
                        if valid:
                            self._prefind_result=list(path)
                            self._prefind_cancel(keep_result=True)
                        elif kind=='timeout':
                            self._prefind_expired()
                        else:
                            self._prefind_paused_key=key
                            self._prefind_cancel()
                self._prefind_feedback(board)
                now=time.monotonic()
                if not self._prefind_probe_busy and now>=self._prefind_probe_next:
                    self._remember_route_timer_windows()
                    self._prefind_probe_busy=True
                    self._prefind_probe_next=now+1.5
                    threading.Thread(target=self._prefind_probe_worker,
                        args=(board,rect,goals,self.selected_skull_safety()),daemon=True).start()
            # Drain cancelled probes too; never leave a stale busy flag forever.
            if not eligible:
                while True:
                    try: self._prefind_probe_queue.get_nowait()
                    except queue.Empty: break
                    self._prefind_probe_busy=False
        except (tk.TclError,OSError,ValueError,AttributeError):
            self._prefind_cancel()
        finally:
            if not self._prefind_stopped and self.root.winfo_exists():
                self._prefind_after=self.root.after(300,self._prefind_tick)

    def _farming_sync_stats_focus(self):
        """Follow game focus without activating any window or reviving closed UI."""
        win=getattr(self,'_farming_stats',None)
        if win is None or not win.winfo_exists() or win.state() in ('withdrawn','iconic'):
            self._farming_stats_front=None
            return
        front=False
        if getattr(self,'_farming_result_visible',False) or getattr(self,'_farming_lobby_visible',False):
            if sys.platform!='win32':
                front=True
            else:
                from ctypes import wintypes
                api=ctypes.windll.user32
                api.GetForegroundWindow.restype=wintypes.HWND
                api.IsIconic.argtypes=(wintypes.HWND,)
                api.IsIconic.restype=wintypes.BOOL
                api.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT)
                api.GetAncestor.restype=wintypes.HWND
                info=self._reward_game_window_info()
                foreground=int(api.GetForegroundWindow() or 0)
                foreground=int(api.GetAncestor(foreground,2) or foreground)
                own=int(api.GetAncestor(win.winfo_id(),2) or win.winfo_id())
                front=bool(info and foreground in (int(info['hwnd']),own)
                           and not api.IsIconic(int(info['hwnd'])))
        if front!=getattr(self,'_farming_stats_front',None):
            win.attributes('-topmost',front)
            if sys.platform=='win32':
                from ctypes import wintypes
                api=ctypes.windll.user32
                api.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT)
                api.GetAncestor.restype=wintypes.HWND
                hwnd=api.GetAncestor(win.winfo_id(),2) or win.winfo_id()
                api.SetWindowPos.argtypes=(wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,
                                          ctypes.c_int,ctypes.c_int,wintypes.UINT)
                api.SetWindowPos.restype=wintypes.BOOL
                # TOPMOST when gaming, BOTTOM otherwise; never activate.
                api.SetWindowPos(hwnd,wintypes.HWND(-1 if front else 1),0,0,0,0,0x13)
            elif front: win.lift()
            else: win.lower()
            self._farming_stats_front=front

    def _attribute_probe_tick(self):
        """Inventory/board check; shortened-time route presearch has its own lifecycle."""
        self._attribute_probe_after = None
        if self._attribute_probe_stopped:
            return
        try:
            if getattr(self,'_all_actions_stopped',False) is True:return
            if getattr(self,'_background_running',False):
                return
            if self._path_button_pointer_down or self.toolbar._drag_origin is not None:
                return
            rect, reason = self._attribute_probe_ready_rect()
            if rect is None:
                self._reset_attribute_probe(reason)
                if not (self.busy or self.playback_active):
                    self._clear_live_board()
            elif rect != self._attribute_probe_rect:
                self._reset_attribute_probe("5속성: 판 확인 중")
                self._clear_live_board()
            self._attribute_probe_rect = rect
            while True:
                try:
                    epoch, sampled_rect, board, captured_at = self._attribute_probe_queue.get_nowait()
                except queue.Empty:
                    break
                self._attribute_probe_active = False
                if rect is None or epoch != self._attribute_probe_epoch:
                    continue
                if sampled_rect != rect or time.monotonic()-captured_at > 2.0 or board is None:
                    self._reset_attribute_probe("5속성: 판 확인 대기")
                    self._clear_live_board()
                    continue
                self._accept_attribute_probe(board)
            now = time.monotonic()
            if (self._attribute_probe_candidate is not None
                    and now-self._attribute_probe_last_valid > 2.0):
                self._reset_attribute_probe("5속성: 판 확인 대기")
                self._clear_live_board()
            if rect is not None and not self._attribute_probe_active and now >= self._attribute_probe_next:
                self._attribute_probe_active = True
                self._attribute_probe_next = now + .65
                try:
                    threading.Thread(target=self._attribute_probe_worker,
                                     args=(self._attribute_probe_epoch, rect), daemon=True).start()
                except RuntimeError:
                    self._attribute_probe_active = False
                    self._reset_attribute_probe("5속성: 판 확인 대기")
        except (tk.TclError, OSError, ValueError, AttributeError):
            self._reset_attribute_probe("5속성: 화면 확인 대기")
            self._clear_live_board()
        finally:
            if not self._attribute_probe_stopped and self.root.winfo_exists():
                self._attribute_probe_after = self.root.after(250, self._attribute_probe_tick)

    def _on_goal_mode_changed(self):
        self.save_user_settings()
        self._prefind_cancel()
        self._route_combo_fallback=None
        self._clear_route_result_notice()
        self._reset_attribute_probe("5속성: 확인 대기")

    def selected_goals(self) -> tuple[bool, ...]:
        """Keep stable mode IDs while displaying the six requested party goals."""
        try:
            selected = int(self.goal_var.get())
        except (AttributeError, ValueError, tk.TclError):
            selected = 0
        if selected not in {i for i, _, _ in ROUTE_GOAL_OPTIONS}:
            selected = 0
        return master_multicolor_goals() if selected==9 else tuple(index == selected for index in range(7))

    def selected_skull_safety(self) -> bool:
        # Until HP/poison damage are measured, retain conservative hazard ranking.
        # This is a tie-breaker for shape/colour activation, not an HP safety claim.
        return True

    def _combo_fallback_matches(self, board, goals=None, path=None, check_selection=True):
        """One clicked run only; never globally relax the selected party goal."""
        context=getattr(self,'_route_combo_fallback',None)
        if not isinstance(context,dict): return False
        if (tuple(board)!=context['board'] or tuple(getattr(self,'spikes',()))!=context['spikes']
                or (check_selection and self.selected_goals()!=context['requested_goals'])):
            return False
        reason=context.get('reason')
        if reason=='five_attribute_inventory_shortage':
            if five_attribute_possible_by_count(board): return False
        elif reason not in ('route_search_limit','master_survival'):
            return False
        if goals is not None and tuple(goals)!=context['effective_goals']: return False
        if path is not None and tuple(path)!=context.get('path'): return False
        return True

    def _prepare_route_goals(self, board, spikes):
        requested=self.selected_goals()
        effective,fallback=inventory_route_goals(board,requested)
        offer=getattr(self,'_route_combo_offer',None)
        offered=(self._combo_offer_matches(board,spikes,requested)
                 and not bool(getattr(self,'_force_next_search',False)))
        if offered:
            effective=(False,)*len(requested)
            fallback=True
        self._route_combo_fallback=None
        if fallback:
            self._route_combo_fallback=dict(board=tuple(board),spikes=tuple(spikes),
                requested_goals=tuple(requested),effective_goals=tuple(effective),
                reason='route_search_limit' if offered else 'five_attribute_inventory_shortage',
                offer_seconds=offer.get('seconds') if offered else None,
                offer_cap=offer.get('cap') if offered else None)
        return effective

    def _route_playback_goals(self, board, path):
        if self._combo_fallback_matches(board,path=path):
            return self._route_combo_fallback['effective_goals']
        return self.selected_goals()

    def calculate_clicked(self, use_current=False):
        resume_actions(self)
        if getattr(self,'_background_running',False):
            self.set_status('백그라운드 파밍 중입니다. F10으로 중지한 뒤 직접 계산할 수 있습니다.');return
        if getattr(self, '_manual_reward_busy', False):
            self.set_status('결과창 집계 중입니다. F10으로 중지한 뒤 경로를 계산할 수 있습니다.')
            return
        if self.busy:
            return
        if self.playback_active:
            self.set_status("자동 실행 중에는 새 경로를 계산할 수 없어요. F10으로 중지해 주세요.")
            return
        if self.ocr_busy or self.resource_selector_active or self.detecting_area:
            self.set_status('화면 인식 또는 영역 지정이 끝난 뒤 경로를 계산해 주세요.')
            return
        if getattr(self, '_route_ui_waiting_capture', False):
            return
        if (not getattr(self, "_route_ui_active", False)
                and getattr(self, "_prefind_process", None) is not None
                and not getattr(self, "_force_next_search", False)):
            # An explicit click takes priority over speculative presearch.
            # Do not require a second click after a background timeout.
            self._prefind_cancel()
        if self._begin_route_ui_session():
            self._route_ui_waiting_capture = True
            # Let Windows finish minimizing/unmapping before reading the board.
            self.root.after(200, lambda epoch=getattr(self,'_stop_epoch',0): self._run_route_capture(use_current) if action_request_current(self,epoch) else None)
            return
        self._refresh_board_rect_from_emulator()
        if not self.board_rect:
            self.auto_detect_area(start_calculation=True)
            return
        try:
            if use_current and self.board:
                board = list(self.board)
            else:
                board = self.capture_and_classify(show_preview=True)
        except Exception as exc:
            if self.board_rect:
                # A saved region can become stale after a display/resolution change.
                # Clear it and use the same automatic detector available on first run.
                self.set_status(f"저장된 영역을 사용할 수 없어 뮤뮤 창/주변 좌표를 다시 탐지합니다: {exc}")
                self.auto_detect_area(start_calculation=True)
                return
            self.set_status(f"화면 판독 오류: {exc}")
            return
        goals = self._prepare_route_goals(board,self.spikes)
        auto_execute = True
        avoid_skull = self.selected_skull_safety()
        signature = tuple(board)
        spike_signature = tuple(self.spikes)
        same_solved_state = (
            signature == getattr(self, "_last_solved_signature", None)
            and spike_signature == getattr(self, "_last_solved_spike_signature", None)
            and goals == getattr(self, "last_solved_goals", None)
            and avoid_skull == getattr(self, "last_solved_avoid_skull", None)
            and bool(self.last_path)
        )
        # Repeated F6/button presses on the exact same board reuse the verified
        # route instead of recapturing/solving needlessly. Shift+F6 forces a fresh search.
        force_search = bool(getattr(self, "_force_next_search", False))
        self._force_next_search = False
        self._force_prefind_bypass = force_search
        if same_solved_state and not force_search and not auto_execute:
            self.overlay.show_route(self.board_rect, self.last_path, self.spikes)
            self.set_status("현재 퍼즐판과 조건이 직전 계산과 동일해 기존 경로를 재사용했어요. 새 탐색은 Shift+F6.")
            if auto_execute:
                self._route_ui_waiting_playback = True
                self.root.after(80, lambda: self._schedule_playback(
                    list(self.last_path), (tuple(board), tuple(self.spikes)), source="계산 후 자동 실행"))
            return
        if goals[2]:
            counts=five_attribute_inventory(board)
            self._v66_log(f"{APP_VERSION} five-attribute precheck: {counts}; possible={five_attribute_possible_by_count(board)}")
        self._clear_route_result_notice()
        self._reset_attribute_probe("5속성: 경로 계산 중")
        self.busy = True
        self._v88_calc_start = time.perf_counter()
        if auto_execute:
            self.overlay.hide()  # Remove any route from the previous calculation immediately.
        fallback=isinstance(getattr(self,"_route_combo_fallback",None),dict)
        self._route_debug_update(dict(combo_fallback=fallback,requested_goals=list(self.selected_goals()),effective_goals=list(goals)))
        self.set_status("이번 턴 콤보 우선 탐색 중…" if fallback else "경로 탐색 중…")
        self._route_cancel=threading.Event()
        threading.Thread(target=self._solve_worker,
                         args=(board, list(self.spikes), goals, auto_execute, avoid_skull),
                         kwargs=dict(action_epoch=getattr(self,'_stop_epoch',0),cancel_event=self._route_cancel),daemon=True).start()

    def _remember_route_timer_windows(self):
        self._route_timer_allowed_hwnds=set()
        if sys.platform!='win32': return
        from ctypes import wintypes
        api=ctypes.windll.user32
        api.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT)
        api.GetAncestor.restype=wintypes.HWND
        for window in (self.root,self.toolbar.win):
            hwnd=int(window.winfo_id())
            self._route_timer_allowed_hwnds.add(int(api.GetAncestor(hwnd,2) or hwnd))

    def _capture_route_timer_image(self, cache_for_error=True):
        """Read visible game pixels with either the game or our controls focused.

        This is separate from reward monitoring's game-only foreground rule.
        Native hit tests still reject other applications covering the timer.
        """
        info=self._reward_game_window_info()
        if not info: raise RuntimeError('이동시간을 읽을 게임 창을 찾지 못했습니다.')
        x,y,width,height=map(int,info['rect'])
        if sys.platform=='win32':
            from ctypes import wintypes
            api=ctypes.windll.user32
            api.GetAncestor.argtypes=(wintypes.HWND,wintypes.UINT)
            api.GetAncestor.restype=wintypes.HWND
            api.GetForegroundWindow.restype=wintypes.HWND
            api.IsIconic.argtypes=(wintypes.HWND,)
            api.IsWindowVisible.argtypes=(wintypes.HWND,)
            api.WindowFromPoint.argtypes=(wintypes.POINT,)
            api.WindowFromPoint.restype=wintypes.HWND
            game=int(info['hwnd'])
            if api.IsIconic(game) or not api.IsWindowVisible(game):
                raise RuntimeError('게임 창이 최소화되거나 숨겨져 있습니다.')
            foreground=int(api.GetForegroundWindow() or 0)
            foreground=int(api.GetAncestor(foreground,2) or foreground)
            if foreground not in ({game}|set(getattr(self,'_route_timer_allowed_hwnds',()))):
                raise RuntimeError('다른 프로그램이 전면에 있어 이동시간 캡처를 보류했습니다.')
            points=[(x+round(width*fx),y+round(height*fy))
                    for fx in (.55,.65,.76,.87,.98) for fy in (.195,.24,.29)]
            bx,by,bw,bh=self.board_rect
            points.append((int(bx+bw/2),int(by+bh/2)))
            for px,py in points:
                owner=api.WindowFromPoint(wintypes.POINT(px,py))
                if not owner or int(api.GetAncestor(owner,2) or owner)!=game:
                    raise RuntimeError('게임의 이동시간 표시줄 또는 보드가 다른 창에 가려져 있습니다.')
        rect=dict(left=x,top=y,width=width,height=height)
        with mss.mss() as capture:
            shot=capture.grab(rect)
            image=Image.frombytes('RGB',shot.size,shot.rgb)
        snapshot=getattr(self,'_manual_error_snapshot',None)
        if cache_for_error and snapshot is not None and snapshot['board']==list(self.board):
            snapshot.update(game_image=image,game_window_rect=[x,y,width,height])
            snapshot.pop('game_original_unavailable',None)
        return image

    def _clear_route_result_notice(self):
        self._route_result_notice=None
        self._route_combo_offer=None
        toolbar=getattr(self,'toolbar',None)
        if toolbar is not None and getattr(toolbar,'button',None) is not None:
            toolbar.button.configure(text='▶ 경로 계산')

    def _read_combo_condition(self, image, board, store=True):
        frame,enemy,bonus=combo_status_candidates(image)
        condition=dict(board=tuple(board),required_total=0,combo_bonus=0,confirmed=True,
                       enemy_badges=[],bonus_badges=[],source='screen_icons')
        cache=getattr(self,'_combo_icon_ocr_cache',{})
        new_cache={}
        def read(box,yellow):
            crop=frame.crop(box)
            key=(yellow,hashlib.sha256(crop.tobytes()).hexdigest())
            if key in cache:
                new_cache[key]=cache[key];return cache[key]
            readings=[]
            for view in combo_digit_views(crop,yellow):
                text,score=self._recognize_title_line(view)
                text=re.sub(r'\s+','',text)
                match=re.fullmatch(r'(\d{1,2})',text)
                readings.append((int(match.group(1)) if match else None,score,text))
            value=None
            if len(readings)==2 and all(v is not None and score>=.82 for v,score,_ in readings) and readings[0][0]==readings[1][0]:
                candidate=readings[0][0]
                if 1<=candidate<=20:value=candidate
            answer=(value,readings)
            new_cache[key]=answer
            return answer
        for box in enemy:
            try:value,readings=read(box,False)
            except Exception as exc:value,readings=None,[str(exc)]
            condition['enemy_badges'].append(dict(box=box,value=value,ocr=readings))
            if value is None:condition['confirmed']=False
            else:condition['required_total']=max(condition['required_total'],value+1)
        # An unread bonus contributes zero. Multiple identical crops from bead
        # proposals must not add the same buff twice; use the largest confirmed one.
        for box in bonus:
            try:value,readings=read(box,True)
            except Exception as exc:value,readings=None,[str(exc)]
            condition['bonus_badges'].append(dict(box=box,value=value,ocr=readings))
            if value is not None:condition['combo_bonus']=max(condition['combo_bonus'],value)
        self._combo_icon_ocr_cache=new_cache
        if store:
            self._route_combo_condition=condition
            self._route_debug_update(dict(combo_condition=condition))
        return condition

    def _combo_goals(self, board, goals):
        condition=getattr(self,'_route_combo_condition',None)
        if not isinstance(condition,dict) or condition.get('board')!=tuple(board):return goals
        return combo_condition_goals(goals,condition['required_total'],condition['combo_bonus'])

    def _combo_condition_hold(self, board):
        condition=getattr(self,'_route_combo_condition',None)
        return (isinstance(condition,dict) and condition.get('board')==tuple(board)
                and not condition.get('confirmed',False))

    def _restricted_drag_move_cap(self, image=None, report=True):
        """Read the timer bar and return a geometry-aware drag limit.

        OCR is restricted to the cyan/red time bar; an unconfident time read
        produces a conservative path cap, not a guessed number of seconds.
        """
        try:
            image = image if image is not None else self._capture_route_timer_image()
            if report:
                try:App._read_combo_condition(self,image,getattr(self,'board',[]))
                except Exception as exc:
                    self._route_combo_condition=dict(board=tuple(getattr(self,'board',[])),required_total=0,combo_bonus=0,
                        confirmed=False,error=str(exc))
            rgb = np.asarray(image.convert("RGB"))
            H, W = rgb.shape[:2]
            x0, x1 = int(W*.53), int(W*.99)
            y0, y1 = int(H*.19), int(H*.30)
            region = rgb[y0:y1, x0:x1]
            if region.size == 0: raise ValueError('이동시간 영역이 비어 있습니다.')
            r, g, b = region[...,0].astype(np.int16), region[...,1].astype(np.int16), region[...,2].astype(np.int16)
            cyan = (g > 145) & (b > 145) & (r < 135)
            red = (r > 170) & (g < 140) & (b < 135)
            # Read both full cyan and shortened cyan/red timer bars.
            lengths = np.sum(cyan | red, axis=1)
            row = int(np.argmax(lengths)); filled = int(lengths[row])
            nred = int(red[max(0,row-2):row+3].sum())
            if filled < W*.22:
                raise ValueError('이동시간 표시줄을 확인하지 못했습니다.')
            yline = y0 + row
            timer_crop = image.crop((x0, max(0,yline-33), x1, min(H,yline+40)))
            seconds = None
            boxes=timer_badge_boxes(timer_crop)
            readings=[]
            confirmed=[]
            for box in boxes:
                badge=timer_crop.crop(box)
                inset=max(2,round(min(badge.size)*.08))
                views=(badge,badge.crop((inset,inset,badge.width-inset,badge.height-inset)).convert('L').convert('RGB'))
                votes=[]
                for view in views:
                    try:
                        number,score,text=self._recognize_number_crop(view.resize((view.width*3,view.height*3),Image.Resampling.LANCZOS))
                        readings.append((number,round(score,4),text))
                        if number is not None and 1<=number<=30 and score>=.85: votes.append(number)
                    except Exception as exc:
                        readings.append((None,0,str(exc)))
                if len(votes)==2 and votes[0]==votes[1]: confirmed.append(votes[0])
            if confirmed and len(set(confirmed))==1: seconds=confirmed[0]
            if seconds is None:
                try:
                    local=self._run_ocr_tokens(timer_crop.resize((timer_crop.width*2,timer_crop.height*2),Image.Resampling.LANCZOS))
                    viable=[]
                    for token in local:
                        val=token.text.strip()
                        # If a badge was found, text elsewhere (e.g. scenery's 4)
                        # cannot invalidate or replace the timer's own number.
                        in_badge=not boxes or any(x0<=token.cx/2<=x1 and y0<=token.cy/2<=y1 for x0,y0,x1,y1 in boxes)
                        if in_badge and token.score>=.85 and re.fullmatch(r'\d{1,2}',val) and 1<=int(val)<=30:
                            viable.append(int(val))
                    if len(set(viable))==1: seconds=viable[0]
                except Exception as exc:
                    if report: self._v66_log(f'timer OCR unavailable: {exc}')
            if report: self._route_debug_update(dict(timer_badge_boxes=boxes,timer_badge_readings=readings))
            cap = 0 if seconds is None and master_multicolor(self.selected_goals()) else self._drag_move_cap(seconds or 5)
            if report: self._route_debug_update(dict(timer_seconds=seconds, timer_move_cap=cap, timer_source='bar',timer_error=None if seconds is not None else '표시줄 숫자를 확정하지 못했습니다.'))
            if report: self._v66_log(f"v78 reduced timer: seconds={seconds}, safe move cap={cap}, red={nred}, filled={filled}")
            return cap, seconds
        except Exception as exc:
            if report:
                self._route_combo_condition=dict(board=tuple(getattr(self,'board',[])),required_total=0,combo_bonus=0,
                    confirmed=False,error=str(exc))
                self._v66_log(f"v78 reduced timer scan failed: {exc}")
            cap = 0 if master_multicolor(self.selected_goals()) else self._drag_move_cap(5)
            if report: self._route_debug_update(dict(timer_seconds=None,timer_move_cap=cap,timer_source='capture_failed',timer_error=str(exc)))
            return cap, None


    def _drag_move_cap(self, seconds):
        # Include interpolation steps and scheduling overhead at the actual
        # board size; never allow an unread timer to become an unlimited path.
        rect = getattr(self,'board_rect',None)
        cell = max(rect[2]/COLS,rect[3]/ROWS) if rect else 120
        per_move = max(.14, math.ceil(cell/10)*.014+.005)
        context=getattr(self,'_route_combo_fallback',None)
        survival=(isinstance(context,dict) and context.get('reason')=='master_survival'
                  and context.get('board')==tuple(getattr(self,'board',[])) and context.get('requested_goals')==self.selected_goals())
        usable=float(seconds)-(4.0 if master_multicolor(self.selected_goals()) and not survival else 0.0)
        return max(0,min(65,int((usable-1.0)/per_move)))

    def _solve_worker(self, board, spikes, goals, auto_execute=False, avoid_skull=True,action_epoch=None,cancel_event=None):
        if action_epoch is None:action_epoch=getattr(self,'_stop_epoch',0)
        if cancel_event is None:cancel_event=getattr(self,'_route_cancel',None)
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=action_epoch
        if not action_request_current(self,action_epoch):return
        try:
            started = time.perf_counter()
            move_cap, detected_seconds = self._restricted_drag_move_cap()
            master_requested=master_multicolor(goals)
            requested_master=tuple(goals)
            enemy_unconfirmed=App._combo_condition_hold(self,board)
            if master_requested and detected_seconds is not None and (detected_seconds<=5 or enemy_unconfirmed or not five_attribute_possible_by_count(board)):
                condition=getattr(self,'_route_combo_condition',{})
                goals=master_survival_goals(bonus=condition.get('combo_bonus',0))
                self._route_combo_fallback=dict(board=tuple(board),spikes=tuple(spikes),requested_goals=requested_master,effective_goals=goals,reason='master_survival')
                move_cap=self._drag_move_cap(detected_seconds)
            route_operation_seconds(detected_seconds,goals)
            timer_elapsed = time.perf_counter() - started
            if App._combo_condition_hold(self,board) and not master_survival(goals):
                raise RuntimeError('적 콤보 조건 미확인 · 아이콘 숫자를 확인하지 못해 실행을 보류합니다.')
            effective=App._combo_goals(self,board,goals)
            fallback=App._combo_fallback_matches(self,board,goals=goals,check_selection=False)
            fallback_context=getattr(self,'_route_combo_fallback',None)
            if (fallback and fallback_context.get('reason')=='route_search_limit'
                    and fallback_context.get('offer_seconds') is not None
                    and (detected_seconds!=fallback_context['offer_seconds'] or move_cap!=fallback_context['offer_cap'])):
                goals=fallback_context['requested_goals']
                self._route_combo_fallback=None
                fallback=False
                self._route_debug_update(dict(combo_offer_expired_timer=True))
            effective=App._combo_goals(self,board,goals)
            required_total=int(effective[7]) if len(effective)>7 else 0
            combo_bonus=int(effective[8]) if len(effective)>8 else 0
            default_budget = FIVE_ATTR_TIME_BUDGET if goals[2] or fallback else SOLVER_TIME_BUDGET
            budget = route_search_budget(detected_seconds,default_budget)
            cap = move_cap if move_cap is not None else (FIVE_ATTR_DEFAULT_MOVE_CAP if goals[2] else SOLVER_MAX_MOVES)
            # A wider-route probe only diagnoses a real move-length restriction.
            # It never supplies a route for execution and shares the same budget.
            wider_cap = max(FIVE_ATTR_DEFAULT_MOVE_CAP,cap or 0)
            reserve = min(.75,budget*.20) if detected_seconds is not None and cap is not None and cap<min(wider_cap,65) else 0.0
            if fallback: reserve=0.0
            context=dict(board=tuple(board),goals=tuple(goals),timer_seconds=detected_seconds,
                         move_cap=cap,search_budget=budget,primary_budget=budget-reserve,failure_kind=None)
            self._route_search_context=context
            self._v66_log(f"{APP_VERSION} solver strategy: {'5-attribute search' if goals[2] else 'normal'}, budget={budget}, reserve={reserve}")
            def search(seconds,limit):
                if goals[2]:
                    return solve_five_attributes_v101(board,time_budget=seconds,max_moves=limit,
                                                      spikes=spikes,avoid_skull=avoid_skull,required_total=required_total,combo_bonus=combo_bonus,cancel_event=cancel_event)
                return solve_path(board,time_budget=seconds,max_moves=limit,
                    beam_width=SOLVER_BEAM_WIDTH,goals=effective,avoid_skull=avoid_skull,spikes=spikes,cancel_event=cancel_event)
            search_started=time.perf_counter()
            cached = (self._prefind_cached_route(board, spikes, goals, avoid_skull, detected_seconds, cap)
                      if getattr(self,"_prefind_result",None) is not None else None)
            if cached is not None:
                path,combos,matched,after = cached
                self._route_debug_update(dict(route_source="short_timer_presearch"))
            else:
                path,combos,matched,after=search(min(budget-reserve,1.5) if master_requested else budget-reserve,cap)
            details=board_goal_metrics(after,effective)
            if not details['all_met']:
                context['failure_kind']='timer_unconfirmed' if detected_seconds is None else 'route_not_found'
                # Inventory impossibility must not be mislabeled a time shortage.
                inventory_ok=not goals[2] or five_attribute_possible_by_count(board)
                remaining=min(reserve,max(0.0,budget-(time.perf_counter()-search_started)))
                if inventory_ok and remaining>0:
                    probe_path,probe_combos,probe_matched,probe_after=search(remaining,wider_cap)
                    probe_ok=board_goal_metrics(probe_after,effective)['all_met']
                    context.update(wider_probe_met=probe_ok,wider_probe_moves=len(probe_path)-1)
                    if probe_ok:
                        if len(probe_path)-1>cap:
                            context['failure_kind']='time_limit_insufficient'
                        else:
                            path,combos,matched,after=probe_path,probe_combos,probe_matched,probe_after
                            context['failure_kind']=None
            if master_requested and not board_goal_metrics(after,effective)['all_met']:
                survival_goals=master_survival_goals(required_total,combo_bonus)
                self._route_combo_fallback=dict(board=tuple(board),spikes=tuple(spikes),requested_goals=requested_master,effective_goals=survival_goals,reason='master_survival')
                cap=self._drag_move_cap(detected_seconds)
                remaining=max(0.0,budget-(time.perf_counter()-search_started))
                if remaining>0:
                    path,combos,matched,after=solve_path(board,time_budget=remaining,max_moves=cap,goals=survival_goals,spikes=spikes,avoid_skull=avoid_skull,cancel_event=cancel_event)
                goals=survival_goals;effective=survival_goals
                context.update(goals=goals,move_cap=cap,master_survival=True,failure_kind=None if board_goal_metrics(after,effective)['all_met'] else 'route_not_found')
            if master_requested and len(path)==1 and board_goal_metrics(after,effective)['all_met'] and cap>=1:
                # A press/release without crossing a cell does not resolve a
                # pre-matched skill board. Equal-colour swaps preserve the proof.
                pairs=[(i,j) for i in range(30) for j in neighbors(i) if board[i]==board[j]]
                if pairs:
                    path=list(min(pairs,key=lambda pair:int(spikes[pair[0]])+int(spikes[pair[1]])))
                    after=board_after_drag(board,path)
                    verified=board_goal_metrics(after,effective)
                    combos,matched=verified['combos'],verified['matched']
            self._route_debug_update(dict(search_budget=budget,search_primary_budget=budget-reserve,
                search_failure_kind=context['failure_kind'],
                wider_probe_met=context.get('wider_probe_met'),wider_probe_moves=context.get('wider_probe_moves')))
            if goals[2]:
                self._v66_log(f"{APP_VERSION} attribute search result: {board_goal_metrics(after, (False,False,True,False))['attr_count']}/5")
            self._v66_log(f"v78 path time cap={move_cap} seconds={detected_seconds} steps={len(path)-1}")
            elapsed = time.perf_counter() - started
            self._v66_log(f"{APP_VERSION} timing: timer_scan={timer_elapsed:.3f}s solver={elapsed-timer_elapsed:.3f}s total_worker={elapsed:.3f}s")
            post_action_result(self,action_epoch, lambda: self._on_solved(
                list(board), list(spikes), path, combos, matched, elapsed, auto_execute, False, goals, avoid_skull))
        except Exception as exc:
            post_action_result(self,action_epoch, lambda msg=str(exc): self._on_solve_error(msg))

    def _route_execution_notice(self,message):
        self.set_status(message)
        board=getattr(self,'board',None)
        if isinstance(board,list) and len(board)==ROWS*COLS:
            self._route_result_notice=dict(board=tuple(board),goals=self.selected_goals(),text=message,button_text='↻ 다시 계산')
            if hasattr(self,'attribute_status_var'):self._set_attribute_status(message,'#ffb2a9')
            else:self._attribute_status=(message,'#ffb2a9')
            toolbar=getattr(self,'toolbar',None)
            if toolbar is not None:toolbar.button.configure(text='↻ 다시 계산')

    def _on_solve_error(self, message: str):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self._route_combo_fallback=None
        self._route_debug_update(dict(phase='solver_error',error=message))
        self.busy = False
        self._route_execution_notice(f"자동 이동 시작 실패: {message}")

    def _on_solved(self, board, spikes, path, combos, matched, elapsed, auto_execute, from_auto, goals=None, avoid_skull=True):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self._route_debug_solution(board,path,elapsed,goals)
        fallback=App._combo_fallback_matches(self,board,goals=goals)
        if fallback:
            self._route_combo_fallback["path"]=tuple(path)
        self.last_path = list(path)
        self.board = list(board)
        self.spikes = list(spikes)
        self._last_solved_signature = tuple(board)
        self._last_solved_spike_signature = tuple(spikes)
        self.last_solved_goals = tuple(goals if goals is not None else self.selected_goals())
        self.last_solved_avoid_skull = bool(avoid_skull)
        self.last_solved_elapsed = float(elapsed)
        for rec in reversed(self.board_history):
            if tuple(rec.get("board", [])) == tuple(board):
                rec["combos"] = int(combos)
                break
        try:
            _atomic_json_write(self.data_paths["board_history"], {"schema_version": 1, "boards": self.board_history})
            self.refresh_board_history_list()
        except Exception:
            pass
        if not auto_execute:
            self.overlay.show_route(self.board_rect, path, self.spikes)
        else:
            # Hold intermediate route drawings until pre-drag board verification.
            # Avoids apparent route changes during solve/countdown/recheck.
            self.overlay.hide()
        self.busy = False
        selected = tuple(goals if goals is not None else self.selected_goals())
        final_board = board_after_drag(list(board), path)
        details = board_goal_metrics(final_board, App._combo_goals(self,board,selected))
        spikes_cleared, spike_crossings = spike_route_metrics(board, spikes, path)
        unmet = [goal_label(i) for i, enabled in enumerate(selected[:7])
                 if enabled and not details["achieved"][i]]
        if not details['combo_met']:
            unmet.append(f"최종 {details['required_total']}콤보 이상")
        if App._combo_condition_hold(self,board) and not master_survival(selected):unmet.append('적 콤보 조건 미확인')
        warning = "" if self.overlay.clickthrough_ok else " · 경고: 클릭 통과 설정을 확인하지 못했어요"
        label = "자동 갱신" if from_auto else "인식 완료"
        result_text = (
            f"{label} · 예상 {combos}콤보 / {matched}개 드롭 매치 · 이동 {len(path)-1}회 · "
            f"빔 서치 {elapsed:.1f}초"
        )
        if details['required_total'] or details['combo_bonus']:
            result_text += f" · 판 {combos} + 가산 {details['combo_bonus']} = 최종 {details['final_combos']}콤보"
            if details['required_total']:result_text += f" / 적 요구 {details['required_total']}콤보 이상"
        result_text += f" · 가시 제거 예상 {spikes_cleared}개 / 통과 예상 {spike_crossings}회"
        if spikes_cleared or spike_crossings:
            result_text += " · 가시 위험 점수는 실제 피해량이 아닌 통과/제거 수 기반 추정"
        result_text += f" · 독 매치 예상 {details['skull_matched']}개"
        if selected[2]:
            missing_attrs = PRIMARY_ATTRIBUTES - details["matched_colors"]
            missing_names = ", ".join(COLOR_INFO[x][0] for x in sorted(missing_attrs))
            result_text += f" · 공격 속성 {details['attr_count']}/5종 매칭 (속성 절대 우선)"
            if missing_names:
                result_text += f" · 미매칭: {missing_names}"
        if avoid_skull:
            result_text += " · 안전 우선: 독 매치 수 최소화"
        if avoid_skull:
            if details["skull_matched"] == 0:
                result_text += " · 독 매치 없는 경로"
            else:
                result_text += " · 경고: 독이 매치될 수 있음 (피해량 미확인)"
        if selected and selected[0]:
            if details["final_combos"] >= 10:
                result_text += f" · 10콤보 경로 발견 ({combos}콤보)"
            else:
                result_text += f" · 10콤보 경로 미발견: 현재 탐색 최고 {combos}콤보 (불가능 확정은 아님)"
        elif any(selected):
            if unmet:
                result_text += " · 선택 목표 미달: " + ", ".join(unmet)
            else:
                result_text += " · 선택한 조건 달성"
        if any(selected[4:7]):
            result_text += f" · 목표 모양 {details['shape_count']}개 매치"
        if selected[3]:
            result_text += f" · 회복 매치 {details['recovery_matched']}개"
        if self.board:
            counts = {code: self.board.count(code) for code in COLOR_ORDER if self.board.count(code)}
            brief = ", ".join(f"{COLOR_INFO[k][0]} {v}" for k, v in counts.items())
            result_text += f" · 판독: {brief}"
        result_text += f". 최적해 보장은 아니에요{warning}. F6=다시 계산 · Shift+F6=새 탐색"
        if selected and selected[0]:
            goal_result = "10콤보 달성" if details["final_combos"] >= 10 else "10콤보 미달"
        else:
            goal_result = "목표 충족" if not unmet else "목표 미달"
        self.last_result_summary = f"결과: {combos}콤보" + (f" · 공격 {details['attr_count']}/5속성" if selected[2] else "") + f" · 가시 제거 {spikes_cleared} · 가시 통과 {spike_crossings} · 독 {details['skull_matched']} · {goal_result}"
        if fallback:
            result_text=("달인 다색 · 6콤보 생존 이동 · " if master_survival(selected) else "이번 턴 콤보 우선 · ")+result_text
            self.last_result_summary=f"결과: {combos}콤보 · "+("달인 다색 생존 이동 (공격 조건 완화)" if master_survival(selected) else "이번 턴 콤보 우선 (5속성 설정 유지)")
        if details['required_total'] or details['combo_bonus']:
            self.last_result_summary+=f" · 최종 {details['final_combos']}콤보 (가산 +{details['combo_bonus']}) / 적 요구 {details['required_total']}"
        self.set_status(result_text)
        if fallback and details["combos"]<1:
            self._route_combo_fallback=None
            self.set_status("콤보 경로 미발견 · 이동하지 않습니다. 5속성 설정은 유지됩니다.")
            return
        # Selected goals are required. Never execute a near miss automatically.
        if auto_execute:
            if unmet:
                reason = " · 드롭 개수 부족으로 5속성 매치 불가" if selected[2] and not five_attribute_possible_by_count(board) else ""
                self.set_status(result_text + reason + " · 목표 미달: 자동 실행 보류")
                text=(f"5속성 경로 미발견 {details['attr_count']}/5 · 이동 안 함"
                      if selected[2] else '목표 경로 미발견 · 이동 안 함')
                if not details['combo_met']:
                    text=f"콤보 조건 미달 · 최종 {details['final_combos']}/{details['required_total']}콤보 · 이동 안 함"
                context=getattr(self,'_route_search_context',None)
                failure_kind=None
                if isinstance(context,dict) and context['board']==tuple(board) and context['goals']==selected:
                    failure_kind=context.get('failure_kind')
                if failure_kind=='time_limit_insufficient':
                    text='제한시간부족 · '+text
                elif failure_kind=='timer_unconfirmed':
                    text='이동시간 미확인 · '+text
                self.set_status(result_text+reason+' · '+text)
                self._route_result_notice=dict(board=tuple(board),goals=selected,text=text)
                self._route_debug_update(dict(phase='goal_unmet',execution_skipped=True,
                    skip_reason=failure_kind or 'selected_goal_not_found',unmet_goals=unmet))
                self._set_attribute_status(text,'#ffb2a9')
                if hasattr(self,'toolbar'):
                    self.toolbar.button.configure(text='↻ 다시 계산 · '+('제한시간부족' if failure_kind=='time_limit_insufficient' else '경로 미발견'))
                if selected[2] and five_attribute_possible_by_count(board):
                    timer=context.get('timer_seconds') if isinstance(context,dict) else None
                    cap=context.get('move_cap') if isinstance(context,dict) else None
                    App._offer_combo_after_limit(self,board,spikes,selected,timer,cap,details['attr_count'])
                return
            self._route_ui_waiting_playback = True
            self.root.after(80, lambda: self._schedule_playback(
                list(path), (tuple(board), tuple(spikes)), source="계산 후 자동 실행"))

    def execute_current_path(self):
        if self.busy:
            self.set_status("경로 계산이 끝난 뒤 실행해 주세요.")
            return
        if not self.board_rect or not self.last_path:
            self.set_status("먼저 퍼즐판 영역을 지정하고 경로를 계산해 주세요.")
            return
        signature = getattr(self, "_last_solved_signature", None)
        self._schedule_playback(list(self.last_path), (tuple(self.board or []), tuple(self.spikes)), source="수동 실행")

    def _schedule_playback(self, path, signature, source="수동 실행"):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self._route_ui_waiting_playback = False
        if self.playback_active:
            self.set_status("이미 자동 실행이 예약되었거나 진행 중이에요. F10으로 취소할 수 있어요.")
            return
        if not self.board_rect or not path:
            self.set_status("실행할 경로가 없어요. 먼저 경로를 계산해 주세요.")
            return
        if not self._check_playback_goal(signature[0] if signature else self.board, path):
            return
        if __import__("sys").platform != "win32":
            self.set_status("자동 마우스 실행은 Windows에서만 지원합니다.")
            return
        self._begin_route_ui_session()
        self.play_cancel.clear()
        self.playback_active = True
        self.drag_in_progress = False
        self.playback_path = list(path)
        self.playback_signature = signature
        self.playback_source = source
        self.playback_countdown_deadline = time.monotonic() + 0.10
        self._playback_countdown_tick()

    def _check_playback_goal(self, board, path):
        """Shared gate for manual/autoplay and the final pre-drag check."""
        if (not board or len(board) != ROWS * COLS or not path
                or any(not isinstance(cell, int) or not 0 <= cell < ROWS * COLS for cell in path)
                or any(b not in neighbors(a) for a, b in zip(path, path[1:]))):
            self._route_execution_notice("실행 보류: 유효하지 않은 보드 또는 경로입니다.")
            return False
        fallback=App._combo_fallback_matches(self,board,path=path)
        goals = self._route_combo_fallback["effective_goals"] if fallback else self.selected_goals()
        if App._combo_condition_hold(self,board) and not master_survival(goals):
            self._route_execution_notice('실행 보류: 적 콤보 조건 미확인')
            return False
        details = board_goal_metrics(board_after_drag(board, path), App._combo_goals(self,board,goals))
        if not details['combo_met']:
            self._route_execution_notice(f"실행 보류: 최종 {details['final_combos']}콤보 / 적 요구 {details['required_total']}콤보 이상")
            return False
        unmet = [goal_label(i) for i, enabled in enumerate(goals[:7]) if enabled and not details['achieved'][i]]
        if fallback and details["combos"]<1:
            self._route_execution_notice("실행 보류: 콤보 우선 경로에서 매치를 확인하지 못했습니다.")
            return False
        if unmet:
            self._route_execution_notice("실행 보류: 선택 목표 미달 · " + ", ".join(unmet))
            return False
        return True

    def _playback_countdown_tick(self):
        if not self.playback_active:
            return
        if self.play_cancel.is_set():
            self._cancel_countdown()
            return
        remaining = self.playback_countdown_deadline - time.monotonic()
        if remaining > 0:
            self.set_status(
                f"{self.playback_source}: {remaining:.1f}초 뒤 드래그 시작 · F10으로 취소")
            self.playback_after = self.root.after(
                min(100, max(20, int(remaining * 1000))), self._playback_countdown_tick
            )
            return
        self.playback_after = None
        # v88: The single authoritative pre-drag verification runs after
        # the control panel has been minimized. The former pre-minimize scan
        # duplicated an expensive 30-cell OCR/classification pass, and could
        # disagree because the window itself was obscuring the game.
        try:
            self.root.iconify()
        except tk.TclError:
            pass
        self._v88_preverify_start = time.perf_counter()
        self.playback_after = self.root.after(150, self._validate_and_start_drag)

    def _validate_and_start_drag(self):
        self.playback_after = None
        if not self.playback_active or self.play_cancel.is_set():
            self._cancel_countdown("자동 실행이 취소됐어요.")
            self.restore_control_panel()
            return
        try:
            current_board = self.capture_and_classify(show_preview=False)
            current_signature = (tuple(current_board), tuple(self.spikes))
            if self.playback_signature and current_signature != self.playback_signature:
                expected_board, expected_spikes = self.playback_signature
                self._v66_log(f"v70 post-minimize mismatch: board_changed={tuple(current_board) != tuple(expected_board)} spike_changed={tuple(self.spikes) != tuple(expected_spikes)}")
                if tuple(current_board) != tuple(expected_board):
                    check_again = self.capture_and_classify(show_preview=False)
                    if tuple(check_again) != tuple(expected_board):
                        diff = [i for i, (a,b) in enumerate(zip(check_again, expected_board)) if a != b]
                        self._v66_log(f"v72 post-minimize aborted: changed_cells={diff}; recheck failed")
                        self._cancel_countdown("자동실행 취소: 퍼즐판 재확인 불일치 " + str([f'{i//COLS+1}행{i%COLS+1}열' for i in diff[:6]]))
                        self.restore_control_panel()
                        return
                    self._v66_log("v72 post-minimize: second scan matched")
                self.spikes = list(expected_spikes)
                self.board = list(expected_board)
        except Exception as exc:
            self._cancel_countdown(f"실행 직전 화면 확인 실패: {exc}")
            self.restore_control_panel()
            return

        if not self._check_playback_goal(self.board, self.playback_path):
            self._cancel_countdown("자동실행 취소: 실행 직전 선택 목표 미달")
            self.restore_control_panel()
            return
        self._v66_log(f"{APP_VERSION} timing: final_board_verify={time.perf_counter()-getattr(self, '_v88_preverify_start', time.perf_counter()):.3f}s")
        rect, path = tuple(self.board_rect), list(self.playback_path)
        # Hide clickable UI so the underlying game can receive the physical drag.
        # The route layer is hidden only briefly while locating/activating the game window,
        # then restored before the first mouse-down and kept visible throughout the drag.
        # Keep the game route button visible while the drag runs.
        # The button is outside the puzzle rectangle and does not intercept input.
        # Keep the transparent route layer mapped during autoplay.  Repeated
        # hide/show caused flashing and unnecessary full-window redraws.
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            cx = int(rect[0] + rect[2] / 2)
            cy = int(rect[1] + rect[3] / 2)
            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            user32.WindowFromPoint.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.WindowFromPoint(wintypes.POINT(cx, cy))
            if hwnd:
                top_hwnd = user32.GetAncestor(hwnd, 2) or hwnd
                user32.SetForegroundWindow(top_hwnd)
        except Exception:
            pass
        previous_combo=getattr(self,'_route_combo_condition',None)
        previous_combo=tuple(previous_combo.get(k) for k in ('required_total','combo_bonus','confirmed')) if isinstance(previous_combo,dict) else None
        cap, seconds = self._restricted_drag_move_cap()
        try:route_operation_seconds(seconds,self._route_playback_goals(self.board,path))
        except RuntimeError as error:
            self._cancel_countdown(str(error));self.restore_control_panel();return
        current_combo=getattr(self,'_route_combo_condition',None)
        current_combo=tuple(current_combo.get(k) for k in ('required_total','combo_bonus','confirmed')) if isinstance(current_combo,dict) else None
        survival=master_survival(self._route_playback_goals(self.board,path))
        condition_changed=(current_combo!=previous_combo)
        if survival and previous_combo is not None and current_combo is not None:
            # The shield needs six combos regardless of the enemy absorption
            # threshold. Only a changed bonus can invalidate this route.
            condition_changed=current_combo[1]!=previous_combo[1]
        if previous_combo is not None and condition_changed:
            self._cancel_countdown('자동실행 취소: 적 콤보 조건 또는 가산이 변경되었습니다. 다시 계산해 주세요.')
            self.restore_control_panel()
            return
        # Buff durations and enemy conditions may change during countdown.
        # The screen freshly read above is authoritative, including bonus expiry.
        if not self._check_playback_goal(self.board,path):
            self._cancel_countdown('자동실행 취소: 현재 적 콤보 조건 미달 또는 인식 미확인')
            self.restore_control_panel()
            return
        if cap is not None and len(path)-1 > cap:
            self._cancel_countdown('자동실행 취소: 현재 이동 제한시간 안에 경로를 완료할 수 없습니다. 다시 계산해 주세요.')
            self.restore_control_panel()
            return
        self._route_debug_before_drag()
        # The click-through transparent overlay should not intercept the drag.
        if not self.overlay.visible:
            self.overlay.show_route(rect, path, self.spikes)
        if not self.overlay.clickthrough_ok:
            self.set_status("주의: 오버레이 클릭 통과를 확인하지 못했습니다. 경로는 표시했지만 자동 드래그가 방해받을 수 있습니다.")
        self._v66_log("F6 mouse playback starting")
        self.drag_in_progress = True
        self.set_status("자동 이동 중 · 마우스 입력 보호 · F10으로 중지")
        threading.Thread(target=self._perform_drag, args=(rect, path), daemon=True).start()

    def _perform_drag(self, rect, path):
        """Protect playback from other mouse input while preserving keyboard F10."""
        error = None
        cancelled = False
        mouse = None
        pressed_at=None;reached=[]
        try:
            left, top, width, height = rect
            cw, ch = width / COLS, height / ROWS
            points = [(int(left + (idx % COLS + 0.5) * cw),
                       int(top + (idx // COLS + 0.5) * ch)) for idx in path]
            if not points:
                raise RuntimeError("경로에 이동 지점이 없습니다.")
            if self.play_cancel.is_set():
                cancelled = True
                return
            mouse = WindowsDragInput()
            mouse.start()  # Never press a game orb if the hook cannot start.
            self._active_drag_input = mouse
            mouse.move(*points[0])
            time.sleep(0.18)
            if self.play_cancel.is_set():
                cancelled = True
                return
            mouse.down()
            pressed_at=time.perf_counter()
            reached.append(dict(cell=path[0],seconds=0.0))
            time.sleep(0.06)
            # Small intermediate cursor moves make the drag less likely to be
            # interpreted as a series of jumps by the Android game container.
            for cell_index,((x0, y0), (x1, y1)) in enumerate(zip(points, points[1:]),1):
                distance = max(abs(x1 - x0), abs(y1 - y0))
                steps = max(2, int(math.ceil(distance / 10.0)))
                for step in range(1, steps + 1):
                    if self.play_cancel.is_set():
                        cancelled = True
                        break
                    t = step / steps
                    target = (int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t))
                    mouse.move(*target)
                    time.sleep(0.007)
                if cancelled:
                    break
                reached.append(dict(cell=path[cell_index],seconds=round(time.perf_counter()-pressed_at,4)))
            if not cancelled:
                mouse.move(*points[-1])
                time.sleep(0.12)  # let MuMu process the last orb location before release
        except Exception as exc:
            cancelled = self.play_cancel.is_set()
            if not cancelled:
                error = str(exc)
        finally:
            if mouse is not None:
                try:
                    mouse.close()
                    time.sleep(0.035)
                except Exception as exc:
                    error = error or str(exc)
            self._active_drag_input = None
            self._route_debug_update(dict(input_reached=reached,
                input_seconds=round(time.perf_counter()-pressed_at,4) if pressed_at is not None else 0.0))
            try:
                self._playback_finish_queue.put((error, cancelled))
            except Exception:
                pass

    def _stage_crystal_icon(self,crop):
        """Cache one draft image; create no DB entry until its title is confirmed."""
        import io
        stream=io.BytesIO();crop.save(stream,format='PNG')
        data=stream.getvalue();digest=hashlib.sha256(data).hexdigest()
        with self._crystal_db_lock:
            db=self._load_crystal_db()
            candidates=list(db['crystals'])
            seen={str(row['id']) for row in candidates}
            for group in self._load_crystal_ledger().get('farming_groups',{}).values():
                for row in group['materials']:
                    if row['id'] not in seen and row.get('image_file'):
                        candidates.append(row);seen.add(row['id'])
            probe=crystal_art_signatures(crop)
            ranks=[]
            for entry in candidates:
                path=self.data_paths['icons_dir']/str(entry.get('image_file') or '')
                if not path.is_file(): continue
                if hashlib.sha256(path.read_bytes()).hexdigest()==digest:
                    return dict(id=str(entry['id']),image_file=entry['image_file'],
                                confirmed=bool(known_crystal_name(entry)),crystal_name=known_crystal_name(entry))
                with Image.open(path) as saved:
                    refs=crystal_art_signatures(saved)
                score=min(float(np.abs(a-b).mean()) for a,b in zip(probe,refs))
                ranks.append((score,str(entry['id']),entry))
            ranks.sort(key=lambda row:(row[0],row[1]))
            if ranks and ranks[0][0]<10 and (len(ranks)==1 or ranks[1][0]-ranks[0][0]>=3.5):
                entry=ranks[0][2]
                return dict(id=str(entry['id']),image_file=entry['image_file'],
                            confirmed=bool(known_crystal_name(entry)),crystal_name=known_crystal_name(entry))
            filename=f'draft_{digest}.png'
            path=self.data_paths['icons_dir']/filename
            if not path.exists(): path.write_bytes(data)
            return dict(id='draft:'+digest,image_file=filename,confirmed=False,crystal_name='')

    def _schedule_auto_crystal_title(self, binding, nonce, title_roi=None):
        mapping=getattr(self,'_crystal_click_map',None)
        if not mapping or mapping['nonce']!=nonce: return
        self._auto_title_serial=getattr(self,'_auto_title_serial',0)+1
        ticket=dict(serial=self._auto_title_serial,binding=binding,nonce=nonce,
                    attempt=0,deadline=time.monotonic()+18,title_roi=title_roi)
        self.root.after(550,lambda t=ticket:self._start_auto_crystal_title(t))

    def _start_auto_crystal_title(self,ticket):
        if ticket['serial']!=getattr(self,'_auto_title_serial',0): return
        mapping=self._crystal_click_map
        if (not mapping or mapping['nonce']!=ticket['nonce'] or self._crystal_click_stopped
                or self.busy or self.playback_active or getattr(self,'_manual_reward_busy',False)):
            self._restore_auto_title_panel()
            return
        if time.monotonic()>ticket['deadline']:
            self._restore_auto_title_panel()
            return
        if self.ocr_busy:
            self.root.after(400,lambda t=ticket:self._start_auto_crystal_title(t))
            return
        try:
            info=find_emulator_window_info()
            if (not info or int(info['hwnd'])!=mapping['hwnd']
                    or tuple(info['rect'])!=mapping['game_rect'] or not self._route_button_game_in_foreground()):
                raise ValueError('게임 창 상태가 바뀌었습니다.')
            panel=getattr(self.toolbar,'crystal_window',None)
            if panel is not None and panel.winfo_exists() and panel.state()=='normal':
                self.toolbar.minimize_crystal_window()
                self.root.update_idletasks()
                # Let the window manager/DWM finish the minimize transition
                # before capturing the game title. Do not block Tk with sleep.
                self.root.after(200,lambda t=ticket:self._start_auto_crystal_title(t))
                return
            title_roi=ticket.get('title_roi')
            if title_roi is None:
                wx,wy,ww,wh=info['rect']
                search_roi=(wx+round(ww*.25),wy,round(ww*.5),round(wh*.36))
                with mss.mss() as capture:
                    sx,sy,sw,sh=search_roi
                    shot=capture.grab(dict(left=sx,top=sy,width=sw,height=sh))
                    search=Image.frombytes('RGB',shot.size,shot.rgb)
                located=locate_material_modal_title(search)
                if located:
                    box,_=located;x0,y0,x1,y1=box
                    title_roi=(sx+x0,sy+y0,x1-x0,y1-y0)
            x,y,w,h=title_roi or mapping['title_roi']
            with mss.mss() as capture:
                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                title=Image.frombytes('RGB',shot.size,shot.rgb)
            context=dict(hwnd=int(info['hwnd']),game_rect=tuple(info['rect']),title_roi=(x,y,w,h))
            ledger=self._load_crystal_ledger();database=self._load_crystal_db()
            self._material_title_known_names=tuple(dict.fromkeys(
                [g['name'] for g in ledger.get('farming_groups',{}).values()]
                +[known_crystal_name(row) for row in database.get('crystals',[]) if known_crystal_name(row)]))
            self.ocr_busy=True
            self._auto_title_inflight=ticket['serial']
            self.set_status('크리스탈 상세 이름 자동 인식 중…')
            threading.Thread(target=self._auto_crystal_title_worker,args=(ticket,title,context),daemon=True).start()
        except Exception as exc:
            self._restore_auto_title_panel()
            self.set_status('크리스탈 이름 자동 인식 보류: '+str(exc))

    def _auto_crystal_title_worker(self,ticket,title,context):
        name=None;error=None
        try:
            tokens=self._read_material_detail_tokens(title)
            name=crystal_detail_name(tokens)
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
        self._crystal_click_queue.put(('auto_title',ticket,name,error,context))

    def _finish_auto_crystal_title(self,ticket,name,error,context):
        if getattr(self,'_auto_title_inflight',None)==ticket['serial']:
            self._auto_title_inflight=None
            self.ocr_busy=False
        if ticket['serial']!=getattr(self,'_auto_title_serial',0):
            self._restore_auto_title_panel()
            return
        mapping=getattr(self,'_crystal_click_map',None)
        if ticket.get('nonce') and (not mapping or mapping['nonce']!=ticket['nonce']):
            self._restore_auto_title_panel()
            return
        self.last_ocr_debug=('[크리스탈 상세 자동 인식]\n'
            +f"제목 영역: {context.get('title_roi')}\n"
            +f"인식 이름: {name or '미확인'} / 시도 {ticket['attempt']+1}\n"
            +getattr(self,'_material_title_notes','')+'\n'+str(error or ''))
        self._v66_log(f"material detail read: name={name!r}, roi={context.get('title_roi')}, error={error!r}")
        if name:
            self._material_detail_context=context
            self._on_crystal_detail_done(name,ticket['binding'],background=True)
            self._restore_auto_title_panel()
        elif ticket['attempt']<2 and time.monotonic()<ticket['deadline']:
            retry=dict(ticket,attempt=ticket['attempt']+1)
            self.root.after(650,lambda t=retry:self._start_auto_crystal_title(t))
        else:
            self._restore_auto_title_panel()
            self.set_status('크리스탈 상세 제목을 확정하지 못했습니다. 상세창을 유지하고 F7로 다시 읽어 주세요.'+
                            (' '+error if error else ''))

    def _restore_auto_title_panel(self):
        # Automatic title registration never reopens or raises the management
        # window. It stays minimised; explicit F7/list opening presents it again.
        self._auto_title_hidden=False

    def _fit_material_panel(self,source=None):
        """Explicit F7 opens the full list; no title-avoidance size constraint."""
        panel=getattr(self.toolbar,'crystal_window',None)
        if panel is None or not panel.winfo_exists(): return
        try:
            panel.update_idletasks()
            sw,sh=panel.winfo_screenwidth(),panel.winfo_screenheight()
            content=getattr(self.toolbar,'_crystal_content_frame',None)
            width=min(max(760,content.winfo_reqwidth()+36) if content else 760,sw-20)
            height=min(max(320,content.winfo_reqheight()+35) if content else panel.winfo_height(),sh-60)
            x=max(0,min(panel.winfo_x(),sw-width))
            y=max(0,min(panel.winfo_y(),sh-height-30))
            panel.geometry(f'{width}x{height}+{x}+{y}')
            canvas=getattr(self.toolbar,'crystal_canvas',None)
            if canvas is not None: canvas.yview_moveto(0)
        except tk.TclError:
            pass

    def _crystal_click_init(self):
        self._crystal_click_queue=queue.SimpleQueue()
        self._crystal_click_map=None
        self._crystal_click_ready=None
        self._crystal_pending_click=None
        self._crystal_context_busy=False
        self._crystal_context_next=0.0
        self._crystal_rearm_after=0.0
        self._crystal_click_epoch=0
        self._crystal_click_stopped=False
        self._crystal_click_after=None
        self._crystal_observer=None
        self._detail_watch_busy=False
        self._detail_watch_next=0.0
        self._detail_watch_candidate=None
        self._detail_watch_last_attempt=None
        self._auto_title_serial=0
        self._auto_title_inflight=None
        self._auto_title_hidden=False
        if sys.platform=='win32':
            try:
                self._crystal_observer=WindowsMaterialClickObserver(self._record_crystal_click)
                self._crystal_observer.start()
            except Exception as exc:
                self._v66_log(f'material click observer unavailable: {exc}')
                self._crystal_observer=None
        self._crystal_click_after=self.root.after(100,self._crystal_click_tick)

    def _arm_crystal_clicks(self,key,targets,source):
        if not source: return
        ledger=self._load_crystal_ledger(); group=ledger['farming_groups'][key]
        self._material_name_snapshot=dict(key=key,revision=group['revision'],at=time.monotonic(),
            hwnd=source.get('hwnd'),game_rect=tuple(source.get('game_rect',())))
        if not source.get('title_image'): return
        title=source['title_image']
        box=source.get('title_box',(round(title.width*.3),0,round(title.width*.7),title.height))
        expected=np.asarray(title.crop(box).convert('L'))<160
        if not expected.size or np.count_nonzero(expected)<4: return
        normalized=source['image'].resize((735,290),Image.Resampling.LANCZOS)
        slots=[]
        for index,target in enumerate(targets):
            if 'crop_box' not in target: return
            region=tuple(target['crop_box'])
            signature=np.asarray(normalized.crop(region).resize((32,40)).convert('RGB'),dtype=np.int16)
            slots.append(dict(index=index,id=group['materials'][index]['id'],box=region,signature=signature,
                              confirmed=bool(group['materials'][index].get('confirmed',False))))
        self._crystal_click_epoch+=1
        self._crystal_click_map=dict(source,key=key,revision=group['revision'],slots=slots,
                                     title_box=box,title_mask=expected,nonce=uuid.uuid4().hex)
        self._crystal_click_ready=None
        self._crystal_pending_click=None
        self._crystal_context_next=0.0
        self._detail_watch_next=0.0
        self._detail_watch_candidate=None
        self._detail_watch_last_attempt=None

    def _record_crystal_click(self,x,y,owner):
        # Native hook thread: constant-time filtering and queue only, no Tk or I/O.
        lobby=getattr(self,'_farming_lobby_click_context',None)
        if lobby and owner==lobby['hwnd']:
            left,top,width,height=lobby['rect']
            fx,fy=(x-left)/width,(y-top)/height
            if (.26<=fx<=.49 or .51<=fx<=.745) and .707<=fy<=.83:
                self._crystal_click_queue.put(('lobby_start',lobby))
        if getattr(getattr(self,'toolbar',None),'_crystal_presentation_active',False):
            self._crystal_click_queue.put(('presentation_click',owner))
        state=self._crystal_click_ready
        if self.ocr_busy or self.busy or self.playback_active: return
        mapping=getattr(self,'_crystal_click_map',None)
        if not state or time.monotonic()-state['at']>.9 or owner!=state['hwnd']:
            if mapping and owner==mapping['hwnd']:
                slots=mapping.get('slots',[])
                snapshot=getattr(self,'_material_name_snapshot',None)
                if len(slots)==1 and snapshot and 0<=time.monotonic()-snapshot['at']<=300:
                    left,top,width,height=mapping['roi'];x0,y0,x1,y1=slots[0]['box']
                    if left+x0*width/735<=x<left+x1*width/735 and top+y0*height/290<=y<top+y1*height/290:
                        self._crystal_click_queue.put(('single_click',mapping['nonce']))
                        return
                self._crystal_click_queue.put(('other_click',))
            return
        left,top,width,height=state['roi']
        for slot in state['slots']:
            x0,y0,x1,y1=slot['box']
            if left+x0*width/735<=x<left+x1*width/735 and top+y0*height/290<=y<top+y1*height/290:
                self._crystal_click_ready=None
                self._crystal_click_epoch+=1
                self._crystal_rearm_after=time.monotonic()+.6
                binding=(state['key'],slot['index'],slot['id'],state['revision'])
                self._crystal_click_queue.put(('click',binding,state['nonce'],time.monotonic()))
                return
        self._crystal_click_queue.put(('other_click',))
        self._crystal_click_ready=None
        self._crystal_click_epoch+=1

    def _crystal_context_worker(self,source,epoch):
        ready=None
        try:
            info=find_emulator_window_info()
            if not info or tuple(info['rect'])!=source['game_rect']: raise ValueError('game moved')
            if time.monotonic()<self._crystal_rearm_after: raise ValueError('click settling')
            if sys.platform=='win32' and ctypes.windll.user32.GetAsyncKeyState(1)&0x8000:
                raise ValueError('mouse down')
            with mss.mss() as capture:
                x,y,w,h=source['roi']
                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                image=Image.frombytes('RGB',shot.size,shot.rgb).resize((735,290),Image.Resampling.LANCZOS)
                x,y,w,h=source['title_roi']
                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                title=Image.frombytes('RGB',shot.size,shot.rgb)
            observed=np.asarray(title.crop(source['title_box']).convert('L'))<160
            if observed.shape!=source['title_mask'].shape or float(np.mean(observed!=source['title_mask']))>.05:
                raise ValueError('owner header changed')
            slots=[]
            for slot in source['slots']:
                observed=np.asarray(image.crop(slot['box']).resize((32,40)).convert('RGB'),dtype=np.int16)
                if float(np.abs(observed-slot['signature']).mean())<6.0: slots.append(slot)
            if slots: ready=dict(source,slots=slots,at=time.monotonic())
        except Exception:
            pass
        self._crystal_click_queue.put(('context',epoch,ready))

    def _crystal_detail_watch_worker(self,source):
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=getattr(self,'_stop_epoch',0)
        candidate=None
        try:
            info=find_emulator_window_info()
            if (not info or int(info['hwnd'])!=source['hwnd']
                    or tuple(info['rect'])!=source['game_rect']):
                raise ValueError('game changed')
            wx,wy,ww,wh=source['game_rect']
            roi=(wx+round(ww*.25),wy,round(ww*.5),round(wh*.36))
            with mss.mss() as capture:
                x,y,w,h=roi
                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                image=Image.frombytes('RGB',shot.size,shot.rgb)
            located=locate_material_modal_title(image)
            if located:
                box,title=located
                fingerprint=material_title_fingerprint(title)
                parent=material_title_fingerprint(source['title_image'])
                if fingerprint and fingerprint!=parent:
                    x0,y0,x1,y1=box
                    candidate=dict(fingerprint=fingerprint,title_roi=(roi[0]+x0,roi[1]+y0,x1-x0,y1-y0))
        except Exception:
            pass
        self._crystal_click_queue.put(('detail_probe',source['nonce'],candidate))

    def _accept_crystal_detail_probe(self,nonce,candidate):
        self._detail_watch_busy=False
        mapping=getattr(self,'_crystal_click_map',None)
        if not mapping or mapping['nonce']!=nonce: return
        if candidate is None:
            self._detail_watch_candidate=None
            return
        fingerprint=candidate['fingerprint']
        if fingerprint==getattr(self,'_detail_watch_last_attempt',None): return
        previous=getattr(self,'_detail_watch_candidate',None)
        self._detail_watch_candidate=(fingerprint,candidate['title_roi'])
        if previous!=self._detail_watch_candidate: return
        if (self.ocr_busy or self.busy or self.playback_active
                or getattr(self,'_auto_title_inflight',None) is not None): return
        pending=getattr(self,'_crystal_pending_click',None)
        binding=pending[0] if pending and 0<=time.monotonic()-pending[1]<=300 else None
        if binding and (binding[0]!=mapping['key'] or binding[3]!=mapping['revision']): binding=None
        # No click signal: only one material can be linked, and its explicit OCR
        # name must still match its owner in single_owner_material_binding.
        if not binding and len(mapping['slots'])!=1: return
        self._detail_watch_last_attempt=fingerprint
        self._schedule_auto_crystal_title(binding,nonce,title_roi=candidate['title_roi'])

    def _drain_crystal_clicks(self):
        while True:
            try: message=self._crystal_click_queue.get_nowait()
            except queue.Empty: break
            if message[0]=='click':
                _,binding,nonce,at=message
                if self._crystal_click_map and nonce==self._crystal_click_map['nonce']:
                    self._crystal_pending_click=(binding,at)
                    tree=getattr(getattr(self,'toolbar',None),'candidate_tree',None)
                    if tree is not None and tree.exists(str(binding[1])):
                        tree.selection_set(str(binding[1]));tree.see(str(binding[1]))
                    self.set_status(f"클릭한 재료 {binding[1]+1} · 상세 이름을 자동으로 확인합니다.")
                    self._schedule_auto_crystal_title(binding,nonce)
            elif message[0]=='lobby_start':
                if message[1]==getattr(self,'_farming_lobby_click_context',None):
                    self._farming_close_lobby_stats()
            elif message[0]=='presentation_click':
                self.toolbar._end_crystal_presentation(message[1])
            elif message[0]=='single_click':
                _,nonce=message
                self._schedule_auto_crystal_title(None,nonce)
            elif message[0]=='auto_title':
                _,ticket,name,error,context=message
                self._finish_auto_crystal_title(ticket,name,error,context)
            elif message[0]=='detail_probe':
                self._accept_crystal_detail_probe(message[1],message[2])
            elif message[0]=='other_click':
                self._crystal_pending_click=None
                self._auto_title_serial=getattr(self,'_auto_title_serial',0)+1
                self._restore_auto_title_panel()
            else:
                _,epoch,ready=message
                self._crystal_context_busy=False
                if epoch==self._crystal_click_epoch: self._crystal_click_ready=ready

    def _crystal_click_tick(self):
        self._crystal_click_after=None
        if self._crystal_click_stopped: return
        try:
            if getattr(self,'_all_actions_stopped',False) is True:return
            self._drain_crystal_clicks()
            mapping=self._crystal_click_map
            snapshot=getattr(self,'_material_name_snapshot',None)
            watch_allowed=(mapping and snapshot and 0<=time.monotonic()-snapshot['at']<=300
                and any(not slot.get('confirmed',False) for slot in mapping['slots']))
            if (watch_allowed and not getattr(self,'_detail_watch_busy',False) and not self.ocr_busy
                    and not self.busy and not self.playback_active and not getattr(self,'_manual_reward_busy',False)
                    and time.monotonic()>=getattr(self,'_detail_watch_next',0)
                    and self._route_button_game_in_foreground()):
                self._detail_watch_busy=True
                self._detail_watch_next=time.monotonic()+.8
                threading.Thread(target=self._crystal_detail_watch_worker,args=(mapping,),daemon=True).start()
            if (self._crystal_click_map and not self._crystal_context_busy and not self.ocr_busy
                    and not self.busy and not self.playback_active and time.monotonic()>=self._crystal_context_next):
                self._crystal_context_busy=True
                self._crystal_context_next=time.monotonic()+.12
                threading.Thread(target=self._crystal_context_worker,
                    args=(self._crystal_click_map,self._crystal_click_epoch),daemon=True).start()
        finally:
            if not self._crystal_click_stopped and self.root.winfo_exists():
                self._crystal_click_after=self.root.after(100,self._crystal_click_tick)

    def _on_crystal_detail_done(self,name,binding,error=None,background=False):
        if getattr(self,'_all_actions_stopped',False) is True:return
        self.ocr_busy=False
        if error or not name:
            self.set_status('크리스탈 이름 판독 보류: '+str(error or '제목을 읽지 못했습니다.'))
            return
        if not binding:
            binding=single_owner_material_binding(self._load_crystal_ledger(),name,
                getattr(self,'_material_name_snapshot',None),
                getattr(self,'_material_detail_context',None) or getattr(self,'_material_capture_context',None),time.monotonic())
        if not binding:
            self._farming_sync_views(self._load_crystal_ledger())
            if not background: self.toolbar.show_crystal_window()
            self.toolbar.refresh()
            self.set_status(f'{name} 확인 · 연결할 재료를 확정하지 못했습니다. 돌파 화면에서 F7로 읽은 뒤 게임에서 해당 재료를 클릭해 주세요.')
            return
        try:
            with self._crystal_db_lock:
                ledger=self._load_crystal_ledger();db=self._load_crystal_db()
                key,index,cid,revision=binding
                group=ledger.get('farming_groups',{}).get(key,{})
                if not 0<=index<len(group.get('materials',[])): raise ValueError('재료 목록이 바뀌었습니다.')
                row=group['materials'][index]
                entry=next((r for r in db['crystals'] if str(r['id'])==cid),None)
                image_file=(entry or {}).get('image_file') or row.get('image_file')
                if not image_file or not (self.data_paths['icons_dir']/image_file).is_file():
                    image_file=row.get('image_file')
                if not image_file or not (self.data_paths['icons_dir']/image_file).is_file():
                    raise ValueError('클릭한 재료 이미지가 없습니다. 돌파 화면에서 F7로 다시 읽어 주세요.')
                for saved in db['crystals']:
                    if (crystal_name_key(saved.get('crystal_name') or saved.get('label',''))==crystal_name_key(name)
                            and not (self.data_paths['icons_dir']/str(saved.get('image_file') or '')).is_file()):
                        saved['image_file']=''
                # A known material name may only reuse a compatible existing image.
                matches=[r for r in db['crystals'] if crystal_name_key(r.get('crystal_name') or r.get('label',''))==crystal_name_key(name)]
                if (len(matches)==1 and str(matches[0]['id'])!=cid
                        and matches[0].get('image_file')
                        and (self.data_paths['icons_dir']/matches[0]['image_file']).is_file()):
                    with Image.open(self.data_paths['icons_dir']/image_file) as a, Image.open(self.data_paths['icons_dir']/matches[0]['image_file']) as b:
                        score=min(float(np.abs(x-y).mean()) for x,y in zip(crystal_art_signatures(a),crystal_art_signatures(b)))
                    if score>=13: raise ValueError('같은 이름의 기존 이미지와 일치하지 않아 중복 등록을 보류했습니다.')
                updated,database,canonical=name_crystal_material(ledger,db,binding,name,image_file)
                _atomic_json_write(self.data_paths['database'],database)
                _atomic_json_write(self.data_paths['ledger'],updated)
            if canonical!=cid:
                if str((getattr(self,'active_target',None) or {}).get('id'))==cid:
                    self.active_target=updated.get('active_target')
                if str(getattr(self,'resource_crystal_id',None))==cid:
                    self.resource_crystal_id=canonical
                if self.user_settings.get('target_crystal_id')==cid:
                    self.user_settings['target_crystal_id']=canonical
                    self.save_user_settings()
            self._farming_sync_views(updated)
            snapshot=getattr(self,'_material_name_snapshot',None)
            if snapshot and snapshot['key']==key:
                snapshot['revision']=updated['farming_groups'][key]['revision']
            self._crystal_pending_click=None
            if self._crystal_click_map and self._crystal_click_map['key']==key:
                mapping=dict(self._crystal_click_map)
                mapping['revision']=updated['farming_groups'][key]['revision']
                mapping['slots']=[dict(slot,id=updated['farming_groups'][key]['materials'][slot['index']]['id'],
                                      confirmed=bool(updated['farming_groups'][key]['materials'][slot['index']].get('confirmed',False)))
                                  for slot in mapping['slots']]
                self._crystal_click_map=mapping
                self._crystal_click_epoch+=1;self._crystal_click_ready=None
            self._farming_attempts=0
            if not background: self.toolbar.show_crystal_window()
            self.toolbar.refresh()
            self.set_status(f"{updated['farming_groups'][key]['name']} · 재료 {index+1}: {crystal_material_name(name)} 저장 · 기존 이미지 재사용")
        except (OSError,ValueError,KeyError) as exc:
            self.set_status(f'크리스탈 이름 등록 보류: {exc}')

    def _background_init(self):
        self._background_running=False;self._background_stopped=False
        self._background_cancel=threading.Event();self._background_queue=queue.SimpleQueue()
        self._background_device=None;self._background_probe_busy=False;self._background_probe_next=0.0
        self._background_context=None;self._background_valid_at=0.0;self._background_pressed=False;self._background_pressed_at=0.0;self._background_raise_at=0.0
        self._background_header_cache=None;self._background_entry_frame=None;self._background_last_error=None
        self._background_button_win=tk.Toplevel(self.root)
        self._background_button_win.withdraw();self._background_button_win.overrideredirect(True)
        self._background_button=tk.Button(self._background_button_win,text='▶ 재료 파밍',command=self._background_entry_clicked,
            bg='#137c71',fg='white',activebackground='#239b8d',font=('Segoe UI',11,'bold'),relief='solid',borderwidth=1,padx=18,pady=9)
        self._background_button.pack(fill='both',expand=True)
        self._party_button=tk.Button(self._background_button_win,text='파티 설정',command=self.show_party_settings,
            bg='#294a60',fg='white',activebackground='#3d647d',font=('Segoe UI',11,'bold'),relief='solid',borderwidth=1,padx=18,pady=9)
        self._party_button.pack(fill='both',expand=True)
        self._team_party_context=None;self._team_party_valid_at=0.0
        self._team_party_win=None
        self._team_party_button=None
        for button in (self._background_button,self._party_button):
            button.bind('<ButtonPress-1>',lambda _e:(setattr(self,'_background_pressed',True),setattr(self,'_background_pressed_at',time.monotonic())),add='+')
            button.bind('<ButtonRelease-1>',lambda _e:self.root.after(120,lambda:setattr(self,'_background_pressed',False)),add='+')
        self._awakening_register_context=None;self._awakening_register_at=0.0;self._awakening_register_busy=False
        self._awakening_register_win=tk.Toplevel(self.root)
        self._awakening_register_win.withdraw();self._awakening_register_win.overrideredirect(True)
        self._awakening_register_button=tk.Button(self._awakening_register_win,text='각성 스킬 등록',command=self._awakening_register_clicked,
            bg='#294a60',fg='white',activebackground='#3d647d',font=('Segoe UI',11,'bold'),relief='solid',borderwidth=1)
        self._awakening_register_button.pack(fill='x')
        self._awakening_register_status=tk.StringVar(value='눌러서 이름·각성 확인')
        tk.Label(self._awakening_register_win,textvariable=self._awakening_register_status,bg='#eef4f6',fg='#294a60',
                 font=('Segoe UI',9),wraplength=190,padx=4,pady=4).pack(fill='both',expand=True)
        self._background_after=self.root.after(450,self._background_tick)

    def _background_header(self,frame):
        header=frame.crop((155,46,485,103))
        fingerprint,views=background_heading_views(header)
        cached=getattr(self,'_background_header_cache',None)
        if fingerprint and cached and cached[0]==fingerprint:return cached[1]
        readings=[self._recognize_title_line(view) for view in views]
        result=None
        if len(readings)==2 and min(score for _,score in readings)>=.82:
            values=[_compact_ocr_text(text) for text,_ in readings]
            if values[0]==values[1] and values[0] in ('잠입확인','연전확인','결과','각성스킬상세','크리처상세','팀','근원트리','홈','모험','던전','육성','크리스탈획득던전','출현아이템','중간돌파확인','한계돌파확인','최종한계돌파확인'):result=values[0]
        if fingerprint and result:self._background_header_cache=(fingerprint,result)
        return result

    def _update_root_tree_context(self, frame, heading=None):
        """Keep tree descendants suppressed until a positively identified exit."""
        if heading is None:
            try:heading=self._background_header(frame)
            except Exception:heading=None
        active=getattr(self,'_root_tree_active',False) is True
        if heading=='근원트리':active=True
        elif heading in ('팀','잠입확인','연전확인','홈','모험','던전'):active=False
        elif active and heading not in ('크리처상세','각성스킬상세','결과'):
            if background_board(frame) is not None:active=False
        self._root_tree_active=active
        return active

    def _hide_tree_rewards(self):
        self._farming_result_visible=False
        self._farming_lobby_visible=False
        self._manual_reward_busy=False
        self.reward_poll_busy=False
        self._farming_positive=0
        self._farming_displayed_session=None
        window=getattr(self,'_farming_stats',None)
        if window is not None:window.withdraw()

    def _background_stage(self,frame,tokens):
        values=[]
        for token in tokens:
            if token.score>=.85 and token.cy<115:
                found=re.search(r'(\d{1,2})\s*[-－–ー]\s*(\d{1,2})',token.text)
                if found:values.append('-'.join(found.groups()))
        if len(set(values))==1:return values[0]
        crop=frame.crop((720,45,910,105))
        _,views=material_title_line_views(crop)
        readings=[]
        for view in views:
            text,score=self._recognize_title_line(view)
            found=re.search(r'(\d{1,2})\s*[-－–ー]\s*(\d{1,2})',text)
            readings.append('-'.join(found.groups()) if found and score>=.85 else None)
        if len(readings)==2 and readings[0] and readings[0]==readings[1]:return readings[0]
        raise RuntimeError('스테이지 번호를 확정하지 못했습니다. 자동 재도전을 시작하지 않았습니다.')

    @staticmethod
    def _background_action(tokens,labels):
        labels={_compact_ocr_text(label) for label in labels}
        matches=[t for t in tokens if t.score>=.85 and _compact_ocr_text(t.text) in labels and t.cy>600]
        if len(matches)!=1:raise RuntimeError('진행 버튼을 확정하지 못해 자동 입력을 보류했습니다.')
        return matches[0].cx,matches[0].cy

    def _read_awakening_owner(self,frame):
        owner_box,_=awakening_dialog_geometry(frame)
        crop=frame.crop(owner_box)
        box,views=material_title_line_views(crop)
        if len(views)<2:raise ValueError('이름 글자 영역을 찾지 못했습니다. 게임의 각성 상세 화면인지 확인해 주세요.')
        settings=getattr(self,'_awakening_register_settings',None)
        if not isinstance(settings,dict):settings=getattr(self,'user_settings',{})
        votes=[];observations=[]
        for view in views:
            text,score=self._recognize_title_line(view)
            text=str(text).strip();observations.append((text,round(float(score),4)))
            numbers=awakening_owner_numbers(settings,text)
            if re.search('[가-힣]',text) and score>=(.85 if len(numbers)==1 else .90):votes.append((text,numbers))
        self._awakening_owner_diagnostics=dict(box=box,screen_box=owner_box,readings=observations)
        if len(votes)==2 and _compact_ocr_text(votes[0][0])==_compact_ocr_text(votes[1][0]):
            if len(votes[0][1])>1:raise ValueError(votes[0][0]+' · 같은 이름에 여러 몬스터 번호가 연결되어 있습니다. 파티 정보를 확인해 주세요.')
            return votes[0][0]
        observed=' / '.join((text or '미인식')+f' ({score:.2f})' for text,score in observations)
        raise ValueError('이름 판독 불일치 · '+observed+' · 파티창의 게임 각성 등록/다시 읽기로 재시도할 수 있습니다.')

    def _awakening_detail_tokens(self,frame):
        # Read headings with detection, but read every body line directly so small
        # numbers/second lines cannot silently disappear from full-screen detection.
        panels=awakening_panel_regions(frame)
        settings=getattr(self,'_awakening_register_settings',None)
        if not isinstance(settings,dict):settings=getattr(self,'user_settings',{})
        reused={top:confirmed_awakening_icon(party_image_png(frame.crop((447,top+20,491,top+65))),settings) for top,_ in panels}
        if all(reused.values()) and self._background_header(frame)=='각성스킬상세':
            all_tokens=[OCRToken('각성 스킬 상세',170,55,650,90,.99)]
        else:all_tokens=self._run_ocr_tokens(frame)
        tokens=[t for t in all_tokens if t.cy<110 or any(top+12<t.cy<top+70 for top,_ in panels)]
        heading=''.join(t.text for t in sorted(tokens,key=lambda t:t.x1) if t.score>=.85 and 40<t.cy<110 and 150<t.x1<700)
        if _compact_ocr_text(heading)!='각성스킬상세' and self._background_header(frame)=='각성스킬상세':
            tokens=[t for t in tokens if not (40<t.cy<110 and 150<t.x1<700)]
            tokens.append(OCRToken('각성 스킬 상세',170,55,650,90,.85))
        for top,bottom in panels:
            if reused.get(top):
                entry=reused[top]
                tokens=[t for t in tokens if not (top+12<t.cy<top+70 and 493<=t.x1<1160)]
                tokens.extend([OCRToken(entry['name'],515,top+25,1000,top+55,.99),
                               OCRToken(entry['description'],495,top+90,1150,top+120,.99)])
                continue
            heading_tokens=[t for t in tokens if top+12<t.cy<top+70 and 493<=t.x1<1160]
            if not heading_tokens or any(t.score<.88 for t in heading_tokens):
                header=frame.crop((495,top+12,1165,top+70))
                local=self._run_ocr_tokens(header.resize((header.width*3,header.height*3),Image.Resampling.LANCZOS))
                if local and all(t.score>=.88 for t in local):
                    tokens=[t for t in tokens if not (top+12<t.cy<top+70 and 493<=t.x1<1160)]
                    tokens.extend(OCRToken(t.text,495+t.x1/3,top+12+t.y1/3,495+t.x2/3,top+12+t.y2/3,t.score) for t in local)
            heading_tokens=[t for t in tokens if top+12<t.cy<top+70 and 493<=t.x1<1160]
            if not heading_tokens or any(t.score<.88 for t in heading_tokens):
                # White title glyphs on a grey bar need the opposite polarity
                # from black body text. Exclude the icon and bar ornament.
                header=frame.crop((505,top+20,1100,top+64)).convert('RGB')
                pixels=np.asarray(header,dtype=np.int16)
                glyph=(pixels.min(axis=2)>190)&(pixels.max(axis=2)-pixels.min(axis=2)<55)
                yy,xx=np.where(glyph)
                if len(xx)>=12:
                    left=max(0,int(xx.min())-3);right=min(header.width,int(xx.max())+4)
                    y0=max(0,int(yy.min())-3);y1=min(header.height,int(yy.max())+4)
                    original=header.crop((left,y0,right,y1))
                    binary=Image.fromarray(np.where(glyph[y0:y1,left:right],0,255).astype('uint8')).convert('RGB')
                    votes=[]
                    for view in (original,binary):
                        padded=Image.new('RGB',(view.width+16,view.height+12),'white');padded.paste(view,(8,6))
                        value,confidence=self._recognize_title_line(padded.resize((padded.width*3,padded.height*3),Image.Resampling.LANCZOS))
                        votes.append((value.strip(),confidence))
                    if (all(v and c>=.88 for v,c in votes)
                            and _compact_ocr_text(votes[0][0])==_compact_ocr_text(votes[1][0])):
                        tokens=[t for t in tokens if not (top+12<t.cy<top+70 and 493<=t.x1<1160)]
                        tokens.append(OCRToken(votes[0][0],505+left,top+20+y0,505+right,top+20+y1,min(c for _,c in votes)))
            box=(495,top+75,1165,bottom-8);body=frame.crop(box)
            rgb=np.asarray(body.convert('RGB')).astype('int16')
            ink=(rgb.max(axis=2)-rgb.min(axis=2)<60)&(rgb.mean(axis=2)<170)
            rows=ink.sum(axis=1)>=3
            for i in range(1,len(rows)-1):
                if rows[i-1] and rows[i+1]:rows[i]=True
            diff=np.diff(np.r_[False,rows,False].astype('int8'))
            for y0,y1 in zip(np.where(diff==1)[0],np.where(diff==-1)[0]):
                if y1-y0<7:continue
                _,xs=np.where(ink[y0:y1])
                if not len(xs):continue
                left=max(0,int(xs.min())-5);right=min(body.width,int(xs.max())+6)
                line=body.crop((left,max(0,y0-3),right,min(body.height,y1+3)))
                # Padding keeps ascenders and decimals away from the model's edge.
                padded=Image.new('RGB',(line.width+16,line.height+12),'white');padded.paste(line,(8,6))
                text,score=self._recognize_title_line(padded.resize((padded.width*2,padded.height*2),Image.Resampling.LANCZOS))
                if score<.85:
                    import cv2
                    gray=cv2.normalize(np.asarray(padded.convert('L')),None,0,255,cv2.NORM_MINMAX)
                    retry=Image.fromarray(gray).convert('RGB').resize((padded.width*3,padded.height*3),Image.Resampling.LANCZOS)
                    alternative,confidence=self._recognize_title_line(retry)
                    if confidence>score:text,score=alternative,confidence
                if score<.85:
                    detected=[t for t in all_tokens if t.score>=.85 and 493<=t.x1<1160
                              and top+75+y0-4<=t.cy<=top+75+y1+4]
                    detected.sort(key=lambda t:t.x1)
                    # Reject a partial detector box: the complete ink line must
                    # be covered, including numbers at either end.
                    if (detected and min(t.x1 for t in detected)<=495+left+12
                            and max(t.x2 for t in detected)>=495+right-12):
                        text=' '.join(t.text.strip() for t in detected)
                        score=min(t.score for t in detected)
                tokens.append(OCRToken(text,495+left,top+75+y0,495+right,top+75+y1,score))
        notes=[]
        for index,(top,bottom) in enumerate(panels,1):
            title=[t for t in tokens if 493<=t.x1<1160 and top+12<t.cy<top+70]
            body=[t for t in tokens if 493<=t.x1<1160 and top+75<t.cy<bottom-8]
            for label,items,threshold in (('이름',title,.88),('설명',body,.85)):
                if not items or any(t.score<threshold for t in items):
                    notes.append(f'{index}번 {label}: '+(' / '.join(f'{t.text} ({t.score:.2f})' for t in items) or '미인식'))
        self._awakening_panel_diagnostics=notes
        return tokens

    def _awakening_screen_update(self,context,valid,at,fingerprint=None,number=None,icons=None):
        if context!=self._background_context:return
        self._awakening_register_context=context if valid else None
        self._awakening_register_at=at if valid else 0.0
        previous=getattr(self,'_awakening_display_fingerprint',None)
        current=fingerprint if valid else None
        previous_needed=getattr(self,'_awakening_register_needed',True)
        self._awakening_register_needed=(awakening_registration_needed(getattr(self,'user_settings',{}),number,icons) if valid else True)
        completed=getattr(self,'_awakening_completed_fingerprints',{})
        if current and not number and isinstance(completed,dict) and completed.get(current):
            self._awakening_register_needed=awakening_registration_needed(getattr(self,'user_settings',{}),completed[current],icons)
        previous_number=getattr(self,'_awakening_display_number',None)
        self._awakening_display_number=number if valid else None
        if previous==current and previous_number==self._awakening_display_number and previous_needed==self._awakening_register_needed:return
        self._awakening_display_fingerprint=current
        status='눌러서 현재 캐릭터 각성 등록'
        if valid and number:
            profile=next((p for p in self.user_settings.get('background_party_profiles',[]) if str(p.get('number'))==str(number)),None)
            if profile:
                profile=background_profile_merge(profile,self.user_settings)
                status=f"{profile.get('name','캐릭터')} (No.{number}) · "+('✓ 각성 확인 완료' if profile.get('awakening_registered') else '각성 등록 필요')
            else:status=f'No.{number} · 눌러서 각성 등록'
        self._awakening_display_status=status
        if not getattr(self,'_awakening_register_busy',False):
            self._awakening_register_button.configure(text='각성 스킬 등록',state='normal')
            self._awakening_register_status.set(status)

    def _awakening_screen_identity(self,frame):
        # Only the static monster number/name header; artwork and EXP animation
        # must not reset a completion message on the same character.
        patch=frame.crop((824,194,1548,289))
        fingerprint=hashlib.sha256(patch.tobytes()+frame.crop((1402,613,1546,657)).tobytes()).hexdigest()
        cached=getattr(self,'_awakening_screen_identity_cache',None)
        if cached and cached[0]==fingerprint and (cached[1] is not None or time.monotonic()<getattr(self,'_awakening_identity_retry_at',0)):return cached
        crop=frame.crop((824,194,1008,240));votes=[]
        try:
            for scale in (2,3):
                text,score=self._recognize_title_line(crop.resize((crop.width*scale,crop.height*scale),Image.Resampling.LANCZOS))
                match=re.search(r'(?:No|N0)[.．]?\s*(\d{1,4})(?!\d)',text,re.I)
                votes.append(match[1] if match and score>=.85 else None)
        except Exception:votes=[]
        number=votes[0] if len(votes)==2 and votes[0] is not None and votes[0]==votes[1] else None
        self._awakening_screen_identity_cache=(fingerprint,number)
        self._awakening_identity_retry_at=time.monotonic()+3
        return fingerprint,number

    def _awakening_register_result(self,success,text):
        cancel=getattr(self,'_awakening_register_cancel',None)
        if cancel is not None:cancel.set()
        self._awakening_register_busy=False;self._awakening_register_request=None
        self._awakening_register_button.configure(text='다시 등록' if not success else '각성 스킬 등록',state='normal')
        changed=(getattr(self,'_awakening_request_fingerprint',None)!=getattr(self,'_awakening_display_fingerprint',None))
        if success:
            fingerprint=getattr(self,'_awakening_request_fingerprint',None)
            completed=getattr(self,'_awakening_completed_fingerprints',{})
            registered_number=getattr(self,'_awakening_request_registered_number',None)
            if fingerprint and registered_number:completed[fingerprint]=registered_number
            self._awakening_completed_fingerprints=completed
            if not changed:
                self._awakening_register_needed=False
                window=getattr(self,'_awakening_register_win',None)
                if window is not None:window.withdraw()
        if changed:self._awakening_register_button.configure(text='각성 스킬 등록',state='normal')
        self._awakening_register_status.set(getattr(self,'_awakening_display_status','눌러서 현재 캐릭터 각성 등록') if changed else ('✓ 등록 완료' if success else '등록 실패')+' · '+text.split('\n')[0])
        self.show_party_settings(automatic=False);self._party_scan_progress(text)
        parent=self._party_settings_window
        if success:messagebox.showinfo('각성 등록 완료',text,parent=parent)
        else:messagebox.showwarning('각성 등록 실패',text,parent=parent)

    def _awakening_register_timeout(self,request):
        if getattr(self,'_awakening_register_request',None)!=request or not self._awakening_register_busy:return
        self._awakening_register_result(False,'20초 안에 판독을 마치지 못했습니다. 저장하지 않았습니다.\n파티창의 게임 각성 등록/다시 읽기로 재시도할 수 있습니다.')

    def _awakening_register_clicked(self,from_party=False):
        resume_actions(self)
        if any(getattr(self,key,False) is True for key in ('_background_running','playback_active','busy')):
            self._party_scan_progress('파티 읽기 또는 자동 플레이가 진행 중입니다. 완료하거나 F10으로 중지한 뒤 각성을 등록하세요.');return
        if self._awakening_register_busy:
            self._party_scan_progress('각성 등록 진행 중 · 잠시 기다려 주세요. (최대 20초)');return
        context=self._awakening_register_context
        if from_party and not isinstance(context,tuple):
            context=getattr(self,'_awakening_register_last_context',None)
            if not isinstance(context,tuple):
                info=find_emulator_window_info()
                context=(int(info['hwnd']),tuple(info['rect'])) if info else None
        if not isinstance(context,tuple):
            self._awakening_register_result(False,'크리처 상세 화면을 확인하지 못했습니다.\n게임에서 크리처 상세창을 연 뒤 다시 눌러주세요.');return
        # A slow passive OCR used to expire this timestamp and silently ignore clicks.
        # Always acknowledge a visible button; the worker checks a fresh screenshot.
        self._awakening_register_last_context=context
        self._awakening_request_fingerprint=getattr(self,'_awakening_display_fingerprint',None)
        self._awakening_request_registered_number=None
        self._awakening_register_settings=json.loads(json.dumps(self.user_settings)) if isinstance(self.user_settings,dict) else {}
        self._awakening_register_cancel=threading.Event()
        self._awakening_register_busy=True
        request=uuid.uuid4().hex;self._awakening_register_request=request
        self._awakening_register_deadline=time.monotonic()+18.0
        self._awakening_register_button.configure(text='등록 중…',state='disabled')
        self._awakening_register_status.set('OCR 대기·이름·각성 설명 확인 중… (최대 20초)')
        self._party_scan_progress('각성 등록 · OCR 대기·이름·각성 확인 중… (최대 20초)')
        self.root.after(20000,lambda:self._awakening_register_timeout(request))
        threading.Thread(target=self._awakening_register_worker,args=(context,request),daemon=True).start()

    def _awakening_detail_tap(self,context,device,opening,frame=None):
        assert_action_active(self)
        cancel=getattr(self,'_awakening_register_cancel',None)
        if cancel is not None and cancel.is_set():raise RuntimeError('각성 등록을 중지했습니다.')
        px,py=(1424,635) if opening else awakening_dialog_geometry(frame)[1] if frame is not None else (1204,150)
        if device is not None:
            device.tap(px,py)
            return
        info=find_emulator_window_info()
        if not info or (int(info['hwnd']),tuple(info['rect']))!=context or not self._material_button_game_visible(info):
            raise ValueError('게임이 가려져 각성 상세를 열지 않았습니다. 크리처 상세를 앞에 두고 다시 눌러주세요.')
        x,y,w,h=context[1];mouse=WindowsDragInput()
        try:
            mouse.start();mouse.move(round(x+px*w/1600),round(y+py*h/940))
            mouse.down();time.sleep(.06)
        finally:mouse.close()

    def _awakening_register_worker(self,context,request=None):
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=getattr(self,'_stop_epoch',0)
        priority_owner=(threading.get_ident(),request)
        self._ocr_priority_owner=priority_owner
        device=None
        try:
            deadline=time.monotonic()+6
            while self._background_probe_busy:
                if time.monotonic()>deadline:raise ValueError('각성 읽기 대기시간 초과 · 다시 등록해 주세요.')
                time.sleep(.1)
            settings=getattr(self,'_awakening_register_settings',{})
            if not isinstance(settings,dict):settings={}
            cancel=getattr(self,'_awakening_register_cancel',None) or threading.Event()
            adb_error=''
            if settings.get('background_adb_path') or settings.get('background_adb_serial'):
                try:
                    exe=find_background_adb(settings.get('background_adb_path',''))
                    device=BackgroundADB(exe,settings.get('background_adb_serial',''),cancel,capture_settings=settings)
                    device.connect()
                except Exception as error:
                    adb_error=str(error)
                    if device is not None:device.close_capture()
                    device=None
            self._awakening_register_capture_info=dict(request=request,method='ADB' if device is not None else 'desktop',configuration=(settings.get('background_adb_path',''),settings.get('background_adb_serial','')))
            readings=[];owners=[];complete=[];problems=[];opened_detail=False
            for reading_index in range(3):
                if request is not None and getattr(self,'_awakening_register_request',None)!=request:return
                self._background_queue.put(('awakening_register_progress',request,f'이름·각성 확인 중 · {reading_index+1}/2' if reading_index<2 else '판독 차이 재확인 중 · 마지막 1회'))
                if device is not None:
                    frame=device.capture()
                else:
                    info=find_emulator_window_info()
                    if not info or (int(info['hwnd']),tuple(info['rect']))!=context or not self._material_button_game_visible(info):
                        raise ValueError('게임 화면을 읽지 못했습니다. 파밍 연결 설정을 확인하거나 게임의 각성 상세를 앞에 두고 파티창에서 다시 읽어주세요.'+(' ADB: '+adb_error if adb_error else ''))
                    x,y,w,h=context[1]
                    with mss.mss() as capture:
                        shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                        frame=Image.frombytes('RGB',shot.size,shot.rgb).resize((1600,940),Image.Resampling.LANCZOS)
                heading=self._background_header(frame)
                if reading_index==0 and heading=='크리처상세':
                    self._awakening_detail_tap(context,device,True);opened_detail=True
                    for _ in range(3):
                        if cancel.wait(.35):return
                        if device is not None:frame=device.capture()
                        else:
                            info=find_emulator_window_info()
                            if not info or (int(info['hwnd']),tuple(info['rect']))!=context or not self._material_button_game_visible(info):
                                raise ValueError('게임이 가려져 각성 읽기를 중지했습니다.')
                            x,y,w,h=context[1]
                            with mss.mss() as capture:
                                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                                frame=Image.frombytes('RGB',shot.size,shot.rgb).resize((1600,940),Image.Resampling.LANCZOS)
                        heading=self._background_header(frame)
                        if heading=='각성스킬상세':break
                if heading!='각성스킬상세':raise ValueError('크리처 상세 화면에서 각성 스킬 등록을 눌러주세요. 각성 상세가 열리지 않아 저장하지 않았습니다.')
                owner=self._read_awakening_owner(frame)
                self._background_queue.put(('awakening_register_progress',request,f'이름 확인: {owner} · 각성 설명 {reading_index+1}/2'))
                rows=awakening_detail_rows(self._awakening_detail_tokens(frame),frame)
                expected=len(awakening_panel_regions(frame))
                problem=''
                if len(rows)!=expected:
                    names=' / '.join(row['name'] for row in rows) or '없음'
                    problem=f'각성 {len(rows)}/{expected}개 판독 · 이름 또는 설명의 글자가 불확실합니다.\n읽힌 각성: {names}'
                    notes=getattr(self,'_awakening_panel_diagnostics',[])
                    if notes:problem+='\n'+'\n'.join(notes[:3])
                owners.append(owner);readings.append(rows);complete.append(not problem);problems.append(problem)
                for previous in range(reading_index):
                    if (complete[previous] and complete[reading_index]
                            and _compact_ocr_text(owners[previous])==_compact_ocr_text(owner)
                            and not awakening_readings_error(readings[previous],rows)):
                        if opened_detail:self._awakening_detail_tap(context,device,False,frame)
                        self._background_queue.put(('awakening_register',context,[owners[previous],owner],[readings[previous],rows],request))
                        return
            valid=[i for i,ok in enumerate(complete) if ok]
            if len(valid)>=2:
                one,two=valid[-2:]
                problem=('캐릭터 이름 재확인 불일치 · '+owners[one]+' / '+owners[two] if _compact_ocr_text(owners[one])!=_compact_ocr_text(owners[two])
                         else awakening_readings_error(readings[one],readings[two]))
            else:problem=next((item for item in reversed(problems) if item),'각성 판독 결과 확인 필요')
            raise ValueError(problem+'\n3회 안에 일치하는 판독을 얻지 못했습니다. 파티창에서 다시 읽을 수 있습니다.')
        except Exception as error:self._background_queue.put(('awakening_register_error',str(error),request))
        finally:
            if device is not None:device.close_capture()
            if getattr(self,'_ocr_priority_owner',None)==priority_owner:self._ocr_priority_owner=None

    def _background_probe(self,context):
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=getattr(self,'_stop_epoch',0);ACTION_LOCAL.passive_display=True
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=getattr(self,'_stop_epoch',0)
        valid=False;frame=None;awakening_attempt=0
        try:
            _,(x,y,w,h)=context
            with mss.mss() as capture:
                shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                image=Image.frombytes('RGB',shot.size,shot.rgb)
            frame=image.resize((1600,940),Image.Resampling.LANCZOS)
            heading=self._background_header(frame)
            if self._update_root_tree_context(frame,heading):self._farming_queue.put(('root_tree',))
            valid=heading=='잠입확인'
            self._background_queue.put(('team_screen',context,heading=='팀',time.monotonic()))
            fingerprint,number=self._awakening_screen_identity(frame) if heading=='크리처상세' else (None,None)
            icons=([party_image_png(frame.crop((1402+50*i,613,1446+50*i,657))) for i in range(3)] if heading=='크리처상세' else None)
            self._background_queue.put(('awakening_screen',context,heading=='크리처상세',time.monotonic(),fingerprint,number,icons))
            if heading!='각성스킬상세':self._awakening_detail_seen=None;self._awakening_detail_attempts={}
            if heading=='각성스킬상세' and getattr(self,'_all_actions_stopped',False) is not True:
                fingerprint=hashlib.sha256(frame.crop((425,200,1175,860)).tobytes()).hexdigest()
                attempts=getattr(self,'_awakening_detail_attempts',{})
                if attempts.get(fingerprint,0)<3 and fingerprint!=getattr(self,'_awakening_detail_seen',None):
                    self._awakening_detail_attempts={fingerprint:attempts.get(fingerprint,0)+1}
                    awakening_attempt=attempts.get(fingerprint,0)+1
                    if awakening_attempt==1:self._background_queue.put(('status','각성 상세 자동 읽기 · 이름과 설명을 두 번 확인 중'))
                    first=awakening_detail_rows(self._awakening_detail_tokens(frame),frame)
                    if first:
                        with mss.mss() as capture:
                            shot=capture.grab(dict(left=x,top=y,width=w,height=h))
                            repeat=Image.frombytes('RGB',shot.size,shot.rgb).resize((1600,940),Image.Resampling.LANCZOS)
                        second=awakening_detail_rows(self._awakening_detail_tokens(repeat),repeat)
                        self._background_queue.put(('awakening_detail',first,second))
                        agreed=[a for a in first if any(a['name']==b['name'] and _compact_ocr_text(a['description'])==_compact_ocr_text(b['description']) for b in second)]
                        if len(first)==len(second)==len(agreed)==len(awakening_panel_regions(frame)):self._awakening_detail_seen=fingerprint
                        elif awakening_attempt>=3 and not agreed:self._background_queue.put(('status','각성 상세 판독 보류 · 확인 필요 · 상세창을 다시 열어주세요.'))
                    elif awakening_attempt>=3:self._background_queue.put(('status','각성 상세 판독 보류 · 확인 필요 · 상세창을 다시 열어주세요.'))
        except Exception:
            if awakening_attempt>=3:self._background_queue.put(('status','각성 상세 판독 보류 · 확인 필요 · 상세창을 다시 열어주세요.'))
        self._background_queue.put(('entry',context,valid,time.monotonic(),frame if valid else None))

    def _background_tick(self):
        self._background_after=None
        if self._background_stopped:return
        try:
            while True:
                try:message=self._background_queue.get_nowait()
                except queue.Empty:break
                if getattr(self,'_all_actions_stopped',False) is True and message[0] not in ('done','entry','team_screen','awakening_screen'):continue
                if message[0] in ('party','party_member','board','credited') and getattr(self,'_background_cancel',threading.Event()).is_set():continue
                if message[0]=='entry':
                    _,context,valid,at,frame=message;self._background_probe_busy=False
                    if context==self._background_context and valid:
                        self._background_valid_at=at;self._background_entry_frame=frame
                    elif context==self._background_context:self._background_valid_at=0.0
                elif message[0]=='team_screen':
                    if message[1]==self._background_context:
                        self._team_party_context=message[1] if message[2] else None
                        self._team_party_valid_at=message[3] if message[2] else 0.0
                elif message[0]=='awakening_screen':
                    self._awakening_screen_update(*message[1:])
                elif message[0]=='awakening_register_progress':
                    if message[1]==getattr(self,'_awakening_register_request',None):
                        self._awakening_register_status.set(message[2]);self._party_scan_progress(message[2])
                elif message[0] in ('awakening_register','awakening_register_error'):
                    request=message[-1]
                    if request is not None and request!=getattr(self,'_awakening_register_request',None):continue
                    try:
                        if message[0]=='awakening_register_error':raise ValueError(message[1])
                        _,registration_context,owners,readings,_=message
                        capture_info=getattr(self,'_awakening_register_capture_info',{})
                        if capture_info.get('request')==request and capture_info.get('method')=='ADB':
                            configuration=(self.user_settings.get('background_adb_path',''),self.user_settings.get('background_adb_serial',''))
                            if capture_info.get('configuration')!=configuration:raise ValueError('연결 기기가 변경되어 저장하지 않았습니다. 다시 읽어주세요.')
                        elif registration_context!=self._background_context:
                            info=find_emulator_window_info()
                            if not info or (int(info['hwnd']),tuple(info['rect']))!=registration_context:
                                raise ValueError('게임 화면이 변경되어 등록을 보류했습니다. 다시 읽어주세요.')
                        number=register_character_awakenings(self.user_settings,*owners,*readings)
                        self._awakening_request_registered_number=number
                        self.save_user_settings();self.show_party_settings(automatic=False)
                        profiles=self.user_settings.get('background_party_profiles',[])
                        slot=next((i for i,p in enumerate(profiles) if str(p.get('number'))==number),None)
                        self._party_settings_refresh()
                        if slot is not None:self._party_open_awakening(slot)
                        names='\n'.join('✓ '+row['name'] for row in readings[0])
                        result=f'{owners[0]} (No.{number}) · 각성 등록·확정 완료\n\n{names}\n\n해당 캐릭터 아래 각성 아이콘을 눌러 저장 내용을 확인할 수 있습니다.'
                        self._awakening_register_result(True,result)
                    except (ValueError,OSError) as error:self._awakening_register_result(False,str(error))
                elif message[0]=='awakening_detail':
                    accepted=apply_awakening_detail_readings(self.user_settings,message[1],message[2])
                    if accepted:
                        self.save_user_settings()
                        window=getattr(self,'_party_settings_window',None)
                        if window is not None and window.winfo_exists():self._party_settings_refresh()
                        self._party_scan_progress('각성 상세 자동 읽기: '+', '.join(accepted)+' · 설명을 확인해 주세요. 확인한 기존 정정은 유지합니다.')
                elif message[0]=='status_template_frame':self._party_template_editor(message[1])
                elif message[0]=='status':self._party_scan_progress(message[1])
                elif message[0]=='party_member':
                    _,slot,profile=message
                    profiles=self.user_settings.setdefault('background_party_profiles',[])
                    while len(profiles)<6:profiles.append(dict(slot=len(profiles)))
                    if profile.get('observed_attributes') and profile.get('attributes_read_version')==2:
                        self.user_settings.setdefault('monster_attribute_observations',{})[str(profile['number'])]=profile['observed_attributes']
                    profile=background_party_update(profiles[slot],profile)
                    profiles[slot]=profile
                    record_party_description_review(self.user_settings,profile)
                    number=str(profile.get('number') or '')
                    if number and profile.get('name_confirmed') and re.search('[가-힣]',profile.get('name','')):
                        self.user_settings.setdefault('monster_korean_names',{})[number]=profile['name']
                    self.save_user_settings()
                    window=getattr(self,'_party_settings_window',None)
                    if window is not None and window.winfo_exists():self._party_settings_refresh()
                elif message[0]=='party':
                    for profile in message[1]:
                        if profile.get('observed_attributes') and profile.get('attributes_read_version')==2:
                            self.user_settings.setdefault('monster_attribute_observations',{})[str(profile['number'])]=profile['observed_attributes']
                        number=str(profile.get('number') or '')
                        if number and profile.get('name_confirmed') and re.search('[가-힣]',profile.get('name','')):
                            self.user_settings.setdefault('monster_korean_names',{})[number]=profile['name']
                    old=self.user_settings.get('background_party_profiles',[])
                    self.user_settings['background_party_profiles']=[background_party_update(old[i] if i<len(old) else {},item) for i,item in enumerate(message[1])];self.save_user_settings()
                    window=getattr(self,'_party_settings_window',None)
                    if window is not None and window.winfo_exists():self._party_settings_refresh()
                elif message[0]=='board':
                    _,board,spikes,crop=message
                    self.board=list(board);self.spikes=list(spikes);self.update_board_view()
                elif message[0]=='credited':
                    self._farming_sync_views(self._load_crystal_ledger());self.toolbar.refresh()
                elif message[0]=='done':
                    self._background_running=False;self._background_device=None
                    self._manual_reward_busy=False
                    self._farming_attempts=3;self._farming_battle_seen=False
                    self._background_valid_at=0.0;self._background_entry_frame=None
                    self._background_probe_next=time.monotonic()+1.5
                    self._background_last_error=message[1] if message[1].startswith('재료 파밍 중지 ·') else None
                    self._background_button.configure(text='▶ 재료 파밍')
                    self._party_scan_progress(message[1]);self.toolbar.restore_after_playback();self.toolbar.refresh()
                    if message[1].endswith('목표 재료 모두 충족 · 파밍 완료'):self._background_complete_notification(message[1])
            if self._background_pressed:
                held=sys.platform=='win32' and bool(ctypes.windll.user32.GetAsyncKeyState(1)&0x8000)
                if held or time.monotonic()-getattr(self,'_background_pressed_at',0)<.75:return
                self._background_pressed=False
            info=find_emulator_window_info()
            visible=self._material_button_game_visible(info) if info else False
            context=(int(info['hwnd']),tuple(info['rect'])) if visible else None
            if context!=self._background_context:
                self._background_context=context;self._background_valid_at=0.0
            blocked=(self._background_running or self.busy or self.playback_active or self.ocr_busy
                     or getattr(self,'_manual_reward_busy',False) or getattr(self,'_route_ui_active',False))
            now=time.monotonic()
            if not context or blocked or now-self._background_valid_at>2.5:
                self._background_button_win.withdraw()
            else:
                x,y,w,h=context[1]
                self._background_button_win.geometry(f'{round(w*.123)}x{round(h*.12)}+{round(x+w*.862)}+{round(y+h*.30)}')
                if self._background_button_win.state()=='withdrawn' or now-getattr(self,'_background_raise_at',0)>=1.0:
                    show_game_overlay_without_focus(self._background_button_win)
                    self._background_raise_at=now
            awakening_window=getattr(self,'_awakening_register_win',None)
            if awakening_window is not None:
                if (not context or blocked or context!=self._awakening_register_context
                        or (not self._awakening_register_busy and (now-self._awakening_register_at>4 or not getattr(self,'_awakening_register_needed',True)))):
                    awakening_window.withdraw()
                else:
                    x,y,w,h=context[1]
                    awakening_window.geometry(f'{round(w*.125)}x{round(h*.070)}+{round(x+w*.445)}+{round(y+h*.055)}')
                    show_game_overlay_without_focus(awakening_window)
            if context and not blocked and not getattr(self,'_awakening_register_busy',False) and not self._background_probe_busy and now>=self._background_probe_next:
                self._background_probe_busy=True;self._background_probe_next=now+1.2
                threading.Thread(target=self._background_probe,args=(context,),daemon=True).start()
        except (tk.TclError,OSError,ValueError,AttributeError):
            self._background_button_win.withdraw()
            team_window=getattr(self,'_team_party_win',None)
            if team_window is not None:team_window.withdraw()
        finally:
            if not self._background_stopped and self.root.winfo_exists():self._background_after=self.root.after(250,self._background_tick)

    def _background_entry_clicked(self):
        self._background_last_error=None
        self._background_button.configure(text='▶ 재료 파밍')
        self.start_background_farming()

    def show_party_settings(self,automatic=True):
        action_epoch=getattr(self,'_stop_epoch',0)
        if automatic:resume_actions(self)
        existing=getattr(self,'_party_settings_window',None)
        if existing is not None and existing.winfo_exists():
            self._party_bring_forward(existing)
            if automatic:existing.after_idle(lambda:action_request_current(self,action_epoch) and existing.winfo_exists() and self._party_scan_start(automatic=True))
            return
        win=tk.Toplevel(self.root);self._party_settings_window=win
        win.title('파티 설정 · 정보 확인 및 정정');win.geometry(f'1040x{min(900,win.winfo_screenheight()-70)}');win.minsize(900,680)
        self._party_bring_forward(win)
        body=ttk.Frame(win,padding=12);body.pack(fill='both',expand=True)
        selected=tk.IntVar(value=0);self._party_scan_selected=selected
        top=ttk.Frame(body);top.pack(fill='x',pady=(0,8))
        ttk.Label(top,text='파티 정보',font=('Segoe UI',15,'bold')).pack(side='left',padx=(0,20))
        ttk.Button(top,text='게임 각성 등록/다시 읽기',command=lambda:self._awakening_register_clicked(from_party=True)).pack(side='left',padx=(0,8))
        ttk.Button(top,text='자동 등록 다시 확인',command=lambda:self._party_scan_start(automatic=True)).pack(side='left')
        ttk.Button(top,text='전투 상태 아이콘 등록',command=self._party_status_template).pack(side='right')
        ttk.Label(body,text='현재 파티를 자동 확인하고 필요한 캐릭터만 등록합니다. 캐릭터를 선택하면 정보를 확인·정정할 수 있습니다.').pack(anchor='w')
        members=ttk.Frame(body);members.pack(fill='x',pady=6)
        name_vars=[tk.StringVar() for _ in range(6)];level_vars=[tk.StringVar() for _ in range(6)];attribute_vars=[tk.StringVar() for _ in range(6)]
        portrait_labels=[];awake_labels=[];name_labels=[];captions=[];awake_frames=[]
        for i in range(6):
            members.columnconfigure(i,weight=1,uniform='party')
            card=ttk.Frame(members,padding=4);card.grid(row=0,column=i,sticky='nsew')
            art=ttk.Frame(card,height=178);art.pack(fill='x');art.pack_propagate(False)
            label=ttk.Label(art,anchor='center');label.pack(fill='both',expand=True);portrait_labels.append(label)
            caption=ttk.Frame(card,height=54);captions.append(caption);caption.pack(fill='x',pady=(4,0));caption.pack_propagate(False)
            name=ttk.Label(caption,textvariable=name_vars[i],wraplength=145,anchor='center',justify='center');name.pack(fill='both',expand=True);name_labels.append(name)
            ttk.Radiobutton(card,textvariable=level_vars[i],variable=selected,value=i).pack(anchor='center')
            ttk.Label(card,textvariable=attribute_vars[i],anchor='center',foreground='#46657b').pack(fill='x')
            icons=ttk.Frame(card,width=98,height=30);awake_frames.append(icons);icons.pack(anchor='center',pady=(4,0));icons.grid_propagate(False);labels=[]
            for j in range(3):
                item=ttk.Label(icons,anchor='center');item.grid(row=0,column=j,padx=1);labels.append(item)
                item.bind('<Button-1>',lambda e,slot=i:select_awakening(slot))
            awake_labels.append(labels)
            label.bind('<Button-1>',lambda e,slot=i:select_awakening(slot) if current_card_has_footer(slot) and e.y>=e.widget.winfo_height()*.78 else selected.set(slot));name.bind('<Button-1>',lambda e,slot=i:selected.set(slot))
        def fit_names(event=None):
            from tkinter import font as tkfont
            width=max(90,members.winfo_width()//6-14)
            font=tkfont.nametofont('TkDefaultFont')
            lines=max([math.ceil(font.measure(var.get())/width)+1 for var in name_vars]+[2])
            for label,caption in zip(name_labels,captions):label.configure(wraplength=width);caption.configure(height=max(54,lines*font.metrics('linespace')))
        members.bind('<Configure>',fit_names)
        fields={key:tk.StringVar() for key in ('number','name','level','cooldown','priority','attributes_ko')}
        row=ttk.Frame(body);row.pack(fill='x',pady=5)
        for key,label,width in [('number','몬스터 번호',10),('name','한글 이름',28),('level','레벨',6),('cooldown','스킬 턴',6),('priority','동점 우선순위',8),('attributes_ko','속성 (DB·정정)',14)]:
            col=ttk.Frame(row);col.pack(side='left',padx=4);ttk.Label(col,text=label).pack(anchor='w');ttk.Entry(col,textvariable=fields[key],width=width).pack()
        source=tk.StringVar();source_row=ttk.Frame(body);source_row.pack(fill='x',pady=3)
        ttk.Label(source_row,textvariable=source,wraplength=800).pack(side='left')
        ttk.Button(source_row,text='출처·일본어 원문',command=lambda:show_original()).pack(side='right')
        # Footer is outside tabs so it never scrolls or disappears.
        footer=ttk.Frame(body);footer.pack(side='bottom',fill='x',pady=(8,0))
        status=tk.StringVar();self._party_scan_status=status
        ttk.Label(footer,textvariable=status,wraplength=980,foreground='#245f73').pack(anchor='w')
        actions=ttk.Frame(footer);actions.pack(fill='x',pady=(5,0))
        tabs=ttk.Notebook(body);tabs.pack(fill='both',expand=True,pady=5)
        page_canvases={}
        def scroll_page(title):
            page=ttk.Frame(tabs);tabs.add(page,text=title)
            canvas=tk.Canvas(page,highlightthickness=0);bar=ttk.Scrollbar(page,orient='vertical',command=canvas.yview)
            canvas.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
            content=ttk.Frame(canvas,padding=8);item=canvas.create_window((0,0),window=content,anchor='nw')
            content.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
            page_canvases[str(page)]=canvas
            return page,content
        skill_page,skill_body=scroll_page('스킬')
        skill_image=ttk.Label(skill_body,anchor='w');skill_image.pack(anchor='w')
        active_row=ttk.Frame(skill_body);active_row.pack(fill='x',pady=8)
        active_summary=tk.StringVar(value='효과 확인 필요')
        ttk.Label(active_row,textvariable=active_summary,wraplength=570,justify='left').pack(side='left',fill='x',expand=True)
        active_checks=ttk.Frame(active_row);active_checks.pack(side='right')
        enabled=tk.BooleanVar(value=True);ttk.Checkbutton(active_checks,text='자동 사용',variable=enabled,command=lambda:save()).pack(anchor='w')
        verified=tk.BooleanVar();ttk.Checkbutton(active_checks,text='스킬 효과가 맞음',variable=verified,command=lambda:save()).pack(anchor='w')
        active_toggle=ttk.Button(skill_body,text='효과 정정 ▾');active_toggle.pack(anchor='e')
        active_editor=ttk.Frame(skill_body)
        skill=ScrolledText(active_editor,height=4,wrap='word');skill.pack(fill='x',pady=4)
        def toggle_active():
            if active_editor.winfo_manager():active_editor.pack_forget();active_toggle.configure(text='효과 정정 ▾')
            else:active_editor.pack(fill='x',after=active_toggle,pady=8);active_toggle.configure(text='효과 정정 ▴')
        active_toggle.configure(command=toggle_active)
        awake_page=skill_page;awake_body=ttk.Frame(skill_body)
        ttk.Label(awake_body,text='선택한 캐릭터의 각성 · 같은 각성도 슬롯별로 표시합니다. 정정은 각 카드의 버튼을 눌러주세요.',wraplength=920).pack(anchor='w',pady=(0,6))
        awakening=ScrolledText(awake_body,height=2,wrap='word')
        awake_vars=[];awake_desc=[];awake_known=[];awake_images=[];awake_notices=[];awake_summaries=[]
        choices=['확인 필요']+sorted(set(AWAKENING_EFFECTS_KO)|set(self.user_settings.get('shared_awakening_descriptions',{})))
        def toggle_editor(editor,button):
            if editor.winfo_manager():editor.pack_forget();button.configure(text='정정 ▾')
            else:editor.pack(fill='x',padx=12,pady=(0,10));button.configure(text='정정 ▴')
        for i in range(3):
            box=tk.Frame(awake_body,bg='#f4f4f1',highlightbackground='#c9ced1',highlightthickness=1);box.pack(fill='x',pady=5)
            head=tk.Frame(box,bg='#657078');head.pack(fill='x',padx=10,pady=(9,0))
            image_label=tk.Label(head,bg='#657078');image_label.pack(side='left',padx=(6,10),pady=5);awake_images.append(image_label)
            var=tk.StringVar(value='확인 필요');awake_vars.append(var)
            tk.Label(head,textvariable=var,bg='#657078',fg='white',font=('Segoe UI',12,'bold'),anchor='w').pack(side='left',fill='x',expand=True)
            info=tk.StringVar();awake_notices.append(info)
            tk.Label(head,textvariable=info,bg='#657078',fg='#fff1bf',font=('Segoe UI',10)).pack(side='right',padx=10)
            summary=tk.StringVar(value='효과 확인 필요');awake_summaries.append(summary)
            effect=tk.Label(box,textvariable=summary,bg='#f4f4f1',fg='#30383e',font=('Segoe UI',11),justify='left',anchor='w',wraplength=850)
            effect.pack(fill='x',padx=(66,18),pady=(12,8))
            effect.bind('<Configure>',lambda e:e.widget.configure(wraplength=max(180,e.width-8)))
            controls=ttk.Frame(box);controls.pack(fill='x',padx=12,pady=(0,8))
            editor=ttk.Frame(box)
            edit_button=ttk.Button(controls,text='정정 ▾');edit_button.configure(command=lambda e=editor,b=edit_button:toggle_editor(e,b));edit_button.pack(side='right')
            combo=ttk.Combobox(editor,textvariable=var,values=choices,state='readonly',width=30);combo.pack(anchor='w',pady=4)
            known=tk.BooleanVar();awake_known.append(known)
            ttk.Checkbutton(controls,text='효과가 맞음',variable=known,command=lambda slot=i:confirm_awakening(slot)).pack(side='left')
            desc=ScrolledText(editor,height=3,wrap='word');desc.pack(fill='x',pady=5);awake_desc.append(desc)
            def sync_summary(event=None,widget=desc,value=summary):
                value.set(widget.get('1.0','end').strip() or '효과 확인 필요')
                widget.edit_modified(False)
            desc.bind('<<Modified>>',sync_summary)
            ttk.Button(editor,text='게임에서 읽은 설명 적용',command=lambda slot=i:apply_game_awakening(slot)).pack(anchor='e')
            combo.bind('<<ComboboxSelected>>',lambda e,slot=i:update_awakening(slot))
        auto_page=skill_page;auto_body=active_editor
        effects={key:tk.BooleanVar() for key in SKILL_EFFECT_LABELS}
        grid=ttk.LabelFrame(auto_body,text='액티브 스킬 효과 · 인식이 틀리면 수정');grid.pack(fill='x',pady=8)
        def update_active_summary(*_):
            active_summary.set(' · '.join(label for key,label in SKILL_EFFECT_LABELS.items() if effects[key].get()) or '효과 확인 필요')
        for i,(key,label) in enumerate(SKILL_EFFECT_LABELS.items()):
            ttk.Checkbutton(grid,text=label,variable=effects[key]).grid(row=i//4,column=i%4,sticky='w',padx=10,pady=3)
            effects[key].trace_add('write',update_active_summary)
        policies={'auto':'상황에 따라 자동','locked_only':'5속성 부족·잠금 존재 시','shortage_only':'속성 부족일 때만','time_only':'조작시간 감소 시에만','cleanse_only':'바인드 / 각성 무효 시에만'}
        policy=tk.StringVar();policyrow=ttk.Frame(auto_body);policyrow.pack(fill='x')
        ttk.Label(policyrow,text='사용 조건: ').pack(side='left');ttk.Combobox(policyrow,textvariable=policy,values=list(policies.values()),state='readonly',width=30).pack(side='left')
        opts=ttk.Frame(auto_body);opts.pack(fill='x',pady=8)
        ttk.Label(opts,text='5속성 부족 시 잠금 해제 → 5속성 생성 → 재탐색').pack(anchor='w')
        notice=tk.BooleanVar(value=self.user_settings.get('background_complete_notify',True))
        ttk.Checkbutton(opts,text='파밍 완료 바탕화면 알림',variable=notice).pack(side='left')
        def save_common():
            self.user_settings.update(background_complete_notify=notice.get());self.save_user_settings();status.set('완료 알림 설정을 저장했습니다.')
        ttk.Button(opts,text='공통 설정 저장',command=save_common).pack(side='right')
        def current():
            data=self.user_settings.get('background_party_profiles',[])
            return data[selected.get()] if len(data)>selected.get() else dict(slot=selected.get())
        def photo(label,encoded,maximum):
            label.configure(image='',text='');label.image=None
            if not encoded:return False
            try:
                import base64,io
                from PIL import ImageTk
                image=Image.open(io.BytesIO(base64.b64decode(encoded))).convert('RGB');image.thumbnail(maximum,Image.Resampling.LANCZOS)
                label.image=ImageTk.PhotoImage(image,master=win);label.configure(image=label.image);return True
            except (ValueError,OSError):return False
        def current_card_has_footer(slot):
            profiles=self.user_settings.get('background_party_profiles',[])
            return len(profiles)>slot and profiles[slot].get('portrait_image_version') in (3,4) and bool(profiles[slot].get('portrait_png'))
        def select_awakening(slot):
            selected.set(slot)
            previous=getattr(self,'_party_awakening_window',None)
            if previous is not None and previous.winfo_exists():previous.destroy()
            popup=tk.Toplevel(win);self._party_awakening_window=popup
            popup.title('각성 스킬 상세');popup.geometry(f'760x{min(690,popup.winfo_screenheight()-80)}');popup.minsize(560,460)
            popup.transient(win)
            profile=background_profile_merge(current(),self.user_settings)
            ttk.Label(popup,text=profile.get('name','미등록 캐릭터'),font=('Segoe UI',15,'bold'),anchor='center').pack(fill='x',pady=12)
            bottom=ttk.Frame(popup,padding=12);bottom.pack(side='bottom',fill='x')
            notice=tk.StringVar();ttk.Label(bottom,textvariable=notice,wraplength=690).pack(fill='x',pady=(0,6))
            actions=ttk.Frame(bottom);actions.pack(fill='x')
            canvas=tk.Canvas(popup,bg='#e3e4e3',highlightthickness=0)
            bar=ttk.Scrollbar(popup,orient='vertical',command=canvas.yview);bar.pack(side='right',fill='y')
            canvas.configure(yscrollcommand=bar.set);canvas.pack(fill='both',expand=True)
            content=tk.Frame(canvas,bg='#e3e4e3');window_id=canvas.create_window((0,0),window=content,anchor='nw')
            content.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window_id,width=e.width))
            checks=[];rows=party_awakening_effects(profile,self.user_settings)
            icons=profile.get('awakening_icons_png',[])
            def persist(indices,confirmed):
                if getattr(self,'_background_running',False):notice.set('등록 중입니다. 읽기가 끝난 뒤 확인해 주세요.');return False
                try:
                    updated=confirm_party_awakenings(self.user_settings,slot,indices,confirmed)
                    self.save_user_settings();refresh()
                    for i,var in enumerate(checks):var.set(bool(updated[i].get('verified')))
                    notice.set('저장 완료 · 같은 각성의 설명 확인 상태도 공유합니다.')
                    return True
                except ValueError as error:notice.set(str(error));return False
            def check(index):
                if not persist([index],checks[index].get()):checks[index].set(not checks[index].get())
            for i,item in enumerate(rows):
                card=tk.Frame(content,bg='#f6f6f3',highlightbackground='#c2c6c9',highlightthickness=1);card.pack(fill='x',padx=18,pady=9)
                head=tk.Frame(card,bg='#737c82');head.pack(fill='x',padx=10,pady=(10,0))
                icon=tk.Label(head,bg='#737c82');icon.pack(side='left',padx=8,pady=6)
                if not photo(icon,icons[i] if i<len(icons) else item.get('icon_png',''),(42,42)):icon.configure(text='?',fg='white')
                tk.Label(head,text=item.get('name','확인 필요'),bg='#737c82',fg='white',font=('Segoe UI',13,'bold'),anchor='w',wraplength=530).pack(side='left',fill='x',expand=True)
                effect=tk.Label(card,text=item.get('description') or '효과 확인 필요',bg='#f6f6f3',fg='#30383e',font=('Segoe UI',12),anchor='w',justify='left',wraplength=610)
                effect.pack(fill='x',padx=(64,18),pady=(15,8));effect.bind('<Configure>',lambda e:e.widget.configure(wraplength=max(180,e.width-8)))
                value=tk.BooleanVar(value=item.get('verified',False));checks.append(value)
                ttk.Checkbutton(card,text='설명이 맞음',variable=value,command=lambda index=i:check(index)).pack(anchor='e',padx=16,pady=(0,10))
            if not rows:ttk.Label(content,text='각성 정보가 없습니다. 게임의 각성 상세에서 먼저 등록해 주세요.',wraplength=650).pack(padx=20,pady=30)
            ttk.Button(actions,text='이 캐릭터 각성 모두 확인',command=lambda:persist(range(len(rows)),True)).pack(side='left')
            def edit_descriptions():
                editor=tk.Toplevel(popup);editor.title('각성 설명 정정');editor.geometry('700x650');editor.transient(popup)
                body=ttk.Frame(editor,padding=12);body.pack(fill='both',expand=True)
                values=[]
                for item in rows:
                    box=ttk.LabelFrame(body,text='각성 설명');box.pack(fill='x',pady=6)
                    name=tk.StringVar(value=item.get('name','확인 필요'))
                    ttk.Combobox(box,textvariable=name,values=choices,width=35).pack(anchor='w',padx=8,pady=6)
                    description=ScrolledText(box,height=3,wrap='word');description.pack(fill='x',padx=8,pady=(0,6))
                    description.insert('1.0',item.get('description',''));values.append((name,description))
                hint=tk.StringVar();ttk.Label(body,textvariable=hint,wraplength=650).pack(fill='x',pady=6)
                def save_edits():
                    if getattr(self,'_background_running',False):hint.set('파티 등록이 끝난 뒤 수정해 주세요.');return
                    profiles=self.user_settings.get('background_party_profiles',[])
                    if slot>=len(profiles) or profiles[slot].get('number')!=profile.get('number'):hint.set('파티가 변경됐습니다. 상세 창을 다시 열어주세요.');return
                    updated=[]
                    for i,(name,description) in enumerate(values):
                        title=name.get().strip();text=description.get('1.0','end').strip()
                        if not title or not text:hint.set('각성 이름과 설명을 입력해 주세요.');return
                        original=rows[i]
                        same=title==original.get('name') and text==original.get('description')
                        updated.append(dict(original,name=title,description=text,verified=bool(original.get('verified')) if same else False,known=title in AWAKENING_EFFECTS_KO,source=original.get('source','') if same else '사용자 정정'))
                    descriptions={}
                    for item in updated:
                        if item['name'] in descriptions and descriptions[item['name']]!=item['description']:
                            hint.set('같은 각성의 설명은 동일하게 입력해 주세요.');return
                        descriptions[item['name']]=item['description']
                    for item in updated:
                        previous=dict(self.user_settings.get('shared_awakening_descriptions',{}).get(item['name'],{}))
                        share_awakening_description(self.user_settings,item['name'],item['description'],item['verified'])
                        previous.update(self.user_settings['shared_awakening_descriptions'][item['name']])
                        self.user_settings['shared_awakening_descriptions'][item['name']]=previous
                    observation=dict(awakening_effects=updated,awakenings=' / '.join(item['name'] for item in updated),awakening_registered=bool(updated) and all(item.get('verified') for item in updated))
                    number=str(profile['number'])
                    self.user_settings.setdefault('monster_awakening_observations',{}).setdefault(number,{}).update(observation)
                    for member in profiles:
                        if str(member.get('number'))==number:member.update(observation)
                    override=self.user_settings.get('monster_overrides',{}).get(number)
                    if override is not None:
                        for key in ('awakening_effects','awakenings'):override[key]=observation[key]
                    record_party_description_review(self.user_settings,profiles[slot]);self.save_user_settings();refresh()
                    editor.destroy();select_awakening(slot)
                ttk.Button(body,text='정정 저장',command=save_edits).pack(side='right',pady=8)
                ttk.Button(body,text='취소',command=editor.destroy).pack(side='right',padx=8,pady=8)
                self._party_bring_forward(editor)
            ttk.Button(actions,text='게임에서 다시 읽기',command=lambda:self._awakening_register_clicked(from_party=True)).pack(side='left',padx=8)
            ttk.Button(actions,text='설명 정정',command=edit_descriptions).pack(side='left',padx=8)
            ttk.Button(actions,text='닫기',command=popup.destroy).pack(side='right')
            tag='AwakeningPopup'+str(id(popup))
            def wheel(event):
                canvas.yview_scroll(-1 if getattr(event,'delta',0)>0 or getattr(event,'num',0)==4 else 1,'units');return 'break'
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):popup.bind_class(tag,sequence,wheel)
            def bind_wheel(widget):
                widget.bindtags((tag,)+widget.bindtags())
                for child in widget.winfo_children():bind_wheel(child)
            bind_wheel(content)
            def cleanup(event):
                if event.widget is popup:
                    for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):popup.unbind_class(tag,sequence)
            popup.bind('<Destroy>',cleanup,add='+')
            self._party_bring_forward(popup)
        self._party_open_awakening=select_awakening
        def show_original():
            p=background_profile_merge(current(),self.user_settings)
            child=tk.Toplevel(win);child.title('출처·일본어 원문');child.geometry('760x360')
            text=ScrolledText(child,wrap='word');text.pack(fill='both',expand=True)
            text.insert('1.0','출처: '+p.get('source','미확인')+'\n\n일본어 이름: '+p.get('name_ja','미확인')+'\n\n스킬: '+p.get('skill_ja','미확인')+'\n\n각성: '+p.get('awakenings_ja','미확인'));text.configure(state='disabled')
        def apply_game_awakening(i):
            name=awake_vars[i].get();candidate=self.user_settings.get('awakening_description_candidates',{}).get(name)
            if not candidate:status.set('게임에서 각성 스킬 상세를 먼저 열어주세요.');return
            share_awakening_description(self.user_settings,name,candidate['description'],False)
            self.user_settings['shared_awakening_descriptions'][name].update(source='게임 각성 상세 OCR',icon_png=candidate.get('icon_png',''),description_png=candidate.get('description_png',''))
            awake_desc[i].delete('1.0','end');awake_desc[i].insert('1.0',candidate['description']);awake_known[i].set(False);awake_notices[i].set('게임 설명 · 확인 필요')
            self.save_user_settings();status.set('읽은 게임 설명을 적용했습니다. 내용 확인 후 체크하면 공유 확인으로 저장합니다.')
        def confirm_awakening(i):
            if share_awakening_description(self.user_settings,awake_vars[i].get(),awake_desc[i].get('1.0','end').strip(),awake_known[i].get()):
                self.save_user_settings();awake_notices[i].set('공유 확인 완료' if awake_known[i].get() else '확인 필요')
                status.set('같은 각성의 설명과 확인 상태를 공유했습니다. 설명 검토 데이터도 보존합니다.')
            else:status.set('각성 이름과 설명을 먼저 선택해 주세요.')
        def update_awakening(i):
            name=awake_vars[i].get();shared=self.user_settings.get('shared_awakening_descriptions',{}).get(name,{})
            description=shared.get('description') or AWAKENING_EFFECTS_KO.get(name,'효과 확인 필요')
            awake_desc[i].delete('1.0','end');awake_desc[i].insert('1.0',description);awake_known[i].set(bool(shared.get('verified')))
            awake_notices[i].set('공유 확인 완료' if shared.get('verified') else '확인 필요' if name=='확인 필요' or '확인' in description else 'DB 효과 · 아이콘 확인 필요')
            names=[var.get() for var in awake_vars if var.get() not in ('확인 필요','없음')]
            awakening.delete('1.0','end');awakening.insert('1.0',' / '.join(names) or '없음')
        def populate(p):
            for key,var in fields.items():var.set(str(p.get(key,'') or ''))
            skill.delete('1.0','end');skill.insert('1.0',p.get('text',''))
            if not photo(skill_image,p.get('skill_panel_png'),(940,240)):skill_image.configure(text='스킬 이미지 확인 필요 · 파티를 다시 읽어주세요.')
            awakening.delete('1.0','end');awakening.insert('1.0',p.get('awakenings',''))
            values=party_awakening_effects(p,self.user_settings);icons=p.get('awakening_icons_png',[])
            for i in range(3):
                item=values[i] if i<len(values) else dict(name='없음',description=AWAKENING_EFFECTS_KO['없음'],known=True)
                name=item.get('name','확인 필요');awake_vars[i].set(name)
                awake_desc[i].delete('1.0','end');awake_desc[i].insert('1.0',item.get('description') or '효과 확인 필요')
                awake_known[i].set(item.get('verified',False));photo(awake_images[i],icons[i] if i<len(icons) else '',(40,40))
                awake_notices[i].set('확인 완료' if item.get('verified') else '확인 필요' if not item.get('known') or '확인' in item.get('description','') else 'DB 효과 · 아이콘 확인 필요')
            for key,var in effects.items():var.set(key in p.get('effects',[]))
            enabled.set(p.get('auto_use',True));verified.set(p.get('verified',False));policy.set(policies.get(p.get('use_policy','auto'),policies['auto']))
            source.set('스킬 턴: '+p.get('cooldown_source','확인 필요')+'  · 이름: '+{'game_confirmed':'게임 확인','user_corrected':'사용자 정정','translated_draft':'번역 임시명'}.get(p.get('name_ko_status'),'확인 필요'))
            unknown=any(not item.get('verified') and (not item.get('known') or '확인' in item.get('description','')) for item in values)
            status.set('각성 효과 확인 필요 · 캐릭터 아래 각성 아이콘을 눌러 확인하세요.' if unknown else '확인 완료' if p.get('verified') else '이미지와 효과를 확인하고 저장하세요.')
        def load(*_):
            p=background_profile_merge(current(),self.user_settings);data=self.user_settings.get('background_party_profiles',[])
            if len(data)>selected.get() and p.get('number') and data[selected.get()]!=p:data[selected.get()]=p;self.save_user_settings()
            populate(p)
        def refresh():
            data=self.user_settings.get('background_party_profiles',[])
            for i,var in enumerate(name_vars):
                p=background_profile_merge(data[i],self.user_settings) if i<len(data) else {}
                if not photo(portrait_labels[i],p.get('portrait_png'),(104,178)):portrait_labels[i].configure(text='이미지 확인 필요',wraplength=94)
                attribute_vars[i].set((p.get('attributes_ko') or '속성 확인 필요')+'\n'+('✓ 각성 등록 완료' if p.get('awakening_registered') and all(v.get('verified') for v in party_awakening_effects(p,self.user_settings)) else '□ 각성 등록 필요'))
                var.set(f'{i+1}. '+p.get('name','미확인'));level_vars[i].set(f'Lv{p.get("level") or "?"} · '+('확인 완료' if p.get('verified') else '읽기 완료' if p.get('number') else '미등록'))
                if current_card_has_footer(i):awake_frames[i].pack_forget()
                else:awake_frames[i].pack(anchor='center',pady=(4,0))
                icons=p.get('awakening_icons_png',[])
                for j,label in enumerate(awake_labels[i]):
                    if not photo(label,icons[j] if j<len(icons) else '',(30,30)):label.configure(text='?')
            fit_names();load()
        def db_fill():
            p=background_profile_merge(dict(slot=selected.get(),number=fields['number'].get().strip()),self.user_settings)
            if not p.get('source'):status.set('해당 번호가 DB에 없습니다. 직접 입력할 수 있습니다.');return
            # Keep the selected slot's captured images and level when its identity matches.
            old=current()
            if str(old.get('number'))==str(p.get('number')):
                for key in ('level','skill_panel_png','awakening_icons_png'):p[key]=old.get(key)
            populate(p);status.set('DB 정보를 불러왔습니다. 이미지와 효과를 확인한 뒤 저장하세요.')
        def save():
            if getattr(self,'_background_running',False):status.set('F10으로 파밍을 중지한 뒤 저장하세요.');return
            try:
                number=fields['number'].get().strip()
                if not number.isdigit() or int(number)<1:raise ValueError('몬스터 번호를 입력하세요.')
                old=current();p=dict(old,number=int(number),attributes_ko=fields['attributes_ko'].get().strip(),name=fields['name'].get().strip(),text=skill.get('1.0','end').strip(),awakenings=awakening.get('1.0','end').strip(),effects=[k for k,v in effects.items() if v.get()],auto_use=enabled.get(),verified=verified.get(),use_policy=next(k for k,v in policies.items() if v==policy.get()),priority=int(fields['priority'].get() or 0))
                for key in ('level','cooldown'):
                    value=fields[key].get().strip()
                    if value and (not value.isdigit() or not 0<=int(value)<=999):raise ValueError('레벨/턴은 0~999 숫자입니다.')
                    p[key]=int(value) if value else None
                p['attributes_source']='user_corrected' if p.get('attributes_ko')!=old.get('attributes_ko') else old.get('attributes_source','DB')
                if old.get('number')!=p['number']:
                    for key in ('portrait_png','skill_panel_png','awakening_icons_png','portrait','entry_level_signature','detail_signature'):p.pop(key,None)
                p['awakening_effects']=[dict(name=v.get(),description=awake_desc[i].get('1.0','end').strip(),known=v.get() in AWAKENING_EFFECTS_KO or awake_known[i].get(),verified=awake_known[i].get(),source='사용자 정정') for i,v in enumerate(awake_vars) if v.get()!='없음']
                for item in p['awakening_effects']:
                    if item.get('verified'):share_awakening_description(self.user_settings,item['name'],item['description'])
                record_party_description_review(self.user_settings,p)
                override={k:p[k] for k in ('name','text','awakenings','awakening_effects','attributes_ko','attributes_source','effects','auto_use','priority','verified','cooldown','use_policy')}
                self.user_settings.setdefault('monster_overrides',{})[number]=override
                data=self.user_settings.setdefault('background_party_profiles',[])
                while len(data)<6:data.append(dict(slot=len(data)))
                data[selected.get()]=p;self.user_settings.update(background_complete_notify=notice.get());self.save_user_settings();refresh();status.set('정정 내용을 저장했습니다. 이미지 원본과 함께 유지합니다.')
            except ValueError as exc:status.set(str(exc))
        ttk.Button(actions,text='번호로 DB 정보 불러오기',command=db_fill).pack(side='left')
        ttk.Button(actions,text='설명에서 효과 다시 읽기',command=lambda:[v.set(k in background_skill_details(skill.get('1.0','end'))['effects']) for k,v in effects.items()]).pack(side='left',padx=8)
        ttk.Button(actions,text='확인 / 정정 저장',command=save).pack(side='right')
        wheel_tag='PartyWheel'+str(id(win))
        def wheel(event):
            canvas=page_canvases.get(tabs.select())
            if canvas is None:return
            upward=getattr(event,'delta',0)>0 or getattr(event,'num',0)==4
            direction=-1 if upward else 1
            widget=event.widget
            if widget.winfo_class()=='Text':
                first,last=widget.yview()
                if (upward and first>0) or (not upward and last<1):widget.yview_scroll(direction,'units');return 'break'
            canvas.yview_scroll(direction,'units');return 'break'
        for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):win.bind_class(wheel_tag,sequence,wheel)
        def install_wheel(widget):
            if widget.winfo_class() not in ('Scrollbar','TScrollbar'):
                widget.bindtags((wheel_tag,)+tuple(tag for tag in widget.bindtags() if tag!=wheel_tag))
            for child in widget.winfo_children():install_wheel(child)
        install_wheel(win)
        def cleanup_wheel(event):
            if event.widget is win:
                for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):win.unbind_class(wheel_tag,sequence)
        win.bind('<Destroy>',cleanup_wheel,add='+')
        selected.trace_add('write',load);self._party_settings_refresh=refresh;refresh()
        if automatic:win.after_idle(lambda:action_request_current(self,action_epoch) and win.winfo_exists() and self._party_scan_start(automatic=True))

    def _party_scan_progress(self,text):
        self.set_status(text)
        window=getattr(self,'_party_settings_window',None)
        if window is not None and window.winfo_exists():self._party_scan_status.set(text)

    def _party_bring_forward(self,window):
        window.deiconify();window.attributes('-topmost',True);window.lift()
        window.after_idle(lambda:window.winfo_exists() and window.focus_force())
        window.after(800,lambda:window.winfo_exists() and window.attributes('-topmost',False))

    def _party_scan_start(self,slot=None,start_slot=0,automatic=False):
        resume_actions(self)
        wizard=getattr(self,'_background_setup_wizard',None)
        if wizard is not None and not wizard.closed and not wizard.saved:self._party_scan_progress('설정 마법사를 완료하거나 취소한 뒤 파티를 읽어주세요.');return
        if getattr(self,'_background_running',False) or self.busy or self.ocr_busy:
            self._party_scan_progress('현재 작업을 중지한 뒤 파티를 읽어주세요.');return
        try:exe=find_background_adb(self.user_settings.get('background_adb_path',''))
        except Exception as exc:self._party_scan_progress(str(exc));return
        self._party_scan_progress('파티 읽기 연결 중 · F10 중지')
        self._background_cancel=threading.Event();self._background_running=True
        self._background_run_settings=json.loads(json.dumps(self.user_settings))
        self._background_run_settings['party_force_refresh']=not automatic
        device=BackgroundADB(exe,self.user_settings.get('background_adb_serial',''),self._background_cancel,capture_settings=self._background_run_settings)
        self._background_device=device
        def worker():
            try:
                frame=device.connect()
                heading=self._background_header(frame)
                detail_open=heading not in ('잠입확인','팀')
                if automatic:
                    if detail_open:raise RuntimeError('게임의 팀 또는 잠입 확인 화면을 열고 자동 등록 다시 확인을 눌러주세요.')
                    cache=self._background_run_settings.get('background_party_profiles',[])
                    if not isinstance(cache,list):cache=[]
                    targets=party_registration_targets(party_card_frame(frame,heading),cache)
                    if len(targets)==6:
                        self._background_run_settings['party_force_refresh']=True
                        profiles,frame=self._background_party(device,frame)
                    else:
                        profiles=[dict(cache[i]) for i in range(6)] if len(cache)>=6 else [dict(cache[i]) if i<len(cache) else dict(slot=i) for i in range(6)]
                        for target in targets:
                            profiles,frame=self._background_party(device,frame,slots=[target])
                            self._background_run_settings['background_party_profiles']=profiles
                        card_frame=party_card_frame(frame,self._background_header(frame))
                        for i,item in enumerate(profiles):
                            item.update(portrait_png=background_portrait_image(card_frame,i),portrait_image_version=4)
                    self._background_queue.put(('party',profiles))
                    message=f'자동 등록 완료 · {len(targets)}명 등록·갱신 / {6-len(targets)}명 기존 정보 사용' if targets else '현재 파티 6명 확인 완료 · 저장된 정보를 사용합니다.'
                elif detail_open:
                    tokens=self._run_ocr_tokens(frame)
                    if not any(t.score>=.85 and re.search(r'No[.．]?\s*\d+',t.text,re.I) and t.x1>800 and 180<t.cy<270 for t in tokens):raise RuntimeError('팀·잠입 확인 또는 캐릭터 상세 화면에서 읽어주세요.')
                if not automatic:
                    self._background_party(device,frame,slots=[slot] if slot is not None else None,start_slot=slot if slot is not None else start_slot,detail_open=detail_open)
                    message='파티 정보 읽기 완료 · 읽은 슬롯별로 저장했습니다.'
            except Exception as exc:message='파티 정보 읽기 중지 · '+str(exc)
            device.close_capture()
            self._background_queue.put(('done',message))
        threading.Thread(target=worker,daemon=True).start()

    def _party_status_template(self):
        if getattr(self,'_background_running',False):self.set_status('F10으로 파밍을 중지한 뒤 등록하세요.');return
        try:exe=find_background_adb(self.user_settings.get('background_adb_path',''))
        except Exception as exc:self.set_status(str(exc));return
        def worker():
            try:
                d=BackgroundADB(exe,self.user_settings.get('background_adb_serial',''),capture_settings=self.user_settings);d.connect()
                self._background_queue.put(('status_template_frame',d.capture()))
            except Exception as exc:self._background_queue.put(('status','상태 아이콘 캡처 실패 · '+str(exc)))
            finally:
                if 'd' in locals():d.close_capture()
        threading.Thread(target=worker,daemon=True).start()

    def _party_template_editor(self,frame):
        from PIL import ImageTk
        import base64,io
        win=tk.Toplevel(self.root);win.title('전투 상태 아이콘 등록 · 아이콘 그림만 드래그');win.geometry('830x600')
        labels={'void_shield':'일정 피해 이상 무효 방패','attack_down':'공격력 감소','awoken_void':'각성 무효','bound':'바인드',
                'recovery_down':'회복력 감소','damage_absorb':'데미지 흡수','attribute_absorb':'속성 흡수'}
        choice=tk.StringVar(value=list(labels.values())[0]);ttk.Combobox(win,textvariable=choice,values=list(labels.values()),state='readonly',width=35).pack()
        ttk.Label(win,text='남은 턴 숫자를 제외하고 아이콘 그림만 선택하세요. 50/75% 감소 방패는 무효 방패로 등록하지 마세요.').pack()
        canvas=tk.Canvas(win,width=800,height=470);canvas.pack();photo=ImageTk.PhotoImage(frame.resize((800,470)));canvas.image=photo
        canvas.create_image(0,0,anchor='nw',image=photo);selection={}
        def down(e):selection.update(start=(e.x,e.y));canvas.delete('selection')
        def drag(e):
            if 'start' in selection:
                x,y=selection['start'];canvas.delete('selection');canvas.create_rectangle(x,y,e.x,e.y,outline='red',width=2,tags='selection')
        def up(e):
            if 'start' in selection:
                x,y=selection['start'];selection['box']=(min(x,e.x)*2,min(y,e.y)*2,max(x,e.x)*2,max(y,e.y)*2)
        canvas.bind('<ButtonPress-1>',down);canvas.bind('<B1-Motion>',drag);canvas.bind('<ButtonRelease-1>',up)
        def save():
            box=selection.get('box')
            if not box or not 10<=box[2]-box[0]<=100 or not 10<=box[3]-box[1]<=100:return
            crop=frame.crop(box);stream=io.BytesIO();crop.save(stream,format='PNG')
            key=next(k for k,v in labels.items() if v==choice.get())
            self.user_settings.setdefault('background_status_templates',{})[key]=dict(png=base64.b64encode(stream.getvalue()).decode(),area='enemy' if box[1]<600 else 'party')
            self.save_user_settings();win.destroy();self.set_status('상태 아이콘을 저장했습니다. 다음 파밍부터 자동 비교합니다.')
        ttk.Button(win,text='선택한 상태 아이콘 저장',command=save).pack(pady=8)

    def _background_complete_notification(self,message):
        self.user_settings['background_last_completion']=dict(message=message,at=time.strftime('%Y-%m-%d %H:%M:%S'))
        self.save_user_settings()
        if not self.user_settings.get('background_complete_notify',True):return
        win=tk.Toplevel(self.root);win.title('재료 파밍 완료');win.overrideredirect(True);win.attributes('-topmost',True)
        x=max(0,win.winfo_screenwidth()-390);y=max(0,win.winfo_screenheight()-190)
        win.geometry(f'370x130+{x}+{y}')
        ttk.Label(win,text='✓ 재료 파밍 완료',font=('Segoe UI',14,'bold')).pack(anchor='w',padx=14,pady=(12,5))
        ttk.Label(win,text=message,wraplength=340).pack(anchor='w',padx=14)
        ttk.Button(win,text='닫기',command=win.destroy).pack(anchor='e',padx=12,pady=6)
        # Do not steal keyboard focus from the application the user is using.
        try:win.bell()
        except tk.TclError:pass
        win.after(15000,win.destroy)

    def open_community_adb_help(self):
        import webbrowser
        try:
            if not webbrowser.open(COMMUNITY_ADB_HELP_URL):raise RuntimeError('브라우저 실행 실패')
        except Exception:
            self.show_background_path_help()

    def show_background_path_help(self,path='',serial=''):
        parent=getattr(self,'_background_settings_window',None)
        if parent is None or not parent.winfo_exists():parent=self.root
        win=tk.Toplevel(parent)
        win.title('경로 설정 방법 · MuMu ADB 연결');win.geometry('760x650')
        body=ttk.Frame(win,padding=16);body.pack(fill='both',expand=True)
        ttk.Label(body,text='경로 설정 방법',font=('Segoe UI',15,'bold')).pack(anchor='w',pady=(0,8))
        ttk.Label(body,text='ADB 파일 선택 → 기기 주소 확인 → 저장 → 파티 읽기로 확인').pack(anchor='w',pady=(0,10))
        text=ScrolledText(body,wrap='word',font=('Segoe UI',10),padx=10,pady=10)
        text.pack(fill='both',expand=True);text.insert('1.0',background_connection_help(path,serial));text.configure(state='disabled')
        buttons=ttk.Frame(body);buttons.pack(fill='x',pady=(10,0))
        def copy():
            win.clipboard_clear();win.clipboard_append(background_connection_help(path,serial))
        ttk.Button(buttons,text='그림으로 보는 ADB·포트 설정',command=self.open_community_adb_help).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='설명 복사',command=copy).pack(side='left')
        ttk.Button(buttons,text='닫기',command=win.destroy).pack(side='right')
        self._party_bring_forward(win)

    def show_background_setup_wizard(self,path='',serial='',on_saved=None):
        if getattr(self,'_background_running',False):self.set_status('F10으로 파밍을 중지한 뒤 설정 마법사를 열어주세요.');return
        current=getattr(self,'_background_setup_wizard',None)
        if current is not None and not current.closed and current.win.winfo_exists():
            if on_saved is not None:current.on_saved=on_saved
            self._party_bring_forward(current.win);return current
        wizard=BackgroundSetupWizard(self,on_saved,path or self.user_settings.get('background_adb_path',''),serial or self.user_settings.get('background_adb_serial',''))
        self._background_setup_wizard=wizard
        return wizard

    def install_google_adb_ui(self,path_value,on_applied=None,parent=None):
        if getattr(self,'_background_running',False):self.set_status('F10으로 파밍을 중지한 뒤 ADB를 교체하세요.');return
        existing=getattr(self,'_google_adb_window',None)
        if existing is not None and existing.winfo_exists():self._party_bring_forward(existing);return
        win=tk.Toplevel(parent or self.root);self._google_adb_window=win;win.title('Google 공식 ADB로 강제 교체');win.geometry('730x430')
        body=ttk.Frame(win,padding=16);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Google 공식 Android Platform-Tools',font=('Segoe UI',14,'bold')).pack(anchor='w')
        ttk.Label(body,text='프로그램 폴더의 platform-tools에 설치하고 .\\platform-tools\\adb.exe로 저장합니다.\n프로그램 업데이트 시 유지되며, 프로그램 폴더와 함께 이동할 수 있습니다.',wraplength=690).pack(anchor='w',pady=10)
        log=ScrolledText(body,height=8,wrap='word');log.pack(fill='both',expand=True);log.insert('1.0','설치 준비 중…');log.configure(state='disabled')
        row=ttk.Frame(body);row.pack(fill='x',pady=(10,0))
        ttk.Button(row,text='Google 공식 안내·약관',command=lambda:__import__('webbrowser').open(GOOGLE_ADB_TERMS)).pack(side='left')
        cancel=threading.Event();self._google_adb_cancel=cancel;events=queue.SimpleQueue();closed=[False];finished=[False]
        previous=str(path_value).strip();old_settings=self.user_settings.get('background_adb_path','')
        try:old_exe=find_background_adb(previous)
        except RuntimeError:old_exe=previous
        detected_root=self.user_settings.get('background_mumu_root','')
        if not detected_root:
            try:detected_root=find_mumu_capture_install(old_exe)[0]
            except (RuntimeError,OSError,ValueError):pass
        def tell(message):
            log.configure(state='normal');log.delete('1.0','end');log.insert('1.0',message);log.configure(state='disabled')
        def close():closed[0]=True;cancel.set();win.destroy()
        win.protocol('WM_DELETE_WINDOW',close);ttk.Button(row,text='닫기 / 취소',command=close).pack(side='right')
        def worker():
            try:events.put(('result',install_google_adb(cancel=cancel,progress=lambda message:events.put(('progress',message)))))
            except Exception as error:events.put(('error',str(error)))
        def timeout():
            if not finished[0] and not closed[0]:finished[0]=True;cancel.set();tell('설치 시간이 초과되어 중단했습니다. 기존 ADB 설정을 유지합니다.')
        win.after(120000,timeout)
        def poll():
            if closed[0] or finished[0]:return
            while True:
                try:kind,result=events.get_nowait()
                except queue.Empty:win.after(100,poll);return
                if kind=='progress':tell(result);continue
                finished[0]=True
                if kind=='error':tell('ADB 교체를 완료하지 못했습니다. 기존 경로를 유지합니다.\n\n'+result);return
                if self.user_settings.get('background_adb_path','')!=old_settings or getattr(self,'_background_running',False):
                    tell('공식 ADB 설치는 완료했습니다. 설정이 변경되었거나 파밍 중이어서 경로 적용은 보류했습니다.\n'+result['path']);return
                preserved=self.user_settings.get('background_previous_adb_path','')
                if old_exe and Path(old_exe).resolve()!=Path(result['path']).resolve():preserved=previous or old_exe
                old=dict(self.user_settings)
                self.user_settings.update(background_adb_path=result['configured'],background_previous_adb_path=preserved,
                    background_google_adb_version=result['release']['version'],background_google_adb_source=result['release']['url'])
                if detected_root:self.user_settings['background_mumu_root']=detected_root
                self.save_user_settings()
                saved=_load_json_dict(self.data_paths['settings'])
                if saved.get('background_adb_path')!=result['configured']:
                    self.user_settings.clear();self.user_settings.update(old);tell('파일 설치는 완료했지만 설정 저장을 확인하지 못했습니다. 기존 설정을 유지합니다.');return
                if on_applied:on_applied(result['configured'],detected_root)
                text='Google 공식 ADB 교체 완료\n설정 경로: '+result['configured']+'\n실제 위치: '+result['path']+'\n\n'+result['version']+'\n\n기존 MuMu 파일과 SDK 경로는 유지했습니다. 스크린샷 테스트로 기기 연결을 확인하세요.\n연결 서비스 재시작이 필요하면 설정 마법사의 해당 버튼을 사용하세요.'
                tell(text);self.set_status('Google 공식 ADB 적용 완료 · '+result['configured'])
                return
        threading.Thread(target=worker,daemon=True).start();win.after(100,poll);self._party_bring_forward(win)

    def restore_previous_adb(self,on_applied=None,parent=None):
        if getattr(self,'_background_running',False):self.set_status('F10으로 파밍을 중지한 뒤 ADB를 되돌리세요.');return
        previous=self.user_settings.get('background_previous_adb_path','')
        if not previous:messagebox.showinfo('기존 ADB 없음','저장된 이전 ADB 경로가 없습니다.',parent=parent or self.root);return
        try:find_background_adb(previous)
        except RuntimeError as error:messagebox.showwarning('기존 ADB 확인',str(error),parent=parent or self.root);return
        current=self.user_settings.get('background_adb_path','')
        self.user_settings['background_adb_path']=previous;self.save_user_settings()
        if _load_json_dict(self.data_paths['settings']).get('background_adb_path')!=previous:
            self.user_settings['background_adb_path']=current
            messagebox.showwarning('설정 저장 실패','이전 ADB 경로의 저장을 확인하지 못했습니다. 현재 설정을 유지합니다.',parent=parent or self.root);return
        if on_applied:on_applied(previous,self.user_settings.get('background_mumu_root',''))
        self.set_status('기존 ADB 경로로 되돌렸습니다. · '+previous)

    def show_background_capture_test(self,settings=None,parent=None):
        resume_actions(self)
        from PIL import ImageTk
        if getattr(self,'_background_running',False):self.set_status('F10으로 파밍을 중지한 뒤 스크린샷을 테스트하세요.');return
        settings=dict(settings or self.user_settings)
        host=parent or self.root
        existing=getattr(self,'_capture_test_preview',None)
        if existing is not None:
            try:existing.destroy()
            except tk.TclError:pass
        outer=ttk.Frame(host,padding=6);outer.pack(fill='x')
        self._capture_test_preview=outer
        preview=ttk.Label(outer,text='연결 확인 중…',anchor='center');preview.pack(side='left',fill='both',expand=True)
        right=ttk.Frame(outer);right.pack(side='right',padx=(8,0),anchor='n')
        speed=tk.StringVar(value='측정 중…')
        ttk.Label(right,textvariable=speed,wraplength=145).pack(anchor='w')
        events=queue.SimpleQueue();cancel=threading.Event();self._capture_test_cancel=cancel
        closed=[False];finished=[False]
        def release(event=None):
            if event is not None and event.widget is not outer:return
            closed[0]=True;cancel.set()
            preview.image=None
            while True:
                try:events.get_nowait()
                except queue.Empty:break
            if getattr(self,'_capture_test_preview',None) is outer:self._capture_test_preview=None
        outer.bind('<Destroy>',release,add='+')
        ttk.Button(right,text='닫기',command=outer.destroy).pack(anchor='e',pady=6)
        def finish_error(text):
            speed.set('캡처 실패');preview.configure(text=text,wraplength=370)
        def timeout():
            if closed[0] or finished[0]:return
            finished[0]=True;cancel.set();finish_error('30초 안에 끝나지 않았습니다. 기기 연결을 확인하세요.')
        outer.after(30000,timeout)
        def worker():
            try:
                exe=find_background_adb(settings.get('background_adb_path',''))
                result=capture_background_screenshot(exe,settings.get('background_adb_serial',''),settings,cancel)
                if not cancel.is_set():events.put(('result',result))
            except Exception as error:
                if not cancel.is_set():events.put(('error',str(error)))
        def poll():
            if closed[0] or finished[0]:return
            try:kind,result=events.get_nowait()
            except queue.Empty:outer.after(100,poll);return
            finished[0]=True
            if kind=='error':finish_error(result);return
            frame=result.pop('frame')
            frame.thumbnail((400,235),Image.Resampling.LANCZOS)
            preview.image=ImageTk.PhotoImage(frame,master=outer);preview.configure(image=preview.image,text='')
            speed.set(f"{result['capture_ms']:.2f}ms\n({result['method']})")
        threading.Thread(target=worker,daemon=True).start();outer.after(100,poll)

    def background_settings(self):
        if getattr(self,'_background_running',False):self.set_status('F10으로 파밍을 중지한 뒤 연결 설정을 바꿀 수 있습니다.');return
        from tkinter import filedialog
        window=tk.Toplevel(self.root);self._background_settings_window=window
        window.title('백그라운드 파밍 연결 설정');window.geometry(f'800x{min(880,window.winfo_screenheight()-70)}')
        self._party_bring_forward(window)
        body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
        ttk.Label(body,text='MuMu SDK 또는 ADB로 화면을 읽고, 선택한 Android 기기에 터치합니다.',wraplength=595).pack(anchor='w')
        path=tk.StringVar(value=self.user_settings.get('background_adb_path',''))
        serial=tk.StringVar(value=self.user_settings.get('background_adb_serial',''))
        skills=tk.BooleanVar(value=self.user_settings.get('background_auto_skills',True))
        capture_modes={'auto':'자동 · MuMu 우선 / 실패 시 ADB','mumu':'MuMu 고속 · 실패 시 ADB','adb':'ADB 기본'}
        capture_mode=tk.StringVar(value=capture_modes.get(self.user_settings.get('background_capture_mode','auto'),capture_modes['auto']))
        install=tk.StringVar(value=self.user_settings.get('background_mumu_root',''))
        instance=tk.StringVar(value=str(self.user_settings.get('background_mumu_index','')))
        def capture_values():
            return dict(self.user_settings,background_adb_path=portable_background_adb_path(path.get()) if path.get().strip() else '',background_adb_serial=serial.get().strip(),
                        background_capture_mode=next(k for k,v in capture_modes.items() if v==capture_mode.get()),
                        background_mumu_root=install.get().strip().strip('"'),background_mumu_index=instance.get().strip())
        def apply_wizard(found_path,found_serial):
            if window.winfo_exists():path.set(found_path);serial.set(found_serial)
        wizard_row=ttk.Frame(body);wizard_row.pack(fill='x',pady=(12,0))
        ttk.Button(wizard_row,text='설정 마법사 시작',command=lambda:self.show_background_setup_wizard(path.get(),serial.get(),apply_wizard)).pack(side='left')
        ttk.Label(wizard_row,text='처음이라면 단계별로 연결을 확인하세요.').pack(side='left',padx=10)
        official_row=ttk.Frame(body);official_row.pack(fill='x',pady=(10,0))
        def apply_official(found_path,found_root):
            if window.winfo_exists():
                path.set(found_path)
                if found_root:install.set(found_root)
        ttk.Button(official_row,text='Google 공식 ADB로 강제 교체',command=lambda:self.install_google_adb_ui(path.get(),apply_official,window)).pack(side='left')
        ttk.Button(official_row,text='이전 ADB로 되돌리기',command=lambda:self.restore_previous_adb(apply_official,window)).pack(side='left',padx=8)
        ttk.Label(body,text=r'설치 위치: 프로그램 폴더\platform-tools\adb.exe · 업데이트 후 유지',foreground='#526776').pack(anchor='w',pady=(4,0))

        help_row=ttk.Frame(body);help_row.pack(fill='x',pady=(12,0))
        ttk.Button(help_row,text='경로 설정 방법',command=lambda:self.show_background_path_help(path.get(),serial.get())).pack(side='left')
        ttk.Button(help_row,text='MuMu 고속 캡처 설정 예시',command=lambda:__import__('webbrowser').open('https://arca.live/b/hx3n/148912243')).pack(side='left',padx=6)
        ttk.Button(help_row,text='MAA 연결 문서',command=lambda:__import__('webbrowser').open('https://docs.maa.plus/ko-kr/manual/connection.html')).pack(side='left')
        ttk.Label(body,text='ADB 파일 (비우면 자동 탐색)').pack(anchor='w',pady=(12,0))
        row=ttk.Frame(body);row.pack(fill='x');ttk.Entry(row,textvariable=path).pack(side='left',fill='x',expand=True)
        def browse():
            selected=filedialog.askopenfilename(parent=window,title='MuMu의 adb.exe 선택',filetypes=[('ADB','*.exe'),('모든 파일','*')])
            if selected:path.set(selected)
        ttk.Button(row,text='찾기',command=browse).pack(side='left')
        ttk.Label(body,text='기기 주소 (비우면 한 대만 연결됐을 때 자동 선택)').pack(anchor='w',pady=(8,0))
        ttk.Entry(body,textvariable=serial).pack(fill='x')
        capture_box=ttk.LabelFrame(body,text='화면 캡처 · MuMu 고속 연결',padding=10);capture_box.pack(fill='x',pady=(12,4))
        ttk.Combobox(capture_box,textvariable=capture_mode,values=list(capture_modes.values()),state='readonly',width=42).pack(anchor='w')
        ttk.Label(capture_box,text='MuMu 설치 루트 (ADB 파일 경로와 다름 · 비우면 ADB 기준 탐색)').pack(anchor='w',pady=(8,0))
        capture_row=ttk.Frame(capture_box);capture_row.pack(fill='x');ttk.Entry(capture_row,textvariable=install).pack(side='left',fill='x',expand=True)
        def choose_install():
            folder=filedialog.askdirectory(parent=window,title='MuMuPlayer 설치 루트 선택 (nx_main의 상위 폴더)')
            if folder:install.set(folder)
        ttk.Button(capture_row,text='폴더 선택',command=choose_install).pack(side='left',padx=(6,0))
        number_row=ttk.Frame(capture_box);number_row.pack(fill='x',pady=(8,0))
        ttk.Label(number_row,text='MuMu 기기 번호 #').pack(side='left');ttk.Entry(number_row,textvariable=instance,width=8).pack(side='left',padx=5)
        ttk.Label(number_row,text='빈칸: 표준 16384 포트 계열에서 자동 확인').pack(side='left')
        ttk.Label(capture_box,text='예: C:\\Program Files\\Netease\\MuMuPlayer / #0 → 16384, #1 → 16416\n5555·emulator 주소는 기기 번호를 직접 확인하세요. SDK 오류 시 ADB로 복귀합니다.',wraplength=710).pack(anchor='w',pady=6)
        ttk.Button(capture_box,text='스크린샷 테스트',command=lambda:self.show_background_capture_test(capture_values(),capture_box)).pack(anchor='w')
        ttk.Checkbutton(body,text='상세에서 확인한 스킬 자동 사용',variable=skills).pack(anchor='w',pady=8)
        ttk.Label(body,text='MuMu를 종료하거나 PC를 절전하면 멈춥니다. F10은 파밍 중지입니다.',wraplength=590).pack(anchor='w')
        def save():
            values=capture_values()
            try:
                if values['background_mumu_index']:mumu_instance_index(values['background_adb_serial'],values['background_mumu_index'])
            except ValueError as error:messagebox.showwarning('기기 번호 확인',str(error),parent=window);return
            self.user_settings.update(values,background_auto_skills=skills.get())
            self.save_user_settings();window.destroy()
        ttk.Button(body,text='저장',command=save).pack(anchor='e',pady=8)

    def show_farming_confirmation(self):
        existing=getattr(self,'_farming_confirm_window',None)
        if existing is not None and existing.winfo_exists():existing.deiconify();existing.lift();return
        try:
            ledger=self._load_crystal_ledger();key=background_target_key(ledger)
            group=ledger.get('farming_groups',{}).get(key)
            if not group or not group.get('materials'):raise ValueError('돌파 화면에서 재료 목록을 먼저 확인해 주세요.')
            rows=farming_group_rows(ledger,key);db=self._load_crystal_db()
        except Exception as error:self.set_status(str(error));return
        epoch=getattr(self,'_stop_epoch',0)
        win=tk.Toplevel(self.root);self._farming_confirm_window=win
        win.title('재료 파밍 확인');win.geometry('560x220');win.minsize(490,180)
        style=ttk.Style(win)
        style.configure('FarmConfirm.TFrame',background='#f5f7fa')
        style.configure('FarmConfirm.TLabel',background='#f5f7fa',foreground='#1e293b')
        style.configure('FarmTable.TFrame',background='white')
        style.configure('FarmTable.TLabel',background='white',foreground='#334155')
        style.configure('FarmHeader.TLabel',background='white',foreground='#64748b')
        body=ttk.Frame(win,padding=14,style='FarmConfirm.TFrame');body.pack(fill='both',expand=True)
        ttk.Label(body,style='FarmConfirm.TLabel',text='돌파 대상: '+group['name'],font=('Malgun Gothic',12,'bold'),wraplength=510).pack(anchor='w',pady=(0,12))
        area=ttk.Frame(body,style='FarmTable.TFrame');area.pack(fill='both',expand=True)
        canvas=tk.Canvas(area,highlightthickness=0,bg='white');scroll=ttk.Scrollbar(area,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        listing=ttk.Frame(canvas,style='FarmTable.TFrame',padding=(0,6));item=canvas.create_window((0,0),window=listing,anchor='nw')
        listing.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        win.bind('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120),'units'))
        for event,direction in (('<Button-4>',-1),('<Button-5>',1)):win.bind(event,lambda e,d=direction:canvas.yview_scroll(d,'units'))
        listing.columnconfigure(1,weight=1);checks={};needs={};photos=[]
        entries={str(entry['id']):entry for entry in db.get('crystals',[])}
        listing.columnconfigure(0,minsize=40)
        for col,text in enumerate(('재료','필요','보유','부족'),1):
            ttk.Label(listing,text=text,style='FarmHeader.TLabel',anchor='w' if col==1 else 'e').grid(row=0,column=col,sticky='ew',padx=(0,10),pady=(0,6))
        for index,row in enumerate(rows,1):
            cid=row['id'];checks[cid]=tk.BooleanVar(value=row['deficit']>0);needs[cid]=tk.StringVar(value=str(row['required']))
            if row['deficit']<=0:continue
            entry=entries.get(cid,{})
            label=ttk.Label(listing,text=crystal_display_name(entry,row,index),wraplength=235,style='FarmTable.TLabel')
            try:
                from PIL import ImageTk
                path=self.data_paths['icons_dir']/str(entry.get('image_file') or row.get('image_file') or '')
                with Image.open(path) as source:image=source.convert('RGB');image.thumbnail((32,32));photo=ImageTk.PhotoImage(image)
                photos.append(photo);ttk.Label(listing,image=photo,style='FarmTable.TLabel').grid(row=index,column=0,sticky='w',padx=(0,8),pady=5)
            except (OSError,ValueError,KeyError):pass
            label.grid(row=index,column=1,sticky='w',padx=(0,10),pady=5)
            ttk.Entry(listing,textvariable=needs[cid],width=7,justify='right').grid(row=index,column=2,sticky='e',padx=(0,10))
            ttk.Label(listing,text=str(row['owned']),anchor='e',style='FarmTable.TLabel').grid(row=index,column=3,sticky='e',padx=(0,10))
            deficit=tk.StringVar(value=str(row['deficit']));ttk.Label(listing,textvariable=deficit,anchor='e',style='FarmTable.TLabel').grid(row=index,column=4,sticky='e',padx=(0,10))
            def update(*_,var=needs[cid],owned=row['owned'],out=deficit):
                try:out.set(str(max(0,int(var.get())-owned)))
                except ValueError:out.set('—')
            needs[cid].trace_add('write',update)
        win._material_photos=photos
        notice=tk.StringVar();notice_label=ttk.Label(body,style='FarmConfirm.TLabel',textvariable=notice,foreground='#b33b32',wraplength=510);notice_label.pack(side='bottom',anchor='w',pady=(8,0),before=area)
        controls=ttk.Frame(body,style='FarmConfirm.TFrame');controls.pack(side='bottom',fill='x',pady=(8,0),before=area)
        def cancel():
            if win.winfo_exists():win.destroy()
            self._farming_confirm_window=None
        def confirm():
            if epoch!=getattr(self,'_stop_epoch',0):cancel();return
            try:
                with self._crystal_db_lock:
                    current=self._load_crystal_ledger()
                    if current.get('farming_groups',{}).get(key)!=group:raise ValueError('재료 목록이 바뀌었습니다. 창을 다시 열어주세요.')
                    updated,chosen=prepare_farming_selection(current,key,{cid:var.get() for cid,var in needs.items()},[cid for cid,var in checks.items() if var.get()])
                    _atomic_json_write(self.data_paths['ledger'],updated)
                cancel();self.start_background_farming(confirmed=True,material_ids=chosen,confirmed_key=key)
            except (ValueError,OSError) as error:notice.set(str(error))
        ttk.Button(controls,text='취소',command=cancel).pack(side='right')
        ttk.Button(controls,text='확인',command=confirm,style='Accent.TButton').pack(side='right',padx=(0,8))
        win.protocol('WM_DELETE_WINDOW',cancel);win.bind('<Escape>',lambda e:cancel())
        # Size from actual font/DPI metrics; a single row should not leave a tall blank list.
        win.update_idletasks()
        table_height=listing.winfo_reqheight()
        reserved=body.winfo_reqheight()-area.winfo_reqheight()
        height=min(max(180,table_height+reserved+4),max(180,min(700,win.winfo_screenheight()-90)))
        win.geometry(f'560x{height}')
        if table_height<=height-reserved:scroll.pack_forget()
        self._party_bring_forward(win)

    def start_background_farming(self,confirmed=False,material_ids=None,confirmed_key=None):
        if getattr(self,'_background_running',False):self.stop_background_farming();return
        if not confirmed:self.show_farming_confirmation();return
        resume_actions(self)
        wizard=getattr(self,'_background_setup_wizard',None)
        if wizard is not None and not wizard.closed and not wizard.saved:self.set_status('설정 마법사를 완료하거나 취소한 뒤 재료 파밍을 시작해주세요.');return
        if self.busy or self.playback_active or self.ocr_busy or getattr(self,'_manual_reward_busy',False) or getattr(self,'reward_poll_busy',False):
            self.set_status('현재 작업이 끝난 뒤 재료 파밍을 시작해 주세요.');return
        try:
            ledger=self._load_crystal_ledger();key=background_target_key(ledger)
            if confirmed_key is not None and key!=confirmed_key:raise ValueError('돌파 대상이 바뀌었습니다. 다시 확인해 주세요.')
            if background_goal_complete(ledger,key,material_ids=material_ids):self.set_status('선택한 재료가 모두 충족되어 파밍을 시작하지 않습니다.');return
            self._background_selected_material_ids=list(material_ids) if material_ids is not None else None
            groups=json.loads(json.dumps(ledger['farming_groups']))
            selected_group=dict(groups[key],materials=[row for row in groups[key]['materials'] if material_ids is None or row['id'] in material_ids])
            templates=self._farming_material_templates({key:selected_group})
            executable=find_background_adb(self.user_settings.get('background_adb_path',''))
        except Exception as exc:
            self.set_status(str(exc));self.root.deiconify();self.root.lift();return
        self._background_cancel=threading.Event();self._background_running=True
        self._farming_scan_cancel=self._background_cancel
        self._farming_battle_seen=False;self._farming_result_visible=False;self._farming_lobby_visible=False
        self._background_button_win.withdraw();self._prefind_cancel();self.overlay.hide()
        self.toolbar.hide_for_playback()
        if self._farming_stats is not None:self._farming_stats.withdraw()
        self._disarm_reward_watch('background farming started')
        self._background_entry_reference=self._background_entry_frame.copy() if self._background_entry_frame else None
        self._background_run_goals=self.selected_goals()
        self._background_run_settings=json.loads(json.dumps(self.user_settings))
        self._background_device=BackgroundADB(executable,self.user_settings.get('background_adb_serial',''),self._background_cancel,capture_settings=self._background_run_settings)
        self.set_status(f"{groups[key]['name']} 재료 파밍 · MuMu 백그라운드 연결 중 · F10 중지")
        self.root.iconify()
        threading.Thread(target=self._background_worker,args=(key,groups,templates),daemon=True).start()

    def stop_background_farming(self):
        if not getattr(self,'_background_running',False):return
        self._background_cancel.set()
        device=getattr(self,'_background_device',None)
        if device is not None:device.abort()
        self.set_status('백그라운드 파밍 중지 요청 · 진행 중인 터치를 해제합니다.')

    @staticmethod
    def _background_active_text(tokens):
        labels=[t for t in tokens if t.score>=.82 and _compact_ocr_text(t.text) in ('스킬','액티브스킬','skill')]
        if len(labels)!=1:return ''
        label=labels[0]
        bottom=min([t.y1 for t in tokens if t.y1>label.y2+20 and (any(word in _compact_ocr_text(t.text) for word in ('각성스킬','팀스킬','진화','성장')) or _compact_ocr_text(t.text) in ('hp','공격력','회복력','각성','싱크로'))]+[label.y2+220])
        lines=sorted([t for t in tokens if t.score>=.82 and label.y2-2<=t.cy<bottom
                      and t.x1>=label.x1-40 and _compact_ocr_text(t.text) not in ('스킬','액티브스킬','skill')],key=lambda t:(round(t.cy/20),t.x1))
        return ' '.join(t.text for t in lines)

    def _party_read_detail(self,device,detail,slot,signature):
        tokens=self._run_ocr_tokens(detail)
        if not any(t.score>=.82 and _compact_ocr_text(t.text) in ('스킬','액티브스킬','skill') for t in tokens):
            raise RuntimeError(f'{slot+1}번 상세 화면을 확인하지 못했습니다.')

        first=self._background_active_text(tokens)
        if self._background_cancel.wait(.2):raise RuntimeError('재료 파밍을 중지했습니다.')
        second_detail=device.capture()
        second_tokens=self._run_ocr_tokens(second_detail)
        second=self._background_active_text(second_tokens)
        text=first if first and _compact_ocr_text(first)==_compact_ocr_text(second) else ''
        title=extract_card_name_from_title(tokens,detail.size) or f'파티원 {slot+1}'
        header=' '.join(t.text for t in tokens if t.score>=.85 and t.x1>800 and 180<t.cy<335)
        number=re.search(r'No[.．]?\s*(\d{1,4})',header,re.I)
        second_header=' '.join(t.text for t in second_tokens if t.score>=.85 and t.x1>800 and 180<t.cy<335)
        second_number=re.search(r'No[.．]?\s*(\d{1,4})',second_header,re.I)
        if not number or not second_number or number[1]!=second_number[1]:raise RuntimeError(f'{slot+1}번 번호를 두 번 확인하지 못했습니다. 이 슬롯은 저장하지 않았습니다.')
        level=re.search(r'(?:Lv|LV|레벨)\s*(\d{1,3})',header,re.I)
        second_level=re.search(r'(?:Lv|LV|레벨)\s*(\d{1,3})',second_header,re.I)
        if not level or not second_level or level[1]!=second_level[1]:level=None
        names=[t.text for t in tokens if t.score>=.85 and t.x1>920 and 240<t.cy<285 and not re.search(r'\d',t.text)]
        second_names=[t.text for t in second_tokens if t.score>=.85 and t.x1>920 and 240<t.cy<285 and not re.search(r'\d',t.text)]
        name_confirmed=len(names)==len(second_names)==1 and _compact_ocr_text(names[0])==_compact_ocr_text(second_names[0])
        if name_confirmed:title=names[0]
        turns=background_detail_cooldown(tokens)
        if turns!=background_detail_cooldown(second_tokens):turns=None
        profile=dict(slot=slot,name=title,name_confirmed=name_confirmed,portrait=signature.tolist(),text=text,
                     number=int(number[1]) if number else None,level=int(level[1]) if level else None,
                     cooldown=turns,cooldown_source='게임 상세 OCR' if turns is not None else '',source='게임 상세 OCR',verified=False)
        attributes=background_detail_attributes(detail)
        expected_count=3 if str(profile.get('number')) in ('300','304','307') else 1
        if len(attributes)>=expected_count and attributes==background_detail_attributes(second_detail):profile.update(observed_attributes=attributes,attributes_ko=' / '.join(attributes),attributes_source='game_confirmed',attributes_read_version=2)
        if profile.get('attributes_read_version')==2:
            self._background_run_settings.setdefault('monster_attribute_observations',{})[str(profile['number'])]=profile['observed_attributes']
        profile['skill_panel_png']=party_image_png(detail.crop((824,344,1547,535)))
        profile['awakening_icons_png']=[party_image_png(detail.crop((1402+50*i,613,1446+50*i,657)).resize((30,30),Image.Resampling.LANCZOS)) for i in range(3)]
        profile['detail_signature']=np.asarray(detail.crop((100,235,750,730)).convert('L').resize((16,16)),dtype=np.int16).tolist()
        return background_profile_merge(profile,self._background_run_settings)

    def _party_wait_detail(self,device,slot,signature):
        error=None
        for _ in range(4):
            if self._background_cancel.wait(.45):raise RuntimeError('파티 읽기를 중지했습니다. 이미 읽은 정보는 유지됩니다.')
            detail=device.capture()
            try:return self._party_read_detail(device,detail,slot,signature)
            except RuntimeError as exc:error=exc
        raise RuntimeError(str(error or '상세 화면 응답을 확인하지 못했습니다.'))

    def _background_party(self,device,frame,slots=None,start_slot=0,detail_open=False):
        settings=self._background_run_settings;cache=settings.get('background_party_profiles',[])
        if not isinstance(cache,list):cache=[]
        profiles=[dict(cache[i]) if i<len(cache) else dict(slot=i,name=f'파티원 {i+1}',effects=[]) for i in range(6)]
        heading=self._background_header(frame) if not detail_open else None
        card_frame=party_card_frame(frame,heading)
        dx,dy=party_screen_offset(heading)
        portraits=[background_portrait_image(card_frame,i) if not detail_open else profiles[i].get('portrait_png','') for i in range(6)]
        signatures=[background_portrait(card_frame,i,entry=True) if not detail_open else np.asarray(profiles[i].get('portrait',[])) for i in range(6)]
        full=slots is None
        if full and not detail_open and not settings.get('party_force_refresh'):
            matched=[]
            for i,signature in enumerate(signatures):
                item=profiles[i]
                if not item.get('number') or item.get('portrait_image_version')!=4 or not item.get('skill_panel_png') or np.asarray(item.get('entry_level_signature',[])).shape!=(10,40) or float(np.abs(np.asarray(item.get('entry_level_signature'),dtype=np.int16)-np.asarray(background_entry_level_signature(card_frame,i))).mean())>1.8 or np.asarray(item.get('portrait',[])).shape!=signature.shape or float(np.abs(np.asarray(item['portrait'],dtype=np.int16)-signature).mean())>=8:break
                matched.append(background_profile_merge(dict(item,slot=i,portrait_png=portraits[i],portrait_image_version=4),settings))
            if len(matched)==6:return matched,frame
        targets=[(start_slot+i)%6 for i in range(6)] if full else list(slots)
        if not targets or any(i not in range(6) for i in targets):raise RuntimeError('읽을 파티 슬롯이 올바르지 않습니다.')
        first=targets[0]
        if not detail_open:
            self._background_queue.put(('status',f'{first+1}번 캐릭터 상세 열기 · 4.2초 길게 누름 · F10 중지'))
            bounds=background_entry_card_rects(card_frame)[first];x=(bounds[0]+bounds[2])/2+dx
            device.swipe(x,310+dy,x,310+dy,4200)
        previous=None;first_profile=None
        for index,slot in enumerate(targets):
            self._background_queue.put(('status',f'파티 {slot+1}번 정보 읽는 중 · {index+1}/{len(targets)} · F10 중지'))
            profile=self._party_wait_detail(device,slot,signatures[slot])
            if previous is not None and profile['number']==previous['number']:
                known_same=(profiles[slot].get('number')==profile['number'] and profiles[targets[index-1]].get('number')==profile['number'])
                if not known_same:
                    for _ in range(2):
                        profile=self._party_wait_detail(device,slot,signatures[slot])
                        if profile['number']!=previous['number']:break
                    else:raise RuntimeError('다음 캐릭터로 전환됐는지 확인하지 못했습니다. 저장된 슬롯은 유지됩니다. 선택한 슬롯만 다시 읽어주세요.')
            if detail_open and profiles[slot].get('number')!=profile['number']:profile['portrait']=[]
            if portraits[slot] and (not detail_open or profiles[slot].get('number')==profile['number']):profile.update(portrait_png=portraits[slot],portrait_image_version=4)
            if not detail_open:profile['entry_level_signature']=background_entry_level_signature(card_frame,slot)
            profile=background_party_update(profiles[slot],profile)
            profiles[slot]=profile
            if first_profile is None:first_profile=profile
            # Save each confirmed slot immediately, even if a later slot fails.
            self._background_queue.put(('party_member',slot,profile))
            previous=profile
            if index+1<len(targets):
                device.tap(1568,490)
        if full:
            device.tap(1568,490)
            returned=self._party_wait_detail(device,first,signatures[first])
            old_sig=np.asarray(first_profile['detail_signature']);new_sig=np.asarray(returned['detail_signature'])
            if returned['number']!=first_profile['number'] or float(np.abs(old_sig-new_sig).mean())>35:
                raise RuntimeError('첫 캐릭터로 돌아온 것을 확인하지 못했습니다. 개별 저장한 정보는 유지됩니다.')
        device.back()
        if self._background_cancel.wait(.5):raise RuntimeError('파티 읽기를 중지했습니다. 이미 읽은 정보는 유지됩니다.')
        frame=device.capture()
        returned_heading=self._background_header(frame)
        if returned_heading not in ('잠입확인','팀'):raise RuntimeError('파티 정보를 저장했지만 팀/잠입 확인 화면으로 복귀하지 못했습니다.')
        card_frame=party_card_frame(frame,returned_heading)
        if detail_open:
            for slot in targets:
                profiles[slot].update(portrait_png=background_portrait_image(card_frame,slot),portrait_image_version=4,entry_level_signature=background_entry_level_signature(card_frame,slot))
                self._background_queue.put(('party_member',slot,profiles[slot]))
        return profiles,frame

    def _background_use_skill(self,device,frame,slot):
        assert_action_active(self)
        if self._background_cancel.is_set() or slot not in background_ready_slots(frame):return False
        device.tap((.057+.0875*slot)*1600,740)
        if self._background_cancel.wait(.4):raise RuntimeError('재료 파밍을 중지했습니다.')
        after=device.capture();tokens=self._run_ocr_tokens(after)
        allowed={'사용','사용하기','스킬사용','발동'}
        matches=[t for t in tokens if t.score>=.90 and _compact_ocr_text(t.text) in allowed and t.cy>after.height*.45]
        close=[t for t in tokens if t.score>=.85 and _compact_ocr_text(t.text)=='닫기']
        if len(matches)==1 and close:
            device.tap(matches[0].cx,matches[0].cy)
        elif matches:
            raise RuntimeError('스킬 사용 화면의 닫기·사용하기 버튼을 확정하지 못했습니다.')
        elif background_board(after) is not None and slot not in background_ready_slots(after):
            return True
        else:raise RuntimeError('스킬 사용 화면을 확인하지 못해 파밍을 멈췄습니다.')
        for _ in range(3):
            if self._background_cancel.wait(.35):raise RuntimeError('재료 파밍을 중지했습니다.')
            current=device.capture()
            if background_board(current) is not None and slot not in background_ready_slots(current):return True
        raise RuntimeError('사용하기 이후 스킬 발동을 확인하지 못했습니다.')

    def _background_team_goals(self,tokens):
        text=_compact_ocr_text(' '.join(t.text for t in tokens if t.score>=.82 and 480<t.cy<740))
        goals=self._background_run_goals
        if re.search(r'5(?:개)?속성.*(?:동시|공격)',text):
            if re.search(r'남은조작시간[이가]*4초이상',text) and re.search(r'6콤보이상',text):goals=master_multicolor_goals()
            elif not master_multicolor(goals):goals=(False,False,True,False,False,False,False)
        found=re.search(r'(\d{1,2})콤보이상',text)
        required=int(found.group(1)) if found and 1<=int(found.group(1))<=15 else 0
        return goals,required

    def _background_low_hp(self,frame):
        crop=frame.crop((580,875,855,930))
        readings=[]
        for scale in (3,4):
            text,score=self._recognize_title_line(crop.resize((crop.width*scale,crop.height*scale),Image.Resampling.LANCZOS))
            found=re.fullmatch(r'\s*(\d{1,7})\s*/\s*(\d{1,7})\s*',text)
            readings.append(tuple(map(int,found.groups())) if found and score>=.85 else None)
        return (len(readings)==2 and readings[0] is not None and readings[0]==readings[1]
                and 0<readings[0][1] and readings[0][0]/readings[0][1]<.45)

    def _background_battle_turn(self,device,frame,profiles,goals,team_min):
        used=set()
        applied=set()
        math_spent=0.0;casts=0;searches=0
        for attempt in range(12):
            observed=background_board(frame)
            if observed is None:return False
            board,spikes=observed['board'],observed['spikes']
            condition=self._read_combo_condition(frame,board,store=False)
            self._manual_error_snapshot=dict(board=list(board),board_image=observed['crop'].copy(),game_image=frame.copy(),
                path=[],spikes=list(spikes),goals=list(goals),combo_condition=condition,phase='background_search',
                source='adb_background',version=APP_VERSION,board_rect=list(observed['rect']),captured_local=time.strftime('%Y-%m-%d %H:%M:%S'))
            self._background_queue.put(('board',board,spikes,observed['crop'].copy()))
            if not condition['confirmed'] and not master_multicolor(goals):raise RuntimeError('적 콤보 조건 미확인 · 파밍을 중지했습니다.')
            _,seconds=self._restricted_drag_move_cap(image=frame,report=False)
            required=max(team_min,condition['required_total'],6 if master_multicolor(goals) else 0);bonus=condition['combo_bonus']
            shortage=bool(goals[2] and not five_attribute_possible_by_count(board))
            ready=background_ready_slots(frame)
            settings=getattr(self,'_background_run_settings',{})
            rgb=np.asarray(frame);strip=rgb[round(.875*940):round(.925*940),:870]
            awoken_void=int(((strip[...,0]>160)&(strip[...,1]<90)&(strip[...,2]<100)).sum())>1800
            possible=sum(board.count(c)//3 for c in COLOR_ORDER)+bonus
            state=background_status_text(self._run_ocr_tokens(frame)) if any(p.get('effects') for p in profiles) else {}
            state.update(background_template_states(frame,settings))
            state['void_shield']=state.get('void_shield',False) or background_enemy_void(frame)
            state.update(shortage=shortage,rainbow=bool(goals[2]),short_time=seconds is not None and seconds<float(settings.get('background_normal_seconds',10)),
                         combo_short=required>bonus and (possible<required or bool(condition.get('required_total')) and bonus==0),
                         locked=shortage and background_lock_count(frame)>0,
                         low_hp=self._background_low_hp(frame) if any('heal' in p.get('effects',[]) or 'shield' in p.get('effects',[]) for p in profiles) else False)
            state['awoken_void']=state.get('awoken_void',False) or awoken_void
            for flag,effect in (('short_time','time'),('bound','bind_clear'),('awoken_void','awoken_clear'),('void_shield','void_pierce'),('attack_down','attack'),('combo_short','bonus')):
                if effect in applied:state[flag]=False
            choice,reason=background_skill_decision(profiles,ready,state,used)
            if choice is not None and casts<8:
                used.add(choice);casts+=1
                self._background_queue.put(('status',f'자동 스킬 · {profiles[choice]["name"]} · {reason} · F10 중지'))
                if not self._background_use_skill(device,frame,choice):raise RuntimeError('스킬 발동을 확인하지 못해 파밍을 중지했습니다.')
                applied.update(profiles[choice].get('effects',[]))
                if 'haste' in profiles[choice].get('effects',[]):used={i for i in used if 'haste' in profiles[i].get('effects',[])}
                if self._background_cancel.wait(.6):return False
                self._background_queue.put(('status','스킬 사용 완료 · 판과 콤보 가산 다시 확인 · F10 중지'))
                frame=device.capture();continue
            survival=master_multicolor(goals) and seconds is not None and (seconds<=5 or shortage or not condition['confirmed'])
            operation_seconds=route_operation_seconds(seconds,master_survival_goals() if survival else goals)
            cap=device.move_cap(operation_seconds)
            effective=master_survival_goals(required,bonus) if survival else tuple(False for _ in goals) if shortage else goals
            # With no useful skill and insufficient colours, charge skills by playing combos.
            combo_turn=shortage and choice is None and not master_multicolor(goals)
            target=combo_condition_goals(effective,0 if combo_turn else required,bonus)
            if survival:self._background_queue.put(('status','달인 다색 · 공격 조건 부족 · 6콤보 이상 생존 경로 · F10 중지'))
            if combo_turn:self._background_queue.put(('status','5속성 부족 · 사용 가능한 대응 스킬 없음 · 콤보로 턴 진행 · F10 중지'))
            if not combo_turn:self._background_queue.put(('status',f'백그라운드 경로 탐색 · 적/팀 {required}콤보 이상 · 가산 +{bonus} · F10 중지'))
            search_ceiling=7.0 if master_multicolor(goals) and not survival else 8.0
            budget=min(route_search_budget(seconds,3.0 if searches==0 else 1.0),max(0.0,search_ceiling-math_spent))
            if budget<=0 and master_multicolor(goals) and not survival:
                survival=True;effective=master_survival_goals(6,bonus);target=effective;cap=device.move_cap(seconds)
                budget=max(0.0,min(1.0,8.0-math_spent))
                self._background_queue.put(('status','달인 다색 · 공격 탐색 한도 · 6콤보 생존 경로로 전환 · F10 중지'))
            if budget<=0:raise RuntimeError('탐색 실패 · 계산 대기 한도 도달 · 파밍 중지')
            math_started=time.monotonic();searches+=1
            if effective[2]:
                path,_,_,after=solve_five_attributes_v101(board,time_budget=budget,max_moves=cap,spikes=spikes,
                    avoid_skull=False,required_total=required,combo_bonus=bonus,seed_offset=attempt,cancel_event=self._background_cancel)
            else:path,_,_,after=solve_path(board,time_budget=budget,max_moves=cap,goals=target,spikes=spikes,avoid_skull=False,seed_offset=attempt,cancel_event=self._background_cancel)
            math_spent+=time.monotonic()-math_started
            if self._background_cancel.is_set():return False
            details=board_goal_metrics(after,target)
            if not details['all_met'] or details['combos']<1:
                if math_spent>=8.0:raise RuntimeError('탐색 실패 · 계산 대기 한도 도달 · 파밍 중지')
                retry_state=dict(state,shortage=bool(goals[2]),locked=bool(goals[2]) and background_lock_count(frame)>0,search_failed=True)
                choice,_=background_skill_decision(profiles,ready,retry_state,used)
                if choice is not None and casts<8:
                    used.add(choice);casts+=1
                    self._background_queue.put(('status',f'경로 미발견 · 다른 스킬 사용 · {profiles[choice]["name"]} · F10 중지'))
                    if not self._background_use_skill(device,device.capture(),choice):raise RuntimeError('스킬 발동을 확인하지 못했습니다.')
                    applied.update(profiles[choice].get('effects',[]))
                    if 'haste' in profiles[choice].get('effects',[]):used={i for i in used if 'haste' in profiles[i].get('effects',[])}
                    if self._background_cancel.wait(.7):return False
                    frame=device.capture();continue
                # A five-colour miss may try combos, retaining the enemy floor.
                if effective[2]:
                    if master_multicolor(goals):
                        survival=True;effective=master_survival_goals(required,bonus)
                        cap=device.move_cap(seconds)
                        self._background_queue.put(('status','달인 다색 공격 경로 미발견 · 6콤보 이상 생존 경로 · F10 중지'))
                    target=master_survival_goals(6,bonus) if survival else combo_condition_goals((False,)*7,required,bonus)
                    remaining=max(0.0,min(2.0,8.0-math_spent))
                    if remaining<=0:raise RuntimeError('탐색 실패 · 계산 대기 한도 도달 · 파밍 중지')
                    math_started=time.monotonic()
                    path,_,_,after=solve_path(board,time_budget=remaining,max_moves=cap,goals=target,spikes=spikes,avoid_skull=False,cancel_event=self._background_cancel)
                    math_spent+=time.monotonic()-math_started
                    details=board_goal_metrics(after,target)
                if not details['all_met'] or details['combos']<1:
                    raise RuntimeError(f'탐색 실패 · 최종 {details["final_combos"]}/{required}콤보 · 파밍 중지')
            if len(path)==1:
                # A pre-matched skill board still needs a real cell movement.
                # Swap equal-colour neighbours so the verified matches stay intact.
                pairs=[(i,j) for i in range(30) for j in neighbors(i) if board[i]==board[j]]
                if not pairs:raise RuntimeError('이미 맞춰진 판의 시작 이동을 확인하지 못했습니다.')
                path=list(min(pairs,key=lambda pair:int(spikes[pair[0]])+int(spikes[pair[1]])))
            fresh=device.capture();current=background_board(fresh)
            if current is None:return False
            next_condition=self._read_combo_condition(fresh,current['board'],store=False)
            _,now_seconds=self._restricted_drag_move_cap(image=fresh,report=False)
            if (current['board']!=board or current['spikes']!=spikes or now_seconds!=seconds
                    or (not survival and not next_condition['confirmed'])
                    or any(next_condition[k]!=condition[k] for k in (('combo_bonus',) if survival else ('required_total','combo_bonus')))):
                frame=fresh;continue
            if len(path)-1>device.move_cap(route_operation_seconds(now_seconds,master_survival_goals() if survival else goals)):raise RuntimeError('제한시간부족 · 입력 직전 파밍 경로를 보류했습니다.')
            # Keep one pre-move original in RAM; files are written only on manual save.
            self._manual_error_snapshot=dict(board=list(board),board_image=current['crop'].copy(),game_image=fresh.copy(),
                path=list(path),spikes=list(spikes),goals=list(effective),combo_condition=condition,
                predicted_combos=details['combos'],predicted_final_combos=details['final_combos'],required_final_combos=details['required_total'] if survival else required,enemy_required_final_combos=required,
                phase='background_before_drag',combo_turn_for_cooldown=combo_turn,master_survival=survival,source='adb_background',version=APP_VERSION,board_rect=list(current['rect']),captured_local=time.strftime('%Y-%m-%d %H:%M:%S'))
            self._background_queue.put(('board',board,spikes,current['crop'].copy()))
            device.drag(current['rect'],path,route_operation_seconds(now_seconds,master_survival_goals() if survival else goals))
            self._background_queue.put(('status',('달인 다색 생존 이동 완료' if survival else '콤보로 턴 진행 완료' if combo_turn else '백그라운드 이동 완료')+f' · 최종 {details["final_combos"]}콤보 예상 · F10 중지'))
            if self._background_cancel.wait(3.0):return False
            return True
        raise RuntimeError('판 또는 상태가 계속 바뀌어 파밍을 중지했습니다.')

    def _stage_db_path(self):
        return self.data_paths['ledger'].parent/'stage_db.json'

    def _stage_db_load(self):
        data=_load_json_dict(self._stage_db_path())
        data.setdefault('version',1);data.setdefault('stages',{})
        return data

    def _stage_save(self,number,**values):
        assert_action_active(self)
        with self._crystal_db_lock:
            db=self._stage_db_load();row=db['stages'].setdefault(number,{'number':number})
            row.update(values);row['updated_at']=time.time()
            _atomic_json_write(self._stage_db_path(),db)

    def _stage_save_image(self,image):
        assert_action_active(self)
        import io
        stream=io.BytesIO();image.save(stream,format='PNG');data=stream.getvalue()
        name=hashlib.sha256(data).hexdigest()+'.png'
        folder=self._stage_db_path().parent/'stage_images';folder.mkdir(parents=True,exist_ok=True)
        path=folder/name
        if not path.exists():path.write_bytes(data)
        return name

    def _nav_pause(self,device,seconds=.45):
        assert_action_active(self)
        if self._background_cancel.wait(seconds):raise RuntimeError('재료 파밍을 중지했습니다.')
        return device.capture()

    def _nav_wait(self,device,headings,seconds=10):
        deadline=time.monotonic()+seconds;previous=None
        while time.monotonic()<deadline:
            frame=self._nav_pause(device)
            heading=self._background_header(frame)
            if heading in headings and heading==previous:return frame
            previous=heading
        raise RuntimeError('화면 전환 확인 실패: '+', '.join(headings))

    def _nav_text_button(self,device,frame,pattern,min_y=100):
        found=[t for t in self._run_ocr_tokens(frame) if t.score>=.85 and t.cy>=min_y and re.fullmatch(pattern,_compact_ocr_text(t.text))]
        if len(found)!=1:raise RuntimeError('이동 버튼을 확정하지 못했습니다: '+pattern)
        device.tap(found[0].cx,found[0].cy)

    def _nav_detail_matches(self,frame,name):
        wanted=crystal_name_key(name);settings=self._background_run_settings
        numbers=set()
        for row in settings.get('background_party_profiles',[])+monster_catalog().get('monsters',[]):
            if crystal_name_key(row.get('name') or row.get('name_ko',''))==wanted and str(row.get('number','')).isdigit():numbers.add(str(row['number']))
        for number,known in settings.get('monster_korean_names',{}).items():
            if crystal_name_key(known)==wanted:numbers.add(str(number))
        _,observed=self._awakening_screen_identity(frame)
        if observed is not None and len(numbers)==1:return str(observed) in numbers
        # Tight glyph crops avoid feeding a short name plus a long blank strip to OCR.
        crop=frame.crop((940,238,1548,290))
        _,views=material_title_line_views(crop)
        if len(views)!=2:views=[crop.resize((crop.width*scale,crop.height*scale),Image.Resampling.LANCZOS) for scale in (2,3)]
        readings=[self._recognize_title_line(view) for view in views]
        if all(score>=.85 and crystal_name_key(text)==wanted for text,score in readings):return True
        if all(score>=.9 and crystal_name_key(text) for text,score in readings) and crystal_name_key(readings[0][0])==crystal_name_key(readings[1][0]):return False
        return None

    def _nav_target_detail(self,device,frame,name):
        wanted=crystal_name_key(name)
        cache=self._background_run_settings.get('background_party_profiles',[])
        slots=[i for i,row in enumerate(cache) if crystal_name_key(row.get('name',''))==wanted]
        opened_target=False
        for _ in range(10):
            heading=self._background_header(frame)
            if heading=='크리처상세':
                for attempt in range(3):
                    matched=self._nav_detail_matches(frame,name)
                    if matched is True:return frame
                    if matched is False:break
                    if attempt<2:frame=self._nav_pause(device,.5)
                if matched is None:raise RuntimeError('상세 이름·몬스터 번호 확인 실패 · 팀 화면을 반복해서 열지 않았습니다. 대상: '+name)
                if opened_target:raise RuntimeError('선택한 캐릭터가 돌파 대상과 다릅니다. 파티 정보를 다시 확인해 주세요. 대상: '+name)
                device.back();frame=self._nav_pause(device);continue
            if heading in ('팀','잠입확인') and len(slots)==1:
                card=party_card_frame(frame,heading);box=background_entry_card_rects(card)[slots[0]];dx,dy=party_screen_offset(heading)
                x=(box[0]+box[2])/2+dx;device.swipe(x,310+dy,x,310+dy,4200);opened_target=True
                frame=self._nav_wait(device,{'크리처상세'});continue
            if heading=='결과':
                self._nav_text_button(device,frame,r'다음',600);frame=self._nav_pause(device);continue
            if heading=='연전확인':
                self._nav_text_button(device,frame,r'취소|닫기',600);frame=self._nav_pause(device);continue
            # Main screen exposes the green training button.
            tokens=self._run_ocr_tokens(frame)
            team=[t for t in tokens if t.score>=.9 and _compact_ocr_text(t.text)=='팀' and t.cy>500]
            if len(slots)==1 and len(team)==1:
                device.tap(team[0].cx,team[0].cy);frame=self._nav_wait(device,{'팀'});continue
            training=[t for t in tokens if t.score>=.9 and _compact_ocr_text(t.text)=='육성' and t.cy>500]
            if len(training)==1:
                device.tap(training[0].cx,training[0].cy);frame=self._nav_wait(device,{'육성'})
                # The search field is fixed in the confirmed training page.
                device.tap(720,280);device.search_text(name)
                frame=self._nav_pause(device,.8)
                search=self._run_ocr_tokens(frame.crop((100,245,1460,310)))
                if not any(t.score>=.85 and crystal_name_key(t.text)==wanted for t in search):raise RuntimeError('육성 검색어 입력을 확인하지 못했습니다. 게임에서 이름을 검색한 뒤 다시 시작해 주세요.')
                # Only choose a single filtered creature; never choose the first of many.
                # Level labels provide one marker per visible card, independently of attribute.
                levels=[t for t in self._run_ocr_tokens(frame) if t.score>=.8 and re.fullmatch(r'(?:Lv\.?|LV\.?|lv\.?)[0-9]+',t.text.replace(' ','')) and t.cy>380]
                if len(levels)!=1:raise RuntimeError('육성 검색 결과가 한 캐릭터인지 확인하지 못했습니다.')
                x=levels[0].cx;y=levels[0].cy-85;device.swipe(x,y,x,y,4200);opened_target=True
                frame=self._nav_wait(device,{'크리처상세'});continue
            device.back();frame=self._nav_pause(device)
        raise RuntimeError('돌파 대상의 크리처 상세 화면에 도달하지 못했습니다.')

    def _stage_register_preview(self,device,frame,number,templates):
        if self._background_header(frame)!='출현아이템':raise RuntimeError('출현 아이템 화면을 확인하지 못했습니다.')
        drops=[];seen=[]
        for page in range(8):
            boxes=detect_reward_card_rects(frame,viewport=(.23,.77,.215,.90))
            if not boxes:raise RuntimeError('출현 크리스탈 목록을 읽지 못했습니다.')
            for box in boxes:
                crop=frame.crop(box);signature=crystal_art_signatures(crop)
                if any(min(float(np.abs(a-b).mean()) for a,b in zip(signature,old))<3 for old in seen):continue
                seen.append(signature);cid=self._farming_identify_art(crop,templates)
                drops.append(dict(id=cid,image_file=self._stage_save_image(crop)))
            before=frame.crop((460,240,1160,850))
            device.swipe(1140,770,1140,380,450);following=self._nav_pause(device)
            if self._background_header(following)!='출현아이템':raise RuntimeError('출현 목록 등록 중 화면이 바뀌었습니다.')
            if float(np.abs(np.asarray(before,dtype=np.int16)-np.asarray(following.crop((460,240,1160,850)),dtype=np.int16)).mean())<1.0:break
            frame=following
        else:raise RuntimeError('출현 목록의 끝을 확인하지 못했습니다.')
        # Preview ×N is owned stock, never a reward or per-run yield.
        self._stage_save(number,drops=drops,preview_verified=True)

    def _stage_entry_metadata(self,frame):
        tokens=self._run_ocr_tokens(frame);number=self._background_stage(frame,tokens)
        crop=frame.crop((745,45,1060,104));values=[]
        for scale in (2,3):
            text,score=self._recognize_title_line(crop.resize((crop.width*scale,crop.height*scale)))
            match=re.search(r'배틀\s*(\d{1,2})',text)
            values.append(int(match.group(1)) if match and score>=.85 else None)
        if values[0] and values[0]==values[1]:self._stage_save(number,battles=values[0])
        else:self._stage_save(number)
        return number

    def _background_prepare_entry(self,device,frame,key,templates):
        with self._crystal_db_lock:ledger=self._load_crystal_ledger()
        rows=[row for row in farming_group_rows(ledger,key) if row['deficit']>0]
        if not rows:raise RuntimeError('부족한 재료가 없습니다.')
        deficits={row['id']:row['deficit'] for row in rows}
        heading=self._background_header(frame)
        if heading=='출현아이템':
            number=self._background_stage(frame,self._run_ocr_tokens(frame))
            if not self._stage_db_load()['stages'].get(number,{}).get('preview_verified'):
                self._stage_register_preview(device,frame,number,templates)
            device.back();frame=self._nav_wait(device,{'크리스탈획득던전'})
            return self._background_pick_stage(device,frame,deficits,templates)
        if heading=='크리스탈획득던전':return self._background_pick_stage(device,frame,deficits,templates)
        def confirmation(image):
            header=breakthrough_button_candidate(image)
            if header is None:return False
            _,views=material_title_line_views(header)
            return confirmed_breakthrough_heading([self._recognize_title_line(view) for view in views]) is True
        if not confirmation(frame):
            name=ledger['farming_groups'][key]['name']
            frame=self._nav_target_detail(device,frame,name)
            self._nav_text_button(device,frame,r'(?:(?:초절|초|최종)?한계|중간|최종)?돌파')
            for _ in range(8):
                frame=self._nav_pause(device)
                if confirmation(frame):break
            else:raise RuntimeError('돌파 확인 화면의 재료 목록을 확인하지 못했습니다.')
        material=frame.crop((145,240,880,530))
        candidates=self._auto_material_targets(material,[])
        available=[]
        for item in candidates:
            cid=self._farming_identify_art(item['crop'],templates)
            if cid in {row['id'] for row in rows}:available.append((cid,item))
        if not available:raise RuntimeError('돌파 확인창의 부족 재료를 등록 이미지와 연결하지 못했습니다.')
        # Inspect the material with the largest remaining shortage first.
        deficits={row['id']:row['deficit'] for row in rows}
        best_known=choose_farming_stage(self._stage_db_load(),deficits)
        best_ids={str(d.get('id')) for d in self._stage_db_load()['stages'].get(best_known,{}).get('drops',[]) if d.get('id')}
        preferred=[pair for pair in available if pair[0] in best_ids]
        cid,item=max(preferred or available,key=lambda pair:deficits[pair[0]])
        box=item['crop_box'];device.tap(145+(box[0]+box[2])/2,240+(box[1]+box[3])/2)
        frame=self._nav_wait(device,{'크리스탈획득던전'})
        return self._background_pick_stage(device,frame,deficits,templates)

    def _stage_resolve_known(self,templates):
        db=self._stage_db_load()
        for stage_number,stage_row in db['stages'].items():
            changed=False
            for drop in stage_row.get('drops',[]):
                if drop.get('id'):continue
                path=self._stage_db_path().parent/'stage_images'/drop['image_file']
                if path.is_file():
                    with Image.open(path) as image:resolved=self._farming_identify_art(image,templates)
                    if resolved:drop['id']=resolved;changed=True
            if changed:self._stage_save(stage_number,drops=stage_row['drops'])
        return db

    def _background_pick_stage(self,device,frame,deficits,templates):
        tokens=self._run_ocr_tokens(frame)
        stage_entries=stage_list_entries(frame,tokens,self._stage_db_load())
        stage_tokens=[(entry['number'],entry['token']) for entry in stage_entries]
        if not stage_tokens:raise RuntimeError('획득 가능한 스테이지 번호를 읽지 못했습니다.')
        for number,t in stage_tokens:
            db=self._stage_db_load();saved=db['stages'].get(number,{})
            if saved.get('preview_verified') and saved.get('image_file'):continue
            image=frame.crop((max(0,int(t.cx-67)),max(40,int(t.cy-180)),min(1600,int(t.cx+67)),int(t.cy-12)))
            self._stage_save(number,image_file=self._stage_save_image(image))
            if saved.get('preview_verified'):continue
            # Medal is immediately above this stage illustration.
            device.tap(*next(entry['medal'] for entry in stage_entries if entry['number']==number));preview=self._nav_wait(device,{'출현아이템'})
            self._stage_register_preview(device,preview,number,templates)
            device.back();frame=self._nav_wait(device,{'크리스탈획득던전'})
        db=self._stage_resolve_known(templates)
        best=choose_farming_stage(db,deficits,[n for n,t in stage_tokens])
        if best is None:raise RuntimeError('부족 재료를 얻는 스테이지를 DB에서 확인하지 못했습니다.')
        t=next(t for number,t in stage_tokens if number==best)
        device.tap(t.cx,t.cy-90);frame=self._nav_wait(device,{'잠입확인'})
        actual=self._stage_entry_metadata(frame)
        if actual!=best:raise RuntimeError('선택한 스테이지와 잠입 확인 번호가 다릅니다.')
        self._background_queue.put(('status',f'파밍 스테이지 ★{actual} · 부족 재료 포함 종류 우선 선택'))
        return frame

    def _background_worker(self,key,groups,templates):
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=getattr(self,'_stop_epoch',0)
        device=self._background_device;cancel=self._background_cancel
        message='백그라운드 파밍을 중지했습니다.'
        try:
            frame=device.connect()
            self._background_queue.put(('status','화면 캡처: '+getattr(device,'last_capture_method','ADB')+' · '+getattr(device,'capture_fallback_reason','')))
            entry=self._background_header(frame)=='잠입확인'
            known_stage=self._background_stage(frame,self._run_ocr_tokens(frame)) if entry else None
            with self._crystal_db_lock:current=self._load_crystal_ledger()
            deficits={r['id']:r['deficit'] for r in farming_group_rows(current,key)}
            if not entry or not stage_has_shortage(self._stage_resolve_known(templates),known_stage,deficits):
                frame=self._background_prepare_entry(device,frame,key,templates)
            reference=getattr(self,'_background_entry_reference',None)
            if reference is not None and any(float(np.abs(background_portrait(frame,i,True)-background_portrait(reference,i,True)).mean())>20 for i in range(6)):
                raise RuntimeError('선택한 ADB 기기의 파티가 버튼을 누른 게임과 다릅니다.')
            tokens=self._run_ocr_tokens(frame);stage=self._background_stage(frame,tokens)
            self._stage_entry_metadata(frame)
            goals,team_min=self._background_team_goals(tokens)
            profiles=background_cached_party(frame,self._background_run_settings)
            missing=[str(i+1) for i,p in enumerate(profiles) if not p.get('cached_party_match')]
            if missing:self._background_queue.put(('status','저장된 스킬 정보 없음: '+', '.join(missing)+'번 · 해당 스킬은 건너뛰고 퍼즐을 진행합니다.'))
            known=sum(bool(p['effects']) for p in profiles)
            self._background_queue.put(('status',f'재료 파밍 시작 · {groups[key]["name"]} · 자동 스킬 {known}/6 확인 · F10 중지'))
            device.tap(*self._background_action(self._run_ocr_tokens(frame),{'도전하기'}))
            session=None;credited=False;gold_next_sent=False;candidate=None;repeats=0
            last_action=time.monotonic();last_progress=last_action;last_header=None;awaiting_change=None
            while not cancel.wait(.35):
                with self._crystal_db_lock:
                    ledger=self._load_crystal_ledger()
                    if background_target_key(ledger)!=key or ledger.get('farming_groups')!=groups:raise RuntimeError('목표 재료 목록이 바뀌어 파밍을 중지했습니다.')
                    if background_goal_complete(ledger,key,groups[key]['revision'],getattr(self,'_background_selected_material_ids',None)):
                        message=f'{groups[key]["name"]} · 목표 재료 모두 충족 · 파밍 완료';break
                frame=device.capture();observed=background_board(frame)
                if observed is not None:
                    if awaiting_change is not None:
                        if tuple(observed['board'])==awaiting_change:
                            if time.monotonic()-last_action>8:raise RuntimeError('자동 이동 후 퍼즐판 변화를 확인하지 못해 파밍을 중지했습니다.')
                            continue
                        awaiting_change=None
                    signature=(tuple(observed['board']),tuple(observed['spikes']))
                    repeats=repeats+1 if candidate==signature else 1;candidate=signature
                    if repeats<2:continue
                    if credited:
                        session=None;credited=False;gold_next_sent=False
                    if session is None:
                        session=uuid.uuid4().hex;credited=False
                        with self._crystal_db_lock:
                            ledger=self._load_crystal_ledger()
                            ledger['reward_session']=dict(id=session,open=True,credited=False,amounts={},source='background_farming')
                            _atomic_json_write(self.data_paths['ledger'],ledger)
                    if any(float(np.abs(background_portrait(frame,i)-np.asarray(profiles[i]['portrait'],dtype=np.int16)).mean())>28 for i in range(6)):
                        raise RuntimeError('전투 파티가 등록한 파티와 달라 자동 스킬 입력을 중지했습니다.')
                    acted=self._background_battle_turn(device,frame,profiles,goals,team_min)
                    if acted:awaiting_change=signature[0];last_action=time.monotonic()
                    last_progress=time.monotonic();last_header=None;candidate=None;continue
                candidate=None;repeats=0
                header=self._background_header(frame)
                if header is None:
                    if time.monotonic()-last_progress>40:raise RuntimeError('40초 동안 전투/결과 화면을 확인하지 못해 파밍을 멈췄습니다.')
                    continue
                if time.monotonic()-last_action<.8:continue
                if header=='연전확인' and last_header=='lobby_clicked':
                    if time.monotonic()-last_action>8:raise RuntimeError('연전 시작 뒤 화면 전환을 확인하지 못했습니다.')
                    continue
                if header=='결과':
                    if session is None:raise RuntimeError('이번 파밍의 전투 기록이 없어 보상을 자동 집계하지 않았습니다.')
                    if self._looks_like_reward_frame(frame):
                        if gold_next_sent:
                            if time.monotonic()-last_action>8:raise RuntimeError('크리스탈 다음 버튼 이후 화면 전환을 확인하지 못했습니다.')
                            continue
                        if not credited:
                            rect=dict(left=0,top=0,width=1600,height=940)
                            items=self._farming_collect_pages(rect,frame,templates)
                            amounts=self._farming_match_items(items,groups,templates)
                            if cancel.is_set():break
                            final_frame=device.capture()
                            if self._background_header(final_frame)!='결과' or not self._looks_like_reward_frame(final_frame):
                                raise RuntimeError('집계 중 보상창이 바뀌었습니다.')
                            with self._crystal_db_lock:
                                current=self._load_crystal_ledger()
                                if background_target_key(current)!=key:raise RuntimeError('집계 중 목표 재료가 바뀌었습니다.')
                                ledger,changed=credit_farming_result(current,session,amounts,groups)
                                if changed:_atomic_json_write(self.data_paths['ledger'],ledger)
                            credited=True;self._background_queue.put(('credited',amounts))
                            if background_goal_complete(ledger,key,material_ids=getattr(self,'_background_selected_material_ids',None)):message=f'{groups[key]["name"]} · 목표 재료 모두 충족 · 파밍 완료';break
                        stage_row=self._stage_db_load()['stages'].get(stage,{})
                        remaining={r['id']:r['deficit'] for r in farming_group_rows(ledger,key) if r['deficit']>0}
                        current_ids={str(d.get('id')) for d in stage_row.get('drops',[]) if d.get('id')}
                        if stage_row.get('preview_verified') and not (current_ids&set(remaining)):
                            frame=self._background_prepare_entry(device,device.capture(),key,templates)
                            stage=self._background_stage(frame,self._run_ocr_tokens(frame))
                            profiles=background_cached_party(frame,self._background_run_settings)
                            device.tap(*self._background_action(self._run_ocr_tokens(frame),{'도전하기'}))
                            session=None;credited=False;gold_next_sent=False;candidate=None;repeats=0;awaiting_change=None
                            last_action=time.monotonic();last_progress=last_action;last_header=None
                            continue
                        device.tap(*self._background_action(self._run_ocr_tokens(device.capture()),{'다음'}))
                        gold_next_sent=True;last_action=time.monotonic();last_progress=last_action
                        continue
                    elif credited:
                        if time.monotonic()-last_action>8:raise RuntimeError('집계 후 다음 화면을 확인하지 못했습니다.')
                        continue
                    # A white mana page must be positively identified. A missed
                    # gold crystal page must NEVER be skipped without accounting.
                    tokens=self._run_ocr_tokens(frame)
                    if not any(t.score>=.85 and _compact_ocr_text(t.text)=='획득' and 160<t.cy<245 for t in tokens):
                        raise RuntimeError('보상 페이지 종류를 확인하지 못해 다음 버튼을 누르지 않았습니다.')
                    # White mana page or credited crystal page; one click then await a change.
                    marker=_quick_icon_signature(frame.resize((160,94)))
                    if last_header==('결과',marker):
                        if time.monotonic()-last_action>8:raise RuntimeError('다음 버튼 이후 화면 전환을 확인하지 못했습니다.')
                        continue
                    device.tap(*self._background_action(tokens,{'다음'}))
                    last_header=('결과',marker);last_action=time.monotonic();last_progress=last_action
                elif header=='연전확인':
                    if not credited:raise RuntimeError('보상 집계가 끝나지 않아 다음 판을 시작하지 않았습니다.')
                    tokens=self._run_ocr_tokens(frame)
                    if self._background_stage(frame,tokens)!=stage:raise RuntimeError('재도전 스테이지가 달라 파밍을 중지했습니다.')
                    device.tap(*self._background_action(tokens,{'OK','0K'}))
                    session=None;credited=False;gold_next_sent=False;awaiting_change=None;last_header='lobby_clicked'
                    last_action=time.monotonic();last_progress=last_action
                else:raise RuntimeError('잠입 확인 화면으로 돌아와 파밍을 종료했습니다.')
        except Exception as exc:message='사용자가 백그라운드 파밍을 중지했습니다.' if cancel.is_set() else '재료 파밍 중지 · '+str(exc)
        finally:
            device.close_capture()
            self._background_queue.put(('done',message))

    def _farming_init(self):
        self._farming_queue = queue.SimpleQueue()
        self._farming_probe_busy = False
        self._farming_probe_next = 0.0
        self._farming_positive = 0
        self._farming_negative = 0
        self._farming_result_visible = False
        self._farming_lobby_visible = False
        self._farming_lobby_click_context = None
        self._farming_lobby_clicked_at = 0.0
        self._farming_lobby_title_cache = None
        self._farming_battle_seen = False
        self._farming_attempts = 0
        self._farming_retry_at = 0.0
        self._farming_scan_cancel = threading.Event()
        self._farming_stopped = False
        self._farming_after = None
        self._farming_stats = None
        self._farming_displayed_session = None
        self._farming_hidden_session = None
        self._pending_farming_materials = None
        self._farming_sync_views(self._load_crystal_ledger())
        self._farming_after = self.root.after(600, self._farming_tick)

    def _register_farming_profile(self, name, targets):
        with self._crystal_db_lock:
            ledger, key = register_farming_group(self._load_crystal_ledger(), name, targets)
            _atomic_json_write(self.data_paths['ledger'], ledger)
        self.crystal_candidates = [dict(t, card_name=name) for t in targets]
        self.card_name_detected = name
        self._pending_farming_materials = None
        self._recent_material_scan_status = '성공'
        self.resource_last_read = time.strftime('%H:%M:%S')
        if hasattr(self, 'toolbar'):
            self.toolbar.show_crystal_window()
            self.toolbar.refresh()
        self.set_status(f'{name}: 재료 {len(targets)}종 확인 · 게임에서 재료를 클릭하면 상세 이름을 자동 등록합니다.')
        return key

    def _edit_farming_group_name(self):
        ledger = self._load_crystal_ledger()
        key = ledger.get('active_farming_group')
        group = ledger.get('farming_groups', {}).get(key, {})
        name = simpledialog.askstring('돌파 대상 이름', '돌파 화면 위에 표시된 이름을 입력하세요.',
                                      initialvalue=group.get('name', ''), parent=self.root)
        if not name or not name.strip():
            return
        if self._pending_farming_materials:
            self._register_farming_profile(name.strip(), self._pending_farming_materials)
        elif group:
            with self._crystal_db_lock:
                ledger = self._load_crystal_ledger()
                ledger['farming_groups'][key].update(name=name.strip()[:60], revision=uuid.uuid4().hex)
                _atomic_json_write(self.data_paths['ledger'], ledger)
            self.card_name_detected = name.strip()[:60]
            self.toolbar.refresh()
        else:
            with self._crystal_db_lock:
                ledger,key=register_farming_owner(self._load_crystal_ledger(),name.strip())
                _atomic_json_write(self.data_paths['ledger'],ledger)
            self._farming_sync_views(ledger)
            self.toolbar.refresh()
            self.set_status(f'{name.strip()}: 돌파 대상 이름 등록 완료 · 돌파 화면에서 F7로 재료를 읽으세요.')

    def _farming_observe_board(self):
        # A real stable board also closes a lobby view if the click hook was unavailable.
        if getattr(self,'_farming_lobby_visible',False):
            self._farming_lobby_visible=False
            self._farming_lobby_click_context=None
            if self._farming_stats is not None: self._farming_stats.withdraw()
        # A real stable board, rather than F6, starts the next reward visit.
        if self._farming_battle_seen or getattr(self, '_manual_reward_busy', False):
            return
        self._farming_battle_seen = True
        self._farming_lobby_visible = False
        self._farming_lobby_click_context = None
        self._farming_result_visible = False
        self._farming_positive = 0
        self._farming_attempts = 0
        self._farming_retry_at = 0.0
        self._farming_displayed_session = None
        self._manual_reward_committed = False
        self.reward_processed_this_visit = False
        try:
            with self._crystal_db_lock:
                ledger = self._load_crystal_ledger()
                if ledger.get('reward_session', {}).get('open'):
                    ledger['reward_session']['open'] = False
                    _atomic_json_write(self.data_paths['ledger'], ledger)
        except OSError as exc:
            self.set_status(f'전투 전환 기록 저장 실패: {exc}')
            self._farming_battle_seen = False
        if self._farming_stats is not None:
            self._farming_stats.withdraw()

    def _farming_begin_result(self):
        with self._crystal_db_lock:
            ledger = self._load_crystal_ledger()
            if not ledger.get('reward_session', {}).get('open'):
                ledger['reward_session'] = {'id': uuid.uuid4().hex, 'open': True,
                                             'credited': False, 'amounts': {}}
                _atomic_json_write(self.data_paths['ledger'], ledger)
            return ledger

    def _farming_result_title_state(self, image):
        # The two-letter gray title is often skipped by text detection. Read an
        # isolated line directly as well; two agreeing views prevent guessing.
        width,height=image.size
        title=image.crop((round(width*.098),round(height*.052),
                          round(width*.145),round(height*.100)))
        tokens=self._run_ocr_tokens(title.resize((title.width*3,title.height*3),Image.Resampling.LANCZOS))
        if any(_compact_ocr_text(t.text)=='결과' and t.score>=.85 for t in tokens):
            return True
        box,views=material_title_line_views(title)
        if not box or len(views)!=2: return None
        readings=[self._recognize_title_line(view) for view in views]
        if all(_compact_ocr_text(text)=='결과' and score>=.85 for text,score in readings): return True
        if all(score>=.85 and _compact_ocr_text(text) for text,score in readings) and \
                _compact_ocr_text(readings[0][0])==_compact_ocr_text(readings[1][0]): return False
        return None

    def _farming_result_header_confirmed(self,image):
        return self._farming_result_title_state(image) is True

    def _farming_result_page_visible(self,image):
        # Covers both the white mana/experience page and the gold crystal page.
        # This controls the HUD lifetime only; credit still requires crystal art.
        rgb=np.asarray(image.convert('RGB'))
        height,width=rgb.shape[:2]
        header=rgb[round(height*.049):round(height*.098),round(width*.15):round(width*.38)]
        if not header.size or float(np.mean(header.min(axis=2)>230))<.65: return False
        return self._farming_result_title_state(image)

    def _farming_lobby_state(self, image):
        header=lobby_confirmation_title(image)
        if header is None: return False
        fingerprint=material_title_fingerprint(header)
        if not fingerprint: return False
        cache=getattr(self,'_farming_lobby_title_cache',None)
        if cache and cache[0]==fingerprint: return cache[1]
        _,views=material_title_line_views(header)
        readings=[self._recognize_title_line(view) for view in views]
        if len(readings)!=2 or min(score for _,score in readings)<.85: return False
        values=[_compact_ocr_text(text) for text,_ in readings]
        if values[0]!=values[1]: return False
        valid=values[0]=='연전확인'
        self._farming_lobby_title_cache=(fingerprint,valid)
        return valid

    def _farming_close_lobby_stats(self):
        self._farming_lobby_clicked_at=time.monotonic()
        self._farming_lobby_visible=False
        self._farming_lobby_click_context=None
        self._farming_displayed_session=None
        if self._farming_stats is not None: self._farming_stats.withdraw()

    def _farming_probe_worker(self, rect):
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=getattr(self,'_stop_epoch',0)
        try:
            image = self._farming_capture(rect)
            canonical=image.resize((1600,940),Image.Resampling.LANCZOS)
            if self._update_root_tree_context(canonical):
                self._farming_queue.put(('root_tree',))
                self._farming_queue.put(('probe',False,time.monotonic(),rect,False,False,False))
                return
            small = image.resize((800, 470), Image.Resampling.BILINEAR)
            result_heading=self._farming_result_header_confirmed(image)
            crystal_page = bool(result_heading and self._looks_like_reward_frame(small))
            lobby=self._farming_lobby_state(image)
            found = bool(not lobby and result_heading and (crystal_page or self._farming_result_page_visible(image)))
            board_visible = False
            board_rect = self.board_rect
            if board_rect:
                x,y,w,h = board_rect
                x,y = x-rect['left'],y-rect['top']
                if x >= 0 and y >= 0 and x+w <= image.width and y+h <= image.height:
                    board_image = image.crop((x,y,x+w,y+h))
                    board, _ = classify_board(board_image)
                    board_visible = _live_board_grid_is_visible(board_image, board)
                    if board_visible:
                        found = False
                        lobby = False
            self._farming_queue.put(('probe', found, time.monotonic(), rect, board_visible, crystal_page, lobby))
        except Exception:
            self._farming_queue.put(('probe', None, time.monotonic(), rect))

    def _farming_capture(self, rect):
        assert_action_active(self)
        if getattr(self,'_background_running',False) and getattr(self,'_background_device',None) is not None:
            return self._background_device.capture()
        info = find_emulator_window_info()
        if not info or tuple(info['rect']) != tuple(rect[k] for k in ('left', 'top', 'width', 'height')):
            raise RuntimeError('게임 창 위치가 바뀌어 재료 인식을 중단했습니다.')
        if sys.platform == 'win32':
            from ctypes import wintypes
            api = ctypes.windll.user32
            api.IsIconic.argtypes = (wintypes.HWND,)
            api.IsIconic.restype = wintypes.BOOL
            api.GetForegroundWindow.restype = wintypes.HWND
            api.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            api.GetAncestor.restype = wintypes.HWND
            api.WindowFromPoint.argtypes = (wintypes.POINT,)
            api.WindowFromPoint.restype = wintypes.HWND
            allowed = {int(info['hwnd'])} | set(getattr(self, '_farming_allowed_hwnds', ()))
            foreground = api.GetAncestor(api.GetForegroundWindow(), 2)
            if api.IsIconic(info['hwnd']) or int(foreground or 0) not in allowed:
                raise RuntimeError('게임 창이 앞에 있을 때만 결과를 인식합니다.')
            # Statistics live at the right below mana, outside the reward list.
            for rx, ry in ((.32,.42),(.5,.42),(.68,.42),(.5,.66)):
                point = wintypes.POINT(round(rect['left']+rect['width']*rx),
                                        round(rect['top']+rect['height']*ry))
                owner = int(api.GetAncestor(api.WindowFromPoint(point), 2) or 0)
                excluded_stats = (getattr(self, '_farming_stats_capture_excluded', False)
                                  and owner == getattr(self, '_farming_stats_hwnd', None))
                if owner != int(info['hwnd']) and not excluded_stats:
                    raise RuntimeError('보상 목록이 다른 창에 가려져 있습니다.')
        with mss.mss() as capture:
            shot = capture.grab(rect)
            return Image.frombytes('RGB', shot.size, shot.rgb)

    def _farming_tick(self):
        self._farming_after = None
        if self._farming_stopped:
            return
        try:
            if getattr(self,'_all_actions_stopped',False) is True:return
            if getattr(self,'_background_running',False):
                return
            self._farming_sync_stats_focus()
            while True:
                try:
                    message = self._farming_queue.get_nowait()
                except queue.Empty:
                    break
                if message[0]=='root_tree':
                    self._hide_tree_rewards()
                    continue
                if message[0]=='scan' and len(message)>5 and message[5]!=getattr(self,'_stop_epoch',0):continue
                if message[0] == 'scan':
                    self._farming_finish_scan(*message[1:5])
                    continue
                _, present, captured_at, rect = message[:4]
                self._farming_probe_busy = False
                current = self._reward_game_capture_rect()
                if time.monotonic()-captured_at > 2 or current != rect:
                    continue
                if len(message) > 4 and message[4]:
                    self._farming_observe_board()
                if present is None:
                    continue  # Occlusion/OCR contention is not a result-page exit.
                lobby=bool(message[6]) if len(message)>6 else False
                if lobby and time.monotonic()-getattr(self,'_farming_lobby_clicked_at',0)<1.5:
                    continue
                if lobby:
                    self._farming_lobby_visible=True
                    self._farming_result_visible=False
                    self._farming_positive=0
                    self._farming_negative=0
                    info=self._reward_game_window_info()
                    self._farming_lobby_click_context=dict(hwnd=int(info['hwnd']),rect=tuple(info['rect'])) if info else None
                    continue
                if getattr(self,'_farming_lobby_visible',False):
                    self._farming_lobby_visible=False
                    self._farming_lobby_click_context=None
                    self._farming_displayed_session=None
                    if self._farming_stats is not None: self._farming_stats.withdraw()
                self._farming_crystal_page = bool(message[5]) if len(message)>5 else bool(present)
                if present:
                    self._farming_positive += 1
                    self._farming_negative = 0
                    if self._farming_positive >= 2:
                        self._farming_result_visible = True
                        self._farming_battle_seen = False
                else:
                    self._farming_positive = 0
                    self._farming_negative += 1
                    if self._farming_negative >= 2:
                        self._farming_result_visible = False
                        self._farming_displayed_session = None
                        if self._farming_stats is not None:
                            self._farming_stats.withdraw()
            if self.busy or self.playback_active or self.ocr_busy or getattr(self, '_manual_reward_busy', False):
                return
            ledger = self._load_crystal_ledger()
            if not ledger.get('farming_groups'):
                return
            rect = self._reward_game_capture_rect()
            if rect is None:
                return
            if getattr(self,'_root_tree_active',False) is True:self._hide_tree_rewards()
            if getattr(self,'_farming_lobby_visible',False):
                marker='lobby:'+str(ledger.get('reward_session',{}).get('id','registered'))
                if self._farming_displayed_session!=marker:
                    self._farming_show_stats('연전 확인 · 저장된 재료 수량 · OK를 누르면 숨깁니다.')
            elif self._farming_result_visible:
                ledger = self._farming_begin_result()
                session = ledger['reward_session']
                if not getattr(self,'_farming_crystal_page',True):
                    if self._farming_displayed_session != session['id']:
                        self._farming_show_stats('크리스탈 획득 화면 대기 · 카드 아래 수량으로 집계합니다.')
                elif session.get('credited'):
                    if self._farming_displayed_session != session['id']:
                        self._farming_show_stats('집계 완료 · 같은 결과는 다시 더하지 않습니다.')
                elif self._farming_attempts < 3 and time.monotonic() >= self._farming_retry_at:
                    self._farming_start_scan(rect, ledger)
                    return
                elif self._farming_attempts >= 3 and self._farming_displayed_session != session['id']:
                    self._farming_show_stats(getattr(self, '_farming_last_message', '자동 집계 보류 · 인식 재시도를 누를 수 있습니다.'))
            if not self._farming_probe_busy and time.monotonic() >= self._farming_probe_next:
                self._farming_probe_busy = True
                self._farming_probe_next = time.monotonic()+1.0
                self._farming_allowed_hwnds = set()
                if sys.platform == 'win32':
                    from ctypes import wintypes
                    api = ctypes.windll.user32
                    api.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                    api.GetAncestor.restype = wintypes.HWND
                    for window in (self.root, self._farming_stats):
                        if window is not None and window.winfo_exists():
                            self._farming_allowed_hwnds.add(int(api.GetAncestor(window.winfo_id(), 2)))
                threading.Thread(target=self._farming_probe_worker, args=(rect,), daemon=True).start()
        except (OSError, ValueError, KeyError, tk.TclError) as exc:
            self.set_status(f'결과창 확인 대기: {exc}')
        finally:
            if not self._farming_stopped and self.root.winfo_exists():
                self._farming_after = self.root.after(300, self._farming_tick)

    def _farming_start_scan(self, rect, ledger):
        if getattr(self,'_root_tree_active',False) is True:return
        if getattr(self,'_all_actions_stopped',False) is True:return
        if not any(r.get('confirmed',True) for g in ledger['farming_groups'].values() for r in g['materials']):
            self._farming_attempts=3
            self._farming_show_stats('재료 이름 등록 대기 · 돌파 화면에서 재료를 클릭하고 상세 이름 자동 인식을 기다려 주세요.')
            return
        self._manual_reward_busy = True
        self._farming_attempts += 1
        self._farming_scan_cancel=threading.Event()
        self._farming_retry_at = time.monotonic()+5.0
        self._farming_show_stats('자동 인식 중 · 완료될 때까지 결과창을 유지해 주세요. F10으로 중지할 수 있습니다.')
        self.overlay.hide()
        if hasattr(self, 'toolbar'):
            self.toolbar.hide_for_capture()
            self.toolbar._temporarily_hidden = False
        self.root.iconify()
        self.set_status('결과창 자동 집계 중 · 아래쪽 보상도 확인합니다 · F10 중지')
        threading.Thread(target=self._farming_scan_worker,
                         args=(rect, ledger['reward_session']['id'], ledger['farming_groups']),
                         daemon=True).start()

    def _farming_wheel(self, rect, delta):
        assert_action_active(self)
        if self._farming_scan_cancel.is_set():
            raise RuntimeError('재료 인식을 중지했습니다.')
        frame=self._farming_capture(rect)  # Verify game and reward page before every wheel, including restore.
        if not self._looks_like_reward_frame(frame):
            raise RuntimeError('결과창이 닫혀 스크롤 입력을 중지했습니다.')
        if getattr(self,'_background_running',False):
            # A small overlapping touch scroll lets the existing merge verify rows.
            if delta<0:self._background_device.swipe(960,610,960,520,300)
            else:self._background_device.swipe(960,430,960,520,300)
            if self._background_cancel.wait(.22):raise RuntimeError('재료 파밍을 중지했습니다.')
            return
        mouse = WindowsDragInput()
        try:
            mouse.start()
            self._result_scroll_input = mouse
            mouse.move(round(rect['left']+rect['width']*.60), round(rect['top']+rect['height']*.55))
            if self._farming_scan_cancel.is_set():
                raise RuntimeError('재료 인식을 중지했습니다.')
            mouse.wheel(delta)
        finally:
            mouse.close()
            self._result_scroll_input = None
        time.sleep(.22)

    def _farming_read_card_quantity(self,image,box):
        import cv2
        x0,y0,x1,y1=box
        # Restrict both position and color: orange artwork above the badge is
        # excluded, as is the global x2 at the top of the reward dialog.
        zone=image.crop((round(x0+.38*(x1-x0)),round(y0+.76*(y1-y0)),x1,y1))
        rgb=np.asarray(zone.convert('RGB'),dtype=np.int16)
        orange=(rgb[...,0]>=155)&(rgb[...,0]>rgb[...,1]+25)&(rgb[...,1]>40)&(rgb[...,1]<190)&(rgb[...,2]<130)
        count,labels,stats,_=cv2.connectedComponentsWithStats(orange.astype(np.uint8),8)
        mask=np.zeros(labels.shape,dtype=np.uint8)
        for index in range(1,count):
            x,y,w,h,area=map(int,stats[index])
            if h>=max(5,zone.height*.23) and area>=8 and w>=2:
                mask[labels==index]=255
        ys,xs=np.where(mask)
        if len(xs)<15: return None
        mask=mask[ys.min():ys.max()+1,xs.min():xs.max()+1]
        binary=cv2.resize(255-mask,(max(1,round(mask.shape[1]*64/mask.shape[0])),64),interpolation=cv2.INTER_NEAREST)
        binary=cv2.copyMakeBorder(binary,12,12,12,12,cv2.BORDER_CONSTANT,value=255)
        readings=[self._recognize_number_crop(Image.fromarray(view).convert('RGB'),allow_multiplier=True)
                  for view in (binary,cv2.GaussianBlur(binary,(3,3),.6))]
        values=[value for value,score,_ in readings if value is not None and value>0 and score>=.85]
        return values[0] if len(values)==2 and values[0]==values[1] else None

    def _farming_read_page(self, image, templates=None):
        boxes = [b for b in detect_reward_card_rects(image)
                 if b[1] > image.height*.23 and b[3] < image.height*.761]
        if templates is not None:
            boxes = [box for box in boxes if self._farming_identify_art(image.crop(box),templates) is not None]
        if not boxes: return []
        read = lambda crop: [t for t in self._run_ocr_tokens(crop) if t.score >= .60]
        tokens = read(image)
        for x0,y0,x1,y1 in boxes:
            values = {n for n,t in _find_reward_amount_tokens(tokens, image.size)
                      if x0-8 <= t.cx <= x1+9 and y0+.58*(y1-y0) <= t.cy <= y1+17}
            if len(values) > 1:
                raise ValueError('같은 카드 수량이 다르게 읽혀 집계를 보류했습니다.')
        # Only wanted artwork needs quantity OCR. Unrelated reward cards must
        # not block accounting when their tiny amount label is unreadable.
        items = extract_visual_reward_items(image, tokens, read, boxes=boxes)
        items = [i for i in items if i['screen_box'] in boxes]
        seen={item['screen_box'] for item in items}
        for box in boxes:
            if box in seen: continue
            amount=self._farming_read_card_quantity(image,box)
            if amount is not None:
                x0,y0,x1,y1=box
                items.append(dict(amount=amount,crop=image.crop(box).convert('RGB'),screen_box=box,
                                  x=(x0+x1)/2,y=(y0+y1)/2))
        if len(items) != len(boxes):
            raise ValueError('카드 아래 획득 수량을 전부 읽지 못해 집계를 보류했습니다.')
        return items

    def _farming_scrolled_frame(self,rect,previous):
        last_error=None
        for attempt in range(3):
            if self._farming_scan_cancel.is_set(): raise RuntimeError('재료 인식을 중지했습니다.')
            current=self._farming_capture(rect)
            if not self._looks_like_reward_frame(current):
                raise ValueError('결과창이 닫혀 집계를 중단했습니다.')
            try:
                delta=reward_scroll_delta(previous,current)
                if abs(delta)>=1 or attempt==2: return current,delta
            except ValueError as exc:
                last_error=exc
                if attempt==2: raise
            # Do not send another wheel event during an unfinished animation.
            time.sleep(.12)
        raise last_error or ValueError('보상 목록 이동을 확인하지 못했습니다.')

    def _farming_collect_pages(self, rect, first, templates=None):
        balance = 0
        image = first
        aggregate = []
        observed_cards = False
        offset = 0.0
        try:
            # Normalize to the beginning even if the user opened the bottom of the list.
            for _ in range(24):
                position = reward_scrollbar_position(image)
                if position is None or position[0]:
                    break
                self._farming_wheel(rect, 120)
                balance += 1
                new,delta = self._farming_scrolled_frame(rect,image)
                if delta > 2:
                    raise ValueError('보상 목록이 예상과 다른 방향으로 움직였습니다.')
                if abs(delta) < 1:
                    raise ValueError('보상 목록 맨 위를 확인하지 못했습니다.')
                image = new
            else:
                raise ValueError('보상 목록이 너무 길어 전체 확인을 보류했습니다.')
            for _ in range(30):
                if self._farming_scan_cancel.is_set():
                    raise RuntimeError('재료 인식을 중지했습니다.')
                if templates is None:
                    page = self._farming_read_page(image)
                else:
                    observed_cards |= any(b[1]>image.height*.23 and b[3]<image.height*.761
                                          for b in detect_reward_card_rects(image))
                    page = self._farming_read_page(image,templates)
                aggregate = merge_reward_page(aggregate, page, offset, image.height)
                position = reward_scrollbar_position(image)
                if position is None or position[1]:
                    if not aggregate and not observed_cards:
                        raise ValueError('온전히 보이는 보상 카드가 없습니다.')
                    if position is None and any(b[3] >= image.height*.761 for b in detect_reward_card_rects(image)):
                        raise ValueError('화면 아래 잘린 보상이 있어 전체 집계를 확인하지 못했습니다.')
                    return aggregate
                self._farming_wheel(rect, -120)
                balance -= 1
                new,delta = self._farming_scrolled_frame(rect,image)
                if delta < 1:
                    raise ValueError('보상 목록 맨 아래를 확인하지 못했습니다.')
                offset += delta
                image = new
            raise ValueError('보상 목록 전체를 확인하지 못해 집계를 보류했습니다.')
        finally:
            # Undo exactly the wheel events we sent. F10 stops all further input.
            for _ in range(abs(balance)):
                if self._farming_scan_cancel.is_set():
                    break
                try:
                    self._farming_wheel(rect, -120 if balance > 0 else 120)
                except Exception:
                    break

    def _farming_material_templates(self, groups):
        db = self._load_crystal_db()
        wanted = {r['id'] for group in groups.values() for r in group['materials'] if r.get('confirmed',True)}
        templates = {}
        for entry in db['crystals']:
            cid = str(entry.get('id'))
            if cid not in wanted:
                continue
            path = self.data_paths['icons_dir']/str(entry.get('image_file') or '')
            if not path.is_file():
                raise ValueError('등록한 재료 아이콘 파일이 없어 집계를 보류했습니다.')
            with Image.open(path) as source:
                templates[cid] = crystal_art_signatures(source)
        if set(templates) != wanted:
            raise ValueError('재료 아이콘 등록이 불완전합니다. 돌파 화면에서 F7로 다시 등록하세요.')
        if not wanted: raise ValueError('재료 이름이 아직 등록되지 않았습니다. 게임에서 재료를 클릭하고 상세 이름 자동 인식을 기다려 주세요.')
        return templates

    @staticmethod
    def _farming_identify_art(crop,templates):
        probe = crystal_art_signatures(crop)
        ranks = sorted((min(float(np.abs(a-b).mean()) for a,b in zip(probe,refs)),cid)
                       for cid,refs in templates.items())
        if not ranks: raise ValueError('등록 재료 이미지가 없습니다.')
        best,cid=ranks[0]
        next_score=ranks[1][0] if len(ranks)>1 else 100.0
        if best<13.0:
            if next_score-best<3.5:
                raise ValueError('서로 비슷한 재료 아이콘을 구분하지 못해 집계를 보류했습니다.')
            return cid
        if best<18.0:
            raise ValueError('등록한 재료와 유사한 아이콘이 있어 확인을 보류했습니다.')
        return None

    def _farming_match_items(self,items,groups,templates=None):
        if templates is None: templates=self._farming_material_templates(groups)
        amounts={}
        for item in items:
            cid=self._farming_identify_art(item['crop'],templates)
            if cid is not None: amounts[cid]=amounts.get(cid,0)+int(item['amount'])
        return amounts

    def _farming_scan_worker(self, rect, session_id, groups):
        scan_epoch=getattr(self,'_stop_epoch',0)
        ACTION_LOCAL.app=self;ACTION_LOCAL.epoch=scan_epoch
        amounts, error = None, None
        try:
            time.sleep(.30)  # Let our windows finish minimizing before sampling.
            first = self._farming_capture(rect)
            if not self._looks_like_reward_frame(first):
                raise ValueError('크리스탈 결과창이 확인되지 않습니다.')
            if not self._farming_result_header_confirmed(first):
                raise ValueError('상단 결과 제목을 확인하지 못해 집계를 보류했습니다.')
            templates = self._farming_material_templates(groups)
            items = self._farming_collect_pages(rect, first, templates)
            amounts = self._farming_match_items(items, groups, templates)
            final=self._farming_capture(rect)
            if self._farming_scan_cancel.is_set() or not self._farming_result_header_confirmed(final) or not self._looks_like_reward_frame(final):
                raise ValueError('결과창 확인이 끝나기 전에 화면이 바뀌었습니다.')
        except Exception as exc:
            error = str(exc)
        self._farming_queue.put(('scan', session_id, groups, amounts, error,scan_epoch))

    def _farming_finish_scan(self, session_id, groups, amounts, error):
        if getattr(self,'_root_tree_active',False) is True:return
        self._manual_reward_busy = False
        self._farming_probe_next = 0.0
        if self._farming_scan_cancel.is_set():
            error = '재료 인식을 중지했습니다. 저장한 수량은 바뀌지 않았습니다.'
            self._farming_attempts = 3
        if not error:
            try:
                with self._crystal_db_lock:
                    ledger, changed = credit_farming_result(self._load_crystal_ledger(), session_id, amounts, groups)
                    if changed:
                        _atomic_json_write(self.data_paths['ledger'], ledger)
                self._farming_sync_views(ledger)
                message = f"이번 결과: 등록 재료 {sum(amounts.values())}개 획득 · 집계 완료"
            except Exception as exc:
                error = f'집계 저장 보류: {exc}'
        if error:
            message = '자동 집계 보류 · '+error
            self._farming_retry_at = time.monotonic()+5.0
        self.set_status(message)
        self._farming_show_stats(message)

    def _farming_sync_views(self, ledger):
        stocks = ledger.get('stocks', {})
        group = ledger.get('farming_groups', {}).get(ledger.get('active_farming_group'), {})
        if group and not getattr(self, '_pending_farming_materials', None):
            previous = {row['id']:row for row in self.crystal_candidates}
            self.crystal_candidates = [{**previous.get(row['id'],{}),**row,'current':row['owned'],'card_name':group['name']}
                for row in farming_group_rows(ledger,ledger['active_farming_group'])]
            self.card_name_detected = group['name']
        for row in self.crystal_candidates:
            if row['id'] in stocks:
                key = ledger.get('active_farming_group')
                registered = next((r for r in ledger.get('farming_groups', {}).get(key, {}).get('materials', [])
                                   if r['id'] == row['id']), None)
                required = int(registered['required']) if registered else int(row['required'])
                row.update(required=required, current=stocks[row['id']], deficit=max(0, required-stocks[row['id']]))
        if self.active_target and self.active_target.get('id') in stocks:
            self.active_target.update(owned_before=stocks[self.active_target['id']], collected=0,
                goal_amount=max(0, int(self.active_target['required'])-stocks[self.active_target['id']]),
                complete=stocks[self.active_target['id']] >= int(self.active_target['required']))
        self.refresh_crystal_ledger()
        if hasattr(self, 'toolbar'):
            self.toolbar.refresh()

    def _farming_show_stats(self, message):
        if getattr(self,'_root_tree_active',False) is True:return
        self._farming_last_message = message
        ledger = self._load_crystal_ledger()
        groups = ledger.get('farming_groups', {})
        session = ledger.get('reward_session', {})
        lobby=bool(getattr(self,'_farming_lobby_visible',False))
        if not groups or not (self._farming_result_visible or lobby):
            return
        marker='lobby:'+str(session.get('id','registered')) if lobby else session.get('id')
        self._farming_displayed_session = marker
        if self._farming_hidden_session == marker:
            return
        if self._farming_stats is None or not self._farming_stats.winfo_exists():
            win = tk.Toplevel(self.root)
            self._farming_stats = win
            win.withdraw()
            win.title('크리스탈 획득 통계')
            win.attributes('-topmost', False)
            self._farming_stats_front = None
            body = ttk.Frame(win, padding=10)
            body.pack(fill='both', expand=True)
            self._farming_group_var = tk.StringVar()
            self._farming_group_select = ttk.Combobox(body, textvariable=self._farming_group_var, state='readonly')
            self._farming_group_select.pack(fill='x', pady=(0,8))
            self._farming_group_select.bind('<<ComboboxSelected>>', lambda _e: self._farming_select_group())
            style = ttk.Style(win)
            style.configure('FarmingResult.Treeview', rowheight=56)
            self._farming_tree = ttk.Treeview(body, columns=('need','gain','owned','missing'),
                show='tree headings', height=5, style='FarmingResult.Treeview')
            self._farming_tree.heading('#0', text='재료')
            self._farming_tree.column('#0', width=125, stretch=True)
            for name, title in (('need','필요'),('gain','이번'),('owned','보유'),('missing','부족')):
                self._farming_tree.heading(name, text=title)
                self._farming_tree.column(name, width=48, minwidth=42, stretch=False, anchor='center')
            self._farming_tree.pack(fill='both', expand=True)
            self._farming_message_var = tk.StringVar()
            ttk.Label(body, textvariable=self._farming_message_var, wraplength=315).pack(fill='x', pady=8)
            self._farming_retry_button = ttk.Button(body, text='인식 재시도', command=self._farming_retry)
            self._farming_retry_button.pack(fill='x')
            win.protocol('WM_DELETE_WINDOW', self._farming_hide_stats)
        key = ledger.get('active_farming_group')
        if key not in groups:
            key = next(iter(groups))
        self._farming_group_keys = list(groups)
        self._farming_group_select['values'] = [groups[k]['name'] for k in self._farming_group_keys]
        self._farming_group_select.current(self._farming_group_keys.index(key))
        self._farming_tree.delete(*self._farming_tree.get_children())
        self._farming_photos = []
        from PIL import ImageTk
        entries = {str(row['id']):row for row in self._load_crystal_db()['crystals']}
        rows = farming_group_rows(ledger, key)
        for row in rows:
            options = {}
            entry = entries.get(row['id'], {})
            path = self.data_paths['icons_dir']/str(entry.get('image_file') or row.get('image_file') or '')
            if path.is_file():
                with Image.open(path) as image:
                    photo = ImageTk.PhotoImage(fit_image_preserve_aspect(image, (38,48)))
                self._farming_photos.append(photo)
                options['image'] = photo
            gained = ('이름대기' if not row.get('confirmed',True)
                      else row['gained'] if session.get('credited') else '미확인')
            self._farming_tree.insert('', 'end', text=crystal_display_name(entry,row,row['index']),
                values=(row['required'], gained, row['owned'], row['deficit']), **options)
        if rows and all(row['deficit'] == 0 for row in rows):
            message += ' · 모든 재료 충족'
        self._farming_message_var.set(message)
        self._farming_retry_button.state(['disabled' if lobby or getattr(self, '_manual_reward_busy', False) else '!disabled'])
        if self._farming_stats.state() == 'withdrawn':
            rect = self._reward_game_capture_rect()
            if rect:
                self._farming_stats.geometry(f"350x450+{max(0,rect['left']+rect['width']-358)}+{max(0,rect['top']+round(rect['height']*.12))}")
            self._farming_stats.deiconify()
            self._farming_stats_capture_excluded = exclude_own_window_from_capture(self._farming_stats)
            if sys.platform == 'win32':
                self._farming_stats_hwnd = int(ctypes.windll.user32.GetAncestor(self._farming_stats.winfo_id(), 2))
                if not hasattr(self, '_farming_allowed_hwnds'):
                    self._farming_allowed_hwnds = set()
                self._farming_allowed_hwnds.add(self._farming_stats_hwnd)
        self._farming_sync_stats_focus()

    def _farming_hide_stats(self):
        ledger = self._load_crystal_ledger()
        self._farming_hidden_session = self._farming_displayed_session or ledger.get('reward_session', {}).get('id')
        self._farming_stats.withdraw()

    def _farming_select_group(self):
        index = self._farming_group_select.current()
        if index < 0:
            return
        with self._crystal_db_lock:
            ledger = self._load_crystal_ledger()
            ledger['active_farming_group'] = self._farming_group_keys[index]
            _atomic_json_write(self.data_paths['ledger'], ledger)
        self._farming_sync_views(ledger)
        self._farming_show_stats('집계 결과 · 등록한 모든 재료를 아이콘으로 대조합니다.')

    def _farming_retry(self):
        resume_actions(self)
        if getattr(self,"_farming_lobby_visible",False):
            self.set_status("연전 확인에서는 저장된 수량을 표시합니다. 획득 집계는 결과창에서 수행합니다.")
            return
        if getattr(self, '_manual_reward_busy', False):
            return
        session = self._load_crystal_ledger().get('reward_session', {})
        if session.get('credited'):
            self._farming_show_stats('이미 집계한 결과입니다. 같은 결과는 다시 더하지 않습니다.')
            return
        self._farming_attempts = 0
        self._farming_retry_at = 0.0
        self._farming_scan_cancel=threading.Event()

    def _v66_debug_init(self):
        """Create log immediately and monitor Tk heartbeat without touching Tk off-thread."""
        self._v66_debug_file = None
        self._v66_debug_lock = threading.Lock()
        self._v66_last_heartbeat = time.monotonic()
        self._v66_monitor_stop = threading.Event()
        try:
            debug_dir = self.data_paths["root"] / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            # New installed version: remove only debug files created by earlier
            # runs. Leave database, icon images and learned digit templates alone.
            version_marker = debug_dir / '.active_version'
            try:
                previous = version_marker.read_text(encoding='utf-8').strip() if version_marker.exists() else ''
                if previous != APP_VERSION:
                    for old in debug_dir.iterdir():
                        if old.is_file() and old != version_marker and (
                            old.name.endswith('_gui_watchdog.log') or
                            old.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')
                        ):
                            try: old.unlink()
                            except OSError: pass
                    version_marker.write_text(APP_VERSION, encoding='utf-8')
            except OSError:
                pass
            self._v66_debug_file = open(debug_dir / f"{APP_VERSION}_gui_watchdog.log", "a", encoding="utf-8", buffering=1)
            self._v66_log(f"{APP_VERSION} started; five-attribute priority (5>4>3>2>1), timer starts at drag, reward diagnostics")
            self.root.after(1000, self._v66_heartbeat)
            threading.Thread(target=self._v66_watch_gui, name="v66-gui-watchdog", daemon=True).start()
        except Exception as exc:
            # Diagnostics must never prevent normal startup.
            try:
                if self._v66_debug_file:
                    self._v66_debug_file.write(f"watchdog init failed: {exc!r}\n")
            except Exception:
                pass

    def _v66_log(self, message):
        try:
            if self._v66_debug_file is not None:
                with self._v66_debug_lock:
                    self._v66_debug_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
                    self._v66_debug_file.flush()
        except Exception:
            pass

    def _v66_heartbeat(self):
        self._v66_last_heartbeat = time.monotonic()
        if not self._v66_monitor_stop.is_set():
            self.root.after(1000, self._v66_heartbeat)

    def _v66_watch_gui(self):
        """Never invoke any tkinter operation from this worker."""
        import faulthandler
        last_dump = 0.0
        while not self._v66_monitor_stop.wait(2.0):
            try:
                stalled = time.monotonic() - self._v66_last_heartbeat
                if stalled >= 10.0 and time.monotonic() - last_dump >= 20.0:
                    last_dump = time.monotonic()
                    self._v66_log(f"GUI STALLED: heartbeat delayed {stalled:.1f}s; thread traceback follows")
                    if self._v66_debug_file is not None:
                        with self._v66_debug_lock:
                            faulthandler.dump_traceback(file=self._v66_debug_file, all_threads=True)
                            self._v66_debug_file.flush()
            except Exception:
                pass

    def _poll_playback_finish_queue(self):
        try:
            while not self._playback_finish_queue.empty():
                error, cancelled = self._playback_finish_queue.get_nowait()
                self._finish_playback(error, cancelled)
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self._poll_playback_finish_queue)

    def _finish_playback(self, error=None, cancelled=False):
        """Always release playback flags; restore GUI in a separate event-loop tick."""
        self._route_combo_fallback=None
        self._v66_log(f"F6 drag finished: error={error!r}, cancelled={cancelled}")
        self.drag_in_progress = False
        self.playback_active = False
        self.playback_after = None
        self._clear_live_board()
        self._route_debug_finish(error,cancelled)
        if error:
            message = f"자동 드래그 오류: {error}. F6 또는 경로 계산 버튼으로 다시 시도할 수 있습니다."
        elif cancelled:
            message = "자동 실행을 중지했어요. F6 또는 경로 계산 버튼으로 다시 시도할 수 있습니다."
        else:
            message = "경로 드래그 완료. 게임에서 매치 결과를 확인하세요. F6으로 경로 재계산 · F10=중지"
        if error:self._route_execution_notice(message)
        else:self.set_status(message)
        # Explicit async teardown avoids re-entering Tk window management from
        # the worker completion dispatch; keep the farming HUD untouched.
        self.root.after(10, self._restore_ui_after_playback)

    def _restore_ui_after_playback(self):
        self._v66_log("F6 restoration started")
        try:
            self.overlay.hide()
        except Exception as exc:
            self.set_status(f"경로 오버레이 정리 오류: {exc}")
        try:
            if hasattr(self, "toolbar") and not getattr(self, '_route_ui_active', False):
                self.toolbar.restore_after_playback()
        except Exception as exc:
            self.set_status(f"자동 실행 후 창 복구 오류: {exc}")
            self._v66_log(f"F6 restoration error: {exc!r}")
        self._v66_log("F6 restoration completed")

    def stop_playback(self):
        self._all_actions_stopped=True
        armed=getattr(self,'_reward_monitor_armed',None)
        if armed is not None:armed.clear()
        self.reward_poll_busy=False
        self._stop_epoch=getattr(self,'_stop_epoch',0)+1
        for field in ('play_cancel','_route_cancel','_background_cancel','_farming_scan_cancel','_awakening_register_cancel','_capture_test_cancel','_google_adb_cancel'):
            event=getattr(self,field,None)
            if event is not None:event.set()
        self._resource_scan_generation=getattr(self,'_resource_scan_generation',0)+1
        self._auto_title_serial=getattr(self,'_auto_title_serial',0)+1
        self._attribute_probe_epoch=getattr(self,'_attribute_probe_epoch',0)+1
        self._awakening_register_request=None;self._awakening_register_busy=False
        self._background_probe_busy=False;self._background_probe_next=0.0
        self._material_button_probe_busy=False;self._material_button_probe_next=0.0
        self._background_valid_at=0.0;self._material_button_valid_at=0.0
        self.busy=False;self.ocr_busy=False;self.detecting_area=False;self.detect_then_calculate=False
        self._route_ui_waiting_capture=False;self._route_ui_waiting_playback=False;self._route_ui_active=False
        self._background_pressed=False;self._material_button_pressed=False
        for field in ('_active_drag_input','_result_scroll_input'):
            mouse=getattr(self,field,None)
            if mouse is not None:
                try:mouse.close()
                except Exception:pass
        device=getattr(self,'_background_device',None)
        if device is not None:
            try:device.abort()
            except Exception:pass
        for field in ('_resource_selector','_card_title_selector','_board_selector'):
            selector=getattr(self,field,None)
            if selector is not None:
                try:selector.cancel()
                except Exception:pass
        for field in ('_farming_confirm_window','_capture_test_window'):
            window=getattr(self,field,None)
            if window is not None:
                try:window.destroy()
                except tk.TclError:pass
        for field in ('_background_button_win','_awakening_register_win','_material_button_win','_farming_stats'):
            window=getattr(self,field,None)
            if window is not None:
                try:window.withdraw()
                except tk.TclError:pass
        self.set_status('F10 · 모든 작업과 예약 입력을 중지했습니다. 버튼을 눌러 새 작업을 시작할 수 있습니다.')
        self._route_combo_fallback=None
        if getattr(self, "_prefind_key", None) is not None:
            self._prefind_paused_key = self._prefind_key
            self._prefind_cancel()
            self.set_status("사전 탐색을 중지했습니다. 판이나 조건이 바뀌면 다시 확인합니다.")
        self._manual_reward_busy=False
        if not self.playback_active:
            self.set_status('F10 · 모든 작업과 예약 입력을 중지했습니다. 버튼을 눌러 새 작업을 시작할 수 있습니다.')
            return
        self.play_cancel.set()
        if not self.drag_in_progress:
            self._cancel_countdown("자동 실행을 취소했어요.")
        else:
            self.set_status("자동 실행 중지 요청됨. 마우스 버튼을 놓는 중…")

    def _cancel_countdown(self, message="자동 실행을 취소했어요."):
        self._route_combo_fallback=None
        if self.playback_after:
            try:
                self.root.after_cancel(self.playback_after)
            except tk.TclError:
                pass
            self.playback_after = None
        self.playback_active = False
        self.drag_in_progress = False
        self.play_cancel.set()
        self._route_debug_update(dict(phase='execution_cancelled',reason=message))
        self._route_execution_notice(message)


    def close(self):
        wizard=getattr(self,'_background_setup_wizard',None)
        if wizard is not None:wizard.close()
        google_cancel=getattr(self,'_google_adb_cancel',None)
        if google_cancel is not None:google_cancel.set()
        registration_cancel=getattr(self,'_awakening_register_cancel',None)
        if registration_cancel is not None:registration_cancel.set()
        self._awakening_register_request=None
        capture_cancel=getattr(self,'_capture_test_cancel',None)
        if capture_cancel is not None:capture_cancel.set()
        self._background_stopped=True
        self.stop_background_farming()
        if getattr(self,'_background_after',None) is not None:self.root.after_cancel(self._background_after)
        if getattr(self,'_background_button_win',None) is not None:self._background_button_win.destroy()
        if getattr(self,'_awakening_register_win',None) is not None:self._awakening_register_win.destroy()
        if getattr(self,'_team_party_win',None) is not None:self._team_party_win.destroy()
        self._material_button_stopped=True
        if getattr(self,"_material_button_after",None) is not None:
            self.root.after_cancel(self._material_button_after)
        if getattr(self,"_material_button_win",None) is not None:
            self._material_button_win.destroy()
        self._prefind_stopped = True
        self._prefind_cancel()
        if getattr(self, "_prefind_after", None) is not None:
            self.root.after_cancel(self._prefind_after)
        if hasattr(self,'_crystal_click_stopped'):
            self._crystal_click_stopped=True
            if self._crystal_click_after is not None: self.root.after_cancel(self._crystal_click_after)
            if self._crystal_observer is not None:
                try: self._crystal_observer.close()
                except Exception: pass
        if hasattr(self, '_farming_scan_cancel'):
            self._farming_stopped = True
            self._farming_scan_cancel.set()
            if self._farming_after is not None:
                self.root.after_cancel(self._farming_after)
            mouse = getattr(self, '_result_scroll_input', None)
            if mouse is not None:
                try:
                    mouse.close()
                except Exception:
                    pass
        self._attribute_probe_stopped = True
        if self._attribute_probe_after is not None:
            self.root.after_cancel(self._attribute_probe_after)
        try:
            if hasattr(self, "_v66_monitor_stop"):
                self._v66_monitor_stop.set()
            self._v66_log("normal shutdown")
        except Exception:
            pass
        self._reward_monitor_stop.set()
        self._reward_monitor_armed.clear()
        self.play_cancel.set()
        mouse = getattr(self, '_active_drag_input', None)
        if mouse is not None:
            try:
                mouse.close()
            except Exception:
                pass
        try:
            _atomic_json_write(self.data_paths["board_history"], {"schema_version": 1, "boards": self.board_history[-30:]})
        except Exception:
            pass
        try: self.overlay.win.destroy()
        except Exception: pass
        try:
            if hasattr(self, "toolbar"):
                panel = getattr(self.toolbar, "crystal_window", None)
                if panel is not None and panel.winfo_exists():
                    panel.destroy()
                strip = getattr(self.toolbar, "farming_strip", None)
                if strip is not None and strip.winfo_exists():
                    strip.destroy()
                self.toolbar.win.destroy()
        except Exception:
            pass
        if hasattr(self,'_route_debug_writer'): self._route_debug_writer.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    __import__('multiprocessing').freeze_support()
    if __import__("sys").platform != "win32":
        print("이 시험판은 Windows 전용입니다.")
    App().run()
