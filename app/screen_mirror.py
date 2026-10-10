"""官方 WebRTC 投屏：本地 webplayer + QWebEngineView。

文档：https://dev.moyunteng.com/docs/NewMYTOS/MYT_ANDROID_webrtctouping
非桥接端口：TCP = 30000+(index-1)*100+7，UDP = 30000+(index-1)*100+8
桥接端口：TCP/UDP 均为 10008。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.config import app_dir, record_exception, record_message
from app.models import webrtc_ports

# 官方播放器：postMessage({type:"close"}) / on_play_end → g_player.end()，通知主机停编码
_DISCONNECT_JS = """
(function(){
  try { window.__mytMirrorClosing = true; } catch (e) {}
  try {
    if (window.h5_lgair && typeof window.h5_lgair.on_play_end === 'function') {
      window.h5_lgair.on_play_end({text:'closing'}, 0);
      return;
    }
  } catch (e) {}
  try { window.postMessage({type:'close'}, '*'); } catch (e) {}
})();
"""

# 断线后官方会挂 start.jpg（#lgairad）；主动关闭时打了 __mytMirrorClosing，不算待重连
_PROBE_WAITING_JS = """
(function(){
  try {
    if (window.__mytMirrorClosing) return 'closing';
    if (document.getElementById('lgairad')) return 'waiting';
    return 'ok';
  } catch (e) { return 'ok'; }
})();
"""

STREAM_PROBE_COOLDOWN_S = 5.0
TIMEOUT_COOLDOWN_S = 15.0
# 同一拍最多新开几路，避免五路同时 setUrl 抢崩/黑一坑
_LAUNCH_BUDGET_DEFAULT = 2

__all__ = [
    "ScreenMirrors",
    "player_url",
    "sdk_available",
    "webrtc_ports",
    "webplayer_dir",
]


@dataclass
class MirrorSession:
    slot: int
    device_id: str
    host_ip: str
    tcp_port: int
    udp_port: int
    url: str = ""


_player_server: ThreadingHTTPServer | None = None
_player_lock = threading.Lock()
_player_port = 0


def webplayer_dir() -> Path:
    return app_dir() / "webplayer"


def sdk_available() -> bool:
    root = webplayer_dir()
    return (root / "play.html").is_file() and (root / "js" / "h5.lgair.wt.all.min-2.2.js").is_file()


def player_url(
    host_ip: str,
    tcp_port: int,
    udp_port: int,
    *,
    quality: int = 1,
    width: int = 360,
    height: int = 640,
) -> str:
    """官方 play.html 参数：shost/sport/q/v/rtc_i/rtc_p（对齐客户端 dashboard）。"""
    base = _ensure_player_server()
    query = urlencode(
        {
            "shost": host_ip,
            "sport": str(int(tcp_port)),
            "q": str(int(quality)),
            "v": "h264",
            "rtc_i": host_ip,
            "rtc_p": str(int(udp_port)),
            "width": str(int(width)),
            "height": str(int(height)),
        }
    )
    return f"{base}/play.html?{query}"


def configure_webrtc_view(view: QWebEngineView) -> None:
    """每个投屏视图允许局域网 WebRTC，并自动授权媒体权限。"""
    if view is None or getattr(view, "_myt_webrtc_configured", False):
        return
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    page = view.page()

    def _grant(origin, feature) -> None:
        page.setFeaturePermission(
            origin,
            feature,
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser,
        )

    page.featurePermissionRequested.connect(_grant)
    view._myt_webrtc_configured = True


def _ensure_player_server() -> str:
    global _player_server, _player_port
    with _player_lock:
        if _player_server is not None and _player_port:
            return f"http://127.0.0.1:{_player_port}"
        root = webplayer_dir()
        if not (root / "play.html").is_file():
            raise FileNotFoundError(f"缺少官方 webplayer: {root}")
        class _QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A003
                # 启动图等可选资源 404 不刷控制台
                if len(args) >= 2 and str(args[1]) == "404":
                    return

        handler = partial(_QuietHandler, directory=str(root))

        class _Server(ThreadingHTTPServer):
            allow_reuse_address = True

        server = _Server(("127.0.0.1", 0), handler)
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, name="webplayer-http", daemon=True)
        thread.start()
        _player_server = server
        _player_port = port
        return f"http://127.0.0.1:{port}"


def _tcp_listening(host: str, port: int, timeout: float = 0.35) -> bool:
    if not host or port < 1 or port > 65535:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


class ScreenMirrors:
    def __init__(self) -> None:
        self._sessions: dict[int, MirrorSession] = {}
        self._skipped: dict[int, str] = {}
        self._cooldown_until: dict[int, float] = {}
        self._views: dict[int, QWebEngineView] = {}
        self._launch_budget = _LAUNCH_BUDGET_DEFAULT
        self._fail_bound: set[int] = set()

    def reset_launch_budget(self, n: int = _LAUNCH_BUDGET_DEFAULT) -> None:
        self._launch_budget = max(1, int(n))

    def forget_skip(self, slot: int) -> None:
        self._skipped.pop(slot, None)
        self._cooldown_until.pop(slot, None)

    def has_session(self, slot: int, device_id: str = "") -> bool:
        session = self._sessions.get(slot)
        if session is None:
            return False
        if device_id and session.device_id != device_id:
            return False
        return True

    def invalidate(self, slot: int, *, cooldown_s: float = STREAM_PROBE_COOLDOWN_S) -> None:
        """页面加载失败 / 渲染进程挂掉后清会话，交给轮询重试。"""
        import time

        self._sessions.pop(slot, None)
        if cooldown_s > 0:
            self._cooldown_until[slot] = time.monotonic() + cooldown_s

    def is_user_skipped(self, slot: int, device_id: str = "") -> bool:
        skipped = self._skipped.get(slot)
        if not skipped:
            return False
        if device_id and skipped != device_id:
            return False
        return True

    def in_cooldown(self, slot: int) -> bool:
        import time

        until = self._cooldown_until.get(slot, 0.0)
        if until <= 0:
            return False
        if time.monotonic() >= until:
            self._cooldown_until.pop(slot, None)
            return False
        return True

    def ensure(
        self,
        slot: int,
        device_id: str,
        name: str,
        host_ip: str,
        tcp_port: int,
        udp_port: int,
        view: QWebEngineView | None,
        *,
        force: bool = False,
        reload: bool = False,
    ) -> str:
        del name  # 官方 URL 不需要标题
        import time

        if view is None:
            return "launching"
        if self._skipped.get(slot) == device_id:
            return "cancelled"
        if not force and self.in_cooldown(slot):
            return "cooldown"
        if not sdk_available():
            return "missing_sdk"
        current = self._sessions.get(slot)
        if current and current.device_id != device_id:
            self.stop(slot)
            current = None
        url = player_url(host_ip, int(tcp_port), int(udp_port))
        same_target = bool(
            current
            and current.device_id == device_id
            and current.host_ip == host_ip
            and current.tcp_port == int(tcp_port)
            and current.udp_port == int(udp_port)
            and current.url == url
            and self._views.get(slot) is view
        )
        # force 清冷却；reload 才允许同地址再 setUrl（重启补投）。普通加载不反复刷。
        if force:
            self._cooldown_until.pop(slot, None)
        if same_target and not reload:
            return "embedded"
        if not force and self._launch_budget <= 0:
            return "launching"
        if not _tcp_listening(host_ip, int(tcp_port), timeout=0.2):
            self._cooldown_until[slot] = time.monotonic() + STREAM_PROBE_COOLDOWN_S
            return "cooldown"
        try:
            _ensure_player_server()
        except Exception:
            record_exception("启动 webplayer 服务")
            return "missing_sdk"
        self._views[slot] = view
        self._bind_view_failures(slot, view)
        session = MirrorSession(
            slot=slot,
            device_id=device_id,
            host_ip=host_ip,
            tcp_port=int(tcp_port),
            udp_port=int(udp_port),
            url=url,
        )
        self._sessions[slot] = session
        try:
            configure_webrtc_view(view)
            if not force:
                self._launch_budget -= 1
            view.setUrl(QUrl(url))
            view.show()
        except Exception:
            record_exception("加载 WebRTC 投屏")
            self._sessions.pop(slot, None)
            self._cooldown_until[slot] = time.monotonic() + TIMEOUT_COOLDOWN_S
            return "start_failed"
        return "embedded"

    def _bind_view_failures(self, slot: int, view: QWebEngineView) -> None:
        if getattr(view, "_myt_fail_bound", False):
            return
        page = view.page()

        def _on_load(ok: bool, slot=slot, view=view) -> None:
            if ok:
                return
            if self._views.get(slot) is not view or slot not in self._sessions:
                return
            record_message(f"WebRTC 页面加载失败 slot={slot}，将重试")
            self.invalidate(slot)

        def _on_crash(_status, _code, slot=slot, view=view) -> None:
            if self._views.get(slot) is not view:
                return
            record_message(f"WebRTC 渲染进程异常 slot={slot}，将重试")
            self.invalidate(slot, cooldown_s=STREAM_PROBE_COOLDOWN_S)

        try:
            page.loadFinished.connect(_on_load)
            page.renderProcessTerminated.connect(_on_crash)
            view._myt_fail_bound = True
            self._fail_bound.add(slot)
        except (TypeError, RuntimeError, AttributeError):
            return

    def poll(self, _parents: dict[int, int] | None = None) -> dict[int, str]:
        # 存活由 probe_waiting + 主窗口按「运行中」决定是否重载。
        return {}

    def probe_waiting(self, slot: int, callback) -> None:
        """异步探测是否停在等待重试页。callback(slot, 'ok'|'waiting'|'closing'|'none')。"""
        view = self._views.get(slot)
        if view is None or slot not in self._sessions:
            callback(slot, "none")
            return
        page = view.page()

        def _done(result, slot=slot) -> None:
            status = result if result in {"ok", "waiting", "closing"} else "ok"
            try:
                callback(slot, status)
            except Exception:
                record_exception(f"投屏探测回调 slot={slot}")

        try:
            page.runJavaScript(_PROBE_WAITING_JS, _done)
        except RuntimeError:
            callback(slot, "none")

    def stop(self, slot: int, *, disconnect: bool = True) -> None:
        self._sessions.pop(slot, None)
        view = self._views.pop(slot, None)
        if view is None:
            return
        try:
            if disconnect:
                view.page().runJavaScript(_DISCONNECT_JS)
            view.setUrl(QUrl("about:blank"))
        except RuntimeError:
            pass

    def stop_all(self, *, disconnect: bool = True) -> None:
        for slot in list(self._sessions):
            self.stop(slot, disconnect=disconnect)

    def release_for_exit(self) -> None:
        """退出前断开全部 WebRTC，并干掉本进程拉起的 QtWebEngine 子进程。

        不 join、不等待线程池；只冲几拍事件循环，避免主机侧继续编码占满 CPU。
        """
        self.stop_all(disconnect=True)
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                for _ in range(5):
                    app.processEvents()
        except Exception:
            pass
        kill_child_webengine_processes()

    def mark_cancelled(self, slot: int, device_id: str) -> None:
        self._skipped[slot] = device_id
        self._cooldown_until.pop(slot, None)
        self.stop(slot)


def kill_child_webengine_processes() -> int:
    """结束本进程的子级 QtWebEngineProcess，迫使 ICE/DTLS 立刻掉线。"""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes

    th32cs_snapprocess = 0x00000002
    invalid = wintypes.HANDLE(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if snap == invalid:
        return 0
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    parent = os.getpid()
    killed = 0
    try:
        more = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            name = (entry.szExeFile or "").lower()
            if (
                int(entry.th32ParentProcessID) == parent
                and "qtwebengineprocess" in name
            ):
                handle = kernel32.OpenProcess(0x0001, False, int(entry.th32ProcessID))
                if handle:
                    if kernel32.TerminateProcess(handle, 1):
                        killed += 1
                    kernel32.CloseHandle(handle)
            more = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return killed
