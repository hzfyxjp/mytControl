"""主机和云机模型。事件里只保留坑位需要的字段。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

BRIDGE_WEBRTC_PORT = 10008


def webrtc_ports(index_num: int, *, bridge: bool = False) -> tuple[int | None, int | None]:
    """官方 WebRTC 端口：桥接固定 10008；非桥接 30000+(index-1)*100+7/8。"""
    if bridge:
        return BRIDGE_WEBRTC_PORT, BRIDGE_WEBRTC_PORT
    index = int(index_num or 0)
    if index < 1:
        return None, None
    base = 30000 + (index - 1) * 100
    return base + 7, base + 8


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
    webrtc_tcp: int | None = None
    webrtc_udp: int | None = None
    s5_api_port: int | None = None

    @classmethod
    def from_api(cls, item: dict) -> "CloudDevice":
        bindings = item.get("portBindings")
        index_num = int(item.get("indexNum") or 0)
        tcp, udp = webrtc_ports(index_num)
        # 若 docker 映射了对应端口则用 HostPort，否则用文档公式端口
        if tcp is not None:
            mapped_tcp = host_port(bindings, f"{tcp}/tcp")
            if mapped_tcp:
                tcp = mapped_tcp
        if udp is not None:
            mapped_udp = host_port(bindings, f"{udp}/udp")
            if mapped_udp:
                udp = mapped_udp
        return cls(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            status=str(item.get("status") or item.get("state") or "").strip(),
            index_num=index_num,
            video_port=host_port(bindings, "10000/tcp"),
            control_port=host_port(bindings, "10001/udp"),
            webrtc_tcp=tcp,
            webrtc_udp=udp,
            s5_api_port=host_port(bindings, "9082/tcp"),
        )

    def slim(
        self,
        boot_status: int | None = None,
        api_ready: bool | None = None,
    ) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "index_num": self.index_num,
            "video_port": self.video_port,
            "control_port": self.control_port,
            "webrtc_tcp": self.webrtc_tcp,
            "webrtc_udp": self.webrtc_udp,
            "s5_api_port": self.s5_api_port,
            "boot_status": boot_status,
            "api_ready": api_ready,
        }
