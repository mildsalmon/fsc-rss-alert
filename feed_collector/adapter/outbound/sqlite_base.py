from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Self


DEFAULT_DB_PATH = "feed.db"


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteRepoBase:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                  id TEXT PRIMARY KEY,
                  name TEXT,
                  mechanism TEXT,
                  parser_version INTEGER,
                  channel_id TEXT,
                  interval_minutes INTEGER,
                  last_attempt_at TEXT,
                  last_success_at TEXT,
                  consecutive_failures INTEGER NOT NULL DEFAULT 0,
                  failure_alert_sent INTEGER NOT NULL DEFAULT 0,
                  last_failure_reason TEXT,
                  last_failure_at TEXT
                )
                """
            )
            self._ensure_column("sources", "last_failure_reason", "TEXT")
            self._ensure_column("sources", "last_failure_at", "TEXT")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_items (
                  source_id TEXT,
                  item_id TEXT,
                  first_seen_at TEXT,
                  PRIMARY KEY (source_id, item_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_id TEXT,
                  item_id TEXT,
                  title TEXT,
                  channel_id TEXT,
                  slack_ts TEXT,
                  sent_at TEXT,
                  status TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_seen_items_source_first_seen
                ON seen_items(source_id, first_seen_at)
                """
            )

    def _ensure_source_row(self, source_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO sources (id) VALUES (?)",
            (source_id,),
        )

    def _ensure_column(self, table_name: str, column_name: str, column_type: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if any(row[1] == column_name for row in rows):
            return
        self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
