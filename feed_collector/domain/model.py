from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


Mechanism = Literal["rss", "html", "json_board", "datatables", "bulk"]
EmptyResultPolicy = Literal["error", "valid"]
ParamValue = str | int | float | bool | None


@dataclass(frozen=True)
class Item:
    item_id: str
    title: str
    link: str
    published: datetime | None


@dataclass(frozen=True)
class SourceConfig:
    id: str
    slug: str
    name: str
    mechanism: Mechanism
    parser_version: int
    channel_id: str | None
    interval_minutes: int
    url: str
    params: Mapping[str, ParamValue] = field(default_factory=dict)
    list_path: str | None = None
    detail_url: str | None = None
    empty_result_policy: EmptyResultPolicy = "error"
