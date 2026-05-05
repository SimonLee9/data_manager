"""Tests for the identity module — robot ID resolution chain."""
from __future__ import annotations

import pytest

from sn2_backup.identity import (
    is_meaningful_serial,
    resolve_robot_id,
    select_primary_mac,
)


def test_uses_config_value_when_provided() -> None:
    rid = resolve_robot_id(
        config_value="S100-B-test01",
        get_serial=lambda: "ANY",
        get_mac=lambda: "aabbccddeeff",
        get_hostname=lambda: "host",
    )
    assert rid == "S100-B-test01"


def test_strips_whitespace_in_config_value() -> None:
    rid = resolve_robot_id(
        config_value="  bot-1  ",
        get_serial=lambda: None,
        get_mac=lambda: None,
        get_hostname=lambda: "host",
    )
    assert rid == "bot-1"


def test_falls_back_to_serial_when_config_empty() -> None:
    rid = resolve_robot_id(
        config_value=None,
        get_serial=lambda: "AB1234567",
        get_mac=lambda: "aabbccddeeff",
        get_hostname=lambda: "host",
    )
    assert rid == "AB1234567"


def test_skips_garbage_serials() -> None:
    rid = resolve_robot_id(
        config_value=None,
        get_serial=lambda: "Default String",
        get_mac=lambda: "aabbccddeeff",
        get_hostname=lambda: "host",
    )
    assert rid == "robot-ddeeff"


def test_falls_back_to_mac_when_no_serial(tmp_path) -> None:
    rid = resolve_robot_id(
        config_value=None,
        get_serial=lambda: None,
        get_mac=lambda: "aa:bb:cc:dd:ee:ff",
        get_hostname=lambda: "host",
    )
    assert rid == "robot-ddeeff"


def test_falls_back_to_hostname_when_no_mac() -> None:
    rid = resolve_robot_id(
        config_value=None,
        get_serial=lambda: None,
        get_mac=lambda: None,
        get_hostname=lambda: "rainbow-ODROID-H4",
    )
    assert rid == "rainbow-ODROID-H4"


def test_raises_when_everything_unknown() -> None:
    with pytest.raises(RuntimeError):
        resolve_robot_id(
            config_value=None,
            get_serial=lambda: None,
            get_mac=lambda: None,
            get_hostname=lambda: "",
        )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "Default String",
        "default string",
        "To be filled by O.E.M.",
        "To Be Filled By O.E.M.",
        "Not Specified",
        "None",
        "System Serial Number",
        "00000000",
    ],
)
def test_garbage_serial_values_are_filtered(value: str) -> None:
    assert is_meaningful_serial(value) is False


@pytest.mark.parametrize("value", ["AB1234567", "RBT-001", "S100-B-r3"])
def test_real_serial_values_pass_filter(value: str) -> None:
    assert is_meaningful_serial(value) is True


# ----- select_primary_mac -----

def test_select_primary_mac_picks_wired_over_wireless() -> None:
    addrs = [
        ("wlxa047d7601e3b", "a0:47:d7:60:1e:3b"),
        ("enp2s0",          "00:1e:06:45:97:69"),
    ]
    assert select_primary_mac(addrs) == "00:1e:06:45:97:69"


def test_select_primary_mac_alphabetical_tiebreak_within_priority() -> None:
    addrs = [
        ("enp2s0", "00:1e:06:45:97:69"),
        ("enp1s0", "00:1e:06:45:97:68"),
    ]
    # Both wired → alphabetically-first iface name wins (deterministic)
    assert select_primary_mac(addrs) == "00:1e:06:45:97:68"


def test_select_primary_mac_skips_loopback_docker_veth_bridge() -> None:
    """Reproduces the user's real ODROID-H4 ifconfig output."""
    addrs = [
        ("br-22d16afa122c", "72:6f:72:b1:e6:8d"),  # docker bridge → skip
        ("docker0",         "7a:f5:72:4c:67:8d"),  # docker → skip
        ("enp1s0",          "00:1e:06:45:97:68"),  # physical wired down ✓
        ("enp2s0",          "00:1e:06:45:97:69"),  # physical wired up   ✓
        ("lo",              "00:00:00:00:00:00"),  # loopback → skip
        ("veth3edfbf0",     "de:62:bb:f2:14:fa"),  # veth → skip
        ("wlxa047d7601e3b", "a0:47:d7:60:1e:3b"),  # wireless ✓ (lower priority)
    ]
    assert select_primary_mac(addrs) == "00:1e:06:45:97:68"


def test_select_primary_mac_falls_back_to_wireless_when_no_wired() -> None:
    addrs = [
        ("docker0",         "02:42:ac:11:00:01"),
        ("wlan0",           "a0:47:d7:60:1e:3b"),
    ]
    assert select_primary_mac(addrs) == "a0:47:d7:60:1e:3b"


def test_select_primary_mac_returns_none_when_only_virtual() -> None:
    addrs = [
        ("docker0",     "02:42:ac:11:00:01"),
        ("br-abc",      "72:6f:72:b1:e6:8d"),
        ("veth0",       "de:62:bb:f2:14:fa"),
        ("lo",          "00:00:00:00:00:00"),
    ]
    assert select_primary_mac(addrs) is None


def test_select_primary_mac_skips_multicast_random_macs() -> None:
    addrs = [
        # multicast bit set → looks like uuid.getnode random fallback
        ("eth0",  "01:23:45:67:89:ab"),
        ("eth1",  "00:1e:06:45:97:68"),
    ]
    assert select_primary_mac(addrs) == "00:1e:06:45:97:68"


def test_select_primary_mac_handles_dash_separator() -> None:
    addrs = [("eth0", "00-1e-06-45-97-68")]
    assert select_primary_mac(addrs) == "00:1e:06:45:97:68"


def test_select_primary_mac_handles_uppercase() -> None:
    addrs = [("eth0", "00:1E:06:45:97:68")]
    assert select_primary_mac(addrs) == "00:1e:06:45:97:68"


def test_select_primary_mac_skips_zero_address() -> None:
    addrs = [
        ("eth0", "00:00:00:00:00:00"),
        ("eth1", "00:1e:06:45:97:68"),
    ]
    assert select_primary_mac(addrs) == "00:1e:06:45:97:68"


def test_select_primary_mac_skips_malformed() -> None:
    addrs = [
        ("eth0", "garbage"),
        ("eth1", "00:1e:06:45:97:68"),
    ]
    assert select_primary_mac(addrs) == "00:1e:06:45:97:68"


def test_select_primary_mac_empty_list() -> None:
    assert select_primary_mac([]) is None


def test_resolve_robot_id_uses_new_mac_format() -> None:
    """resolve_robot_id consumes the colon-formatted MAC and produces robot-XXXXXX."""
    rid = resolve_robot_id(
        config_value=None,
        get_serial=lambda: None,
        get_mac=lambda: "00:1e:06:45:97:68",
        get_hostname=lambda: "host",
    )
    assert rid == "robot-459768"
