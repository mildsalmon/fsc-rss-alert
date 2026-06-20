from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from feed_collector.core.dedup import dedup, with_dedup_key
from feed_collector.domain import Item, SourceConfig
from feed_collector.ports import AuditPort, NotifierPort, SourcePort, StatePort


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


def oldest_first(items: Sequence[Item]) -> list[Item]:
    max_datetime = datetime.max.replace(tzinfo=timezone.utc)

    def sort_key(item: Item) -> datetime:
        if item.published is None:
            return max_datetime
        if item.published.tzinfo is None:
            return item.published.replace(tzinfo=timezone.utc)
        return item.published.astimezone(timezone.utc)

    return sorted(items, key=sort_key)


def resolve_channel_id(source: SourceConfig, state: StatePort) -> str:
    channel_id = source.channel_id or state.get_channel_id(source.id)
    if not channel_id:
        raise ValueError(f"Source {source.id} has no channel_id")
    return channel_id


def poll(
    source: SourceConfig,
    adapter: SourcePort,
    state: StatePort,
    notifier: NotifierPort,
    audit: AuditPort,
    *,
    dry_run: bool = False,
) -> PollResult:
    items = [with_dedup_key(source.id, item) for item in adapter.fetch()]
    first_run = state.is_first_run(source.id)

    if first_run:
        if not dry_run:
            state.advance(source.id, items)
        return PollResult(
            source_id=source.id,
            fetched_count=len(items),
            new_count=0,
            sent_count=0,
            first_run=True,
            dry_run=dry_run,
            new_items=(),
            sent_items=(),
        )

    new_items = oldest_first(dedup(source.id, items, state))
    if dry_run:
        return PollResult(
            source_id=source.id,
            fetched_count=len(items),
            new_count=len(new_items),
            sent_count=0,
            first_run=False,
            dry_run=True,
            new_items=tuple(new_items),
            sent_items=(),
        )

    channel_id = resolve_channel_id(source, state)
    sent_items: list[Item] = []
    for item in new_items:
        notifier.send(channel_id, item)
        audit.log(source.id, item)
        state.mark_seen(source.id, item.item_id)
        sent_items.append(item)

    return PollResult(
        source_id=source.id,
        fetched_count=len(items),
        new_count=len(new_items),
        sent_count=len(sent_items),
        first_run=False,
        dry_run=False,
        new_items=tuple(new_items),
        sent_items=tuple(sent_items),
    )
