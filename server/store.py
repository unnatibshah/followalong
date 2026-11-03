"""Files under FA_DATA_DIR: JSON updated under a file lock (two sessions may write at once), the visitor hash, ET days."""

import contextlib
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

try:
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX)
except ImportError:  # Windows
    import msvcrt

    def _lock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


@contextlib.contextmanager
def locked_json(path: Path, default):
    """Yield the file's data under an exclusive lock; whatever it holds on exit is written back atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+") as lock:  # released when the file closes
        _lock(lock)
        data = read_json(path, default)
        yield data
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)


def vhash(cookie: str) -> str:
    """What we keep instead of the `fa_visitor` cookie (data dictionary: owner_visitor, visitors.json)."""
    return hashlib.sha256(cookie.encode()).hexdigest()[:32]


def et_now() -> datetime:
    return datetime.now(ET)


def secs_to_et_midnight(now: datetime) -> int:
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))
