from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fsc_rss_alert.errors import PollError


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "seen_ids": [],
            "consecutive_failures": 0,
            "failure_alert_sent": False,
        }
    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict):
        raise PollError(f"{path} must contain a JSON object")
    seen_ids = state.get("seen_ids", [])
    if not isinstance(seen_ids, list) or not all(isinstance(item, str) for item in seen_ids):
        raise PollError(f"{path} field seen_ids must be a list of strings")
    state.setdefault("consecutive_failures", 0)
    state.setdefault("failure_alert_sent", False)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")
    tmp_path.replace(path)


def merge_seen_ids(current_ids: list[str], previous_ids: list[str], limit: int) -> list[str]:
    merged: list[str] = []
    for entry_id in current_ids + previous_ids:
        if entry_id and entry_id not in merged:
            merged.append(entry_id)
        if len(merged) >= limit:
            break
    return merged


def reset_failure_state(state: dict[str, Any]) -> None:
    state["consecutive_failures"] = 0
    state["failure_alert_sent"] = False
    state.pop("last_failure_at", None)
    state.pop("last_error", None)


def has_failure_state(state: dict[str, Any]) -> bool:
    return bool(
        state.get("consecutive_failures")
        or state.get("failure_alert_sent")
        or "last_failure_at" in state
        or "last_error" in state
    )


def record_failure_state(state: dict[str, Any], error: Exception) -> int:
    failure_count = int(state.get("consecutive_failures") or 0) + 1
    state["consecutive_failures"] = failure_count
    state["last_failure_at"] = utc_now_iso()
    state["last_error"] = str(error)[:500]
    return failure_count

