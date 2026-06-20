from __future__ import annotations

from typing import Protocol

from feed_collector.application.dto import PollResult


class PollInputPort(Protocol):
    def poll(self, *, dry_run: bool = False) -> PollResult: ...
