from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast

import requests

from feed_collector.application.port.output.channel_provisioner import ChannelProvisionerPort
from feed_collector.application.port.output.notifier import NotifierPort
from feed_collector.domain import Item


SLACK_API_BASE_URL = "https://slack.com/api"


class SlackApiError(RuntimeError):
    """Raised when Slack returns an unsuccessful API response."""


class SlackResponse(Protocol):
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
    ) -> None:
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN")
        self.session = session if session is not None else cast(SlackHttpClient, requests.Session())
        self.timeout_seconds = timeout_seconds
        self.api_base_url = api_base_url.rstrip("/")

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
    ) -> None:
        self.api = _SlackApi(
            bot_token=bot_token,
            session=session,
            timeout_seconds=timeout_seconds,
            api_base_url=api_base_url,
        )

    def send(self, channel_id: str, item: Item) -> str:
        data = self.api.post(
            "chat.postMessage",
            {
                "channel": channel_id,
                "text": format_slack_item_message(item),
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
    ) -> None:
        self.api = _SlackApi(
            bot_token=bot_token,
            session=session,
            timeout_seconds=timeout_seconds,
            api_base_url=api_base_url,
        )

    def ensure_feed_channel(self, slug: str) -> str:
        name = feed_channel_name(slug)
        data = self.api.post("conversations.create", {"name": name}, allowed_errors=("name_taken",))
        channel_id = _channel_id_from_data(data)
        if channel_id is not None:
            return channel_id

        if data.get("error") == "name_taken":
            existing = self.find_channel_id_by_name(name)
            if existing is not None:
                return existing

        error = data.get("error", "unknown_error")
        raise SlackApiError(f"Slack conversations.create failed: {error}")

    def find_channel_id_by_name(self, name: str) -> str | None:
        cursor = ""
        while True:
            params = {"exclude_archived": "true", "limit": "1000", "types": "public_channel,private_channel"}
            if cursor:
                params["cursor"] = cursor
            data = self.api.get("conversations.list", params)
            for channel in data.get("channels", []):
                if isinstance(channel, Mapping) and channel.get("name") == name:
                    channel_id = channel.get("id")
                    if isinstance(channel_id, str):
                        return channel_id
            metadata = data.get("response_metadata")
            cursor = ""
            if isinstance(metadata, Mapping):
                next_cursor = metadata.get("next_cursor")
                if isinstance(next_cursor, str):
                    cursor = next_cursor
            if not cursor:
                return None


def feed_channel_name(slug: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-")
    if not normalized:
        normalized = "source"
    channel_name = f"feed-{normalized}"
    if len(channel_name) <= 80:
        return channel_name
    suffix = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{channel_name[:71]}-{suffix}"


def _channel_id_from_data(data: Mapping[str, Any]) -> str | None:
    channel = data.get("channel")
    if not isinstance(channel, Mapping):
        return None
    channel_id = channel.get("id")
    return channel_id if isinstance(channel_id, str) else None
