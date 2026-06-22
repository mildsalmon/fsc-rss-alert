from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from feed_collector.adapter.outbound import (
    HttpClient,
    HttpFetcherFactory,
    MofaCookieGateFetcher,
    RssAdapter,
    RssAdapterFactory,
)
from feed_collector.adapter.outbound.http_fetch import HttpFetchOptions
from feed_collector.adapter.outbound.rss import parse_items
from feed_collector.domain import ParamValue, SourceConfig
from feed_collector.errors import PollError


RSS_BYTES = b"""<?xml version="1.0" encoding="UTF-8"?>
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


def make_source(
    *,
    params: dict[str, ParamValue] | None = None,
    empty_result_policy: str = "error",
) -> SourceConfig:
    return SourceConfig(
        id="mofa",
        slug="mofa-rss",
        name="MOFA RSS",
        mechanism="rss",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://www.mofa.go.kr/rss.xml",
        params=params or {},
        empty_result_policy=cast(Any, empty_result_policy),
    )


@dataclass
class FakeFetcher:
    payload: bytes = RSS_BYTES
    called_urls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> bytes:
        self.called_urls.append(url)
        return self.payload


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


def test_rss_adapter_fetches_bytes_with_injected_fetcher_and_maps_items() -> None:
    fetcher = FakeFetcher()
    source = make_source()
    adapter = RssAdapter(source, fetcher=fetcher)

    items = adapter.fetch()

    assert fetcher.called_urls == [source.url]
    assert len(items) == 1
    assert items[0].item_id == "guid-1"
    assert items[0].title == "First title"
    assert items[0].link == "https://example.test/first"


def test_rss_adapter_parses_published_datetime_with_dateutil() -> None:
    item = parse_items(RSS_BYTES, source_id="mofa")[0]

    assert item.published is not None
    assert item.published.year == 2026
    assert item.published.month == 6
    assert item.published.day == 2
    assert item.published.hour == 15
    assert item.published.tzinfo is not None
    offset = item.published.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 60 * 60


def test_rss_adapter_uses_item_id_priority_guid_before_link_through_public_parser() -> None:
    feed_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <guid>guid-value</guid>
          <title>Title</title>
          <link>https://example.test/link</link>
        </item>
      </channel>
    </rss>
    """

    item = parse_items(feed_bytes, source_id="mofa")[0]

    assert item.item_id == "guid-value"


def test_rss_adapter_uses_link_when_guid_is_missing_through_public_parser() -> None:
    feed_bytes = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Title</title>
          <link>https://example.test/link</link>
        </item>
      </channel>
    </rss>
    """

    item = parse_items(feed_bytes, source_id="mofa")[0]

    assert item.item_id == "https://example.test/link"


def test_mofa_cookie_gate_fetcher_uses_two_hit_flow_with_bounded_redirects() -> None:
    session = FakeSession(
        [
            FakeResponse(307, headers={"Set-Cookie": "TMOSHCooKie=abc; Path=/"}),
            FakeResponse(200, content=RSS_BYTES),
        ]
    )
    fetcher = MofaCookieGateFetcher(
        client=HttpClient(
            options=HttpFetchOptions(
                timeout_seconds=7,
                retries=1,
                retry_delay_seconds=0,
                max_redirects=2,
                user_agent="test-agent",
            ),
            session=session,
        ),
    )

    payload = fetcher.fetch("https://www.mofa.go.kr/rss.xml")

    assert payload == RSS_BYTES
    assert [call["allow_redirects"] for call in session.calls] == [False, True]
    assert [call["timeout"] for call in session.calls] == [7, 7]
    assert [call["headers"]["User-Agent"] for call in session.calls] == ["test-agent", "test-agent"]
    assert session.max_redirects == 30


def test_rss_adapter_factory_selects_mofa_fetch_profile_from_config() -> None:
    session = FakeSession(
        [
            FakeResponse(307, headers={"Set-Cookie": "TMOSHCooKie=abc; Path=/"}),
            FakeResponse(200, content=RSS_BYTES),
        ]
    )
    source = make_source(params={"fetch_profile": "mofa_cookie_gate", "fetch_retry_delay_seconds": 0})
    adapter_factory = RssAdapterFactory(
        HttpFetcherFactory(
            session_factory=lambda: session,
            retries=1,
        )
    )

    items = adapter_factory.create(source).fetch()

    assert [item.item_id for item in items] == ["guid-1"]
    assert [call["allow_redirects"] for call in session.calls] == [False, True]


def test_rss_adapter_retries_fetch_failures_with_clear_source_context() -> None:
    session = FakeSession([FakeResponse(503), FakeResponse(503)])
    fetcher = HttpFetcherFactory(
        session_factory=lambda: session,
        retries=2,
        retry_delay_seconds=0,
    ).create(make_source())
    adapter = RssAdapter(make_source(), fetcher=fetcher)

    with pytest.raises(PollError, match="RSS fetch failed for mofa: .*after 2 attempts"):
        adapter.fetch()

    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ({"fetch_retries": 0}, "Source mofa param fetch_retries must be a positive integer"),
        ({"timeout_seconds": "bad"}, "Source mofa param timeout_seconds must be a positive integer"),
        (
            {"fetch_retry_delay_seconds": -1},
            "Source mofa param fetch_retry_delay_seconds must be a non-negative number",
        ),
        ({"max_redirects": True}, "Source mofa param max_redirects must be a positive integer"),
        ({"fetch_profile": "unknown"}, "Source mofa has unsupported fetch_profile"),
    ],
)
def test_rss_adapter_validates_fetch_params(params: dict[str, ParamValue], match: str) -> None:
    with pytest.raises(PollError, match=match):
        HttpFetcherFactory().create(make_source(params=params))


def test_rss_adapter_empty_result_policy_controls_empty_feeds() -> None:
    empty_feed = b"<rss><channel><title>Empty</title></channel></rss>"

    assert parse_items(empty_feed, source_id="mofa", empty_result_policy="valid") == []
    with pytest.raises(PollError, match="produced no items"):
        parse_items(empty_feed, source_id="mofa")
