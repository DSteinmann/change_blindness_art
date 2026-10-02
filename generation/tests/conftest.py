"""Shared fixtures for generation-service tests."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image


def _tiny_png(color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    img = Image.new("RGB", (16, 16), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def tiny_png_bytes() -> bytes:
    return _tiny_png()


@pytest.fixture
def tiny_png_b64(tiny_png_bytes: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(tiny_png_bytes).decode()}"


@pytest.fixture
def fake_openrouter_response(tiny_png_b64: str):
    """A minimal well-formed OpenRouter chat-completions payload."""
    def _factory(caption: str | None = "a new soap bubble in the corner") -> dict:
        content = f"CAPTION: {caption}" if caption is not None else "noop"
        return {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "images": [
                            {"type": "image_url", "image_url": {"url": tiny_png_b64}}
                        ],
                    }
                }
            ]
        }

    return _factory
