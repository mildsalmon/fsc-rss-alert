from __future__ import annotations

import errno
import fcntl
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import IO, Self, Sequence

from feed_collector.application.port.output.channel import ChannelResolverPort
from feed_collector.application.port.output.seen_state import SeenStatePort
from feed_collector.application.port.output.source_state import SourceStatePort
from feed_collector.domain import Item, SourceConfig


DEFAULT_DB_PATH = "feed.db"
DEFAULT_LOCK_PATH = "/tmp/feed_collector.lock"


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


class _SqliteRepoBase:
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
                  failure_alert_sent INTEGER NOT NULL DEFAULT 0
                )
                """
            )
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


class SqliteSourceStateRepo(_SqliteRepoBase, SourceStatePort):
    def ensure_source(self, source: SourceConfig) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO sources (
                  id, name, mechanism, parser_version, interval_minutes
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name = excluded.name,
                  mechanism = excluded.mechanism,
                  parser_version = excluded.parser_version,
                  interval_minutes = excluded.interval_minutes
                """,
                (
                    source.id,
                    source.name,
                    source.mechanism,
                    source.parser_version,
                    source.interval_minutes,
                ),
            )

    def record_attempt(self, source_id: str) -> None:
        with self._conn:
            self._ensure_source_row(source_id)
            self._conn.execute(
                "UPDATE sources SET last_attempt_at = ? WHERE id = ?",
                (_utc_now_text(), source_id),
            )

    def record_success(self, source_id: str) -> None:
        with self._conn:
            self._ensure_source_row(source_id)
            self._conn.execute(
                """
                UPDATE sources
                SET last_success_at = ?, consecutive_failures = 0, failure_alert_sent = 0
                WHERE id = ?
                """,
                (_utc_now_text(), source_id),
            )

    def record_failure(self, source_id: str, reason: str) -> None:
        del reason
        with self._conn:
            self._ensure_source_row(source_id)
            self._conn.execute(
                """
                UPDATE sources
                SET consecutive_failures = consecutive_failures + 1
                WHERE id = ?
                """,
                (source_id,),
            )


class SqliteStateRepo(_SqliteRepoBase, SeenStatePort):
    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        max_seen_items_per_source: int | None = 1000,
    ) -> None:
        self.max_seen_items_per_source = max_seen_items_per_source
        super().__init__(db_path)

    def is_first_run(self, source_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_items WHERE source_id = ? LIMIT 1",
            (source_id,),
        ).fetchone()
        return row is None

    def seen_contains(self, source_id: str, item_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_items WHERE source_id = ? AND item_id = ?",
            (source_id, item_id),
        ).fetchone()
        return row is not None

    def filter_new(self, source_id: str, items: Sequence[Item]) -> list[Item]:
        return [item for item in items if not self.seen_contains(source_id, item.item_id)]

    def replace_baseline(self, source_id: str, items: Sequence[Item]) -> None:
        now = _utc_now_text()
        with self._conn:
            self._conn.execute("DELETE FROM seen_items WHERE source_id = ?", (source_id,))
            self._insert_seen(source_id, [item.item_id for item in items], now)
            self._prune_seen(source_id)

    def mark_seen(self, source_id: str, item_ids: Sequence[str]) -> None:
        if not item_ids:
            return

        now = _utc_now_text()
        with self._conn:
            self._insert_seen(source_id, item_ids, now)
            self._prune_seen(source_id)

    def _insert_seen(self, source_id: str, item_ids: Sequence[str], now: str) -> None:
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO seen_items (source_id, item_id, first_seen_at)
            VALUES (?, ?, ?)
            """,
            [(source_id, item_id, now) for item_id in item_ids],
        )

    def _prune_seen(self, source_id: str) -> None:
        if self.max_seen_items_per_source is None:
            return

        self._conn.execute(
            """
            DELETE FROM seen_items
            WHERE source_id = ?
              AND item_id NOT IN (
                SELECT item_id
                FROM seen_items
                WHERE source_id = ?
                ORDER BY first_seen_at DESC, item_id DESC
                LIMIT ?
              )
            """,
            (source_id, source_id, self.max_seen_items_per_source),
        )


class SqliteChannelRepo(_SqliteRepoBase, ChannelResolverPort):
    def get_channel_id(self, source_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT channel_id FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return row[0]

    def set_channel_id(self, source_id: str, channel_id: str) -> None:
        with self._conn:
            self._ensure_source_row(source_id)
            self._conn.execute(
                "UPDATE sources SET channel_id = ? WHERE id = ?",
                (channel_id, source_id),
            )


@dataclass
class PollLock:
    path: Path
    _handle: IO[str] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> PollLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def try_acquire_poll_lock(lock_path: str | Path = DEFAULT_LOCK_PATH) -> PollLock | None:
    lock = PollLock(Path(lock_path))
    if not lock.acquire():
        return None
    return lock
