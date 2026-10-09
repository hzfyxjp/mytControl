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


def test_id_match_suffix():
    nodes = parse_ui_dump(
        """<?xml version='1.0'?><hierarchy>
        <node class="android.widget.EditText" resource-id="com.xingin.xhs:id/0_resource_name_obfuscated"
         text="" content-desc="" clickable="true" bounds="[10,20][30,40]" />
        </hierarchy>"""
    )
    found = find_nodes(nodes, resource_id="0_resource_name_obfuscated")
    assert len(found) == 1
