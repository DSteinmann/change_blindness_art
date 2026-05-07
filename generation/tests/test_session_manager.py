"""Tests for SessionManager metadata extensions."""
from __future__ import annotations

import json

from PIL import Image

from session_manager import SessionManager


def _tiny_image() -> Image.Image:
    return Image.new("RGB", (8, 8), (1, 2, 3))


def test_start_session_records_mode_when_passed_in_runtime(tmp_path):
    sm = SessionManager(tmp_path)
    sid = sm.start_new_session(runtime={"mode": "semantic"})
    metadata = json.loads((tmp_path / sid / "metadata.json").read_text())
    assert metadata["runtime"]["mode"] == "semantic"
    assert metadata["edit_history"] == []


def test_save_generation_persists_caption(tmp_path):
    sm = SessionManager(tmp_path)
    sid = sm.start_new_session(runtime={"mode": "semantic"})
    entry = sm.save_generation(
        _tiny_image(), "TL", "raw prompt", "BR",
        caption="a bird landed on the ledge",
    )
    assert entry["caption"] == "a bird landed on the ledge"

    metadata = json.loads((tmp_path / sid / "metadata.json").read_text())
    assert metadata["sequence"][0]["caption"] == "a bird landed on the ledge"
    assert metadata["edit_history"] == ["a bird landed on the ledge"]


def test_edit_history_is_capped_at_five(tmp_path):
    sm = SessionManager(tmp_path)
    sm.start_new_session(runtime={"mode": "semantic"})
    for i in range(7):
        sm.save_generation(_tiny_image(), "TL", "p", "BR", caption=f"c{i}")
    assert sm.metadata["edit_history"] == ["c2", "c3", "c4", "c5", "c6"]


def test_save_generation_without_caption_still_records_entry(tmp_path):
    sm = SessionManager(tmp_path)
    sm.start_new_session(runtime={"mode": "cycling"})
    entry = sm.save_generation(_tiny_image(), "TL", "p", "BR")
    assert entry["caption"] is None
    assert sm.metadata["edit_history"] == []


def test_duplicate_caption_flag_is_stored_when_set(tmp_path):
    sm = SessionManager(tmp_path)
    sm.start_new_session(runtime={"mode": "semantic"})
    entry = sm.save_generation(
        _tiny_image(), "TL", "p", "BR",
        caption="twin", duplicate_caption=True,
    )
    assert entry["duplicate_caption"] is True


def test_duplicate_caption_flag_absent_by_default(tmp_path):
    sm = SessionManager(tmp_path)
    sm.start_new_session(runtime={"mode": "semantic"})
    entry = sm.save_generation(_tiny_image(), "TL", "p", "BR", caption="solo")
    assert "duplicate_caption" not in entry


def test_load_session_accepts_legacy_metadata_without_edit_history(tmp_path):
    sid = "legacy-session"
    folder = tmp_path / sid
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({
        "session_id": sid,
        "created_at": 0,
        "sequence": [{"index": 0, "filename": "x.png", "target_sector": "TL",
                      "focus_sector": "BR", "prompt": "p", "timestamp": 0}],
    }))
    sm = SessionManager(tmp_path)
    metadata = sm.load_session(sid)
    assert metadata["sequence"][0]["prompt"] == "p"
