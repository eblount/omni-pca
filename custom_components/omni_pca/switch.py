"""Switch platform — per-zone bypass control and the console beeper.

Lights are exposed via the ``light`` platform. The switch platform is
reserved for *configuration* toggles like zone bypass, where the user
wants a write surface that pairs with the diagnostic ``zone bypassed``
binary sensor for read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from omni_pca.commands import Command, CommandFailedError

from .const import DOMAIN
from .coordinator import OmniDataUpdateCoordinator
from .helpers import is_binary_zone_type, prettify_name

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: OmniDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [OmniConsoleBeeperSwitch(coordinator)]
    for index in sorted(coordinator.data.zones):
        props = coordinator.data.zones[index]
        if not is_binary_zone_type(props.zone_type):
            continue
        entities.append(OmniZoneBypassSwitch(coordinator, index))
    async_add_entities(entities)


class OmniZoneBypassSwitch(CoordinatorEntity[OmniDataUpdateCoordinator], SwitchEntity):
    """Toggle that bypasses or restores a single zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: OmniDataUpdateCoordinator, index: int
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.unique_id}-zone-{index}-bypass"
        props = coordinator.data.zones[index]
        base = prettify_name(props.name) or f"Zone {index}"
        self._attr_name = f"{base} Bypass"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._index in self.coordinator.data.zones
        )

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.zone_status.get(self._index)
        if status is None:
            return None
        return status.is_bypassed

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.bypass_zone(self._index)
        except CommandFailedError as err:
            raise HomeAssistantError(f"Panel rejected bypass: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.restore_zone(self._index)
        except CommandFailedError as err:
            raise HomeAssistantError(f"Panel rejected restore: {err}") from err
        await self.coordinator.async_request_refresh()


class OmniConsoleBeeperSwitch(
    CoordinatorEntity[OmniDataUpdateCoordinator], SwitchEntity, RestoreEntity
):
    """Enable or disable the beeper on every console.

    Off silences the consoles' own announcements — on a real Omni IIe
    (fw 3.2r2) the door chime stops while key-press beeps keep working.

    The panel has no way to report this setting, so the state shown is
    the last one sent from Home Assistant, restored across restarts and
    assumed enabled (the panel default) when nothing was ever sent.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Console Beeper"
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: OmniDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}-console-beeper"
        self._attr_device_info = coordinator.device_info
        self._attr_is_on = True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_beeper(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_beeper(False)

    async def _set_beeper(self, enabled: bool) -> None:
        # Command 102 is enuUnitCommand.ConsoleBeep: parameter1 enables
        # (1) or disables (0) the beeper, parameter2 picks the console
        # with 0 meaning all of them.
        try:
            await self.coordinator.client.execute_command(
                Command.CONSOLE_BEEP, int(enabled), 0
            )
        except CommandFailedError as err:
            raise HomeAssistantError(
                f"Panel rejected console beeper change: {err}"
            ) from err
        self._attr_is_on = enabled
        self.async_write_ha_state()
