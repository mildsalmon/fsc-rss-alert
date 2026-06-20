from __future__ import annotations

from typing import Protocol

from feed_collector.application.dto import PollResult
from feed_collector.application.port.base import InputPort


class PollInputPort(InputPort, Protocol):
    def poll(self, *, dry_run: bool = False) -> PollResult: ...
