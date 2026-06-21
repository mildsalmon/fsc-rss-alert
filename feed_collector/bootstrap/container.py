from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from feed_collector.application.port.input.poll import PollInputPort
from feed_collector.application.port.output.audit import AuditPort
from feed_collector.application.port.output.channel import ChannelResolverPort
from feed_collector.application.port.output.notifier import NotifierPort
from feed_collector.application.port.output.seen_state import SeenStatePort
from feed_collector.application.port.output.source import SourcePort
from feed_collector.application.service.poll import PollService
from feed_collector.domain import SourceConfig


SourceAdapterFactory = Callable[[SourceConfig], SourcePort]


@dataclass(frozen=True)
class AppContainer:
    source_configs: Mapping[str, SourceConfig]
    source_adapter_factory: SourceAdapterFactory
    seen_state: SeenStatePort
    channel_resolver: ChannelResolverPort
    notifier: NotifierPort
    audit: AuditPort

    def poll_service(self, source_id: str) -> PollInputPort:
        source = self._source_config(source_id)
        adapter = self.source_adapter_factory(source)
        return PollService(
            source=source,
            adapter=adapter,
            seen_state=self.seen_state,
            channel_resolver=self.channel_resolver,
            notifier=self.notifier,
            audit=self.audit,
        )

    def _source_config(self, source_id: str) -> SourceConfig:
        try:
            return self.source_configs[source_id]
        except KeyError as exc:
            raise KeyError(f"Unknown source_id: {source_id}") from exc
