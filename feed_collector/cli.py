from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Sequence

from feed_collector.adapter.outbound import (
    SlackBotNotifier,
    SlackChannelManager,
    SqliteAuditLog,
    SqliteChannelRepo,
    SqliteSourceStateRepo,
    SqliteStateRepo,
    try_acquire_poll_lock,
)
from feed_collector.adapter.outbound.poll_lock import DEFAULT_LOCK_PATH
from feed_collector.adapter.outbound.sqlite_base import DEFAULT_DB_PATH
from feed_collector.application.dto import PollResult
from feed_collector.application.port.output import (
    AuditPort,
    ChannelProvisionerPort,
    ChannelResolverPort,
    NotifierPort,
    SeenStatePort,
    SourceRunState,
    SourceStatePort,
)
from feed_collector.application.service.poll import PollService
from feed_collector.config import DEFAULT_FAILURE_THRESHOLD, DEFAULT_TIMEOUT_SECONDS, int_from_env
from feed_collector.digest import DEFAULT_STALE_MULTIPLIER, build_daily_digest, format_digest_message
from feed_collector.domain import Item, SourceConfig
from feed_collector.errors import FetchFailureReason, PollError, infer_from_error
from feed_collector.registry import SourceAdapterFactory, SourceAdapterRegistry, load_sources


class ChannelStateStore(ChannelResolverPort, Protocol):
    def set_channel_id(self, source_id: str, channel_id: str) -> None: ...


IMMEDIATE_FAILURE_REASONS = frozenset(
    {
        FetchFailureReason.STRUCTURE_CHANGED,
        FetchFailureReason.BLOCKED,
        FetchFailureReason.LOGIN_REQUIRED,
        FetchFailureReason.NOT_FOUND,
    }
)


@dataclass(frozen=True)
class SourceChannelResolver(ChannelResolverPort):
    source: SourceConfig
    channel_repo: ChannelStateStore
    channel_provisioner: ChannelProvisionerPort | None = None

    def get_channel_id(self, source_id: str) -> str | None:
        if source_id != self.source.id:
            return None

        stored_channel_id = self.channel_repo.get_channel_id(source_id)
        if stored_channel_id:
            return stored_channel_id
        if self.source.channel_id:
            return self.source.channel_id
        if self.channel_provisioner is None:
            return None

        channel_id = self.channel_provisioner.ensure_feed_channel(self.source.slug)
        self.channel_repo.set_channel_id(source_id, channel_id)
        return channel_id


@dataclass
class PollRunner:
    sources: Sequence[SourceConfig]
    adapter_factory: SourceAdapterFactory
    seen_state: SeenStatePort
    source_state: SourceStatePort
    channel_repo: ChannelStateStore
    notifier: NotifierPort
    audit: AuditPort
    channel_provisioner: ChannelProvisionerPort | None = None
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    now: datetime | None = None

    def run(self, *, dry_run: bool = False, source_ids: Sequence[str] = ()) -> int:
        selected = set(source_ids)
        exit_code = 0
        for source in self.sources:
            if selected and source.id not in selected:
                continue
            if self._run_one(source, dry_run=dry_run) != 0:
                exit_code = 1

        missing_sources = selected.difference(source.id for source in self.sources)
        for source_id in sorted(missing_sources):
            print(f"[{source_id}] unknown source", file=sys.stderr)
            exit_code = 1
        return exit_code

    def _run_one(self, source: SourceConfig, *, dry_run: bool) -> int:
        if not dry_run:
            self.source_state.ensure_source(source)

        run_state = self.source_state.get_state(source.id)
        if not is_due(source, run_state, now=self._now()):
            print(f"[{source.id}] skipped: interval gate has not elapsed")
            return 0

        adapter = self.adapter_factory(source)
        channel_resolver = SourceChannelResolver(
            source=source,
            channel_repo=self.channel_repo,
            channel_provisioner=None if dry_run else self.channel_provisioner,
        )
        service = PollService(
            source=source,
            adapter=adapter,
            seen_state=self.seen_state,
            channel_resolver=channel_resolver,
            notifier=self.notifier,
            audit=self.audit,
        )

        if dry_run:
            try:
                self._print_result(service.poll(dry_run=True))
                return 0
            except Exception as exc:  # noqa: BLE001
                reason = infer_from_error(exc)
                print(f"[{source.id}] dry-run failed: {reason.value}: {exc}", file=sys.stderr)
                return 1

        self.source_state.record_attempt(source.id)
        try:
            result = service.poll()
        except Exception as exc:  # noqa: BLE001
            reason = infer_from_error(exc)
            self.source_state.record_failure(source.id, reason.value)
            failed_state = self.source_state.get_state(source.id)
            self._maybe_send_failure_alert(source, reason, failed_state, exc)
            print(f"[{source.id}] failed: {reason.value}: {exc}", file=sys.stderr)
            return 1

        self.source_state.record_success(source.id)
        self._print_result(result)
        return 0

    def _print_result(self, result: PollResult) -> None:
        prefix = f"[{result.source_id}]"
        if result.first_run:
            action = "would initialize baseline" if result.dry_run else "initialized baseline"
            print(f"{prefix} {action}: fetched={result.fetched_count}, sent=0")
            return
        if result.dry_run:
            print(f"{prefix} dry-run: fetched={result.fetched_count}, new={result.new_count}, sent=0")
            for item in result.new_items:
                published = f" [{item.published.isoformat()}]" if item.published else ""
                print(f"- {item.title}{published}")
                if item.link:
                    print(f"  {item.link}")
            return
        print(f"{prefix} fetched={result.fetched_count}, new={result.new_count}, sent={result.sent_count}")

    def _maybe_send_failure_alert(
        self,
        source: SourceConfig,
        reason: FetchFailureReason,
        state: SourceRunState,
        exc: BaseException,
    ) -> None:
        if state.failure_alert_sent:
            return
        if reason not in IMMEDIATE_FAILURE_REASONS and state.consecutive_failures < self.failure_threshold:
            return
        if self.channel_provisioner is None:
            return

        try:
            channel_id = self._ops_channel_id()
            self.notifier.send(channel_id, failure_alert_item(source, reason, state, exc, now=self._now()))
            self.source_state.mark_failure_alert_sent(source.id)
        except Exception as alert_error:  # noqa: BLE001
            print(f"[{source.id}] failure alert could not be sent: {alert_error}", file=sys.stderr)

    def _ops_channel_id(self) -> str:
        channel_id = self.channel_repo.get_channel_id("feed-ops")
        if channel_id:
            return channel_id
        if self.channel_provisioner is None:
            raise PollError("feed-ops channel provisioner is not configured")
        channel_id = self.channel_provisioner.ensure_feed_channel("feed-ops")
        self.channel_repo.set_channel_id("feed-ops", channel_id)
        return channel_id

    def _now(self) -> datetime:
        return self.now or datetime.now(timezone.utc)


def is_due(source: SourceConfig, state: SourceRunState, *, now: datetime | None = None) -> bool:
    if state.last_attempt_at is None:
        return True

    last_attempt = _parse_state_datetime(state.last_attempt_at)
    if last_attempt is None:
        return True
    current = now or datetime.now(timezone.utc)
    return current - last_attempt >= timedelta(minutes=source.interval_minutes)


def failure_alert_item(
    source: SourceConfig,
    reason: FetchFailureReason,
    state: SourceRunState,
    exc: BaseException,
    *,
    now: datetime | None = None,
) -> Item:
    current = now or datetime.now(timezone.utc)
    title = (
        f"{source.name} poll failed: {reason.value} "
        f"(consecutive failures: {state.consecutive_failures}) - {exc}"
    )
    return Item(
        item_id=f"failure://{source.id}/{reason.value}/{state.consecutive_failures}",
        title=title,
        link=source.url,
        published=current,
    )


def run_poll(args: argparse.Namespace) -> int:
    sources = load_sources(args.sources_file)
    if args.source_ids:
        selected = set(args.source_ids)
        sources = [source for source in sources if source.id in selected]

    if args.dry_run:
        runner = PollRunner(
            sources=sources,
            adapter_factory=SourceAdapterRegistry(),
            seen_state=DryRunSeenState(args.db_path),
            source_state=DryRunSourceState(args.db_path),
            channel_repo=DryRunChannelStore(args.db_path),
            notifier=NoopNotifier(),
            audit=NoopAudit(),
            channel_provisioner=None,
            failure_threshold=args.failure_threshold,
        )
        return runner.run(dry_run=True, source_ids=args.source_ids)

    lock = try_acquire_poll_lock(args.lock_file)
    if lock is None:
        print("Another feed_collector poll is already running.")
        return 0

    with lock:
        with (
            SqliteStateRepo(args.db_path) as seen_state,
            SqliteSourceStateRepo(args.db_path) as source_state,
            SqliteChannelRepo(args.db_path) as channel_repo,
            SqliteAuditLog(args.db_path) as audit,
        ):
            runner = PollRunner(
                sources=sources,
                adapter_factory=SourceAdapterRegistry(),
                seen_state=seen_state,
                source_state=source_state,
                channel_repo=channel_repo,
                notifier=SlackBotNotifier(timeout_seconds=args.slack_timeout_seconds),
                audit=audit,
                channel_provisioner=SlackChannelManager(timeout_seconds=args.slack_timeout_seconds),
                failure_threshold=args.failure_threshold,
            )
            return runner.run(dry_run=args.dry_run, source_ids=args.source_ids)


@dataclass(frozen=True)
class DryRunSeenState(SeenStatePort):
    db_path: str | Path

    def is_first_run(self, source_id: str) -> bool:
        return not self._exists(
            "SELECT 1 FROM seen_items WHERE source_id = ? LIMIT 1",
            (source_id,),
        )

    def seen_contains(self, source_id: str, item_id: str) -> bool:
        return self._exists(
            "SELECT 1 FROM seen_items WHERE source_id = ? AND item_id = ?",
            (source_id, item_id),
        )

    def replace_baseline(self, source_id: str, items: Sequence[Item]) -> None:
        del source_id, items

    def mark_seen(self, source_id: str, item_ids: Sequence[str]) -> None:
        del source_id, item_ids

    def _exists(self, sql: str, params: tuple[object, ...]) -> bool:
        row = _fetchone(self.db_path, sql, params)
        return row is not None


@dataclass(frozen=True)
class DryRunSourceState(SourceStatePort):
    db_path: str | Path

    def ensure_source(self, source: SourceConfig) -> None:
        del source

    def get_state(self, source_id: str) -> SourceRunState:
        row = _fetchone(
            self.db_path,
            """
            SELECT
              last_attempt_at,
              last_success_at,
              consecutive_failures,
              failure_alert_sent,
              last_failure_reason
            FROM sources
            WHERE id = ?
            """,
            (source_id,),
        )
        if row is None:
            return SourceRunState(source_id=source_id)
        return SourceRunState(
            source_id=source_id,
            last_attempt_at=row[0],
            last_success_at=row[1],
            consecutive_failures=int(row[2] or 0),
            failure_alert_sent=bool(row[3]),
            last_failure_reason=row[4],
        )

    def record_attempt(self, source_id: str) -> None:
        del source_id

    def record_success(self, source_id: str) -> None:
        del source_id

    def record_failure(self, source_id: str, reason: str) -> None:
        del source_id, reason

    def mark_failure_alert_sent(self, source_id: str) -> None:
        del source_id


@dataclass(frozen=True)
class DryRunChannelStore(ChannelStateStore):
    db_path: str | Path

    def get_channel_id(self, source_id: str) -> str | None:
        row = _fetchone(
            self.db_path,
            "SELECT channel_id FROM sources WHERE id = ?",
            (source_id,),
        )
        if row is None:
            return None
        channel_id = row[0]
        return channel_id if isinstance(channel_id, str) and channel_id else None

    def set_channel_id(self, source_id: str, channel_id: str) -> None:
        del source_id, channel_id


class NoopNotifier(NotifierPort):
    def send(self, channel_id: str, item: Item) -> str | None:
        del channel_id, item
        return None


class NoopAudit(AuditPort):
    def log_sent_delivery(
        self,
        source_id: str,
        item: Item,
        *,
        channel_id: str,
        delivery_id: str | None = None,
    ) -> None:
        del source_id, item, channel_id, delivery_id


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    normalized_argv = _normalized_argv(list(argv or sys.argv[1:]))
    parser = argparse.ArgumentParser(description="Poll configured feed sources.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    poll_parser = subparsers.add_parser("poll", help="Poll due sources.")
    poll_parser.add_argument("--dry-run", action="store_true", help="Fetch and print candidates without writes.")
    poll_parser.add_argument("--sources-file", default="sources.yaml", help="Path to source registry YAML.")
    poll_parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to sqlite state database.")
    poll_parser.add_argument("--state-file", dest="db_path", help=argparse.SUPPRESS)
    poll_parser.add_argument("--lock-file", default=DEFAULT_LOCK_PATH, help="Path to the single-run lock file.")
    poll_parser.add_argument("--source", dest="source_ids", action="append", default=[], help="Poll only this source id.")
    poll_parser.add_argument(
        "--failure-threshold",
        type=int,
        default=int_from_env("FAILURE_ALERT_THRESHOLD", DEFAULT_FAILURE_THRESHOLD),
        help="Consecutive transient failures before sending an ops alert.",
    )
    poll_parser.add_argument(
        "--slack-timeout-seconds",
        type=int,
        default=int_from_env("SLACK_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        help="Slack API timeout.",
    )
    poll_parser.set_defaults(handler=run_poll)

    digest_parser = subparsers.add_parser("digest", help="Send the daily feed-ops digest.")
    digest_parser.add_argument("--sources-file", default="sources.yaml", help="Path to source registry YAML.")
    digest_parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to sqlite state database.")
    digest_parser.add_argument("--lock-file", default=DEFAULT_LOCK_PATH, help="Path to the single-run lock file.")
    digest_parser.add_argument(
        "--stale-multiplier",
        type=int,
        default=int_from_env("DIGEST_STALE_MULTIPLIER", DEFAULT_STALE_MULTIPLIER),
        help="Warn when last success is older than interval_minutes multiplied by this value.",
    )
    digest_parser.add_argument(
        "--slack-timeout-seconds",
        type=int,
        default=int_from_env("SLACK_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        help="Slack API timeout.",
    )
    digest_parser.set_defaults(handler=run_digest)
    return parser.parse_args(normalized_argv)


def run_digest(args: argparse.Namespace) -> int:
    sources = load_sources(args.sources_file)
    lock = try_acquire_poll_lock(args.lock_file)
    if lock is None:
        print("Another feed_collector poll or digest is already running.")
        return 0

    with lock:
        with (
            SqliteAuditLog(args.db_path) as audit,
            SqliteChannelRepo(args.db_path) as channel_repo,
        ):
            digest = build_daily_digest(
                args.db_path,
                sources,
                stale_multiplier=args.stale_multiplier,
            )
            audit.prune(now=digest.generated_at)
            channel_manager = SlackChannelManager(timeout_seconds=args.slack_timeout_seconds)
            channel_id = ensure_ops_channel_id(channel_repo, channel_manager)
            SlackBotNotifier(timeout_seconds=args.slack_timeout_seconds).send_text(
                channel_id,
                format_digest_message(digest),
            )

    print(f"Sent daily digest for {digest.window.target_date.isoformat()} to feed-ops.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return args.handler(args)
    except PollError as exc:
        print(exc, file=sys.stderr)
        return 2


def _normalized_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["poll"]
    if argv[0] in {"poll", "digest", "-h", "--help"}:
        return argv
    if argv[0].startswith("-"):
        return ["poll", *argv]
    return argv


def _parse_state_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fetchone(db_path: str | Path, sql: str, params: tuple[object, ...]) -> sqlite3.Row | None:
    if not _db_exists(db_path):
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None


def _db_exists(db_path: str | Path) -> bool:
    return str(db_path) != ":memory:" and Path(db_path).exists()


def ensure_ops_channel_id(
    channel_repo: ChannelStateStore,
    channel_provisioner: ChannelProvisionerPort,
) -> str:
    channel_id = channel_repo.get_channel_id("feed-ops")
    if channel_id:
        return channel_id
    channel_id = channel_provisioner.ensure_feed_channel("feed-ops")
    channel_repo.set_channel_id("feed-ops", channel_id)
    return channel_id


__all__ = [
    "PollRunner",
    "SourceChannelResolver",
    "ensure_ops_channel_id",
    "failure_alert_item",
    "is_due",
    "main",
    "parse_args",
    "run_poll",
]
