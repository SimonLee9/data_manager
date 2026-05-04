"""Detect new [error] / [critical] lines in error_logs/ since last cycle.

Each file's byte offset is persisted in state, so we only ever read the new
tail. If a file shrinks (truncation/rotation-in-place), we reset its offset to
zero.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .state import State

_INTERESTING_TOKENS = ("[error]", "[critical]")


@dataclass(frozen=True)
class ErrorEvent:
    relpath: str
    line: str


def scan_new_errors(
    *,
    error_logs_root: Path,
    state: State,
) -> list[ErrorEvent]:
    """Read tail of each file and return [error]/[critical] lines.

    Updates `state` in-memory: offsets advance to the last fully-terminated
    line. Caller is responsible for persisting state via `state.save()`.
    """
    error_logs_root = Path(error_logs_root)
    if not error_logs_root.exists():
        return []

    events: list[ErrorEvent] = []
    for dirpath, _dirs, files in os.walk(error_logs_root):
        for name in sorted(files):
            abspath = Path(dirpath) / name
            try:
                size = abspath.stat().st_size
            except (FileNotFoundError, PermissionError):
                continue

            relpath = abspath.relative_to(error_logs_root).as_posix()
            offset = state.get_error_log_offset(relpath)

            if offset > size:
                offset = 0  # rotated-in-place / truncated

            try:
                with open(abspath, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
            except (FileNotFoundError, PermissionError):
                continue

            text = chunk.decode("utf-8", errors="replace")
            consumed = 0
            last_nl = text.rfind("\n")
            if last_nl == -1:
                # no complete line; do not advance offset
                continue
            complete = text[: last_nl + 1]
            consumed = len(complete.encode("utf-8"))

            for raw_line in complete.splitlines():
                line = raw_line.rstrip("\r")
                lower = line.lower()
                if any(tok in lower for tok in _INTERESTING_TOKENS):
                    events.append(ErrorEvent(relpath=relpath, line=line))

            state.set_error_log_offset(relpath, offset + consumed)

    return events
