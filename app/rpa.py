"""云机 RPA：点击/滑动/按键/落字/shell/dump 走主机 :8000 /rpa/*。"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import unquote

import httpx

from app.config import API_PORT

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
    text = (xml_text or "").replace("\x00", "").strip()
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
    """对单台云机的 RPA 会话（主机 :8000 /rpa/*，需云机名）。

    api_port 已不再用于 RPA（历史参数，可传 0）。
    """

    def __init__(
        self,
        host_ip: str,
        api_port: int = 0,
        stop_event: threading.Event | None = None,
        timeout: float = 20.0,
        poll_interval: float = 0.2,
        device_name: str = "",
        host_api_port: int = API_PORT,
    ) -> None:
        self.host_ip = host_ip
        self.api_port = int(api_port or 0)  # 兼容旧调用，RPA 不用
        self.device_name = (device_name or "").strip()
        self.host_api_port = int(host_api_port or API_PORT)
        self.stop_event = stop_event
        self.timeout = timeout
        self.poll_interval = max(0.05, float(poll_interval))

    def _check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise RpaError("已停止")

    def _require_name(self) -> str:
        if not self.device_name:
            raise RpaError("缺少云机名，无法调用官方 /rpa 接口")
        return self.device_name

    def _host_rpa(self, path: str, body: dict) -> dict:
        """POST http://{host}:8000/rpa/..."""
        self._check_stop()
        url = f"http://{self.host_ip}:{self.host_api_port}{path}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=3.0),
                trust_env=False,
            ) as client:
                response = client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise RpaError(f"{exc} ({path} {self.host_ip}:{self.host_api_port})") from exc
        if response.status_code >= 400:
            raise RpaError(
                f"{path} HTTP {response.status_code} ({self.host_ip}:{self.host_api_port})"
            )
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise RpaError(f"{path} 返回非 JSON") from exc
        if not isinstance(payload, dict):
            raise RpaError(f"{path} 返回格式错误")
        code = payload.get("code")
        if code not in (200, "200", 0, "0", None):
            raise RpaError(
                str(
                    payload.get("message")
                    or payload.get("msg")
                    or payload.get("error")
                    or payload.get("reason")
                    or f"{path} 失败"
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

    def shell(self, cmdline: str, timeout: int | None = None) -> str:
        body: dict = {"name": self._require_name(), "cmd": cmdline or ""}
        if timeout is not None:
            body["timeout"] = int(timeout)
        payload = self._host_rpa("/rpa/shell", body)
        return self._decode_ret(payload).replace("\x00", "")

    def sleep(self, seconds: float) -> None:
        sleep_with_stop(self.stop_event, seconds)

    def open_uri(self, uri: str, package: str = "com.xingin.xhs") -> None:
        self.shell(deep_link_cmd(uri, package))

    def tap(self, x: int, y: int) -> None:
        self._host_rpa(
            "/rpa/click",
            {"name": self._require_name(), "x": int(x), "y": int(y)},
        )

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None:
        self._host_rpa(
            "/rpa/swipe",
            {
                "name": self._require_name(),
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "duration": int(duration_ms),
            },
        )

    def keyevent(self, code: int) -> None:
        self._host_rpa(
            "/rpa/key",
            {"name": self._require_name(), "code": int(code)},
        )

    def back(self) -> None:
        """KEYCODE_BACK。"""
        self.keyevent(4)

    def home(self) -> None:
        """KEYCODE_HOME。"""
        self.keyevent(3)

    def tasks(self) -> None:
        """KEYCODE_APP_SWITCH：最近任务 / 后台。"""
        self.keyevent(187)

    def input_text(self, text: str) -> None:
        """向当前焦点输入框写入文本（支持中文/表情）。"""
        self._host_rpa(
            "/rpa/input_text",
            {"name": self._require_name(), "text": text or ""},
        )

    def dump_nodes(self) -> list[UiNode]:
        # 优先 /rpa/dump_ui（约 0.15s）；失败再用 /rpa/shell + uiautomator（约 3s）
        try:
            return parse_ui_dump(self._dump_ui_xml())
        except RpaError:
            return parse_ui_dump(self._dump_ui_xml_via_shell())

    def _dump_ui_xml(self) -> str:
        """POST http://{host}:8000/rpa/dump_ui  {"name": 云机名}"""
        payload = self._host_rpa("/rpa/dump_ui", {"name": self._require_name()})
        data = payload.get("data")
        xml_text = ""
        if isinstance(data, dict):
            xml_text = str(data.get("xml") or "")
        elif isinstance(data, str):
            xml_text = data
        xml_text = xml_text.replace("\x00", "")
        if "<hierarchy" not in xml_text and "<node" not in xml_text:
            raise RpaError("dump_ui XML 为空或不可读")
        return xml_text

    def _dump_ui_xml_via_shell(self) -> str:
        """官方 /rpa/shell：uiautomator dump 后 cat 读出 XML。"""
        self.shell(f"uiautomator dump {DUMP_PATH}")
        output = self.shell(f"cat {DUMP_PATH}")
        if "<hierarchy" not in output and "<node" not in output:
            output = self.shell(f"toybox cat {DUMP_PATH}")
        if "<hierarchy" not in output and "<node" not in output:
            raise RpaError("UI dump 为空或不可读")
        return output

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

    def set_text_via_clipboard(self, text: str) -> None:
        """兼容旧调用：现改为官方 input_text。"""
        self.input_text(text)

    def input_ascii(self, text: str) -> None:
        """原方案：shell `input text`（经 /rpa/shell）。空格写成 %s。"""
        escaped = (text or "").replace(" ", "%s")
        self.shell(f"input text {escaped}")
