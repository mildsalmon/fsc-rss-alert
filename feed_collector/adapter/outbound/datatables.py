from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser
import requests

from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import Item, ParamValue, SourceConfig


DEFAULT_LENGTH = 30
DEFAULT_PUBLISHED_TIMEZONE = "Asia/Seoul"
ADAPTER_PARAM_KEYS = frozenset(
    {
        "item_id_field",
        "title_field",
        "published_field",
        "published_timezone",
    }
)


class DataTablesAdapterError(ValueError):
    pass


class DataTablesAdapter(SourcePort):
    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg

    def fetch(self) -> list[Item]:
        try:
            response = requests.post(self.cfg.url, data=build_request(self.cfg), timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DataTablesAdapterError(f"Source {self.cfg.id} DataTables request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise DataTablesAdapterError(f"Source {self.cfg.id} DataTables response was not valid JSON") from exc
        rows = rows_at_path(payload, self.cfg)
        if not rows and self.cfg.empty_result_policy == "error":
            raise DataTablesAdapterError(f"Source {self.cfg.id} returned no rows")

        assert_newest_first(rows, self.cfg)
        return [map_row(row, self.cfg) for row in rows]


def build_request(cfg: SourceConfig) -> dict[str, ParamValue]:
    request: dict[str, ParamValue] = {
        "draw": 1,
        "start": 0,
        "length": DEFAULT_LENGTH,
    }
    request.update({key: value for key, value in cfg.params.items() if key not in ADAPTER_PARAM_KEYS})
    return request


def rows_at_path(payload: object, cfg: SourceConfig) -> list[Mapping[str, Any]]:
    if not cfg.list_path:
        raise DataTablesAdapterError(f"Source {cfg.id} requires list_path")

    current = payload
    for part in cfg.list_path.split("."):
        if not isinstance(current, Mapping):
            raise DataTablesAdapterError(f"Source {cfg.id} list_path {cfg.list_path!r} does not resolve to a list")
        if part not in current:
            raise DataTablesAdapterError(f"Source {cfg.id} response missing list_path segment {part!r}")
        current = current[part]

    if not isinstance(current, list):
        raise DataTablesAdapterError(f"Source {cfg.id} list_path {cfg.list_path!r} does not resolve to a list")

    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(current):
        if not isinstance(row, Mapping):
            raise DataTablesAdapterError(f"Source {cfg.id} row {index} is not an object")
        rows.append(row)
    return rows


def map_row(row: Mapping[str, Any], cfg: SourceConfig) -> Item:
    item_id_field = field_param(cfg, "item_id_field")
    title_field = field_param(cfg, "title_field")
    published_field = field_param(cfg, "published_field")

    item_id = required(row, item_id_field, cfg)
    title = required(row, title_field, cfg)
    published_value = required(row, published_field, cfg)

    if cfg.detail_url is None:
        raise DataTablesAdapterError(f"Source {cfg.id} requires detail_url")

    return Item(
        item_id=str(item_id),
        title=str(title),
        link=cfg.detail_url.format(id=item_id),
        published=parse_published(published_value, cfg, published_field),
    )


def field_param(cfg: SourceConfig, param: str) -> str:
    value = cfg.params.get(param)
    if not isinstance(value, str) or not value.strip():
        raise DataTablesAdapterError(f"Source {cfg.id} requires params.{param}")
    return value.strip()


def required(row: Mapping[str, Any], field: str, cfg: SourceConfig) -> Any:
    value = row.get(field)
    if value is None or value == "":
        raise DataTablesAdapterError(f"Source {cfg.id} row missing required field {field!r}")
    return value


def parse_published(value: object, cfg: SourceConfig, field: str) -> datetime:
    if not isinstance(value, str):
        raise DataTablesAdapterError(f"Source {cfg.id} field {field!r} must be a string")
    try:
        parsed = parser.parse(value)
    except (OverflowError, ValueError) as exc:
        raise DataTablesAdapterError(f"Source {cfg.id} has invalid {field} {value!r}") from exc
    timezone = published_timezone(cfg)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def published_timezone(cfg: SourceConfig) -> ZoneInfo:
    raw_timezone = cfg.params.get("published_timezone", DEFAULT_PUBLISHED_TIMEZONE)
    if not isinstance(raw_timezone, str) or not raw_timezone.strip():
        raise DataTablesAdapterError(f"Source {cfg.id} params.published_timezone must be a timezone name")
    try:
        return ZoneInfo(raw_timezone)
    except ZoneInfoNotFoundError as exc:
        raise DataTablesAdapterError(f"Source {cfg.id} has unknown timezone {raw_timezone!r}") from exc


def assert_newest_first(rows: list[Mapping[str, Any]], cfg: SourceConfig) -> None:
    published_field = field_param(cfg, "published_field")
    previous: datetime | None = None
    for index, row in enumerate(rows):
        published_value = required(row, published_field, cfg)
        current = parse_published(published_value, cfg, published_field)
        if previous is not None and current > previous:
            raise DataTablesAdapterError(f"Source {cfg.id} rows are not newest-first at index {index}")
        previous = current
