from __future__ import annotations

import os
from pathlib import Path

from fsc_rss_alert.errors import PollError


FEED_URL = "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111"
DEFAULT_STATE_FILE = Path("state.json")
DEFAULT_SEEN_LIMIT = 50
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_FETCH_RETRIES = 3
DEFAULT_FETCH_RETRY_DELAY_SECONDS = 10.0
DEFAULT_NOTIFY_THROTTLE_SECONDS = 1.0
USER_AGENT = "fsc-rss-alert/0.1 (+https://github.com/actions)"


def int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise PollError(f"{name} must be an integer") from exc
    if value < 1:
        raise PollError(f"{name} must be at least 1")
    return value


def float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise PollError(f"{name} must be a number") from exc
    if value < 0:
        raise PollError(f"{name} must be at least 0")
    return value
