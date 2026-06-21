from __future__ import annotations

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


@dataclass(frozen=True)
class HttpFetchOptions:
    timeout_seconds: int
    retries: int
    retry_delay_seconds: float
    max_redirects: int
    user_agent: str


@dataclass
class DefaultHttpFetcher(ByteFetcher):
    options: HttpFetchOptions
    session: Any = field(default_factory=requests.Session)

    def fetch(self, url: str) -> bytes:
        return _with_retries(
            lambda: self._fetch_once(url),
            retries=self.options.retries,
            retry_delay_seconds=self.options.retry_delay_seconds,
        )

    def _fetch_once(self, url: str) -> bytes:
        response = _get(
            self.session,
            url,
            options=self.options,
            allow_redirects=True,
        )
        _ensure_success(response)
        return bytes(response.content)


@dataclass
class MofaCookieGateFetcher(ByteFetcher):
    options: HttpFetchOptions
    cookie_name: str = MOFA_COOKIE_NAME
    session: Any = field(default_factory=requests.Session)

    def fetch(self, url: str) -> bytes:
        return _with_retries(
            lambda: self._fetch_once(url),
            retries=self.options.retries,
            retry_delay_seconds=self.options.retry_delay_seconds,
        )

    def _fetch_once(self, url: str) -> bytes:
        seed_response = _get(
            self.session,
            url,
            options=self.options,
            allow_redirects=False,
        )
        if 200 <= int(seed_response.status_code) < 300:
            return bytes(seed_response.content)
        if not _is_redirect(seed_response.status_code):
            _ensure_success(seed_response)

        set_cookie = seed_response.headers.get("Set-Cookie", "")
        if self.cookie_name.lower() not in set_cookie.lower():
            raise PollError(f"MOFA cookie gate did not set {self.cookie_name}")

        response = _get(
            self.session,
            url,
            options=self.options,
            allow_redirects=True,
        )
        _ensure_success(response)
        return bytes(response.content)


def http_fetcher_from_source(
    source: SourceConfig,
    *,
    session: Any | None = None,
    timeout_seconds: int | None = None,
    retries: int | None = None,
    retry_delay_seconds: float | None = None,
    max_redirects: int | None = None,
    user_agent: str | None = None,
) -> ByteFetcher:
    options = http_fetch_options_from_source(
        source,
        timeout_seconds=timeout_seconds,
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
        max_redirects=max_redirects,
        user_agent=user_agent,
    )
    profile = str(source.params.get("fetch_profile") or DEFAULT_FETCH_PROFILE)
    if profile == DEFAULT_FETCH_PROFILE:
        return DefaultHttpFetcher(options=options, session=session or requests.Session())
    if profile == MOFA_FETCH_PROFILE:
        return MofaCookieGateFetcher(options=options, session=session or requests.Session())
    raise PollError(
        f"Source {source.id} has unsupported fetch_profile {profile!r}; "
        f"expected one of: {DEFAULT_FETCH_PROFILE}, {MOFA_FETCH_PROFILE}"
    )


def http_fetch_options_from_source(
    source: SourceConfig,
    *,
    timeout_seconds: int | None = None,
    retries: int | None = None,
    retry_delay_seconds: float | None = None,
    max_redirects: int | None = None,
    user_agent: str | None = None,
) -> HttpFetchOptions:
    return HttpFetchOptions(
        timeout_seconds=_positive_int(
            source,
            "timeout_seconds",
            timeout_seconds,
            DEFAULT_TIMEOUT_SECONDS,
        ),
        retries=_positive_int(source, "fetch_retries", retries, DEFAULT_FETCH_RETRIES),
        retry_delay_seconds=_non_negative_float(
            source,
            "fetch_retry_delay_seconds",
            retry_delay_seconds,
            DEFAULT_FETCH_RETRY_DELAY_SECONDS,
        ),
        max_redirects=_positive_int(source, "max_redirects", max_redirects, DEFAULT_MAX_REDIRECTS),
        user_agent=user_agent or str(source.params.get("user_agent") or DEFAULT_RSS_USER_AGENT),
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


def _with_retries(fetch_once: Any, *, retries: int, retry_delay_seconds: float) -> bytes:
    last_error: PollError | None = None
    for attempt in range(1, retries + 1):
        try:
            return fetch_once()
        except PollError as exc:
            last_error = exc
            if attempt == retries:
                break
            if retry_delay_seconds:
                sleep(retry_delay_seconds)
    raise PollError(f"Feed fetch failed after {retries} attempts: {last_error}")


def _get(
    session: Any,
    url: str,
    *,
    options: HttpFetchOptions,
    allow_redirects: bool,
) -> requests.Response:
    original_max_redirects = getattr(session, "max_redirects", None)
    if hasattr(session, "max_redirects"):
        session.max_redirects = options.max_redirects
    try:
        return session.get(
            url,
            headers={"User-Agent": options.user_agent},
            timeout=options.timeout_seconds,
            allow_redirects=allow_redirects,
        )
    except requests.Timeout as exc:
        raise PollError("Feed fetch timed out") from exc
    except requests.TooManyRedirects as exc:
        raise PollError(f"Feed fetch exceeded {options.max_redirects} redirects") from exc
    except requests.RequestException as exc:
        raise PollError(f"Feed fetch failed: {exc}") from exc
    finally:
        if original_max_redirects is not None:
            session.max_redirects = original_max_redirects


def _ensure_success(response: requests.Response) -> None:
    status_code = int(response.status_code)
    if 200 <= status_code < 300:
        return
    raise PollError(f"Feed fetch returned HTTP {status_code}")


def _is_redirect(status_code: int) -> bool:
    return 300 <= int(status_code) < 400


__all__ = [
    "ByteFetcher",
    "DefaultHttpFetcher",
    "HttpFetchOptions",
    "MofaCookieGateFetcher",
    "http_fetch_options_from_source",
    "http_fetcher_from_source",
]
