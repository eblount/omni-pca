"""HA websocket commands for the program viewer.

The frontend talks to the integration via Home Assistant's standard
websocket API. We register three commands here, all namespaced under
``omni_pca/programs/``:

* ``omni_pca/programs/list`` — paginated, filterable summary list. Each
  result row carries the token stream for the one-line summary plus the
  metadata the frontend needs to filter and drill in.
* ``omni_pca/programs/get`` — full detail for a single slot. Returns
  the structured-English token stream for the compact form or the
  full clausal chain.
* ``omni_pca/programs/fire`` — send ``Command.EXECUTE_PROGRAM`` over
  the wire to ask the panel to run a program now. Returns success/error.

All commands take an ``entry_id`` so multi-panel installs can address
the right coordinator. The frontend's panel UI uses HA's `<ha-conn>`
WS client; this module just produces JSON-safe dicts.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from omni_pca.commands import Command

from .bundled.program_engine import ClausalChain, build_chains
from .bundled.program_renderer import (
    NameResolver,
    ProgramRenderer,
    StateResolver,
    Token,
)
from .bundled.programs import PROGRAM_BYTES, Program, ProgramType
from .const import (
    DOMAIN,
    PROGRAM_CHAIN_WRITES_ENABLED,
    PROGRAM_FIRE_ENABLED,
    PROGRAM_WRITES_ENABLED,
)
from .program_changes import (
    ProgramChangeError,
    SlotChange,
    async_get_program_changes,
)

if TYPE_CHECKING:
    from .coordinator import OmniDataUpdateCoordinator


# --------------------------------------------------------------------------
# Coordinator-backed resolvers
# --------------------------------------------------------------------------


class _CoordinatorNameResolver:
    """Resolve object names from coordinator-discovered topology.

    The coordinator's :class:`OmniData` stores ``zones`` / ``units`` /
    ``areas`` / ``thermostats`` / ``buttons`` as dicts of typed
    properties dataclasses (each with a ``name`` attribute). For
    ``message`` / ``code`` / ``timeclock`` we don't track HA-side
    properties — fall through to ``None`` so the renderer generates
    ``"Message 5"``-style labels.
    """

    def __init__(self, coordinator: "OmniDataUpdateCoordinator") -> None:
        self._coordinator = coordinator

    def name_of(self, kind: str, index: int) -> str | None:
        data = self._coordinator.data
        if data is None:
            return None
        bucket = {
            "zone": data.zones,
            "unit": data.units,
            "area": data.areas,
            "thermostat": data.thermostats,
            "button": data.buttons,
        }.get(kind)
        if bucket is None:
            return None
        props = bucket.get(index)
        if props is None:
            return None
        return getattr(props, "name", None) or None


class _CoordinatorStateResolver:
    """Live-state overlay using the coordinator's *_status maps.

    The status dicts update on every poll *and* are patched in-place
    when the event listener decodes a push event, so each websocket
    call sees the freshest available state without a round-trip to
    the panel.
    """

    _AREA_MODES: dict[int, str] = {
        0: "Off", 1: "Day", 2: "Night", 3: "Away",
        4: "Vacation", 5: "Day Instant", 6: "Night Delayed",
    }

    def __init__(self, coordinator: "OmniDataUpdateCoordinator") -> None:
        self._coordinator = coordinator

    def state_of(self, kind: str, index: int) -> str | None:
        data = self._coordinator.data
        if data is None:
            return None
        if kind == "zone":
            status = data.zone_status.get(index)
            if status is None:
                return None
            if status.is_bypassed:
                return "BYPASSED"
            return {0: "SECURE", 1: "NOT READY", 2: "TROUBLE", 3: "TAMPER"}.get(
                status.current_state, f"state {status.current_state}",
            )
        if kind == "unit":
            status = data.unit_status.get(index)
            if status is None:
                return None
            if status.state == 0:
                return "OFF"
            if status.state >= 100:
                return f"ON {status.state - 100}%"
            return "ON"
        if kind == "area":
            status = data.area_status.get(index)
            if status is None:
                return None
            return self._AREA_MODES.get(status.mode, f"mode {status.mode}")
        if kind == "thermostat":
            status = data.thermostat_status.get(index)
            if status is None or status.temperature_raw == 0:
                return None
            return f"{status.temperature_raw // 2 - 40}°F"
        return None


# --------------------------------------------------------------------------
# Token serialisation + reference extraction
# --------------------------------------------------------------------------


def _tokens_to_json(tokens: list[Token]) -> list[dict[str, Any]]:
    """Serialise a Token list to plain dicts the websocket layer can JSON.

    ``dataclasses.asdict`` would also work but produces ``None`` keys for
    fields irrelevant to the token's kind; we omit those explicitly so
    the wire format stays compact and the frontend sees clean shapes.
    """
    out: list[dict[str, Any]] = []
    for t in tokens:
        d: dict[str, Any] = {"k": t.kind, "t": t.text}
        if t.entity_kind is not None:
            d["ek"] = t.entity_kind
        if t.entity_id is not None:
            d["ei"] = t.entity_id
        if t.state is not None:
            d["s"] = t.state
        out.append(d)
    return out


def _extract_references(tokens: list[Token]) -> list[str]:
    """Collect distinct ``"<kind>:<id>"`` references from a token stream.

    Used to populate each list-row's ``references`` field so the
    frontend can filter on "involves this entity" without re-parsing
    the tokens. Returns a deduplicated, stable-ordered list.
    """
    seen: dict[str, None] = {}
    for t in tokens:
        if t.entity_kind and t.entity_id is not None:
            seen[f"{t.entity_kind}:{t.entity_id}"] = None
    return list(seen.keys())


# --------------------------------------------------------------------------
# Helper: pick a coordinator + build the renderer
# --------------------------------------------------------------------------


def _coordinator_for_entry(
    hass: HomeAssistant, entry_id: str,
) -> "OmniDataUpdateCoordinator | None":
    return hass.data.get(DOMAIN, {}).get(entry_id)


def _build_renderer(coordinator: "OmniDataUpdateCoordinator") -> ProgramRenderer:
    return ProgramRenderer(
        names=_CoordinatorNameResolver(coordinator),
        state=_CoordinatorStateResolver(coordinator),
    )


def _classify_trigger(p: Program) -> str:
    """Stable string label for the trigger type — used in filter chips."""
    try:
        return ProgramType(p.prog_type).name
    except ValueError:
        return f"UNKNOWN_{p.prog_type}"


# --------------------------------------------------------------------------
# Websocket commands
# --------------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/list",
        vol.Required("entry_id"): str,
        vol.Optional("trigger_types"): [str],
        vol.Optional("references_entity"): str,   # e.g. "zone:5"
        vol.Optional("search"): str,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=1500)),
        vol.Optional("offset"): vol.All(int, vol.Range(min=0)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_list_programs(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Paginated list of programs with filters applied.

    Returns rows containing the one-line summary tokens plus the
    metadata needed for filter UI (trigger type, references). The
    frontend renders the summary inline; clicking a row triggers a
    follow-up ``programs/get`` for the full detail.
    """
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return

    renderer = _build_renderer(coordinator)
    programs = coordinator.data.programs if coordinator.data else {}
    # Clausal chains span multiple slots — group them so each chain
    # appears once instead of one row per slot.
    chains_by_head_slot = {
        c.head.slot: c for c in build_chains(tuple(programs.values()))
    }
    consumed_chain_slots: set[int] = set()
    for chain in chains_by_head_slot.values():
        if chain.head.slot is not None:
            consumed_chain_slots.add(chain.head.slot)
        for cond in chain.conditions:
            if cond.slot is not None:
                consumed_chain_slots.add(cond.slot)
        for action in chain.actions:
            if action.slot is not None:
                consumed_chain_slots.add(action.slot)

    rows: list[dict[str, Any]] = []
    for slot in sorted(programs):
        if slot in chains_by_head_slot:
            chain = chains_by_head_slot[slot]
            summary = renderer.summarize_chain(chain)
            rows.append({
                "slot": slot,
                "last_slot": max(
                    m.slot for m in (chain.head, *chain.conditions, *chain.actions)
                    if m.slot is not None
                ),
                "kind": "chain",
                "trigger_type": _classify_trigger(chain.head),
                "summary": _tokens_to_json(summary),
                "references": _extract_references(summary),
                "condition_count": len(chain.conditions),
                "action_count": len(chain.actions),
            })
            continue
        if slot in consumed_chain_slots:
            continue  # part of a chain we already rendered
        program = programs[slot]
        summary = renderer.summarize_program(program)
        rows.append({
            "slot": slot,
            "last_slot": slot,
            "kind": "compact",
            "trigger_type": _classify_trigger(program),
            "summary": _tokens_to_json(summary),
            "references": _extract_references(summary),
            "condition_count": (1 if program.cond else 0) + (1 if program.cond2 else 0),
            "action_count": 1,
        })

    # Filtering happens after rendering so the filter UI can use the
    # final, name-resolved text. Trade-off: O(N) per request even when
    # filters narrow the result; with N ≤ 1500 this is fine in practice.
    trigger_types: list[str] | None = msg.get("trigger_types")
    references_entity: str | None = msg.get("references_entity")
    search: str | None = msg.get("search")
    if search:
        search_lower = search.lower()
    filtered = []
    for row in rows:
        if trigger_types and row["trigger_type"] not in trigger_types:
            continue
        if references_entity and references_entity not in row["references"]:
            continue
        if search:
            row_text = "".join(
                tok["t"] for tok in row["summary"] if tok.get("k") != "newline"
            ).lower()
            if search_lower not in row_text:
                continue
        filtered.append(row)

    total = len(rows)
    filtered_total = len(filtered)
    offset = msg.get("offset", 0)
    limit = msg.get("limit", 200)
    page = filtered[offset : offset + limit]

    connection.send_result(msg["id"], {
        "programs": page,
        "total": total,
        "filtered_total": filtered_total,
        "offset": offset,
        "limit": limit,
        # First slot after everything in use; None when the table is full.
        "next_free_slot": _next_free_slot(programs),
        # The panel script compares this with the version it was built
        # for and stops offering changes if they differ.
        "version": await _integration_version(hass),
        "can_write": PROGRAM_WRITES_ENABLED,
        "can_edit_chains": PROGRAM_WRITES_ENABLED and PROGRAM_CHAIN_WRITES_ENABLED,
        "can_fire": PROGRAM_WRITES_ENABLED and PROGRAM_FIRE_ENABLED,
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/get",
        vol.Required("entry_id"): str,
        vol.Required("slot"): vol.All(int, vol.Range(min=1, max=1500)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_get_program(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Full structured-English detail for one slot.

    If the requested slot is the head of a clausal chain we return the
    rendered chain; if it's a continuation slot (an AND/OR/THEN in the
    middle of a chain) we still return the chain that contains it, so
    the frontend always shows the complete program even when the user
    clicks an interior slot.
    """
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return

    renderer = _build_renderer(coordinator)
    programs = coordinator.data.programs if coordinator.data else {}
    target = programs.get(msg["slot"])
    if target is None:
        connection.send_error(msg["id"], "not_found", "no program at that slot")
        return

    chains = build_chains(tuple(programs.values()))
    containing_chain: ClausalChain | None = None
    for chain in chains:
        members = (
            (chain.head,) + chain.conditions + chain.actions
        )
        if any(m.slot == msg["slot"] for m in members):
            containing_chain = chain
            break

    if containing_chain is not None:
        tokens = renderer.render_chain(containing_chain)
        connection.send_result(msg["id"], {
            "slot": containing_chain.head.slot,
            "kind": "chain",
            "trigger_type": _classify_trigger(containing_chain.head),
            "tokens": _tokens_to_json(tokens),
            "references": _extract_references(tokens),
            "chain_slots": [m.slot for m in members if m.slot is not None],
            # Per-member raw fields + role so the editor can render
            # an editable form for each line of the clausal chain.
            # role is "head" / "condition" / "action".
            "chain_members": [
                {
                    "slot": m.slot,
                    "role": (
                        "head" if m is containing_chain.head
                        else "action" if m in containing_chain.actions
                        else "condition"
                    ),
                    "fields": _program_to_fields(m),
                }
                for m in members if m.slot is not None
            ],
        })
        return

    tokens = renderer.render_program(target)
    connection.send_result(msg["id"], {
        "slot": msg["slot"],
        "kind": "compact",
        "trigger_type": _classify_trigger(target),
        "tokens": _tokens_to_json(tokens),
        "references": _extract_references(tokens),
        # Raw program fields for the editor to seed its form. The
        # rendered token stream is for *display*; the form needs the
        # underlying integer values to round-trip cleanly.
        "fields": _program_to_fields(target),
    })


async def _integration_version(hass: HomeAssistant) -> str:
    """This integration's version, as declared in manifest.json."""
    from homeassistant.loader import async_get_integration

    return str((await async_get_integration(hass, DOMAIN)).version)


def _next_free_slot(programs: dict[int, Program]) -> int | None:
    slot = max(programs, default=0) + 1
    return slot if slot <= 1500 else None


def _occupant_description(programs: dict[int, Program], slot: int) -> str:
    """Say what is in ``slot``, for a "slot is in use" refusal."""
    for chain in build_chains(tuple(programs[s] for s in sorted(programs))):
        members = [chain.head, *chain.conditions, *chain.actions]
        slots = [m.slot for m in members if m.slot is not None]
        if slot in slots and len(slots) > 1:
            return (
                f"slot {slot} is in use: it is line {slots.index(slot) + 1} of the "
                f"multi-line program in slots {min(slots)}-{max(slots)}"
            )
    return f"slot {slot} is in use by another program"


def _refuse_write(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    *,
    enabled: bool | None = None,
) -> bool:
    """Send a ``read_only`` error and return True if this write is disabled."""
    if PROGRAM_WRITES_ENABLED and (enabled is None or enabled):
        return False
    connection.send_error(
        msg["id"], "read_only", "this kind of program change is not enabled",
    )
    return True


# Compact programs: one record is the whole program.
_SINGLE_LINE_TYPES: frozenset[int] = frozenset({
    int(ProgramType.TIMED), int(ProgramType.EVENT), int(ProgramType.YEARLY),
})

# Returned by _read_slot when it has already sent an error.
_FAILED: Any = object()


async def _read_slot(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    coordinator: "OmniDataUpdateCoordinator",
    slot: int,
) -> Any:
    """Read ``slot`` from the panel; sends the error and returns _FAILED."""
    try:
        return await async_get_program_changes(hass, coordinator).async_read(slot)
    except NotImplementedError as err:
        connection.send_error(msg["id"], "not_supported", str(err))
    except RuntimeError as err:
        connection.send_error(msg["id"], "not_connected", str(err))
    except Exception as err:
        connection.send_error(msg["id"], "read_failed", str(err))
    return _FAILED


async def _apply_change(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    coordinator: "OmniDataUpdateCoordinator",
    action: str,
    changes: list[SlotChange],
    *,
    expect_before: dict[int, set[bytes]] | None = None,
    conflict_message: str | None = None,
) -> dict[str, Any] | None:
    """Apply a journalled change; on failure send the error and return None."""
    try:
        entry = await async_get_program_changes(hass, coordinator).async_apply(
            action, changes, user_id=connection.user.id,
            expect_before=expect_before,
        )
        # None means the panel already held exactly this: still a success.
        return entry if entry is not None else {"id": None, "changes": []}
    except ProgramChangeError as err:
        message = conflict_message if err.code == "conflict" and conflict_message else str(err)
        connection.send_error(
            msg["id"], "invalid" if conflict_message and err.code == "conflict" else err.code,
            message,
        )
    except RuntimeError as err:
        connection.send_error(msg["id"], "not_connected", str(err))
    return None


def _program_to_fields(program: Program) -> dict[str, Any]:
    """Serialise a Program for the editor form. Mirrors the field
    layout of :func:`_PROGRAM_FIELD_SCHEMA` so a round-trip
    fetch → edit → save is straightforward.
    """
    return {
        "prog_type": program.prog_type,
        "cond": program.cond,
        "cond2": program.cond2,
        "cmd": program.cmd,
        "par": program.par,
        "pr2": program.pr2,
        "month": program.month,
        "day": program.day,
        "days": program.days,
        "hour": program.hour,
        "minute": program.minute,
        "remark_id": program.remark_id,
    }


_PROGRAM_FIELD_SCHEMA = vol.Schema(
    {
        vol.Required("prog_type"): vol.All(int, vol.Range(min=0, max=10)),
        vol.Optional("cond", default=0): vol.All(int, vol.Range(min=0, max=0xFFFF)),
        vol.Optional("cond2", default=0): vol.All(int, vol.Range(min=0, max=0xFFFF)),
        vol.Optional("cmd", default=0): vol.All(int, vol.Range(min=0, max=0xFF)),
        vol.Optional("par", default=0): vol.All(int, vol.Range(min=0, max=0xFF)),
        vol.Optional("pr2", default=0): vol.All(int, vol.Range(min=0, max=0xFFFF)),
        vol.Optional("month", default=0): vol.All(int, vol.Range(min=0, max=0xFF)),
        vol.Optional("day", default=0): vol.All(int, vol.Range(min=0, max=0xFF)),
        vol.Optional("days", default=0): vol.All(int, vol.Range(min=0, max=0xFF)),
        vol.Optional("hour", default=0): vol.All(int, vol.Range(min=0, max=0xFF)),
        vol.Optional("minute", default=0): vol.All(int, vol.Range(min=0, max=0xFF)),
        vol.Optional("remark_id"): vol.Any(None, vol.All(int, vol.Range(min=0))),
    },
    extra=vol.PREVENT_EXTRA,
)


_BLOCK_HEAD_TYPES: frozenset[int] = frozenset({
    int(ProgramType.WHEN), int(ProgramType.AT), int(ProgramType.EVERY),
})
_BLOCK_CONDITION_TYPES: frozenset[int] = frozenset({
    int(ProgramType.AND), int(ProgramType.OR),
})
_BLOCK_BODY_TYPES: frozenset[int] = _BLOCK_CONDITION_TYPES | {int(ProgramType.THEN)}


async def _read_block(
    hass: HomeAssistant,
    coordinator: "OmniDataUpdateCoordinator",
    head_slot: int,
) -> list[Program]:
    """Read the multi-line block starting at ``head_slot`` from the panel.

    Returns its records in slot order, or an empty list if that slot
    does not hold a block's first line. The panel is the authority here,
    not the cached table.
    """
    changes = async_get_program_changes(hass, coordinator)
    head = await changes.async_read(head_slot)
    if head is None or head.prog_type not in _BLOCK_HEAD_TYPES:
        return []
    block = [head]
    slot = head_slot + 1
    while slot <= 1500:
        record = await changes.async_read(slot)
        if record is None or record.prog_type not in _BLOCK_BODY_TYPES:
            break
        block.append(record)
        slot += 1
    return block


async def _first_free_run(
    hass: HomeAssistant, coordinator: "OmniDataUpdateCoordinator", length: int,
) -> int | None:
    """First slot of ``length`` free slots after everything in use."""
    programs = coordinator.data.programs if coordinator.data else {}
    start = _next_free_slot(programs)
    if start is None or start + length - 1 > 1500:
        return None
    changes = async_get_program_changes(hass, coordinator)
    for slot in range(start, start + length):
        if await changes.async_read(slot) is not None:
            return None
    return start


def _block_records(
    msg: dict[str, Any],
) -> tuple[Program, list[Program], list[Program]]:
    """Validate a block payload; raises ``vol.Invalid`` with a clear message."""
    head = Program(**_PROGRAM_FIELD_SCHEMA(msg["head"]))
    conditions = [Program(**_PROGRAM_FIELD_SCHEMA(c)) for c in msg["conditions"]]
    actions = [Program(**_PROGRAM_FIELD_SCHEMA(a)) for a in msg["actions"]]
    if head.prog_type not in _BLOCK_HEAD_TYPES:
        raise vol.Invalid("the first line must be a WHEN, AT or EVERY record")
    if any(c.prog_type not in _BLOCK_CONDITION_TYPES for c in conditions):
        raise vol.Invalid("conditions must be AND or OR records")
    if any(a.prog_type != int(ProgramType.THEN) for a in actions):
        raise vol.Invalid("actions must be THEN records")
    if not actions:
        raise vol.Invalid("a multi-line program needs at least one action")
    return head, conditions, actions


def _at_slots(records: list[Program], first_slot: int) -> list[SlotChange]:
    return [
        SlotChange(
            first_slot + i,
            Program.from_wire_bytes(r.encode_wire_bytes(), slot=first_slot + i),
        )
        for i, r in enumerate(records)
    ]


async def _run_block_command(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any], work: Any,
) -> None:
    """Run a block handler body, turning its failures into WS errors."""
    try:
        await work()
    except ProgramChangeError as err:
        connection.send_error(msg["id"], err.code, str(err))
    except NotImplementedError as err:
        connection.send_error(msg["id"], "not_supported", str(err))
    except RuntimeError as err:
        connection.send_error(msg["id"], "not_connected", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/chain/write",
        vol.Required("entry_id"): str,
        vol.Required("head_slot"): vol.All(int, vol.Range(min=1, max=1500)),
        vol.Required("head"): dict,        # WHEN / AT / EVERY program dict
        vol.Required("conditions"): [dict],
        vol.Required("actions"): [dict],
        # The records the editor loaded, in slot order, so a block that
        # changed on the panel in the meantime is not overwritten.
        vol.Optional("original"): [dict],
        # Allow moving the block to free slots when it no longer fits.
        vol.Optional("relocate", default=False): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_chain_write(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Rewrite a multi-line block (trigger + conditions + actions).

    The block occupies consecutive slots from ``head_slot``. If the new
    version is longer and the slots after it are in use, the write is
    refused with ``no_room`` unless ``relocate`` is set, in which case
    the block moves to the first free slots after everything in use.
    A shorter version frees the slots it no longer needs.
    """
    if _refuse_write(connection, msg, enabled=PROGRAM_CHAIN_WRITES_ENABLED):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    try:
        head, conditions, actions = _block_records(msg)
        original = [
            Program(**_PROGRAM_FIELD_SCHEMA(o)) for o in msg.get("original", [])
        ]
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid", f"bad program: {err}")
        return
    head_slot = msg["head_slot"]
    new_records = [head, *conditions, *actions]

    async def work() -> None:
        existing = await _read_block(hass, coordinator, head_slot)
        if not existing:
            raise ProgramChangeError(
                "not_found", f"no multi-line program starts at slot {head_slot}",
            )
        if "original" in msg and [r.encode_wire_bytes() for r in existing] != [
            r.encode_wire_bytes() for r in original
        ]:
            raise ProgramChangeError(
                "conflict",
                "this program changed on the panel since it was loaded; "
                "reload and try again",
            )
        old_slots = [r.slot for r in existing]
        changes_api = async_get_program_changes(hass, coordinator)

        target = head_slot
        blocked: int | None = None
        for slot in range(head_slot + len(existing), head_slot + len(new_records)):
            if slot > 1500 or await changes_api.async_read(slot) is not None:
                blocked = slot
                break
        if blocked is not None:
            free = await _first_free_run(hass, coordinator, len(new_records))
            if not msg["relocate"] or free is None:
                where = (
                    f" It can be moved to slots {free}-{free + len(new_records) - 1}."
                    if free is not None else " There is no free run of slots for it."
                )
                raise ProgramChangeError(
                    "no_room",
                    f"this program now needs {len(new_records)} slots but slot "
                    f"{blocked} is in use.{where}",
                )
            target = free

        changes = _at_slots(new_records, target)
        new_slots = {c.slot for c in changes}
        changes += [SlotChange(s, None) for s in old_slots if s not in new_slots]
        await changes_api.async_apply(
            "edit_chain", changes, user_id=connection.user.id, block=True,
            expect_before={
                r.slot: {r.encode_wire_bytes()} for r in existing
            },
        )
        connection.send_result(msg["id"], {
            "head_slot": target,
            "written_slots": sorted(new_slots),
            "cleared_slots": sorted(s for s in old_slots if s not in new_slots),
        })

    await _run_block_command(connection, msg, work)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/chain/clear",
        vol.Required("entry_id"): str,
        vol.Required("head_slot"): vol.All(int, vol.Range(min=1, max=1500)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_chain_clear(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a whole multi-line block, every line of it."""
    if _refuse_write(connection, msg, enabled=PROGRAM_CHAIN_WRITES_ENABLED):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    head_slot = msg["head_slot"]

    async def work() -> None:
        existing = await _read_block(hass, coordinator, head_slot)
        if not existing:
            raise ProgramChangeError(
                "not_found", f"no multi-line program starts at slot {head_slot}",
            )
        await async_get_program_changes(hass, coordinator).async_apply(
            "clear_chain", [SlotChange(r.slot, None) for r in existing],
            user_id=connection.user.id, block=True,
        )
        connection.send_result(msg["id"], {
            "head_slot": head_slot,
            "cleared_slots": [r.slot for r in existing],
        })

    await _run_block_command(connection, msg, work)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/chain/clone",
        vol.Required("entry_id"): str,
        vol.Required("source_slot"): vol.All(int, vol.Range(min=1, max=1500)),
        vol.Required("target_slot"): vol.All(int, vol.Range(min=1, max=1500)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_chain_clone(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Copy a whole multi-line block to free slots from ``target_slot``."""
    if _refuse_write(connection, msg, enabled=PROGRAM_CHAIN_WRITES_ENABLED):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    src = msg["source_slot"]
    dst = msg["target_slot"]

    async def work() -> None:
        existing = await _read_block(hass, coordinator, src)
        if not existing:
            raise ProgramChangeError(
                "not_found", f"no multi-line program starts at slot {src}",
            )
        last = dst + len(existing) - 1
        programs = coordinator.data.programs if coordinator.data else {}
        next_free = _next_free_slot(programs)
        hint = f"; the next free slot is {next_free}" if next_free else ""
        if last > 1500:
            raise ProgramChangeError(
                "invalid", f"slots {dst}-{last} run past slot 1500{hint}",
            )
        changes_api = async_get_program_changes(hass, coordinator)
        for slot in range(dst, last + 1):
            if await changes_api.async_read(slot) is not None:
                raise ProgramChangeError(
                    "invalid",
                    f"this program needs slots {dst}-{last} but "
                    f"{_occupant_description(programs, slot)}{hint}",
                )
        await changes_api.async_apply(
            "clone_chain", _at_slots(existing, dst),
            user_id=connection.user.id, block=True,
            expect_before={s: {bytes(PROGRAM_BYTES)} for s in range(dst, last + 1)},
        )
        connection.send_result(msg["id"], {
            "source_slot": src, "target_slot": dst,
            "written_slots": list(range(dst, last + 1)),
        })

    await _run_block_command(connection, msg, work)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/objects/list",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_list_objects(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return discovered objects so the frontend editor can populate
    object pickers (zone / unit / area / thermostat / button).

    Returns a flat dict mapping each kind to a list of
    ``{index, name}`` entries in slot order. Cached client-side after
    the first call — the topology doesn't change unless the user
    reloads the integration.
    """
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    data = coordinator.data
    if data is None:
        connection.send_result(msg["id"], {})
        return

    def _flatten(bucket) -> list[dict[str, Any]]:
        return [
            {"index": idx, "name": getattr(obj, "name", "") or f"slot {idx}"}
            for idx, obj in sorted(bucket.items())
        ]

    connection.send_result(msg["id"], {
        "zones": _flatten(data.zones),
        "units": _flatten(data.units),
        "areas": _flatten(data.areas),
        "thermostats": _flatten(data.thermostats),
        "buttons": _flatten(data.buttons),
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/write",
        vol.Required("entry_id"): str,
        vol.Required("slot"): vol.All(int, vol.Range(min=1, max=1500)),
        vol.Required("program"): dict,
        # The fields the editor loaded, so a slot that changed on the
        # panel in the meantime is not silently overwritten.
        vol.Optional("original"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_write_program(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Write a single-line (TIMED / EVENT / YEARLY) program to ``slot``.

    The ``program`` payload is a JSON-friendly dict mirroring the
    bundled :class:`Program` dataclass — every field passed by name,
    default 0 for fields the caller omits. The slot must be free or
    already hold a single-line program; multi-line blocks are edited
    through ``programs/chain/write``.
    """
    if _refuse_write(connection, msg):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    slot = msg["slot"]
    try:
        validated = _PROGRAM_FIELD_SCHEMA(msg["program"])
        original = (
            _PROGRAM_FIELD_SCHEMA(msg["original"]) if "original" in msg else None
        )
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid", f"bad program payload: {err}")
        return
    program = Program(slot=slot, **validated)
    if program.prog_type not in _SINGLE_LINE_TYPES:
        connection.send_error(
            msg["id"], "invalid",
            "only single-line (timed / event / yearly) programs can be written here",
        )
        return
    current = await _read_slot(hass, connection, msg, coordinator, slot)
    if current is _FAILED:
        return
    if current is not None and current.prog_type not in _SINGLE_LINE_TYPES:
        connection.send_error(
            msg["id"], "invalid",
            f"slot {slot} is part of a multi-line program",
        )
        return
    expect = None
    if original is not None:
        expect = {slot: {Program(slot=slot, **original).encode_wire_bytes()}}
    if await _apply_change(
        hass, connection, msg, coordinator, "edit",
        [SlotChange(slot, program)], expect_before=expect,
    ) is None:
        return
    connection.send_result(msg["id"], {"slot": slot, "written": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/clear",
        vol.Required("entry_id"): str,
        vol.Required("slot"): vol.All(int, vol.Range(min=1, max=1500)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_clear_program(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete the single-line program in ``slot``.

    Refused for a record of a multi-line block: freeing one slot of a
    block would leave its other lines behind.
    """
    if _refuse_write(connection, msg):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    slot = msg["slot"]
    current = await _read_slot(hass, connection, msg, coordinator, slot)
    if current is _FAILED:
        return
    if current is None:
        connection.send_error(msg["id"], "not_found", f"slot {slot} is already empty")
        return
    if current.prog_type not in _SINGLE_LINE_TYPES:
        connection.send_error(
            msg["id"], "invalid", f"slot {slot} is part of a multi-line program",
        )
        return
    if await _apply_change(
        hass, connection, msg, coordinator, "clear", [SlotChange(slot, None)],
        expect_before={slot: {current.encode_wire_bytes()}},
    ) is None:
        return
    connection.send_result(msg["id"], {"slot": slot, "cleared": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/clone",
        vol.Required("entry_id"): str,
        vol.Required("source_slot"): vol.All(int, vol.Range(min=1, max=1500)),
        vol.Required("target_slot"): vol.All(int, vol.Range(min=1, max=1500)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_clone_program(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Copy the single-line program in ``source_slot`` to a free slot."""
    if _refuse_write(connection, msg):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    src = msg["source_slot"]
    dst = msg["target_slot"]
    if src == dst:
        connection.send_error(
            msg["id"], "invalid", "source and target slots must differ",
        )
        return
    source_program = await _read_slot(hass, connection, msg, coordinator, src)
    if source_program is _FAILED:
        return
    if source_program is None:
        connection.send_error(
            msg["id"], "not_found", f"no program at source slot {src}",
        )
        return
    if source_program.prog_type not in _SINGLE_LINE_TYPES:
        connection.send_error(
            msg["id"], "invalid", f"slot {src} is part of a multi-line program",
        )
        return
    cloned = Program.from_wire_bytes(source_program.encode_wire_bytes(), slot=dst)
    programs = coordinator.data.programs if coordinator.data else {}
    next_free = _next_free_slot(programs)
    hint = f"; the next free slot is {next_free}" if next_free else ""
    if await _apply_change(
        hass, connection, msg, coordinator, "clone", [SlotChange(dst, cloned)],
        expect_before={dst: {bytes(PROGRAM_BYTES)}},
        conflict_message=(
            f"target slot {dst} is not free ({_occupant_description(programs, dst)})"
            f"{hint}"
        ),
    ) is None:
        return
    connection.send_result(
        msg["id"], {"source_slot": src, "target_slot": dst, "cloned": True},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/history",
        vol.Required("entry_id"): str,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=200)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_program_history(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """The journal of changes made through this panel, newest first."""
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    changes = async_get_program_changes(hass, coordinator)
    entries = await changes.async_history(msg.get("limit", 50))
    connection.send_result(msg["id"], {"entries": entries})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/undo",
        vol.Required("entry_id"): str,
        vol.Required("journal_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_program_undo(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Put back what one journalled change replaced."""
    if _refuse_write(connection, msg):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    changes = async_get_program_changes(hass, coordinator)
    try:
        entry = await changes.async_undo(
            msg["journal_id"], user_id=connection.user.id,
        )
    except ProgramChangeError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    except RuntimeError as err:
        connection.send_error(msg["id"], "not_connected", str(err))
        return
    connection.send_result(msg["id"], {"entry": entry})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "omni_pca/programs/fire",
        vol.Required("entry_id"): str,
        vol.Required("slot"): vol.All(int, vol.Range(min=1, max=1500)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_fire_program(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Ask the panel to execute a program right now.

    Sends ``Command(EXECUTE_PROGRAM, parameter2=slot)`` via the
    coordinator's :class:`OmniClient`. The panel acks; any state
    changes the program triggers come back as ordinary push events.
    """
    if _refuse_write(connection, msg, enabled=PROGRAM_FIRE_ENABLED):
        return
    coordinator = _coordinator_for_entry(hass, msg["entry_id"])
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "panel not configured")
        return
    try:
        client = coordinator.client
    except RuntimeError as err:
        connection.send_error(msg["id"], "not_connected", str(err))
        return
    try:
        await client.execute_command(Command.EXECUTE_PROGRAM, parameter2=msg["slot"])
    except Exception as err:
        connection.send_error(msg["id"], "fire_failed", str(err))
        return
    connection.send_result(msg["id"], {"slot": msg["slot"], "fired": True})


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Idempotently register the program-viewer websocket commands.

    Called from the integration's ``async_setup_entry``; safe to call
    once per HA boot. HA's ``websocket_api.async_register_command`` is
    itself idempotent so a stray double-call from reload paths is fine.
    """
    websocket_api.async_register_command(hass, _ws_list_programs)
    websocket_api.async_register_command(hass, _ws_get_program)
    websocket_api.async_register_command(hass, _ws_fire_program)
    websocket_api.async_register_command(hass, _ws_clear_program)
    websocket_api.async_register_command(hass, _ws_clone_program)
    websocket_api.async_register_command(hass, _ws_write_program)
    websocket_api.async_register_command(hass, _ws_chain_write)
    websocket_api.async_register_command(hass, _ws_chain_clear)
    websocket_api.async_register_command(hass, _ws_chain_clone)
    websocket_api.async_register_command(hass, _ws_list_objects)
    websocket_api.async_register_command(hass, _ws_program_history)
    websocket_api.async_register_command(hass, _ws_program_undo)


# --------------------------------------------------------------------------
# Side-panel registration
# --------------------------------------------------------------------------


# Where the integration serves the bundled panel JS from. Phase C builds
# the actual ESM bundle and drops it at ``custom_components/omni_pca/
# www/panel.js`` — we register a static path so HA serves it at
# ``/api/omni_pca/panel.js``.
_PANEL_FRONTEND_URL: str = "omni-panel-programs"
_PANEL_WEBCOMPONENT: str = "omni-panel-programs"
_PANEL_JS_PATH: str = "/api/omni_pca/panel.js"


async def async_register_side_panel(hass: HomeAssistant) -> None:
    """Register the sidebar entry that hosts the program viewer.

    The bundled panel JS is served from the integration's ``www/``
    directory via a registered static path. Until Phase C ships the
    bundle, the panel registration still appears (HA shows a generic
    loader) so the wiring can be exercised end-to-end.
    """
    from pathlib import Path

    from homeassistant.components.frontend import (
        async_remove_panel,
    )
    from homeassistant.components.panel_custom import async_register_panel

    # Serve <integration>/www/panel.js at /api/omni_pca/panel.js.
    www_dir = Path(__file__).parent / "www"
    www_dir.mkdir(exist_ok=True)
    panel_js = www_dir / "panel.js"
    if not panel_js.exists():
        # Stub so the static path resolves even before Phase C builds the
        # real bundle. The stub renders a "panel coming soon" message so
        # users on dev installs see something useful rather than 404.
        panel_js.write_text(_STUB_PANEL_JS)
    await hass.http.async_register_static_paths(
        [_StaticPathConfig(_PANEL_JS_PATH, str(panel_js), False)]
    )

    # async_remove_panel before re-register so reload doesn't duplicate.
    try:
        async_remove_panel(hass, _PANEL_FRONTEND_URL)
    except Exception:
        pass
    await async_register_panel(
        hass,
        frontend_url_path=_PANEL_FRONTEND_URL,
        webcomponent_name=_PANEL_WEBCOMPONENT,
        sidebar_title="Omni Programs",
        sidebar_icon="mdi:script-text-outline",
        # The version in the URL makes a browser fetch the script again
        # after every upgrade instead of running a cached older editor
        # against a newer server.
        module_url=f"{_PANEL_JS_PATH}?v={await _integration_version(hass)}",
        embed_iframe=False,
        require_admin=True,
    )


_STUB_PANEL_JS: str = """\
// omni-pca side panel — stub until Phase C frontend lands.
class OmniPanelPrograms extends HTMLElement {
  set hass(hass) {
    if (!this._rendered) {
      this.innerHTML = `
        <style>
          :host, .root { display: block; padding: 24px; font-family: sans-serif; }
          h1 { font-size: 1.25rem; margin: 0 0 8px; }
          p  { color: #666; margin: 0; }
        </style>
        <div class="root">
          <h1>Omni Programs</h1>
          <p>Frontend bundle not yet installed.
             Phase C of the program viewer will populate this panel.</p>
        </div>`;
      this._rendered = true;
    }
  }
}
customElements.define('omni-panel-programs', OmniPanelPrograms);
"""


# Late import: HA's StaticPathConfig moved in 2024.7. The integration is
# pinned to a current HA release so this import works, but importing at
# module top would force the dep on tests that don't need the panel.
from homeassistant.components.http import StaticPathConfig as _StaticPathConfig  # noqa: E402
