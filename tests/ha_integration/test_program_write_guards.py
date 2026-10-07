"""Guardrails around changing the panel's programs from the side panel.

Covers what ships switched on and off, the refusals that protect
multi-line blocks and occupied slots, and the journal: every change is
verified against the panel and can be undone.
"""

from __future__ import annotations

import pytest
from custom_components.omni_pca import const, program_changes
from custom_components.omni_pca.const import DOMAIN
from homeassistant.core import HomeAssistant

# A THEN record (multi-line block member), as the panel sends it.
_THEN_RECORD = bytes.fromhex("0a 00 00 00 00 01 00 00 02 00 00 00 00 00")


async def _call(hass, hass_ws_client, entry, command: dict) -> dict:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({**command, "entry_id": entry.entry_id})
    return await client.receive_json()


def _fields(hass, entry, slot: int) -> dict:
    p = hass.data[DOMAIN][entry.entry_id].data.programs[slot]
    return {
        "prog_type": p.prog_type, "cond": p.cond, "cond2": p.cond2,
        "cmd": p.cmd, "par": p.par, "pr2": p.pr2, "month": p.month,
        "day": p.day, "days": p.days, "hour": p.hour, "minute": p.minute,
    }


# ---- what ships on and off -------------------------------------------------


def test_shipped_switches() -> None:
    assert const.PROGRAM_WRITES_ENABLED is True
    assert const.PROGRAM_CHAIN_WRITES_ENABLED is True
    assert const.PROGRAM_FIRE_ENABLED is False


async def test_list_reports_what_is_enabled(
    hass: HomeAssistant, configured_panel, hass_ws_client
) -> None:
    response = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/list"}
    )
    result = response["result"]
    assert result["can_write"] is True
    assert result["can_edit_chains"] is True
    assert result["can_fire"] is False


async def test_fire_is_refused(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/fire", "slot": 12,
    })
    assert response["success"] is False
    assert response["error"]["code"] == "read_only"
    assert mock.state.programs == before


async def test_panel_script_is_tied_to_the_integration_version(
    hass: HomeAssistant, configured_panel, hass_ws_client
) -> None:
    """The script URL carries the version (so an upgrade is not served
    from the browser cache) and the list reports it (so a script that is
    stale anyway can tell)."""
    import json
    from pathlib import Path

    import custom_components.omni_pca as integration

    version = json.loads(
        (Path(integration.__file__).parent / "manifest.json").read_text()
    )["version"]
    panel = hass.data["frontend_panels"]["omni-panel-programs"]
    module_url = panel.config["_panel_custom"]["module_url"]
    assert module_url == f"/api/omni_pca/panel.js?v={version}"

    response = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/list"}
    )
    assert response["result"]["version"] == version


@pytest.mark.parametrize(
    "command",
    [
        {"type": "omni_pca/programs/chain/write", "head_slot": 30,
         "head": {"prog_type": 5}, "conditions": [],
         "actions": [{"prog_type": 10}]},
        {"type": "omni_pca/programs/chain/clear", "head_slot": 30},
        {"type": "omni_pca/programs/chain/clone", "source_slot": 30,
         "target_slot": 300},
    ],
)
async def test_block_edits_are_refused_when_switched_off(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client,
    monkeypatch, command: dict,
) -> None:
    monkeypatch.setattr(
        "custom_components.omni_pca.websocket.PROGRAM_CHAIN_WRITES_ENABLED", False
    )
    mock = panel[0]
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, command)
    assert response["success"] is False
    assert response["error"]["code"] == "read_only"
    assert mock.state.programs == before


async def test_everything_is_refused_when_switched_off(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.omni_pca.websocket.PROGRAM_WRITES_ENABLED", False
    )
    mock = panel[0]
    before = dict(mock.state.programs)
    for command in (
        {"type": "omni_pca/programs/clear", "slot": 12},
        {"type": "omni_pca/programs/clone", "source_slot": 12, "target_slot": 13},
        {"type": "omni_pca/programs/write", "slot": 12,
         "program": _fields(hass, configured_panel, 12)},
        {"type": "omni_pca/programs/undo", "journal_id": "x"},
    ):
        response = await _call(hass, hass_ws_client, configured_panel, command)
        assert response["error"]["code"] == "read_only", command
    assert mock.state.programs == before


async def test_commands_need_an_admin(
    hass: HomeAssistant, configured_panel, hass_ws_client, hass_admin_user
) -> None:
    hass_admin_user.groups = []
    response = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/list"}
    )
    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"


# ---- refusals that protect other programs ----------------------------------


async def test_block_members_cannot_be_cleared_cloned_or_overwritten(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    # Put a block record on the panel behind the integration's back: the
    # guards must look at the panel, not at the cached table.
    mock.state.programs[200] = _THEN_RECORD
    before = dict(mock.state.programs)
    for command in (
        {"type": "omni_pca/programs/clear", "slot": 200},
        {"type": "omni_pca/programs/clone", "source_slot": 200, "target_slot": 201},
        {"type": "omni_pca/programs/write", "slot": 200,
         "program": _fields(hass, configured_panel, 12)},
    ):
        response = await _call(hass, hass_ws_client, configured_panel, command)
        assert response["success"] is False, command
        assert response["error"]["code"] == "invalid", command
        assert "multi-line" in response["error"]["message"]
    assert mock.state.programs == before


async def test_write_refuses_block_record_types(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/write", "slot": 300,
        "program": {"prog_type": 10, "cmd": 1, "pr2": 2},
    })
    assert response["error"]["code"] == "invalid"
    assert mock.state.programs == before


async def test_clone_refuses_an_occupied_target(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/clone", "source_slot": 12, "target_slot": 42,
    })
    assert response["error"]["code"] == "invalid"
    assert "not free" in response["error"]["message"]
    assert mock.state.programs == before


@pytest.fixture
def block_on_panel(panel) -> None:
    """Seed WHEN zone 1 not ready / THEN unit 2 on / THEN unit 1 off in
    slots 30-32. Request it before ``configured_panel`` so the
    integration discovers it at setup."""
    programs = panel[0].state.programs
    programs[30] = bytes.fromhex("05 00 00 00 00 00 00 00 00 06 01 00 00 00")
    programs[31] = bytes.fromhex("0a 00 00 00 00 01 00 00 02 00 00 00 00 00")
    programs[32] = bytes.fromhex("0a 00 00 00 00 00 00 00 01 00 00 00 00 00")


async def test_clone_into_a_block_says_what_is_there(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client
) -> None:
    """A slot inside a multi-line program looks empty in the list, which
    shows one row per program; the refusal has to explain itself."""
    mock = panel[0]
    entry = configured_panel
    before = dict(mock.state.programs)

    listing = await _call(hass, hass_ws_client, entry, {"type": "omni_pca/programs/list"})
    rows = {r["slot"]: r for r in listing["result"]["programs"]}
    assert rows[30]["last_slot"] == 32
    assert rows[12]["last_slot"] == 12
    assert 31 not in rows
    assert listing["result"]["next_free_slot"] == 100

    response = await _call(hass, hass_ws_client, entry, {
        "type": "omni_pca/programs/clone", "source_slot": 12, "target_slot": 31,
    })
    assert response["error"]["code"] == "invalid"
    message = response["error"]["message"]
    assert "line 2 of the multi-line program in slots 30-32" in message
    assert "next free slot is 100" in message
    assert mock.state.programs == before


async def test_clear_of_an_empty_slot_is_reported(
    hass: HomeAssistant, configured_panel, hass_ws_client
) -> None:
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/clear", "slot": 700,
    })
    assert response["error"]["code"] == "not_found"


async def test_write_refuses_if_the_slot_changed_since_it_was_loaded(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    original = _fields(hass, configured_panel, 12)
    # Someone else changes slot 12 on the panel after the editor loaded it.
    mock.state.programs[12] = mock.state.programs[42]
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/write", "slot": 12,
        "program": {**original, "hour": 7}, "original": original,
    })
    assert response["error"]["code"] == "conflict"
    assert mock.state.programs == before


# ---- journal and undo -------------------------------------------------------


async def test_edit_is_journalled_and_can_be_undone(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]
    start = dict(mock.state.programs)
    original = _fields(hass, configured_panel, 12)

    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/write", "slot": 12,
        "program": {**original, "hour": 7}, "original": original,
    })
    assert response["success"] is True
    assert mock.state.programs[12] != start[12]
    assert coordinator.data.programs[12].hour == 7

    history = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/history"}
    )
    entry = history["result"]["entries"][0]
    assert entry["action"] == "edit"
    assert entry["status"] == "applied"
    assert entry["changes"] == [{
        "slot": 12,
        "before": start[12].hex(),
        "after": mock.state.programs[12].hex(),
    }]

    undo = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/undo", "journal_id": entry["id"],
    })
    assert undo["success"] is True
    assert mock.state.programs == start
    assert coordinator.data.programs[12].hour == 6

    again = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/undo", "journal_id": entry["id"],
    })
    assert again["error"]["code"] == "invalid"


async def test_clear_and_clone_can_be_undone(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    start = dict(mock.state.programs)
    for command in (
        {"type": "omni_pca/programs/clone", "source_slot": 12, "target_slot": 13},
        {"type": "omni_pca/programs/clear", "slot": 42},
    ):
        response = await _call(hass, hass_ws_client, configured_panel, command)
        assert response["success"] is True, response
    assert mock.state.programs[13] == start[12]
    assert 42 not in mock.state.programs

    history = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/history"}
    )
    for entry in history["result"]["entries"]:
        undo = await _call(hass, hass_ws_client, configured_panel, {
            "type": "omni_pca/programs/undo", "journal_id": entry["id"],
        })
        assert undo["success"] is True, undo
    assert mock.state.programs == start


async def test_undo_refuses_if_the_slot_changed_again(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel
) -> None:
    mock = panel[0]
    await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/clear", "slot": 42,
    })
    history = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/history"}
    )
    mock.state.programs[42] = mock.state.programs[12]  # changed elsewhere
    before = dict(mock.state.programs)
    undo = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/undo",
        "journal_id": history["result"]["entries"][0]["id"],
    })
    assert undo["error"]["code"] == "conflict"
    assert mock.state.programs == before


async def test_a_write_that_does_not_stick_is_reported(
    hass: HomeAssistant, configured_panel, hass_ws_client, panel, monkeypatch
) -> None:
    """The panel acks but keeps the old program: the read-back catches it."""
    mock = panel[0]
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]

    async def _lost_write(client, slot, program) -> None:
        return None

    monkeypatch.setattr(program_changes, "async_write_program", _lost_write)
    original = _fields(hass, configured_panel, 12)
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/write", "slot": 12,
        "program": {**original, "hour": 7}, "original": original,
    })
    assert response["error"]["code"] == "verify_failed"
    assert mock.state.programs == before
    assert coordinator.data.programs[12].hour == 6

    history = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/history"}
    )
    assert history["result"]["entries"][0]["status"] == "failed"
