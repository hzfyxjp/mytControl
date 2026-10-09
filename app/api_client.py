"""魔云腾盒子 HTTP API。单次请求等到返回或超时。"""

from __future__ import annotations

import socket
from urllib.parse import quote

import httpx

from app.config import API_PORT
from app.models import CloudDevice


class ApiError(Exception):
    pass


def probe_host(ip: str, port: int = API_PORT, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_devices(ip: str) -> list[CloudDevice]:
    payload = _request("GET", ip, "/android")
    data = payload.get("data") or {}
    items = data.get("list") or []
    return [CloudDevice.from_api(item) for item in items if isinstance(item, dict)]


def delete_device(ip: str, name: str) -> None:
    _request("DELETE", ip, "/android", params={"name": name}, timeout=20.0)


def restart_device(ip: str, name: str) -> None:
    _request("POST", ip, "/android/restart", json_body={"name": name}, timeout=20.0)


def batch_install_apk(ip: str, apk_urls: list[str], names: list[str]) -> dict:
    return _request(
        "POST",
        ip,
        "/android/batchInstallApk",
        json_body={"apkUrls": apk_urls, "containerNames": names},
        timeout=120.0,
    )


def task_status(ip: str, task_id: str) -> dict:
    return _request("GET", ip, "/android/task-status", params={"taskId": task_id}, timeout=15.0)


def boot_status(ip: str, name: str) -> int:
    quoted = quote(name, safe="")
    payload = _request(
        "GET",
        ip,
        f"/android/bootStatus/{quoted}",
        params={"isblock": 0},
        timeout=6.0,
    )
    data = payload.get("data") or {}
    return int(data.get("bootStatus"))


def set_s5_proxy(
    host_ip: str,
    instance_index: int,
    proxy_ip: str,
    proxy_port: int,
    usr: str,
    pwd: str,
    api_port: int | None = None,
) -> dict:
    from app.s5_proxy import resolve_s5_api_port

    port = resolve_s5_api_port(api_port, instance_index)
    url = f"http://{host_ip}:{port}/proxy"
    params = {
        "cmd": 2,
        "type": 2,
        "ip": proxy_ip,
        "port": proxy_port,
        "usr": usr,
        "pwd": pwd,
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=3.0), trust_env=False) as client:
            response = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise ApiError(f"{exc} (目标 {host_ip}:{port})") from exc
    if response.status_code >= 400:
        raise ApiError(f"HTTP {response.status_code} (目标 {host_ip}:{port})")
    if not response.content:
        raise ApiError(f"代理接口无响应 (目标 {host_ip}:{port})")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError("代理响应不是 JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError("代理响应格式错误")
    code = payload.get("code")
    if code not in (200, "200"):
        raise ApiError(str(payload.get("error") or payload.get("msg") or payload.get("reason") or "设置代理失败"))
    return payload


def _request(
    method: str,
    ip: str,
    path: str,
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: float = 8.0,
) -> dict:
    url = f"http://{ip}:{API_PORT}{path}"
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=2.0), trust_env=False) as client:
            response = client.request(method, url, params=params, json=json_body)
    except httpx.HTTPError as exc:
        raise ApiError(str(exc)) from exc
    if response.status_code >= 400:
        raise ApiError(f"HTTP {response.status_code}")
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError("响应不是 JSON") from exc
    if not isinstance(payload, dict):
        return {}
    code = payload.get("code")
    if code not in (0, None):
        raise ApiError(str(payload.get("message") or "请求失败"))
    return payload
