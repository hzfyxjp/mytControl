"""小红书坑位脚本：选择器与停留时间与桌面 Auto.js 保持一致。"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

from app.rpa import DeviceRpa, RpaError, extract_sms_code
from app.script_timing import ScriptTimings
from app.xhs_ext import (
    DEFAULT_ADD,
    DEFAULT_API,
    ExtApiError,
    ScriptContent,
    comment_lines_from_local,
    fetch_phone,
    fetch_sms_body,
    get2_from_local,
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
    device_name: str = "",
) -> DeviceRpa:
    if not (device_name or "").strip():
        raise RpaError("缺少云机名")
    return DeviceRpa(
        host_ip,
        api_port,
        stop_event=stop,
        poll_interval=timings.poll_interval,
        device_name=device_name,
    )


def run_home(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    device_name: str = "",
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings, device_name)
    rpa.open_uri("xhsdiscover://profile", PKG)
    return ScriptResult(True, "已打开主页")


def _find_comment_edit(rpa: DeviceRpa, timeout: float):
    """评论输入框：官方 dump 为 id + EditText + depth(1)；无障碍兜底 depth(9)。

    不能只写 id+depth：同层还有 View 会先被命中，必须带 EditText。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for depth in (1, 9):
            found = rpa.find(
                resource_id=OBF_ID,
                class_name="android.widget.EditText",
                depth=depth,
            )
            if found:
                return found[0]
        rpa.sleep(0.2)
    raise RpaError("未找到评论输入框")


def _find_note_comment_tab(rpa: DeviceRpa):
    """笔记底栏右下角评论按钮：Button + content-desc 以「评论」/Comment 开头。"""
    for n in rpa.dump_nodes():
        if "Button" not in n.class_name or not n.clickable:
            continue
        desc = (n.content_desc or "").strip()
        if desc.startswith("评论") or desc.startswith("Comment"):
            return n
    return None


def run_comment(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    script_content: ScriptContent | None = None,
    device_name: str = "",
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings, device_name)
    cfg = script_content or ScriptContent()
    lines = comment_lines_from_local(cfg.comment_content)
    t = timings

    # 先点底栏评论按钮两次（间隔 0.5s），等到「发送」再走原评论流程
    if not (rpa.exists(text="发送") or rpa.exists(text="Post")):
        for i in range(2):
            tab = _find_note_comment_tab(rpa)
            if tab is None:
                raise RpaError("未找到右下角评论按钮")
            rpa.click_node(tab)
            rpa.sleep(0.5)
        deadline = time.monotonic() + t.find_timeout
        while time.monotonic() < deadline:
            if rpa.exists(text="发送") or rpa.exists(text="Post"):
                break
            rpa.sleep(t.poll_interval)
        else:
            raise RpaError("未出现发送按钮")

    def _reply_bodies_now() -> list:
        """第一条评论正文：官方 dump 为 depth(3)；无障碍树多为 depth(13)。"""
        for depth in (3, 13):
            nodes = [
                n
                for n in rpa.find(
                    resource_id=OBF_ID,
                    class_name="android.widget.TextView",
                    clickable=False,
                    depth=depth,
                )
                if (n.text or "").strip()
                and n.bounds[2] > n.bounds[0]
                and n.bounds[3] > n.bounds[1]
            ]
            if nodes:
                return nodes
        return []

    def _find_reply_targets() -> list:
        deadline = time.monotonic() + t.find_timeout
        while time.monotonic() < deadline:
            nodes = _reply_bodies_now()
            if nodes:
                return nodes
            rpa.sleep(t.poll_interval)
        raise RpaError("未找到可回复评论")

    def _open_composer() -> None:
        if rpa.exists(text="发送") or rpa.exists(text="Post"):
            return
        # 发完回复后常回到列表，先点「说点什么」/评论按钮再出输入框
        for n in rpa.dump_nodes():
            if n.clickable and (
                (n.text or "").startswith("说点什么")
                or (n.content_desc or "") == "评论框"
                or (n.text or "").startswith("Leave a comment")
                or (n.text or "").startswith("Add a comment")
            ):
                rpa.click_node(n)
                rpa.sleep(0.5)
                break
        else:
            tab = _find_note_comment_tab(rpa)
            if tab is None:
                raise RpaError("未找到评论入口")
            rpa.click_node(tab)
            rpa.sleep(0.5)
        deadline = time.monotonic() + t.find_timeout
        while time.monotonic() < deadline:
            if rpa.exists(text="发送") or rpa.exists(text="Post"):
                return
            rpa.sleep(t.poll_interval)
        raise RpaError("未出现发送按钮")

    def send_line(text: str) -> None:
        _open_composer()
        box = _find_comment_edit(rpa, t.find_timeout)
        rpa.click_node(box)
        rpa.input_text(text)
        rpa.sleep(t.sleep_long)
        if rpa.exists(text="发送"):
            rpa.click_text("发送", timeout=t.click_timeout)
        else:
            rpa.click_text("Post", timeout=t.click_timeout)
        rpa.sleep(t.sleep_long)

    def _wait_comment_list() -> None:
        """发完主评后要回到列表，才能点自己的评论、等精确「回复」。"""
        deadline = time.monotonic() + t.find_timeout
        while time.monotonic() < deadline:
            if _reply_bodies_now():
                return
            # 还停在输入层：返回列表
            if rpa.exists(text="发送") or rpa.exists(text="Post"):
                rpa.back()
            rpa.sleep(t.poll_interval)
        raise RpaError("未回到评论列表")

    def reply_first(text: str) -> None:
        # 对齐 Auto.js：点自己的第一条评论，死等精确「回复」再点
        nodes = _find_reply_targets()
        rpa.click_node(nodes[0])
        rpa.sleep(t.sleep_long)
        deadline = time.monotonic() + t.find_timeout
        clicked = False
        while time.monotonic() < deadline:
            if rpa.exists(text="回复"):
                rpa.sleep(t.sleep_long)
                rpa.click_text("回复", timeout=t.click_timeout)
                clicked = True
                break
            if rpa.exists(text="Reply"):
                rpa.sleep(t.sleep_long)
                rpa.click_text("Reply", timeout=t.click_timeout)
                clicked = True
                break
            rpa.sleep(t.poll_interval)
        if not clicked:
            raise RpaError("未找到回复按钮")
        rpa.sleep(t.sleep_long)
        send_line(text)

    send_line(lines[0])
    _wait_comment_list()
    reply_first(lines[1])
    _wait_comment_list()
    reply_first(lines[2])
    return ScriptResult(True, "评论完成")


def run_switch_account(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    device_name: str = "",
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings, device_name)
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
    script_content: ScriptContent | None = None,
    device_name: str = "",
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings, device_name)
    t = timings
    payload = get2_from_local(script_content)
    title = str(payload.get("title") or "")
    if not title:
        raise ExtApiError("未配置进群用户ID")
    rpa.open_uri(f"xhsdiscover://user/{title}", PKG)
    rpa.wait_one(text="群聊", timeout=t.page_timeout)
    rpa.sleep(t.sleep_long)
    # 官方 dump：「群聊」TextView depth=6 且不可点，要点包住它的可点行
    _click_label_or_row(rpa, "群聊")
    rpa.sleep(t.sleep_long)
    rpa.wait_one(text="立即加入", timeout=t.page_timeout)
    rpa.sleep(t.sleep_long)
    # 官方 dump：立即加入 Button depth=3
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
    device_name: str = "",
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings, device_name)
    t = timings
    ticket = fetch_phone(api_url)
    cache.phone = ticket.phone
    cache.sms_url = ticket.url

    # 页面由人工切到登录页；有 EditText 就直接官方 input_text 写当前焦点
    if not rpa.exists(class_name="android.widget.EditText"):
        return ScriptResult(True, f"已取号 {ticket.phone}，未找到手机号输入框", cache)
    rpa.input_text(ticket.phone)
    # 填完即成功；不再死等 "Enter code"（中文页/未点 Next 时会误报「未找到控件」）
    return ScriptResult(True, f"已取号 {ticket.phone}", cache)


def run_fetch_code(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    cache: SlotScriptCache,
    device_name: str = "",
) -> ScriptResult:
    if not cache.sms_url:
        raise ExtApiError("请先取号")
    rpa = _rpa(host_ip, api_port, stop, timings, device_name)
    t = timings
    body = fetch_sms_body(cache.sms_url)
    code = extract_sms_code(body)
    if not code:
        raise ExtApiError("未找到验证码")

    # 中文：输入验证码 / 验证手机号码；英文：Verify phone number + EditText
    on_code_page = (
        rpa.exists(text="输入验证码")
        or rpa.exists(text="验证手机号码")
        or rpa.exists(text="Verify phone number")
        or rpa.exists(class_name="android.widget.EditText")
    )
    if not on_code_page:
        raise RpaError("未找到验证码输入框")
    # 与取号一致：不点控件，官方 input_text 写入当前焦点
    rpa.input_text(code)
    rpa.sleep(t.sleep_mid)

    # 对齐原流程：等到生日页 → 下滑 3 次 → 下一步/Next（中英文）
    birthday_labels = ("您的生日是哪天？", "When is your birthday?")
    deadline = time.monotonic() + t.long_timeout
    while time.monotonic() < deadline:
        if any(rpa.exists(text=label) for label in birthday_labels):
            break
        rpa.sleep(t.poll_interval)
    else:
        raise RpaError("未找到生日页")
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
    if rpa.exists(text="下一步"):
        rpa.click_text("下一步", timeout=t.click_timeout)
    else:
        rpa.click_text("Next", timeout=t.click_timeout)
    return ScriptResult(True, f"已填验证码 {code}", cache)


def run_return_phone(
    cache: SlotScriptCache,
    add_url: str = DEFAULT_ADD,
) -> ScriptResult:
    if not cache.phone or not cache.sms_url:
        raise ExtApiError("请先取号")
    raw = return_phone(cache.phone, cache.sms_url, add_url)
    return ScriptResult(True, f"返号结果: {raw[:120]}", cache)


def _click_label_or_row(rpa: DeviceRpa, text: str) -> None:
    """文案节点不可点时，点面积最小且包住它的可点父行。"""
    label = rpa.wait_one(text=text)
    cx, cy = label.center
    best = None
    best_area = None
    for node in rpa.dump_nodes():
        if not node.clickable:
            continue
        x1, y1, x2, y2 = node.bounds
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        area = max(1, (x2 - x1) * (y2 - y1))
        if best is None or area < best_area:
            best = node
            best_area = area
    rpa.click_node(best or label)


def _collection_cards(rpa: DeviceRpa) -> list:
    """官方 dump 合集卡片是可点 ViewGroup depth=5；无障碍才是 depth=19。"""
    nodes = rpa.dump_nodes()
    cards = [
        node
        for node in nodes
        if node.class_name.endswith("ViewGroup")
        and node.clickable
        and node.bounds[1] >= 600
        and (node.bounds[3] - node.bounds[1]) >= 120
        and (node.bounds[2] - node.bounds[0]) >= 400
    ]
    if cards:
        return cards
    return [
        node
        for node in nodes
        if node.class_name.endswith("ViewGroup") and node.depth == 19 and node.clickable
    ]


def _wait_collection_cards(rpa: DeviceRpa, timeout: float):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        cards = _collection_cards(rpa)
        if cards:
            return cards
        rpa.sleep(0.4)
    raise RpaError("未找到合集卡片")


def run_edit_profile(
    host_ip: str,
    api_port: int,
    stop: threading.Event | None,
    timings: ScriptTimings,
    script_content: ScriptContent | None = None,
    device_name: str = "",
) -> ScriptResult:
    rpa = _rpa(host_ip, api_port, stop, timings, device_name)
    t = timings
    rpa.open_uri("xhsdiscover://me/profile", PKG)
    rpa.wait_one(text="简介", timeout=t.page_timeout)
    rpa.sleep(t.sleep_long)
    # 官方 dump 无「修改」文案：简介 TextView depth=3 不可点，可点行是 RelativeLayout depth=2
    _click_label_or_row(rpa, "简介")
    rpa.sleep(t.sleep_long)

    payload = get2_from_local(script_content)
    text1 = str(payload.get("text") or "")
    tags = str(payload.get("tags") or "")
    parts = text1.split("|")
    while len(parts) < 4:
        parts.append("")
    # 不对各段 strip，保留表情与空格
    signature = "\n".join(parts[:4])

    # 官方 dump EditText depth=2；无障碍兜底仍是 depth=10
    try:
        edit = rpa.wait_one(
            class_name="android.widget.EditText", depth=2, timeout=t.short_timeout
        )
    except RpaError:
        edit = rpa.wait_one(
            class_name="android.widget.EditText", depth=10, timeout=t.find_timeout
        )
    rpa.click_node(edit)
    rpa.input_text(signature)
    rpa.sleep(t.sleep_long)
    rpa.click_text("保存", timeout=t.click_timeout)
    rpa.sleep(t.sleep_mid)

    if tags:
        rpa.open_uri(f"xhsdiscover://user/{tags}", PKG)
        rpa.wait_one(text="收藏", timeout=t.page_timeout)
        rpa.sleep(t.sleep_mid)
        # 收藏 Tab 官方 dump 不可点，点包住它的父行
        _click_label_or_row(rpa, "收藏")
        rpa.sleep(t.sleep_mid)
        # 官方 dump 没有「合集 2」文案，等卡片即可
        groups = _wait_collection_cards(rpa, t.page_timeout)
        rpa.sleep(t.sleep_long)
        for node in groups:
            rpa.click_node(node)
            rpa.wait_one(text="收藏合集", timeout=t.click_timeout)
            rpa.sleep(t.sleep_mid)
            _click_label_or_row(rpa, "收藏合集")
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
    script_content: ScriptContent | None = None,
    device_name: str = "",
) -> ScriptResult:
    t = timings or ScriptTimings()
    content = script_content or ScriptContent()
    name = (device_name or "").strip()
    try:
        if action == "home":
            return run_home(host_ip, api_port, stop, t, name)
        if action == "comment":
            return run_comment(host_ip, api_port, stop, t, content, name)
        if action == "switch":
            return run_switch_account(host_ip, api_port, stop, t, name)
        if action == "join":
            return run_join_group(host_ip, api_port, stop, t, content, name)
        if action == "phone":
            return run_fetch_phone(host_ip, api_port, stop, t, cache, device_name=name)
        if action == "code":
            return run_fetch_code(host_ip, api_port, stop, t, cache, name)
        if action == "return_phone":
            return run_return_phone(cache)
        if action == "edit":
            return run_edit_profile(host_ip, api_port, stop, t, content, name)
        raise RpaError(f"未知动作 {action}")
    except (RpaError, ExtApiError) as exc:
        return ScriptResult(False, str(exc), cache)
    except Exception as exc:
        return ScriptResult(False, f"脚本异常: {exc}", cache)
