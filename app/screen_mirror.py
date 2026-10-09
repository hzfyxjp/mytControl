"""把官方 WIN 投屏窗口嵌进坑位。五个坑位共用这一处，不按云机建定时器。"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from app.config import debug_log_dir, record_message, screen_sdk_dirs

EXE_CANDIDATES = (
    "android_window_silent.exe",
    "android_window.exe",
    "android_window_silent_admin.exe",
    "android_window_admin.exe",
)
LAUNCH_TIMEOUT_S = 8.0

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
else:
    _user32 = None
    _kernel32 = None


@dataclass
class SdkPaths:
    exe: Path
    lgcloud: Path


@dataclass
class MirrorSession:
    slot: int
    device_id: str
    title: str
    process: subprocess.Popen
    started: float
    hwnd: int = 0
    log_file: object | None = None


class ScreenMirrors:
    def __init__(self) -> None:
        self._sessions: dict[int, MirrorSession] = {}
        self._seq = 0
        self._job = _create_job()

    def active_ids(self) -> set[str]:
        alive: set[str] = set()
        for session in self._sessions.values():
            if session.process.poll() is None or session.hwnd:
                alive.add(session.device_id)
        return alive

    def ensure(
        self,
        slot: int,
        device_id: str,
        name: str,
        host_ip: str,
        video_port: int,
        control_port: int,
        parent_hwnd: int,
    ) -> str:
        current = self._sessions.get(slot)
        if current and current.device_id == device_id and current.process.poll() is None:
            if current.hwnd:
                _fit_child(current.hwnd, parent_hwnd)
            return "embedded" if current.hwnd else "launching"
        self.stop(slot)
        sdk = resolve_sdk()
        if sdk is None:
            return "missing_sdk"
        self._seq += 1
        title = f"MYT{slot}-{self._seq}-{name}"[:80]
        log_handle = _open_launch_log()
        try:
            process = subprocess.Popen(
                [
                    str(sdk.exe),
                    "-ip",
                    host_ip,
                    "-vport",
                    str(video_port),
                    "-cport",
                    str(control_port),
                    "-title",
                    title,
                    "-path",
                    str(sdk.lgcloud),
                ],
                cwd=str(sdk.exe.parent),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            record_message(f"启动投屏失败: {exc}")
            close = getattr(log_handle, "close", None)
            if close is not None:
                close()
            return "start_failed"
        _assign_job(self._job, process)
        self._sessions[slot] = MirrorSession(
            slot=slot,
            device_id=device_id,
            title=title,
            process=process,
            started=time.monotonic(),
            log_file=log_handle,
        )
        return "launching"

    def poll(self, parents: dict[int, int]) -> dict[int, str]:
        updates: dict[int, str] = {}
        for slot, session in list(self._sessions.items()):
            parent = parents.get(slot, 0)
            if session.hwnd:
                alive = session.process.poll() is None and _is_window(session.hwnd)
                if not alive:
                    self._close_session(session)
                    self._sessions.pop(slot, None)
                    updates[slot] = "exited"
                    continue
                if parent:
                    _fit_child(session.hwnd, parent)
                continue
            hwnd = _find_hwnd(session.title)
            if hwnd and parent:
                _embed(hwnd, parent)
                session.hwnd = hwnd
                updates[slot] = "embedded"
                continue
            if session.process.poll() is not None:
                record_message(f"投屏进程退出 slot={slot} code={session.process.returncode}")
                self._close_session(session)
                self._sessions.pop(slot, None)
                updates[slot] = "exited"
                continue
            if time.monotonic() - session.started > LAUNCH_TIMEOUT_S:
                record_message(f"投屏窗口超时 slot={slot} title={session.title}")
                self.stop(slot)
                updates[slot] = "timeout"
        return updates

    def stop(self, slot: int) -> None:
        session = self._sessions.pop(slot, None)
        if session is not None:
            self._close_session(session)

    def stop_all(self) -> None:
        for slot in list(self._sessions):
            self.stop(slot)

    @staticmethod
    def _close_session(session: MirrorSession) -> None:
        if session.process.poll() is None:
            session.process.terminate()
        close = getattr(session.log_file, "close", None)
        if close is not None:
            try:
                close()
            except OSError:
                pass


_cached_sdk: SdkPaths | None | bool = None


def resolve_sdk() -> SdkPaths | None:
    global _cached_sdk
    if _cached_sdk is False:
        return None
    if isinstance(_cached_sdk, SdkPaths):
        return _cached_sdk
    found = None
    for folder in screen_sdk_dirs():
        found = find_sdk(folder)
        if found is not None:
            break
    _cached_sdk = found if found is not None else False
    if found is None:
        record_message(f"缺少投屏组件: {', '.join(str(folder) for folder in screen_sdk_dirs())}")
    return found


def sdk_available() -> bool:
    return resolve_sdk() is not None


def find_sdk(root: Path) -> SdkPaths | None:
    if not root.exists():
        return None
    found: dict[str, Path] = {}
    lgcloud: Path | None = None
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).relative_to(root).parts) > 3:
            dirnames.clear()
            continue
        for filename in filenames:
            lower = filename.lower()
            if lower in EXE_CANDIDATES and lower not in found:
                found[lower] = Path(dirpath) / filename
            if lower == "lgcloud.exe" and lgcloud is None:
                lgcloud = Path(dirpath) / filename
    for name in EXE_CANDIDATES:
        exe = found.get(name)
        if exe is None:
            continue
        cloud = lgcloud or (exe.parent / "LgCloud.exe")
        return SdkPaths(exe, cloud)
    return None


def _open_launch_log():
    try:
        folder = debug_log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{time.strftime('%Y-%m-%d')}.log"
        return path.open("ab", buffering=0)
    except OSError:
        return subprocess.DEVNULL


def _find_hwnd(title: str) -> int:
    if _user32 is None or not title:
        return 0
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        text = buffer.value
        if text == title or text.startswith(title):
            found.append(int(hwnd))
            return False
        return True

    _user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def _embed(hwnd: int, parent: int) -> None:
    if _user32 is None:
        return
    _user32.SetParent(hwnd, parent)
    style = _get_style(hwnd)
    style = (style & ~0x00C00000 & ~0x00040000 & ~0x80000000) | 0x40000000 | 0x10000000
    _set_style(hwnd, style)
    _fit_child(hwnd, parent)


def _fit_child(hwnd: int, parent: int) -> None:
    if _user32 is None or not hwnd or not parent or not _is_window(hwnd):
        return
    rect = wintypes.RECT()
    _user32.GetClientRect(parent, ctypes.byref(rect))
    _user32.MoveWindow(hwnd, 0, 0, rect.right - rect.left, rect.bottom - rect.top, True)


def _get_style(hwnd: int) -> int:
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _user32.GetWindowLongPtrW.restype = ctypes.c_void_p
        _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        return int(_user32.GetWindowLongPtrW(hwnd, -16) or 0)
    return int(_user32.GetWindowLongW(hwnd, -16) or 0)


def _set_style(hwnd: int, style: int) -> None:
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        _user32.SetWindowLongPtrW(hwnd, -16, ctypes.c_void_p(style))
        return
    _user32.SetWindowLongW(hwnd, -16, style)


def _is_window(hwnd: int) -> bool:
    if _user32 is None or not hwnd:
        return False
    try:
        return bool(_user32.IsWindow(hwnd))
    except (OSError, AttributeError):
        return False


def _configure_win32() -> None:
    if _user32 is None:
        return
    _user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    _user32.SetParent.restype = wintypes.HWND
    _user32.MoveWindow.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.BOOL,
    ]
    _user32.MoveWindow.restype = wintypes.BOOL
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    if _kernel32 is None:
        return
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL


def _create_job():
    if _kernel32 is None:
        return None
    try:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000
        ok = _kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            return None
        return job
    except (OSError, AttributeError):
        return None


def _assign_job(job, process: subprocess.Popen) -> None:
    if _kernel32 is None or not job:
        return
    handle = getattr(process, "_handle", None)
    if not handle:
        return
    try:
        _kernel32.AssignProcessToJobObject(job, handle)
    except (OSError, AttributeError):
        return


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_configure_win32()
