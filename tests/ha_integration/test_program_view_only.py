"""The Omni Programs panel ships view-only: reads work, writes are refused."""

from __future__ import annotations

import pytest
from custom_components.omni_pca.const import DOMAIN, PROGRAM_WRITES_ENABLED
from homeassistant.core import HomeAssistant


def test_writes_ship_disabled() -> None:
    assert PROGRAM_WRITES_ENABLED is False


async def test_list_reports_view_only(
    hass: HomeAssistant, configured_panel, hass_ws_client
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "omni_pca/programs/list",
        "entry_id": configured_panel.entry_id,
    })
    response = await client.receive_json()
    assert response["success"] is True
    assert response["result"]["can_write"] is False


@pytest.mark.parametrize(
    "command",
    [
        {"type": "omni_pca/programs/write", "slot": 5,
         "program": {"prog_type": 1}},
        {"type": "omni_pca/programs/clear", "slot": 5},
        {"type": "omni_pca/programs/clone", "source_slot": 5, "target_slot": 6},
        {"type": "omni_pca/programs/chain/write", "head_slot": 5,
         "head": {"prog_type": 5}, "conditions": [],
         "actions": [{"prog_type": 10}]},
    ],
)
async def test_write_commands_are_refused(
    hass: HomeAssistant, configured_panel, hass_ws_client, command: dict
) -> None:
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]
    before = dict(coordinator.data.programs)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {**command, "entry_id": configured_panel.entry_id}
    )
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == "read_only"
    assert coordinator.data.programs == before


async def test_commands_need_an_admin(
    hass: HomeAssistant, configured_panel, hass_ws_client, hass_admin_user
) -> None:
    hass_admin_user.groups = []
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "omni_pca/programs/list",
        "entry_id": configured_panel.entry_id,
    })
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"
