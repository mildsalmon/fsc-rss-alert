from __future__ import annotations

from dataclasses import dataclass

from typing import Protocol

from feed_collector.domain import SourceConfig


@dataclass(frozen=True)
class SourceRunState:
    source_id: str
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    consecutive_failures: int = 0
    failure_alert_sent: bool = False
    last_failure_reason: str | None = None


class SourceStatePort(Protocol):
    def ensure_source(self, source: SourceConfig) -> None: ...

    def get_state(self, source_id: str) -> SourceRunState: ...

    def record_attempt(self, source_id: str) -> None: ...

    def record_success(self, source_id: str) -> None: ...

    def record_failure(self, source_id: str, reason: str) -> None: ...

    def mark_failure_alert_sent(self, source_id: str) -> None: ...
