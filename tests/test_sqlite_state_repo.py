from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any

from feed_collector.adapter.outbound import (
    SqliteChannelRepo,
    SqliteSourceStateRepo,
    SqliteStateRepo,
    try_acquire_poll_lock,
)
from feed_collector.application.port.output import ChannelResolverPort, SeenStatePort, SourceStatePort
from feed_collector.domain import Item, SourceConfig
from feed_collector.domain.service import item_dedup_key


def make_source(source_id: str, channel_id: str | None = None) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        slug=f"{source_id}-slug",
        name=f"{source_id} source",
        mechanism="rss",
        parser_version=1,
        channel_id=channel_id,
        interval_minutes=30,
        url="https://example.test/feed.xml",
    )


def make_item(item_id: str) -> Item:
    return Item(
        item_id=item_id,
        title=f"title {item_id}",
        link=f"https://example.test/{item_id}",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def seen_count(db_path: Path, source_id: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM seen_items WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    assert row is not None
    return row[0]


def source_row(db_path: Path, source_id: str) -> sqlite3.Row:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
    assert row is not None
    return row


def child_try_lock(lock_path: str, queue: Queue[Any]) -> None:
    lock = try_acquire_poll_lock(lock_path)
    queue.put(lock is not None)
    if lock is not None:
        lock.release()


def test_sqlite_repos_create_schema_and_keep_channel_state_separate(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"

    with SqliteSourceStateRepo(db_path) as source_repo:
        source_repo.ensure_source(make_source("mofa", channel_id="C_CONFIG"))
    with SqliteChannelRepo(db_path) as channel_repo:
        channel_repo.set_channel_id("mofa", "C_SQLITE")
    with SqliteSourceStateRepo(db_path) as source_repo:
        source_repo.ensure_source(make_source("mofa", channel_id="C_CONFIG_CHANGED"))

    with SqliteChannelRepo(db_path) as channel_repo:
        assert channel_repo.get_channel_id("mofa") == "C_SQLITE"

    with sqlite3.connect(db_path) as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {"sources", "seen_items", "audit_log"}.issubset(table_names)


def test_sqlite_adapters_declare_narrow_output_ports(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"

    with (
        SqliteStateRepo(db_path) as seen_state_adapter,
        SqliteSourceStateRepo(db_path) as source_state_adapter,
        SqliteChannelRepo(db_path) as channel_resolver_adapter,
    ):
        seen_state: SeenStatePort = seen_state_adapter
        source_state: SourceStatePort = source_state_adapter
        channel_resolver: ChannelResolverPort = channel_resolver_adapter

        seen_state.mark_seen("mofa", ["one"])
        source_state.record_attempt("mofa")
        assert channel_resolver.get_channel_id("mofa") is None


def test_seen_items_are_isolated_by_source(tmp_path: Path) -> None:
    with SqliteStateRepo(tmp_path / "feed.db") as repo:
        repo.mark_seen("source-a", ["same-id"])

        assert repo.seen_contains("source-a", "same-id") is True
        assert repo.seen_contains("source-b", "same-id") is False
        assert repo.filter_new("source-b", [make_item("same-id")]) == [make_item("same-id")]


def test_seen_contains_accepts_legacy_http_443_url(tmp_path: Path) -> None:
    with SqliteStateRepo(tmp_path / "feed.db") as repo:
        repo.mark_seen("mofa", ["http://www.mofa.go.kr:443/www/brd/m_4080/view.do?seq=377346"])

        assert repo.seen_contains("mofa", "https://www.mofa.go.kr/www/brd/m_4080/view.do?seq=377346") is True
        assert repo.filter_new(
            "mofa",
            [make_item("https://www.mofa.go.kr/www/brd/m_4080/view.do?seq=377346")],
        ) == []


def test_replace_baseline_and_mark_seen_are_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"

    with SqliteStateRepo(db_path) as repo:
        assert repo.is_first_run("mofa") is True

        repo.replace_baseline("mofa", [make_item("one"), make_item("one")])
        repo.mark_seen("mofa", ["one", "two", "two"])

        assert repo.is_first_run("mofa") is False
        assert repo.seen_contains("mofa", "one") is True
        assert repo.seen_contains("mofa", "two") is True

    assert seen_count(db_path, "mofa") == 2


def test_content_hash_fallback_is_stable_for_items_without_source_key() -> None:
    published = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = Item(item_id="", title=" same ", link="", published=published)
    second = Item(item_id="", title="same", link="", published=published)

    assert item_dedup_key("mofa", first) == item_dedup_key("mofa", second)
    assert item_dedup_key("mofa", first).startswith("content://mofa/")


def test_attempt_success_and_failure_timestamps_are_separate(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"

    with SqliteSourceStateRepo(db_path) as repo:
        repo.record_attempt("lawreq")
        repo.record_failure("lawreq", "NETWORK")

        state = repo.get_state("lawreq")
        assert state.last_attempt_at is not None
        assert state.last_success_at is None
        assert state.consecutive_failures == 1
        assert state.last_failure_reason == "NETWORK"

        failed_row = source_row(db_path, "lawreq")
        assert failed_row["last_attempt_at"] is not None
        assert failed_row["last_success_at"] is None
        assert failed_row["consecutive_failures"] == 1
        assert failed_row["last_failure_reason"] == "NETWORK"

        repo.mark_failure_alert_sent("lawreq")
        assert repo.get_state("lawreq").failure_alert_sent is True

        repo.record_success("lawreq")

    success_row = source_row(db_path, "lawreq")
    assert success_row["last_success_at"] is not None
    assert success_row["consecutive_failures"] == 0
    assert success_row["failure_alert_sent"] == 0


def test_poll_lock_rejects_second_process_without_error(tmp_path: Path) -> None:
    lock_path = tmp_path / "feed_collector.lock"
    first_lock = try_acquire_poll_lock(lock_path)
    assert first_lock is not None

    queue: Queue[Any] = Queue()
    process = Process(target=child_try_lock, args=(str(lock_path), queue))

    try:
        process.start()
        process.join(timeout=5)

        assert process.exitcode == 0
        assert queue.get(timeout=1) is False
    finally:
        first_lock.release()
