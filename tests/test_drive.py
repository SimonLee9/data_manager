"""Tests for the drive uploader (using a fake DriveClient)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sn2_backup.drive import (
    DriveAPIError,
    ensure_folder_chain,
    upload_file,
)
from sn2_backup.state import State


class FakeDrive:
    """Minimal in-memory model of Drive folders + files matching DriveClient."""

    def __init__(self) -> None:
        self.folders: dict[str, list[tuple[str, str]]] = {}  # parent_id -> [(name, child_id)]
        self.files: dict[str, list[tuple[str, str]]] = {}    # parent_id -> [(name, child_id)]
        self.contents: dict[str, bytes] = {}                  # file_id -> last uploaded bytes
        self._next = 1
        self.calls: list[tuple] = []

    def _new_id(self, prefix: str) -> str:
        i = self._next
        self._next += 1
        return f"{prefix}{i:03d}"

    def find_one(self, parent_id: str, name: str, *, folder: bool = False) -> str | None:
        self.calls.append(("find_one", parent_id, name, folder))
        bucket = self.folders if folder else self.files
        for n, fid in bucket.get(parent_id, []):
            if n == name:
                return fid
        return None

    def create_folder(self, parent_id: str, name: str) -> str:
        self.calls.append(("create_folder", parent_id, name))
        fid = self._new_id("F")
        self.folders.setdefault(parent_id, []).append((name, fid))
        return fid

    def create_file(self, parent_id: str, name: str, abspath: Path) -> str:
        self.calls.append(("create_file", parent_id, name, str(abspath)))
        fid = self._new_id("X")
        self.files.setdefault(parent_id, []).append((name, fid))
        self.contents[fid] = abspath.read_bytes()
        return fid

    def update_file(self, file_id: str, abspath: Path) -> str:
        self.calls.append(("update_file", file_id, str(abspath)))
        self.contents[file_id] = abspath.read_bytes()
        return file_id


# ---------- ensure_folder_chain ----------

def test_ensure_folder_chain_creates_missing_levels(tmp_path: Path) -> None:
    state = State.load(tmp_path / "state.json")
    client = FakeDrive()

    fid = ensure_folder_chain(
        client=client,
        root_parent_id="ROOT",
        parts=["S100-B-test01", "bbx", "event"],
        state=state,
    )

    assert fid is not None
    assert state.get_drive_folder("S100-B-test01") is not None
    assert state.get_drive_folder("S100-B-test01/bbx") is not None
    assert state.get_drive_folder("S100-B-test01/bbx/event") == fid
    create_calls = [c for c in client.calls if c[0] == "create_folder"]
    assert len(create_calls) == 3


def test_ensure_folder_chain_uses_state_cache(tmp_path: Path) -> None:
    state = State.load(tmp_path / "state.json")
    client = FakeDrive()
    ensure_folder_chain(client=client, root_parent_id="ROOT", parts=["bot", "bbx"], state=state)
    client.calls.clear()

    ensure_folder_chain(client=client, root_parent_id="ROOT", parts=["bot", "bbx"], state=state)

    # Second call should hit cache: no API calls at all.
    assert client.calls == []


def test_ensure_folder_chain_finds_existing_remote_when_cache_missing(tmp_path: Path) -> None:
    state = State.load(tmp_path / "state.json")
    client = FakeDrive()
    # Pre-existing remote folder layout (e.g., manually created)
    bot_id = client.create_folder("ROOT", "bot")
    bbx_id = client.create_folder(bot_id, "bbx")
    client.calls.clear()

    fid = ensure_folder_chain(client=client, root_parent_id="ROOT", parts=["bot", "bbx"], state=state)

    assert fid == bbx_id
    # Did not create folders, only looked them up
    assert all(c[0] != "create_folder" for c in client.calls)
    assert state.get_drive_folder("bot/bbx") == bbx_id


# ---------- upload_file ----------

def test_upload_file_creates_when_absent(tmp_path: Path) -> None:
    src = tmp_path / "data.csv"
    src.write_bytes(b"hello")
    client = FakeDrive()

    fid = upload_file(client=client, parent_id="P", name="data.csv", abspath=src)

    assert client.contents[fid] == b"hello"
    assert any(c[0] == "create_file" for c in client.calls)


def test_upload_file_updates_when_remote_with_same_name_exists(tmp_path: Path) -> None:
    src = tmp_path / "data.csv"
    src.write_bytes(b"v2")
    client = FakeDrive()
    existing = client.create_file("P", "data.csv", src)
    client.contents[existing] = b"v1"
    src.write_bytes(b"v2-final")
    client.calls.clear()

    fid = upload_file(client=client, parent_id="P", name="data.csv", abspath=src)

    assert fid == existing
    assert client.contents[existing] == b"v2-final"
    assert any(c[0] == "update_file" for c in client.calls)


def test_upload_file_propagates_drive_api_error(tmp_path: Path) -> None:
    src = tmp_path / "data.csv"
    src.write_bytes(b"x")

    class FailingDrive(FakeDrive):
        def create_file(self, parent_id: str, name: str, abspath: Path) -> str:
            raise DriveAPIError("boom")

    with pytest.raises(DriveAPIError):
        upload_file(client=FailingDrive(), parent_id="P", name="data.csv", abspath=src)
