from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from fsc_rss_alert.config import (
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_NOTIFY_THROTTLE_SECONDS,
    DEFAULT_SEEN_LIMIT,
    DEFAULT_STATE_FILE,
    DEFAULT_TIMEOUT_SECONDS,
    FEED_URL,
    float_from_env,
    int_from_env,
)
from fsc_rss_alert.feed import FeedEntry, fetch_feed, parse_entries
from fsc_rss_alert.notify import (
    ConsoleNotifier,
    build_notifier,
    format_entry_message,
    format_failure_message,
)
from fsc_rss_alert.state import (
    has_failure_state,
    load_state,
    merge_seen_ids,
    record_failure_state,
    reset_failure_state,
    save_state,
)


def record_failure(
    state_path: Path,
    state: dict[str, Any],
    error: Exception,
    dry_run: bool,
    timeout_seconds: int,
    failure_threshold: int,
) -> int:
    failure_count = record_failure_state(state, error)

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


def print_dry_run_summary(
    feed_title: str,
    entries: list[FeedEntry],
    new_entries: list[FeedEntry],
    first_run: bool,
) -> None:
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
            throttle_seconds = float_from_env("NOTIFY_THROTTLE_SECONDS", DEFAULT_NOTIFY_THROTTLE_SECONDS)
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


def main() -> int:
    return run(parse_args())
