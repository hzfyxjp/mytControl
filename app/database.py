"""SQLite。单连接，访问都拿同一把锁。"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.config import API_PORT, db_path
from app.script_timing import ScriptTimings


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hosts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 8000,
                    api_key TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            self._conn.commit()

    def get_host_ip(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = 'host_ip'"
            ).fetchone()
            if row and str(row["value"]).strip():
                return str(row["value"]).strip()
            if "address" not in self._host_columns():
                return ""
            host = self._conn.execute(
                "SELECT address FROM hosts WHERE enabled = 1 AND address != '' ORDER BY id LIMIT 1"
            ).fetchone()
            return str(host["address"]).strip() if host else ""

    def set_host_ip(self, ip: str) -> None:
        with self._lock:
            self._set_setting_locked("host_ip", ip)
            if "address" not in self._host_columns():
                return
            existing = self._conn.execute(
                "SELECT id FROM hosts WHERE address = ?",
                (ip,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE hosts SET enabled = 1, port = ? WHERE id = ?",
                    (API_PORT, existing["id"]),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO hosts(name, address, port, api_key, enabled)
                    VALUES (?, ?, ?, '', 1)
                    """,
                    (ip, ip, API_PORT),
                )
            self._conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._set_setting_locked(key, value)

    def get_s5_ip_pool(self) -> str:
        return self.get_setting("s5_ip_pool", "")

    def set_s5_ip_pool(self, text: str) -> None:
        self.set_setting("s5_ip_pool", text)

    def get_apk_path(self) -> str:
        return self.get_setting("apk_path", "").strip()

    def set_apk_path(self, path: str) -> None:
        self.set_setting("apk_path", (path or "").strip())

    def get_package_name(self) -> str:
        return self.get_setting("package_name", "com.xingin.xhs").strip() or "com.xingin.xhs"

    def set_package_name(self, name: str) -> None:
        self.set_setting("package_name", (name or "").strip())

    def get_download_port(self) -> str:
        return self.get_setting("download_port", "18888").strip() or "18888"

    def set_download_port(self, port: str) -> None:
        self.set_setting("download_port", (port or "").strip())

    def get_script_timings(self) -> ScriptTimings:
        raw = self.get_setting("script_timings", "")
        if not raw.strip():
            return ScriptTimings()
        try:
            data = json.loads(raw)
        except ValueError:
            return ScriptTimings()
        return ScriptTimings.from_dict(data if isinstance(data, dict) else {})

    def set_script_timings(self, timings: ScriptTimings) -> None:
        self.set_setting("script_timings", json.dumps(timings.to_dict(), ensure_ascii=False))

    def get_slot_s5_ip(self, slot: int) -> str:
        return self.get_setting(f"s5_slot_{slot}", "").strip()

    def set_slot_s5_ip(self, slot: int, proxy_ip: str) -> None:
        self.set_setting(f"s5_slot_{slot}", (proxy_ip or "").strip())

    def clear_slot_s5_ip(self, slot: int) -> None:
        self.set_slot_s5_ip(slot, "")

    def _set_setting_locked(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self._conn.commit()

    def _host_columns(self) -> set[str]:
        return {str(row["name"]) for row in self._conn.execute("PRAGMA table_info(hosts)")}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
