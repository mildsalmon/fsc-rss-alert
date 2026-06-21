from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

import pytest

from feed_collector.application.service.poll import PollService, poll
from feed_collector.domain import Item, SourceConfig
from feed_collector.domain.service import content_hash_item_id


@dataclass
class FakeAdapter:
    items: list[Item]

    def fetch(self) -> list[Item]:
        return self.items


@dataclass
class FakeState:
    first_run: bool = False
    seen: set[str] = field(default_factory=set)
    advanced: list[Item] = field(default_factory=list)
    marked: list[str] = field(default_factory=list)
    channel_id: str | None = "C123"

    def is_first_run(self, source_id: str) -> bool:
        return self.first_run

    def seen_contains(self, source_id: str, item_id: str) -> bool:
        return item_id in self.seen

    def filter_new(self, source_id: str, items: Sequence[Item]) -> list[Item]:
        return [item for item in items if not self.seen_contains(source_id, item.item_id)]

    def mark_seen(self, source_id: str, item_id: str, slack_ts: str | None = None) -> None:
        self.marked.append(item_id)
        self.seen.add(item_id)

    def advance(self, source_id: str, items: Sequence[Item]) -> None:
        self.advanced.extend(items)
        self.seen.update(item.item_id for item in items)

    def record_attempt(self, source_id: str) -> None:
        return None

    def record_success(self, source_id: str) -> None:
        return None

    def record_failure(self, source_id: str, reason: str) -> None:
        return None

    def get_channel_id(self, source_id: str) -> str | None:
        return self.channel_id

    def set_channel_id(self, source_id: str, channel_id: str) -> None:
        self.channel_id = channel_id

    def digest_counts(self, since: datetime) -> dict[str, int]:
        return {}


@dataclass
class FakeNotifier:
    fail_on: str | None = None
    sent: list[tuple[str, Item]] = field(default_factory=list)

    def send(self, channel_id: str, item: Item) -> None:
        if item.item_id == self.fail_on:
            raise RuntimeError("send failed")
        self.sent.append((channel_id, item))


@dataclass
class FakeAudit:
    logged: list[tuple[str, Item]] = field(default_factory=list)

    def log(self, source_id: str, item: Item) -> None:
        self.logged.append((source_id, item))


def make_source(channel_id: str | None = "C123") -> SourceConfig:
    return SourceConfig(
        id="mofa",
        slug="mofa-sanctions",
        name="MOFA sanctions",
        mechanism="rss",
        parser_version=1,
        channel_id=channel_id,
        interval_minutes=30,
        url="https://example.test/feed.xml",
    )


def make_item(item_id: str, published: datetime | None = None) -> Item:
    return Item(item_id=item_id, title=f"title {item_id}", link=f"https://example.test/{item_id}", published=published)


def test_poll_first_run_stores_baseline_without_sending() -> None:
    items = [make_item("newest"), make_item("oldest")]
    state = FakeState(first_run=True)
    notifier = FakeNotifier()
    audit = FakeAudit()

    result = poll(make_source(), FakeAdapter(items), state, notifier, audit)

    assert result.first_run is True
    assert result.sent_count == 0
    assert state.advanced == items
    assert notifier.sent == []
    assert audit.logged == []


def test_poll_service_implements_input_port_shape() -> None:
    service = PollService(make_source(), FakeAdapter([]), FakeState(first_run=True), FakeNotifier(), FakeAudit())

    result = service.poll(dry_run=True)

    assert result.first_run is True
    assert result.dry_run is True


def test_poll_sends_new_items_oldest_first_and_marks_seen_after_audit() -> None:
    newer = make_item("newer", datetime(2026, 1, 2, tzinfo=timezone.utc))
    older = make_item("older", datetime(2026, 1, 1, tzinfo=timezone.utc))
    state = FakeState(first_run=False)
    notifier = FakeNotifier()
    audit = FakeAudit()

    result = poll(make_source(), FakeAdapter([newer, older]), state, notifier, audit)

    assert result.new_items == (older, newer)
    assert [item.item_id for _, item in notifier.sent] == ["older", "newer"]
    assert [item.item_id for _, item in audit.logged] == ["older", "newer"]
    assert state.marked == ["older", "newer"]


def test_poll_send_failure_does_not_mark_failed_item_seen() -> None:
    item = make_item("fails")
    state = FakeState(first_run=False)
    notifier = FakeNotifier(fail_on="fails")

    with pytest.raises(RuntimeError, match="send failed"):
        poll(make_source(), FakeAdapter([item]), state, notifier, FakeAudit())

    assert state.marked == []
    assert "fails" not in state.seen


def test_poll_dry_run_skips_writes_and_delivery() -> None:
    item = make_item("fresh")
    state = FakeState(first_run=False)
    notifier = FakeNotifier()
    audit = FakeAudit()

    result = poll(make_source(), FakeAdapter([item]), state, notifier, audit, dry_run=True)

    assert result.dry_run is True
    assert result.new_items == (item,)
    assert notifier.sent == []
    assert audit.logged == []
    assert state.marked == []


def test_dedup_filters_seen_and_batch_duplicates() -> None:
    state = FakeState(seen={"seen"})
    items = [make_item("seen"), make_item("fresh"), make_item("fresh")]

    result = poll(make_source(), FakeAdapter(items), state, FakeNotifier(), FakeAudit(), dry_run=True)

    assert [item.item_id for item in result.new_items] == ["fresh"]


def test_dedup_uses_stable_content_hash_for_missing_item_id() -> None:
    published = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item = Item(item_id="", title="same", link="", published=published)
    expected = content_hash_item_id("mofa", "same", "", published)

    result = poll(make_source(), FakeAdapter([item]), FakeState(), FakeNotifier(), FakeAudit(), dry_run=True)

    assert result.new_items == (Item(item_id=expected, title="same", link="", published=published),)
