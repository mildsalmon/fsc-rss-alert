from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from feed_collector.adapter.outbound import (
    SlackApiError,
    SlackBotNotifier,
    SlackChannelManager,
    feed_channel_name,
    format_feed_channel_purpose,
    format_feed_channel_topic,
    format_slack_item_message,
)
from feed_collector.application.port.output import ChannelProvisionerPort
from feed_collector.domain import Item


@dataclass
class FakeResponse:
    data: dict[str, Any]
    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        return self.data

    def raise_for_status(self) -> None:
        return None


@dataclass
class FakeSlackSession:
    post_responses: list[FakeResponse]
    get_responses: list[FakeResponse] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)
    gets: list[dict[str, Any]] = field(default_factory=list)

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout: float,
    ) -> FakeResponse:
        self.posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.post_responses.pop(0)

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.gets.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return self.get_responses.pop(0)


def make_item() -> Item:
    return Item(
        item_id="item-1",
        title=" Policy update ",
        link="https://example.test/policy",
        published=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )


def make_markup_item() -> Item:
    return Item(
        item_id="item-2",
        title=" <@channel> & <policy> ",
        link="https://example.test/policy?a=1&b=<x>",
        published=None,
    )


def test_format_slack_item_message_preserves_title_date_link() -> None:
    assert (
        format_slack_item_message(make_item())
        == "> 제목: *Policy update*\n날짜: 2026-01-02 03:04:05\n링크: https://example.test/policy"
    )


def test_format_slack_item_message_escapes_slack_control_chars() -> None:
    assert (
        format_slack_item_message(make_markup_item())
        == "> 제목: *&lt;@channel&gt; &amp; &lt;policy&gt;*\n"
        "날짜: unknown\n"
        "링크: https://example.test/policy?a=1&amp;b=&lt;x&gt;"
    )


def test_slack_bot_notifier_posts_chat_message() -> None:
    session = FakeSlackSession([FakeResponse({"ok": True, "ts": "123.456"})])
    notifier = SlackBotNotifier(bot_token="xoxb-test", session=session, timeout_seconds=3)

    delivery_id = notifier.send("C123", make_item())

    assert delivery_id == "123.456"
    assert len(session.posts) == 1
    post = session.posts[0]
    assert post["url"] == "https://slack.com/api/chat.postMessage"
    assert post["headers"]["Authorization"] == "Bearer xoxb-test"
    assert post["timeout"] == 3
    assert post["json"] == {
        "channel": "C123",
        "text": "> 제목: *Policy update*\n날짜: 2026-01-02 03:04:05\n링크: https://example.test/policy",
        "unfurl_links": False,
        "mrkdwn": True,
    }


def test_slack_bot_notifier_posts_plain_text_message() -> None:
    session = FakeSlackSession([FakeResponse({"ok": True, "ts": "123.456"})])
    notifier = SlackBotNotifier(bot_token="xoxb-test", session=session)

    delivery_id = notifier.send_text("COPS", "daily digest")

    assert delivery_id == "123.456"
    assert session.posts[0]["json"] == {
        "channel": "COPS",
        "text": "daily digest",
        "unfurl_links": False,
        "mrkdwn": False,
    }


def test_slack_bot_notifier_rejects_malformed_success_without_ts() -> None:
    session = FakeSlackSession([FakeResponse({"ok": True})])
    notifier = SlackBotNotifier(bot_token="xoxb-test", session=session)

    with pytest.raises(SlackApiError, match="returned no message ts"):
        notifier.send("C123", make_item())


def test_slack_bot_notifier_raises_for_slack_error() -> None:
    session = FakeSlackSession([FakeResponse({"ok": False, "error": "channel_not_found"})])
    notifier = SlackBotNotifier(bot_token="xoxb-test", session=session)

    with pytest.raises(SlackApiError, match="chat.postMessage failed: channel_not_found"):
        notifier.send("C404", make_item())


def test_slack_bot_notifier_retries_http_429_once() -> None:
    session = FakeSlackSession(
        [
            FakeResponse({"ok": False, "error": "rate_limited"}, status_code=429, headers={"Retry-After": "0"}),
            FakeResponse({"ok": True, "ts": "123.456"}),
        ]
    )
    notifier = SlackBotNotifier(bot_token="xoxb-test", session=session)

    assert notifier.send("C123", make_item()) == "123.456"
    assert len(session.posts) == 2


def test_slack_channel_manager_reuses_existing_channel_on_name_taken() -> None:
    session = FakeSlackSession(
        post_responses=[
            FakeResponse({"ok": False, "error": "name_taken"}),
            FakeResponse({"ok": True}),
            FakeResponse({"ok": True}),
        ],
        get_responses=[
            FakeResponse(
                {
                    "ok": True,
                    "channels": [{"id": "CFEED", "name": "feed-feed-ops", "is_member": True}],
                    "response_metadata": {"next_cursor": ""},
                }
            )
        ],
    )
    manager = SlackChannelManager(bot_token="xoxb-test", session=session)
    port: ChannelProvisionerPort = manager

    channel_id = port.ensure_feed_channel("feed ops")

    assert channel_id == "CFEED"
    assert session.posts[0]["url"] == "https://slack.com/api/conversations.create"
    assert session.posts[0]["json"] == {"name": "feed-feed-ops"}
    assert session.posts[1]["url"] == "https://slack.com/api/conversations.setPurpose"
    assert session.posts[1]["json"] == {"channel": "CFEED", "purpose": "Feed Collector channel: feed ops."}
    assert session.posts[2]["url"] == "https://slack.com/api/conversations.setTopic"
    assert session.posts[2]["json"] == {"channel": "CFEED", "topic": "feed ops"}
    assert session.gets[0]["url"] == "https://slack.com/api/conversations.list"
    assert session.gets[0]["params"]["types"] == "public_channel"


def test_slack_channel_manager_joins_existing_public_channel_on_name_taken() -> None:
    session = FakeSlackSession(
        post_responses=[
            FakeResponse({"ok": False, "error": "name_taken"}),
            FakeResponse({"ok": True, "channel": {"id": "CFEED"}}),
            FakeResponse({"ok": True}),
            FakeResponse({"ok": True}),
        ],
        get_responses=[
            FakeResponse(
                {
                    "ok": True,
                    "channels": [{"id": "CFEED", "name": "feed-feed-ops", "is_member": False}],
                    "response_metadata": {"next_cursor": ""},
                }
            )
        ],
    )
    manager = SlackChannelManager(bot_token="xoxb-test", session=session)

    assert manager.ensure_feed_channel("feed ops") == "CFEED"
    assert session.gets[0]["params"]["types"] == "public_channel"
    assert session.posts[1]["url"] == "https://slack.com/api/conversations.join"
    assert session.posts[1]["json"] == {"channel": "CFEED"}
    assert session.posts[2]["url"] == "https://slack.com/api/conversations.setPurpose"
    assert session.posts[3]["url"] == "https://slack.com/api/conversations.setTopic"


def test_slack_channel_manager_rejects_existing_private_channel_when_not_member() -> None:
    session = FakeSlackSession(
        post_responses=[FakeResponse({"ok": False, "error": "name_taken"})],
        get_responses=[
            FakeResponse(
                {
                    "ok": True,
                    "channels": [
                        {"id": "GFEED", "name": "feed-feed-ops", "is_member": False, "is_private": True}
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            )
        ],
    )
    manager = SlackChannelManager(bot_token="xoxb-test", session=session)

    with pytest.raises(SlackApiError, match="not a member"):
        manager.ensure_feed_channel("feed ops")


def test_slack_channel_manager_creates_feed_channel() -> None:
    session = FakeSlackSession(
        [
            FakeResponse({"ok": True, "channel": {"id": "CNEW"}}),
            FakeResponse({"ok": True}),
            FakeResponse({"ok": True}),
        ]
    )
    manager = SlackChannelManager(bot_token="xoxb-test", session=session)

    assert manager.ensure_feed_channel("FSC notices") == "CNEW"
    assert session.posts[0]["json"] == {"name": "feed-fsc-notices"}
    assert session.posts[1]["json"] == {"channel": "CNEW", "purpose": "Feed Collector channel: FSC notices."}
    assert session.posts[2]["json"] == {"channel": "CNEW", "topic": "FSC notices"}


def test_slack_channel_manager_sets_source_metadata() -> None:
    session = FakeSlackSession(
        [
            FakeResponse({"ok": True, "channel": {"id": "CNEW"}}),
            FakeResponse({"ok": True}),
            FakeResponse({"ok": True}),
        ]
    )
    manager = SlackChannelManager(bot_token="xoxb-test", session=session)

    assert (
        manager.ensure_feed_channel(
            "fsc-lawreq",
            display_name="FSC law requests",
            source_url="https://better.fsc.go.kr/fsc_new/replyCase/selectReplyCaseLawreqList.do",
        )
        == "CNEW"
    )

    assert session.posts[1]["json"] == {
        "channel": "CNEW",
        "purpose": "Feed Collector source: FSC law requests. Base URL: "
        "https://better.fsc.go.kr/fsc_new/replyCase/selectReplyCaseLawreqList.do",
    }
    assert session.posts[2]["json"] == {
        "channel": "CNEW",
        "topic": "FSC law requests",
    }


def test_slack_channel_metadata_update_is_best_effort_for_scope_errors() -> None:
    session = FakeSlackSession(
        [
            FakeResponse({"ok": False, "error": "missing_scope"}),
            FakeResponse({"ok": False, "error": "missing_scope"}),
        ]
    )
    manager = SlackChannelManager(bot_token="xoxb-test", session=session)

    assert manager.update_feed_channel_metadata("CFEED", display_name="FSC") is False

    assert [post["url"] for post in session.posts] == [
        "https://slack.com/api/conversations.setPurpose",
    ]


def test_feed_channel_metadata_formatters_trim_to_slack_limit() -> None:
    purpose = format_feed_channel_purpose(display_name="A" * 260, source_url="https://example.test/source")
    topic = format_feed_channel_topic(display_name="A" * 260, source_url="https://example.test/source")

    assert len(purpose) == 250
    assert purpose.endswith("...")
    assert len(topic) == 250
    assert topic.endswith("...")


def test_feed_channel_name_is_deterministic() -> None:
    assert feed_channel_name("Feed Ops!") == "feed-feed-ops"
    assert feed_channel_name("  ") == "feed-source"


def test_feed_channel_name_adds_hash_suffix_when_truncated() -> None:
    first = feed_channel_name("a" * 100)
    second = feed_channel_name("a" * 99 + "b")

    assert len(first) == 80
    assert len(second) == 80
    assert first != second
    assert first.startswith("feed-")
