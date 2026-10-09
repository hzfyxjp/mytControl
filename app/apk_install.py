"""本机临时提供 APK，盒子通过 batchInstallApk 拉取安装。"""

from __future__ import annotations

import errno
import hashlib
import ipaddress
import json
import socket
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, unquote_plus

from app.api_client import ApiError, batch_install_apk, task_status
from app.config import record_message

_CHUNK = 1024 * 1024
_POLL_SLICE_S = 0.2
_POLL_GAP_S = 2.0


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True


class LocalApkServer:
    def __init__(self, port: int) -> None:
        self.port = min(65535, max(1, int(port)))
        self.files: dict[str, Path] = {}
        self.lock = threading.RLock()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self) -> None:
                self._send_file(False)

            def do_GET(self) -> None:
                self._send_file(True)

            def _send_file(self, include_body: bool) -> None:
                request_path = unquote(self.path.split("?", 1)[0])
                with owner.lock:
                    apk_path = owner.files.get(request_path)
                if apk_path is None:
                    self.send_error(404)
                    return
                try:
                    size = apk_path.stat().st_size
                    start, end = 0, size - 1
                    range_header = self.headers.get("Range", "")
                    if range_header.startswith("bytes="):
                        range_start, _, range_end = range_header[6:].split(",", 1)[0].strip().partition("-")
                        if range_start:
                            start = int(range_start)
                            end = int(range_end) if range_end else size - 1
                        else:
                            start = max(0, size - int(range_end))
                        if start < 0 or start >= size or end < start:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.end_headers()
                            return
                        end = min(end, size - 1)
                        self.send_response(206)
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    else:
                        self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.android.package-archive")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(end - start + 1))
                    self.end_headers()
                    if include_body:
                        with apk_path.open("rb") as stream:
                            stream.seek(start)
                            remaining = end - start + 1
                            while remaining and (chunk := stream.read(min(_CHUNK, remaining))):
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                except (OSError, ConnectionError):
                    return

            def log_message(self, format: str, *args: object) -> None:
                return

        try:
            self.server = _ReusableServer(("0.0.0.0", self.port), Handler)
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if exc.errno == errno.EADDRINUSE or winerror == 10048:
                raise ApiError(f"下载端口 {self.port} 已被占用") from exc
            raise ApiError(f"无法启动 APK 下载服务: {exc}") from exc
        self.thread = threading.Thread(target=self.server.serve_forever, name="apk-file-server", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        server = self.server
        self.server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self.thread
        self.thread = None
        if thread is not None:
            thread.join(timeout=2)

    def register(self, apk_path: Path, file_key: str, advertise_ip: str) -> str:
        download_path = f"/apk/{file_key}/{apk_path.name}"
        with self.lock:
            self.files[download_path] = apk_path
        host = advertise_ip.strip()
        if not host:
            raise ApiError("未找到本机 IPv4，盒子无法下载安装包")
        return f"http://{host}:{self.port}/apk/{file_key}/{quote(apk_path.name)}"


_server: LocalApkServer | None = None
_server_lock = threading.RLock()


def ensure_apk_server(port: int) -> LocalApkServer:
    global _server
    with _server_lock:
        if _server is not None and _server.port == port and _server.server is not None:
            return _server
        previous = _server
        server = LocalApkServer(port)
        server.start()
        _server = server
        if previous is not None:
            previous.stop()
        return server


def shutdown_apk_server() -> None:
    global _server
    with _server_lock:
        server = _server
        _server = None
    if server is not None:
        server.stop()


def apk_file_info(apk_path: Path) -> tuple[int, str]:
    try:
        with zipfile.ZipFile(apk_path) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise ApiError(f"APK 文件损坏: {bad_file}")
        digest = hashlib.sha256()
        size = 0
        with apk_path.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
        return size, digest.hexdigest()
    except zipfile.BadZipFile as exc:
        raise ApiError("APK 文件不是完整的安装包") from exc
    except OSError as exc:
        raise ApiError(f"读取 APK 失败: {exc}") from exc


def reachable_local_ipv4(remote_ip: str) -> str:
    remote = (remote_ip or "").strip()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((remote, 1))
            local = str(probe.getsockname()[0] or "").strip()
        finally:
            probe.close()
        if local and not local.startswith("127."):
            return local
    except OSError:
        pass
    try:
        remote_addr = ipaddress.ip_address(remote)
    except ValueError:
        return ""
    for address in _local_ipv4_addresses():
        try:
            if remote_addr in ipaddress.ip_network(f"{address}/24", strict=False):
                return address
        except ValueError:
            continue
    return ""


def _local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = item[4][0]
            if address and not address.startswith("127.") and address not in addresses:
                addresses.append(address)
    except OSError:
        return []
    return addresses


def extract_task_id(value: object) -> str:
    if isinstance(value, dict):
        for key in ("taskId", "task_id", "id"):
            if value.get(key):
                return str(value[key])
        for key in ("data", "result"):
            found = extract_task_id(value.get(key))
            if found:
                return found
    return ""


def batch_install_failures(value: object) -> dict[str, str]:
    data = value.get("data", value) if isinstance(value, dict) else {}
    failed_map = data.get("failedMap", {}) if isinstance(data, dict) else {}
    if not isinstance(failed_map, dict):
        return {}
    failures: dict[str, str] = {}
    for key, reason in failed_map.items():
        name = str(key).split("|", 1)[0].strip()
        if name:
            failures[name] = unquote_plus(str(reason or "安装失败"))
    return failures


def batch_install_successes(value: object) -> set[str] | None:
    data = value.get("data", value) if isinstance(value, dict) else {}
    if not isinstance(data, dict) or "successNames" not in data:
        return None
    names = data.get("successNames")
    if not isinstance(names, list):
        return set()
    return {
        str(name).split("|", 1)[0].strip()
        for name in names
        if str(name).split("|", 1)[0].strip()
    }


def batch_install_is_success(value: object, expected_count: int) -> bool:
    data = value.get("data", value) if isinstance(value, dict) else {}
    if not isinstance(data, dict):
        return False
    state = str(data.get("status", data.get("state", ""))).strip().lower()
    if state not in {"completed", "complete", "finished", "success", "succeeded", "成功", "完成"}:
        return False
    if batch_install_failures(value):
        return False
    try:
        failed_count = int(data.get("failedCount", 0))
        total = int(data.get("total", expected_count))
    except (TypeError, ValueError):
        return False
    if failed_count != 0 or total != expected_count:
        return False
    processed_value = data.get("processed")
    success_count_value = data.get("successCount")
    if processed_value not in (None, ""):
        try:
            processed = int(processed_value)
        except (TypeError, ValueError):
            return False
        if processed != expected_count:
            return False
    elif success_count_value not in (None, ""):
        try:
            success_count = int(success_count_value)
        except (TypeError, ValueError):
            return False
        if success_count != expected_count:
            return False
    else:
        return False
    successes = batch_install_successes(value)
    if successes is not None and successes and len(successes) != expected_count:
        return False
    return True


def install_named_devices(
    host_ip: str,
    names: list[str],
    apk_path: Path,
    package_name: str,
    port: int,
    stop_event: threading.Event,
) -> list[str]:
    if stop_event.is_set():
        return ["安装已停止"]
    if not package_name.strip():
        raise ApiError("请填写包名")
    path = Path(apk_path)
    if not path.is_file():
        raise ApiError("请选择安装包")
    _size, file_key = apk_file_info(path)
    advertise = reachable_local_ipv4(host_ip)
    server = ensure_apk_server(port)
    apk_url = server.register(path, file_key, advertise)
    record_message(f"批量安装 {package_name.strip()} {apk_url}")
    response = batch_install_apk(host_ip, [apk_url], list(names))
    task_id = extract_task_id(response)
    if not task_id:
        raise ApiError("批量安装接口未返回任务编号")
    result = _wait_task(host_ip, task_id, stop_event)
    failures = batch_install_failures(result)
    if failures:
        return [f"{name}: {reason}" for name, reason in failures.items()]
    data = result.get("data", result) if isinstance(result, dict) else {}
    state = str(data.get("status", data.get("state", "")) if isinstance(data, dict) else "").lower()
    if state in {"failed", "failure", "error", "失败"}:
        raise ApiError("批量安装任务失败")
    if not batch_install_is_success(result, len(names)):
        raise ApiError("批量安装未确认全部成功")
    return []


def _wait_task(host_ip: str, task_id: str, stop_event: threading.Event) -> dict:
    started = time.monotonic()
    last_changed = started
    last_snapshot = ""
    timeout = 1800.0
    idle_timeout = 600.0
    while time.monotonic() - started < timeout:
        if stop_event.is_set():
            raise ApiError("安装已停止")
        status = task_status(host_ip, task_id)
        data = status.get("data", status) if isinstance(status, dict) else {}
        state = str(data.get("status", data.get("state", "")) if isinstance(data, dict) else "").strip().lower()
        if state in {
            "success",
            "succeeded",
            "completed",
            "complete",
            "finished",
            "failed",
            "failure",
            "error",
            "失败",
            "成功",
            "完成",
        }:
            return status
        snapshot = json.dumps(data, ensure_ascii=False, sort_keys=True) if isinstance(data, dict) else str(data)
        if snapshot != last_snapshot:
            last_snapshot = snapshot
            last_changed = time.monotonic()
        elif time.monotonic() - last_changed >= idle_timeout:
            raise ApiError("安装任务长时间没有进度")
        _sleep(stop_event, _POLL_GAP_S)
    raise ApiError("安装等待超时")


def _sleep(stop_event: threading.Event, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event.wait(_POLL_SLICE_S):
            raise ApiError("安装已停止")
