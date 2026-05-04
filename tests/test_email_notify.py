"""Tests for email_notify — SMTP wrapper with event body builders."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from sn2_backup.email_notify import (
    Notifier,
    build_failure_body,
    build_new_errors_body,
    build_robot_id_announcement_body,
)
from sn2_backup.error_watch import ErrorEvent


class FakeSMTP:
    def __init__(self) -> None:
        self.starttls_called = False
        self.login_args: tuple | None = None
        self.sent: list = []

    def starttls(self) -> None:
        self.starttls_called = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, msg) -> None:  # noqa: ANN001
        self.sent.append(msg)

    def quit(self) -> None:
        pass


@pytest.fixture
def fake_factory():
    fake = FakeSMTP()

    @contextmanager
    def factory(host: str, port: int):
        factory.host = host  # type: ignore[attr-defined]
        factory.port = port  # type: ignore[attr-defined]
        try:
            yield fake
        finally:
            fake.quit()

    factory.fake = fake  # type: ignore[attr-defined]
    return factory


def test_send_uses_starttls_login_and_send(fake_factory) -> None:
    n = Notifier(
        host="smtp.gmail.com",
        port=587,
        username="me@example.com",
        app_password="pw",
        to="me@example.com",
        smtp_factory=fake_factory,
    )

    n.send(subject="hi", body="body line\n")

    fake = fake_factory.fake
    assert fake.starttls_called is True
    assert fake.login_args == ("me@example.com", "pw")
    assert len(fake.sent) == 1
    msg = fake.sent[0]
    assert msg["From"] == "me@example.com"
    assert msg["To"] == "me@example.com"
    assert msg["Subject"] == "hi"
    assert "body line" in msg.get_content()


def test_announce_robot_id_includes_id(fake_factory) -> None:
    n = Notifier(
        host="h", port=587, username="u@x", app_password="p", to="t@x",
        smtp_factory=fake_factory,
    )
    n.announce_robot_id("S100-B-test01")
    msg = fake_factory.fake.sent[0]
    body = msg.get_content()
    assert "S100-B-test01" in body
    assert "S100-B-test01" in msg["Subject"] or "S100-B-test01" in body


def test_report_failure_lists_each_failure(fake_factory) -> None:
    n = Notifier(
        host="h", port=587, username="u@x", app_password="p", to="t@x",
        smtp_factory=fake_factory,
    )
    n.report_failure(
        robot_id="bot1",
        failures=[("bbx/a.csv", "TimeoutError"), ("bbx/b.csv", "PermissionDenied")],
    )
    body = fake_factory.fake.sent[0].get_content()
    assert "bbx/a.csv" in body and "TimeoutError" in body
    assert "bbx/b.csv" in body and "PermissionDenied" in body
    assert "bot1" in body


def test_report_new_errors_lists_each_event(fake_factory) -> None:
    n = Notifier(
        host="h", port=587, username="u@x", app_password="p", to="t@x",
        smtp_factory=fake_factory,
    )
    events = [
        ErrorEvent(relpath="a.log", line="[t1] [error] one"),
        ErrorEvent(relpath="b.log", line="[t2] [critical] two"),
    ]
    n.report_new_errors(robot_id="bot1", events=events)
    body = fake_factory.fake.sent[0].get_content()
    assert "[error] one" in body
    assert "[critical] two" in body
    assert "a.log" in body and "b.log" in body


# ---------- pure body builders (no SMTP) ----------

def test_build_failure_body_handles_empty_list() -> None:
    body = build_failure_body(robot_id="bot1", failures=[])
    # Even with no failures we expect a body that mentions the robot ID.
    assert "bot1" in body


def test_build_robot_id_announcement_body_mentions_id() -> None:
    body = build_robot_id_announcement_body("X123")
    assert "X123" in body


def test_build_new_errors_body_includes_count_and_lines() -> None:
    events = [
        ErrorEvent(relpath="a.log", line="[t] [error] one"),
        ErrorEvent(relpath="a.log", line="[t] [error] two"),
    ]
    body = build_new_errors_body(robot_id="bot1", events=events)
    assert "2" in body  # count
    assert "[error] one" in body
    assert "[error] two" in body
