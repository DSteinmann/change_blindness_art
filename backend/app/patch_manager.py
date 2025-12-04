from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class PatchManager:
    """Lightweight helper that rotates through pre-generated peripheral patches."""

    def __init__(self, assets_root: Path) -> None:
        self.assets_root = assets_root
        self._lock = asyncio.Lock()
        self._manifest: list[dict[str, Any]] = []
        self._cursor = 0

    async def load(self) -> None:
        manifest_file = self.assets_root / "manifest.json"
        if manifest_file.exists():
            self._manifest = json.loads(manifest_file.read_text())
        else:
            self._manifest = [
                {"id": path.stem, "url": f"/assets/{path.name}", "stimulus": "default"}
                for path in sorted(self.assets_root.glob("*.svg"))
            ]
        if not self._manifest:
            raise RuntimeError(
                "No patch assets found. Add SVG/PNG assets or a manifest.json inside ''%s''."
                % self.assets_root
            )

    async def next_patch(self, stimulus: str | None = None) -> dict[str, Any]:
        async with self._lock:
            if stimulus:
                filtered = [item for item in self._manifest if item.get("stimulus") == stimulus]
            else:
                filtered = self._manifest
            if not filtered:
                raise ValueError(f"No patches found for stimulus '{stimulus}'.")
            selection = filtered[self._cursor % len(filtered)]
            self._cursor += 1
            return selection
