from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser
import requests

from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import Item, ParamValue, SourceConfig


DEFAULT_LENGTH = 30
KST = ZoneInfo("Asia/Seoul")


class DataTablesAdapterError(ValueError):
    pass


class DataTablesAdapter(SourcePort):
    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg

    def fetch(self) -> list[Item]:
        response = requests.post(self.cfg.url, data=build_request(self.cfg), timeout=20)
        response.raise_for_status()

        payload = response.json()
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
    request.update(cfg.params)
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
    lawreq_idx = required(row, "lawreqIdx", cfg)
    title = required(row, "title", cfg)
    reg_dt = required(row, "regDt", cfg)

    if cfg.detail_url is None:
        raise DataTablesAdapterError(f"Source {cfg.id} requires detail_url")

    return Item(
        item_id=str(lawreq_idx),
        title=str(title),
        link=cfg.detail_url.format(id=lawreq_idx),
        published=parse_kst_reg_dt(reg_dt, cfg),
    )


def required(row: Mapping[str, Any], field: str, cfg: SourceConfig) -> Any:
    value = row.get(field)
    if value is None or value == "":
        raise DataTablesAdapterError(f"Source {cfg.id} row missing required field {field!r}")
    return value


def parse_kst_reg_dt(value: object, cfg: SourceConfig) -> datetime:
    if not isinstance(value, str):
        raise DataTablesAdapterError(f"Source {cfg.id} regDt must be a string")
    try:
        parsed = parser.parse(value)
    except (OverflowError, ValueError) as exc:
        raise DataTablesAdapterError(f"Source {cfg.id} has invalid regDt {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def assert_newest_first(rows: list[Mapping[str, Any]], cfg: SourceConfig) -> None:
    previous: datetime | None = None
    for index, row in enumerate(rows):
        reg_dt = row.get("regDt")
        if reg_dt in (None, ""):
            continue
        current = parse_kst_reg_dt(reg_dt, cfg)
        if previous is not None and current > previous:
            raise DataTablesAdapterError(f"Source {cfg.id} rows are not newest-first at index {index}")
        previous = current
