from app.rpa import (
    DeviceRpa,
    deep_link_cmd,
    extract_sms_code,
    find_nodes,
    parse_bounds,
    parse_ui_dump,
)
from app.xhs_scripts import SCRIPT_HANDLERS


SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.xingin.xhs" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,1920]">
    <node index="0" text="发送" resource-id="0_resource_name_obfuscated" class="android.widget.TextView" package="com.xingin.xhs" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[800,1700][1000,1800]" />
    <node index="1" text="" resource-id="0_resource_name_obfuscated" class="android.widget.EditText" package="com.xingin.xhs" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[100,1600][700,1750]" />
    <node index="2" text="hello" resource-id="0_resource_name_obfuscated" class="android.widget.TextView" package="com.xingin.xhs" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[50,200][500,260]" />
    <node index="3" text="" resource-id="" class="android.view.View" package="com.xingin.xhs" content-desc="设置" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[900,80][1040,160]" />
  </node>
</hierarchy>
"""


def test_parse_bounds():
    assert parse_bounds("[10,20][30,40]") == (10, 20, 30, 40)
    assert parse_bounds("bad") is None


def test_parse_ui_dump_and_find():
    nodes = parse_ui_dump(SAMPLE_XML)
    assert len(nodes) >= 4
    send = find_nodes(nodes, text="发送")
    assert len(send) == 1
    assert send[0].center == (900, 1750)
    settings = find_nodes(nodes, desc="设置")
    assert len(settings) == 1
    # 对齐原脚本：混淆 id + depth + clickable
    depth_child = find_nodes(nodes, resource_id="0_resource_name_obfuscated", depth=1, clickable=True)
    assert any(n.class_name == "android.widget.EditText" for n in depth_child)
    replies = find_nodes(
        nodes,
        resource_id="0_resource_name_obfuscated",
        class_name="android.widget.TextView",
        clickable=False,
        depth=1,
    )
    assert len(replies) == 1
    assert replies[0].text == "hello"


def test_deep_link_cmd():
    cmd = deep_link_cmd("xhsdiscover://profile")
    assert "am start" in cmd
    assert "xhsdiscover://profile" in cmd
    assert "com.xingin.xhs" in cmd


def test_extract_sms_code():
    assert extract_sms_code("您的验证码是131839，5分钟内有效") == "131839"
    assert extract_sms_code("no code") is None


def test_script_handler_labels():
    assert SCRIPT_HANDLERS["home"] == "主页"
    assert SCRIPT_HANDLERS["return_phone"] == "返号"
    assert SCRIPT_HANDLERS["stop"] == "停止"


def test_decode_ret_percent_encoding():
    assert DeviceRpa._decode_ret({"code": 200, "ret": "hello_rpa%0A"}) == "hello_rpa\n"
    assert DeviceRpa._decode_ret({"data": "plain"}) == "plain"


def test_dump_nodes_uses_host_rpa_dump_ui(monkeypatch):
    xml = SAMPLE_XML + "\x00"
    calls: list[tuple] = []

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"code": 0, "message": "OK", "data": {"xml": xml}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            calls.append((url, json))
            return _Resp()

    monkeypatch.setattr("app.rpa.httpx.Client", _Client)
    rpa = DeviceRpa("192.168.0.105", 30101, device_name="cloud-a")
    nodes = rpa.dump_nodes()
    assert calls == [
        ("http://192.168.0.105:8000/rpa/dump_ui", {"name": "cloud-a"})
    ]
    assert find_nodes(nodes, text="发送")


def test_dump_nodes_falls_back_to_rpa_shell(monkeypatch):
    calls: list[tuple] = []

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.content = b"{}"

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            calls.append((url, json))
            if url.endswith("/rpa/dump_ui"):
                return _Resp({"code": 1, "message": "fail"})
            cmd = (json or {}).get("cmd") or ""
            if "uiautomator dump" in cmd:
                return _Resp({"code": 0, "data": {"output": "UI hierchary dumped to: /sdcard/uidump.xml"}})
            if "cat " in cmd:
                return _Resp({"code": 0, "data": {"output": SAMPLE_XML}})
            return _Resp({"code": 0, "data": {"output": ""}})

    monkeypatch.setattr("app.rpa.httpx.Client", _Client)
    rpa = DeviceRpa("192.168.0.105", device_name="cloud-a")
    nodes = rpa.dump_nodes()
    assert any(url.endswith("/rpa/dump_ui") for url, _ in calls)
    assert any(url.endswith("/rpa/shell") for url, _ in calls)
    assert find_nodes(nodes, text="发送")


def test_official_click_swipe_key_input(monkeypatch):
    calls: list[tuple] = []

    class _Resp:
        status_code = 200
        content = b'{"code":0}'

        def json(self):
            return {"code": 0, "message": "OK", "data": {}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            calls.append((url, json))
            return _Resp()

    monkeypatch.setattr("app.rpa.httpx.Client", _Client)
    rpa = DeviceRpa("192.168.0.105", 30101, device_name="cloud-a")
    rpa.tap(10, 20)
    rpa.swipe(1, 2, 3, 4, 350)
    rpa.keyevent(4)
    rpa.input_text("你好😊")
    assert calls == [
        ("http://192.168.0.105:8000/rpa/click", {"name": "cloud-a", "x": 10, "y": 20}),
        (
            "http://192.168.0.105:8000/rpa/swipe",
            {
                "name": "cloud-a",
                "x1": 1,
                "y1": 2,
                "x2": 3,
                "y2": 4,
                "duration": 350,
            },
        ),
        ("http://192.168.0.105:8000/rpa/key", {"name": "cloud-a", "code": 4}),
        (
            "http://192.168.0.105:8000/rpa/input_text",
            {"name": "cloud-a", "text": "你好😊"},
        ),
    ]


def test_shell_uses_host_rpa(monkeypatch):
    calls: list[tuple] = []

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"code": 0, "data": {"output": "ok%0A\x00", "exitCode": 0}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            calls.append((url, json))
            return _Resp()

    monkeypatch.setattr("app.rpa.httpx.Client", _Client)
    rpa = DeviceRpa("192.168.0.105", device_name="cloud-a")
    assert rpa.shell("echo ok") == "ok\n"
    assert calls == [
        (
            "http://192.168.0.105:8000/rpa/shell",
            {"name": "cloud-a", "cmd": "echo ok"},
        )
    ]


def test_id_match_suffix():
    nodes = parse_ui_dump(
        """<?xml version='1.0'?><hierarchy>
        <node class="android.widget.EditText" resource-id="com.xingin.xhs:id/0_resource_name_obfuscated"
         text="" content-desc="" clickable="true" bounds="[10,20][30,40]" />
        </hierarchy>"""
    )
    found = find_nodes(nodes, resource_id="0_resource_name_obfuscated")
    assert len(found) == 1
