import os
from pathlib import Path
from pupil_labs.real_time_screen_gaze import marker_generator
import cv2

# Output directory for markers
OUTPUT_DIR = Path("frontend/public/assets/markers")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Generate markers for IDs 0-3 (corners) using the default tag36h11 family
# Generate at native resolution, then scale up using NEAREST interpolation to keep edges sharp
TARGET_SIZE = 512
for marker_id in range(4):
    img = marker_generator.generate_marker(marker_id=marker_id)
    # Resize to target size using NEAREST interpolation for crisp edges
    img_resized = cv2.resize(img, (TARGET_SIZE, TARGET_SIZE), interpolation=cv2.INTER_NEAREST)
    out_path = OUTPUT_DIR / f"tag36_11_{marker_id:05d}.png"
    cv2.imwrite(str(out_path), img_resized)
    print(f"Generated {out_path} ({TARGET_SIZE}x{TARGET_SIZE})")

print("Done generating AprilTag markers.")
