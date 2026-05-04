"""Tests for the error_watch module — new error line extraction with offsets."""
from __future__ import annotations

from pathlib import Path

from sn2_backup.error_watch import ErrorEvent, scan_new_errors
from sn2_backup.state import State


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_extracts_error_and_critical_lines_first_pass(tmp_path: Path) -> None:
    log = tmp_path / "error_logs" / "a.log"
    _write(
        log,
        "[2026-05-04 14:30:43.666] [info] [ERROR_MANAGER] init\n"
        "[2026-05-04 14:50:07.818] [error] [ERROR_MANAGER] [R0DxE001] marker not found\n"
        "[2026-05-04 14:51:00.000] [critical] [ERROR_MANAGER] system halt\n"
        "[2026-05-04 14:52:00.000] [warn] [ERROR_MANAGER] battery low\n",
    )
    state = State.load(tmp_path / "state.json")

    events = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)

    assert [e.line for e in events] == [
        "[2026-05-04 14:50:07.818] [error] [ERROR_MANAGER] [R0DxE001] marker not found",
        "[2026-05-04 14:51:00.000] [critical] [ERROR_MANAGER] system halt",
    ]
    assert events[0].relpath == "a.log"


def test_offset_advances_so_second_pass_yields_only_new(tmp_path: Path) -> None:
    log = tmp_path / "error_logs" / "a.log"
    _write(log, "[t1] [info] one\n[t2] [error] err1\n")
    state = State.load(tmp_path / "state.json")

    first = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    assert [e.line for e in first] == ["[t2] [error] err1"]

    # Append more content
    with open(log, "a") as fh:
        fh.write("[t3] [error] err2\n[t4] [info] noise\n")

    second = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    assert [e.line for e in second] == ["[t3] [error] err2"]


def test_truncated_file_resets_offset(tmp_path: Path) -> None:
    """If the file shrinks (truncation/rotation in place), reset offset to 0."""
    log = tmp_path / "error_logs" / "a.log"
    _write(log, "[t1] [error] big-old-error\n" + "x" * 1000 + "\n")
    state = State.load(tmp_path / "state.json")

    scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    assert state.get_error_log_offset("a.log") > 0

    # Simulate rotation-in-place: file now smaller, fresh content
    log.write_text("[t9] [error] post-rotate-error\n")

    events = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    assert [e.line for e in events] == ["[t9] [error] post-rotate-error"]


def test_handles_no_files(tmp_path: Path) -> None:
    state = State.load(tmp_path / "state.json")
    (tmp_path / "error_logs").mkdir()
    events = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    assert events == []


def test_skips_partial_trailing_line(tmp_path: Path) -> None:
    """If the last bit lacks a newline, leave it for the next cycle."""
    log = tmp_path / "error_logs" / "a.log"
    _write(log, "[t1] [error] complete\n[t2] [error] partial-no-nl")
    state = State.load(tmp_path / "state.json")

    events = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    assert [e.line for e in events] == ["[t1] [error] complete"]

    # Now finish the partial line
    with open(log, "a") as fh:
        fh.write("\n")
    events2 = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    assert [e.line for e in events2] == ["[t2] [error] partial-no-nl"]


def test_multiple_files_each_track_own_offset(tmp_path: Path) -> None:
    _write(tmp_path / "error_logs" / "a.log", "[t] [error] from-a\n")
    _write(tmp_path / "error_logs" / "b.log", "[t] [error] from-b\n")
    state = State.load(tmp_path / "state.json")

    events = scan_new_errors(error_logs_root=tmp_path / "error_logs", state=state)
    paths = sorted(e.relpath for e in events)
    assert paths == ["a.log", "b.log"]
