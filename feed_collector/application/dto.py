from __future__ import annotations

from dataclasses import dataclass

from feed_collector.domain import Item


@dataclass(frozen=True)
class PollResult:
    source_id: str
    fetched_count: int
    new_count: int
    sent_count: int
    first_run: bool
    dry_run: bool
    new_items: tuple[Item, ...]
    sent_items: tuple[Item, ...]
