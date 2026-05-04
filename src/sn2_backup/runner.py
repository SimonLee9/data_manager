"""One backup cycle, end-to-end.

Composition root that wires `scanner`, `error_watch`, `drive`, `email_notify`
together. The fakes used in `tests/test_runner.py` document the duck-typed
interfaces accepted here.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Protocol

from .config import Config
from .drive import DriveAPIError, ensure_folder_chain, upload_file
from .error_watch import ErrorEvent, scan_new_errors
from .identity import resolve_robot_id
from .scanner import scan_candidates
from .state import State

log = logging.getLogger("sn2_backup.runner")


@dataclass
class CycleResult:
    robot_id: str
    started_at: str
    candidates_count: int
    uploaded: int
    failed: int
    new_errors: int


class _NotifierLike(Protocol):
    def announce_robot_id(self, robot_id: str) -> None: ...
    def report_failure(self, *, robot_id: str, failures: Iterable[tuple[str, str]]) -> None: ...
    def report_new_errors(self, *, robot_id: str, events: Iterable[ErrorEvent]) -> None: ...


def run_once(
    *,
    config: Config,
    state: State,
    drive_client,            # DriveClient (duck-typed)
    notifier: _NotifierLike | None,
    dry_run: bool = False,
    now: float | None = None,
) -> CycleResult:
    started = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).isoformat()

    robot_id = resolve_robot_id(config_value=config.robot_id)
    log.info("resolved robot_id=%s (config=%r)", robot_id, config.robot_id)

    # Persist robot_id to state on first sight (helps later debugging).
    if state.robot_id != robot_id:
        state.robot_id = robot_id

    # ----- announce on first cycle -----
    if (
        config.robot_id_announce_once
        and not state.robot_id_announced
        and not dry_run
        and notifier is not None
    ):
        try:
            notifier.announce_robot_id(robot_id)
            state.robot_id_announced = True
        except Exception:  # noqa: BLE001  — logged, not raised, so backup can proceed
            log.exception("robot_id announcement email failed; will retry next cycle")

    # ----- scan candidates -----
    candidates = list(
        scan_candidates(
            data_root=config.data_root,
            state=state,
            mtime_quiet_seconds=config.scanner.mtime_quiet_seconds,
            now=now,
        )
    )
    log.info("scan: %d candidate file(s)", len(candidates))

    if dry_run:
        for c in candidates:
            log.info("[dry-run] would upload: %s (size=%d)", c.relpath, c.size)
        return CycleResult(
            robot_id=robot_id,
            started_at=started,
            candidates_count=len(candidates),
            uploaded=0,
            failed=0,
            new_errors=0,
        )

    # ----- upload -----
    uploaded = 0
    failures: list[tuple[str, str]] = []
    for c in candidates:
        rel_parent = c.relpath.rsplit("/", 1)[0] if "/" in c.relpath else ""
        path_parts = [robot_id] + ([p for p in rel_parent.split("/") if p] if rel_parent else [])
        name = c.relpath.rsplit("/", 1)[-1]
        try:
            parent_id = ensure_folder_chain(
                client=drive_client,
                root_parent_id=config.drive.parent_folder_id,
                parts=path_parts,
                state=state,
            )
            file_id = upload_file(
                client=drive_client,
                parent_id=parent_id,
                name=name,
                abspath=c.abspath,
            )
            state.record_upload(
                relpath=c.relpath,
                size=c.size,
                mtime_ns=c.mtime_ns,
                drive_file_id=file_id,
            )
            uploaded += 1
            log.info("uploaded: %s -> %s", c.relpath, file_id)
        except DriveAPIError as exc:
            log.error("upload failed: %s (%s)", c.relpath, exc)
            failures.append((c.relpath, type(exc).__name__))
        except Exception as exc:  # noqa: BLE001 — surface as failure event but don't crash cycle
            log.exception("unexpected upload error: %s", c.relpath)
            failures.append((c.relpath, type(exc).__name__))

    # ----- error_logs new event scan -----
    new_error_events = scan_new_errors(
        error_logs_root=config.data_root / "error_logs",
        state=state,
    )
    log.info("error_watch: %d new error/critical line(s)", len(new_error_events))

    # ----- send event-only emails -----
    if notifier is not None:
        if failures and config.email.on_failure:
            try:
                notifier.report_failure(robot_id=robot_id, failures=failures)
            except Exception:
                log.exception("failure email send failed")
        if new_error_events and config.email.on_new_error_log:
            try:
                notifier.report_new_errors(robot_id=robot_id, events=new_error_events)
            except Exception:
                log.exception("new-errors email send failed")

    # ----- wrap up -----
    state.last_cycle = {
        "started_at": started,
        "candidates": len(candidates),
        "uploaded": uploaded,
        "failed": len(failures),
        "new_errors": len(new_error_events),
    }
    state.save()

    return CycleResult(
        robot_id=robot_id,
        started_at=started,
        candidates_count=len(candidates),
        uploaded=uploaded,
        failed=len(failures),
        new_errors=len(new_error_events),
    )
