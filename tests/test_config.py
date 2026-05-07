"""Tests for config loading and validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from sn2_backup.config import Config, ConfigError, load_config


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


_VALID = """\
data_root: /home/rainbow/data/sn2_log
robot_id: S100-B-test01

drive:
  parent_folder_id: FOLDERID
  credentials_path: ~/.sn2_backup/credentials.json
  token_path: ~/.sn2_backup/token.json

email:
  smtp_host: smtp.gmail.com
  smtp_port: 587
  username: a@b.com
  app_password_env: APP_PW
  to: a@b.com
"""


def test_load_valid_config(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    _write(p, _VALID)

    cfg = load_config(p)

    assert isinstance(cfg, Config)
    assert cfg.data_root == Path("/home/rainbow/data/sn2_log")
    assert cfg.robot_id == "S100-B-test01"
    assert cfg.robot_id_announce_once is True  # default
    assert cfg.drive.parent_folder_id == "FOLDERID"
    assert cfg.email.smtp_host == "smtp.gmail.com"
    assert cfg.email.smtp_port == 587
    assert cfg.email.on_failure is True   # default
    assert cfg.email.on_new_error_log is True  # default
    assert cfg.scanner.mtime_quiet_seconds == 300  # default
    assert cfg.scanner.exclude_globs == []


def test_robot_id_can_be_omitted(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    _write(p, _VALID.replace("robot_id: S100-B-test01\n", ""))
    cfg = load_config(p)
    assert cfg.robot_id is None


def test_expands_user_in_credential_paths(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    _write(p, _VALID)
    cfg = load_config(p)
    assert "~" not in str(cfg.drive.credentials_path)
    assert "~" not in str(cfg.drive.token_path)


def test_missing_required_section_raises(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    _write(p, "data_root: /tmp\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_required_field_in_drive_raises(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    _write(
        p,
        """\
data_root: /tmp
drive:
  credentials_path: /a
  token_path: /b
email:
  smtp_host: smtp.gmail.com
  smtp_port: 587
  username: a@b.com
  app_password_env: APP_PW
  to: a@b.com
""",
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_smtp_port_must_be_int(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    bad = _VALID.replace("smtp_port: 587", "smtp_port: notanint")
    _write(p, bad)
    with pytest.raises(ConfigError):
        load_config(p)


def test_negative_mtime_quiet_seconds_rejected(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    body = _VALID + "\nscanner:\n  mtime_quiet_seconds: -5\n"
    _write(p, body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_always_upload_globs_loaded(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    body = _VALID + (
        "\nscanner:\n"
        "  mtime_quiet_seconds: 300\n"
        "  always_upload_globs:\n"
        "    - 'snlog/snlog_*.log'\n"
        "    - 'snlog/*-log-list.html'\n"
    )
    _write(p, body)
    cfg = load_config(p)
    assert cfg.scanner.always_upload_globs == [
        "snlog/snlog_*.log",
        "snlog/*-log-list.html",
    ]


def test_always_upload_globs_must_be_list_of_strings(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    body = _VALID + "\nscanner:\n  always_upload_globs: 'not-a-list'\n"
    _write(p, body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")
