from __future__ import annotations

import errno
import fcntl
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO


DEFAULT_LOCK_PATH = "/tmp/feed_collector.lock"


@dataclass
class PollLock:
    path: Path
    _handle: IO[str] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> PollLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def try_acquire_poll_lock(lock_path: str | Path = DEFAULT_LOCK_PATH) -> PollLock | None:
    lock = PollLock(Path(lock_path))
    if not lock.acquire():
        return None
    return lock
