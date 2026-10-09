"""主机和云机模型。事件里只保留坑位需要的字段。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


def host_port(bindings: dict | None, key: str) -> int | None:
    entries = (bindings or {}).get(key) or []
    if not entries or not isinstance(entries[0], dict):
        return None
    raw = entries[0].get("HostPort")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class CloudDevice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    status: str = ""
    index_num: int = 0
    video_port: int | None = None
    control_port: int | None = None
    s5_api_port: int | None = None

    @classmethod
    def from_api(cls, item: dict) -> "CloudDevice":
        bindings = item.get("portBindings")
        return cls(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            status=str(item.get("status") or item.get("state") or "").strip(),
            index_num=int(item.get("indexNum") or 0),
            video_port=host_port(bindings, "10000/tcp"),
            control_port=host_port(bindings, "10001/udp"),
            s5_api_port=host_port(bindings, "9082/tcp"),
        )

    def slim(self, boot_status: int | None = None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "index_num": self.index_num,
            "video_port": self.video_port,
            "control_port": self.control_port,
            "s5_api_port": self.s5_api_port,
            "boot_status": boot_status,
        }
