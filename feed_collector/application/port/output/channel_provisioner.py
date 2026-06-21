from __future__ import annotations

from typing import Protocol


class ChannelProvisionerPort(Protocol):
    def ensure_feed_channel(self, slug: str) -> str: ...
