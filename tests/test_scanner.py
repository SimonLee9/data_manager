"""Tests for the scanner module — mtime guard + dedup against state."""
from __future__ import annotations

import os
from pathlib import Path

from sn2_backup.scanner import Candidate, scan_candidates
from sn2_backup.state import State


def _touch(path: Path, content: bytes = b"x", mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_skips_files_modified_too_recently(tmp_path: Path) -> None:
    fresh = tmp_path / "snlog" / "fresh.log"
    stable = tmp_path / "snlog" / "stable.log"
    _touch(fresh, b"hello", mtime=1_700_000_900)   # 100s old vs now=1_700_001_000
    _touch(stable, b"world", mtime=1_700_000_000)  # 1000s old

    state = State.load(tmp_path / "state.json")

    cands = list(scan_candidates(
        data_root=tmp_path,
        state=state,
        mtime_quiet_seconds=300,
        now=1_700_001_000.0,
    ))

    relpaths = [c.relpath for c in cands]
    assert "snlog/stable.log" in relpaths
    assert "snlog/fresh.log" not in relpaths


def test_skips_files_already_uploaded_with_same_size_and_mtime(tmp_path: Path) -> None:
    f = tmp_path / "bbx" / "file.csv"
    _touch(f, b"abcde", mtime=1_700_000_000)
    state = State.load(tmp_path / "state.json")
    state.record_upload(
        relpath="bbx/file.csv",
        size=5,
        mtime_ns=int(1_700_000_000 * 1e9),
        drive_file_id="x",
    )

    cands = list(scan_candidates(
        data_root=tmp_path,
        state=state,
        mtime_quiet_seconds=0,
        now=1_700_001_000.0,
    ))

    assert cands == []


def test_re_includes_file_when_mtime_changed(tmp_path: Path) -> None:
    f = tmp_path / "bbx" / "file.csv"
    _touch(f, b"abcde", mtime=1_700_000_500)
    state = State.load(tmp_path / "state.json")
    state.record_upload(
        relpath="bbx/file.csv",
        size=5,
        mtime_ns=int(1_700_000_000 * 1e9),  # older mtime recorded
        drive_file_id="x",
    )

    cands = list(scan_candidates(
        data_root=tmp_path,
        state=state,
        mtime_quiet_seconds=0,
        now=1_700_001_000.0,
    ))

    assert [c.relpath for c in cands] == ["bbx/file.csv"]


def test_walks_nested_subdirectories(tmp_path: Path) -> None:
    _touch(tmp_path / "snlog" / "fault_log" / "deep.txt", b"x", mtime=1_700_000_000)
    _touch(tmp_path / "bbx" / "event" / "snap.csv", b"x", mtime=1_700_000_000)
    state = State.load(tmp_path / "state.json")

    cands = list(scan_candidates(
        data_root=tmp_path,
        state=state,
        mtime_quiet_seconds=0,
        now=1_700_001_000.0,
    ))

    rel = sorted(c.relpath for c in cands)
    assert rel == ["bbx/event/snap.csv", "snlog/fault_log/deep.txt"]


def test_returns_candidate_with_size_mtime_and_abspath(tmp_path: Path) -> None:
    f = tmp_path / "snlog" / "x.log"
    _touch(f, b"abc", mtime=1_700_000_000)
    state = State.load(tmp_path / "state.json")

    cands = list(scan_candidates(
        data_root=tmp_path,
        state=state,
        mtime_quiet_seconds=0,
        now=1_700_001_000.0,
    ))

    assert len(cands) == 1
    c: Candidate = cands[0]
    assert c.relpath == "snlog/x.log"
    assert c.abspath == f
    assert c.size == 3
    # mtime_ns from filesystem may have slight rounding; check it's near 1.7e18
    assert abs(c.mtime_ns - int(1_700_000_000 * 1e9)) < 1_000_000


def test_ignores_state_file_in_data_root(tmp_path: Path) -> None:
    """If state.json happened to live inside data_root we wouldn't want to upload it,
    but in our design state lives elsewhere. Even so, scanner should not crash on
    extra files and only walks the data_root we point at, not the state path."""
    f = tmp_path / "snlog" / "real.log"
    _touch(f, b"x", mtime=1_700_000_000)

    state = State.load(tmp_path / "elsewhere" / "state.json")
    cands = list(scan_candidates(
        data_root=tmp_path,
        state=state,
        mtime_quiet_seconds=0,
        now=1_700_001_000.0,
    ))
    assert [c.relpath for c in cands] == ["snlog/real.log"]
