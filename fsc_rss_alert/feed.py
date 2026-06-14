from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import feedparser

from fsc_rss_alert.config import USER_AGENT
from fsc_rss_alert.errors import PollError


@dataclass(frozen=True)
class FeedEntry:
    entry_id: str
    title: str
    link: str
    published: str


def fetch_feed(url: str, timeout_seconds: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise PollError(f"Feed fetch returned HTTP {status}")
            return response.read()
    except urllib.error.HTTPError as exc:
        raise PollError(f"Feed fetch returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise PollError(f"Feed fetch failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise PollError("Feed fetch timed out") from exc


def normalize_entry(raw_entry: Any) -> FeedEntry | None:
    entry_id = raw_entry.get("id") or raw_entry.get("guid") or raw_entry.get("link")
    if not entry_id:
        return None
    return FeedEntry(
        entry_id=str(entry_id).strip(),
        title=str(raw_entry.get("title") or "(untitled)").strip(),
        link=str(raw_entry.get("link") or "").strip(),
        published=str(
            raw_entry.get("published")
            or raw_entry.get("updated")
            or raw_entry.get("pubDate")
            or raw_entry.get("date")
            or ""
        ).strip(),
    )


def parse_entries(feed_bytes: bytes) -> tuple[str, list[FeedEntry]]:
    parsed = feedparser.parse(feed_bytes)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise PollError(f"Feed parse failed: {parsed.bozo_exception}")
    if not parsed.entries:
        raise PollError("Feed parse produced no entries")

    entries = [entry for raw_entry in parsed.entries if (entry := normalize_entry(raw_entry))]
    if not entries:
        raise PollError("Feed entries did not include guid or link values")

    feed_title = str(parsed.feed.get("title") or "FSC RSS").strip()
    return feed_title, entries

