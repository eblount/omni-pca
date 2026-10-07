"""Editing multi-line programs (a trigger line plus AND / THEN lines).

A block lives in adjacent slots, so rewriting one touches several
records while the panel keeps running. These tests pin the rules that
keep that safe: the order records are freed and written in, growing
without trampling a neighbour, and undo putting a whole block back.
"""

from __future__ import annotations

import pytest
from custom_components.omni_pca import program_changes
from custom_components.omni_pca.const import DOMAIN
from homeassistant.core import HomeAssistant

# WHEN zone 1 not ready / THEN unit 2 on / THEN unit 1 off, as on the wire.
_WHEN = "05 00 00 00 00 00 00 00 00 06 01 00 00 00"
_THEN_2_ON = "0a 00 00 00 00 01 00 00 02 00 00 00 00 00"
_THEN_1_OFF = "0a 00 00 00 00 00 00 00 01 00 00 00 00 00"
_AND_DARK = "08 00 00 03 00 00 00 00 00 00 00 00 00 00"

_HEAD = {"prog_type": 5, "month": 0x06, "day": 0x01}
_ACT_2_ON = {"prog_type": 10, "cmd": 1, "pr2": 2}
_ACT_1_OFF = {"prog_type": 10, "cmd": 0, "pr2": 1}
_COND_DARK = {"prog_type": 8, "cond": 0x0000, "cond2": 0x0300}


def _b(hex_body: str) -> bytes:
    return bytes.fromhex(hex_body)


@pytest.fixture
def block_on_panel(panel) -> None:
    """The block in slots 30-32; request it before ``configured_panel``."""
    programs = panel[0].state.programs
    programs[30], programs[31], programs[32] = _b(_WHEN), _b(_THEN_2_ON), _b(_THEN_1_OFF)


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, bool]]:
    """Every record written to the panel, as (slot, is_a_real_record)."""
    seen: list[tuple[int, bool]] = []
    real = program_changes.async_write_program

    async def _recording(client, slot, program) -> None:
        seen.append((slot, not program.is_empty()))
        await real(client, slot, program)

    monkeypatch.setattr(program_changes, "async_write_program", _recording)
    return seen


async def _call(hass, hass_ws_client, entry, command: dict) -> dict:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({**command, "entry_id": entry.entry_id})
    return await client.receive_json()


async def _undo_latest(hass, hass_ws_client, entry) -> dict:
    history = await _call(hass, hass_ws_client, entry, {"type": "omni_pca/programs/history"})
    return await _call(hass, hass_ws_client, entry, {
        "type": "omni_pca/programs/undo",
        "journal_id": history["result"]["entries"][0]["id"],
    })


async def test_rewrite_frees_backwards_then_writes_forwards(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client, writes
) -> None:
    mock = panel[0]
    start = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/write", "head_slot": 30,
        "head": _HEAD, "conditions": [],
        "actions": [_ACT_2_ON, {**_ACT_1_OFF, "pr2": 2}],
    })
    assert response["success"] is True, response
    assert response["result"]["head_slot"] == 30
    # Actions go first, the trigger last; then the trigger comes back
    # first and the actions last. No line is ever left without its trigger.
    assert writes == [
        (32, False), (31, False), (30, False),
        (30, True), (31, True), (32, True),
    ]
    assert mock.state.programs[32] == _b("0a 00 00 00 00 00 00 00 02 00 00 00 00 00")
    assert mock.state.programs[30] == start[30]

    undo = await _undo_latest(hass, hass_ws_client, configured_panel)
    assert undo["success"] is True, undo
    assert mock.state.programs == start


async def test_saving_an_unchanged_block_writes_nothing(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client, writes
) -> None:
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/write", "head_slot": 30,
        "head": _HEAD, "conditions": [], "actions": [_ACT_2_ON, _ACT_1_OFF],
    })
    assert response["success"] is True, response
    assert writes == []
    history = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/history"}
    )
    assert history["result"]["entries"] == []


async def test_block_grows_in_place_when_the_next_slot_is_free(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client
) -> None:
    mock = panel[0]
    start = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/write", "head_slot": 30,
        "head": _HEAD, "conditions": [_COND_DARK],
        "actions": [_ACT_2_ON, _ACT_1_OFF],
    })
    assert response["success"] is True, response
    assert [mock.state.programs[s] for s in (30, 31, 32, 33)] == [
        _b(_WHEN), _b(_AND_DARK), _b(_THEN_2_ON), _b(_THEN_1_OFF),
    ]
    listing = await _call(
        hass, hass_ws_client, configured_panel, {"type": "omni_pca/programs/list"}
    )
    rows = {r["slot"]: r for r in listing["result"]["programs"]}
    assert rows[30]["last_slot"] == 33

    undo = await _undo_latest(hass, hass_ws_client, configured_panel)
    assert undo["success"] is True, undo
    assert mock.state.programs == start


async def test_block_that_no_longer_fits_can_be_moved(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client
) -> None:
    mock = panel[0]
    coordinator = hass.data[DOMAIN][configured_panel.entry_id]
    # Another program sits right after the block (clone 12 into 33).
    assert (await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/clone", "source_slot": 12, "target_slot": 33,
    }))["success"]
    start = dict(mock.state.programs)
    grown = {
        "type": "omni_pca/programs/chain/write", "head_slot": 30,
        "head": _HEAD, "conditions": [_COND_DARK],
        "actions": [_ACT_2_ON, _ACT_1_OFF],
    }

    refused = await _call(hass, hass_ws_client, configured_panel, grown)
    assert refused["error"]["code"] == "no_room"
    assert "slot 33 is in use" in refused["error"]["message"]
    assert "moved to slots 100-103" in refused["error"]["message"]
    assert mock.state.programs == start

    moved = await _call(hass, hass_ws_client, configured_panel, {**grown, "relocate": True})
    assert moved["success"] is True, moved
    assert moved["result"]["head_slot"] == 100
    assert [mock.state.programs[s] for s in (100, 101, 102, 103)] == [
        _b(_WHEN), _b(_AND_DARK), _b(_THEN_2_ON), _b(_THEN_1_OFF),
    ]
    assert not {30, 31, 32} & set(mock.state.programs)
    assert mock.state.programs[33] == start[33]
    assert sorted(coordinator.data.programs) == sorted(mock.state.programs)

    undo = await _undo_latest(hass, hass_ws_client, configured_panel)
    assert undo["success"] is True, undo
    assert mock.state.programs == start


async def test_block_shrinks_and_frees_its_last_slot(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client
) -> None:
    mock = panel[0]
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/write", "head_slot": 30,
        "head": _HEAD, "conditions": [], "actions": [_ACT_2_ON],
    })
    assert response["success"] is True, response
    assert response["result"]["cleared_slots"] == [32]
    assert 32 not in mock.state.programs
    assert mock.state.programs[31] == _b(_THEN_2_ON)


async def test_write_refuses_if_the_block_changed_since_it_was_loaded(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client
) -> None:
    mock = panel[0]
    original = [_HEAD, _ACT_2_ON, _ACT_1_OFF]
    mock.state.programs[31] = _b(_THEN_1_OFF)  # changed elsewhere
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/write", "head_slot": 30,
        "head": _HEAD, "conditions": [], "actions": [_ACT_2_ON],
        "original": original,
    })
    assert response["error"]["code"] == "conflict"
    assert mock.state.programs == before


async def test_delete_removes_every_line_and_can_be_undone(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client, writes
) -> None:
    mock = panel[0]
    start = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/clear", "head_slot": 30,
    })
    assert response["success"] is True, response
    assert writes == [(32, False), (31, False), (30, False)]
    assert not {30, 31, 32} & set(mock.state.programs)

    undo = await _undo_latest(hass, hass_ws_client, configured_panel)
    assert undo["success"] is True, undo
    assert mock.state.programs == start


async def test_clone_copies_the_whole_block(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client
) -> None:
    mock = panel[0]
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/clone", "source_slot": 30, "target_slot": 200,
    })
    assert response["success"] is True, response
    assert [mock.state.programs[s] for s in (200, 201, 202)] == [
        _b(_WHEN), _b(_THEN_2_ON), _b(_THEN_1_OFF),
    ]
    assert mock.state.programs[30] == _b(_WHEN)


async def test_clone_refuses_when_the_copy_would_overlap(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client
) -> None:
    mock = panel[0]
    before = dict(mock.state.programs)
    # Slots 40-42 are needed, and 42 holds a program.
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/clone", "source_slot": 30, "target_slot": 40,
    })
    assert response["error"]["code"] == "invalid"
    assert "needs slots 40-42" in response["error"]["message"]
    assert mock.state.programs == before


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"head": {"prog_type": 1}, "conditions": [], "actions": [_ACT_2_ON]},
         "WHEN, AT or EVERY"),
        ({"head": _HEAD, "conditions": [_ACT_2_ON], "actions": [_ACT_2_ON]},
         "AND or OR"),
        ({"head": _HEAD, "conditions": [], "actions": [_COND_DARK]}, "THEN"),
        ({"head": _HEAD, "conditions": [], "actions": []}, "at least one action"),
    ],
)
async def test_malformed_blocks_are_refused(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client,
    payload: dict, needle: str,
) -> None:
    mock = panel[0]
    before = dict(mock.state.programs)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/write", "head_slot": 30, **payload,
    })
    assert response["error"]["code"] == "invalid"
    assert needle in response["error"]["message"]
    assert mock.state.programs == before


@pytest.mark.parametrize("command", ["chain/write", "chain/clear", "chain/clone"])
async def test_block_commands_need_a_block_at_that_slot(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client,
    command: str,
) -> None:
    """Slot 31 is a block's second line, slot 12 a single-line program."""
    mock = panel[0]
    before = dict(mock.state.programs)
    for slot in (31, 12, 700):
        extra = {
            "chain/write": {"head_slot": slot, "head": _HEAD, "conditions": [],
                            "actions": [_ACT_2_ON]},
            "chain/clear": {"head_slot": slot},
            "chain/clone": {"source_slot": slot, "target_slot": 300},
        }[command]
        response = await _call(hass, hass_ws_client, configured_panel, {
            "type": f"omni_pca/programs/{command}", **extra,
        })
        assert response["error"]["code"] == "not_found", (slot, response)
    assert mock.state.programs == before


async def test_a_failure_part_way_is_journalled_and_can_be_undone(
    hass: HomeAssistant, panel, block_on_panel, configured_panel, hass_ws_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = panel[0]
    start = dict(mock.state.programs)
    real = program_changes.async_write_program
    calls = 0

    async def _fails_on_fifth(client, slot, program) -> None:
        nonlocal calls
        calls += 1
        if calls == 5:
            raise OSError("link dropped")
        await real(client, slot, program)

    monkeypatch.setattr(program_changes, "async_write_program", _fails_on_fifth)
    response = await _call(hass, hass_ws_client, configured_panel, {
        "type": "omni_pca/programs/chain/write", "head_slot": 30,
        "head": _HEAD, "conditions": [], "actions": [_ACT_1_OFF, _ACT_2_ON],
    })
    assert response["error"]["code"] == "write_failed"
    # Freed 32, 31, 30, rewrote 30, then failed on 31: only the trigger
    # line is there, so the half-written program can't do anything.
    assert {s for s in (30, 31, 32) if s in mock.state.programs} == {30}

    monkeypatch.setattr(program_changes, "async_write_program", real)
    undo = await _undo_latest(hass, hass_ws_client, configured_panel)
    assert undo["success"] is True, undo
    assert mock.state.programs == start
