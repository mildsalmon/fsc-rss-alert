from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

import pytest

from feed_collector.bootstrap import AppContainer
from feed_collector.application.port.input.poll import PollInputPort
from feed_collector.application.service.poll import PollService
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
    baseline: list[Item] = field(default_factory=list)
    marked_batches: list[list[str]] = field(default_factory=list)
    channel_id: str | None = "C123"

    def is_first_run(self, source_id: str) -> bool:
        return self.first_run

    def seen_contains(self, source_id: str, item_id: str) -> bool:
        return item_id in self.seen

    def replace_baseline(self, source_id: str, items: Sequence[Item]) -> None:
        self.baseline = list(items)
        self.seen = {item.item_id for item in items}

    def mark_seen(self, source_id: str, item_ids: Sequence[str]) -> None:
        item_id_list = list(item_ids)
        self.marked_batches.append(item_id_list)
        self.seen.update(item_id_list)

    def get_channel_id(self, source_id: str) -> str | None:
        return self.channel_id


@dataclass
class FakeNotifier:
    fail_on: str | None = None
    delivery_id: str | None = None
    sent: list[tuple[str, Item]] = field(default_factory=list)

    def send(self, channel_id: str, item: Item) -> str | None:
        if item.item_id == self.fail_on:
            raise RuntimeError("send failed")
        self.sent.append((channel_id, item))
        return self.delivery_id


@dataclass
class FakeAudit:
    logged: list[tuple[str, Item, str | None, str | None, str]] = field(default_factory=list)

    def log(
        self,
        source_id: str,
        item: Item,
        *,
        channel_id: str | None = None,
        delivery_id: str | None = None,
        status: str = "sent",
    ) -> None:
        self.logged.append((source_id, item, channel_id, delivery_id, status))


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


def make_service(
    items: list[Item],
    state: FakeState,
    notifier: FakeNotifier | None = None,
    audit: FakeAudit | None = None,
) -> PollService:
    return PollService(make_source(), FakeAdapter(items), state, state, notifier or FakeNotifier(), audit or FakeAudit())


def test_poll_first_run_stores_baseline_without_sending() -> None:
    items = [make_item("newest"), make_item("oldest"), make_item("oldest")]
    state = FakeState(first_run=True)
    notifier = FakeNotifier()
    audit = FakeAudit()

    result = make_service(items, state, notifier, audit).poll()

    assert result.first_run is True
    assert result.sent_count == 0
    assert [item.item_id for item in state.baseline] == ["newest", "oldest"]
    assert notifier.sent == []
    assert audit.logged == []


def test_poll_service_implements_input_port_shape() -> None:
    state = FakeState(first_run=True)
    service = PollService(make_source(), FakeAdapter([]), state, state, FakeNotifier(), FakeAudit())
    input_port: PollInputPort = service

    result = input_port.poll(dry_run=True)

    assert result.first_run is True
    assert result.dry_run is True


def test_poll_sends_new_items_oldest_first_and_marks_seen_after_audit() -> None:
    newer = make_item("newer", datetime(2026, 1, 2, tzinfo=timezone.utc))
    older = make_item("older", datetime(2026, 1, 1, tzinfo=timezone.utc))
    state = FakeState(first_run=False)
    notifier = FakeNotifier()
    audit = FakeAudit()

    result = make_service([newer, older], state, notifier, audit).poll()

    assert result.new_items == (older, newer)
    assert [item.item_id for _, item in notifier.sent] == ["older", "newer"]
    assert [item.item_id for _, item, _, _, _ in audit.logged] == ["older", "newer"]
    assert state.marked_batches == [["older", "newer"]]


def test_poll_passes_delivery_metadata_to_audit() -> None:
    item = make_item("fresh")
    state = FakeState(first_run=False)
    notifier = FakeNotifier(delivery_id="123.456")
    audit = FakeAudit()

    make_service([item], state, notifier, audit).poll()

    assert audit.logged == [("mofa", item, "C123", "123.456", "sent")]


def test_poll_send_failure_does_not_advance_any_seen_state() -> None:
    first = make_item("first", datetime(2026, 1, 1, tzinfo=timezone.utc))
    second = make_item("fails", datetime(2026, 1, 2, tzinfo=timezone.utc))
    state = FakeState(first_run=False)
    notifier = FakeNotifier(fail_on="fails")
    audit = FakeAudit()

    with pytest.raises(RuntimeError, match="send failed"):
        make_service([first, second], state, notifier, audit).poll()

    assert [item.item_id for _, item in notifier.sent] == ["first"]
    assert [item.item_id for _, item, _, _, _ in audit.logged] == ["first"]
    assert state.marked_batches == []
    assert "first" not in state.seen
    assert "fails" not in state.seen


def test_poll_dry_run_skips_writes_and_delivery() -> None:
    item = make_item("fresh")
    state = FakeState(first_run=False)
    notifier = FakeNotifier()
    audit = FakeAudit()

    result = make_service([item], state, notifier, audit).poll(dry_run=True)

    assert result.dry_run is True
    assert result.new_items == (item,)
    assert notifier.sent == []
    assert audit.logged == []
    assert state.marked_batches == []


def test_dedup_filters_seen_and_batch_duplicates() -> None:
    state = FakeState(seen={"seen"})
    items = [make_item("seen"), make_item("fresh"), make_item("fresh")]

    result = make_service(items, state).poll(dry_run=True)

    assert [item.item_id for item in result.new_items] == ["fresh"]


def test_dedup_uses_stable_content_hash_for_missing_item_id() -> None:
    published = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item = Item(item_id="", title="same", link="", published=published)
    expected = content_hash_item_id("mofa", "same", "", published)

    result = make_service([item], FakeState()).poll(dry_run=True)

    assert result.new_items == (Item(item_id=expected, title="same", link="", published=published),)


def test_container_builds_poll_input_port() -> None:
    source = make_source()
    state = FakeState(first_run=False)
    item = make_item("fresh")
    notifier = FakeNotifier()
    audit = FakeAudit()

    container = AppContainer(
        source_configs={source.id: source},
        source_adapter_factory=lambda source_config: FakeAdapter([item]),
        seen_state=state,
        channel_resolver=state,
        notifier=notifier,
        audit=audit,
    )

    service = container.poll_service("mofa")
    result = service.poll()

    assert result.sent_items == (item,)
    assert notifier.sent == [("C123", item)]


def test_container_rejects_unknown_source_id() -> None:
    state = FakeState()
    container = AppContainer(
        source_configs={},
        source_adapter_factory=lambda source_config: FakeAdapter([]),
        seen_state=state,
        channel_resolver=state,
        notifier=FakeNotifier(),
        audit=FakeAudit(),
    )

    with pytest.raises(KeyError, match="Unknown source_id: missing"):
        container.poll_service("missing")
