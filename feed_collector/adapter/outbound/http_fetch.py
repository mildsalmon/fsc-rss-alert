from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import sleep
from typing import Any, Protocol

import requests

from feed_collector.config import (
    DEFAULT_FETCH_RETRIES,
    DEFAULT_FETCH_RETRY_DELAY_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from feed_collector.domain import SourceConfig
from feed_collector.errors import PollError


DEFAULT_RSS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 feed-collector/0.1"
)
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_FETCH_PROFILE = "default"
MOFA_FETCH_PROFILE = "mofa_cookie_gate"
MOFA_COOKIE_NAME = "TMOSHCooKie"


class ByteFetcher(Protocol):
    def fetch(self, url: str) -> bytes: ...


class ByteFetcherFactory(Protocol):
    def create(self, source: SourceConfig) -> ByteFetcher: ...


@dataclass(frozen=True)
class HttpFetchOptions:
    timeout_seconds: int
    retries: int
    retry_delay_seconds: float
    max_redirects: int
    user_agent: str


@dataclass
class HttpClient:
    options: HttpFetchOptions
    session: Any = field(default_factory=requests.Session)
    sleep_fn: Callable[[float], None] = sleep

    def get(self, url: str, *, allow_redirects: bool) -> requests.Response:
        original_max_redirects = getattr(self.session, "max_redirects", None)
        if hasattr(self.session, "max_redirects"):
            self.session.max_redirects = self.options.max_redirects
        try:
            return self.session.get(
                url,
                headers={"User-Agent": self.options.user_agent},
                timeout=self.options.timeout_seconds,
                allow_redirects=allow_redirects,
            )
        except requests.Timeout as exc:
            raise PollError("Feed fetch timed out") from exc
        except requests.TooManyRedirects as exc:
            raise PollError(f"Feed fetch exceeded {self.options.max_redirects} redirects") from exc
        except requests.RequestException as exc:
            raise PollError(f"Feed fetch failed: {exc}") from exc
        finally:
            if original_max_redirects is not None:
                self.session.max_redirects = original_max_redirects

    def ensure_success(self, response: requests.Response) -> None:
        status_code = int(response.status_code)
        if 200 <= status_code < 300:
            return
        raise PollError(f"Feed fetch returned HTTP {status_code}")

    def is_redirect(self, response: requests.Response) -> bool:
        return 300 <= int(response.status_code) < 400

    def fetch_with_retries(self, fetch_once: Callable[[], bytes]) -> bytes:
        last_error: PollError | None = None
        for attempt in range(1, self.options.retries + 1):
            try:
                return fetch_once()
            except PollError as exc:
                last_error = exc
                if attempt == self.options.retries:
                    break
                if self.options.retry_delay_seconds:
                    self.sleep_fn(self.options.retry_delay_seconds)
        raise PollError(f"Feed fetch failed after {self.options.retries} attempts: {last_error}")


@dataclass
class DefaultHttpFetcher(ByteFetcher):
    client: HttpClient

    def fetch(self, url: str) -> bytes:
        return self.client.fetch_with_retries(lambda: self._fetch_once(url))

    def _fetch_once(self, url: str) -> bytes:
        response = self.client.get(url, allow_redirects=True)
        self.client.ensure_success(response)
        return bytes(response.content)


@dataclass
class MofaCookieGateFetcher(ByteFetcher):
    client: HttpClient
    cookie_name: str = MOFA_COOKIE_NAME

    def fetch(self, url: str) -> bytes:
        return self.client.fetch_with_retries(lambda: self._fetch_once(url))

    def _fetch_once(self, url: str) -> bytes:
        seed_response = self.client.get(url, allow_redirects=False)
        if 200 <= int(seed_response.status_code) < 300:
            return bytes(seed_response.content)
        if not self.client.is_redirect(seed_response):
            self.client.ensure_success(seed_response)

        set_cookie = seed_response.headers.get("Set-Cookie", "")
        if self.cookie_name.lower() not in set_cookie.lower():
            raise PollError(f"MOFA cookie gate did not set {self.cookie_name}")

        response = self.client.get(url, allow_redirects=True)
        self.client.ensure_success(response)
        return bytes(response.content)


@dataclass(frozen=True)
class HttpFetcherFactory(ByteFetcherFactory):
    session_factory: Callable[[], Any] = requests.Session
    timeout_seconds: int | None = None
    retries: int | None = None
    retry_delay_seconds: float | None = None
    max_redirects: int | None = None
    user_agent: str | None = None

    def create(self, source: SourceConfig) -> ByteFetcher:
        options = self.options_from_source(source)
        client = HttpClient(options=options, session=self.session_factory())
        profile = str(source.params.get("fetch_profile") or DEFAULT_FETCH_PROFILE)
        if profile == DEFAULT_FETCH_PROFILE:
            return DefaultHttpFetcher(client=client)
        if profile == MOFA_FETCH_PROFILE:
            return MofaCookieGateFetcher(client=client)
        raise PollError(
            f"Source {source.id} has unsupported fetch_profile {profile!r}; "
            f"expected one of: {DEFAULT_FETCH_PROFILE}, {MOFA_FETCH_PROFILE}"
        )

    def options_from_source(self, source: SourceConfig) -> HttpFetchOptions:
        return HttpFetchOptions(
            timeout_seconds=_positive_int(
                source,
                "timeout_seconds",
                self.timeout_seconds,
                DEFAULT_TIMEOUT_SECONDS,
            ),
            retries=_positive_int(source, "fetch_retries", self.retries, DEFAULT_FETCH_RETRIES),
            retry_delay_seconds=_non_negative_float(
                source,
                "fetch_retry_delay_seconds",
                self.retry_delay_seconds,
                DEFAULT_FETCH_RETRY_DELAY_SECONDS,
            ),
            max_redirects=_positive_int(source, "max_redirects", self.max_redirects, DEFAULT_MAX_REDIRECTS),
            user_agent=self.user_agent or str(source.params.get("user_agent") or DEFAULT_RSS_USER_AGENT),
        )


def _positive_int(source: SourceConfig, key: str, explicit: int | None, default: int) -> int:
    raw_value: object = explicit if explicit is not None else source.params.get(key, default)
    if raw_value is None or isinstance(raw_value, bool):
        raise PollError(f"Source {source.id} param {key} must be a positive integer")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise PollError(f"Source {source.id} param {key} must be a positive integer") from exc
    if value < 1:
        raise PollError(f"Source {source.id} param {key} must be a positive integer")
    return value


def _non_negative_float(source: SourceConfig, key: str, explicit: float | None, default: float) -> float:
    raw_value: object = explicit if explicit is not None else source.params.get(key, default)
    if raw_value is None or isinstance(raw_value, bool):
        raise PollError(f"Source {source.id} param {key} must be a non-negative number")
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise PollError(f"Source {source.id} param {key} must be a non-negative number") from exc
    if value < 0:
        raise PollError(f"Source {source.id} param {key} must be a non-negative number")
    return value


__all__ = [
    "ByteFetcher",
    "ByteFetcherFactory",
    "DefaultHttpFetcher",
    "HttpClient",
    "HttpFetcherFactory",
    "HttpFetchOptions",
    "MofaCookieGateFetcher",
]
