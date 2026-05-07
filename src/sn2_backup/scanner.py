"""Walk the data root and produce upload candidates.

A candidate is a file that:
  1. has been quiet (no mtime change) for at least `mtime_quiet_seconds`,
     UNLESS its relpath matches one of `always_upload_globs` (in which case
     the quiet guard is bypassed — useful for actively-appended log files).
  2. is not already recorded as uploaded with matching size + mtime in `state`.

The scanner deliberately knows nothing about Drive, email, or the runner.
It just turns the file system + state into a stream of candidates.
"""
from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .state import State


@dataclass(frozen=True)
class Candidate:
    relpath: str           # path relative to data_root, POSIX-style
    abspath: Path
    size: int
    mtime_ns: int


def _matches_any(relpath: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(relpath, p) for p in patterns)


def scan_candidates(
    *,
    data_root: Path,
    state: State,
    mtime_quiet_seconds: float,
    always_upload_globs: Sequence[str] = (),
    now: float | None = None,
) -> Iterator[Candidate]:
    """Yield candidates in a deterministic order (sorted by relpath).

    `now` is injectable for testing; defaults to wall-clock time.
    `always_upload_globs` is a list of fnmatch-style patterns (matched against
    the POSIX relative path). Matching files skip the mtime quiet check but
    still go through the (size, mtime_ns) dedup against `state`.
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

            relpath = abspath.relative_to(data_root).as_posix()
            mtime = st.st_mtime
            mtime_ns = st.st_mtime_ns
            size = st.st_size

            always_upload = _matches_any(relpath, always_upload_globs)
            if not always_upload and mtime > quiet_cutoff:
                continue  # still being written / too fresh

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
