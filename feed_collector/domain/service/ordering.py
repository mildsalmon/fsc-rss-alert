from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from feed_collector.domain import Item


def oldest_first(items: Sequence[Item]) -> list[Item]:
    max_datetime = datetime.max.replace(tzinfo=timezone.utc)

    def sort_key(item: Item) -> datetime:
        if item.published is None:
            return max_datetime
        if item.published.tzinfo is None:
            return item.published.replace(tzinfo=timezone.utc)
        return item.published.astimezone(timezone.utc)

    return sorted(items, key=sort_key)
