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


FSC_LEGISLATION_HTML = """
<html>
  <body>
    <ul class="board-list">
      <li>
        <div class="inner">
          <div class="count">1405</div>
          <div class="cont">
            <div class="subject">
              <a href="./po040301/view?noticeId=4153&curPage=&srchKey=&srchText=&srchBeginDt=&srchEndDt="
                 title="「신용협동조합법 시행령」 일부개정령(안) 입법예고">
                「신용협동조합법 시행령」 일부개정령(안) 입법예고
              </a>
            </div>
            <div class="info">
              <span>구분 : 입법예고</span>
              <span>법률구분 : 신용협동조합법 시행령</span>
              <span>예고기간 : 2026-06-05 ~ 2026-07-15</span>
            </div>
          </div>
          <div class="day">2026-06-05</div>
        </div>
      </li>
    </ul>
  </body>
</html>
"""


OFAC_RECENT_ACTIONS_HTML = """
<html>
  <body>
    <div class="view-content">
      <div class="margin-bottom-4 search-result views-row">
        <div>
          <div class="font-sans-lg margin-bottom-05 margin-top-1 text-no-underline">
            <a href="/recent-actions/20260629" hreflang="en">Russia-related Designations Removals</a>
          </div>
        </div>
        <div>
          <div class="margin-top-1 font-sans-2xs line-height-sans-3 margin-bottom-1">
            June 29, 2026 -
            <a href="/recent-actions/sanctions-list-updates">Sanctions List Updates</a>
          </div>
        </div>
      </div>
    </div>
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


def make_fsc_legislation_source(
    *,
    params: dict[str, ParamValue] | None = None,
    empty_result_policy: EmptyResultPolicy = "error",
) -> SourceConfig:
    base_params: dict[str, ParamValue] = {
        "row_tag": "li",
        "item_id_query_param": "noticeId",
        "link_href_contains": "po040301/view",
        "published_regex": r"(?P<date>\d{4}-\d{2}-\d{2})$",
    }
    return SourceConfig(
        id="fsc-legislation",
        slug="fsc-legislation",
        name="금융위 입법예고/규정변경예고",
        mechanism="html",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://www.fsc.go.kr/po040301",
        params=base_params | (params or {}),
        empty_result_policy=empty_result_policy,
    )


def make_ofac_recent_actions_source(
    *,
    params: dict[str, ParamValue] | None = None,
    empty_result_policy: EmptyResultPolicy = "error",
) -> SourceConfig:
    base_params: dict[str, ParamValue] = {
        "row_tag": "div",
        "row_class_contains": "views-row",
        "link_href_contains": "/recent-actions/",
        "item_id_regex": r"/recent-actions/(?P<id>[^/?#]+)",
        "published_regex": r"(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})",
        "published_timezone": "UTC",
    }
    return SourceConfig(
        id="ofac-sdn",
        slug="ofac-sdn",
        name="OFAC Sanctions List Updates",
        mechanism="html",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://ofac.treasury.gov/recent-actions/sanctions-list-updates",
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


def test_html_scrape_adapter_maps_fsc_legislation_list_rows_to_items() -> None:
    adapter = HtmlScrapeAdapter(
        make_fsc_legislation_source(),
        fetcher=FakeFetcher(FSC_LEGISLATION_HTML.encode()),
    )

    items = adapter.fetch()

    assert len(items) == 1
    assert items[0].item_id == "4153"
    assert items[0].title == "「신용협동조합법 시행령」 일부개정령(안) 입법예고"
    assert (
        items[0].link
        == "https://www.fsc.go.kr/po040301/view?noticeId=4153&curPage=&srchKey=&srchText=&srchBeginDt=&srchEndDt="
    )
    assert items[0].published == datetime(2026, 6, 5, tzinfo=ZoneInfo("Asia/Seoul"))


def test_html_scrape_adapter_maps_ofac_recent_action_rows_to_items() -> None:
    adapter = HtmlScrapeAdapter(
        make_ofac_recent_actions_source(),
        fetcher=FakeFetcher(OFAC_RECENT_ACTIONS_HTML.encode()),
    )

    items = adapter.fetch()

    assert len(items) == 1
    assert items[0].item_id == "20260629"
    assert items[0].title == "Russia-related Designations Removals"
    assert items[0].link == "https://ofac.treasury.gov/recent-actions/20260629"
    assert items[0].published == datetime(2026, 6, 29, tzinfo=ZoneInfo("UTC"))


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


def test_html_scrape_adapter_errors_on_missing_published_regex_match() -> None:
    source = make_fsc_legislation_source(params={"published_regex": r"게시일 : (?P<date>\d{4}-\d{2}-\d{2})"})
    adapter = HtmlScrapeAdapter(source, fetcher=FakeFetcher(FSC_LEGISLATION_HTML.encode()))

    with pytest.raises(HtmlScrapeAdapterError, match="published_regex"):
        adapter.fetch()
