from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import pytest
import requests

from feed_collector.adapter.outbound.datatables import (
    DEFAULT_LENGTH,
    DataTablesAdapter,
    DataTablesAdapterError,
    DataTablesHttpClient,
    DataTablesOrderingValidator,
    DataTablesRequestBuilder,
    DataTablesRowMapper,
    DataTablesRowsExtractor,
    extract_detail_cell_text,
)
from feed_collector.domain import EmptyResultPolicy, ParamValue, SourceConfig


def make_source(
    *,
    params: dict[str, ParamValue] | None = None,
    list_path: str | None = "data",
    detail_url: str | None = "https://example.test/lawreq/{id}",
    empty_result_policy: EmptyResultPolicy = "error",
) -> SourceConfig:
    default_params: dict[str, ParamValue] = {
        "item_id_field": "lawreqIdx",
        "title_field": "title",
        "published_field": "regDt",
        "published_timezone": "Asia/Seoul",
    }
    default_params.update(params or {})
    return SourceConfig(
        id="fsc-lawreq",
        slug="fsc-lawreq",
        name="FSC law requests",
        mechanism="datatables",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://example.test/datatables",
        params=default_params,
        list_path=list_path,
        detail_url=detail_url,
        empty_result_policy=empty_result_policy,
    )


def test_build_request_includes_datatables_defaults_and_config_params() -> None:
    cfg = make_source(
        params={
            "search[value]": "law",
            "order[0][column]": 1,
            "order[0][dir]": "desc",
            "length": 50,
        }
    )

    request = DataTablesRequestBuilder().build(cfg)

    assert request == {
        "draw": 1,
        "start": 0,
        "length": 50,
        "search[value]": "law",
        "order[0][column]": 1,
        "order[0][dir]": "desc",
    }


def test_build_request_uses_default_length() -> None:
    assert DataTablesRequestBuilder().build(make_source())["length"] == DEFAULT_LENGTH


def test_map_row_to_item_link_and_kst_published() -> None:
    item = DataTablesRowMapper().map(
        {"lawreqIdx": 123, "title": "Capital market act", "regDt": "2026-06-21 09:30:00"},
        make_source(),
    )

    assert item.item_id == "123"
    assert item.title == "Capital market act"
    assert item.link == "https://example.test/lawreq/123"
    assert item.published is not None
    assert item.published == datetime(2026, 6, 21, 9, 30, tzinfo=item.published.tzinfo)
    offset = item.published.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 60 * 60


def test_map_row_uses_configured_field_names() -> None:
    cfg = make_source(
        params={
            "item_id_field": "idx",
            "title_field": "subject",
            "published_field": "createdAt",
            "published_timezone": "UTC",
        },
        detail_url="https://example.test/detail/{id}",
    )

    item = DataTablesRowMapper().map({"idx": "abc", "subject": "Custom row", "createdAt": "2026-06-21 09:30:00"}, cfg)

    assert item.item_id == "abc"
    assert item.title == "Custom row"
    assert item.link == "https://example.test/detail/abc"
    assert item.published is not None
    offset = item.published.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0


def test_map_row_allows_sources_without_published_field_when_ordering_field_exists() -> None:
    cfg = make_source(params={"published_field": None, "ordering_field": "rownumber"})

    item = DataTablesRowMapper().map({"lawreqIdx": 123, "title": "No date", "rownumber": 2578}, cfg)

    assert item.item_id == "123"
    assert item.published is None
    DataTablesOrderingValidator().validate_newest_first(
        [
            {"lawreqIdx": 123, "title": "Newer", "rownumber": 2578},
            {"lawreqIdx": 122, "title": "Older", "rownumber": 2577},
        ],
        cfg,
    )


def test_extract_detail_cell_text_reads_reply_date() -> None:
    html = """
    <table>
      <tr><th scope="row">등록자</th><td>관리자</td></tr>
      <tr><th scope="row">회신일</th><td>2026-06-08</td></tr>
    </table>
    """

    assert extract_detail_cell_text(html, "회신일") == "2026-06-08"


def test_adapter_enriches_published_from_detail_page() -> None:
    class FakeResponse:
        text = """
        <table>
          <tr><th scope="row">회신일</th><td>2026-06-08</td></tr>
        </table>
        """

        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            raise AssertionError("detail enrichment should not parse JSON")

    class FakeSession:
        def get(self, url: str, *, timeout: int) -> FakeResponse:
            assert url == "https://example.test/lawreq/123"
            assert timeout == 20
            return FakeResponse()

        def post(self, url: str, *, data: Mapping[str, ParamValue], timeout: int) -> FakeResponse:
            del url, data, timeout
            raise AssertionError("detail enrichment should not POST")

    cfg = make_source(
        params={"published_field": None, "ordering_field": "lawreqNumber", "published_detail_label": "회신일"}
    )
    item = DataTablesRowMapper().map({"lawreqIdx": 123, "title": "No date", "lawreqNumber": "260100"}, cfg)

    enriched = DataTablesAdapter(cfg, http_client=DataTablesHttpClient(session=FakeSession())).enrich_items([item])[0]

    assert enriched.published is not None
    assert enriched.published == datetime(2026, 6, 8, 0, 0, tzinfo=enriched.published.tzinfo)


def test_map_row_converts_aware_reg_dt_to_kst() -> None:
    item = DataTablesRowMapper().map(
        {"lawreqIdx": "abc", "title": "Notice", "regDt": "2026-06-21T00:30:00Z"},
        make_source(),
    )

    assert item.published is not None
    assert item.published.hour == 9
    offset = item.published.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 60 * 60


@pytest.mark.parametrize(
    "row, match",
    [
        ({"title": "Missing id", "regDt": "2026-06-21"}, "lawreqIdx"),
        ({"lawreqIdx": 1, "regDt": "2026-06-21"}, "title"),
        ({"lawreqIdx": 1, "title": "Missing date"}, "regDt"),
        ({"lawreqIdx": 1, "title": "Bad date", "regDt": "not-a-date"}, "invalid regDt"),
    ],
)
def test_map_row_missing_or_invalid_required_fields_fail(row: dict[str, object], match: str) -> None:
    with pytest.raises(DataTablesAdapterError, match=match):
        DataTablesRowMapper().map(row, make_source())


def test_map_row_requires_configured_field_mapping() -> None:
    cfg = make_source(params={"item_id_field": None})

    with pytest.raises(DataTablesAdapterError, match="requires params.item_id_field"):
        DataTablesRowMapper().map({"lawreqIdx": 1, "title": "Title", "regDt": "2026-06-21"}, cfg)


def test_map_row_rejects_unknown_timezone() -> None:
    cfg = make_source(params={"published_timezone": "Not/AZone"})

    with pytest.raises(DataTablesAdapterError, match="unknown timezone"):
        DataTablesRowMapper().map({"lawreqIdx": 1, "title": "Title", "regDt": "2026-06-21"}, cfg)


def test_rows_at_path_rejects_structure_change() -> None:
    with pytest.raises(DataTablesAdapterError, match="list_path"):
        DataTablesRowsExtractor().extract({"payload": []}, make_source(list_path="data"))

    with pytest.raises(DataTablesAdapterError, match="does not resolve to a list"):
        DataTablesRowsExtractor().extract({"data": {"nested": []}}, make_source(list_path="data"))


def test_newest_first_assertion_accepts_descending_rows() -> None:
    DataTablesOrderingValidator().validate_newest_first(
        [
            {"lawreqIdx": 2, "title": "Newer", "regDt": "2026-06-21"},
            {"lawreqIdx": 1, "title": "Older", "regDt": "2026-06-20"},
        ],
        make_source(),
    )


def test_newest_first_assertion_rejects_ascending_rows() -> None:
    with pytest.raises(DataTablesAdapterError, match="newest-first"):
        DataTablesOrderingValidator().validate_newest_first(
            [
                {"lawreqIdx": 1, "title": "Older", "regDt": "2026-06-20"},
                {"lawreqIdx": 2, "title": "Newer", "regDt": "2026-06-21"},
            ],
            make_source(),
        )


def test_fetch_posts_datatables_request_and_maps_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"data": [{"lawreqIdx": 7, "title": "Law request", "regDt": "2026-06-21"}]}

    def fake_post(url: str, *, data: dict[str, object], timeout: int) -> FakeResponse:
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("feed_collector.adapter.outbound.datatables.requests.post", fake_post)

    items = DataTablesAdapter(make_source(params={"search[value]": "capital"})).fetch()

    assert captured["url"] == "https://example.test/datatables"
    assert captured["data"] == {
        "draw": 1,
        "start": 0,
        "length": DEFAULT_LENGTH,
        "search[value]": "capital",
    }
    assert [item.item_id for item in items] == ["7"]


def test_fetch_wraps_http_errors_with_source_context(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            raise requests.HTTPError("503 Service Unavailable")

    monkeypatch.setattr(
        "feed_collector.adapter.outbound.datatables.requests.post",
        lambda url, *, data, timeout: FakeResponse(),
    )

    with pytest.raises(DataTablesAdapterError, match="Source fsc-lawreq DataTables request failed"):
        DataTablesAdapter(make_source()).fetch()


def test_fetch_wraps_invalid_json_with_source_context(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            raise ValueError("not json")

    monkeypatch.setattr(
        "feed_collector.adapter.outbound.datatables.requests.post",
        lambda url, *, data, timeout: FakeResponse(),
    )

    with pytest.raises(DataTablesAdapterError, match="Source fsc-lawreq DataTables response was not valid JSON"):
        DataTablesAdapter(make_source()).fetch()


def test_fetch_empty_result_policy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"data": []}

    monkeypatch.setattr(
        "feed_collector.adapter.outbound.datatables.requests.post",
        lambda url, *, data, timeout: FakeResponse(),
    )

    with pytest.raises(DataTablesAdapterError, match="returned no rows"):
        DataTablesAdapter(make_source()).fetch()


def test_fetch_empty_result_policy_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"data": []}

    monkeypatch.setattr(
        "feed_collector.adapter.outbound.datatables.requests.post",
        lambda url, *, data, timeout: FakeResponse(),
    )

    assert DataTablesAdapter(make_source(empty_result_policy="valid")).fetch() == []
