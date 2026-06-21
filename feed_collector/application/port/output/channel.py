from __future__ import annotations

from typing import Protocol


class ChannelResolverPort(Protocol):
    def get_channel_id(self, source_id: str) -> str | None: ...
