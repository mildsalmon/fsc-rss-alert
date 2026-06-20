from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from typing import Sequence

from feed_collector.domain import Item


def content_hash_item_id(source_id: str, title: str, body: str, published: datetime | None) -> str:
    published_text = published.isoformat() if published else ""
    payload = "\0".join([title.strip(), body.strip(), published_text])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"content://{source_id}/{digest}"


def item_dedup_key(source_id: str, item: Item) -> str:
    stable_key = item.item_id.strip()
    if stable_key:
        return stable_key
    return content_hash_item_id(source_id, item.title, item.link, item.published)


def with_dedup_key(source_id: str, item: Item) -> Item:
    dedup_key = item_dedup_key(source_id, item)
    if item.item_id == dedup_key:
        return item
    return replace(item, item_id=dedup_key)


def unique_items(source_id: str, items: Sequence[Item]) -> list[Item]:
    unique: list[Item] = []
    seen: set[str] = set()

    for item in items:
        normalized = with_dedup_key(source_id, item)
        if normalized.item_id in seen:
            continue
        seen.add(normalized.item_id)
        unique.append(normalized)

    return unique
