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

APP_VERSION = "v101.14"
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
FIVE_ATTR_TIME_BUDGET = 10.0  # Temporary ceiling while improving five-attribute success rates.
FIVE_ATTR_BEAM_WIDTH = 180
FIVE_ATTR_DEFAULT_MOVE_CAP = 78

def five_attribute_inventory(board):
    """Count normal attack orbs before launching expensive 5-attribute search."""
    return {key: sum(orb == key for orb in board) for key in ("R", "B", "G", "Y", "P")}

def five_attribute_possible_by_count(board):
    return all(count >= 3 for count in five_attribute_inventory(board).values())


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
    if (dark_blue_fraction >= 0.28
            and deep_dark_fraction >= 0.22
            and bright_fraction <= 0.22
            and median_value <= 0.62):
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


def board_goal_metrics(board: list[str], goals: tuple[bool, bool, bool, bool]) -> dict:
    """Measure combo goals on the board produced by releasing the dragged orb.

    goals are: (at_least_8_combos, connected_8_orbs, all_5_attributes, recovery_priority).
    Recovery priority prefers more matched recovery drops when combo counts tie.
    """
    combos, matched, groups = combo_details(board)
    largest_group = max((len(group) for group in groups), default=0)
    matched_colors = {board[idx] for group in groups for idx in group}
    attr_count = len(matched_colors & PRIMARY_ATTRIBUTES)
    recovery_matched = sum(len(group) for group in groups if board[min(group)] == "H")
    recovery_combos = sum(1 for group in groups if board[min(group)] == "H")
    skull_matched = sum(len(group) for group in groups if board[min(group)] == "S")
    skull_combos = sum(1 for group in groups if board[min(group)] == "S")
    enabled = [bool(x) for x in goals] + [False] * max(0, 4 - len(goals))
    achieved = [
        combos >= 8 if enabled[0] else True,
        largest_group >= 8 if enabled[1] else True,
        attr_count >= 5 if enabled[2] else True,
        recovery_matched >= 3 if enabled[3] else True,
    ]
    progress = []
    if enabled[0]: progress.append(min(combos / 8.0, 1.0))
    if enabled[1]: progress.append(min(largest_group / 8.0, 1.0))
    if enabled[2]: progress.append(min(attr_count / 5.0, 1.0))
    if enabled[3]: progress.append(min(recovery_matched / 3.0, 1.0))
    selected_count = sum(enabled)
    achieved_count = sum(1 for i, flag in enumerate(enabled) if flag and achieved[i])
    progress_total = sum(progress)
    return {
        "combos": combos,
        "matched": matched,
        "groups": groups,
        "largest_group": largest_group,
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


def goal_label(index: int) -> str:
    return ("8콤보 이상", "동일 색상 8개 연결", "5속성 공격", "회복 우선")[index]


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


def solve_path(
    board: list[str],
    time_budget: float = SOLVER_TIME_BUDGET,
    max_moves: Optional[int] = SOLVER_MAX_MOVES,
    beam_width: int = SOLVER_BEAM_WIDTH,
    goals: tuple[bool, bool, bool, bool] = (True, False, False, False),
    avoid_skull: bool = True,
    spikes: Optional[list[bool]] = None,
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
    start_board = tuple(board)
    start_spikes = tuple((list(spikes) + [False] * (ROWS * COLS))[:ROWS * COLS]) if spikes else tuple([False] * (ROWS * COLS))
    enabled_goals = tuple(bool(v) for v in goals)
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
        combo_tier = 2 if raw["combos"] >= 10 else (1 if raw["combos"] >= 8 else 0)
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
        else:
            rank = (*safety_rank, *objective_rank)
        return rank, raw["combos"], raw["matched"], potential, cleared_spikes, skull_matched

    states = []
    e0 = evaluate(start_board, start_spikes, 0)
    for start_idx in range(ROWS * COLS):
        states.append((e0[0], start_board, start_spikes, start_idx, -1, (start_idx,),
                       e0[1], e0[2], e0[3], 0, 1, 0))

    best_path = [0]
    best_rank = (*e0[0], -1, 0)
    deadline = time.perf_counter() + max(0.0, time_budget)
    depth = 1
    while time.perf_counter() < deadline and (max_moves is None or depth <= max_moves):
        candidates: dict[tuple, tuple] = {}
        for state in states:
            if time.perf_counter() >= deadline:
                break
            rank, bd, sp, current, previous, path, _co, _ma, _pot, turns, unique_count, crossings = state
            for nxt in neighbors(current):
                if time.perf_counter() >= deadline:
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
                if enabled_goals[2] and ev[0][0] == 5:
                    # Five distinct attack matches are sufficient, even at
                    # five combos. Do not finish the beam or seek extra combos.
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
        if enabled_goals[2] and best_rank[0] == 5:
            break
        if (not enabled_goals[2] and not any(start_spikes) and all(x != 'S' for x in start_board)
                and len(best_path) - 1 <= 36):
            early_details = board_goal_metrics(board_after_drag(list(start_board), best_path), enabled_goals)
            # In five-attribute mode, 10 combos alone are NOT a valid early exit:
            # keep searching until all five attack colors are also matched.
            if early_details["combos"] >= 10 and (not enabled_goals[2] or early_details["attr_count"] == 5):
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


def solve_five_attributes_v101(board, time_budget=FIVE_ATTR_TIME_BUDGET, max_moves=65, spikes=None, avoid_skull=True):
    """Find the first five-attribute route with incremental, diverse beam search.

    Keep bitboards per color, updating only the two swapped colors. Cache color
    features, not complete combo calculations. Reserve half the beam for different
    matched-color masks so one four-color arrangement cannot crowd out every
    alternative. Restart with deterministic color weights if a beam is exhausted.
    Full combo/group evaluation is used to verify the returned route.
    """
    deadline = time.perf_counter() + max(0.0, float(time_budget))
    board0 = list(board)
    if len(board0) != ROWS * COLS or any(color not in COLOR_ORDER for color in board0):
        raise ValueError("퍼즐판에는 유효한 드롭 30칸이 필요합니다.")
    cap = max(0, int(max_moves)) if max_moves is not None else FIVE_ATTR_DEFAULT_MOVE_CAP
    goal = (False, False, True, False)
    initial = board_goal_metrics(board0, goal)
    if (initial['all_met'] or not five_attribute_possible_by_count(board0)
            or cap == 0 or time.perf_counter() >= deadline):
        return [0], initial['combos'], initial['matched'], board0

    encoded = bytes(COLOR_ORDER.index(color) for color in board0)
    masks0 = tuple(sum(1 << i for i, color in enumerate(encoded) if color == k)
                   for k in range(len(COLOR_ORDER)))
    thorn0 = sum(1 << i for i, flag in enumerate((list(spikes or []) + [False] * 30)[:30]) if flag)
    has_thorns = bool(thorn0)
    has_poison = bool(masks0[COLOR_ORDER.index('S')]) and avoid_skull
    poison_index = COLOR_ORDER.index('S')
    feature = lru_cache(maxsize=100000)(_attribute_bit_features)
    rng = random.Random(''.join(board0))
    width = max(30, FIVE_ATTR_BEAM_WIDTH)
    best_path = [0]
    best_rank = None

    def evaluate(masks, weights, thorn_bits, crossings):
        matched = 0
        potential = 0.0
        cleared = 0
        for color in range(5):
            found, pairs, marked = feature(masks[color])
            matched |= found << color
            if not found:
                potential += min(pairs, 12) * weights[color]
            if has_thorns:
                cleared += (marked & thorn_bits).bit_count()
        if has_thorns:
            for color in range(5, len(COLOR_ORDER)):
                cleared += (feature(masks[color])[2] & thorn_bits).bit_count()
        poison = feature(masks[poison_index])[2].bit_count() if has_poison else 0
        rank = (matched.bit_count(), -crossings if has_thorns else 0,
                cleared if has_thorns else 0, -poison, potential)
        return matched, rank

    while time.perf_counter() < deadline:
        weights = tuple(rng.uniform(.7, 1.4) for _ in range(5))
        matched, initial_rank = evaluate(masks0, weights, thorn0, 0)
        states = [(initial_rank, encoded, masks0, i, -1, (i,), matched, thorn0, 0)
                  for i in range(ROWS * COLS)]
        # Lower crossing counts dominate repeated board/cursor/thorn states.
        seen = {}
        for _depth in range(cap):
            candidates = {}
            for _, current_board, masks, cursor, previous, path, _, thorn_bits, crossings in states:
                if time.perf_counter() >= deadline:
                    break
                held = current_board[cursor]
                for nxt in _FIVE_ATTRIBUTE_NEIGHBORS[cursor]:
                    if nxt == previous:
                        continue
                    if time.perf_counter() >= deadline:
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
                    if matched == 31:
                        # Independent replay/group calculation is authoritative.
                        after = board_after_drag(board0, route)
                        details = board_goal_metrics(after, goal)
                        if not details['all_met']:
                            raise RuntimeError("5속성 탐색 결과 검증에 실패했습니다.")
                        return list(route), details['combos'], details['matched'], after
                    if best_rank is None or (rank, -len(route)) > best_rank:
                        best_rank = (rank, -len(route))
                        best_path = list(route)
                    candidates[key] = (rank, moved, next_masks, nxt, cursor, route,
                                       matched, next_thorns, next_crossings)
            if not candidates or time.perf_counter() >= deadline:
                break
            ordered = sorted(candidates.values(), key=lambda state: state[0], reverse=True)
            states = ordered[:width // 2]
            selected = {id(state) for state in states}
            buckets = {}
            for state in ordered:
                if id(state) not in selected:
                    buckets.setdefault(state[6], []).append(state)
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
        consistency.append(max(votes) / len(offsets))
    details.update(normal_cell_count=len(consistency),required_normal_cells=8,
                   color_consistency=float(np.mean(consistency)) if consistency else 0.0,
                   required_color_consistency=.65)
    passed=len(consistency)>=8 and details['color_consistency']>=.65
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



def detect_reward_card_rects(image: Image.Image) -> list[tuple[int, int, int, int]]:
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
        left, right = int(W * .23), int(W * .77)
        top, bottom = int(H * .215), int(H * .77)
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
                                read_amount=None) -> list[dict]:
    """Match quantity labels with independently detected cards; never infer ×2 globally."""
    boxes=detect_reward_card_rects(image)
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
            font=("Segoe UI Symbol", 12, "bold"), padx=6, pady=3,
            cursor="fleur", takefocus=False,
        )
        self.grip.pack(side="left", fill="y")
        self.grip.bind("<ButtonPress-1>", self._start_drag)
        self.grip.bind("<B1-Motion>", self._drag)
        self.grip.bind("<ButtonRelease-1>", self._end_drag)

        self.button = tk.Button(
            outer, text="▶ 경로 계산", command=self.app.trigger_route,
            bg="#203d52", fg="#ffffff", activebackground="#315875",
            activeforeground="#ffffff", relief="flat", bd=0,
            font=("Segoe UI Emoji", 9, "bold"), padx=8, pady=5,
            cursor="hand2", takefocus=False,
        )
        self.button.pack(side="left", fill="both", expand=True)
        self.button.bind("<ButtonPress-1>", lambda _event: setattr(self.app, '_path_button_pointer_down', True), add='+')
        self.button.bind("<ButtonRelease-1>", lambda _event: setattr(self.app, '_path_button_pointer_down', False), add='+')
        self.attr_state_var = tk.StringVar(value="속성 확인 전")
        self.attr_state_label = tk.Label(outer, textvariable=self.attr_state_var,
            bg="#172432", fg="#f8d67b", font=("Malgun Gothic", 9, "bold"), padx=7, pady=3)
        self.attr_state_label.pack(side="left")
        self.refresh_attribute_state()
        self.button.bind("<ButtonPress-3>", self._start_drag)
        self.button.bind("<B3-Motion>", self._drag)
        self.button.bind("<ButtonRelease-3>", self._end_drag)

        self.win.update_idletasks()
        screen_w, screen_h = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        # v70: migrate old placement once. Align with the game HUD at the left,
        # just above the "7콤보 이상" label. User may drag the grip afterwards.
        if int(self.app.user_settings.get("path_button_position_version", 0) or 0) < 79:
            info = find_emulator_window_info()
            if info:
                gx, gy, gw, gh = info["rect"]
                self.app.user_settings["path_execute_button_xy"] = [
                    gx + int(gw * 0.55), gy + int(gh * 0.156) - 44]
            else:
                self.app.user_settings["path_execute_button_xy"] = [820, 94]
            self.app.user_settings["path_button_user_moved"] = False
            self.app.user_settings.pop("path_execute_button_relative", None)
            self.app.user_settings["path_button_position_version"] = 79
            self.app.save_user_settings()
        saved_xy = self.app.user_settings.get("path_execute_button_xy")
        if isinstance(saved_xy, list) and len(saved_xy) == 2:
            try:
                x, y = int(saved_xy[0]), int(saved_xy[1])
            except (TypeError, ValueError):
                x, y = 45, 165
        else:
            x, y = 45, 165
        width, height = max(138, self.win.winfo_reqwidth()), max(36, self.win.winfo_reqheight())
        x = max(0, min(screen_w - width, x))
        y = max(0, min(screen_h - height, y))
        self.win.geometry(f"{width}x{height}+{x}+{y}")
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
            if mode != 2:
                self.attr_state_label.pack_forget()
                self._fit_attribute_indicator()
                return
            if not self.attr_state_label.winfo_manager():
                self.attr_state_label.pack(side="left")
            text, color = self.app._attribute_status
            self.attr_state_var.set(text)
            self.attr_state_label.configure(fg=color)
            self._fit_attribute_indicator()
        except (tk.TclError, ValueError):
            pass

    def _fit_attribute_indicator(self):
        # The original button had a fixed width; live count text would clip.
        # Keep the user's position when it fits, and prevent a longer status
        # from being clipped off the screen.
        if getattr(self.app, 'toolbar', None) is not self or self._drag_origin is not None:
            return
        self.win.update_idletasks()
        width = max(138, self.win.winfo_reqwidth())
        height = max(36, self.win.winfo_reqheight())
        if (width, height) != (self.win.winfo_width(), self.win.winfo_height()):
            x = max(0, min(self.win.winfo_screenwidth()-width, self.win.winfo_x()))
            y = max(0, min(self.win.winfo_screenheight()-height, self.win.winfo_y()))
            self.win.geometry(f"{width}x{height}+{x}+{y}")

    def _position_path_button_beside_strip(self):
        """Place once above 7콤보 (right half); preserve manual relative position."""
        try:
            info = find_emulator_window_info()
            if not info:
                return
            gx, gy, gw, gh = (int(v) for v in info['rect'])
            self.win.update_idletasks()
            bw, bh = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
            relative = self.app.user_settings.get("path_execute_button_relative")
            moved = self.app.user_settings.get("path_button_user_moved", False)
            if moved and isinstance(relative, list) and len(relative) == 2:
                x = gx + int(gw * float(relative[0]))
                y = gy + int(gh * float(relative[1]))
            else:
                # 1600x900 MuMu reference: 7-combo bar begins near x=880, y=145.
                # Screen coordinates are relative to the entire emulator window.
                x = gx + round(gw * 0.55)
                y = gy + round(gh * 0.156) - bh - 8
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            x = max(0, min(sw - bw, x)); y = max(0, min(sh - bh, y))
            if abs(self.win.winfo_x() - x) > 2 or abs(self.win.winfo_y() - y) > 2:
                self.win.geometry(f'+{x}+{y}')
        except (tk.TclError, ValueError, TypeError):
            pass

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
        except tk.TclError:
            pass

    def toggle_crystal_window(self):
        if self.crystal_window is not None:
            try:
                if self.crystal_window.winfo_exists() and self.crystal_window.state() != "withdrawn":
                    self._save_crystal_window_xy()
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
        except tk.TclError:
            pass
        self.app.root.after_idle(self.app.save_user_settings)

    def _create_crystal_window(self):
        if self.crystal_window is not None:
            try:
                if self.crystal_window.winfo_exists():
                    return
            except tk.TclError:
                pass
        win = tk.Toplevel(self.app.root)
        self.crystal_window = win
        win.title("퍼드0 크리스탈")
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
        self.card_name_label = tk.Label(
            outer, text=f"대상 카드: {self.app.card_name_detected or '이름 확인 필요'}",
            bg="#f1f4f7", fg="#243f56", font=("Segoe UI", 12, "bold"), anchor="w"
        )
        self.card_name_label.pack(fill="x", pady=(0, 8))
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
            tree_frame, columns=columns, show="tree headings", height=6,
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
        height = min(800, max(480, screen_h - 90))
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
        name = '이름 확인 필요' if pending else group.get('name', '등록 대기')
        self.card_name_label.configure(text=f'돌파 대상: {name}')
        pinned=bool(self.app.crystal_panel_pin_var.get())
        self.pin_button.configure(text=('📌 관리창 고정: 켬' if pinned else '📌 관리창 고정: 꺼짐'),
                                  bg=('#177c71' if pinned else '#50697c'))
        self.crystal_window.attributes('-topmost',pinned)
        self.breakthrough_label.configure(text=('모든 재료 충족' if not pending and self.app.all_crystal_requirements_met() else ''))
        self.read_stamp_label.configure(text='돌파 화면 F7 → 게임에서 재료 클릭 → 상세창 F7: 이름 자동 연결. 확인된 재료만 결과창에서 집계합니다.')
        self.refresh_candidate_list()

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
            if self.crystal_window and self.crystal_window.winfo_exists() and self.crystal_window.state() != "withdrawn":
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
        if self._capture_panel_was_visible:
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
        try:
            # Farming HUD must remain visible throughout F6 autoplay.
            # Keep the small route button visible while the mouse executes.
            if self.crystal_window and self.crystal_window.winfo_exists() and self.crystal_window.state() != "withdrawn":
                self._playback_panel_was_visible = True
                self.crystal_window.withdraw()
        except tk.TclError:
            pass

    def restore_after_playback(self):
        """Restore only the windows that were visible before the drag started."""
        self.release_floating_pointer_capture()
        self._temporarily_hidden = False
        try:
            if (getattr(self, "_playback_button_was_visible", False) and not self._user_hidden
                    and self.app._route_button_game_in_foreground() and self.app._path_button_may_show()):
                self.win.deiconify()
                self._position_path_button_beside_strip()
                self.win.attributes("-topmost", True)
                self.win.lift()
            if getattr(self, "_playback_panel_was_visible", False):
                if self.crystal_window and self.crystal_window.winfo_exists():
                    self.crystal_window.deiconify()
                    self.crystal_window.attributes("-topmost", bool(self.app.crystal_panel_pin_var.get()))
                    if self.app.crystal_panel_pin_var.get():
                        self.crystal_window.lift()
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


def known_crystal_name(entry):
    name=str(entry.get('crystal_name') or entry.get('label') or '').strip()
    if not name or re.fullmatch(r'(?:크리스탈\s*\d+|C\d+)',name): return ''
    return name


def crystal_name_key(name):
    return re.sub(r'(?:의)?크리스[탈털]$','',_compact_ocr_text(name))


def crystal_display_name(entry, row, index):
    return (entry.get('crystal_name') or row.get('crystal_name') or entry.get('label')
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
    key,index,expected_id,revision=binding
    group=ledger.get('farming_groups',{}).get(key)
    if not group or group['revision']!=revision or not 0<=index<len(group['materials']):
        raise ValueError('재료 구성이 바뀌어 이름 연결을 보류했습니다. 재료를 다시 클릭해 주세요.')
    source=group['materials'][index]
    if source['id']!=expected_id: raise ValueError('선택한 재료가 바뀌었습니다.')
    entries=database.get('crystals',[])
    entry=next((r for r in entries if str(r['id'])==expected_id),None)
    matching=[r for r in entries if crystal_name_key(r.get('crystal_name') or r.get('label',''))==crystal_name_key(name)]
    if entry is not None:
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
    target.update(crystal_name=name,label=name)
    stocks=updated.setdefault('stocks',{})
    if canonical not in stocks: stocks[canonical]=int(stocks.get(expected_id,0))
    for other in updated['farming_groups'].values():
        changed=False
        for row in other['materials']:
            if row['id']==expected_id or row['id']==canonical:
                row.update(id=canonical,confirmed=True,crystal_name=name,image_file=target['image_file'])
                changed=True
        if changed:
            ids=[r['id'] for r in other['materials']]
            if len(ids)!=len(set(ids)): raise ValueError('한 대상의 서로 다른 재료를 같은 이미지로 합칠 수 없습니다.')
            other['revision']=uuid.uuid4().hex
    if canonical!=expected_id: stocks.pop(expected_id,None)
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
    groups[key] = {'name': name, 'revision': uuid.uuid4().hex, 'materials': rows}
    updated['active_farming_group'] = key
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


def reward_scroll_delta(previous, current):
    """Measure actual content translation; never invent a scroll distance."""
    import cv2
    a, b = np.asarray(reward_viewport(previous)), np.asarray(reward_viewport(current))
    if a.shape != b.shape:
        raise ValueError('집계 중 게임 창 크기가 바뀌었습니다.')
    if float(np.abs(a.astype(np.int16)-b.astype(np.int16)).mean()) < .7:
        return 0.0
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
        self._first_f7_pin_applied = False
        self.resource_selector_active = False
        self.last_ocr_debug = "OCR 아직 실행 전입니다. F7은 크리스탈 재료 영역을 읽습니다. 카드 이름은 제목 영역을 별도로 지정하세요."
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
        self._build_ui()
        # Restore non-destructive preferences from the persistent settings file.
        try:
            self.goal_var.set(max(0, min(3, int(self.user_settings.get("goal_index", 0)))))
            self.skull_safety_var.set(bool(self.user_settings.get("avoid_skull", True)))
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
            metrics=board_goal_metrics(final,goals if goals is not None else self.selected_goals())
            snapshot.update(path=list(path),predicted_final_board=list(final),
                predicted_combos=metrics['combos'],predicted_attributes=metrics['attr_count'],
                goals=list(goals if goals is not None else self.selected_goals()),solver_seconds=elapsed)
        except Exception as exc:
            self._v66_log(f'manual error snapshot metadata unavailable: {exc}')

    def _route_debug_before_drag(self):
        snapshot=getattr(self,'_manual_error_snapshot',None)
        if not snapshot or snapshot['board']!=list(self.board): return
        x,y,w,h=self.board_rect
        snapshot.update(execution_goals=list(self.selected_goals()),
            input_points=[dict(cell=i,x=int(x+(i%COLS+.5)*w/COLS),
                               y=int(y+(i//COLS+.5)*h/ROWS)) for i in self.playback_path])
        try:
            # Transfer ownership of the fresh RGB image; no duplicate full-screen copy.
            snapshot['game_image']=self._capture_reward_monitor_image()
            snapshot['game_window_rect']=list(self._last_reward_monitor_rect)
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
            state=self._route_debug_probe_state)
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
        self._route_ui_active = True
        self._route_ui_waiting_capture = False
        self._route_ui_waiting_playback = False
        self.toolbar.hide_for_playback()
        self.root.iconify()
        self.root.update_idletasks()
        self.root.after(400, self._poll_route_ui_session)
        return True

    def _run_route_capture(self, use_current):
        self._route_ui_waiting_capture = False
        try:
            self.calculate_clicked(use_current=use_current)
        except Exception as exc:
            self.busy = False
            self.set_status(f'경로 계산 오류: {exc}')

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
            if hasattr(self, "skull_safety_var"):
                settings["avoid_skull"] = bool(self.skull_safety_var.get())
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
        goal_options = ("8콤보 이상 목표 (남은 시간에 10콤보까지 탐색)",
                        "동일 색상 드롭 8개 이상 연결",
                        "5속성 공격 (불·물·나무·빛·어둠 · 최대 10초 탐색)",
                        "회복 우선 (콤보 수가 같으면 회복 매치 우선)")
        for index, text in enumerate(goal_options):
            ttk.Radiobutton(goals_frame, text=text, variable=self.goal_var, value=index,
                            command=self._on_goal_mode_changed).pack(anchor="w")
        ttk.Label(goals_frame,
                  text="회복도 일반 콤보에 포함됩니다. 방해(J)는 '방해', 해골(S)은 '독'으로 표시합니다. 독 매치는 피해가 발생할 수 있습니다.",
                  wraplength=535, justify="left").pack(anchor="w", pady=(4, 0))
        self.skull_safety_var = tk.BooleanVar(value=bool(self.user_settings.get("avoid_skull", True)))
        ttk.Checkbutton(goals_frame, text="독 매치 최소화 (권장)", variable=self.skull_safety_var,
                        command=self.save_user_settings).pack(anchor="w", pady=(5, 0))
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
        # Pin on the first accepted F7 of this run; later scans respect manual toggles.
        if not manual and not getattr(self, '_first_f7_pin_applied', False):
            self._first_f7_pin_applied = True
            self.crystal_panel_pin_var.set(True)
            self.root.after_idle(self.save_user_settings)
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
        self.crystal_candidates = []
        self._recent_material_scan_status = "스캔 중"
        self._pending_farming_materials = None
        self.resource_values["crystal_required"] = None
        self.resource_values["crystal_current"] = None
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

    def _run_ocr_tokens(self, image: Image.Image):
        engine = self._get_ocr_engine()
        rgb = np.asarray(image.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        if not self._ocr_infer_lock.acquire(blocking=False):
            raise RuntimeError("OCR 엔진이 이전 작업을 처리 중입니다. 잠시 후 다시 시도하세요.")
        try:
            raw_result = engine(bgr, use_det=True, use_cls=True, use_rec=True)
        finally:
            self._ocr_infer_lock.release()
        return rapidocr_result_to_tokens(raw_result)

    def _recognize_number_crop(self, image, allow_multiplier=False):
        """Read an already isolated number without the text detector or rotation model."""
        engine = self._get_ocr_engine()
        bgr = np.asarray(image.convert('RGB'))[:,:,::-1].copy()
        if not self._ocr_infer_lock.acquire(blocking=False):
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
        try:
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
                    title_for_ocr = title_image.resize((max(1, title_image.width * 3), max(1, title_image.height * 3)), Image.Resampling.LANCZOS)
                    title_tokens = [t for t in self._run_ocr_tokens(title_for_ocr) if t.score >= .60]
                    detail_name=crystal_detail_name(title_tokens)
                    if detail_name:
                        binding=getattr(self,'_crystal_detail_binding_snapshot',None)
                        if generation == getattr(self,'_resource_scan_generation',0):
                            self.root.after(0,lambda name=detail_name,b=binding:self._on_crystal_detail_done(name,b))
                        return
                    card_name = extract_card_name_from_title(title_tokens, title_for_ocr.size)
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
            self.root.after(0, lambda v=values,d=debug,m=manual,targets=targets_meta,cn=card_name,
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
            self.root.after(0, lambda d=message, m=manual, fb=fallback_on_failure:
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
            self.set_status('재료는 읽었지만 돌파 대상 이름을 확인하지 못했습니다. [돌파 이름 직접 지정]으로 등록을 완료하세요.')
            if hasattr(self, 'toolbar'):
                self.toolbar.show_crystal_window()
                self.toolbar.refresh()
            return
        try:
            key=self._register_farming_profile(card_name, targets)
            self._arm_crystal_clicks(key,targets,source_context)
        except (ValueError, OSError) as exc:
            self.set_status(f'재료 등록 보류: {exc}')

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
            ResourceRegionSelector(self)
        except Exception:
            self.resource_selector_active = False
            raise

    def on_resource_area_selected(self, rect):
        self.resource_selector_active = False
        self._remember_resource_anchor(rect)
        self.set_status(f"크리스탈 재료 영역 저장 완료 ({rect[2]}×{rect[3]}). 빨간 필요 수량과 아래 ×N을 판독합니다…")
        # Show the crystal panel only after OCR completes.
        self.root.after(100, lambda: self.request_resource_ocr(manual=True, fallback_on_failure=False))

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
            CardTitleRegionSelector(self)
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
                                self.root.after(0, lambda n=parsed, d=debug: self._on_card_name_ocr_done(n, d, None))
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
            self.root.after(0, lambda n=name, d=debug: self._on_card_name_ocr_done(n, d, None if n else "카드 이름을 읽지 못했습니다."))
        except Exception as exc:
            debug = f"카드 이름 OCR 오류: {type(exc).__name__}: {exc}\n캡처 이미지: {getattr(self, 'last_card_title_crop_path', '')}"
            try:
                self.root.after(0, lambda d=debug: self._on_card_name_ocr_done(None, d, d))
            except tk.TclError:
                pass

    def _on_card_name_ocr_done(self, name, debug, error):
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
        try:
            monitor_origin = (int(monitor.get("left", 0)), int(monitor.get("top", 0)))
            # 1) Prefer the saved normalized location in the current MuMu window. This is fast
            # and tracks moving/resized emulator windows without asking the user again.
            rel_rect = self._relative_rect_for_window(window_info)
            if rel_rect:
                exact, _ = self._crop_rect_from_desktop(screen_image, monitor, rel_rect, margin=0)
                if exact is not None and _board_image_is_plausible(exact):
                    self.root.after(0, lambda r=rel_rect: self._on_auto_area_detected(r, 0.99, None, then_calculate))
                    return
            # 2) Validate the last absolute rect (older settings may not have a MuMu anchor).
            if saved_rect:
                exact, _ = self._crop_rect_from_desktop(screen_image, monitor, saved_rect, margin=0)
                if exact is not None and _board_image_is_plausible(exact):
                    self.root.after(0, lambda r=tuple(saved_rect): self._on_auto_area_detected(r, 0.98, None, then_calculate))
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
                            self.root.after(0, lambda r=candidate, c=confidence: self._on_auto_area_detected(r, c, None, then_calculate))
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
                            self.root.after(0, lambda r=candidate, c=confidence: self._on_auto_area_detected(r, c, None, then_calculate))
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
            self.root.after(0, lambda: self._on_auto_area_detected(rect, confidence, None, then_calculate))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda msg=message: self._on_auto_area_detected(None, 0.0, msg, then_calculate))

    def _on_auto_area_detected(self, rect, confidence, error, then_calculate):
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
            BoardSelector(self)
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
        win.geometry("850x480")
        win.minsize(700, 340)
        self.crystal_ledger_window = win
        frame = ttk.Frame(win, padding=10); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="목표로 지정한 크리스탈만 집계합니다. 이름 변경 후에도 아이콘 데이터는 사용자 데이터 폴더에 유지됩니다.",
                  wraplength=620).pack(anchor="w", pady=(0, 8))
        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        table_frame = ttk.Frame(frame); table_frame.pack(fill="both", expand=True)
        columns = ("label", "required", "owned", "short", "count")
        ttk.Style(win).configure("CrystalLedger.Treeview", rowheight=54)
        self.crystal_ledger_tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", height=5,
                                                style="CrystalLedger.Treeview")
        self.crystal_ledger_tree.heading("#0", text="아이콘")
        self.crystal_ledger_tree.column("#0", width=64, stretch=False, anchor="center")
        self._crystal_photo_refs = []
        for key, title, width in (("label", "크리스탈 이름", 330), ("required", "필요", 78),
                                  ("owned", "보유", 78), ("short", "부족", 78),
                                  ("count", "자동 획득 누계", 105)):
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
                                    owned if owned is not None else '—', shortage, counts.get(cid,0)),
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
        try:
            # Foreground may be the farming strip after pressing its button;
            # unlike the background watcher, a direct user click permits capture.
            with mss.mss() as sct:
                shot = sct.grab(rect)
                image = Image.frombytes('RGB', shot.size, shot.rgb)
            debug_path = self.data_paths['root'] / 'debug' / 'last_manual_reward.png'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(debug_path)
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
            self.root.after(0, lambda: self._finish_manual_reward_scan(items,hits,count,clipped,None))
        except Exception as exc:
            self._v66_log(f'{APP_VERSION} manual reward error: {type(exc).__name__}: {exc}')
            self.root.after(0, lambda error=str(exc): self._finish_manual_reward_scan([],[],0,False,error))

    def _finish_manual_reward_scan(self, items, hits, count, clipped, error):
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
                if not self._emulator_is_foreground_for_reward():
                    continue
                # v87: inspect only the fixed title area first. Do not run
                # full-screen OCR/card detection on stage-transition frames.
                if now - last_title_check < 2.0:
                    continue
                last_title_check = now
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
        try:
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
            self.root.after(0, lambda: self._on_reward_scan_done(has_reward_title, items, sig, debug))
        except Exception as exc:
            message = f"획득 결과 OCR 오류: {type(exc).__name__}: {exc}"
            self.root.after(0, lambda msg=message: self._on_reward_scan_done(False, [], None, msg))

    def _on_reward_scan_done(self, has_title, items, signature, debug):
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
            if self.goal_var.get() == 2:
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
                if int(self.goal_var.get()) == 2:
                    self._set_attribute_status(*five_attribute_inventory_status(board))
                else:
                    self._set_attribute_status("퍼즐판 인식됨 · 계산 버튼/F6", "#84edbc")
                self.toolbar.raise_to_top()

    def _attribute_probe_tick(self):
        """Display-only precheck. F6/button remain the only route-search triggers."""
        self._attribute_probe_after = None
        if self._attribute_probe_stopped:
            return
        try:
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
        self._reset_attribute_probe("5속성: 확인 대기")

    def selected_goals(self) -> tuple[bool, bool, bool, bool]:
        """Return exactly one enabled goal based on the radio selection."""
        try:
            selected = int(self.goal_var.get())
        except (AttributeError, ValueError, tk.TclError):
            selected = 0
        selected = max(0, min(3, selected))
        return tuple(index == selected for index in range(4))

    def selected_skull_safety(self) -> bool:
        try:
            return bool(self.skull_safety_var.get())
        except (AttributeError, tk.TclError):
            return True

    def calculate_clicked(self, use_current=False):
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
        if self._begin_route_ui_session():
            self._route_ui_waiting_capture = True
            # Let Windows finish minimizing/unmapping before reading the board.
            self.root.after(200, lambda: self._run_route_capture(use_current))
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
        goals = self.selected_goals()
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
        self._reset_attribute_probe("5속성: 경로 계산 중")
        self.busy = True
        self._v88_calc_start = time.perf_counter()
        if auto_execute:
            self.overlay.hide()  # Remove any route from the previous calculation immediately.
        self.set_status("경로 탐색 중…")
        threading.Thread(target=self._solve_worker,
                         args=(board, list(self.spikes), goals, auto_execute, avoid_skull), daemon=True).start()

    def _restricted_drag_move_cap(self):
        """Return (move_cap, seconds_or_None) only when enemy-shortened timer bar is visible.

        OCR is restricted to the cyan/red time bar; an unconfident time read
        produces a conservative path cap, not a guessed number of seconds.
        """
        try:
            image = self._capture_reward_monitor_image()  # emulator-only screenshot
            rgb = np.asarray(image.convert("RGB"))
            H, W = rgb.shape[:2]
            x0, x1 = int(W*.53), int(W*.99)
            y0, y1 = int(H*.19), int(H*.30)
            region = rgb[y0:y1, x0:x1]
            if region.size == 0: return None, None
            r, g, b = region[...,0].astype(np.int16), region[...,1].astype(np.int16), region[...,2].astype(np.int16)
            cyan = (g > 145) & (b > 145) & (r < 135)
            red = (r > 170) & (g < 140) & (b < 135)
            # A thin horizontal red segment attached to the long cyan timer bar.
            lengths = np.sum(cyan | red, axis=1)
            row = int(np.argmax(lengths)); filled = int(lengths[row])
            nred = int(red[max(0,row-2):row+3].sum())
            if filled < W*.22 or nred < W*.09:
                return None, None
            yline = y0 + row
            timer_crop = image.crop((x0, max(0,yline-33), x1, min(H,yline+40)))
            seconds = None
            try:
                local = self._run_ocr_tokens(timer_crop.resize((timer_crop.width*2,timer_crop.height*2), Image.Resampling.LANCZOS))
                viable=[]
                for t in local:
                    val = t.text.strip()
                    if re.fullmatch(r"\d{1,2}", val) and 3 <= int(val) <= 11:
                        viable.append((t.score, int(val)))
                if len({v for _,v in viable}) == 1:
                    seconds = viable[0][1]
            except Exception as exc:
                self._v66_log(f"v78 timer OCR unavailable: {exc}")
            cap = max(12, int((seconds-1.2)/0.14)) if seconds else 40
            cap = min(65, cap)
            self._v66_log(f"v78 reduced timer: seconds={seconds}, safe move cap={cap}, red={nred}, filled={filled}")
            return cap, seconds
        except Exception as exc:
            self._v66_log(f"v78 reduced timer scan failed: {exc}")
            return None, None

    def _solve_worker(self, board, spikes, goals, auto_execute=False, avoid_skull=True):
        try:
            started = time.perf_counter()
            move_cap, detected_seconds = self._restricted_drag_move_cap()
            timer_elapsed = time.perf_counter() - started
            budget = FIVE_ATTR_TIME_BUDGET if goals[2] else SOLVER_TIME_BUDGET
            self._v66_log(f"{APP_VERSION} solver strategy: {'5-attribute search' if goals[2] else 'normal'}, budget={budget}")
            cap = move_cap if move_cap is not None else (FIVE_ATTR_DEFAULT_MOVE_CAP if goals[2] else SOLVER_MAX_MOVES)
            if goals[2]:
                path, combos, matched, after = solve_five_attributes_v101(
                    board, time_budget=budget, max_moves=cap, spikes=spikes, avoid_skull=avoid_skull)
            else:
                path, combos, matched, after = solve_path(
                    board, time_budget=budget, max_moves=cap,
                    beam_width=SOLVER_BEAM_WIDTH, goals=goals, avoid_skull=avoid_skull, spikes=spikes)
            if goals[2]:
                self._v66_log(f"{APP_VERSION} attribute search result: {board_goal_metrics(after, (False,False,True,False))['attr_count']}/5")
            self._v66_log(f"v78 path time cap={move_cap} seconds={detected_seconds} steps={len(path)-1}")
            elapsed = time.perf_counter() - started
            self._v66_log(f"{APP_VERSION} timing: timer_scan={timer_elapsed:.3f}s solver={elapsed-timer_elapsed:.3f}s total_worker={elapsed:.3f}s")
            self.root.after(0, lambda: self._on_solved(
                list(board), list(spikes), path, combos, matched, elapsed, auto_execute, False, goals, avoid_skull))
        except Exception as exc:
            self.root.after(0, lambda msg=str(exc): self._on_solve_error(msg))

    def _on_solve_error(self, message: str):
        self._route_debug_update(dict(phase='solver_error',error=message))
        self.busy = False
        self.set_status(f"오류: {message}")

    def _on_solved(self, board, spikes, path, combos, matched, elapsed, auto_execute, from_auto, goals=None, avoid_skull=True):
        self._route_debug_solution(board,path,elapsed,goals)
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
        details = board_goal_metrics(final_board, selected)
        spikes_cleared, spike_crossings = spike_route_metrics(board, spikes, path)
        unmet = [goal_label(i) for i, enabled in enumerate(selected)
                 if enabled and not details["achieved"][i]]
        warning = "" if self.overlay.clickthrough_ok else " · 경고: 클릭 통과 설정을 확인하지 못했어요"
        label = "자동 갱신" if from_auto else "인식 완료"
        result_text = (
            f"{label} · 예상 {combos}콤보 / {matched}개 드롭 매치 · 이동 {len(path)-1}회 · "
            f"빔 서치 {elapsed:.1f}초"
        )
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
            if combos >= 8:
                result_text += f" · 8콤보 이상 경로 발견 ({combos}콤보)"
            else:
                result_text += f" · 8콤보 경로 미발견: 현재 탐색 최고 {combos}콤보 (불가능 확정은 아님)"
        elif any(selected):
            if unmet:
                result_text += " · 선택 목표 미달: " + ", ".join(unmet)
            else:
                result_text += " · 선택한 조건 달성"
        if selected[3]:
            result_text += f" · 회복 매치 {details['recovery_matched']}개"
        if self.board:
            counts = {code: self.board.count(code) for code in COLOR_ORDER if self.board.count(code)}
            brief = ", ".join(f"{COLOR_INFO[k][0]} {v}" for k, v in counts.items())
            result_text += f" · 판독: {brief}"
        result_text += f". 최적해 보장은 아니에요{warning}. F6=다시 계산 · Shift+F6=새 탐색"
        if selected and selected[0]:
            goal_result = "8+ 달성" if combos >= 8 else "8+ 미달"
        else:
            goal_result = "목표 충족" if not unmet else "목표 미달"
        self.last_result_summary = f"결과: {combos}콤보" + (f" · 공격 {details['attr_count']}/5속성" if selected[2] else "") + f" · 가시 제거 {spikes_cleared} · 가시 통과 {spike_crossings} · 독 {details['skull_matched']} · {goal_result}"
        self.set_status(result_text)
        # Selected goals are required. Never execute a near miss automatically.
        if auto_execute:
            if unmet:
                reason = " · 드롭 개수 부족으로 5속성 매치 불가" if selected[2] and not five_attribute_possible_by_count(board) else ""
                self.set_status(result_text + reason + " · 목표 미달: 자동 실행 보류")
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
            self.set_status("실행 보류: 유효하지 않은 보드 또는 경로입니다.")
            return False
        goals = self.selected_goals()
        details = board_goal_metrics(board_after_drag(board, path), goals)
        unmet = [goal_label(i) for i, enabled in enumerate(goals) if enabled and not details['achieved'][i]]
        if unmet:
            self.set_status("실행 보류: 선택 목표 미달 · " + ", ".join(unmet))
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
        if sys.platform=='win32':
            try:
                self._crystal_observer=WindowsMaterialClickObserver(self._record_crystal_click)
                self._crystal_observer.start()
            except Exception as exc:
                self._v66_log(f'material click observer unavailable: {exc}')
                self._crystal_observer=None
        self._crystal_click_after=self.root.after(100,self._crystal_click_tick)

    def _arm_crystal_clicks(self,key,targets,source):
        if not source or not source.get('title_image'): return
        ledger=self._load_crystal_ledger(); group=ledger['farming_groups'][key]
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
            slots.append(dict(index=index,id=group['materials'][index]['id'],box=region,signature=signature))
        self._crystal_click_epoch+=1
        self._crystal_click_map=dict(source,key=key,revision=group['revision'],slots=slots,
                                     title_box=box,title_mask=expected,nonce=uuid.uuid4().hex)
        self._crystal_click_ready=None
        self._crystal_pending_click=None
        self._crystal_context_next=0.0

    def _record_crystal_click(self,x,y,owner):
        # Native hook thread: constant-time filtering and queue only, no Tk or I/O.
        state=self._crystal_click_ready
        if self.ocr_busy or self.busy or self.playback_active: return
        mapping=getattr(self,'_crystal_click_map',None)
        if not state or time.monotonic()-state['at']>.9 or owner!=state['hwnd']:
            if mapping and owner==mapping['hwnd']:
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
                    self.set_status(f"클릭한 재료 {binding[1]+1} 연결 대기 · 상세창에서 F7을 누르세요.")
            elif message[0]=='other_click':
                self._crystal_pending_click=None
            else:
                _,epoch,ready=message
                self._crystal_context_busy=False
                if epoch==self._crystal_click_epoch: self._crystal_click_ready=ready

    def _crystal_click_tick(self):
        self._crystal_click_after=None
        if self._crystal_click_stopped: return
        try:
            self._drain_crystal_clicks()
            if (self._crystal_click_map and not self._crystal_context_busy and not self.ocr_busy
                    and not self.busy and not self.playback_active and time.monotonic()>=self._crystal_context_next):
                self._crystal_context_busy=True
                self._crystal_context_next=time.monotonic()+.12
                threading.Thread(target=self._crystal_context_worker,
                    args=(self._crystal_click_map,self._crystal_click_epoch),daemon=True).start()
        finally:
            if not self._crystal_click_stopped and self.root.winfo_exists():
                self._crystal_click_after=self.root.after(100,self._crystal_click_tick)

    def _on_crystal_detail_done(self,name,binding,error=None):
        self.ocr_busy=False
        if error or not name:
            self.set_status('크리스탈 이름 판독 보류: '+str(error or '제목을 읽지 못했습니다.'))
            return
        if not binding:
            self.set_status(f'{name} 확인 · 돌파 화면에서 F7로 재료 위치를 읽은 뒤 해당 재료를 클릭하고 F7을 눌러 주세요.')
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
                if not image_file: raise ValueError('클릭한 재료 이미지가 없습니다. 돌파 화면에서 F7로 다시 읽어 주세요.')
                # A known material name may only reuse a compatible existing image.
                matches=[r for r in db['crystals'] if crystal_name_key(r.get('crystal_name') or r.get('label',''))==crystal_name_key(name)]
                if not entry and len(matches)==1:
                    with Image.open(self.data_paths['icons_dir']/image_file) as a, Image.open(self.data_paths['icons_dir']/matches[0]['image_file']) as b:
                        score=min(float(np.abs(x-y).mean()) for x,y in zip(crystal_art_signatures(a),crystal_art_signatures(b)))
                    if score>=13: raise ValueError('같은 이름의 기존 이미지와 일치하지 않아 중복 등록을 보류했습니다.')
                updated,database,canonical=name_crystal_material(ledger,db,binding,name,image_file)
                _atomic_json_write(self.data_paths['database'],database)
                _atomic_json_write(self.data_paths['ledger'],updated)
            self._farming_sync_views(updated)
            self._crystal_pending_click=None
            if self._crystal_click_map and self._crystal_click_map['key']==key:
                mapping=dict(self._crystal_click_map)
                mapping['revision']=updated['farming_groups'][key]['revision']
                mapping['slots']=[dict(slot,id=updated['farming_groups'][key]['materials'][slot['index']]['id']) for slot in mapping['slots']]
                self._crystal_click_map=mapping
                self._crystal_click_epoch+=1;self._crystal_click_ready=None
            self._farming_attempts=0
            self.toolbar.show_crystal_window()
            self.toolbar.refresh()
            self.set_status(f"{updated['farming_groups'][key]['name']} · 재료 {index+1}: {name} 저장 · 기존 이미지 재사용")
        except (OSError,ValueError,KeyError) as exc:
            self.set_status(f'크리스탈 이름 등록 보류: {exc}')

    def _farming_init(self):
        self._farming_queue = queue.SimpleQueue()
        self._farming_probe_busy = False
        self._farming_probe_next = 0.0
        self._farming_positive = 0
        self._farming_negative = 0
        self._farming_result_visible = False
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
        self.set_status(f'{name}: 재료 {len(targets)}종 확인 · 게임에서 재료를 클릭하고 상세창에서 F7로 이름을 등록하세요.')
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
        # A real stable board, rather than F6, starts the next reward visit.
        if self._farming_battle_seen or getattr(self, '_manual_reward_busy', False):
            return
        self._farming_battle_seen = True
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

    def _farming_probe_worker(self, rect):
        try:
            image = self._farming_capture(rect)
            small = image.resize((800, 470), Image.Resampling.BILINEAR)
            found = self._looks_like_reward_frame(small)
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
            self._farming_queue.put(('probe', found, time.monotonic(), rect, board_visible))
        except Exception:
            self._farming_queue.put(('probe', False, time.monotonic(), rect))

    def _farming_capture(self, rect):
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
            # Our statistics live to the left of the result list. Never read another app.
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
            while True:
                try:
                    message = self._farming_queue.get_nowait()
                except queue.Empty:
                    break
                if message[0] == 'scan':
                    self._farming_finish_scan(*message[1:])
                    continue
                _, present, captured_at, rect = message[:4]
                self._farming_probe_busy = False
                current = self._reward_game_capture_rect()
                if time.monotonic()-captured_at > 2 or current != rect:
                    continue
                if len(message) > 4 and message[4]:
                    self._farming_observe_board()
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
            if self._farming_result_visible:
                ledger = self._farming_begin_result()
                session = ledger['reward_session']
                if session.get('credited'):
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
        if not any(r.get('confirmed',True) for g in ledger['farming_groups'].values() for r in g['materials']):
            self._farming_attempts=3
            self._farming_show_stats('재료 이름 등록 대기 · 돌파 화면에서 재료를 클릭하고 상세창 F7로 이름을 확인하세요.')
            return
        self._manual_reward_busy = True
        self._farming_attempts += 1
        self._farming_scan_cancel.clear()
        self._farming_retry_at = time.monotonic()+5.0
        self._farming_show_stats('자동 인식 중 · 완료될 때까지 결과창을 유지해 주세요. F10으로 중지할 수 있습니다.')
        if (self._farming_stats is not None and not getattr(self, '_farming_stats_capture_excluded', False)
                and rect['width']*.29 < 358):
            self._farming_stats.withdraw()
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
        if self._farming_scan_cancel.is_set():
            raise RuntimeError('재료 인식을 중지했습니다.')
        self._farming_capture(rect)  # Verify the game is still the input target.
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

    def _farming_read_page(self, image):
        boxes = [b for b in detect_reward_card_rects(image)
                 if b[1] > image.height*.23 and b[3] < image.height*.761]
        read = lambda crop: [t for t in self._run_ocr_tokens(crop) if t.score >= .60]
        tokens = read(image)
        for x0,y0,x1,y1 in boxes:
            values = {n for n,t in _find_reward_amount_tokens(tokens, image.size)
                      if x0-8 <= t.cx <= x1+9 and y0+.58*(y1-y0) <= t.cy <= y1+17}
            if len(values) > 1:
                raise ValueError('같은 카드 수량이 다르게 읽혀 집계를 보류했습니다.')
        items = extract_visual_reward_items(image, tokens, read)
        items = [i for i in items if i['screen_box'] in boxes]
        if len(items) != len(boxes):
            raise ValueError('카드 아래 획득 수량을 전부 읽지 못해 집계를 보류했습니다.')
        return items

    def _farming_collect_pages(self, rect, first):
        balance = 0
        image = first
        aggregate = []
        offset = 0.0
        try:
            # Normalize to the beginning even if the user opened the bottom of the list.
            for _ in range(24):
                position = reward_scrollbar_position(image)
                if position is None or position[0]:
                    break
                self._farming_wheel(rect, 120)
                balance += 1
                new = self._farming_capture(rect)
                if not self._looks_like_reward_frame(new):
                    raise ValueError('결과창이 닫혀 집계를 중단했습니다.')
                delta = reward_scroll_delta(image, new)
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
                page = self._farming_read_page(image)
                aggregate = merge_reward_page(aggregate, page, offset, image.height)
                position = reward_scrollbar_position(image)
                if position is None or position[1]:
                    if not aggregate:
                        raise ValueError('온전히 보이는 보상 카드가 없습니다.')
                    if position is None and any(b[3] >= image.height*.761 for b in detect_reward_card_rects(image)):
                        raise ValueError('화면 아래 잘린 보상이 있어 전체 집계를 확인하지 못했습니다.')
                    return aggregate
                self._farming_wheel(rect, -120)
                balance -= 1
                new = self._farming_capture(rect)
                if not self._looks_like_reward_frame(new):
                    raise ValueError('결과창이 닫혀 집계를 중단했습니다.')
                delta = reward_scroll_delta(image, new)
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

    def _farming_match_items(self, items, groups):
        import cv2
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
        for group in groups.values():
            for row in group['materials']:
                if row['id'] not in templates and row.get('image_file'):
                    path=self.data_paths['icons_dir']/row['image_file']
                    if path.is_file():
                        with Image.open(path) as source: templates[row['id']]=crystal_art_signatures(source)
        if not wanted: raise ValueError('재료 이름이 아직 등록되지 않았습니다. 게임에서 재료를 클릭하고 상세창에서 F7을 누르세요.')
        amounts = {}
        for item in items:
            probe = crystal_art_signatures(item['crop'])
            ranks = sorted((min(float(np.abs(a-b).mean()) for a, b in zip(probe, refs)), cid)
                           for cid, refs in templates.items())
            best, cid = ranks[0]
            next_score = ranks[1][0] if len(ranks) > 1 else 100.0
            if best < 13.0:
                if next_score-best < 3.5:
                    raise ValueError('서로 비슷한 재료 아이콘을 구분하지 못해 집계를 보류했습니다.')
                if cid in wanted:
                    amounts[cid] = amounts.get(cid, 0)+int(item['amount'])
            elif best < 18.0:
                raise ValueError('등록한 재료와 유사한 아이콘이 있어 확인을 보류했습니다.')
        return amounts

    def _farming_scan_worker(self, rect, session_id, groups):
        amounts, error = None, None
        try:
            time.sleep(.30)  # Let our windows finish minimizing before sampling.
            first = self._farming_capture(rect)
            if not self._looks_like_reward_frame(first):
                raise ValueError('크리스탈 결과창이 확인되지 않습니다.')
            header = first.crop((round(first.width*.08), round(first.height*.04),
                                 round(first.width*.44), round(first.height*.113)))
            header = header.resize((header.width*2, header.height*2), Image.Resampling.LANCZOS)
            if '결과' not in ''.join(_compact_ocr_text(t.text) for t in self._run_ocr_tokens(header)):
                raise ValueError('상단 결과 제목을 확인하지 못해 집계를 보류했습니다.')
            items = self._farming_collect_pages(rect, first)
            amounts = self._farming_match_items(items, groups)
            if self._farming_scan_cancel.is_set() or not self._looks_like_reward_frame(self._farming_capture(rect)):
                raise ValueError('결과창 확인이 끝나기 전에 화면이 바뀌었습니다.')
        except Exception as exc:
            error = str(exc)
        self._farming_queue.put(('scan', session_id, groups, amounts, error))

    def _farming_finish_scan(self, session_id, groups, amounts, error):
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
        self._farming_last_message = message
        ledger = self._load_crystal_ledger()
        groups = ledger.get('farming_groups', {})
        session = ledger.get('reward_session', {})
        if not groups or not self._farming_result_visible:
            return
        self._farming_displayed_session = session.get('id')
        if self._farming_hidden_session == session.get('id'):
            return
        if self._farming_stats is None or not self._farming_stats.winfo_exists():
            win = tk.Toplevel(self.root)
            self._farming_stats = win
            win.withdraw()
            win.title('크리스탈 획득 통계')
            win.attributes('-topmost', True)
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
        self._farming_retry_button.state(['disabled' if getattr(self, '_manual_reward_busy', False) else '!disabled'])
        if self._farming_stats.state() == 'withdrawn':
            rect = self._reward_game_capture_rect()
            if rect:
                self._farming_stats.geometry(f"350x450+{max(0,rect['left']+8)}+{max(0,rect['top']+145)}")
            self._farming_stats.deiconify()
            self._farming_stats_capture_excluded = exclude_own_window_from_capture(self._farming_stats)
            if sys.platform == 'win32':
                self._farming_stats_hwnd = int(ctypes.windll.user32.GetAncestor(self._farming_stats.winfo_id(), 2))
                if not hasattr(self, '_farming_allowed_hwnds'):
                    self._farming_allowed_hwnds = set()
                self._farming_allowed_hwnds.add(self._farming_stats_hwnd)

    def _farming_hide_stats(self):
        ledger = self._load_crystal_ledger()
        self._farming_hidden_session = ledger.get('reward_session', {}).get('id')
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
        if getattr(self, '_manual_reward_busy', False):
            return
        session = self._load_crystal_ledger().get('reward_session', {})
        if session.get('credited'):
            self._farming_show_stats('이미 집계한 결과입니다. 같은 결과는 다시 더하지 않습니다.')
            return
        self._farming_attempts = 0
        self._farming_retry_at = 0.0
        self._farming_scan_cancel.clear()

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
        self.set_status(message)
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
        if getattr(self, '_manual_reward_busy', False) and hasattr(self, '_farming_scan_cancel'):
            self._farming_scan_cancel.set()
            mouse = getattr(self, '_result_scroll_input', None)
            if mouse is not None:
                try:
                    mouse.close()
                except Exception:
                    pass
            self.set_status('재료 자동 인식을 중지하는 중입니다.')
        if not self.playback_active:
            return
        self.play_cancel.set()
        mouse = getattr(self, '_active_drag_input', None)
        if mouse is not None:
            try:
                mouse.close()
            except Exception:
                pass
        if not self.drag_in_progress:
            self._cancel_countdown("자동 실행을 취소했어요.")
        else:
            self.set_status("자동 실행 중지 요청됨. 마우스 버튼을 놓는 중…")

    def _cancel_countdown(self, message="자동 실행을 취소했어요."):
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
        self.set_status(message)


    def close(self):
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
    if __import__("sys").platform != "win32":
        print("이 시험판은 Windows 전용입니다.")
    App().run()
