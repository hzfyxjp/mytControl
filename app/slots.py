"""把云机分到 1–5 号坑位。"""

from __future__ import annotations

from app.models import CloudDevice
from app.statuses import priority_rank

SLOT_INDEXES = (1, 2, 3, 4, 5)


def should_fetch_devices(host_ip: str | None, reachable: bool) -> bool:
    return bool((host_ip or "").strip()) and bool(reachable)


def assign_slots(devices: list[CloudDevice]) -> dict[int, CloudDevice | None]:
    grouped: dict[int, list[tuple[int, CloudDevice]]] = {index: [] for index in SLOT_INDEXES}
    for order, device in enumerate(devices):
        if device.index_num not in grouped:
            continue
        grouped[device.index_num].append((order, device))

    assigned: dict[int, CloudDevice | None] = {}
    for index, items in grouped.items():

        def sort_key(item: tuple[int, CloudDevice]) -> tuple[int, int, int]:
            order, device = item
            rank = priority_rank(device.status)
            if rank is None:
                return (1, 0, order)
            return (0, rank, order)

        items.sort(key=sort_key)
        assigned[index] = items[0][1] if items else None
    return assigned
