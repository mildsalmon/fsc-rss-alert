from __future__ import annotations

from feed_collector.adapter.outbound.sqlite_base import SqliteRepoBase, utc_now_text
from feed_collector.application.port.output.source_state import SourceStatePort
from feed_collector.domain import SourceConfig


class SqliteSourceStateRepo(SqliteRepoBase, SourceStatePort):
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
                (utc_now_text(), source_id),
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
                (utc_now_text(), source_id),
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
