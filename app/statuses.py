"""云机状态。界面和任务都从这里取文案。"""

from __future__ import annotations

RUNNING = "running"
STARTING = "starting"
RESETTING = "resetting"
EXITED = "exited"
CREATED = "created"
DELETING = "deleting"
RESTARTING = "restarting"
INSTALLING = "installing"
SETTING_PROXY = "setting_proxy"
SCRIPTING = "scripting"

PRIORITY_ORDER = (RUNNING, STARTING, RESETTING)

LABELS = {
    RUNNING: "运行中",
    STARTING: "开机队列中",
    RESETTING: "重置中",
    EXITED: "已退出",
    CREATED: "已创建",
    DELETING: "删除中",
    RESTARTING: "重启中",
    INSTALLING: "安装中",
    SETTING_PROXY: "设置代理中",
    SCRIPTING: "脚本执行中",
}

BOOT_READY = 200


def status_label(status: str | None) -> str:
    if not status:
        return "未知"
    return LABELS.get(status.lower(), status)


def priority_rank(status: str | None) -> int | None:
    if not status:
        return None
    try:
        return PRIORITY_ORDER.index(status.lower())
    except ValueError:
        return None


EMPTY_SLOT_HEADING = "【暂无云机】"


def slot_heading(name: str | None, status: str | None, s5_ip: str | None = None) -> str:
    if not name:
        return EMPTY_SLOT_HEADING
    text = f"{name}（{status_label(status)}）"
    proxy = (s5_ip or "").strip()
    if proxy:
        text = f"{text}【S5IP:{proxy}】"
    else:
        text = f"{text}【S5IP:未设置】"
    return text
