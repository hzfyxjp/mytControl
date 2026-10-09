"""五坑位中控窗口。"""

from __future__ import annotations

import os
import time
from collections import deque
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
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
    QVBoxLayout,
    QWidget,
)

from app.config import parse_ipv4, record_exception, record_message
from app.database import Database
from app.s5_proxy import parse_s5_pool, take_unique_proxies
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
from app.xhs_scripts import SCRIPT_HANDLERS

REFRESH_INTERVAL_S = 2.0
EVENT_INTERVAL_MS = 50

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

SLOT_MANAGE_ACTIONS = [
    ("delete", "删除"),
    ("restart", "重启"),
]

# 与单坑脚本按钮对应（不含停止）；用户文案里重复的「切号」按一次处理
BATCH_SCRIPT_ACTIONS = [
    (action, f"批量{label}")
    for action, label in SLOT_ACTIONS
    if action != "stop"
]


class SlotPanel(QFrame):
    def __init__(self, index: int, on_action=None) -> None:
        super().__init__()
        self.index = index
        self._on_action = on_action
        self.device_id = ""
        self.device_name = ""
        self.device_status = ""
        self.s5_api_port: int | None = None
        self.s5_ip = ""
        self.phone = ""
        self.sms_url = ""
        self.script_busy = False
        self._heading = EMPTY_SLOT_HEADING
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.title = QLabel(EMPTY_SLOT_HEADING)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._action_buttons: dict[str, QPushButton] = {}
        row1 = QHBoxLayout()
        row1.setSpacing(2)
        row1.setContentsMargins(0, 0, 0, 0)
        row2 = QHBoxLayout()
        row2.setSpacing(2)
        row2.setContentsMargins(0, 0, 0, 0)
        row3 = QHBoxLayout()
        row3.setSpacing(2)
        row3.setContentsMargins(0, 0, 0, 0)
        for i, (action, label) in enumerate(SLOT_ACTIONS):
            button = QPushButton(label)
            button.setEnabled(False)
            button.setMinimumWidth(36)
            button.clicked.connect(lambda _checked=False, a=action: self._emit_action(a))
            self._action_buttons[action] = button
            (row1 if i < 5 else row2).addWidget(button, 1)
        for action, label in SLOT_MANAGE_ACTIONS:
            button = QPushButton(label)
            button.setEnabled(False)
            button.setMinimumWidth(36)
            if action == "delete":
                button.setStyleSheet(
                    "QPushButton { color: #c62828; font-weight: 600; }"
                    "QPushButton:disabled { color: #e57373; }"
                )
            else:
                button.setStyleSheet(
                    "QPushButton { color: #2e7d32; font-weight: 600; }"
                    "QPushButton:disabled { color: #81c784; }"
                )
            button.clicked.connect(lambda _checked=False, a=action: self._emit_action(a))
            self._action_buttons[action] = button
            row3.addWidget(button, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 2, 6, 2)
        outer.setSpacing(0)
        outer.addWidget(self.title)
        outer.addLayout(row1)
        outer.addLayout(row2)
        outer.addLayout(row3)

    def _emit_action(self, action: str) -> None:
        if self._on_action is not None:
            self._on_action(self.index, action)

    def set_heading(self, text: str) -> None:
        if text == self._heading:
            return
        self._heading = text
        self.title.setToolTip(text)
        self._apply_heading()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_heading()

    def apply_device(self, payload: dict | None) -> None:
        if not payload:
            self.clear()
            return
        self.device_id = str(payload.get("id") or "")
        self.device_name = str(payload.get("name") or "")
        self.device_status = str(payload.get("status") or "")
        raw_port = payload.get("s5_api_port")
        try:
            self.s5_api_port = int(raw_port) if raw_port not in (None, "") else None
        except (TypeError, ValueError):
            self.s5_api_port = None
        self.refresh_heading()

    def clear(self) -> None:
        self.device_id = ""
        self.device_name = ""
        self.device_status = ""
        self.s5_api_port = None
        self.s5_ip = ""
        self.script_busy = False
        self.refresh_heading()
        self.update_action_enabled(False, batch_busy=False)

    def update_action_enabled(self, host_online: bool = True, batch_busy: bool = False) -> None:
        has_device = bool(host_online and self.device_name and not batch_busy)
        running = bool(
            has_device
            and self.device_status.lower() == RUNNING
            and self.s5_api_port
        )
        for action, button in self._action_buttons.items():
            if action in {"delete", "restart"}:
                enabled = has_device
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
        self._panels = {
            index: SlotPanel(index, on_action=self._on_slot_action) for index in range(1, 6)
        }
        self._host_ip = self._db.get_host_ip()
        self._host_state = "unconfigured"
        self._epoch = 0
        self._batch_running = False
        self._last_submit = 0.0
        self._sized = False
        self._pending_delete: dict[str, int] = {}
        self._build_ui()
        if self._host_ip:
            self._host_state = "checking"
            self._set_status("正在检查主机")
        else:
            self._set_status("未配置主机，不获取云机")
        self._update_batch_enabled()
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

        self._slot_row = QHBoxLayout()
        self._slot_row.setSpacing(12)
        for index in range(1, 6):
            self._slot_row.addWidget(self._panels[index], 1)
        self._root.addLayout(self._slot_row)

        self._batch_script_row = QHBoxLayout()
        self._batch_script_buttons: dict[str, QPushButton] = {}
        self._batch_script_row.addStretch(1)
        for action, label in BATCH_SCRIPT_ACTIONS:
            button = QPushButton(label)
            button.setEnabled(False)
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
        self._batch_delete.setStyleSheet(
            "QPushButton { color: #c62828; font-weight: 600; }"
            "QPushButton:disabled { color: #e57373; }"
        )
        self._batch_restart.setStyleSheet(
            "QPushButton { color: #2e7d32; font-weight: 600; }"
            "QPushButton:disabled { color: #81c784; }"
        )
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
        save_button.clicked.connect(self._save_host)
        self._status = QLabel()
        self._status.setMinimumWidth(140)
        choose_apk = QPushButton("选择安装包")
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

    def closeEvent(self, event) -> None:
        self._remember_host_ip()
        self._remember_s5_pool()
        self._remember_install_settings()
        self._remember_script_timings()
        self._timer.stop()
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
            self.resize(1600, 900)
            return
        area = screen.availableGeometry()
        width = max(960, area.width() - 40)
        self.resize(int(width), self.sizeHint().height())
        frame = self.frameGeometry()
        frame.moveCenter(area.center())
        self.move(frame.topLeft())

    def _on_tick(self) -> None:
        try:
            self._drain()
            self._maybe_refresh()
        except Exception:
            record_exception("界面刷新")

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
                self._set_status(str(event.data.get("message") or "获取云机失败"))
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
            self._set_status(f"坑位{slot} 停止中")
            return
        if action in {"delete", "restart"}:
            self._slot_manage(slot, action)
            return
        self._start_slot_script(slot, action)

    def _start_slot_script(self, slot: int, action: str) -> bool:
        panel = self._panels.get(slot)
        if panel is None or not self._host_ip:
            return False
        if not panel.device_name or panel.device_status.lower() != RUNNING:
            return False
        if not panel.s5_api_port:
            QMessageBox.warning(self, "脚本", f"坑位{slot} 缺少 9082 映射端口。")
            return False
        label = SCRIPT_HANDLERS.get(action, action)
        if not self.tasks.submit_slot_script(
            slot,
            action,
            self._host_ip,
            int(panel.s5_api_port),
            panel.device_name,
            self._epoch,
        ):
            return False
        panel.script_busy = True
        panel.update_action_enabled(self._host_state == "online", self._batch_running)
        panel.set_heading(slot_heading(panel.device_name, SCRIPTING, panel.s5_ip))
        self._set_status(f"坑位{slot} {label}中")
        return True

    def _script_batch(self, action: str) -> None:
        """遍历当前可跑脚本的坑位，逐个点单坑同名按钮；失败仍按单坑弹窗。"""
        if self._host_state != "online" or not self._host_ip or self._batch_running:
            return
        started = 0
        for slot in range(1, 6):
            panel = self._panels[slot]
            if not panel.device_name or panel.device_status.lower() != RUNNING:
                continue
            if self._start_slot_script(slot, action):
                started += 1
        if started:
            label = SCRIPT_HANDLERS.get(action, action)
            self._set_status(f"批量{label}：已提交 {started} 个坑位")

    def _slot_manage(self, slot: int, action: str) -> None:
        panel = self._panels.get(slot)
        if panel is None or not panel.device_name or self._host_state != "online":
            return
        if self._batch_running:
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
        panel.update_action_enabled(self._host_state == "online", self._batch_running)
        if ok:
            self._set_status(f"坑位{slot} {label}完成")
            return
        self._set_status(f"坑位{slot} {label}失败")
        # 用户点停止也弹一下，避免误以为卡住没反应
        tip = message or f"{label}失败（无详细错误）"
        QMessageBox.warning(self, f"坑位{slot} {label}", tip)

    def _maybe_refresh(self) -> None:
        if not self._host_ip or self._batch_running:
            return
        now = time.monotonic()
        if self._last_submit and now - self._last_submit < REFRESH_INTERVAL_S:
            return
        if self.tasks.submit_refresh(self._host_ip, set(), self._epoch):
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
        self._batch_running = False
        self._clear_panels()
        self._host_state = "checking"
        self._set_status("正在检查主机")
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
        self._batch_running = True
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
        self._batch_running = True
        self._update_batch_enabled()
        self._mark_phase([name for _slot, name, _port, _proxy in assignments], SETTING_PROXY)

    def _running_slots(self) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        for index in range(1, 6):
            panel = self._panels[index]
            if panel.device_name and panel.device_status.lower() == RUNNING:
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
        self._batch_running = True
        self._update_batch_enabled()
        if action == "delete":
            self._pending_delete = {
                panel.device_name: index
                for index, panel in self._panels.items()
                if panel.device_name in names
            }
        phase = DELETING if action == "delete" else RESTARTING
        self._mark_phase(names, phase)

    def _mark_phase(self, names: list[str], phase: str) -> None:
        selected = set(names)
        for panel in self._panels.values():
            if panel.device_name in selected:
                panel.set_heading(slot_heading(panel.device_name, phase, panel.s5_ip))

    def _apply_batch_done(self, data: dict) -> None:
        self._batch_running = False
        failed = data.get("failed") or []
        action_key = str(data.get("action") or "")
        labels = {"delete": "删除", "restart": "重启", "install": "安装", "proxy": "设置代理"}
        action = labels.get(action_key, "操作")
        if action_key == "proxy":
            self._apply_proxy_done(data)
        elif action_key == "delete":
            self._clear_s5_for_deleted(failed)
        if failed:
            QMessageBox.warning(self, f"批量{action}", "\n".join(str(item) for item in failed))
        self._update_batch_enabled()
        self._last_submit = 0.0
        # 删除后立刻拉列表，空坑或下一台云机马上显示，且代理已清
        if action_key == "delete" and self._host_ip:
            if self.tasks.submit_refresh(self._host_ip, set(), self._epoch):
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
        self._set_status("主机不通，不获取云机")
        if changed:
            record_message(f"主机不通 {self._host_ip}")
            self._clear_panels()
        self._update_batch_enabled()

    def _apply_devices(self, slots: dict) -> None:
        self._host_state = "online"
        if not self._batch_running:
            self._set_status("已连接")
        self.setUpdatesEnabled(False)
        try:
            for index, panel in self._panels.items():
                payload = slots.get(index)
                previous_name = panel.device_name
                next_name = str((payload or {}).get("name") or "")
                if previous_name and previous_name != next_name:
                    self._clear_slot_s5(index)
                elif not next_name and panel.s5_ip:
                    self._clear_slot_s5(index)
                if payload is None:
                    panel.clear()
                else:
                    if not panel.s5_ip:
                        panel.s5_ip = self._db.get_slot_s5_ip(index)
                    panel.apply_device(payload)
        finally:
            self.setUpdatesEnabled(True)
        self._update_batch_enabled()
        for panel in self._panels.values():
            panel.script_busy = self.tasks.slot_script_busy(panel.index)
            panel.update_action_enabled(self._host_state == "online", self._batch_running)

    def _clear_panels(self) -> None:
        self.setUpdatesEnabled(False)
        try:
            for index, panel in self._panels.items():
                panel.clear()
                # Keep persisted S5 until delete/name change; only reset visual when host down.
                panel.s5_ip = self._db.get_slot_s5_ip(index)
                panel.update_action_enabled(False, batch_busy=False)
        finally:
            self.setUpdatesEnabled(True)

    def _current_names(self) -> list[str]:
        return [self._panels[index].device_name for index in range(1, 6) if self._panels[index].device_name]

    def _update_batch_enabled(self) -> None:
        ready = self._host_state == "online" and not self._batch_running and any(
            panel.device_name for panel in self._panels.values()
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
        for panel in self._panels.values():
            panel.script_busy = self.tasks.slot_script_busy(panel.index)
            panel.update_action_enabled(self._host_state == "online", self._batch_running)

    def _set_status(self, text: str) -> None:
        if self._status.text() != text:
            self._status.setText(text)
        # 已连接绿色，未连接/不通橙色；其它提示保持默认色
        if text == "已连接":
            color = "#2e7d32"
        elif text in {"主机不通，不获取云机", "未配置主机，不获取云机"} or "不通" in text or "未连接" in text:
            color = "#ef6c00"
        elif text == "正在检查主机":
            color = "#ef6c00"
        else:
            color = ""
        style = f"color: {color}; font-weight: 600;" if color else ""
        if self._status.styleSheet() != style:
            self._status.setStyleSheet(style)
