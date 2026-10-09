import threading
import zipfile
from http.client import HTTPConnection
from pathlib import Path

from app.apk_install import (
    apk_file_info,
    batch_install_failures,
    batch_install_is_success,
    batch_install_successes,
    ensure_apk_server,
    install_named_devices,
    shutdown_apk_server,
)


def test_batch_install_reads_failed_map_by_container_name():
    result = {
        "data": {
            "status": "completed",
            "failedMap": {"DY-1|app.apk": "安装失败"},
        }
    }
    assert batch_install_failures(result) == {"DY-1": "安装失败"}


def test_batch_install_success_requires_every_target():
    result = {
        "data": {
            "status": "completed",
            "total": 2,
            "processed": 2,
            "failedCount": 0,
            "successNames": ["DY-1|app.apk", "DY-2|app.apk"],
            "failedMap": {},
        }
    }
    assert batch_install_successes(result) == {"DY-1", "DY-2"}
    assert batch_install_is_success(result, 2)
    assert not batch_install_is_success(result, 3)


def test_local_apk_server_serves_file_without_loading_it(tmp_path: Path, monkeypatch):
    apk = tmp_path / "demo.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
    _size, file_key = apk_file_info(apk)
    monkeypatch.setattr("app.apk_install.reachable_local_ipv4", lambda ip: "127.0.0.1")
    monkeypatch.setattr(
        "app.apk_install.batch_install_apk",
        lambda ip, urls, names: {"data": {"taskId": "task-1", "apk": urls, "names": names}},
    )
    monkeypatch.setattr(
        "app.apk_install.task_status",
        lambda ip, task_id: {
            "data": {
                "status": "completed",
                "total": 1,
                "processed": 1,
                "failedCount": 0,
                "successNames": ["云机A|demo.apk"],
                "failedMap": {},
            }
        },
    )
    try:
        failed = install_named_devices(
            "192.168.0.105",
            ["云机A"],
            apk,
            "com.xingin.xhs",
            18889,
            threading.Event(),
        )
        assert failed == []
        server = ensure_apk_server(18889)
        url_path = f"/apk/{file_key}/demo.apk"
        connection = HTTPConnection("127.0.0.1", 18889, timeout=3)
        connection.request("GET", url_path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        assert response.status == 200
        assert body == apk.read_bytes()
        assert server.files[url_path] == apk
    finally:
        shutdown_apk_server()
