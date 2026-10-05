from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Assignment, HardConstraint, ScheduleState, SoftConstraint


class ScheduleStore:
    def __init__(self, path: str | None = None):
        default = Path(os.getenv("SCHEDULE_DATA_DIR", "data")) / "schedules.sqlite3"
        self.path = Path(path or os.getenv("SCHEDULE_DB_PATH", str(default)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS states (
                  schedule_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request_cache (
                  request_key TEXT PRIMARY KEY, schedule_id TEXT NOT NULL,
                  normalized_query TEXT NOT NULL, resulting_version INTEGER NOT NULL,
                  response TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def load(self, schedule_id: str, week_start: str) -> ScheduleState:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM states WHERE schedule_id=?", (schedule_id,)).fetchone()
        if not row:
            return ScheduleState(schedule_id, week_start)
        payload = json.loads(row[0])
        return ScheduleState(
            payload["schedule_id"], payload["week_start"], payload["version"],
            [Assignment(**a) for a in payload.get("assignments", [])],
            [HardConstraint(**c) for c in payload.get("hard_constraints", [])],
            [SoftConstraint(**c) for c in payload.get("soft_constraints", [])],
            payload.get("status", "draft"),
        )

    def exists(self, schedule_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM states WHERE schedule_id=?", (schedule_id,)).fetchone() is not None

    def save(self, state: ScheduleState) -> None:
        payload = json.dumps(asdict(state), ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            conn.execute("INSERT INTO states VALUES (?,?) ON CONFLICT(schedule_id) DO UPDATE SET payload=excluded.payload", (state.schedule_id, payload))

    @staticmethod
    def request_key(schedule_id: str, query: str) -> str:
        return hashlib.sha256(f"{schedule_id}\n{query}".encode("utf-8")).hexdigest()

    def replay(self, schedule_id: str, query: str, current_version: int) -> dict[str, Any] | None:
        key = self.request_key(schedule_id, query)
        with self.connect() as conn:
            row = conn.execute("SELECT response,resulting_version FROM request_cache WHERE request_key=?", (key,)).fetchone()
        if not row or row[1] != current_version:
            return None
        result = json.loads(row[0])
        result.update({"status": "already_generated", "replayed": True})
        return result

    def cache(self, schedule_id: str, query: str, resulting_version: int, response: dict[str, Any]) -> None:
        key = self.request_key(schedule_id, query)
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO request_cache(request_key,schedule_id,normalized_query,resulting_version,response) VALUES (?,?,?,?,?)",
                (key, schedule_id, query, resulting_version, json.dumps(response, ensure_ascii=False, sort_keys=True)),
            )
