"""Atomic JSON-backed state for the backup tool.

The state file is the source of truth for which files have already been
uploaded, where to resume reading each error_log, and the resolved Drive
folder IDs. It is rewritten atomically so a crash mid-cycle never leaves a
torn file on disk.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class UploadedRecord:
    size: int
    mtime_ns: int
    drive_file_id: str

    def to_json(self) -> dict[str, Any]:
        return {"size": self.size, "mtime_ns": self.mtime_ns, "drive_file_id": self.drive_file_id}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "UploadedRecord":
        return cls(size=int(d["size"]), mtime_ns=int(d["mtime_ns"]), drive_file_id=str(d["drive_file_id"]))


@dataclass
class State:
    path: Path
    robot_id: str | None = None
    robot_id_announced: bool = False
    last_cycle: dict[str, Any] = field(default_factory=dict)
    uploaded: dict[str, UploadedRecord] = field(default_factory=dict)
    error_log_offsets: dict[str, int] = field(default_factory=dict)
    drive_folder_cache: dict[str, str] = field(default_factory=dict)
    _extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "State":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupt state file at {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"state file at {path} is not a JSON object")

        known = {"schema_version", "robot_id", "robot_id_announced", "last_cycle",
                 "uploaded", "error_log_offsets", "drive_folder_cache"}
        extra = {k: v for k, v in raw.items() if k not in known}

        return cls(
            path=path,
            robot_id=raw.get("robot_id"),
            robot_id_announced=bool(raw.get("robot_id_announced", False)),
            last_cycle=dict(raw.get("last_cycle") or {}),
            uploaded={k: UploadedRecord.from_json(v) for k, v in (raw.get("uploaded") or {}).items()},
            error_log_offsets={k: int(v) for k, v in (raw.get("error_log_offsets") or {}).items()},
            drive_folder_cache={k: str(v) for k, v in (raw.get("drive_folder_cache") or {}).items()},
            _extra=extra,
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "robot_id": self.robot_id,
            "robot_id_announced": self.robot_id_announced,
            "last_cycle": self.last_cycle,
            "uploaded": {k: v.to_json() for k, v in self.uploaded.items()},
            "error_log_offsets": dict(self.error_log_offsets),
            "drive_folder_cache": dict(self.drive_folder_cache),
        }
        payload.update(self._extra)

        fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".json.tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def is_uploaded(self, relpath: str, size: int, mtime_ns: int) -> bool:
        rec = self.uploaded.get(relpath)
        if rec is None:
            return False
        return rec.size == size and rec.mtime_ns == mtime_ns

    def record_upload(self, relpath: str, size: int, mtime_ns: int, drive_file_id: str) -> None:
        self.uploaded[relpath] = UploadedRecord(size=size, mtime_ns=mtime_ns, drive_file_id=drive_file_id)

    def get_error_log_offset(self, relpath: str) -> int:
        return self.error_log_offsets.get(relpath, 0)

    def set_error_log_offset(self, relpath: str, offset: int) -> None:
        self.error_log_offsets[relpath] = int(offset)

    def get_drive_folder(self, key: str) -> str | None:
        return self.drive_folder_cache.get(key)

    def set_drive_folder(self, key: str, folder_id: str) -> None:
        self.drive_folder_cache[key] = folder_id
