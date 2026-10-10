"""魔云腾中控入口。"""

from __future__ import annotations

import os
import sys
import threading

# 局域网 WebRTC：禁止把内网 IP 藏成 *.local（否则常报 errorcode -105 / UDP -2）
# 五坑各一路投屏，渲染进程至少给满 5；limit=2 时打包后常见一路黑屏/起不来
_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
# 去掉旧包/环境里残留的过严限制
_parts = [p for p in _flags.split() if not p.startswith("--renderer-process-limit=")]
_flags = " ".join(_parts)
for _need in (
    "--disable-features=WebRtcHideLocalIpsWithMdns",
    "--renderer-process-limit=5",
):
    if _need not in _flags:
        _flags = f"{_flags} {_need}".strip()
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _flags

# QtWebEngine 须在创建 QApplication 之前导入
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PySide6.QtWidgets import QApplication

from app.config import prune_debug_logs, record_exception
from app.main_window import MainWindow


def _install_hooks() -> None:
    def _sys(exc_type, exc, tb):
        record_exception("主线程", exc_type, exc, tb)
        sys.__excepthook__(exc_type, exc, tb)

    def _thread(args):
        record_exception("线程", args.exc_type, args.exc_value, args.exc_traceback)

    def _unraisable(unraisable):
        record_exception(
            "unraisable",
            unraisable.exc_type,
            unraisable.exc_value,
            unraisable.exc_traceback,
        )

    sys.excepthook = _sys
    threading.excepthook = _thread
    sys.unraisablehook = _unraisable


def _configure_webrtc_profile() -> None:
    """允许 WebRTC 使用局域网私有地址，否则投屏连不上盒子。"""
    settings = QWebEngineProfile.defaultProfile().settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)


def main() -> None:
    _install_hooks()
    prune_debug_logs()
    app = QApplication(sys.argv)
    app.setApplicationName("MoyuntengControl")
    _configure_webrtc_profile()
    try:
        window = MainWindow()
        window.show()
    except Exception:
        record_exception("启动界面")
        raise
    app.exec()


if __name__ == "__main__":
    main()
