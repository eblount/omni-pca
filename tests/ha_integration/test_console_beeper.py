"""Console beeper switch — an optimistic toggle over Command 102."""

from __future__ import annotations

from custom_components.omni_pca.const import DOMAIN
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import mock_restore_cache

from omni_pca.commands import Command


def _beeper_entity_id(hass: HomeAssistant) -> str:
    matches = [
        s.entity_id
        for s in hass.states.async_all("switch")
        if s.entity_id.endswith("console_beeper")
    ]
    assert len(matches) == 1
    return matches[0]


def _record_commands(hass: HomeAssistant, entry_id: str) -> list[tuple[int, int, int]]:
    """Wrap the live client's execute_command, keeping the real round-trip."""
    client = hass.data[DOMAIN][entry_id].client
    sent: list[tuple[int, int, int]] = []
    real = client.execute_command

    async def spy(command: Command, parameter1: int = 0, parameter2: int = 0) -> None:
        sent.append((int(command), parameter1, parameter2))
        await real(command, parameter1, parameter2)

    client.execute_command = spy  # type: ignore[method-assign]
    return sent


async def test_beeper_defaults_on(hass: HomeAssistant, configured_panel) -> None:
    assert hass.states.get(_beeper_entity_id(hass)).state == "on"


async def test_turn_off_then_on_sends_command_102_to_all_consoles(
    hass: HomeAssistant, configured_panel
) -> None:
    entity_id = _beeper_entity_id(hass)
    sent = _record_commands(hass, configured_panel.entry_id)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == "on"

    assert sent == [(102, 0, 0), (102, 1, 0)]


async def test_last_state_is_restored_without_sending(
    hass: HomeAssistant, config_entry_data
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data,
        title="Mock Omni",
        unique_id="restore-test",
    )
    entry.add_to_hass(hass)
    # Entity IDs derive from the device name, which comes from the panel
    # model, so learn the ID from a first setup before seeding the cache.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = _beeper_entity_id(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    mock_restore_cache(hass, [State(entity_id, "off")])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        assert hass.states.get(entity_id).state == "off"
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_send_command_service_reaches_panel(
    hass: HomeAssistant, configured_panel
) -> None:
    sent = _record_commands(hass, configured_panel.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "send_command",
        {
            "entry_id": configured_panel.entry_id,
            "command": 102,
            "parameter1": 0,
            "parameter2": 0,
        },
        blocking=True,
    )

    assert sent == [(102, 0, 0)]
