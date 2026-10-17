"""The suite itself must never reach a real phone (a connected phone would get real calls/messages)."""
import adbutils
import pytest

from app.device.adb_controller import adb_controller


def test_no_phone_visible_to_tests():
    assert adb_controller.is_device_connected is False
    assert adb_controller.get_connected_device() is None


def test_real_adb_server_access_is_blocked():
    with pytest.raises(RuntimeError, match="never talk to a real ADB server"):
        adbutils.AdbClient().device_list()


def test_legacy_live_scripts_are_not_collected(request):
    collected = {item.path.name for item in request.session.items}
    assert "test_full_agent_flow.py" not in collected
    assert "test_whatsapp_variations.py" not in collected
