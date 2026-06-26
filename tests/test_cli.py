from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from feed_collector.adapter.outbound import SqliteAuditLog, SqliteChannelRepo, SqliteSourceStateRepo, SqliteStateRepo
from feed_collector.application.port.output import SourceRunState
from feed_collector import cli
from feed_collector.cli import PollRunner, failure_alert_item, is_due
from feed_collector.domain import Item, SourceConfig
from feed_collector.errors import FetchFailureReason, PollError, infer_from_error


@dataclass
class FakeAdapter:
    items: list[Item] = field(default_factory=list)
    error: Exception | None = None
    fetch_count: int = 0

    def fetch(self) -> list[Item]:
        self.fetch_count += 1
        if self.error is not None:
            raise self.error
        return self.items


@dataclass
class FakeNotifier:
    sent: list[tuple[str, Item]] = field(default_factory=list)

    def send(self, channel_id: str, item: Item) -> str:
        self.sent.append((channel_id, item))
        return "123.456"


@dataclass
class FakeProvisioner:
    channels: dict[str, str] = field(default_factory=lambda: {"ops": "COPS", "source": "CSOURCE"})
    requested: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    metadata_updates: list[tuple[str, str | None, str | None]] = field(default_factory=list)

    def ensure_feed_channel(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        source_url: str | None = None,
    ) -> str:
        self.requested.append((slug, display_name, source_url))
        return self.channels.get(slug, f"C-{slug}")

    def update_feed_channel_metadata(
        self,
        channel_id: str,
        *,
        display_name: str | None = None,
        source_url: str | None = None,
    ) -> None:
        self.metadata_updates.append((channel_id, display_name, source_url))


def make_source(source_id: str = "mofa", *, interval_minutes: int = 30, channel_id: str | None = "C123") -> SourceConfig:
    return SourceConfig(
        id=source_id,
        slug=f"{source_id}-slug",
        name=f"{source_id} source",
        mechanism="rss",
        parser_version=1,
        channel_id=channel_id,
        interval_minutes=interval_minutes,
        url=f"https://example.test/{source_id}.xml",
    )


def make_item(item_id: str = "one") -> Item:
    return Item(
        item_id=item_id,
        title=f"title {item_id}",
        link=f"https://example.test/{item_id}",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def make_runner(
    tmp_path: Path,
    *,
    source: SourceConfig,
    adapter: FakeAdapter,
    notifier: FakeNotifier | None = None,
    provisioner: FakeProvisioner | None = None,
    now: datetime | None = None,
) -> tuple[PollRunner, SqliteSourceStateRepo, SqliteStateRepo, SqliteChannelRepo, SqliteAuditLog]:
    db_path = tmp_path / "feed.db"
    seen_state = SqliteStateRepo(db_path)
    source_state = SqliteSourceStateRepo(db_path)
    channel_repo = SqliteChannelRepo(db_path)
    audit = SqliteAuditLog(db_path)
    runner = PollRunner(
        sources=[source],
        adapter_factory=lambda cfg: adapter,
        seen_state=seen_state,
        source_state=source_state,
        channel_repo=channel_repo,
        notifier=notifier or FakeNotifier(),
        audit=audit,
        channel_provisioner=provisioner or FakeProvisioner(),
        failure_threshold=3,
        now=now,
    )
    return runner, source_state, seen_state, channel_repo, audit


def close_repos(*repos: object) -> None:
    for repo in repos:
        close = getattr(repo, "close")
        close()


def test_is_due_uses_last_attempt_timestamp() -> None:
    source = make_source(interval_minutes=30)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert is_due(source, SourceRunState(source.id), now=now) is True
    assert is_due(
        source,
        SourceRunState(source.id, last_attempt_at=(now - timedelta(minutes=29)).isoformat()),
        now=now,
    ) is False
    assert is_due(
        source,
        SourceRunState(source.id, last_attempt_at=(now - timedelta(minutes=30)).isoformat()),
        now=now,
    ) is True


def test_poll_runner_skips_not_due_sources_without_fetching(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    source = make_source()
    adapter = FakeAdapter([make_item()])
    runner, source_state, seen_state, channel_repo, audit = make_runner(
        tmp_path,
        source=source,
        adapter=adapter,
        now=now,
    )
    try:
        source_state.ensure_source(source)
        with sqlite3.connect(tmp_path / "feed.db") as conn:
            conn.execute(
                "UPDATE sources SET last_attempt_at = ? WHERE id = ?",
                ((now - timedelta(minutes=5)).isoformat(), source.id),
            )

        assert runner.run() == 0

        assert adapter.fetch_count == 0
    finally:
        close_repos(seen_state, source_state, channel_repo, audit)


def test_dry_run_fetches_candidates_without_writing_state(tmp_path: Path) -> None:
    source = make_source(channel_id=None)
    adapter = FakeAdapter([make_item()])
    notifier = FakeNotifier()
    provisioner = FakeProvisioner()
    runner, source_state, seen_state, channel_repo, audit = make_runner(
        tmp_path,
        source=source,
        adapter=adapter,
        notifier=notifier,
        provisioner=provisioner,
    )
    try:
        assert runner.run(dry_run=True) == 0

        assert adapter.fetch_count == 1
        assert source_state.get_state(source.id).last_attempt_at is None
        assert seen_state.is_first_run(source.id) is True
        assert channel_repo.get_channel_id(source.id) is None
        assert notifier.sent == []
        assert provisioner.requested == []
    finally:
        close_repos(seen_state, source_state, channel_repo, audit)


def test_poll_runner_records_failure_reason_and_sends_immediate_ops_alert(tmp_path: Path) -> None:
    source = make_source()
    adapter = FakeAdapter(error=PollError("Feed fetch returned HTTP 404"))
    notifier = FakeNotifier()
    provisioner = FakeProvisioner()
    runner, source_state, seen_state, channel_repo, audit = make_runner(
        tmp_path,
        source=source,
        adapter=adapter,
        notifier=notifier,
        provisioner=provisioner,
    )
    try:
        assert runner.run() == 1

        state = source_state.get_state(source.id)
        assert state.consecutive_failures == 1
        assert state.last_failure_reason == FetchFailureReason.NOT_FOUND.value
        assert state.failure_alert_sent is True
        assert provisioner.requested == [("ops", "Feed Collector Ops", None)]
        assert channel_repo.get_channel_id("feed-ops") == "COPS"
        assert len(notifier.sent) == 1
        assert notifier.sent[0][0] == "COPS"
        assert "NOT_FOUND" in notifier.sent[0][1].title
    finally:
        close_repos(seen_state, source_state, channel_repo, audit)


def test_poll_runner_passes_source_metadata_to_auto_created_channel(tmp_path: Path) -> None:
    source = make_source(channel_id=None)
    adapter = FakeAdapter([make_item("fresh")])
    notifier = FakeNotifier()
    provisioner = FakeProvisioner()
    runner, source_state, seen_state, channel_repo, audit = make_runner(
        tmp_path,
        source=source,
        adapter=adapter,
        notifier=notifier,
        provisioner=provisioner,
    )
    try:
        seen_state.mark_seen(source.id, ["old"])

        assert runner.run() == 0

        assert provisioner.requested == [(source.slug, source.name, source.url)]
        assert channel_repo.get_channel_id(source.id) == f"C-{source.slug}"
        assert notifier.sent[0][0] == f"C-{source.slug}"
    finally:
        close_repos(seen_state, source_state, channel_repo, audit)


def test_poll_runner_does_not_repeat_metadata_update_for_stored_channel(tmp_path: Path) -> None:
    source = make_source(channel_id=None)
    adapter = FakeAdapter([])
    provisioner = FakeProvisioner()
    runner, source_state, seen_state, channel_repo, audit = make_runner(
        tmp_path,
        source=source,
        adapter=adapter,
        provisioner=provisioner,
    )
    try:
        seen_state.mark_seen(source.id, ["old"])
        channel_repo.set_channel_id(source.id, "CSTORED")

        assert runner.run() == 0

        assert provisioner.requested == []
        assert provisioner.metadata_updates == []
    finally:
        close_repos(seen_state, source_state, channel_repo, audit)


def test_infer_from_error_classifies_common_failures() -> None:
    assert infer_from_error(PollError("Feed parse failed for source")) is FetchFailureReason.STRUCTURE_CHANGED
    assert infer_from_error(PollError("Feed fetch timed out")) is FetchFailureReason.TIMEOUT
    assert infer_from_error(PollError("Feed fetch returned HTTP 403")) is FetchFailureReason.BLOCKED
    assert infer_from_error(PollError("Feed fetch returned HTTP 404")) is FetchFailureReason.NOT_FOUND


def test_failure_alert_item_is_actionable() -> None:
    source = make_source()
    item = failure_alert_item(
        source,
        FetchFailureReason.STRUCTURE_CHANGED,
        SourceRunState(source.id, consecutive_failures=2),
        PollError("missing field"),
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert item.item_id == "failure://mofa/STRUCTURE_CHANGED/2"
    assert "mofa source poll failed" in item.title
    assert item.link == source.url


def test_cli_dry_run_does_not_create_state_or_lock_files(tmp_path: Path, monkeypatch) -> None:
    sources_file = tmp_path / "sources.yaml"
    db_path = tmp_path / "feed.db"
    lock_path = tmp_path / "poll.lock"
    sources_file.write_text(
        """
        - id: mofa
          slug: mofa
          name: MOFA
          mechanism: rss
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://example.test/rss.xml
        """,
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "SourceAdapterRegistry", lambda: lambda source: FakeAdapter([make_item()]))
    args = cli.parse_args(
        [
            "poll",
            "--dry-run",
            "--sources-file",
            str(sources_file),
            "--db-path",
            str(db_path),
            "--lock-file",
            str(lock_path),
        ]
    )

    assert cli.run_poll(args) == 0

    assert not db_path.exists()
    assert not lock_path.exists()
