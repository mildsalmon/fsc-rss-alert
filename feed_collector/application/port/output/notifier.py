from __future__ import annotations

from typing import Protocol

from feed_collector.domain import Item


class NotifierPort(Protocol):
    def send(self, channel_id: str, item: Item) -> None: ...
