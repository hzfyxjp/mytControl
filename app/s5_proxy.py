"""S5 代理行解析与实例端口公式。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class S5Proxy:
    line: str
    ip: str
    port: int
    usr: str
    pwd: str


def instance_proxy_port(index: int) -> int:
    """共享 IP 非网桥：实例 N 的代理接口端口 = (N-1)*3 + 10005。"""
    if index < 1:
        raise ValueError(index)
    return (index - 1) * 3 + 10005


def resolve_s5_api_port(mapped_9082: int | None, instance_index: int) -> int:
    """优先用 portBindings 里 9082/tcp 的主机映射；没有才退回共享 IP 公式。"""
    if mapped_9082 is not None and 1 <= int(mapped_9082) <= 65535:
        return int(mapped_9082)
    return instance_proxy_port(instance_index)


def parse_s5_line(line: str) -> S5Proxy | None:
    text = (line or "").strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split(":")
    if len(parts) < 4:
        return None
    ip, port_text, usr, pwd = parts[0].strip(), parts[1].strip(), parts[2], ":".join(parts[3:])
    if not ip or not port_text.isdigit() or not usr:
        return None
    port = int(port_text)
    if port < 1 or port > 65535:
        return None
    return S5Proxy(line=text, ip=ip, port=port, usr=usr, pwd=pwd)


def parse_s5_pool(text: str) -> list[S5Proxy]:
    result: list[S5Proxy] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        item = parse_s5_line(raw)
        if item is None or item.line in seen:
            continue
        seen.add(item.line)
        result.append(item)
    return result


def take_unique_proxies(text: str, count: int) -> list[S5Proxy]:
    if count <= 0:
        return []
    pool = parse_s5_pool(text)
    return pool[:count]


def remove_proxy_lines(text: str, lines: list[str]) -> str:
    remove = {str(item).strip() for item in lines if str(item).strip()}
    kept: list[str] = []
    for raw in (text or "").splitlines():
        if raw.strip() in remove:
            continue
        kept.append(raw)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)
