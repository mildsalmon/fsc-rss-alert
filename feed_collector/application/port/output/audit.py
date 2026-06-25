from __future__ import annotations

from typing import Protocol

from feed_collector.domain import Item


class AuditPort(Protocol):
    def log_sent_delivery(
        self,
        source_id: str,
        item: Item,
        *,
        channel_id: str,
        delivery_id: str | None = None,
    ) -> None: ...
