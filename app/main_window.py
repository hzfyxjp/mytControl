"""五坑位中控窗口。"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from collections import deque
from html import escape
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QSize, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.config import parse_ipv4, process_thread_count, record_exception, record_message
from app.database import Database
from app.s5_proxy import parse_s5_pool, take_unique_proxies
from app.screen_mirror import ScreenMirrors, sdk_available
from app.script_timing import ScriptTimings
from app.statuses import (
    DELETING,
    EMPTY_SLOT_HEADING,
    INSTALLING,
    RESTARTING,
    RUNNING,
    SCRIPTING,
    SETTING_PROXY,
    slot_heading,
)
from app.tasks import TaskEvent, TaskService
from app.xhs_ext import ScriptContent
from app.xhs_scripts import SCRIPT_HANDLERS

REFRESH_INTERVAL_S = 2.0
EVENT_INTERVAL_MS = 50
THREAD_LABEL_INTERVAL_S = 1.0
MIRROR_STOP_DEBOUNCE_S = 2.5
# 检测到等待页后最短隔多久再 setUrl，避免狂刷
MIRROR_RECONNECT_MIN_S = 3.0
# 9082 通了之后再等一会，WebRTC 口才稳；期间保留 pending，失败会强制重载
RESTART_MIRROR_DELAY_S = 5.0
RESTART_MIRROR_RETRY_S = 8.0
RESTART_MIRROR_GIVE_UP_S = 180.0

SLOT_ACTIONS = [
    ("home", "主页"),
    ("comment", "评论"),
    ("switch", "切号"),
    ("join", "进群"),
    ("phone", "取号"),
    ("code", "取码"),
    ("return_phone", "返号"),
    ("edit", "改收"),
    ("stop", "停止"),
]

# 对齐官方/安卓导航栏：◀ 返回 / ● 主页 / ■ 后台
SLOT_NAV_ACTIONS = [
    ("nav_back", "◀"),
    ("nav_home", "●"),
    ("nav_tasks", "■"),
]

SLOT_MANAGE_ACTIONS = [
    ("delete", "删除"),
    ("restart", "重启"),
    ("mirror", "投屏"),
    ("master", "主控"),
]

_SLOT_FRAME_NORMAL = """
QFrame#SlotPanel {
    border: 1px solid #222222;
    background: #ffffff;
}
"""

_SLOT_FRAME_MASTER = """
QFrame#SlotPanel {
    border: 3px solid #2563eb;
    background: #ffffff;
}
"""

_BTN_HOVER = """
QPushButton {
    background: #f3f4f6;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 14px;
    min-height: 30px;
}
QPushButton:hover:!disabled {
    background: #dbeafe;
    border: 1px solid #60a5fa;
    color: #1d4ed8;
}
QPushButton:pressed:!disabled {
    background: #bfdbfe;
    border: 1px solid #3b82f6;
}
QPushButton:disabled {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    color: #9ca3af;
}
"""

_BTN_DELETE = """
QPushButton {
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 6px;
    color: #c62828;
    font-weight: 600;
    padding: 6px 12px;
    font-size: 14px;
    min-height: 30px;
}
QPushButton:hover:!disabled {
    background: #fecaca;
    border: 1px solid #ef4444;
    color: #b91c1c;
}
QPushButton:pressed:!disabled {
    background: #fca5a5;
}
QPushButton:disabled {
    background: #fef2f2;
    border: 1px solid #fecaca;
    color: #e57373;
}
"""

_BTN_RESTART = """
QPushButton {
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    border-radius: 6px;
    color: #2e7d32;
    font-weight: 600;
    padding: 6px 12px;
    font-size: 14px;
    min-height: 30px;
}
QPushButton:hover:!disabled {
    background: #bbf7d0;
    border: 1px solid #22c55e;
    color: #166534;
}
QPushButton:pressed:!disabled {
    background: #86efac;
}
QPushButton:disabled {
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    color: #81c784;
}
"""

_BTN_SIDE = """
QPushButton {
    background: #f3f4f6;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 4px 4px;
    font-size: 13px;
    min-height: 28px;
}
QPushButton:hover:!disabled {
    background: #dbeafe;
    border: 1px solid #60a5fa;
    color: #1d4ed8;
}
QPushButton:pressed:!disabled {
    background: #bfdbfe;
    border: 1px solid #3b82f6;
}
QPushButton:disabled {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    color: #9ca3af;
}
"""

_BTN_NAV = """
QPushButton {
    background: transparent;
    /* 常态也占 1px，hover 只改颜色，避免撑破行高 */
    border: 1px solid transparent;
    border-radius: 0px;
    color: #555555;
    font-size: 16px;
    font-weight: 700;
    padding: 0px;
    margin: 0px;
}
QPushButton:hover:!disabled {
    background: #d4d4d4;
    border: 1px solid #9a9a9a;
    color: #333333;
}
QPushButton:pressed:!disabled {
    background: #c4c4c4;
    border: 1px solid #888888;
}
QPushButton:disabled {
    color: #b0b0b0;
    background: transparent;
    border: 1px solid transparent;
}
"""

# 与单坑脚本按钮对应（不含停止）；用户文案里重复的「切号」按一次处理
BATCH_SCRIPT_ACTIONS = [
    (action, f"批量{label}")
    for action, label in SLOT_ACTIONS
    if action != "stop"
]


_MIRROR_HINTS = {
    "": "暂无投屏",
    "idle": "暂无投屏",
    "launching": "排队启动投屏…",
    "embedded": "",
    "missing_sdk": "缺少 webplayer/",
    "start_failed": "投屏启动失败",
    "timeout": "连接超时，稍后重试",
    "cooldown": "等待重连…",
    "exited": "投屏已退出",
    "no_ports": "缺少 WebRTC 端口",
    "cancelled": "已取消投屏",
}

PHONE_ASPECT_W = 9
PHONE_ASPECT_H = 16


def phone_height_for_width(width: int) -> int:
    """竖屏 9:16：高度 = 宽度 × 16 / 9。"""
    return max(1, int(max(int(width), 1) * PHONE_ASPECT_H / PHONE_ASPECT_W))


def selected_batch_names(slots: list[tuple[bool, str]]) -> list[str]:
    """批量只处理勾选且当前有云机的坑位。"""
    return [name for checked, name in slots if checked and name]


class MirrorTouchOverlay(QWidget):
    """群控主控坑透明触控层：挂在 WebView 子级上（与上次流畅方案一致）。

    关闭群控时必须 hide（不要用鼠标穿透），否则会挡原生拖动。
    """

    def __init__(self, view: "MirrorWebView") -> None:
        super().__init__(view)
        self._view = view
        self._dragging = False
        self.setStyleSheet("background: transparent;")
        # 拖动时用 grabMouse，不必常开 tracking，少产生无用 move
        self.setMouseTracking(False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def sync_geometry(self) -> None:
        self.setGeometry(self._view.rect())
        if self.isVisible():
            self.raise_()

    def set_capture(self, enabled: bool) -> None:
        if enabled:
            self.sync_geometry()
            self.show()
            self.raise_()
        else:
            if self._dragging:
                self._finish_at(self.width() // 2, self.height() // 2)
            if QWidget.mouseGrabber() is self:
                self.releaseMouse()
            self.hide()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self.grabMouse()
        pos = event.position()
        self._view.dispatch_touch("down", int(pos.x()), int(pos.y()), sync=True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        pos = event.position()
        self._view.dispatch_touch("move", int(pos.x()), int(pos.y()), sync=True)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        self._finish_at(int(pos.x()), int(pos.y()))
        event.accept()

    def leaveEvent(self, event) -> None:
        # grabMouse 期间仍可能收到 leave；拖动中不在这里抬手，避免误断
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        self._view.handle_wheel(event, sync=True)

    def _finish_at(self, x: int, y: int) -> None:
        self._dragging = False
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self._view.dispatch_touch("up", x, y, sync=True)


class MirrorWebView(QWebEngineView):
    """官方播放器；非群控原生触控；群控主控时子级遮罩注入（更跟手）。"""

    _WHEEL_STEP = 240
    _SWIPE_PX = 40
    _COOLDOWN_MS = 220
    # 主控 move 合并约 60fps；跟随同步约 30fps（AGENTS：事件节流，避免洪峰）
    _MOVE_FLUSH_MS = 16
    _FOLLOW_MOVE_MS = 33
    _TOUCH_FN = {
        "down": "sendTouchStartEvent",
        "move": "sendTouchMoveEvent",
        "up": "sendTouchEndEvent",
    }
    # 只在指针真正离开页面文档时抬手（relatedTarget==null）。
    _INSTALL_LEAVE_JS = """
    (function(){
      if(window.__mytLeaveHook)return;
      window.__mytLeaveHook=true;
      function endDrag(ev){
        var p=window.h5_lgair;
        if(!p||!p.g_mouse_down||typeof p.sendTouchEndEvent!=='function')return;
        var x=1,y=1;
        if(ev&&typeof ev.clientX==='number'){x=ev.clientX;y=ev.clientY;}
        try{p.sendTouchEndEvent(x,y);}catch(e){}
      }
      document.addEventListener('mouseout', function(ev){
        if(ev.relatedTarget==null) endDrag(ev);
      }, true);
      window.addEventListener('blur', function(){ endDrag(null); }, false);
    })();
    """
    # 非群控时滚轮常被 Chromium 吃掉；页面里转成触摸滑动。
    _INSTALL_WHEEL_JS = """
    (function(){
      if(window.__mytWheelHook)return;
      window.__mytWheelHook=true;
      var acc=0, last=0;
      var STEP=240, COOLDOWN=220, DIST=40;
      document.addEventListener('wheel', function(e){
        if(window.__mytOverlayCapture)return;
        e.preventDefault();
        e.stopPropagation();
        var p=window.h5_lgair;
        if(!p||typeof p.sendTouchStartEvent!=='function')return;
        var dy = e.deltaY;
        if(e.deltaMode===1) dy *= 40;
        else if(e.deltaMode===2) dy *= (window.innerHeight||800);
        acc += dy;
        if(Math.abs(acc)<STEP)return;
        var now=Date.now();
        if(now-last<COOLDOWN){acc=0;return;}
        var scrollDown = acc>0;
        acc=0;
        last=now;
        var maxX=Math.max(1,(window.innerWidth||2)-1);
        var maxY=Math.max(1,(window.innerHeight||2)-1);
        var x=Math.max(1, Math.min(maxX, Math.floor(e.clientX||maxX/2)));
        var y=Math.max(1, Math.min(maxY, Math.floor(e.clientY||maxY/2)));
        var y2=scrollDown ? Math.max(1,y-DIST) : Math.min(maxY,y+DIST);
        p.sendTouchStartEvent(x,y);
        setTimeout(function(){
          p.sendTouchMoveEvent(x,y2);
          setTimeout(function(){p.sendTouchEndEvent(x,y2);},24);
        },20);
      }, {passive:false, capture:true});
    })();
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.slot_index = 0
        self._wheel_acc = 0
        self._last_swipe_ms = 0.0
        self._group_enabled = False
        self._pending_move: tuple[int, int, bool] | None = None
        self._pending_follow_move: tuple[int, int] | None = None
        self._last_follow_move_ms = 0.0
        self._on_group_touch = None
        self._on_group_swipe = None
        self._overlay = MirrorTouchOverlay(self)
        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.setInterval(self._MOVE_FLUSH_MS)
        self._move_timer.timeout.connect(self._flush_pending_move)
        self.loadFinished.connect(self._on_load_finished)

    def set_group_sync(self, on_touch, on_swipe) -> None:
        self._on_group_touch = on_touch
        self._on_group_swipe = on_swipe

    def set_group_capture(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._group_enabled == enabled:
            if enabled:
                self._overlay.set_capture(True)
            return
        self._group_enabled = enabled
        flag = "true" if enabled else "false"
        self.page().runJavaScript(f"window.__mytOverlayCapture={flag};")
        if not enabled:
            self._move_timer.stop()
            self._pending_move = None
            self._pending_follow_move = None
        self._overlay.set_capture(enabled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._overlay.sync_geometry()

    def _clamp_xy(self, x: int, y: int) -> tuple[int, int]:
        max_x = max(1, self.width() - 1)
        max_y = max(1, self.height() - 1)
        return max(1, min(max_x, int(x))), max(1, min(max_y, int(y)))

    def inject_touch(self, kind: str, x: int, y: int) -> None:
        fn = self._TOUCH_FN.get(kind)
        if not fn:
            return
        x, y = self._clamp_xy(x, y)
        # 短脚本，降低频繁 runJavaScript 的开销
        self.page().runJavaScript(
            f"var p=window.h5_lgair;if(p&&p.{fn})try{{p.{fn}({x},{y});}}catch(e){{}}"
        )

    def inject_swipe(self, x: int, y: int, y2: int) -> None:
        x, y = self._clamp_xy(x, y)
        _, y2 = self._clamp_xy(x, y2)
        js = (
            "(function(){"
            "var p=window.h5_lgair;"
            "if(!p||typeof p.sendTouchStartEvent!=='function')return;"
            f"var x={x},y={y},y2={y2};"
            "p.sendTouchStartEvent(x,y);"
            "setTimeout(function(){"
            "p.sendTouchMoveEvent(x,y2);"
            "setTimeout(function(){p.sendTouchEndEvent(x,y2);},24);"
            "},20);"
            "})();"
        )
        self.page().runJavaScript(js)

    def _emit_group_touch(self, kind: str, x: int, y: int) -> None:
        """在已节流的 flush 里直接同步，不再 singleShot(0) 往 Qt 队列塞回调。"""
        if not self._group_enabled or self._on_group_touch is None:
            return
        w = max(1, self.width())
        h = max(1, self.height())
        self._on_group_touch(self.slot_index, kind, x / w, y / h)

    def _flush_follow_move(self, force: bool = False) -> None:
        pending = self._pending_follow_move
        if pending is None:
            return
        now = time.monotonic() * 1000.0
        if not force and now - self._last_follow_move_ms < self._FOLLOW_MOVE_MS:
            return
        self._pending_follow_move = None
        self._last_follow_move_ms = now
        self._emit_group_touch("move", pending[0], pending[1])

    def _flush_pending_move(self) -> None:
        pending = self._pending_move
        if pending is None:
            return
        self._pending_move = None
        x, y, sync = pending
        self.inject_touch("move", x, y)
        if sync:
            # 跟随单独 30fps；主控已按 16ms 注入
            self._pending_follow_move = (x, y)
            self._flush_follow_move(force=False)

    def dispatch_touch(self, kind: str, x: int, y: int, *, sync: bool) -> None:
        x, y = self._clamp_xy(x, y)
        if kind == "move":
            # 只保留最新坐标，定时刷出（主控约 60fps）
            self._pending_move = (x, y, sync)
            if not self._move_timer.isActive():
                self._move_timer.start()
            return
        # down/up 先刷出未发送的 move，保证轨迹完整
        if self._pending_move is not None:
            self._move_timer.stop()
            self._flush_pending_move()
        if kind == "up" and sync:
            self._flush_follow_move(force=True)
        self.inject_touch(kind, x, y)
        if sync:
            self._emit_group_touch(kind, x, y)

    def handle_wheel(self, event, *, sync: bool) -> None:
        delta = int(event.angleDelta().y())
        if delta == 0:
            event.accept()
            return
        self._wheel_acc += delta
        if abs(self._wheel_acc) < self._WHEEL_STEP:
            event.accept()
            return
        now = time.monotonic() * 1000.0
        if now - self._last_swipe_ms < self._COOLDOWN_MS:
            self._wheel_acc = 0
            event.accept()
            return
        direction = -1 if self._wheel_acc < 0 else 1
        self._wheel_acc = 0
        self._last_swipe_ms = now
        pos = event.position()
        x = max(1, int(pos.x()))
        y = max(1, int(pos.y()))
        dist = self._SWIPE_PX
        y2 = y - dist if direction < 0 else y + dist
        x, y = self._clamp_xy(x, y)
        _, y2 = self._clamp_xy(x, y2)
        self.inject_swipe(x, y, y2)
        if sync and self._group_enabled and self._on_group_swipe is not None:
            w = max(1, self.width())
            h = max(1, self.height())
            self._on_group_swipe(self.slot_index, x / w, y / h, y2 / h)
        event.accept()

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            return
        self.page().runJavaScript(self._INSTALL_LEAVE_JS)
        self.page().runJavaScript(self._INSTALL_WHEEL_JS)
        flag = "true" if self._group_enabled else "false"
        self.page().runJavaScript(f"window.__mytOverlayCapture={flag};")

    def wheelEvent(self, event) -> None:
        # 非遮罩时由页面 JS 处理；遮罩显示时滚轮在 overlay 上
        event.accept()


class PhoneMirrorFrame(QWidget):
    """投屏宿主：官方 WebRTC webplayer（QWebEngineView），高度按 9:16。"""

    def __init__(self) -> None:
        super().__init__()
        from app.screen_mirror import configure_webrtc_view

        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(90)
        self.setStyleSheet("background-color: #000;")
        self.host = QWidget(self)
        self.host.setStyleSheet("background-color: #000;")
        self.view = MirrorWebView(self.host)
        self.view.setStyleSheet("background-color: #000;")
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        configure_webrtc_view(self.view)
        self.hint = QLabel(_MIRROR_HINTS["idle"], self.host)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet("color: #eee; background: #000; font-size: 13px;")
        self._stack = QStackedLayout(self.host)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.hint)
        self._stack.addWidget(self.view)
        self._stack.setCurrentWidget(self.hint)
        self._applying_height = False

    def set_group_capture(self, enabled: bool) -> None:
        self.view.set_group_capture(bool(enabled))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return phone_height_for_width(width)

    def sizeHint(self) -> QSize:
        width = max(self.width(), 90)
        return QSize(width, phone_height_for_width(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(90, phone_height_for_width(90))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = max(1, self.width())
        height = phone_height_for_width(width)
        if not self._applying_height and abs(self.height() - height) > 1:
            self._applying_height = True
            self.setFixedHeight(height)
            self._applying_height = False
        self.host.setGeometry(0, 0, width, max(1, self.height()))


class SlotPanel(QFrame):
    def __init__(self, index: int, on_action=None, on_selected=None) -> None:
        super().__init__()
        self.index = index
        self._on_action = on_action
        self._on_selected = on_selected
        self.device_id = ""
        self.device_name = ""
        self.device_status = ""
        self.s5_api_port: int | None = None
        self.video_port: int | None = None
        self.control_port: int | None = None
        self.webrtc_tcp: int | None = None
        self.webrtc_udp: int | None = None
        self.index_num: int = 0
        self.api_ready: bool | None = None
        self.s5_ip = ""
        self.phone = ""
        self.sms_url = ""
        self.script_busy = False
        self._heading = EMPTY_SLOT_HEADING
        self._mirror_state = "idle"
        self._is_master = False
        self.setObjectName("SlotPanel")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(_SLOT_FRAME_NORMAL)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.title = QLabel(EMPTY_SLOT_HEADING)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._select = QCheckBox()
        self._select.setChecked(True)
        self._select.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._select.toggled.connect(self._emit_selected)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(4)
        heading.addWidget(self._select, 0)
        heading.addWidget(self.title, 1)
        self._phone = PhoneMirrorFrame()
        self.mirror_host = self._phone.host
        self.mirror_view = self._phone.view
        self._mirror_hint = self._phone.hint
        self._action_buttons: dict[str, QPushButton] = {}
        # 左：脚本按钮独占；右：投屏 + 安卓导航栏 + 删除/重启/投屏
        side = QVBoxLayout()
        side.setSpacing(2)
        side.setContentsMargins(0, 0, 0, 0)
        for action, label in SLOT_ACTIONS:
            button = QPushButton(label)
            button.setEnabled(False)
            button.setFixedWidth(72)
            button.setMinimumHeight(28)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
            button.setStyleSheet(_BTN_SIDE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, a=action: self._emit_action(a))
            self._action_buttons[action] = button
            side.addWidget(button, 1)
        nav_bar = QFrame()
        nav_bar.setObjectName("androidNavBar")
        nav_bar.setStyleSheet(
            "#androidNavBar {"
            "  background: #e8e8e8;"
            "  border: none;"
            "}"
        )
        nav_bar.setFixedHeight(34)
        nav = QHBoxLayout(nav_bar)
        nav.setSpacing(0)
        nav.setContentsMargins(0, 0, 0, 0)
        for action, label in SLOT_NAV_ACTIONS:
            button = QPushButton(label)
            button.setEnabled(False)
            button.setMinimumWidth(48)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            button.setToolTip(
                {"nav_back": "返回", "nav_home": "主页", "nav_tasks": "后台"}.get(action, "")
            )
            button.setStyleSheet(_BTN_NAV)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, a=action: self._emit_action(a))
            self._action_buttons[action] = button
            nav.addWidget(button, 1)
        manage = QHBoxLayout()
        manage.setSpacing(4)
        manage.setContentsMargins(0, 0, 0, 0)
        for action, label in SLOT_MANAGE_ACTIONS:
            button = QPushButton(label)
            button.setEnabled(False)
            button.setMinimumWidth(56)
            button.setMinimumHeight(32)
            if action == "delete":
                button.setStyleSheet(_BTN_DELETE)
            elif action == "restart":
                button.setStyleSheet(_BTN_RESTART)
            else:
                button.setStyleSheet(_BTN_HOVER)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, a=action: self._emit_action(a))
            self._action_buttons[action] = button
            manage.addWidget(button, 1)
        right = QVBoxLayout()
        right.setSpacing(4)
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(manage, 0)
        right.addWidget(self._phone, 1)
        right.addWidget(nav_bar, 0)
        body = QHBoxLayout()
        body.setSpacing(4)
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(side, 0)
        body.addLayout(right, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 2, 6, 2)
        outer.setSpacing(4)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.addLayout(heading, 0)
        outer.addLayout(body, 0)

    def _emit_action(self, action: str) -> None:
        if self._on_action is not None:
            self._on_action(self.index, action)

    def _emit_selected(self, _checked: bool = False) -> None:
        if self._on_selected is not None:
            self._on_selected(self.index)

    def is_selected(self) -> bool:
        return self._select.isChecked()

    def is_master(self) -> bool:
        return self._is_master

    def set_master(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._is_master == enabled:
            return
        self._is_master = enabled
        self.setStyleSheet(_SLOT_FRAME_MASTER if enabled else _SLOT_FRAME_NORMAL)

    def set_group_capture(self, enabled: bool) -> None:
        self._phone.set_group_capture(enabled)

    def set_selected(self, checked: bool) -> bool:
        if self._select.isChecked() == checked:
            return False
        self._select.blockSignals(True)
        self._select.setChecked(checked)
        self._select.blockSignals(False)
        return True

    def set_heading(self, text: str) -> None:
        if text == self._heading:
            return
        self._heading = text
        self.title.setToolTip(text)
        self._apply_heading()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_heading()

    def apply_device(self, payload: dict | None) -> bool:
        if not payload:
            return self.clear()
        device_id = str(payload.get("id") or "")
        device_name = str(payload.get("name") or "")
        device_status = str(payload.get("status") or "")
        s5_api_port = self._as_optional_int(payload.get("s5_api_port"))
        video_port = self._as_optional_int(payload.get("video_port"))
        control_port = self._as_optional_int(payload.get("control_port"))
        webrtc_tcp = self._as_optional_int(payload.get("webrtc_tcp"))
        webrtc_udp = self._as_optional_int(payload.get("webrtc_udp"))
        try:
            index_num = int(payload.get("index_num") or 0)
        except (TypeError, ValueError):
            index_num = 0
        raw_api = payload.get("api_ready")
        # 未探测时保持原值，避免重启等待中被 None 冲掉 False
        if raw_api is None:
            api_ready = self.api_ready
        else:
            api_ready = bool(raw_api)
        if (
            device_id == self.device_id
            and device_name == self.device_name
            and device_status == self.device_status
            and s5_api_port == self.s5_api_port
            and video_port == self.video_port
            and control_port == self.control_port
            and webrtc_tcp == self.webrtc_tcp
            and webrtc_udp == self.webrtc_udp
            and index_num == self.index_num
            and api_ready == self.api_ready
        ):
            return False
        self.device_id = device_id
        self.device_name = device_name
        self.device_status = device_status
        self.s5_api_port = s5_api_port
        self.video_port = video_port
        self.control_port = control_port
        self.webrtc_tcp = webrtc_tcp
        self.webrtc_udp = webrtc_udp
        self.index_num = index_num
        self.api_ready = api_ready
        self.refresh_heading()
        return True

    @staticmethod
    def _as_optional_int(raw) -> int | None:
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def clear(self) -> bool:
        if not self.device_name and not self.device_id:
            self.set_mirror_hint("idle")
            return False
        self.device_id = ""
        self.device_name = ""
        self.device_status = ""
        self.s5_api_port = None
        self.video_port = None
        self.control_port = None
        self.webrtc_tcp = None
        self.webrtc_udp = None
        self.index_num = 0
        self.api_ready = None
        self.s5_ip = ""
        self.script_busy = False
        self.refresh_heading()
        self.set_mirror_hint("idle")
        self.update_action_enabled(False)
        return True

    def mirror_ready(self) -> bool:
        """运行中有 WebRTC 端口，且（若探测过）9082 接口已通。"""
        if not self.can_mirror():
            return False
        if self.api_ready is False:
            return False
        return True

    def can_mirror(self) -> bool:
        return bool(
            self.device_name
            and self.device_status.lower() == RUNNING
            and self.webrtc_tcp
            and self.webrtc_udp
        )

    def set_mirror_hint(self, state: str) -> None:
        if state == self._mirror_state:
            return
        self._mirror_state = state
        text = _MIRROR_HINTS.get(state, state or _MIRROR_HINTS["idle"])
        showing_view = self._phone._stack.currentWidget() is self.mirror_view
        # 已进入 WebView 后，排队/冷却不再切回灰字，避免加载过程闪好几次
        if showing_view and state in {"launching", "cooldown", "timeout"}:
            return
        if state == "embedded" or not text:
            self._phone._stack.setCurrentWidget(self.mirror_view)
            return
        if self._mirror_hint.text() != text:
            self._mirror_hint.setText(text)
        self._phone._stack.setCurrentWidget(self._mirror_hint)

    def update_action_enabled(self, host_online: bool = True) -> None:
        # 单机按钮只看本坑设备状态；批量进度不锁单机
        has_device = bool(host_online and self.device_name)
        running = bool(
            has_device
            and self.device_status.lower() == RUNNING
            and self.s5_api_port
        )
        for action, button in self._action_buttons.items():
            if action in {"delete", "restart"}:
                enabled = has_device
            elif action == "mirror":
                enabled = bool(
                    has_device
                    and self.device_status.lower() == RUNNING
                    and self.webrtc_tcp
                    and self.webrtc_udp
                )
            elif action == "master":
                enabled = has_device
            elif action in {"nav_back", "nav_home", "nav_tasks"}:
                enabled = running
            elif action == "stop":
                enabled = running and self.script_busy
            else:
                enabled = running
            if button.isEnabled() != enabled:
                button.setEnabled(enabled)

    def refresh_heading(self, status: str | None = None) -> None:
        current = self.device_status if status is None else status
        if not self.device_name:
            self.set_heading(EMPTY_SLOT_HEADING)
            return
        self.set_heading(slot_heading(self.device_name, current, self.s5_ip))

    def _apply_heading(self) -> None:
        width = self.title.width() or self.width() or 120
        shown = QFontMetrics(self.title.font()).elidedText(
            self._heading,
            Qt.TextElideMode.ElideMiddle,
            max(40, width - 8),
        )
        if "未设置" in shown:
            html = escape(shown).replace(
                "未设置",
                '<span style="color:#ef6c00">未设置</span>',
            )
            if self.title.textFormat() != Qt.TextFormat.RichText:
                self.title.setTextFormat(Qt.TextFormat.RichText)
            if self.title.text() != html:
                self.title.setText(html)
            return
        if self.title.textFormat() != Qt.TextFormat.PlainText:
            self.title.setTextFormat(Qt.TextFormat.PlainText)
        if self.title.text() != shown:
            self.title.setText(shown)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("魔云腾中控")
        self._db = Database()
        self._events: deque[TaskEvent] = deque()
        self.tasks = TaskService(self._events)
        self._mirrors = ScreenMirrors()
        self._panels = {
            index: SlotPanel(
                index,
                on_action=self._on_slot_action,
                on_selected=self._on_slot_selected,
            )
            for index in range(1, 6)
        }
        self._host_ip = self._db.get_host_ip()
        self._host_state = "unconfigured"
        self._epoch = 0
        self._batch_running = False
        self._batch_count = 0
        self._last_submit = 0.0
        self._sized = False
        self._sdk_warned = False
        self._pending_delete: dict[str, int] = {}
        self._pending_restart: set[int] = set()
        self._restart_api_ready_at: dict[int, float] = {}
        self._restart_mirror_at: dict[int, float] = {}
        self._pending_install: set[int] = set()
        self._pending_proxy: set[int] = set()
        self._resizing_height = False
        self._last_thread_label_at = 0.0
        self._mirror_stop_since: dict[int, float] = {}
        self._mirror_reconnect_at: dict[int, float] = {}
        self._mirror_probe_busy: set[int] = set()
        self._master_slot: int | None = None
        self._build_ui()
        if self._host_ip:
            self._host_state = "checking"
        self._set_host_status()
        self._update_batch_enabled()
        if not sdk_available():
            record_message("官方 WebRTC 投屏未找到，请把 webplayer/（含 play.html）放在程序旁")
        self._timer = QTimer(self)
        self._timer.setInterval(EVENT_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        self._root = QVBoxLayout(central)
        self._root.setContentsMargins(12, 4, 12, 4)
        self._root.setSpacing(0)
        self._root.setAlignment(Qt.AlignmentFlag.AlignTop)
        central.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self._select_row = QHBoxLayout()
        self._thread_label = QLabel("线程: 0")
        self._thread_label.setMinimumWidth(88)
        self._select_all = QPushButton("全选")
        self._invert_select = QPushButton("反选")
        self._group_control = QCheckBox("群控")
        self._select_all.setStyleSheet(_BTN_HOVER)
        self._invert_select.setStyleSheet(_BTN_HOVER)
        self._select_all.setMinimumHeight(34)
        self._invert_select.setMinimumHeight(34)
        self._select_all.setMinimumWidth(72)
        self._invert_select.setMinimumWidth(72)
        self._select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._invert_select.setCursor(Qt.CursorShape.PointingHandCursor)
        self._group_control.setMinimumHeight(34)
        self._group_control.setCursor(Qt.CursorShape.PointingHandCursor)
        self._group_control.setToolTip(
            "开启后：仅操作【主控】坑位时，触控/滚轮/导航会同步到其它已勾选且运行中的云机"
        )
        self._select_all.clicked.connect(self._select_all_slots)
        self._invert_select.clicked.connect(self._invert_slot_selection)
        self._group_control.toggled.connect(self._on_group_control_toggled)
        self._select_row.addWidget(self._thread_label, 0)
        self._select_row.addWidget(self._select_all)
        self._select_row.addWidget(self._invert_select)
        self._select_row.addWidget(self._group_control)
        self._select_row.addStretch(1)
        self._root.addLayout(self._select_row, 0)
        self._refresh_thread_label(force=True)

        self._slot_row = QHBoxLayout()
        self._slot_row.setSpacing(12)
        self._slot_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        for index in range(1, 6):
            panel = self._panels[index]
            view = panel.mirror_view
            view.slot_index = index
            view.set_group_sync(self._sync_group_touch, self._sync_group_swipe)
            self._slot_row.addWidget(panel, 1, Qt.AlignmentFlag.AlignTop)
        self._root.addLayout(self._slot_row, 0)

        self._batch_script_row = QHBoxLayout()
        self._batch_script_buttons: dict[str, QPushButton] = {}
        self._batch_script_row.addStretch(1)
        for action, label in BATCH_SCRIPT_ACTIONS:
            button = QPushButton(label)
            button.setEnabled(False)
            button.setStyleSheet(_BTN_HOVER)
            button.setMinimumHeight(34)
            button.setMinimumWidth(84)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, a=action: self._script_batch(a))
            self._batch_script_buttons[action] = button
            self._batch_script_row.addWidget(button)
        self._batch_script_row.addStretch(1)
        self._root.addLayout(self._batch_script_row)

        self._batch_row = QHBoxLayout()
        self._batch_delete = QPushButton("批量删除")
        self._batch_restart = QPushButton("批量重启")
        self._batch_install = QPushButton("批量安装")
        self._batch_proxy = QPushButton("批量设置代理")
        self._batch_delete.setStyleSheet(_BTN_DELETE)
        self._batch_restart.setStyleSheet(_BTN_RESTART)
        self._batch_install.setStyleSheet(_BTN_HOVER)
        self._batch_proxy.setStyleSheet(_BTN_HOVER)
        for _btn in (
            self._batch_delete,
            self._batch_restart,
            self._batch_install,
            self._batch_proxy,
        ):
            _btn.setMinimumHeight(34)
            _btn.setMinimumWidth(100)
            _btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._batch_delete.clicked.connect(self._delete_batch)
        self._batch_restart.clicked.connect(self._restart_batch)
        self._batch_install.clicked.connect(self._install_batch)
        self._batch_proxy.clicked.connect(self._proxy_batch)
        self._batch_row.addStretch(1)
        self._batch_row.addWidget(self._batch_delete)
        self._batch_row.addWidget(self._batch_restart)
        self._batch_row.addWidget(self._batch_install)
        self._batch_row.addWidget(self._batch_proxy)
        self._batch_row.addStretch(1)
        self._root.addLayout(self._batch_row)

        self._install_row = QHBoxLayout()
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("例如 192.168.0.105")
        self._ip_edit.setFixedWidth(180)
        self._ip_edit.setText(self._host_ip)
        self._ip_edit.returnPressed.connect(self._save_host)
        save_button = QPushButton("保存")
        save_button.setStyleSheet(_BTN_HOVER)
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        save_button.clicked.connect(self._save_host)
        self._status = QLabel()
        self._status.setMinimumWidth(140)
        choose_apk = QPushButton("选择安装包")
        choose_apk.setStyleSheet(_BTN_HOVER)
        choose_apk.setCursor(Qt.CursorShape.PointingHandCursor)
        choose_apk.clicked.connect(self._choose_apk)
        self._apk_path = QLineEdit()
        self._apk_path.setFixedWidth(420)
        self._apk_path.setText(self._db.get_apk_path())
        self._package_name = QLineEdit()
        self._package_name.setFixedWidth(200)
        self._package_name.setText(self._db.get_package_name())
        self._download_port = QLineEdit()
        self._download_port.setFixedWidth(80)
        self._download_port.setText(self._db.get_download_port())
        self._install_row.addStretch(1)
        self._install_row.addWidget(QLabel("主机IP"))
        self._install_row.addWidget(self._ip_edit)
        self._install_row.addWidget(save_button)
        self._install_row.addWidget(self._status)
        self._install_row.addSpacing(16)
        self._install_row.addWidget(choose_apk)
        self._install_row.addWidget(self._apk_path)
        self._install_row.addSpacing(16)
        self._install_row.addWidget(QLabel("包名"))
        self._install_row.addWidget(self._package_name)
        self._install_row.addSpacing(12)
        self._install_row.addWidget(QLabel("端口"))
        self._install_row.addWidget(self._download_port)
        self._install_row.addStretch(1)
        self._root.addLayout(self._install_row)

        self._timing_row = QHBoxLayout()
        self._timing_spins: dict[str, QDoubleSpinBox] = {}
        timing_fields = [
            ("start_delay", "启动前", 0.0, 10.0, 0.1, 2),
            ("sleep_long", "长停留", 0.0, 60.0, 0.1, 2),
            ("sleep_mid", "中停留", 0.0, 60.0, 0.1, 2),
            ("sleep_short", "短停留", 0.0, 30.0, 0.1, 2),
            ("find_timeout", "找控件", 1.0, 120.0, 1.0, 1),
            ("page_timeout", "页面等", 1.0, 180.0, 1.0, 1),
            ("long_timeout", "长等", 1.0, 180.0, 1.0, 1),
            ("poll_interval", "轮询", 0.05, 5.0, 0.05, 2),
        ]
        saved_timings = self._db.get_script_timings()
        self._timing_row.addStretch(1)
        self._timing_row.addWidget(QLabel("脚本时间(秒)"))
        for key, label, lo, hi, step, decimals in timing_fields:
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setFixedWidth(72)
            spin.setValue(float(getattr(saved_timings, key)))
            spin.valueChanged.connect(self._on_timing_changed)
            self._timing_spins[key] = spin
            self._timing_row.addWidget(QLabel(label))
            self._timing_row.addWidget(spin)
        self._timing_row.addStretch(1)
        self._root.addLayout(self._timing_row)
        self.tasks.set_script_timings(saved_timings)

        saved_script = ScriptContent.from_dict(self._db.get_script_content())
        self._script_content_row = QHBoxLayout()
        self._script_content_row.setSpacing(8)
        self._comment_content = QPlainTextEdit()
        self._comment_content.setPlaceholderText("评论话术：用 | 分成至少 3 段")
        self._comment_content.setFixedHeight(72)
        self._comment_content.setFixedWidth(220)
        self._comment_content.setPlainText(saved_script.comment_content)
        self._join_title = QLineEdit()
        self._join_title.setPlaceholderText("进群用户ID")
        self._join_title.setFixedWidth(260)
        self._join_title.setText(saved_script.join_title)
        self._edit_text = QPlainTextEdit()
        self._edit_text.setPlaceholderText("简介文案：| 分段，可含表情与空格")
        self._edit_text.setFixedHeight(72)
        self._edit_text.setFixedWidth(220)
        self._edit_text.setPlainText(saved_script.edit_text)
        self._edit_tags = QLineEdit()
        self._edit_tags.setPlaceholderText("收藏用户ID")
        self._edit_tags.setFixedWidth(260)
        self._edit_tags.setText(saved_script.edit_tags)
        self._script_content_row.addStretch(1)
        self._script_content_row.addWidget(QLabel("评论话术"))
        self._script_content_row.addWidget(self._comment_content)
        self._script_content_row.addWidget(QLabel("进群用户ID"))
        self._script_content_row.addWidget(self._join_title)
        self._script_content_row.addWidget(QLabel("简介文案"))
        self._script_content_row.addWidget(self._edit_text)
        self._script_content_row.addWidget(QLabel("收藏用户ID"))
        self._script_content_row.addWidget(self._edit_tags)
        self._script_content_row.addStretch(1)
        self._root.addLayout(self._script_content_row)
        self.tasks.set_script_content(saved_script)

        self._proxy_pool_row = QHBoxLayout()
        self._s5_pool = QPlainTextEdit()
        self._s5_pool.setPlaceholderText("每行一条：IP:端口:账号:密码")
        self._s5_pool.setFixedHeight(90)
        self._s5_pool.setFixedWidth(420)
        self._s5_pool.setPlainText(self._db.get_s5_ip_pool())
        self._proxy_pool_row.addStretch(1)
        self._proxy_pool_row.addWidget(QLabel("S5ip池"))
        self._proxy_pool_row.addWidget(self._s5_pool)
        self._proxy_pool_row.addStretch(1)
        self._root.addLayout(self._proxy_pool_row)

        for index, panel in self._panels.items():
            panel.s5_ip = self._db.get_slot_s5_ip(index)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._sized:
            self._sized = True
            self._apply_initial_size()
            QTimer.singleShot(0, self._fit_height_to_content)
            QTimer.singleShot(50, self._fit_height_to_content)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._sized or self._resizing_height:
            return
        # 宽度变了 → 投屏高度变 → 整窗高度贴内容
        QTimer.singleShot(0, self._fit_height_to_content)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # 普通应用行为：被点到/切回来时提到前面（不用 TOPMOST/NOTOPMOST）
        if event.type() == QEvent.Type.WindowActivate and self.isActiveWindow():
            self._bring_forward_normal()

    def _bring_forward_normal(self) -> None:
        try:
            self.raise_()
            self.activateWindow()
            if sys.platform != "win32":
                return
            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            user32.BringWindowToTop(hwnd)
            # HWND_TOP=0，只调本窗口，不碰 TOPMOST 链
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        except Exception:
            record_exception("窗口前置")

    def closeEvent(self, event) -> None:
        self._remember_host_ip()
        self._remember_script_content()
        self._remember_s5_pool()
        self._remember_install_settings()
        self._remember_script_timings()
        self._timer.stop()
        # 先断 WebRTC 再退，避免主机端投屏编码一直占满 CPU
        self._mirrors.release_for_exit()
        self.tasks.shutdown(fast=True)
        event.accept()
        os._exit(0)

    def _collect_script_timings(self) -> ScriptTimings:
        values = {key: spin.value() for key, spin in self._timing_spins.items()}
        return ScriptTimings.from_dict(values)

    def _on_timing_changed(self, _value: float = 0.0) -> None:
        timings = self._collect_script_timings()
        self.tasks.set_script_timings(timings)
        try:
            self._db.set_script_timings(timings)
        except Exception:
            record_exception("保存脚本时间")

    def _remember_script_timings(self) -> None:
        try:
            self._db.set_script_timings(self._collect_script_timings())
        except Exception:
            record_exception("保存脚本时间")

    def _remember_host_ip(self) -> None:
        parsed = parse_ipv4(self._ip_edit.text())
        if not parsed:
            return
        try:
            self._db.set_host_ip(parsed)
        except Exception:
            record_exception("保存主机IP")

    def _collect_script_content(self) -> ScriptContent:
        # 不 strip：保留表情与空格
        return ScriptContent(
            comment_content=self._comment_content.toPlainText(),
            join_title=self._join_title.text(),
            edit_text=self._edit_text.toPlainText(),
            edit_tags=self._edit_tags.text(),
        )

    def _remember_script_content(self) -> None:
        try:
            content = self._collect_script_content()
            self._db.set_script_content(
                comment_content=content.comment_content,
                join_title=content.join_title,
                edit_text=content.edit_text,
                edit_tags=content.edit_tags,
            )
            self.tasks.set_script_content(content)
        except Exception:
            record_exception("保存脚本文案")

    def _remember_s5_pool(self) -> None:
        try:
            self._db.set_s5_ip_pool(self._s5_pool.toPlainText())
        except Exception:
            record_exception("保存S5ip池")

    def _remember_install_settings(self) -> None:
        try:
            self._db.set_apk_path(self._apk_path.text().strip())
            self._db.set_package_name(self._package_name.text().strip())
            self._db.set_download_port(self._download_port.text().strip())
        except Exception:
            record_exception("保存安装设置")

    def _apply_initial_size(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1400, 900)
            return
        area = screen.availableGeometry()
        # 默认宽度：显示器可用宽度略小一点
        width = max(960, int(area.width()) - 40)
        height = self._height_for_window_width(width)
        # 若内容高度超出屏幕，略收窄宽度，保持 9:16 且整窗仍能放下
        max_h = max(720, int(area.height()) - 40)
        while width > 960 and height > max_h:
            width -= 20
            height = self._height_for_window_width(width)
        self.resize(int(width), int(min(height, max_h)))
        self._fit_height_to_content()
        frame = self.frameGeometry()
        frame.moveCenter(area.center())
        self.move(frame.topLeft())

    def _chrome_below_phones(self) -> int:
        title_h = max(self._panels[1].title.sizeHint().height(), 22)
        manage_h = 36
        nav_h = 40
        slot_pad = 12
        rest = 0
        for layout in (
            self._select_row,
            self._batch_script_row,
            self._batch_row,
            self._install_row,
            self._timing_row,
            self._script_content_row,
            self._proxy_pool_row,
        ):
            rest += max(layout.sizeHint().height(), 28)
        return int(title_h + manage_h + nav_h + slot_pad + rest + 24)

    def _width_for_max_height(self, max_height: int) -> int:
        """由最大窗口高度反推宽度，保证 9:16 五等分后总高不爆。"""
        chrome = self._chrome_below_phones()
        phone_h = max(160, int(max_height) - chrome)
        phone_w = max(90, int(phone_h * PHONE_ASPECT_W / PHONE_ASPECT_H))
        slot_w = phone_w + 12 + 72 + 2
        return int(slot_w * 5 + 12 * 4 + 24)

    def _height_for_window_width(self, window_width: int) -> int:
        """窗口高度 = 五等分投屏(宽×16/9) + 标题/按钮/下方工具行。"""
        slot_gaps = 12 * 4
        slot_width = max(1, (int(window_width) - 24 - slot_gaps) / 5)
        phone_width = max(1, slot_width - 12 - 72 - 2)
        phone_height = phone_height_for_width(phone_width)
        return int(phone_height + self._chrome_below_phones())

    def _fit_height_to_content(self) -> None:
        """整窗高度锁成内容高度，底部不留空。"""
        if not self._sized or self._resizing_height:
            return
        for panel in self._panels.values():
            phone = panel._phone
            width = max(1, phone.width())
            height = phone_height_for_width(width)
            if abs(phone.height() - height) > 1:
                phone.setFixedHeight(height)
        target = int(self._height_for_window_width(self.width()))
        if self.minimumHeight() == target and self.maximumHeight() == target:
            if abs(self.height() - target) <= 2:
                return
        self._resizing_height = True
        try:
            # 只锁高度：宽仍可拖，高始终贴内容
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.resize(self.width(), target)
            self.setFixedHeight(target)
        finally:
            self._resizing_height = False

    def _on_tick(self) -> None:
        try:
            self._drain()
            self._poll_mirrors()
            self._refresh_thread_label()
            self._maybe_refresh()
        except Exception:
            record_exception("界面刷新")

    def _refresh_thread_label(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._last_thread_label_at and now - self._last_thread_label_at < THREAD_LABEL_INTERVAL_S:
            return
        self._last_thread_label_at = now
        total = process_thread_count()
        pool = self.tasks.pool_active_count()
        text = f"线程: {total}"
        if pool:
            text = f"线程: {total}（忙 {pool}）"
        if self._thread_label.text() != text:
            self._thread_label.setText(text)

    def _restart_wait_mirror(self, slot: int) -> bool:
        """重启中的坑：必须运行中且 9082 接口已通才允许投屏。"""
        return slot in self._pending_restart

    def _clear_restart_slot(self, slot: int) -> None:
        self._pending_restart.discard(slot)
        self._restart_api_ready_at.pop(slot, None)
        self._restart_mirror_at.pop(slot, None)

    def _restart_mirror_due(self, slot: int) -> bool:
        """重启坑：9082 通后延迟，再周期性强制重载，避免早投黑屏后不再试。"""
        panel = self._panels.get(slot)
        if panel is None or panel.api_ready is not True or not panel.can_mirror():
            return False
        now = time.monotonic()
        ready_at = self._restart_api_ready_at.get(slot)
        if ready_at is None:
            self._restart_api_ready_at[slot] = now
            return False
        if now - ready_at >= RESTART_MIRROR_GIVE_UP_S:
            # 长时间仍投不上则放开 pending，避免批量按钮一直锁死
            self._clear_restart_slot(slot)
            return False
        if now - ready_at < RESTART_MIRROR_DELAY_S:
            return False
        last = self._restart_mirror_at.get(slot, 0.0)
        return (now - last) >= RESTART_MIRROR_RETRY_S or last <= 0

    def _slot_allowed_to_mirror(self, slot: int) -> bool:
        panel = self._panels.get(slot)
        if panel is None or not panel.can_mirror():
            return False
        if self._mirrors.is_user_skipped(slot, panel.device_id):
            return False
        # 自动重连也走 mirror_ready：api_ready=False 时不连，避免流未就绪弹连接超时
        if not panel.mirror_ready():
            return False
        # 重启后：等 9082 通 + 延迟后再投
        if self._restart_wait_mirror(slot):
            return self._restart_mirror_due(slot)
        return True

    def _ensure_slot_mirror(
        self, slot: int, panel, *, force: bool = False, reload: bool = False
    ) -> str:
        return self._mirrors.ensure(
            slot,
            panel.device_id,
            panel.device_name,
            self._host_ip,
            int(panel.webrtc_tcp),
            int(panel.webrtc_udp),
            panel.mirror_view,
            force=force,
            reload=reload,
        )

    def _on_mirror_probe(self, slot: int, status: str) -> None:
        """播放器停在等待页时：有运行中云机才重载，否则停掉。"""
        self._mirror_probe_busy.discard(slot)
        panel = self._panels.get(slot)
        if panel is None or status != "waiting":
            return
        if not self._slot_allowed_to_mirror(slot):
            self._mirrors.stop(slot)
            panel.set_mirror_hint("idle")
            return
        now = time.monotonic()
        last = self._mirror_reconnect_at.get(slot, 0.0)
        if now - last < MIRROR_RECONNECT_MIN_S:
            return
        self._mirror_reconnect_at[slot] = now
        state = self._ensure_slot_mirror(slot, panel, reload=True)
        panel.set_mirror_hint(state)

    def _poll_mirrors(self) -> None:
        for slot, state in self._mirrors.poll().items():
            panel = self._panels.get(slot)
            if panel is not None:
                panel.set_mirror_hint(state)
        # 掉线后自动重连（仅运行中且允许投屏的坑；空坑/未就绪不连）
        if self._host_state != "online" or not self._host_ip:
            return
        self._mirrors.reset_launch_budget()
        now = time.monotonic()
        for slot, panel in self._panels.items():
            if self._restart_wait_mirror(slot):
                if panel.api_ready is True and panel.can_mirror():
                    self._restart_api_ready_at.setdefault(slot, now)
                if not self._slot_allowed_to_mirror(slot):
                    if panel._mirror_state not in {"idle", "cancelled", "cooldown"}:
                        self._mirrors.stop(slot)
                        panel.set_mirror_hint("idle")
                    elif panel.api_ready is True and panel.can_mirror():
                        if panel._mirror_state != "cooldown":
                            panel.set_mirror_hint("cooldown")
                    continue
                # 重启窗口：按间隔强制重载，直到确认投上并移出 pending
                self._restart_mirror_at[slot] = now
                state = self._ensure_slot_mirror(slot, panel, force=True, reload=True)
                panel.set_mirror_hint(state)
                if state == "embedded":
                    self._clear_restart_slot(slot)
                continue
            if not self._slot_allowed_to_mirror(slot):
                # 坑位无运行中云机：有投屏会话就停，避免空等/乱重连
                if self._mirrors.has_session(slot) or panel._mirror_state not in {
                    "idle",
                    "cancelled",
                    "cooldown",
                }:
                    if not panel.can_mirror():
                        since = self._mirror_stop_since.get(slot)
                        if since is None:
                            self._mirror_stop_since[slot] = now
                        elif now - since >= MIRROR_STOP_DEBOUNCE_S:
                            self._mirrors.stop(slot)
                            self._mirror_stop_since.pop(slot, None)
                            panel.set_mirror_hint("idle")
                    else:
                        self._mirrors.stop(slot)
                        panel.set_mirror_hint("idle")
                continue
            self._mirror_stop_since.pop(slot, None)
            # 已嵌入：探测是否卡在「轻触重试」等待页，是则仅运行中坑重载
            if panel._mirror_state == "embedded" and self._mirrors.has_session(
                slot, panel.device_id
            ):
                if slot not in self._mirror_probe_busy:
                    self._mirror_probe_busy.add(slot)
                    self._mirrors.probe_waiting(slot, self._on_mirror_probe)
                continue
            if self._mirrors.in_cooldown(slot):
                if panel._mirror_state != "cooldown":
                    panel.set_mirror_hint("cooldown")
                continue
            state = self._ensure_slot_mirror(slot, panel)
            panel.set_mirror_hint(state)

    def _sync_mirrors(self, only_slots: set[int] | None = None) -> None:
        if self._host_state != "online" or not self._host_ip:
            self._mirrors.stop_all()
            self._mirror_stop_since.clear()
            for panel in self._panels.values():
                panel.set_mirror_hint("idle")
            return
        self._mirrors.reset_launch_budget()
        now = time.monotonic()
        targets = only_slots if only_slots is not None else self._panels.keys()
        for slot in targets:
            panel = self._panels.get(slot)
            if panel is None:
                continue
            if self._restart_wait_mirror(slot):
                if panel.api_ready is True and panel.can_mirror():
                    self._restart_api_ready_at.setdefault(slot, now)
                if not self._restart_mirror_due(slot):
                    # 重启未到投屏窗口：保持空闲，避免 9082 刚通就早投
                    if panel._mirror_state not in {"idle", "cancelled", "cooldown"}:
                        self._mirrors.stop(slot)
                        panel.set_mirror_hint("idle")
                    continue
                self._mirror_stop_since.pop(slot, None)
                self._restart_mirror_at[slot] = now
                state = self._ensure_slot_mirror(slot, panel, force=True, reload=True)
                if state == "missing_sdk" and not self._sdk_warned:
                    self._sdk_warned = True
                    QMessageBox.warning(
                        self,
                        "投屏",
                        "未找到官方 WebRTC 播放器。请把 webplayer\\ 目录（含 play.html）放在程序同目录。",
                    )
                panel.set_mirror_hint(state)
                if state == "embedded":
                    self._clear_restart_slot(slot)
                continue
            if not panel.can_mirror():
                # 列表刷新瞬间端口/状态抖动：防抖后再停
                since = self._mirror_stop_since.get(slot)
                if since is None:
                    self._mirror_stop_since[slot] = now
                elif now - since >= MIRROR_STOP_DEBOUNCE_S:
                    self._mirrors.stop(slot)
                    self._mirror_stop_since.pop(slot, None)
                    if not panel.device_name or panel.device_status.lower() != RUNNING:
                        self._mirrors.forget_skip(slot)
                    if not panel.device_name:
                        panel.set_mirror_hint("idle")
                    elif panel.device_status.lower() == RUNNING and not (
                        panel.webrtc_tcp and panel.webrtc_udp
                    ):
                        panel.set_mirror_hint("no_ports")
                continue
            self._mirror_stop_since.pop(slot, None)
            state = self._ensure_slot_mirror(slot, panel)
            if state == "missing_sdk" and not self._sdk_warned:
                self._sdk_warned = True
                QMessageBox.warning(
                    self,
                    "投屏",
                    "未找到官方 WebRTC 播放器。请把 webplayer\\ 目录（含 play.html）放在程序同目录。",
                )
            panel.set_mirror_hint(state)

    def _drain(self) -> None:
        events: list[TaskEvent] = []
        for _ in range(400):
            try:
                events.append(self._events.popleft())
            except IndexError:
                break
        merged: list[TaskEvent] = []
        index_by_kind: dict[str, int] = {}
        for event in events:
            if event.kind in {"devices", "host"} and event.kind in index_by_kind:
                merged[index_by_kind[event.kind]] = event
                continue
            index_by_kind[event.kind] = len(merged)
            merged.append(event)
        for event in merged:
            # 脚本结果不按 epoch 丢弃，否则失败时用户看不到弹窗
            if event.kind != "script_done" and event.data.get("epoch") != self._epoch:
                continue
            if event.kind == "host":
                self._apply_host_down()
            elif event.kind == "devices":
                self._apply_devices(event.data.get("slots") or {})
            elif event.kind == "error":
                record_message(str(event.data.get("message") or "获取云机失败"))
            elif event.kind == "batch_done":
                self._apply_batch_done(event.data)
            elif event.kind == "script_done":
                self._apply_script_done(event.data)

    def _on_slot_action(self, slot: int, action: str) -> None:
        panel = self._panels.get(slot)
        if panel is None or not self._host_ip:
            return
        if action == "stop":
            self.tasks.stop_slot_script(slot)
            return
        if action in {"delete", "restart"}:
            self._slot_manage(slot, action)
            return
        if action == "mirror":
            self._remirror_slot(slot)
            return
        if action == "master":
            self._set_master_slot(slot)
            return
        if action in {"nav_back", "nav_home", "nav_tasks"}:
            self._send_slot_nav(slot, action)
            return
        self._start_slot_script(slot, action)

    def _set_master_slot(self, slot: int) -> None:
        """单选主控：外框加粗蓝边；群控只跟随该坑操作。"""
        panel = self._panels.get(slot)
        if panel is None or not panel.device_name:
            return
        if self._master_slot == slot:
            return
        prev = self._master_slot
        self._master_slot = slot
        if prev is not None and prev in self._panels:
            self._panels[prev].set_master(False)
        panel.set_master(True)
        self._refresh_group_capture()

    def _clear_master_if_slot(self, slot: int) -> None:
        if self._master_slot != slot:
            return
        self._master_slot = None
        panel = self._panels.get(slot)
        if panel is not None:
            panel.set_master(False)
        self._refresh_group_capture()

    def _send_slot_nav(self, slot: int, action: str) -> None:
        """对齐官方：返回 / 主页 / 后台。群控+主控时同步到其它勾选坑。"""
        panel = self._panels.get(slot)
        if panel is None or not self._host_ip or self._host_state != "online":
            return
        if not panel.device_name or panel.device_status.lower() != RUNNING:
            return
        command = {
            "nav_back": "back",
            "nav_home": "home",
            "nav_tasks": "tasks",
        }.get(action, "")
        if not command:
            return
        # 导航走官方 /rpa/key，只需云机名
        self.tasks.submit_slot_nav(
            slot,
            command,
            self._host_ip,
            int(panel.s5_api_port or 0),
            panel.device_name,
        )
        if not self._group_control.isChecked() or self._master_slot != slot:
            return
        for other, other_panel in self._panels.items():
            if other == slot:
                continue
            if not other_panel.is_selected():
                continue
            if not other_panel.device_name or other_panel.device_status.lower() != RUNNING:
                continue
            self.tasks.submit_slot_nav(
                other,
                command,
                self._host_ip,
                int(other_panel.s5_api_port or 0),
                other_panel.device_name,
            )

    def _on_group_control_toggled(self, _checked: bool) -> None:
        self._refresh_group_capture()

    def _refresh_group_capture(self) -> None:
        """仅主控+群控时显示遮罩并同步；关闭后 hide 遮罩，本机恢复原生拖动。"""
        group_on = self._group_control.isChecked()
        master = self._master_slot
        for slot, panel in self._panels.items():
            panel.set_group_capture(group_on and slot == master)

    def _group_follower_views(self, source_slot: int):
        """群控跟随：其它已勾选且运行中的坑位（主控除外）。"""
        for slot, panel in self._panels.items():
            if slot == source_slot:
                continue
            if not panel.is_selected():
                continue
            if not panel.device_name or panel.device_status.lower() != RUNNING:
                continue
            yield panel.mirror_view

    def _sync_group_touch(self, source_slot: int, kind: str, nx: float, ny: float) -> None:
        if not self._group_control.isChecked() or self._master_slot != source_slot:
            return
        for view in self._group_follower_views(source_slot):
            w = max(1, view.width())
            h = max(1, view.height())
            view.inject_touch(kind, int(nx * w), int(ny * h))

    def _sync_group_swipe(
        self, source_slot: int, nx: float, ny: float, ny2: float
    ) -> None:
        if not self._group_control.isChecked() or self._master_slot != source_slot:
            return
        for view in self._group_follower_views(source_slot):
            w = max(1, view.width())
            h = max(1, view.height())
            view.inject_swipe(int(nx * w), int(ny * h), int(ny2 * h))

    def _remirror_slot(self, slot: int) -> None:
        panel = self._panels.get(slot)
        if panel is None or not self._host_ip or self._host_state != "online":
            return
        if not panel.can_mirror():
            QMessageBox.warning(self, "投屏", f"坑位{slot} 未运行或缺少 WebRTC 端口。")
            return
        self._mirrors.stop(slot)
        self._mirrors.forget_skip(slot)
        state = self._mirrors.ensure(
            slot,
            panel.device_id,
            panel.device_name,
            self._host_ip,
            int(panel.webrtc_tcp),
            int(panel.webrtc_udp),
            panel.mirror_view,
            force=True,
        )
        panel.set_mirror_hint(state)

    def _start_slot_script(self, slot: int, action: str) -> bool:
        panel = self._panels.get(slot)
        if panel is None or not self._host_ip:
            return False
        if not panel.device_name or panel.device_status.lower() != RUNNING:
            return False
        # 脚本走官方 /rpa/*，只需云机名
        # 跑脚本前同步界面最新文案到 TaskService（不 strip）
        self.tasks.set_script_content(self._collect_script_content())
        if not self.tasks.submit_slot_script(
            slot,
            action,
            self._host_ip,
            int(panel.s5_api_port or 0),
            panel.device_name,
            self._epoch,
        ):
            return False
        panel.script_busy = True
        self._update_slot_actions({slot})
        panel.set_heading(slot_heading(panel.device_name, SCRIPTING, panel.s5_ip))
        return True

    def _on_slot_selected(self, _slot: int) -> None:
        # 勾选只影响批量按钮，不重刷五个坑的脚本按钮
        self._update_batch_buttons()

    def _select_all_slots(self) -> None:
        changed = False
        for panel in self._panels.values():
            if panel.set_selected(True):
                changed = True
        if changed:
            self._update_batch_buttons()

    def _invert_slot_selection(self) -> None:
        for panel in self._panels.values():
            panel.set_selected(not panel.is_selected())
        self._update_batch_buttons()

    def _script_batch(self, action: str) -> None:
        """遍历当前勾选且可跑脚本的坑位；失败仍按单坑弹窗。"""
        if self._host_state != "online" or not self._host_ip:
            return
        started = 0
        for slot in range(1, 6):
            panel = self._panels[slot]
            if not panel.is_selected():
                continue
            if not panel.device_name or panel.device_status.lower() != RUNNING:
                continue
            if self._start_slot_script(slot, action):
                started += 1

    def _slot_manage(self, slot: int, action: str) -> None:
        panel = self._panels.get(slot)
        if panel is None or not panel.device_name or self._host_state != "online":
            return
        name = panel.device_name
        if action == "delete":
            answer = QMessageBox.question(
                self,
                "删除云机",
                f"删除坑位{slot} 的云机？\n{name}",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.tasks.stop_slot_script(slot)
        self._start_batch(action, [name])

    def _apply_script_done(self, data: dict) -> None:
        slot = int(data.get("slot") or 0)
        panel = self._panels.get(slot)
        if panel is None:
            return
        panel.script_busy = self.tasks.slot_script_busy(slot)
        phone = str(data.get("phone") or "").strip()
        sms_url = str(data.get("sms_url") or "").strip()
        if phone:
            panel.phone = phone
        if sms_url:
            panel.sms_url = sms_url
        ok = bool(data.get("ok"))
        message = str(data.get("message") or "").strip()
        action = str(data.get("action") or "")
        label = SCRIPT_HANDLERS.get(action, action)
        if panel.device_name and not panel.script_busy:
            panel.refresh_heading()
        self._update_slot_actions({slot})
        if ok:
            return
        # 用户点停止也弹一下，避免误以为卡住没反应
        tip = message or f"{label}失败（无详细错误）"
        QMessageBox.warning(self, f"坑位{slot} {label}", tip)

    def _restart_check_names(self) -> set[str]:
        return {
            self._panels[slot].device_name
            for slot in self._pending_restart
            if self._panels[slot].device_name
        }

    def _maybe_refresh(self) -> None:
        if not self._host_ip:
            return
        # 等重启就绪时仍要刷列表探 9082；其它批量进行中不抢刷新
        if self._batch_running and not self._pending_restart:
            return
        now = time.monotonic()
        if self._last_submit and now - self._last_submit < REFRESH_INTERVAL_S:
            return
        if self.tasks.submit_refresh(self._host_ip, self._restart_check_names(), self._epoch):
            self._last_submit = now

    def _save_host(self) -> None:
        parsed = parse_ipv4(self._ip_edit.text())
        if not parsed:
            QMessageBox.warning(self, "主机IP", "请输入有效的 IPv4 地址。端口固定为 8000。")
            self._ip_edit.setText(self._host_ip)
            return
        self._host_ip = parsed
        self._db.set_host_ip(parsed)
        self._ip_edit.setText(parsed)
        self._epoch += 1
        self._batch_count = 0
        self._batch_running = False
        self._clear_panels()
        self._host_state = "checking"
        self._set_host_status()
        self._update_batch_enabled()
        self._last_submit = 0.0

    def _choose_apk(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "选择安装包",
            "",
            "APK (*.apk);;所有文件 (*.*)",
        )
        if path:
            self._apk_path.setText(path)
            try:
                self._db.set_apk_path(path)
            except Exception:
                record_exception("保存安装包路径")

    def _delete_batch(self) -> None:
        names = self._current_names()
        if not names or self._host_state != "online":
            return
        answer = QMessageBox.question(
            self,
            "批量删除",
            "删除当前坑位中的这些云机？\n" + "\n".join(names),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_batch("delete", names)

    def _restart_batch(self) -> None:
        names = self._current_names()
        if not names or self._host_state != "online":
            return
        self._start_batch("restart", names)

    def _install_batch(self) -> None:
        names = self._current_names()
        if not names or self._host_state != "online":
            return
        apk_path = Path(self._apk_path.text().strip())
        package_name = self._package_name.text().strip()
        port = self._parsed_download_port()
        if not apk_path.is_file():
            QMessageBox.warning(self, "批量安装", "请先选择安装包。")
            return
        if not package_name:
            QMessageBox.warning(self, "批量安装", "请填写包名。")
            return
        if port is None:
            QMessageBox.warning(self, "批量安装", "端口需要是 1 到 65535 的数字。")
            return
        epoch = self._epoch + 1
        if not self.tasks.submit_install(
            self._host_ip,
            names,
            str(apk_path),
            package_name,
            port,
            epoch,
        ):
            return
        self._epoch = epoch
        self._begin_ui_batch()
        self._pending_install = {
            panel.index
            for panel in self._panels.values()
            if panel.device_name in names
        }
        self._update_batch_enabled()
        self._mark_phase(names, INSTALLING)

    def _proxy_batch(self) -> None:
        if self._host_state != "online" or not self._host_ip:
            return
        running = self._running_slots()
        if not running:
            QMessageBox.warning(self, "批量设置代理", "当前没有运行中的云机。")
            return
        pool_text = self._s5_pool.toPlainText()
        available = parse_s5_pool(pool_text)
        if len(available) < len(running):
            QMessageBox.warning(
                self,
                "批量设置代理",
                f"运行中云机 {len(running)} 台，S5ip池有效行不足（当前 {len(available)} 条）。",
            )
            return
        proxies = take_unique_proxies(pool_text, len(running))
        assignments = [
            (slot, self._panels[slot].device_name, self._panels[slot].s5_api_port, proxy)
            for (slot, _name), proxy in zip(running, proxies)
        ]
        missing = [f"实例{slot}" for slot, _name, api_port, _proxy in assignments if not api_port]
        if missing:
            QMessageBox.warning(
                self,
                "批量设置代理",
                "以下云机缺少 9082 映射端口，无法设置：" + "、".join(missing),
            )
            return
        epoch = self._epoch + 1
        if not self.tasks.submit_set_proxy(self._host_ip, assignments, epoch):
            return
        self._epoch = epoch
        self._begin_ui_batch()
        self._pending_proxy = {slot for slot, _name, _port, _proxy in assignments}
        self._update_batch_enabled()
        self._mark_phase([name for _slot, name, _port, _proxy in assignments], SETTING_PROXY)

    def _running_slots(self) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        for index in range(1, 6):
            panel = self._panels[index]
            if (
                panel.is_selected()
                and panel.device_name
                and panel.device_status.lower() == RUNNING
            ):
                result.append((index, panel.device_name))
        return result

    def _parsed_download_port(self) -> int | None:
        text = self._download_port.text().strip()
        if not text.isdigit():
            return None
        port = int(text)
        if port < 1 or port > 65535:
            return None
        return port

    def _start_batch(self, action: str, names: list[str]) -> None:
        epoch = self._epoch + 1
        if not self.tasks.submit_batch(self._host_ip, action, names, epoch):
            return
        self._epoch = epoch
        self._begin_ui_batch()
        # 先记下忙坑再刷按钮，避免短暂锁死全部坑位
        if action == "delete":
            self._pending_delete = {
                panel.device_name: index
                for index, panel in self._panels.items()
                if panel.device_name in names
            }
        if action == "restart":
            self._pending_restart = {
                panel.index
                for panel in self._panels.values()
                if panel.device_name in names
            }
            self._reset_mirrors_for_names(names)
        self._update_batch_enabled()
        phase = DELETING if action == "delete" else RESTARTING
        self._mark_phase(names, phase)

    def _begin_ui_batch(self) -> None:
        self._batch_count += 1
        self._batch_running = True

    def _end_ui_batch(self) -> None:
        self._batch_count = max(0, self._batch_count - 1)
        self._batch_running = self._batch_count > 0

    def _reset_mirrors_for_names(self, names: list[str]) -> None:
        selected = set(names)
        for panel in self._panels.values():
            if panel.device_name not in selected:
                continue
            self._mirrors.stop(panel.index)
            self._mirrors.forget_skip(panel.index)
            # 清掉旧的 api_ready，避免重启瞬间用过期「已通」提前投屏
            panel.api_ready = False
            self._restart_api_ready_at.pop(panel.index, None)
            self._restart_mirror_at.pop(panel.index, None)
            self._mirror_stop_since.pop(panel.index, None)
            panel.set_mirror_hint("idle")

    def _mark_phase(self, names: list[str], phase: str) -> None:
        selected = set(names)
        for panel in self._panels.values():
            if panel.device_name in selected:
                panel.set_heading(slot_heading(panel.device_name, phase, panel.s5_ip))

    def _apply_batch_done(self, data: dict) -> None:
        self._end_ui_batch()
        failed = data.get("failed") or []
        action_key = str(data.get("action") or "")
        labels = {"delete": "删除", "restart": "重启", "install": "安装", "proxy": "设置代理"}
        action = labels.get(action_key, "操作")
        if action_key == "proxy":
            self._apply_proxy_done(data)
            self._pending_proxy.clear()
        elif action_key == "delete":
            self._clear_s5_for_deleted(failed)
        elif action_key == "install":
            self._pending_install.clear()
        if action_key == "restart" and failed:
            failed_names = {str(item).split(":", 1)[0].strip() for item in failed}
            self._pending_restart = {
                slot
                for slot in self._pending_restart
                if self._panels[slot].device_name not in failed_names
            }
        if failed:
            QMessageBox.warning(self, f"批量{action}", "\n".join(str(item) for item in failed))
        self._update_batch_enabled()
        self._last_submit = 0.0
        # 删除/重启后立刻拉列表；重启还会探 9082，通了再投屏
        if action_key in {"delete", "restart"} and self._host_ip:
            check = self._restart_check_names() if action_key == "restart" else set()
            if self.tasks.submit_refresh(self._host_ip, check, self._epoch):
                self._last_submit = time.monotonic()

    def _apply_proxy_done(self, data: dict) -> None:
        succeeded = data.get("succeeded") or []
        for item in succeeded:
            if not isinstance(item, dict):
                continue
            slot = int(item.get("slot") or 0)
            s5_ip = str(item.get("s5_ip") or "").strip()
            if slot in self._panels and s5_ip:
                panel = self._panels[slot]
                panel.s5_ip = s5_ip
                self._db.set_slot_s5_ip(slot, s5_ip)
                panel.refresh_heading()
        # S5ip池保留不删；仅关闭/保存时写回数据库。
        self._remember_s5_pool()

    def _clear_s5_for_deleted(self, failed: list) -> None:
        failed_names = {str(item).split(":", 1)[0].strip() for item in failed}
        for name, slot in list(self._pending_delete.items()):
            if name in failed_names:
                continue
            panel = self._panels[slot]
            self._clear_slot_s5(slot)
            # 先清空坑位展示与代理，等刷新再填下一台（或【暂无云机】）
            if panel.device_name == name:
                panel.clear()
                self._clear_master_if_slot(slot)
        self._pending_delete = {}

    def _clear_slot_s5(self, slot: int) -> None:
        panel = self._panels[slot]
        if panel.s5_ip:
            panel.s5_ip = ""
            panel.refresh_heading()
        self._db.clear_slot_s5_ip(slot)

    def _apply_host_down(self) -> None:
        changed = self._host_state != "down"
        self._host_state = "down"
        self._set_host_status()
        if not changed:
            return
        record_message(f"主机不通 {self._host_ip}")
        self._clear_panels()
        self._update_batch_buttons()

    def _apply_devices(self, slots: dict) -> None:
        self._host_state = "online"
        self._set_host_status()
        changed: set[int] = set()
        for index, panel in self._panels.items():
            payload = slots.get(index)
            previous_name = panel.device_name
            next_name = str((payload or {}).get("name") or "")
            if previous_name and previous_name != next_name:
                self._clear_slot_s5(index)
            elif not next_name and panel.s5_ip:
                self._clear_slot_s5(index)
            if payload is None:
                if panel.clear():
                    changed.add(index)
                    self._clear_master_if_slot(index)
                continue
            s5_loaded = False
            if not panel.s5_ip:
                loaded = self._db.get_slot_s5_ip(index)
                if loaded:
                    panel.s5_ip = loaded
                    s5_loaded = True
            if panel.apply_device(payload):
                changed.add(index)
            elif s5_loaded:
                panel.refresh_heading()
        if changed:
            self._update_batch_buttons()
            self._update_slot_actions(changed)
        if self._pending_restart:
            now = time.monotonic()
            for slot in list(self._pending_restart):
                panel = self._panels[slot]
                if not panel.device_name:
                    self._clear_restart_slot(slot)
                    continue
                if panel.api_ready is True and panel.can_mirror():
                    self._restart_api_ready_at.setdefault(slot, now)
            # 仅同步已到投屏窗口的重启坑；未投上的仍留在 pending，由 poll 重试
            due = {
                slot
                for slot in list(self._pending_restart)
                if self._restart_mirror_due(slot)
            }
            if due:
                self._sync_mirrors(due)
            if self._pending_restart and self._host_ip:
                # 未就绪的坑继续在下一拍刷新里探 9082
                self._last_submit = 0.0
        # 其它有变化的坑照常同步；仍在等重启的坑上面已单独处理
        if changed:
            other = changed - self._pending_restart
            if other:
                self._sync_mirrors(other)

    def _clear_panels(self) -> None:
        for index, panel in self._panels.items():
            panel.clear()
            panel.set_master(False)
            # Keep persisted S5 until delete/name change; only reset visual when host down.
            panel.s5_ip = self._db.get_slot_s5_ip(index)
            panel.update_action_enabled(False)
        self._master_slot = None
        self._refresh_group_capture()
        self._mirrors.stop_all()
        self._pending_restart.clear()
        self._restart_api_ready_at.clear()
        self._restart_mirror_at.clear()
        self._pending_install.clear()
        self._pending_proxy.clear()

    def _current_names(self) -> list[str]:
        return selected_batch_names(
            [
                (self._panels[index].is_selected(), self._panels[index].device_name)
                for index in range(1, 6)
            ]
        )

    def _update_batch_buttons(self) -> None:
        ready = self._host_state == "online" and not self._batch_running and any(
            panel.is_selected() and panel.device_name for panel in self._panels.values()
        )
        if self._batch_delete.isEnabled() != ready:
            self._batch_delete.setEnabled(ready)
        if self._batch_restart.isEnabled() != ready:
            self._batch_restart.setEnabled(ready)
        if self._batch_install.isEnabled() != ready:
            self._batch_install.setEnabled(ready)
        script_ready = self._host_state == "online" and not self._batch_running and bool(
            self._running_slots()
        )
        if self._batch_proxy.isEnabled() != script_ready:
            self._batch_proxy.setEnabled(script_ready)
        for button in self._batch_script_buttons.values():
            if button.isEnabled() != script_ready:
                button.setEnabled(script_ready)

    def _update_slot_actions(self, only_slots: set[int] | None = None) -> None:
        targets = only_slots if only_slots is not None else self._panels.keys()
        online = self._host_state == "online"
        for slot in targets:
            panel = self._panels.get(slot)
            if panel is None:
                continue
            panel.script_busy = self.tasks.slot_script_busy(panel.index)
            panel.update_action_enabled(online)

    def _update_batch_enabled(self) -> None:
        # 批量只影响批量按钮；单机按钮由 _update_slot_actions 按设备状态刷新
        self._update_batch_buttons()
        self._update_slot_actions()

    def _set_host_status(self) -> None:
        """主机 IP 旁只显示本机到盒子的连接状态。"""
        texts = {
            "online": "已连接",
            "checking": "检查中",
            "down": "未连接",
            "unconfigured": "未配置",
        }
        text = texts.get(self._host_state, "未配置")
        if self._status.text() != text:
            self._status.setText(text)
        if self._host_state == "online":
            color = "#2e7d32"
        else:
            color = "#ef6c00"
        style = f"color: {color}; font-weight: 600;"
        if self._status.styleSheet() != style:
            self._status.setStyleSheet(style)
