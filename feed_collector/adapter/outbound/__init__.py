"""Outbound adapters that implement application output ports."""

from feed_collector.adapter.outbound.datatables import DataTablesAdapter
from feed_collector.adapter.outbound.poll_lock import try_acquire_poll_lock
from feed_collector.adapter.outbound.sqlite_channel import SqliteChannelRepo
from feed_collector.adapter.outbound.sqlite_seen_state import SqliteStateRepo
from feed_collector.adapter.outbound.sqlite_source_state import SqliteSourceStateRepo

__all__ = [
    "DataTablesAdapter",
    "SqliteChannelRepo",
    "SqliteSourceStateRepo",
    "SqliteStateRepo",
    "try_acquire_poll_lock",
]
