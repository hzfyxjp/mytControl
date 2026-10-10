"""小红书脚本：取号仍走 HTTP；评论/进群/改收文案改读本地配置。"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

DEFAULT_API = "http://8.136.11.82/api.php"
DEFAULT_ADD = "http://8.136.11.82/add.php"


class ExtApiError(Exception):
    pass


@dataclass(frozen=True)
class PhoneTicket:
    phone: str
    url: str


@dataclass(frozen=True)
class ScriptContent:
    """应用内配置的脚本文案；字段不 strip，保留表情与空格。"""

    comment_content: str = ""
    join_title: str = ""
    edit_text: str = ""
    edit_tags: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> "ScriptContent":
        raw = data if isinstance(data, dict) else {}
        return cls(
            comment_content=str(raw.get("comment_content") or ""),
            join_title=str(raw.get("join_title") or ""),
            edit_text=str(raw.get("edit_text") or ""),
            edit_tags=str(raw.get("edit_tags") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "comment_content": self.comment_content,
            "join_title": self.join_title,
            "edit_text": self.edit_text,
            "edit_tags": self.edit_tags,
        }


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


def comment_lines_from_local(content: str) -> list[str]:
    """评论话术：按 | 分段，不对各段 strip。"""
    parts = str(content or "").split("|")
    if len(parts) < 3:
        raise ExtApiError("评论话术不足 3 段（用 | 分隔）")
    return parts


def get2_from_local(content: ScriptContent | dict | None) -> dict[str, str]:
    """进群/改收用本地配置；不对 title/text/tags 做 strip。"""
    cfg = content if isinstance(content, ScriptContent) else ScriptContent.from_dict(content)
    return {
        "title": cfg.join_title,
        "text": cfg.edit_text,
        "tags": cfg.edit_tags,
    }
