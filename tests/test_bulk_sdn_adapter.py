from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from feed_collector.adapter.outbound.bulk_sdn import (
    BulkSdnAdapter,
    BulkSdnAdapterError,
    parse_sdn_items,
)
from feed_collector.domain import EmptyResultPolicy, ParamValue, SourceConfig


SDN_XML = b"""<?xml version="1.0" standalone="yes"?>
<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">
  <publshInformation>
    <Publish_Date>06/25/2026</Publish_Date>
    <Record_Count>2</Record_Count>
  </publshInformation>
  <sdnEntry>
    <uid>36</uid>
    <lastName>AEROCARIBBEAN AIRLINES</lastName>
    <sdnType>Entity</sdnType>
    <programList>
      <program>CUBA</program>
    </programList>
  </sdnEntry>
  <sdnEntry>
    <uid>29784</uid>
    <firstName>IVAN</firstName>
    <lastName>PETROV</lastName>
    <sdnType>Individual</sdnType>
    <programList>
      <program>RUSSIA-EO14024</program>
      <program>CYBER2</program>
    </programList>
  </sdnEntry>
</sdnList>
"""


@dataclass
class FakeFetcher:
    payload: bytes

    def fetch(self, url: str) -> bytes:
        return self.payload


def make_source(
    *,
    params: dict[str, ParamValue] | None = None,
    detail_url: str | None = "https://sanctionssearch.ofac.treas.gov/Details.aspx?id={id}",
    empty_result_policy: EmptyResultPolicy = "error",
) -> SourceConfig:
    base_params: dict[str, ParamValue] = {
        "published_timezone": "UTC",
    }
    return SourceConfig(
        id="ofac-sdn",
        slug="ofac-sdn",
        name="OFAC SDN",
        mechanism="bulk",
        parser_version=1,
        channel_id=None,
        interval_minutes=30,
        url="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML",
        params=base_params | (params or {}),
        detail_url=detail_url,
        empty_result_policy=empty_result_policy,
    )


def test_parse_sdn_items_maps_entity_entries() -> None:
    items = parse_sdn_items(SDN_XML, make_source())

    assert len(items) == 2
    assert items[0].item_id == "36"
    assert items[0].title == "AEROCARIBBEAN AIRLINES (Entity; CUBA)"
    assert items[0].link == "https://sanctionssearch.ofac.treas.gov/Details.aspx?id=36"
    assert items[0].published == datetime(2026, 6, 25, tzinfo=ZoneInfo("UTC"))


def test_parse_sdn_items_maps_individual_entries() -> None:
    items = parse_sdn_items(SDN_XML, make_source())

    assert items[1].item_id == "29784"
    assert items[1].title == "IVAN PETROV (Individual; RUSSIA-EO14024, CYBER2)"
    assert items[1].link == "https://sanctionssearch.ofac.treas.gov/Details.aspx?id=29784"


def test_bulk_sdn_adapter_fetches_and_parses_items() -> None:
    adapter = BulkSdnAdapter(make_source(), fetcher=FakeFetcher(SDN_XML))

    items = adapter.fetch()

    assert [item.item_id for item in items] == ["36", "29784"]


def test_parse_sdn_items_uses_source_url_when_detail_url_is_missing() -> None:
    item = parse_sdn_items(SDN_XML, make_source(detail_url=None))[0]

    assert item.link == "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"


def test_parse_sdn_items_errors_on_invalid_xml() -> None:
    with pytest.raises(BulkSdnAdapterError, match="not valid"):
        parse_sdn_items(b"<sdnList>", make_source())


def test_parse_sdn_items_errors_on_missing_publish_date() -> None:
    xml = b"""
    <sdnList>
      <sdnEntry><uid>36</uid><lastName>AEROCARIBBEAN AIRLINES</lastName></sdnEntry>
    </sdnList>
    """

    with pytest.raises(BulkSdnAdapterError, match="Publish_Date"):
        parse_sdn_items(xml, make_source())


def test_parse_sdn_items_errors_on_empty_required_source() -> None:
    xml = b"""
    <sdnList>
      <publshInformation><Publish_Date>06/25/2026</Publish_Date></publshInformation>
    </sdnList>
    """

    with pytest.raises(BulkSdnAdapterError, match="returned no rows"):
        parse_sdn_items(xml, make_source())


def test_parse_sdn_items_can_accept_empty_valid_source() -> None:
    xml = b"""
    <sdnList>
      <publshInformation><Publish_Date>06/25/2026</Publish_Date></publshInformation>
    </sdnList>
    """

    assert parse_sdn_items(xml, make_source(empty_result_policy="valid")) == []


def test_parse_sdn_items_errors_on_missing_uid() -> None:
    xml = b"""
    <sdnList>
      <publshInformation><Publish_Date>06/25/2026</Publish_Date></publshInformation>
      <sdnEntry><lastName>AEROCARIBBEAN AIRLINES</lastName></sdnEntry>
    </sdnList>
    """

    with pytest.raises(BulkSdnAdapterError, match="uid"):
        parse_sdn_items(xml, make_source())


def test_parse_sdn_items_errors_on_unknown_timezone() -> None:
    with pytest.raises(BulkSdnAdapterError, match="unknown timezone"):
        parse_sdn_items(SDN_XML, make_source(params={"published_timezone": "No/SuchZone"}))
