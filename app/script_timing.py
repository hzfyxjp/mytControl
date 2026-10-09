"""脚本停留 / 找控件超时。界面改完立刻生效，当前值为默认。"""

from __future__ import annotations

from dataclasses import asdict, dataclass


def _clamp(value: float, low: float, high: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = low
    return max(low, min(high, num))


@dataclass
class ScriptTimings:
    start_delay: float = 0.1
    sleep_long: float = 2.0
    sleep_mid: float = 1.0
    sleep_short: float = 0.6
    find_timeout: float = 20.0
    page_timeout: float = 25.0
    long_timeout: float = 30.0
    poll_interval: float = 0.2

    @classmethod
    def from_dict(cls, data: dict | None) -> "ScriptTimings":
        raw = data if isinstance(data, dict) else {}
        return cls(
            start_delay=_clamp(raw.get("start_delay", 0.1), 0.0, 10.0),
            sleep_long=_clamp(raw.get("sleep_long", 2.0), 0.0, 60.0),
            sleep_mid=_clamp(raw.get("sleep_mid", 1.0), 0.0, 60.0),
            sleep_short=_clamp(raw.get("sleep_short", 0.6), 0.0, 30.0),
            find_timeout=_clamp(raw.get("find_timeout", 20.0), 1.0, 120.0),
            page_timeout=_clamp(raw.get("page_timeout", 25.0), 1.0, 180.0),
            long_timeout=_clamp(raw.get("long_timeout", 30.0), 1.0, 180.0),
            poll_interval=_clamp(raw.get("poll_interval", 0.2), 0.05, 5.0),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def click_timeout(self) -> float:
        # 原脚本点「发送」等用较短超时，上限跟找控件一致
        return min(15.0, self.find_timeout) if self.find_timeout >= 15.0 else self.find_timeout

    @property
    def short_timeout(self) -> float:
        return min(10.0, self.find_timeout)
