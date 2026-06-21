from __future__ import annotations

from datetime import datetime
from typing import Any

import feedparser
from dateutil import parser as date_parser

from feed_collector.adapter.outbound.http_fetch import ByteFetcher, http_fetcher_from_source
from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import Item, SourceConfig
from feed_collector.errors import PollError


class RssAdapter(SourcePort):
    def __init__(
        self,
        source: SourceConfig,
        *,
        fetcher: ByteFetcher | None = None,
        session: Any | None = None,
        timeout_seconds: int | None = None,
        retries: int | None = None,
        retry_delay_seconds: float | None = None,
        max_redirects: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.source = source
        self.fetcher: ByteFetcher = fetcher or http_fetcher_from_source(
            source,
            session=session,
            timeout_seconds=timeout_seconds,
            retries=retries,
            retry_delay_seconds=retry_delay_seconds,
            max_redirects=max_redirects,
            user_agent=user_agent,
        )

    def fetch(self) -> list[Item]:
        try:
            feed_bytes = self.fetcher.fetch(self.source.url)
        except PollError as exc:
            raise PollError(f"RSS fetch failed for {self.source.id}: {exc}") from exc
        return parse_items(
            feed_bytes,
            source_id=self.source.id,
            empty_result_policy=self.source.empty_result_policy,
        )


def parse_items(
    feed_bytes: bytes,
    *,
    source_id: str,
    empty_result_policy: str = "error",
) -> list[Item]:
    parsed = feedparser.parse(feed_bytes)
    entries: list[Any] = list(getattr(parsed, "entries", []))
    if getattr(parsed, "bozo", False) and not entries:
        raise PollError(f"RSS parse failed for {source_id}: {parsed.bozo_exception}")

    items = [item for entry in entries if (item := _entry_to_item(entry, source_id))]
    if not items and empty_result_policy == "error":
        raise PollError(f"RSS parse produced no items for {source_id}")
    return items


def _entry_to_item(entry: Any, source_id: str) -> Item | None:
    item_id = _entry_text(entry, "guid") or _entry_text(entry, "id") or _entry_text(entry, "link")
    if not item_id:
        return None

    published_text = (
        _entry_text(entry, "published")
        or _entry_text(entry, "updated")
        or _entry_text(entry, "pubDate")
        or _entry_text(entry, "date")
    )
    try:
        published = date_parser.parse(published_text) if published_text else None
    except (TypeError, ValueError, OverflowError) as exc:
        raise PollError(f"RSS entry has invalid published date for {source_id}: {published_text}") from exc

    return Item(
        item_id=item_id,
        title=_entry_text(entry, "title") or "(untitled)",
        link=_entry_text(entry, "link") or "",
        published=published,
    )


def _entry_text(entry: Any, key: str) -> str:
    value = entry.get(key)
    return str(value).strip() if value is not None else ""


__all__ = ["RssAdapter", "parse_items"]
