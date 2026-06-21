from __future__ import annotations

from typing import Protocol

from feed_collector.domain import SourceConfig


class SourceStatePort(Protocol):
    def ensure_source(self, source: SourceConfig) -> None: ...

    def record_attempt(self, source_id: str) -> None: ...

    def record_success(self, source_id: str) -> None: ...

    def record_failure(self, source_id: str, reason: str) -> None: ...
