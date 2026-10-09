"""云机 RPA：经主机 9082 映射口 /modifydev 执行 shell、dump、点击、滑动、输入。"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import unquote

import httpx

DUMP_PATH = "/sdcard/uidump.xml"
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class RpaError(Exception):
    pass


@dataclass(frozen=True)
class UiNode:
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    clickable: bool
    depth: int
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2


def parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = BOUNDS_RE.fullmatch((raw or "").strip())
    if not match:
        return None
    return tuple(int(match.group(i)) for i in range(1, 5))  # type: ignore[return-value]


def parse_ui_dump(xml_text: str) -> list[UiNode]:
    text = (xml_text or "").strip()
    if not text:
        return []
    # uiautomator dump 有时带无关前缀
    start = text.find("<")
    if start > 0:
        text = text[start:]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RpaError(f"UI dump 解析失败: {exc}") from exc
    nodes: list[UiNode] = []

    def walk(elem: ET.Element, depth: int) -> None:
        bounds = parse_bounds(elem.attrib.get("bounds", ""))
        if bounds is not None:
            nodes.append(
                UiNode(
                    text=elem.attrib.get("text") or "",
                    content_desc=elem.attrib.get("content-desc") or "",
                    resource_id=elem.attrib.get("resource-id") or "",
                    class_name=elem.attrib.get("class") or "",
                    clickable=str(elem.attrib.get("clickable") or "").lower() == "true",
                    depth=depth,
                    bounds=bounds,
                )
            )
        for child in list(elem):
            walk(child, depth + 1)

    # hierarchy 本身一般无 bounds；子节点从 depth=0 起，贴近 Auto.js depth()
    if root.tag == "hierarchy":
        for child in list(root):
            walk(child, 0)
    else:
        walk(root, 0)
    return nodes


def _id_match(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    # dump 里常见 com.xingin.xhs:id/xxx，脚本里写 xxx
    if actual.endswith("/" + expected) or actual.endswith(":id/" + expected):
        return True
    return False


def find_nodes(
    nodes: list[UiNode],
    *,
    text: str | None = None,
    desc: str | None = None,
    resource_id: str | None = None,
    class_name: str | None = None,
    clickable: bool | None = None,
    depth: int | None = None,
) -> list[UiNode]:
    result: list[UiNode] = []
    for node in nodes:
        if text is not None and node.text != text:
            continue
        if desc is not None and node.content_desc != desc:
            continue
        if resource_id is not None and not _id_match(node.resource_id, resource_id):
            continue
        if class_name is not None and node.class_name != class_name:
            continue
        if clickable is not None and node.clickable != clickable:
            continue
        if depth is not None and node.depth != depth:
            continue
        result.append(node)
    return result


def deep_link_cmd(uri: str, package: str = "com.xingin.xhs") -> str:
    safe_uri = uri.replace('"', '\\"')
    return (
        f'am start -a android.intent.action.VIEW -d "{safe_uri}" '
        f"-p {package}"
    )


def extract_sms_code(text: str) -> str | None:
    match = re.search(r"\d{6}", text or "")
    return match.group(0) if match else None


def sleep_with_stop(stop_event: threading.Event | None, seconds: float) -> None:
    end = time.monotonic() + max(0.0, seconds)
    while True:
        if stop_event is not None and stop_event.is_set():
            raise RpaError("已停止")
        remain = end - time.monotonic()
        if remain <= 0:
            return
        time.sleep(min(0.2, remain))


class DeviceRpa:
    """对单台云机的 RPA 会话（主机 IP + 9082 映射端口）。"""

    def __init__(
        self,
        host_ip: str,
        api_port: int,
        stop_event: threading.Event | None = None,
        timeout: float = 20.0,
        poll_interval: float = 0.2,
    ) -> None:
        self.host_ip = host_ip
        self.api_port = int(api_port)
        self.stop_event = stop_event
        self.timeout = timeout
        self.poll_interval = max(0.05, float(poll_interval))

    def _check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise RpaError("已停止")

    def _modifydev(self, data: dict, method: str = "POST") -> dict:
        self._check_stop()
        url = f"http://{self.host_ip}:{self.api_port}/modifydev"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=3.0),
                trust_env=False,
            ) as client:
                if method.upper() == "GET":
                    response = client.get(url, params=data)
                else:
                    response = client.post(url, data=data)
        except httpx.HTTPError as exc:
            raise RpaError(f"{exc} (目标 {self.host_ip}:{self.api_port})") from exc
        if response.status_code >= 400:
            raise RpaError(f"HTTP {response.status_code} (目标 {self.host_ip}:{self.api_port})")
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError:
            return {"raw": response.text}
        if not isinstance(payload, dict):
            return {"raw": payload}
        code = payload.get("code")
        if code not in (200, "200", 0, "0", None):
            raise RpaError(
                str(
                    payload.get("msg")
                    or payload.get("message")
                    or payload.get("error")
                    or payload.get("reason")
                    or "modifydev 失败"
                )
            )
        return payload

    @staticmethod
    def _decode_ret(payload: dict) -> str:
        for key in ("ret", "data", "output", "result", "raw"):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                nested = value.get("output") or value.get("result") or value.get("ret")
                if nested is None:
                    continue
                value = nested
            text = str(value)
            if not text:
                continue
            # MYT shell 输出常在 ret 里做 percent-encoding
            if "%" in text:
                try:
                    return unquote(text)
                except Exception:
                    return text
            return text
        return ""

    def shell(self, cmdline: str) -> str:
        payload = self._modifydev({"cmd": 6, "cmdline": cmdline}, method="POST")
        return self._decode_ret(payload)

    def download_file(self, remote_path: str) -> str:
        self._check_stop()
        url = f"http://{self.host_ip}:{self.api_port}/download"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=3.0),
                trust_env=False,
            ) as client:
                response = client.get(url, params={"path": remote_path})
        except httpx.HTTPError as exc:
            raise RpaError(f"{exc} (下载 {remote_path})") from exc
        if response.status_code >= 400:
            raise RpaError(f"下载失败 HTTP {response.status_code}: {remote_path}")
        return response.text

    def sleep(self, seconds: float) -> None:
        sleep_with_stop(self.stop_event, seconds)

    def open_uri(self, uri: str, package: str = "com.xingin.xhs") -> None:
        self.shell(deep_link_cmd(uri, package))

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(
            f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}"
        )

    def keyevent(self, code: int) -> None:
        self.shell(f"input keyevent {int(code)}")

    def back(self) -> None:
        self.keyevent(4)

    def dump_nodes(self) -> list[UiNode]:
        self.shell(f"uiautomator dump {DUMP_PATH}")
        output = ""
        try:
            output = self.download_file(DUMP_PATH)
        except RpaError:
            output = self.shell(f"cat {DUMP_PATH}")
            if "<hierarchy" not in output and "<node" not in output:
                output = self.shell(f"toybox cat {DUMP_PATH}")
        if "<hierarchy" not in output and "<node" not in output:
            raise RpaError("UI dump 为空或不可读")
        return parse_ui_dump(output)

    def find(
        self,
        *,
        text: str | None = None,
        desc: str | None = None,
        resource_id: str | None = None,
        class_name: str | None = None,
        clickable: bool | None = None,
        depth: int | None = None,
    ) -> list[UiNode]:
        return find_nodes(
            self.dump_nodes(),
            text=text,
            desc=desc,
            resource_id=resource_id,
            class_name=class_name,
            clickable=clickable,
            depth=depth,
        )

    def wait_nodes(
        self,
        *,
        timeout: float = 20.0,
        interval: float | None = None,
        **filters,
    ) -> list[UiNode]:
        gap = self.poll_interval if interval is None else max(0.05, float(interval))
        end = time.monotonic() + timeout
        last_error = "未找到控件"
        while time.monotonic() < end:
            self._check_stop()
            try:
                found = self.find(**filters)
                if found:
                    return found
            except RpaError as exc:
                last_error = str(exc)
            sleep_with_stop(self.stop_event, gap)
        raise RpaError(last_error)

    def wait_one(self, **kwargs) -> UiNode:
        return self.wait_nodes(**kwargs)[0]

    def exists(self, **filters) -> bool:
        try:
            return bool(self.find(**filters))
        except RpaError:
            return False

    def click_node(self, node: UiNode, offset_x: int = 0, offset_y: int = 0) -> None:
        x, y = node.center
        self.tap(x + offset_x, y + offset_y)

    def click_text(self, text: str, timeout: float = 20.0, **extra) -> None:
        node = self.wait_one(text=text, timeout=timeout, **extra)
        self.click_node(node)

    def click_desc(self, desc: str, timeout: float = 20.0, **extra) -> None:
        node = self.wait_one(desc=desc, timeout=timeout, **extra)
        self.click_node(node)

    def set_clipboard(self, text: str) -> None:
        """写入云机剪贴板：GET /clipboard?cmd=2&text=..."""
        self._check_stop()
        url = f"http://{self.host_ip}:{self.api_port}/clipboard"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=3.0),
                trust_env=False,
            ) as client:
                response = client.get(url, params={"cmd": 2, "text": text or ""})
        except httpx.HTTPError as exc:
            raise RpaError(f"设置剪贴板失败: {exc}") from exc
        if response.status_code >= 400:
            raise RpaError(f"设置剪贴板 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RpaError("剪贴板响应不是 JSON") from exc
        code = payload.get("code") if isinstance(payload, dict) else None
        if code not in (200, "200"):
            raise RpaError(
                str(payload.get("reason") or payload.get("msg") or "设置剪贴板失败")
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and data.get("status") is False:
            raise RpaError("设置剪贴板未成功")

    def paste(self) -> None:
        # KEYCODE_PASTE = 279；部分 ROM 再用 Ctrl+V
        self.keyevent(279)

    def set_text_via_clipboard(self, text: str) -> None:
        # 不额外 sleep，停留时间由脚本按原 Auto.js 控制
        self.set_clipboard(text)
        self.paste()

    def input_ascii(self, text: str) -> None:
        escaped = (text or "").replace(" ", "%s")
        self.shell(f"input text {escaped}")
