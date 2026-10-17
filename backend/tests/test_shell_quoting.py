"""User text sent to the phone is always passed as quoted arguments, never spliced into a shell string."""
import shlex

import pytest
from adbutils._utils import list2cmdline

from app.agent.universal_mobile_agent import UniversalMobileAgent
from app.device.adb_controller import adb_controller

EVIL = 'hi"; reboot; echo "$(id)` whoami `\'x'


def commands_containing(dev, needle):
    return [c for c in dev.commands if needle in (c if isinstance(c, str) else " ".join(c))]


def assert_single_literal_argument(dev, needle):
    """The payload reached the phone as one intact argument of a list command (adbutils shell-quotes lists)"""
    cmds = commands_containing(dev, needle)
    assert cmds, f"no command carried {needle!r}"
    for cmd in cmds:
        assert isinstance(cmd, list), f"user text spliced into a shell string: {cmd!r}"
        # What the phone's /bin/sh actually parses back out of adbutils' quoted command line
        assert shlex.split(list2cmdline(cmd)) == cmd


def test_sms_body(fake_phone):
    adb_controller.send_sms("+15550192834", EVIL)
    assert_single_literal_argument(fake_phone, "reboot")


def test_email_recipient_and_body(fake_phone):
    adb_controller.send_email(to_email="a@b.com'; reboot; '", message=EVIL, subject=EVIL)
    assert_single_literal_argument(fake_phone, "SENDTO")
    for cmd in commands_containing(fake_phone, "mailto:"):
        uri = cmd[cmd.index("-d") + 1]
        assert " " not in uri and "'" not in uri  # recipient is URL-encoded


def test_browser_url(fake_phone):
    adb_controller.open_url_in_browser("https://example.com/?q=1&x=$(reboot)")
    assert_single_literal_argument(fake_phone, "reboot")


def test_key_press_from_model_output(fake_phone):
    adb_controller.press_key("66; reboot")
    assert_single_literal_argument(fake_phone, "reboot")


def test_unknown_app_name(fake_phone):
    fake_phone.shell_output["pm list packages"] = "package:x; reboot\n"
    adb_controller.open_app("x; reboot")
    for cmd in commands_containing(fake_phone, "reboot"):
        assert isinstance(cmd, list)


async def test_youtube_query(fake_phone):
    await UniversalMobileAgent._handle_youtube("play song hello $(reboot) on youtube")
    for cmd in commands_containing(fake_phone, "youtube"):
        if "SEARCH" in str(cmd):
            assert isinstance(cmd, list)


def test_swipe_coordinates_are_integers(fake_phone):
    adb_controller.swipe(1, 2, 3, 4, 300)
    assert fake_phone.commands[-1] == ["input", "swipe", "1", "2", "3", "4", "300"]
    # Non-numeric coordinates are rejected instead of reaching the phone's shell
    before = len(fake_phone.commands)
    assert adb_controller.swipe("1; reboot", 2, 3, 4) is False
    assert len(fake_phone.commands) == before
