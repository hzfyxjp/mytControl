"""路径、端口和崩溃日志。"""

from __future__ import annotations

import ipaddress
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

API_PORT = 8000
DEBUG_LOG_KEEP_DAYS = 7


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    appdata = Path.home() / "AppData" / "Roaming"
    env = os.environ.get("APPDATA")
    if env:
        appdata = Path(env)
    return appdata / "MoyuntengControl"


def db_path() -> Path:
    return data_dir() / "moyunteng.sqlite3"


def debug_log_dir() -> Path:
    return app_dir() / "debug_log"


def parse_ipv4(text: str) -> str | None:
    try:
        return str(ipaddress.IPv4Address((text or "").strip()))
    except ValueError:
        return None


def process_thread_count() -> int:
    """当前进程总线程数（Windows 用快照；其它平台退回 Python 线程数）。"""
    if sys.platform != "win32":
        import threading

        return max(1, threading.active_count())
    import ctypes
    from ctypes import wintypes

    th32cs_snapthread = 0x00000004
    invalid = wintypes.HANDLE(-1).value

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(th32cs_snapthread, 0)
    if snap == invalid:
        import threading

        return max(1, threading.active_count())
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(THREADENTRY32)
    count = 0
    pid = os.getpid()
    try:
        more = kernel32.Thread32First(snap, ctypes.byref(entry))
        while more:
            if int(entry.th32OwnerProcessID) == pid:
                count += 1
            more = kernel32.Thread32Next(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return max(1, count)


def prune_debug_logs(keep_days: int = DEBUG_LOG_KEEP_DAYS) -> None:
    folder = debug_log_dir()
    if not folder.exists():
        return
    cutoff = datetime.now().date() - timedelta(days=keep_days)
    for path in folder.glob("*.log"):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink(missing_ok=True)


def record_message(context: str) -> None:
    """只记失败/异常类消息，不写正常过程。"""
    folder = debug_log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y-%m-%d}.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {context}\n")


def record_exception(context: str, exc_type=None, exc=None, tb=None) -> None:
    if exc_type is None:
        exc_type, exc, tb = sys.exc_info()
    folder = debug_log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y-%m-%d}.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {context}\n")
        if exc_type is None:
            handle.write("无异常详情\n")
            return
        traceback.print_exception(exc_type, exc, tb, file=handle)
