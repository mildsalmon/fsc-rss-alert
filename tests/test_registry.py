from __future__ import annotations

from pathlib import Path

import pytest

from feed_collector.adapter.outbound import DataTablesAdapter, HtmlScrapeAdapter, MofaCookieGateFetcher, RssAdapter
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
        - id: lawreq
          slug: fsc-lawreq
          name: FSC law requests
          mechanism: datatables
          parser_version: 1
          channel_id:
          interval_minutes: 30
          url: https://example.test/datatables
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
        """,
        encoding="utf-8",
    )

    mofa, lawreq, fss, fsc_legislation = load_sources(sources_file)
    registry = SourceAdapterRegistry()

    mofa_adapter = registry.create(mofa)
    lawreq_adapter = registry.create(lawreq)
    fss_adapter = registry.create(fss)
    fsc_legislation_adapter = registry.create(fsc_legislation)

    assert mofa.params["fetch_profile"] == "mofa_cookie_gate"
    assert isinstance(mofa_adapter, RssAdapter)
    assert isinstance(mofa_adapter.fetcher, MofaCookieGateFetcher)
    assert isinstance(lawreq_adapter, DataTablesAdapter)
    assert isinstance(fss_adapter, HtmlScrapeAdapter)
    assert isinstance(fsc_legislation_adapter, HtmlScrapeAdapter)


def test_default_better_fsc_detail_urls_keep_menu_params() -> None:
    sources = {source.id: source for source in load_sources(Path("sources.yaml"))}

    lawreq = sources["lawreq"]
    opinion = sources["opinion"]

    assert (
        lawreq.detail_url
        == "https://better.fsc.go.kr/fsc_new/replyCase/LawreqDetail.do?stNo=11&muNo=85&muGpNo=75&lawreqIdx={id}"
    )
    assert lawreq.params["published_detail_label"] == "회신일"
    assert (
        opinion.detail_url
        == "https://better.fsc.go.kr/fsc_new/replyCase/OpinionDetail.do?stNo=11&muNo=84&muGpNo=75&opinionIdx={id}"
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


def test_load_sources_rejects_bad_source_shape(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text("- id: missing-required-fields\n", encoding="utf-8")

    with pytest.raises(PollError, match="mechanism"):
        load_sources(sources_file)
