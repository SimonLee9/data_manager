"""CLI entry point: `python -m sn2_backup --config <path> [--dry-run|--once]`.

The systemd service uses the default mode (one cycle, then exit).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .drive import DriveAPIError, GoogleDriveClient
from .email_notify import Notifier
from .runner import run_once
from .state import State

DEFAULT_CONFIG_PATH = Path("~/.sn2_backup/config.yaml").expanduser()
DEFAULT_STATE_PATH = Path("~/.sn2_backup/state.json").expanduser()


def _make_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sn2_backup")
    p.add_argument(
        "--config", "-c",
        type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"path to config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    p.add_argument(
        "--state",
        type=Path, default=DEFAULT_STATE_PATH,
        help=f"path to state.json (default: {DEFAULT_STATE_PATH})",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="scan only; do not upload, send email, or modify state",
    )
    p.add_argument(
        "--once", action="store_true",
        help="run a single cycle (the default; flag exists for clarity)",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="verbose logging (DEBUG level)",
    )
    return p


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = _make_argparser().parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("sn2_backup")

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("config error: %s", exc)
        return 2

    state = State.load(args.state)

    drive_client = None
    notifier = None

    if not args.dry_run:
        # Drive client (OAuth handshake / token refresh)
        try:
            drive_client = GoogleDriveClient.from_credentials(
                credentials_path=config.drive.credentials_path,
                token_path=config.drive.token_path,
            )
        except DriveAPIError as exc:
            log.error("Drive auth failed: %s", exc)
            # Best-effort: try to send an email so the operator notices.
            try:
                notifier = _build_notifier(config)
                notifier.send(
                    subject="[sn2_backup] Drive auth failed",
                    body=f"Cycle aborted: {exc}\n",
                )
            except Exception:
                log.exception("could not even email about Drive auth failure")
            return 3

        try:
            notifier = _build_notifier(config)
        except RuntimeError as exc:
            log.error("email config error: %s", exc)
            return 4

    try:
        result = run_once(
            config=config,
            state=state,
            drive_client=drive_client,
            notifier=notifier,
            dry_run=args.dry_run,
        )
    except Exception:
        log.exception("unhandled error during cycle")
        return 5

    log.info(
        "cycle done: candidates=%d uploaded=%d failed=%d new_errors=%d",
        result.candidates_count, result.uploaded, result.failed, result.new_errors,
    )
    return 0 if result.failed == 0 else 1


def _build_notifier(config) -> Notifier:
    pw = os.environ.get(config.email.app_password_env)
    if not pw:
        raise RuntimeError(
            f"environment variable {config.email.app_password_env!r} is not set; "
            "put it in /home/rainbow/.sn2_backup/env (mode 600)"
        )
    return Notifier(
        host=config.email.smtp_host,
        port=config.email.smtp_port,
        username=config.email.username,
        app_password=pw,
        to=config.email.to,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
