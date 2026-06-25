"""Outbound adapters that implement application output ports."""

from feed_collector.adapter.outbound.datatables import (
    DataTablesAdapter,
    DataTablesHttpClient,
    DataTablesOrderingValidator,
    DataTablesRequestBuilder,
    DataTablesRowMapper,
    DataTablesRowsExtractor,
)
from feed_collector.adapter.outbound.http_fetch import (
    DefaultHttpFetcher,
    HttpClient,
    HttpFetcherFactory,
    MofaCookieGateFetcher,
)
from feed_collector.adapter.outbound.poll_lock import try_acquire_poll_lock
from feed_collector.adapter.outbound.rss import RssAdapter, RssAdapterFactory
from feed_collector.adapter.outbound.slack_bot import (
    SlackApiError,
    SlackBotNotifier,
    SlackChannelManager,
    feed_channel_name,
    format_slack_item_message,
)
from feed_collector.adapter.outbound.sqlite_audit import SqliteAuditLog
from feed_collector.adapter.outbound.sqlite_channel import SqliteChannelRepo
from feed_collector.adapter.outbound.sqlite_seen_state import SqliteStateRepo
from feed_collector.adapter.outbound.sqlite_source_state import SqliteSourceStateRepo

__all__ = [
    "DataTablesAdapter",
    "DataTablesHttpClient",
    "DataTablesOrderingValidator",
    "DataTablesRequestBuilder",
    "DataTablesRowMapper",
    "DataTablesRowsExtractor",
    "DefaultHttpFetcher",
    "HttpClient",
    "HttpFetcherFactory",
    "MofaCookieGateFetcher",
    "RssAdapter",
    "RssAdapterFactory",
    "SlackApiError",
    "SlackBotNotifier",
    "SlackChannelManager",
    "SqliteAuditLog",
    "SqliteChannelRepo",
    "SqliteSourceStateRepo",
    "SqliteStateRepo",
    "feed_channel_name",
    "format_slack_item_message",
    "try_acquire_poll_lock",
]
