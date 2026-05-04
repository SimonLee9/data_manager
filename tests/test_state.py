"""Tests for the state module — atomic JSON-backed state."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sn2_backup.state import State


def test_load_returns_empty_state_when_file_missing(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = State.load(state_path)

    assert state.path == state_path
    assert state.robot_id is None
    assert state.robot_id_announced is False
    assert state.uploaded == {}
    assert state.error_log_offsets == {}
    assert state.drive_folder_cache == {}


def test_save_writes_atomically_and_load_roundtrips(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = State.load(state_path)
    state.robot_id = "S100-B-test01"
    state.robot_id_announced = True
    state.record_upload(
        relpath="bbx/file.csv",
        size=12345,
        mtime_ns=1_700_000_000_000_000_000,
        drive_file_id="abc",
    )
    state.set_error_log_offset("error_logs/err.log", 256)
    state.set_drive_folder("S100-B-test01/bbx", "FID")
    state.last_cycle = {"started_at": "2026-05-04T17:00:00+09:00", "uploaded": 1}
    state.save()

    reloaded = State.load(state_path)
    assert reloaded.robot_id == "S100-B-test01"
    assert reloaded.robot_id_announced is True
    assert reloaded.is_uploaded("bbx/file.csv", 12345, 1_700_000_000_000_000_000) is True
    assert reloaded.is_uploaded("bbx/file.csv", 99, 1_700_000_000_000_000_000) is False
    assert reloaded.error_log_offsets["error_logs/err.log"] == 256
    assert reloaded.drive_folder_cache["S100-B-test01/bbx"] == "FID"
    assert reloaded.last_cycle == {"started_at": "2026-05-04T17:00:00+09:00", "uploaded": 1}


def test_save_uses_atomic_replace_no_temp_left_behind(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = State.load(state_path)
    state.robot_id = "X"
    state.save()

    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == []
    assert state_path.exists()


def test_is_uploaded_distinguishes_size_and_mtime(tmp_path: Path) -> None:
    state = State.load(tmp_path / "state.json")
    state.record_upload("a.csv", 100, 1, "id1")

    assert state.is_uploaded("a.csv", 100, 1) is True
    assert state.is_uploaded("a.csv", 101, 1) is False  # size differs
    assert state.is_uploaded("a.csv", 100, 2) is False  # mtime differs
    assert state.is_uploaded("b.csv", 100, 1) is False  # path differs


def test_existing_file_with_unknown_keys_is_preserved_on_save(tmp_path: Path) -> None:
    """Forward compatibility: don't drop fields we don't recognize."""
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "robot_id": "Z",
                "future_field": {"a": 1},
                "uploaded": {},
                "error_log_offsets": {},
                "drive_folder_cache": {},
            }
        )
    )

    state = State.load(state_path)
    state.save()

    on_disk = json.loads(state_path.read_text())
    assert on_disk.get("future_field") == {"a": 1}


def test_corrupt_state_file_raises(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{not json")

    with pytest.raises(ValueError):
        State.load(state_path)
