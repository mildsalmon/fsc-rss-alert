from __future__ import annotations

from typing import Protocol

from feed_collector.application.port.base import OutputPort
from feed_collector.domain import Item


class AuditPort(OutputPort, Protocol):
    def log(self, source_id: str, item: Item) -> None: ...
