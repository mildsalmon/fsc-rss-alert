from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from feed_collector.adapter.outbound.html_scrape import (
    HtmlScrapeAdapter,
    HtmlScrapeAdapterError,
    parse_html_rows,
)
from feed_collector.domain import EmptyResultPolicy, ParamValue, SourceConfig


FSS_HTML = """
<html>
  <body>
    <table>
      <tbody>
        <tr>
          <td class="num">20667</td>
          <td class="title">
            <a href="/fss/bbs/B0000188/view.do?nttId=218843&menuNo=200218&pageIndex=1">
              2026년 5월 외국인 증권투자 동향
            </a>
          </td>
          <td>자본시장국</td>
          <td>2026-06-26</td>
          <td></td>
          <td>123</td>
        </tr>
      </tbody>
    </table>
  </body>
</html>
"""


@dataclass
class FakeFetcher:
    payload: bytes

    def fetch(self, url: str) -> bytes:
        return self.payload


def make_source(
    *,
    params: dict[str, ParamValue] | None = None,
    empty_result_policy: EmptyResultPolicy = "error",
) -> SourceConfig:
    base_params: dict[str, ParamValue] = {
        "item_id_query_param": "nttId",
        "link_href_contains": "/fss/bbs/B0000188/view.do",
        "title_cell_index": 1,
        "date_cell_index": 3,
    }
    return SourceConfig(
        id="fss",
        slug="fss-press",
        name="금감원 보도자료",
        mechanism="html",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218",
        params=base_params | (params or {}),
        empty_result_policy=empty_result_policy,
    )


def test_parse_html_rows_collects_cells_and_links() -> None:
    rows = parse_html_rows(FSS_HTML)

    assert len(rows) == 1
    assert rows[0].cells[0].text == "20667"
    assert rows[0].cells[1].links[0].text == "2026년 5월 외국인 증권투자 동향"


def test_html_scrape_adapter_maps_fss_table_rows_to_items() -> None:
    adapter = HtmlScrapeAdapter(make_source(), fetcher=FakeFetcher(FSS_HTML.encode()))

    items = adapter.fetch()

    assert len(items) == 1
    assert items[0].item_id == "218843"
    assert items[0].title == "2026년 5월 외국인 증권투자 동향"
    assert (
        items[0].link
        == "https://www.fss.or.kr/fss/bbs/B0000188/view.do?nttId=218843&menuNo=200218&pageIndex=1"
    )
    assert items[0].published == datetime(2026, 6, 26, tzinfo=ZoneInfo("Asia/Seoul"))


def test_html_scrape_adapter_errors_on_empty_required_source() -> None:
    adapter = HtmlScrapeAdapter(make_source(), fetcher=FakeFetcher(b"<html></html>"))

    with pytest.raises(HtmlScrapeAdapterError, match="returned no rows"):
        adapter.fetch()


def test_html_scrape_adapter_can_accept_empty_valid_source() -> None:
    adapter = HtmlScrapeAdapter(
        make_source(empty_result_policy="valid"),
        fetcher=FakeFetcher(b"<html></html>"),
    )

    assert adapter.fetch() == []


def test_html_scrape_adapter_requires_valid_date_cell() -> None:
    source = make_source(params={"date_cell_index": 9})
    adapter = HtmlScrapeAdapter(source, fetcher=FakeFetcher(FSS_HTML.encode()))

    with pytest.raises(HtmlScrapeAdapterError, match="date_cell_index"):
        adapter.fetch()
