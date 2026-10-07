"""The committed panel bundle must be built for the manifest's version.

The panel script compares the version compiled into it with the one the
server reports and refuses to offer changes on a mismatch. A release
whose bundle was not rebuilt after the version bump would lock its own
editor, so catch that here.
"""

from __future__ import annotations

import json
from pathlib import Path

_INTEGRATION = Path(__file__).parent.parent / "custom_components" / "omni_pca"


def test_bundle_is_built_for_the_manifest_version() -> None:
    version = json.loads((_INTEGRATION / "manifest.json").read_text())["version"]
    bundle = (_INTEGRATION / "www" / "panel.js").read_text()
    assert f'"{version}"' in bundle, (
        f"www/panel.js was not built for version {version}; run "
        "`npm run build` in custom_components/omni_pca/frontend"
    )
