from __future__ import annotations

from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit

from feed_collector.adapter.outbound.sqlite_base import DEFAULT_DB_PATH, SqliteRepoBase, utc_now_text
from feed_collector.application.port.output.seen_state import SeenStatePort
from feed_collector.domain import Item


class SqliteStateRepo(SqliteRepoBase, SeenStatePort):
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
        return self._seen_contains(source_id, item_id) or self._seen_contains_legacy_http_443(source_id, item_id)

    def _seen_contains(self, source_id: str, item_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_items WHERE source_id = ? AND item_id = ?",
            (source_id, item_id),
        ).fetchone()
        return row is not None

    def _seen_contains_legacy_http_443(self, source_id: str, item_id: str) -> bool:
        legacy_item_id = _legacy_http_443_url(item_id)
        if legacy_item_id == item_id:
            return False
        return self._seen_contains(source_id, legacy_item_id)

    def filter_new(self, source_id: str, items: Sequence[Item]) -> list[Item]:
        return [item for item in items if not self.seen_contains(source_id, item.item_id)]

    def replace_baseline(self, source_id: str, items: Sequence[Item]) -> None:
        now = utc_now_text()
        with self._conn:
            self._conn.execute("DELETE FROM seen_items WHERE source_id = ?", (source_id,))
            self._insert_seen(source_id, [item.item_id for item in items], now)
            self._prune_seen(source_id)

    def mark_seen(self, source_id: str, item_ids: Sequence[str]) -> None:
        if not item_ids:
            return

        now = utc_now_text()
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


def _legacy_http_443_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname is None:
        return value
    try:
        port = parsed.port
    except ValueError:
        return value
    if port not in (None, 443):
        return value
    return urlunsplit(("http", f"{parsed.hostname}:443", parsed.path, parsed.query, parsed.fragment))
