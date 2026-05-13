"""Tests for the binocular/monocular gaze source filter."""
from __future__ import annotations

from backend.app.pupil_source import filter_to_one_source


def _binocular(ts: float, conf: float = 0.9):
    return {"norm_pos": [0.5, 0.5], "confidence": conf, "timestamp": ts,
            "topic": "gaze.3d.01.", "base_data": [{}, {}]}


def _monocular_left(ts: float, conf: float = 0.7):
    return {"norm_pos": [0.4, 0.4], "confidence": conf, "timestamp": ts,
            "topic": "gaze.3d.0.", "base_data": [{}]}


def _monocular_right(ts: float, conf: float = 0.75):
    return {"norm_pos": [0.6, 0.6], "confidence": conf, "timestamp": ts,
            "topic": "gaze.3d.1.", "base_data": [{}]}


def test_empty_input_returns_empty():
    assert filter_to_one_source([]) == []


def test_binocular_preferred_when_mixed():
    pts = [_monocular_left(1.0), _binocular(1.0), _monocular_right(1.0)]
    out = filter_to_one_source(pts)
    assert len(out) == 1
    assert out[0]["topic"] == "gaze.3d.01."


def test_multiple_binoculars_all_kept():
    pts = [_binocular(1.0), _binocular(2.0), _binocular(3.0)]
    out = filter_to_one_source(pts)
    assert len(out) == 3


def test_monocular_only_deduped_to_one_per_timestamp():
    pts = [_monocular_left(1.0, conf=0.7), _monocular_right(1.0, conf=0.9)]
    out = filter_to_one_source(pts)
    assert len(out) == 1
    # Higher-confidence wins for the same timestamp.
    assert out[0]["confidence"] == 0.9


def test_monocular_across_timestamps_kept_separately():
    pts = [_monocular_left(1.0), _monocular_left(2.0), _monocular_left(3.0)]
    out = filter_to_one_source(pts)
    assert len(out) == 3


def test_topic_only_detection_without_base_data():
    pts = [{"timestamp": 1.0, "confidence": 0.8, "topic": "gaze.2d.01.",
            "norm_pos": [0.5, 0.5]}]
    out = filter_to_one_source(pts)
    assert len(out) == 1  # detected as binocular via topic


def test_missing_topic_and_base_data_treated_as_monocular():
    pts = [{"timestamp": 1.0, "confidence": 0.5, "norm_pos": [0.5, 0.5]}]
    out = filter_to_one_source(pts)
    assert len(out) == 1
