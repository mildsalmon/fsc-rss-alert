from __future__ import annotations

from typing import Protocol


class ChannelProvisionerPort(Protocol):
    def ensure_feed_channel(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        source_url: str | None = None,
    ) -> str: ...

    def update_feed_channel_metadata(
        self,
        channel_id: str,
        *,
        display_name: str | None = None,
        source_url: str | None = None,
    ) -> bool: ...
