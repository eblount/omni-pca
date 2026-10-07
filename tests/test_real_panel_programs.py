"""Check the wire codec and the renderer against real-panel record layouts.

The mock panel only ever echoes back what our own encoder produced, so
it can't tell us whether we agree with real hardware. Two sets of
records do that here:

* ``_SAMPLE`` — made-up programs laid out the way an Omni IIe (firmware
  3.2r2) was seen to send them. Always runs.
* ``fixtures/omni_iie_programs.json`` — a panel's actual program table,
  ``{"programs": {"<slot>": "<14 hex bytes>"}}``. It describes someone's
  house, so it is gitignored and those tests skip without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omni_pca.program_engine import build_chains
from omni_pca.program_renderer import ProgramRenderer, TokenKind
from omni_pca.programs import Program

_FIXTURE = Path(__file__).parent / "fixtures" / "omni_iie_programs.json"

# slot → 14-byte ProgramData body, as hex.
_SAMPLE: dict[int, str] = {
    # compact EVENT programs
    1: "02 00 00 00 00 01 69 00 09 06 15 00 00 01",
    2: "02 0b 2c 00 00 65 1e 28 06 04 03 00 00 01",
    3: "02 00 00 00 00 07 00 00 02 03 f1 00 00 01",
    # WHEN / AND / THEN / THEN
    4: "05 00 00 00 00 00 00 00 00 b2 00 00 00 00",
    5: "08 00 00 03 00 00 00 00 00 00 00 00 00 00",
    6: "0a 00 00 00 00 02 00 00 00 00 00 00 00 00",
    7: "0a 00 00 00 00 42 78 00 01 00 00 00 00 00",
    # AT / THEN
    8: "06 00 00 00 00 00 00 00 00 00 00 fe 1a 00",
    9: "0a 00 00 00 00 1d 00 00 04 00 00 00 00 00",
}
_SAMPLE_RENDERED: dict[int, str] = {
    1: "WHEN Zone 21 becomes not ready / THEN Turn ON Unit 9 for 5 min",
    2: (
        "WHEN Zone 3 becomes secure / AND IF Unit 300 is ON"
        " / THEN Set level Unit 6 to 40% for 30 sec"
    ),
    3: "WHEN ALL ON in Area 1 / THEN Execute button Button 2",
    4: (
        "WHEN Area 2 is set to Away / AND IF it is dark outside"
        " / THEN Turn ALL OFF / AND Set heat setpoint on Thermostat 1 to 68°F"
    ),
    8: "AT sunset every day / THEN UPB link ON 4",
}


class _NoNames:
    def name_of(self, kind: str, index: int) -> str | None:
        return None


_RENDERER = ProgramRenderer(names=_NoNames())


def _decode(table: dict[int, str]) -> dict[int, Program]:
    return {
        slot: Program.from_wire_bytes(bytes.fromhex(body), slot=slot)
        for slot, body in table.items()
    }


def _text(tokens) -> str:
    lines = "".join(
        "\n" if t.kind == TokenKind.NEWLINE else t.text for t in tokens
    )
    return " / ".join(line.strip() for line in lines.splitlines())


def _render(programs: dict[int, Program]) -> dict[int, str]:
    """Head slot → rendered text, for compact programs and chains alike."""
    out: dict[int, str] = {}
    for slot, p in programs.items():
        if not p.is_multi_record():
            out[slot] = _text(_RENDERER.render_program(p))
    for chain in build_chains(tuple(programs[s] for s in sorted(programs))):
        out[chain.head.slot] = _text(_RENDERER.render_chain(chain))
    return out


def _real_table() -> dict[int, str]:
    if not _FIXTURE.exists():
        pytest.skip(f"real-panel fixture not present: {_FIXTURE.name}")
    raw = json.loads(_FIXTURE.read_text())["programs"]
    return {int(slot): body for slot, body in raw.items()}


# ---- made-up records, real layouts ---------------------------------------


@pytest.mark.parametrize("slot", sorted(_SAMPLE))
def test_sample_wire_round_trip(slot: int) -> None:
    body = bytes.fromhex(_SAMPLE[slot])
    assert Program.from_wire_bytes(body, slot=slot).encode_wire_bytes() == body


def test_sample_fields_are_big_endian() -> None:
    p = _decode(_SAMPLE)[2]
    assert p.cond == 0x0B2C
    assert p.pr2 == 0x2806
    assert p.event_id == 0x0403


@pytest.mark.parametrize("slot", sorted(_SAMPLE_RENDERED))
def test_sample_render(slot: int) -> None:
    assert _render(_decode(_SAMPLE))[slot] == _SAMPLE_RENDERED[slot]


# ---- a real panel's table (skipped unless the fixture is present) --------


def test_real_table_round_trips() -> None:
    for slot, body in _real_table().items():
        raw = bytes.fromhex(body)
        assert Program.from_wire_bytes(raw, slot=slot).encode_wire_bytes() == raw


def test_real_table_every_record_belongs_to_a_program() -> None:
    programs = _decode(_real_table())
    chains = build_chains(tuple(programs[s] for s in sorted(programs)))
    in_chains = {
        m.slot for c in chains for m in (c.head, *c.conditions, *c.actions)
    }
    compact = {s for s, p in programs.items() if not p.is_multi_record()}
    assert in_chains | compact == set(programs)


def test_real_table_renders_without_raw_codes() -> None:
    for slot, text in _render(_decode(_real_table())).items():
        assert "event 0x" not in text, (slot, text)
        assert "command " not in text, (slot, text)
        assert "misc condition" not in text, (slot, text)
