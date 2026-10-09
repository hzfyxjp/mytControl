"""小红书坑位脚本：选择器与停留时间与桌面 Auto.js 保持一致。"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field

from app.rpa import DeviceRpa, RpaError, extract_sms_code
from app.script_timing import ScriptTimings
from app.xhs_ext import (
    DEFAULT_ADD,
    DEFAULT_API,
    DEFAULT_GET,
    DEFAULT_GET2,
    ExtApiError,
    fetch_comment_lines,
    fetch_get2,
    fetch_phone,
    fetch_sms_body,
    return_phone,
)

PKG = "com.xingin.xhs"
OBF_ID = "0_resource_name_obfuscated"


@dataclass
class SlotScriptCache:
    phone: str = ""
    sms_url: str = ""


@dataclass
class ScriptResult:
    ok: bool
    message: str = ""
    cache: SlotScriptCache = field(default_factory=SlotScriptCache)


def _rpa(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
) -> DeviceRpa:
    if not api_port:
        raise RpaError("缺少 9082 映射端口")
    return DeviceRpa(
        host_ip,
        api_port,
        stop_event=stop,
        poll_interval=timings.poll_interval,
    )


def run_home(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings)
    rpa.open_uri("xhsdiscover://profile", PKG)
    return ScriptResult(True, "已打开主页")


def run_comment(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    get_url: str = DEFAULT_GET,
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings)
    lines = fetch_comment_lines(get_url)
    t = timings

    def send_line(text: str) -> None:
        box = rpa.wait_one(resource_id=OBF_ID, depth=9, timeout=t.find_timeout)
        rpa.click_node(box)
        rpa.set_text_via_clipboard(text)
        rpa.sleep(t.sleep_long)
        rpa.click_text("发送", timeout=t.click_timeout)
        rpa.sleep(t.sleep_long)

    def reply_first(text: str) -> None:
        nodes = rpa.wait_nodes(
            resource_id=OBF_ID,
            class_name="android.widget.TextView",
            clickable=False,
            depth=13,
            timeout=t.find_timeout,
        )
        rpa.click_node(nodes[0])
        rpa.sleep(t.sleep_long)
        rpa.wait_one(text="回复", timeout=t.find_timeout)
        rpa.sleep(t.sleep_long)
        rpa.click_text("回复", timeout=t.click_timeout)
        rpa.sleep(t.sleep_long)
        send_line(text)

    send_line(lines[0])
    reply_first(lines[1])
    reply_first(lines[2])
    return ScriptResult(True, "评论完成")


def run_switch_account(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings)
    t = timings
    rpa.open_uri("xhsdiscover://profile", PKG)
    rpa.sleep(t.sleep_long)
    rpa.click_desc("设置", timeout=t.find_timeout)
    rpa.sleep(t.sleep_long)
    rpa.wait_one(text="账号与安全", timeout=t.find_timeout)
    rpa.sleep(t.sleep_long)
    roots = rpa.dump_nodes()
    if not roots:
        raise RpaError("无法获取屏幕尺寸")
    max_x = max(n.bounds[2] for n in roots)
    max_y = max(n.bounds[3] for n in roots)
    for _ in range(3):
        rpa.swipe(max_x // 2, int(max_y * 0.8), max_x // 2, int(max_y * 0.2), 300)
    rpa.click_text("切换账号", timeout=t.find_timeout)
    rpa.sleep(t.sleep_long)
    rpa.wait_one(text="添加或注册新账号", timeout=t.find_timeout)
    rpa.sleep(t.sleep_long)
    rpa.click_text("添加或注册新账号", timeout=t.click_timeout)
    rpa.sleep(t.sleep_long)
    rpa.wait_one(text="手机号登录", timeout=t.find_timeout)
    rpa.sleep(t.sleep_long)
    rpa.click_desc("全部删除", timeout=t.click_timeout)
    return ScriptResult(True, "切号完成")


def run_join_group(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    get2_url: str = DEFAULT_GET2,
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings)
    t = timings
    payload = fetch_get2(get2_url)
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ExtApiError("get2 缺少 title")
    rpa.open_uri(f"xhsdiscover://user/{title}", PKG)
    rpa.wait_one(text="群聊", timeout=t.page_timeout)
    rpa.sleep(t.sleep_long)
    rpa.click_text("群聊", timeout=t.click_timeout)
    rpa.sleep(t.sleep_long)
    rpa.wait_one(text="立即加入", timeout=t.page_timeout)
    rpa.sleep(t.sleep_long)
    rpa.click_text("立即加入", timeout=t.click_timeout)
    rpa.sleep(t.sleep_long)
    return ScriptResult(True, "进群完成")


def run_fetch_phone(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    cache: SlotScriptCache,
    api_url: str = DEFAULT_API,
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings)
    t = timings
    ticket = fetch_phone(api_url)
    cache.phone = ticket.phone
    cache.sms_url = ticket.url

    if rpa.exists(text="请输入手机号码"):
        node = rpa.wait_one(text="请输入手机号码", timeout=t.short_timeout)
        rpa.click_node(node)
        rpa.input_ascii(ticket.phone)
    elif rpa.exists(text="请输入手机号", depth=11):
        node = rpa.wait_one(text="请输入手机号", depth=11, timeout=t.short_timeout)
        rpa.click_node(node)
        rpa.input_ascii(ticket.phone)
    else:
        return ScriptResult(True, f"已取号 {ticket.phone}，未找到手机号输入框", cache)

    rpa.wait_one(text="Enter code", timeout=t.long_timeout)
    return ScriptResult(True, f"已取号 {ticket.phone}", cache)


def run_fetch_code(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    cache: SlotScriptCache,
) -> ScriptResult:
    if not cache.sms_url:
        raise ExtApiError("请先取号")
    rpa = _rpa(host_ip, api_port, stop, timings)
    t = timings
    body = fetch_sms_body(cache.sms_url)
    code = extract_sms_code(body)
    if not code:
        raise ExtApiError("未找到验证码")

    if rpa.exists(text="输入验证码"):
        node = rpa.wait_one(text="输入验证码", timeout=t.short_timeout)
        rpa.click_node(node)
        rpa.input_ascii(code)
        rpa.sleep(t.sleep_mid)
    elif rpa.exists(text="验证手机号码"):
        rpa.input_ascii(code)
    else:
        raise RpaError("未找到验证码输入框")

    rpa.wait_one(text="您的生日是哪天？", timeout=t.long_timeout)
    rpa.sleep(t.sleep_mid)
    for _ in range(3):
        rpa.swipe(
            random.randint(112, 271),
            random.randint(519, 600),
            random.randint(108, 271),
            random.randint(900, 975),
            300,
        )
        rpa.sleep(t.sleep_short)
    rpa.sleep(t.sleep_mid)
    rpa.click_text("下一步", timeout=t.click_timeout)
    return ScriptResult(True, f"已填验证码 {code}", cache)


def run_return_phone(
    cache: SlotScriptCache,
    add_url: str = DEFAULT_ADD,
) -> ScriptResult:
    if not cache.phone or not cache.sms_url:
        raise ExtApiError("请先取号")
    raw = return_phone(cache.phone, cache.sms_url, add_url)
    return ScriptResult(True, f"返号结果: {raw[:120]}", cache)


def run_edit_profile(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    get2_url: str = DEFAULT_GET2,
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings)
    t = timings
    rpa.open_uri("xhsdiscover://me/profile", PKG)
    rpa.wait_one(text="简介", timeout=t.page_timeout)
    rpa.sleep(t.sleep_long)
    intro = rpa.wait_one(text="简介", timeout=t.short_timeout)
    rpa.click_node(intro, offset_x=222, offset_y=0)
    rpa.sleep(t.sleep_long)

    payload = fetch_get2(get2_url)
    text1 = str(payload.get("text") or "")
    tags = str(payload.get("tags") or "").strip()
    parts = text1.split("|")
    while len(parts) < 4:
        parts.append("")
    signature = "\n".join(parts[:4])

    edit = rpa.wait_one(class_name="android.widget.EditText", depth=10, timeout=t.find_timeout)
    rpa.click_node(edit)
    rpa.set_text_via_clipboard(signature)
    rpa.sleep(t.sleep_long)
    rpa.click_text("保存", timeout=t.click_timeout)
    rpa.sleep(t.sleep_mid)

    if tags:
        rpa.open_uri(f"xhsdiscover://user/{tags}", PKG)
        rpa.wait_one(text="收藏", timeout=t.page_timeout)
        rpa.sleep(t.sleep_mid)
        rpa.click_text("收藏", timeout=t.click_timeout)
        rpa.sleep(t.sleep_mid)
        rpa.wait_one(text="合集 2", timeout=t.page_timeout)
        rpa.sleep(t.sleep_long)
        groups = rpa.find(class_name="android.view.ViewGroup", depth=19)
        for node in groups:
            rpa.click_node(node)
            rpa.wait_one(text="收藏合集", timeout=t.click_timeout)
            rpa.sleep(t.sleep_mid)
            rpa.click_text("收藏合集", timeout=t.short_timeout)
            rpa.sleep(t.sleep_mid)
            rpa.wait_one(text="已收藏", timeout=t.click_timeout)
            rpa.back()
            rpa.sleep(t.sleep_mid)
        rpa.sleep(t.sleep_mid)
    return ScriptResult(True, "改收完成")


SCRIPT_HANDLERS = {
    "home": "主页",
    "comment": "评论",
    "switch": "切号",
    "join": "进群",
    "phone": "取号",
    "code": "取码",
    "return_phone": "返号",
    "edit": "改收",
    "stop": "停止",
}


def run_script(
    action: str,
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    cache: SlotScriptCache,
    timings: ScriptTimings | None = None,
) -> ScriptResult:
    t = timings or ScriptTimings()
    try:
        if action == "home":
            return run_home(host_ip, api_port, stop, t)
        if action == "comment":
            return run_comment(host_ip, api_port, stop, t)
        if action == "switch":
            return run_switch_account(host_ip, api_port, stop, t)
        if action == "join":
            return run_join_group(host_ip, api_port, stop, t)
        if action == "phone":
            return run_fetch_phone(host_ip, api_port, stop, t, cache)
        if action == "code":
            return run_fetch_code(host_ip, api_port, stop, t, cache)
        if action == "return_phone":
            return run_return_phone(cache)
        if action == "edit":
            return run_edit_profile(host_ip, api_port, stop, t)
        raise RpaError(f"未知动作 {action}")
    except (RpaError, ExtApiError) as exc:
        return ScriptResult(False, str(exc), cache)
    except Exception as exc:
        return ScriptResult(False, f"脚本异常: {exc}", cache)
