from __future__ import annotations

from typing import Sequence

from feed_collector.application.dto import PollResult
from feed_collector.application.port.output.audit import AuditPort
from feed_collector.application.port.output.notifier import NotifierPort
from feed_collector.application.port.output.source import SourcePort
from feed_collector.application.port.output.state import StatePort
from feed_collector.domain import Item, SourceConfig
from feed_collector.domain.service import oldest_first, unique_items, with_dedup_key


class PollService:
    def __init__(
        self,
        source: SourceConfig,
        adapter: SourcePort,
        state: StatePort,
        notifier: NotifierPort,
        audit: AuditPort,
    ) -> None:
        self.source = source
        self.adapter = adapter
        self.state = state
        self.notifier = notifier
        self.audit = audit

    def poll(self, *, dry_run: bool = False) -> PollResult:
        items = [with_dedup_key(self.source.id, item) for item in self.adapter.fetch()]
        first_run = self.state.is_first_run(self.source.id)

        if first_run:
            if not dry_run:
                self.state.advance(self.source.id, items)
            return PollResult(
                source_id=self.source.id,
                fetched_count=len(items),
                new_count=0,
                sent_count=0,
                first_run=True,
                dry_run=dry_run,
                new_items=(),
                sent_items=(),
            )

        new_items = oldest_first(self._filter_new_items(items))
        if dry_run:
            return PollResult(
                source_id=self.source.id,
                fetched_count=len(items),
                new_count=len(new_items),
                sent_count=0,
                first_run=False,
                dry_run=True,
                new_items=tuple(new_items),
                sent_items=(),
            )

        channel_id = self._resolve_channel_id()
        sent_items = []
        for item in new_items:
            self.notifier.send(channel_id, item)
            self.audit.log(self.source.id, item)
            self.state.mark_seen(self.source.id, item.item_id)
            sent_items.append(item)

        return PollResult(
            source_id=self.source.id,
            fetched_count=len(items),
            new_count=len(new_items),
            sent_count=len(sent_items),
            first_run=False,
            dry_run=False,
            new_items=tuple(new_items),
            sent_items=tuple(sent_items),
        )

    def _filter_new_items(self, items: Sequence[Item]) -> list[Item]:
        return [item for item in unique_items(self.source.id, items) if not self.state.seen_contains(self.source.id, item.item_id)]

    def _resolve_channel_id(self) -> str:
        channel_id = self.source.channel_id or self.state.get_channel_id(self.source.id)
        if not channel_id:
            raise ValueError(f"Source {self.source.id} has no channel_id")
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
    return PollService(source, adapter, state, notifier, audit).poll(dry_run=dry_run)
