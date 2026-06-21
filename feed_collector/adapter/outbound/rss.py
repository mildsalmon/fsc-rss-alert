from __future__ import annotations

from time import sleep
from typing import Any, cast

import feedparser
import requests
from dateutil import parser as date_parser

from feed_collector.application.port.output.source import SourcePort
from feed_collector.config import (
    DEFAULT_FETCH_RETRIES,
    DEFAULT_FETCH_RETRY_DELAY_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from feed_collector.domain import Item, ParamValue, SourceConfig
from feed_collector.errors import PollError


DEFAULT_RSS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 feed-collector/0.1"
)
DEFAULT_MAX_REDIRECTS = 5
REQUESTS_DEFAULT_MAX_REDIRECTS = 30
MOFA_COOKIE_NAME = "TMOSHCooKie"


class RssAdapter(SourcePort):
    def __init__(
        self,
        source: SourceConfig,
        *,
        session: requests.Session | None = None,
        timeout_seconds: int | None = None,
        retries: int | None = None,
        retry_delay_seconds: float | None = None,
        max_redirects: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.source = source
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds or _int_param(
            source.params,
            "timeout_seconds",
            DEFAULT_TIMEOUT_SECONDS,
        )
        self._retries = retries or _int_param(source.params, "fetch_retries", DEFAULT_FETCH_RETRIES)
        self._retry_delay_seconds = (
            retry_delay_seconds
            if retry_delay_seconds is not None
            else _float_param(
                source.params,
                "fetch_retry_delay_seconds",
                DEFAULT_FETCH_RETRY_DELAY_SECONDS,
            )
        )
        self._max_redirects = max_redirects or _int_param(
            source.params,
            "max_redirects",
            DEFAULT_MAX_REDIRECTS,
        )
        self._headers = {"User-Agent": user_agent or DEFAULT_RSS_USER_AGENT}

    def fetch(self) -> list[Item]:
        return self.parse_items(self.fetch_bytes())

    def fetch_bytes(self) -> bytes:
        last_error: PollError | None = None
        for attempt in range(1, self._retries + 1):
            try:
                return self._fetch_once()
            except PollError as exc:
                last_error = exc
                if attempt == self._retries:
                    break
                if self._retry_delay_seconds:
                    sleep(self._retry_delay_seconds)

        raise PollError(f"RSS fetch failed for {self.source.id} after {self._retries} attempts: {last_error}")

    def parse_items(self, feed_bytes: bytes) -> list[Item]:
        parsed = feedparser.parse(feed_bytes)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise PollError(f"RSS parse failed for {self.source.id}: {parsed.bozo_exception}")

        items = [item for entry in parsed.entries if (item := self._entry_to_item(entry))]
        if not items and self.source.empty_result_policy == "error":
            raise PollError(f"RSS parse produced no items for {self.source.id}")
        return items

    def _fetch_once(self) -> bytes:
        try:
            response = self._session.get(
                self.source.url,
                headers=self._headers,
                timeout=self._timeout_seconds,
                allow_redirects=False,
            )
            if _needs_mofa_cookie_retry(response):
                response = self._get_with_bounded_redirects(self.source.url)
            elif _is_redirect(response.status_code):
                response = self._get_with_bounded_redirects(self.source.url)
        except requests.RequestException as exc:
            raise PollError(f"RSS fetch failed for {self.source.id}: {exc}") from exc

        if response.status_code != 200:
            raise PollError(f"RSS fetch for {self.source.id} returned HTTP {response.status_code}")
        return response.content

    def _get_with_bounded_redirects(self, url: str) -> requests.Response:
        original_max_redirects = getattr(self._session, "max_redirects", REQUESTS_DEFAULT_MAX_REDIRECTS)
        self._session.max_redirects = self._max_redirects
        try:
            return self._session.get(
                url,
                headers=self._headers,
                timeout=self._timeout_seconds,
                allow_redirects=True,
            )
        except requests.TooManyRedirects as exc:
            raise PollError(
                f"RSS fetch for {self.source.id} exceeded {self._max_redirects} redirects"
            ) from exc
        finally:
            self._session.max_redirects = original_max_redirects

    def _entry_to_item(self, entry: Any) -> Item | None:
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
            raise PollError(f"RSS entry has invalid published date for {self.source.id}: {published_text}") from exc

        return Item(
            item_id=item_id,
            title=_entry_text(entry, "title") or "(untitled)",
            link=_entry_text(entry, "link") or "",
            published=published,
        )


def _needs_mofa_cookie_retry(response: requests.Response) -> bool:
    set_cookie = response.headers.get("Set-Cookie", "")
    return response.status_code == 307 and MOFA_COOKIE_NAME.lower() in set_cookie.lower()


def _is_redirect(status_code: int) -> bool:
    return 300 <= status_code < 400


def _entry_text(entry: Any, key: str) -> str:
    value = entry.get(key)
    return str(value).strip() if value is not None else ""


def _int_param(params: Any, key: str, default: int) -> int:
    value = cast(ParamValue, params.get(key, default))
    if value is None or isinstance(value, bool):
        return default
    return int(value)


def _float_param(params: Any, key: str, default: float) -> float:
    value = cast(ParamValue, params.get(key, default))
    if value is None or isinstance(value, bool):
        return default
    return float(value)
