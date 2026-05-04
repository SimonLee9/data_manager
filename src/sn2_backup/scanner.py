"""Walk the data root and produce upload candidates.

A candidate is a file that:
  1. has been quiet (no mtime change) for at least `mtime_quiet_seconds`, and
  2. is not already recorded as uploaded with matching size + mtime in `state`.

The scanner deliberately knows nothing about Drive, email, or the runner.
It just turns the file system + state into a stream of candidates.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .state import State


@dataclass(frozen=True)
class Candidate:
    relpath: str           # path relative to data_root, POSIX-style
    abspath: Path
    size: int
    mtime_ns: int


def scan_candidates(
    *,
    data_root: Path,
    state: State,
    mtime_quiet_seconds: float,
    now: float | None = None,
) -> Iterator[Candidate]:
    """Yield candidates in a deterministic order (sorted by relpath).

    `now` is injectable for testing; defaults to wall-clock time.
    """
    data_root = Path(data_root)
    if now is None:
        now = time.time()

    if not data_root.exists():
        return

    quiet_cutoff = now - mtime_quiet_seconds

    candidates: list[Candidate] = []
    for dirpath, _dirs, files in os.walk(data_root):
        for name in files:
            abspath = Path(dirpath) / name
            try:
                st = abspath.stat()
            except (FileNotFoundError, PermissionError):
                continue
            if not _is_regular(st.st_mode):
                continue
            mtime = st.st_mtime
            if mtime > quiet_cutoff:
                continue  # still being written / too fresh

            relpath = abspath.relative_to(data_root).as_posix()
            mtime_ns = st.st_mtime_ns
            size = st.st_size

            if state.is_uploaded(relpath, size, mtime_ns):
                continue

            candidates.append(
                Candidate(relpath=relpath, abspath=abspath, size=size, mtime_ns=mtime_ns)
            )

    candidates.sort(key=lambda c: c.relpath)
    yield from candidates


def _is_regular(mode: int) -> bool:
    import stat
    return stat.S_ISREG(mode)


# kept for type-hint friendliness with `Iterable`
_ = Iterable
