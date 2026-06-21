from __future__ import annotations

from feed_collector.adapter.outbound.sqlite_base import SqliteRepoBase
from feed_collector.application.port.output.channel import ChannelResolverPort


class SqliteChannelRepo(SqliteRepoBase, ChannelResolverPort):
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
