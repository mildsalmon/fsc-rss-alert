"""Outbound adapters that implement application output ports."""

from feed_collector.adapter.outbound.state_sqlite import SqliteStateRepo, try_acquire_poll_lock

__all__ = ["SqliteStateRepo", "try_acquire_poll_lock"]
