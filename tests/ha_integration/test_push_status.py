"""Pushed state changes — a real panel only pushes after EnableNotifications.

Found on an Omni IIe (fw 3.2r2): with notifications enabled the panel
sends an unsolicited ExtendedStatus (opcode 59) per changed object, not
a SystemEvents (opcode 55) message.
"""

from __future__ import annotations

import pytest
from custom_components.omni_pca.const import DOMAIN
from homeassistant.core import HomeAssistant

from omni_pca.connection import OmniConnection
from omni_pca.message import encode_v2
from omni_pca.opcodes import OmniLink2MessageType

FRONT_DOOR = "binary_sensor.omni_pro_ii_front_door"
LIVING_LAMP = "light.omni_pro_ii_living_lamp"


@pytest.fixture
def sent_requests(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, bytes]]:
    """Record every request the integration sends, from the first connect."""
    sent: list[tuple[int, bytes]] = []
    real = OmniConnection.request

    async def spy(self, opcode, payload=b"", timeout=None):
        sent.append((int(opcode), bytes(payload)))
        return await real(self, opcode, payload, timeout)

    monkeypatch.setattr(OmniConnection, "request", spy)
    return sent


def _extended_status(object_type: int, *records: bytes) -> object:
    body = bytes([object_type, len(records[0])]) + b"".join(records)
    return encode_v2(OmniLink2MessageType.ExtendedStatus, body)


async def test_notifications_are_enabled_right_after_connecting(
    hass: HomeAssistant, sent_requests, configured_panel
) -> None:
    enable = (int(OmniLink2MessageType.EnableNotifications), b"\x01")

    assert enable in sent_requests
    # Before any status poll, so nothing that changes in between is missed.
    first_poll = next(
        i
        for i, (opcode, _) in enumerate(sent_requests)
        if opcode == int(OmniLink2MessageType.RequestExtendedStatus)
    )
    assert sent_requests.index(enable) < first_poll


async def test_pushed_zone_status_updates_entity_without_a_poll(
    hass: HomeAssistant, configured_panel, sent_requests
) -> None:
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]
    assert hass.states.get(FRONT_DOOR).state == "off"
    polls_before = len(sent_requests)

    # Zone 1, status 0x01 (not ready), loop 0xfd — as captured on the wire.
    coordinator._handle_unsolicited(_extended_status(1, bytes.fromhex("000101fd")))
    await hass.async_block_till_done()

    assert hass.states.get(FRONT_DOOR).state == "on"
    assert len(sent_requests) == polls_before

    coordinator._handle_unsolicited(_extended_status(1, bytes.fromhex("0001007f")))
    await hass.async_block_till_done()

    assert hass.states.get(FRONT_DOOR).state == "off"


async def test_pushed_unit_status_updates_light(
    hass: HomeAssistant, configured_panel
) -> None:
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]
    assert hass.states.get(LIVING_LAMP).state == "off"

    # Unit 1, state 1 (on), no timer.
    coordinator._handle_unsolicited(_extended_status(2, bytes.fromhex("0001010000")))
    await hass.async_block_till_done()

    assert hass.states.get(LIVING_LAMP).state == "on"


async def test_push_for_undiscovered_object_is_ignored(
    hass: HomeAssistant, configured_panel
) -> None:
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]
    before = coordinator.data

    coordinator._handle_unsolicited(_extended_status(1, bytes.fromhex("006301fd")))

    assert coordinator.data is before


@pytest.mark.parametrize(
    "payload", [b"", b"\x01", b"\x01\x00", b"\x63\x04\x00\x01\x01\xfd", b"\x01\x04\x00"]
)
async def test_malformed_push_does_not_raise(
    hass: HomeAssistant, configured_panel, payload: bytes
) -> None:
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]
    before = coordinator.data

    coordinator._handle_unsolicited(
        encode_v2(OmniLink2MessageType.ExtendedStatus, payload)
    )

    assert coordinator.data is before
