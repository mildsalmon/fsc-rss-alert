from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_collector import cli
from feed_collector.adapter.outbound import SqliteAuditLog
from feed_collector.domain import Item


class FakeSlackBotNotifier:
    instances: list[FakeSlackBotNotifier] = []

    def __init__(self, **kwargs: object) -> None:
        del kwargs
        self.sent_texts: list[tuple[str, str]] = []
        self.__class__.instances.append(self)

    def send_text(self, channel_id: str, text: str) -> str:
        self.sent_texts.append((channel_id, text))
        return "123.456"


class FakeSlackChannelManager:
    instances: list[FakeSlackChannelManager] = []

    def __init__(self, **kwargs: object) -> None:
        del kwargs
        self.requested: list[str] = []
        self.__class__.instances.append(self)

    def ensure_feed_channel(self, slug: str) -> str:
        self.requested.append(slug)
        return "COPS"


def make_item(item_id: str) -> Item:
    return Item(
        item_id=item_id,
        title=f"title {item_id}",
        link=f"https://example.test/{item_id}",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def write_sources_file(path: Path) -> None:
    path.write_text(
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


def test_digest_cli_sends_one_feed_ops_message_and_prunes_old_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sources_file = tmp_path / "sources.yaml"
    db_path = tmp_path / "feed.db"
    lock_path = tmp_path / "digest.lock"
    write_sources_file(sources_file)
    now = datetime.now(timezone.utc)
    old_sent_at = (now - timedelta(days=91)).isoformat()
    fresh_sent_at = (now - timedelta(days=1)).isoformat()

    with SqliteAuditLog(db_path, retention_days=90):
        pass
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO sources (id) VALUES (?)", ("mofa",))
        conn.executemany(
            """
            INSERT INTO audit_log (source_id, item_id, title, sent_at, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("mofa", "old", "title old", old_sent_at, "sent"),
                ("mofa", "fresh", "title fresh", fresh_sent_at, "sent"),
            ],
        )
        conn.execute(
            "UPDATE sources SET last_success_at = ? WHERE id = ?",
            (now.isoformat(), "mofa"),
        )

    FakeSlackBotNotifier.instances.clear()
    FakeSlackChannelManager.instances.clear()
    monkeypatch.setattr(cli, "SlackBotNotifier", FakeSlackBotNotifier)
    monkeypatch.setattr(cli, "SlackChannelManager", FakeSlackChannelManager)
    args = cli.parse_args(
        [
            "digest",
            "--sources-file",
            str(sources_file),
            "--db-path",
            str(db_path),
            "--lock-file",
            str(lock_path),
        ]
    )

    assert cli.run_digest(args) == 0

    assert FakeSlackChannelManager.instances[0].requested == ["feed-ops"]
    assert FakeSlackBotNotifier.instances[0].sent_texts[0][0] == "COPS"
    assert "Feed collector daily digest" in FakeSlackBotNotifier.instances[0].sent_texts[0][1]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT item_id FROM audit_log ORDER BY item_id").fetchall()
        channel_row = conn.execute("SELECT channel_id FROM sources WHERE id = 'feed-ops'").fetchone()
    assert rows == [("fresh",)]
    assert channel_row == ("COPS",)
