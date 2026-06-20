from __future__ import annotations

from typing import Protocol


class InputPort(Protocol):
    """Application API called by inbound adapters."""


class OutputPort(Protocol):
    """External dependency API called by application services."""
