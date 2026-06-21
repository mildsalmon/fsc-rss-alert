from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_collector.adapter.outbound.sqlite_base import DEFAULT_DB_PATH, SqliteRepoBase, utc_now_text
from feed_collector.application.port.output.audit import AuditPort
from feed_collector.domain import Item


class SqliteAuditLog(SqliteRepoBase, AuditPort):
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, *, retention_days: int = 90) -> None:
        self.retention_days = retention_days
        super().__init__(db_path)

    def log(
        self,
        source_id: str,
        item: Item,
        *,
        channel_id: str | None = None,
        delivery_id: str | None = None,
        status: str = "sent",
    ) -> None:
        self.log_delivery(
            source_id,
            item,
            channel_id=channel_id,
            slack_ts=delivery_id,
            status=status,
        )

    def log_sent_delivery(
        self,
        source_id: str,
        item: Item,
        *,
        channel_id: str,
        delivery_id: str | None = None,
    ) -> None:
        self.log_delivery(
            source_id,
            item,
            channel_id=channel_id,
            slack_ts=delivery_id,
            status="sent",
        )

    def log_delivery(
        self,
        source_id: str,
        item: Item,
        *,
        channel_id: str | None = None,
        slack_ts: str | None = None,
        status: str = "sent",
        sent_at: str | None = None,
    ) -> None:
        with self._conn:
            self._ensure_source_row(source_id)
            self._conn.execute(
                """
                INSERT INTO audit_log (
                  source_id, item_id, title, channel_id, slack_ts, sent_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    item.item_id,
                    item.title,
                    channel_id,
                    slack_ts,
                    sent_at or utc_now_text(),
                    status,
                ),
            )
            self.prune()

    def prune(self, *, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.retention_days)
        cutoff_text = cutoff.isoformat()
        with self._conn:
            self._conn.execute(
                "DELETE FROM audit_log WHERE sent_at < ?",
                (cutoff_text,),
            )
