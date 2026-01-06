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
        
    def start_new_session(self, session_id: Optional[str] = None) -> str:
        """Start a new recording session."""
        if session_id is None:
            session_id = f"session_{int(time.time())}"
        
        self.current_session_id = session_id
        self.current_session_dir = self.sessions_dir / session_id
        self.current_session_dir.mkdir(parents=True, exist_ok=True)
        self.sequence_index = 0
        
        self.metadata = {
            "session_id": session_id,
            "created_at": time.time(),
            "sequence": []
        }
        
        self._save_metadata()
        print(f"Started session: {session_id}")
        return session_id
    
    def save_generation(self, 
                       image: Image.Image, 
                       sector_name: str,
                       prompt: str,
                       focus_sector: str) -> Dict:
        """Save a generated image and its metadata."""
        if not self.current_session_dir:
            raise ValueError("No active session. Call start_new_session() first.")
        
        # Save image
        filename = f"{self.sequence_index:04d}_{sector_name}.png"
        image_path = self.current_session_dir / filename
        image.save(image_path, "PNG")
        
        # Create metadata entry
        entry = {
            "index": self.sequence_index,
            "filename": filename,
            "target_sector": sector_name,
            "focus_sector": focus_sector,
            "prompt": prompt,
            "timestamp": time.time()
        }
        
        self.metadata["sequence"].append(entry)
        self._save_metadata()
        
        self.sequence_index += 1
        print(f"Saved generation {self.sequence_index}: {sector_name}")
        
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
