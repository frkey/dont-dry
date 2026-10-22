"""Small JSON files in STATE_DIR: the scheduler's heartbeat and the last print's result.

They are replaced atomically, so the healthcheck never reads a half-written heartbeat.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HEARTBEAT = "heartbeat"
LAST_RUN = "last-run.json"

_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY
_warned: set[str] = set()  # warn once per file, not on every heartbeat


def write(state_dir: str, name: str, payload: dict[str, Any]) -> None:
    """Replaces ``state_dir/name`` with ``payload`` as JSON. Never raises: state is advisory."""
    folder = Path(state_dir)
    path = folder / name
    tmp = folder / f".{name}.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)  # left behind by a crash; O_EXCL would refuse it
        # O_EXCL creates a new file and never follows a symlink planted in its place
        with os.fdopen(os.open(tmp, _FLAGS, 0o644), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        if name not in _warned:
            _warned.add(name)
            log.warning("cannot write %s (set STATE_DIR to a writable folder): %s", path, exc)
        return
    _warned.discard(name)


def read(state_dir: str, name: str) -> dict[str, Any] | None:
    """The file's JSON object, or None when it is missing or unreadable."""
    try:
        data = json.loads((Path(state_dir) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
