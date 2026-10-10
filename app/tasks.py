"""后台任务。结果写入队列，不走 Qt 信号。"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QRunnable, QThreadPool

from app.api_client import (
    ApiError,
    delete_device,
    device_api_is_available,
    list_devices,
    probe_host,
    restart_device,
    set_s5_proxy,
)
from app.apk_install import install_named_devices
from app.config import record_exception, record_message
from app.s5_proxy import S5Proxy
from app.slots import assign_slots, should_fetch_devices
from app.script_timing import ScriptTimings
from app.statuses import RUNNING
from app.xhs_ext import ScriptContent
from app.xhs_scripts import SlotScriptCache, run_script


@dataclass
class TaskEvent:
    kind: str
    data: dict = field(default_factory=dict)


def collect_refresh(
    ip: str,
    check_api_names: set[str],
    stop_event: threading.Event,
    epoch: int,
) -> TaskEvent | None:
    if stop_event.is_set():
        return None
    if not should_fetch_devices(ip, probe_host(ip)):
        return TaskEvent("host", {"state": "down", "epoch": epoch})
    if stop_event.is_set():
        return None
    devices = list_devices(ip)
    assigned = assign_slots(devices)
    need_api = {str(name) for name in check_api_names if name}
    slots: dict[int, dict | None] = {}
    for index, device in assigned.items():
        if stop_event.is_set():
            return None
        if device is None:
            slots[index] = None
            continue
        api_ready = None
        if device.name in need_api and device.status.lower() == RUNNING and device.s5_api_port:
            # 对齐 moyunteng._wait_device：运行中且 9082 口通了才算界面可用
            api_ready = device_api_is_available(ip, int(device.s5_api_port))
        slots[index] = device.slim(api_ready=api_ready)
    return TaskEvent("devices", {"slots": slots, "epoch": epoch})


class Worker(QRunnable):
    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn
        self.setAutoDelete(True)

    def run(self) -> None:
        self._fn()


class TaskService:
    def __init__(self, events: deque) -> None:
        self._events = events
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(8)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._refresh_running = False
        self._batch_running = False
        self._batch_count = 0
        self._slot_script_stops: dict[int, threading.Event] = {}
        self._slot_script_busy: set[int] = set()
        # 导航键：每坑同时只占一个 Worker；连点只保留最新一次
        self._slot_nav_busy: set[int] = set()
        self._slot_nav_pending: dict[int, tuple[str, str, int, str]] = {}
        self._slot_caches: dict[int, SlotScriptCache] = {}
        self._script_timings = ScriptTimings()
        self._script_content = ScriptContent()
        self._timings_lock = threading.Lock()

    def set_script_timings(self, timings: ScriptTimings) -> None:
        with self._timings_lock:
            self._script_timings = ScriptTimings.from_dict(timings.to_dict())

    def set_script_content(self, content: ScriptContent | dict | None) -> None:
        with self._timings_lock:
            self._script_content = (
                content
                if isinstance(content, ScriptContent)
                else ScriptContent.from_dict(content)
            )

    def get_script_content(self) -> ScriptContent:
        with self._timings_lock:
            return ScriptContent.from_dict(self._script_content.to_dict())

    def get_script_timings(self) -> ScriptTimings:
        with self._timings_lock:
            return ScriptTimings.from_dict(self._script_timings.to_dict())

    @property
    def refresh_busy(self) -> bool:
        with self._lock:
            return self._refresh_running

    @property
    def batch_busy(self) -> bool:
        with self._lock:
            return self._batch_running

    def pool_active_count(self) -> int:
        return int(self._pool.activeThreadCount())

    def shutdown(self, fast: bool = True) -> None:
        del fast
        self._stop.set()
        with self._lock:
            for event in self._slot_script_stops.values():
                event.set()

    def slot_script_busy(self, slot: int) -> bool:
        with self._lock:
            return slot in self._slot_script_busy

    def stop_slot_script(self, slot: int) -> None:
        with self._lock:
            event = self._slot_script_stops.get(slot)
            if event is not None:
                event.set()

    def get_slot_cache(self, slot: int) -> SlotScriptCache:
        with self._lock:
            cache = self._slot_caches.get(slot)
            if cache is None:
                cache = SlotScriptCache()
                self._slot_caches[slot] = cache
            return cache

    def submit_refresh(self, ip: str, skip_boot_ids: set[str], epoch: int) -> bool:
        with self._lock:
            if self._stop.is_set() or self._refresh_running or self._batch_running:
                return False
            self._refresh_running = True
        self._pool.start(Worker(lambda: self._refresh(ip, set(skip_boot_ids), epoch)))
        return True

    def _begin_batch(self) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            self._batch_count += 1
            self._batch_running = True
            return True

    def _end_batch(self) -> None:
        with self._lock:
            self._batch_count = max(0, self._batch_count - 1)
            self._batch_running = self._batch_count > 0

    def submit_batch(self, ip: str, action: str, names: list[str], epoch: int) -> bool:
        if not self._begin_batch():
            return False
        self._pool.start(Worker(lambda: self._batch(ip, action, list(names), epoch)))
        return True

    def submit_install(
        self,
        ip: str,
        names: list[str],
        apk_path: str,
        package_name: str,
        port: int,
        epoch: int,
    ) -> bool:
        if not self._begin_batch():
            return False
        self._pool.start(
            Worker(
                lambda: self._install(ip, list(names), apk_path, package_name, port, epoch)
            )
        )
        return True

    def submit_set_proxy(
        self,
        ip: str,
        assignments: list[tuple[int, str, int | None, S5Proxy]],
        epoch: int,
    ) -> bool:
        if not self._begin_batch():
            return False
        packed = [(slot, name, api_port, proxy) for slot, name, api_port, proxy in assignments]
        self._pool.start(Worker(lambda: self._set_proxy(ip, packed, epoch)))
        return True

    def submit_slot_nav(
        self,
        slot: int,
        command: str,
        host_ip: str,
        api_port: int,
        device_name: str = "",
    ) -> bool:
        """单坑导航键：返回 / 主页 / 后台。每坑同时只占一个 Worker。"""
        name = (device_name or "").strip()
        with self._lock:
            if self._stop.is_set():
                return False
            if slot in self._slot_nav_busy:
                # 已有导航在跑：不另开线程，只覆盖为最新指令
                self._slot_nav_pending[slot] = (command, host_ip, int(api_port), name)
                return True
            self._slot_nav_busy.add(slot)
            self._slot_nav_pending.pop(slot, None)
        self._pool.start(
            Worker(
                lambda: self._run_slot_nav(
                    slot, command, host_ip, int(api_port), name
                )
            )
        )
        return True

    def _run_slot_nav(
        self,
        slot: int,
        command: str,
        host_ip: str,
        api_port: int,
        device_name: str = "",
    ) -> None:
        from app.rpa import DeviceRpa

        current = (command, host_ip, int(api_port), (device_name or "").strip())
        try:
            while True:
                cmd, ip, port, name = current
                try:
                    client = DeviceRpa(ip, port, device_name=name)
                    if cmd == "back":
                        client.back()
                    elif cmd == "home":
                        client.home()
                    elif cmd == "tasks":
                        client.tasks()
                    else:
                        record_message(f"未知导航键 slot={slot} command={cmd}")
                except Exception:
                    record_exception(f"坑位导航 slot={slot} command={cmd}")
                with self._lock:
                    pending = self._slot_nav_pending.pop(slot, None)
                    if pending is None:
                        break
                    current = pending
        finally:
            with self._lock:
                self._slot_nav_busy.discard(slot)
                # 收尾瞬间又被点了：再起一个，避免丢最后一次
                leftover = self._slot_nav_pending.pop(slot, None)
                restart = leftover is not None and not self._stop.is_set()
                if restart and leftover is not None:
                    self._slot_nav_busy.add(slot)
            if restart and leftover is not None:
                cmd, ip, port, name = leftover
                self._pool.start(
                    Worker(
                        lambda s=slot, c=cmd, i=ip, p=port, n=name: self._run_slot_nav(
                            s, c, i, p, n
                        )
                    )
                )

    def submit_slot_script(
        self,
        slot: int,
        action: str,
        host_ip: str,
        api_port: int,
        device_name: str,
        epoch: int,
    ) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            # 同坑已有脚本：只停旧任务再换新，不并行叠两个同类 Worker 意图
            old = self._slot_script_stops.get(slot)
            if old is not None:
                old.set()
            stop = threading.Event()
            self._slot_script_stops[slot] = stop
            self._slot_script_busy.add(slot)
            cache = self._slot_caches.get(slot) or SlotScriptCache()
            self._slot_caches[slot] = cache
        self._pool.start(
            Worker(
                lambda: self._run_slot_script(
                    slot, action, host_ip, api_port, device_name, cache, stop, epoch
                )
            )
        )
        return True

    def _run_slot_script(
        self,
        slot: int,
        action: str,
        host_ip: str,
        api_port: int,
        device_name: str,
        cache: SlotScriptCache,
        stop: threading.Event,
        epoch: int,
    ) -> None:
        result_message = ""
        ok = False
        try:
            # 对齐 JS：停旧任务后稍等再开
            from app.rpa import sleep_with_stop

            timings = self.get_script_timings()
            script_content = self.get_script_content()
            sleep_with_stop(stop, timings.start_delay)
            result = run_script(
                action,
                host_ip,
                api_port,
                stop,
                cache,
                timings,
                script_content,
                device_name=device_name,
            )
            ok = result.ok
            result_message = result.message
            with self._lock:
                self._slot_caches[slot] = result.cache
        except Exception as exc:
            from app.rpa import RpaError

            if isinstance(exc, RpaError) and str(exc) == "已停止":
                result_message = "已停止"
                ok = False
            else:
                record_exception(f"坑位脚本 slot={slot} action={action}")
                result_message = str(exc).strip() or "脚本执行失败"
                ok = False
        finally:
            with self._lock:
                current = self._slot_script_stops.get(slot)
                if current is stop:
                    self._slot_script_busy.discard(slot)
                    self._slot_script_stops.pop(slot, None)
                    emit_done = True
                else:
                    emit_done = False
            if emit_done:
                self._emit(
                    TaskEvent(
                        "script_done",
                        {
                            "slot": slot,
                            "action": action,
                            "name": device_name,
                            "ok": ok,
                            "message": result_message,
                            "epoch": epoch,
                            "phone": cache.phone,
                            "sms_url": cache.sms_url,
                        },
                    )
                )

    def _emit(self, event: TaskEvent) -> None:
        self._events.append(event)

    def _refresh(self, ip: str, skip_boot_ids: set[str], epoch: int) -> None:
        try:
            event = collect_refresh(ip, skip_boot_ids, self._stop, epoch)
            if event is not None:
                self._emit(event)
        except Exception:
            record_exception("刷新云机")
            self._emit(TaskEvent("error", {"message": "获取云机失败", "epoch": epoch}))
        finally:
            with self._lock:
                self._refresh_running = False

    def _batch(self, ip: str, action: str, names: list[str], epoch: int) -> None:
        failed: list[str] = []
        try:
            for name in names:
                if self._stop.is_set():
                    break
                try:
                    if action == "delete":
                        delete_device(ip, name)
                    else:
                        restart_device(ip, name)
                except ApiError as exc:
                    failed.append(f"{name}: {exc}")
                    record_exception(f"批量{action} {name}")
        except Exception:
            record_exception("批量操作")
            failed.append("批量操作失败")
        finally:
            self._end_batch()
            self._emit(
                TaskEvent(
                    "batch_done",
                    {"action": action, "failed": failed, "epoch": epoch},
                )
            )

    def _install(
        self,
        ip: str,
        names: list[str],
        apk_path: str,
        package_name: str,
        port: int,
        epoch: int,
    ) -> None:
        failed: list[str] = []
        try:
            failed = install_named_devices(
                ip,
                names,
                Path(apk_path),
                package_name,
                port,
                self._stop,
            )
        except ApiError as exc:
            failed = [str(exc)]
        except Exception:
            record_exception("批量安装")
            failed = ["批量安装失败"]
        finally:
            self._end_batch()
            self._emit(
                TaskEvent(
                    "batch_done",
                    {"action": "install", "failed": failed, "epoch": epoch},
                )
            )

    def _set_proxy(
        self,
        ip: str,
        assignments: list[tuple[int, str, int | None, S5Proxy]],
        epoch: int,
    ) -> None:
        failed: list[str] = []
        succeeded: list[dict] = []
        try:
            for slot, name, api_port, proxy in assignments:
                if self._stop.is_set():
                    break
                try:
                    set_s5_proxy(
                        ip,
                        slot,
                        proxy.ip,
                        proxy.port,
                        proxy.usr,
                        proxy.pwd,
                        api_port=api_port,
                    )
                    succeeded.append(
                        {
                            "slot": slot,
                            "name": name,
                            "s5_ip": proxy.ip,
                            "line": proxy.line,
                        }
                    )
                except ApiError as exc:
                    failed.append(f"实例{slot} {name}: {exc}")
                    record_exception(f"设置代理 slot={slot}")
        except Exception:
            record_exception("批量设置代理")
            failed.append("批量设置代理失败")
        finally:
            self._end_batch()
            self._emit(
                TaskEvent(
                    "batch_done",
                    {
                        "action": "proxy",
                        "failed": failed,
                        "succeeded": succeeded,
                        "epoch": epoch,
                    },
                )
            )
