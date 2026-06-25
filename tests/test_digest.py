from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_collector.adapter.outbound import SqliteAuditLog
from feed_collector.digest import (
    build_daily_digest,
    format_digest_message,
    previous_kst_day_window,
    relative_time,
    source_warning,
)
from feed_collector.domain import Item, SourceConfig


def make_source(source_id: str = "mofa", *, interval_minutes: int = 30) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        slug=f"{source_id}-slug",
        name=f"{source_id} source",
        mechanism="rss",
        parser_version=1,
        channel_id=None,
        interval_minutes=interval_minutes,
        url=f"https://example.test/{source_id}.xml",
    )


def make_item(item_id: str) -> Item:
    return Item(
        item_id=item_id,
        title=f"title {item_id}",
        link=f"https://example.test/{item_id}",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def set_source_state(
    db_path: Path,
    source_id: str,
    *,
    last_success_at: datetime | None,
    consecutive_failures: int = 0,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO sources (id) VALUES (?)", (source_id,))
        conn.execute(
            """
            UPDATE sources
            SET last_success_at = ?, consecutive_failures = ?
            WHERE id = ?
            """,
            (
                last_success_at.isoformat() if last_success_at else None,
                consecutive_failures,
                source_id,
            ),
        )


def test_previous_kst_day_window_uses_kst_boundaries() -> None:
    window = previous_kst_day_window(datetime(2026, 6, 25, 0, 30, tzinfo=timezone.utc))

    assert window.target_date.isoformat() == "2026-06-24"
    assert window.start_utc == datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    assert window.end_utc == datetime(2026, 6, 24, 15, 0, tzinfo=timezone.utc)


def test_build_daily_digest_counts_previous_kst_day_and_source_state(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"
    now = datetime(2026, 6, 25, 0, 30, tzinfo=timezone.utc)
    with SqliteAuditLog(db_path, retention_days=365) as audit:
        audit.log_delivery("mofa", make_item("inside-1"), sent_at="2026-06-23T16:00:00+00:00")
        audit.log_delivery("mofa", make_item("inside-2"), sent_at="2026-06-24T14:59:59+00:00")
        audit.log_delivery("mofa", make_item("outside"), sent_at="2026-06-24T15:00:00+00:00")
    set_source_state(db_path, "mofa", last_success_at=now - timedelta(minutes=20))
    set_source_state(db_path, "lawreq", last_success_at=None, consecutive_failures=2)

    digest = build_daily_digest(db_path, [make_source("mofa"), make_source("lawreq")], now=now)

    assert digest.ok_count == 1
    assert digest.warning_count == 1
    assert digest.stats[0].sent_count == 2
    assert digest.stats[0].warning is False
    assert digest.stats[1].sent_count == 0
    assert digest.stats[1].warning is True
    assert digest.stats[1].warning_reason == "no successful poll recorded"


def test_source_warning_uses_last_success_threshold() -> None:
    source = make_source(interval_minutes=30)
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)

    assert source_warning(source, now - timedelta(minutes=90), 3, now=now) == (False, None)
    warning, reason = source_warning(source, now - timedelta(minutes=91), 0, now=now)

    assert warning is True
    assert reason == "last success older than 90 minutes"


def test_relative_time_formats_common_ranges() -> None:
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)

    assert relative_time(None, now=now) == "never"
    assert relative_time(now - timedelta(seconds=10), now=now) == "just now"
    assert relative_time(now - timedelta(minutes=12), now=now) == "12m ago"
    assert relative_time(now - timedelta(hours=3), now=now) == "3h ago"
    assert relative_time(now - timedelta(days=2), now=now) == "2d ago"


def test_format_digest_message_contains_summary_and_source_lines(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"
    now = datetime(2026, 6, 25, 0, 30, tzinfo=timezone.utc)
    with SqliteAuditLog(db_path, retention_days=365):
        pass
    set_source_state(db_path, "mofa", last_success_at=now - timedelta(minutes=20))
    set_source_state(db_path, "lawreq", last_success_at=None, consecutive_failures=2)

    digest = build_daily_digest(db_path, [make_source("mofa"), make_source("lawreq")], now=now)
    message = format_digest_message(digest)

    assert "Feed collector daily digest - 2026-06-24 KST" in message
    assert "Summary: OK 1 / WARN 1" in message
    assert "OK mofa source (mofa)" in message
    assert "WARN lawreq source (lawreq)" in message
