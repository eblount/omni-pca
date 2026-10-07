"""Read and write panel programs using the bundled program codec.

The ``omni-pca`` library Home Assistant installs decodes program records
with the wrong byte order for a real panel and has no way to write one,
so the integration does both here instead:

* Reads still go through the library's ``iter_programs`` (it knows the
  v1 and v2 enumeration dances), but each record is re-decoded from its
  original bytes with the bundled :class:`Program`.
* Writes send ``DownloadProgram`` on the client's connection directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from omni_pca.client import OmniClient
from omni_pca.commands import CommandFailedError
from omni_pca.connection import ConnectionError as OmniConnectionError
from omni_pca.opcodes import OmniLink2MessageType

from .bundled.programs import MAX_PROGRAMS, PROGRAM_BYTES, Program, ProgramType


def program_from_library_wire(lib_program: Any) -> Program:
    """Re-decode a Program the library read off the wire.

    The library's wire encoder is the exact inverse of its decoder, so
    encoding gives back the 14 bytes the panel sent.
    """
    return Program.from_wire_bytes(
        lib_program.encode_wire_bytes(), slot=lib_program.slot
    )


def program_from_library_file(lib_program: Any) -> Program:
    """Convert a Program the library parsed out of a ``.pca`` file."""
    return Program.from_file_record(
        lib_program.encode_file_record(), slot=lib_program.slot
    )


async def async_iter_programs(client: Any) -> AsyncIterator[Program]:
    """Yield every defined program on the panel, in slot order."""
    async for lib_program in client.iter_programs():
        yield program_from_library_wire(lib_program)


def _require_v2(client: Any) -> None:
    if not isinstance(client, OmniClient):
        raise NotImplementedError(
            "this panel connection can't read or write a single program; "
            "use a TCP (Omni-Link II) connection for editing"
        )


async def async_read_program(client: Any, slot: int) -> Program | None:
    """Read one slot straight from the panel; ``None`` if it is free.

    Asks for "the next defined program after ``slot - 1``", the same
    request the full enumeration is built from, and checks the answer
    is ``slot`` itself.
    """
    if not 1 <= slot <= MAX_PROGRAMS:
        raise ValueError(f"program slot {slot} out of range 1..{MAX_PROGRAMS}")
    _require_v2(client)
    after = slot - 1
    reply = await client._conn.request(  # noqa: SLF001 - no public single read
        OmniLink2MessageType.UploadProgram,
        bytes([(after >> 8) & 0xFF, after & 0xFF, 1]),
    )
    if reply.opcode == int(OmniLink2MessageType.EOD):
        return None
    if reply.opcode != int(OmniLink2MessageType.ProgramData):
        raise OmniConnectionError(
            f"unexpected opcode {reply.opcode} reading program slot {slot}"
        )
    if len(reply.payload) < 2 + PROGRAM_BYTES:
        raise OmniConnectionError(
            f"ProgramData payload too short ({len(reply.payload)} bytes)"
        )
    if ((reply.payload[0] << 8) | reply.payload[1]) != slot:
        return None
    program = Program.from_wire_bytes(
        reply.payload[2 : 2 + PROGRAM_BYTES], slot=slot
    )
    return None if program.is_empty() else program


async def async_write_program(client: Any, slot: int, program: Program) -> None:
    """Write ``program`` into ``slot`` (1-based) on the panel.

    Raises :class:`NotImplementedError` for a v1 (UDP) connection, whose
    protocol can only replace the whole program table at once.
    """
    if not 1 <= slot <= MAX_PROGRAMS:
        raise ValueError(f"program slot {slot} out of range 1..{MAX_PROGRAMS}")
    _require_v2(client)
    body = program.encode_wire_bytes()
    if len(body) != PROGRAM_BYTES:
        raise ValueError(
            f"encoded program body must be {PROGRAM_BYTES} bytes, got {len(body)}"
        )
    payload = bytes([(slot >> 8) & 0xFF, slot & 0xFF]) + body
    reply = await client._conn.request(  # noqa: SLF001 - no public write API
        OmniLink2MessageType.DownloadProgram, payload
    )
    if reply.opcode == int(OmniLink2MessageType.Nak):
        raise CommandFailedError(f"panel NAK'd DownloadProgram for slot {slot}")
    if reply.opcode != int(OmniLink2MessageType.Ack):
        raise OmniConnectionError(
            f"unexpected opcode {reply.opcode} after DownloadProgram "
            f"(expected {int(OmniLink2MessageType.Ack)})"
        )


async def async_clear_program(client: Any, slot: int) -> None:
    """Free ``slot`` by writing an all-zero record to it."""
    await async_write_program(
        client, slot, Program(slot=slot, prog_type=int(ProgramType.FREE))
    )
