import sqlite3
import threading
from pathlib import Path

from app.config import parse_ipv4
from app.database import Database
from app.models import CloudDevice, webrtc_ports
from app.slots import assign_slots, should_fetch_devices
from app.statuses import slot_heading
from app.tasks import collect_refresh


def _device(name: str, status: str, index_num: int, device_id: str = "") -> CloudDevice:
    return CloudDevice(id=device_id or name, name=name, status=status, index_num=index_num)


def test_priority_statuses_come_first():
    devices = [
        _device("start", "starting", 2),
        _device("exit", "exited", 2),
        _device("reset", "resetting", 2),
        _device("run", "running", 2),
    ]
    assert assign_slots(devices)[2].name == "run"


def test_non_priority_keeps_api_order():
    devices = [
        _device("created-first", "created", 3),
        _device("exited-later", "exited", 3),
    ]
    assert assign_slots(devices)[3].name == "created-first"


def test_starting_before_resetting():
    devices = [
        _device("reset", "resetting", 4),
        _device("start", "starting", 4),
    ]
    assert assign_slots(devices)[4].name == "start"


def test_status_match_is_case_insensitive():
    devices = [
        _device("later", "exited", 1),
        _device("first", "Running", 1),
    ]
    assert assign_slots(devices)[1].name == "first"


def test_same_priority_keeps_api_order():
    devices = [
        _device("first-running", "running", 1),
        _device("second-running", "running", 1),
    ]
    assert assign_slots(devices)[1].name == "first-running"


def test_empty_slot_and_out_of_range_indexes():
    devices = [
        _device("slot1", "exited", 1),
        _device("ignored-zero", "running", 0),
        _device("ignored-six", "running", 6),
        _device("slot5", "created", 5),
    ]
    assigned = assign_slots(devices)
    assert assigned[1].name == "slot1"
    assert assigned[2] is None
    assert assigned[3] is None
    assert assigned[4] is None
    assert assigned[5].name == "slot5"
    assert all(device.name not in {"ignored-zero", "ignored-six"} for device in assigned.values() if device)


def test_should_fetch_only_when_configured_and_reachable():
    assert should_fetch_devices("", True) is False
    assert should_fetch_devices(None, True) is False
    assert should_fetch_devices("192.168.0.105", False) is False
    assert should_fetch_devices("192.168.0.105", True) is True


def test_parse_ipv4():
    assert parse_ipv4(" 192.168.0.105 ") == "192.168.0.105"
    assert parse_ipv4("") is None
    assert parse_ipv4("192.168.0.105:8000") is None
    assert parse_ipv4("box.local") is None


def test_from_api_reads_mirror_ports():
    device = CloudDevice.from_api(
        {
            "id": "abc",
            "name": "1005052135-1-2",
            "status": "exited",
            "indexNum": 2,
            "portBindings": {
                "10000/tcp": [{"HostPort": "30103"}],
                "10001/udp": [{"HostPort": "30104"}],
            },
            "dataPath": "/mmc/data/should-not-matter",
        }
    )
    assert device.slim()["video_port"] == 30103
    assert device.slim()["control_port"] == 30104
    assert "dataPath" not in device.slim()


def test_webrtc_ports_by_index():
    assert webrtc_ports(1) == (30007, 30008)
    assert webrtc_ports(3) == (30207, 30208)
    assert webrtc_ports(0) == (None, None)
    assert webrtc_ports(1, bridge=True) == (10008, 10008)


def test_slot_heading():
    assert slot_heading(None, None) == "【暂无云机】"
    assert slot_heading("云机A", "running") == "云机A（运行中）【S5IP:未设置】"
    assert slot_heading("云机A", "starting") == "云机A（开机队列中）【S5IP:未设置】"
    assert slot_heading("云机A", "resetting") == "云机A（重置中）【S5IP:未设置】"
    assert slot_heading("云机A", "deleting") == "云机A（删除中）【S5IP:未设置】"
    assert slot_heading("云机A", "restarting") == "云机A（重启中）【S5IP:未设置】"
    assert slot_heading("云机A", "installing") == "云机A（安装中）【S5IP:未设置】"


def test_unreachable_host_does_not_list_devices(monkeypatch):
    monkeypatch.setattr("app.tasks.probe_host", lambda ip: False)

    def _fail(ip):
        raise AssertionError(ip)

    monkeypatch.setattr("app.tasks.list_devices", _fail)
    event = collect_refresh("192.168.0.105", set(), threading.Event(), 0)
    assert event is not None
    assert event.kind == "host"
    assert event.data["state"] == "down"


def test_reachable_host_lists_and_assigns(monkeypatch):
    monkeypatch.setattr("app.tasks.probe_host", lambda ip: True)
    monkeypatch.setattr(
        "app.tasks.list_devices",
        lambda ip: [_device("top", "exited", 1), _device("live", "running", 1)],
    )
    event = collect_refresh("192.168.0.105", set(), threading.Event(), 3)
    assert event is not None
    assert event.kind == "devices"
    assert event.data["slots"][1]["name"] == "live"
    assert event.data["slots"][1]["boot_status"] is None
    assert event.data["slots"][1]["api_ready"] is None
    assert event.data["epoch"] == 3


def test_refresh_probes_api_for_pending_restart_names(monkeypatch):
    from app.models import CloudDevice

    monkeypatch.setattr("app.tasks.probe_host", lambda ip: True)
    device = CloudDevice(
        id="d1",
        name="live",
        status="running",
        index_num=1,
        video_port=10000,
        control_port=10001,
        s5_api_port=19082,
    )
    monkeypatch.setattr("app.tasks.list_devices", lambda ip: [device])
    monkeypatch.setattr("app.tasks.device_api_is_available", lambda ip, port, timeout=2.0: True)
    event = collect_refresh("192.168.0.105", {"live"}, threading.Event(), 1)
    assert event is not None
    assert event.data["slots"][1]["api_ready"] is True


def test_host_ip_roundtrip(tmp_path: Path):
    database = Database(tmp_path / "moyunteng.sqlite3")
    assert database.get_host_ip() == ""
    database.set_host_ip("192.168.0.105")
    assert database.get_host_ip() == "192.168.0.105"
    database.close()
    reopened = Database(tmp_path / "moyunteng.sqlite3")
    assert reopened.get_host_ip() == "192.168.0.105"
    reopened.close()


def test_host_ip_reads_existing_address_column(tmp_path: Path):
    path = tmp_path / "moyunteng.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 8000,
            api_key TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO hosts(name, address, port, enabled) VALUES ('box', '192.168.0.105', 8000, 1)"
    )
    conn.commit()
    conn.close()
    database = Database(path)
    assert database.get_host_ip() == "192.168.0.105"
    database.set_host_ip("192.168.0.106")
    assert database.get_host_ip() == "192.168.0.106"
    database.close()


def test_phone_height_follows_9_16_width():
    from app.main_window import phone_height_for_width

    assert phone_height_for_width(180) == 320
    assert phone_height_for_width(90) == 160
    assert phone_height_for_width(9) == 16


def test_process_thread_count_at_least_one():
    from app.config import process_thread_count

    assert process_thread_count() >= 1


def test_batch_only_uses_checked_named_slots():
    from app.main_window import selected_batch_names

    names = selected_batch_names(
        [
            (True, "a"),
            (False, "b"),
            (True, ""),
            (True, "c"),
            (False, ""),
        ]
    )
    assert names == ["a", "c"]


def test_apply_device_skips_unchanged_snapshot():
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PySide6.QtWidgets import QApplication

    from app.main_window import SlotPanel

    app = QApplication.instance() or QApplication([])
    panel = SlotPanel(1)
    payload = {
        "id": "d1",
        "name": "n1",
        "status": "running",
        "s5_api_port": 19082,
        "webrtc_tcp": 30007,
        "webrtc_udp": 30008,
        "index_num": 1,
    }
    assert panel.apply_device(payload) is True
    assert panel.apply_device(payload) is False
    assert panel.apply_device({**payload, "status": "exited"}) is True
    panel.deleteLater()
    _ = app


def test_webrtc_ports_follow_official_formula():
    from app.models import webrtc_ports

    assert webrtc_ports(1) == (30007, 30008)
    assert webrtc_ports(2) == (30107, 30108)
    assert webrtc_ports(12) == (31107, 31108)
    assert webrtc_ports(1, bridge=True) == (10008, 10008)
    assert webrtc_ports(0) == (None, None)


def test_device_slim_includes_webrtc_ports():
    device = CloudDevice(
        id="d1",
        name="live",
        status="running",
        index_num=3,
        webrtc_tcp=30207,
        webrtc_udp=30208,
        s5_api_port=19082,
    )
    slim = device.slim()
    assert slim["webrtc_tcp"] == 30207
    assert slim["webrtc_udp"] == 30208
    assert slim["index_num"] == 3


def test_mirror_cooldown_blocks_ensure_until_expired(monkeypatch):
    import time

    from app.screen_mirror import ScreenMirrors

    class _View:
        pass

    mirrors = ScreenMirrors()
    mirrors._cooldown_until[2] = time.monotonic() + 30
    assert mirrors.in_cooldown(2) is True
    assert mirrors.ensure(2, "d2", "n2", "127.0.0.1", 30007, 30008, _View()) == "cooldown"
    mirrors.forget_skip(2)
    assert mirrors.in_cooldown(2) is False
    monkeypatch.setattr(
        "app.screen_mirror._tcp_listening",
        lambda host, port, timeout=0.35: False,
    )
    assert mirrors.ensure(2, "d2", "n2", "127.0.0.1", 30007, 30008, _View()) == "cooldown"
    assert mirrors.in_cooldown(2) is True


def test_mirror_probe_waiting_reports_status():
    from app.screen_mirror import MirrorSession, ScreenMirrors

    class _Page:
        def __init__(self, result):
            self._result = result

        def runJavaScript(self, _js, callback):
            callback(self._result)

    class _View:
        def __init__(self, result):
            self._page = _Page(result)

        def page(self):
            return self._page

    mirrors = ScreenMirrors()
    mirrors._sessions[1] = MirrorSession(
        slot=1, device_id="d1", host_ip="127.0.0.1", tcp_port=1, udp_port=2
    )
    mirrors._views[1] = _View("waiting")
    seen = []
    mirrors.probe_waiting(1, lambda slot, status: seen.append((slot, status)))
    assert seen == [(1, "waiting")]

    mirrors._views[1] = _View("closing")
    seen.clear()
    mirrors.probe_waiting(1, lambda slot, status: seen.append((slot, status)))
    assert seen == [(1, "closing")]

    mirrors._sessions.clear()
    seen.clear()
    mirrors.probe_waiting(1, lambda slot, status: seen.append((slot, status)))
    assert seen == [(1, "none")]


def test_mirror_ready_blocks_when_api_not_ready():
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PySide6.QtWidgets import QApplication

    from app.main_window import SlotPanel

    app = QApplication.instance() or QApplication([])
    panel = SlotPanel(3)
    panel.apply_device(
        {
            "id": "d3",
            "name": "n3",
            "status": "running",
            "s5_api_port": 19082,
            "webrtc_tcp": 30207,
            "webrtc_udp": 30208,
            "index_num": 3,
            "api_ready": False,
        }
    )
    assert panel.can_mirror() is True
    assert panel.mirror_ready() is False
    panel.deleteLater()
    _ = app
