"""Tests for the identity module — robot ID resolution chain."""
from __future__ import annotations

import pytest

from sn2_backup.identity import (
    is_meaningful_serial,
    resolve_robot_id,
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
