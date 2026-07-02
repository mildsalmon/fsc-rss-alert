from __future__ import annotations

from pathlib import Path

import pytest

from feed_collector.adapter.outbound import (
    BulkSdnAdapter,
    DataTablesAdapter,
    HtmlScrapeAdapter,
    JsonBoardAdapter,
    MofaCookieGateFetcher,
    RssAdapter,
)
from feed_collector.errors import PollError
from feed_collector.registry import SourceAdapterRegistry, load_sources


def test_load_sources_yaml_and_dispatches_mechanisms(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        """
        - id: mofa
          slug: mofa-sanctions
          name: MOFA sanctions
          mechanism: rss
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://www.mofa.go.kr/www/brd/rss.do?brdId=235
          params:
            fetch_profile: mofa_cookie_gate
            fetch_retry_delay_seconds: 0
          empty_result_policy: error
        - id: fsc-press
          slug: fsc-press-release
          name: FSC press releases
          mechanism: rss
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111
          display_url: https://www.fsc.go.kr/no010101
          empty_result_policy: error
        - id: lawreq
          slug: fsc-lawreq
          name: FSC law requests
          mechanism: datatables
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://example.test/datatables
          display_url: https://example.test/lawreq
          params:
            stNo: 11
            muNo: 85
            muGpNo: 75
            item_id_field: lawreqIdx
            title_field: title
            published_field: regDt
          list_path: data
          detail_url: https://example.test/lawreq/{id}
        - id: fss
          slug: fss-press
          name: FSS press
          mechanism: html
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://example.test/fss/list.do
          params:
            item_id_query_param: nttId
            link_href_contains: view.do
            title_cell_index: 1
            date_cell_index: 3
          empty_result_policy: error
        - id: fsc-legislation
          slug: fsc-legislation
          name: FSC legislation
          mechanism: html
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://www.fsc.go.kr/po040301
          params:
            row_tag: li
            item_id_query_param: noticeId
            link_href_contains: po040301/view
            published_regex: (?P<date>\\d{4}-\\d{2}-\\d{2})$
          empty_result_policy: error
        - id: fiu-sanctions
          slug: fiu-sanctions
          name: FIU sanctions
          mechanism: json_board
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://www.kofiu.go.kr/cmn/board/selectBoardListFile.do
          params:
            seCd: "0022"
            item_id_field: ntcnYardOrdrNo
            title_field: ntcnYardSjNm
            published_field: ntcnYardRgiDt
          list_path: result
          detail_url: https://www.kofiu.go.kr/kor/notification/sanctions_view.do?ntcnYardOrdrNo={id}&seCd=0022
          empty_result_policy: error
        - id: ofac-sdn
          slug: ofac-sdn
          name: OFAC SDN
          mechanism: bulk
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML
          params:
            published_timezone: UTC
            timeout_seconds: 60
            max_seen_items_per_source:
          detail_url: https://sanctionssearch.ofac.treas.gov/Details.aspx?id={id}
          empty_result_policy: error
        """,
        encoding="utf-8",
    )

    mofa, fsc_press, lawreq, fss, fsc_legislation, fiu, ofac = load_sources(sources_file)
    registry = SourceAdapterRegistry()

    mofa_adapter = registry.create(mofa)
    fsc_press_adapter = registry.create(fsc_press)
    lawreq_adapter = registry.create(lawreq)
    fss_adapter = registry.create(fss)
    fsc_legislation_adapter = registry.create(fsc_legislation)
    fiu_adapter = registry.create(fiu)
    ofac_adapter = registry.create(ofac)

    assert mofa.params["fetch_profile"] == "mofa_cookie_gate"
    assert isinstance(mofa_adapter, RssAdapter)
    assert isinstance(mofa_adapter.fetcher, MofaCookieGateFetcher)
    assert fsc_press.display_url == "https://www.fsc.go.kr/no010101"
    assert isinstance(fsc_press_adapter, RssAdapter)
    assert lawreq.display_url == "https://example.test/lawreq"
    assert isinstance(lawreq_adapter, DataTablesAdapter)
    assert isinstance(fss_adapter, HtmlScrapeAdapter)
    assert isinstance(fsc_legislation_adapter, HtmlScrapeAdapter)
    assert isinstance(fiu_adapter, JsonBoardAdapter)
    assert isinstance(ofac_adapter, BulkSdnAdapter)


def test_default_better_fsc_detail_urls_keep_menu_params() -> None:
    sources = {source.id: source for source in load_sources(Path("sources.yaml"))}

    lawreq = sources["lawreq"]
    opinion = sources["opinion"]

    assert (
        lawreq.detail_url
        == "https://better.fsc.go.kr/fsc_new/replyCase/LawreqDetail.do?stNo=11&muNo=85&muGpNo=75&lawreqIdx={id}"
    )
    assert (
        lawreq.display_url
        == "https://better.fsc.go.kr/fsc_new/replyCase/LawreqList.do?muGpNo=75&muNo=85&stNo=11"
    )
    assert lawreq.params["published_detail_label"] == "회신일"
    assert (
        opinion.detail_url
        == "https://better.fsc.go.kr/fsc_new/replyCase/OpinionDetail.do?stNo=11&muNo=84&muGpNo=75&opinionIdx={id}"
    )
    assert (
        opinion.display_url
        == "https://better.fsc.go.kr/fsc_new/replyCase/OpinionList.do?muGpNo=75&muNo=86&stNo=11"
    )
    assert opinion.params["item_id_field"] == "opinionIdx"
    assert opinion.params["ordering_field"] == "opinionNumber"
    assert opinion.params["published_detail_label"] == "회신일"


def test_default_fss_source_uses_html_scrape_config() -> None:
    sources = {source.id: source for source in load_sources(Path("sources.yaml"))}

    fss = sources["fss"]

    assert fss.mechanism == "html"
    assert fss.url == "https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218"
    assert fss.params["item_id_query_param"] == "nttId"
    assert fss.params["link_href_contains"] == "/fss/bbs/B0000188/view.do"
    assert fss.params["date_cell_index"] == 3


def test_default_fsc_press_source_uses_rss_config() -> None:
    sources = {source.id: source for source in load_sources(Path("sources.yaml"))}

    fsc_press = sources["fsc-press"]

    assert fsc_press.name == "금융위 보도자료"
    assert fsc_press.slug == "fsc-press-release"
    assert fsc_press.mechanism == "rss"
    assert fsc_press.url == "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111"
    assert fsc_press.display_url == "https://www.fsc.go.kr/no010101"
    assert fsc_press.empty_result_policy == "error"


def test_default_fsc_legislation_source_uses_html_scrape_config() -> None:
    sources = {source.id: source for source in load_sources(Path("sources.yaml"))}

    fsc_legislation = sources["fsc-legislation"]

    assert fsc_legislation.name == "금융위 입법예고/규정변경예고"
    assert fsc_legislation.slug == "fsc-legislation"
    assert fsc_legislation.mechanism == "html"
    assert fsc_legislation.url == "https://www.fsc.go.kr/po040301"
    assert fsc_legislation.params["row_tag"] == "li"
    assert fsc_legislation.params["item_id_query_param"] == "noticeId"
    assert fsc_legislation.params["link_href_contains"] == "po040301/view"


def test_default_fiu_source_uses_json_board_config() -> None:
    sources = {source.id: source for source in load_sources(Path("sources.yaml"))}

    fiu = sources["fiu-sanctions"]

    assert fiu.name == "FIU 제재공시"
    assert fiu.slug == "fiu-sanctions"
    assert fiu.mechanism == "json_board"
    assert fiu.url == "https://www.kofiu.go.kr/cmn/board/selectBoardListFile.do"
    assert fiu.display_url == "https://www.kofiu.go.kr/kor/notification/sanctions.do"
    assert fiu.params["seCd"] == "0022"
    assert fiu.params["item_id_field"] == "ntcnYardOrdrNo"
    assert fiu.params["item_revision_field"] == "ntcnYardChangeDt"
    assert fiu.params["published_field"] == "ntcnYardRgiDt"
    assert "ordering_field" not in fiu.params
    assert fiu.list_path == "result"


def test_default_ofac_source_uses_recent_actions_html_config() -> None:
    sources = {source.id: source for source in load_sources(Path("sources.yaml"))}

    ofac = sources["ofac-sdn"]

    assert ofac.name == "OFAC Sanctions List Updates"
    assert ofac.slug == "ofac-sdn"
    assert ofac.mechanism == "html"
    assert ofac.url == "https://ofac.treasury.gov/recent-actions/sanctions-list-updates"
    assert ofac.display_url == "https://ofac.treasury.gov/recent-actions/sanctions-list-updates"
    assert ofac.params["row_tag"] == "div"
    assert ofac.params["row_class_contains"] == "views-row"
    assert ofac.params["link_href_contains"] == "/recent-actions/"
    assert ofac.params["item_id_regex"] == "/recent-actions/(?P<id>[^/?#]+)"
    assert ofac.params["published_regex"] == r"(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})"
    assert ofac.params["published_timezone"] == "UTC"
    assert ofac.params["timeout_seconds"] == 60


def test_load_sources_rejects_bad_source_shape(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text("- id: missing-required-fields\n", encoding="utf-8")

    with pytest.raises(PollError, match="mechanism"):
        load_sources(sources_file)
