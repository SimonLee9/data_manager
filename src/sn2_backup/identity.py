"""Robot ID resolution.

Priority:
  1. explicit config value (`robot_id` in config.yaml)
  2. system serial via `dmidecode -s system-serial-number` (if meaningful)
  3. last 6 hex chars of the primary network interface's MAC, prefixed `robot-`
  4. hostname
  5. raise RuntimeError if nothing usable
"""
from __future__ import annotations

import socket
import subprocess
import uuid
from typing import Callable

# Common BIOS/board placeholders that look like a serial but aren't.
_GARBAGE = {
    "",
    "default string",
    "to be filled by o.e.m.",
    "not specified",
    "not applicable",
    "none",
    "system serial number",
    "0",
    "00000000",
    "n/a",
    "unknown",
}


def is_meaningful_serial(value: str) -> bool:
    if value is None:
        return False
    v = value.strip().lower()
    if v in _GARBAGE:
        return False
    if all(c == "0" for c in v):
        return False
    return True


def _real_get_serial() -> str | None:
    try:
        out = subprocess.run(
            ["dmidecode", "-s", "system-serial-number"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _real_get_mac() -> str | None:
    try:
        node = uuid.getnode()
    except Exception:
        return None
    if not node:
        return None
    # uuid.getnode synthesizes a random multicast bit if it can't read a NIC.
    # Bit 0x010000000000 (universal/local) being set means it's randomly generated.
    if node & 0x010000000000:
        return None
    return f"{node:012x}"


def _real_get_hostname() -> str:
    return socket.gethostname()


def resolve_robot_id(
    *,
    config_value: str | None,
    get_serial: Callable[[], str | None] = _real_get_serial,
    get_mac: Callable[[], str | None] = _real_get_mac,
    get_hostname: Callable[[], str] = _real_get_hostname,
) -> str:
    """Return the resolved robot identifier as a non-empty string."""
    if config_value and config_value.strip():
        return config_value.strip()

    serial = get_serial()
    if serial and is_meaningful_serial(serial):
        return serial.strip()

    mac = get_mac()
    if mac:
        cleaned = mac.replace(":", "").replace("-", "").lower()
        if len(cleaned) >= 6:
            return f"robot-{cleaned[-6:]}"

    hostname = get_hostname()
    if hostname and hostname.strip():
        return hostname.strip()

    raise RuntimeError(
        "could not determine robot_id: config empty, no serial, no MAC, no hostname"
    )
