from __future__ import annotations

from pathlib import Path

import pytest

from feed_collector.adapter.outbound import DataTablesAdapter, MofaCookieGateFetcher, RssAdapter
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
        """,
        encoding="utf-8",
    )

    mofa, lawreq = load_sources(sources_file)
    registry = SourceAdapterRegistry()

    mofa_adapter = registry.create(mofa)
    lawreq_adapter = registry.create(lawreq)

    assert mofa.params["fetch_profile"] == "mofa_cookie_gate"
    assert isinstance(mofa_adapter, RssAdapter)
    assert isinstance(mofa_adapter.fetcher, MofaCookieGateFetcher)
    assert isinstance(lawreq_adapter, DataTablesAdapter)


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


def test_load_sources_rejects_bad_source_shape(tmp_path: Path) -> None:
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text("- id: missing-required-fields\n", encoding="utf-8")

    with pytest.raises(PollError, match="mechanism"):
        load_sources(sources_file)
