"""Test discovery setup for the backend.

Ensures `import backend.app.pupil_source` resolves when pytest is run from the
repository root, mirroring the deployed import path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
