from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from feed_collector.adapter.outbound.bulk_sdn import BulkSdnAdapterFactory
from feed_collector.adapter.outbound.datatables import DataTablesAdapter
from feed_collector.adapter.outbound.html_scrape import HtmlScrapeAdapterFactory
from feed_collector.adapter.outbound.http_fetch import HttpFetcherFactory
from feed_collector.adapter.outbound.json_board import JsonBoardAdapter
from feed_collector.adapter.outbound.rss import RssAdapterFactory
from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import EmptyResultPolicy, Mechanism, ParamValue, SourceConfig
from feed_collector.errors import PollError


SourceAdapterFactory = Callable[[SourceConfig], SourcePort]


def load_sources(path: str | Path) -> list[SourceConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw_sources = _source_entries(raw)
    return [_source_from_mapping(index, source) for index, source in enumerate(raw_sources)]


@dataclass(frozen=True)
class SourceAdapterRegistry:
    rss_factory: RssAdapterFactory = field(default_factory=lambda: RssAdapterFactory(HttpFetcherFactory()))
    datatables_factory: SourceAdapterFactory = DataTablesAdapter
    html_factory: HtmlScrapeAdapterFactory = field(default_factory=lambda: HtmlScrapeAdapterFactory(HttpFetcherFactory()))
    json_board_factory: SourceAdapterFactory = JsonBoardAdapter
    bulk_factory: BulkSdnAdapterFactory = field(default_factory=lambda: BulkSdnAdapterFactory(HttpFetcherFactory()))

    def create(self, source: SourceConfig) -> SourcePort:
        if source.mechanism == "rss":
            return self.rss_factory.create(source)
        if source.mechanism == "datatables":
            return self.datatables_factory(source)
        if source.mechanism == "html":
            return self.html_factory.create(source)
        if source.mechanism == "json_board":
            return self.json_board_factory(source)
        if source.mechanism == "bulk":
            return self.bulk_factory.create(source)
        raise PollError(f"Source {source.id} has unsupported mechanism {source.mechanism!r}")

    def __call__(self, source: SourceConfig) -> SourcePort:
        return self.create(source)


def _source_entries(raw: object) -> Sequence[object]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, Mapping):
        sources = raw.get("sources")
        if isinstance(sources, list):
            return sources
    raise PollError("sources config must be a list or a mapping with a sources list")


def _source_from_mapping(index: int, raw: object) -> SourceConfig:
    if not isinstance(raw, Mapping):
        raise PollError(f"sources[{index}] must be an object")

    source_id = _required_str(raw, "id", index)
    mechanism = _mechanism(_required_str(raw, "mechanism", index), source_id)
    return SourceConfig(
        id=source_id,
        slug=_required_str(raw, "slug", index),
        name=_required_str(raw, "name", index),
        mechanism=mechanism,
        parser_version=_required_int(raw, "parser_version", index),
        channel_id=_optional_str(raw, "channel_id", index),
        interval_minutes=_required_int(raw, "interval_minutes", index),
        url=_required_str(raw, "url", index),
        params=_params(raw.get("params"), source_id),
        list_path=_optional_str(raw, "list_path", index),
        detail_url=_optional_str(raw, "detail_url", index),
        empty_result_policy=_empty_result_policy(raw.get("empty_result_policy", "error"), source_id),
    )


def _required_str(raw: Mapping[object, object], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PollError(f"sources[{index}].{key} must be a non-empty string")
    return value.strip()


def _optional_str(raw: Mapping[object, object], key: str, index: int) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PollError(f"sources[{index}].{key} must be a string or null")
    stripped = value.strip()
    return stripped or None


def _required_int(raw: Mapping[object, object], key: str, index: int) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or value is None:
        raise PollError(f"sources[{index}].{key} must be a positive integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise PollError(f"sources[{index}].{key} must be a positive integer") from exc
    if parsed < 1:
        raise PollError(f"sources[{index}].{key} must be a positive integer")
    return parsed


def _params(raw: object, source_id: str) -> Mapping[str, ParamValue]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise PollError(f"Source {source_id} params must be an object")

    params: dict[str, ParamValue] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise PollError(f"Source {source_id} params keys must be strings")
        if value is not None and not isinstance(value, str | int | float | bool):
            raise PollError(f"Source {source_id} param {key} must be a scalar value")
        params[key] = value
    return params


def _mechanism(value: str, source_id: str) -> Mechanism:
    if value not in {"rss", "html", "json_board", "datatables", "bulk"}:
        raise PollError(f"Source {source_id} has unsupported mechanism {value!r}")
    return cast(Mechanism, value)


def _empty_result_policy(value: object, source_id: str) -> EmptyResultPolicy:
    if value not in {"error", "valid"}:
        raise PollError(f"Source {source_id} empty_result_policy must be 'error' or 'valid'")
    return cast(EmptyResultPolicy, value)


__all__ = ["SourceAdapterRegistry", "SourceAdapterFactory", "load_sources"]
