from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_collector.adapter.outbound import SqliteAuditLog
from feed_collector.domain import Item


def make_item(item_id: str = "item-1") -> Item:
    return Item(
        item_id=item_id,
        title=f"title {item_id}",
        link=f"https://example.test/{item_id}",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def audit_rows(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute("SELECT * FROM audit_log ORDER BY id"))


def test_sqlite_audit_log_inserts_delivery_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"
    sent_at = datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat()

    with SqliteAuditLog(db_path, retention_days=365) as audit:
        audit.log_delivery(
            "mofa",
            make_item(),
            channel_id="C123",
            slack_ts="123.456",
            sent_at=sent_at,
            status="sent",
        )

    rows = audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["source_id"] == "mofa"
    assert rows[0]["item_id"] == "item-1"
    assert rows[0]["title"] == "title item-1"
    assert rows[0]["channel_id"] == "C123"
    assert rows[0]["slack_ts"] == "123.456"
    assert rows[0]["sent_at"] == sent_at
    assert rows[0]["status"] == "sent"


def test_sqlite_audit_log_existing_port_log_remains_supported(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"

    with SqliteAuditLog(db_path) as audit:
        audit.log("mofa", make_item())

    rows = audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "sent"
    assert rows[0]["channel_id"] is None
    assert rows[0]["slack_ts"] is None


def test_sqlite_audit_log_prunes_rows_older_than_retention(tmp_path: Path) -> None:
    db_path = tmp_path / "feed.db"
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    old_sent_at = (now - timedelta(days=91)).isoformat()
    fresh_sent_at = (now - timedelta(days=89)).isoformat()

    with SqliteAuditLog(db_path, retention_days=90) as audit:
        audit.log_delivery("mofa", make_item("old"), sent_at=old_sent_at)
        audit.log_delivery("mofa", make_item("fresh"), sent_at=fresh_sent_at)
        audit.prune(now=now)

    rows = audit_rows(db_path)
    assert [row["item_id"] for row in rows] == ["fresh"]
