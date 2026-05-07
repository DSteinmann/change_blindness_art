"""
Session Manager for reproducible runs.
Saves generated images and metadata to disk for replay.
"""
import json
import time
from pathlib import Path
from typing import Optional, Dict, List
from PIL import Image
import io
import base64


class SessionManager:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.current_session_id: Optional[str] = None
        self.current_session_dir: Optional[Path] = None
        self.sequence_index = 0
        self.metadata: Dict = {}
        
    def start_new_session(
        self,
        session_id: Optional[str] = None,
        participant_id: Optional[str] = None,
        runtime: Optional[Dict] = None,
    ) -> str:
        """Start a new recording session.

        `runtime` is an optional snapshot of the config at the moment of recording
        (model name, surface name, pupil confidence threshold, grid size, etc.)
        which makes replays reproducible even if the code or env changes.
        """
        if session_id is None:
            session_id = f"session_{int(time.time())}"

        self.current_session_id = session_id
        self.current_session_dir = self.sessions_dir / session_id
        self.current_session_dir.mkdir(parents=True, exist_ok=True)
        self.sequence_index = 0

        self.metadata = {
            "session_id": session_id,
            "created_at": time.time(),
            "participant_id": participant_id,
            "runtime": runtime or {},
            "stats": {"blink_count": 0, "frame_drops": 0},
            "calibration": None,
            "edit_history": [],
            "sequence": [],
        }

        self._save_metadata()
        print(f"Started session: {session_id} (participant={participant_id})")
        return session_id

    def record_blink(self) -> None:
        if not self.current_session_dir:
            return
        self.metadata.setdefault("stats", {"blink_count": 0, "frame_drops": 0})
        self.metadata["stats"]["blink_count"] = self.metadata["stats"].get("blink_count", 0) + 1
        self._save_metadata()

    def record_calibration(self, calibration: Dict) -> None:
        if not self.current_session_dir:
            return
        self.metadata["calibration"] = calibration
        self._save_metadata()

    def save_generation(
        self,
        image: Image.Image,
        sector_name: str,
        prompt: str,
        focus_sector: str,
        latency_ms: Optional[float] = None,
        caption: Optional[str] = None,
        duplicate_caption: bool = False,
    ) -> Dict:
        """Save a generated image and its metadata."""
        if not self.current_session_dir:
            raise ValueError("No active session. Call start_new_session() first.")

        filename = f"{self.sequence_index:04d}_{sector_name}.png"
        image_path = self.current_session_dir / filename
        image.save(image_path, "PNG")

        entry = {
            "index": self.sequence_index,
            "filename": filename,
            "target_sector": sector_name,
            "focus_sector": focus_sector,
            "prompt": prompt,
            "caption": caption,
            "timestamp": time.time(),
            "latency_ms": latency_ms,
        }
        if duplicate_caption:
            entry["duplicate_caption"] = True

        self.metadata["sequence"].append(entry)
        if caption:
            history = self.metadata.setdefault("edit_history", [])
            history.append(caption)
            if len(history) > 5:
                del history[: len(history) - 5]
        self._save_metadata()

        lat = f", {latency_ms:.0f}ms" if latency_ms is not None else ""
        print(f"Saved generation {entry['index']}: {sector_name}{lat}")
        self.sequence_index += 1
        return entry
    
    def _save_metadata(self):
        """Save session metadata to JSON."""
        if self.current_session_dir:
            metadata_path = self.current_session_dir / "metadata.json"
            with open(metadata_path, 'w') as f:
                json.dump(self.metadata, f, indent=2)
    
    def list_sessions(self) -> List[str]:
        """List all available sessions."""
        return [d.name for d in self.sessions_dir.iterdir() if d.is_dir()]
    
    def load_session(self, session_id: str) -> Dict:
        """Load a saved session's metadata."""
        session_dir = self.sessions_dir / session_id
        metadata_path = session_dir / "metadata.json"
        
        if not metadata_path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        
        with open(metadata_path, 'r') as f:
            return json.load(f)
    
    def get_image(self, session_id: str, index: int) -> Optional[Image.Image]:
        """Load a specific image from a session."""
        session_dir = self.sessions_dir / session_id
        metadata = self.load_session(session_id)
        
        if index >= len(metadata["sequence"]):
            return None
        
        entry = metadata["sequence"][index]
        image_path = session_dir / entry["filename"]
        
        if not image_path.exists():
            return None
        
        return Image.open(image_path)


class ReplayManager:
    """Manages replay of saved sessions."""
    
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self.replay_session_id: Optional[str] = None
        self.replay_metadata: Optional[Dict] = None
        self.replay_index = 0
    
    def start_replay(self, session_id: str):
        """Start replaying a saved session."""
        self.replay_metadata = self.session_manager.load_session(session_id)
        self.replay_session_id = session_id
        self.replay_index = 0
        print(f"Started replay of session: {session_id}")
        print(f"  Total generations: {len(self.replay_metadata['sequence'])}")
    
    def get_next_generation(self) -> Optional[Dict]:
        """Get the next generation in the replay sequence."""
        if not self.replay_metadata or not self.replay_session_id:
            return None
        
        if self.replay_index >= len(self.replay_metadata["sequence"]):
            return None
        
        entry = self.replay_metadata["sequence"][self.replay_index]
        self.replay_index += 1
        
        # Load the image
        image = self.session_manager.get_image(
            self.replay_session_id,  # Now guaranteed to be str, not None
            entry["index"]
        )
        
        return {
            **entry,
            "image": image
        }
    
    def is_replaying(self) -> bool:
        """Check if currently in replay mode."""
        return self.replay_metadata is not None
    
    def stop_replay(self):
        """Stop replay mode."""
        self.replay_metadata = None
        self.replay_session_id = None
        self.replay_index = 0
