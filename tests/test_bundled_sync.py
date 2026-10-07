"""The integration's bundled program modules must match ``src/omni_pca``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "dev" / "sync_bundled.py"


def test_bundled_copy_is_current() -> None:
    spec = importlib.util.spec_from_file_location("_sync_bundled", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stale = module.stale_files()
    assert not stale, (
        f"run `python dev/sync_bundled.py`; out of date: "
        f"{[p.name for p in stale]}"
    )
