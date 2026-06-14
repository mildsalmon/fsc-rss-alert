from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from fsc_rss_alert.errors import PollError
from fsc_rss_alert.feed import FeedEntry


class Notifier:
    def send(self, text: str) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def send(self, text: str) -> None:
        print(text)
        print()


class SlackNotifier(Notifier):
    def __init__(self, webhook_url: str, timeout_seconds: int) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    def send(self, text: str) -> None:
        body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status >= 300:
                raise PollError(f"Slack webhook returned HTTP {response.status}")


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: int) -> None:
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    def send(self, text: str) -> None:
        body = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": "false",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status >= 300:
                raise PollError(f"Telegram API returned HTTP {response.status}")


def build_notifier(dry_run: bool, timeout_seconds: int) -> Notifier:
    if dry_run:
        return ConsoleNotifier()

    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if slack_webhook_url:
        return SlackNotifier(slack_webhook_url, timeout_seconds)

    if telegram_bot_token and telegram_chat_id:
        return TelegramNotifier(telegram_bot_token, telegram_chat_id, timeout_seconds)

    if telegram_bot_token or telegram_chat_id:
        raise PollError("Telegram requires both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    raise PollError("No notification channel configured")


def format_entry_message(entry: FeedEntry) -> str:
    parts = [
        "FSC 새 보도자료",
        f"제목: {entry.title}",
    ]
    if entry.published:
        parts.append(f"날짜: {entry.published}")
    if entry.link:
        parts.append(f"링크: {entry.link}")
    return "\n".join(parts)


def format_failure_message(error: Exception, failure_count: int) -> str:
    error_text = str(error).replace("\n", " ")[:500]
    return "\n".join(
        [
            "FSC RSS 폴링 실패",
            f"연속 실패: {failure_count}회",
            f"오류: {error_text}",
        ]
    )

