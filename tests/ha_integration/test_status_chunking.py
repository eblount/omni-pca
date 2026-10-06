"""Status polls must be split into windows a real panel can answer.

A real Omni IIe answers a 50-zone ExtendedStatus request but goes silent
on a 60-zone one, so the coordinator may never ask for ``1..max(index)``
in a single request.
"""

from __future__ import annotations

import pytest
from custom_components.omni_pca.coordinator import OmniDataUpdateCoordinator

from omni_pca.commands import CommandFailedError
from omni_pca.models import ObjectType


class _FakePanel:
    """Records every range asked for; NAKs ranges past ``last_valid``."""

    def __init__(self, last_valid: int = 0xFFFF) -> None:
        self.calls: list[tuple[int, int]] = []
        self._last_valid = last_valid

    async def fetch(
        self, object_type: ObjectType, start: int, end: int
    ) -> list[int]:
        self.calls.append((start, end))
        if end > self._last_valid:
            raise CommandFailedError("NAK")
        return list(range(start, end + 1))


async def _run(panel: _FakePanel, indices: list[int], chunk: int) -> list[int]:
    # The helper only touches its arguments, so no coordinator instance
    # (and no running Home Assistant) is needed.
    return await OmniDataUpdateCoordinator._fetch_status_chunked(
        None,  # type: ignore[arg-type]
        panel.fetch,
        ObjectType.ZONE,
        indices,
        chunk,
    )


async def test_sparse_indices_are_windowed_and_gaps_skipped() -> None:
    # Zone layout of the Omni IIe this was found on: 1..16 hardwired,
    # then scattered expansion zones up to 63.
    zones = [*range(1, 17), 33, 49, 50, 52, 54, 55, 56, 58, 59, 60, 62, 63]
    panel = _FakePanel()

    records = await _run(panel, zones, 32)

    assert panel.calls == [(1, 16), (33, 63)]
    assert all(end - start < 32 for start, end in panel.calls)
    assert set(zones) <= set(records)


async def test_no_window_exceeds_chunk() -> None:
    panel = _FakePanel()

    await _run(panel, list(range(1, 101)), 32)

    assert panel.calls == [(1, 32), (33, 64), (65, 96), (97, 100)]


async def test_nakd_window_falls_back_to_single_objects() -> None:
    # Eight areas discovered by the name fallback on a two-area panel:
    # the range request is NAK'd, the two real areas must still report.
    panel = _FakePanel(last_valid=2)

    records = await _run(panel, list(range(1, 9)), 8)

    assert records == [1, 2]
    assert panel.calls[0] == (1, 8)
    assert panel.calls[1:] == [(i, i) for i in range(1, 9)]


async def test_empty_indices_make_no_requests() -> None:
    panel = _FakePanel()

    assert await _run(panel, [], 32) == []
    assert panel.calls == []


@pytest.mark.parametrize("index", [1, 77])
async def test_single_nakd_object_is_skipped(index: int) -> None:
    panel = _FakePanel(last_valid=0)

    assert await _run(panel, [index], 32) == []
    assert panel.calls == [(index, index)]
