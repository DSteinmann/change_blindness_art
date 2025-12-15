from __future__ import annotations

import time
from dataclasses import dataclass


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp value to inclusive range [lower, upper]."""
    return max(lower, min(upper, value))


@dataclass
class BlinkInference:
    state: str
    confidence: float
    filtered_value: float


class HysteresisBlinkDetector:
    """Exponential smoothing + hysteresis window for stable blink states."""

    def __init__(
        self,
        *,
        close_below: float,
        open_above: float,
        hold_ms: float = 120.0,
        ema_alpha: float = 0.3,
        initial_value: float | None = None,
    ) -> None:
        if open_above <= close_below:
            raise ValueError("open_above must be greater than close_below for blink hysteresis")
        self.close_below = close_below
        self.open_above = open_above
        self.hold_seconds = max(0.0, hold_ms) / 1000.0
        self.ema_alpha = max(0.0, min(1.0, ema_alpha))
        self.filtered_value = initial_value if initial_value is not None else open_above
        self.state = "open"
        self.last_change = time.monotonic()

    def update(self, sample_value: float) -> BlinkInference:
        weight = self.ema_alpha
        if weight == 0.0:
            self.filtered_value = sample_value
        else:
            self.filtered_value = (1.0 - weight) * self.filtered_value + weight * sample_value
        now = time.monotonic()
        if self.state == "open" and self.filtered_value <= self.close_below:
            self.state = "closed"
            self.last_change = now
        elif self.state == "closed":
            if now - self.last_change >= self.hold_seconds and self.filtered_value >= self.open_above:
                self.state = "open"
                self.last_change = now
        confidence = self._state_confidence()
        return BlinkInference(state=self.state, confidence=confidence, filtered_value=self.filtered_value)

    def _state_confidence(self) -> float:
        span = max(1e-6, self.open_above - self.close_below)
        normalized = clamp((self.filtered_value - self.close_below) / span, 0.0, 1.0)
        if self.state == "open":
            return 0.2 + 0.8 * normalized
        return 0.2 + 0.8 * (1.0 - normalized)
