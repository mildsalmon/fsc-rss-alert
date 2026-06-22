from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser
import requests

from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import Item, ParamValue, SourceConfig


DEFAULT_LENGTH = 30
DEFAULT_TIMEOUT_SECONDS = 20
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


class DataTablesResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class DataTablesHttpSession(Protocol):
    def post(
        self,
        url: str,
        *,
        data: Mapping[str, ParamValue],
        timeout: int,
    ) -> DataTablesResponse: ...


@dataclass(frozen=True)
class DataTablesRequestBuilder:
    default_length: int = DEFAULT_LENGTH

    def build(self, cfg: SourceConfig) -> dict[str, ParamValue]:
        request: dict[str, ParamValue] = {
            "draw": 1,
            "start": 0,
            "length": self.default_length,
        }
        request.update({key: value for key, value in cfg.params.items() if key not in ADAPTER_PARAM_KEYS})
        return request


@dataclass(frozen=True)
class DataTablesHttpClient:
    session: DataTablesHttpSession = requests
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def post_json(self, cfg: SourceConfig, request: Mapping[str, ParamValue]) -> object:
        try:
            response = self.session.post(cfg.url, data=request, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DataTablesAdapterError(f"Source {cfg.id} DataTables request failed: {exc}") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise DataTablesAdapterError(f"Source {cfg.id} DataTables response was not valid JSON") from exc


@dataclass(frozen=True)
class DataTablesRowsExtractor:
    def extract(self, payload: object, cfg: SourceConfig) -> list[Mapping[str, Any]]:
        if not cfg.list_path:
            raise DataTablesAdapterError(f"Source {cfg.id} requires list_path")

        current = payload
        for part in cfg.list_path.split("."):
            if not isinstance(current, Mapping):
                raise DataTablesAdapterError(
                    f"Source {cfg.id} list_path {cfg.list_path!r} does not resolve to a list"
                )
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


@dataclass(frozen=True)
class DataTablesRowMapper:
    def map(self, row: Mapping[str, Any], cfg: SourceConfig) -> Item:
        item_id_field = self._field_param(cfg, "item_id_field")
        title_field = self._field_param(cfg, "title_field")
        published_field = self._field_param(cfg, "published_field")

        item_id = self._required(row, item_id_field, cfg)
        title = self._required(row, title_field, cfg)
        published_value = self._required(row, published_field, cfg)

        if cfg.detail_url is None:
            raise DataTablesAdapterError(f"Source {cfg.id} requires detail_url")

        return Item(
            item_id=str(item_id),
            title=str(title),
            link=cfg.detail_url.format(id=item_id),
            published=self.parse_published(published_value, cfg, published_field),
        )

    def published_at(self, row: Mapping[str, Any], cfg: SourceConfig) -> datetime:
        published_field = self._field_param(cfg, "published_field")
        published_value = self._required(row, published_field, cfg)
        return self.parse_published(published_value, cfg, published_field)

    def parse_published(self, value: object, cfg: SourceConfig, field: str) -> datetime:
        if not isinstance(value, str):
            raise DataTablesAdapterError(f"Source {cfg.id} field {field!r} must be a string")
        try:
            parsed = parser.parse(value)
        except (OverflowError, ValueError) as exc:
            raise DataTablesAdapterError(f"Source {cfg.id} has invalid {field} {value!r}") from exc
        timezone = self._published_timezone(cfg)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)

    def _field_param(self, cfg: SourceConfig, param: str) -> str:
        value = cfg.params.get(param)
        if not isinstance(value, str) or not value.strip():
            raise DataTablesAdapterError(f"Source {cfg.id} requires params.{param}")
        return value.strip()

    def _required(self, row: Mapping[str, Any], field: str, cfg: SourceConfig) -> Any:
        value = row.get(field)
        if value is None or value == "":
            raise DataTablesAdapterError(f"Source {cfg.id} row missing required field {field!r}")
        return value

    def _published_timezone(self, cfg: SourceConfig) -> ZoneInfo:
        raw_timezone = cfg.params.get("published_timezone", DEFAULT_PUBLISHED_TIMEZONE)
        if not isinstance(raw_timezone, str) or not raw_timezone.strip():
            raise DataTablesAdapterError(f"Source {cfg.id} params.published_timezone must be a timezone name")
        try:
            return ZoneInfo(raw_timezone)
        except ZoneInfoNotFoundError as exc:
            raise DataTablesAdapterError(f"Source {cfg.id} has unknown timezone {raw_timezone!r}") from exc


@dataclass(frozen=True)
class DataTablesOrderingValidator:
    row_mapper: DataTablesRowMapper = DataTablesRowMapper()

    def validate_newest_first(self, rows: list[Mapping[str, Any]], cfg: SourceConfig) -> None:
        previous: datetime | None = None
        for index, row in enumerate(rows):
            current = self.row_mapper.published_at(row, cfg)
            if previous is not None and current > previous:
                raise DataTablesAdapterError(f"Source {cfg.id} rows are not newest-first at index {index}")
            previous = current


class DataTablesAdapter(SourcePort):
    def __init__(
        self,
        cfg: SourceConfig,
        *,
        request_builder: DataTablesRequestBuilder | None = None,
        http_client: DataTablesHttpClient | None = None,
        rows_extractor: DataTablesRowsExtractor | None = None,
        row_mapper: DataTablesRowMapper | None = None,
        ordering_validator: DataTablesOrderingValidator | None = None,
    ) -> None:
        self.cfg = cfg
        self.request_builder = request_builder or DataTablesRequestBuilder()
        self.http_client = http_client or DataTablesHttpClient()
        self.rows_extractor = rows_extractor or DataTablesRowsExtractor()
        self.row_mapper = row_mapper or DataTablesRowMapper()
        self.ordering_validator = ordering_validator or DataTablesOrderingValidator(self.row_mapper)

    def fetch(self) -> list[Item]:
        request = self.request_builder.build(self.cfg)
        payload = self.http_client.post_json(self.cfg, request)
        rows = self.rows_extractor.extract(payload, self.cfg)
        if not rows and self.cfg.empty_result_policy == "error":
            raise DataTablesAdapterError(f"Source {self.cfg.id} returned no rows")

        self.ordering_validator.validate_newest_first(rows, self.cfg)
        return [self.row_mapper.map(row, self.cfg) for row in rows]


__all__ = [
    "DEFAULT_LENGTH",
    "DataTablesAdapter",
    "DataTablesAdapterError",
    "DataTablesHttpClient",
    "DataTablesOrderingValidator",
    "DataTablesRequestBuilder",
    "DataTablesRowMapper",
    "DataTablesRowsExtractor",
]
