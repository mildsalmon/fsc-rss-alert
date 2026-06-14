from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser


FEED_URL = "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111"
DEFAULT_STATE_FILE = Path("state.json")
DEFAULT_SEEN_LIMIT = 50
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_TIMEOUT_SECONDS = 20
USER_AGENT = "fsc-rss-alert/0.1 (+https://github.com/actions)"


class PollError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedEntry:
    entry_id: str
    title: str
    link: str
    published: str


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise PollError(f"{name} must be an integer") from exc
    if value < 1:
        raise PollError(f"{name} must be at least 1")
    return value


def float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise PollError(f"{name} must be a number") from exc
    if value < 0:
        raise PollError(f"{name} must be at least 0")
    return value


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "seen_ids": [],
            "consecutive_failures": 0,
            "failure_alert_sent": False,
        }
    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict):
        raise PollError(f"{path} must contain a JSON object")
    seen_ids = state.get("seen_ids", [])
    if not isinstance(seen_ids, list) or not all(isinstance(item, str) for item in seen_ids):
        raise PollError(f"{path} field seen_ids must be a list of strings")
    state.setdefault("consecutive_failures", 0)
    state.setdefault("failure_alert_sent", False)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")
    tmp_path.replace(path)


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

    entries: list[FeedEntry] = []
    for raw_entry in parsed.entries:
        entry = normalize_entry(raw_entry)
        if entry is not None:
            entries.append(entry)

    if not entries:
        raise PollError("Feed entries did not include guid or link values")

    feed_title = str(parsed.feed.get("title") or "FSC RSS").strip()
    return feed_title, entries


def merge_seen_ids(current_ids: list[str], previous_ids: list[str], limit: int) -> list[str]:
    merged: list[str] = []
    for entry_id in current_ids + previous_ids:
        if entry_id and entry_id not in merged:
            merged.append(entry_id)
        if len(merged) >= limit:
            break
    return merged


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


def reset_failure_state(state: dict[str, Any]) -> None:
    state["consecutive_failures"] = 0
    state["failure_alert_sent"] = False
    state.pop("last_failure_at", None)
    state.pop("last_error", None)


def has_failure_state(state: dict[str, Any]) -> bool:
    return bool(
        state.get("consecutive_failures")
        or state.get("failure_alert_sent")
        or "last_failure_at" in state
        or "last_error" in state
    )


def record_failure(
    state_path: Path,
    state: dict[str, Any],
    error: Exception,
    dry_run: bool,
    timeout_seconds: int,
    failure_threshold: int,
) -> int:
    failure_count = int(state.get("consecutive_failures") or 0) + 1
    state["consecutive_failures"] = failure_count
    state["last_failure_at"] = utc_now_iso()
    state["last_error"] = str(error)[:500]

    should_alert = failure_count >= failure_threshold and not state.get("failure_alert_sent", False)
    if should_alert:
        try:
            notifier = build_notifier(dry_run=dry_run, timeout_seconds=timeout_seconds)
            notifier.send(format_failure_message(error, failure_count))
            state["failure_alert_sent"] = True
        except Exception as alert_error:  # noqa: BLE001
            print(f"Failure alert could not be sent: {alert_error}", file=sys.stderr)

    if not dry_run:
        save_state(state_path, state)

    print(f"Poll failed: {error}", file=sys.stderr)
    return 1


def print_dry_run_summary(feed_title: str, entries: list[FeedEntry], new_entries: list[FeedEntry], first_run: bool) -> None:
    print(f"Feed: {feed_title}")
    print(f"Parsed entries: {len(entries)}")
    if first_run:
        print("Dry run: first real run would store the current feed as baseline and send no alerts.")
    elif new_entries:
        print(f"Dry run: would send {len(new_entries)} alert(s), oldest-first.")
        notifier = ConsoleNotifier()
        for entry in reversed(new_entries):
            notifier.send(format_entry_message(entry))
    else:
        print("Dry run: no new entries compared with state.")

    print("Latest parsed entries:")
    for entry in entries[:5]:
        date = f" [{entry.published}]" if entry.published else ""
        print(f"- {entry.title}{date}")
        if entry.link:
            print(f"  {entry.link}")


def run(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file)
    timeout_seconds = int_from_env("FETCH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    seen_limit = int_from_env("SEEN_ID_LIMIT", DEFAULT_SEEN_LIMIT)
    failure_threshold = int_from_env("FAILURE_ALERT_THRESHOLD", DEFAULT_FAILURE_THRESHOLD)

    state = load_state(state_path)

    try:
        feed_bytes = fetch_feed(FEED_URL, timeout_seconds)
        feed_title, entries = parse_entries(feed_bytes)
    except Exception as error:  # noqa: BLE001
        return record_failure(state_path, state, error, args.dry_run, timeout_seconds, failure_threshold)

    previous_seen_ids = list(state.get("seen_ids", []))
    previous_seen_set = set(previous_seen_ids)
    current_ids = [entry.entry_id for entry in entries]
    first_run = not previous_seen_ids
    new_entries = [entry for entry in entries if entry.entry_id not in previous_seen_set]

    if args.dry_run:
        print_dry_run_summary(feed_title, entries, new_entries, first_run)
        return 0

    if first_run:
        state["seen_ids"] = merge_seen_ids(current_ids, [], seen_limit)
        reset_failure_state(state)
        save_state(state_path, state)
        print(f"Initialized baseline with {len(state['seen_ids'])} IDs. No alerts sent.")
        return 0

    if new_entries:
        try:
            notifier = build_notifier(dry_run=False, timeout_seconds=timeout_seconds)
            throttle_seconds = float_from_env("NOTIFY_THROTTLE_SECONDS", 1.0)
            entries_to_send = list(reversed(new_entries))
            for index, entry in enumerate(entries_to_send):
                notifier.send(format_entry_message(entry))
                if throttle_seconds and index < len(entries_to_send) - 1:
                    time.sleep(throttle_seconds)
        except Exception as error:  # noqa: BLE001
            return record_failure(state_path, state, error, args.dry_run, timeout_seconds, failure_threshold)

    next_seen_ids = merge_seen_ids(current_ids, previous_seen_ids, seen_limit)
    should_save = next_seen_ids != previous_seen_ids or has_failure_state(state)
    if should_save:
        state["seen_ids"] = next_seen_ids
        reset_failure_state(state)
        save_state(state_path, state)

    if new_entries:
        print(f"Sent {len(new_entries)} alert(s).")
    else:
        print("No new entries.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll the FSC RSS feed and send new item alerts.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions instead of sending alerts or writing state.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="Path to the JSON state file.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
