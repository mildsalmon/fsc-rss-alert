from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from typing import Sequence

from feed_collector.domain import Item
from feed_collector.ports import StatePort


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


def dedup(source_id: str, items: Sequence[Item], state: StatePort) -> list[Item]:
    new_items: list[Item] = []
    batch_seen: set[str] = set()

    for item in items:
        normalized = with_dedup_key(source_id, item)
        if normalized.item_id in batch_seen:
            continue
        batch_seen.add(normalized.item_id)
        if state.seen_contains(source_id, normalized.item_id):
            continue
        new_items.append(normalized)

    return new_items
