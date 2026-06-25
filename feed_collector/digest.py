from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from feed_collector.domain import SourceConfig


KST = ZoneInfo("Asia/Seoul")
DEFAULT_STALE_MULTIPLIER = 3


@dataclass(frozen=True)
class DigestWindow:
    target_date: date
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True)
class DigestSourceStat:
    source: SourceConfig
    sent_count: int
    last_success_at: datetime | None
    consecutive_failures: int
    warning: bool
    warning_reason: str | None = None


@dataclass(frozen=True)
class DailyDigest:
    window: DigestWindow
    stats: tuple[DigestSourceStat, ...]
    generated_at: datetime

    @property
    def warning_count(self) -> int:
        return sum(1 for stat in self.stats if stat.warning)

    @property
    def ok_count(self) -> int:
        return len(self.stats) - self.warning_count


def build_daily_digest(
    db_path: str | Path,
    sources: list[SourceConfig],
    *,
    now: datetime | None = None,
    stale_multiplier: int = DEFAULT_STALE_MULTIPLIER,
) -> DailyDigest:
    current = _as_utc(now or datetime.now(timezone.utc))
    window = previous_kst_day_window(current)
    sent_counts = _sent_counts(db_path, window)
    source_states = _source_states(db_path)

    stats = []
    for source in sources:
        state = source_states.get(source.id, {})
        last_success_at = _parse_datetime(state.get("last_success_at"))
        consecutive_failures = _as_int(state.get("consecutive_failures"))
        warning, warning_reason = source_warning(
            source,
            last_success_at,
            consecutive_failures,
            now=current,
            stale_multiplier=stale_multiplier,
        )
        stats.append(
            DigestSourceStat(
                source=source,
                sent_count=sent_counts[source.id],
                last_success_at=last_success_at,
                consecutive_failures=consecutive_failures,
                warning=warning,
                warning_reason=warning_reason,
            )
        )

    return DailyDigest(window=window, stats=tuple(stats), generated_at=current)


def format_digest_message(digest: DailyDigest) -> str:
    generated_kst = digest.generated_at.astimezone(KST)
    lines = [
        f"Feed collector daily digest - {digest.window.target_date.isoformat()} KST",
        f"Generated: {generated_kst.strftime('%Y-%m-%d %H:%M KST')}",
        f"Summary: OK {digest.ok_count} / WARN {digest.warning_count}",
        "",
    ]
    for stat in digest.stats:
        status = "WARN" if stat.warning else "OK"
        line = (
            f"- {status} {stat.source.name} ({stat.source.id}): "
            f"sent yesterday {stat.sent_count}, "
            f"last success {relative_time(stat.last_success_at, now=digest.generated_at)}, "
            f"failures {stat.consecutive_failures}"
        )
        if stat.warning_reason:
            line = f"{line} - {stat.warning_reason}"
        lines.append(line)
    return "\n".join(lines)


def previous_kst_day_window(now: datetime) -> DigestWindow:
    current_kst = _as_utc(now).astimezone(KST)
    target_date = current_kst.date() - timedelta(days=1)
    start_kst = datetime.combine(target_date, time.min, tzinfo=KST)
    end_kst = start_kst + timedelta(days=1)
    return DigestWindow(
        target_date=target_date,
        start_utc=start_kst.astimezone(timezone.utc),
        end_utc=end_kst.astimezone(timezone.utc),
    )


def source_warning(
    source: SourceConfig,
    last_success_at: datetime | None,
    consecutive_failures: int,
    *,
    now: datetime | None = None,
    stale_multiplier: int = DEFAULT_STALE_MULTIPLIER,
) -> tuple[bool, str | None]:
    if last_success_at is None:
        return True, "no successful poll recorded"
    current = _as_utc(now or datetime.now(timezone.utc))
    stale_after = timedelta(minutes=source.interval_minutes * stale_multiplier)
    age = current - _as_utc(last_success_at)
    if age > stale_after:
        return True, f"last success older than {source.interval_minutes * stale_multiplier} minutes"
    del consecutive_failures
    return False, None


def relative_time(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "never"
    current = _as_utc(now or datetime.now(timezone.utc))
    delta = max(timedelta(0), current - _as_utc(value))
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _sent_counts(db_path: str | Path, window: DigestWindow) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not _db_exists(db_path):
        return counts
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT source_id, sent_at FROM audit_log WHERE status = 'sent'").fetchall()
    for source_id, sent_at in rows:
        parsed = _parse_datetime(sent_at)
        if parsed is not None and window.start_utc <= parsed < window.end_utc:
            counts[str(source_id)] += 1
    return counts


def _source_states(db_path: str | Path) -> dict[str, dict[str, object]]:
    if not _db_exists(db_path):
        return {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, last_success_at, consecutive_failures
            FROM sources
            """
        ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _db_exists(db_path: str | Path) -> bool:
    return str(db_path) != ":memory:" and Path(db_path).exists()


__all__ = [
    "DEFAULT_STALE_MULTIPLIER",
    "DailyDigest",
    "DigestSourceStat",
    "DigestWindow",
    "build_daily_digest",
    "format_digest_message",
    "previous_kst_day_window",
    "relative_time",
    "source_warning",
]
