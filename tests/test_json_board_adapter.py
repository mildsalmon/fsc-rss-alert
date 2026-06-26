from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import NoReturn

import pytest
import requests

from feed_collector.adapter.outbound.json_board import (
    DEFAULT_PAGE,
    DEFAULT_SIZE,
    JsonBoardAdapter,
    JsonBoardAdapterError,
    JsonBoardHttpClient,
    JsonBoardOrderingValidator,
    JsonBoardRequestBuilder,
    JsonBoardRowMapper,
    JsonBoardRowsExtractor,
)
from feed_collector.domain import EmptyResultPolicy, ParamValue, SourceConfig


def make_source(
    *,
    params: dict[str, ParamValue] | None = None,
    list_path: str | None = "result",
    detail_url: str | None = "https://www.kofiu.go.kr/kor/notification/sanctions_view.do?ntcnYardOrdrNo={id}&seCd=0022",
    empty_result_policy: EmptyResultPolicy = "error",
) -> SourceConfig:
    default_params: dict[str, ParamValue] = {
        "seCd": "0022",
        "item_id_field": "ntcnYardOrdrNo",
        "title_field": "ntcnYardSjNm",
        "published_field": "ntcnYardRgiDt",
        "ordering_field": "ntcnYardOrdrNo",
        "published_timezone": "Asia/Seoul",
    }
    default_params.update(params or {})
    return SourceConfig(
        id="fiu-sanctions",
        slug="fiu-sanctions",
        name="FIU sanctions",
        mechanism="json_board",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://www.kofiu.go.kr/cmn/board/selectBoardListFile.do",
        params=default_params,
        list_path=list_path,
        detail_url=detail_url,
        empty_result_policy=empty_result_policy,
    )


def test_build_request_includes_json_board_defaults_and_config_params() -> None:
    cfg = make_source(
        params={
            "selScope": "전체",
            "subSech": "은행",
            "size": 20,
            "page": 2,
        }
    )

    request = JsonBoardRequestBuilder().build(cfg)

    assert request == {
        "page": 2,
        "size": 20,
        "seCd": "0022",
        "selScope": "전체",
        "subSech": "은행",
    }


def test_build_request_uses_default_pagination() -> None:
    request = JsonBoardRequestBuilder().build(make_source())

    assert request["page"] == DEFAULT_PAGE
    assert request["size"] == DEFAULT_SIZE


def test_map_row_to_item_link_and_kst_published() -> None:
    item = JsonBoardRowMapper().map(
        {
            "ntcnYardOrdrNo": "126",
            "ntcnYardSjNm": "국민은행 제재내용 공개안",
            "ntcnYardRgiDt": "2026-04-24 09:20:49",
        },
        make_source(),
    )

    assert item.item_id == "126"
    assert item.title == "국민은행 제재내용 공개안"
    assert (
        item.link
        == "https://www.kofiu.go.kr/kor/notification/sanctions_view.do?ntcnYardOrdrNo=126&seCd=0022"
    )
    assert item.published is not None
    assert item.published == datetime(2026, 4, 24, 9, 20, 49, tzinfo=item.published.tzinfo)
    offset = item.published.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 60 * 60


def test_ordering_validator_accepts_newest_first_rows() -> None:
    JsonBoardOrderingValidator().validate_newest_first(
        [
            {"ntcnYardOrdrNo": "126", "ntcnYardSjNm": "Newer", "ntcnYardRgiDt": "2026-04-24 09:20:49"},
            {"ntcnYardOrdrNo": "125", "ntcnYardSjNm": "Older", "ntcnYardRgiDt": "2026-04-24 09:20:28"},
        ],
        make_source(),
    )


def test_ordering_validator_rejects_older_before_newer_rows() -> None:
    with pytest.raises(JsonBoardAdapterError, match="newest-first"):
        JsonBoardOrderingValidator().validate_newest_first(
            [
                {"ntcnYardOrdrNo": "125", "ntcnYardSjNm": "Older", "ntcnYardRgiDt": "2026-04-24 09:20:28"},
                {"ntcnYardOrdrNo": "126", "ntcnYardSjNm": "Newer", "ntcnYardRgiDt": "2026-04-24 09:20:49"},
            ],
            make_source(),
        )


def test_rows_extractor_reads_configured_list_path() -> None:
    rows = JsonBoardRowsExtractor().extract(
        {"payload": {"items": [{"ntcnYardOrdrNo": "126"}]}},
        make_source(list_path="payload.items"),
    )

    assert rows == [{"ntcnYardOrdrNo": "126"}]


def test_adapter_fetches_json_board_items() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return {
                "result": [
                    {
                        "ntcnYardOrdrNo": "126",
                        "ntcnYardSjNm": "국민은행 제재내용 공개안",
                        "ntcnYardRgiDt": "2026-04-24 09:20:49",
                    }
                ]
            }

    class FakeSession:
        def get(self, url: str, *, params: Mapping[str, ParamValue], timeout: int) -> FakeResponse:
            assert url == "https://www.kofiu.go.kr/cmn/board/selectBoardListFile.do"
            assert params["seCd"] == "0022"
            assert params["page"] == DEFAULT_PAGE
            assert params["size"] == DEFAULT_SIZE
            assert timeout == 20
            return FakeResponse()

    adapter = JsonBoardAdapter(make_source(), http_client=JsonBoardHttpClient(session=FakeSession()))

    items = adapter.fetch()

    assert len(items) == 1
    assert items[0].item_id == "126"


def test_adapter_errors_on_empty_required_source() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return {"result": []}

    class FakeSession:
        def get(self, url: str, *, params: Mapping[str, ParamValue], timeout: int) -> FakeResponse:
            del url, params, timeout
            return FakeResponse()

    adapter = JsonBoardAdapter(make_source(), http_client=JsonBoardHttpClient(session=FakeSession()))

    with pytest.raises(JsonBoardAdapterError, match="returned no rows"):
        adapter.fetch()


def test_adapter_can_accept_empty_valid_source() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return {"result": []}

    class FakeSession:
        def get(self, url: str, *, params: Mapping[str, ParamValue], timeout: int) -> FakeResponse:
            del url, params, timeout
            return FakeResponse()

    adapter = JsonBoardAdapter(
        make_source(empty_result_policy="valid"),
        http_client=JsonBoardHttpClient(session=FakeSession()),
    )

    assert adapter.fetch() == []


def test_http_client_wraps_request_failures() -> None:
    class FakeSession:
        def get(self, url: str, *, params: Mapping[str, ParamValue], timeout: int) -> NoReturn:
            del url, params, timeout
            raise requests.Timeout("boom")

    with pytest.raises(JsonBoardAdapterError, match="JSON board request failed"):
        JsonBoardHttpClient(session=FakeSession()).get_json(make_source(), {})


def test_http_client_rejects_invalid_json() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            raise ValueError("invalid")

    class FakeSession:
        def get(self, url: str, *, params: Mapping[str, ParamValue], timeout: int) -> FakeResponse:
            del url, params, timeout
            return FakeResponse()

    with pytest.raises(JsonBoardAdapterError, match="not valid JSON"):
        JsonBoardHttpClient(session=FakeSession()).get_json(make_source(), {})
