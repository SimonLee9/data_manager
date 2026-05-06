"""Robot ID resolution.

Priority:
  1. explicit config value (`robot_id` in config.yaml)
  2. system serial via `dmidecode -s system-serial-number` (if meaningful)
  3. last 6 hex chars of the primary network interface's MAC, prefixed `robot-`
  4. hostname
  5. raise RuntimeError if nothing usable

For step 3 we deliberately avoid `uuid.getnode()` because on hosts with Docker
or other virtualization it can land on a container/bridge MAC, or fall back to
a randomly-generated multicast address. Instead we read `/sys/class/net/*` and
pick a stable physical interface, skipping known virtual ones (lo, docker*,
veth*, br-*, virbr*, tun*, tap*) and any address with the multicast bit set.
"""
from __future__ import annotations

import glob
import os
import re
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterable

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

_VIRTUAL_PREFIXES = (
    "docker", "veth", "br-", "br_", "virbr", "tun", "tap", "lxc", "vmnet",
    "vnet", "kube", "flannel", "cni", "cali", "weave", "zt", "wg", "bond",
)


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


def select_primary_mac(addrs: Iterable[tuple[str, str]]) -> str | None:
    """Pure function: pick the primary NIC's MAC from `(iface_name, mac)` pairs.

    - Skips loopback and virtual / container interfaces by name prefix.
    - Skips empty / null / multicast addresses.
    - Among survivors prefers physical wired (`eth*`/`en*`), then wireless
      (`wl*`), then anything else. Within the same priority, picks the
      alphabetically-first interface name for determinism.
    """
    candidates: list[tuple[int, str, str]] = []
    for iface, mac in addrs:
        if not iface or iface == "lo":
            continue
        if iface.startswith(_VIRTUAL_PREFIXES):
            continue
        if not mac:
            continue
        normalized = mac.strip().lower().replace("-", ":")
        if not normalized or normalized == "00:00:00:00:00:00":
            continue
        try:
            first_octet = int(normalized.split(":")[0], 16)
        except (ValueError, IndexError):
            continue
        # multicast bit (LSB of first octet) — set by uuid.getnode's random
        # fallback, never set on real unicast NICs.
        if first_octet & 0x01:
            continue

        if iface.startswith(("eth", "en")):
            priority = 1
        elif iface.startswith(("wlan", "wlx", "wl")):
            priority = 2
        else:
            priority = 3
        candidates.append((priority, iface, normalized))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def _real_get_mac() -> str | None:
    """Read every `/sys/class/net/*/address` entry and pick the primary NIC."""
    addrs: list[tuple[str, str]] = []
    for path in sorted(glob.glob("/sys/class/net/*/address")):
        iface = os.path.basename(os.path.dirname(path))
        try:
            with open(path) as fh:
                mac = fh.read().strip()
        except OSError:
            continue
        addrs.append((iface, mac))
    return select_primary_mac(addrs)


def _real_get_hostname() -> str:
    return socket.gethostname()


# ---------- host fingerprint (for announcement emails) ----------

@dataclass(frozen=True)
class NetworkInterface:
    name: str
    mac: str
    ipv4: str | None


@dataclass(frozen=True)
class HostInfo:
    hostname: str
    interfaces: list[NetworkInterface] = field(default_factory=list)


def _get_ipv4(iface: str) -> str | None:
    """Return the IPv4 address of `iface` if any, else None.

    Uses the `ip` command (universally available on Linux distros we care
    about). Failure is non-fatal — we just return None and the caller logs
    "(no ipv4)".
    """
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", iface],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)\b", out.stdout)
    return m.group(1) if m else None


def gather_host_info() -> HostInfo:
    """Collect hostname + non-virtual interface details for diagnostic emails.

    Skips loopback and virtual interfaces using the same prefix list as the
    MAC selector — the goal is to surface the network identities a human can
    use to identify this physical robot from the network.
    """
    hostname = _real_get_hostname()
    interfaces: list[NetworkInterface] = []
    for path in sorted(glob.glob("/sys/class/net/*/address")):
        iface = os.path.basename(os.path.dirname(path))
        if not iface or iface == "lo":
            continue
        if iface.startswith(_VIRTUAL_PREFIXES):
            continue
        try:
            with open(path) as fh:
                mac = fh.read().strip()
        except OSError:
            continue
        if not mac or mac == "00:00:00:00:00:00":
            continue
        interfaces.append(
            NetworkInterface(name=iface, mac=mac, ipv4=_get_ipv4(iface))
        )
    return HostInfo(hostname=hostname, interfaces=interfaces)


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
