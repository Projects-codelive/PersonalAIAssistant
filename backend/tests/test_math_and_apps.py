"""Spoken math parsing and app-name → installed package resolution across phone brands."""
import pytest

from app.agent.tools.screen_tools import open_app
from app.agent.universal_mobile_agent import UniversalMobileAgent
from app.device.adb_controller import adb_controller


@pytest.mark.parametrize("text, expected", [
    ("25 multiplied by 4", ("25 * 4", 100.0)),
    ("6 times 7", ("6 * 7", 42.0)),
    ("10 divided by 2", ("10 / 2", 5.0)),
    ("8 plus 2", ("8 + 2", 10.0)),
    ("9 minus 3", ("9 - 3", 6.0)),
    ("addition of 2 and 3", ("2 + 3", 5.0)),
    ("subtract 5 from 10", ("10 - 5", 5.0)),
    ("25 * 4", ("25 * 4", 100.0)),
    ("2 aur 3 ko jodo", ("2 + 3", 5.0)),
    ("7 divided by 0", ("7 / 0", 0.0)),
])
def test_parse_math_expression(text, expected):
    assert UniversalMobileAgent.parse_math_expression(text) == expected


@pytest.mark.parametrize("text", ["call mom", "send 2 messages to alice", "open whatsapp", "hi agent"])
def test_non_math_is_not_parsed(text):
    assert UniversalMobileAgent.parse_math_expression(text) is None


PIXEL = "package:com.google.android.calculator\npackage:com.google.android.deskclock\npackage:com.whatsapp\n"
SAMSUNG = "package:com.sec.android.app.popupcalculator\npackage:com.sec.android.app.clockpackage\n"


@pytest.mark.parametrize("installed, name, expected", [
    (SAMSUNG, "calculator", "com.sec.android.app.popupcalculator"),  # mapped package installed
    (PIXEL, "calculator", "com.google.android.calculator"),          # Samsung map → Pixel equivalent
    (PIXEL, "clock", "com.google.android.deskclock"),
    (PIXEL, "WhatsApp", "com.whatsapp"),
    ("package:org.example.superweather\n", "superweather", "org.example.superweather"),  # fuzzy fallback
])
def test_package_resolution(fake_phone, installed, name, expected):
    fake_phone.shell_output["pm list packages"] = installed
    assert adb_controller.get_package_for_app_name(name) == expected


def test_open_app_reports_missing_app(fake_phone):
    fake_phone.shell_output["pm list packages"] = PIXEL
    assert adb_controller.open_app("instagram") is False
    assert not any(c == ["app_start", "com.instagram.android"] for c in fake_phone.commands)


async def test_open_app_tool_does_not_claim_success_for_missing_app(fake_phone):
    fake_phone.shell_output["pm list packages"] = PIXEL
    result = await open_app.ainvoke({"app_name": "instagram"})
    assert result.startswith("Error")


def test_open_app_launches_resolved_package(fake_phone):
    fake_phone.shell_output["pm list packages"] = PIXEL
    assert adb_controller.open_app("calculator") is True
    assert ["app_start", "com.google.android.calculator"] in fake_phone.commands
