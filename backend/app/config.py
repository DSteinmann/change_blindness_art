from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass
class Settings:
    zmq_endpoint: str = os.getenv("ARIA_ZMQ_ENDPOINT", "tcp://127.0.0.1:5555")
    patch_dir: Path = Path(os.getenv("PATCH_ASSETS_DIR", "assets/patches")).resolve()
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080",
        ).split(",")
    )
    telemetry_history: int = int(os.getenv("TELEMETRY_HISTORY", "1024"))
    pupil_host: str = os.getenv("PUPIL_HOST", "127.0.0.1")
    pupil_remote_port: int = int(os.getenv("PUPIL_REMOTE_PORT", "50020"))
    pupil_topic: str = os.getenv("PUPIL_TOPIC", "gaze.")
    pupil_confidence_threshold: float = float(os.getenv("PUPIL_CONFIDENCE_THRESHOLD", "0.6"))
    pupil_surface_name: str = os.getenv("PUPIL_SURFACE_NAME", "screen")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.patch_dir.mkdir(parents=True, exist_ok=True)
    return settings
