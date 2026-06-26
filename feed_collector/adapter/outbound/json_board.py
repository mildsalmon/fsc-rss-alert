from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser
import requests

from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import Item, ParamValue, SourceConfig


DEFAULT_PAGE = 1
DEFAULT_SIZE = 10
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_PUBLISHED_TIMEZONE = "Asia/Seoul"
ADAPTER_PARAM_KEYS = frozenset(
    {
        "item_id_field",
        "ordering_field",
        "published_field",
        "published_timezone",
        "title_field",
    }
)


class JsonBoardAdapterError(ValueError):
    pass


class JsonBoardResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class JsonBoardHttpSession(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, ParamValue],
        timeout: int,
    ) -> JsonBoardResponse: ...


@dataclass(frozen=True)
class JsonBoardRequestBuilder:
    default_page: int = DEFAULT_PAGE
    default_size: int = DEFAULT_SIZE

    def build(self, cfg: SourceConfig) -> dict[str, ParamValue]:
        request: dict[str, ParamValue] = {
            "page": self.default_page,
            "size": self.default_size,
        }
        request.update({key: value for key, value in cfg.params.items() if key not in ADAPTER_PARAM_KEYS})
        return request


@dataclass(frozen=True)
class JsonBoardHttpClient:
    session: JsonBoardHttpSession = cast(JsonBoardHttpSession, requests)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def get_json(self, cfg: SourceConfig, request: Mapping[str, ParamValue]) -> object:
        try:
            response = self.session.get(cfg.url, params=request, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise JsonBoardAdapterError(f"Source {cfg.id} JSON board request failed: {exc}") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise JsonBoardAdapterError(f"Source {cfg.id} JSON board response was not valid JSON") from exc


@dataclass(frozen=True)
class JsonBoardRowsExtractor:
    def extract(self, payload: object, cfg: SourceConfig) -> list[Mapping[str, Any]]:
        if not cfg.list_path:
            raise JsonBoardAdapterError(f"Source {cfg.id} requires list_path")

        current = payload
        for part in cfg.list_path.split("."):
            if not isinstance(current, Mapping):
                raise JsonBoardAdapterError(
                    f"Source {cfg.id} list_path {cfg.list_path!r} does not resolve to a list"
                )
            if part not in current:
                raise JsonBoardAdapterError(f"Source {cfg.id} response missing list_path segment {part!r}")
            current = current[part]

        if not isinstance(current, list):
            raise JsonBoardAdapterError(f"Source {cfg.id} list_path {cfg.list_path!r} does not resolve to a list")

        rows: list[Mapping[str, Any]] = []
        for index, row in enumerate(current):
            if not isinstance(row, Mapping):
                raise JsonBoardAdapterError(f"Source {cfg.id} row {index} is not an object")
            rows.append(row)
        return rows


@dataclass(frozen=True)
class JsonBoardRowMapper:
    def map(self, row: Mapping[str, Any], cfg: SourceConfig) -> Item:
        item_id_field = self._field_param(cfg, "item_id_field")
        title_field = self._field_param(cfg, "title_field")
        published_field = self._optional_field_param(cfg, "published_field")

        item_id = self._required(row, item_id_field, cfg)
        title = self._required(row, title_field, cfg)

        if cfg.detail_url is None:
            raise JsonBoardAdapterError(f"Source {cfg.id} requires detail_url")

        return Item(
            item_id=str(item_id),
            title=str(title),
            link=cfg.detail_url.format(id=item_id),
            published=self._published(row, cfg, published_field),
        )

    def ordering_value(self, row: Mapping[str, Any], cfg: SourceConfig) -> datetime | float | str:
        ordering_field = self._optional_field_param(cfg, "ordering_field")
        published_field = self._optional_field_param(cfg, "published_field")
        field = ordering_field or published_field
        if field is None:
            raise JsonBoardAdapterError(f"Source {cfg.id} requires params.published_field or params.ordering_field")

        value = self._required(row, field, cfg)
        if published_field is not None and field == published_field:
            return self.parse_published(value, cfg, field)
        if isinstance(value, bool):
            raise JsonBoardAdapterError(f"Source {cfg.id} field {field!r} must be sortable")
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise JsonBoardAdapterError(f"Source {cfg.id} row missing required field {field!r}")
            try:
                return float(stripped)
            except ValueError:
                return stripped
        raise JsonBoardAdapterError(f"Source {cfg.id} field {field!r} must be sortable")

    def parse_published(self, value: object, cfg: SourceConfig, field: str) -> datetime:
        if not isinstance(value, str):
            raise JsonBoardAdapterError(f"Source {cfg.id} field {field!r} must be a string")
        try:
            parsed = parser.parse(value)
        except (OverflowError, ValueError) as exc:
            raise JsonBoardAdapterError(f"Source {cfg.id} has invalid {field} {value!r}") from exc
        timezone = self._published_timezone(cfg)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)

    def _field_param(self, cfg: SourceConfig, param: str) -> str:
        value = self._optional_field_param(cfg, param)
        if value is None:
            raise JsonBoardAdapterError(f"Source {cfg.id} requires params.{param}")
        return value

    def _optional_field_param(self, cfg: SourceConfig, param: str) -> str | None:
        value = cfg.params.get(param)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise JsonBoardAdapterError(f"Source {cfg.id} params.{param} must be a non-empty string")
        return value.strip()

    def _required(self, row: Mapping[str, Any], field: str, cfg: SourceConfig) -> Any:
        value = row.get(field)
        if value is None or value == "":
            raise JsonBoardAdapterError(f"Source {cfg.id} row missing required field {field!r}")
        return value

    def _published_timezone(self, cfg: SourceConfig) -> ZoneInfo:
        raw_timezone = cfg.params.get("published_timezone", DEFAULT_PUBLISHED_TIMEZONE)
        if not isinstance(raw_timezone, str) or not raw_timezone.strip():
            raise JsonBoardAdapterError(f"Source {cfg.id} params.published_timezone must be a timezone name")
        try:
            return ZoneInfo(raw_timezone)
        except ZoneInfoNotFoundError as exc:
            raise JsonBoardAdapterError(f"Source {cfg.id} has unknown timezone {raw_timezone!r}") from exc

    def _published(self, row: Mapping[str, Any], cfg: SourceConfig, published_field: str | None) -> datetime | None:
        if published_field is None:
            return None
        published_value = self._required(row, published_field, cfg)
        return self.parse_published(published_value, cfg, published_field)


@dataclass(frozen=True)
class JsonBoardOrderingValidator:
    row_mapper: JsonBoardRowMapper = JsonBoardRowMapper()

    def validate_newest_first(self, rows: list[Mapping[str, Any]], cfg: SourceConfig) -> None:
        previous: datetime | float | str | None = None
        for index, row in enumerate(rows):
            current = self.row_mapper.ordering_value(row, cfg)
            if previous is not None and self._is_newer(current, previous, cfg):
                raise JsonBoardAdapterError(f"Source {cfg.id} rows are not newest-first at index {index}")
            previous = current

    def _is_newer(
        self,
        current: datetime | float | str,
        previous: datetime | float | str,
        cfg: SourceConfig,
    ) -> bool:
        if isinstance(current, datetime) and isinstance(previous, datetime):
            return current > previous
        if isinstance(current, float) and isinstance(previous, float):
            return current > previous
        if isinstance(current, str) and isinstance(previous, str):
            return current > previous
        raise JsonBoardAdapterError(f"Source {cfg.id} ordering field produced mixed value types")


class JsonBoardAdapter(SourcePort):
    def __init__(
        self,
        cfg: SourceConfig,
        *,
        request_builder: JsonBoardRequestBuilder | None = None,
        http_client: JsonBoardHttpClient | None = None,
        rows_extractor: JsonBoardRowsExtractor | None = None,
        row_mapper: JsonBoardRowMapper | None = None,
        ordering_validator: JsonBoardOrderingValidator | None = None,
    ) -> None:
        self.cfg = cfg
        self.request_builder = request_builder or JsonBoardRequestBuilder()
        self.http_client = http_client or JsonBoardHttpClient()
        self.rows_extractor = rows_extractor or JsonBoardRowsExtractor()
        self.row_mapper = row_mapper or JsonBoardRowMapper()
        self.ordering_validator = ordering_validator or JsonBoardOrderingValidator(self.row_mapper)

    def fetch(self) -> list[Item]:
        request = self.request_builder.build(self.cfg)
        payload = self.http_client.get_json(self.cfg, request)
        rows = self.rows_extractor.extract(payload, self.cfg)
        if not rows and self.cfg.empty_result_policy == "error":
            raise JsonBoardAdapterError(f"Source {self.cfg.id} returned no rows")

        self.ordering_validator.validate_newest_first(rows, self.cfg)
        return [self.row_mapper.map(row, self.cfg) for row in rows]


__all__ = [
    "DEFAULT_PAGE",
    "DEFAULT_SIZE",
    "JsonBoardAdapter",
    "JsonBoardAdapterError",
    "JsonBoardHttpClient",
    "JsonBoardOrderingValidator",
    "JsonBoardRequestBuilder",
    "JsonBoardRowMapper",
    "JsonBoardRowsExtractor",
]
