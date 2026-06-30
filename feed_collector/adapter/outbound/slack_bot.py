from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Callable, Literal, Protocol, cast

import requests

from feed_collector.application.port.output.channel_provisioner import ChannelProvisionerPort
from feed_collector.application.port.output.notifier import NotifierPort
from feed_collector.domain import Item


SLACK_API_BASE_URL = "https://slack.com/api"
SLACK_CHANNEL_METADATA_LIMIT = 250
SLACK_CHANNEL_METADATA_ALLOWED_ERRORS = (
    "missing_scope",
    "method_not_supported_for_channel_type",
    "no_permission",
    "not_in_channel",
    "user_is_restricted",
)


class SlackApiError(RuntimeError):
    """Raised when Slack returns an unsuccessful API response."""


class SlackResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class SlackHttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout: float,
    ) -> SlackResponse: ...

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        timeout: float,
    ) -> SlackResponse: ...


def escape_slack_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_slack_item_message(item: Item) -> str:
    title = escape_slack_text(item.title.strip() or "(untitled)")
    published = item.published.isoformat() if item.published is not None else "unknown"
    link = escape_slack_text(item.link.strip() or "(no link)")
    return f"{title}\nDate: {published}\nLink: {link}"


class _SlackApi:
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        session: SlackHttpClient | None = None,
        timeout_seconds: float = 10,
        api_base_url: str = SLACK_API_BASE_URL,
        retry_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN")
        self.session = session if session is not None else cast(SlackHttpClient, requests.Session())
        self.timeout_seconds = timeout_seconds
        self.api_base_url = api_base_url.rstrip("/")
        self.retry_sleep = retry_sleep

    def post(
        self,
        method: str,
        payload: Mapping[str, object],
        *,
        allowed_errors: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        return self._request("post", method, payload=payload, allowed_errors=allowed_errors)

    def get(
        self,
        method: str,
        params: Mapping[str, str],
        *,
        allowed_errors: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        return self._request("get", method, params=params, allowed_errors=allowed_errors)

    def _request(
        self,
        http_method: Literal["get", "post"],
        method: str,
        *,
        payload: Mapping[str, object] | None = None,
        params: Mapping[str, str] | None = None,
        allowed_errors: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        if not self.bot_token:
            raise ValueError("Slack bot token is required")

        response: SlackResponse | None = None
        for attempt in range(2):
            if http_method == "post":
                response = self.session.post(
                    f"{self.api_base_url}/{method}",
                    headers=self._headers(),
                    json=payload or {},
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.session.get(
                    f"{self.api_base_url}/{method}",
                    headers=self._headers(),
                    params=params or {},
                    timeout=self.timeout_seconds,
                )
            if response.status_code == 429:
                retry_after = _retry_after_seconds(response.headers)
                if attempt == 0:
                    self.retry_sleep(retry_after)
                    continue
                raise SlackApiError(f"Slack {method} rate limited; retry after {retry_after:g}s")
            break
        assert response is not None
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise SlackApiError(f"Slack {method} returned a non-object response")
        if data.get("ok") is not True:
            error = str(data.get("error", "unknown_error"))
            if error in allowed_errors:
                return data
            raise SlackApiError(f"Slack {method} failed: {error}")
        return data

    def _headers(self) -> Mapping[str, str]:
        assert self.bot_token is not None
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }


class SlackBotNotifier(NotifierPort):
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        session: SlackHttpClient | None = None,
        timeout_seconds: float = 10,
        api_base_url: str = SLACK_API_BASE_URL,
        retry_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api = _SlackApi(
            bot_token=bot_token,
            session=session,
            timeout_seconds=timeout_seconds,
            api_base_url=api_base_url,
            retry_sleep=retry_sleep,
        )

    def send(self, channel_id: str, item: Item) -> str:
        return self.send_text(channel_id, format_slack_item_message(item))

    def send_text(self, channel_id: str, text: str) -> str:
        data = self.api.post(
            "chat.postMessage",
            {
                "channel": channel_id,
                "text": text,
                "unfurl_links": False,
                "mrkdwn": False,
            },
        )
        ts = data.get("ts")
        if not isinstance(ts, str) or not ts:
            raise SlackApiError("Slack chat.postMessage returned no message ts")
        return ts


class SlackChannelManager(ChannelProvisionerPort):
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        session: SlackHttpClient | None = None,
        timeout_seconds: float = 10,
        api_base_url: str = SLACK_API_BASE_URL,
        retry_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api = _SlackApi(
            bot_token=bot_token,
            session=session,
            timeout_seconds=timeout_seconds,
            api_base_url=api_base_url,
            retry_sleep=retry_sleep,
        )

    def ensure_feed_channel(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        source_url: str | None = None,
    ) -> str:
        name = feed_channel_name(slug)
        data = self.api.post("conversations.create", {"name": name}, allowed_errors=("name_taken",))
        channel_id = _channel_id_from_data(data)
        if channel_id is not None:
            self.update_feed_channel_metadata(channel_id, display_name=display_name or slug, source_url=source_url)
            return channel_id

        if data.get("error") == "name_taken":
            existing = self.find_channel_by_name(name)
            if existing is not None:
                channel_id = self._ensure_can_post(existing)
                self.update_feed_channel_metadata(channel_id, display_name=display_name or slug, source_url=source_url)
                return channel_id

        error = data.get("error", "unknown_error")
        raise SlackApiError(f"Slack conversations.create failed: {error}")

    def update_feed_channel_metadata(
        self,
        channel_id: str,
        *,
        display_name: str | None = None,
        source_url: str | None = None,
    ) -> bool:
        purpose = format_feed_channel_purpose(display_name=display_name, source_url=source_url)
        topic = format_feed_channel_topic(display_name=display_name, source_url=source_url)
        for method, payload_key, text in (
            ("conversations.setPurpose", "purpose", purpose),
            ("conversations.setTopic", "topic", topic),
        ):
            try:
                data = self.api.post(
                    method,
                    {"channel": channel_id, payload_key: text},
                    allowed_errors=SLACK_CHANNEL_METADATA_ALLOWED_ERRORS,
                )
            except Exception:  # noqa: BLE001
                return False
            if data.get("ok") is not True:
                return False
        return True

    def find_channel_by_name(self, name: str) -> Mapping[str, Any] | None:
        cursor = ""
        while True:
            params = {"exclude_archived": "true", "limit": "1000", "types": "public_channel"}
            if cursor:
                params["cursor"] = cursor
            data = self.api.get("conversations.list", params)
            for channel in data.get("channels", []):
                if isinstance(channel, Mapping) and channel.get("name") == name:
                    return channel
            metadata = data.get("response_metadata")
            cursor = ""
            if isinstance(metadata, Mapping):
                next_cursor = metadata.get("next_cursor")
                if isinstance(next_cursor, str):
                    cursor = next_cursor
            if not cursor:
                return None

    def find_channel_id_by_name(self, name: str) -> str | None:
        channel = self.find_channel_by_name(name)
        if channel is None:
            return None
        channel_id = channel.get("id")
        return channel_id if isinstance(channel_id, str) else None

    def _ensure_can_post(self, channel: Mapping[str, Any]) -> str:
        channel_id = channel.get("id")
        if not isinstance(channel_id, str) or not channel_id:
            raise SlackApiError("Slack conversations.list returned channel without id")
        if channel.get("is_member") is True:
            return channel_id
        if channel.get("is_private") is True:
            raise SlackApiError(f"Slack channel {channel_id} exists but the bot is not a member")

        data = self.api.post("conversations.join", {"channel": channel_id})
        joined_channel_id = _channel_id_from_data(data)
        return joined_channel_id or channel_id


def _retry_after_seconds(headers: Mapping[str, str]) -> float:
    value = headers.get("Retry-After", "1")
    try:
        retry_after = float(value)
    except ValueError:
        return 1
    return max(retry_after, 0)


def feed_channel_name(slug: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-")
    if not normalized:
        normalized = "source"
    channel_name = f"feed-{normalized}"
    if len(channel_name) <= 80:
        return channel_name
    suffix = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{channel_name[:71]}-{suffix}"


def format_feed_channel_purpose(*, display_name: str | None = None, source_url: str | None = None) -> str:
    name = _metadata_value(display_name, "Feed Collector")
    if source_url:
        return _truncate_metadata(f"Feed Collector source: {name}. Base URL: {source_url.strip()}")
    return _truncate_metadata(f"Feed Collector channel: {name}.")


def format_feed_channel_topic(*, display_name: str | None = None, source_url: str | None = None) -> str:
    del source_url
    name = _metadata_value(display_name, "Feed Collector")
    return _truncate_metadata(name)


def _metadata_value(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    stripped = value.strip()
    return stripped or fallback


def _truncate_metadata(value: str) -> str:
    if len(value) <= SLACK_CHANNEL_METADATA_LIMIT:
        return value
    return f"{value[: SLACK_CHANNEL_METADATA_LIMIT - 3]}..."


def _channel_id_from_data(data: Mapping[str, Any]) -> str | None:
    channel = data.get("channel")
    if not isinstance(channel, Mapping):
        return None
    channel_id = channel.get("id")
    return channel_id if isinstance(channel_id, str) else None
