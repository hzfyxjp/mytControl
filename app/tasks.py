"""后台任务。结果写入队列，不走 Qt 信号。"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QRunnable, QThreadPool

from app.api_client import ApiError, delete_device, list_devices, probe_host, restart_device, set_s5_proxy
from app.apk_install import install_named_devices
from app.config import record_exception
from app.s5_proxy import S5Proxy
from app.slots import assign_slots, should_fetch_devices
from app.script_timing import ScriptTimings
from app.xhs_scripts import SlotScriptCache, run_script


@dataclass
class TaskEvent:
    kind: str
    data: dict = field(default_factory=dict)


def collect_refresh(
    ip: str,
    _skip_boot_ids: set[str],
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
    slots: dict[int, dict | None] = {}
    for index, device in assigned.items():
        if stop_event.is_set():
            return None
        slots[index] = None if device is None else device.slim()
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
        self._slot_script_stops: dict[int, threading.Event] = {}
        self._slot_script_busy: set[int] = set()
        self._slot_caches: dict[int, SlotScriptCache] = {}
        self._script_timings = ScriptTimings()
        self._timings_lock = threading.Lock()

    def set_script_timings(self, timings: ScriptTimings) -> None:
        with self._timings_lock:
            self._script_timings = ScriptTimings.from_dict(timings.to_dict())

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

    def submit_batch(self, ip: str, action: str, names: list[str], epoch: int) -> bool:
        with self._lock:
            if self._stop.is_set() or self._batch_running:
                return False
            self._batch_running = True
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
        with self._lock:
            if self._stop.is_set() or self._batch_running:
                return False
            self._batch_running = True
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
        with self._lock:
            if self._stop.is_set() or self._batch_running:
                return False
            self._batch_running = True
        packed = [(slot, name, api_port, proxy) for slot, name, api_port, proxy in assignments]
        self._pool.start(Worker(lambda: self._set_proxy(ip, packed, epoch)))
        return True

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
            sleep_with_stop(stop, timings.start_delay)
            result = run_script(action, host_ip, api_port, stop, cache, timings)
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
            with self._lock:
                self._batch_running = False
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
            with self._lock:
                self._batch_running = False
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
            with self._lock:
                self._batch_running = False
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
