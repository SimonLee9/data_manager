"""Load and validate the YAML config file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised on missing or invalid config."""


@dataclass
class DriveConfig:
    parent_folder_id: str
    credentials_path: Path
    token_path: Path


@dataclass
class EmailConfig:
    smtp_host: str
    smtp_port: int
    username: str
    app_password_env: str
    to: str
    on_failure: bool = True
    on_new_error_log: bool = True


@dataclass
class ScannerConfig:
    mtime_quiet_seconds: int = 300
    exclude_globs: list[str] = field(default_factory=list)
    # Files matching any of these glob patterns bypass the mtime quiet guard
    # and are uploaded as soon as their (size, mtime_ns) differs from state.
    # Useful for actively-appended log files where you want the running file
    # mirrored to Drive throughout the day, not only after it stops growing.
    always_upload_globs: list[str] = field(default_factory=list)


@dataclass
class Config:
    data_root: Path
    robot_id: str | None
    robot_id_announce_once: bool
    drive: DriveConfig
    email: EmailConfig
    scanner: ScannerConfig


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


def _require(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"missing required field '{key}' in {where}")
    return d[key]


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping in {path}")

    data_root = _expand(_require(raw, "data_root", "config root"))
    robot_id = raw.get("robot_id") or None
    if robot_id is not None:
        robot_id = str(robot_id).strip() or None
    announce = bool(raw.get("robot_id_announce_once", True))

    drive_raw = raw.get("drive")
    if not isinstance(drive_raw, dict):
        raise ConfigError("missing 'drive' section")
    drive = DriveConfig(
        parent_folder_id=str(_require(drive_raw, "parent_folder_id", "drive")),
        credentials_path=_expand(_require(drive_raw, "credentials_path", "drive")),
        token_path=_expand(_require(drive_raw, "token_path", "drive")),
    )

    email_raw = raw.get("email")
    if not isinstance(email_raw, dict):
        raise ConfigError("missing 'email' section")
    try:
        smtp_port = int(_require(email_raw, "smtp_port", "email"))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"email.smtp_port must be an integer: {exc}") from exc
    email = EmailConfig(
        smtp_host=str(_require(email_raw, "smtp_host", "email")),
        smtp_port=smtp_port,
        username=str(_require(email_raw, "username", "email")),
        app_password_env=str(_require(email_raw, "app_password_env", "email")),
        to=str(_require(email_raw, "to", "email")),
        on_failure=bool(email_raw.get("on_failure", True)),
        on_new_error_log=bool(email_raw.get("on_new_error_log", True)),
    )

    scanner_raw = raw.get("scanner") or {}
    if not isinstance(scanner_raw, dict):
        raise ConfigError("'scanner' must be a mapping if provided")
    quiet = int(scanner_raw.get("mtime_quiet_seconds", 300))
    if quiet < 0:
        raise ConfigError("scanner.mtime_quiet_seconds must be >= 0")
    excludes = scanner_raw.get("exclude_globs") or []
    if not isinstance(excludes, list) or not all(isinstance(s, str) for s in excludes):
        raise ConfigError("scanner.exclude_globs must be a list of strings")
    always = scanner_raw.get("always_upload_globs") or []
    if not isinstance(always, list) or not all(isinstance(s, str) for s in always):
        raise ConfigError("scanner.always_upload_globs must be a list of strings")
    scanner = ScannerConfig(
        mtime_quiet_seconds=quiet,
        exclude_globs=list(excludes),
        always_upload_globs=list(always),
    )

    return Config(
        data_root=data_root,
        robot_id=robot_id,
        robot_id_announce_once=announce,
        drive=drive,
        email=email,
        scanner=scanner,
    )
