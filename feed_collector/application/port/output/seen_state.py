from __future__ import annotations

from typing import Protocol, Sequence

from feed_collector.domain import Item


class SeenStatePort(Protocol):
    def is_first_run(self, source_id: str) -> bool: ...

    def seen_contains(self, source_id: str, item_id: str) -> bool: ...

    def replace_baseline(self, source_id: str, items: Sequence[Item]) -> None: ...

    def mark_seen(self, source_id: str, item_ids: Sequence[str]) -> None: ...
