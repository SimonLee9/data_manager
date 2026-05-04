"""SMTP-based event notifier for the sn2_backup tool.

Three event shapes (only sent when the corresponding event happens — never on
empty cycles):

  * `announce_robot_id`     — first cycle on a fresh state file.
  * `report_failure`        — at least one upload failed in this cycle.
  * `report_new_errors`     — `error_logs/` produced new [error]/[critical] lines.
"""
from __future__ import annotations

import smtplib
from contextlib import contextmanager
from email.message import EmailMessage
from typing import Callable, ContextManager, Iterable

from .error_watch import ErrorEvent


# ---------- pure body builders (easy to unit-test) ----------

def build_robot_id_announcement_body(robot_id: str) -> str:
    return (
        f"sn2_backup is now active on this robot.\n"
        f"All uploads will go under robot_id = {robot_id!r}.\n"
        "If this looks wrong, set `robot_id` in config.yaml and restart the timer."
    )


def build_failure_body(*, robot_id: str, failures: Iterable[tuple[str, str]]) -> str:
    lines = [
        f"sn2_backup cycle on robot {robot_id!r} encountered upload failure(s):",
        "",
    ]
    fails = list(failures)
    if not fails:
        lines.append("(no specific files reported — see journalctl -u sn2-backup)")
    else:
        for relpath, reason in fails:
            lines.append(f"  - {relpath}  ({reason})")
    lines += [
        "",
        "These files will be retried automatically on the next cycle.",
    ]
    return "\n".join(lines) + "\n"


def build_new_errors_body(*, robot_id: str, events: Iterable[ErrorEvent]) -> str:
    events = list(events)
    lines = [
        f"sn2_backup detected {len(events)} new error/critical line(s) on robot {robot_id!r}:",
        "",
    ]
    for ev in events:
        lines.append(f"[{ev.relpath}] {ev.line}")
    return "\n".join(lines) + "\n"


# ---------- SMTP transport ----------

SmtpFactory = Callable[[str, int], ContextManager[smtplib.SMTP]]


@contextmanager
def _default_smtp_factory(host: str, port: int):
    smtp = smtplib.SMTP(host, port, timeout=30)
    try:
        yield smtp
    finally:
        try:
            smtp.quit()
        except smtplib.SMTPException:
            pass


class Notifier:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        app_password: str,
        to: str,
        smtp_factory: SmtpFactory | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.app_password = app_password
        self.to = to
        self._factory = smtp_factory or _default_smtp_factory

    # ----- low-level send -----
    def send(self, *, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.username
        msg["To"] = self.to
        msg["Subject"] = subject
        msg.set_content(body)

        with self._factory(self.host, self.port) as smtp:
            smtp.starttls()
            smtp.login(self.username, self.app_password)
            smtp.send_message(msg)

    # ----- event-shaped helpers -----
    def announce_robot_id(self, robot_id: str) -> None:
        self.send(
            subject=f"[sn2_backup] robot_id={robot_id} now active",
            body=build_robot_id_announcement_body(robot_id),
        )

    def report_failure(
        self,
        *,
        robot_id: str,
        failures: Iterable[tuple[str, str]],
    ) -> None:
        body = build_failure_body(robot_id=robot_id, failures=failures)
        self.send(
            subject=f"[sn2_backup] {robot_id} upload failure",
            body=body,
        )

    def report_new_errors(
        self,
        *,
        robot_id: str,
        events: Iterable[ErrorEvent],
    ) -> None:
        events = list(events)
        body = build_new_errors_body(robot_id=robot_id, events=events)
        self.send(
            subject=f"[sn2_backup] {robot_id} {len(events)} new error line(s)",
            body=body,
        )
