"""Apply program changes to the panel safely, and keep a way back.

Every change the Omni Programs panel makes goes through
:meth:`ProgramChanges.async_apply`, which:

1. reads the affected slots from the panel (never trusting the cached
   table) so the "before" state is what is really there;
2. saves a journal entry holding the before and after bytes *before*
   writing anything, so an interrupted write can still be undone;
3. writes one slot at a time and reads each back, failing loudly if the
   panel holds anything other than what was sent;
4. updates the coordinator's cached table from what was verified.

A multi-line block is several records in adjacent slots, and the panel
keeps running while they are rewritten. Changes flagged ``block`` are
therefore written so the panel never holds a misleading mix: every
affected slot that is in use is first freed from the last slot
backwards (a block loses its actions before its conditions and its
trigger, and no line is ever left behind a different program), then the
new records are written from the first slot forwards (a block gains its
trigger and all its conditions before any action).

The journal lives in Home Assistant's ``.storage`` and is what
:meth:`ProgramChanges.async_undo` works from. The first time it is
created it also snapshots the whole program table as a baseline.
"""

from __future__ import annotations

import asyncio
import uuid
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .bundled.programs import PROGRAM_BYTES, Program, ProgramType
from .const import DOMAIN, LOGGER
from .program_io import async_read_program, async_write_program

if TYPE_CHECKING:
    from .coordinator import OmniDataUpdateCoordinator

_STORAGE_VERSION = 1
_MAX_ENTRIES = 200

STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_UNDONE = "undone"

_EMPTY = bytes(PROGRAM_BYTES)


class ProgramChangeError(Exception):
    """A change was refused or did not verify. ``code`` suits a WS error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SlotChange:
    """What one slot should hold afterwards; ``None`` frees it."""

    slot: int
    program: Program | None

    @property
    def body(self) -> bytes:
        return _EMPTY if self.program is None else self.program.encode_wire_bytes()


def _to_hex(body: bytes) -> str | None:
    """Journal form of a slot body: hex, or ``None`` for a free slot."""
    return None if body == _EMPTY else body.hex()


def _from_hex(value: str | None) -> bytes:
    return _EMPTY if value is None else bytes.fromhex(value)


def _program_for(slot: int, body: bytes) -> Program | None:
    return None if body == _EMPTY else Program.from_wire_bytes(body, slot=slot)


class ProgramChanges:
    """Verified writes plus the undo journal for one config entry."""

    def __init__(
        self, hass: HomeAssistant, coordinator: OmniDataUpdateCoordinator
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}.program_journal.{coordinator.config_entry.entry_id}",
        )
        self._data: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    # ---- journal ---------------------------------------------------------

    async def _async_load(self) -> dict[str, Any]:
        if self._data is None:
            self._data = await self._store.async_load() or {"entries": []}
        if "baseline" not in self._data:
            programs = (
                self._coordinator.data.programs if self._coordinator.data else {}
            )
            self._data["baseline"] = {
                "taken": dt_util.utcnow().isoformat(),
                "programs": {
                    str(slot): p.encode_wire_bytes().hex()
                    for slot, p in sorted(programs.items())
                },
            }
            await self._store.async_save(self._data)
        return self._data

    async def async_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Journal entries, newest first."""
        data = await self._async_load()
        return list(reversed(data["entries"]))[:limit]

    # ---- reading ---------------------------------------------------------

    async def _async_read_body(self, slot: int) -> bytes:
        program = await async_read_program(self._coordinator.client, slot)
        return _EMPTY if program is None else program.encode_wire_bytes()

    async def async_read(self, slot: int) -> Program | None:
        """What the panel holds in ``slot`` right now."""
        return _program_for(slot, await self._async_read_body(slot))

    # ---- writing ---------------------------------------------------------

    async def async_apply(
        self,
        action: str,
        changes: list[SlotChange],
        *,
        user_id: str | None,
        expect_before: dict[int, set[bytes]] | None = None,
        undoes: str | None = None,
        block: bool = False,
    ) -> dict[str, Any] | None:
        """Write ``changes`` (one per slot), journalled and verified.

        ``expect_before`` maps a slot to the bodies it is allowed to hold
        beforehand; anything else means the panel changed under us and
        the whole change is refused before a single write.

        ``block`` selects the free-backwards-then-write-forwards order
        described in the module docstring; without it each slot is
        simply overwritten, in the order given.

        Returns the journal entry, or ``None`` if the panel already
        holds exactly what was asked for (nothing written or journalled).
        """
        async with self._lock:
            data = await self._async_load()
            before: dict[int, bytes] = {}
            for change in changes:
                before[change.slot] = await self._async_read_body(change.slot)
            for slot, allowed in (expect_before or {}).items():
                if before[slot] not in allowed:
                    raise ProgramChangeError(
                        "conflict",
                        f"slot {slot} no longer holds what was expected; "
                        "reload and try again",
                    )
            if all(before[c.slot] == c.body for c in changes):
                return None

            entry: dict[str, Any] = {
                "id": uuid.uuid4().hex[:12],
                "time": dt_util.utcnow().isoformat(),
                "user_id": user_id,
                "action": action,
                "status": STATUS_PENDING,
                "changes": [
                    {
                        "slot": c.slot,
                        "before": _to_hex(before[c.slot]),
                        "after": _to_hex(c.body),
                    }
                    for c in changes
                ],
            }
            if undoes is not None:
                entry["undoes"] = undoes
            if block:
                entry["block"] = True
            data["entries"].append(entry)
            del data["entries"][:-_MAX_ENTRIES]
            await self._store.async_save(data)

            try:
                if block:
                    in_use = sorted(
                        (c.slot for c in changes if before[c.slot] != _EMPTY),
                        reverse=True,
                    )
                    for slot in in_use:
                        await self._async_write_verified(SlotChange(slot, None))
                    for change in sorted(changes, key=lambda c: c.slot):
                        if change.program is not None:
                            await self._async_write_verified(change)
                else:
                    for change in changes:
                        await self._async_write_verified(change)
            except Exception as err:
                entry["status"] = STATUS_FAILED
                entry["error"] = str(err)
                await self._store.async_save(data)
                await self._async_resync([c.slot for c in changes])
                if isinstance(err, ProgramChangeError):
                    raise
                if isinstance(err, NotImplementedError):
                    raise ProgramChangeError("not_supported", str(err)) from err
                raise ProgramChangeError("write_failed", str(err)) from err

            entry["status"] = STATUS_APPLIED
            await self._store.async_save(data)
            self._update_cache({c.slot: c.body for c in changes})
            return entry

    async def _async_write_verified(self, change: SlotChange) -> None:
        program = change.program or Program(
            slot=change.slot, prog_type=int(ProgramType.FREE)
        )
        await async_write_program(self._coordinator.client, change.slot, program)
        got = await self._async_read_body(change.slot)
        if got != change.body:
            raise ProgramChangeError(
                "verify_failed",
                f"slot {change.slot} read back as {got.hex(' ')} after "
                f"writing {change.body.hex(' ')}",
            )

    async def _async_resync(self, slots: list[int]) -> None:
        """After a failed change, make the cache match the panel again."""
        try:
            self._update_cache(
                {slot: await self._async_read_body(slot) for slot in slots}
            )
        except Exception:
            LOGGER.warning(
                "could not re-read program slots %s after a failed change",
                slots, exc_info=True,
            )

    def _update_cache(self, bodies: dict[int, bytes]) -> None:
        data = self._coordinator.data
        if data is None:
            return
        for slot, body in bodies.items():
            program = _program_for(slot, body)
            if program is None:
                data.programs.pop(slot, None)
            else:
                data.programs[slot] = program
        # Keep slot order: chains are found by walking adjacent slots.
        ordered = dict(sorted(data.programs.items()))
        data.programs.clear()
        data.programs.update(ordered)
        self._coordinator.async_update_listeners()

    # ---- undo ------------------------------------------------------------

    async def async_undo(
        self, journal_id: str, *, user_id: str | None
    ) -> dict[str, Any]:
        """Put back what the journal entry ``journal_id`` replaced.

        Refused if any of its slots now holds something that is neither
        the entry's "before" nor its "after" — someone changed it since.
        """
        data = await self._async_load()
        target = next((e for e in data["entries"] if e["id"] == journal_id), None)
        if target is None:
            raise ProgramChangeError("not_found", f"no change {journal_id}")
        if target["status"] == STATUS_UNDONE:
            raise ProgramChangeError("invalid", "that change was already undone")
        changes = [
            SlotChange(c["slot"], _program_for(c["slot"], _from_hex(c["before"])))
            for c in reversed(target["changes"])
        ]
        expect = {
            c["slot"]: {_from_hex(c["before"]), _from_hex(c["after"])}
            for c in target["changes"]
        }
        if target.get("block"):
            # A block change that failed part-way leaves freed slots.
            for allowed in expect.values():
                allowed.add(_EMPTY)
        entry = await self.async_apply(
            "undo", changes, user_id=user_id, expect_before=expect,
            undoes=journal_id, block=bool(target.get("block")),
        )
        if entry is None:
            # A failed change that never altered the panel: nothing to put back.
            entry = {"id": None, "action": "undo", "changes": []}
        target["status"] = STATUS_UNDONE
        await self._store.async_save(data)
        return entry


# One per coordinator; a reloaded entry gets a new coordinator and so a
# fresh instance, while the journal itself persists in storage.
_INSTANCES: weakref.WeakKeyDictionary[Any, ProgramChanges] = (
    weakref.WeakKeyDictionary()
)


def async_get_program_changes(
    hass: HomeAssistant, coordinator: OmniDataUpdateCoordinator
) -> ProgramChanges:
    """The :class:`ProgramChanges` for a coordinator, created on first use."""
    existing = _INSTANCES.get(coordinator)
    if existing is None:
        existing = _INSTANCES[coordinator] = ProgramChanges(hass, coordinator)
    return existing
