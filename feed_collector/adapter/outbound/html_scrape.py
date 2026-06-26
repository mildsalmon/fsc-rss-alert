from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Sequence
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser

from feed_collector.adapter.outbound.http_fetch import ByteFetcher, ByteFetcherFactory, HttpFetcherFactory
from feed_collector.application.port.output.source import SourcePort
from feed_collector.domain import Item, SourceConfig


DEFAULT_PUBLISHED_TIMEZONE = "Asia/Seoul"
DEFAULT_LINK_HREF_CONTAINS = "view.do"
DEFAULT_ITEM_ID_QUERY_PARAM = "nttId"


class HtmlScrapeAdapterError(ValueError):
    pass


@dataclass(frozen=True)
class HtmlLink:
    href: str
    text: str


@dataclass(frozen=True)
class HtmlCell:
    text: str
    links: tuple[HtmlLink, ...] = ()


@dataclass(frozen=True)
class HtmlRow:
    cells: tuple[HtmlCell, ...]


class HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[HtmlRow] = []
        self._current_cells: list[HtmlCell] | None = None
        self._current_cell_parts: list[str] | None = None
        self._current_cell_links: list[HtmlLink] = []
        self._current_link_href: str | None = None
        self._current_link_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "tr" and self._current_cells is None:
            self._current_cells = []
            return
        if tag == "td" and self._current_cells is not None and self._current_cell_parts is None:
            self._current_cell_parts = []
            self._current_cell_links = []
            return
        if tag == "a" and self._current_cell_parts is not None:
            href = _attr(attrs, "href")
            if href:
                self._current_link_href = href
                self._current_link_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._current_link_href is not None:
            self._current_cell_links.append(
                HtmlLink(
                    href=self._current_link_href,
                    text=_normalize_text(" ".join(self._current_link_parts)),
                )
            )
            self._current_link_href = None
            self._current_link_parts = []
            return
        if tag == "td" and self._current_cells is not None and self._current_cell_parts is not None:
            self._current_cells.append(
                HtmlCell(
                    text=_normalize_text(" ".join(self._current_cell_parts)),
                    links=tuple(self._current_cell_links),
                )
            )
            self._current_cell_parts = None
            self._current_cell_links = []
            return
        if tag == "tr" and self._current_cells is not None:
            if self._current_cells:
                self.rows.append(HtmlRow(cells=tuple(self._current_cells)))
            self._current_cells = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._current_cell_parts is None:
            return
        self._current_cell_parts.append(data)
        if self._current_link_href is not None:
            self._current_link_parts.append(data)


@dataclass(frozen=True)
class HtmlRowMapper:
    def map(self, row: HtmlRow, cfg: SourceConfig) -> Item | None:
        link = self._item_link(row, cfg)
        if link is None:
            return None

        item_id = self._item_id(link.href, cfg)
        published = self._published(row, cfg)
        return Item(
            item_id=item_id,
            title=link.text or self._title_cell_text(row, cfg),
            link=urljoin(cfg.url, link.href),
            published=published,
        )

    def _item_link(self, row: HtmlRow, cfg: SourceConfig) -> HtmlLink | None:
        needle = _str_param(cfg, "link_href_contains", DEFAULT_LINK_HREF_CONTAINS)
        for cell in row.cells:
            for link in cell.links:
                if needle in link.href:
                    return link
        return None

    def _item_id(self, href: str, cfg: SourceConfig) -> str:
        query_param = _str_param(cfg, "item_id_query_param", DEFAULT_ITEM_ID_QUERY_PARAM)
        values = parse_qs(urlsplit(href).query).get(query_param)
        if not values or not values[0].strip():
            raise HtmlScrapeAdapterError(f"Source {cfg.id} item link missing query param {query_param!r}: {href}")
        return values[0].strip()

    def _published(self, row: HtmlRow, cfg: SourceConfig) -> datetime:
        date_cell_index = _non_negative_int_param(cfg, "date_cell_index")
        if date_cell_index >= len(row.cells):
            raise HtmlScrapeAdapterError(
                f"Source {cfg.id} date_cell_index {date_cell_index} exceeds row width {len(row.cells)}"
            )
        raw = row.cells[date_cell_index].text
        if not raw:
            raise HtmlScrapeAdapterError(f"Source {cfg.id} row missing published date")
        try:
            parsed = parser.parse(raw)
        except (OverflowError, ValueError) as exc:
            raise HtmlScrapeAdapterError(f"Source {cfg.id} has invalid published date {raw!r}") from exc
        timezone = _published_timezone(cfg)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)

    def _title_cell_text(self, row: HtmlRow, cfg: SourceConfig) -> str:
        title_cell_index = _non_negative_int_param(cfg, "title_cell_index", default=1)
        if title_cell_index < len(row.cells) and row.cells[title_cell_index].text:
            return row.cells[title_cell_index].text
        raise HtmlScrapeAdapterError(f"Source {cfg.id} row missing title")


@dataclass(frozen=True)
class HtmlScrapeAdapter(SourcePort):
    cfg: SourceConfig
    fetcher: ByteFetcher
    row_mapper: HtmlRowMapper = HtmlRowMapper()

    def fetch(self) -> list[Item]:
        html = self.fetcher.fetch(self.cfg.url).decode("utf-8", errors="replace")
        rows = parse_html_rows(html)
        items = [item for row in rows if (item := self.row_mapper.map(row, self.cfg)) is not None]
        if not items and self.cfg.empty_result_policy == "error":
            raise HtmlScrapeAdapterError(f"Source {self.cfg.id} returned no rows")
        return items


@dataclass(frozen=True)
class HtmlScrapeAdapterFactory:
    fetcher_factory: ByteFetcherFactory = field(default_factory=HttpFetcherFactory)

    def create(self, source: SourceConfig) -> HtmlScrapeAdapter:
        return HtmlScrapeAdapter(source, fetcher=self.fetcher_factory.create(source))

    def __call__(self, source: SourceConfig) -> HtmlScrapeAdapter:
        return self.create(source)


def parse_html_rows(html: str) -> list[HtmlRow]:
    parser = HtmlTableParser()
    parser.feed(html)
    return parser.rows


def _published_timezone(cfg: SourceConfig) -> ZoneInfo:
    raw_timezone = _str_param(cfg, "published_timezone", DEFAULT_PUBLISHED_TIMEZONE)
    try:
        return ZoneInfo(raw_timezone)
    except ZoneInfoNotFoundError as exc:
        raise HtmlScrapeAdapterError(f"Source {cfg.id} has unknown timezone {raw_timezone!r}") from exc


def _attr(attrs: Sequence[tuple[str, str | None]], name: str) -> str | None:
    for key, value in attrs:
        if key == name:
            return value
    return None


def _str_param(cfg: SourceConfig, key: str, default: str) -> str:
    value = cfg.params.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise HtmlScrapeAdapterError(f"Source {cfg.id} param {key} must be a non-empty string")
    return value.strip()


def _non_negative_int_param(cfg: SourceConfig, key: str, default: int | None = None) -> int:
    raw_value = cfg.params.get(key, default)
    if raw_value is None or isinstance(raw_value, bool):
        raise HtmlScrapeAdapterError(f"Source {cfg.id} param {key} must be a non-negative integer")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise HtmlScrapeAdapterError(f"Source {cfg.id} param {key} must be a non-negative integer") from exc
    if value < 0:
        raise HtmlScrapeAdapterError(f"Source {cfg.id} param {key} must be a non-negative integer")
    return value


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "HtmlCell",
    "HtmlLink",
    "HtmlRow",
    "HtmlRowMapper",
    "HtmlScrapeAdapter",
    "HtmlScrapeAdapterError",
    "HtmlScrapeAdapterFactory",
    "parse_html_rows",
]
