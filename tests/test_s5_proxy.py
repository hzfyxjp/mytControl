from app.models import CloudDevice
from app.s5_proxy import (
    instance_proxy_port,
    parse_s5_line,
    parse_s5_pool,
    remove_proxy_lines,
    resolve_s5_api_port,
    take_unique_proxies,
)
from app.statuses import slot_heading


def test_instance_proxy_port_formula():
    assert instance_proxy_port(1) == 10005
    assert instance_proxy_port(2) == 10008
    assert instance_proxy_port(3) == 10011
    assert instance_proxy_port(4) == 10014
    assert instance_proxy_port(5) == 10017


def test_resolve_s5_api_port_prefers_mapped_9082():
    assert resolve_s5_api_port(30001, 1) == 30001
    assert resolve_s5_api_port(None, 1) == 10005
    assert resolve_s5_api_port(0, 2) == 10008


def test_cloud_device_reads_s5_api_port():
    device = CloudDevice.from_api(
        {
            "id": "1",
            "name": "a",
            "status": "running",
            "indexNum": 1,
            "portBindings": {"9082/tcp": [{"HostIp": "", "HostPort": "30001"}]},
        }
    )
    assert device.s5_api_port == 30001
    assert device.slim()["s5_api_port"] == 30001


def test_parse_s5_line():
    item = parse_s5_line("154.12.142.71:6240:AAAA100413:AAAA100413")
    assert item is not None
    assert item.ip == "154.12.142.71"
    assert item.port == 6240
    assert item.usr == "AAAA100413"
    assert item.pwd == "AAAA100413"
    assert parse_s5_line("bad") is None
    assert parse_s5_line("") is None


def test_take_unique_proxies_skips_duplicates():
    text = "\n".join(
        [
            "1.1.1.1:1000:a:b",
            "1.1.1.1:1000:a:b",
            "2.2.2.2:2000:c:d",
            "3.3.3.3:3000:e:f",
        ]
    )
    taken = take_unique_proxies(text, 2)
    assert [item.ip for item in taken] == ["1.1.1.1", "2.2.2.2"]
    assert len(parse_s5_pool(text)) == 3


def test_remove_proxy_lines():
    text = "1.1.1.1:1000:a:b\n2.2.2.2:2000:c:d\n3.3.3.3:3000:e:f"
    updated = remove_proxy_lines(text, ["2.2.2.2:2000:c:d"])
    assert updated == "1.1.1.1:1000:a:b\n3.3.3.3:3000:e:f"


def test_slot_heading_with_s5_ip():
    assert slot_heading("云机A", "running") == "云机A（运行中）【S5IP:未设置】"
    assert slot_heading("云机A", "running", "154.12.142.71") == "云机A（运行中）【S5IP:154.12.142.71】"
    assert slot_heading("云机A", "setting_proxy") == "云机A（设置代理中）【S5IP:未设置】"
