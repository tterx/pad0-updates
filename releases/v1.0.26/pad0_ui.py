"""Windows, overlays, editor/settings views and layout helpers.

This module imports domain APIs, never the main controller or device backends.
AppUIMixin requires the controller's state/methods and _ui_backend_services().
Importing it creates no Tk windows, native handles, devices or user files.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import Callable
from PIL import Image

from pad0_ai import (
    AI_REVIEW_ACTIONS,
    ai_github_credential,
    ai_github_repo,
    ai_record_share_changes,
    ai_review_feedback,
    ai_review_list,
    ai_storage_guide_text,
)
from pad0_common import (
    AI_GITHUB_DEFAULT_REPO,
    APP_TITLE,
    COLOR_INFO,
    ROUTE_GOAL_OPTIONS,
    SKILL_TACTIC_STATES,
    skill_tactics_party,
    normalize_skill_tactics,
    _atomic_json_write,
    _load_json_dict,
)
from pad0_crystal import (
    background_goal_complete,
    background_target_key,
    configured_farming_selection,
    crystal_display_name,
    crystal_name_key,
    farming_group_rows,
    known_crystal_name,
    save_farming_selection,
)
from pad0_solver import (
    COLOR_ORDER,
    COLS,
    ROWS,
    five_attribute_inventory,
)


@dataclass(frozen=True)
class UIBackendServices:
    """Explicit controller callbacks; constructing this object starts no work."""
    probe_factory: Callable
    portable_adb_path: Callable
    instance_index: Callable
    connection_help: Callable
    awakening_labels: object
    skill_labels: object
    action_current: Callable
    profile_merge: Callable
    skill_details: Callable
    confirm_awakenings: Callable
    awakening_effects: Callable
    record_party_review: Callable
    resume_actions: Callable
    share_awakening_description: Callable


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


def path_hud_geometry(game_rect,board_rect=None,preferred=None,width=260,height=84,screen=(1920,1080)):
    gx,gy,gw,gh=map(int,game_rect)
    edge=gx+round(gw*.55)
    if isinstance(board_rect,(tuple,list)) and len(board_rect)==4:edge=min(edge,int(board_rect[0]))
    x,y=preferred if preferred is not None else (edge-width-6,gy+round(gh*.22))
    x=max(0,min(int(x),edge-width-6,screen[0]-width))
    y=max(0,min(int(y),screen[1]-height))
    return x,y,width,height


class FloatingPathExecuteButton:
    """A draggable path-execution button; the crystal panel is opened from the main app."""
    def __init__(self, app):
        self.app = app
        if app.user_settings.get('path_button_size_version')!=179:
            saved=app.user_settings.get('path_execute_button_xy')
            relative=app.user_settings.get('path_execute_button_relative')
            info=find_emulator_window_info()
            if app.user_settings.get('path_button_user_moved'):
                if isinstance(saved,list) and len(saved)==2:app.user_settings['path_execute_button_xy']=[saved[0]+24,saved[1]]
                if isinstance(relative,list) and len(relative)==2 and info:
                    app.user_settings['path_execute_button_relative']=[relative[0]+24/info['rect'][2],relative[1]]
            app.user_settings['path_button_size_version']=179
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
            font=("Segoe UI Emoji", 11, "bold"), padx=8, pady=8,wraplength=220,
            cursor="hand2", takefocus=False,
        )
        self.path_body.grid_columnconfigure(0,weight=1)
        self.button.grid(row=0,column=0,sticky='nsew')
        self.attribute_strip=tk.Frame(self.path_body,bg='#172432')
        self.attribute_strip.grid(row=1,column=0,sticky='ew',padx=(7,7),pady=(5,0))
        self.five_state_label=tk.Label(self.attribute_strip,text='5속성: 확인 대기',
            bg='#172432',fg='#f8d67b',font=('Malgun Gothic',9),anchor='w',justify='left',wraplength=220)
        self.five_state_label.pack(fill='x')
        self.attribute_counts_frame=tk.Frame(self.attribute_strip,bg='#172432',width=220,height=38)
        self.attribute_counts_frame.pack(fill='x',pady=(3,0))
        self.attribute_counts_frame.pack_propagate(False)
        self.attribute_shortage_label=tk.Label(self.attribute_counts_frame,text='속성 확인 전',
            bg='#172432',fg='#a9c7da',font=('Malgun Gothic',9),anchor='w',justify='left',wraplength=210)
        self.attribute_shortage_label.place(x=0,y=0,relwidth=1,relheight=1)
        self.button.bind("<ButtonPress-1>", lambda _event: setattr(self.app, '_path_button_pointer_down', True), add='+')
        self.button.bind("<ButtonRelease-1>", lambda _event: setattr(self.app, '_path_button_pointer_down', False), add='+')
        self.attr_state_var = tk.StringVar(value="속성 확인 전")
        self.status_body=tk.Frame(self.path_body,bg='#172432')
        self.status_body.grid(row=2,column=0,sticky='ew')
        self.route_actions=tk.Frame(self.path_body,bg='#172432')
        self.route_actions.grid(row=3,column=0,sticky='ew')
        tk.Button(self.route_actions,text='다시 계산',command=lambda:self.app.trigger_route(force=True),bg='#294a60',fg='white',relief='flat').pack(side='left',fill='x',expand=True)
        tk.Button(self.route_actions,text='최고 콤보 실행',command=self.app.trigger_best_combo,bg='#294a60',fg='white',relief='flat').pack(side='left',fill='x',expand=True)

        self.attr_state_label = tk.Label(self.status_body, textvariable=self.attr_state_var,
            bg="#172432", fg="#f8d67b", font=("Malgun Gothic", 9), padx=7, pady=7,
            anchor="w",justify="left",wraplength=220)
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
        width,height=self._path_hud_width(),max(84,self.win.winfo_reqheight())
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
            strip=getattr(self,'attribute_strip',None)
            if self.app.selected_goals()[4]:
                if strip is not None:strip.grid_remove()
                if not self.attr_state_label.winfo_manager():self.attr_state_label.pack(fill='x')
                text,color=self.app._attribute_status
                if not text.startswith(('1TPA','2TPA','3TPA','4TPA','TPA','속성부족')) and not isinstance(getattr(self.app,'_route_result_notice',None),dict):text='TPA · 경로 계산 대기';color='#f8d67b'
                self.attr_state_var.set(text)
                self.attr_state_label.configure(fg=color,height=0,wraplength=220)
                self._fit_attribute_indicator()
                return
            if strip is not None:
                if mode in (2,9):strip.grid()
                else:strip.grid_remove()
            if mode not in (2,9) and not isinstance(getattr(self.app,"_route_result_notice",None),dict):
                self.attr_state_label.pack_forget()
                self._fit_attribute_indicator()
                return
            if not self.attr_state_label.winfo_manager():
                self.attr_state_label.pack(fill="x")
            text, color = self.app._attribute_status
            if strip is not None and mode in (2,9):
                first=text.splitlines()[0] if text else '5속성: 확인 대기'
                five=first if first.startswith('5속성') else '5속성: 확인 대기'
                if first.startswith('5속성:'):
                    self.five_state_label.pack(fill='x',before=self.attribute_counts_frame)
                else:self.five_state_label.pack_forget()
                five_color=('#ffb2a9' if any(word in five for word in ('부족','미확보'))
                            else '#84edbc' if any(word in five for word in ('경로 확인','수량 충족')) else '#f8d67b')
                if self.five_state_label.cget('text')!=five or self.five_state_label.cget('fg')!=five_color:
                    self.five_state_label.configure(text=five,fg=five_color)
                board=getattr(self.app,'_attribute_probe_candidate',None)
                if getattr(self.app,'_live_board_ready',False):self.refresh_attribute_counts(board)
                if first.startswith('5속성') and not isinstance(getattr(self.app,'_route_result_notice',None),dict):
                    text=(next((line for line in text.splitlines() if line.startswith(('6콤보:','6콤보·회복:'))),'')
                          if mode==9 else '' if '수량 부족' in first else first.partition(' · ')[2])
                    color=('#84edbc' if '경로 확인' in text else '#ffb2a9' if any(word in text for word in ('부족','미확보')) else '#f8d67b')
            if self.attr_state_var.get()!=text:self.attr_state_var.set(text)
            self.attr_state_label.configure(fg=color, height=0,
                wraplength=220)
            self._fit_attribute_indicator()
        except (tk.TclError, ValueError):
            pass

    def _path_hud_width(self):
        return 260

    def refresh_attribute_counts(self, board):
        if not isinstance(board,(tuple,list)) or len(board)!=30:return
        label=getattr(self,'attribute_shortage_label',None)
        if label is None:return
        missing=[f'{COLOR_INFO[code][0]} {count}/3'
                 for code,count in five_attribute_inventory(board).items() if count<3]
        text='5속성 부족: ' + ' · '.join(missing) if missing else '5속성 수량 충족'
        color='#ffb2a9' if missing else '#84edbc'
        key=(text,color)
        if key==getattr(self,'_attribute_counts_key',None):return
        self._attribute_counts_key=key
        label.configure(text=text,fg=color)

    def _fit_attribute_indicator(self):
        if getattr(self.app,'toolbar',None) is not self or self._drag_origin is not None:return
        self.win.update_idletasks()
        width,height=self._path_hud_width(),max(84,self.win.winfo_reqheight())
        info=find_emulator_window_info();sw,sh=self.win.winfo_screenwidth(),self.win.winfo_screenheight()
        game=info['rect'] if info else (0,0,sw,sh)
        x,y,width,height=path_hud_geometry(game,getattr(self.app,'board_rect',None),
            (self.win.winfo_x(),self.win.winfo_y()),width,height,(sw,sh))
        if (self.win.winfo_x(),self.win.winfo_y(),self.win.winfo_width(),self.win.winfo_height())!=(x,y,width,height):
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
                self._path_hud_width(),max(84,self.win.winfo_reqheight()),(self.win.winfo_screenwidth(),self.win.winfo_screenheight()))
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
        self.pin_button = None
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
            selectmode="extended", style="CrystalCandidate.Treeview")
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
        actions = tk.Frame(outer, bg="#f1f4f7")
        actions.pack(fill="x")
        tk.Button(actions, text="DB 관리", command=self.app.show_crystal_ledger,
                  bg="#50697c", fg="white", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), padx=8, pady=6).pack(side="left", fill="x", expand=True, padx=(0,4))
        self.farming_button = tk.Button(actions, text="자동파밍", command=self.start_selected_crystal_farming,
                  bg="#287a58", fg="white", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        self.farming_button.pack(side="left", fill="x", expand=True, padx=(4,0))
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
        if self.pin_button is not None:
            self.pin_button.configure(text=('📌 관리창 고정: 켬' if pinned else '📌 관리창 고정: 꺼짐'),
                                      bg=('#177c71' if pinned else '#50697c'))
        self.crystal_window.attributes('-topmost',pinned or getattr(self,'_crystal_presentation_active',False))
        self.breakthrough_label.configure(text=('모든 재료 충족' if not pending and self.app.all_crystal_requirements_met() else ''))
        notice=getattr(self,'_farming_start_notice',None)
        self.read_stamp_label.configure(text=notice[0] if notice and time.monotonic()<notice[1] else '파밍할 재료를 선택한 뒤 자동파밍을 누르세요. Ctrl/Shift로 여러 재료 선택 · 위쪽 재료부터 획득 던전 확인 · F10 중지')
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
            old_ids = getattr(self, '_candidate_row_ids', {})
            old_selection = {old_ids.get(iid) for iid in tree.selection()}
            ledger = self.app._load_crystal_ledger()
            key = ledger.get('active_farming_group')
            group = ledger.get('farming_groups', {}).get(key, {})
            self._candidate_group_snapshot = (key, json.loads(json.dumps(group)))
            self._candidate_row_ids = {}
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
                self._candidate_row_ids[iid] = cid
                required = int(candidate.get("required", 0))
                current = int(candidate.get("current", 0))
                deficit = max(0, int(candidate.get("deficit", required-current)))
                insert_kwargs = {'image': photo} if photo is not None else {}
                tree.insert("", "end", iid=iid, text=label,
                            values=(required, current, deficit), **insert_kwargs)
            selected_rows = [iid for iid,cid in self._candidate_row_ids.items() if cid in old_selection]
            if selected_rows: tree.selection_set(selected_rows)
            tree.configure(height=max(1,min(5,len(candidates))))
            if not candidates:
                tree.insert("", "end", iid="empty", text="미등록", values=("?", "?", "?"))
        except tk.TclError:
            pass

    def start_selected_crystal_farming(self):
        def feedback(text):
            self._farming_start_notice=(str(text),time.monotonic()+8)
            self.app.set_status(str(text))
            label=getattr(self,'read_stamp_label',None)
            if label is not None:
                try:label.configure(text=str(text))
                except tk.TclError:pass
        if getattr(self,'_farming_start_pending',False):
            feedback('이전 보상 판독이 끝나면 선택한 재료의 자동파밍을 시작합니다. F10으로 취소할 수 있습니다.');return
        if (getattr(self.app, '_background_running', False) or getattr(self.app, '_material_catalog_busy', False)
                or any(getattr(self.app, flag, False) for flag in ('busy','playback_active','ocr_busy','_manual_reward_busy'))):
            feedback('현재 작업이 끝난 뒤 자동파밍을 눌러 주세요.');return
        try:
            selected = {self._candidate_row_ids[iid] for iid in self.candidate_tree.selection()
                        if iid in self._candidate_row_ids}
            if not selected: raise ValueError('파밍할 재료를 하나 이상 선택해 주세요.')
            saved=getattr(self,'_candidate_group_snapshot',None)
            if not saved or len(saved)!=2:raise ValueError('재료확인/F7로 돌파 대상과 재료를 먼저 읽어 주세요.')
            key,snapshot=saved
            with self.app._crystal_db_lock:
                ledger = self.app._load_crystal_ledger()
                group = ledger.get('farming_groups', {}).get(key)
                identity=lambda row:(row.get('name'),row.get('revision'),[(str(item['id']),item['required']) for item in row.get('materials',[])])
                if ledger.get('active_farming_group') != key or not group or identity(group)!=identity(snapshot):
                    raise ValueError('재료 목록이 바뀌었습니다. 재료를 다시 선택해 주세요.')
                requirements = {row['id']: row['required'] for row in group['materials']}
                updated, chosen = save_farming_selection(ledger, key, requirements, selected)
                if background_goal_complete(updated, key, material_ids=chosen):
                    raise ValueError('선택한 재료가 모두 충족되었습니다.')
                _atomic_json_write(self.app.data_paths['ledger'], updated)
            epoch=getattr(self.app,'_stop_epoch',0);deadline=time.monotonic()+3
            self._farming_start_pending=True
            def begin():
                if epoch!=getattr(self.app,'_stop_epoch',0):
                    self._farming_start_pending=False;feedback('F10 중지로 자동파밍 시작을 취소했습니다.');return
                if getattr(self.app,'reward_poll_busy',False) is True:
                    if time.monotonic()<deadline:
                        feedback('보상 판독 마무리 대기 중 · 끝나면 자동파밍 시작 · F10 취소')
                        self.app.root.after(100,begin);return
                    self._farming_start_pending=False;feedback('보상 판독이 아직 진행 중입니다. 잠시 후 자동파밍을 다시 눌러 주세요.');return
                self._farming_start_pending=False
                feedback('선택한 재료의 자동파밍을 준비합니다…')
                try:self.app.start_background_farming(confirmed=True,material_ids=chosen,confirmed_key=key)
                except Exception as error:feedback('자동파밍 시작 실패: '+str(error));return
                if getattr(self.app,'_background_running',False) is True:feedback('자동파밍 시작 · F10 중지')
                else:
                    variable=getattr(self.app,'status_var',None)
                    message=variable.get() if variable is not None else None
                    if isinstance(message,str) and message:feedback(message)
            begin()
        except (ValueError, RuntimeError, OSError) as error:
            self._farming_start_pending=False;feedback(str(error))

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
            if self.win.state() == 'withdrawn':
                self.win.deiconify()
                self._position_path_button_beside_strip()
                self.win.attributes("-topmost", True)
                self.win.lift()
        except tk.TclError:
            pass


def farming_confirmation_position(anchor,size,bounds):
    ax,ay,aw,ah=anchor;w,h=size;vx,vy,vw,vh=bounds
    x=ax-w-12 if ax-w-12>=vx else ax+aw+12
    return (round(max(vx,min(x,vx+vw-w))),round(max(vy,min(ay,vy+vh-h))))


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
        path=self.path.get();self.probe=self.app._ui_backend_services().probe_factory(path,capture_settings=dict(self.app.user_settings))
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
            self.app.user_settings.update(background_adb_path=self.app._ui_backend_services().portable_adb_path(key[0]),background_adb_serial=key[1])
            self.app.save_user_settings()
            saved=_load_json_dict(self.app.data_paths['settings'])
            if any(saved.get(k)!=v for k,v in zip(('background_adb_path','background_adb_serial'),(self.app._ui_backend_services().portable_adb_path(key[0]),key[1]))):
                self.tell('설정 파일 저장을 확인하지 못했습니다. 저장 폴더 권한을 확인해주세요.');return
            if self.on_saved:self.on_saved(self.app._ui_backend_services().portable_adb_path(key[0]),key[1])
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


class AppUIMixin:
    """Presentation methods; the controller supplies state and backend callbacks."""

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
        ttk.Button(outer,text='스킬 택틱 · 상태별 우선순위',command=self.show_skill_tactics).pack(fill='x',pady=3)
        ttk.Button(outer,text='파밍 설정 · 목표 재료 선택',command=self.show_farming_settings).pack(fill='x',pady=3)
        ttk.Button(farm_row,text='파밍 연결 설정',command=self.background_settings).pack(side='left',padx=(6,0))
        ttk.Button(outer,text='매칭 속도 조절 (±0.5ms)',command=self.show_maa_timing_settings).pack(fill='x',pady=3)
        attribute_host = ttk.Frame(outer)
        attribute_host.pack(fill="x")
        self.attribute_status_frame = ttk.Frame(attribute_host)
        self.attribute_status_var = tk.StringVar(value=self._attribute_status[0])
        self.attribute_status_label = ttk.Label(self.attribute_status_frame, textvariable=self.attribute_status_var,
                                               wraplength=535, justify="left")
        self.attribute_status_label.pack(anchor="w", fill="x", pady=(3, 4))
        ttk.Label(self.attribute_status_frame, text="TPA는 4매치 개수를 우선하고 6콤보는 선택합니다. 수량 충족은 경로 발견을 뜻하지 않습니다.",
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
        ttk.Button(buttons, text="스테이지 / 보상", command=self.show_stage_rewards).pack(side="left", padx=4)
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

    def show_creature_registry(self):
        existing=getattr(self,'_creature_registry_window',None)
        if existing is not None and existing.winfo_exists():
            self._creature_registry_refresh();existing.deiconify();existing.lift();return
        win=tk.Toplevel(self.root);self._creature_registry_window=win
        win.title('등록한 크리처 · 단독 등록 / 각성 확인');win.geometry('920x620');win.minsize(760,460)
        body=ttk.Frame(win,padding=12);body.pack(fill='both',expand=True)
        ttk.Label(body,text='단독 등록과 각성 등록 결과 · 파티 슬롯을 변경하지 않습니다.').pack(anchor='w')
        ttk.Button(body,text='목록 새로고침',command=lambda:refresh()).pack(anchor='w',pady=8)
        panes=ttk.Panedwindow(body,orient='horizontal');panes.pack(fill='both',expand=True)
        left=ttk.Frame(panes);right=ttk.Frame(panes);panes.add(left,weight=1);panes.add(right,weight=3)
        tree=ttk.Treeview(left,columns=('name',),show='tree headings',selectmode='browse');tree.heading('#0',text='번호');tree.heading('name',text='이름');tree.column('#0',width=70);tree.column('name',width=220);tree.pack(fill='both',expand=True)
        details=ScrolledText(right,wrap='word',state='disabled');details.pack(fill='both',expand=True)
        def select(event=None):
            ids=tree.selection()
            if not ids:return
            number=ids[0];saved=self.user_settings.get('monster_detail_observations',{}).get(number,{})
            party=next((row for row in self.user_settings.get('background_party_profiles',[]) if str(row.get('number'))==number),{})
            profile=self._ui_backend_services().profile_merge(dict({**party,**saved},number=int(number)),self.user_settings)
            rows=profile.get('awakening_effects') or []
            text=f"No.{number} · {profile.get('name','이름 미확인')}\n레벨 {profile.get('level') or '?'} · 스킬 턴 {profile.get('cooldown') if profile.get('cooldown') is not None else '?'}\n속성 {profile.get('attributes_ko','미확인')}\n\n스킬 설명\n{saved.get('skill_screen_text') or saved.get('text') or profile.get('text') or '미등록'}\n\n각성\n"
            text+='\n\n'.join(f"{row.get('name','미확인')}\n{row.get('description','')}" for row in rows) or '각성 미등록'
            details.configure(state='normal');details.delete('1.0','end');details.insert('1.0',text);details.configure(state='disabled')
        def refresh():
            selected=tree.selection();tree.delete(*tree.get_children())
            numbers=set(self.user_settings.get('monster_detail_observations',{}))|set(self.user_settings.get('monster_awakening_observations',{}))
            numbers|={str(row.get('number')) for row in self.user_settings.get('background_party_profiles',[]) if row.get('number')}
            for number in sorted((n for n in numbers if str(n).isdigit()),key=int):
                name=self.user_settings.get('monster_korean_names',{}).get(number) or self.user_settings.get('monster_detail_observations',{}).get(number,{}).get('name','이름 미확인')
                tree.insert('','end',iid=number,text=number,values=(name,))
            if tree.get_children():tree.selection_set(selected[0] if selected and selected[0] in tree.get_children() else tree.get_children()[0]);select()
        tree.bind('<<TreeviewSelect>>',select);self._creature_registry_refresh=refresh;refresh()

    def show_skill_tactics(self):
        existing=getattr(self,'_skill_tactics_window',None)
        if existing is not None and existing.win.winfo_exists():
            existing.win.deiconify();existing.win.lift();return
        self._skill_tactics_window=SkillTacticsWindow(self)

    def show_party_settings(self,automatic=True):
        services = self._ui_backend_services()
        action_epoch=getattr(self,'_stop_epoch',0)
        if automatic:services.resume_actions(self)
        existing=getattr(self,'_party_settings_window',None)
        if existing is not None and existing.winfo_exists():
            self._party_bring_forward(existing)
            if automatic:existing.after_idle(lambda:services.action_current(self,action_epoch) and existing.winfo_exists() and self._party_scan_start(automatic=True))
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
        ttk.Button(top,text='스킬 택틱',command=self.show_skill_tactics).pack(side='left',padx=8)
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
        choices=['확인 필요']+sorted(set(services.awakening_labels)|set(self.user_settings.get('shared_awakening_descriptions',{})))
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
        effects={key:tk.BooleanVar() for key in services.skill_labels}
        grid=ttk.LabelFrame(auto_body,text='액티브 스킬 효과 · 인식이 틀리면 수정');grid.pack(fill='x',pady=8)
        def update_active_summary(*_):
            active_summary.set(' · '.join(label for key,label in services.skill_labels.items() if effects[key].get()) or '효과 확인 필요')
        for i,(key,label) in enumerate(services.skill_labels.items()):
            ttk.Checkbutton(grid,text=label,variable=effects[key]).grid(row=i//4,column=i%4,sticky='w',padx=10,pady=3)
            effects[key].trace_add('write',update_active_summary)
        policies={'auto':'상황에 따라 자동','locked_only':'5속성 부족·잠금 존재 시','shortage_only':'속성 부족일 때만','time_only':'조작시간 감소 시에만','cleanse_only':'바인드 / 각성 무효 시에만'}
        policy=tk.StringVar();policyrow=ttk.Frame(auto_body);policyrow.pack(fill='x')
        ttk.Label(policyrow,text='사용 조건: ').pack(side='left');ttk.Combobox(policyrow,textvariable=policy,values=list(policies.values()),state='readonly',width=30).pack(side='left')
        opts=ttk.Frame(footer);opts.pack(fill='x',pady=(6,0))
        ttk.Label(opts,text='5속성 부족 시 잠금 해제 → 5속성 생성 → 재탐색').pack(anchor='w')
        notice=tk.BooleanVar(value=self.user_settings.get('background_complete_notify',True))
        ttk.Checkbutton(opts,text='파밍 완료 바탕화면 알림',variable=notice).pack(side='left')
        sound=tk.BooleanVar(value=self.user_settings.get('background_complete_sound',True))
        ttk.Checkbutton(opts,text='작은 완료음',variable=sound).pack(side='left',padx=(8,0))
        def save_common():
            self.user_settings.update(background_complete_notify=notice.get(),background_complete_sound=sound.get());self.save_user_settings();status.set('완료 알림 설정을 저장했습니다.')
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
            profile=services.profile_merge(current(),self.user_settings)
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
            checks=[];rows=services.awakening_effects(profile,self.user_settings)
            icons=profile.get('awakening_icons_png',[])
            def persist(indices,confirmed):
                if getattr(self,'_background_running',False):notice.set('등록 중입니다. 읽기가 끝난 뒤 확인해 주세요.');return False
                try:
                    updated=services.confirm_awakenings(self.user_settings,slot,indices,confirmed)
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
                        updated.append(dict(original,name=title,description=text,verified=bool(original.get('verified')) if same else False,known=title in services.awakening_labels,source=original.get('source','') if same else '사용자 정정'))
                    descriptions={}
                    for item in updated:
                        if item['name'] in descriptions and descriptions[item['name']]!=item['description']:
                            hint.set('같은 각성의 설명은 동일하게 입력해 주세요.');return
                        descriptions[item['name']]=item['description']
                    for item in updated:
                        previous=dict(self.user_settings.get('shared_awakening_descriptions',{}).get(item['name'],{}))
                        services.share_awakening_description(self.user_settings,item['name'],item['description'],item['verified'])
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
                    services.record_party_review(self.user_settings,profiles[slot]);self.save_user_settings();refresh()
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
            p=services.profile_merge(current(),self.user_settings)
            child=tk.Toplevel(win);child.title('출처·일본어 원문');child.geometry('760x360')
            text=ScrolledText(child,wrap='word');text.pack(fill='both',expand=True)
            text.insert('1.0','출처: '+p.get('source','미확인')+'\n\n일본어 이름: '+p.get('name_ja','미확인')+'\n\n스킬: '+p.get('skill_ja','미확인')+'\n\n각성: '+p.get('awakenings_ja','미확인'));text.configure(state='disabled')
        def apply_game_awakening(i):
            name=awake_vars[i].get();candidate=self.user_settings.get('awakening_description_candidates',{}).get(name)
            if not candidate:status.set('게임에서 각성 스킬 상세를 먼저 열어주세요.');return
            services.share_awakening_description(self.user_settings,name,candidate['description'],False)
            self.user_settings['shared_awakening_descriptions'][name].update(source='게임 각성 상세 OCR',icon_png=candidate.get('icon_png',''),description_png=candidate.get('description_png',''))
            awake_desc[i].delete('1.0','end');awake_desc[i].insert('1.0',candidate['description']);awake_known[i].set(False);awake_notices[i].set('게임 설명 · 확인 필요')
            self.save_user_settings();status.set('읽은 게임 설명을 적용했습니다. 내용 확인 후 체크하면 공유 확인으로 저장합니다.')
        def confirm_awakening(i):
            if services.share_awakening_description(self.user_settings,awake_vars[i].get(),awake_desc[i].get('1.0','end').strip(),awake_known[i].get()):
                self.save_user_settings();awake_notices[i].set('공유 확인 완료' if awake_known[i].get() else '확인 필요')
                status.set('같은 각성의 설명과 확인 상태를 공유했습니다. 설명 검토 데이터도 보존합니다.')
            else:status.set('각성 이름과 설명을 먼저 선택해 주세요.')
        def update_awakening(i):
            name=awake_vars[i].get();shared=self.user_settings.get('shared_awakening_descriptions',{}).get(name,{})
            description=shared.get('description') or services.awakening_labels.get(name,'효과 확인 필요')
            awake_desc[i].delete('1.0','end');awake_desc[i].insert('1.0',description);awake_known[i].set(bool(shared.get('verified')))
            awake_notices[i].set('공유 확인 완료' if shared.get('verified') else '확인 필요' if name=='확인 필요' or '확인' in description else 'DB 효과 · 아이콘 확인 필요')
            names=[var.get() for var in awake_vars if var.get() not in ('확인 필요','없음')]
            awakening.delete('1.0','end');awakening.insert('1.0',' / '.join(names) or '없음')
        def populate(p):
            for key,var in fields.items():var.set(str(p.get(key,'') or ''))
            skill.delete('1.0','end');skill.insert('1.0',p.get('text',''))
            if not photo(skill_image,p.get('skill_panel_png'),(940,240)):skill_image.configure(text='스킬 이미지 확인 필요 · 파티를 다시 읽어주세요.')
            awakening.delete('1.0','end');awakening.insert('1.0',p.get('awakenings',''))
            values=services.awakening_effects(p,self.user_settings);icons=p.get('awakening_icons_png',[])
            for i in range(3):
                item=values[i] if i<len(values) else dict(name='없음',description=services.awakening_labels['없음'],known=True)
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
            p=services.profile_merge(current(),self.user_settings);data=self.user_settings.get('background_party_profiles',[])
            if len(data)>selected.get() and p.get('number') and data[selected.get()]!=p:data[selected.get()]=p;self.save_user_settings()
            populate(p)
        def refresh():
            data=self.user_settings.get('background_party_profiles',[])
            for i,var in enumerate(name_vars):
                p=services.profile_merge(data[i],self.user_settings) if i<len(data) else {}
                if not photo(portrait_labels[i],p.get('portrait_png'),(104,178)):portrait_labels[i].configure(text='이미지 확인 필요',wraplength=94)
                attribute_vars[i].set((p.get('attributes_ko') or '속성 확인 필요')+'\n'+('✓ 각성 등록 완료' if p.get('awakening_registered') and all(v.get('verified') for v in services.awakening_effects(p,self.user_settings)) else '□ 각성 등록 필요'))
                var.set(f'{i+1}. '+p.get('name','미확인'));level_vars[i].set(f'Lv{p.get("level") or "?"} · '+('확인 완료' if p.get('verified') else '읽기 완료' if p.get('number') else '미등록'))
                if current_card_has_footer(i):awake_frames[i].pack_forget()
                else:awake_frames[i].pack(anchor='center',pady=(4,0))
                icons=p.get('awakening_icons_png',[])
                for j,label in enumerate(awake_labels[i]):
                    if not photo(label,icons[j] if j<len(icons) else '',(30,30)):label.configure(text='?')
            fit_names();load()
        def db_fill():
            p=services.profile_merge(dict(slot=selected.get(),number=fields['number'].get().strip()),self.user_settings)
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
                p['awakening_effects']=[dict(name=v.get(),description=awake_desc[i].get('1.0','end').strip(),known=v.get() in services.awakening_labels or awake_known[i].get(),verified=awake_known[i].get(),source='사용자 정정') for i,v in enumerate(awake_vars) if v.get()!='없음']
                for item in p['awakening_effects']:
                    if item.get('verified'):services.share_awakening_description(self.user_settings,item['name'],item['description'])
                services.record_party_review(self.user_settings,p)
                override={k:p[k] for k in ('name','text','awakenings','awakening_effects','attributes_ko','attributes_source','effects','auto_use','priority','verified','cooldown','use_policy')}
                self.user_settings.setdefault('monster_overrides',{})[number]=override
                data=self.user_settings.setdefault('background_party_profiles',[])
                while len(data)<6:data.append(dict(slot=len(data)))
                data[selected.get()]=p;self.user_settings.update(background_complete_notify=notice.get(),background_complete_sound=sound.get());self.save_user_settings();refresh();status.set('정정 내용을 저장했습니다. 이미지 원본과 함께 유지합니다.')
            except ValueError as exc:status.set(str(exc))
        ttk.Button(actions,text='번호로 DB 정보 불러오기',command=db_fill).pack(side='left')
        ttk.Button(actions,text='설명에서 효과 다시 읽기',command=lambda:[v.set(k in services.skill_details(skill.get('1.0','end'))['effects']) for k,v in effects.items()]).pack(side='left',padx=8)
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
        if automatic:win.after_idle(lambda:services.action_current(self,action_epoch) and win.winfo_exists() and self._party_scan_start(automatic=True))

    def _party_bring_forward(self,window):
        window.deiconify();window.attributes('-topmost',True);window.lift()
        jobs=(window.after_idle(lambda:window.winfo_exists() and window.focus_force()),
              window.after(800,lambda:window.winfo_exists() and window.attributes('-topmost',False)))
        def cleanup(event):
            if event.widget is not window:return
            for job in jobs:
                try:window.after_cancel(job)
                except tk.TclError:pass
        window.bind('<Destroy>',cleanup,add='+')

    def show_background_path_help(self,path='',serial=''):
        services = self._ui_backend_services()
        parent=getattr(self,'_background_settings_window',None)
        if parent is None or not parent.winfo_exists():parent=self.root
        win=tk.Toplevel(parent)
        win.title('경로 설정 방법 · MuMu ADB 연결');win.geometry('760x650')
        body=ttk.Frame(win,padding=16);body.pack(fill='both',expand=True)
        ttk.Label(body,text='경로 설정 방법',font=('Segoe UI',15,'bold')).pack(anchor='w',pady=(0,8))
        ttk.Label(body,text='ADB 파일 선택 → 기기 주소 확인 → 저장 → 파티 읽기로 확인').pack(anchor='w',pady=(0,10))
        text=ScrolledText(body,wrap='word',font=('Segoe UI',10),padx=10,pady=10)
        text.pack(fill='both',expand=True);text.insert('1.0',services.connection_help(path,serial));text.configure(state='disabled')
        buttons=ttk.Frame(body);buttons.pack(fill='x',pady=(10,0))
        def copy():
            win.clipboard_clear();win.clipboard_append(services.connection_help(path,serial))
        ttk.Button(buttons,text='그림으로 보는 ADB·포트 설정',command=self.open_community_adb_help).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='설명 복사',command=copy).pack(side='left')
        ttk.Button(buttons,text='닫기',command=win.destroy).pack(side='right')
        self._party_bring_forward(win)

    def show_background_setup_wizard(self,path='',serial='',on_saved=None):
        if getattr(self,'_speed_test_running',False):self.set_status('속도 테스트 입력 정리가 끝난 뒤 연결 설정을 확인하세요.');return
        if getattr(self,'_background_running',False):self.set_status('F10으로 파밍을 중지한 뒤 설정 마법사를 열어주세요.');return
        current=getattr(self,'_background_setup_wizard',None)
        if current is not None and not current.closed and current.win.winfo_exists():
            if on_saved is not None:current.on_saved=on_saved
            self._party_bring_forward(current.win);return current
        wizard=BackgroundSetupWizard(self,on_saved,path or self.user_settings.get('background_adb_path',''),serial or self.user_settings.get('background_adb_serial',''))
        self._background_setup_wizard=wizard
        return wizard

    def show_maa_timing_settings(self):
        backend='mumu_sdk'
        label='MuMu SDK'
        sample_key='background_sdk_sample_ms';corner_key='background_sdk_corner_ms'
        sample_default,corner_default=18.75,12.5
        existing=getattr(self,'_maa_timing_window',None)
        if existing is not None and existing.winfo_exists():
            if getattr(existing,'_pad0_input_backend',backend)==backend:
                existing.deiconify();self._party_bring_forward(existing);return
            existing.destroy()
        win=tk.Toplevel(self.root);self._maa_timing_window=win;win._pad0_input_backend=backend
        win.title('자동플레이 매칭 속도 조절');win.geometry('560x460');win.resizable(False,False)
        self._party_bring_forward(win)
        footer=ttk.Frame(win,padding=(14,8));footer.pack(side='bottom',fill='x')
        body=ttk.Frame(win,padding=16);body.pack(fill='both',expand=True)
        sample=tk.StringVar(value=str(self.user_settings.get(sample_key,sample_default)))
        corner=tk.StringVar(value=str(self.user_settings.get(corner_key,corner_default)))
        notice=tk.StringVar(value='값이 작을수록 빠릅니다. 0.5ms씩 조절할 수 있습니다.')
        estimate=tk.StringVar()
        ttk.Label(body,text=label+' 매칭 속도',font=('Malgun Gothic',12,'bold')).pack(anchor='w')
        ttk.Label(body,text='실행 중 저장하면 다음 턴부터 적용합니다. 이동 중인 경로의 속도는 유지합니다.',wraplength=515).pack(anchor='w',pady=(6,10))
        row=ttk.Frame(body);row.pack(fill='x')
        ttk.Label(row,text='이동 간격 (10~40ms)').pack(side='left')
        ttk.Spinbox(row,textvariable=sample,from_=10,to=40,increment=.5,width=8).pack(side='left',padx=8)
        ttk.Label(body,textvariable=estimate).pack(anchor='w',pady=5)
        row=ttk.Frame(body);row.pack(fill='x',pady=5)
        def values():
            step=float(sample.get());extra=float(corner.get())
            if not math.isfinite(step) or not 10<=step<=40 or not math.isfinite(extra) or not 0<=extra<=80:
                raise ValueError('이동 간격은 10~40ms, 코너 추가 대기는 0~80ms여야 합니다.')
            return {sample_key:step,corner_key:extra}
        def apply():
            previous={k:(k in self.user_settings,self.user_settings.get(k)) for k in (sample_key,corner_key)}
            try:
                pending=values()
                self.user_settings.update(pending);self.save_user_settings()
                saved=_load_json_dict(self.data_paths['settings'])
                if any(saved.get(k)!=v for k,v in pending.items()):raise OSError('이동 속도 설정을 파일에 저장하지 못했습니다.')
            except (ValueError,TypeError,OSError) as error:
                for k,(existed,value) in previous.items():
                    if existed:self.user_settings[k]=value
                    else:self.user_settings.pop(k,None)
                messagebox.showerror('매칭 속도',str(error),parent=win);return
            # A backend-specific immutable request is consumed at a new turn.
            # This never switches the controller or edits a running gesture.
            self._background_timing_request=pending
            notice.set(label+' 속도를 저장했습니다. '+('다음 턴부터 적용됩니다.' if getattr(self,'_background_running',False) else '다음 자동플레이부터 적용됩니다.'))
        def adjust(delta):
            try:current=values()[sample_key]
            except (ValueError,TypeError) as error:
                messagebox.showerror('매칭 속도',str(error),parent=win);return
            sample.set(f'{min(40,max(10,round(current+delta,3))):g}');apply()
        ttk.Button(row,text='더 빠르게 (-0.5ms)',command=lambda:adjust(-.5)).pack(side='left',padx=(0,8))
        ttk.Button(row,text='더 느리게 (+0.5ms)',command=lambda:adjust(.5)).pack(side='left')
        row=ttk.Frame(body);row.pack(fill='x',pady=8)
        ttk.Label(row,text=f'코너 추가 대기 (기본 {corner_default:g}ms)').pack(side='left')
        ttk.Spinbox(row,textvariable=corner,from_=0,to=80,increment=.5,width=8).pack(side='left',padx=8)
        def reset():sample.set(f'{sample_default:g}');corner.set(f'{corner_default:g}');apply()
        ttk.Label(body,textvariable=notice,wraplength=515).pack(anchor='w',pady=5)
        def update_estimate(*_):
            try:estimate.set(f'한 칸 기본 이동 약 {4*float(sample.get()):g}ms · 실제 처리 시간은 별도')
            except (ValueError,TypeError):estimate.set('이동 간격을 숫자로 입력하세요.')
        sample.trace_add('write',update_estimate);update_estimate()
        ttk.Button(footer,text=label+' 기본값 복원',command=reset).pack(side='left')
        ttk.Button(footer,text='저장 · 적용',command=apply).pack(side='right')
        ttk.Button(footer,text='닫기',command=win.destroy).pack(side='right',padx=8)

    def background_settings(self):
        services = self._ui_backend_services()
        def connection_busy():
            # _manual_play_active means passive manual mode after F10/failure,
            # not an in-flight drag. Keep the actual worker/playback guards.
            return bool(getattr(self,'_background_running',False) or getattr(self,'playback_active',False))
        if connection_busy():self.set_status('F10으로 파밍·경로 실행을 중지한 뒤 연결 설정을 열 수 있습니다.');return
        existing=getattr(self,'_background_settings_window',None)
        try:
            if existing is not None and existing.winfo_exists():
                self._party_bring_forward(existing);return
        except tk.TclError:pass
        self._background_settings_window=None
        from tkinter import filedialog
        window=tk.Toplevel(self.root);self._background_settings_window=window
        def close():
            if getattr(self,'_background_settings_window',None) is window:self._background_settings_window=None
            window.destroy()
        window.protocol('WM_DELETE_WINDOW',close)
        window.title('백그라운드 파밍 연결 설정');window.geometry(f'800x{min(880,window.winfo_screenheight()-70)}')
        self._party_bring_forward(window)
        footer=ttk.Frame(window,padding=(16,8));footer.pack(side='bottom',fill='x')
        area=ttk.Frame(window);area.pack(fill='both',expand=True)
        canvas=tk.Canvas(area,highlightthickness=0);scroll=ttk.Scrollbar(area,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        body=ttk.Frame(canvas,padding=16);item=canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda _e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        def wheel(event):canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
        window.bind('<MouseWheel>',wheel)
        ttk.Label(body,text='MuMu 고속 연결로 화면과 입력을 처리합니다. 처음 연결할 때 연결 도구를 설치할 수 있습니다.',wraplength=595).pack(anchor='w')
        path=tk.StringVar(value=self.user_settings.get('background_adb_path',''))
        serial=tk.StringVar(value=self.user_settings.get('background_adb_serial',''))
        skills=tk.BooleanVar(value=self.user_settings.get('background_auto_skills',True))
        ttk.Label(body,text='조작: MuMu SDK · 스킬 클릭: 기존 컨트롤러',font=('Malgun Gothic',10,'bold')).pack(anchor='w',pady=8)
        mumu_root=tk.StringVar(value=self.user_settings.get('background_mumu_root',''))
        mumu_index=tk.StringVar(value=self.user_settings.get('background_mumu_index',''))
        sdk_sample=tk.StringVar(value=str(self.user_settings.get('background_sdk_sample_ms',18.75)))
        sdk_corner=tk.StringVar(value=str(self.user_settings.get('background_sdk_corner_ms',12.5)))
        for label,sample_var,corner_var in (('MuMu SDK',sdk_sample,sdk_corner),):
            timing_row=ttk.Frame(body);timing_row.pack(fill='x',pady=5)
            ttk.Label(timing_row,text=label+' 이동 간격(ms)').pack(side='left')
            ttk.Spinbox(timing_row,textvariable=sample_var,from_=10,to=40,increment=.5,width=7).pack(side='left',padx=6)
            ttk.Label(timing_row,text='코너 추가 대기(ms)').pack(side='left')
            ttk.Spinbox(timing_row,textvariable=corner_var,from_=0,to=80,increment=.5,width=7).pack(side='left',padx=6)
        ttk.Label(body,text='이동 간격 10~40ms · 코너 추가 대기 0~80ms · 매칭 속도는 다음 자동플레이부터 적용됩니다.',foreground='#526776').pack(anchor='w')
        timing_keys=('background_sdk_sample_ms','background_sdk_corner_ms')
        def capture_values(probe_input=False):
            if connection_busy():raise ValueError('F10으로 파밍·경로 실행을 중지한 뒤 연결 설정을 저장하거나 확인해 주세요.')
            values=dict(self.user_settings,background_skill_input_backend='controller',
                        background_sdk_sample_ms=float(sdk_sample.get()),background_sdk_corner_ms=float(sdk_corner.get()),
                        background_adb_path=services.portable_adb_path(path.get()) if path.get().strip() else '',background_adb_serial=serial.get().strip(),
                        background_connection_profile='fast',background_mumu_enhanced=True,background_mumu_root=mumu_root.get().strip(),background_mumu_index=mumu_index.get().strip(),
                        background_capture_preference='auto',background_input_preference='auto',background_touch_mode='scrcpy',
                        background_capture_mode='mumu_enhanced',background_adb_raw_capture=True,background_input_backend='mumu_sdk')
            for label,sample_key,corner_key in (('MuMu SDK',timing_keys[0],timing_keys[1]),):
                sample=values[sample_key];extra=values[corner_key]
                if not math.isfinite(sample) or not 10<=sample<=40 or not math.isfinite(extra) or not 0<=extra<=80:
                    raise ValueError(label+' 이동 간격은 10~40ms, 코너 추가 대기는 0~80ms여야 합니다.')
            values.pop('background_probe_input',None)
            if probe_input:values['background_probe_input']=True
            return values
        def apply_wizard(found_path,found_serial):
            if window.winfo_exists():path.set(found_path);serial.set(found_serial)
        wizard_row=ttk.Frame(body);wizard_row.pack(fill='x',pady=(12,0))
        ttk.Button(wizard_row,text='설정 마법사 시작',command=lambda:self.show_background_setup_wizard(path.get(),serial.get(),apply_wizard)).pack(side='left')
        ttk.Label(wizard_row,text='처음이라면 단계별로 연결을 확인하세요.').pack(side='left',padx=10)
        official_row=ttk.Frame(body);official_row.pack(fill='x',pady=(10,0))
        def apply_official(found_path,found_root):
            if window.winfo_exists():
                path.set(found_path)
                if found_root:mumu_root.set(found_root)
        ttk.Button(official_row,text='Google 공식 ADB로 강제 교체',command=lambda:self.install_google_adb_ui(path.get(),apply_official,window)).pack(side='left')
        ttk.Button(official_row,text='이전 ADB로 되돌리기',command=lambda:self.restore_previous_adb(apply_official,window)).pack(side='left',padx=8)
        ttk.Label(body,text=r'설치 위치: 프로그램 폴더\platform-tools\adb.exe · 업데이트 후 유지',foreground='#526776').pack(anchor='w',pady=(4,0))

        help_row=ttk.Frame(body);help_row.pack(fill='x',pady=(12,0))
        ttk.Button(help_row,text='경로 설정 방법',command=lambda:self.show_background_path_help(path.get(),serial.get())).pack(side='left')
        ttk.Button(help_row,text='MAA 연결 문서',command=lambda:__import__('webbrowser').open('https://docs.maa.plus/ko-kr/manual/connection.html')).pack(side='left')
        ttk.Label(body,text='ADB 파일 (비우면 자동 탐색)').pack(anchor='w',pady=(12,0))
        row=ttk.Frame(body);row.pack(fill='x');ttk.Entry(row,textvariable=path).pack(side='left',fill='x',expand=True)
        def browse():
            selected=filedialog.askopenfilename(parent=window,title='MuMu의 adb.exe 선택',filetypes=[('ADB','*.exe'),('모든 파일','*')])
            if selected:path.set(selected)
        ttk.Button(row,text='찾기',command=browse).pack(side='left')
        ttk.Label(body,text='기기 주소 (비우면 한 대만 연결됐을 때 자동 선택)').pack(anchor='w',pady=(8,0))
        ttk.Entry(body,textvariable=serial).pack(fill='x')
        capture_box=ttk.LabelFrame(body,text='MuMu 고속 연결',padding=10);capture_box.pack(fill='x',pady=(12,4))
        ttk.Label(capture_box,text='선택한 기기의 화면을 확인한 뒤 고속 캡처와 입력을 준비합니다. 드롭을 누른 뒤에는 다른 입력으로 전환하지 않습니다.',wraplength=690).pack(anchor='w',pady=4)
        ttk.Label(capture_box,text='MuMu 설치 폴더 (비우면 자동 탐색)').pack(anchor='w')
        cap_row=ttk.Frame(capture_box);cap_row.pack(fill='x')
        ttk.Entry(cap_row,textvariable=mumu_root).pack(side='left',fill='x',expand=True)
        def browse_mumu():
            selected=filedialog.askdirectory(parent=window,title='MuMu 설치 폴더 선택')
            if selected:mumu_root.set(selected)
        ttk.Button(cap_row,text='찾기',command=browse_mumu).pack(side='left')
        index_row=ttk.Frame(capture_box);index_row.pack(fill='x',pady=4)
        ttk.Label(index_row,text='MuMu 기기 번호 # (표준 포트면 자동)').pack(side='left')
        ttk.Entry(index_row,textvariable=mumu_index,width=8).pack(side='left',padx=8)
        def preview_connection():
            try:values=capture_values(False)
            except (ValueError,TypeError) as error:
                messagebox.showerror('입력 방식·이동 설정',str(error),parent=window);return
            self.show_background_capture_test(values,capture_box)
        ttk.Button(capture_box,text='연결 화면 확인 (입력 없음)',command=preview_connection).pack(anchor='w',pady=(4,0))
        ttk.Checkbutton(body,text='상세에서 확인한 스킬 자동 사용',variable=skills).pack(anchor='w',pady=8)
        ttk.Label(body,text='MuMu를 종료하거나 PC를 절전하면 멈춥니다. F10은 파밍 중지입니다.',wraplength=590).pack(anchor='w')
        def save():
            try:values=capture_values()
            except (ValueError,TypeError) as error:
                messagebox.showerror('입력 방식·이동 설정',str(error),parent=window);return
            self.user_settings.pop('background_probe_input',None)
            self.user_settings.update(values,background_auto_skills=skills.get())
            self.save_user_settings()
            self._background_timing_request={key:values[key] for key in timing_keys}
            close()
        ttk.Button(footer,text='저장',command=save).pack(anchor='e')

    def show_farming_confirmation(self,settings_only=False,target_key=None):
        local_after_stop=settings_only and getattr(self,'_all_actions_stopped',False) is True
        if getattr(self,'_material_catalog_busy',False) and not local_after_stop:
            self.set_status('크리스탈 이름 등록 완료 후 파밍 설정을 열어주세요. F10으로 중지할 수 있습니다.');return
        existing=getattr(self,'_farming_confirm_window',None)
        try:
            if existing is not None and existing.winfo_exists():
                self._party_bring_forward(existing);return
        except tk.TclError:self._farming_confirm_window=None
        try:
            ledger=self._load_crystal_ledger();key=target_key or background_target_key(ledger)
            group=ledger.get('farming_groups',{}).get(key)
            if not group or not group.get('materials'):
                self.set_status('게임의 생성·돌파 확인창에서 재료확인/F7 → 크리스탈 이미지·미등록 이름 자동 등록 → 파밍 설정 순서로 진행하세요.')
                self._show_material_scan_guide();return
            rows=farming_group_rows(ledger,key);db=self._load_crystal_db()
        except Exception as error:self.set_status(str(error));return
        epoch=getattr(self,'_stop_epoch',0)
        win=tk.Toplevel(self.root);win.withdraw();self._farming_confirm_window=win
        win.title('파밍 설정' if settings_only else '재료 파밍 확인');win.geometry('620x260');win.minsize(560,220)
        style=ttk.Style(win)
        style.configure('FarmConfirm.TFrame',background='#f5f7fa')
        style.configure('FarmConfirm.TLabel',background='#f5f7fa',foreground='#1e293b')
        style.configure('FarmTable.TFrame',background='white')
        style.configure('FarmTable.TLabel',background='white',foreground='#334155')
        style.configure('FarmHeader.TLabel',background='white',foreground='#64748b')
        body=ttk.Frame(win,padding=14,style='FarmConfirm.TFrame');body.pack(fill='both',expand=True)
        ttk.Label(body,style='FarmConfirm.TLabel',text='파밍할 재료를 선택하세요',font=('Malgun Gothic',12,'bold')).pack(anchor='w')
        keys=[k for k,g in ledger.get('farming_groups',{}).items() if g.get('materials')]
        selector=ttk.Combobox(body,state='readonly',values=[ledger['farming_groups'][k]['name'] for k in keys])
        selector.current(keys.index(key));selector.pack(fill='x',pady=(8,6))
        def change_group(_event):
            chosen_key=keys[selector.current()]
            cancel(keep_connection=True);self.show_farming_confirmation(settings_only=settings_only,target_key=chosen_key)
        selector.bind('<<ComboboxSelected>>',change_group)
        ttk.Label(body,style='FarmConfirm.TLabel',text='새 대상은 게임의 생성·돌파 확인창을 열고 [재료확인] 또는 F7을 누르세요. 등록된 이름은 건너뛰고 이름 없는 크리스탈만 등록합니다. 완료 후 원래 재료창으로 돌아와 크리스탈 창을 맨 위에 표시합니다. 선택하지 않으면 보상 집계를 사용하지 않습니다.',wraplength=570).pack(anchor='w',pady=(0,8))
        def rescan_materials():
            cancel();self.request_resource_ocr(manual=False,fallback_on_failure=True)
        ttk.Button(body,text='게임의 현재 재료창 다시 확인 (F7)',command=rescan_materials).pack(anchor='w',pady=(0,8))
        area=ttk.Frame(body,style='FarmTable.TFrame');area.pack(fill='both',expand=True)
        canvas=tk.Canvas(area,highlightthickness=0,bg='white');scroll=ttk.Scrollbar(area,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        listing=ttk.Frame(canvas,style='FarmTable.TFrame',padding=(0,6));item=canvas.create_window((0,0),window=listing,anchor='nw')
        listing.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        win.bind('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120),'units'))
        for event,direction in (('<Button-4>',-1),('<Button-5>',1)):win.bind(event,lambda e,d=direction:canvas.yview_scroll(d,'units'))
        listing.columnconfigure(2,weight=1);checks={};needs={};photos=[]
        saved=configured_farming_selection(ledger)
        saved_ids=set(saved['material_ids']) if saved is not None and saved['key']==key else set()
        entries={str(entry['id']):entry for entry in db.get('crystals',[])}
        listing.columnconfigure(1,minsize=40)
        for col,text in enumerate(('재료','필요','보유','부족'),2):
            ttk.Label(listing,text=text,style='FarmHeader.TLabel',anchor='w' if col==2 else 'e').grid(row=0,column=col,sticky='ew',padx=(0,10),pady=(0,6))
        for index,row in enumerate(rows,1):
            cid=row['id'];checks[cid]=tk.BooleanVar(value=cid in saved_ids);needs[cid]=tk.StringVar(value=str(row['required']))
            ttk.Checkbutton(listing,variable=checks[cid]).grid(row=index,column=0,sticky='w',padx=(2,6))
            entry=entries.get(cid,{})
            stages=[number for number,saved in self._stage_db_load()['stages'].items() if saved.get('preview_verified') and any(str(drop.get('id'))==cid for drop in saved.get('drops',[]))]
            destination='획득: '+', '.join('★'+n for n in sorted(stages)) if stages else '이름 확인 필요 · 재료확인/F7' if not row.get('confirmed',True) else '획득 스테이지 미확인 · 재료확인/F7'
            label=ttk.Label(listing,text=crystal_display_name(entry,row,index)+'\n'+destination,wraplength=235,style='FarmTable.TLabel')
            try:
                from PIL import ImageTk
                path=self.data_paths['icons_dir']/str(entry.get('image_file') or row.get('image_file') or '')
                with Image.open(path) as source:image=source.convert('RGB');image.thumbnail((32,32));photo=ImageTk.PhotoImage(image)
                photos.append(photo);ttk.Label(listing,image=photo,style='FarmTable.TLabel').grid(row=index,column=1,sticky='w',padx=(0,8),pady=5)
            except (OSError,ValueError,KeyError):pass
            label.grid(row=index,column=2,sticky='w',padx=(0,10),pady=5)
            ttk.Entry(listing,textvariable=needs[cid],width=7,justify='right').grid(row=index,column=3,sticky='e',padx=(0,10))
            ttk.Label(listing,text=str(row['owned']),anchor='e',style='FarmTable.TLabel').grid(row=index,column=4,sticky='e',padx=(0,10))
            deficit=tk.StringVar(value=str(row['deficit']));ttk.Label(listing,textvariable=deficit,anchor='e',style='FarmTable.TLabel').grid(row=index,column=5,sticky='e',padx=(0,10))
            def update(*_,var=needs[cid],owned=row['owned'],out=deficit):
                try:out.set(str(max(0,int(var.get())-owned)))
                except ValueError:out.set('—')
            needs[cid].trace_add('write',update)
        win._material_photos=photos
        notice=tk.StringVar();notice_label=ttk.Label(body,style='FarmConfirm.TLabel',textvariable=notice,foreground='#b33b32',wraplength=510);notice_label.pack(side='bottom',anchor='w',pady=(8,0),before=area)
        hide_stats=tk.BooleanVar(value=self.user_settings.get('farming_hide_stats',False))
        ttk.Checkbutton(body,text='보상 집계창 숨기기 (수량은 계속 집계)',variable=hide_stats).pack(side='bottom',anchor='w',before=area)
        controls=ttk.Frame(body,style='FarmConfirm.TFrame');controls.pack(side='bottom',fill='x',pady=(8,0),before=area)
        def cancel(keep_connection=False):
            if not keep_connection and not settings_only:
                discard=getattr(self,'_discard_farming_warmup',None)
                if discard is not None:discard()
            if win.winfo_exists():win.destroy()
            self._farming_confirm_window=None
        def confirm():
            if epoch!=getattr(self,'_stop_epoch',0):cancel();return
            try:
                with self._crystal_db_lock:
                    current=self._load_crystal_ledger()
                    if current.get('farming_groups',{}).get(key)!=group:raise ValueError('재료 목록이 바뀌었습니다. 창을 다시 열어주세요.')
                    updated,chosen=save_farming_selection(current,key,{cid:var.get() for cid,var in needs.items()},[cid for cid,var in checks.items() if var.get()])
                    if not settings_only and not chosen:raise ValueError('파밍할 재료를 하나 이상 선택해 주세요.')
                    _atomic_json_write(self.data_paths['ledger'],updated)
                self.user_settings['farming_hide_stats']=hide_stats.get();self.save_user_settings()
                cancel(keep_connection=not settings_only)
                if settings_only:
                    self._prefind_cancel();self._prefind_paused_key=None;self._prefind_failed_key=None
                    self._prefind_condition_wait=None;self._prefind_probe_next=0.0
                    self._attribute_probe_next=0.0
                    self._farming_displayed_session=None
                    if getattr(self,'_farming_stats',None) is not None:self._farming_stats.withdraw()
                    self.set_status(f'파밍 설정 저장 · {len(chosen)}종 선택' if chosen else '파밍 설정 저장 · 보상 집계 끔')
                else:self.start_background_farming(confirmed=True,material_ids=chosen,confirmed_key=key)
            except (ValueError,OSError) as error:notice.set(str(error))
        ttk.Button(controls,text='취소',command=cancel).pack(side='right')
        ttk.Button(controls,text='저장' if settings_only else '확인 · 파밍 시작',command=confirm,style='Accent.TButton').pack(side='right',padx=(0,8))
        ttk.Button(controls,text='전체 해제',command=lambda:[var.set(False) for var in checks.values()]).pack(side='left')
        ttk.Button(controls,text='부족 재료 선택',command=lambda:[checks[row['id']].set(row['deficit']>0) for row in rows]).pack(side='left',padx=6)
        win.protocol('WM_DELETE_WINDOW',cancel);win.bind('<Escape>',lambda e:cancel())
        # Size from actual font/DPI metrics; a single row should not leave a tall blank list.
        win.update_idletasks()
        table_height=listing.winfo_reqheight()
        reserved=body.winfo_reqheight()-area.winfo_reqheight()
        height=min(max(180,table_height+reserved+4),max(180,min(700,win.winfo_screenheight()-90)))
        anchor=getattr(self,'_background_button_win',None)
        try:
            ax,ay,aw,ah=anchor.winfo_rootx(),anchor.winfo_rooty(),anchor.winfo_width(),anchor.winfo_height()
        except (AttributeError,tk.TclError):ax,ay,aw,ah=win.winfo_pointerx(),win.winfo_pointery(),1,1
        vx,vy=win.winfo_vrootx(),win.winfo_vrooty()
        vw,vh=win.winfo_vrootwidth(),win.winfo_vrootheight()
        px,py=farming_confirmation_position((ax,ay,aw,ah),(620,height),(vx,vy,vw,vh))
        win.geometry(f'620x{height}{px:+d}{py:+d}')
        if table_height<=height-reserved:scroll.pack_forget()
        self._party_bring_forward(win)

    def show_ai_share_settings(self):
        messagebox.showinfo('AI 모델','학습·모델 배포는 개발환경에서만 진행합니다.',parent=self.root)

    def show_ai_storage_guide(self):
        messagebox.showinfo('AI 모델','로컬 학습·학습 자료 저장 기능은 제거됐습니다.',parent=self.root)

    def show_stage_rewards(self):
        """Inspect stage artwork and compare its recorded drops with farming targets."""
        from PIL import ImageTk
        from tkinter import filedialog
        existing=getattr(self,'_stage_rewards_window',None)
        if existing is not None and existing.winfo_exists():
            self._stage_rewards_refresh();existing.deiconify();existing.lift();return
        win=tk.Toplevel(self.root);self._stage_rewards_window=win
        win.title('스테이지 DB · 보상 확인');win.geometry('1100x720');win.minsize(1000,650)
        shell=ttk.Frame(win,padding=12);shell.pack(fill='both',expand=True)
        ttk.Label(shell,text='스테이지와 등록 보상을 선택하고 오른쪽 파밍 재료와 비교하세요.',font=('Malgun Gothic',11,'bold')).pack(anchor='w')
        ttk.Label(shell,text='보유 수량과 보상 수량은 다를 수 있습니다. 숫자가 아닌 테두리·캐릭터 그림으로 같은 재료인지 판단하세요.',wraplength=1050).pack(anchor='w',pady=(4,8))
        status=tk.StringVar(value='스테이지를 선택하세요.')
        footer=ttk.Frame(shell);footer.pack(side='bottom',fill='x',pady=(8,0))
        ttk.Label(footer,textvariable=status,wraplength=830).pack(side='left',fill='x',expand=True)
        ttk.Button(footer,text='닫기',command=win.withdraw).pack(side='right')
        body=ttk.Frame(shell);body.pack(fill='both',expand=True)
        stages_frame=ttk.Frame(body,width=180);stages_frame.pack(side='left',fill='y',padx=(0,8));stages_frame.pack_propagate(False)
        ttk.Label(stages_frame,text='스테이지',font=('Malgun Gothic',10,'bold')).pack(anchor='w')
        stages=ttk.Treeview(stages_frame,columns=('state',),show='tree headings',height=12,selectmode='browse')
        stages.heading('#0',text='번호');stages.heading('state',text='보상 자료')
        stages.column('#0',width=68,stretch=False);stages.column('state',width=100)
        stages.pack(fill='both',expand=True,pady=4)
        illustration=ttk.Label(stages_frame,anchor='center',text='스테이지 이미지 없음');illustration.pack(fill='x',pady=6)
        ttk.Button(stages_frame,text='새로고침',command=lambda:refresh()).pack(fill='x')
        originals=ttk.Button(stages_frame,text='원본 보상 화면',command=lambda:show_original());originals.pack(fill='x',pady=(5,0))
        drops_frame=ttk.Frame(body,width=220);drops_frame.pack(side='left',fill='y',padx=(0,10));drops_frame.pack_propagate(False)
        ttk.Label(drops_frame,text='등록된 보상',font=('Malgun Gothic',10,'bold')).pack(anchor='w')
        drops=ttk.Treeview(drops_frame,columns=('name',),show='tree headings',height=12,selectmode='browse')
        drops.heading('#0',text='순서');drops.heading('name',text='연결 재료');drops.column('#0',width=45,stretch=False);drops.column('name',width=160)
        drops.pack(fill='both',expand=True,pady=4)
        edit_buttons=ttk.Frame(drops_frame);edit_buttons.pack(fill='x',pady=(6,0))
        replace=ttk.Button(edit_buttons,text='스샷 교체',command=lambda:replace_image());replace.pack(side='left',fill='x',expand=True,padx=(0,4))
        delete=ttk.Button(edit_buttons,text='보상 삭제',command=lambda:delete_drop());delete.pack(side='left',fill='x',expand=True)
        right=ttk.Frame(body);right.pack(side='left',fill='both',expand=True)
        ttk.Label(right,text='비교할 현재 파밍 / 등록 크리스탈').pack(anchor='w')
        choice=tk.StringVar();selector=ttk.Combobox(right,textvariable=choice,state='readonly');selector.pack(fill='x',pady=(4,8))
        actions=ttk.Frame(right);actions.pack(fill='x',pady=(0,8))
        same=ttk.Button(actions,text='같은 재료',command=lambda:decide(True));same.pack(side='left',padx=(0,6))
        different=ttk.Button(actions,text='다른 재료',command=lambda:decide(False));different.pack(side='left',padx=(0,6))
        pending=ttk.Button(actions,text='재료판단에 남기기',command=lambda:queue_pair());pending.pack(side='left')
        views=ttk.Frame(right);views.pack(fill='both',expand=True)
        labels=[]
        for heading in ('스테이지에 등록된 보상','현재 파밍 / 등록 크리스탈'):
            col=ttk.Frame(views);col.pack(side='left',fill='both',expand=True,padx=4)
            ttk.Label(col,text=heading,font=('Malgun Gothic',10,'bold')).pack(pady=4)
            label=ttk.Label(col,anchor='center',text='이미지 없음');label.pack(fill='both',expand=True);labels.append(label)
        ttk.Label(right,text='오른쪽에서 올바른 재료를 고르고 ‘같은 재료’를 누르면 연결을 수정합니다.\n교체·삭제한 자료는 다음 스테이지 확인에서 다시 읽습니다.',wraplength=600).pack(fill='x',pady=6)
        ttk.Button(right,text='재료판단 열기',command=self.show_quantity_management).pack(anchor='e')
        state={'rows':{},'stage':None,'index':None,'drop':None,'crystals':{},'choices':{},'photos':[],'illustration':None}
        self._stage_rewards_state=state
        def busy():return bool(getattr(self,'_background_running',False) or getattr(self,'_material_catalog_busy',False))
        def selected():
            if busy():raise ValueError('자동파밍을 중지한 뒤 보상 자료를 수정해 주세요.')
            if state['stage'] is None or state['drop'] is None:raise ValueError('스테이지의 보상을 선택하세요.')
            return state['stage'],state['index'],state['drop'].get('image_file')
        def selected_cid():
            cid=state['choices'].get(choice.get())
            if cid is None:raise ValueError('비교할 크리스탈을 선택하세요.')
            return cid
        def show_pair(event=None):
            state['photos']=[]
            for label in labels:label.configure(image='',text='이미지 없음')
            row=state['drop'];cid=state['choices'].get(choice.get())
            enabled=row is not None and not busy()
            for button in (replace,delete):button.configure(state='normal' if enabled else 'disabled')
            for button in (same,different,pending):button.configure(state='disabled')
            try:
                images=[self._stage_drop_image(state['stage'],state['index'],expected_image_file=row.get('image_file')) if row else None,None]
                if cid:
                    entry=state['crystals'][cid];name=str(entry.get('image_file') or '')
                    if Path(name).name!=name or not name:raise ValueError('등록 크리스탈 이미지 경로를 확인하세요.')
                    with Image.open(self.data_paths['icons_dir']/name) as image:images[1]=image.convert('RGB')
                for image,label in zip(images,labels):
                    if image is None:continue
                    photo=ImageTk.PhotoImage(fit_image_preserve_aspect(image,(245,320)),master=win)
                    state['photos'].append(photo);label.configure(image=photo,text='')
                enabled=row is not None and not busy()
                for button in (replace,delete):button.configure(state='normal' if enabled else 'disabled')
                for button in (same,different,pending):button.configure(state='normal' if enabled and cid else 'disabled')
            except (OSError,ValueError,RuntimeError,tk.TclError) as error:
                status.set('보상 이미지 확인 실패: '+str(error))
        def pick_drop(event=None):
            chosen=drops.selection();state['index']=None;state['drop']=None
            if chosen:
                index=int(chosen[0]);row=state['rows'][state['stage']]['drops'][index]
                state['index']=index;state['drop']=row
                linked=str(row.get('id') or '')
                if linked and not preferred:
                    label=next((label for label,cid in state['choices'].items() if cid==linked),None)
                    if label:choice.set(label)
                status.set(f"★{state['stage']} · 보상 {index+1} · 숫자가 달라도 같은 재료일 수 있습니다.")
            show_pair()
        def pick_stage(event=None):
            chosen=stages.selection();state['stage']=chosen[0] if chosen else None
            state['index']=None;state['drop']=None;drops.delete(*drops.get_children());state['illustration']=None
            illustration.configure(image='',text='스테이지 이미지 없음')
            originals.configure(state='disabled')
            if chosen:
                row=state['rows'][chosen[0]]
                originals.configure(state='normal' if row.get('preview_pages') else 'disabled')
                for index,drop in enumerate(row.get('drops',[])):
                    cid=str(drop.get('id') or '');entry=state['crystals'].get(cid,{})
                    drops.insert('','end',iid=str(index),text=str(index+1),values=(entry.get('crystal_name') or entry.get('label') or ('미연결' if not cid else cid),))
                name=str(row.get('image_file') or '')
                if name and Path(name).name==name:
                    try:
                        with Image.open(self._stage_db_path().parent/'stage_images'/name) as image:small=fit_image_preserve_aspect(image,(165,145))
                        photo=ImageTk.PhotoImage(small,master=win);state['illustration']=photo;illustration.configure(image=photo,text='')
                    except OSError:illustration.configure(text='스테이지 이미지 읽기 실패')
                status.set('다음 스테이지 확인에서 보상 자료를 갱신합니다.' if row.get('preview_refresh_required') or not row.get('preview_verified') else '등록된 보상과 현재 파밍 재료를 비교하세요.')
                if drops.get_children():drops.selection_set(drops.get_children()[0]);pick_drop();return
            show_pair()
        def refresh(keep_stage=None,keep_drop=None):
            nonlocal preferred
            old=keep_stage or state['stage'];old_drop=state['index'] if keep_drop is None else keep_drop
            try:
                db=self._load_crystal_db();state['crystals']={str(c['id']):c for c in db.get('crystals',[])}
                ledger=self._load_crystal_ledger();group=ledger.get('farming_groups',{}).get(ledger.get('active_farming_group'),{})
                selected_ids={str(cid) for cid in (getattr(self,'_background_selected_material_ids',None) or [])}
                preferred=[str(r['id']) for r in group.get('materials',[]) if not selected_ids or str(r['id']) in selected_ids]
                preferred.extend(cid for cid in state['crystals'] if cid in selected_ids and cid not in preferred)
                ranked=sorted(state['crystals'],key=lambda cid:(cid not in preferred,preferred.index(cid) if cid in preferred else 0,list(state['crystals']).index(cid)))
                state['choices']={('파밍 · ' if cid in preferred else '')+str(state['crystals'][cid].get('crystal_name') or state['crystals'][cid].get('label') or cid)+' ['+cid+']':cid for cid in ranked}
                selector.configure(values=list(state['choices']))
                if choice.get() not in state['choices']:choice.set(next(iter(state['choices']),''))
                rows=self._stage_review_rows();state['rows']={str(row['number']):row for row in rows}
                stages.delete(*stages.get_children())
                for number,row in state['rows'].items():
                    flag='갱신 대기' if row.get('preview_refresh_required') or not row.get('preview_verified') else '등록됨'
                    stages.insert('','end',iid=number,text='★'+number,values=(f"{len(row.get('drops',[]))}종 · {flag}",))
                chosen=old if old in state['rows'] else next(iter(state['rows']),None)
                if chosen is not None:
                    stages.selection_set(chosen);pick_stage()
                    if old_drop is not None and str(old_drop) in drops.get_children():drops.selection_set(str(old_drop));pick_drop()
                else:
                    state['stage']=None;state['drop']=None;drops.delete(*drops.get_children());show_pair();status.set('등록된 스테이지가 없습니다. 게임에서 스테이지를 확인하면 표시됩니다.')
                if busy():status.set('자동파밍 중 · 보상 자료는 확인만 가능합니다. 수정하려면 파밍을 중지하세요.')
            except (OSError,ValueError,RuntimeError,KeyError) as error:status.set('스테이지 DB 읽기 실패: '+str(error))
        def show_original():
            number=state['stage'];row=state['rows'].get(number,{})
            pages=row.get('preview_pages') or []
            if not pages:status.set('저장된 원본 보상 화면이 없습니다. 다음 스테이지 확인에서 저장됩니다.');return
            viewer=tk.Toplevel(win);viewer.title('스테이지 ★'+str(number)+' · 등록 당시 보상 원본');viewer.geometry('1020x740');viewer.minsize(700,500)
            controls=ttk.Frame(viewer,padding=8);controls.pack(fill='x')
            ttk.Label(controls,text='등록 당시 원본 화면 · 아래 목록에서 페이지를 선택하세요.').pack(side='left')
            page=tk.StringVar(value='1');choose=ttk.Combobox(controls,textvariable=page,values=[str(i+1) for i in range(len(pages))],state='readonly',width=5);choose.pack(side='left',padx=8)
            ttk.Button(controls,text='닫기',command=viewer.destroy).pack(side='right')
            image_label=ttk.Label(viewer,anchor='center');image_label.pack(fill='both',expand=True,padx=8,pady=8)
            holder={}
            def load_page(event=None):
                try:
                    name=str(pages[int(page.get())-1])
                    if not name or Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('원본 이미지 경로가 잘못됐습니다.')
                    folder=self._stage_db_path().parent/'stage_images';path=folder/name
                    if not path.resolve().is_relative_to(folder.resolve()):raise ValueError('원본 이미지 경로가 잘못됐습니다.')
                    with Image.open(path) as image:display=fit_image_preserve_aspect(image,(990,650))
                    holder['photo']=ImageTk.PhotoImage(display,master=viewer);image_label.configure(image=holder['photo'],text='')
                except (OSError,ValueError,IndexError) as error:image_label.configure(image='',text='원본 보상 이미지 읽기 실패: '+str(error))
            choose.bind('<<ComboboxSelected>>',load_page);load_page()
            viewer._stage_source_images=holder
        def decide(same_material):
            try:
                number,index,filename=selected();cid=selected_cid()
                self._stage_review_decide(number,index,cid,same_material,expected_image_file=filename)
                refresh(number,index);status.set(('같은 재료' if same_material else '다른 재료')+' 판단 저장 · 보유·획득 수량은 변경하지 않았습니다.')
            except (OSError,ValueError,RuntimeError) as error:status.set(str(error))
        def queue_pair():
            try:
                number,index,filename=selected();cid=selected_cid()
                self._stage_review_pair(number,index,cid,expected_image_file=filename)
                refresh_icons=getattr(self,'_icon_review_refresh',None)
                if callable(refresh_icons):refresh_icons()
                status.set('재료판단에 저장했습니다. 재료판단 열기에서 같은 재료 / 다른 재료로 확인하세요.')
            except (OSError,ValueError,RuntimeError) as error:status.set(str(error))
        def replace_image():
            try:
                number,index,filename=selected()
                path=filedialog.askopenfilename(parent=win,title='올바른 보상 스크린샷 선택',filetypes=[('이미지','*.png *.jpg *.jpeg *.webp *.bmp'),('모든 파일','*')])
                if not path:return
                with Image.open(path) as opened:
                    if opened.width*opened.height>16000000:raise ValueError('이미지가 너무 큽니다. 보상 아이콘 부분만 잘라 선택하세요.')
                    image=opened.convert('RGB')
                if not messagebox.askyesno('보상 스샷 교체','선택한 보상 이미지를 교체할까요? 다음 스테이지 확인에서 다시 갱신합니다.',parent=win):return
                self._stage_review_edit(number,index,image=image,expected_image_file=filename)
                refresh(number,index);status.set('보상 스샷 교체 완료 · 다음 스테이지 확인에서 갱신합니다.')
            except (OSError,ValueError,RuntimeError) as error:status.set(str(error))
        def delete_drop():
            try:
                number,index,filename=selected()
                if not messagebox.askyesno('등록 보상 삭제','선택한 보상 자료만 삭제할까요? 다음 스테이지 확인에서 다시 등록합니다.',parent=win):return
                self._stage_review_delete(number,index,expected_image_file=filename)
                refresh(number,index);status.set('선택 보상 삭제 완료 · 다음 스테이지 확인에서 다시 등록합니다.')
            except (OSError,ValueError,RuntimeError) as error:status.set(str(error))
        preferred=[]
        stages.bind('<<TreeviewSelect>>',pick_stage);drops.bind('<<TreeviewSelect>>',pick_drop);selector.bind('<<ComboboxSelected>>',show_pair)
        self._stage_rewards_refresh=refresh;refresh()

    def _build_icon_review_tab(self,parent):
        from PIL import ImageTk
        from pad0_crystal_review import icon_review_list, icon_review_confirm
        root=self.data_paths['root']
        shell=ttk.Frame(parent,padding=12);shell.pack(fill='both',expand=True)
        ttk.Label(shell,text='현재 화면과 등록된 재료를 좌우로 비교해 주세요.',font=('Malgun Gothic',11,'bold')).pack(anchor='w')
        ttk.Label(shell,text='테두리·캐릭터 그림으로 판단하세요. 이미지 갱신 기록은 숫자가 달라도 같은 재료일 수 있습니다.',wraplength=830).pack(anchor='w',pady=6)
        status=tk.StringVar(value='자동파밍에서 아이콘을 확정하지 못하면 비교 기록이 쌓입니다.')
        ttk.Label(shell,textvariable=status,wraplength=830).pack(side='bottom',fill='x',pady=8)
        body=ttk.Frame(shell);body.pack(fill='both',expand=True)
        left=ttk.Frame(body,width=195);left.pack(side='left',fill='y',padx=(0,12))
        cases=tk.Listbox(left,width=23,exportselection=False);cases.pack(fill='both',expand=True)
        right=ttk.Frame(body);right.pack(side='left',fill='both',expand=True)
        candidate=tk.StringVar();selector=ttk.Combobox(right,textvariable=candidate,state='readonly',width=48);selector.pack(fill='x',pady=(0,8))
        actions=ttk.Frame(right);actions.pack(fill='x',pady=(0,8))
        views=ttk.Frame(right);views.pack(fill='both',expand=True)
        labels=[];headings=[]
        for title in ('현재 화면의 아이콘','등록된 재료 이미지'):
            column=ttk.Frame(views);column.pack(side='left',fill='both',expand=True,padx=4)
            heading=ttk.Label(column,text=title,font=('Malgun Gothic',10,'bold'));heading.pack(anchor='center',pady=5);headings.append(heading)
            label=ttk.Label(column,anchor='center');label.pack(fill='both',expand=True);labels.append(label)
        state={'records':[],'record':None,'choices':[],'photos':[]}
        def show_pair(event=None):
            record=state['record']
            if record is None:return
            try:index=state['choices'].index(candidate.get())
            except ValueError:return
            reference=record['candidates'][index]
            titles=(('새 사용 이미지','이전 사용 이미지') if record.get('category')=='image_update' else ('출현 / 보상 등록 이미지','현재 파밍 / 등록 크리스탈') if record.get('category')=='stage_reward' else ('실제 보상 화면의 크리스탈','현재 파밍 / 등록 크리스탈') if record.get('category')=='farming_reward' else ('현재 화면의 아이콘','등록된 재료 이미지'))
            for heading,title in zip(headings,titles):heading.configure(text=title)
            folder=Path(root)/'crystal_review'/'cases'/record['id'];photos=[]
            try:
                for path,label in zip((folder/'probe.png',folder/f'reference_{index}.png'),labels):
                    with Image.open(path) as opened:image=opened.convert('RGB')
                    image=fit_image_preserve_aspect(image,(250,300))
                    photo=ImageTk.PhotoImage(image,master=parent);photos.append(photo);label.configure(image=photo,text='')
                state['photos']=photos
                verdict=record.get('decisions',{}).get(reference['cid'],'미확인')
                stage=record.get('context',{}).get('stage_number')
                prefix='★'+str(stage)+' · ' if stage else ''
                status.set(prefix+f"{reference['label']} · 비교 차이 {reference['score']:.2f} · 판단 {verdict} · {record['reason']}")
            except (OSError,ValueError,tk.TclError) as error:status.set('비교 이미지 읽기 실패: '+str(error))
        def pick(event=None):
            indexes=cases.curselection()
            if not indexes:return
            record=state['records'][indexes[0]];state['record']=record
            state['choices']=[f"{c['label']} [{c['cid']}]" for c in record['candidates']]
            selector.configure(values=state['choices'])
            index=next((i for i,c in enumerate(record['candidates']) if c['cid'] not in record.get('decisions',{})),0)
            candidate.set(state['choices'][index]);show_pair()
        def refresh():
            state['records']=icon_review_list(root);state['record']=None;cases.delete(0,'end')
            for record in state['records']:
                category=record.get('category');stage=record.get('context',{}).get('stage_number')
                prefix='이미지 갱신 · ' if category=='image_update' else ('★'+str(stage)+' · ' if stage else '')+'보상 · ' if category in ('stage_reward','farming_reward') else ''
                cases.insert('end',('미확인' if record['status']=='pending' else '확인 완료')+' · '+prefix+record['candidates'][0]['label'])
            if state['records']:cases.selection_set(0);pick()
            else:
                status.set('아직 아이콘 비교 기록이 없습니다. 자동파밍에서 구분이 어려운 아이콘은 이곳에 기록됩니다.')
                for label in labels:label.configure(image='',text='비교 기록 없음')
        def confirm(same):
            record=state['record']
            if record is None:return
            try:
                index=state['choices'].index(candidate.get());cid=record['candidates'][index]['cid']
                if getattr(self,'_background_running',False):raise ValueError('자동파밍을 중지한 뒤 재료판단을 저장해 주세요.')
                saved=self._stage_review_confirm_record(record,cid,same) if record.get('category')=='stage_reward' else icon_review_confirm(root,record['id'],cid,same)
                refresh()
                refresh_stages=getattr(self,'_stage_rewards_refresh',None)
                if callable(refresh_stages):refresh_stages()
                status.set('판단 저장 완료 · 다음 아이콘 비교부터 사용합니다.')
            except (ValueError,OSError,RuntimeError) as error:status.set(str(error))
        ttk.Button(actions,text='같은 재료',width=14,command=lambda:confirm(True)).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='다른 재료',width=14,command=lambda:confirm(False)).pack(side='left')
        ttk.Button(actions,text='다시 읽기',command=refresh).pack(side='right')
        cases.bind('<<ListboxSelect>>',pick);selector.bind('<<ComboboxSelected>>',show_pair)
        self._icon_review_refresh=refresh;refresh()

    def show_quantity_management(self):
        messagebox.showinfo('수량 판독','수량은 내장 판독과 OCR로 확인합니다. 로컬 학습 자료는 저장하지 않습니다.',parent=self.root)

    def show_ai_management(self,case_id=None):
        win=tk.Toplevel(self.root)
        win.title('AI 모델')
        body=ttk.Frame(win,padding=16);body.pack(fill='both',expand=True)
        ttk.Label(body,text=self._visual_model_status_text(),wraplength=560).pack(anchor='w')
        ttk.Label(body,text='앱은 개발환경에서 학습해 배포한 내장 모델만 사용합니다.\n사용자 학습 모델·원본·정답·전송 대기 자료는 저장하지 않습니다.',wraplength=560).pack(anchor='w',pady=10)
        ttk.Button(body,text='닫기',command=win.destroy).pack(anchor='e')

    def _farming_show_stats(self, message):
        if getattr(self,'_root_tree_active',False) is True:return
        self._farming_last_message = message
        ledger = self._load_crystal_ledger()
        selection=configured_farming_selection(ledger)
        if selection is None or getattr(self,'user_settings',{}).get('farming_hide_stats',False) or all(r['deficit']==0 for r in farming_group_rows(ledger,selection['key']) if r['id'] in selection['material_ids']):
            window=getattr(self,'_farming_stats',None)
            if window is not None:window.withdraw()
            return
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
        key = selection['key']
        self._farming_group_keys = [key]
        self._farming_group_select['values'] = [groups[k]['name'] for k in self._farming_group_keys]
        self._farming_group_select.current(self._farming_group_keys.index(key))
        self._farming_tree.delete(*self._farming_tree.get_children())
        self._farming_photos = []
        from PIL import ImageTk
        entries = {str(row['id']):row for row in self._load_crystal_db()['crystals']}
        rows = [row for row in farming_group_rows(ledger, key) if row['id'] in selection['material_ids']]
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


class SkillTacticsWindow:
    """A separate draft editor; saving changes settings, never game input."""
    def __init__(self,app):
        self.app=app;self.win=tk.Toplevel(app.root);self.win.title('스킬 택틱 · 상태별 우선순위')
        self.win.geometry(f'1080x{min(820,self.win.winfo_screenheight()-65)}');self.win.minsize(940,700)
        body=ttk.Frame(self.win,padding=14);body.pack(fill='both',expand=True)
        header=ttk.Frame(body);header.pack(fill='x')
        ttk.Label(header,text='스킬 택틱',font=('Segoe UI',16,'bold')).pack(side='left')
        self.enabled=tk.BooleanVar(self.win,value=False)
        ttk.Checkbutton(header,text='상태별 우선순위 사용',variable=self.enabled).pack(side='left',padx=20)
        ttk.Button(header,text='현재 파티 다시 불러오기',command=self.reload_party).pack(side='right')
        ttk.Label(body,text='현재 파티에서 캐릭터를 고르고, 상태별로 사용할 순서를 정하세요. 저장한 택틱은 다음 자동 파밍부터 적용됩니다.').pack(anchor='w',pady=(8,6))
        self.members=ttk.Frame(body);self.members.pack(fill='x');self.member=tk.IntVar(self.win,value=0)
        card_font=('Malgun Gothic' if sys.platform=='win32' else 'Noto Sans CJK KR',9)
        self.member_labels=[];self.member_art=[];self.member_skills=[]
        for slot in range(6):
            self.members.columnconfigure(slot,weight=1,uniform='tactic_member')
            card=ttk.LabelFrame(self.members,text=f'{slot+1}번',padding=6);card.grid(row=0,column=slot,sticky='nsew',padx=3)
            art=ttk.Label(card,anchor='center');art.pack();self.member_art.append(art)
            choice=ttk.Radiobutton(card,variable=self.member,value=slot,text='선택')
            choice.pack(anchor='center',pady=4)
            name=ttk.Label(card,text='미등록',wraplength=145,justify='center',anchor='center',font=card_font)
            name.pack(fill='x');self.member_labels.append(name)
            label=ttk.Label(card,text='',wraplength=145,justify='left',font=card_font);label.pack(fill='x');self.member_skills.append(label)
        ttk.Separator(body).pack(fill='x',pady=10)
        editor=ttk.Frame(body);editor.pack(fill='both',expand=True)
        left=ttk.Frame(editor);left.pack(side='left',fill='both',expand=True,padx=(0,14))
        self.states=ttk.Treeview(left,columns=('order',),show='tree headings',height=10,selectmode='browse')
        self.states.heading('#0',text='상태');self.states.column('#0',width=155,minwidth=130)
        self.states.heading('order',text='우선순위 (파티 번호)');self.states.column('order',width=235,minwidth=180)
        scrollbar=ttk.Scrollbar(left,orient='vertical',command=self.states.yview);self.states.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');self.states.pack(fill='both',expand=True)
        right=ttk.Frame(editor);right.pack(side='left',fill='both',expand=True)
        self.condition=tk.StringVar(self.win,value='상태 선택')
        ttk.Label(right,textvariable=self.condition,font=('Segoe UI',11,'bold')).pack(anchor='w')
        self.order_list=tk.Listbox(right,height=8,exportselection=False,selectmode='browse');self.order_list.pack(fill='both',expand=True,pady=6)
        buttons=ttk.Frame(right);buttons.pack(fill='x')
        ttk.Button(buttons,text='선택한 크리처 추가',command=self.add_member).pack(side='left')
        ttk.Button(buttons,text='위로',command=lambda:self.move_member(-1)).pack(side='left',padx=4)
        ttk.Button(buttons,text='아래로',command=lambda:self.move_member(1)).pack(side='left')
        ttk.Button(buttons,text='빼기',command=self.remove_member).pack(side='left',padx=4)
        ttk.Button(right,text='이 상태를 기본 자동 순서로',command=self.reset_state).pack(anchor='w',pady=6)
        ttk.Label(body,text='준비되지 않았거나 해당 상태에 대응하지 않는 스킬은 건너뜁니다. 바인드·각성무효 해제 등 기존 우선 대응 순서는 유지됩니다.',wraplength=1030).pack(anchor='w',pady=(10,4))
        footer=ttk.Frame(body);footer.pack(side='bottom',fill='x',before=header)
        self.status=tk.StringVar(self.win,value='')
        ttk.Label(footer,textvariable=self.status,wraplength=810).pack(side='left',fill='x',expand=True)
        self.save_button=ttk.Button(footer,text='저장',command=self.save);self.save_button.pack(side='right',padx=(8,0))
        ttk.Button(footer,text='닫기',command=self.win.destroy).pack(side='right')
        self.states.bind('<<TreeviewSelect>>',lambda event:self.select_state())
        self.reload_party()

    def reload_party(self):
        services=self.app._ui_backend_services()
        raw=self.app.user_settings.get('background_party_profiles',[])
        self.profiles=[services.profile_merge(raw[i],self.app.user_settings) if i<len(raw) else {} for i in range(6)]
        self.party=skill_tactics_party(self.profiles)
        self.orders={};config=self.app.user_settings.get('skill_tactics')
        try:validated=normalize_skill_tactics(config,self.profiles)
        except (ValueError,TypeError):validated=dict(enabled=False,orders={})
        self.enabled.set(validated['enabled']);self.orders={key:list(order) for key,order in validated['orders'].items()}
        for slot,profile in enumerate(self.profiles):
            name=profile.get('name') or '미등록';number=self.party[slot]
            self.member_labels[slot].configure(text=f'{name}\n'+(f'No.{number}' if number else '파티 등록 필요'))
            description=profile.get('text') or '스킬 정보가 없습니다.'
            text=(profile.get('attributes_ko') or '속성 확인 필요')+' · '+str(profile.get('cooldown') or '?')+'턴\n'+(description[:70]+'…' if len(description)>70 else description)
            self.member_skills[slot].configure(text=text)
            label=self.member_art[slot];label.configure(image='',text='');label.image=None
            if profile.get('portrait_png'):
                try:
                    import base64,io
                    from PIL import ImageTk
                    image=Image.open(io.BytesIO(base64.b64decode(profile['portrait_png']))).convert('RGB');image.thumbnail((64,96))
                    label.image=ImageTk.PhotoImage(image,master=self.win);label.configure(image=label.image)
                except (OSError,ValueError):pass
        self.states.delete(*self.states.get_children())
        for key,label,_,_ in SKILL_TACTIC_STATES:self.states.insert('','end',iid=key,text=label,values=(self.order_summary(key),))
        self.states.selection_set('bound');self.select_state()
        self.status.set('현재 파티 기준으로 편집 중 · 저장 전에는 적용되지 않습니다.' if not isinstance(config,dict) or config.get('party')==self.party else '파티가 바뀌어 기본 순서로 불러왔습니다. 새 순서를 저장해 주세요.')

    def current_state(self):
        selected=self.states.selection();return selected[0] if selected else None

    def order_summary(self,key):
        order=self.orders.get(key,[])
        return ' → '.join(str(slot+1) for slot in order) if order else '기본 자동 순서'

    def select_state(self):
        key=self.current_state()
        if key is None:return
        label=next(label for value,label,_,_ in SKILL_TACTIC_STATES if value==key)
        self.condition.set(label+' · 먼저 사용할 순서');self.order_list.delete(0,'end')
        for rank,slot in enumerate(self.orders.get(key,[]),1):self.order_list.insert('end',f'{rank}. {slot+1}번 · '+self.profiles[slot].get('name','미등록'))
        self.states.set(key,'order',self.order_summary(key))

    def add_member(self):
        key=self.current_state();slot=self.member.get()
        if key is None:return
        if not self.party[slot]:self.status.set('먼저 파티 설정에서 해당 크리처를 등록해 주세요.');return
        order=self.orders.setdefault(key,[])
        if slot in order:self.status.set('이미 이 상태의 우선순위에 있습니다. 위로·아래로 순서를 바꿀 수 있습니다.');return
        order.append(slot);self.select_state();self.order_list.selection_set(len(order)-1)
        self.status.set('변경 사항을 저장해 주세요.')

    def move_member(self,direction):
        key=self.current_state();selected=self.order_list.curselection()
        if key is None or not selected:return
        index=selected[0];order=self.orders.get(key,[]);target=index+direction
        if not 0<=target<len(order):return
        order[index],order[target]=order[target],order[index];self.select_state();self.order_list.selection_set(target)
        self.status.set('변경 사항을 저장해 주세요.')

    def remove_member(self):
        key=self.current_state();selected=self.order_list.curselection()
        if key is None or not selected:return
        self.orders[key].pop(selected[0]);self.select_state();self.status.set('변경 사항을 저장해 주세요.')

    def reset_state(self):
        key=self.current_state()
        if key is None:return
        self.orders.pop(key,None);self.select_state();self.status.set('기본 자동 순서로 되돌렸습니다. 저장해 주세요.')

    def save(self):
        try:
            config=normalize_skill_tactics(dict(enabled=self.enabled.get(),party=self.party,orders=self.orders),
                self.app.user_settings.get('background_party_profiles',[]))
        except (ValueError,TypeError) as error:self.status.set(str(error));return
        self.app.user_settings['skill_tactics']=config;self.app.save_user_settings()
        self.status.set('저장했습니다. 다음 자동 파밍부터 적용됩니다.' if config['enabled'] else '저장했습니다. 기본 자동 스킬 순서를 사용합니다.')
