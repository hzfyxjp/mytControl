"""小红书脚本用的外部 HTTP（取号 / 话术 / 接码）。"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

DEFAULT_API = "http://8.136.11.82/api.php"
DEFAULT_ADD = "http://8.136.11.82/add.php"
DEFAULT_GET = "http://8.136.11.82/get.php"
DEFAULT_GET2 = "http://8.136.11.82/get2.php"


class ExtApiError(Exception):
    pass


@dataclass(frozen=True)
class PhoneTicket:
    phone: str
    url: str


def _get_text(url: str, timeout: float = 20.0) -> str:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), trust_env=False) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise ExtApiError(str(exc)) from exc
    if response.status_code >= 400:
        raise ExtApiError(f"HTTP {response.status_code}")
    return response.text


def _get_json(url: str, timeout: float = 20.0) -> dict:
    text = _get_text(url, timeout=timeout)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ExtApiError("响应不是 JSON") from exc
    if not isinstance(payload, dict):
        raise ExtApiError("响应格式错误")
    return payload


def fetch_phone(api_url: str = DEFAULT_API) -> PhoneTicket:
    payload = _get_json(api_url)
    if int(payload.get("code") or 0) != 1:
        raise ExtApiError("暂无数据")
    phone = str(payload.get("phone") or "").strip()
    url = str(payload.get("url") or "").replace("\\/", "/").strip()
    if not phone or not url:
        raise ExtApiError("取号结果缺少 phone/url")
    return PhoneTicket(phone=phone, url=url)


def return_phone(phone: str, sms_url: str, add_url: str = DEFAULT_ADD) -> str:
    from urllib.parse import quote

    full = f"{add_url}?phone={quote(phone)}&url={quote(sms_url)}"
    return _get_text(full)


def fetch_sms_body(sms_url: str) -> str:
    return _get_text(sms_url)


def fetch_comment_lines(get_url: str = DEFAULT_GET) -> list[str]:
    payload = _get_json(get_url)
    content = str(payload.get("content") or "")
    parts = content.split("|")
    if len(parts) < 3:
        raise ExtApiError("话术不足 3 段")
    return parts


def fetch_get2(get2_url: str = DEFAULT_GET2) -> dict:
    return _get_json(get2_url)
