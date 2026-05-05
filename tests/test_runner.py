"""Integration test for run_once with fake Drive + fake Notifier."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import pytest

from sn2_backup.config import Config, DriveConfig, EmailConfig, ScannerConfig
from sn2_backup.error_watch import ErrorEvent
from sn2_backup.runner import run_once
from sn2_backup.state import State


# ----- fakes -----

class FakeDrive:
    def __init__(self) -> None:
        self.folders: dict[str, list[tuple[str, str]]] = {}
        self.files: dict[str, list[tuple[str, str]]] = {}
        self.contents: dict[str, bytes] = {}
        self._next = 1
        self.fail_for: set[str] = set()  # names that should fail on create_file

    def _new_id(self, prefix: str) -> str:
        i = self._next
        self._next += 1
        return f"{prefix}{i:03d}"

    def find_one(self, parent_id: str, name: str, *, folder: bool = False):
        bucket = self.folders if folder else self.files
        for n, fid in bucket.get(parent_id, []):
            if n == name:
                return fid
        return None

    def create_folder(self, parent_id: str, name: str) -> str:
        fid = self._new_id("F")
        self.folders.setdefault(parent_id, []).append((name, fid))
        return fid

    def create_file(self, parent_id: str, name: str, abspath: Path) -> str:
        if name in self.fail_for:
            from sn2_backup.drive import DriveAPIError
            raise DriveAPIError("simulated failure")
        fid = self._new_id("X")
        self.files.setdefault(parent_id, []).append((name, fid))
        self.contents[fid] = abspath.read_bytes()
        return fid

    def update_file(self, file_id: str, abspath: Path) -> str:
        self.contents[file_id] = abspath.read_bytes()
        return file_id


class FakeNotifier:
    def __init__(self) -> None:
        self.announce_calls: list[str] = []
        self.failure_calls: list[tuple[str, list[tuple[str, str]]]] = []
        self.new_error_calls: list[tuple[str, list[ErrorEvent]]] = []

    def announce_robot_id(self, robot_id: str) -> None:
        self.announce_calls.append(robot_id)

    def report_failure(self, *, robot_id: str, failures: Iterable[tuple[str, str]]) -> None:
        self.failure_calls.append((robot_id, list(failures)))

    def report_new_errors(self, *, robot_id: str, events: Iterable[ErrorEvent]) -> None:
        self.new_error_calls.append((robot_id, list(events)))


# ----- fixture: realistic data_root -----

STABLE_S = 1_000_000_000
STABLE_NS = STABLE_S * 1_000_000_000
FRESH_S = 5_000_000_000
FRESH_NS = FRESH_S * 1_000_000_000


def _set_mtime(p: Path, ns: int) -> None:
    os.utime(p, ns=(ns, ns))


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """Build a realistic sn2_log mirror with mixed fresh/stable files."""
    root = tmp_path / "sn2_log"
    # stable files (mtime old)
    f1 = root / "bbx" / "old.csv"
    f1.parent.mkdir(parents=True)
    f1.write_bytes(b"a")
    _set_mtime(f1, STABLE_NS)

    f2 = root / "snlog" / "fault_log" / "f.txt"
    f2.parent.mkdir(parents=True)
    f2.write_bytes(b"bb")
    _set_mtime(f2, STABLE_NS)

    f3 = root / "error_logs" / "e.log"
    f3.parent.mkdir(parents=True)
    f3.write_text("[t1] [info] init\n[t2] [error] BOOM\n")
    _set_mtime(f3, STABLE_NS)

    # fresh file (mtime now-ish) — should NOT be uploaded
    fresh = root / "bbx" / "fresh.csv"
    fresh.write_bytes(b"f")
    _set_mtime(fresh, FRESH_NS)

    return root


def _make_config(data_root: Path) -> Config:
    return Config(
        data_root=data_root,
        robot_id="bot1",
        robot_id_announce_once=True,
        drive=DriveConfig(
            parent_folder_id="ROOT",
            credentials_path=Path("/dev/null"),
            token_path=Path("/dev/null"),
        ),
        email=EmailConfig(
            smtp_host="x", smtp_port=587, username="u@x",
            app_password_env="APP_PW", to="t@x",
            on_failure=True, on_new_error_log=True,
        ),
        scanner=ScannerConfig(mtime_quiet_seconds=300, exclude_globs=[]),
    )


# ----- tests -----

def test_first_cycle_announces_uploads_stable_files_and_reports_errors(
    tmp_path: Path, data_root: Path
) -> None:
    state = State.load(tmp_path / "state.json")
    drive = FakeDrive()
    notifier = FakeNotifier()
    cfg = _make_config(data_root)

    result = run_once(
        config=cfg,
        state=state,
        drive_client=drive,
        notifier=notifier,
        now=float(STABLE_S + 1_000_000),  # well past stable mtime quiet cutoff
    )

    # stable files uploaded, fresh skipped
    uploaded_names = {n for files in drive.files.values() for (n, _) in files}
    assert "old.csv" in uploaded_names
    assert "f.txt" in uploaded_names
    assert "e.log" in uploaded_names
    assert "fresh.csv" not in uploaded_names

    # announcement happened on first cycle
    assert notifier.announce_calls == ["bot1"]

    # error_logs produced an event email
    assert len(notifier.new_error_calls) == 1
    rid, events = notifier.new_error_calls[0]
    assert rid == "bot1"
    assert any("BOOM" in e.line for e in events)

    # no failures
    assert notifier.failure_calls == []

    # state recorded
    assert state.robot_id_announced is True
    assert state.is_uploaded("bbx/old.csv", 1, STABLE_NS)
    assert result.uploaded == 3
    assert result.failed == 0
    assert result.new_errors >= 1


def test_second_cycle_is_idempotent_and_silent_when_nothing_new(
    tmp_path: Path, data_root: Path
) -> None:
    state = State.load(tmp_path / "state.json")
    drive = FakeDrive()
    notifier = FakeNotifier()
    cfg = _make_config(data_root)

    run_once(config=cfg, state=state, drive_client=drive, notifier=notifier, now=float(STABLE_S + 1_000_000))
    notifier_calls_before = (
        len(notifier.announce_calls),
        len(notifier.failure_calls),
        len(notifier.new_error_calls),
    )
    drive_files_before = {n for files in drive.files.values() for (n, _) in files}

    # second cycle, nothing changed
    notifier2 = FakeNotifier()
    result = run_once(config=cfg, state=state, drive_client=drive, notifier=notifier2, now=float(STABLE_S + 1_000_000))

    drive_files_after = {n for files in drive.files.values() for (n, _) in files}
    assert drive_files_after == drive_files_before  # no new uploads
    assert result.uploaded == 0
    assert result.failed == 0
    assert result.new_errors == 0
    assert notifier2.announce_calls == []  # not announced again
    assert notifier2.failure_calls == []
    assert notifier2.new_error_calls == []
    # ensure first cycle did fire announcements at least once
    assert notifier_calls_before[0] == 1


def test_failure_is_reported_and_failed_file_retried_next_cycle(
    tmp_path: Path, data_root: Path
) -> None:
    state = State.load(tmp_path / "state.json")
    drive = FakeDrive()
    drive.fail_for = {"old.csv"}
    notifier = FakeNotifier()
    cfg = _make_config(data_root)

    run_once(config=cfg, state=state, drive_client=drive, notifier=notifier, now=float(STABLE_S + 1_000_000))

    # failure email fired
    assert len(notifier.failure_calls) == 1
    rid, fails = notifier.failure_calls[0]
    assert rid == "bot1"
    assert any("old.csv" in f[0] for f in fails)

    # state did NOT record old.csv as uploaded
    assert state.is_uploaded("bbx/old.csv", 1, STABLE_NS) is False

    # next cycle: succeed this time
    drive.fail_for = set()
    notifier2 = FakeNotifier()
    run_once(config=cfg, state=state, drive_client=drive, notifier=notifier2, now=float(STABLE_S + 1_000_000))
    assert state.is_uploaded("bbx/old.csv", 1, STABLE_NS) is True


def test_robot_id_change_re_announces_under_new_id(
    tmp_path: Path, data_root: Path
) -> None:
    """If the operator edits config.robot_id later, the new id is re-announced."""
    state = State.load(tmp_path / "state.json")
    drive = FakeDrive()
    notifier1 = FakeNotifier()
    cfg1 = _make_config(data_root)  # robot_id="bot1"

    run_once(config=cfg1, state=state, drive_client=drive, notifier=notifier1,
             now=float(STABLE_S + 1_000_000))
    assert notifier1.announce_calls == ["bot1"]
    assert state.robot_id == "bot1"

    # Operator changes the robot_id (e.g., via config edit) and reruns.
    cfg2 = replace(cfg1, robot_id="bot2")
    notifier2 = FakeNotifier()
    run_once(config=cfg2, state=state, drive_client=drive, notifier=notifier2,
             now=float(STABLE_S + 1_000_000))

    assert notifier2.announce_calls == ["bot2"]
    assert state.robot_id == "bot2"


def test_dry_run_does_no_uploads_no_email_no_state_changes(
    tmp_path: Path, data_root: Path
) -> None:
    state_path = tmp_path / "state.json"
    state = State.load(state_path)
    drive = FakeDrive()
    notifier = FakeNotifier()
    cfg = _make_config(data_root)

    result = run_once(
        config=cfg,
        state=state,
        drive_client=drive,
        notifier=notifier,
        dry_run=True,
        now=float(STABLE_S + 1_000_000),
    )

    assert drive.files == {}
    assert notifier.announce_calls == []
    assert notifier.failure_calls == []
    assert notifier.new_error_calls == []
    assert not state_path.exists()  # state not written in dry-run
    assert result.uploaded == 0
    assert result.failed == 0
    # but candidates *would have been* listed
    assert result.candidates_count >= 3
