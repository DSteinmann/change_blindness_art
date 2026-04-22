"""Cycling prompt bank with per-sector overrides."""
from __future__ import annotations

import json
from pathlib import Path

from sectors import sector_name


class PromptBank:
    """
    Order of resolution for a sector:
      1. sector-specific prompts from sector_prompts.json (cycling if list)
      2. global cycling prompts from prompts.txt
      3. default prompts from sector_prompts.json (cycling)
      4. hard-coded fallback string
    Each sector advances its own index independently.
    """

    FALLBACK = "add more detail, photorealistic, seamless blend"

    def __init__(self, prompts_file: Path, sector_prompts_file: Path):
        self.prompts_file = prompts_file
        self.sector_prompts_file = sector_prompts_file
        self.prompts: list[str] = []
        self.prompt_index = 0
        self.sector_prompts: dict = {}
        self.sector_prompt_indices: dict[str, int] = {}
        self.default_prompts: list[str] = [self.FALLBACK]
        self.default_prompt_index = 0

    def load(self) -> None:
        self._load_cycling_prompts()
        self._load_sector_prompts()

    def _load_cycling_prompts(self) -> None:
        if not self.prompts_file.exists():
            self.prompts = [
                "add one more element to the image, photorealistic, high detail",
                "enhance the peripheral area, photorealistic, seamless blend",
            ]
            print(f"Using {len(self.prompts)} default cycling prompts")
            return
        with self.prompts_file.open() as f:
            self.prompts = [
                line.strip() for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]
        print(f"Loaded {len(self.prompts)} cycling prompts from {self.prompts_file}")

    def _load_sector_prompts(self) -> None:
        if not self.sector_prompts_file.exists():
            print(f"No sector prompts file at {self.sector_prompts_file}")
            return
        try:
            data = json.loads(self.sector_prompts_file.read_text())
        except Exception as e:
            print(f"Error loading sector prompts: {e}")
            return
        self.sector_prompts = data.get("sectors", {})
        default_val = data.get("default", self.default_prompts)
        self.default_prompts = [default_val] if isinstance(default_val, str) else list(default_val)
        self.sector_prompt_indices = {k: 0 for k in self.sector_prompts}
        print(f"Loaded {len(self.sector_prompts)} sector-specific prompts")

    def for_sector(self, row: int, col: int) -> str:
        name = sector_name(row, col)
        sector_data = self.sector_prompts.get(name)
        if isinstance(sector_data, list) and sector_data:
            idx = self.sector_prompt_indices.get(name, 0)
            self.sector_prompt_indices[name] = (idx + 1) % len(sector_data)
            print(f"Sector {name}: using prompt {idx + 1}/{len(sector_data)}")
            return sector_data[idx]
        if isinstance(sector_data, str):
            return sector_data
        if self.prompts:
            return self.next_cycling()
        if self.default_prompts:
            prompt = self.default_prompts[self.default_prompt_index]
            self.default_prompt_index = (self.default_prompt_index + 1) % len(self.default_prompts)
            return prompt
        return self.FALLBACK

    def next_cycling(self) -> str:
        if not self.prompts:
            return "photorealistic, high detail"
        prompt = self.prompts[self.prompt_index]
        self.prompt_index = (self.prompt_index + 1) % len(self.prompts)
        return prompt

    def reset(self) -> None:
        self.prompt_index = 0
        self.default_prompt_index = 0
        for k in self.sector_prompt_indices:
            self.sector_prompt_indices[k] = 0
