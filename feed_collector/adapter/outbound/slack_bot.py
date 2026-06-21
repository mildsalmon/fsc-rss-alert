from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Protocol, cast

import requests

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


def format_slack_item_message(item: Item) -> str:
    title = item.title.strip() or "(untitled)"
    published = item.published.isoformat() if item.published is not None else "unknown"
    link = item.link.strip() or "(no link)"
    return f"{title}\nDate: {published}\nLink: {link}"


class SlackBotNotifier(NotifierPort):
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

    def send(self, channel_id: str, item: Item) -> str | None:
        data = self._request(
            "chat.postMessage",
            {
                "channel": channel_id,
                "text": format_slack_item_message(item),
                "unfurl_links": False,
            },
        )
        ts = data.get("ts")
        return ts if isinstance(ts, str) else None

    def _request(self, method: str, payload: Mapping[str, object]) -> Mapping[str, Any]:
        if not self.bot_token:
            raise ValueError("Slack bot token is required")

        response = self.session.post(
            f"{self.api_base_url}/{method}",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise SlackApiError(f"Slack {method} returned a non-object response")
        if data.get("ok") is not True:
            error = data.get("error", "unknown_error")
            raise SlackApiError(f"Slack {method} failed: {error}")
        return data

    def _headers(self) -> Mapping[str, str]:
        assert self.bot_token is not None
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }


class SlackChannelManager:
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

    def ensure_feed_channel(self, slug: str) -> str:
        name = feed_channel_name(slug)
        data = self._post("conversations.create", {"name": name})
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
            data = self._get("conversations.list", params)
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

    def _post(self, method: str, payload: Mapping[str, object]) -> Mapping[str, Any]:
        if not self.bot_token:
            raise ValueError("Slack bot token is required")

        response = self.session.post(
            f"{self.api_base_url}/{method}",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise SlackApiError(f"Slack {method} returned a non-object response")
        if data.get("ok") is not True and data.get("error") != "name_taken":
            error = data.get("error", "unknown_error")
            raise SlackApiError(f"Slack {method} failed: {error}")
        return data

    def _get(self, method: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        if not self.bot_token:
            raise ValueError("Slack bot token is required")

        response = self.session.get(
            f"{self.api_base_url}/{method}",
            headers=self._headers(),
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise SlackApiError(f"Slack {method} returned a non-object response")
        if data.get("ok") is not True:
            error = data.get("error", "unknown_error")
            raise SlackApiError(f"Slack {method} failed: {error}")
        return data

    def _headers(self) -> Mapping[str, str]:
        if self.bot_token is None:
            raise ValueError("Slack bot token is required")
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }


def feed_channel_name(slug: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-")
    if not normalized:
        normalized = "source"
    return f"feed-{normalized}"[:80]


def _channel_id_from_data(data: Mapping[str, Any]) -> str | None:
    channel = data.get("channel")
    if not isinstance(channel, Mapping):
        return None
    channel_id = channel.get("id")
    return channel_id if isinstance(channel_id, str) else None
