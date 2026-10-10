from collections import deque
import threading
import time

from app.tasks import TaskService


def test_slot_nav_does_not_stack_workers(monkeypatch):
    events = deque()
    service = TaskService(events)
    started = []
    gate = threading.Event()
    calls = []

    class FakeRpa:
        def __init__(self, host_ip, api_port, device_name=""):
            self.device_name = device_name

        def back(self):
            calls.append("back")
            gate.wait(timeout=1.0)

        def home(self):
            calls.append("home")

        def tasks(self):
            calls.append("tasks")

    monkeypatch.setattr("app.rpa.DeviceRpa", FakeRpa)
    real_start = service._pool.start

    def tracking_start(worker):
        started.append(1)
        return real_start(worker)

    monkeypatch.setattr(service._pool, "start", tracking_start)

    assert service.submit_slot_nav(1, "back", "127.0.0.1", 19082, "dev-a") is True
    assert service.submit_slot_nav(1, "home", "127.0.0.1", 19082, "dev-a") is True
    assert service.submit_slot_nav(1, "tasks", "127.0.0.1", 19082, "dev-a") is True
    # 连点三次只起一个 Worker
    assert len(started) == 1
    gate.set()
    deadline = time.time() + 2
    while time.time() < deadline and 1 in service._slot_nav_busy:
        time.sleep(0.02)
    assert 1 not in service._slot_nav_busy
    # 会执行到最后一次 pending（tasks）
    assert "tasks" in calls
