from __future__ import annotations

from typing import Protocol

from feed_collector.domain import Item


class SourcePort(Protocol):
    def fetch(self) -> list[Item]: ...
