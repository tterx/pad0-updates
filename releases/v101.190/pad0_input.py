"""Mouse hooks and Android gesture transports, independent of GUI/recognition.

Device objects provide cancellation, native dimensions, shell/trace services and
create_input_capture_client(cancel) for a fresh SDK release channel. Importing
this module never connects a device, loads a DLL or creates user data files.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import random
import re
import subprocess
import sys
import threading
import time
import uuid


def park_background_cursor(hwnd,window_rect,board_rect,api=None):
    """Move once only if the visible emulator board is under the PC cursor."""
    if api is None and sys.platform!='win32':return dict(moved=False,reason='not_windows')
    if not hwnd:return dict(moved=False,reason='window_unavailable')
    from ctypes import wintypes
    if api is None:
        api=ctypes.windll.user32
        api.GetCursorPos.argtypes=[ctypes.POINTER(wintypes.POINT)];api.GetCursorPos.restype=wintypes.BOOL
        api.WindowFromPoint.argtypes=[wintypes.POINT];api.WindowFromPoint.restype=wintypes.HWND
        api.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];api.GetAncestor.restype=wintypes.HWND
        api.SetCursorPos.argtypes=[ctypes.c_int,ctypes.c_int];api.SetCursorPos.restype=wintypes.BOOL
    point=wintypes.POINT()
    if not api.GetCursorPos(ctypes.byref(point)):raise RuntimeError('자동 시작 전 마우스 위치를 확인하지 못했습니다.')
    gx,gy,gw,gh=window_rect;bx,by,bw,bh=board_rect
    left=gx+bx*gw/1600;top=gy+by*gh/940;right=left+bw*gw/1600;bottom=top+bh*gh/940
    if not (left<=point.x<right and top<=point.y<bottom):return dict(moved=False,reason='outside_board')
    window=api.WindowFromPoint(point);root=api.GetAncestor(window,2) or window
    target=api.GetAncestor(hwnd,2) or hwnd
    if root!=target:return dict(moved=False,reason='another_window')
    destination=(round(left-20*gw/1600),round(top+12*gh/940))
    before=[point.x,point.y]
    if not api.SetCursorPos(*destination):raise RuntimeError('자동 시작 전 마우스를 퍼즐판 밖으로 옮기지 못했습니다.')
    if not api.GetCursorPos(ctypes.byref(point)) or left<=point.x<right and top<=point.y<bottom:
        raise RuntimeError('자동 시작 전 마우스가 퍼즐판 밖으로 나갔는지 확인하지 못했습니다.')
    return dict(moved=True,reason='parked_outside_board',before=before,after=[point.x,point.y])


def _input_resource_root():
    # Same existing Pad0Overlay tools directory; no crystal DB initialization.
    return Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))/'Pad0Overlay'


MANUAL_MASTER_MOVE_SECONDS=.075
SDK_FEEDBACK_MOVE_SECONDS=.08125
ANDROID_CONTROL_CORNER_DWELL_SECONDS=.0625
ANDROID_CONTROL_FEEDBACK_MOVE_SECONDS=.1125

# Slower first. A measured setting is local to this verified device/controller.
INPUT_SPEED_CANDIDATES=(
    dict(sample_seconds=.030,corner_seconds=.090),
    dict(sample_seconds=.025,corner_seconds=.075),
    dict(sample_seconds=.021875,corner_seconds=.0625),
    dict(sample_seconds=.01875,corner_seconds=.050),
    dict(sample_seconds=.01875,corner_seconds=.03125),
)

def validated_input_timing(value):
    if not isinstance(value,dict):raise ValueError('이동 속도 형식이 잘못됐습니다.')
    result={key:value.get(key) for key in ('sample_seconds','corner_seconds')}
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in result.values()):
        raise ValueError('이동 속도 숫자가 잘못됐습니다.')
    if not .01875<=result['sample_seconds']<=.04 or not .03125<=result['corner_seconds']<=.12:
        raise ValueError('이동 속도 범위를 벗어났습니다.')
    if result['corner_seconds']<result['sample_seconds']:raise ValueError('모서리 대기가 너무 짧습니다.')
    return result

def input_timing_move_budget(timing):
    timing=validated_input_timing(timing)
    return 4*timing['sample_seconds']+max(0,timing['corner_seconds']-timing['sample_seconds'])

def input_speed_identity(device):
    info=(getattr(device,'input_diagnostics',{}) or {}).get('maa_info') or {}
    settings=getattr(device,'capture_settings',{}) or {}
    fields=dict(serial=device.serial,size=list(device.native_size or []),adb=str(device.executable),
        package=device.package,mumu_root=settings.get('background_mumu_root',''),
        mumu_index=settings.get('background_mumu_index',''),maa_packages=list(MAA_PACKAGES),
        input_methods=info.get('input_methods'),capture_methods=info.get('screencap_methods'),config=info.get('config',{}),
        coordinate_policy=info.get('pad0_input_coordinates'))
    return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def calibrated_input_timing(device):
    # Only the MAA route uses this profile; old low-level input tools keep their
    # original timing. A changed device, resolution or controller invalidates it.
    info=(getattr(device,'input_diagnostics',{}) or {}).get('maa_info') or {}
    if info.get('input_methods')!=6:return None
    override=getattr(device,'_speed_test_timing',None)
    if override is not None:return validated_input_timing(override)
    record=(getattr(device,'capture_settings',{}) or {}).get('background_input_speed_profile')
    try:
        if (not isinstance(record,dict) or record.get('schema')!=1 or record.get('verified_runs',0)<3
                or record.get('identity')!=input_speed_identity(device)):return None
        return validated_input_timing(record['timing'])
    except (ValueError,TypeError,KeyError):return None

def run_input_speed_trials(trial,cancel,progress=None):
    """A trial supplies fresh-board/held-layout/visible-UP proof, never just ACK."""
    records=[];passed=[];chosen=None
    def run(timing,confirmation):
        if cancel.is_set():raise RuntimeError('속도 테스트를 중지했습니다.')
        row=trial(dict(timing),len(records),confirmation)
        if cancel.is_set():raise RuntimeError('속도 테스트를 중지했습니다.')
        if not isinstance(row,dict):raise RuntimeError('속도 테스트 결과 형식 오류')
        row=dict(row,timing=dict(timing),confirmation=confirmation);records.append(row)
        if progress is not None:progress(row,len(records))
        return row.get('passed') is True
    for timing in INPUT_SPEED_CANDIDATES:
        if not run(timing,False):break
        passed.append(dict(timing))
    if passed:
        # Keep a tested margin rather than selecting the edge of the passing
        # range. The selected setting must then pass two more fresh trials.
        chosen=passed[-2] if len(passed)>=2 else passed[-1]
        for _ in range(2):
            if not run(chosen,True):chosen=None;break
    return dict(trials=records,timing=chosen,verified_runs=3 if chosen is not None else 0)

def _update_input_diagnostics(device,**values):
    """Keep capture identity and failed backend reasons when opening input."""
    current=getattr(device,'input_diagnostics',None)
    if not isinstance(current,dict):current={};device.input_diagnostics=current
    current.update(values)

# Official, replaceable runtime. No MAA game resources or modified native DLLs.
MAA_PACKAGES=('MaaFw==5.14.3','MaaAgentBinary==1.0.1','StrEnum==0.4.15')
_MAA_INSTALL_LOCK=threading.Lock()

def maa_raw_coordinate_config(config,size):
    """Keep MaaTouch/minitouch's display basis equal to verified raw pixels.

    ScreenshotUseRawSize controls ControllerAgent, not MtouchHelper's separate
    DisplayFrames lookup. Window/secondary display sizes must not scale a raw
    screenshot coordinate a second time. Rotation still uses MAA's device query.
    """
    if (not isinstance(size,(tuple,list)) or len(size)!=2
            or any(type(v) is not int or v<=0 for v in size) or size[0]*size[1]>16000000):
        raise ValueError('MAA 입력 좌표의 원본 해상도가 잘못됐습니다.')
    result=json.loads(json.dumps(config or {}))
    command=result.setdefault('command',{})
    if not isinstance(command,dict):raise ValueError('MAA 명령 설정 형식이 잘못됐습니다.')
    width,height=size
    command['Resolution']=['{ADB}','-s','{ADB_SERIAL}','shell',f"printf '{width} {height} 0\\n'"]
    return result

def ensure_maa_runtime(cancel):
    """Install once for single-file updater users; never during a held gesture."""
    import importlib.metadata
    def installed():
        try:return all(importlib.metadata.version(p.split('==')[0]).lstrip('v')==p.split('==')[1] for p in MAA_PACKAGES)
        except importlib.metadata.PackageNotFoundError:return False
    if installed():return
    with _MAA_INSTALL_LOCK:
        if installed():return
        if cancel.is_set():raise RuntimeError('MAA 연결 준비를 중지했습니다.')
        runtime=_input_resource_root()/'tools'/'maa-5.14.3'
        runtime.mkdir(parents=True,exist_ok=True)
        if str(runtime) not in sys.path:sys.path.insert(0,str(runtime))
        importlib.invalidate_caches()
        if installed():return
        executable=Path(sys.executable)
        if executable.name.lower()=='pythonw.exe':executable=executable.with_name('python.exe')
        command=[str(executable),'-m','pip','install','--disable-pip-version-check','--no-deps','--upgrade','--target',str(runtime),*MAA_PACKAGES]
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
            deadline=time.monotonic()+180
            while True:
                if cancel.is_set() or time.monotonic()>deadline:
                    process.kill();process.communicate()
                    raise RuntimeError('MAA 연결 준비를 중지했습니다.' if cancel.is_set() else 'MAA 연결 도구 설치 시간 초과')
                try:
                    output,_=process.communicate(timeout=.1)
                    if process.returncode:raise RuntimeError('MAA 연결 도구 설치 실패: '+output.decode('utf-8','replace')[-600:])
                    break
                except subprocess.TimeoutExpired:pass
        importlib.invalidate_caches()
        if not installed():raise RuntimeError('MAA 연결 도구 버전을 확인하지 못했습니다.')

def android_touch_state(text):
    """True for an explicitly down Android touch, False for idle, None unknown.

    Hover-only display records must not be mistaken for a held touch.
    """
    lines=str(text).splitlines()
    for index,line in enumerate(lines):
        if 'TouchStates:' not in line:continue
        if '<no displays touched>' in line:return False
        indent=len(line)-len(line.lstrip());block=[]
        for candidate in lines[index+1:index+41]:
            if candidate.strip() and len(candidate)-len(candidate.lstrip())<=indent:break
            block.append(candidate)
        downs=re.findall(r'\bdown\s*=\s*(true|false)\b','\n'.join(block),re.I)
        return any(value.lower()=='true' for value in downs) if downs else None
    return None


def release_emulator_mouse_buttons(hwnd):
    """Explicit F10 cleanup only: UP/CANCEL to the known host, no move/focus/DOWN."""
    if sys.platform!='win32' or not hwnd:return dict(attempted=False)
    from ctypes import wintypes as w
    api=ctypes.WinDLL('user32',use_last_error=True)
    callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    api.IsWindow.argtypes=(w.HWND,);api.IsWindow.restype=w.BOOL
    api.EnumChildWindows.argtypes=(w.HWND,callback_type,w.LPARAM);api.EnumChildWindows.restype=w.BOOL
    api.PostMessageW.argtypes=(w.HWND,w.UINT,ctypes.c_size_t,ctypes.c_ssize_t);api.PostMessageW.restype=w.BOOL
    api.GetCursorPos.argtypes=(ctypes.POINTER(w.POINT),);api.GetCursorPos.restype=w.BOOL
    api.ScreenToClient.argtypes=(w.HWND,ctypes.POINTER(w.POINT));api.ScreenToClient.restype=w.BOOL
    if not api.IsWindow(hwnd):return dict(attempted=False,reason='window_closed')
    windows=[int(hwnd)]
    def child(handle,_):
        if len(windows)<128:windows.append(int(handle))
        return True
    api.EnumChildWindows(hwnd,callback_type(child),0)
    cursor=w.POINT();have_cursor=bool(api.GetCursorPos(ctypes.byref(cursor)))
    errors=[]
    for handle in windows:
        point=w.POINT(cursor.x,cursor.y)
        local=bool(have_cursor and api.ScreenToClient(handle,ctypes.byref(point)))
        packed=((int(point.x)&0xffff)|((int(point.y)&0xffff)<<16)) if local else 0
        for message in (0x0202,0x001F):
            if not api.PostMessageW(handle,message,0,packed if message==0x0202 else 0):errors.append((handle,message))
    # Release a Windows button state even when this session used SDK only.
    # This does not move the cursor, activate a window, or issue any press.
    api.mouse_event.argtypes=(w.DWORD,w.DWORD,w.DWORD,w.DWORD,ctypes.c_size_t);api.mouse_event.restype=None
    api.mouse_event(0x0004,0,0,0,0x50414430)
    if errors:raise RuntimeError('MuMu 마우스 놓기 전달 미확인: '+str(errors))
    return dict(attempted=True,windows=windows,released_messages=True)


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
        api.WindowFromPoint.argtypes = (w.POINT,)
        api.WindowFromPoint.restype = w.HWND
        api.ScreenToClient.argtypes = (w.HWND,ctypes.POINTER(w.POINT))
        api.ScreenToClient.restype = w.BOOL
        api.IsWindow.argtypes = (w.HWND,)
        api.IsWindow.restype = w.BOOL
        api.PostMessageW.argtypes = (w.HWND,w.UINT,ctypes.c_size_t,ctypes.c_ssize_t)
        api.PostMessageW.restype = w.BOOL
        self._Point=w.POINT
        self._release_seen=threading.Event()
        self._last_screen=None
        self._target_window=None
        self._release_diagnostics={}
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
                    if message_id==0x0202 and event.flags&1 and event.dwExtraInfo==self.INPUT_TAG:
                        self._release_seen.set()
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
            self._last_screen=(int(x),int(y))
            dx, dy = self.absolute_position(x, y, *(self._api.GetSystemMetrics(i)
                                                   for i in (76, 77, 78, 79)))
            self._send(0x0001 | 0x8000 | 0x4000 | 0x2000, dx, dy)

    def down(self):
        with self._lock:
            point=getattr(self,'_last_screen',None)
            if point is not None:
                self._target_window=self._api.WindowFromPoint(self._Point(*point))
                if not self._target_window:raise RuntimeError('드래그 대상 창을 확인하지 못했습니다.')
            self._release_seen.clear()
            self._send(0x0002)
            self._pressed = True

    def wheel(self, delta):
        with self._lock:
            self._send(0x0800, mouse_data=delta & 0xffffffff)

    def _release_window_button(self):
        target=getattr(self,'_target_window',None)
        point=getattr(self,'_last_screen',None)
        if not target or point is None or not self._api.IsWindow(target):return
        local=self._Point(*point)
        if not self._api.ScreenToClient(target,ctypes.byref(local)):
            raise RuntimeError('마우스 해제 위치를 확인하지 못했습니다.')
        position=(int(local.x)&0xffff)|((int(local.y)&0xffff)<<16)
        # Release the same child window that received DOWN; never send a new DOWN.
        if not self._api.PostMessageW(target,0x0202,0,position):
            raise RuntimeError('게임 창의 마우스 해제 전달에 실패했습니다.')
        diagnostics=getattr(self,'_release_diagnostics',None)
        if isinstance(diagnostics,dict):diagnostics['window_up_posted']=True

    def release_again(self):
        with self._lock:
            if self._pressed:return  # Active gesture is owned by close().
            self._api.mouse_event(0x0004,0,0,0,self.INPUT_TAG)
            self._release_window_button()

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
                        seen=getattr(self,'_release_seen',None)
                        if seen is not None:
                            acknowledged=seen.wait(.15)
                            diagnostics=getattr(self,'_release_diagnostics',None)
                            if isinstance(diagnostics,dict):diagnostics['native_up_seen']=bool(acknowledged)
                        self._release_window_button()
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


SCRCPY_SERVER_SHA256='958f0944a62f23b1f33a16e9eb14844c1a04b882ca175a738c16d23cb22b86c0'


SCRCPY_LICENSE='scrcpy v3.1 — Copyright (C) 2018 Genymobile\nSource: https://github.com/Genymobile/scrcpy/tree/v3.1\nUnmodified scrcpy-server v3.1 binary; PAD0 supplies a separate control client.\n\n\n                                 Apache License\n                           Version 2.0, January 2004\n                        http://www.apache.org/licenses/\n\n   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n\n   1. Definitions.\n\n      "License" shall mean the terms and conditions for use, reproduction,\n      and distribution as defined by Sections 1 through 9 of this document.\n\n      "Licensor" shall mean the copyright owner or entity authorized by\n      the copyright owner that is granting the License.\n\n      "Legal Entity" shall mean the union of the acting entity and all\n      other entities that control, are controlled by, or are under common\n      control with that entity. For the purposes of this definition,\n      "control" means (i) the power, direct or indirect, to cause the\n      direction or management of such entity, whether by contract or\n      otherwise, or (ii) ownership of fifty percent (50%) or more of the\n      outstanding shares, or (iii) beneficial ownership of such entity.\n\n      "You" (or "Your") shall mean an individual or Legal Entity\n      exercising permissions granted by this License.\n\n      "Source" form shall mean the preferred form for making modifications,\n      including but not limited to software source code, documentation\n      source, and configuration files.\n\n      "Object" form shall mean any form resulting from mechanical\n      transformation or translation of a Source form, including but\n      not limited to compiled object code, generated documentation,\n      and conversions to other media types.\n\n      "Work" shall mean the work of authorship, whether in Source or\n      Object form, made available under the License, as indicated by a\n      copyright notice that is included in or attached to the work\n      (an example is provided in the Appendix below).\n\n      "Derivative Works" shall mean any work, whether in Source or Object\n      form, that is based on (or derived from) the Work and for which the\n      editorial revisions, annotations, elaborations, or other modifications\n      represent, as a whole, an original work of authorship. For the purposes\n      of this License, Derivative Works shall not include works that remain\n      separable from, or merely link (or bind by name) to the interfaces of,\n      the Work and Derivative Works thereof.\n\n      "Contribution" shall mean any work of authorship, including\n      the original version of the Work and any modifications or additions\n      to that Work or Derivative Works thereof, that is intentionally\n      submitted to Licensor for inclusion in the Work by the copyright owner\n      or by an individual or Legal Entity authorized to submit on behalf of\n      the copyright owner. For the purposes of this definition, "submitted"\n      means any form of electronic, verbal, or written communication sent\n      to the Licensor or its representatives, including but not limited to\n      communication on electronic mailing lists, source code control systems,\n      and issue tracking systems that are managed by, or on behalf of, the\n      Licensor for the purpose of discussing and improving the Work, but\n      excluding communication that is conspicuously marked or otherwise\n      designated in writing by the copyright owner as "Not a Contribution."\n\n      "Contributor" shall mean Licensor and any individual or Legal Entity\n      on behalf of whom a Contribution has been received by Licensor and\n      subsequently incorporated within the Work.\n\n   2. Grant of Copyright License. Subject to the terms and conditions of\n      this License, each Contributor hereby grants to You a perpetual,\n      worldwide, non-exclusive, no-charge, royalty-free, irrevocable\n      copyright license to reproduce, prepare Derivative Works of,\n      publicly display, publicly perform, sublicense, and distribute the\n      Work and such Derivative Works in Source or Object form.\n\n   3. Grant of Patent License. Subject to the terms and conditions of\n      this License, each Contributor hereby grants to You a perpetual,\n      worldwide, non-exclusive, no-charge, royalty-free, irrevocable\n      (except as stated in this section) patent license to make, have made,\n      use, offer to sell, sell, import, and otherwise transfer the Work,\n      where such license applies only to those patent claims licensable\n      by such Contributor that are necessarily infringed by their\n      Contribution(s) alone or by combination of their Contribution(s)\n      with the Work to which such Contribution(s) was submitted. If You\n      institute patent litigation against any entity (including a\n      cross-claim or counterclaim in a lawsuit) alleging that the Work\n      or a Contribution incorporated within the Work constitutes direct\n      or contributory patent infringement, then any patent licenses\n      granted to You under this License for that Work shall terminate\n      as of the date such litigation is filed.\n\n   4. Redistribution. You may reproduce and distribute copies of the\n      Work or Derivative Works thereof in any medium, with or without\n      modifications, and in Source or Object form, provided that You\n      meet the following conditions:\n\n      (a) You must give any other recipients of the Work or\n          Derivative Works a copy of this License; and\n\n      (b) You must cause any modified files to carry prominent notices\n          stating that You changed the files; and\n\n      (c) You must retain, in the Source form of any Derivative Works\n          that You distribute, all copyright, patent, trademark, and\n          attribution notices from the Source form of the Work,\n          excluding those notices that do not pertain to any part of\n          the Derivative Works; and\n\n      (d) If the Work includes a "NOTICE" text file as part of its\n          distribution, then any Derivative Works that You distribute must\n          include a readable copy of the attribution notices contained\n          within such NOTICE file, excluding those notices that do not\n          pertain to any part of the Derivative Works, in at least one\n          of the following places: within a NOTICE text file distributed\n          as part of the Derivative Works; within the Source form or\n          documentation, if provided along with the Derivative Works; or,\n          within a display generated by the Derivative Works, if and\n          wherever such third-party notices normally appear. The contents\n          of the NOTICE file are for informational purposes only and\n          do not modify the License. You may add Your own attribution\n          notices within Derivative Works that You distribute, alongside\n          or as an addendum to the NOTICE text from the Work, provided\n          that such additional attribution notices cannot be construed\n          as modifying the License.\n\n      You may add Your own copyright statement to Your modifications and\n      may provide additional or different license terms and conditions\n      for use, reproduction, or distribution of Your modifications, or\n      for any such Derivative Works as a whole, provided Your use,\n      reproduction, and distribution of the Work otherwise complies with\n      the conditions stated in this License.\n\n   5. Submission of Contributions. Unless You explicitly state otherwise,\n      any Contribution intentionally submitted for inclusion in the Work\n      by You to the Licensor shall be under the terms and conditions of\n      this License, without any additional terms or conditions.\n      Notwithstanding the above, nothing herein shall supersede or modify\n      the terms of any separate license agreement you may have executed\n      with Licensor regarding such Contributions.\n\n   6. Trademarks. This License does not grant permission to use the trade\n      names, trademarks, service marks, or product names of the Licensor,\n      except as required for reasonable and customary use in describing the\n      origin of the Work and reproducing the content of the NOTICE file.\n\n   7. Disclaimer of Warranty. Unless required by applicable law or\n      agreed to in writing, Licensor provides the Work (and each\n      Contributor provides its Contributions) on an "AS IS" BASIS,\n      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or\n      implied, including, without limitation, any warranties or conditions\n      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A\n      PARTICULAR PURPOSE. You are solely responsible for determining the\n      appropriateness of using or redistributing the Work and assume any\n      risks associated with Your exercise of permissions under this License.\n\n   8. Limitation of Liability. In no event and under no legal theory,\n      whether in tort (including negligence), contract, or otherwise,\n      unless required by applicable law (such as deliberate and grossly\n      negligent acts) or agreed to in writing, shall any Contributor be\n      liable to You for damages, including any direct, indirect, special,\n      incidental, or consequential damages of any character arising as a\n      result of this License or out of the use or inability to use the\n      Work (including but not limited to damages for loss of goodwill,\n      work stoppage, computer failure or malfunction, or any and all\n      other commercial damages or losses), even if such Contributor\n      has been advised of the possibility of such damages.\n\n   9. Accepting Warranty or Additional Liability. While redistributing\n      the Work or Derivative Works thereof, You may choose to offer,\n      and charge a fee for, acceptance of support, warranty, indemnity,\n      or other liability obligations and/or rights consistent with this\n      License. However, in accepting such obligations, You may act only\n      on Your own behalf and on Your sole responsibility, not on behalf\n      of any other Contributor, and only if You agree to indemnify,\n      defend, and hold each Contributor harmless for any liability\n      incurred by, or claims asserted against, such Contributor by reason\n      of your accepting any such warranty or additional liability.\n\n   END OF TERMS AND CONDITIONS\n\n   APPENDIX: How to apply the Apache License to your work.\n\n      To apply the Apache License to your work, attach the following\n      boilerplate notice, with the fields enclosed by brackets "[]"\n      replaced with your own identifying information. (Don\'t include\n      the brackets!)  The text should be enclosed in the appropriate\n      comment syntax for the file format. We also recommend that a\n      file or class name and description of purpose be included on the\n      same "printed page" as the copyright notice for easier\n      identification within third-party archives.\n\n   Copyright (C) 2018 Genymobile\n   Copyright (C) 2018-2024 Romain Vimont\n\n   Licensed under the Apache License, Version 2.0 (the "License");\n   you may not use this file except in compliance with the License.\n   You may obtain a copy of the License at\n\n       http://www.apache.org/licenses/LICENSE-2.0\n\n   Unless required by applicable law or agreed to in writing, software\n   distributed under the License is distributed on an "AS IS" BASIS,\n   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n   See the License for the specific language governing permissions and\n   limitations under the License.\n\n'


SCRCPY_SERVER_B85='P)h>@000002mm1nAp(abJ{dCr001}u0021v002!zR6#9CPDU?dZ*4DOZe((AX=E>Ab!lv5FK2RLWNc+GVQ_FQZDn*}WMOn+E^u;haAk6IX=QUuEGY0zElEsCEJ+MYEh^5;&$BhuGvrFlODW3FOmQztOvy<N$SF<F%!5f==$Qi*002-+0|Wp7000O8AqXJ?4gJPKb^rhXcmMzZCjbBdO+{2eElEyBFLq^eb7^mGEn{zPbaHQOEop9MZ!UCYcyw@bZ**@fN-fAQ&Me6<%1kX*sOC~oD6T9nNzJuVaQ6%W@^bQ%6LaE=^7BjL3ld8*tQ3?~0)qU#T%AMWT|9%7fGUbo%QA~I^YegG=B6fTCTYoLX{pI3M&{-wh8D(&2B{WChDoVLi53>7M#g4lN#;o@N?f&E08mQ<1ONa400;mf2q6N>pmndbTmb-l3j+WP0001EY+-YAWpgfMWq6d`2bdJa-nQ|o$xCJdMMN@)5?qpkAVH$!3@SN;<O~v(926BK2#87+$r+U>phOWAkR&XUm81f~^4(SSAGg_aJlFf__4BFj>aOmt4%0K!&bDdWKUrLnVybfQI^&)_+GE_wSwC)S``#aI3YNb4)y+1W4-QK1SKXCT{TmjIRr1f0WPbI2jI!fLXr<QYQL3^}sonXNT0~~9qSWs}rP?%AY6jDsn4naPH+(Ad9HnNb^r<=c3=8lDzQ!IrK)O^ul?~6J6l$X$UPCi<LVrxaWPF4<n2$Bsf*)}f50ECcPd$YKsDNJRi#IV0BXBQ`Pu)-JQ~p?=dI4q80L{=6?a>9@(I2BQ78CJ4KEw>n!e`iq6F7r-T*6gc#~p;z`BX}zMJD9N2bhMBFdvJs1S_x>o3I(%uoHW50EclL@wkfXxQ)Ab2w!@Zhm3d*<<SUlU?@Jo0&K!QoWU)GGO%tafU>BE*64(87>Q|Efn7L(z+*o31fE7YOu{Z4#qWrD+^4eQ1=K`SbjJkD!ZPf}&-fE5GtxJB0WYE~s-qs7qcetJJZ51I*5e>9LOsEDBMy~N6OGXUeK8QjF#!v(0^4u^ClJWwQ`r%R(x`!k=!0>Xfn_*^@RL536Gc%SEzk?|umz_O$V}g$Hag)=yo32TfRnfk{S@Z_@}o3r;Z?LpFHFLGY{EfYL9#5IUx-6FG{Z0~z;673n5;gP4~;PrA7eir;IV8zRU8e`1MlGyGG(Xl(HkFOGg9Z^SVB|0g_$^vYY6A$_(C^K$Je-w)VbJ?com~D4`1Ul(&qN5GH8dfn2#^;3*vDH_u<dOdLj;`&=Aeh9Ru(l7GN`u;SMr9?Nd2W0572q-oQwFj789SeJU3!qX|0VO-#gWtig9Uhvd)rRAIEk1T4f?*oTw2hTBL$_*tJyg$yW!lBkIG=#P>32($4i=3@!ABOW*L7ZTvh$G$*uG{tbt!k4&+d(ing4v-E{A`TT$8%@vweK81cVH!Td9L&Wge1kK109}CV0Fog!G9VMOAs6zZ0E(bEUP5_PMQt=fQw+j9?8h%iz>@`;4=<q-I$=0QVjR|CKdvD79Oo`tqBBNd5l-O`B#UExPzJB#ZA`@)T*5s(fG)(bhCFy4mCy*?Fapyt6LT;Zi?AGDVJp7DclaL1aTb?w6MrEA;ldmv$c!Awg90dw5-5YpsEvkbf|lrrZs?04cpIZJ9*gk}ZsPGG^fl_E52j%ij^Hi|7xk&Gn2RI$9T|#o4x>5xV>Nz4%=7FkjKxM=M~)XbJ~11+a08i(a}B}}EW#ONEa6jiF&GPR0C(`ji(IqtItE}iHsNRZN^<=}U3A4eSc*Nkh?r9BM^r*H48&Bd#y(tz|0R}zf~bmS=!LPEhfO$)OUO~0;|q1s3zP8$_TW51WjGE|0(J2QhGH@nVKa{4GJIvZ=Ak*pVku4{bve#!bi#Ysh+mQ7W%fI&paX{Ea~#7TNLij^5Ir#in{f|MR^W9O<FE_|aRs?6vTTgQVjRHVh^xfC5k_JIuAmS%WBu?IuHfk^oQIf<Ex3)+udomBF}}k+JX@95EzH0f<f+E_h8Z}71Qe*w`HU6#6_3^6m_}F3#(Bin^r=_yCO*Yp+(FJ-9Jd&T`8a^vc&aw%1Nvhjjv#X#`T>ja6MS{q4%9+_%*0Oojtup<?w}3E<13s(*7|G@Mq?>XA<}^Tjp6tfX&Z81fo_<MJ@^Yb8!--_;S|y|=9tC=`~dYTeTdFjjk8Gc8n1C^g=zQ>5AfXUtOq{FDP(QJ>j$Rd01{B3DaSJ2!#3Q+W6e0W@H&QI7B*lDzQZwGK>{Mp=^s=^6ZFFe_zYj+7_KASf_rO}Lu2&DyO@iwaRe9fH`2G{T7%kXh29vC+1P;#xQ{HYI1ceLTHtNW!)~0w4Ww*M-=GFsVHhT38Mfm(0&O_{Q3-7^3ZGy%;_(o%ZCQVmK_m3SG%Uk*{EXX3(T;mJynt8mI=W#P-ot!+iSKa{50Sn-?V>HlV-3zgcVL;QhEAA;{rD3ZJ90ds9j0Ih;_(ofI{8#Fltu;ALko1nU`)YhScz}&6HeeV?!(V}rV^-z)_4=+upAe07il|l-l7uT$66f3bv)jM;{jbU8_Tg7-(U~U;xcZcTUV|jn1=b-fL%C<pYbcM!`F>_8e~QsDx)pBVK^pZE|y?DcHkgR;4%^r>CU<#KVCvDw8AuejJfy%JFp+|&^@?L;3YIbR}913n1mJBh;8@<x1f4*Od%ukqdc0T2Nq);_Tp#ULIP6t;#!NMsD$chiM|+*=~#d@*od=8z%#wsPw0quuo6GvPv}0}6CfR)Kmk-l1H6IpSb$A9h|9Q-41GC%@d}!v2gYJLKF8NMfM4(@^7o@J(EzQ{9fL6r3-Kkk;|OjeZGYAUWzi5FF%%zS88+YuuHgYP44^NO8M#pqrBD?$(F85f9-Ywx{V)Wh@D8S8Hs;|AtieWX!M8YoA8{WA26BzUP%OYM#3S{aoKNV8saS)J*n%DS7C+)RP9q+daTE8T25}ukYCML_$d0@yfFgJi<xmAR(Ev@*2A$CdZ{jVC#(2!ZQtZYt+(OJ?`V7yYBx;}&x?==B#B|KUCzy{VScOg4fqgiHV>pGgxP;$u8+W0HaLq$3o<LUQL>@egf+&pV@gjQS11!fboWx(qF_ia67=(p5j_@%01WoZ47UL)QhSM%yMk92_2)v6=unIeH6qj%n*YOa=-{M+}DyW6JXoMzcgYFoFF_?@wSc3KV2K#Xucc9+pyhT>zK|U12%czZ4(Hes>67OLZw&N_W;T~c}@EU+DD2O`ff+^U5Q%FF@kz60p8Z)s6e<F4i<Ixx+uncDrGn(y1U39^R*nm@bfJ|dJ&rt=n&=_se1-;Q9Lop7M@fnt5J@(-^uHYv0SgtjA4B1ftrBM|P&>H>lHa^50EX6wP#u1#wO*};Uar7$+q7=%Z3aX(FUPEj2zyJ)#L`=pa?80f>LE7=`AH0ahXo)uHguWPy_b?YLu?+|DGcMzA1Shag$cBO_fhwqv_UMVB7=xKujs5r;*YObHiQLa27hXa&v_e<(!!V4-`<RDy*n>kjhI9A>_mS)!_8;O<8r9Gbi*Ohhko;ZFE9ArTD1%qf0v*r`gYYh9VFA`-D-Pf!uHim{lUOEVkq0GE9j!3{6Ywe4V-HT?HvI3g&B%@7sD#Goj4_ynwb+d_xQ)>JT=P*7FQE=PU>M%PY^=sk9K&VYN9+g8gQ6&phIkdt(Fr{<8dEV7E3pM9a2_`inat}Yvf%~PKvQ(W5KO`XtipPHixc<-7jPZ75SYStBL`kY4YWXKyoqs`fwkC%LpXy=xPb)tKcvr*8PA{)%A*Qup*h;37v9EX%)?Tw#y;Fdx~W`8Q3b6r1~ae_E3q4Ua1cju9H$YFOZWqKk$_awxK~DTltm@fMO$>nAiRh9Sb|m9fGzkIhj1Lf;2Lh@0a8!reFUCEQB*`7biha~!UlYUgE)n&xP?3L&*1z-UX(&Jbj1L?jTu;m%{YS7xQ>U&@Dck7MNkH{&>X!n8gsA&>#!9E@hj9!t}V!eqNsufXovn7jp<m9Jvf5XxPrS#J&S7ro<~{KL{s#|D13??xQ@VVjwj^AbEt_%cmusL0kg3JU*Rau<2KaC>^D4x0;q*n=!+p3kNH@E!?=n+5i^I^bmT;7)I?W|!!mq@lX!@XpK$J>9BQC3dSe1+;xl}W{Wy(35d4&DJo2L`s-rCiVkTDLOMHhzIECMl>@&6n`B4jv(G~+R7BlcU)?o+s;V3TPcicm0F4qfWLq5EOs%V0?=#4iq5wr0HzQTU|f*ZJx&^*=!FQOiLVi3mRBP_%Q{D9NAiu;J2&$3YfB~cMw&>tV-Gpxln?7<oQ4!wZm9C=V26;THr@D`?H9S-6=e#b+kTF84J<Us|zhHe;)G5828upN7G8Fvs`#On-lq6o^N8d{@2CSfM#V->!_ew@TD1Qv7LA`1$lII5yH8loAxVJP0gR4l{>e2e3_jQdFTIj<uqgtBOWmgtL7n1MyujQu!@i@1x>61D?*Q3lme53SJ&J<tz>F$y1GCO*d|?8QYyzTlik7UV}^ltNY1#j9w8H_#3JFc1^)DduA_R$wExU_XxHSKP!yvy7!&^HBm-&=9TA4TCWrQ!y8dunJqT7sqfO*YF1tkYXA4X2^!;Q43u$8MCkutME0BARf06Ue0{Tjv^?7I(Qx3Fcf1k19Pw#tMLu?;0*r2J@{A94qnF;EWuISL;01QZ<vLp*p8#PhCHj-Pnd~Q$hewoAKu3gcx(;rA`YeSGOD5uUPosPz-Uat9IV1t9K>l{!tc0;$XfazPa+RWpb8qI3EHCzx}y&UVkq9jG<=SY*p0LJ3#ry|twUZELpi*HT4;{$coQQq7Vl#!KF2D2iEnTKC-58YA_0N*w2zF)jptAdWl$Zjp(Fa@Exe0aSccuWjKA;z;SHRx$ccO?g5oHH%BY4W=!)TZ7awC8w&D!_L}Vl92%bh6G)8y4g&A0cZTJbl;2zR$;`I=PQ5qFc8=debhGPug!)$zp&#@jmaT-@~3n{;3|DhmCqanIsASU8te2%Yi2$%6UV!z^k3s0jsDxyBxV=%^GD&}GhcHt+S!Zkd=6JIkw3ZXbEqb}N_8&+Z~_TUK4;|gv<ZKnV6G~!SJHPHy2&>s^p4;!%q-{UH7A+Uw}3S>kMJdNj29515=8lf|KVI;<5DrRCKmSPjW!9g6uIsAr)NV}E&g#0Lq(x`wMXo!AzAIq>C7ofIro+2xXq9U51BYI#G*5Ej9;4bubjv*95O|(P@^u`#xi>a80mG}}n@GXwu6mH-)QtV*AARC@VQPe;^jKVvZhmF{aqqvIOxQ}Gtu>Q!5d?<;kcom&62oo_I^RX0Lu?MFSk6Q@r<hVpWl*B7&j?Nf_ahQoE*od7t0kw;B963-Nub>$Q;9Y!*Wmt!;*o&jMiO_EL88RUcilYIVq6_+A1SVrKzQQ3~#$BZTmi>TYD2wW7jvg3@;TVq(Fc(X)7TfVXe#T|oL+p3lhu|sXLtzw08B{_8bizoyi`iI?jrb1nxDMYQ&T(W!E|f+E)JGTe#Sl!yRD6sDSczTu2^Vk$w{RB^5!lQ70pvyjltfiDL=&{fAWX#u?7{*3gtNGW+xQ#4eY^)p2IN2?ltwi)L^E_iZw$v6ypO5)1WT|EJ8=vbaR&)Vzn|AUJcClGh&t$w@mPXgIE_DW7y1Cl0kR?=UPNV7MLo1aSM<RUjKVa`#Rlv{JbuFiBtOVGh1|%GA}E8ZXoxoGj<@k1W?&vxVlxgS9ybvBo_<136hTF_M<;a0a7@4y%*E$eg>Co&$8ZKW@c?NL(LX4F5-5+xXpQa|gt3^0h1iI_IE<fg92I`xbsyDH8}%_!DfJ<eYo#inHD+Ni_F+E`;2^%oA^d>DID#MX6OQ5-j^k&Xz)76KX`I0?IE!<L$9epU3%H0&xQr{fifi}{zvDV?;3jV2HvYh$_zQP%7x(Zt?&ARx@DPeij)o6@1Q0|BVMGvvWJrz_NQqQPjWkG$SfoRGWWZy19P*fy%7iD)vwL_7S&<FdkpnrA3%T($^5R+KM*$SXbBIGB6h;vgMKL^&7f>7}@FGg06kbAUltEdP!^<d-3aE%mKKG1a6}*C~sD|pOftsj=+NguNsE7J!fQD!U`RZ1^hS$*qP0<X^(E=^e3a!xwZP5<x(E%ON32&e?x}Yn%p*!TOC)EqR(Fc9e5B)I!10i2UsKFS5p%{kYkdL+12#mxijK&y@#W;+|1Wd#`co&oK9^S_Xn2ag-5K}P?(=h|`(TSP``FKLj!6)GDz2fb;;;psfZL;Dmuj1{i;;pEC#*?>!innu$w_=L7Ns6~Ninl3>w-<`H5sJ49inj=gw*`t@dBv@_;ucwPYpb{|RorGOZVwf=dWu^$CAVCP+ZrXeDT><##Y?{8<yy&0ui|A;@$#j3=~28)C@$}c%dq0Is<;%YAMq28;uwzOXPm%EoWg0G!7n(AbBM=z{E7>>h|9QwtGI^WaUC~s6Sr_1f8bC2g*&*5d-xmo@c;>U2<7K|h7W!O5JU)JL=b~yNRAXpiBw39G)RkBq(gdSz+-qE8Sw-%;Ynmh7Gy<s<Umg3LLNMgym%J*kRJt55YHhFg-{qpP!z@RJc^?PUPMWJfZtIez<GtHXolu!ftF~6)@XyaXovRbfR5;dH_#bf&=uX#9X-$!z0ezd&=>tMQ6>KKDa@s_0~gJd+Fn9AFMalP!Y|WQlyZN@SCaBD%GIPihH?!lPoivIUjj03TPaUt{2Nl9L%Fk*&2qa)c_HPlQeIBEyOh7A+)c{6Dff`_LCQU)e3WuuDW9P{P|6o650LV0%Kf72#tTF*DW{;^N6P8_iEU=~lyiB?`90;L{>0^$@{F%Qxwn+7dzRJMGkpun{iFThPh8L5o_2<LrXS;(W|DUrUt;?+z0-TPeXeI6mV2gI?`d-j<<ZghnRkqo_j=|%?3wq3XC2}xn|q3&?DyGHHtV*4a!AH6m9p9IUrG5Y^P2Gy8NWlyw;8`l%F%XWq@7JtzAy78^URx^a)1~AZBkAVaPy{=c}+Q$l+E;M$czEsKjmxzx7_rU^9Ee|8KnJ9QjU}9O}Tg=aeQ<=ANMTl3C}v4a#@-F37Ousla+E68J}Ion{p002244Zr~TZX{(M^2&5VD>vn*4tEp0w4<+YUand6Z17Rm*cxxVoJOUk`vd*eK97V<2suxES`&o&nGOjFF$SEf87kXW7&aQoeqCrcli@=Q;8uI#VpJ#9WOZLW~=5^2+vzm#%u<<3*nPD!(GWqe6bzm<|UzouNq9NTi7nDS9sZaGgMmXmpRQ?6)^bLpQ-vOZ>f6?3f0yftOsol*`2-S|2(e!Y~<wd-3crwO|Ctm`=*OnHo~b3IQ#H}I^RDQA@S8+yjKFvoDvwPVU-<oIdf>4(;y>Dw!JoSSlIPr18FJpOyi_HLlu*Nm6t_VaA7DOZtg8BDppOf$^0tl?x689&Z5ew=6BW_ZTW@QnY+(+@K}$Hq)qx8+i9L;EYG+@11jDG#H(M#_^YZ<O*h%3n%(uB`t|S?(SwFK4{jcc00=GwsZicGgSzpd9n_RO0!&!0hv&>%+w=@w`}~+-t{9DQ}TJSt{GLj`DJIe9C@YN%@4V+e&HQw7E(pUQ12cT>niwt37?V+S7+?Jmc4R#;^7C=UUHtuJuf_&NHtm$IJSxljF@yv)<FrdX;#s*x(tz!PEW*Y5yB3UzK*Y%k+z+Z0<=GOZh(2d@kic$o0c^Pn)J(L9PR)oF<ewKBK3cE##K9)3aSWJ=?p>Gk&*c{65e4eV*|@%J?s&Z0?tqNqM2H+mD{<PkGuo<r#m~GybY){BNG|zj?-Am+_{2!&AN~Wz!FTk$Gi%?|7!U=PCc~DL?R(A9~6uv@36xa-6i8S|?7ER?6nw$)Mf2W5z$G6W8CAOEK?4@^Ms<b~1XVG3DqsKH+KqNu9V|nNgj2O`m7cuFrQ!xxO45*|j@1%z2tqyYtk(R!Tcdr5rteOu0!YvD_x)+BD_Ply^zFw`ZDRA$KmD@)+-Uf8xAj{E5>{_Ov<2GyOtOdAX+?FX#2Mp8hfA=(V(<r+?z4yim#&q#xpB8<$BrdQ6(~`cPtdi>JKXQ;xninexF<V%fY-?UeFS=^s;$_e^u$Q@-OVn-6B9?MK4ydK)LlOmuv_Ke24K*KAiI*{(%WHrIzQq}*SwjfFh>y^v14J{Qu7_f&;E$5>HM9~SfUb8)1R^(^6;zNF_kH06w8H~uBharKg{gDIExtWRmr`j~Py=KW5}lRV?|GJdm^<D~sEp7kv2Sx-|gCF@z<^p8wm(KG!ko^n;sdRFxu|E63$?Dml<*AFL-Z{jHr^GqK-KC5}!ukKm5nx-G+_%Y=+(oSu3mU2DxmUXW0X{V8=omb5<rrk8J%lPePy)a1HX|COCZnQi`#<wzkE9KU*{$ELXrW_A#Oh3wcn)0-8;&`)-=6!oxS?)(tHvMMa%eRy9*QIQZ$s1ChLpy&+*&LsLN!iSMSIWzo=AM+zx%qc=JmvdRHup9Uq`aQ#6Qavv{6i`4HXn8dT=^(vzm(%C`=orEvX*in;^Y02E2r_4GkVI|D2Jt-mvTtTMLpA0@RX~2%H}<Lbl&=mk4V|P2gxAi7L0#P${jrOn)|D0xi{m3QXWM4aVeYgD5I3eF#d_?yp%IZ*}MmdmKQR<sFaseep<@wJ=1UTly`fkIY>EQv~MZrkM^x+`s<W4OZkpxUi0B|v>b^^oIZ`GoY6B)HqUr-eU7%7m+{%892b+g4y8QvR`HZ;d&-SH<rb6+MEl&+PH)e0hk3@2@r<ABDbMtj&HIJudd_8hPAM<(EO$NS=c4`WnRmJN^s~25f{EjgGX7a9pNUCa)>X=RqsO+VY(DJ&XP;`%KJ}Cj>cn<Zc&4xIIcCmy)-8Rq#ByfO^yd9ew45th;xU%rGkwuyp8e&S*If7h*>|4lYkSI#J>?djdClih(Q*e*``ta$toQWcAWu6ZJkw0@lqV-k?30<E@pC=pcu$*4Jmby1cXZy>o@IUMSvT`OF;2=mJkuQZw3E%3IR1oZns~~EqWj%5{=R2kbDtkA2a+e2$9VcVg=c*F<caH?*)u+ur<~tYuHs897ftTjr^!A0(NnHXxnOiZdfKV&X=j*co!c-?Rw;K*o_M|wqFf}pEuQwxXNk{5j{(o|u-nt-WY2PEdgh($8Nb9+Hup$5qWjS^{SHrgjAvO#J?F??Z#!(`lhOU_>1Xqrn<ct`J==Jda`x!;lX9-;eoWz0xue(26p4MFF@@)v8AvSW@{Bk4sL|=oJ!<sW%g;20qx;S?eU%i6>wnR+U9~;q8>dLTzP0d_JA0<-PdQJtA3W`w&t0SC37+XEQ!W<09(t}P=05PL=r!MS4A1n;YwrD`WpkfwzFTOo-91S~DVuxdN>VoW9+jnR?l-DP+1ziuB4u;GQB}%wSynYEo6p~>OWAy`Qd7#SnWl!6w@|JnW%K%8JGv}SdA&|7oA({j@^0p>FXe-t@=?l-q<n^QV<}&y+(62AC^wXHB&AQ)jjn%8VmUM8>q$A6XPP+5b)wS;6W7OF)1qZ_J&Bf!r%WuHd&_9KY|6xSuHu=-oKw;9V?1ph_4I=|UtX1RZO^>sc>AXhLy7G)@hqzi<=3M7F_<{Mb4qv5+(9Sa^LLPAV3m}6r*!wa9pt!;mWO%9=au{Ojym!FypyN=hEBX!H|5=Ouin`+O;?@xzR8rw$hvj+Ow+^Djww%)buiyIOq1#Rc&6{`Dfjb~`+Ld*J>@~3@?cMSh^IW%vkt>`;(MI8JmcSz{S~X~s87O5rQ=<y`TQdtH{h$xca8M^qM*ruI%zXb-(|Xts*zfz%y$6M45&W?E(eBP_KvuGHpYA>kj;L6XmW{mImPdCn%{iZpI3cO{R=9nz6_hs!rk&r9<;+jRXE~?&ynR-Mf)DNysD(0w^>EKKvrk{<|~(D{U!tIL$U_Lla$MHGF+8>-E1ezX-b;+$tFumw(*<KoS)H0gDxks+_utB?WkYtH=jMb{^}^>J28H>-{l&~b&~5PH%M;uo6oD={9R@KZZh1B`c2gLR(^FW?6O$I<vGc5F)qiGeV9Ib*yS?}_a~bxmn|e)O16@0E!jr0tz<jN_L3bWJ4$wvd_%IcWEb*H*1xOddp6taRN7=f-SWHfx5+_ju-Y9oIb3}kG@rM(*XH}4!REN}yWtnZZup6a8_s02sD7O78>)t>$70N9^TX8J<`|~k9)6PnHQr`Q^-9ouUhmo+PCxAmxZxjd2GnVr<J2!UtLn2-f6iu{j<-2OU9|b5irDr}spLU7e+p?oRnSeJ+U5q8*5+E3$>tjMq)eArGM{8sn@^~L(%v{3KSgqI*yRw(p_0QShuhqzCWPJgEe*SNmf2jbw#sz*A};esT>VJNaWejW$qytaOHPqIA^C@7T#U;alC>lo$GCQz*u1Km#<+fM7UTM-xy|2HYw~S1((eC}%(vfWfcL~ojgtK}is@&tA4hZEHB}}v>SoG(&(cTdwK+~Tk*us;w)DFkW7C}v<7j7{<R)@F<3CU?KbD*$`H7_YjLc*Ozst$w1YQly_mmT5zKJs5M44}*%r{Y{H))QWiE6s4A2Ip7YDm7Lrr7O%N0qe4{kt;!t_)9-;Yn(M9e$r-bDV!bdpTME$y@>N1WdZ&DY8COWVt491x$`nPXtX?)kSQ^>CbJp*I(GIr;FP8i|S%FYpYdZH~vo<|Ch~;Dk0*A&3X4B^EV5dbldfzY}ZsZ&93)US?_7G{An`(G?{-o*YW0lm*0h5J|A)Uf=$=nbY2NE#hCBU`r7?HgMTagUAC6&W7F-|kJK!?J|C%tYHW=8&MRQQmooW<<WieKHQn!qw@Pl8+#&gm<W9+5lDj3pmHbX}kK|s-eUken4@e%A{9f{q<PVaQ11_gX`hqTN+3cW)*z~JpVK+XzWDCjFHrwm(B_EHt?agX)y~-VN^B0S_>0gxWEYr=h8K=`o{}qmL(^r<^y5uZ5KW8yKPtx2&n5^VC8Kd83JwH}+?eXxj91nA3cn<BG;ZIC|2F>$3bM5i{8G9>F#3ZjP49}J2%w^boH#1kgu1jk-+}Cfir^+5QIaoa%bkpav`H`w&vxOdG^BFxh=;j+|v$?JxcGEYt8C3mk_R?7*CVT5_Ha}N6BW}9<HW#QFHXG^RWW3L==OyL0>5lKY9A!mg%y*~(wZ-ppo8)1eZa>bG{WnkgeV+9DJn8rOEYI}M0@<DgoR<|MCf#xtO8+mEeqX4P>B&sDNLAP7JKIIH_px?4NAeTNPbELo<~!Y>U5~})b<^fL^;E>9J5Cm}on=_w=hT<?o2;s{N6dG=8&t~}liO7r@(Z@xtnU}*{0N%wf!+RFs@m%JZ3cM1?DCl8amk-;mezj-Og2*o?feat+1{nn@5`Ak*1RWRy=VDNrqJgDCd;V{HeElN;hI5L-y+5hw<1@_`mCUx^xEZ6$zhViCHwhZ_V>H_PWsJv*6up9Qtp3OGCmYzzOxS4?<7{q@nv$V<Vnc^VVC_QCO6pox79M;Y8hU`UB9``t>NF&ewSq=FZ<2+*!^w&TG@`Z{M%5wY$VxO@>R(<{4P67c9HCA({0Z>v)(Z-&3)v0+5hWVukjJ{owi%v2I@10UG`$SjT|>U{Vsdi+@%T!O{UNXY?juKhfTWsi;Z$Wu}Rw7L^~zza9N!!#tkQzOd*-lW+z=<hAYIl>FdjI1M*9`zuc_0+2e0BYiN$YEi$}CHMQ59tunk->P>!Uv!B{y^KHG?W>dY7+{XKhnKmn_Pi$6E^KE{u4%@7yQiM&`Rw-@vQ$=ic(syjO*OekJ|F-$03dEQUsI)QW`}^(oeeHJTQzHW|Z%F!sE(c4_klYnC-|r8!$H7ki?IwB0Z@%B>{wi$p8C{k3_sII~<DH_Jejopuell4~@=eK`ewXD~{sGRih05h3n{j%8-{nf1`&35S|AFCUewWK7SNP5I0e&@Aaxd-uB-i<)DvLIGO!BxoX|MaoRek*m`LhiFEW;;c_@o>!C+XKe{U+Ug{7IH@*k&#j6ExXSC$s79&rh=bDQ&v@@{{bRSex#?{3QGTDVy&8`y{UmBW3#SQoqAyKwY-EP2CT<^~n@=<CodouJXsY>CN@<6z|4Of1l#tcO=dCohGZ>bm!eE<~RH4G|TM~F<DX#CeNsIw!hBEb@Ug8O+Wm?a24{L^oPk)ev|IHb58xGCfMw(-nZ$F%X8|i8f|lfnoaxh>b!0LytH$k`C|eulSw9*Od*+4GL>X%$uyE_C1WMiNv5~y_SbpoceC8gHeJ7;XT9FFc}`8Txm~>%bn{P@;ng->|C{mi!*0F>VK=<YrhC6*#y=Tx%Q5dy&a2;5%@{XaE5_Bgv{_JfB7apEY=2yk{<t9PdqLLsg1mpbAnSX9_vxc;x_-aF6@9!-xBeGoJuk?5U6AABf~>~{S^foC{v~zM?w`x*id~P(a=&!N^shD<r{`)n{IcKV8@i(34Ue;V$=+vQk^6izzLHJ%KJtp(_nYBkHr@NgD;#m=xVysf_qR=VpMFK|FRsY@xT|W2J)W=fZ+FQS+GR_d?mp(KywAES$M-dPUArdR{~NEirk&sTceZ3YZPHDDU6s@qgDwk%Ot#krZPrzbq`pJM<&}tePAH&e2TYdKp9S3T=#UKCbn}_<+ahlKw-NJ9QJkKtTuzgmE;&Q;BgvVPvm|T!T`rScF1f;HNgWKD45*OJ>vs7!W&3V&T`pypUq|JoKW_1U);x=Mi}|L8Tuvu%t3T~=@R#|#DQKQ`3fgBd?odC#4nLz!yJoms#615Lu%88*94tA+W=WktY|>3{hChwCToEzPN|n@g!zNun-DlW5k8q#${xD!Nw@MQ<c|+y5c}Klt^PVbfr~g|$6EXQf-Lm;m^@%ahU6s_uf+pMRuYzuPv*cD8zs;swZi4hfg4}l{$bDA=)29l#oGLk8a!tfM(^XRc7Bm^Kzdvq<mxNqa2)pqWZMx-{@#$jR`1CR6|0Rpl86)NyuoQY_(B<v0dEP6d-fTXXl*}nvSf|!G!sgkPGWuQC_hns9pZ1&QMXKo+>9=OOnXckD&x03L3&SRhsjnj@d#WrfFRUtS^NeeRIb6P|?gmVjP~Sw%^WUe{cOmn<cPZ7yW<+(h`I6dBdnwc<J6}rG(PnDNw9;-`)mt41xZzQ3Pplek$H&U}*nh@9P#r?%^IbPzI;l^m%B$~fPSi&NuHH<aPKA9}!sc0WH=IG8vh!t7mDP-Z$qH(g&5CMC#C$&N+I^f?F!ONy<0`*yYqPTIDcL7vJ{PWKr+b3!%ouiQ?kk^^{&`XrR(nGx@9X_GE9;gKlQr~Go1OLch#Oyy`7+CVPs#GLNM>ct_e9L+%-7VPLGwKNbGnVq{OUW#=alJkk<SH9)=`aZ_SLTj&2#ECRQiy~ioQ%Xf7RDm-#n~e?T~qPeVSenFj?2`&!<%%{c*%RuRhJTn~$rd$^7aCUz3o@;=ZOfOZX;{1ym!|KWLs^AEru&OuFqX$oz*Q<~eq^esStm+kTvCtbU4^=h)r&!s>N9zOZ^tEsVHaFL@who=11n6;rM4bj4J2^_d;tRLzx~Z?l>DB4VCLkEmoJlPT3$n~&N3@H|^o)z-W9GkHB?KCgG}7yl>g+3hbO!zFoFRUv4yqiQ7C+~z3N(dOf7xXn%~Z^&d;-O}a*HBN@#u~}PJjF@!GFUhM<D;e%#v%g*-!@o#gl)Mu$&&@wjPla5Lvsux%%VvTu9d=novZ`bQ$ydpj<oJ3?74tn4GSAz)?JJ{B>x~hUXY`jg&+9EVFX`{evU0wZRbAAWfJt|pm6Q3){WCmLSG0LtRS%gwq1xCysoo2@=}t#no*`eBcFU`DzK?<?yQx_=d#KqqQ~5r&nc6qUW_R_e%~;=OHq-j%O8o+xDSZpc$|{X-9{CE}`)kN#4ShlKV#tlZBzf6pL|w6&TwRs%*Cc<l*;fB<v!=c-<8R3LTQ*ax+tSV-HXo`#Z8p?@$?zQ+zAMw;lXm{L*-Sr>_7h}1AKGl7x#-&Iw9UHOXS0^}lT~RqNLG{mP))9LHD$P_3h137lV^14h{;R399c`fW9w_F?7pWNt}XkaHur5qLnbThCSj8ax^lz~o8zz!?+?q{4EyrMnExkYfEpGuIZ!PKx#5jrlN=8bmvNG&od)WF{v=>>pY9kj|Gz|D-79GFlwN9cklGhE8BsNDeP!L)W;H!D#!WX&as=6kJFiyE*I2!$M>71HYNyXfOcquz$C&TM9;gwlR}+?Jj?X6a$9X&av$`5Knay4gny|d15jWgF#$<Mz&7>cjac5($yUn<>c`4$C+u3|ZS7*BBDzAN?+gv?k-{+d)EMb?GV%+#HG3NWXA*v4bE!B^<-Im<JA7Xu4vHaI1e+ilYFC?PMkZol7ZRGW>4cCj~Ve|c+dtGcR<J-#XVOyzhC-b$F`gUr#eO+uPuZ!*ena&+A?Pa)w%-2EY>p;K%WXCts$HH#-XUWqxYv?mJo9p<n`To$YS4Ww@qpVj)X{V#K(^1;#DD8BVb~;Kson$>aNjsg?TXy|B$@+Ja_Bu&>ous`^(q1QNuhT#6x$S&I+I@rfF=GQJ-`C@8exSz(-1s^XH#{oF)sK!b-*39*b(ZCImgRMp<#m?jb(ZCIk>zxe_3vV?H(~SrsOz7uGQO+yPgkk$Ci8WZ`floNyWQPnySx1}o!g%7GTc+W=&KSkIYNzLxR>f++vzRs_2ymsyomW;)eZMmBkg(CPj#`w{p9tiztj&<cl2fI2g-F|pqg&a|AF#4^rlQVNX?L}tOrKS_nMV;1N;5wOgr8Tmk7IT5_aR;+I*nr*{m<~&D3w&yrcKp9HTBud&TMhA>6@D4x7v%Syb|Q$rmJxOO}+ZDOq2#fn*cO7LqL`TS>N)d{gqQ<X@6^Bz@XtLCIp0#U;y2R*<YL*;{gu<hzoSBvbfY=9bJO`Lr*(-cw|{XnrU;HEh0D{>b+KP+n=@54ilmW-?#vh|4xMJL%H)`)9Y`hRJ>%rpDUq-7sG1AKH9T`9dbksOM}(R4JPe)I^(A)cZ30LCAbR>e?GF?V0a&6M`lawBOc8RKVtV6||X8heEDCZ1aJN*qo@6+uWtn+1#Tt$o!AX^ciLPCqnM~;><Res4O-YtE@6z4#`}SaW-F3MWwwmGQNUjU&(%w{UrxT4wQV;<~M3^$Zh9a(%#!P7pn0x-N%x1q<*R7G8w-@a;4-d$<>lyOMWAHSn`PEF=_93$bFywv-Il;>7SD}52!OTeWS2@Zs4BH@6<z^L+$_QJDfWSZ8M|-HW#TB5%)YmTJkN~uWzealJ0sjLdK6!zw75?%=h8$esd)C9|cTuza22&i@W1vluSR0>p}6b$=`Jwo3nMNup2+m=1e_0V!l6j^Np7IOzsJoY^&-A-SGF4ha`WnSyp`;a>M3+WDJ=jXfmID{U0OuH)G`bHdfjnr%L*swds~SPW>X|C#WBTCL^kO$m9bxMsl3Zt?EF?O;;_(wPWrR#!I{7xihI3G~dU&b|<R&cKk$HuZg_U*SDETr3jgH>p4-@bE3R&F?mDsj^sU?HS}{a=KJ|iZM*Nt_4!>HepiMksn6{2BpH5RePV~-S92u?>yu%V1NB*(Z|Vy+hv<u8^L@VCzaOZK_VskKYHWum^G<l29d572+wbw`*zr@;3woj*{zShMaKrD~?5tV_O?FV7Y*yBbC70M7D&OzB*O4h|fNmw(+U9iKRfhkPVbf1jq+d*Sx8M8UvFq`n^z(<@Y1Oys`u9WW-w$QJscN2`eya4(H1)9^o+j%tjpcL;yWs{AH+(VThRyubRX;oZbZLKvDrJwS8M5DJsI#_y27AaHPct|#%Y{uw)XlIPUoFPXXO5qb)Z2FYkJw|4A|{vU_af$dZFgMFRPU*VL6^-WTibkJwY51_wX-=*wYNE0b+GxN>S}Yk>L&F)ZGND7OFMmRPEmbrM$})DcO+AXTo$(Ztnc}d``-OE8Q)y8yW~il33`I$6q|+BM>bcgSu+18GW}f1MKawQn-A>gO=kQ7nf^x^J{mIL<GX&IrGBveGmC!vJ8Uw!dSEl3FCpy42O};+l3~e+WQ@&~DwPbUmP{iVYx99hXY--tB$Zw=gXCk9=6IMb@4G%$izVH0Hb;GKhv&%fCvu*BBEz4mMRxd8`8?}0^{O5IOuZ&KM@<Nrd{UY7ZLajgT(!{F&s8t!v3C4yd!Cwl^BmV)`t7dGYC31gJ(o~9<i>Be`KdZ+GeIX0yZQ3ke5jwX8SoXb>Gu`1nbG&0%{(g3X2@4a=4%pm%jsxyy6$fCl<s5mRefHj`^~2E6^WSCzM>H~zGuYEKf-2C-y)lL^eUS>^$!u(?!|~}_p0PIng5Q>8LFFoUcsF|^Eh(q22IXVuiN}c^|R@Y=Xr7*&y(YCo*aMk<oKJfzOe1gm;JOrEwIB2WP45ako>@=JAW6*b!UOB*FtsJ&bN?y^FDNu?7u~_o{Ln1ni_DKCup*WZW46ELnT9DmrumFEEeOQgD~?iX8p}}EM~n2*<7Y(gxvEIpV&;$jl(9F>zxskRrM~Lcl3`D^ZbNc50mD(mCreH|FoG#%?!EB6*j4SHEc%I9-DEhtbN{Mot=LPN3?lAyoCF<(;<^9)Z(yx4#U=ep|Yw|Ve|Zi+y14}ze`nq-$VPH#dALM11C#44xY2QPm<Spn+1G3ZKhJ~Z98@JRhvz9i-?>4r5KZ~^$>EIoR7=Z2ub(3)^gsVRkV3r#e_`0s|wldqH5TDsJ4b&{VAKD+0Pf2)1G-A(X?|aVxGfr+p$9W&t%b{%P%Fj*o@OZ+MK6;viU&e37Jf;iiO<pOExR19ySZ;UN#@|JsUO|wCCXp`oE6NeD*m^GyJSw51&1cR?u&KZRYSTwi)AFWi#HMFJ^k5eePtg${b^!7rAQJd!^jxtd#rxl^ib(!{)h=*KPeOxt}&!QL?6FZ^_SXu2x$@=J^rVAFI_WNjKl>f9ex-s<6w{Ve>r6OZIhijr8Xlb;i!Oh9k_pUYdIIdbw7Ww$rVZ>DJ2Y?OKkk+d-2{Rhp1Vw?1oW|CO*Cz8QA))uf&3_W6<;cK&rLt8aPGWJH|{xr~>5ENt?D%59%Rao-QDS1TmleCy?WThA6Px9R3vPmlD8m~``RkoDdm%iSRJZ;;Q6Ob(a)Mbfmhf$gYibB!8gbG2>P<N%wms9d&vx15dYplyGnx}}c?UH)uyyLvigva%jvr@N!yw7FjovUyv-W%Ccom3oYQuEnkQCiRtVcav(Prv=^k++mmI^M)^FznjbzH2Jh{VKcAlVl%bsVKbm#4ViqXzLd<ya=w!D^(%GF=GU^EuVp!3%W^iWjkbQX>Y{%Mo9Ai1w8LBEI=xkGvcp?tc$->phqtK>l3eG)E}Pg~tvlFUt9#n~OZSy}_q@zX{f5mo(%xwMd4{RK8!^wpxc=F$Ua{?O=L)gMJ~y*o-3YpTBJ8r1&CP1P%`Iw!&9BsE$t^ZFsaI{g8|;4GP7n37xls+b`L*PI{iJ<vr;yL3+y7>|0x@oRjbdCoePdkvCU?kw+M&WKTi9fb%45eDkPNFL<Tpxd^FC;&Dr3*PowA)f<^0@9518}G)SL5Zm%N_tlJ`%$<hrs;-aqYD7wvqz8Q;wgUr|4W+;c+aed}(PoRaPOR@U!Z^}MfA$mCo4bH;xs<G<s5<eH$#)OxMtx}bT!Xq2trqfY3J4DXfc_o^p-Wx^((lJslSPkZI_;l1j6J=o?UoBLE*yS@8ld-uuq?vv}tKDM};OlP)xzbt1z*Wdnj_^#wVn+K$w12WwKj&t+*vKbx`bi*@jUXwhf&F9Sr)Ym$X%}l<^VK=@~jLR-DZhDgkrQL%nmnzNt-^=j#GJHse57F=U0w!a$%LnRjn<@2u8Gc|hnNE;lZRdZe9t)ZD>1H;c*WcUBtXhW6b5^eXA5=)M51PEJH`om8`$0F}>}NCnW%lD?b;VA9SVi<3)E|-gkErL=4<YlMR#iLvBS*x|pviC5t)P2;OWXPcT`z2Mmzo(d&vUu^>7TfsMq<o!T|e33qcVI<hL1^q99LWH@Nv~s{}?ve%f2r*!@q{j^Ioq0XVq074!b-OHqU=;w&PEz9{P|S?yi3bo9C?D{yiz5lb%$i?fv*kRnLBJds0<buiA94e<$U7X0oKs1YOo<#P^2HEWXY*bEqz1_uSYuncjR4b5gE@CcE3bsCwB9>UV9fQ<H2e-&UL5^d6hj?0xA;xi3AX%Gu-ml&t?Lb<VE;Db56QzjBJ>y=sgbt`_5_uWp|YbNlPG`93M=hR=mvy646Q==~A%yx2B7-5K`J(Xe@5%nkn{pSz!B`V(RE9GF}FIcfKt+G_jH4Cf4*T&>oHOxDo7?DQ-3!HCHwc6-jrbvRyaxAVtKyXU3d^XgkW{=D@6dFlW2(*NhBU(ZXwo|k?-ud=AOY`T6tFa2locbU%g<F9ImZU0wU?gd%y1zGL|)mt?Unsm#*Aj`iX%fBGYzaY!MAj`j?#_M%9-SRKU@-N78FG{~(l771+?O&4iFR5=N-EuC;adt_TcS)9aNtSm>mUBs#b4iwSS?#v%UzXR?E3$o8)Gj;zih4@r4%+9y?B_mL<a3`ZYMA}p=Zd_}Ug7;)Z<|l}3Ps%TMQQJj<b#NLUh8FhTwaysT$Ru1u5v{+$K_Rdow+KnGgswx=Bm8TnCYs;xOU9(dX3}7%zsT)(bEGa-_SE{w$h*4Tqe0v&$U@i&$GE+bqbkWt=<i};e9r%s)F|UvGR63f0On6P3}j3<4SDS(_}T7&aB7p@;>;wnqa59&K26cU%JkfvAfMv`hv}uRsN94Cv`2!dXftzx7oa)+eb{MRhMkW+Pop{-%vN@Ike<Hmxt`~ZZQAfl8r(pORG;qZg`VT_uS77bwlsA?Pc~IiJ0fazO(J#RJ~LnXwto(y2*U8A^V(}t}i*?rn~Onl<WR2>8D$AyxwBI1|gRXZ5H)?XtR%+7BbJB?XlC{R)46M=(j)RI`Wq~YRCVjj!WLKpL^Vq@ppJ7G23}ZHBy~zHnh*P++jIILM99Q%G#W-%1OQ)a`lUCURUkHCi5s?#N-0yw|Q7KwHf2<YICoCk^OpCw&$*VuXtCkZ+GQ;#k=yo;$8V(@veNYcu&^%9<O9(eebdS79o>oRqK!&UN5=9W(HqAJKy8JPa<x3dBi*~=l1vCyaJkb{$~2lA^Y27_Wk00>G%7xp7%NK?gdTOR!@dZ9#Xk&9#Q3O{-|2n{7KEW*<Y=-c~tGNc}$%Rndjx)av#Y1-Urmz3A*90CAZjoQa>AV`HE!Kka=#-tyhAqm&rmwlYQ;=GC_^jZ`tgvQrhXC)O95rNH(<Tj++EIZp`?NHWPH3u*<Yz*Z%J|$LN(2lj(h{ZI0DzZH}|o+lR7U57|z$JrCJ$*KIzj>x5i3mVCped%tOh_t;dv+F_HEbraiO5nnT#Q+0Ej)pUj!S6{;B6kXEhhq@FQ((d!kuy&st#mI1sPSDjNCe3dFnY^oO*mTcV#OT7Rmd%Q~wv4YUS&vL6?IqKd_4%Mlcb}R}-;=DS2TBgL*-j6a;kWJciSGSba@J!Q^QYAN>~Ja>pGseoye^qhXA7C%v5VNxN7HI~E>M=AR=fM<SXr-F+9}R->9jj9(rb5p$-wgdFwgVpg7*F`gU;c*A2xZ)USA*6?tRbW`WHK#QJ+)422DPp^Mzc#U^A00ZZoqkA;Z;Vc$7`|I%0Bj$oxKDyxMK^oEjT8zl-PI2V~a2+T}bY+w+uc|5LL4Pigm@N*0~Wu5T9YSMfH3_WQRi`huN5tIU^8GP_P;r_WA*y%08;M<t7xd|L9M${u6#lAS*%`}_T{$s#Iw#N=f=K9>%t(E*b_RXb=hg<2AJ!)GHdbH<q8z;o;Wj6SQfhurYqu*<HtzMaiywR`UCS?!+tdY1L;!T5aoft^2}E~1wOP2RKbiwo+%m3e<wP~TTyF#U6~eR2A4Nv*O4P3BQ$YzB0Bo9^?FIQH)fo5}Shn+1JoLnhtlA&j@5N5*knR0_NCZ`!QnyAW~n-?aHq=@|PPciLr~FRRTII)~)bF>bnQHdAVog`{5!$$A&kY4mpilW8SmCDTc!m&_pfn50{;LLBFtBPQK(T38>``_1!_?7u-llhyPln*lv9<i?xd8z`)g=nWB<J0j+{^iJCKDXJgWZw5`e=P`=X57onNxQWf*^s<N>Hv6L(=i_|yoTVI>#b~bt<6n^Tthm0bwuMdJ(Q_i^_xRlNQ6+Rv)jnkMXZQT1)R&a=q@@1SzAl%P@ul=lwIghPS1+qx7;w4B=Ci(iHuLC%HlNbZ1YMr8nN$C1Gh&xln(ZHAvy%SGW`a&{+s&c}+03qQgx&P!{~tBuUFOs;*~}%G)jlUzT4&Q`Z068qBW}L7GF`tI^Zayf{Z-if)}L#qtbW?gUtW%b3c8R!6fpUWJw7Yw3i^J;WGeePLPh<oevSDn=|A<ajISc|y~29OhD}~kyTj%;{j&O|1zc7Mn!IZJxvCt;Rizy>-aL<1mGwMlvx4ub81r0rVf|vr{05-gKh?E+eyxViZLb?ObT+$wHFP1BA#8pV@QR(TChgZ^xR$(*)M7ZD9e$v`4V&K!bn8_|*1N8D@2BfA{y^B|QQIH&XfGUNen-$vSD*TFL6f<BFWYp>YarMCM&>DWrf;lE=x@R%OX{8EYjR$_u1o2+X}75?uPN=m7jS9bmo%5<G-rKHyUlf8-vgU>?D5z_>RZTiThK42z7^9SvH3uS!zLfvY^}@cvO$yOB+KZ}!{&DgU)JXTpKGIwYIB|JLwf~-=J!ur_LJ-{IZ$$t<Y37ml0zlU@1E;W;%}n1lZ^h8-cxUWr)?yeKf$MVnLiw1UMH3NI|8PDz0_Br{(#iSN;}^ACe+7EeL6FLz&uBqSl>Ir{ZWc{@A&L*X?T}!ev4)T^;xK&LVX+0^0!F+T<V`W?%HqbS$?$sOX|x?eLK(cqxFZWZ$|xe+V7lX`R4a;u2MgU`kB;s@GO6~)aQTbQy)qD9X-pB)_10UkJNYaEI(SmgnHl4J~fB--|)0=+aq^S|Lo6h{_aWEZ<JX->KjRY7tj0$r9Pnis)f{d^{ijCJ~Q?Gq`sSH{i5|nsh>jq7pz}@IR>KZ*IU|KL;Wi12P8SZ`Y7|;GJbVh+8_8|>YGr1QR?4J(*6`_|1R~1Xn&BYPvh#%Zz)B8r#STqH~-)y^Y2vdw_l#7{xtIs@yu`jiO&C~%s*6?7yTOo4YYa2#IM?%boIle{nVa%^Up-;&(Z$yB>S(E{eSTNYOl=yR+9C5$Uk<v)2{w)&-$J5Y+qlgAK|IL=&7F|^&@4yqV0d`H=ham)oiIBm1Oy*{wnofOa161_2mPmkNoPk)Q?e*?mx4>`Kf>IjH@3j+y9i*kC64LAoaVF%>SJ-uNi(-Tk6g4!>29le((9ZxjrcKo3DP=R_ezmS-(-z{#Vprr2i+F0nh$5+tZu*Ps;ogJ^SYl?VI`$)L*0i9`nDW{`>J&`WH9<yZ=((Na`p3OMQ2#e=o`Unf_dD{`igj^S-NAB~)Vl4(i9s{2$18@BD|UUnBLC|4V&5_1{VT6mS2Tzx|=i-{1193)F}2`t08ii`JX|G5fQ+&#yY4b@NaCm+hM__0y8HZ}w+z=3goG)77Kr$9P%aGt{SI`R4yEjP8H)kJ+D-%^wCj=jQ)NJ-Yu7Df9Q1{HmPP&rDMPgS6k2`n0q^E6MT?8)p0BeX2C|v!y<j)E|-gFT}g{KmM2Pza{l^{-r*}c~}3*ztrcJ`cGwf=_HSu{`31)NvSvg4`3S4`kVR@)Yp;v-_@h%SJ{BeK2kr|v;7hFkC}fm^ADlE8rwfFN&jVyUgv-Hsk+q9|CjaO^s8%sL6Z5inb%j^|6b}BCh33ko9*|h4_xr6S80DylKJP8=>vY1h5A<1FHSQ55IJ>PUU2Pyo}_*#^+lQg6Y6_0|B@u_za{hkB=dja*}gQs#O=Ey^-Gh?f5!ZF8ttdO=u>@Zf0=sp`gc(FWABS@{^g$eGt$2Kef-|cKaBdfnSVu+_RV>)n)>glA4~npB=uP&+gx((uS!xM{T=zym)!l$>Lm4bW&5|u{N`^(c+bCX=JkW+|1R}w|4aRC>T_In_3M)K|6pl<gw(H3Qa@bkmr4DGB<nX{>VK8`jY;YkQJ*5{S1(@iscH1j*Xq&ZzrEl5My6klxZ>vD<XOJ?-PvAd-%vkA>c33VesjqK)PGL<U#Um05A9_A^i?<iW|esS<n<gs>7{;)`tRpY0jb~mFZGqAep{0D>u7#+i}im)>bEE9-<~eiL8;%7Wd7*u#jDp`{WqTFn}5EO`T<gJ?x(!_-_);W`EN`8?j+0Kqs-qn;rcK2-zHi9UTOcR)PI+xe!numKkQe(N&OyA`*HMdtLXLhH{U<)dFLNNrU>~}cB$W&Wc^E->jU-Gq<+7wPg+lXb?Qe+{edLyoBAfye@guq93KZg{a4(x|8L6t-~UT};CEMlC`tQe&2Jyl{$o=ALz4QrX8%+F8ue>g{$cg#^=F<OpQB{{BT438EcH{R{>LQs(ZBPukoqmO|C49^%X-%Tu*`q-U+OPQ{jq=9e)~rRmHEEg{gYGDTn~1b?U(wWWqcM-{bA}$Nc{;J|CFabp87^oe@VIbH_`1|Vy<7*_mujR(tnpc^XCft)heksf4{|h{Fv*}AnKdlaP=3}qt_qP{$%Puk^0jr@%S~rExlcqvsmi?Ofr7~axU{9mijZE{;TcjzmrmbSK99+?G`bwAI$%!)c+#m?@F4#C$g9N>^EKg*(A$1^(Ux*QR>gBN6&9lf0g?3QXlVGzs8>R`#|c?C+XjM^v`|fUqt;5j;~+;W&Yx~-24}k%s+?u(?|TOrqo|n|NZ*8M(VG4*WdiT4(2b#{QIfD$nwqK;qkuzcqw511_8&{Z8!h5B->wF?yn|F{cq~g<D-oEe-xPid#N{n!^eAm^}rJ9BY(L18%gTT_4EYwuS)$*PrbQ+FxQjJF@Cjx`af9zTS?}BnygO!-!lJgPye*=^zSo&y81uNf6_d9{Pm7We1Gy+lJ-sg2<pqo{CECK{bcH2r~dJKKKplwy!G#x<B$3c)Mujp?<Dmj&GA8fg0z1>N&RQCYFYns^$(KNFEqzTGR|+QPe@Y#h1vhqFQYy?%YT@p{$;cOsn2uA&F@pu3zqvEnz2l+?Z3RXozu!34&Bq3Zw^w)Soe>;XYkCwk7>=S*H7b?7ajk-jL)Q22Hbd8bA<7sf7&*`K$1!YsWE=0&iswR9`@BKh03H?2D)bZf!$cYocSxqvFUo&+o1|&>zd|!yA~<6E16>>VCOJvn%+FgWVh*@v{%9xVD7X&vo1ZenP1jQp^B>*?&;I3;yP%5Ga{|ZK_7I_mR4nx&jugeW-~6rTC&pW3hic>ZHrA?G<`Ca-e*4kcs^H9b<MV)Y0~>D)vwvSaITc9aQd+7)@Yq7-!@qwb)ajSCs@1Gx@)$>^n&@FL(?m!pB|jEALr69YZ+r&?i!borF#4N|FW<A%KylEd*}DXNB33kNA^`HKDw{AKC-Wp#Xq{Qa@&37xBDs$by*+TS6gLYb<K82#i`hICd;b&xpJ#mZT4HyH2>^3zw9@^+i$UUTQdFEK9BCp%<)P0<<=zovc2rf_I6*kWnbQweHq<vxoD%HZ0G;pZ<+pMza6ADSu}&igRDh{yuR3A*SPztd@ym3M)zm&_(%6Ay%YEM|6zZ=6#wY{^eNwe?9czckN<PJ|LeQ~75LX<rrbZrOx{P1nM(g0Gus|HW~wDQX7bu&#%GV2y3}QU<e1s!Ic7Lkist25iH+@9cZbTAc(erMXfc0QN2Rv?^f=3K$6?cWcP{>0o3Vi&aZl;m0o!&kHf?N<o^@!w_J6i6e^1x8pPBaEI(MS|r~bA5+Nyp<x6ZLe%vu*qT<f5;8+7favF&F3r`<mP*LLguU)y#4F^qPz{g3{T)}y`g^1t<lX*->5`$_YEw%WEQ(sr(YZJTr6-y<%IE^Gf=+h0E?%Lv3~h;7-k!48$K?0=s9W*Lv$Wn}%I=l;z9IQO?DIrlrux!>8I`yDy=!}`&4e=cqGmE-k)pZj%HzCeAZ{Liz$RVd7~`?$8G2(=2kav$EccP;ybF5fV3(SNQI*&f&BRo?tbuNqv@b@^c4Y>Cr7W~R%S*nUX$$d*~telTk=qv{%$!Au`XoIX9%dtN=b!8LtVMY+EvYyJkV**}?;`y0g3Ytg|Z*CO^>TsochpKFBa<0J9f_EV2+S#=uv)%CxtJ3*cK9>J`KR>@daSoO?ygzHucomR)%->;8ezvAQn^YzHA!zCHtBQCp6<NI&pv)S<>8GnQE=DksgKv>1zHpdq|^?1)T>$vhJv&WmC^P`wf#hR=NrdKI^S$&Od>p6o*wbprWrLx}_nYQlJmieBexG$NCO=ITOs%PAf9IttUu|6}L{r~%Q_P<|m<@NM@^qLv>$Tc(B`RFxs`y<!PROcVPX2yB0nd#3by=HDta?OnUKVLKd*E#l|_e$H7oMYYP9P4h+v96qB`I4MtSy<QHvVH&i983FeudmsPJbHxx>-`bE5LZ}^;pjdpbpAi@Tg~`l=l}DVG~-J!KH};3n(B35EnDyUq>R+p&sI~_&t8jdsK&XG%C3{CFE9HohZ;<8RG}`d^8S<f`s%aWP=)C;$nt#5RaEEDgH-ppiaNVL6USorxB<a5{@1fL;7l7DOgyH{(rPhp3S~YIiA~caO<ks_7~~A$_)p1ypSJ50v}b3uKVoEFr5eb3)=y(v9{kVoYvy@1+Bez$A7^g@A4Qcsj@NsA%}gelPEQUPt~ml3Jl07Fka!V5@I)aQ)DTpNq98hEB7(9i0YL>7@y1*6+V#Q<U2nW~J$F6V)m?U7mHWnHSLI*zUUyG-68!o7!)ManRqv=*@2Xd?UcIA5*@^_eH8>IB4~uq3nu4@Zj4khPfqZ<G!VorrQSTVbLXbCyBOM`Zfn${rr!NIUid8jM4XJrEM!i+FhK|)j8hz0yXpJ2k3h5Lw1fh_nTi>?@?&dku)B+F+)ShcVec-#@Rz(_@c(K46n8TAw0N?ary;=@2HOUB~;@g*jSnzP{^M6qe3nGPTEVvtp!o#8V?B0l9rZ^#0BNqHVkT*|3>cR@exK_a!N=yYtG3uapZY9K&D@6<i-!2!SLl6#8==u<b4#kvTMEZ9@ZC(MC{*aO%IhH<DAF70Utq5YFg(3yw%l{CQE~?i<2))XI#&`1s;$~^ijqcRe=#FxlW5)sc4Gf6;I*PEiX%9JwJzZn1DqG4AW*@PM_{-sMk!)K!c?W?Du&!o^e)pCdV~0XtegW3=Kx|`SPNfJ^nPq9PPNpH9VU3<)oW}@W3}ItnfV{eggUQg|tUZ3Db`z#>6vYU}itdKC@YOhCKC3+)&Y9x`G-n=8=?41+CN61$hL8nKmo-B}Q3=Kyi#3_vl?+B%Ec6<-tP1UqP#vrfsn9=A0!>SrVd7<3u)-~uHzVZBP>9uszTBOQN42LhwuR}YKQ0dT50L%X0fILSM}H~19tu)MJI!wA98R6Lf**Qn5x(j8{}$xMT)~SYFs;Y1g@#y+W7}aB*n+<d<TLLpG;ihN6E#zKVFbqXbdcKj7a-fW1(u#OAE&rrZL3FlDhAksl<x!9EaDg41VZU#kb~5Y$(v(sqY7#4<knV(Gd8BCd?`}HS!!3#d`P5KYxZ$l7EoPa8ntRz^coH!UdypHBv>yIpZ{IN=V1MiGw-MPM$WvAo~m)pY<(P4K(tACRfM|(TLbm|5b~rNXy0FhIx+SH;V1z&#hvRaa_=m`OO)%dA@#!F2>u6t06y^bBz!#qAK2W^w&y1fk0MWLWa|f$Sr(dDGyDT**cN=!C`39;T8sA=>u@I8uAFb>I8-9W8A|DyubHr`NkS28V^?F#2x1-NO!DI_M;Ojz4amMhWnt`AUSyA@V2{Z{P7z;WaOP2NkLQHT#c_RV<D5BQwSyxkr+bX2yYZYceziNs2asMBOj{-Z8%N{%0L1njXD^I1QpC}O3=Qy_i`yI8v!lSQE$xBE=3k-lSWnsHZ-krXP@G$u#LV73-f^C?b;V_W1vl|mc=@_ZXLsCeQG<~lap(OC?%ZF&&HWYJYR$oQFT>lSulJ05$Hl4kO87Mg*EkgGN8%iL^<4*-nFJ<xXeqQ!3ey++T+ciq&$xTy*So)hJNsAKL){+ht|R`nw&%8zW$h+;#P8nz^5*^8p7QI6|Hqf6x7^gGOS;5S1&s}0de#E(I`GU{Kr(;#@b8H5-ue*m#<#IP3Ud8NU(1mqk1HCamnY#!As<r8v(;LJlya}WL&)^FTJg&65*FzZric{~z~?s4=)`_|JdE?fp7b)Z|F8L)3EPG!d|>2Fy0wAi_5-wh{5X`c^5*eOa<{AgEo)BNqwdjxd}H&v4UY4zEnK>>MLMwc?t0?@%^P-e7r4_lw%hV<5;~EObX&#@%%#MY!N2=jj_Y~&T$G?aUW}L|%tIOWTxPOXq~E&^`q^Cvoh4|cd7Ro1|Aq;Bs2Gq;eF92_VV1D0(L-ua0ZSXJF^y(KAuC#AIdkX7gS2`$hz|oRHCADYD{Iz`WlVMC(EXJ#?2of#5?3>i41L6EZyE@h3Akd>2CyWrVKMa@ly6ScMq$n~wOag}!?fw$!hV(~nF-4^1aq&}l7Mj-#!;>nacV@k2@unk1S#GG@tqLyP&(Hr-gumS-FQ4C;w3<aj%jiGt!s=z+v$zGX%KzBmCB6o`(mBu;!WZ!${Jya*;}k)PZIK-6RsX--8P<QZ7>(-&<(V1!`9yh9W1T1!PzLO_EUsBh`6}GCHVjrgGNR)aH!^mdK>(Wthb5g3HF#N;rqJqsXlm(3qQ>VALhc(@WBso;b;2bjV?UrgO73Hc^~|UZ{(VFBF;4pxU!mPo`cU)e9ppWF+R`6XAqwq$RCuKQ1U~m-Snn$jGK0cFYT-gKidayapALk@DpA5IX?KwE_}8RKHY`?-UmO^g`e+(cewC*KKL9L-syv%@51N%;PYMh0w4Th7k-fsewhnj=z}kF;TQYh*SYXZeDIrG_@zGhZ7zI~fEU|i&0Pp5zf_6UR%xAiGxpB097j=Q97!bDZ$nDGGDl1D`8ZN7#?jk~CSJ964IO=|HdYNQQT-qcH%U7MLVE47qqSUSil*ve<1<}n)N@Wj={sk(Xa>l?Aw3p&1lNdfsL_xK`{;DC(fV#M+`(h|4FSsWGz`<AV;kBP)oWldT!WZinh&!!s%H>$F=7@OKFr^v`hJKx4>6|&eVF&6`tJ~P24X7wsl61{_eIP}h&k0C=ZUDk4`P}SvqAM??u+VsBZfSeAK_2!rl`IbV(Jldy&rQ$R3D6(y%F<<hiVmJox7uY8lU|T_q%`}*MYcdd`1wL*8I3r5H|>)j1&A47k)g#2jX)Vi|OlCf1D!_SB1}S5qD#kybeTM3ZH*N-1S{>ze8LSpKl{>deD!HBQAl@=Mnd4m$F%ii{o=8;=b&Xri!=$_`DNwmv_N^_e)goPxX(uja|yH7IFRXxd?H`ccJBT#P!AJ9K?OnCEh!T>x0i3VpV#B3tx@!7(P$HIE%W_@fhNI<MSBA&F#YfyAjt5pCb`BqYE9^Bd!vkLlC#TOPZG=t^%I}5m(bC-dx0$<FgWR-*t(12I9)_8AM!V7g|n4Toj-GgP0!ef*X%G3!j@1x3Ek7j6z%~KL3fhX<cZkL!61vzaVa17hVoRTm+x5Aa36-boN4A2|iaL?wT&Ngb-JZ&-)P9zYFfCpQCyhpEn}z;x2S<MqCj-FQfdr#9M>75I!$J+=MP=_$%Uq_&f`7<GbKqMO*-%QxUhIOMXxOY|Tz*WBR5p<-Y%C>(umNG5zo^Wxnla>)7;xF@1BF@K^tAjZJ4_dRZ5mFaFsYmadBF7j?;Z?$6esbg!6xMHhJPXR9I|j_KuH(rNqI(ql#aU~hdH4(CY~r6FydLefxH$Uw7%45SK;)H8}`McmOBPOj>tQAEf#_^mZ)CnD|@Mxk4)W+3iZPuO$t`)K^WR+0G2@O!KWe;R%tjNdIP>22yY9a;x2p;p9`VU*JVDPzM`qRbSOHHyEF2>QU52E$MtF61x;W0!k!$l-Uf=R1$z8h*cQQ#=>He`W>z`C<$#e4jym0HwB<*kSnHQgtqVe~tM6umZ*87ya+7K=HZoTPSvNSb(vg^W-=Wzn@?Q{yxlFt1hQj#7K^&Fkh<b4=q*aA?yxMyo>OAxf`aX>O!c?Qr#`}P<9iOlr<$T6!Nk8D|Ai8xb2?wFU9Zap6?Uzdy3%9;Ud4uG>hPOy>>T#*JX8Rsk(sb-;@8v0f#d)i8EdDE|j?r^I2`z&{>$qKKPyUw2><imh_~1;BRUp4(0PPw&E$tA~6OQz8B*64k%Ew30kYBBW#@q_7#SI!KUyOs((-XS@``S6l!8I&4Hfp6U7)<_-@DVXYu<*kzRI83I=Gau#__)dq@=wP@ly7PscES^yD#{=0MN)nPLnqd|!g!S9!iK#_z?R?+*N)?<tRpd9-=(zejxD{jOJM!sK`-{ubbGKK|xGdTQSM9hY^YA+;w%`uIYPEcgw5ppg54n3Jv+(wYY8+Co~7{h#{N*^n-4)9yq0=42etXbijG9vkLB+9+`SRQOC$*kE}4zlEHxXWpD*Os)yL_mb>BbD=)0Ld(E*DD{k0pD2a+ag0A~5Ai8R48|$WHwoRf@ct<km%i11>ZL!f-gal*psIWB-q>*eoExrr@bRnuwCe0d?5Vp3-}L-{F8^V5^J{;6`S|>0uOHXe{??xsU+`AjyOTfYv)_qdR^0K~zsLQ)c>QI!-@4(PJ8s=D;HUStZvL0P>)03Dem-dmhh@+F4rabF6h1t6iT26OS7^U4dql68ePGeSw@;63IO=fgr$?K59e>>~z3w^Ti#`wY3)5#F`^~;fBNJ=yKDKm7%a!{KNxd~{_^ThCUVp;0r|RE%ea2x;yC0kI>hj9dAAM%b=^af=@_$#Kn_GGOqIoysdi4tY&Bxz4_?v;h6Yw_ye`D}B9DjrH*9U*a_}h)kjcxe*3V$Et?+yGtjlT!*cPsv`#2?)UdN%&1F%Wlb9*#e{i)%k7&v(%MWw>k?wu5B1NjAJ>hfTKfbl!o^oY1)<I%`CGH*`jEDy<~}#J<J;K==QEu+N}#F{D!hRx8#)bCJd~@pmfz8u1s$Un%}@X#+pnw6DY8zwq}roBkK^7exG1_*{v<`|x+8U6xB2i2F|G;Eyy=hcS7EB8k5k?cOo+Jt6H($yS!^oylgLG`e*5fX)}u87n$xM!Fh0k4R@6>0BnAO+5tYt{f114EunzW3KS5>#q3L(fyp{(^FmL!cX?WuXEvZ1zfY&fj1%iXaHvv!F7B9rHLnS)mNg>O@uMzEa{M__tB1|DngPbsDTB=H%Sf*!i@Io5~HkCU2O_1b1YZE)hppUi>E7-%+?VpH;6u33uAG-px!mg(G988n?^!46EtKZN*U7Nl;M8245$0bw$g>q^ueES;WK<?ej4GV#g75$nMB#J{aMMF5{Ds)8mzmq447x<6_a%Kp$=mB6Fhnv>8lm=74%WdJ^HAT;%|+kixMSk^l7hd{tcm>vb^P%Wsa|m@4E0)1YEWCTz^IQP*KL4g~Xb%?Mm|EcHpp5syNy~+ObV!tc1mfm}M{)6t}Ea=~zW!jvg={|4hk<m<Yu%H4<umswe_lplOXu(=;EgU%T)nKKMEpeuWRd$%Z$XlYKgk?JoRW0oUxEwC@o<2uQzPLv2}!C<R_$=izk?#FPmRzXk89=6(DZqk2YY4kb?+`vccJH0*Y1IMGL&vetp0>VpSdc)Jf??849U!Ao8E1wMGW3%}3@kGb&0K6wAN4sY9hyiK|A={|Vcg<tN2@9V;+_~5lJ{IV|A8D9Ggsc|2UVUGZ5pP@h-O3ba9IbdyV$y|VKZF0?}FuCRgXyapeYwZ$zE{E3Ilkj;oeqVvlkMa9tDC1zwg_hdI^tO}X+ld%22Q8Tk@p&fx^7uOgf9K=xV*FhKq@iui%mmWjwq{O87|KC4C<oP`98@#UW6L7z+i!>D?A>^6h?a`|7Vg`R%$iFfAOC>GJ?V|;!8XRb7n6^_&k~+|n%8>sX<^I6K8@V@Y*{PyvHf^cd>L}=QL_b~^*ByXPF#t-TZf4$d@7pQH|EfvlQm#}PIkbQHrX3ig0ufs0gmlU+giDA+>bSx%W!lf&F+;#vrD!H4AQ%#4GdYsP&%~#EbW4H;PX~+vsj1#qFmGMv|`YTa@sjUVrpZzaT;l8Ukk@pj5q_X_VDP;wOx6{1Z{bQ(>cf_EJPg5$a$0Qde58BGofpT#}%5n?xin;K1C~GA;a~vWxzJp&z9a|Y@RCL+E`x~76u2_--Sh7Sj>fWl>y1xgmS)v$-RNe_<oc9uG#iGIrAPHVj|=Xr4TO<uPEMB?zY1P5%Zc4a~G3)6ajo+;Ysadk-miF%~ySK?j$I$=cRVI7{6Fp$8h|9i9&MuRe~}8%IHaFT@^9Ltz;zUsCK))Vy$COKs+7o%GGDmK8xf8CwqcgkG<2iYn?sDKQq&<OAa@$C95*pKg*l%VXo3fCtY03bLM=S`GsXd0xXL#L=~<0DNHqIHhpBDW{Cb15O!Y0q6I2&AEr%oPk}xtLk@tUW-p}gS*%@Zd*gVmsX)3U7G8z9KB0sm8D5FBpnMVkCnw2W#_NaiDlpz3MZ5-(aV99<IO7#34%K&<=13-HRdc+EmF9+-Gan=jmg_lsDmn8OdIoallk^Pc%**H*%9)SQ(~z~4EJFU8_i5O-u4VVIi|6QTx!iwRDN?CdsCCfcps<pQufvr_eE?gk`5$pMC#l>G?Tx?Z(bXOCN3{dMsHqQ;OmziOg%n&xPc>)m$5mlnkMfIl1(ts^8w2T3QoA0*UP<|&+|h@RLFwihgnUJ5>pAmbL1)f<RFtFr_=A;G&Ti9hgdrzmI?8gCgHpU%P3uYJI;0}Rn_rbw)CWf+o>KajQo{WIM099&in^Vm(SF>)%IIl!jj3(g(R@g@%&7@nx01B=HRcjm%fjh!QvDX+_oH_`seXy2*rdFQ<=KFFAE}Och4O9{wv@T}ay}AkZV`}OYZ1_H;ak}IJIqyNqs2PRl_cRIZ%YyOh?w^WD04Bli!jyK*sIVHA4L|#8*}E{EG+tV&ZJd~okEyWz!I=}#8L`KFUq)c@qIMP#OZB$ajrOLuE+Ar*hh4ajTkpJ3;>Ef5<^k!lSS-*anT>b-SYR8AnYpv<*)p|@&}3y9_qoRexyx(A%9xm(pkaRvCq-y7Q<eCm3RiRr)=SD1>Zn_9<a}xP=DQy{c^DA^KI-}9EaZ0bD1Q{U}4tAuYp|VGVwkDXO&V`Y_ANqfm|8TsKA6BS+1~ijI!us3Ul}CrER{2>pdC;bodeTJ4m7R*K<hMDAt$uvZJZGPS&Q~#VviDrs}zP99J9Iui}d^mkO0;;DboVV5I8jvvEcMLoC!52z%#Y(AO9SvA`WhE}qaX!7#b_Vr@9~_Wem}4&*YIYSn?f@Kq8v8VsGrZ}HwYfboL2i+IZ^B*v=;3ZFKTMh0iIBXHE{8^~a&=L4zzk;0-Cd|VOsiA<HYJQNPhVf$+_2nSR9BUM3yU#6XlW5$sf&q;rIkLgR?H>$ON>H$nQjOm7pbo?pSzWBG8>R@>h#km~gIQh`C4}^>8IRM*c>S4;aL?2gFqKpfd6jT1`5}EeRUGk+k{m5pK@(;=|xAYLEzZ-@J`T!611zt1&@|hM`UK9#OivsX@Fc|0?#`q;`)YJ&1-bCunh-fz(BjGU7e4yZe5W*#?5lCZ6MDS*x$UrueVF?0N=1itz38s|bs2&*reepY{zF<V721Ltrh?Z**#fpz=AsG70Z0K7u3{od`vc8pr*|^Gxjz0zSEN-jByelIi{8iwu9Dij9kM=b)44*}9=3P8m83cT<B>Iky;0TBG)j_gkIF2+IX}@zy9U+R5CY&3N$Jr}XN$^StSK1{B=An};K6=e!i&!swR$vpK!w=F*AzYf8NTt;DafYUh3uwjTB3fvisUb~T`M97~HZH71$Mw>Rq6X4Zsu{$MDlM!IP!lX?4ilQP!7zr|d6eYL1=!=lC3$fsJ{*Z@AF5UnmEfcz9GkHOLq*^Gx`<9mlwlf&kOfIeEOM==hhK^?4aA>V6fPQ2bSy|dCm|XtLVnJd{9K9r?1lX7h5YPg^Rw3q<d*{YU5UR6{FUR+=IOBz?HwRq_7;5YE%@3yYKADh`G4eZFM<;v9sYXxX@|8^_Y+@3$k!0^HB@X39~TK3<IE6J7DCR3kh39dG)PrZ$dKHnCsJgEB$q>7xqO8veKZn=)PEfgU=F=72Q%d1KnOWt=hF-GF+&~>gghJw*&GO&QL{)q1t&r-Cqj}FdpVpa`TxTS``J6S8#xlf8Z)rQ0uka!Nd!4!*HsYfDjcyn60tcFu{lCdq_B5Hawg*E3~7Wr%u4**pdJK$0eiT*LW~fNKr;AZTx%bq26Uo?G)**0#8nyYu5RJNJ%+oxOE`O!Rzw(u>~U&@1S_qPM&S%Gz_CuU?UG2+lQ){T$H$|jTa6N?!O8Ai(w^4w8>O#KoH?p_v2D$qgvB_XuVEmx1vK|DoINWU`FLVNXPCtC=xt7S0ponA(A*e={S>(_R@kllaUJf_;UQhK=go80$#ax>_`8^4A5imVf3fbE0oS6$Npl#kr8u1dT_kL*7UCQk<}+|sjj8hjem+q7$qKL->4-A27H3c`JV~WM+W%HV!6tJ4x~}JN<hpLrIw`+9GIpIw>m1UQwqbq7*c9Z;vBJ``L9Bp2qtzOG%0R4NWx1}vG#bTv!oc)}B`2=7V*Ex?b0iB`7SWsld#n<5#}pwwD7T4FH9{t!Fj9uN6yMU7R5sG`d<(Xg$DA+tMfJ-Zoeq%n;G7Fci}DTzhv&<Yw((-!`k>40J#rdll?p6D961wH571hxG<?3P_Qdx#Aj#(@L3`ZAIkd-Ff%dh(nf7`?dxJ~+ACUIL1?>;Jw8z*(;++Yurc#~EQpYhxr4nUC>Wh$`Hg*l9QrPPSW}(2eDM+z;tDtzaSZyrUQi9s3htunRGqnIUg5FwJFQhxV58>sdpa)pxw!OVIdHO=?GM`>YCon1edTJG<tK~y26UO3FFk5$vuvp599WDh*mCrb5>1~!)syX)JFRqho$G)%x`|Ms+qbPMQ5x!d->(y87JgmkPoWt7Ka<KR~jNZns$2F;Zy8*1Ee7g~>T6!xLbkpxmJC8QF4!Q7+;K=|?$*U`SXPskP`lJv3!8%8Gcau+d_t82>w|28n=k}QkzeT|5>>%lkzCidf)W7s4Sc6-QnD(U_V^bB2mnmnT(|(pQWwuWHGc)w%*atC8(Zy;k{Dm3|Oa`lWS?J`cQ^7DOjDaJ~^)ih1g%;EQuEzMG%u1E1Cr>@&RD)3%Iumn)oz7u_Soj~9&ZUagP!^gqwZoInjaYC0VsiAlPP7SK$m;+b*U9r#?U}*YQ(BappjEvETD6-!<IWH3q|Pge?Wf(O6FgQT7axzZe;Mn1mh=o0@I5LtImghcJ(G4b{C3{dZ2bBq5G_MpFV8^cp_Yqwg(KojHUsX)e(^D;l;KkUe<U)#O+Y^?K$8S55p^B}sV6BSO*FfQTlnh-_V|v^0OnI8@|i67Fa_n*D91505p+g6mXDuEw#)Go7|vX<QH*~1_(}K`#kHN4DC1OGl9g*&NI6I`;B!R@<m0m_yg7w|t%G;;|7Gi?E}ZBlniZt^5G=M;T5HCz9S@0<pM-*be1qcX$It9R3m#Ph5K$bv@EE3Ti1zmYjthJ7xDw(KUN~==SZ~*>;CJ5K#)xAr?0#DxL}9AeOP<O3q(9Cl3SwjMdmwui@Xe}<;3I>5*UPh-P8`~=$ipt$Z5tnDr(iq!flj!<_q2Kr;`}V$sxh6NLeCX;^jr{yfY2Q=7`|SvQJdQD$DFr{`e|){5Ykpt{(f8|AnylB-ivyunxlV0yImqo7JoVO1kpNgglzsn91{m~6LAl?V_!hbvG>u{b@n_6(70Zn<4i(1j?+)7?eV_Ya?2T*xp;pdedAL&8u!DuB)01qKR}Bq2Wst^B1qt9JV0mwg+)HjPv@)5bB+<<u=VztO7lT`#XlLm^Emkv34v?VFYM-X@pDuY<k?_K=V-g_d<N?7gVYqvpOBA#ioJvQNVP<o%9xrJt8r{^YI<W#ZDu4r(OJN0ihN6|vIbSCR!am;Qpfltm-W!|8PSKJl|S6$8PSIzn|K)8XBl_SI`kBOCB}cG$M`ET{v#N_{8!^Yit!)oG5(_%|1peT!A+08zIDBGcHwbMV=8!c{8OEAC%&7R9{6;G)0wLnJ4%sS&n(8yn_<rE{SsLZ?d#=v)~rR?BB2jV!Znzl#xu|ne-mf8v@Wq>p(B$2#Ei;TaJe_8in5&RORLyU>sstH2G6L!Ev=q>TR6KW^Vi#R7(C&g19HX!?|#M;9)C9%+i|k6%6Bf~VmGZP-LpZ2xoo|+u3FfWSXZl{M4ZH>|JNWsDkSFpkhhoPsCFUBjT@C1_K_H_7V87AK~lldPB+9#;AA1STqOQBi!o1}Ud2|eAQw)?yy)u^+TYYV%%wQyhINuPj)jn3uam4nsZmNPJEa(xJB%qi%^0t@zwP*3e#Q7OC!EZcQ=j3G<SW5RGwM($Lom`jTeR-5HD&*FG-ViSfxVLHFt4Os4EpM@ch>FQ1_vhBItJxu&_25u{m+NO?&y45mh+~ShnOLEP4d3<^3F_Qr<XKV+Pekcc&unTo!x=}mEjVoHf(OtNloH%fZ|Yil$y;M1Y!1m3;rGN*>xeQvNTW}Nb}-=%x8^lSLCI(-M0PlQrhmM;*>l<<1fHCY*}=YzdyAiemlROE#+tNrF=WPozraK_)A!gZEKnsvqxb2yFm2bCvmMiA50~t?8KGmJjkdqWq@W?Iq#i;GF8YZ;=5iuRA^o`Y*nOrRfNQPkkGth{o;E0O{DQ^vUAdM<~73JDW6%cj8|nHo#&||I_py6o^?4ylW_)7+M0tO?%;7oAL-f^$T2&;+S}hgaQop4&@=oc`+Siq#;=bNz84hE**7sdH`5+}9qXYr{(^uwUoSyx>KSOQdIc%CoM{kJT2gchc_|}4w5FcL_ht6`bDp)p2d>OURyrj@#?nyg_!i-HtRMs6O291k$PL*}%qc!vp*>j3v{?3{XOP2R;JlT+h-erotQE2gpF>$I!f}10Cb5OZ^v8^Lq<k8*E?SMDUItC{SPI*;=xNAa{4B~|CA?V=D@pBY95sP2VoCiN$X-I>Uldx|_V{x!8^fK+vU+ZO8`PC%=Tq+gK#3vA>R3`)iZOp=7pXDrGnKp%)M%~qU$k*oBJO_z_cs^!GSwUMA&a5NX92^vm+iN9GsXj0-qqr4643zQ>`{<+%xve2_7~l^k?nlh_qE#oTI~yYfiEO|(e6vm>;SU+IfE$ad`4h9%zuity<e+P%+ldvl$g_H_@?6z>&7TEm?gQ7^^$TzDG0A|po)O9cy<BO#bs$|hN|bXG#s)EkVY=)r>~cNU#sn})xMAy_(IZGy&AI1wx<#y9ljFLJqNVP8z|<Z0L%eeZ$*TE=W?hJc3nEQ#jnJ(Kj@6$2{7iZWNX+*3?F}GAJF4n_BKtVuQK=tgLU-!5HI+j7`DOdf8u%gE&eBpv3HUjeh|hzQjAUY*eXsE(%oFKo0Buow{5i!g6y3yU>V;M8i4`0?$4V#tz>XUnhtv-uRAhdvVDZLR@A|%O*jiR>E+ODltEL_0^{*n#!T87{UBLX!jfSem*7V+E`^0wZ67htgdrSmkB7Clp}NQ`n;yBrkxgIqv@mH$LB9zb<XSIlK7)P1;tA3F8DMMS=_35`Vjja9Aeku+>Fp&pzS=%2s@Zl48ja-_F6G55cu`R7Pf7Wpbpv@@B6+N`kaiiSTS9xycPMhNeb??7%yaVC?Bw9cLyBDoMIw(CTv^J42GD<*$Eh0}A1!3lV)1s<#MUQgu8w#k8$>!3mJX0_44F$Eqyt`jH7k5o*z%yvSK6?JfnTta^<T`4rQ9r`0EXZngL5!%^5>8pPb1wZ#cc;b?Evi4j@j`qiFP106xSkc0ZzMsx%lbWp2N)I*=DkACd)_Cu#Q!tsvQPrvNJ*F>5q$<A5zVT&N#@mFs*hSpDdjl92)}i#<C3S^=ILY<x|!1#_}pS-dLP=vbak-k#$hw_LVC)$a?O_CSp(V*s1*Jw^K3P{vz#E!q^xh_FD3KOyg1^EB>g)*rY&=KO^*IH0s2ZXT>{>{uZvp29O?1BmLMIe8%*{aTS)*+JrQCi8hu_w;3M=tX`?bovbaif?Gx7Lct>1$vZnp@`li^>#4=_**KlxmubfZle*jm)Un?j1s98b_9KD9#f+sQ{SG0yT!K<V$cP2+P_4aE4`TY*sK*tDi~1H1X7~(HF4u&^LCS^ZAhD}qgo?)Lp#bI75~2`_8Wy``$EuY689_<uo!mnwO1gct0Hr{58j{ZDU`+Xw%Zr%yw0I{eBXvn98^?_z;?6n9v(zP2I{IEnlyj0wxG2rD0m`j!Xb3pD%?^fjTgTzcZBTIHH?KjxuHk%qg_2fc`sIQXCwn+;r;8kUH;`K5>~Drdx;KP+=5}?Eu$qc>h+hL$tYPgrw<IzBdGRh{VX68COJH13mTWf3i&8@qOf3;Rv7&_c2?}mkiGp#8%>DY19fpTQuICBP>uOl+rnTd<uU?08bgSUUI4vml+=v&nmvb=)4uT=#jy3%ICk<*P%9|6l)I!Qn75SB`VV1gx@>4_{rci$#CcH=O$DG{`%iis9cF+qu8!`5I4?7q%)||`azQ9i$TweemY;bfwulK0)`E-MKoi~}i=F#<hxxuUJNw~V6xqHy{2!B}{n6Tk)H~Z0A6(z|xyt<ae8;-6e{<=^9vCbVk-tg%^_O#xA6W5S$^|;=D6LWbBxi`?KFW9`nAOFu7|839M^_`plJH9zix95ldZIC|xV$cZ|Yc~f8J85UUFQ?f7$DjG4W>PlBG6|=VPK$fPR&%M#RNQ%w#<|5zFNQwS)>(ldcC!oMSLEf!w_SM|`EBOSE5*!^WKxzuiF5Ryg$#=l^ys(6yrMYsibC@WSphdFbl(3KEiOZ&B%!d7vcH0oT>M5{)rpuHvL;S{m4p(p>TEYp#W6d^e-!JKglMO(=8=*7Alqqnq@*w*zS7ZRpt%}^RAXQ(o@2M-Y~!C@sWdxMzDr*{{cPEQ)_Wt7C#Q<}JAiMK=Pl*aPKJ{UKEwuY^v+i|;Cz+fG+%x0&R6ero3HlW*tO1oFYsYul>$(Uc)G7CyX-HdpD=ToR=9(OLgY_45+Z2!s=s2*<TK5Te4bRWwMvCTcf0h$IDds^trVJX_yA{B&8Vl+*{~PN#T)kcNy|Qfbb0f2?@<_~LG-T8b`9rYHd0fhy-+?0@f2qM9hFAos0f`wFe(k$%;AOhXU9)khS+8ALk%*bo`<1=m&45{TGXhTae%7AvvF0Mblv-4DVAY8#_pb_PeBeuLSWmc0vx(g+Lt=(!r}OKB7V2V|B8~u?%k8(ABZyylj85=?>$@@{>A6dqS@wS8+=0d31YiXbMm+0>;oOQ(b)&G_4U4epijj<(3oG@2RdP+vk!EWXCLS$-#$>=Mmg?SnH0=sv=89y1AXM~1ASue19^RWoVwB3|M`&Y-E8S<HY>H|H~O89VfGen>n7~`F}}-)DJLuKjr;OUVg`IH;iWOY3gL&k@Q)-s5#xOWG36W={t5D7Z!Y&$K6L%eQaSP)?Xg(wtF$VgdfF<uy8F*){llHT636EAyp3|7*Qvu>G1Ov^`lm@^y!{NJe?aIGf-3FtM|cLIfAiQwcW!j-q5p<;@+gmcaEms|bxwPHGRoi~o=Q$<Su=>z=l|3=lyZm8%Qs3Lslb`LRS=f0!(Dova98yhuE7mQ{hoM#{YL2zHrrSTmmNv(KdH1r%8runWILN3E#JlYhQ|H5S?f>hHcTrecyuzIowL*VMWmBAFH#EW{UXvMSod@faMN|la_>gR2jgj6?xG&(mV@MKs<#L1Hk9pj%faOP&-h-%iKYOZK<t$HOMYBT<E18HKM_p#yc=_jgG7GbGOn`Qf#jD+8)TXOt4jM~?4K6)PfpaTq$zDYn42lb?+%scd0*Z9Xwx#nSBKOlUfC$+bjxgyPq$0nXoqrq+Pt+<`XSDar?fz<X0j9HyWn?|d?(thD5W!$r1z@!_z8Rr>AkWeyYc%&AAK^-ygACH&yflKwo&>U%Ek-ftOTWOiJiOATzYKM?C@(a{KZ|`+TL(H^?Y_4NbT*nile=q>9Ig*kDp1aO`01;%PPgTaJu_8(|w!azD;xAPI2E(wBPb(y<L`ev(?2;ao^h9x2f*iN%k8sc|#+4N~0ZBvM-=BX(K?MvvYoQ#)$ml?rne5zEJ|Ck6ik$w4Y8r{<Kl<xypL{NiEc)-48wWm~EEt)L)Y7pP;>Yu%aePRn49;v`unNY>}th#u8Y=SRh*~c~0@4OniprNbhj`oKoF{Hp#KcYINFKsXO<VY;wk?6L>AQo+M;&2Dq2EXC`PFwrAg?V!w5hY$MLN8ARzp$1(FewFcAsL8Xy1$V%L~uHeVz$36I``EQT@V6x^;w`~$8wn?_(B;0~|4?|L{ku8j~7QWTj))Si?-&0OF3RUW+leWuVz(-jCWJ)_kKU77y{cN6l@?%bZEz0-MqV9$k?FUz1OmPnE9;b%lwDO-Y&Mu6juoi8*uUv<BFITCI!<ZZo4H0hqCb_TW372r_nWX#=TJ)cxRl|IYZO{_f?xSm}U1#1h2Su4DA=_G2#wKh3g#wSx=z&Ke5q>gCv4wDF_Yls60`-O%DFA-IiHUnuz2U+(&%Z;?sH{c(1*vDORp04J|FRy^FLC3Qy7JgHo8-E{8?M+5*Q)IF_(+@Xwy&RjwkD1*sU~w*kLSvo%%6MoCDmjqtS3K5P3Bf#_*yreZGK-^E_{2BKChb09X|L<_dMQrdpIxmeUJW_n#>=1^e5G1{^Iw$<fgxS59j^3ue?XN>8L(<jmu}*jujj3oV#m||4+0@2kxACZH@yIck<e8;MPsu{bf70PIAwg)=zt0_lB@77O|ezy*LIZiclU~`m6ioVT@ngWBf42FUI&+`u55F=jU2j2`k8?j?cs<vjpX*E4cUE-JP49{q+dWiqz8!dEUFpmI>k!jCVeln$FL}{7nAHN>D=H&gHI1gjS8>J)5A=7jSvH;34juWt04VXp^(&WwO#9;7zz%@a{=Jw#mOIU5b1$S&1u)KjYL>W;Q1)OwF#Rmk>^xiEJi@^VJvjdf_glbPQ5@E|=CF@AHxPY+%EnjlGCe|4GP}^^ElF`^p_c85Y0a<8s|jdxeWx!J20z#MDyvjPaSY=2nL}-|nv9)0mp2mg3uaq}Nr`M$X)aa$rq$eVJo#OWy5}A`C}o0LixHuUL)@G9r6NZ!AZhP0O)Dqr5kxTXY7)cgUS<IeF^YhPa7bdQrGpK-MDeD)IIezFmZGv}<vM)Gfz9;bfijNmmTdnTN@oGXLP@f$B9UgkpzKbLR2%)N*DMJ#{-@!;Tr4rT?8P81oP0iIInd<o7fAG=SS-;+=z#$2TLb;{+Y;@f!q%S3A@r9Y@=AWbs*~v}dp9v0CYk<4`+Jr-*Ydw!uvz-Lvs+i+Ic78(s63o*{EOmG*G$SXX1L^a&C^3E|`I{0_JCJC2@M%CR#1_{ErSdW;M|%1H<D#}Hkr9Un_iejDfv7x`|WvuGlI&{#1$a2c+-(p55rgr|O%5GB1Nyxd6{DLK-nq}k5tD4UW?krML#XURK{ZTa!vAn%H!`y$Eb!BrrA9?*TV7lFLznS5(gUm#g?82)Jggx2}A4ySd22J#=tkc=(mi#XX;NtrFUnF;wbZ$8Bhve_-QIGxDLnN!7Xe_BtOGuU_oxM5_cRA8SP#@fYc9R(ts>R!h@5@%cL(7F;Bs~KuO^F7x%c{HIApQ);lAj86H5#_wia=fr(s;r&OXIHbk*s3}D4}!-LBP=v=I7e=lclb<f@6YN<N;QWBB3P;z)CP5Yu7M#z1De{Ye_cnCi*DrE0(K3%o2{CyUqU-@fd~#Ry)iv;m+NM!M+yMhz0uBNkusal=Vi2O6KP;bZzOA_U;uv#vZYlpfJqH9rc;u|_pyvcS_afZmc_+;&KyYVQf<M+mU0{s%t2z6x**wN4TmV!KG!O+hwaZ2T9gOztJ3~zW&>E(NU&@m&Y~udM{%HxY5}k+tYCRj=ZNxf=LoAfiVc8=dI68(XkKCgkC?#CQkY{NDEvim9u+s5e9sd~h>?t5g(ahMHexwnXH?F<z*#?B4D<)CCY>^u1hhD|&-r{jgf**J)KhWxSS+`W<(`P;)?~Rg2!*p+lI1dQ@PSH5v%Vj4P5@%;Q-w5RjA9vxjla&16MHQ$@za3oG-|$#Z`bQ;>Pub!#p+Y9<Jee%YhAq}uzPnj#N#z44>Xv4!0M<?s8Fw0K$ataIoGTqEvW`*NI9$HT>KSY4O~efM|nJLIpJFNZh#?=`a@v|(^9f}(&cV0^ERIy2nB~cZ9oI|s7WOQSh{4$qXw>Zw7@^ev%1m1bmvQB<-)d9uY|g(J7LI*?a-i|2Se%(gk}xv4BJ39gms2B6Gg!@ip-|EE01EGm3NMaRsh|vTZwfYG$CMO3kX$$(TBYh2xHw9;|jM|39y5CB#Jdz4!pDiYnO`}rJWQjLbX{Qp}rHuwn!<lmne=E5&0Kk{$Wr4!6LAV%T3I^6m!S!t{J^Vp6p;taoP(nje2w8`S>&HD6uDk^)A|vrI0R|zN`VhTuZD0Lw21GJ?9<XW8Tqn27Aa|quj@c+_CN=k%(JnNl&Cfsexv-&_Apa;<CSI-~?UDK^=*N<Kng#uxT7?jK_%I)K5HpBof0?P!8Kl>@t=_%Gm&lPqEWQ&Ks3lmYKcp@1Iy!ES)!h%2VV>xs_l!`$R;p+>Cm9XCGhhr16%<w7J+f$sbMAl)lW`R%`YLx}(97xY8B%2J)pcVoW>@$2<c^x6|b4W<b=4(G75!Yxrz2N?NL=S)(k)n8NZj+R3rg*>AdZv!e@}N8=#mGe2=|?>QObmNbAXF-*OX|N1ejG#CF|Bk#|@kgc;KU`)d56$8iIN+wUp*P}EvMa5o%b4(q|F%zqBhEtI~wUQBaqZsL^Yg7$G7C}CKJD*1Sw1HJZS{#F|ENDxAw_*O|pC=DzlZG9hZO@+8hxIeE)1^#DmTdz9cR@p7fD?w_g4IQ*PHO^OJs_Y#f5S3LF-9Nk>xXJ-rEA>uzxx|x@z&p{4v?I=x4pmK-G;J?d39C~ZgzB5gINR8mV}2<@~uL-@I#@+9n1(*SW`Z}S?cba;<8?`rD0sZY}$a5tR*CAAtTzpSLA;BGn?gFwyCiYzk;t-+B3KEDkUs^&$#iZJvsLetl%`y2OYV1v*Vw6s^CGQ(_$7j@S!{dhpPeJ9>16$D*TP5AgPfp=T2dNG7TIV)v8FHI-iYGbrw_i!TLK+dZR^ocZ`sCHP<I%S|RUYVxQnI5v9BRX%_l1uW#_THcK6B9{ysV#wug-JcG20dl&Juz|jF&Tl3~{X-^)nU`aR$*Ds&a+6h}`5~kqS-^Eu9zXmgVT@~+xUk_jkuX6fIvM6#X#b&YAM1rNri%A@31@kttq=Oc$tJYL+)>>7Gu$u{{POj>O?+?(sHhEwQ(_Mt}T~fObx%5Xq6k4$z=S@2P6;dXr2C)X}8_E@oIHF*SufWp1N|xbDy@IVqU4ijt@hY4XJJr#U)?>=CiVR!go^RN&S)Ny$m`S0W(;J#HeHr<Sc1DH<tZ{-_EXrfDCu1Mm$;hhQM5(B|wNg;Dmv(a@^<_wJEpc^pJMHnO9>*}br-{ATv|wT!d$F+j8=`0sRVr9oi7MsHic_Dig^8(NSk_W!MJP%~D?(un7?*up-U}sL0ox*V{Fc!13^fPf%IX}X=l~oYEvv<9fFz%V-wos&S!qcPftJ)z$c|3XSt`hmjI(h#ZVCP48t?vec#FL2D{H2i$L7vN3e5X?;Z7oIPth$>FCK$Q_Sj{K8*Z3qKRLF=@$>X4%D(O9h3<ZGwsEkX!)M&xU5*y>v_(72pZIo|W!Npd>6u<z@+&Ejt(CLqTnWye^HN?>M(%iIAKq_^ql36f=m2Q6rnWM4i`4xlaV)V|d*d9QDa)pCjER3K>`MkHIL0VA?xb)ORkBq$CYocgPxn{SYO&g1p>qUstRP;-+_A24i}af>&r__n&r|IAjKo+weAcXH<a;3o!&J#%g5XL=1$#eT>a54v;oGpkC)u4?t`1z6SMWEXP5lG!$bP8NtqEY)39e(d$n!p8EM(U9+PJvQNYveUF;iAaT(mZ>0m-jc>Ew|(U*gylgTEMQB?j*qF@`-kp0&@@3F;av8F~46YG^Kg3>)v>1wsE#+#>s8E0eZZPJcONi>xQPpM5*dzd5m=eWZIER+`EE>}-nK;KN)I=^A$Y9LE1xv@i1beWIodzhL#kZs{akAB6Q7Zxxt9xON~vTJ^lygL8e)m|JnZ6y_b~6QtW$>(xrzQatOi6rUpgW*l2>okkiR=^_6e6l-`{KRZ^7FIG%XIq{vC@t2iBo)j!b_t;rt$M-Qv_syHzh#nkk96Bcc20Gf!1ze*Yrkz7ZIq{DB)p$>ccuAf1eC`t=X{DtqOq{ID;a5yaf_%%Fnh)No+VJ>H<ZetE>xRn<YbD~W?!?mb8Nd>I>wei5`^-mtAR`|_dH6t+^U**?uq3+!+e16HhmQFBLR(nqBg?yuQ@?k|sg>?H)w6G`+8%U!S+kbrB@VVfD{8g3+x^OGM{c@y<aIc=4PjoI?%m?pSqx=<`%$SCeZWpHhND2c`5@=^I7XJp@~zrV&)#Raa4FYTa4qPOn;zZb+>zYXa?(UT*0F-+<UJ@)4Z$8dl&nbCdG~^zvd1|QKK_RAz1ILvd+F`?Q8Mopk}B1%+hV%St<P`q+N0cy+_{g}K#U(H`!8?J3}s#IQC{xGCuxu3(D%j`|6WxHQ>GI!-;2E}Cb(bEq^^?2@pm!&=|U#Em;47owx%A-t!X5Ej{rJRSPNES2(E{-8N`@(Q78-%5T#tfg-2Y(Bt}Y@2o+bfMo1r!RBv<6Zo4NCXL?Q&(~c;eAd@NIjqgP$DH~YwbibT=k4U=>?!^`K@k075ga7STaHRcMDNw4D-8{bGI=fNTM)GVOrNuFPVL$FxNAGLy%N%<MVNbTj*@wCZ`E{GU4^_h^@%@;0ziMmuaE!vW@{BLN@9lo&374R7E&O+^RoT~fE*Q2tdkDYt?IEZxyv7F)Y<2I&^zAK#w>o<W`}y_|$lj*A-!&=!%K4Pq>(ZbzzD@V|{VL^!K10VQ3*oQaV|Yidp|fJeVm@u4dsBqHS4aFA+a4#wq&<!dTkcEyivK4qF<<y)|IhFPeBrO!V|ckI15kzG_th!<VK~M-tkIs@YH&&sMY$kv{)sFW6}r#QRHE$70#+F#uyoo-VAUvlr+`%t7g##WBd}VO{k4GAjuBW~z32j~N7>&BSbd_v;;di@tPy2v3Rq*Rz|x5;fel32+5$E(O<-}g6%^QDlx-|vgEIs+2;q>xhN5hH0UJ6?U_%ft64;_B`>}v6I!9oIuPefaqwEK_#-rh*1vU)fVu3BjxCLx+PGE~6Tq3X~QP$bt#g-f=uq6<V2y7(E<`=M$IRYE;#NF<R+tMxW4o}?koVYGkFS#Yc`JC$3#k}RiC?3qaK8)(YeCWey9?a)HjPAjF>BE$GF#qyl3=d|V4-@cUHu*3?4`zoC6Y^mG>%$azFhBb+UcTv`+8j;ZRj4*yo*M;h<Ychpy7g5U8KNaefn91ByT1o}MmOwe=A|g?!5rd=mviE|Z8Pg>n@wQHb+HpY*p6=4Ngix7bj`QblkeHx@;%QJcT%^wb3JkAc8j~y6Zg<=RPFL$PwR#a6k2`wjc(YW2m5U|Y{r8<vKzL>gPqw8d$0#Pt{ZlQ2YY5WY}SJv-@T4J*!FJN1s?2i-S~Ww2Ro-5_GM2iUqxepo9dr^7{!Bm&xcVxn2&rI&4c;752Jf9|Mp=F4`#g&6YyX*`!GRoyYXQ{9?buI81L8}U|vkIx2^av5f7$sm-e~JS0nwpv`;^_%7Z<u8+M=vJG~pW&VxOo8+ND%dwMtQ<Su>Lk3GSIJ-Zur7W22URlfP{9OiFhe(Y=ywxt_(jt6^QH|#P`>z&k%s;fNMx!tg@cxwD<U)%mmVJ;W5_0zt1AN%5YTk%&ujJK_B^Q4&Tmf|j7Ja4N^GcU&5mj?SV-Znnnhw=8M89t1+jX&4li}Cj6=Y1G&FZ-dtzuunq%QXG{_2$RE>M8Lv!YhNo@F;uRhfzG3_k9@EgZadV(L9(hd>Gw>`Hv4{crY7$n1Bbf#fJ%cFyHwwArIy!AEwBI+3k^^i9>!qoLgx3yrmm<P=UMrux{9D4|aMt>_QLrxNg`*9_*ZM*vmcG<GW$6@L<pFhP}aqoz(^F-EX*ItFzzmq;2Qlz)C#px0|<iwE>ecZ{O<JkdF4P4-0;yM*7y7ce(4yQNHl^x$s8Y=1IaIaMzO~#Cp=PX<E5e>OIIFH(NV~*;+e3G*JfPTWa9=0DeENt)AzE8#0P^iaukErN6bxBC2yv;K{9W|1WFGcyT)(t;KYqPX>5)tK5HU&%DV;Yjl^my>FF~d6V9T>=^3e<m;&2{Il3is5Q$W$?k{t#%K9REn7$FKdK>xbut#6xBE<0cSqDDuFqmxGC*g+vrw%{-_rnZZk2W&7Gy`tIJ9d)-lN4gQp?0jmP+{?XHdjy6s9#OqNKDV@U^uoq7~Ml?{9VNnB`srX+7Q|?y!31#@S(Qr8)@OG<=A04krFgDU{X1d2I+}+x(m!s~F7Yw%Srxk2Jj?)~93D03!<!MOuK283qzEh#<;>AuXeaV_br~C~B&uwoca5d+p|HID$l6ebbt)QujgakWN`iPu2jfSa;rgd%xc5*y|N&#<K0+pUycYx^iwcm$q((jeAlP`zgB}U7NJw-MH0je>aJZU@b}`E8HLK?B%txk<6<vkJ)Xwg^lvTciQmDxQcLm@zD2=cH7RyhbT2dhp$3C>GaK6iqz1bBfLfA;vGs#_<89N_rWHW2eEy=!=t=ZarAHMHhB)6Z1@ymkvNu7x=iQ^Rj`y&g{<%7-qfK?YTI-6?Hk?uXVx5R>w9DmE#Bt0)f~%O`Jv3&hmp3NqHo0yV@-*}S+4POZjPq=GWP73=`hy94;TK<<ekywn8ySVHd-u&Gi_qQ2gzPIQZLn{Xa2k8`M~&18r>5e6(<327k-^v={`_}ZY_uzmjv1rsk<8vVI!){6Pml>bnCF1YrIYCsCzU+PfJZ)a^;*{{7$U{tkdL^(rXfb+r-~d?%!L<AC9?<DA7ti{cOK&vi&9E*p76fRd|(=v@)WV&T&Xza<}7qB5`FY<z<RA<04H*ALN|%IAEK!LCBda#Qix@rkHfbG5|4k36uNGr95ZeY-7QE#LUHS5MO1=?%e>ZGQ-U>d4nIjOz4Kogbq{%{|$bnJO$H{%4-oO@4KgVa5&~aT(p7l*i*08<Xb-eB?v1B0lfyH2Pu6a8>O%7<gLzAhA2@1$(w%@yP#r6G=3+YeBjw=v)B<$K}5CvE+|b*?igCa27~_XL;at(?zfEZ3*LWS&qf@#P5LEp^vx&Y+rOw!#wV~S^ex>}O|-_xv({8Io1&1lMkUlKm5@}%Lkzx9$XkmdJhdpoTZr`BG?gjBlS~s!tCo0r={R+pJP(@1d~NTrnAd;A{q6(so&1`$$G;T&2A`6BrX{t4Z@G7u^`xnH+82Yf?0%g$KgVx6$t&X@?P~o02Hf@m<+(vYcz*Kwmtd?_b@a-!?KFwzocT3?@Z0{N27C11PV&{Kv?sBR(kv3z2?|_@>5Qgv59*Mj!(a;1M)o~lKtc(#Y?I`OE<8Sb1)~_v9mo+bTPD%3it?Z0v`Vl~mttPXuf~t~C4USXiF5OfYaoA7d*%y1jeM8rl9p<{SWg-ym`;gy8dMu{4~F(}Y2MhI#!|3-7qZp^`K^3->w(~oSIf3}&yQm}FpH5NO)SYC!m;%Oo=~i4zVR&97%l8^+^|iaktoD5M4Ws)$1<Kh>07tSy;^^p__y088o=(G@E@hXg*t}|ZY#)}AB$6Pi8_lB4>BX^?v<n}%Up-j0~GvYu-3htz4H6FIeX>%Xr@O8zjB*%c4H*=mPPc&Mq#KcfObeOLHY`3H&$(P_Uc+0Sz(pp+i~t&i%-^jcAGP2953Jj(LO@38sT?}w)>5cMlGmUreJR)zfKE)dVRf0R=^igPgCm+Q=9-*h23OSs|L$R>&4tqjWOwc4Yi&uM`c*8-Y6AeHE&o0!)ivOkxf?^T0PlZ%Fs}6^bw(j)iQ=21Z1yZFbrFkGZ&JLnO0oz!OYV`@K<AF4w7S)prV>Q7v;@=3t7jgOLL+sQh*Gl)AS;=q$5If^mFi{_X@1YS?;3InLgU1r#G?=lW!{0k>rn{Jh_IC(n^JWnw%dZqD{-YO3K6;K=Lyd7U$`x%`^+V39aJ05>B)GK)ZOxp;J67A;6dNsVn(O7`YCPfF}HGS;>#X$0ckfU&Qd`+?9N`TgP9!b$qO^?$^8UNz8j!*p_X!t;^OJyCg6K+jA*%JhG8Ro%Ew^m8MGPGmDQ?7|sR@RI;p!+Pl?-QIX^m&9;Zyh3TfjkqcX`R@NFlj<uGxFl=3%Y_u;G;~~}!Z?z^dveP4&;=+y<mU&6InQmR=jVMtb@7Y`C+nG2|F5A6RL$d7`zFItA(r$)qI>Hz?F65U+Ao-<@OF3j9w%8~iA>@#Ow9~KTn>w_iHSOltTzXl%6oegU<2_2bQlO=ByY%}bX$fI|m)T!A^KzG}0{l1^-r|F|*l_Q;_#52y+^N3xS=Dy;?l)gOr(O6oyPl=2yw7&8U)EMO4dud9*kt8YRydzMY`YvcbMX|8G+~gVNG@JQU#yfl^6<gir7T6ZWb$63a;(!3^6gob&UH7?`Ct}dd3c?yaCk2Bv}y@UQu3}9<GX-XRSBqO)Hif7ejTx0uB#l|_GIzdU=>Bfj9EWgA>#uahQDUtaU6SipcG~o*NbzV7S0(_AWPL^7IM#KQCW`PF6G=jyrD`T)i0_hNLzEo-d{gqyW9&WTQ%;;-&TZ^l|GK-nV)!^k=1Dkq%Vh4wo4zhWRqwC%d>^-I@|JJ+|7hg)hcBjY!SPj;K*@9+Qg>0CG1-zZD(wk=c{dNJIsr293p-R`!rt9gTkLdtvG8<m|Xl)H6w1d3)pov3)7%GBd6#jAN3PwAL?9N^(grR(<_0br#h7N+E4<FseKIc(>~h0QRts^0vF#EuyjFZHPKnGA0Se#)3HWjjW{vh;HF%!(>;RM3A)o-9=;H7WMLB1Ni#$?FA<!Tht^iY7#s>j`1tiI-Q<el<6nxmeEchQ7OqKGjT9D`>#6LYQA(H-rL@sai9*gs3L<);jW6CV=e&AWB>gF4`RnM;8^xlVC`+l19T|NohTT{2x|2zpj*G=J$~uw9Nt){!d1e&*(DFtd<axPsqYg?!xUx@aqCV;bw>Iz2$GH+0C!y%nl$0@P9=~zB^ih$EC!ki?JM|SSqNWUTuXL3utfaYx<LhIP9iri^p(q)v)NQjX?D!2>t~~r%TCkZL#o1`$&}()}zDqR)Cag#d#U;+%zg^nl`rB10hP>FWCHf+&<jHVH*YwDC$2WO@kaB^POP)Y@vyelaRec_wwx#qoy2Hfrk3=~zsondI&en0_sL*R6N4elyjo>I3mVOn2Lp8J$j<kqXj!ajhbhF}lqIQnBt!mBgrlHjKR!ytd1o!l$R^Z;yQ%HX^9@6hP{tJi~uWXlgq6NC0+Aiy{wtM+%dtypK`DgoXs`Y$)7q1iRqxHKfr#Pg)E)~3?eBRqGePMd}^IQ30`uIUDs)p<aFpmtRH}!np+|m6_{MxVkV|Ez2Pck6t|1H^4%y(2T{-$rb;@|V(2Y182uR6Z?seOF5UHZ$m+a_{!vpQb*z1Sfv<8bxF{)xW^{E8`6fwU4+<`_8c2wTQFAu&IRqfIWeRvph9#4TOh)$z7pcsaNH2lE)yLw}{3$cQ#<%jDV%P`L5FyG&m2s4YFN<Ju=dXF{_~dR(8#=maG7JRFy}r8*X)LdJ`qqt+mm7pP42&czS7I+rtjbM6n@9bM^k-yB`gt<Lb<@Hjq>&C`3-(KeYg_n?bCL%<FD{QFPa3$|RO&6<vL`kAanIfJ#RGg*svI&0Bqc>E58cgXr}GINY{SdNb!?;QowcI>Pk@dy3KDgRl%@|!yx`f`Fkr=7mJuKWF$1TWj+*DsyLT6oT;lloQP9geS_RUThGt9-tC5<3dMdgib;b~bZ-_2il3tEYo?_tn$v`s!)NUb@z{=bK~e(ByC64#!9PDu}W5ipASA6^hAcz}ZZ~4puD7f#q%32*pY|unrup%M_`%Z@2ZJvVHHf!#NW?SlrCw$dF{$C1fu%E_8ukBB-Z282z=gFOU@J({07X7spRh?G8C6Ie5RHqzhS3_hl4r0$8hf{S4c7cpBXVFy6k8nDQw2QhL%;KTrDVr(uVjcgfp+3eLqEn(Nlk1#S)fo;kN_oX_N~8f|Pgvqs6c^O)5n-{vr@O}?GWtd2eVUz&w=y3VfCbKN#dVFLDkiif|7T%*a~K}hduqRyWb{+SNP5p1T?-q=SWe@k<jfS1Pj(Fo7E@Czh75#vW9{0tZVdkL?J@uLuarVBq`!bisVKed>0EOX$qC454R{~O^gF8n+RpBm$95PqBspCjS<82=Z-k9XncV!P{Yx4T))>lf<C9e%%17c#jX5`AKa^h;-%L$b8zVBsj(A8g;!+`a=$sjbEN*BYQb1x=&ov8LvEY)EYo-#b~;#7;J(N!q#7`5v!NFF7vE^NnZQzjolA%sEFym`OXNT-?++Pv~Q)98DuT#XHTWRx*{uRj4u!xMG>(#7&|v(0q!yI=U0;M{#g$`Z{g<?54y#M!FliUty$upKDX1(}qRa1;XmDaK~$@J64^z!+*wq9*$L=ti-kVKXr$cHN3nV)HCm51GGOucGP^9ZJv)a(DSx@UyQH?EIV<5fHeuJr#mklo@LGXO!_gAcHU=V{^yDOYq4Kj@mg&!9209Xg;F(%`6b~g$c~&Z_I9Liss(gD*7iv?-`NFH9<6Q9q;TxYjwJm4w6DS1GkYl-E7c0=$ha~c8JE&|G$B*1^Q1bcTdF;$R_94=kegZ|?LAY~eg4gw3s|YAJ&_G9=?=4v$a75Lx3r&gcX->+MRxniCXk}|nWFtP+U+NsSYX3`d;4i+^Kp(|Ali>S$9Vn@N7wfhP+8+jlFQrWM`=`2#!K9@DEvQ;opEpM7mHZasD)UX_NIZ5i8^h9@?7|T>d#MOAI7wr7uo%JA@=8mJ@)6zc1U@n$y~&6{4^Snb4}(#``zoaPqxh~cSw1$LA1uEMoby09&S1(pUICHQ_{GEAJ$AEXsyf4tFZzf&j=i@=GX*+;!{h4<cp~}`YXkjX+nZ0SdJRZp>`1yp?Il%J`rH~4(TT#XVweLaG}@uReB^PUoz!~&oC=2-w(r0g7`z=lQ~q8)pd7yXra4zPHpQQ`<|$#IPyEO5c_+hxHZi?HaapE@z^^yu5ic3MV_&7;s0rDT=%VWpOW;ziK!(Px#QxJpffHmv|)wT?EmMiM|zg$TG%2SBNuw)&Vud1rS{k*eIVVtL#|h)jxZ|r736MEtGR@=T8mk0^inpt>@qgF{Bl;}>MS3}cvC>`yD4I4EdX`qQFMP7%D{sd-Gk-qm(h8EKMF3_!w{4Z?%}k<MEgc!FU(1U)iLrsf3tXhgpudcD4pF=N^Hrf)ptT62E)4CdPY8X6sT4xMvUykeczK5=Nl9$|DAf&)X~nm>d#o7PP;ssD36KdIbF(5qC{tSN~C#8ME7G{kB}m$;B;;o`2K1wRPx=sch94*1Ju0%Ytv4*&Xi@OciM@(5#O+kT8Ys3#`p=MjB#*UMAw_osSFxaYa)j5WOo&_J=tZ>&^Rl2vMYp(L7U?Lp6q@TFR0y&JZAjF=EDwYa@y&)JLJCC#Q0^n4(WyU>a>e&;!<3<Um@lUxlb#1IJ1ex*qiQF9ACInAG5_CuVlNZS9P%JEbAD~)NI{iltxL9TgK^rB16=}clNlM#nvbHQ^_B7ZxuSzdcA>fIyAm?O11*AnLD(xTDEM7prQ`4(IsN9RS|DQNv*iILh22*oznkg^MW#H>a<Aq<QB8agnVC)BahZ}sRgSJs}JCJFZ>P|Q;a;zvPHA>F^cSKG-HtbTC&qV0~;=fZ0Duowz?svjy<PQ2|_v~VK*R*JkO6n*u>7u8TmlUE?6vDLJ;R=W4?Mfd!2nc+c<>0=Pr@lyUBA-sP9g>j*xXw?dzRA)xt2E@7=FDMV@ZDUF;5s8cNAL6nD4NKbAP>$*OmH_0&1@d@<*@vTLyan>Ii9-6?gSO&=_!vt2-|oud`{z8=5t_Ixk%fB)rrW@*_^uEWtXs_12wI!AvTbG5ObE@M_v_KPc-m5v4x8zQV>W6NOmLedo9=%!W%)2^T!q;G~bP743HiqT2WM}+q&3AvhCrTOuXLjSD}<XV>f=QYd=i<SVk@LSwi)``|J`OIBV5|Q6tJDMY~?BqWuhAY$<tHRvIE2cOPz7*?eoIPfoj$_7nF=pT!h+84dV<aaN*D-pRX92HdO&Bl6jt%DH_kv94dK*^afi1IPL4-YM!>+Pn5fAKY8#cxRyB2FM3C|<7&*R82Q8C3nJSeK@`v9FU+P@K9-sR5?=lmnM_g(&+b>5Vn_8e<c+qlE6E$57`7<)0yDKu|E&1F=B6O=|=5nCX<ct9D;*w6{W*8ugcJ274aQ@RbnKBG=Bt$>N&1S9R7=zW6oZa(C;FUv|LXR<P70j1vfN2uY4a1ISTVS5Gr-oDc*Z~lR_Qetnd*6|K%TMAH#SeLvb%o*=Gv0O)py1tz(K!pA`jO)*@5z?ecD~Tr*vC|NL7#a~uUYV@2)FDV0pD*Zo0ODnbs48nvtEHZe|3lD)EzNAUt(-{T7E{LCnzy`pKY$A_=;59aH)+wna)cFRkYziiZY2-*fjnD@vKTM;1egB6W4y$E%bQsRY`cksdv{jCnRkUfj^n%G`khjq*js2)V(|AsDgJE^(%%gvHKJ5RbsGeEXQm@h+`7}=doovveP}u3Z`b9FpEnycr`$DIZh7Bnjeq_Zd!J4j@7gKnuSEQF%*64k-qpe-XI>>wna1zMIhXtZ-Gfi>-IXK@di7^}8aFA=o_zn&oc7>ON8eN|^i6xR@mh}Sk{emU*TU+r9iM*7SvGMa&UmLNrn2YwH(~snd-Mr-6UM(8OMR+BbUC`q$98&km#yp;jDIU@Rc>Rg>g}vmyMwjrcd`-@TDQ{^K24_F!m?9uW!bjdSoY-GS@x7WSa#Z-Y?eMv^l}qRKyjYiDQzV4D1V^DYn;HWKzsZn?Gjw2q=FZ*w$QySS@aSZj~Fi*e>J``b{f|ivu7J8Dzty|JUoN-^RZ^o{Veh{a50^vErzkh!9aa+Da8NrJk*zHFrfH(s4h;H;P@7qja)uf`K7qJB>V0kSZQ%TaBO;3@Pv4my#32gxt=4BI8&8`PnP~<r=PF)AYcB-q_2Of>3{5$x9RqUmr<r@WB1``l!lYp{rK#QEq6Tjk?F!hDgiSPOIA{J1JyCm#_3Dmtwgs~5&mA54klTgT06F!I<Y6)pKf--zw(~wy+E#=WSiU%<X#hpwL7JaLxGO|P|xH&Ow3K8;4h-sO(P7>U5H!lv3eqZfYl4%1MTtYiV4m=vD6lS*eTcW?Ts@Ovo>dbBJbiBx<r+yf1sQsv|0=Tlvhyjit;e*`*9`TwOw+2w9Ltk53uaW2Qig4<z8&t`$9s=j=Tq@niiZTn?^pws?3>U2ZucKuK<(FF+AIN7sk6A$Iq+jOt|=3fnQhIUwQKeAU$LywBlDARN!+3K6~NwZkBC)5NQ=2yc_?3ZwE7ya6S=IxMK6Q?=D&10$(lbbbi0f-IqMvRY2-}3+H+xuIxK-mu&wenW<u(Q%6sgER>#r(nh;Hbwttc=q9YXKQht@r$bTIgw!FYvup-&@x5@K$Y$<gO*Id(j?8h2iLx`6I)kODp}2#AY@G9r%Rx;zn0)+tp!3xh$hmt6(y~96)mVeMo7Yeq;H@0_qf|m|Z3(SiQ!`msy^C4p`H9y;T@CG!{TIk`G&OuC8)T>$+v<~g{4AErVn`iBhOA<glN*eq<^+}^7ii8pNc|A#o{6ZsJQLOHGgX<HtV$1aO1aIbfc%6jp{|<pUV-iH5uiGz(-4FY=%t8X26cl7e>dVUhpd9-QL%m-uY=Sm($$>?siv7MpScGHGReV1$fGWMh(#GomvNE^qY4|8&#WX1J1N~UNc+Eex=h(`KHcc+g(|*KN!ACN_(Qn9N@GuLWe*C?TSRzYtfQxr%j|=#Hp%8dmOqH$4q_DU54dcgc6>a^^%yJ3UxWbmsrJky<=;x7wU;0kOUGqfPS{_vEz2)z%QSM}$i^oylH6!?`1Nq(ec`;AWA=#om*QaL_g7uM|LuO0EJ*sVstkyBm9n2n`<bwxjTx!aug^GoA_g71r0qr$a3)|h6PVb$a1P+BkvEf-S~2P;#HbI2QQ!5S;P~aGv7BU#B700Zf0tY*k-jn{`q?0QhMKQh$u3eJVoYhi?ooEpeGh4x@%Dt~>mFxsPgoLbUiJuk2or3+`Z4wp=f>Od=BuAzZ^xG)Poy63P9S?5Id?DGC4G7HhBYJdjTb7WNm4@x*@Q!Hw&q2GB@!fAbSS2+uc0xN#=a%H<UU!l_!a0K+&ZEH8Q7a`eUa`Mslbx;Hnxs1KA2s6CVrO|lhh=~>MJqcSRj7(hGw>sH7iJ|hNUqcWz9jPzQ_o^vx-vNuUIQ;C?<=aQuc6>qIKT--U;eIJ1=<egojI+rbUaPx%dh8kYN;S<wek3^f-G+QHtIf{&1zCJroQUA&-jR>HTnVP)o8`z_2u>9MoN!5d{q;H?61=h&qn^=!ZNf+HOMHngnfqAb_(T`Nk`S=KqhmFAt2Wx*k6FzV~LyOeQlg36POxlCX@Hzz{$JOdx=wGKoSM)Xc;d<2s3^2`(hyg3uDcrLKUYtsAJd)(vp2+M-r#wTjlNwN^lFwJvBYBBFffoO|EQBtYBW@B9Av0`r#p?tafb+dcPOiM1@bSKQ;0=3EcfGK{tKdkXF@a{1@F@Yyt7bN$$gf&%~DA+um^$^G~&O{=&F<_^a2QcvLSf`U?tcTXVTzq`~7%q@A4#>bpH7+c`+1YTG=*YlvbyTA0@<8qnz`2B$wo}DW;io45Pf@?q6D#Cgf7jT^eSZBv3;Z8jc<#puKY`iQ&Ol8W6IL7WVQHw@y3{o3jkNbkw(Tepr{PIh6cFnosaKlFUT82;K=t(ZCs0iVhnmecd5;^WN*!koy5h(j%qOu|!XE%;75<-s|;<}g+3Ki)*H+`w?R1l*CC{M;I_!L<7aYbMYEP1XUis0m_t{<{q?roG_Kqx@x<`6tSPWT+}WPdkWx}ipf@$SWYKdQ&nUm}zD(v_Wc-9}H)?_M%?$vlG48K<co5)+tPt}uoOwT}O!vyLCjSm%1emeND2_iGCrJ9t`yoB<<@A`Ju2eyM%6M5bGxfUmGKGL`P`&d4;n8wBr5##0XvjJjVoJ998TNcY1ZFpra%R~QI2a3Xs%nGSY&b#NruW5&{6A=tv{%L=haF?E@>^GTX9l&@QAxWqX-MC-$@7Y9DsVXQ?bJ5X+AzMWGkw=x6gAkIG;C!Cj0=H*7Q24ERs{EBF&%*NOy7#q9WT|}{pFjfovGP@2$W3uB*XJ5(ThQ)BADL=x=OA#7h@(Q=C(cuG@9r4n18lhmdpKX|kR#TsRtoaY>Kd1K`T!Fo~&2k69=}9NeKl9UQoPs!pdkoWjX!&w!9K5tA4=+(U+%Gzf&g02^yyWn3`@U5CPJTO`wqZMCOAJ%c&JVY?A9w9?a3{j01dG~S9aMXCrjuNa@23U*JOdl`o)KrA+rLZk;>B1ZX;RuFi)kKY*A-}eX*xJ-MQKy570sP?uOQeB%CMNCHK=o^-_KzY!+C#DzlUWbs67F5r{C8n&I7&Qr)|vUdy&~&GaJg{d$JWN?K6IT`ZbI1GljntDqSJV9`>$$nJk4^KV{q}Qr7(<<$ge<ybp?$ZzIcOY7<BW#wUB`xwG#Tmm&U7fJ@AseIGrC=-G~77;;#4jIMJV?-!Rv?CJPL&TV{vo*U`ep3iYQ=t|lvb@%Ff0hG!mQxlS&eH=D-?t|j8STV}snA(^-cO$<Q@*C;wRk|XeG`Teh`t$Qx?z?y_V&zh?WNOq}8p6Ih>Up8oxG3g?U1~pGXYj|Y-z)*zr_(#d5>AoRJ1DsW;E&Mzd;t0*IZx%WDVv7A#&_+%f0M$=e^iL!`wZffk?T8;@8!W-oC7w@M5%;q2ty(Vr%!(9*ke4KXOHo0FMEuqKUBJLJ5mXYM2mPRYmKp4TVsT>_7u;3=-5+i!ty`T_7sbH*~rxGQuw9<%M6R%Y$LOZZC@S|D(r_qHZm$~Gjhu-v|YlcUiL6EclEZ1@#?u%kKw=L@gm=IDa!+DkLQh~DT&RuqU`UzS)Zi65{k`99B&4`hpoficE9Qp@)1AZsx<xX`%WdVbTcqb)NoI5&%JLxy_1j8_FxmQ1~u_&&`8!RM-L0b4vQ>h46UJxmfK^ZL&9*CyH{vGyF6M#A#<IOLOT@og<+qkS3DfA?}-%kmS>1sYY|tAoaeY$-U`cDtknolXK)O)81L0w%c$KMjN=pS(yFEL^+dg$!3jFfBuky6{v7t=RYr#=#N~uxy>RkV@3CzD1f<9j%>U!>K3gN;f@iCtx#DGu_5siuqhl8Xg0H+shSqT-pw9H!-v<4WgV>-yES&SJ*zS3kI`@F~0ebX4O4}3i<m@N&^ni{J*rjY$ZH-5!{}`9YEOw8C>ho<Vb~oeQ#p~`TLv(PBrJg&HcehKU{c4Z`sCV0n7HKV=t220=LEiJo&XWA~by#D7y}ZBD9{eF$gL-N`g^o_oL$Rq26njbDn*Op~y>&DXi?n=1<3(!crTG1D$hQk^4_?4K2g>J=HP&F{{Ve3U7J`D_VxU=W@NU05ncG<qdB8fAzgeJcLYXt%p?A(&mGj!gg2=-bc|jjywTqDD;FUFf@QSmKYTYiiUpi7(&VDcRPZ*^$>Z{G@@JL;y{#J?KCR-$Jwbw0y>R@z0q;9bOHW<Ifbh?WuT|uNSCdg;&5Hp4p>JkhKWNmTp!gA^Cs7^ReYdep%>R88m9OiR0?}F=y{F*hoztQ%%v=d|tuWHlGX%Eh_vYKM#vYoZZ7qj~EySdM3CQXO1IT+MaFCNS!z34RL!JK+MxJ&Jw>{~BgCzMccH5C5+e_47C|39|hIB8tIi?nP9s#mTa0r<bKhqK=H?5^Ja8y^+9HNfBRQnE)f@~9x`IHq?C(7Jd^<g5si<&S2qV-DGG&3G%Xdpq=P>n^3EZx0sn2uFfxV7L-#XbcU3`6z>rrSl&{H1;{V{D$UDBEf)yLqX@o+^o+;$;G#H%l@)U@t2*EzrqaOIZ+6W0`r#<7+a1w`Cqg$Py6$DRi6J$mhaP6cF($Qf3BqjwSA$r$bnY9x0AdMC?9gsPjg=6oN=c!81u*7vCsQd<CM<WZ5Hjyq_tAzKUa;#u3c)~B)6xLU}CMM>{-It&fAO`5dF&8ZLIesi(28=-^jA+z6hPvYC`nOd|$&$vi6)W=J5|N_TnGpS85I0o?QtS2s)9*;U6B)T5~>;wdQ;(Yt8v9ZOxf)uld3I4uA0&&aZ!H{^EjO{Dtc)<qNz5>l7AWFn@6;^Ab-A6&4W0U#PIhbr{)`I$=-fuxX}U?Swt0!{S&gC+t_qn{3d$$z#2Elftih^Cr$1zW+C83`3T4UXk<XtjnmK^1Z_j6wiV0;;$UJ<}QZm7I;R`GtK#NdGM{)0RRSkrS@P|Am3df3Pp>&8S$tOA+sY=f+LuL--z6|pY{3nYs~)%k-;+Z_Z1BOsxNFR_^80};XGcC^EpInzta$ex*ktw<O9SDEmRt>nsu8w3*s?QY^b@AJ=+&$nNlmpeWu!R=vU79a8%cqu<T!8`&B(VgI%<zBDm1%?HhaaSI+v0I{nwN`JlcT_mw&$vxUYrTi2nguCc#C%xuBdtGX`wuj<NsJtIrmV`y&8aB8+so;X0C{JmG7EdDS1#E~a9YkPlZe4H|8X3Ko?0A+sItIP}j>v2$dH{|u{V}1IxG25q49iUG?>eZ(g{xAB}Sr2l2Q;?VDA@TvN$qPog-m~Yx=+yW3-&4>#ZA=W2$cIGcTK_4n)4r3wQv6zb&}Ar_Fc;}a5C@mBoz-Ht7$iJzvd<2>)9F|h@)gYM30jwrJ#NwI%}zW^t(4=PXmSOpc);-#HFDs;^($r5*$I2t&reuvmE^x#fOGb%!WtbIug9T9*ux!A;eHu&ecYh^b@y0ZKzocl<O4K#FL~MU)OgP3Jw|RmP2vHrwTC&HW~N+kN;x=;^>8E*7k(xB?t!o|S%;F(|LIN)rt7w_i^HZLYRPVa&tUkTK7)ZcjbRX$7kMb=Fwo~M$jY&FB7-4l&u}%be^Wlu;;=Nd=A*KjVn~#Vs`$)?@@y=+)^UHbtcGt(AbO>ys_u~XCBQtl?#D2^Zgvup<y}6{X7%AAhI12sg7F69SF(3x4hmkCRDT>p*urNYAm_`xc#Zijl-H46>!Gk4%8#^NwtM^u-@#0e=??P=W$l*gy`^}a1k1qhZGl4a5`&{g6isp1G+V+2g&gx%#AeSKim#*8VVI(yS_p2kDa)|UWF=EjK)re^$FXT`0o2)OZJffNm!bn_=I=P?aKThv7V%^frPM;GC{P>>*2MP@{`x3iSMewnkgS8V-h&*|p*&wD)C3nnizmP(tYiop2?M<Z7e~TcY-zZ_f*S8IC|@_xhul=8e4Xtd|9AzhkNb7$!OyTa3IbHy?NA)(;+(f*_@@+JNa0)I4L@@E?}fc4*N0A7I|9fXZH;#l=3VQXfHh_hIMxH8${3}}2+p8*0cth*|Lj#}5#{|ww%78@3m4iPzLk4tyKXJl;?+0YZgD6S?F(bhYE%&Z={zwt(psAvQvMbYo=yhR?O3po=or3}rP&V&&cwiLrMyX=k2kl5&!@jqx@IN)zb5tmdbOvM?D9;(JAaRW@=Ed+6`fjMN#~MHgCi_yhFv~ze9X6@)E9OYX5Vi!=a8obD~vTPG%Vhy&bm<-#U=mvN~1nMt0`4^VB4(scurw{ma?mvt)$^+!ot&skEc0Qq;b((*p_FqZFxoJG#M}ByeiWvD?}|I*A`ifd`kHaYRg?P(+Zdif>%&2U7e9N#>sduKg0Hn?^**hy)F+);RN+zpO<sF&&ky{L%%Nw{)AHd)LYnB(kI>#&U^4&ni)FYd>?Yn{y{MAg&%+}!*~DP#RW5A{N2Og%>v)d3Lmz`uW;E5+{<_4Oj5Y)Ztevi_zUbqDj&N9<N2Y%?~{b99>q|Kb2G+PHcL0-9Dm;MX2XjM)SRO54&lX;YkY33X@6bQ&fs}EG=!nSZ0Kni>gSQWQm22z<3adr+ec$`C^YTUV^c`_aR(3X$>PBiy`As_vo9WWbTr?6rR1<0<ht!(weZp25tN?7VuCk>qxy_R*Dp-Eeu>WxpX%DJ^vj*W^MznOfYMl)2Kh9hq%jH^=%$o|+$2LdvTf0BweC~_a}XOngLN;T=Il;0Wqa@wP$z+uiv^LV9i7MLp)CFGzq>d6cFx{8WViC;qTumO$iZU->5*SQ<)~Oh(-rawQeh~1s?Ly@%OlM`D2%WKCw87MaAlVQP`O*nYjv+0<uhnkN|hC~0!**TCS&AoB`<~HEl*hNvWC_z6~P{|p<QPv+EoZqSqN@d1-yo}+-2F8Te#10|LA<zRzvwJqCY>iU0xxJZk2_~_nigfG4E;M$by66@Zf0Y#1n#hb&vhTjG2Dk?^nt`8Ia?uyV8s$l}C(AF;)m=1MPnT-=Y@blqI}URY?0DH7D2^c^Rcg>cx3vIWB15ZfEQjV}_;HpVWSFwT@tcWAikW?{3x`@*2E&4XJxM`vg%oW)>X1Tj@G-?HGQ*c0>%n_-|{yGh6Eyjq2=}<?2ZNSEB29h^}KKuZ}|zLWjV?ZSa&Y2m#66%AP=tZ>?u&lQX_IupL_O(XI8Uv2|aUKe|3Wwt-uv-_m3I{oQKcWUfz!?cXOjj*%H;8L#`G_}}%&Zl_0LFB#QlpwF1mI27yt2<!eBlGh)V(4T$ZnOb&j+pX|MdF1dI@w!KYH2x@-urG!`!c*)KzdC5a8-}qj`bCj(u=NhdpX42S-)m#!ccN4*c@o<$^7j%svd@Cuj^D*mn06A@zk<$&jl6Eec+J$=!Fpx(^xM`vse|<1G46FopVGBk?M<pR3Zd5Yac)b;E<NJ;8UHTNHnspgE^7hWmODo`uM7tl-@Vg0cl~u*J$jOI&iCDv+wc85y+2ZYxp;T)^OAp;=cDDR_Z|D@7f{Y=p?&d21k~Ah2Rm>1g#)Bd27j;9@A?nv)8hB66I418lCylA{zXnd#AqS^D@Mfl_j$Z1a<-#C*0?h*UJ_~f2c!igedX$SnWiyec|Us^laRDuLi+M4dsVc9bh4icYY+ZY`%g2q9C+<+Wfx%E)s=ptX(%7rF}_wuO}i1HYI?Hx526BRZ%1S_&i&5FIl}SfrpjNp+o7SGL7#>d`~y<a1tQYRR`b?uJ6^_iyn^i*1NmpI+_hW5cCzvnF3%oAB?hx4I_2HF+c_uam24?aKW^IXoPoU+^N&Hk&hW9_$_J8y()&O|X|NW*`XueiDK|893U%tKvcGly937NeD;Kb3w{xc9nSw2rgDJ9)bCLI_vq|6Q5|UOZXeDLdwD(0Pv|+sD^fyF>@n`6WeSmbLvo0-8t|sp8EJ0^WoJu)aL#!bXvI>zieTig3d>;rStYP4s>-$GJ5*%9w0k7`X{)L=+D9H@v?YFR$?I@8ZXS^la>*6q{I#<Hb1C#)4m*A99201%|cl7*E*8NG?W7;D%?DefZG6iNs<B>Z=tu+8zn2S9b8mI5zeRk~2WyTK2SDeui`xvl?V|75j6=_>S&VGRU_M_cu&v`QVu5ivKFVb*E-#^>!==?qd+NpPVuz;l%+V^_6ov-s#@ptW3`e-Nq(roOd70*X#RfhPl=Yu8U^wUqDT7{LEgjl&my!iXerjEk5xagfH{e8Dv?YBt=w=16yIE$O?4+Qx^a`2S(wX>f0_w4>XdN086)A3HWyCJDZNs=r~XKl4eB|Vn2UzL%-D{?5^vDYv6KugJzjWnaJiIjuY8Eh5wwU$mFgg+rXzm1r<Gm_!Z&d6Sa{I?LLsW9HaukRSe{I!~WtrFk&E{B{!Avj8%LTTvxAsn6}ly}({p#8PfgMv@6{*>;#W3*a?q_P{Ko+<xY*_csGi)rg;*lyAZjn{kci2BvgY3e$L8V%x*mWG}W>NPSP4YC`!18CRA6ovqu6OI|hcd@_S=c}DHwdbe*wwz*Ot<28IXbvO0+#*xwZt7GP)r34b<oqU&{aWei$f}Gi1logd8A7iQv<RhNP^X|N{esZ77|wlwwYF)!S+cLM_TZbWho%t0Avs;t)UVb489m}a8WUh!=^8ihU*XkLTEpyOJrs_uyTU`dmWLQeDcQ0M!tR0ksWbRA;dZxrBb-H=EZRfjh6?w+5cWKXDgP>aV1(OtPxN@8d_b+ogqdz3L+<k8@zsN2rYRWq>TnjexRf5|G{ywk6QL11xJ?gA#$%7C9l&Z$HRN{mu8v>eB)r?}?9Vx0t1~VgJD}l=ku&fbX=e?S=Wy?RNzNbbW!f)n>i*A5raE(c9+q_!5Z#QMG~2T$hcXUXO)~+Xx6y7x{ro0AuhGxj@%c_cxS=Wd0qtSb;jiQKwK{wU`GMu<=HUDIe3OpzSA1TppZ|i-s|0&~w*Q!?gBEbw*J|%=7(Ox7ZUeWab{nL}yq&k(AX)u(pWO!OF>fJ1^#Xo*3;C&=3<`NeB*$zM$?7+8UVUqj-Y-4o&wLg=TLK`gAg3Msvrv0&!Z-@Ocirl<5iv$bWH;+()3HDCelvs_aiT#mL;KAXH%_ajwBkpcG07)mng1TxbgMjpX$0|x=V1GeCm5LhXOWz<T{Jd+Ad+)m7xR)x?~>n<y&R-xzk|4P0_OJ#LgjoMr{wIvvZrRUO=&?mOwRrb;=e_rI^qU;ZjYRbu^MOpQLMxmC!0i<&mfHt=Jhx1r!)DvaSo1IWTK&V2TfLc^k)B^+Y>;^RtUAbhuV|h7J{H4D7f)3ts8ipLxXz0cFw!}la`^B$Kbd1+pi(#TdFT#;MflQ<ZC6%R>0fX-Vc#qoLBxnjuNfVG_lv+&dBLv2A%X){+8&hYZNnrQ5QY8*Hy!eU~W&YfSbNny7px3Z-Ver1w01HF@M9h9Agl*{SCCp*97g05i&jIJ&}yPgYfZp%&FB7)9Ea^KjGJ%hGEj$@@FKOnjJAP4U}vOO&n#!j}kE=^%(U}9rhN<X@zR6N8TzA%hKn&zjk=p_pmOAm4a=wPY>b$J$<&Xl06>yTInr1!E5gCQ|A+wv5)NIQ^!V5z*$>)_-ki<^_{%+)pvTWum0-moIj%rMT_`2yT1BSc76392k>V!KD(CsA^D2aYpE9=#6Q!g!m;h4wVq#VdQpM(BA+*3T4AGJbj-0#@+I@J;P6mbeqn}<E5Hf=NQIY&<ue$6YBv5qkRLQmwT}9sP-Cw6gy+82_A=V{%qODB{4{T`82O1&d&NHEbGzTvUJ<f317G*{F`vVUpY>Y5d_(VnYY!Hhxijqy_>=D2L6S?*r-dgYJM|nOpR0o<$?SvW2;HwVkEl%I^_@L=5`q`sECWHQCA@cZy9Ob5zB6y%l+b<e%GS*~=)SqnMU(vEfcx$~7mej#i1t{SnZNJuy{{da<DZ!FUv$=`sbm7hw|w-q^1Iq%s5L@^*K`K3o-3@^jtrJuE3;k<3`gr_bOs;66ph9I5@V|obJ_pIJo=e3Yey;JOQ+2u8Qd!d3(|%U79-$5ZFW|>*X_*r$6Nu`K}WuCg<W|*UBCWX@ylwQ?VU}*uX>Fy>~VAm{mp!v84JVL^ZZmE5*w#|CA61Bqyp7qA{?P~gYr<JJRCGm|B8KFkbZDLZ!MqpZQXl~H4HhQxE;X(rpYT*%HpM8A5A&+Az_jBNEqWK>%I{Sg2kklnMeA0wuw<WNb>uSvbQnOIIPcjm1Xsk#k@{|V<k$t9QB?ZF*=XN5c`)kge){DKc$f!pk>6-Sxv^-Oiw57M^7Iya=4%RcJLmh%j?}A1GKI+mbGGRLrlBB#|V|**l`w*natWsNQmyq&7HmfVeVL3_b>|6)Di3t{YErZekD#ZcZnMFP^d7;FZ}le@gXLD-NSr{iQ&kHMD{QrqHK{K$501KD#W{pk&1{G$L=zNCi#xvEux_ca~p<-%%Mhl=+{E~Iy92_+#cC!&dBrAUAIT={W%4pm|!Fs+0EyWJ0&E`cVn8n5e|{2;5VvE4HRpt8x15QU*mh$*C)oOGX7--^W^`&dX#Suc)sqp*v2{&^7=QiN702r&?NfH!9ZS7ojsXOAG@=C?8?sX#yz?DJxKoh`R(AO6ZSa1>&6>O!WqxLo30i@KFBNZxxbu!j7od*9%Ucs@Vh(ksnjIgj+DYaM79U-A`Sze2T0;=4dzMvOX6EqcmSWT=jRqUM^sD4n@_V{D2bz0vB>YyDxKPg&l~x<*$(6LCjC56=FZ+dZ;$eQnv7AoA;pWuilllEayy%1woHyGl4O525vF9v-Ogekvd+-69y!~7l6<z32Z6V-XKDnUQA=UUua)}l)j5~SXlWP{hQdbVWvEyzaTm)PQ+YQ^$NI@)*vT;ikn`EPY>zs#JJ~T<(*FM{e4-i`8_Ignh41}jx&vFn;kFJBaCqZ_QkgubNOs0Xm=Oc((Sa?*xakh0oeP4J@C<ofj#g%2e8FJM<tXO!+k-n<8$qp)Fb~Tum+1vXPRnG|0^8{Yl*B&)DvfyPYJ3VvyQIeO!`nzYYAnY$3USoc*1U5HW%4{MqwW+_&TTx$Cs&RqPP)-W+_U6$`x-varp5|D<9Yothcbpl3Tr&SznpMdNQcvU@<d~<TI<(74Nf!3*TNZ`Q~I1@sdL4;50Y==q?#y^9%4Jv3(92Fo8%TvGE|H*sajH|Thbmm-IPvlK%SYpMFU8Y>?oFaH#>vp7%XRXgk-)A*w6RqJ?;qm?ZJU2Jq?zMGd&s<XF52~4()+G4!=`rI`%B|{?HzE-a|4Lk_uN7j~$XZ9vkb4Fqa-vg791>lhuP{j=vr(C-Rvyhas+un(dKeXb14PLlE)~!8^i1teBy`Gxnl11*JFy-xU-ni`kv5PgoG^M662fD*(xR@}I?etpk!Bk2B_75|*@5SS+3wQG-rXowsx#d*FIK1aiJaP66e?an&Js-G(@y0%BIcxEXFJk+2tFb9vMdBcf9Dch=Cuvd5ELM&rW1XpH2KilnpKsqx3Qbgw?w{WFrYyy=yyw!FTIwe>=QH&5PNqVjMoEr?F46?Es@xL)6m(loSj3HfkPVZ{zz#rLZY;4MnatxJNkLcE8gG040G+GB^9SJJr|-Y{%u%eE__!dooJSGpHQc-5N1Wh~#_B!41uaP!t4M~*3xz2%q^`R~qZA0iJ#ONPiCEm37|+oRTAoHeO&!D4<M8!W`OU1PNHn*TiJ+Ihc&Vbh+6Ae|`C8F}4wY?&PU$G>a1Z4!sEw{AyH`E*bA9Bz3qpJNW)_~#zA=DR=Lh$6NhiEa3)#`eSW=*D4rbYp0iZlvwXJW5fKM=8psqx)dH)Nk}z8?mTt95Wne{Reb*2hDgKyP$MrMrxZI<Nhk2C!I4~I#eM&djyWt1vpLx3`yi0d?L<}Fbow6-zPDK!!0HoqZ;of8!Kgt(TTjdjZ*Rgk;eCA;|Rv5gm*iG0~JPUtYC~p*a%b29x83421<0U3irN^!;$kZn@kR6&Bb(#gJqaVN9)wSmHafxxfKfkj%l1-E>{BKU-J5;@LYQK_Z+N=t;L?+Pm@t41DbEF)O@4r73FPgpynO1#|+3>fMq$!jZ6pc)tv16+8<kEqsk%c5k@#VpWONEmEWi}p=2cL;MK@5Y#Z%a=nVdepr(8n%7&vPg(zH`E*~ZHWsgzcIJkDK#<dp1#n^Au8cZi_Gs)Wged6{+XWe^du(~s{-K@hee>ByKuzDJybmyFJNZSiYn2z~8$#lC<%{sAW9ldJSISNC*harP7b`DM|1Y-FG-(xIKK7|yPM=Y@5P?_wkVhk@Y@q401vZG4oV@-|i42{{$#`A_t_wsXHH=E3tV0mFaarFVD8PVZ|$P>p*Mc1)z3semV?vm%wvrl2)bBEz`+)#W$oN8!w&YfWXs4?sr&Utwt*)>wuc*f!#+vf>GW7lvw(SvYTlI*I&I)9I8%mDCvs0RpRD{b>m?!Ra#*`><%aEx4vpvUiN>==&kBPCHG;lbX={C6-`MOcIP_-P4Lc!~vKTf(twUlgr>4Fsk8A4(JzaqS%Z{UQ(FwteH^+e7={+e5ST-1(DCw~MlH)OUZ5gPRw8<E-<I#x_kc^Y;Xj-$;d<N6Q7l$tL943g_d-1BD$-?+}w|IYu7HUr$UX%g5lHn!|F4()mm1!ul1H-6V|0xq2DG%_K8yd}@RdM#9!8sPR0bbPO&J&+SuqHZSXKRb$Y&lUJxy$aW9kdWie7$Rf<mXa3Byj)wwQjf?bXy)AQ=p`@;?iXBTG<RA3APA}D)<0GmvYs;g2XI031dr8$?J1s>M&amlBA-${S190|nE9*|O|0ByYw_c-I0BUzlC;XPJd32bv5Y+1yBeeCv#5{}WOEe#lpI2vg9i+2Yew_|R%UcIg*z*G;XW_KO!H^zjeMI+$r!#mesC|K|&1B=70Z%X6+Sz&dmDagKo<dJl7J5`U+q5NBer-Fn?NokEJDgmcmV1ELDH{;BUI9Ay2%$*+Os*DJ$RElp<QL*+a;KnmXSGXWJ;D~9fKvfe5k4;@4pD4&(@wIW5jOtC`_p0wZ6903W*nTi!>tl?r5i3*?}|elfE*&(FHf-Sp-#LZ?qahbo9b*&e{u72!TZ``<+f2}SVk?@Ic(hHru}R&tHyN{+fuveJci)CdWUH(+ftKV@DTGkZ12^EIuYm^w(#zZtu<zw6)x(v8Xj+@6*?a0MnFGl|GSuGIImDU-zPZVBWGJPxhGw`6Yv4dIr@~d$Diit<KHN|uX%NGN$0)PApK}Oa-zJiv<a_vY#i3FNcNM}h~54K)lekn)lHIowhYdJdedTmcJ!94n}D<BNIJa%)OV#HyE@MUC|QNh><uLr@vRn+MDOUvpZiAHK{@;J9UD{fZHu{EEihh^zT7s(7Z<=0$i9sypTy=Hh9RO~6-ftU{oxyBe`L?Egf=mpbrTs(%U;j0CkX%1LiG~-P{&R+nK6Ti0h$s6y!wqk6Lvp6@l<TzUopIZIm9W*<xP<3;yT2%4+)bwz3KRThkmZb=SPIe++YnpKdPT6<MYorPP08pDqAorc?XtT%kW!+WstYUeq!Ig_PW!-Q(k~=Z$v8m7sD_~x<!tw82Kt=k{AGuRw>l-iI1%)rTMK!7shkBB;miP)qHLorR9mpm(4VqU=n$Mik%GuWR0<PA4v(h5Rwi2Ts~2N!?SebU*9<AIZTsE(|w`koP$OG_I#r((ZcXEqZ6K^-Vb#I=b5I)1`8r}qEwxR7ATao+fK5!@{4_8W3s7EgpB?pKkf+W*q0GqA)ULA=Ke+Z3hxyt5U_VPrROf#t9?emIK-15K}YOpvm9spiQbM_w-q$fk!jf57h900Z!6xb<>Lsoht2_<Ds$TWDbmsV2Ut6WbROf+A`6w5X{4tqZu*tUvNLk5sm}emg2NiOPM3|Z*2?5-HL|m=!Hmqncv~mP<g1uxWGeFa1i#7Gs01JBSzp816P%uXswHi%X_rnUp5|37D{_=E1IIb+I>)QMaJUKo%y)-f1Ke6B)giX4eO?QQ)7yimk@W0NzCkl{rpc?|7?OO0>JUK8mV9HXBr6gRuc@1~uX!+k#X3>gM2{Qe)X9$6apoXTe(Z^!f9&IYgVQLcInms)k6VNJo~qk$lu2ifE&!5I+wofCz7WVm8Ch_4VPD&}U3MreMLsHODjH#zMGGiATAUZ=wZqZULWp|ty$FAW`12Kl`wZI~JtY^9-tB_U;4QT0#5d757s&pab}0BD?Ef`mofjBm(pl^6!C@Ai6t6<V{@K~@qtti(#x~kMqCq~ST#aS}WA34lYimt`JFmSpUaL==ao#PTX!-ZG2hXCuf0<&=V3-==8R01eajSxffO!g^Vn<LydC3FhW$FH-)mok3D=I&lb+p(1dl*K(s)-IB&nT@}p9e_iq>}HwF|bO@3fFRY9gu%H()Ilw@71*8E0m3JfY%ad=`}@KgD2keC>XB4kK^z;FhQvO_+EHQUU;*74oUH|NS2=#PYHZ|0$(4;*GI*}^tusWAHdi9#69%-b9}uMUpI(b#KkwuHR34=w@ZAwN<Kxmhw$d`zUy{6XG%S!ZHDw(-(F`e^#=!7OFc#&=zNm=wbZ7)&RXind26X3_gYK+{@&bL>P4bO94Vc()S1#*OFcsFzm`gSvejDZ46;VoYpECYT1(BH#dDPGb*4}DY@nlMe*Dxwo$zBgyinuEmc5#0xITuk>2Zgxls7EqV{eog;|{Mr(>?!u*NNCe8P}0WyD*N=>kj+V-mvFnVV9K@jHftmisME)Ia9^4J~P9vCU01FfL&Q)jyeBia0`kfFwPMu^Bu3(XJ<&q$0F73hkLcpWnQ0cGLDjI^B9@7j?O!KfNW#bIR$j~fDam{d!Z3|t;Qn?pd&d9g4H>@*4$YYv$B0bUl#9m<fepV8(%tIFxY7T^xm70JI^5@9la5KS+cjc{%V#Ca0>`7mhV+MD&>>Pgo*4=a>)^$k%b1GIF}qh3t{ULer^x`kh!+x_yqd_qOkP11oufX=0`O1(!I*hvIpr%jqQeoqSXwMM*_ox@Q{?Vq;rbKvWlcROXmD4HTLtuB-box?B~P#OuQGsveQR<Tr%#Nif<nEX2v}=KyjCy{!9F(!e5$MR<LvG?_0HXmJ7f6T~b?UVM)IXNOPg*e3E!&(onUpW^alKsqES~o#EJYDZ93`q@JpfLj+4`rkCY`8O(7OBd;CSD~m}%eWbz;cUBv-64F?UBqYbS^=EtandreGyk}Cadvz!pTdr!R&T+=);9%AXyoo%}+xR2>c(t};rM&LgtIzY3UU*Xo^KJZ59FkE^HDkSObMM=$?9U0d^!h2=;srERx!5yS8lThlESa;#eR!|pQzz;Zd%N`M>%D7=wL|`@0FsrnSii4MlvZ%2{WE0oSH}VH*KzVc*@EQGPWm(te|_2uf3<v^^VM@P;;&<~_^Uzo_0^L*`=}mk{HexS7wfY>)$bWSH!849vh(P%!bY*^s5_hOHjb4X9wJMwu<=uH!W$4@v}t@%FZ0h5dSP$xvxJVruRg75con}j0Q<HLc9|q?&Rg0j?Mi!`U23~(P|DV4O-uWz%7vGR2Cv)IfF(B+xQzyIo6$n&d*xBRzb04CMPB$)>ngY3JJHK0lOlgW`>etu?rD#dTVdXFwbDu<+$^_l0Ka#LHwYnbp*LKxi#0wsfP?RkU_JUT<V?u<sNJzwo&6GqpQHRW*i!p(C%MR~s{&g}yHPH+B9eNz5h?6W<T4f@ZVd^t$5NVl#~y2d&c6&Gi$njhSNrCI1oqBmDoMQMX^)+6mSB7N)KuDcV0-}`vsaokY~@p0@ynm<KDYT?x^lMIFVua$3%||QeZB}~UB5`)I#=pbLJWO3?f2=4B+p`(4=PW;MA+^Iw+q6a$(CK#;Bk5FXoJt?u_t4w&ra|)&DXXD1Fwr4OqZ|N=K%^U_IaVfPxk_7uv|XsnR1ukH^ir`Eq%z>75NH%VZZA6$5{ska1_&mW3oQfyx(dcBXzTJ_71}PTt1<O*HOzWLrcU!8KZMeq%d0Kt<o;0r?C8+)gBvaMFh@UOi8Ij%qLNQ*xxE!xG;=#C9j$zCyF}E3pv=awwb(cu5$v8WzVcU9rJg`1h&X*@!Tr07vw<C9Kbt;8B~Vl@>}Wa=E&%v{Ir#0F{KMrx(u_$g(+Qz94IlRGz6zIux3}zk<HgO$|T-H%*QF;gF<YOQ4JT=2Sc)bko=E*KxH_6aE2$>2T4h(L%hyLd5-v2uV2rfhj|{0eQ|FdOy9@Pq}@%nsIo{;%l>w;T{d#ghB*+h%o2VwOTix2U>cS^PQ7Ctj^y_wy|)04%c=BapG((boc9d^<B4OT*!1#qu|PNh18L1iTx&`FgLR|&g&=1WU;VAZXW9lR*c@y`Og>kteF~DtxAt3w&pLt&O^dMjoVn88?pWyCm9*m=+khiu_rvjU5o`vHsJqY=1_h1tv)<JEGdvF9JrI=lQQs;(L`Sfjq&i4W!@Qldb+FcUX@g5zoTJ1Xt*}ewc)ZTh*7LrYc|B#fG6(a#3!L?{K@gTW<BB_jhp;}$w)DJojSm(=<z&Qq(#IwI&gU-}Qs9G7fgP#wT?F<R+g&}m4eX<<eJ4QmAv3{ls=hP^)$2;3a-B-o;Px2}IKT0S5bX2x$!9U`U(LpIV>~y;bNM{@^V;(pOds@4alm}dzWG)jqGP#Ku6@2<x_=%ANv~t{`RdE%-up|=uJpWb6>jYaE;Wg=H;y@8CaY5@&tGf?r`sh(W})efK4Js2NV{k8>G3LrTwWJuVc^G{mn}LY1uzZAs&PzO=i2jcat-P{ZOH8fTmG%mcZA_ED=h9eJ0s8WTA;%RSBTG%nq0)x#L{@|(r=Z1hVc3_wmEB@exgjy!17~1GRaF&jiCMQUgi#o<Bb;38nlPc>uwJcF5vy@Nb^-4?JWHptm_y7(0EhAwcje9H+kg=h%=Vu!Ru|NU2L27u<^k%{F=&Qr^-2qo2(L~00OZBVnb6HS)7EqMCaSF<W(v@VVg=9;oB0TAg0E<MvEJ<r`yH&94UxMUv+|Py7qV(oo-J{u2yMj7gA+^OzXk49=AErgK0g;F?igT!rN}3^uyhLS9&$3zXsD+<fpbzs9czrp6iO~O-yfKdczI)6FLvBd((9YL3j`G7|@!~k=$>zCbU5wfj`;<;pe_{!7s4y_E6v5*MxodWnSOi>aq>HWPGp`zvcDZG^|bE9>cUD<oc5L=k=K7&>726bjC8dr!4HL`<rCbgD1+6u*W1frE;{XUzVyFOxaWqa}Nz8YPJc}ZNzkzePnWTUJsu@J(TTP)9G3KiEQs~#<cfgT34@>{c|Z>yqL=CHhYZ;rMjO|*~cdzQt4<#q4G>j=f!khkAXi^rA6xD^@tYlE%eNAF~Z|_rys&RHenua`;6qnDi2!Uu)8a-$6N|9mjbVWKNEj|KhaWvxfOVkd-S?m3T~liH{}-f`aS7~F}KZ_n<qan<_EYw*mGX&Ife}v!iI~&xGqtb8mXp-a4zzQRcL-M&hy)X6B%B->`O9?RNyMSM{pKDi1%Sgaa;&Hlks-~{?5bSmBbJ{K$ka#kKxIHR}r3mN$_-YGs4qddGPd{CvPs^+;=Ye4m=kz7lf=>@|M1H(f9A?;w{ax>Gm{1*tFz^zT@rt&vS7DroSz%#{G`I<L;wzHuJ{c${+vixZi<!tj9du`8n8UR<6j)gWHRFm>xZ!F%Oi~TLg}}@VIndsy#O&4(I;pJFidUaq=RUqUSgMmW!PJq5PxX`iB&9C4R?$8;)0^FO9FXOY*5Zj?qM<WIlvQ={1Rx-RTYBsE{8OTi8ccA5)AJz4H28oCbjq?LugF5#&<*Xt=U_|L^*nztWrc&E++mG8_wWY|D3Q@78~)CvhQ#NO7glR1}x%>6@bJnHUN7iPe+KfOr_BOFlcpwJzMh)>s?GD|n-aF(06FZ4g;rz^zKamMz}@yZ+{99>DpQ<pToX>`X4hcAd>*a~VlM`qb|s5H{ZO*hzc5?Xr)uqjuT2@<X8UmJG@S{qs(1U&^}!E2m=_Lb@6Z>22^yPlI1t4TaJj9ZM;wr>d_S1l5-Wpt6%V65(p_32%d6cp3_Y<+h_kDyQji1H*k7?iZMYa7VwePp%B9Zz#)4{A%D=AAa=<H^wld+md&m#q#ZalBB3(A=FEH8sSs?3h5z;k2r*EPl#XEbo_M4`3W1X$GEp|Tl@2oY-t-U4};6w%RlV^yFA)23|I8_TC088n(qC5*^Xi<*4ZC_qsXmr@4x3hY<ypYjIePMUz_m?$f?|Be7I?0`kUuRFBtejZ=bVD3!;2yD1Wm~yW;<!wd(?dTC|w2i;VwyyBuE<$9$*G^y>^>P8t*B1&>8Iei*-m<&R7oVf|4kH#+K8neH;pF?vFtu$YCdeT4Qc<6G9(grPl&EOlS&_?jpJZ80Kll7ltROZ?tEEcZI5F=rx;xl-eVAz(M!Gs326;j9^M_qRRcg2OP*Sy@Wc2`xNtDl3-IeDMqvF}k~X?g(j-+=q9jK6R3)&zZ96&WwzHKR*|m5e~J*x!}cl;KiBa#W~?cIoFGMc!lIVgwEp*%p;zkM`gK^jd4bK@aN{aCxtaoVrr@AUpDoI49W2MsmJV}TBo#nrCgtp)JMcSk0&~S{dYnhFGzI!d{FzHh)$gKowA2_=tQeb%d=$KXp?F4>^vLu%m1eLUdLKxa!eaa7H4C77qcZ{A(#vsUEk^R(t=koUOO;sgeAg4>@g_!6O?!lA$uo=2CEh+v-@`UX7_Ii8Vsg+j{g&ahXLUD`4jNt@3fCk9N}xV=b&2lnoVcKT|zt3$WIUAC}EPHo;eUQ3XQO-aMZZ0Y*C_dQa{M~^St6ag|}MZhX`whcyE_sv6^*jLs_?$!7ubR#PfyHT>G7}+iQUiOjE*X$QQv9(IRh?N*@xIYh4bPlfiftH|IxGz>VMOwIFQMHOki()t|MlX9@Bpv%hRqpzH64+}Vq_Vm>Q5pRlO09PLS1rY!rM%5lVb#f0r@uuPZTAFoIZ@ah@pEfe;n2CrE_oQ<8pCm->(b~8Tt?F`28l5Vlwq`ZD=bO!&-mh_$MyMU}fSQly$okgyvkbUV%!_@$W8-Jb#(<o@L3|~W`;m3C`x6R-k-ejR=wMU}lO%Dc&QTV>fUAc^;jsfJ4&!u=K!_DOzWEFH7TOhIDVOhBhO9I0cHlH<BtV=Og)J^^MPu*V`9FKtRvx``dvP3v`Zl0ZeeJv_mpBLYEd?IwB6up7g+Sy0X!EBT}WzN>^;qRRN*$aBD3;pW5-f0%doL|T--#O>v-N5#2McM<#^WUkxj{~6!5T<PG7Xx`56KTKM9*J1Ie<AW?wnt?yL2CMqUw_2s_qnl`tH6D=xJ_&n&$wR~e>>MblO%3$$V+RiYHjV!@6;Zlj^Hh<OK^0sdSqR6XMT1L<&QJW6w)ps9^M|>;KJ*;1~*=hXz<{bc`?LF<`8ofgiJ?g_1<^Nmj&qw*0Y`<IsH7DE9np=0kd8Vp#?~XWmsxNX@7hvazF!ke|(XZuhA;fV;j-)p#qduBIy-A!!pTIEBVxUS$rB6(_A(dgY(lwXtzj1$g|t<L-N<EMumt?NSlWJthH!F(a75McC(Ound#if?~a?a`sHld*F$Gn%dqhi4jaRJHhPG9BmU`P?f*3Pe%(VJ6FgzF+ubM38#e!9^$9Bon^!pT1cf0!_En&qR^e8sdXF&NmU#Vqx7)Btk5X6xO3VLp)<4&Ix^K=0=ln<Pv3&|}h2eTDEPAY=k)<NYV}B#Bk99`wr2R1n_k}VHpKx!}36LSZVKS(3v(gz$hF3S{c_uhM&Mq-?`|hs9em}C$zB@V*yEC#~oiiEPVJ+d44&Gs}`wss=esta?sTb>Y2TqX!mmSH4OkiIX<Qe5%YXI*voWR~i?CBH|Wvj{wG$y9q*eAqox9AX$KBK^@@L-=Bs!t8!zC>J2eJUX2aq#qjeQG^13>)+G)TbX{?WIEO#@T$+LDpUR>G9~#{b;a-Rs{3Djq^UqL+f7;atV2Omgw7&O9+2U%T?k27v{gl{2O$}F1O~o{egaN?6I--u;iNx)@k2}J*)EWC8U*f4fG#J-T=)O|7{ZQ)<8evRlkpU53kSfP5v1nAL(ch`2CeXN5~HpxEhKI%m#mf74}a?37~+bnMVquGxJNRoJ9E-xCZ(#e_w&w;=}xX1w5m?1(@$!*?iqRvja#MiVU;Ct>*S!Ms#rURh1vDW0H2KLAsH{!Th9~wMd4WES=7im2-3Hxu1=SqOWkY)|sD)P*GtZT=We?>1Ww>$}&)EWiiBabAYVc1JDi12F*a&y-~;wa*n=t(>>=yV#+>+ufi}3;cA=W2E{}^nU<h6#yhj#vDCV9KZNSPQVU?9B&*_b_QGj1PN3D_%|RieA0q$%H?m6{06CrRaLAqicJw~Q+azQ8_^K|C|45EGPdXTB2(<Ce$G*@7P@2cvSbF_>w$mQ@hPyquit9?Ug=FtDNLA!aY04o|t&CCVg2?q|IhH@ntK;*!ae3Bz;`fj<QD?k3Yedca9DhaUA}=&l(XBZLTOYG4`8rmu7%c>QhT@gF@>b2xm6TTqa%G&p&sm2$U#~-**JmAyOVDNAl3j~y&s&E&UsCE&mNx%zUvJuc9-p_a_|5b7DPMPCcrZKXlP8~-)leY^<F$*4yFyw5rqW88Dh}dOlQl`?JJ_AKBI8EMP7thiWY>6xX?!{ya{D=+-{J5lL2)o2?nZV+{y+I}|L@Ws$S1r*!!O>a_<W+_n=}nyEFCW4e^0vyYIr5w>eBtbQqw2mi62sYzUCE(daH7%glR^REHuN`Q=I3K**x>tH=I1L%FFZN|02)F{!N|^O}Kuavf1c_&E~(O2YV1-KX?#&;K(AS;MjQyxMiP`yTY(33wI$bX*8%^XUbpL4eYzunHw9jb7PU78;kc_qwR$K*Qvkf&drTQl2Yg7n_up8<eLjvz8MJTgCpPEnw4*E@#N&23nayLY&$5A&DlII)OmCsAdma<^0-h^+<gA|i9U05u?%F#_33@eUkkzI6AYJXZ)OFIs~!N!=@)1DOq{7>bK|@}bS96ySlWzX=g0#<u27IWN81Th>2Vtkxiz#u>{E6zVTgLd;)(1UT7~$A_V(wkodLYIPwB^!br)g3jLQ0+7)M(3x{GsTw_jgP&l|CeWHgu`J1i3J9L1pTlz)C-zSE~KFJ>D$W!v_i#&<2S6zdelu^M(z+2deR#}G`D!Sqp2Z&;^19k>{*@g3QiI=J@Jed?^b7Fa?uw9V_;E@{}jIzFFi-W)!Oo~&?{U-JbJb`OWXds_yxyb_kLn3W|Y_s#*7Vwu|+VIn@J`hsAN<_zI?4RJL>$W@43m8$C*FoGQrPSSOp3^_krU++_PU@dS7_78HfaE`V=x<pb~zE7@w*|^Je+)H%aWs<`3<C1<u#r=_v`%@kFN0P$w<NC63f2`wf)Ny|-DJ(y3C>wXVj{7Sece$jn{J8zIaeH*!7j@hoNn!bM2W8`~&~g8!<F1etrf9apk(C%%@y$=>@y(k_4yz!ziU|!@8f#{7`~wHzZ2IZ(=npv>Nw%xK_o}IRNFJ2bJp32V!--aI9)64S@CBZSj*g2?3@2VG8C!{M3E};UyxBC)%9~9XgJL;0ECR-3TE!ncVI@z#R8ADbk@jDa<quR^A^jqaU!(ndeLH|0D%j@aQrX<HN+$8{_%CMPP+|k0>+8~GPtBJ7l$D%w8JBH~WaAZCYZ=0K&Kj<=`H=?gy}gWZMt(daYwt4qGTA)ma+$<?h-tq%=atG<zK9hLIR@{&ZD1~!Y5R)2y~wmbQL1y%gD<mQAvxv>ja``w0tcT8%>_9+Eni+6PsBWia~`j-9nHKst0Y+zB<E0G+MY)1{v6F;(2NGu_k)+^*cJ@QE2{;|s^GG=>as4!JZeNhl~$~6cBGO&f=0BE!Vg|XSeJ1~7RLd!;(Y|qo1aLe$#y>cw;0H;mXg*XY@Ec<5R-n;dNqZ$Tmoe$VQGxWxN8pLuH0Pf!2Ayb#pArmJdUD;gJ67hF&wPNgh~21%qdUvI0yP_9(jFHx2Y0xI@v{dcXYCU{(q#CRd`0gkF;(<wZ;w>@lI9sXK1*z^5U<^#((SI#&_y|d0yS$$?}A}SEfSifXJ53L@LCiK1~+&s(aA^>hAD8YcX$iCe77|SB7WTGs(_^$A3S%UYMsRzFJ286e}z;y)@8QOUf_*9I9LOJZph#wBGtB`{}K(k@Pjk*WHC_6i;@o)?5GNKj^Kml@vFhue)F4^%l4e^C)v*n)%E*2jDX|=5ct}NlF#U^3jiCUXBj@t^b7%T;Y8Izs75j=iW0}T9d(g9f+QIG!O0lH}u4*-?Nx+iJrG%F(!{DC?5Yn`TI4!Dr7vWaLfysPb8bqQcKYQwpQ$=i>xf?*`A&0n7_->?9JDjy#YG^GRVmzul1_uXYT)LJ-vEfyqSev%I5(O&W2v~WUAGVbo5S*oijeU=7^&jJi=wOPQHU`@1}k1RBD$x+Y|pPb-6>CzA3L6+p*jU*>X2~?CA$9cc57Y_6XAZ+ckLg<kzDB`~_SOTB$4GHE=hrf!n<LpgfpV6sY>#03!-ieMam0#Pa%KXCA%|i--2>hk@oo=-m(hefx*#@*<FvQNPA}9F#2c4{c9(gRGGQpay$4Y~5ne-Z2aDPV&fg2bEQl)7SmiGSB{U%~(CJ#zRi7@d$^XI@cQ(Pv^-s`;eddAdjCyo>xe1oA{BY`|Gl_xc8YuMUYQ59ZVg{ld~P!pcwOH-O~JZdA!;8J<j|&tIzyNp1e*n{?6)M{O%#{HW6a&%U)307n{##qQ8h9i*k(@h|d{_WzOR^e_|y&S0jIQEZJxyK1-gfYQc13eC0gEn#%5teulP%V}8rD{AT*x)v{^98W|mxpRTgBcPbql(j6H4oHfKNcI4Vhd>p|(wo!)VPNgF=bnMPGGKqI}7MHTSAU#b#z4ki{e{sn1WmVWN;(L;@wTQRFc+ZtXy64Fb)|ct(wKD1r%e%95AdP7#9hK$F;!wqxW_e=Fe+-Y+^_c%iq{1{BIgQy2ccsyn@1@nf^vT!DrqgbaQM-5i*>1(3X5-AiK~mn1jL~1$VNl-K)nOeHJa)D>*?A+5-4V*Z-Z;n@IHM+Wqjn>X!%Z?eqF0JxxfHn(!<Y+hqI&0zSXh>NRlU_XVVusJaGY+;juYYU3Q*@W(KuD$Zvy1x^y7r=C8w|Bv!|6VZXSL+fxmrdy2zIJ<TcW+AS=!qF$$`uA--@UzHlpi;ikPuXDFP&IGSvLJW2xY5Yche-_9yuHjbJ-MuSJ1n#YYCC?3H-#k4S{Wrp1=Jov0;yT!UrkDA|ISzYIGdVbH&o8RO=eb|2UyE!|*&-jn?Tj^EzKySV3Ajr2VQKx1uz_J<`HWql3m#oW^$FTo~(kjb9R|n)-vnm<R@>|K}s{IATj}!mwHH`+h;%!eeqe#eV3b5C`s@J_(i^{{O_L!Slz_1_+`~}G+Sa(I8cmxUr*4-N}_*U=#322;4a*2=IV6p8Dt!I$0mzi>dJQQ!vUV}@pJu?^<K4H0~an4PWuoU^It{xMD8W+~drRqfI%_omvM`so@UlENa=d8=>aG6rL45{UtaQ-*!8EUc1lwsFO58e$eYaMXU!el+h@w_p<=73{7(fdCgV?!K#jGeM>JYZRqz5hMEP-R*FMOo_)SXOHmc02ydvRnr%ivVdZ{!YN(u_#@}Aq|20)S@03DqvPmq87%m);_Ra7T+!xH_5J9h+Ht*iI||75_6)H5vVX(BHq>m!*s%GCt?5FWmi-EYI*J~gfomB!elp-Bzrqu)(Otx7j&yn#j)f^gp=FK9{8;e$H(ot$Jca}IK6zv#$}v<UXRe_&vD9s3Lcc_H`TXhum%Zv{;Ybs@I#M&p#7V16hSCcSj#t(O7P`c$$7)i`98V6z7=j!kMT58@BlR7Z#K8!q-+DST~>O#T-3w`_}TG9fM=gFNF7FvA3`a&p;$e#DvnHI03sCnb82WhUEhU&SG<)@q}N=Ke)WR%rgbY)t*NQdVeuFS+7k4)GPN;r+~5`UcaExxSEb{}!&ppQwW4*+1&dyN;q0~z_nehzPQwkD<)Ere9B#p{Pm@p*(3W`t#o-5F5Lc~?ud`reB85~w07u|&D*lS`Hwgmr2Mgf}O1ujBvOuP}>!GeYe<T-+Z~!j@1F)TfFIbakT~j++c3cPF$EfGy@0XP3ExK%_%hhywoi4jLGwSN)bXiK5?<i~!U0$NgJ9N2$K3qYU)pWU<E)UR$&2;&cE?>|kMcL8A&m>+?s=Bl_kvJ=pkk1KmzZH;eVn9GPEzijI<?;v1<#Sl-&Wo0xb+mjQM1TCeT#i^S4__{4FJC5)rYQIcA+&$sGI{&Om~8eeIq3GwHpml2zkvMy@|AMba(N_0nkq^H8QDyaYbd_#xp=t@QC=Bo&&tSCmdni-WBiikatAC=WGMG@m&>VGPV;hk^771R`8q6bt*WY$J1&}h)<pSts!#?W&ZgW}T)a@O7O%*^Ty(p9MToLMy}a$B$=K&*0cge6F2LV*YW8n=T-p}(T$b7(`jx;36!;NceoB{{`17K!J5R^7{R7a5VH+6c=tEC)Ce@W`&cLk{bUOapX<+DODP0~j2|Chn5d$fW1zlS3M;pK%rI$tc`yCG$U7n@OZ|L#_T@avZQ}L=%2)5m+F^7-E){e%vxp;>dPonV%rYq`k_SV6mo(pJQ{Z%-IVFf?0s*iVz!-JX3luTE5rh7|#Qzo9e9R9?}fN>?>o+n73f%ERN9>!=pQ!BToF6~m2rM`AWeP&%EbylK2)4EoKf{E7jN!z-6THD&vsphGx_ubH3yRV`4q}qM;b+UC~_BjsMVXi%Qz+J+nFla#j>7tSHE-`XjYDZ7JyY<+f_|(+4jSW5VDWaq(F8YUh5EaB<U9~O#P@+MUl!)QMcuEW|5(E9ey>w%3W_{~rViX1SD-t98&xvvRd9?r8mY(=p{x5&H95a(A-!8{K7)L=OFm-h*-j+esLmS8^K%l+ujwx_11X8Q1utkKtyHfG`R6`~{h2DQmqusSSzM%)x_%O{#3|J3tbr#YfAnpxuGLQ%ADDf0P>(c>Y7dih6Af(Yv90#dCW|v9DQJxC0&BHj%DAC#--@0-NN&JB?VXYd43LedFstz&{n5wn5X9%1Hfg7;XQ^-FDR>xCY;3tGLp$U^T)pp9o3sY%$LNd50T>Ki}Qm}>ZpTqAJI{usyCzlA{lUz4M%k&`sdf`ZUi##(UN`i^RMiDI%#ie3cQJEM~Bue`Ah;T>@DT3LYO)8bj+_5i_h{GVt=_n4A=S^NMeG99qG8x7#U8yV5nby^{TSReDZSzO6=-t{>TKXm@TBjsx6I1b5n`o)6MJ(SWPZ7m|lw5pfYI^?HCHS>go;#8z8sYg<c@nMf={Z}3d`t-w@Cw2AN!N78;m`DR#5tL^I4om=cVzsWws`!IIGiIm*_t)`fGqX{GW0HXH+A4I5I7ST(CEpNM#{>OnMEttZ`<_RdJ!smZRM6tnKls&?c20^<-W};@VQ@T{i2my;v!hGUM_wR`zHk})TkZ8qt?NrhD<e*i;F4ye!7c)KsB9-acZZ>=QSrzjK^Uh#a)#+X?|*bd{ynKav|c^lnl<wG;f3>sg8kniB(9z5X$4xo$FKGnbe}qRii|(Un`VRjKz~zC#puHfF2+hdkCGZ<u(vH$yb31fh}8}gaRI$II_}s-3a|CN;CCb6=9qV)iVRr2s&}t5E&-cug}0KRPdP;m{^a&KO#8Xn1D;^PRz%k1YAkanXXg<e#W8K(}*XKe-Frt>qLqFgcDAX3!CubYm(>S{e%+`1?Xbx$tLYYtmj-F!cy+M)97oJ8oCsmMcGwBt)jG<DnfMW{FkFt^y3_?!l4*`4!5_99xqUN+$Pk0rO@-SOU1lVhhNJ>h=J_Rx|=%a0to!EH32pB9M@x5qlo?cG|PUmp(oKJB6f3PG<<^Z?ef=f-SJl+{}RHb803?SuS;Bok5?amyj+ZY(xT<~Lo!F#K>>b;XDOea^*1Ck@JCZu3@5K@;^&)Sr@@aS>n}~LNQ_E!ZS1N=L<pA{y1~m^@xz+7OiF}Ho?iJ-+YVVQLV;(+*x;YUu;3zL_s8gK*NJFQ4Cs#`a1GHZ>HDsJg<S9;yvYO!9yPgDnZ%04TW(0eCW~fDe8bbVskL#Ydx<sh7lfqt=in9#0++YK8&vNt88`}4pP}ac4`;P@cP(0-Xw1;uk9V)Qpj8%K2OBZswtJ4<0(W4^BSmp&S_1nNr*czfR0fF`yhr)h!sSd^;RPCwv*I}67cET36G!7y9KN7$i0qiLyd$21m$5Qk@H*B7*{sX>>vL4;p0+L96tkuXSwI%P8;4yKVa>6b1hTjpQEF!p6<y!mDoTsH@lA{>f=?($;w*Su)g_3xiECjzf#p!dD?M-sPOKgV(H=S+=0jpO6JRsQ_NPR$@RikxOdC$%l7Q^KJ+V5Gl7TZ(*kJf9R=0AgK$LQZL-ALIzcGY_slt_X8BG^rL5Vzd?d<*c`PRHY3fQ`HzrX$B<bBz>mHw+QpYI=wBomstFqW!B>^0#uh1W**^uQ>pRyAHOefTWI;ft>$d%i*VVK}n!$5PwYtXz>;y?PTAAgLw3W_?cvO7+Z{K&guvR#qb0&CN~xJduDx9pN6KV7&^_Zf#FbI#HX6^B=rSLy4hrB%O=!adjI~L1Blg;8;b7GTm!dByNBxjrgL;t3Rm9bj6=Wei`4l$LrxAED$iHA&UUx)kGV)X8X=5iTZSWG$fSB!pH|P1ift+c5$K`2&&*PMn(%E(A*67QNSbz-(0i;X+tqqr?^eF{YI9(^2}PK6|Xlp*G7n{1mI4(Aa6G4`!K}NY(RSbdPbZ~-53Fl9MzMW(!-QK5l{1fD8Yp6L)A$JUvb4QfLab&wMuqh2gf)yNr-+yIG##-m|DA8(daX`zycbu%*C*ZQ-8i9^HE~+rn3=cuWN0CiL`g9hbHp|<dk|~33CBBq;FEK*7gt+yJ|6vV^NNJVMPzc+tvdgGv36PEwGI~P&hoz6t{judi6<gyW%xgw2BgYN(M#S)>`6!pwOvtTp{2=7*>iup9X^>YC{nQW8=l(LMUPsMH6ZLZ;_;pBsp-4`jhAX21+$$Ll|ASxr(MMcy&%ARZzJ=Vl~u{Tp~$eP^87S`RZ$urlNq!qF?_zNm9E&`bA{`G6eQ2<_T?7%{!@S*QcuDXQvZqUnRRxa**B2dCx-NP6`%w35JtI!C0O?B8!(wtd7GJ9RJo)aFRyw(*GH<PH;R`vufiOI7-2NCXtz%c#RqPOk#CcR~!Z~eraJI4jKPc?UwkK);P|JOl_vND^1g32xT`?21edB|Kv>T9r!1I@F0`w6!RUEA8tLiHGVA5Bb2TaqO=GnR4rxMeWwa3FM^C9xH=KLVL~kfOad0tWGOC6sBuxI4%}~ZxWlJ(#iS2OO~WFTHda9u*~gC1{XPaJCmL4A-@XivruW(t;2DO!??YPmz|_;zaTcQG2VzjwigZ`JwhJLLkw6Bv7SnK|KTy|hscl|V)!e-m78=|~CsjS%Syi?E*tTPzYk2<ds(5@8_CgQ-YFBSula{A{hWW^a%kgsF@{~LWe<-OgUJg4juU6Ur_69hCnwQwQ6@E>y{|gd??<UF=86ZkR(27v8ZN0D&y<poHW88;eD)lBZ-RgF!qRyXv0#8cLDgig+!z-dVh@)SEo#w;;da`cZh!cf7aGSbqqVoPoE!)VnZWeqj5#_;QeP+c=ly{~lJ{mDuLn4zH-L*Qwl!iDuk}=lb3U1C38Oo8X5|<}Zsml{GASNjAyZUNbv|N7w%%u!OtD4iYKLO?0XI^nqqPsJbfC4>OgBiNk<LqsNLd?$Q;Z3aPf6OTMtj@G{GrI2S>87Hgke<8w7H8VF8jfb5X`l+S3Z%75cU$IFn5<`9C1csl>_qDiQIs7-*g#YlD)yM^3Ta8UeLzex)KU>CD947z;S_?dCdN$2=mbecMIR4Ww|Vq6k^A}z_cWqqQBuspa{`{@p!qVmd|x7y%Ct785@HArad0yZK!91q)%BU?uAcfWNLhMB$nHtpfsB6hip<K4DD%xnK{=C1kT5w<*a2C6W|}ltU5QLXW@{^k2h#{|GSH;vz|KsjCquFEf94@k9z@!eNoPd=QkaRw;nZ3_apFWcj)oX#GCacEZiYAuuykj69d3|0f2<YM@yN9HBr?s&9Nm2q@{Gf|t9vr%-Ek81=g%S-gt0neih>y#&L)<o>2&CiFSD)z4-ec!N=AH&@%nmX>G1Vkc$8n^BlYnMmL@;p>t!rhTx&QSFhxkD)>KLVyHi%cW}Xak_`54$2}SIxMT!)H+YDA-aAr@dDFJs=)U#j%O@#+FJ3cv4zy4pXYg=!U1Fu8b;6<gA6rs|MtLryr8ZLq_De<gK50Y2S3D$RS6oZN$ZA;Z}L6kER<`CDmD5L*c;ba)OI=*%7Z;|9*^$2OvgxxQlnv!X5%}5j-18d<%$wU}i#<H)!2`6(a>|-LfX!WX0qC3uNOq}9nELyp!2V6?<C$UP?5}ZaDP>bL@C{tetgGpDYa4gg@Tw+fo;8aY9g1;=9#f&3K^Xn600D@atFy4A>Lu-%dZ^tt|FixczVABc4nA2hOK6(^Vl8qubK>8OF(W+0ZADyUAAfDkkU(h7jt9lIqV>&k|Gg%H;Sk;<o-P($MI+rGJW>cnTMR#f|$#too`krSRx)IZ5G7a6St|?sz%82&r6I0sGPNY|K#eV>QC(a4mzl;Wpne+Otu9b=UDT(#Sun~{YcIo>)NGN*5q~Oyjc$ZNk{Fb=>_3#vP;*Zf~4A-b@@)E31CesFW7<MECKIm!Aq+1iuq*Gg)dp6?8oPr}mDu#Qg;8c7RazbxX`WGF!tu_&_?O}?=Ut{&HU0sV}8JuE^Fz2l&H{94G7d@Eisg`FiM0CAgRfrVBRY(Q67B^iqQtlA{Ny%}E7Q<4hbXO0ez}FYEu46T|Df8&D^DkTnk1~lurehFH;I5z0y@;eA?Coc{sKx206ZMO#MxXRF{DuGt))VZv9TAs{7iQX!65mSivY6(b8aTp`+iy&z)||Ea=kfaHsp&gbHp{~A(kIoWWk$pTXJK{E?p|DXd0b}BESrH(>DtjK5>+vP(#%E_JqWxxEtC)JJQY+Fizk1thyW0}mNT%;TGZ7o`q`<juI?>z;dd;MXP)kXlc-Izs%lvdzrLZKME1#B5}bTJs}`Pe*i)E^`3$OGfz$Ju8`j5H5a!NwWx5&8?j#I52$oWp3`&ekG|O|vsDKRo2G-E%h(m%TPi+wWix!D+(P&};IIxqtBqM5yRx%4N+h@t*k?=FYiAsYopHUE@yvr5j%c6yHVUt{ZrnJStfGF{c0e&RLpNNqE6Ijj|>JIoFV_SScap!q>ob(v$S8jk0>FdUQD+y;eAkJv56=g-OV~B}MAbBjtpgCdy{=!Am`97d1GY1h+W||mSG>RBy1f>DR_<0Tn@aJjtc@(^^1e?H(a?zQ{UD|<f{(@kc7ZxP8p??7eE5%v^jazB(iYNbq^L;}K+KCq%-A|MhOq|L*N%lo4o)W3nRcqjIeAp%;MN_ffnJHL@TCB?$sB&-=4*pt=AX1rh8YWT8kp{vH4B12~QGQEc=nA6G88JN6Dh3v|wPJPRNP(J(QC7lg91dmlc5_yryFHGqC9HF1X#(z7qb&vp6)!n7l&)XTf7_vnrcEmx$)v5!POM!`e=Dd7nJ$#N9^+=M`BWAzN1(uY8X_cx1HEm_)J5b!An@}<roIa<qm26p5znYbZr!v6u3_j17p-8~i{WT0*LVj{T%^V@QPm82g=T)*0kiZSrQZZqU^ueJB$;#ar0$vN`aY7g8&2JqL8<F=l2m#~4~(z<gI_=ie?JXT<u;7orttSp<g=fq^&F%y$K$6~5=QWO8efm*>v4P?%U2YMkTwuU)I+L(#OK5oweCRZ98IbN<ncmVM>9t#yJn3i%{7kNRKj)GO)H@upVH4Pf<_*uv$3k(B51Eb$-o))Q!)HH8qa(6K-nRxSVIOG+08hi7$`2#O}>M>5_|fmg24kDyMZ8PyF!7~m50&W3?9*8L71cl1SbF@S;d`E&tJvGpWP-Gy({|nrzZ7~z7$*c5$978Bw6!%gy1@CX)XR@FbuyhUb~VwwUZFWYIlw%-s^gtk97zYwL4>s+~cr_r^4fHvin_8;zL$A9Dp}C*C9a-QOJwgZK5=U0wFc%9*n#2FBr3g_;VfeU(UlHoCjso*a(h2sk)z`?`P?IhrS~s=6kn(AFJPgtKV1X`yx=b1dHG^eoyLfkB<L@eh=&W@jCn{eLq>>|E|C9()aE9K1qK!!8x;0-3@(@2^AI-F+Ml*G?CjwAOW&_;2poMrrv?3p8Y)I)OVHMvGpz0;iYga$FG1{Vi1Bhr8^V`vCndPJ{-Z?$zy`v3t6LmU|gf~x>nzB(D&PP{0HzKy+5M!dIZxyqQ6ni)O`xq(J#&vP8$Y`7HH9V9je=Zs5q3dBHfS5_KAv5eKTH%P0{&I5jFfxwMu<EL!3eI1!w9w=ZbT<Pc9LTy@(3;=)Uskbp1p>E_*VjYt&(+E>d-v!}Xk_>+9Ik_Sboo>Gz?qkiRbk$7k;>al9DJ;}H{T)E0sg;B)Wl%nQf=!dRWxcwNu`L)*K+Np(~S<8^O8=fOOA43I~MOcF=}Geby-OcK9lrUx3Enc3+Xl912sUo(AYrlqI5?b|&t>wmwGB*c)2D^XNbR8)2sl_)B%WJMPZD#>ERh!K@2ChR9sR5YllsNXrKs_w1Zj~TL`|4gV`b?Vfq*QrydUZ-(?l7DZbbZ+HOW8993PCVUCZOq$ijph>mH5%uK9@Y&?-bTWa;YAt`%l7SMvdu@SUQtn|L!x{hVf_Ky)Gh2a$+Gaie~Riy?Jd3!X|gT&pU3jI12-^HClBP|vg}B^q4!*rAC`otKO}Oa@h-;4UW@0RCZ6N8kOTN1@1xm-`vp<&b`3f71)`xdeviy8zPfk;iuJ%WPm4NC3f$j`vBUe0$H{y6a$L7)oM&F3R{)IeF_X*?@$1;Vl=gSf`kmT?)Xu*b^_Z(|(NI?KF@UjhTyVTzQSU+Vm2a-@65o1j%>2me({|B*+%NfgjrhJrd^!C$2)bJ=I$H?-dI293WgAoK|0WT4uL#c(^;`fFhQ_uCCtyUAY!>+M7Ij_>YRcaW*o(4FHJK((N{#OL<G&qf^O1+jVvmhcDBazf<T;PRbI22t|9SXLnzXHr;f<Q)zZUVmQ+)ReS-YQo)XM8Ai>EoxOX6Fraojc98coXOQ2`s({*6i4n*?m5CBLz()yg<49_tMPZ!Y7!J&$e);$2qWZr6(VYoXQM;wyi<9sM^7yjCXVm0Nsoq3=TAG>pGR;N2ncUI+Q;#BY3@cT&qR;eVYadpkjq;@{Vc?>6!McPh{8K|0(cXuKY{Ymw;ppr93`KMq?dzLNIei8&9ybK#G}Hc?;V7h$p;2ZY=n5_2Q}4vO)|^IymAv*yrs&{r~TR<y}Kf&|0w7FI{)ctpfMBHDf%)X62j4~n>VLrwX2QhcAF?=A=<jSq<YK0wrYn!N&dub4~zS=9SqMf|5k-%Gyv0`MJ|QEu^_Pi??u@59;~1<#>O#P64Z&+z+IfOy4MVsiK!Dc^4j`Y(cX!SKr>{IbaZzeKryD!#9Xy1b&)|L3AEzXAxh=LO&=Id|5Ic?f&LLnD!==RL#6V6FBIib4EF!5dAYKJ&Ht1iwbmSp?}82|2`N<ZtP_*phKBW!~j-P|9vwCb|{<!fnk7?V3lxuDAFPxs}u7f46J)ylJPqo$_7=Uz(dmK3u1$QT#KhZuf}zdo+)wx2zFzQugDR7SwE6=VrFNT1ULhWyuDj*CWc>12aAUuBI~LSNzNt`By_c*8x!WaQWl6!Wm7Kg>M$)<;~1)>j|l|h(5YnlofS4-X^1(N8k=ZyYcVsLN6P$WHV|2H2y{UyTur25p<LIt!8?4fVry!fdH#Vm;PIr&ZLb+ti}Q?4zP9+Se+al3z%8MNCbxC*?b{q1Qsq{JRBc4a`DAu5V@El7p-73DaFG0@UUT;frODx0$30gIru053-(6g5p<G?yIGR}C6Z<~6`v%S7GNGq&UE-!9atDlCvur&BEV`Pqf|0(21YWu0BZ^?94_Q?MmirblmB1@l4gK;0}Ds8=G{PGA(C=ow(h{<djf0*x(f^Gjp@v$^deRjcwoXvCo;J=vYAlFbUKp{Y%l^jBcDqe6Gj5^HPXYGgpo5>u(<(a^Dxxc4CKckpLDj655#ju9E#V7*_va8d_I{T4J3dzs|g^nkxU_-sAhE(FbmmiCYJ}YvoMQ7E)6ZaDVZM&OvF<KBfu61HjNo+Af3!7p^E>2#M1G6a>57%3yEZAz!=Wt5`oNyHyOkEB}`kuTniv~a2{a#f)(sl{rU+gYBG}!bhNiuUvF&A8)*uzip2Ut(O3w+$?^E85yPllI-UxwfTFy)kjxp?*_<()By8;4Y+xjniRU}33+W^@^mxFGk7rXxE+i5Hxj^37+*v&l&rM=MCye2x2#F16ppYg+U~i4blj((v0&k%nPL2c?+89eV2kyB$(7vQSaN~^u2l%eL1074+EtbUpoG6p!Q%;JMGC%xpG!^T~#Yej`Dd3j75gQqKX*U++RwePGPD=8R0V8D^mD58lmo0>xC3^KgvcGDMR*&a_c{UXC2H}E}!%VUnXpy2QJM&mFJw-#x9vF}3c_Q%SlADVn3Z{{>VoKW{>d~Jufw7>NcRNajv37P4xzNrdaI;DmLXI*oTC#bv6I#I28qF6ru%)e0;O616wef80aAv%9)JRW`XEr2LMyolT8_rI)#*vF#iI=09LT=b#zSecY$WSQCnp@*0<b>rjyOU;oLkh-;ua!oI$+R1o`-b*qo58)U9fCJ8?@fIC*WPsZO)xOCFd`s<2I6AjBIa7SXdQUM@b3YpffBa}s>QBe1Vhs>X)Hp8hQ|Upw1)%v$t*}V*0N{?#8WH|3B$|}7^aa2aynAGIlx>u-_yx7(D7~s8FOoZWXz0P15i1`NJl~X+|As#1}5)jI!IC9VxoRGt6D5%$lWZp<mT5eyfx4h39gDQxp~p+Z-ty-3@&`2eevtwc*~-|X3GnR5Shuxsly!51di$aq!S2Y)5t~h@!^dYoVk`P0U^=Gs@f99L|b7j3GLI)v<~L#xU-$PmM#b9wj~`*e;q(U<6HqXToG8n^iJ^HHMoMg!CArlcMYddB;CWjck!=&4>U70h3lS9Xj^dBG41ut6%2N<`e4^!cwKn#{@CE^fl#oU%_;)Kx+1}7lr;tW!?C3uv0!iaKwr2!c6+Q{z?Leo4gtH(g56=^Ewf<DE!gb}Os2iVg57E1EmvS2tbt&6TCmr#MuNP~f^@WV&~5EDs6&A4I&>`M<ZoN5fFc6Tu0qFc0%X^qW0?Ti73jE~)dl<eBjK*#V7RY07Vc)A;81rMR?fk79c*TB80H0FgSGKA$n)HiH^nF7KxAYjnKstaXyu?e9MqRJ(yJmp$y8qA&J+{sIxf3R4s8yalj-69Tn1<Ij6l7SfMdB#I#VzMoKzrJNT-b)O;&-CcoK(eGmUIZSuk?BOpetGAPqIvqJ}bA;%*pZz<O(WOhSzP6#)_!0aOS!AX`hSCJ7GbgbI^32m_cUg^mDCe0woDC}z@}CRqpYFC`NrHgFU0uLEG_GubRuwZ#c05rXn)0ko?fgW{aG!fX!B(G0H8nWe&Q^cfs1>I(ge!7kncyiew-pb#h~)RsJsSE38sm;)LFAU%>Cm282KZkYFn^A5T^s}Qdj2hAl=zvu`DHZT4_eA9ra7zfW*)Rh4vZowNA(a#~;O6Fb}?0N&MUKw5$3-xw~gT2hNasY+_fQG`6?%3e{{UKJfawr;G7akZK3PxDN%KHaFo9Pb?#G=8q{Sk~#reXRNxvVTCQwg=US{Iw$6^uk;YeR#p`?_PRLW8kYk-n8cs{|embqx)K%E0fB4u;k`;r&D9=^(ru9fET@GgbId6sq!u(EZHY6<iw{2r_@y>R@kgC=z9L!WZl6>+K1zVzn~7HW+;a)4RgV!zF4JefoogtJ&PHNVtDxUvK~#FLd7^pxzgYgnCy&l)tO5JJbuAu==jPwf%k3FiDrS!G2Z)IYj#+p;%~Ozz*~c4YC^{FgVZ`i3LNk;JRQq5?l!}gTVo8m!AG;47zI-%;EDDyy93kqpPp~eyn5+Jx~uT2szl1y?wnQ=Ier~Vz6(3UEM{i1Z(hfL9-$-k^^{vHH&B%<--{eN2CMP3E)V47zdPv1Owbs$=P)RIG)7uhIqxof!#JON{Ref%I1kg84!|yU>0aeG*U>VCZWZ79I5=cl9|l`K@+u?0i5bNyEbvSb6j#*4^@hT+PnCQ1r(>ua54^RCA9=fOeCMrC<_%>&7||d7(4|iEQqd*Qg)RyN(lLEE;9;E&03wYsdxeA10bEpbr;ILcs3irMon||iT4kbAf65w<5|#FaHRxN{w9F}GCvR>Ch-pnaUMSK|Jn=!LR8ciQTsNhAO#ZjA|{eFA$uc-O+x@u*MtD-N*R!KAsYy{_0hVG#3N)c;rTHrP$IB)C^{JE?Hj~pA66%qD`c_rYYA~E%gNPH2n$ijvRPfJWOhR)o=Z69-mAM(pkE^jUtI=<R)@PmS(``>8&K+MRUDJQPnFmN8OkS9VjlK)WyZ(jX<Y6KABO`OT!v9)=5rBO$dQs*GzZLtNbDV$$|sVR0B%NTE|cmSgKjWVPT*SLRH%;-`c9xK#jh4oKsuip&ZLTRZV=J1SQN8alLC<wU%d_E$)Glc6d$5u9|3w3*V87nUcio&qQI#hup1N%sNAR#Sitr2SY~7-mQRiwnL>U6yS5D4SY}f!pD7HF8Ri0ZOBsZGd@`0v$2N_D@QGzph0#$X0n48SSTPG3`URq$MdhO<UZhgoQi72%M&gB3z6g`nH<gjGTZ@oeN|CudV2dlo!1@lEwws3pM^B3$E#yGF8ln%`oDy_%4Ed!@V>nBF6UZG4V<eeMksujH*4>Os;oLH@$@EADqOK{58de7b#ILjCM^V_oSVBQ4nM&-rOeEuhqLBg%yLc)^IwuG)3qQ+sV%AnfcLJs)R58Vz1o<0$Yl?4hISPsiHh^-HUz|)^U2~^1-jd)@O`w?>4=hL&#<S+6DWetyfR&*xD$ip|v2P@}2w5IDcRb4$IMK3WlV)-l@a(z5WW7$D|L0aJsx*{7Z;UG)d#fE!D+oT{4DdBYd?b%63sTtMTuA2G^-gTs95W=(N+xVkk#L6$2XHP>xP{$O5~ECtxGQy?n9|KK!f66BVckDRO*fI*XavTM{8%QTW(7;*jdGfVdMWMrH{rI~Ha(iJsBa;kg(`d$XTqG-pCcKFNy*vq`YA9iq?@Ne+LTNuGMlC*Xzf&N!hID$x=}>}nV+j5j|1iK90K{)au^jQvLa4QBSlQWhaqkbs~uWMQ_q=3Zo*mrLVB78y<tjR3+>h^(TW?jJkgS7y{-$TC`w@GRIy<)kcN7$0D(H+LMapJ>B?n5QCmu%@q(GBAsJ7m&A>9wqd4tm<0!=vm=LD-Oxna<A+%}JeP?THbTSQUWj;CFl>)_+>D}GT(;Zqlw2C!$hkAlTk-=DZXkEA~gj*IZ5)_X048W=*7VaGk4XlIpQgwGI+BFdFM=ifaB*1k^tS20TMW0x%_`1W<{z&kCRxdn2R4fs?`83F8cN^wLP%66k0>D;)x=Hm43`=bd2Nz-!RQ7||g@+<=_&8U00-(^NngS#3>Y^CaNUIC@`6AltDzSG+X=`{SFHlvt&;=i0b=(`w3jMN)haC!7ivt#=T+7AWn@i^H7~1Z@&Zkinutt$z2uqk3r4RQ)(>LV<WAO=tZ%Ian`uqC^24Q7A&^H+B8Vq&ERu1*_ga)F4kyL#2R%IzP3|cgul$iNkl9pZ93A!uFY7~KBf4Bt2sYMV<vs0)G^;-bD<9SdoMIcP`BWgV=M5xmOVl5G76#-U*I*>A~+#HJh909})-L)B*Zmeyv(ufrS3@F-SBCwF%P!2DXmNnye3)#}C(NuNDO4C{{jdQme*lwP{x|f8Etg}$|y9?PAO#h&Y(E=rjd|HPGL%n^t$AB9KR#=Wc8IBGO^aQ&?%oBofJiz>+-mbpxaPKPSgW(zpM^{5kf4CnJ1_oeY&kL;!^$x~}x#R%G{fkwhZb*Dz2$ueRNaj8e3cVl{0wG!}N);bi4Q5I{tdP&rqpV)+Om&G#lhuTfC22!3z-C%Y5nN7+B_7jynBIf`BFqg%>0-0c4UEx}M%fd%cA7!JY6;<i;NtbMH{QZrJ&_3W^bA0o0qrQ8-9wubIE@d(QqTzCjyp8&RSKNzz|fjf6=K(yjTO^_$p;8qU?YvkH#!Ie#OWuSXX8+{sdt5<c(ok|Q)f;tG(<bIo2QJvmCi%)XbR608hW4rT|95jQJx-<u<gu;A3Rtf4<0Uf(et`?*4&fH(N>HUx}X8DCcf7;K!TrdgRyFajixdi;wkPojbSVw_mc|AYF9;+WwLwq(mR%sdpn!S2X!vA8B$q;Rtqy%7|(CQE7`1Y*+sEG+znhB9=xB;4U3V}12evJ+{_P8F{t!#59ES87RKzrafid)8$oX*3^8lqUj#rAjE!*b8gvjP0#FaS6p4(5F2?ls^s%eMy=#CKVfMrwL_Vn2_H~D1!RY<HUF@bx(f0<!F#7riU{dYH(K$WhKqv~+HER@zN+$DFP%+{1k%HK45m~Yq8Sae^275uC_`-d>As2-E(*3#Qcsw_WyDUQPIU9RcctFgx_j0XB_CXXHbNzaDOL#PmT4Gilcfv}CEBd2IgA^N_Ht4nh5K9m99Tg*>44C-{&f(^`0;MD{pj+%nb^jk?R)y_#g^62UMf-)6uK^=B0Tq<P!7SNEY=`zoLXK&via>*Lb0aip8gw<ljML6xfF=pZ{(1@DkjlV%g5QTo2m1PlqC(mM(rpsSaR$TBK$GeGtpQN@V5+Cn|65V);*2uON`#6dj9-3c)9e7-&BK92LacmfqMj>bHX4(6b9gqM%%OHUPe!FP>BYR~Kr2aPlR)ssvT;<q1gs<Ih6B=Ql6X=VL*Xl7eDQ%r4~WGF#4fdBkr<w(LxC5{)8xQio05tA*gc!?8Z(lkWBGd)VJ`JDO&(YMKtzLqy#^mAgzg8;H`pBu!n77*%~rT8JkT|?wkHz0PeEEW5L_2h!h_*RcZk(n;i0vW;1G-*1mKv#jr7637|pn$0ibS7!dx&J5LZ^xg;a`wxIZWuUkJbm(<5E*uba6dsH}qnpBfm1(HP9;IDP985M3aFTqu7$5t3a#`hdra9+XHC`T%@Qq$f<s1dMRtdi)QltYP($HEY5o%X)f<f;+M%f>DSb0T?7jaR&}g$t3i=5y}M!(Kk$BiiD<hVHSV^ko5@Sgd{LR1*Z(6@NYf4Iuhq2sV$z(w#kE1zTIkevboOaELVphu9MwjMGf<bqAe({Y(O+WwrZurB_r3#uAd&clU-YagmMiP+14;jXPs<bMTD}6v3Jwp>xBhfC%di;0)0Uj%%mo;lyl3YY|eMGK&7aAbI>xFctHt9@eJC@mY3p8CBs|Ax~-F~c#R41j!7%zyLqycE&Fez?PP7QEmbFLDNR71PS#oylO0!i3D+X!SOl>8L2U)GE`)nIBv@6oIrRTdHpfC5h0YmE4x4QQAf(h*v?9c^YLH)bYVAw+;Lc=FZdBUUyKcI)ovhtL8G|MCCYaLN_=d{THgR}Gd-ayy-N{}*T?%np6NO}upj8B=Lf^^mevJg8AncXoZLcl4%1f0J06Y#8LJk@Z+Nc$z>=iUjQL0YXHEjwIPx)LtnKlx5^)iZ=D~k(z`*cM3-av7q+;nBhI@!YM5G}S{G!-Tl8dnv>L><$j4)7(cEnF90aT4l{=?f#!r))vo_Zm0b`bgU=8i039n@sdkQMx;)PnU=F+PEXx+IY5XorK2N=G?ZJ5_MfNmoLOqB1I>=p%j%i!`oDiQ)QTOBaw`^(NVFS-wf04sB5Q2RK=Tu&_k-0O=W&%q)v8KxyUl&Q;Fq=+SAbINS*AOa*>1b_(W|o9={e{I<SQdMn;TWv9K>evj^g$DUi@r1nInrsHH`=<9Jyr$D{NwZ4Vxwo6P1j6(VJ4*!`V0@@<jKa6A>IJ@hCnIgGrjaM%bUDo`mZm`K2!2h1|t!oUj@qp7ygsz}E&wJmHoA2Dii9dTU8DHmytg*jy+LzPmAnXybNNEBV#-pOW`MhtbbW*cg@txTqgALluM$fd38LIcroUvDRCDv2v5Qowx<M}B6T*!(CWEue$66IPTWEDJNo0Ywc*)YOq{B$LTH^*sQJJ?OtH7zI)^k}0FxfTdOv7%4~RXtuU7w^G8QjQZoCIj2-kvw*0+w9@%TCO0j@UCxTx2&zQMpdK}z$s5YRYf)pPwC&V|3u-k-z8lpR%H`4-C24apLZ7MTW&vrckEipd+WN3?NVNrXITY;KiV=4y3_=l+%&3|%!TMoYT^4wk!UF^xK$W+%EcC8o0=Vm}j?syvv8l}(O}9AV<+jpn<$uW)Agc=h!YjmA#c)MTRlRT^IrsML(zag_x?0|wuQ<h(7A7Xg#SK@2uFl{bt!UHCu3T|ZixJgowO#?PI!wx9siJ@*Sf}pl%tcYSqahXPqER8^MZzKuQSOqp_Gjp7R~LUz!ET)v6Kb2qw+N`e%&Ul9G8C*S;-<)OX1vXQ|Io(YKePp%SFUmORsqp*3%hmD$c-n{)P{Bk-*`o7aX^&EQ*Oz2ve#8W64&5bTjjeS>-aYkAK#FOw_|TlnY5GjR6tNp63WmIW|M0{VM&2Do`NM&J2L+CbUG^6S3#>>Xl0kEJi-8~^yQ<t9yC=`{Wnu~px!uTO8ZtIj-aVfE4R<RncT*B4o|{N6bj{$=wzr(y?frtZo6V!Wu{zs1&O$>TdC>}OwPqir$=>Mc}I3>e~oE6S@-lQ#3|)eJ^DAGvh;64<u;UvS4^d&vd}7nU36NDn=MzI);jONHTxB(;){2l><XAzB)cNkDS83X$yQIFMtK@Bm0(!%nkeBW*Yx<x>x(NIL~_fzQdx%im5es$72+$#7aD(M7ATJ|D^N*v7Nsh=lU#X-6s1f}-9vu^DpL&l*Fa@y<>6X}N(b&-T`{(D&!+O|DNTne?9{l98%v#R?Q22jSD&Wpn@SVV6y#oGB7VtgdR})Pz*SZRCBD!%r*A-kXwts!^yQFGeb<VEoX|A*Lt{z#nqjJW;+88XQ(0*9ClSsA4rRDjnvX@YlG}cj^+GF!+}geZv|u({ne!#4eci0GU=_H%#wKflTuEpP)IR&RQ)`5_8>T6Tk^om`Q{oc;ROUwuOEPXHB~c*qoq%bm@r`X{g^mh{VxO$^iE8E5jlH#ow}2{-1cAqiD(qE>g&p{^5S50F1DRi1pGrRcEy#5IZOAOW!WQ6UDzi7=l2Tq9S7sSP<?k~qcPk<BeelIyU6ol&!5f^+8hAuK9b)B?Z{zf3r<T_?reM2D!%ZO4i>=BNHDMK9SF5}xCG2otWkJWc0YPdQ6R40?lAsc;w~$J4HJOjlX>yLxq3%=&nNez$icEL=|0{tzrmMRkFb!H%ysoG$H5_PHuv+E#bTw5JppuZMcTa644?ZjJj;IOba<!sxSoSxfbNk<j&K;FCnsRhHUK{$-*M>fY;!*~^^0ccW^LMz@<Ju2XJK35kaZ7J@Pc@}fTCP^4)lqqmuVNZ|ud$Lysg$&ng{Mqmt3}hXQu}m6s)SVKiIOLX(oMiC$aiX{agga`cTJ5fUOI?Oi(W8So+!$to^FT=?CG|B+OIHIYlozg4CL7B(4A(-PQjEK))k=2eTho@1`<`h=P*6zZsK>`%(fy!DwUO0D@F19?<>fp<knmzp0-jr-@#NKPsItQTgA0sK?RFQbyS|dD<vuEhDt;73JGvs>B^hU>X}NLAH@lZPtPjNFIGC|b(v|=`!k#H085}xTMgW+$K8i3=1JTaD3~kbuO$!1>#NE-k(GeAk(I%MNFtt^NN#K+iw?95Z3AJ!tsv0jKwJ|{fwPt#)8c_eDN-1hAA-nrW#ZQw;~NA59@1T11|^ytO~<*woL44#a4eVE1W7%lH8Vf<eIPRuU^Ao-s%@@d4Km2DZ2_cFLgWSh6+AV)G`l*2x31fgnKphm$Vd>UNKN~il4z$U-fYFAISGSbSD0%7ac9m!Z=NH{NvqC^!+RNcuR#sl_cT;07~`Ze%Zdy4DH^c?R?8Dmz?L-)N5a__Mx}iEL7gtHU@D#-Z3{{ZarEZPY8M^{Ybd43nw6-PnG7C9S`9!zJXMb3oxn8CEwhSZ=)AcTdRhT<W-I~WHIpJxB*XWt7ZgYIX7W9Fz*gEE949l5cLJ#b4GKJzE{vB#?sAk`A*mLzhm1JFa@7WfQz>IKo(kqh3*#^+DG9GD#iIR2#|0M!1$vkyL7)gIx?M@8mOYX|#aV@{jpxVknv4zU%|r{sV?9YDl@NV>T}dn#A4(=_F>sA5_Dm~Ou(yB`gkNh@P?{8!J~FiFth6{-cQU2`TGtI1DscnG2);v4PT&St$!_ON6|;~|mvS#a@xh9-eFU&Vx%q<BIX*bdDxm|NZY80<Ys2`BvDTsVaJ(=&W;c*^&8(>e**S_xyz<&CQouuL>orul=D1ox%^4$Y_Zsny(10K()BH7sBO2Sajo+Fs4Cjj_f<z!L!kvNAsBn5B!|&@W*XK&n<V01Dy0{o4#$4JG>{e?KXfkg|@JuJTk00pGwxKj+CH6cUj@B$-9ZB+u)|1zl#=&sKw`B=|V_K!KuaNNzpiRYK(<+15!b+v&{`#D<@R9^1yQmkM75M77IYt+EW-7pN-Vmq_3Y3?u7;=}RtaND<IH6vF5&0s(U5-NVfowVp!T0>)DQ9`3Xa-p6J`N;?jbfvh1v_&agoPpDE>eKQnY4JR%iDV;-YA3-DQ-ZvmPh?>VBIC9PcfFbBZ6bHr@-~13I*g_lFpHN?Nq24+OVGzL1?s=l&*wlT0)>0Ll(NDeDr|vCVI73uJCQ;lSGHdj6@+N(<p_kP#G(MEK7yTM2+!yb}W-KDz+P5FCR)L^On>tMu6clo*Zrq(#O(Z7GD9fA}?PP`dv~vCG?R(nua+DI3q#!0xbDQ@j00KH{;ALZ}b&M^~dwL0Ch&0q}8=a<<1uoj}+Z_;02mD-UbJSnN0k!8XjDS4@EI<RC)MayEfQ6)D!F)92y7>u-W)R5-k>@MS<`-GGW+<5B3q>-$zDAqHG3+$OdgRygtMlF^IlcPcRZ8Ba{tmL(yn(RVXH-m}_mg3&`MWMj+nT+Z_d@Fpxpph-l9M348F_bA&Bh8@J6m*>(k-7JfQjx3qn8NBf=afu+mZ6WJt?v+m|gTcK-{CfUarPT>JxKv<V%v&lXR*-MTMk_krgvl+d{CYkMu0BgnI1m6y^Z9ElQH@xFYu*$qluDAEwgsHuf4LD&&qc=n5x~L84{Vy7!!Kc0@5n28s4&v7IW)y=h<9w-kYhXB&om`xagPH;*%5TDly!?!j%zDAH71j|(F0cvhfMEqTWpW$Y0-(h$@i~nm3KIuVf(5LlH^WUqbDJjIZX_9lxCUYh=_D)=%Gcrs(B%V0Is@8KAOTA+d}vK(CAb|js*{mtODdrW>k|u;$_r%ZymDme{r2=^m1o{1WE+aleAtY>-jJ}K8XJl-cVF-Q_kjyE*esQjZ63qhH#FEkGzj0=K=-`^>@9t1#d7YgPNNarxX=cJw>EH6GELSfk)1|!d{~ev3lov^WAQvii(UmjETmI3`ngGbqJn+I_av6EnMDV?!boPRnd4gZJX0d@kcE)9?H~L2p?W*3;vO_;A-rhP!q2_0YiAA0>ANt^XkR^Jwf!JYP>_(R)g}yugdG%N$90wp!$;m^ABkPnk2Y|7R|zvImerL0zI#E=`O(iMm5^#^4{2@RP!z-&p>PWfy?p~~gArDzfckoRn74l*)DymsRipI>X=eld(#{6?xt)z_T0My-5-TZTtjKW}MVQba*jqN=&1UpFUY|ERZ=wkEJQDbpEGD}fYJ=Nj^f=$7ZFQ)PvSG$kEfcFOWCO`}xJ9#M$&$dr{21^IzK72fQ)STlC`_@7n1>W}<{Ajz%X|Z&NGJ$xHEST$9|{f%n^YjXp@o6*{R4e{gR!A-H=8*C%pT&M6Y1;gk5Qw~F9(Ggr59i1&l?zu;KVW5wK^6Y92^L*MBE{qPS`aAWVwvnWhya5uz<~0<MU99_$H{v0yf8nOBt5Mc3}+z!XyhfCcAi?X;D@qhVwvhFvPBnb_FA7AbAjGytRVsh6aL`xnEzj`weK0i+Q7=-YDAI<=+@u9Ep+D_E0Q1FaS+MGhstC^mjv{UbO4g3-b;%QVp7Kfh@@8upSMwCYnwAdSYmMhMN}wSRe5@&0T{3W^o(HJh^SQxP>371h|qwqg@!;YH}R5aCTT8EjFZK+u(9eVq^*^X$s*2#ionUf<K)*tsFQ*o&bv(uH-;iSmeM1FYc|2N&&^^m0896v68U35+f|7DksN6527DZXm%d>$dWqtYn?!cz3y7B90<eIA|%XF%Mdq0?HUZOVmA&B1bd^{;lLT#lsF%jzKUn_R6NU0RWOl&xJ6~-%Gt1DfwHLhJV#<&RTcxEY2i<@T<?tSNp3b0@_|WDGMzL*MQ019K#KAishH3k3?qwA7IH!8B2$*-YA2GUA)~7X2RrZSTm&xjlNU`5dBKNYH(4%kLN|tj!m`TEW)qC02MoAq*FY%L8|&+3bp*HZX7X@fBoge8!te=_N$_h38oCdHXbf*K+#`P^fDoXgl=x_9a1iE`sEFgGL3>JeF3kiJMtWc>LJK3TMFm0$p$uX~H>fdWE^Lki(l-PWkfR1Dq7UY@*lM&!4#c@xa#<=tY#;=H*t&3cs85t|pl=YQn6M-$U{Ob@XIn7m9Ld`-Iv5-nj0OAqMGhp9$f`KZls2S01oE-(epYXT$TpIiN~k@}Wgg+MWrJgeupuec^uWT~mUNW-(g-RyLY-g8t{cpt+L=aa&Zkn+EXi1(u+;6w+fA%iDfUuUW0y|`MA;x<UA$yhfi|H0j6_lMG-0f?$|&q^d4>jiVt29z<@%<u^gKixlBhM0BticTMVCt3V=%DUd^;MAywY1;%dG7@J5sUPN;s@>NVSnoR?=olkgco()It=7mQ+M;)Ja%gnkAu%%{TzhkzmnB+}aUD_=kjkiRHAXin#K#3xG9y$Skq7iDoqGI9x0kZjyKo+s25etz-JSZsuFpP5*%>G_bM{6p&i++BZgH%?gpWsITh*NDnYNY7+x6U+U|^%)>{AYaN;zTZcw<)}d(~&pK|S2S01X7wz%jcRkwYSx0dEM0oUi`CT7ILJ=ecARoG6PB%j7(7sNq_{jx}iAeVeM(z#XAB_$5MsRNLW^HA$#Mt6S1$zfUzQH(O+lR(K7gb0=#^5Au6=+-i?1r+aefRb%bR+k(8!MtY$z4+sucxmIRKWRV<NJqJM#5bcip@n;G<I)zaJ4KHyR9s0bamfQq#Hl?iYXW>-POmnvM9U0d?IO(kR&U+wJZ{sx;>##H_a*#hx2?lyQ)0)zz}5Kjs3qKJA8cvcx(*>fmLeOq4g$V`$=CH0cZqS4s9~6BU4T5xFIO~u35jHpvW*0&SCfo^I(MH)??x|2tjP{6F5RR0#y)*tXV@*JRmT5P88%{<n9RfAo6;`;*{4zf!DKUx;A31M00ri3PuzMil3oTLyn)Jsb5!QpuEKiP=Dz~iBW#Kg+t{}x76DgV}#PD+<#Fa>aU{AFMpdw=!o|Q2YOkP<1}6nQl9V$<6qDs>$~6|jP><6xYv`=SkHyVdfv&t^<m2+OnZ^&SWi|7!H+~R%x!m}0CBI65E^#Q`Uv%W75|2LEOz*M-bd>r>(>B*^-Oz!=?|=ge^8(Ydf*@M*8^+V9S{8XEV5i{rz>v+W4?`0YBge(9qD)nUj*hC-CJyE(W~P{MKM!6J6=3Js`~C2C|~Id$(yeLRo<d%uY_Ci6~Zl7o??okl({gza7tYDlK1t~V|KC|r$n-^M^~&*x%-_Jd!h8!Tg9rExy#n{K<UkbW))@~J*z-@t~*-=DhI7q4{$Z-jRDqRdC4~S3Kc?|<0$GZ2rl^n&}0FGp5j14v4iFg-^g^xV8$D9T`iS0w*2q$X9UOl05b;_Ri4P)t@!`vTNd!2uoIv}fO%p!wBNaz`Gk1`2;&b9GA+(r@%S(}8%*ZK9|XzfSvCJ*kk<D0cFW(P_?IgF+Z6vY#ecivzXSX=+~1RmkDAOEAEw{JWc9)$FYK{70x*PjJD{<x2rvH?8&=D|E9u8<FumheA@jtC3wZ;olSnW>I6)9c24tQ{bj9<=XeKua6d)jMktU!h5CU;P*t~Qt;FD7UKav)P1fUR!#M<O$s2F_*3P3vm{wXc8usX!B2QRxC!j-Mzu_}ljFvyNRmRl}`*$fCoL4!sMKDZ=?;9xul3j!#5Duw6k24p3C{3$iNPDsKf@oaWU(GARA`o&W=uN(@$X~1r*9M>AmX3?+>*$Q7!8ISI%q~j@ey^7=9v9KOEvTIbNuwv&=6sSdw2i_QgW+$^yb~S`?za?So`Y2mm5;v7OLbhC@%V~0n7)MK{zC3B?Gfl!(d`oyy2|8H_TcTch6sT71_)kGC;e%kw+F-bs1&ZQ^1*0#C1c!RNR>NA<>K>ZEm(XmL%|O#mF5x!5fTeGq9!Hw_vI|}r7vv|6j*crvwW!}PO_F}{+l@UXnnl&(oQ%07tZsU<xk`+4my+EiqvY>}EUB=)VvRG@IF^*Ng=6!-1I2kC7DR&ssmP!Uo9~QvsHR1cMZX+WFCt@nmN7w4l4SJ3K%((e1D=@tH54x%BBT?51S4>g>2VaSc$)&G4u0^Q5TuUDEn9(PGL_3TK+3d?TLCE(YsZ@7W)zYc2|oYOnFeB7FLa=Y`FwnM%uec|J_YLdkw5MV*-|i>hO&?^Q^-qiF1`t5Gbv*uDVWvjxqm#nEWS7u&yUN<OtxT(G;Nrsmb@#ISQ3LZ@eou7wbHIJV|b%TNE!%hwEf~tOBAZRkQJoFJ~1>e8Q4pRlJujR%?Z}Txwc5hj50t!)muk_*oI_2mg9{?VOm2$GunYzM&!<G5=%h#ys=`kv3O}>e31mR;zS3u>ZW)y&$JE9O@{q_8$`9yLmAd?;MXAdg#hn{3{17-@a4T*y}`1@$2|BWO9|FYACT7A)8ZvJyNUyq)=*<-Q!Lj3Rrnl&D}vP-QHB!F9JkcS9+|Opz<4ujvVbH^3C0UV>R}l&KK}D~CewzQdl;rsR*f#7|5>B$w{!u%cEYHlw`Qz%Sh4lV>Uc=reUY(T^JP^clr`B5?iE+$SiJ~J6Q#`00pWzilq0{I2a#JPbunKG>M<C=B2A3{T%$}A8-a{P6=L{%<T3iKc`IQu!D@t;G!mcuErTZEQ5Bonr7*g0(o!C75N&RE6=^q%Kup<Ngkp317mJ5sxt)hrhsiRShjNVJnNJqVfy0Sz#pRQ*8UV$E%^{fbT1p-x)KbK%6y0@Yvxdhq854y4X2j`{zjRSOoXiau#z#^HsLR9LxRUU|0P~;+lr-!11hk&?;@xO83dI_Ri=M7PF(o7xmsmh5lQ!sM8qmA=2?FCakZ&*-Pn$yN_3*)8JFLo_x?#|n;#t$^k3;*IY^F0TIJXvmStz%d+gykCrce%>gWU|vs3ofMfO;Cvg39G;;L<!Q&Fy7zO*(zs5YH_MTL*Ds3T2Db6nxexR)^%|)aXDRGoTF?lz^73Hsaat!gyBdm(YKJV=n`j@MR!$6<v`uppUp`FvDSh1FbAHe@ab0>$EYBKOXAE`&-abRq}W4kmtD(1T1ITq+4$j2C<Nd&6_q0<NWC%Qs=+&1cTRUAe@L$Wow0JF(~L0b_jp&2)k6Z`2x((!(y87;k^3>W0JXsGcZZ>-!TE!qHhpJfx?p<W}A0)fW&W+&ME;#lj)7XNCaL9gP||SPlWr@GX2U)n)uKzx<qtZ{w;zEYrQhL!dlQq0wha4n*+LyVcWI`j`0}+E>1jZc#wX>280F=aEZQhlFoBPW~x$BD?q$48bm0U85iqe(Tb~#yj`Ao9A-&iQA4iJ@VGGj&K>j#W)Ff#0Yb~cfaJfj+Mq_D(QjE-0l^a~j@cyPc!fy(ER9HQ@4Lwsu_i|z>@_wyH#8+)zxa7sK|HEfa+U=XiAXt?<&#J9);e6YW0e2yWSt1%Q7_E@vhT4I64otfS$=ztmK}tUZv1Jh%%tQ7YtB|v@qI~2y{L}!%0Y$nbK?(YKr8SInPO6Gl)>b<u{N1XC1KJBRJ`BCsuF_vK=DZ!Bq*8_*zE=JTt~1|U4j=|7QzLhjm;Flg3d4c^E(J2A{@V(Ja3AVf1f<lt4Sz7#5Ge11f_9#D5%`qv9L)co0Ta03tTgc!2GsOUBdZuq>>U%$4yE=JU3<y0!EV)&M8)YMGKi~0Y(MFl>li~53y-fA^P~MBGxDY_ydD{OW%d}G~Ef*$bI6sIGJX!dSetMB2LwO+kjOgK>V&#1Nylf5s=vygqKN<qDFuVW|OE2NMa#vYxIDBl{GFX7`BdBSset|qOC*u!%z_J$3qaIx*&Ur<6)vIbHH@bP4O(}B;XGS%QJ77Yz#WiO)xLl;aQE)<w@eYXjO)ml@d^M=x>l+_JRpeCz82L8lM|M`ZvRHFaeFe{KtB_;wBQw!FtX>le$nClsa~Ix{b}KXrPN0!6L~t7FdMDFs#S06b#TlPC!3k_zY5Z4I~F^jXB2VEU;m3CZ8M;`#Geeu$wDH@X^~<Fk#^#_3f5YT$59|rc#U$0?j<$Dj+4ATLvibudf(|0#+2K0gzJ3;bgu`D&@5jLZioTh<CwkCiw87+iEGtXjE$>%r(;5j!p+UOVPOvon`3U&Z<WMO=zn62#lhU1n7MD6TBd3B?H%pFb)0r7^t+f<{hDvFy_V!20$F#m6OC%jodGOE`vaxkz@|!b{&1_S1qE*Qq3dD%{{QL=6Uq<E3ydegY~on=%(ejXN2}sA&|^sfPTJgME-o)i2V7o5&rXK9{L?4C>nnF+7mzpa|}>3oc$Y!;iLIXR+N^_E{2Fc^a_R7h3lL8kz9Nf2Zj8(<ZAj2;l;Gtoizdq&Ob=y!rE<}UluH=FPI`yGDscAu?SY<Zwz-CI9OQ~`bj%swdBpo-E9n6NjwmOW_RHpZxuK}j#O`X#V&Z01?aXW1qo9($MN<^aLT>sI>bmPU<~sPsN&%$>bB&?J)>FZv$tj?FE%^Dk@yA}W<*d~8qdUV@z3{g+O*kIFMV<|W`+cC#b>4?D3?Mt5{f%8774itQ!N2K30@vWE4IR-B+bq+Z-qtx;yz+NEE|~3DFF%AsHO<Mm4K}$2AQ9{dBda%b=f-$GbvcSgV16@i+<jOYNsBZQHfOmWmjthf&ZJVkiD5uW5Iw~>XiW3*ernLof#H{#2ZIQgIX{gPf?b$2vV{qFt;q2wgvnYRWNz$@SnM@CLdpFQza}tb*37!R@%)fRF<_t1(LQ+8Spi<^pVJ%p@vNA)vj`@=sJq99ze2rJgnN9kai$wlvRim)NY;*Lz!oqY+P$CSuJ3+cDDU;)ySjyO7Uv+SJsbx6b&jeWy2L2OZ;FY%~i9}V+^NYEuBzXp+SMu>rX0;geU_-{)X^e+g~hiYbXkL4sd_V3J_}%2{G_$ZNEg6A(OgEIR>S!P{O#}>*ji;B-dzTSX(s+DpFRx2m|F5ODV*PNwuLx8{H}ZF=>~pN=BLNI`mGhpv<;VoH`0}t=+E9BSxxg56;hGbAXU(zQuC&l%EzU5zc-5RW`eZ!j&gF<nvV(B;EBqZfaJq<wzoQ4G;B?O`0e-?4A?xfwJ*~nT>`l&>VqbPhpB2g@~f^W4np%KDky#W?IeH=G0BA={E<drj~T%SBJ7DT~;xvf1GhB139~`-bC_)wjDeWhR(<K=j(dg;J(uS1Qg52dKs3?H?s)9rJ|KgJSu9TunGDhtQ3?vm(PS0lg6_b3goH$)+#|^p*a$scD6N1)o>cmi41#bfSnUIi}(bF)IV6AL&;3JV<dps#`qI_!8|bodb{{mE`(4-Rr4<`cCbAn@j{v(1EsmdPyxW#QuS-JqHw_kt-$BjNPN=BMf13mi$gC0a|v$iRv>~hj%K_X;UPLrB4AH=ybo*`pNKIm8pH-z@&PMG@2JCkx{YK;xeXM`G=rP}<iWd0RCIjTD1u%JZYB));w)axy)^xjUodnn`SWppq*gU*jkX5MD<=~y#adXF61P%xGR--WsA9QD;9_Cy=Z3`BeQ8S2msQsZSY^ky$P5cW1nV3@Q^6OEjD6_*ojyvV=21mq+N1N6*UWQeqg0Bp`DMY1=qM*yXpWph<WR0C;i;v?4KkrL$q+2aH&g4%7gr)yIc9E=fWDCtIYh{i3Uwxr;XQ<#2>4u&H-#<fNH~;AJ}Ol%lYlu{Fq*jD#wSbA<&}!i({*+u`1m`Isik)n;blR6U{{fs_CM8qLV<|6T2p!BXo(!gDhgW~#AVUpBo`N=B}PUD`5P_bAP%(jOtd6TLKeB4s3Sz$jmDp<m!QJ<_#|rC7KG+H!l@w$w?jk4k>?b%$WNQp%83zmSJb)A5_RDTY_&sxuX2ES<3OPm=8Kl=J!%A6mExe}H_xc2qGOpHU!Q<)6YUU-)k8Fi2NyIrs4cO$1Sds+;8Ymelod<BpgCk(y*1N{&1P+#rI|w=&#8!m05}=MhjPRZqKMQD@;*Nn-Q;A&&p6q;{0%l#EEKWOq?KbS2gl-@%IhAOwe{<8JY>y9O=^hWbL3=Euwk1iPb?_u5Ir%L>)0WDBT3WZP=4gj!CWCNe@zs}F*d}yZgs0r%%SVLc}4JmA}1zXWTkaNoj}rj#faRt4)`yFf`Y)PHIPsspSrrW?>15k+CjOxwNj}ITCSq-R<cwQs=9IklCr)Re_WZm!FgZA4UqfDy`QEVBRv6GrHEtX50e7HRFJM^hz<W4qba=fA)|exDSFF=3A^|an?S6Wo3Qfip-uv9gl^hkR}eTrAT-FZ$jpSgrn+<SP27$Os0OJJ?Ulmp27JYL6(|lGi0gcT_RU!0RYuyP(M(&q962J4(jYznxvI|*M4@oi0Mj;>%?2hXfd4Se3o3DDC@?^oNkQN{WKk<8K#C;O8)cpSqbc!X8gk%CMbFjartoIEFk;~9A6P0Sh;<|}e-Yr!zieazTL;Ftq&#ahrM8QhsIaE%lEx++w$SPPav5Y@pt_5p?EwN4sLkdwunKpNExltIxwkXlnBer9F*;V@M^?Tuabsb|nEEq#tZpoBhQyvIE}rU@FmdZV0IcGxi$wA2;u;=TaW;oc&0g996&~8Eu7v<hY@kPBMcfb?V}5jTKtU|MwHLaIm;c&0^Ni8{WYw6pH{uz~q;c1EY!e_&Zpgu0r6pNIaxvdFq?Vk(o!w-|9rWapT6ZwRcl2~X*OO`HNs=`2(Wh^Ox$$`xt3}t+1pOH5!zqd2^2Il+PSW}TH-A_S_ltwXWYNNc%`S$>rAAU7O>(>$NykxTZBjX;l=+cV7WvVPgJ{a?gomu7lBE3Q7!wnrpLe_ql_jW}5Qeq#z5wdCtSTujmti8yh#68NQwk^+y-8er<mn5V7_^}oOeI0-u1%UNbEup3CqdBrlO}0U3G|5@uQQXTwc<zXM<mVIoMich3%j~PglNRpDWj!Y4aMV!(N*9P$2a8V;u^d?z+w2ndREe0EiQZF4?3sNJQILtBu%xnGALT6DQSvjgnj%6C~FI;d=eCazL7gI3vu)W9AXyLu{vq3$uPEPeFjYjnvx0p-h@8df3SDm>HwA*$zy^0(y2+p=7tOEljb0bt`w}MHX*7zX%1x(flqwk6V|6#J>H^UGK@;Wl589%Blnwt(OL6G!^o=o5~zV3h`#^?#pp|mD^#wHuuk-C6s&tyiY%<QmbrD-+RpA2vHaMqwWD=7GrriqC9;^`5?RbI3_A9Si&8GV@|3egN|n)DJJ`(voHjPKwpXAge!Yza1a8h4#m)F!(cNOG*s3B7RB`O+3gqSW5>+3%LlP>aD>e^*$g|jC)I%206xNxFx(+H_Z($%2D08y}7--E5g?@QpM>*h9wp;>HS*=*dG9A+SS`#Y2`gDc4%9d5m*#5_BN?7LCax4~{nnJnGx%-Mz(i54*rFO*G>s~{0r!55bdNnBwNBUjFile7Yvjr^znQSnf7yu54o0Z33x!PByK=Uw%<1i`A05QWSFWkgs)hT;F$el7qU{XmzdJsaMRB{|9WPeKT`qZb2wtQ+*%6?C6O5N<CkhRlOg`pT4NnwrTRjy15Tmga=9EGGwTM;xQB`OUa#koP5fN2C>z6n^1-bivo4i=E@%$E{7Oa7EpCIELdmLhK>cu8#n$quVO)V^j2kl6366+yl_fXr|^3SB;D10a)S+BkEK<N5SBo==ar-o_fni+*^VdB#`brGm!smG~Y~*^Fc}#>I&Q-|wrH!L?|)r)9j-Np<tMxMZQynl*0SybuRSv`tn&&POdw7JU25H_m@%nAMC&<G4(wWmfgLSm8r^dB-`X8?{7MhpzlNF4R$#FzKjOXgvvPm>dS$LV&u)<5>*xOZgDm%$mSiJd!mR=k1&7-f_Nh2bg@<4n1_?){h?k!iQcjEavw+8^*00Uh@9#xV5q88iyjd$8iV6KQ8z1{Nr2+m;^$?hc`(5<CCI++-W*}OvlsoOY5vAO|O2)8YZjdftYFJ@#=UwF-#ZYK%-3o^FwejBLdZFdpEEK{MHt8Ed*e;PSOI4HDCyD7FqFg01{2&u`#cabqhLB6j5R$kn)k^1nNP@cxkEV`_R{)hw+A<p>zSXK)KiIP2)k!B=crA;AKqa%79+s$#A6~K6q18$1S>yv~<%0Y8Bq7u>jI`Nh5J4zXvLiY84RZN3?5XU+=0bGT72!8)=c2PNp{7GPA6ymWC4ZEHfS3Rn`qMVY8Xd&XjwSX0kbzEV<0CV7;XQ)kxqWTlJic`y|$uucDG7T&nO1%&EMom*z~m^rE6alSYBg)2Um^HvL&Lf*;*Wa*d_dVxWtx3stbNfRMOL&D=P703?e>j_CS+3;IzfhlLTTyz&SR*aKqJTi9-THsaw*f{&yQT2EAOtGjRu)0^S<q-rvjp)!!(BEZM>oQ!zDS-go=lU1+bWYM}TYoU+xIxb(+)$$nrT!S_85H(wb&vA7eG?_Q}#>i|6m)<v+Bj6>+_f<RskBB*W$((f3*r0;w#I>3rd?CyetWpp0n+W8*G&@^?sdr}U6*x(kSt`K3G3yuLyeKT4tnkFj8Ac5O_?8-*K>=&8o)^?{D|V&?IWEQW4ETmC_sWZ}H6lnGC_HoJx@&_4khfjyEC|2s>LII(NHsqeHH3v<zyh@p*JES+Aab4vb8}@51sr%mY?biqXBa`x89ZeB1}Lkq<e?_kIYwi)_)$EhmKza4>05%+)xt%Ay8<|?K^f4R4sJvPKDR3v50FZN)*q8*Aa2WrtZK&&Y3K4ee1hbccf6sV4(oNy4Pk5(Q8D!xr7r6@Ljk5Jd#2*ciUXA<^29*w<2_UH)Iz{u<(|zM^j4rzc$6c!S^**xa6qT%YUdCLQ+kWb2j*ZQm$p{f%@iRNS;^=AtUTAL0pCP2Q!sG?n=1oy{bBEUd$MSuAJ~Z->W6$lT)?DSm9>t0paNniF)MFqyO5u};NXz(!IuVvA2)93NTG@!za<5b>wC>_E*QB<K7-m1@aWz%YoL#MZ0&w?0s5~R-DrHo0QJK~Pw&8qajSC7O>(XVT`8aZ6u{$|D)jXf@V$jkJQC)y{8W_m0V-=fdg(Sff2hwI5HJKv?I_F@M5|GS@KoL$?epQ6-Z}Vla%tzLM;DOi@Z>PQe#{xPrN^$7i)!WRC@^~t6r&uF4Y0Xo5RjiiC!1fiWOuy!oKs;v&#o?tD7rswr7L~{I<wsJA1gw<#+rr4aVlFcfc7hU0VsK84^1yOPoeQp)4+mGaZt#vpE6or72!xzKa-!uk%}t}<w%Rpq=U%${JU5V8?h-HAHnTysD8;2CH7sVbIV2%Wh*Ys!|m8q(}y41H0AisD|N>7<Y*b?C0mG~2lKDkud3mpz8qu%+$Rr4y<{@xP8ci&P(r|wn?FAAQ=>NukMh(*GudoYT+J$ao?m0q5{KVo^_Xa}o%!j5OBui1r(ts{?AS23iRZ8#=#o)Sw4=7c0M&ey76yTZ2EC06EbbsvJU68?Y2&6vD+0_b-dfG#-|LI+x;6_i%nO?=V1rqF)?01b2fa<)Pl8(5>x5j$^dx;zZr1Y6A)&cd+gn9Y5xf{b!*rY!*O|^^Vv4rUh$(0v5Z7Z+Jggn3Q)L2h<0~A(Q4&ZYwgE|S05KrP@S1{IO}?et$iFJ%_)U2V-DH*?JG+qgpk(<VnafDx1X^zv-S?ekmOYrRGmGxFn$EkewHV%<G`Yl|X&QO^?ngMyZ>QFn{B|m-4oE;<vm*gpMQSjMZ&?z)eR9e1@ik2v^|WeJTowaN%v@XqRPLWFu%VTH5QAtcx17z=gZP;?Ot}kGZx-ot&1UIgJ)2twSV|V8+?)81Ghhcxs5}<ICJYH%Rmcc<u86&!=UQ=<X^0gJwTSp}7uI6hcd>*X-p?fw){);{QtlTbjH~!QQ(PuOYVv*%_Kow5V!nxYc$`Z)OY~wXVaA{iRPWwksrl6xtdxB(#Vop)VwStvfK9_MkI)aI;Ddr@6(rva&L&`~LW`qd0K|a7*BY3~K#6ojBxR!qM@E^;9N1h!<-u06UIf{G(d5){+^P!NcgdiRV2axc9uqGPP@4?V#Vc$z6Xa(-|AM|S!nX+g(p&_f@U!e1Q@O)Yi-A~Pg#6}MA&+mNpf1A4mjrb(D4yNSMinQX3Z)Z$BkE;9mzjjMDeWfJq8li1TZ~ZP6fD3Y3Uwk6v_=v?xMG2<)XO2zFQs=yU@TO;K;oBsy^@gF^@ZjWL2DO>qryTA>VrOOzYaJa!?r0kVQadh1NsPK%zKk~Z^10Sl2T>8Iwr5Unu2L2V`R^nC{LogziABmBbH4SMn{cAjG8GAE7>~dPBYAp5H42`pEQi@6<X~K{*~O~H-(*HLg3@QumN|KtKcg@&BE-ZX&=#=Py{F6$*{awp_`k47C&ioqpfXlaj>{Re6o6Q_&QF$MzjE7yjst^5XNsUeEG%#hGswrCOAaHwy4J7INx?~lT@zC^W5<=P`>nD4x3vBC~xjojFJMV)}ah|(Nc74JAU~_Cw^gEq!P@NM~f{0vDZQQ%(BhP@PjPpgAZ+H`16^S1$y0Blh5E$IS0?kXVlva5VzXcYzHDJT<9HKRX#Hi2T78Kahk^m(0L>&<wQOs7ZsR_ZVXW>C<R^`1k6l|_F!GeQvST$Vyenpx0JXyMHhX@i|<M)pGH_xMxDc9M&5CnTE~O25qY|b1`*lJLb)3UH3j8#&s!Kz#X(#&7K*1(=97}4x{yvK(;K5kYJ^=~NKdtZnOhQVMO|AGRkVmv(iUs=v+XFyLS|;6Xi3tYgbvUPS*TZ*^z%CYg@F<tr|YkUY~FszTa8xD#9rDA+mGyDgF(sg8ms~*#N~$>p!>Jl&uObMI6^->!#oqDEBWZtH^RK+F<HYzqUe5D^@LrC=7~h<MX{y{R3<4+Oc)1XvjD~ic`Tuz`X}Vou^AI|hey6!tK~uYq@9QQGk84!3P7P4@E8=EKe?mxI*1{|c=3t$4pt@o!xB7ZfWLM^y_Lpegt426lKW^J*)nl6A#S2^(lp=JaIY|KGof5aYo@?pMyTbyGwKMaSiIq3Vev)@SQc+)aG+)8h6jb2o4N_--g1L849yyt$RyD=()f5GEnl&5K^#n_Kt3dxwu!mPPEI|#@_>k5h$A3GFK~V6?Lk|ln~<&TcYB-YP|drEh6BVp?%mbnPYI|i0yqBhCnU-xajTF%q_(f-yBJjjp(RTbdAr3cb3P^25zJo4RP!)xu5dqBC;Y+>TA1|lZ61CJn&(1_WDR-6OHNLL@2l_#As}Z^U@#-@X7gguRt<kaW{2h04MAu{Eo$Z^NRDYR9w*t@|N39ucfMr-t~`MK7pz#2Fg7o^bpf~BK@Gcj9C{lf#O?(|j>XNzWYfhgjGGHqjKou>aqEKdWO{Ktn_RGBX~(S#gt>4?cIT4iOFEV<_(!H4-va+0gntjgzlY)9Hl{zKYtJ@arM29zKTJNorMab1--7;@nfgPzzN5-r^~L(-vory07Qku%KI-Cu)+#rIKIsxPw3a4|+DJ=6Bpemr5ev)Ex025{XW8<rUCV#0&ACr}*9#n1%f^;BwTy|BDe--iMIk9Dc;`fy|4E;7@A9kk<=5!T54$Y*;PPt)94LQD!aWN7%Pysw54)|BXIjQZep&I&Sox*}<R4`Hy^!|1F4>xQF3>F0Nefsfn{bQBkbSZK8mkF9mg}xLcPw9|FaO{A@-6zD`$UX;&QdJ;J8g)|3UhLAwR>dFx2)W*)`VAwj9Wf!mFE$w%O4Z;^yRPUbMCSbzHVi_42p!MS|K6Jf2l7Ygp8Lzp^LoVE*ik!fOX-pcZ>2gL7N0Mg<amE2C^<k)t3)zqFVPa_voUGE-E?{)UF9Ot!#!xuH|o3n+3vuk73C>?`qlE@=moCWQ|?CwO3dvp<W40`5=Z@yhYY-uPE<4DDOf|Depr%@2Us&<*%wGSngUrZkIr7`IuU^jE(7mP7S!0|5V3fSUp_|qOQ&>%&`1M%yIc?8|0Oo1DOAx6a1pa!KbxiZc-4nIk$=0{+sB_x&H&Id$&t%od2y>>ho$0ajN8~F9}NDP$`{qP})(x<O(}~OHF)2R};S%v3QsGA@c8C)?j_E<(m$wqLTN6*Xp+0m)}C&b^r1kT)f+4xlf9YzvL303(~{fl7Cu0jRRP=!8t4KRTjck$eUIGhMdZ5%M+SflYMr5Ur}VpoUaPtB~`H8qKVSXcZ=wk9dbvviuICJtWP^f*P9%pOBv4pCWf<8wnIwO+<BudMsC#ZoTph4x5~jh7m9GJ4ZmHpI^}mQzqi_8DG3?Im^S}GKYsMO2e-Hn9MS8w-A8myJ9b28n&x79<CZP%?O)NGw9^RQ_Z4t19?^BC>)KqcWfmmb`?RiCUHXbXTRZ$!-L0MYs;&XOo96yS6O@0g0oLiK_1^&5r!oE0MEeH)q6Wkd9o2uUY1@uM#2$1`f&<iVY`KKVE`A+|AN`vC2TeQhHK4xj>qy<(Hus}CP}j=*gB*Q-#iaYcfxWZq8$k0YI;YS%kIt53;Os<a-!UwTf1&=U4n;YCOy8z!hcV(LI@_KB=QKKppT!jai3HF&c3k&D85gx^*53{tvfKjWqw2^v^(S;~|2Oq2ZR>M7V?Oo)j5~iq*R}=rlDqZW=<Wu$`Un8352A0&^Ewv(^SWrZeVFR#^ZLhi?ZER`kmsq{+VxLjS@(QL->+*IFk;(xu!P^CPU)D7yoL#Z4XgH^)DP&|j+4-E=T7SNOk*qNYE%6EhyKtO_qp#<IgaphFxOYqmi7RRu!Vn=D*XavtZBVZ1hgjtU(+Al<KFdx{&l_a&<ncm_PP6a1+;ksx<-HB(6>RKzMyN6&$jRB?#Hx)rvQBBhv1xl5lVat;;Od&M1KY-`;UGPI|~rD1a_a&-2gdq2Fi9LdtQGgaNz&w+B1RU-2Dl<=fHg|aQG$k?;&?9xu;%2_yuyezl`pgml1yOEV`G;zl;3)$bW+T=ZNx_pQ5{)+(YD^B=^EkiP|~zA0>C+IigJdOXTi&h2Z2K;qHH-dz#!!<nE+gj#9qoDc@}b-}_(K*`9Sf0++D2kNiykgYG^BMBH0{292@{o&D&X{+aI5T;6-<VSlV|`U^JjzVp<;$KX?S{O9_EF743I0rdjn??K;LbT0iI#_$fnSz2}51G@tMQ*_b)pMTq2P`aJ(cj55d_kPzh=>FdV=kI*CORL`hEAZ|31vuN@3&F!rfOFtouE$;4srR^G^j|>90WsRe9pIdOA2`Plwi|uh0b9Gg6Pz80ynPoqXE634#_oC-IOpC8&KC4t!iXKe#^I07C1?U|FQzzx!Bgn${tc!@=QuiN(YZ`M%;N&)aTrtV#e8?Zik*SZNrat8=O_kGqjM3R?H4f*bPl6)5}ottZ2c`dSjc@y^fLO6qjLm<yMGJpuCb#o=*{CFZ~;eX|Ad3g1%vGD2V7rwHE!AM0u1dN2-&^cb<EXx5FpUh@4)tc#s$1{e7Eaa`1;T7c7eS0ANin52buJQ2IF$>aqN)oA95kP9D&c{+9`0i1x|eg-R&QBX^&}p!L8o%F-TR#-lfgc=6wefoj62^jz8tns?MUb?UR7E@87{W`bA9eC70&k^$G06FGCTT`?RZ6P_r{G;Dlow;qAU61dq$>ez*4ifcCyEn)>hkS`+^L#PxXK=zl;tL6m%j3UlbIP>wTSc6qAy0K97J5!ZjYv?E8LAy1)m0iAtETt23=4>|?k&#{H~JPnCX0}UwHUTB_QU_B2#?ee&M?tjLL^S@6(=Fqmk#G>!`x=XL#a};X1^J^}be)t<$-(vvV|BOr1PdwxLl}kJLb(c%K@HJ#X^lg6z`$v0HBYF7;?6||vy8h^DJpC+1e40o62_r5)>$>b}-2I;v@fjZRXG-y(uD?JEh|qNR=d>MxzhVV0|0nb?h~ML`2i@9%<5+KW_I?v9|4qbY2eqb0+`tsOpTiE``W#SpvCm_3K8oOT1U^CTq32wBZH@N}Sm&pBlT*#9_R{w_NM-kh?;`#Ya!<ZM`~&{#v)=({+qa-0&cA^D<3D;D{99ghdGu{RaY2Fo7hZC`%dPGE5fnRcjNE-cMtA!em#*!835tE<B^Pt)?yvD&KY+Py{h3SG55DZ$?bh~yS3CO(;GO&#4*Q=|LmlG{^$|BT)c&8lKI(2f{&S-8EU&@GkjnX=Lx1i5g=?={+kPJN+<zWu9sd;yi3={xqiNsN<~=6_7bN>QCffEJ*C!y?3of_j)xJ~2n%~o!KIwiuaQszb$$b}zC3pN5c<S75T&}8puOdrcbbZ>b9s3Op(NjqGb4d62Z(Rr7+Wz07{Q8}1_N=;DKbUHoF8;f4<r4Vc+)${C+uT~!<t^as1xBeliq3g-_B;g6NpvowbLe4k&Y`noD+bZIfX=Q*+(+Ho-nY6zo}qIRo%86N1*+QVN5I+oC^);(IY<sh97pHqHgpi>B09%0_AEN5F=9VDmoc~<>FwO+c5BVrBRWbe_?!2b-t>JpwC{z#(k!wSDC#GF2YhwuFW~G)-(__6{vMnQ=p4I*9Ei?-L^=5faCZF>oFnM$|0|-Pa~6Y#{shjMKe^7kwH=pHw6b4d2W@}MjhywkPMq~i<gC4qxqszuJjx@s^N0%=arQCyuicGXA9s_uhKSz~`s40b-Hk^$^d0(+z(pKyryqC!)~#IvsP1MvK+A6W9d^*=x4Cg3@7@jyZ{Yab&^`M$H%iQ3`#~_eXI|}lC$<R5qxa}dn)6SvY|)6-|A_5&?qhCjw{3fo=!uV^dwCDE;g){^=jcbkIgh@BkhQ+`qu}gB*yXolGyg04-iatMYV}<^!8wP{al||E9=8qyf1eu`MV~~R4`PxJfpY+Y)n^cP34=TKyIuO}Cy~Nu+`4}0!|v@KZT~(<7P!0*>K)htV@%uk3AbO{`XutulWv!G@-uFi$L;xqP7ME~ZZY(SJSg~|0;W9u1#q?=0%!jr_eVY2Q3z|NAgt|yXl)xh7Y<STe-?V>Up>$(TR!XF=V{#iSvQCW&*!Ki^^bd?eGYvV3VHl<?oW8MbDzaRZTp-Xw55Z{rs(YWJOnlE-=IA64(Uw?FwKF_yFcU6&V1hOg_-gxeV#pKeAWXkf9j+gCBh~6RG)ytRB!oraL#`b3VZ;4r_tFC!Ro_bbTg0U{|{Yurv62wfB6UQ!yfIzcd=UN>_g`WIy=zW{X-mP=p0AK<2tI({hHo%6q9cG0W|l%A3&Whd=GrvUI6FFDfibs+DY(gmtKHTcJUN&p$?im<a!(v?RXK2cJ_zv=RDe_(|~p0H1YZ|&g&<z{Z78<{<f#_z>99L+wXbSVoCfvA=&bIto-(ui0$^kr}`q$t={)zw_d&NEI5~c48=P0e~{IF>VCna?Ry!T_Viiehcl4v-v5IBKm1cS4#pp2xelLm>t2udB)0GiD)XN~UfA_Zpn38f(A@G0<a6W~ZdgFR;{FL#;+NQkzd#}L3Xt3fG1|djp{Tmx_OljtS_&EYH}B_I^fQ;K-!8(ZYWts&z0f(1&gnm((EL3{fUo-aC6uNJJN|13?)($@E`qP>*l$p1zKU`cVTaLoo*bld@FF;u&^hrJOz}J9gG<1Yn(HjD-osv0n1Aiw>ILN$`x}UYev7k^_ow>Yb7~_ziny1zc(!@9ty^Fk+l|gabdIBQ7M=5d#jbY$5{J=0A^z@%JlH)4AM)&gWDg<AgW&8(=lFvjU`KXAcTRUZy^jYj0VcH2$%j4ILI<{baP|P_z{8$u&8J_)B)dxVx8i?)(VN}}#CE-vh#h;Bi0ymKgOd$7J09`4t1dnQzO9dXKIGN*0fKgHs|R?|^H;3-$FK%x-sais)s8-fHGUjxjLva%cDxNxb?<{NoW&9K$PTLPo*kY~dbKSee6(}uY<&l&MQ6*~!8rjwU9WlAwJWd{lYYjFv(CFI>DKpnKIheTJ^^SucVauCa{`@n=$yxh-S0$7?*eBpI_GyF-n%`z$L)Qq3pjV1%j&W(W2p|m#{)wHouj)vZvFTNJYVr@dm*fyd_R)=fCrZQyOA6^r#|SxwfXnFs?J=cyWS4P{U?__;S2e33=4nwLsa+^AM!lw)h<FnyRZk_=VKm?`PqA2^S<eYhCjUzTKXb7JJC4^u~pmu89Vh~plsX!6$|*Ukh#vji^bmdao(68$NtcExkxtrzzeeB(8oPL^fsR25%1>_r!nHv$2~848+U!e!@N*09-;pKBTD@V&yT&0=RV<aRrx&s;;PoFUDY2I6>EZT6Q2UkU>OeX=M{s`W1v*Rr|Qy^z%(ZgcyN*dXYXe{XT4hjhd<>3QGSNp{h#)@wOyZt2H5unkLI0G^(oifPrH<XbROGm$JdDT@vlKS_B`crJ*Mpg8C-P+or7P(mO|&!m%%ywAK;w$3OL)o0-109D)<h4)$>c>u|qiU4nYT=dJ23O|J_riT?WG1>8HWD{ItiV?Lps#uR^<e*h4;?(vCs$ecwQ?N8fIAPJF|ILhw;v)rn&s7hrzgWsk{izQ+Umo}nRh=@}YAN1nyh&)}eZ20Fo|AHwQA;lpjUZ$W+bgJ7>d^{nTeK5gf7Sdec5c^7*RX20Xx$N(p>4BrN*o4t<;brM=wJ3;QA=g~d%9S=^Z|L$t~AksbZUC)Pn+Uf6laNFy{nB>4IWE~LeRp-z-{XNKY*Y_}p5ywt>yikuXx=L4h`0rn!PUpT)bvpF}s?+HodO)0iA6x$i*ch(;K2W1B{?PNJPdj)TDzP1%J*Pb$R?EKPlCx3u0Ux%@X`*rbMM!=Yog+WO9z<uykFgukIe7-l1(;Rm$@ddP{t4!#9rgiZUU<p#C12z5mk9D@gzSIW^B=y(Z7&0)mL0)LpZ^UKKZ~_QXWLJ)4ba(l9tZg^FvDMi?+`i{(Af_uwgry94BhHwUw6^`a~w&X`8_t+MQ|?t9t(RBIqi4I43}sSK99|Q=~al|`YHiVVyb<AMCthlXbQLfjBDPrF8u`rpS+Ak!ni|!0tVQ9+4Fs$b{K%#kv~#D{wIva(^#jSfA+lSYrJropvM9FBZQv(v**XY#shyQ=r;j+2BEwD;`s^S{uu|l>p9ns0IWw}!frYE7thN+?c`rj$ov%r*<TSwKfzJ{529TDtLLXan4>@v9NpptvikE7`wB*#+2Z|~PrHm!haSYJnoqm+!+un>4tc+Xda$+?{xu#u<kj3~51}kUXUkLGl5Jsa=TlyN=8UTExNwI5uFF=EZ7EqsVLp#cbM|ds5-!`lq>FoDjqory#~<{1wgnDvMd*2QPdy6YlMi{_)!QHQLNB9p`4O+KpMApnYoB)Z5h&(`w|ik>yVdJ~=>^KV=Lv95JmGa~M*vYf@J<LGewP>28|{^%A@K_=^uzwg1IKoHaXcJ;w-<SN?|Zz!p6~MNReJ$mwdLL3NBwN?yS=zp_#(CuG`Ra$E?lW&%bk6X_icXdC?wLhzZdJ}-^)Lj-%Cl}SH+WPzvg239e(KF1G~IC{Ea7e0fy$?UxnK7dwow<X(!(61F?qAC3Lp$0%s382Y309#;d%apYTIxUfAV*r(fIgKFZ}?STIoG+`q;CnD={SzS4L4p>liQPi=nu{nWbP>RUg6)%9O^pZ9%!kl-JrLLUNcKs%4l<qso+Xn%%Me-Mj&W{>wn{>ICDyfbFHs{Z0q^-|~G+uS9+?wC8R1=WYKT>C#t<vQ|FudjORN4+0`CiS2A2=wczk3skC_-81|@qJjFeO}zZ-UU(G&X0q${}bSBeUb|KVfPb({aDETPkNv9Hy(S^tJS&vFpL5paXa=_9PFuo3JY-dQ&fOU@TorgB$Q&yLGNe$+VM|eLwp*_u<HQ$_JVSvo%<XF4`RfzgWz2Jf*1SbfSdaD80Nq4Dep6o|5IL1t>67wx78+Ja5q_X*VR$+EEeI|SE&fwpN1lA0e|(;FN1ULDeR=9UYCCU2spdH;XUEkc7F}#zQEzHK|v0E9ZUFiuiNEyKkat1_BVJRya3F-^IOE+hfWZ4AN@A4^nvHRo~jGaf$#XYyg%@37XU@ub^=EaI=i03lbEV!-KY#8i=ThqTU~wb+uon}wQb)4gj47oeF2<H-v#Fa`u2Sf`{a4-9du4U?{#Tjc3dgfx7<xXhw_{}MdjK1eJamI_*7l^9v12p4z}-mnb+$*;Z}O-H-5l7@sd~bAO9hY<0C(UX4&~;aE_h<XV*`>7ya7))7T$?qwV;C*TYV{jJ<`htuK0+&+mG{?a<!-f+Wws3XQz&qF49tJBQNe6>zql2iV@T;5&7}t5+ZRt#?b6w(X~0uXg$zH2KM&dTYEM*Ng6Arl-l}oLku?c&rMB=*}%Zp!s_cScm@%gm?Y{ob!M5zO70-yv66%j$Vcmp4sBV3F?<X6Ew0Xs&GT$VIQ{5uDAMNQTGryr@^V(1JJ7D5BiYde+@kM-YQ_Y6L0nHs%kv&R)D(LZ`@7quY!bIANAp0#~%2=SO+RDzwcG|+>36i{s$3d&wG5D|JXnIT>814K3Db5?NHF;??(KsJ0Zh^pukj}+wS{tm3HV+ltJ5ky0-6KD3>1dfe!r+@NInzoWt+&d33+_e+x4k;HauI4Bwx#dw0X%D&?mVcB5=5I%y|k<6Nv&gRbIeY3WotnJK}DXd|VTPN`C4a9dMBjj(8@MMyN%1Y->tFk+C^0H#W7q@Zg^YRaT-#sQlSsbp%3A?bVeymxZ<m`*p`^PKNJ-}jy0d-o=MDzYme-zm?_US5`cwAZ4Kp6$qddWXy@i*yZ&WUXU`Tb@Q2o3=X#!sb!=aI?)38JU)msX9dV<F&`y_N>zuHhXKGura|%2C{49|2ZVrE7_1;ubu|EK)*%Ng!u4~tkpk<;<zk^QG^<IqSjcCB2(`e*vdx(zK7F0vhPfzV}%AAurhVKFn}IebUp8Mgv}wenCTZB`Ii*OF{T<@0&3?umiOgY`uAqX(zFkKw9BIEMOnOzq93)qp*<*iUqVscEXN1;aBmaLE@`HBOTTO(BU)Ne)a;QndPTPGcZ^Ul@7ICc2dedv9CvFQ=GWfpSXS$67;-9&VjvB3@O9^O*vz!yzzv|;Y<d+ls6ULN{Q!#85fr^|qS*9%6oazpeao@jeFDD`yN87-aYW!7Ran9n0U<=ta^FPj6fsRq7c<06@kMcom?iSXY!MYPQ6MfAmx(z6_u7DaXDAl0{@IL0R!o>y)V(I)Ad4j?;>U^`kwo&K5%6qjSPEuv+VpIPxHZtBgKmQ&Bn%8ds#C;8VuA>ZJP|?QWHCv^5}%CP@j^3!H){;Rh>B6^Y6#0pd^T!JIT&>}hutk<_ldBZ3cFjwrpOD%tYAD}CYy*^i4u2XC=?T;GEQ|LvpzD^C+Ss6%$SHq-L(SFwksTWbI1*b&7x%6q!^|YH!z5#AwqEj|4M`zPa1K|t0Zm|#0^X&&)w|^JDR9_#GK$RHQh4PU1qxLZ1+LiU2nUU!o4*-mKM)<H5w}_vj2;WtMq*<%jKZd?gQ^<{bBHNTsHM>xOUOMnLcTsf4k2=P5%zIb%I`B1Jp)*vBAt(><D8gXO#a;vWUDTGZtS)Z6&q)Gh;b7_;N~J=6r;HDQPoy7rB=_NT0XBW7N9Ib7bHH<ueVO{ejXhCyU7i<Za{<ayhw*+`wF0!KYbo(CWK=_1Ap$x0xeDo+U@fiJgl1WFh%=av}M>PL=bXPBmv)|1s(hu)UIehTKE8l1ItEkOQP~QgJGI1-XFy?n$*jW#HYoetME;4}5|?we)!&d<oa<)ZeAv2lnGS&sb^abt#`2<Q(!Ea!Hr+y`yVvz7@XuPrAnTejQ^rl3U3gWFy%^wljAI>|y;3_%F71@_df^`U`biMx<M{N6C5QmE<C_oLo&lNIu#<)|Yp7WWAd0JIN-p6)ZY$WuHVQpGdQ0ztHAudW3pH)2C8jtm&6fFW2-D>J^%Pj`|u+|CD-C(=Sl3p&q1unW^+SWSsmGIhXu0m;&pyc$4iGX_JBskU_>bYw>w(pFmC|Cy|rMDU46+>&Esz>NfQl^bTkRU<SMt>;VU~_^TK{r0G|9^Y|=R|0?x}rWaE$pzeK&HNOR@6#9I;+O5<Vf!_cVS{~2Wn_s!S;PW`S8F8iH0kRET3|44)DrtM_G5D>4_8z$2H&$=F)$)Sg388A7araO^Mb>EjHEHwzJ1X_iTE>~5{0Q<a^PM9H$szKP7T=-SU7Fpe*)xaLv(P(-2Mx83Z-61R9R?%dWP}YspJJ)?iGV}k#WEe*r&?;?X3+LN-ah49YP_?_m^J=7#+hd>Sb#i5!@2AB6`1qJ$^SJ=otI+rD$@I0On#sGQnHL(M&3b|lXsHK$-BrE<lW>w<PXRS@?LT!`9pFQIo~%I`AKMY`7qxbXv_P&6xs&H{{mbLyOMSl*RR$ZyT44|`pWyW0{*{ady}?b-a7B~t)KrsxEhtU@ZXQ7)!N?3&rL9r!=LZ>J1{mu|B!i4Y5U`yzh3HpCI3dACeM%`Yw_MW7=V;QKmP`|f%0)y2R3VCosNJ;Xooa?mYsXPLge+f>aT>nfxMASkT;PxlMC(KaeDofLhl%7-EY%$Z(euVYQOGf`${sSjsG#uOV3*ky>Fbn+hqm%4zh;S{hs4I>d6MOU+ce-{SRyBICNqB=bWb=L0kkre*|axe7$r3F7yKE$H+gC$H^1qJguK%%`VaG+i82x+v`yICba9x8_1hQ?mmXev*>pz+6TZD;3u~7AENyquG0l@73>lE%MN9`$-L_?<gQ}?`Wk4J+IUm{V;#M?eTb`pPrc^Xtl8=Rljm|V{{2-X)c0gD*p3`mi`?ggw_o!u^*yo(?H%y@mQddt-=_a!q0aLX>KSeRr9yo__GtQ#MDG3R?b`#W^uc#6`Ezm|*zcRG_ndhM`XKaQ((hrilKQV`Z=|jF_Zamm`fU;F{G~{5%wFVumU@49ivHE)u(l7Okz6~Uwzsa&a{cSboy^lf?gGCs^7-+;!uI`O9PO{tZY8~sx6W^9y0`zwsh=R<^ZEJre+%c^$#Hhm@6Y6i<SDHmZ@g!ye@y;^{3m&lHeSgmaZ|45npZ+-ovX&Z5W>+ymHP$M^KVh(Jd0vQnK~2OarIOoPXXSO5tLttSkzCz{||UsGx3Yeb;#Ll;vK(0_4UjoyceUs&QO03F;M>v>Tl!sqX6o2<T?!D{R*)w4Zi<FUxs?2+@pvZcT=8i{1;G50|Wp7000O8AqXJ?goZ;@-T?proCE*>6951JL2hJnZ)s#rVQy(=Wpi{ccx`Nzlg~~RK@i5j-dWs{1zCs?jT<j|GI5C^8sbrJhIo*~JIu<TEIVvw1|l~ef+G*X19&n%fX~2@C-A1fuIX`y(M_}`-*k7?S6@|E&qRuK*_NF8`qq+GG#$g*`Z3r9AHXTN1Yf}q?{v~U=z~{a2)=;t;2Nx<`wlE>tV5;RMtP)<`lL)bmSb#E7u~w$E$<<gKRxfYgOd03RItvp&#suEyUhMG-VA2K?4t|3UUQ3i7p`X|@W+~9Il}XIt%v`>s_IUESJ~1Fy=0EGp#f{DgD-`Z!9BD-cd<-(j@T!m!ZPP-$T_f6raEJNOx0xt1M(*5B*dP<ex?Ir3wE!y&f~oY@WyE7uy5xb1nqUpK4Q+`=C;3GUu+5~yFFtRc8ZDhbi{k#PWr%3;H%#wG9%`VPOQ@5j4u68%=F%47`fH)9QZr%<J`r1%Dm8W?h)vN3AkxHm(4ks1Gyd<{ar{u8FxM3-x2n!n%$2@*7tcj;U8EiKXvoRe0&!tA=WA~L~-EF)p$>W?T+=V<aL7op51+E>jv9WBi65bHQQ3CcPr$r>Rk@?wrcgN7PlH~QA0lpJ!xwB9GfAgvd@Pcu9l-c)m!m%rPqv$8ojT2>}K4_FHlPZ1ONa40000X2q6LsP&N}N0000e0000E00RJWWpi(Ja${w4E@5(WV*mgE0sssEC;$Ke000000RS8T8~^|S0000000000009618~^|S00000P)h*<00RI3000O8AqXJ?hb2B4GXMYpH~;_uIRF3v0000000000q=5hc002!zR6#9CPDU?dZ*4DOZe((AX=E>Ab!lv5FK2RLWNc+GVQ_FQZDn*}WMOn+E^u;haAk6IX=QUzO927^0{{R300;mf2q6Lu{l-Fe0001Z0000d00000000000001_fr<bC08K?yK`lv6MlW_{a&u{KZY^VPZgg^QY%OVSW^XQZWq5ROa&L5RP)h*<00RI3000O8AqXJ?%Aj?xv|IrId<z2r3jhEB0000000000q=7{N003ibVRLh3b1r0Mcu-3L0ssI20000803iq=0)&P`RNesq0GtE>022TJ00000000000000YUI73>Ze((AX=F`dZfRy^b963vZER3W0RjL300000001EfAp#3fHWMfS001Ze000gE000000000000000F<=1za%FRGb#h~6b1q?Wb7N3T1qJ{B000F51psmZ004+!0RR91'


ANDROID_TOUCH_SAMPLE_SECONDS=1.0/60.0


class AndroidTouchPacer:
    """Per-stream high resolution Windows timer; cancellation checked every sample."""
    def __init__(self,cancel):
        self.cancel=cancel;self.kernel=None;self.handle=None;self.lock=threading.RLock()
        if sys.platform=='win32':
            try:
                import ctypes.wintypes as wt
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.CreateWaitableTimerExW.argtypes=(ctypes.c_void_p,wt.LPCWSTR,wt.DWORD,wt.DWORD)
                kernel.CreateWaitableTimerExW.restype=wt.HANDLE
                kernel.SetWaitableTimer.argtypes=(wt.HANDLE,ctypes.POINTER(ctypes.c_longlong),wt.LONG,ctypes.c_void_p,ctypes.c_void_p,wt.BOOL)
                kernel.SetWaitableTimer.restype=wt.BOOL
                kernel.WaitForSingleObject.argtypes=(wt.HANDLE,wt.DWORD);kernel.WaitForSingleObject.restype=wt.DWORD
                kernel.CloseHandle.argtypes=(wt.HANDLE,);kernel.CloseHandle.restype=wt.BOOL
                handle=kernel.CreateWaitableTimerExW(None,None,2,0x1F0003)
                if handle:self.kernel=kernel;self.handle=handle
            except (AttributeError,OSError):pass
        self.mode='windows_high_resolution' if self.handle else 'event_wait'
    def wait(self,seconds):
        with self.lock:return self._wait(seconds)
    def _wait(self,seconds):
        if self.cancel.is_set():return True
        if seconds<=0:return self.cancel.is_set()
        if self.handle is None:return self.cancel.wait(seconds)
        due=ctypes.c_longlong(-max(1,round(seconds*10000000)))
        if not self.kernel.SetWaitableTimer(self.handle,ctypes.byref(due),0,None,None,False):
            raise RuntimeError('고정밀 이동 타이머를 설정하지 못했습니다.')
        if self.kernel.WaitForSingleObject(self.handle,max(100,math.ceil(seconds*1000)+100))!=0:
            raise RuntimeError('고정밀 이동 타이머 응답을 확인하지 못했습니다.')
        return self.cancel.is_set()
    def close(self):
        with self.lock:
            if self.handle is not None:
                self.kernel.CloseHandle(self.handle);self.handle=None


class PersistentAndroidTouch:
    """scrcpy 3.1 control-only channel; one Android process owns the entire gesture."""
    def __init__(self,device):
        self.device=device;self.socket=None;self.server=None;self.port=None
        self.lock=threading.RLock();self.pressed=False;self.last=(0,0);self.closed=False;self.input_size=device.native_size;self.last_up_at=None
        self.pacer=AndroidTouchPacer(device.cancel)

    @staticmethod
    def server_file():
        import base64
        root=_input_resource_root()/'tools'/'scrcpy-3.1'
        root.mkdir(parents=True,exist_ok=True)
        path=root/'scrcpy-server.jar'
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=SCRCPY_SERVER_SHA256:
            data=base64.b85decode(SCRCPY_SERVER_B85)
            if hashlib.sha256(data).hexdigest()!=SCRCPY_SERVER_SHA256:raise RuntimeError('내장 터치 서버 무결성 확인 실패')
            temporary=root/('server-'+uuid.uuid4().hex+'.tmp');temporary.write_bytes(data);os.replace(temporary,path)
        (root/'LICENSE.txt').write_text(SCRCPY_LICENSE,encoding='utf-8')
        return path

    def open(self):
        import socket
        device=self.device;remote='/data/local/tmp/pad0-scrcpy-server-v3.1.jar'
        path=self.server_file()
        wm=''
        try:
            wm=device._shell(['wm','size'],timeout=2)
            sizes=re.findall(r'(Physical|Override) size:\s*(\d+)x(\d+)',wm)
            if sizes:
                _,width,height=sizes[-1];width,height=int(width),int(height)
                nw,nh=device.native_size
                if (width>=height)!=(nw>=nh):width,height=height,width
                if 320<=min(width,height) and max(width,height)<=16384 and abs((width/height)/(nw/nh)-1)<.03:
                    self.input_size=(width,height)
            _update_input_diagnostics(device,capture_size=list(device.native_size),input_size=list(self.input_size),wm_size=wm[:300])
        except RuntimeError:_update_input_diagnostics(device,capture_size=list(device.native_size),input_size=list(self.input_size))
        try:present=device._shell(['sha256sum',remote],timeout=2).split()[0]==SCRCPY_SERVER_SHA256
        except (RuntimeError,IndexError):present=False
        if not present:device._run(['push',str(path),remote],timeout=5)
        scid=uuid.uuid4().int&0x7fffffff
        forwarded=device._run(['forward','tcp:0',f'localabstract:scrcpy_{scid:08x}'],timeout=3).decode().strip()
        if not forwarded.isdigit() or not 1<=int(forwarded)<=65535:raise RuntimeError('터치 연결 포트를 확인하지 못했습니다.')
        self.port=int(forwarded)
        args=[device.executable,'-s',device.serial,'shell',f'CLASSPATH={remote}','app_process','/',
              'com.genymobile.scrcpy.Server','3.1',f'scid={scid:08x}','log_level=warn',
              'video=false','audio=false','control=true','tunnel_forward=true','send_device_meta=false',
              'send_dummy_byte=true','clipboard_autosync=false','power_on=false','cleanup=true']
        with self.lock:
            if self.closed or device.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
            self.server=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if device.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
            if self.server.poll() is not None:raise RuntimeError('Android 연속 터치 서버가 종료되었습니다.')
            sock=None
            try:
                sock=socket.create_connection(('127.0.0.1',self.port),timeout=.2)
                sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                sock.settimeout(.3)
                if sock.recv(1)!=b'\0':raise OSError('터치 서버 준비 대기')
                self.socket=sock
                # Opening a connection must not inject an unpaired touch event.
                return self
            except OSError:
                if sock is not None:sock.close()
                if device.cancel.wait(.05):raise RuntimeError('재료 파밍을 중지했습니다.')
        raise RuntimeError('Android 연속 터치 연결 시간 초과')

    def _send(self,action,x,y):
        import struct
        if self.socket is None or self.closed:raise RuntimeError('연속 터치 연결이 닫혔습니다.')
        nw,nh=self.device.native_size
        width,height=self.input_size
        px=min(width-1,max(0,round(x*width/nw)));py=min(height-1,max(0,round(y*height/nh)))
        # TYPE=2, Android action, stable pointer id=0, native coordinates and pressure.
        message=struct.pack('>BBQiiHHHII',2,action,0,px,py,width,height,0 if action in (1,3) else 65535,0,0)
        self.socket.sendall(message)
        trace=getattr(self.device,'input_trace',None)
        if isinstance(trace,list):
            trace.append(dict(action=action,native=[int(x),int(y)],encoded=[px,py],input_size=[width,height],
                seconds=round(time.perf_counter()-self.device._input_trace_started,6)))
            if len(trace)>1024:del trace[:-1024]
        self.last=(int(x),int(y))

    def event(self,action,x,y):
        with self.lock:
            if self.device.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
            if action==0:self.pressed=True # Preserve release responsibility even if send fails.
            self._send(action,x,y)
            if action==1:self.pressed=False

    def release(self,cancel=False):
        with self.lock:
            if not self.pressed:return
            if cancel:self._send(3,*self.last)
            self._send(1,*self.last);self.pressed=False;self.last_up_at=time.monotonic()

    def repeat_release(self,cancel=False):
        with self.lock:
            if cancel:self._send(3,*self.last)
            self._send(1,*self.last)
            self.pressed=False;self.last_up_at=time.monotonic()

    def close(self):
        with self.lock:
            if self.closed and self.socket is None and self.server is None and self.port is None:return
            try:
                if self.pressed:self.release(cancel=True)
                elif self.last_up_at is not None and self.socket is not None:
                    # Repeat on the original pointer stream even if an earlier
                    # UP was sent. A sent packet alone is not Android confirmation.
                    self.repeat_release(cancel=True)
            finally:
                self.closed=True
                self.pacer.close()
                if self.socket is not None:
                    try:
                        import socket
                        if self.last_up_at is not None:
                            remaining=.15-(time.monotonic()-self.last_up_at)
                            if remaining>0:time.sleep(remaining)
                        # EOF follows CANCEL/UP on this ordered stream. Allow Android
                        # to consume both packets before terminating the server.
                        self.socket.shutdown(socket.SHUT_WR);self.socket.settimeout(.5)
                        deadline=time.monotonic()+.5
                        while time.monotonic()<deadline:
                            chunk=self.socket.recv(4096)
                            if not chunk or not isinstance(chunk,bytes):break
                    except OSError:pass
                    finally:self.socket.close();self.socket=None
                if self.server is not None:
                    try:self.server.wait(timeout=.7)
                    except (OSError,subprocess.TimeoutExpired):
                        try:self.server.kill();self.server.wait(timeout=1)
                        except (OSError,subprocess.TimeoutExpired):pass
                    self.server=None
                if self.port is not None:
                    try:self.device._run(['forward','--remove',f'tcp:{self.port}'],timeout=2,allow_cancel=False)
                    except Exception:pass
                    self.port=None

    def tap(self,x,y):
        completed=False
        try:
            self.event(0,x,y)
            if self.device.cancel.wait(.035):raise RuntimeError('재료 파밍을 중지했습니다.')
            completed=True
        finally:self.release(cancel=self.device.cancel.is_set() or not completed)

    def drag(self,points,checkpoint=None,max_seconds=None):
        device=self.device;completed=False
        started=time.perf_counter()
        diagnostics=getattr(device,'input_diagnostics',None)
        if not isinstance(diagnostics,dict):diagnostics={};device.input_diagnostics=diagnostics
        diagnostics['touch_timer']=self.pacer.mode
        gaps=[];last_sent=None
        try:
            self.event(0,*points[0])
            if device.cancel.wait(.08):raise RuntimeError('재료 파밍을 중지했습니다.')
            last_sent=time.perf_counter()
            for index,((x0,y0),(x1,y1)) in enumerate(zip(points,points[1:]),1):
                for step in range(1,5):
                    # Never catch up by bursting stale points after a scheduler stall.
                    delay=max(0,MANUAL_MASTER_MOVE_SECONDS/4-(time.perf_counter()-last_sent))
                    if self.pacer.wait(delay):raise RuntimeError('재료 파밍을 중지했습니다.')
                    if max_seconds is not None and time.perf_counter()-started>=max(0,float(max_seconds)-.15):
                        raise RuntimeError('제한시간부족 · ADB 이동 지연으로 터치를 해제했습니다.')
                    self.event(2,round(x0+(x1-x0)*step/4),round(y0+(y1-y0)*step/4))
                    sent=time.perf_counter();gaps.append(sent-last_sent);last_sent=sent
                if checkpoint is not None:checkpoint(index)
            if device.cancel.wait(.06):raise RuntimeError('재료 파밍을 중지했습니다.')
            completed=True
        finally:
            diagnostics['move_gap_ms']=[round(gap*1000,3) for gap in gaps]
            self.release(cancel=device.cancel.is_set() or not completed)


ANDROID_BATCH_SHA256='16b0eff6a9f1d0740795aeaf24262642dddc1e1ec1aa869d460536e7e315b9a8'


ANDROID_BATCH_B85='P)h>@6aWAK2mk;8Q(d)g3+M78003M-000XB003ibVRLh3b1r0Mc#V4tU{qJN_S)ynnKRGK<m53hBw;cMBtTvyA%yUT5DWxLlYtsSwUcCkX_J{Sneec^EfgxPR;4Z0T4`-n?DcAEsa)^1FRQJ#iuShDtK9ze(U<MjtJnJeeer*5pFPQh(q9ALS!=KTID4<X_FC(lnHf&(_cXP%)9?QJ*AHInd(YRF+<S7@vpps5D~+Ge^ZxXziXE$n#)xSDwpNSO-_0wD{zA5V32=pjXc=gyL9`IG*hRD*^n{xTA{y}#ZD}F$`H5VOM1H^mW&jmH6sQLl0h@tKff#TZumiXP7y@<yBS0D$2lfG11J?mZfSZ6jfct<i1K$A70)GHPO+?Fq9l(CzHsFiE)4-2_-vNG1J_;-X`hhXvFmMO(Fz_AVM}TN1DgovJD}g>B1zZo@4%`ns1H1~H2SQkT6R;N84vYgg0iOWw2VMkT2Hpbx0Q?o8Rw4)B1`2_hKo!scv;xb4PGA#oDX<OL4!jo_21bAka1C$>_#p5R;A6m@z}>*7f%|~Z1E+w8fo}lM1Fr*r0s@P$pFkNf4`>IL13kbV-~@0QcoujA5N&7&Yy}2@A>c|N1MCM510MiB4ty3k1v~<L6L=Q*PvD2ZYrrpn-vb4UiRyq3U?s2thym{fMuC06QQ$c6N#H@?tH3kBbHLAlw}9UPe+Kk+>>n^2m<Ox`wg5YUEN~6*e&9pEao}#?bHEpY)4*4NuL9o$o&$aeybAmr_zmE}p(q8afhJ%%upZb9WPqE1j{%<tP6Lkv-v(X)UIJbRe#r-`17iUtKp9XCEC8B-#XvW(8MqwS4P=4qft!Kjz$bwRfYZPuz}JDZz>C0Zz%PM61Ma0nVIT@L0V{xwz*gW2U<8-|4gntmjsu?n{slM%JPdpdI0Jki_zCbT@KXS;fZztm1K{{m2$%uP1j4{9pbRJnDuCHQ1gHd}Kow98%mL;CH9##e510=u0P28xpaH;9Cmdzzhj8>r9VLQk5=@D({nW1f7M4z!qV!$MffWE;8^Pt#YG4ifG@zS(954h714-aY;41cQYHgbGjb6oPF(IGguc^1!uB$c$ia!E*K=C(&4}#{LEb~Qqe2L<V70+W9DqSA0xBIb9Pt@D}Sg)T^W6oB6OBDYF$V(M}x_(OjXrBBj)wfdhwbXv__{EC<P9A@a;%gLNs`lx7N<L4?gKE!m>uSj38x`NJ_K*KID4y@TQSq$X9>u=|ev{(=2mEHm{{{RdiVxx@TNGanzE|;0;4fADGVpzh?*SiEJnL|);&(v)Zp9CS?^pZ?_yNUV1O9Tw9|ONFH%=b^1@M<C{&e1$tpDvf9`YTEe<rUl>oCVZm)GVT`1dIOC*a?!_&4+V{;|Qf<7TDXxZ-&q2Xk_4f&-`aC&W~vpDFrtN!#gvRQVT*{!-EZC64uWKST91R6C}>QuHlQr)uX^^`<JDD8H%be@klL&zx*$-_KO{bAhf?G%aZpU90GIlFp{<CDrMOq90c6KOt!?-J{xlO43UDw5tCXML)0leL=PVvZMj}H%Xi6N!9-ws{R>8zpv<vYMfV9`KPM<JK}RuOzVlya0$JO`25;*mZA=LHm9Uz^qkb+d|5VB*@;u-l+-SpI9X0f?XnBhspfZ$q!BtQX$9RV>0J7tr0w*8q&4)o(!<vzjnda8T}<DSw3@yv=^T1i(na*V8t>P{eyv{KkNw*>CAH{jVjtHa<pIp+CP~Zb_mbAoA0%y|KN9=17TK;Cp85U6e#uQAkhGMJOB$k&Novu@CH2#3;+WD(dnC2!WvNdO{Z{HzqsJum%5qqZKMS6j%Vi|!i;^1jprj7^58^na)BVJL(AM8<tne0MUue%Os^n34+N&ikR<uM>dmdF7=S#%#t68?MMg;m#No(kblGZ9(Mn58sarQdq!W(`>(gL}@xzLbJ7t#yFKDLq0N!mpJMeJt_>7ONSqwf*>+8K0=I6hX%`_6|3MkOtygT($fN1gOhNlm&#(l+`(#PQT#Up+K+1F?@@LU&5)r++1O5gju3Z-hoaN4DR7i8$`I$$m|Us)r<9BG<w47o;8Ae;i*%;B&e_pF+@WSZ>RWi`2h_`b$;4^CI=XMSV)un-{6~A)sHc>Rn{V%v?O=`fAjlME&d)T<?NKVE-4`@ooTx0ZpfVT~NQnN&SXK{Z51WO$Utyo1}%8qK65+%tN17(cUFWD6D;(qMkamxdfKDN9G$|Z`gmBtl5?;hVUE<>U?(>o|s&dypsZ!!@N&?*zxaK(*xUyAt?412|CmgDqOWDKzHeSVXCQsnjGR+u_B!+TNX8aki;LYwQcQ(h{x?McG98FQs<lcW5e8Xt)cF&Kl-J+(Yv=qqjQGw;GwMxb=_E|Urs#EIR~BZ(sf6tt`{6$#1fQ<OR*4m({yy2X2GfPhYlZJF|#PR>VblSqBF1Wi8N4xyvC8Os)xg>kR!BB8(}7RhuhKVa1@+cixFBL(DkA-ZQOe==5?fv=WSJE-5<%V`<yEv#Q$0awa5KrRm`Il*)*f*10?<y#ZHFx57Ahp0`^q{2~LEt3g%Jvy!e}~IRl!T(wVJyj_fli737%ffo>cmUaQqChwNcQY@ItR$-IweQ;6AM9>so&>QU@x3(BcT8_tzaOGOsJzxXf~=afFb)nJbfFNC^&Qe#;TN?mSI<biqcwSKBK0Y9+Ok2$-7Y-v{GJj5w3r-Gd0`s7~5kT&(Mi`cZQlC0vXIn@^Z12du+j5rp>d~}{qvn=-(IdH$!7K@s{Si1)LY~(qJU)8SW`&*4+M9#l^_kTnm810?zFYo?9t?rj6$WL+xKjFJBfv4j;#yagia*)T-?!X;&%TcR|*J4Ojd$(d%NH2SL=I@gD2Y1P~jd$LqTFp;2LEBI4z}cQ4etz-Dd}1^F{dZ-YfPWP1Zz42XuwSdDD#1By4Q&>7oV`MD?!SNrrM!VYtlHlt*#9)sf5~zid9MxsPz&9qeN?+P{6!J^ytq$X8@^2o-J*R&6QQ3AQFyyfKLC4LqmSz%^c%2`>!R>wo&BLx`m6K;JLV1ullnC`{FMj($_szxn)FjX>8DIOfVLb{qo(bjOhhYNXNZsDpQPS`Xx)3t4Zc<KN_Q^H9edEE-iJKY`z0^MPMOqKGK(x>X8GJgt6Y1^MTZJQI{T}~7V^mpYAk1sv%Zh1Rv7)=76lK~w!rT0tZjxR&R5p&lI<~iuNARrQRUR#3i)olJG@ievfo`w|DQX&kK3`8@d&X(yUi{3xDVNFD!GlQk<U!Cpr_?BmKgHcgL7eirYM3945@NT)gP3!fQ;)x?^-7c_tO6BLWBE7;f;cy>2$d_H!rZ`&VcY?Z~T+{;gS2H(`^gv=Z@Gh=p50c_ZC031dDS~gJQS(s4p7j{9Rj$kxDtDM3!P-YTw{K<rKRGJqywER`jf_mE~CB46-a<X{9V%TEA5)xvhnBs2wqi$CW*cBQ`CuTlR&Z<08w+I@XX0)f#?LK{diYtM*?x=FZRS&CmuvV+s6&S#IG6v0MzjcQS?+VBJ9)&<kOc#jwc$VrUU!s5PmJ5~&Lp-H$$Z${1QqQMYZM?tJ^y?vnNyVf(a#(mo?o_UW-^i3crT%qpbHXxRTDS`_(j5|3MJ+8Q_;YkB-KTKwQv;`(zsBA%w{otoy06{Bp>-?|(Q15wI%YR#d!+(y-Vxn9+IQCF?%NcNop-sRFeT`u3L>&+h@s>`eO%32+@y6!#Id?<QX(5c;JIeZIUL2HStfFAqiW7Uszm1=l8)12BeP5L>l)({WX>gJCvEml-O?V1;BEyg|tu<v!WhCEuW&XPmb`EfpG6;N*!R!|hAmgd2n{b;e`0_y9GQpicCpK?4pyin5|OSRr<TCg0Lt>f^VytzAKp+b)QIwHU0ROX>q4%f`|1y?=$^qC12_Z>$%^R+asw^#PoO|^3Ljz0VKGjmi=?Z_OSEw8R`F|4i1yq*0AGLwP=VgA+f&7+$09N*1fb99fut8#3gM@8q&v#}u7SyaS1ks*CdpNj4*C$;{jXm0&F%lizh-=yt>+M^Q-NjH|lGFd)73h=lVrM6qtbZhNezJKoSeln3E)GmjQs#8A7WA~pSy~%^JH_n)bGsY|9u^ehlqf*j7lg|~el+Ts)T^E~yWzQgTBImVCO*}Q(QpVAy>2p0UBbLTFEqff!#s3W2A>)flYXr$Cv026;FTF>wFDsy{RsAtZi`j>LM|(ySg@<*zLBB5aYwZR^nLG3V`trCx#o7D>+VYt#z=>>v-ye%?!<Yj<nIX%$QiyiEJcs&UE+w|??XS!NqIgo6pgq9|*>A<d+HF)-w1`sJsfg~=s*2jka-^Ep(HfLyYb{h+B}%N)MT<JA)6|Tr-HT|@-AJ3<E)jO$D*GLC`CNN~^Duj#yE@ngPvLX(2=8+HT<xwoXkSlNSE8R6{k$F#_WoG*d(h+a6nV8E_w|y`%YB)0-w$|wu2f_;o>Dy+1Lx5|wk&EN6`}q&=3@=}Cf5+w_DUYP1y&i>$0=-Ns5-J3-rT2)5_7$|-<r4Rqm9(%(G70tfz^b)_fpk~MYKuti9tW+<@Y&xP7nKhuCVuZsw%T}g0i=&tc`e18+|@A6|Ca^4rGsR?6+Ia>iKr_yVUMFLj!MA(7<c6Y2ZyngICI7lU+2386KFNVOZZUc^z}x6Iq2?pJU*)xy1aC<Y&T*R&!fN*ti=bRN^%Gj8s!6trKD6m7GLl$r@+u74&UdgFTDAV)v9Ivqa~*RB)lZZ|v2XlIPkYM*&rJVn2)~+GIEc;2bm>nRi=@aW$>=n>1W|H|Y+|8iYi%GJGA>8mLHjL2C}ZNO#**^KehC2>#KgK1KbKhP5x-*5&YN1Fuv;8_!I$xPez{sA>?i(J=#E6Jh;DS=Xd--MkCxevqq!eb>ScvUzs!22~ZRaRt{ekmmzWC*rHpg9PO}QGVZ4&5=?o{kkZBLIhOy2A3}19d11~2ib2A@&KH3f!N)288RmiWU#_jv17KU2~z)^rIWeA_G7cC4><H{&KEw?wEY<3WkEG&V34Vs$HhtUW%0B)x7Vp*q_FoZG}g3)3cRV{a-OFbEqzYN9CX4H_B<kM&v;Ui4&<)f#*0>a<l9r9H|)Eatoqari$3=ltk6?H_WM@OWss=$2XPkN;1}1~vA^71WNJZchIvffD;^S0n%{H0o^|sZ?+Q13o2SBE<MAlJC6M|1!Es)-2L1POf6v9U)bC7Yshj~kF)c&gdJpm&c){L#N~z_E1r+;2NIpsU$m3h$t3r83UU}A}F8e-(9Lt9s%U4LfpNdfLrz@%Nq|9#b$z`1VpDU%>IZ?C-(+Rn^KC1EgeH;^foVQ|ZE6DrglWnJsQ#dsabmz0?apw8(feL8R@2&9EcuD3>GjQe4MR?z1_H&ecXX}Fw(n?o4Wv>hM0i#*y*_0Vqf1UjqhWQoSlIm<rDnxXFB}Ig?q)5(^BG4!H-s2$iUh^UI1@W3@ATl|FB>o_t+3VujgvG<s9_|rr2{dI1sDUL!#gnN^EjpvwvC4iA<h5;KDciOK7QUy>{yZg0{4BzmuqC>LA@&4o(ZVC58vLqm;5@CDUcxFKTlFroT2sMp_8_9mFc24tDQL6{13R}7y;8vqsDs`h^C%heRIF<Stf$8C8+MC)*$kPx3;9`-fjd<xUDiu#$amjdzgU({@NG07S@%-;{HM!zSJ%|ppC#pN=8>`3F58++@|a7^%3O_k0-@E;>r9!~6;Mc9wXuixtg5G@&0Z5FoY%V@ruNmEBm4F=Eu)xO`(o4#Q`mJKENea;r5dwDK6whdvCjUS$2?Q+>wS1WnO_rU#jE1CE+a^W6T9XrBF$Y)Sr2xPDtw$bRQPJ-XHSLlY?WZ$9=XnsVXP0x9NR)Y`f0_a=_TZHl#*tIsob*W@>`LXD?Ahn<*#m+h=mrA<*K>~vD(9t={~p5oNZR1O(WWPy;f)mc6|G+FzpD<p}z7kmEy23bo&EK+zu&^&76stvl8(Yu^Mp!G20(5_`fo`{DOQ1*Egn`*3#X9aKWFZWGi#BrzT}3frBfpj^>kYY92GG<sQzQ#h+@8A{;NxQ%JH0{Mk#g$Nh-BEV(lfn>qKw`+LHEJG#9nTXoW%My#ZYir}HDKHer*u_v;C@9Np)al4p5WAo-d&Pi?FsWK6dwG<_9EIgO2jW#!vtR3Kd+``2zMy8dnu}d}Zf5=OGh;q})Zdv9#&5~sg<@YY~c_-^<%Chf*dcQ2&`*qUoGh#Cl+uqkks)uir&*S2{eot&>t*lXf^J+MgiiMF|lvPmGXdB|IUuIrT?8K%5zsaq>?T7Y|Iem^i?sjBq{djio$8&qjn1goAfKPUmQPo#k>5!)fT0Ar#SMyMFj|R=DCzTVL7UCzG#|Nz@$SD))qvObR5vQ-WGodY71+wd?R+#smC+sj~G*}DY!O=j^djrKDT0?G(T-(AZhwV{%^%~-QG_2Vt&Cc*E(1K&u&v07!yDj|gDJFAcL6-~u48Z}NaSn}VT{^1M?=|d@DLuuv>+H`o%UxR!vhQ_J1)lCK%RD4LEl!)?aD1PiZ-kWksU0WPYQC_}a__il4tp$jDl$jDy~nKF6|Ne$9TzlQ?az>P`JTdwJ6B0x#_^!`x{w7#Yq4{N^j0s`*`EiN>#?S4eoL^h8hV;Vt0~rMb1}(ff?F|`$FL=tV2pRwkqKVP@7|1dqYkoO@IGU<(MqwlYWVMU@ZU!YxksrjDSVrx@@<lZEJ-z$;i;hZd3z4!$fG0|471LtLWh1F6;$KERj+Xf<)hj6_JZq|?;3RZ8L52dfXs>C<K|-ETXpv5o^}juM{d%4tdztHElu4d<DPo!y98R8jQhvsyA{V$cz%nzL;-aQLA|#|sHL(&mMsest@k)=dG#EM-GY1q5mn@Jvsrc=HDw$XFPMm<LB!EQ6-S*ijz0R1arDGR;^?JQarE*c8I7<$1gZ%*beAhg)=U@tdMdIRb`nMYkQF^B`|vzg3cHRXx6F!7EP;K`#5e<oSjQo(+(X4;N$2V<Ex`?1D#)Lc`N*Z=Nixz5ZSwo@6<V%k|G_$%;>)NP<<?_!wO;V8$D$gSi!@r*7Acdmw#W=EcF?x1AW1IBnT+MljiRWpNZip;qzxmdUqi)?d)^Q0uP-v*)LV|-Ko_)iDrzcPplG1=*e+^oN>GP$h}yQrsl$9PwOw{OwKfb;TSY&$zV|X}yJRb#$Xe(*=w?uBgdbmxJ@yH+Xtv%?;?yFsgt|0cs4*QI9h&!ovYX5NMx#TUMLE9rNF6O&Q<N*aJFss#zV}EO>I^z3gs6SaehzqI@;SgI;6J`DhQ);VsrW<I<m_%9p4IJkCnwvrJhw?{z`n)~&QR|*`#)HU{NB!r3vyQM4AUp6Jh=4`zuTJ&8#qI~hmm_8sf6|NJ<ZoS^Mpk`RvYreLTv8|%hdi|de2H|h2NBmJnvbniN`8}{I33)U(BO7*+;?`TWu{@G~{+i&uQvyyVg_!kKch+wQUJd>s&vzm3nilH?mH*T=(PZIq9>L>$X<61d|uqfOsccSWX+M_4#gUTeu!}unu;xmRc`cO>OH}QtP$LscrL8x&P1ONtB=Ec>nn`0ke9x1N;BbBE+aJJZq})jE#owy`b#oGQY*@!ZT&3VYubWjB}bMYF}3C(M3Y+6>ntCBF;l=?KOR7YEAK$;Oxm0pRfDZCUxI?{e1Z>BliLN7fj(3p7GW$u=c!D|MyMyk6k}|Y9FWf_ga^>1ie!_ZSvAH(0!Zz37U9)a-N8ihi0g^<7{QNH5E=-Qtq=vr)^WVMah2CtdnD4JN><Wxo712Chu>T&*nWdv!6fBZG|20iUs>c4|KraFETMZJ3g!sB;O#f1pOsPi0gvia@_Br$F%E0_iB%64z%K2cu0NMiO*|q5N8-!Mu*OyGvLftahA@xMOe4bYcn#3eYua63ur$Tyd|mwCr_Uj(SVp8(60Wd=Iwu1xAwjbx26A0lm$dd;OMRM7c?HZ^1LVlo7iz)6bFvpUZGuGE~;ep?R2X!)Ofe?y21fb78I5r<DD;~JA}qHciJ_}L_=VEJ)IO&bNDRRlm%a|*eD_aQ63Pb-lzx##EbyXsXU-fd}&PEzfbdTI4foZ|8ixws0oO|Kp9qDpzUA!tA_f~WQ1Ge{~X`u`ed?F1cMu2#(cG_m#*1(I+=X(?Cmd$jojkp^IEo?KAfBIulYt+(XwwPwRAak*wXSR=hLG+>vG%=i+p+3jbc_{qjmskgZi|-Km@JrTCpfCSf=eNKTE$ucrAGH{Pv?^8Ui<P^eko=rscU+)}c6`R=HG>o<Dnj|H+fkBrmZj0P_}M{10s02%YcOuC5a^Ep6<hH&CCN(#YwG`m>j7<K=Xf{K7%`GVazII9ag(i=<D9$%!p4RP#!f)6-J-xc6ynl&A=N7HYsfivwquofl=k<u{(hqCQlA_H<-p{b>>MMk|)hU(O>R-9P`vW!%&XoE4RU2S31D6A65F!BMd|pj}pe13e{&ezHvFaS^h=6R63I&i;O#P)pTl>T{zUG(~mAm&l$>TO^&W%CqsjD#-qBgC@saB)?CgP5z_2a-$s2oL08mxZWny)V($N9X^NR8*vAx;@ed_yWBqQo>Sv$^8T*8I#ZR~-zJ+rh9LWW64N5JzS%MpwAY_oa|CnZHgi;YfhsqvwKwOjo#l&FyJq~;Yv*mlrMsIPxbSs5t{Q~yh0Ti_n(%KSIhQ3<$?OUWtxgSR(#hdSCUIqAD4Q6LWYZHvyCWnzNpz9uCY{I7x_d}$AZ<e@=^Hj|Af#zpPZxly9>{xoNbf-bmmc!>Y>5vyt%+xcc2_T4I<43|z1T9n*t(RwQ^loJ)iWF)*_*toF`i1Lv+-;)ooZZ{8X8HDCsVsNC9=EI!%L~+B8|FJsYGV&NPK)8Ve=yOcHb@WOnfwvO=Qrj{35OTlDksz>_jH9l)@LOj_uB*_o3^|o-5;f<BiF5V`n@Y?@o<PWc#w2M0}LH<yT!Yk<F_sovd4TN!PmlLy0jSbt#oh%DXSQxHxsuQKpYxWXnh5sa=h0(&>>z9Agyb36*xD7m7Bgvt8+l)bQJHE!*UY^GE9%NyoF(@7kTpCUzw<ODS@JFq4@WgQ4Y(6wGVUlTPiLF4+{%!WJs?B%9NHuu*#x^M<kVn@o%j^3C&QmkhFrFQwu<S?@$D6(1Z)Om7J-CV3h&-zHl#ksKMmaNxcJ<JrXMbQ!E85g(qeJZUlZ-SS7yBzBHqVvVaanfQT=S9K*5Bg2#SG_5Lk4wNON5?N?DkzGAJoJsHwho<CxiOk+arY}8oRU$jNf3iY})MO{J$q{=tJ<0Lx<PcK4Ha)_(g~G=tH7P5)v9s}PIy1R>Qfd!yp=22?$de4EQ$rJ(Od^$S!~mnop~lscOxmrnY4!T<wNx<a8XCAWIbywA*Wuc~WgUraq-}%$*#>9j#HBk&;hkJIg~XlX;1$94kjYmr8qCOYkL;^$+kn9~Fay`N4ZaQ+9@4IMfc9YE9@QL`ve?tDdT&S5?egwoJ89c7#`f)C22eM!fgA%HRxBlxuN&AeOQyRUtVglTTLW)DuSEkFG3aT+obBoZxS2CP#K(iYOvlH&M&i51$sfo5?M<vtjI(niJw7}P+3;}R#Ml_yOk$X-;&xbQ*d5Od?~7*=4N3Mo4bt~)il^c@*);DTXtaJLJs2OM(p=N@c!Paf){emDXhu%HH<{RHckE2;O%5d}^!A!{dto9MblH?<Q?zU5zEe&0mUNOG&)Rf4Gdxc7|6aQ-ne<p9lT9YZ$q^q*c0i3|V~Nx-8H0&k$rL$3CQ=>b7@QbCK<=S<YAAtIfqITj3Gxr6N5`NK#MZt9lt0AxO(K?HK=*KB1j^5#wQD%NFBMCUCdi!_9m~qYM$W{ZiTKDk840;2=T5}w@!e$X9GL(yccwF&5wytMrFO4WF_HKPJDdX%dvmEQC(q&rk->@KU5RWY0cV*Q#;tv-wl5pcWJyOIxp7%@Ko0H#EA34R)rR;c*>32Nn@Fcb1>o{R5_x5LJj+Uv{cQ(D0kHWoiu@4tB}d0b684IA$HzA(_G8Jr6Ukk>vC+FRd?dGwi4-h6Il|tP46f-QEjdh%<S+!zWQtD$1#OyVEiPM<&M*h*>aiphCsTXlBglE+c(TdVgbe2t$VqdWootpr5Ax-t<H`5Q8}ChD1&t+@_ag&(%f8zt{p5lJ8&9l*>9|J_*tW$-CKBWrjl+oa(fEE`z&TR9euJ-2Y$~2gOQZ0n68k0<=}1Y{qzALy*O``zq!Jj=WXhIF$eVRU$TJ2<S#R5_Px86h+cmNsc_ohnKfzlLdmT?8Jd-gNpFnMNY$TqAwT(uk-Xf#+d7icpC1ZSEBACp0ayStgPL89~0o>9)M)nR^`6XpbX3o#JBxBEP>d={N$vhXyY>jm>Gjw1Gw=~r%DX5spPRu4Tj8(go1iH>~9b{w@V<QL1sg`MEIJY4^Gm#==oWmgn$1wvKKx8yIKFYy`^l=;qKQ61K(@gSG-6(d(6QeK`53{+YPVwDX4N5W-BIh_Cf~@S;m!8OA%{*XiXl!%3Gr>I_(teEbkwjt)M4rQfad_Z#COfXWIJ3Kx<JC>%%BF3v&RlvVc3=#<CpSHJHf3RaD3eIQEd?f0SEXPXu<uc9+b}tiaIr=CY`Q5qGLjr8AMCh#D4jv7ymg$sT*{e=iwnEcINfgMV5ytN$-}JNOzaTz<B2`C5G%_<M>d~LjA1x~`TaIIKz?~-;Qyq9l~SAoQ<6(|lCdv2oZU^XeHk1hsW!=RDyW2P17bgk1GMwnYdhPojt<6=v80Bh9ns;${%Bp4Jua-0PjdZedN?5~1}Ao*3Vx-2Xm?`hDp<gHv|}f{YoacSgI5pV9_?sutBb-Pvk9P6dqYb@%i`!ik>=G>WoBpaAXNv07yN%nl$jOwpXL+45oIDonj1{mr+KuLgHDMG+26RZKYAK=KiS+ZTWP8lDz#iqk>-~J&j{If&_h~iZD?nHVOM@(cdnqzW~y`8mKq@eX(H4ba^z|X$rOT)Ma0|w)4_v|o5Mmp5hg<jb9$wQ%15ho@n#iiLinW20ol2^hWO)L9<$(LV>;2~xmr^knJ2KGk{P9|L{R=Cu?ooXTneh|)IV~e_dQL9``j`&{jp6PJm~sPJ6~^JBIt>w8hvI3mrktY>znfWXcu3fTQBITZbA2K6!h9wG0YzqsORL(%ZMhH+5EMjHz@wYdGh=7<frrGPb)b;cR#CSKUg-!zm~_pmB;^0@!Ze5+?ExC&RK5Dn6|F4-=E~(qwrrXWIY$ix$mlr$oZYdyCECCK<-rI<o>*h@;j2T7548w_ph+!kFKzP<H>UV4CkL$*uU*Ovcmpt<FOU?cgjz!u-|1oxWfKjB)2b7{R?UO{h8)|XU}ub{nnn}U*vvo&vVcH=APei<bHSWq^aNDi-`SuO#c17u6X{9iwN4kspa3wXc7Bf{QG=I#GW(nH@~prarienM#P>I|5ip1+UsQfI8__|Ef05;-+Nw3^#1@*O928D0~7!N00;m808?GHZVTt~A^-qfKmY&>00000000000001h0RR910Ap-nb8}^LE@WkRP)h{{000000RRC2IRF3v8zTS!000'


class AndroidBatchTouch(PersistentAndroidTouch):
    """One complete path per command; Android owns timing and acknowledges UP."""
    MAGIC=0x50414439
    def __init__(self,device):
        super().__init__(device)
        self.pacer.close();self.pacer.mode='android_batch'
        self._send_lock=threading.Lock();self._responses_lock=threading.Lock()
        self._responses={};self._sequence=0;self._reader=None;self._authenticated=False
    @staticmethod
    def server_file():
        import base64
        root=_input_resource_root()/'tools'/'pad0-batch-5';root.mkdir(parents=True,exist_ok=True)
        path=root/'pad0-batch.jar'
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=ANDROID_BATCH_SHA256:
            data=base64.b85decode(ANDROID_BATCH_B85)
            if hashlib.sha256(data).hexdigest()!=ANDROID_BATCH_SHA256:raise RuntimeError('Android 경로 실행 모듈 무결성 오류')
            temporary=root/('batch-'+uuid.uuid4().hex+'.tmp');temporary.write_bytes(data);os.replace(temporary,path)
        return path
    def _exact(self,size):
        data=b''
        while len(data)<size:
            if self.closed:raise RuntimeError('Android 경로 연결이 닫혔습니다.')
            try:part=self.socket.recv(size-len(data))
            except __import__('socket').timeout:continue
            if not part:raise RuntimeError('Android 경로 실행 연결이 종료됐습니다.')
            data+=part
        return data
    def _read_responses(self):
        import struct
        try:
            while not self.closed:
                seq,status,count=struct.unpack('>iii',self._exact(12))
                if status not in (0,1,2) or not 0<=count<=1024:raise RuntimeError('잘못된 Android 실행 응답')
                events=[struct.unpack('>iiiq',self._exact(20)) for _ in range(count)]
                length=struct.unpack('>H',self._exact(2))[0]
                message=self._exact(length).decode('utf-8','replace')
                with self._responses_lock:pending=self._responses.pop(seq,None)
                if pending is not None:pending.put((status,events,message))
        except Exception as error:
            with self._responses_lock:pending=list(self._responses.values());self._responses.clear()
            for response in pending:response.put(error)
    def _request(self,command,body,timeout=3,allow_cancel=False):
        import struct
        if not allow_cancel and self.device.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
        if self.closed or not self._authenticated:raise RuntimeError('Android 경로 실행 연결이 준비되지 않았습니다.')
        response=queue.Queue(maxsize=1)
        with self._responses_lock:
            self._sequence+=1;seq=self._sequence;self._responses[seq]=response
        try:
            with self._send_lock:self.socket.sendall(struct.pack('>ii',command,seq)+body)
            deadline=time.monotonic()+timeout
            while True:
                try:result=response.get(timeout=.05)
                except queue.Empty:
                    if time.monotonic()>deadline:raise RuntimeError('Android 경로 완료 응답 시간 초과')
                    continue
                if isinstance(result,Exception):raise result
                return result
        finally:
            with self._responses_lock:self._responses.pop(seq,None)
    def open(self):
        import socket,struct
        device=self.device;path=self.server_file();remote='/data/local/tmp/pad0-batch-5.jar'
        try:present=device._shell(['sha256sum',remote],timeout=2).split()[0]==ANDROID_BATCH_SHA256
        except (RuntimeError,IndexError):present=False
        if not present:device._run(['push',str(path),remote],timeout=5)
        remote_port=random.randrange(32000,59000);token=uuid.uuid4().hex
        forwarded=device._run(['forward','tcp:0',f'tcp:{remote_port}'],timeout=3).decode().strip()
        if not forwarded.isdigit() or not 1<=int(forwarded)<=65535:raise RuntimeError('Android 경로 연결 포트를 확인하지 못했습니다.')
        self.port=int(forwarded)
        self.server=subprocess.Popen([device.executable,'-s',device.serial,'shell',f'CLASSPATH={remote}','app_process','/',
            'Pad0Batch',str(remote_port),token],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if device.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
            if self.server.poll() is not None:
                detail=self.server.communicate()[0].decode('utf-8','replace')[-700:]
                raise RuntimeError('Android 경로 실행 모듈 시작 실패: '+detail)
            sock=None
            try:
                sock=socket.create_connection(('127.0.0.1',self.port),timeout=.15)
                sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1);sock.settimeout(2)
                encoded=token.encode('ascii')
                sock.sendall(struct.pack('>iH',self.MAGIC,len(encoded))+encoded+struct.pack('>ii',*device.native_size))
                ack=b''
                while len(ack)<4:
                    part=sock.recv(4-len(ack))
                    if not part:raise OSError('handshake closed')
                    ack+=part
                if struct.unpack('>i',ack)[0]!=self.MAGIC:raise RuntimeError('Android 경로 실행 버전 불일치')
                self.socket=sock;self.socket.settimeout(.2);self._authenticated=True
                self._reader=threading.Thread(target=self._read_responses,daemon=True);self._reader.start()
                _update_input_diagnostics(device,capture_size=list(device.native_size),input_size=list(device.native_size),
                    touch_timer='android_batch',input_transport='android_path_executor')
                self.read_touch_device()
                return self
            except OSError:
                if sock is not None:sock.close()
                if device.cancel.wait(.05):raise RuntimeError('재료 파밍을 중지했습니다.')
        raise RuntimeError('Android 경로 실행 모듈 연결 시간 초과')
    def read_touch_device(self):
        status,events,message=self._request(6,b'')
        if status or events or not re.fullmatch(r'touchscreen:-?\d+',message):
            raise RuntimeError('Android 터치 장치를 확인하지 못했습니다: '+message)
        self.device.input_diagnostics.update(touch_device_id=int(message.split(':')[1]),touch_source=0x1002)
        status,events,message=self._request(7,b'')
        if status or events or message not in ('api:InputManagerGlobal','api:InputManager'):
            raise RuntimeError('Android 표준 입력 API를 확인하지 못했습니다: '+message)
        self.device.input_diagnostics['touch_input_api']=message.split(':')[1]
    def _gesture(self,points,hold,settle,max_seconds):
        import struct
        if not points or len(points)>66:raise ValueError('Android 전체 경로 길이가 올바르지 않습니다.')
        width,height=self.device.native_size
        if any(not (0<=x<width and 0<=y<height) for x,y in points):raise ValueError('화면 밖 경로를 차단했습니다.')
        body=struct.pack('>iiiii',len(points),round(hold*1000),round(settle*1000),round(max_seconds*1000),18750)
        body+=b''.join(struct.pack('>ii',round(x),round(y)) for x,y in points)
        self.pressed=True;self.device.cancel_command_sent=False;started=time.perf_counter()
        self.device.input_diagnostics.update(batch_pending=True,batch_dispatch_confirmed=False)
        try:
            status,events,message=self._request(1,body,timeout=max_seconds+3)
            trace=getattr(self.device,'input_trace',None)
            for action,x,y,micros in events:
                self.last=(x,y)
                if isinstance(trace,list):trace.append(dict(action=action,native=[x,y],encoded=[x,y],input_size=list(self.input_size),
                    seconds=round(started-self.device._input_trace_started+micros/1000000,6),android_elapsed_us=micros))
            if isinstance(trace,list) and len(trace)>1024:del trace[:-1024]
            moves=[row[3] for row in events if row[0]==2]
            self.device.input_diagnostics.update(batch_pending=False,batch_dispatch_confirmed=status==0,batch_message=message,
                move_gap_ms=[round((b-a)/1000,3) for a,b in zip(moves,moves[1:])],
                android_events=len(events),android_execute_seconds=events[-1][3]/1000000 if events else None)
            if status:raise RuntimeError('Android 경로 실행 중지: '+message)
            self.pressed=False;self.last_up_at=time.monotonic()
        except Exception:
            self.device.input_diagnostics['batch_pending']=False
            try:self.repeat_release(cancel=True)
            except Exception as error:self.device.cancel_release_error=str(error)
            raise
    def drag(self,points,checkpoint=None,max_seconds=None,hold_seconds=.60):
        if checkpoint is not None:raise RuntimeError('Android 경로 실행 중 PC 캡처를 허용하지 않습니다.')
        self._gesture(points,hold_seconds,.06,float(max_seconds or 5))
    def tap(self,x,y):self._gesture([(x,y)],.05,0,1.5)
    def repeat_release(self,cancel=False):
        import struct
        if not self._authenticated or self.closed:return
        status,events,message=self._request(2,struct.pack('>ii',*self.last),timeout=3,allow_cancel=True)
        self.device.input_diagnostics.update(release_scope='owned_pointer',release_message=message,release_dispatch_confirmed=status==0)
        trace=getattr(self.device,'input_trace',None)
        if isinstance(trace,list):
            started=time.perf_counter()-self.device._input_trace_started
            for action,x,y,micros in events:trace.append(dict(action=action,native=[x,y],encoded=[x,y],input_size=list(self.input_size),
                seconds=round(started+micros/1000000,6),android_elapsed_us=micros))
            if len(trace)>1024:del trace[:-1024]
        up_confirmed=any(row[0]==1 for row in events)
        cancelled=any(row[0]==3 for row in events)
        if events:
            self.device.input_diagnostics.update(release_up_confirmed=up_confirmed,release_cancel_confirmed=cancelled,
                release_last_owned_message=message,release_last_owned_dispatch_confirmed=status==0,
                release_up_error=message if status else None)
        else:
            self.device.input_diagnostics.setdefault('release_up_confirmed',False)
            self.device.input_diagnostics.setdefault('release_cancel_confirmed',False)
        if up_confirmed or cancelled:self.pressed=False
        if status:raise RuntimeError('Android 터치 해제 실패: '+message)
        self.device.cancel_command_sent=True
        self.device.input_diagnostics['release_dispatch_confirmed']=True
        self.pressed=False;self.last_up_at=time.monotonic()
    def release(self,cancel=False):
        if self.pressed or cancel:self.repeat_release(cancel=True)
    def close(self):
        with self.lock:
            if self.closed:return
            error=None
            try:
                if self._authenticated:self.repeat_release(cancel=True)
            except Exception as exc:error=exc
            finally:
                self.closed=True;self.pacer.close()
                if self.socket is not None:
                    try:self.socket.shutdown(__import__('socket').SHUT_RDWR)
                    except OSError:pass
                    self.socket.close();self.socket=None
                if self.server is not None:
                    try:self.server.wait(timeout=1)
                    except subprocess.TimeoutExpired:self.server.kill();self.server.wait(timeout=1)
                    if self.server.stdout:self.server.stdout.close()
                    self.server=None
                if self.port is not None:
                    try:self.device._run(['forward','--remove',f'tcp:{self.port}'],timeout=2,allow_cancel=False)
                    finally:self.port=None
            if error is not None:raise error


class MuMuSDKTouch(PersistentAndroidTouch):
    """One SDK worker owns a complete gesture; no host cursor or ADB moves."""
    def __init__(self,device):
        super().__init__(device);self.client=getattr(device,'_input_sdk_client',None) or device._fast
    def open(self):
        if self.client is None or not self.client.input_supported:raise RuntimeError('MuMu SDK 입력을 사용할 수 없습니다.')
        # Capability/target verification is read-only. An unopened SDK session
        # owns no pressed finger; idle-finger UP rejection must not block startup.
        _update_input_diagnostics(self.device,input_transport='mumu_native_sdk',input_size=list(self.device.native_size),finger_id=1)
        return self
    def _execute(self,points,hold,limit,probe=False):
        if self.device.cancel.is_set():raise RuntimeError('재료 파밍을 중지했습니다.')
        self.pressed=True;started=time.perf_counter()
        request=dict(op='input',points=[list(pt) for pt in points],hold=hold,limit=limit)
        if isinstance(self,MaaFrameworkTouch) and len(points)>1:
            timing=calibrated_input_timing(self.device)
            if timing is not None:request['timing']=timing
        if probe:request['probe']=True
        start_context=getattr(self.device,'sdk_start_context',None)
        if len(points)>1 and not probe and start_context is not None:
            request['start_context']=dict(start_context,point=list(points[0]))
        result=self.client.input_request(request)
        self.pressed=not result.get('released',False)
        delivered=[event['native'] for event in result.get('events',[]) if event.get('action') in (0,2)]
        if delivered:self.last=tuple(delivered[-1])
        self.last_up_at=time.monotonic() if not self.pressed else None
        for event in result.get('events',[]):
            self.device.input_trace.append(dict(event,seconds=round(started-self.device._input_trace_started+event['seconds'],6),transport='mumu_native_sdk',finger_id=0 if result.get('input_stream')=='single_pointer' else 1))
        if len(self.device.input_trace)>1024:del self.device.input_trace[:-1024]
        self.device.input_diagnostics.update(sdk_dispatch_confirmed=not result.get('error'),sdk_release_confirmed=not self.pressed,sdk_execute_seconds=result.get('execute_seconds'))
        self.device.input_diagnostics.update(sdk_preflight_release=result.get('preflight_release'),
            sdk_cleanup=result.get('cleanup'),sdk_pacer_mode=result.get('pacer_mode'),
            sdk_start_feedback=result.get('start_feedback'),sdk_display_id=result.get('display_id'),sdk_input_stream=result.get('input_stream','finger'),
            sdk_move_gap_max_seconds=result.get('move_gap_max_seconds'),sdk_move_gap_over_50ms=result.get('move_gap_over_50ms'))
        self.device.input_diagnostics.update(sdk_motion_seconds=result.get('motion_seconds'),sdk_motion_estimate_seconds=result.get('motion_estimate_seconds'),
            sdk_corner_count=result.get('corner_count'),sdk_corner_dwell_seconds=result.get('corner_dwell_seconds'),sdk_feedback_capture_seconds=result.get('feedback_capture_seconds'),
            sdk_moving_gap_max_seconds=result.get('moving_gap_max_seconds'))
        self.device.input_diagnostics['input_timing']=result.get('input_timing')
        held=result.get('held_image');self.device.last_before_up_frame=self.device._normalize_capture(held) if held is not None else None
        if result.get('error'):raise RuntimeError(result['error'])
    def tap(self,x,y):self._execute([(x,y)],.06,1.5)
    def drag(self,points,checkpoint=None,max_seconds=None,hold_seconds=.20):
        if checkpoint is not None:raise RuntimeError('MuMu SDK 경로 중 PC 캡처를 허용하지 않습니다.')
        self._execute(points,hold_seconds,float(max_seconds or 5))
    def probe(self,x,y):
        self._execute([(int(x),int(y))],.18,1.0,probe=True)
        if self.pressed:raise RuntimeError('구름 확인 후 손가락 해제를 확인하지 못했습니다.')
        return self.device.last_before_up_frame
    def repeat_release(self,cancel=False):
        client=self.client
        if client is None or client.closed:
            d=self.device
            client=d.create_input_capture_client(threading.Event())
            try:result=client.input_request(dict(op='release',require_active=self.pressed))
            finally:client.close()
        else:
            client.input_cancel.set();result=client.input_request(dict(op='release'))
        if result.get('error') or result.get('released') is not True or result.get('all_fingers_confirmed') is False:
            raise RuntimeError(result.get('error') or 'MuMu SDK 전체 손가락 해제 응답 미확인')
        self.pressed=False;self.last_up_at=time.monotonic();self.device.cancel_command_sent=True
        self.device.input_diagnostics.update(sdk_release_fingers=result.get('fingers',[]),sdk_release_results=result.get('release_results',[]),sdk_single_touch_release_result=result.get('single_touch_release_result'),sdk_all_fingers_confirmed=result.get('all_fingers_confirmed'),sdk_release_confirmed=True)
    def release(self,cancel=False):
        if self.pressed or cancel:self.repeat_release(cancel)
    def close(self):
        try:self.repeat_release(cancel=True)
        finally:
            self.closed=True;self.pacer.close()
            if self.client is not None and self.client is not self.device._fast:self.client.close()


class MaaFrameworkTouch(MuMuSDKTouch):
    """A complete route stays inside one official MaaFramework controller worker."""
    def open(self):
        if self.client is None or not self.client.input_supported:raise RuntimeError('MAA 입력 연결이 없습니다.')
        self.device.input_diagnostics.update(input_transport=self.client.input_transport,
            input_size=list(self.device.native_size),finger_id=0,maa_info=self.client.ready.get('info',{}))
        return self
    def _execute(self,*args,**kwargs):
        previous=list(self.device.input_trace);existing={id(event) for event in previous}
        self.device.last_before_up_frame=None
        self.device.input_diagnostics.update(sdk_dispatch_confirmed=False,sdk_release_confirmed=False)
        if self.client.closed:raise RuntimeError('MAA 입력 연결이 종료됐습니다. 자동 입력을 중지합니다.')
        try:super()._execute(*args,**kwargs)
        finally:
            d=self.device;transport=self.client.input_transport
            for event in d.input_trace:
                if id(event) not in existing:event.update(transport=transport,finger_id=0)
            d.input_diagnostics.update(input_transport=transport,
                control_dispatch_confirmed=d.input_diagnostics.get('sdk_dispatch_confirmed',False),
                control_release_confirmed=d.input_diagnostics.get('sdk_release_confirmed',False),
                control_start_feedback=d.input_diagnostics.get('sdk_start_feedback'))
    def repeat_release(self,cancel=False):
        # Never create another controller/pointer stream to release an old one.
        self.client.input_cancel.set()
        if self.client.closed:
            if self.pressed:raise RuntimeError('MAA 입력 연결 종료 · 원래 손가락 해제를 확인하지 못했습니다.')
            return
        result=self.client.input_request(dict(op='release',require_active=self.pressed))
        if result.get('error') or result.get('released') is not True:
            raise RuntimeError(result.get('error') or 'MAA 손가락 해제 미확인')
        self.pressed=False;self.last_up_at=time.monotonic()
        self.device.input_diagnostics.update(control_release_confirmed=True,sdk_release_confirmed=True)
    def close(self):
        try:
            if self.pressed:self.repeat_release(cancel=True)
        finally:self.closed=True;self.pacer.close()


class NativeSDKInputMixin:
    """SDK gesture execution; capture and DLL lifecycle belong to its owner."""

    def release_fingers(self,require_active=False):
        if not self.input_supported:raise RuntimeError('MuMu SDK 손가락 입력 API가 없습니다.')
        was_active=self.touch_active or require_active
        active_stream=getattr(self,'_active_input_stream','finger')
        results=[]
        for finger in range(1,11):
            try:results.append(int(self.touch_up(self.handle,self.display_id,finger)))
            except Exception as exc:results.append(str(exc))
        single_result=None
        if getattr(self,"single_touch_up",None) is not None:
            try:single_result=int(self.single_touch_up(self.handle,0))
            except Exception as exc:single_result=str(exc)
        failed=single_result!=0 if active_stream=='single_pointer' else results[0]!=0
        self.touch_active=bool(was_active and failed)
        if self.touch_active:raise RuntimeError('MuMu SDK 기본 손가락 해제 실패: '+str(results))
        return dict(ready=True,released=True,fingers=list(range(1,11)),release_results=results,all_fingers_confirmed=not any(results),single_touch_release_result=single_result)

    def play_input(self,points,hold,limit,cancel,probe=False,start_context=None,timing=None):
        if not self.input_supported:raise RuntimeError('MuMu SDK 손가락 입력 API가 없습니다.')
        if not points or len(points)>66 or any(len(pt)!=2 or any(type(v) is not int for v in pt) or not (0<=pt[0]<self.size[0] and 0<=pt[1]<self.size[1]) for pt in points):raise ValueError('MuMu 입력 좌표가 잘못됐습니다.')
        if not .01<=hold<=1 or not .1<=limit<=15:raise ValueError('MuMu 입력 시간 범위가 잘못됐습니다.')
        if probe and len(points)!=1:raise ValueError('구름 확인은 한 위치의 누름만 허용합니다.')
        trace=[];started=time.perf_counter();error=None;released=False;held=False
        preflight=None;cleanup=None;last_point=list(points[0])
        feedback_active=start_context is not None and len(points)>1 and not probe
        start_feedback=None;start_checks=0;feedback_started=None
        single=(self.display_id==0 and callable(getattr(self,'single_touch_down',None)) and callable(getattr(self,'single_touch_up',None)))
        stream='single_pointer' if single else 'finger'
        timing=validated_input_timing(timing) if timing is not None else None
        sample=timing['sample_seconds'] if timing is not None else .01875
        corner_dwell=timing['corner_seconds'] if timing is not None else float(getattr(self,'corner_dwell_seconds',.03125))
        edge_budget=input_timing_move_budget(timing) if timing is not None else float(getattr(self,'feedback_move_seconds',SDK_FEEDBACK_MOVE_SECONDS))
        corners=[i for i in range(1,len(points)-1) if
            (points[i][0]-points[i-1][0])*(points[i+1][1]-points[i][1])!=(points[i][1]-points[i-1][1])*(points[i+1][0]-points[i][0]) or
            (points[i][0]-points[i-1][0])*(points[i+1][0]-points[i][0])+(points[i][1]-points[i-1][1])*(points[i+1][1]-points[i][1])<=0]
        motion_estimate=(len(points)-1)*4*sample+(len(corners)*max(0,corner_dwell-sample) if feedback_active else 0)
        motion_seconds=None;motion_started=None;capture_seconds=dict(baseline=0.0,start_checks=0.0,before_up=0.0)
        move_reserve=(len(points)-1)*edge_budget+.22+.08
        pacer=AndroidTouchPacer(cancel)
        def down(x,y,action):
            if cancel.is_set():raise RuntimeError('MuMu 입력 중지')
            self.touch_active=True
            self._active_input_stream=stream
            result=self.single_touch_down(self.handle,0,x,y) if single else self.touch_down(self.handle,self.display_id,1,x,y)
            if result!=0:raise RuntimeError('MuMu SDK 누름/이동 전달 실패')
            last_point[:]=[x,y]
            trace.append(dict(action=action,native=[x,y],seconds=time.perf_counter()-started))
        def wait(seconds):
            if pacer.wait(seconds):raise RuntimeError('MuMu 입력 중지')
            if time.perf_counter()-started>limit-.08:raise RuntimeError('MuMu 입력 시간 한도 초과')
        try:
            # A previous UP acknowledgement can leave the emulator's default
            # pointer latched. Clear both SDK finger and default touch streams
            # before a NEW gesture, never by injecting another DOWN to recover.
            preflight=self.release_fingers()
            wait(.035)
            baseline=None
            if feedback_active:
                feedback_started=time.perf_counter()
                start_feedback=dict(confirmed=False,reason='not_checked',checks=0,seconds=0.0)
                if limit-(time.perf_counter()-started)<move_reserve+hold:
                    raise RuntimeError('제한시간부족 · 시작 드롭 확인과 전체 경로 이동 시간을 확보하지 못했습니다.')
                capture_started=time.perf_counter()
                try:baseline=self.prepare_input_feedback(start_context)
                except Exception as exc:raise RuntimeError('MuMu 시작 드롭 확인 준비 실패: '+str(exc)) from exc
                finally:capture_seconds['baseline']+=time.perf_counter()-capture_started
                if cancel.is_set():raise RuntimeError('MuMu 입력 중지')
                if limit-(time.perf_counter()-started)<move_reserve+hold:
                    raise RuntimeError('제한시간부족 · 시작 판 캡처 후 전체 경로 이동 시간을 확보하지 못했습니다.')
            down(*points[0],0);wait(hold)
            if feedback_active:
                for check in range(4):
                    if cancel.is_set():raise RuntimeError('MuMu 입력 중지')
                    if limit-(time.perf_counter()-started)<move_reserve:
                        raise RuntimeError('제한시간부족 · 시작 드롭 확인 중 전체 경로 이동 시간을 확보하지 못했습니다.')
                    capture_started=time.perf_counter()
                    try:feedback=self.check_input_start(start_context,baseline)
                    except Exception as exc:raise RuntimeError('MuMu 시작 드롭 확인 실패: '+str(exc)) from exc
                    finally:capture_seconds['start_checks']+=time.perf_counter()-capture_started
                    held=True;start_checks+=1
                    if not isinstance(feedback,dict):raise RuntimeError('MuMu 시작 드롭 확인 응답이 잘못됐습니다.')
                    start_feedback=dict(feedback,checks=start_checks,seconds=round(time.perf_counter()-feedback_started,6))
                    if cancel.is_set():raise RuntimeError('MuMu 입력 중지')
                    if limit-(time.perf_counter()-started)<move_reserve:
                        raise RuntimeError('제한시간부족 · 시작 드롭 확인 후 전체 경로 이동 시간을 확보하지 못했습니다.')
                    if start_feedback.get('confirmed') is True:break
                    if start_feedback.get('reject_start') is True:break
                    if check<3:
                        if limit-(time.perf_counter()-started)<move_reserve+.04:
                            raise RuntimeError('제한시간부족 · 시작 드롭 재확인 시간을 확보하지 못했습니다.')
                        wait(.04)
                if start_feedback.get('confirmed') is not True:
                    if start_feedback.get('observed_start_cell') is not None:
                        wanted=start_feedback['expected_start_cell'];observed=start_feedback['observed_start_cell']
                        raise RuntimeError(f'MuMu 시작 위치 불일치 · {wanted//6+1}행 {wanted%6+1}열 대신 {observed//6+1}행 {observed%6+1}열이 들렸습니다. 경로 이동을 보내지 않았습니다.')
                    raise RuntimeError('MuMu 시작 드롭이 잡힌 것을 확인하지 못했습니다. 경로 이동을 보내지 않았습니다.')
            if probe:self.capture();held=True
            deadline=time.perf_counter();last_sent=deadline;motion_started=deadline
            for edge,(one,two) in enumerate(zip(points,points[1:])):
                if feedback_active and edge:
                    previous=points[edge-1]
                    before=(one[0]-previous[0],one[1]-previous[1])
                    after=(two[0]-one[0],two[1]-one[1])
                    if before[0]*after[1]!=before[1]*after[0] or before[0]*after[0]+before[1]*after[1]<=0:
                        # Async scrcpy can coalesce a corner before the game
                        # consumes it (reported 032, cell 7). Keep that center
                        # for several frames without adding input/capture.
                        wait(max(0,last_sent+corner_dwell-time.perf_counter()))
                for part in range(1,5):
                    deadline+=sample
                    # Bound catch-up to one display frame; never burst points
                    # or omit mandatory corners after a scheduling stall.
                    wait(max(0,deadline-time.perf_counter(),last_sent+(sample if timing is not None else ANDROID_TOUCH_SAMPLE_SECONDS)-time.perf_counter()))
                    down(round(one[0]+(two[0]-one[0])*part/4),round(one[1]+(two[1]-one[1])*part/4),2)
                    last_sent=time.perf_counter()
            motion_seconds=time.perf_counter()-motion_started
            if len(points)>1:
                # The last swap is still animating at 60 ms (reported v119).
                # Keep DOWN stationary until it settles; do not weaken board equality.
                wait(.22);capture_started=time.perf_counter()
                try:self.capture();held=True
                finally:capture_seconds['before_up']+=time.perf_counter()-capture_started
        except Exception as exc:error=str(exc)
        finally:
            if motion_started is not None and motion_seconds is None:motion_seconds=time.perf_counter()-motion_started
            try:
                cleanup=self.release_fingers(require_active=self.touch_active)
                released=cleanup.get('released') is True
            except Exception as exc:error=(error+'; ' if error else '')+str(exc);released=False
            self.touch_active=not released
            if released:trace.append(dict(action=1,native=list(last_point),seconds=time.perf_counter()-started))
            pacer.close()
            if not released:error=(error+'; ' if error else '')+'MuMu SDK 손가락 해제 실패'
        moves=[event['seconds'] for event in trace if event['action']==2]
        gaps=[b-a for a,b in zip(moves,moves[1:])]
        corner_gaps={4*i-1 for i in corners} if feedback_active else set()
        moving_gaps=[gap for i,gap in enumerate(gaps) if i not in corner_gaps]
        return dict(ready=True,events=trace,released=released,held_frame=held,error=error,execute_seconds=time.perf_counter()-started,
            preflight_release=preflight,cleanup=cleanup,pacer_mode=pacer.mode,
            start_feedback=start_feedback,display_id=int(self.display_id),input_stream=stream,
            move_gap_max_seconds=max(gaps,default=0),move_gap_over_50ms=sum(gap>.05 for gap in gaps),
            motion_seconds=motion_seconds,motion_estimate_seconds=motion_estimate,corner_count=len(corners) if feedback_active else 0,
            corner_dwell_seconds=corner_dwell,feedback_capture_seconds=capture_seconds,moving_gap_max_seconds=max(moving_gaps,default=0),input_timing=timing)


class AndroidControlTouch(PersistentAndroidTouch):
    """Android InputManager coordinates with read-only ADB visual proof.

    The capture owner supplies callbacks; this module stays image/GUI independent.
    Only one control socket owns DOWN, every MOVE, and UP for a gesture.
    """
    def drag(self,points,checkpoint=None,max_seconds=None,hold_seconds=.20):
        if checkpoint is not None:raise RuntimeError('Android 경로 중 중간 캡처 콜백을 허용하지 않습니다.')
        owner=self;device=self.device
        class Session(NativeSDKInputMixin):
            input_supported=True;touch_active=False;display_id=0;handle=0
            corner_dwell_seconds=ANDROID_CONTROL_CORNER_DWELL_SECONDS
            feedback_move_seconds=ANDROID_CONTROL_FEEDBACK_MOVE_SECONDS
            size=device.native_size
            def touch_down(self,handle,display,finger,x,y):
                owner.event(2 if owner.pressed else 0,x,y)
                return 0
            def release_fingers(self,require_active=False):
                if owner.pressed:owner.repeat_release(cancel=device.cancel.is_set())
                return dict(ready=True,released=not owner.pressed,all_fingers_confirmed=True)
            def prepare_input_feedback(self,context):return device.prepare_path_feedback(context)
            def check_input_start(self,context,before):return device.check_path_start(context,before)
            def capture(self):device.last_before_up_frame=device.capture_path_feedback()
        context=getattr(device,'sdk_start_context',None)
        if context is not None:context=dict(context,point=list(points[0]))
        # Capture latency is charged to the same bounded start/move budget.
        # A missing selected connection must never cause a blind DOWN.
        if len(points)>1 and context is not None and not device.feedback_ready():
            raise RuntimeError('시작 드롭 확인용 ADB 연결이 없습니다. 입력하지 않았습니다.')
        started=time.perf_counter();previous_trace=list(device.input_trace);session=Session()
        result=session.play_input(points,hold_seconds,float(max_seconds or 5),device.cancel,start_context=context)
        events=[dict(event,seconds=round(started-device._input_trace_started+event['seconds'],6),
                     transport='android_control',finger_id=0) for event in result['events']]
        # _send already records transport packets; replace only this gesture's
        # packet traces with the common logical trace used by layout verification.
        device.input_trace[:]=previous_trace+events
        if len(device.input_trace)>1024:del device.input_trace[:-1024]
        device.input_diagnostics.update(input_transport='android_control',input_size=list(device.native_size),
            control_dispatch_confirmed=not result['error'],control_release_confirmed=result['released'],
            control_start_feedback=result['start_feedback'],control_execute_seconds=result['execute_seconds'],
            control_move_gap_max_seconds=result['move_gap_max_seconds'],control_input_size=list(self.input_size))
        device.input_diagnostics.update(control_motion_seconds=result['motion_seconds'],control_motion_estimate_seconds=result['motion_estimate_seconds'],
            control_corner_count=result['corner_count'],control_corner_dwell_seconds=result['corner_dwell_seconds'],control_feedback_capture_seconds=result['feedback_capture_seconds'],
            control_moving_gap_max_seconds=result['moving_gap_max_seconds'],control_pacer_mode=result['pacer_mode'])
        if result['error']:raise RuntimeError(result['error'].replace('MuMu SDK','Android 직접 입력').replace('MuMu 시작','Android 시작').replace('MuMu 입력','Android 입력'))

    def probe(self,x,y):
        if not self.device.feedback_ready():raise RuntimeError('구름 확인용 ADB 연결이 없습니다.')
        try:
            self.event(0,x,y)
            if self.device.cancel.wait(.18):raise RuntimeError('구름 확인을 중지했습니다.')
            return self.device.capture_path_feedback()
        finally:self.release(cancel=self.device.cancel.is_set())


ANDROID_VERIFIED_MOVE_SECONDS=.11
ANDROID_VERIFIED_FIXED_RESERVE=1.65

class AndroidVerifiedBatchTouch(AndroidBatchTouch):
    """One Android-owned route between acknowledged DOWN and final UP.

    Image proof is supplied by the device. No per-MOVE host timer or socket
    packet is used, and continuation never emits a second DOWN.
    """
    def open(self):
        super().open()
        self.device.input_diagnostics['input_transport']='android_verified_batch'
        return self
    def _record_phase(self,events,started):
        for action,x,y,micros in events:
            self.last=(x,y)
            self.device.input_trace.append(dict(action=action,native=[x,y],finger_id=0,
                transport='android_verified_batch',seconds=round(started-self.device._input_trace_started+micros/1e6,6),android_elapsed_us=micros))
        if len(self.device.input_trace)>1024:del self.device.input_trace[:-1024]
    def drag(self,points,checkpoint=None,max_seconds=None,hold_seconds=.12):
        import struct
        device=self.device;limit=float(max_seconds or 5);context=getattr(device,'sdk_start_context',None)
        if context is None:return super().drag(points,checkpoint,max_seconds,hold_seconds)
        if checkpoint is not None:raise RuntimeError('Android 이동 중 PC 캡처 콜백을 허용하지 않습니다.')
        width,height=device.native_size
        if (not 2<=len(points)<=66 or any(len(pt)!=2 or any(type(v) is not int for v in pt) or not(0<=pt[0]<width and 0<=pt[1]<height) for pt in points)
                or not .1<=limit<=15):raise ValueError('Android 전체 경로 정보가 잘못됐습니다.')
        context=dict(context,point=list(points[0]))
        # Read a fresh baseline before DOWN, outside the in-game touch lease.
        if not device.feedback_ready():raise RuntimeError('시작 드롭 확인용 ADB 연결이 없습니다.')
        capture_started=time.perf_counter();baseline=device.prepare_path_feedback(context)
        capture_seconds=time.perf_counter()-capture_started
        reserve=max(.40,capture_seconds*1.3)+.18
        movement=(len(points)-1)*ANDROID_VERIFIED_MOVE_SECONDS
        if movement+hold_seconds+reserve*2>limit:raise RuntimeError('제한시간부족 · ADB 확인과 전체 경로 시간을 확보하지 못했습니다.')
        diagnostics=device.input_diagnostics
        diagnostics.update(input_transport='android_verified_batch',input_size=list(device.native_size),
            control_dispatch_confirmed=False,control_release_confirmed=False,control_start_feedback=None,
            control_capture_before_seconds=round(capture_seconds,6),control_android_acknowledged=False)
        device.last_after_release_frame=None
        self.pressed=True;device.cancel_command_sent=False;started=time.perf_counter();gesture=None;complete=False;movement_requested=False
        try:
            status,events,message=self._request(3,struct.pack('>iiii',*points[0],round(hold_seconds*1000),round(limit*1000)),timeout=2)
            self._record_phase(events,started)
            if status or not re.fullmatch(r'begun:[1-9]\d*',message):raise RuntimeError('Android 시작 누름 실패: '+message)
            gesture=int(message.split(':')[1])
            if len(events)!=1 or events[0][0]!=0 or tuple(events[0][1:3])!=tuple(points[0]):raise RuntimeError('Android 시작 누름 응답이 좌표와 다릅니다.')
            for attempt in range(4):
                if device.cancel.is_set():raise RuntimeError('자동 이동을 중지했습니다.')
                if time.perf_counter()-started+movement+reserve>limit:raise RuntimeError('제한시간부족 · 시작 드롭 확인 시간을 초과했습니다.')
                feedback=device.check_path_start(context,baseline)
                diagnostics['control_start_feedback']=dict(feedback,checks=attempt+1,seconds=round(time.perf_counter()-started,6))
                if device.cancel.is_set():raise RuntimeError('자동 이동을 중지했습니다.')
                if feedback.get('reject_start') is True:
                    raise RuntimeError('시작 드롭 불일치 · 지정한 드롭 대신 다른 위치의 판 변화가 보여 이동하지 않았습니다.')
                if feedback.get('confirmed') is True:break
                if attempt<3 and device.cancel.wait(.04):raise RuntimeError('자동 이동을 중지했습니다.')
            if feedback.get('confirmed') is not True:raise RuntimeError('시작 드롭을 확인하지 못했습니다. 경로를 보내지 않았습니다.')
            if time.perf_counter()-started+movement+reserve>limit:raise RuntimeError('제한시간부족 · 전체 Android 이동 시간을 확보하지 못했습니다.')
            body=struct.pack('>iiiii',gesture,len(points),100,22000,38000)+b''.join(struct.pack('>ii',*point) for point in points)
            movement_requested=True
            status,events,message=self._request(4,body,timeout=max(.1,limit-(time.perf_counter()-started))+.3)
            self._record_phase(events,started)
            if status or message!='held:'+str(gesture):raise RuntimeError('Android 경로 실행 실패: '+message)
            expected=[]
            for one,two in zip(points,points[1:]):
                expected.extend((math.floor(one[0]+(two[0]-one[0])*part/4+.5),math.floor(one[1]+(two[1]-one[1])*part/4+.5)) for part in range(1,5))
            if len(events)!=len(expected) or any(event[0]!=2 or tuple(event[1:3])!=point for event,point in zip(events,expected)):
                raise RuntimeError('Android 경로의 처리 응답이 전체 경유점과 다릅니다.')
            diagnostics['control_dispatch_confirmed']=True
            if device.cancel.is_set():raise RuntimeError('자동 이동을 중지했습니다.')
            device.last_before_up_frame=None
            device.last_before_up_frame=device.capture_path_feedback()
            check=device.verify_path_feedback(context,device.last_before_up_frame)
            diagnostics['control_before_up_layout']=check
            if check.get('ready_for_release') is not True:
                raise RuntimeError('자동 이동 후 실제 판이 계산 경로와 다릅니다. 소유한 터치를 취소하고 중지합니다.')
            if device.cancel.is_set():raise RuntimeError('자동 이동을 중지했습니다.')
            if time.perf_counter()-started>=limit-.08:raise RuntimeError('제한시간부족 · 최종 판 확인 시간을 초과했습니다.')
            status,events,message=self._request(5,struct.pack('>i',gesture),timeout=2)
            self._record_phase(events,started)
            if status or message!='released:'+str(gesture) or len(events)!=1 or events[0][0]!=1 or tuple(events[0][1:3])!=tuple(points[-1]):
                raise RuntimeError('Android 손 떼기 처리 응답을 확인하지 못했습니다: '+message)
            self.pressed=False;self.last_up_at=time.monotonic();complete=True
            diagnostics.update(control_dispatch_confirmed=True,control_release_confirmed=True,control_android_acknowledged=True)
        finally:
            if not complete:
                try:self.repeat_release(cancel=True)
                except Exception as error:device.cancel_release_error=str(error)
                # A separate, explicitly post-release image. Never make stopped
                # F10 capture again or use an input retry to manufacture success.
                if not device.cancel.is_set():
                    moved=movement_requested
                    for check in range(1 if moved else 3):
                        if device.cancel.is_set():break
                        try:
                            device.last_after_release_frame=device.capture_path_feedback()
                            observation=device.observe_failure_release(context,device.last_after_release_frame,moved)
                            diagnostics['failure_post_release_observation']=dict(observation,checks=check+1)
                            if moved or observation.get('stationary_board_restored') is True:break
                            if device.cancel.wait(.05):break
                        except Exception as error:
                            diagnostics['failure_post_release_capture_error']=str(error);break
            diagnostics['control_execute_seconds']=round(time.perf_counter()-started,6)
    def probe(self,x,y):
        import struct
        device=self.device;started=time.perf_counter()
        if not device.feedback_ready():raise RuntimeError('구름 확인용 ADB 연결이 없습니다.')
        try:
            self.pressed=True
            status,events,message=self._request(3,struct.pack('>iiii',x,y,120,2500),timeout=2)
            self._record_phase(events,started)
            if status or not re.fullmatch(r'begun:[1-9]\d*',message):raise RuntimeError('구름 드롭 누름 실패: '+message)
            if device.cancel.is_set():raise RuntimeError('구름 확인을 중지했습니다.')
            frame=device.capture_path_feedback()
            if device.cancel.is_set():raise RuntimeError('구름 확인을 중지했습니다.')
            gesture=int(message.split(':')[1])
            status,events,message=self._request(5,struct.pack('>i',gesture),timeout=2)
            self._record_phase(events,started)
            if status or message!='released:'+str(gesture) or len(events)!=1 or events[0][0]!=1 or tuple(events[0][1:3])!=(x,y):
                raise RuntimeError('구름 드롭 해제 미확인: '+message)
            self.pressed=False;self.last_up_at=time.monotonic()
            return frame
        finally:
            if self.pressed:self.repeat_release(cancel=True)
