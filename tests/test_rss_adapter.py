from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from feed_collector.adapter.outbound import RssAdapter
from feed_collector.domain import SourceConfig
from feed_collector.errors import PollError


def make_source(empty_result_policy: str = "error") -> SourceConfig:
    return SourceConfig(
        id="mofa",
        slug="mofa-rss",
        name="MOFA RSS",
        mechanism="rss",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://www.mofa.go.kr/rss.xml",
        empty_result_policy=cast(Any, empty_result_policy),
    )


def test_rss_adapter_maps_entries_to_items() -> None:
    adapter = RssAdapter(make_source())
    feed_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>MOFA</title>
        <item>
          <guid>guid-1</guid>
          <title>First title</title>
          <link>https://example.test/first</link>
          <pubDate>Tue, 02 Jun 2026 15:30:00 +0900</pubDate>
        </item>
      </channel>
    </rss>
    """

    items = adapter.parse_items(feed_bytes)

    assert len(items) == 1
    assert items[0].item_id == "guid-1"
    assert items[0].title == "First title"
    assert items[0].link == "https://example.test/first"


def test_rss_adapter_parses_published_datetime_with_dateutil() -> None:
    adapter = RssAdapter(make_source())
    feed_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <guid>guid-1</guid>
          <title>First title</title>
          <link>https://example.test/first</link>
          <pubDate>Tue, 02 Jun 2026 15:30:00 +0900</pubDate>
        </item>
      </channel>
    </rss>
    """

    item = adapter.parse_items(feed_bytes)[0]

    assert item.published is not None
    assert item.published.year == 2026
    assert item.published.month == 6
    assert item.published.day == 2
    assert item.published.hour == 15
    assert item.published.tzinfo is not None
    offset = item.published.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 60 * 60


def test_rss_adapter_uses_item_id_priority_guid_id_link() -> None:
    adapter = RssAdapter(make_source())

    item = adapter._entry_to_item(
        {
            "guid": "guid-value",
            "id": "id-value",
            "link": "https://example.test/link",
            "title": "Title",
        }
    )

    assert item is not None
    assert item.item_id == "guid-value"


@dataclass
class FakeResponse:
    status_code: int
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeSession:
    responses: list[FakeResponse]
    max_redirects: int = 30
    calls: list[dict[str, Any]] = field(default_factory=list)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_rss_adapter_uses_two_hit_mofa_cookie_flow_with_bounded_redirects() -> None:
    session = FakeSession(
        [
            FakeResponse(307, headers={"Set-Cookie": "TMOSHCooKie=abc; Path=/"}),
            FakeResponse(200, content=b"<rss><channel><item><guid>g</guid><title>T</title></item></channel></rss>"),
        ]
    )
    adapter = RssAdapter(
        make_source(),
        session=cast(Any, session),
        timeout_seconds=7,
        retries=1,
        max_redirects=2,
    )

    items = adapter.fetch()

    assert [call["allow_redirects"] for call in session.calls] == [False, True]
    assert [call["timeout"] for call in session.calls] == [7, 7]
    assert "Mozilla/5.0" in session.calls[0]["headers"]["User-Agent"]
    assert session.max_redirects == 30
    assert [item.item_id for item in items] == ["g"]


def test_rss_adapter_raises_clear_error_for_non_200_after_fetch_attempts() -> None:
    session = FakeSession([FakeResponse(503)])
    adapter = RssAdapter(make_source(), session=cast(Any, session), retries=1)

    with pytest.raises(PollError, match="returned HTTP 503"):
        adapter.fetch_bytes()


def test_rss_adapter_empty_result_policy_controls_empty_feeds() -> None:
    empty_feed = b"<rss><channel><title>Empty</title></channel></rss>"

    assert RssAdapter(make_source(empty_result_policy="valid")).parse_items(empty_feed) == []
    with pytest.raises(PollError, match="produced no items"):
        RssAdapter(make_source()).parse_items(empty_feed)
