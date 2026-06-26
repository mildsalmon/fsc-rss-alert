from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser

from feed_collector.adapter.outbound.http_fetch import ByteFetcher, ByteFetcherFactory, HttpFetcherFactory
from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import Item, SourceConfig


DEFAULT_PUBLISHED_TIMEZONE = "UTC"


class BulkSdnAdapterError(ValueError):
    pass


@dataclass(frozen=True)
class BulkSdnParser:
    def parse(self, payload: bytes, cfg: SourceConfig) -> list[Item]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise BulkSdnAdapterError(f"Source {cfg.id} SDN XML was not valid") from exc

        publish_date = _required_descendant_text(root, "Publish_Date", cfg)
        published = _parse_published(publish_date, cfg, "Publish_Date")
        items = [self._map_entry(entry, cfg, published) for entry in _descendants(root, "sdnEntry")]
        if not items and cfg.empty_result_policy == "error":
            raise BulkSdnAdapterError(f"Source {cfg.id} returned no rows")
        return items

    def _map_entry(self, entry: ET.Element, cfg: SourceConfig, published: datetime) -> Item:
        item_id = _required_child_text(entry, "uid", cfg)
        name = _entry_name(entry, cfg)
        sdn_type = _optional_child_text(entry, "sdnType")
        programs = _programs(entry)

        return Item(
            item_id=item_id,
            title=_title(name, sdn_type, programs),
            link=_entry_link(cfg, item_id),
            published=published,
        )


@dataclass(frozen=True)
class BulkSdnAdapter(SourcePort):
    cfg: SourceConfig
    fetcher: ByteFetcher
    parser: BulkSdnParser = BulkSdnParser()

    def fetch(self) -> list[Item]:
        return self.parser.parse(self.fetcher.fetch(self.cfg.url), self.cfg)


@dataclass(frozen=True)
class BulkSdnAdapterFactory:
    fetcher_factory: ByteFetcherFactory = field(default_factory=HttpFetcherFactory)

    def create(self, source: SourceConfig) -> BulkSdnAdapter:
        return BulkSdnAdapter(source, fetcher=self.fetcher_factory.create(source))

    def __call__(self, source: SourceConfig) -> BulkSdnAdapter:
        return self.create(source)


def parse_sdn_items(payload: bytes, cfg: SourceConfig) -> list[Item]:
    return BulkSdnParser().parse(payload, cfg)


def _entry_name(entry: ET.Element, cfg: SourceConfig) -> str:
    last_name = _optional_child_text(entry, "lastName")
    first_name = _optional_child_text(entry, "firstName")
    if first_name and last_name:
        return f"{first_name} {last_name}"
    if last_name:
        return last_name
    if first_name:
        return first_name
    raise BulkSdnAdapterError(f"Source {cfg.id} row missing required name")


def _programs(entry: ET.Element) -> tuple[str, ...]:
    for child in entry:
        if _local_name(child.tag) == "programList":
            return tuple(text for text in (_text(program) for program in child) if text)
    return ()


def _title(name: str, sdn_type: str | None, programs: tuple[str, ...]) -> str:
    qualifiers = [value for value in [sdn_type, ", ".join(programs)] if value]
    if not qualifiers:
        return name
    return f"{name} ({'; '.join(qualifiers)})"


def _entry_link(cfg: SourceConfig, item_id: str) -> str:
    if cfg.detail_url:
        return cfg.detail_url.format(id=item_id)
    return cfg.url


def _parse_published(value: str, cfg: SourceConfig, field: str) -> datetime:
    try:
        parsed = parser.parse(value)
    except (OverflowError, ValueError) as exc:
        raise BulkSdnAdapterError(f"Source {cfg.id} has invalid {field} {value!r}") from exc
    timezone = _published_timezone(cfg)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _published_timezone(cfg: SourceConfig) -> ZoneInfo:
    raw_timezone = cfg.params.get("published_timezone", DEFAULT_PUBLISHED_TIMEZONE)
    if not isinstance(raw_timezone, str) or not raw_timezone.strip():
        raise BulkSdnAdapterError(f"Source {cfg.id} params.published_timezone must be a timezone name")
    try:
        return ZoneInfo(raw_timezone)
    except ZoneInfoNotFoundError as exc:
        raise BulkSdnAdapterError(f"Source {cfg.id} has unknown timezone {raw_timezone!r}") from exc


def _required_descendant_text(root: ET.Element, name: str, cfg: SourceConfig) -> str:
    for element in _descendants(root, name):
        text = _text(element)
        if text:
            return text
    raise BulkSdnAdapterError(f"Source {cfg.id} XML missing required element {name!r}")


def _required_child_text(entry: ET.Element, name: str, cfg: SourceConfig) -> str:
    text = _optional_child_text(entry, name)
    if text:
        return text
    raise BulkSdnAdapterError(f"Source {cfg.id} row missing required field {name!r}")


def _optional_child_text(entry: ET.Element, name: str) -> str | None:
    for child in entry:
        if _local_name(child.tag) == name:
            return _text(child)
    return None


def _descendants(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _text(element: ET.Element) -> str | None:
    value = element.text
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


__all__ = [
    "BulkSdnAdapter",
    "BulkSdnAdapterError",
    "BulkSdnAdapterFactory",
    "BulkSdnParser",
    "parse_sdn_items",
]
