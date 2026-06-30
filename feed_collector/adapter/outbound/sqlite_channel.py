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

    def get_channel_metadata(self, source_id: str) -> tuple[str | None, str | None, int | None]:
        row = self._conn.execute(
            """
            SELECT channel_metadata_name, channel_metadata_url, channel_metadata_version
            FROM sources
            WHERE id = ?
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            return None, None, None
        name, url, version = row
        return (
            name if isinstance(name, str) and name else None,
            url if isinstance(url, str) and url else None,
            version if isinstance(version, int) else None,
        )

    def set_channel_metadata(self, source_id: str, display_name: str, source_url: str, version: int) -> None:
        with self._conn:
            self._ensure_source_row(source_id)
            self._conn.execute(
                """
                UPDATE sources
                SET channel_metadata_name = ?, channel_metadata_url = ?, channel_metadata_version = ?
                WHERE id = ?
                """,
                (display_name, source_url, version, source_id),
            )
