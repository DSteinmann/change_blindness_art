import argparse
import io
import os
import base64
import httpx
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw
import uvicorn
from pydantic import BaseModel
from typing import Optional

from session_manager import SessionManager, ReplayManager

app = FastAPI(title="Generation Server (OpenRouter)", version="0.4.0")

# CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Global state
prompts = []
prompt_index = 0
sector_prompts = {}  # Sector-specific prompts (can be string or list)
sector_prompt_indices = {}  # Track cycling index per sector
default_prompts = ["add more detail, photorealistic, seamless blend"]
default_prompt_index = 0
PROMPTS_FILE = Path(__file__).parent / "prompts.txt"
SECTOR_PROMPTS_FILE = Path(__file__).parent / "sector_prompts.json"

# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Image generation model - options: "openai/dall-e-3", "stability/stable-diffusion-xl"
IMAGE_MODEL = os.getenv("OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image")

# Session management
# Use /app/assets which is mounted as a volume in Docker
SESSIONS_DIR = Path("/app/assets/sessions") if Path("/app/assets").exists() else Path(__file__).parent / "sessions"
session_manager = SessionManager(SESSIONS_DIR)
replay_manager = ReplayManager(session_manager)


class GenerateRequest(BaseModel):
    """
    Request for sector-based inpainting.
    Frontend sends which sector to modify (target_row, target_col).
    """
    image_base64: str
    focus_x: float  # Where user is looking (normalized 0-1)
    focus_y: float  # For logging
    # Sector-based targeting (preferred)
    target_row: Optional[int] = None  # 0=top, 1=middle, 2=bottom
    target_col: Optional[int] = None  # 0=left, 1=center, 2=right
    grid_size: int = 3  # 3x3 grid
    # Legacy fallback
    strength: float = 0.75
    peripheral_size: float = 0.3


def load_prompts():
    """Load prompts from file. One prompt per line, skip comments and empty lines."""
    global prompts
    prompts = []
    
    if PROMPTS_FILE.exists():
        with open(PROMPTS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    prompts.append(line)
        print(f"Loaded {len(prompts)} cycling prompts from {PROMPTS_FILE}")
    else:
        prompts = [
            "add one more element to the image, photorealistic, high detail",
            "enhance the peripheral area, photorealistic, seamless blend",
        ]
        print(f"Using {len(prompts)} default cycling prompts")
    
    for i, p in enumerate(prompts):
        print(f"  [{i}] {p[:60]}...")


def load_sector_prompts():
    """Load sector-specific prompts from JSON file. Supports both string and array formats."""
    global sector_prompts, sector_prompt_indices, default_prompts

    if SECTOR_PROMPTS_FILE.exists():
        try:
            with open(SECTOR_PROMPTS_FILE, 'r') as f:
                data = json.load(f)
            sector_prompts = data.get("sectors", {})

            # Handle default as string or array
            default_val = data.get("default", default_prompts)
            if isinstance(default_val, str):
                default_prompts = [default_val]
            else:
                default_prompts = default_val

            # Initialize indices for each sector
            for sector in sector_prompts:
                sector_prompt_indices[sector] = 0

            print(f"Loaded {len(sector_prompts)} sector-specific prompts from {SECTOR_PROMPTS_FILE}")
            for sector, prompt_data in sector_prompts.items():
                if isinstance(prompt_data, list):
                    print(f"  [{sector}] {len(prompt_data)} prompts, first: {prompt_data[0][:40]}...")
                else:
                    print(f"  [{sector}] {prompt_data[:50]}...")
        except Exception as e:
            print(f"Error loading sector prompts: {e}")
            sector_prompts = {}
    else:
        print(f"No sector prompts file found at {SECTOR_PROMPTS_FILE}")


def get_prompt_for_sector(row: int, col: int) -> str:
    """Get the prompt for a specific sector, cycling through available prompts."""
    global sector_prompt_indices, default_prompt_index
    name = sector_name(row, col)

    # First try sector-specific prompt
    if name in sector_prompts:
        prompt_data = sector_prompts[name]

        # Handle array of prompts (cycling)
        if isinstance(prompt_data, list) and len(prompt_data) > 0:
            # Get current index for this sector
            idx = sector_prompt_indices.get(name, 0)
            prompt = prompt_data[idx]
            # Advance to next prompt for next time
            sector_prompt_indices[name] = (idx + 1) % len(prompt_data)
            print(f"Sector {name}: using prompt {idx + 1}/{len(prompt_data)}")
            return prompt
        elif isinstance(prompt_data, str):
            return prompt_data

    # Then try cycling prompts from prompts.txt
    if prompts:
        return get_next_prompt()

    # Finally fall back to default (also supports cycling)
    if default_prompts:
        prompt = default_prompts[default_prompt_index]
        default_prompt_index = (default_prompt_index + 1) % len(default_prompts)
        return prompt

    return "add more detail, photorealistic, seamless blend"


def get_next_prompt():
    """Get the next prompt in the cycle (for non-sector requests)."""
    global prompt_index
    if not prompts:
        return "photorealistic, high detail"
    
    prompt = prompts[prompt_index]
    prompt_index = (prompt_index + 1) % len(prompts)
    return prompt


async def generate_with_openrouter(image: Image.Image, mask: Image.Image, prompt: str, region: tuple) -> Image.Image:
    """
    Generate image using OpenRouter API with Gemini 2.5 Flash Image.
    Uses the modalities parameter to request image output.
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set")

    # Convert image to base64
    img_buffer = io.BytesIO()
    image.save(img_buffer, format="PNG")
    img_base64 = base64.b64encode(img_buffer.getvalue()).decode()

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/ubicomp-capstone",
            },
            json={
                "model": IMAGE_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_base64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": f"Modify this image by adding or changing something in the region from pixel ({region[0]}, {region[1]}) to ({region[2]}, {region[3]}). The modification should be: {prompt}. Return the complete modified image."
                            }
                        ]
                    }
                ],
                # Required for image generation - tells OpenRouter we want image output
                "modalities": ["image", "text"]
            }
        )

        if response.status_code != 200:
            error_text = response.text
            print(f"OpenRouter API error: {response.status_code} - {error_text}")
            raise Exception(f"OpenRouter API error: {response.status_code}")

        result = response.json()
        print(f"OpenRouter response keys: {result.keys()}")

        # Handle response
        choices = result.get("choices", [])
        if not choices:
            print("No choices in response")
            raise Exception("No choices in API response")

        message = choices[0].get("message", {})

        # Images are returned in the "images" field (OpenRouter format)
        images = message.get("images", [])
        if images:
            for img_item in images:
                if img_item.get("type") == "image_url":
                    img_url = img_item.get("image_url", {}).get("url", "")
                    if img_url.startswith("data:image"):
                        # Extract base64 data from data URL
                        b64_data = img_url.split(",", 1)[1]
                        print(f"Successfully extracted image from response ({len(b64_data)} bytes)")
                        return Image.open(io.BytesIO(base64.b64decode(b64_data)))

        # Fallback: check content field (some models may use this)
        content = message.get("content", "")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    img_url = item.get("image_url", {}).get("url", "")
                    if img_url.startswith("data:image"):
                        b64_data = img_url.split(",", 1)[1]
                        return Image.open(io.BytesIO(base64.b64decode(b64_data)))

        # If we got here, no image was found
        print(f"No image in response. Message keys: {message.keys()}")
        print(f"Content preview: {str(content)[:200]}")
        raise Exception("Model did not return an image")


async def generate_with_stability(image: Image.Image, mask: Image.Image, prompt: str) -> Image.Image:
    """Use Stability AI's inpainting endpoint via OpenRouter or direct API."""
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set")
    
    # Convert images to base64
    img_buffer = io.BytesIO()
    image.save(img_buffer, format="PNG")
    img_base64 = base64.b64encode(img_buffer.getvalue()).decode()
    
    mask_buffer = io.BytesIO()
    mask.save(mask_buffer, format="PNG")
    mask_base64 = base64.b64encode(mask_buffer.getvalue()).decode()
    
    # Use Stability's edit endpoint
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "https://api.stability.ai/v2beta/stable-image/edit/inpaint",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Accept": "image/*",
            },
            files={
                "image": ("image.png", img_buffer.getvalue(), "image/png"),
                "mask": ("mask.png", mask_buffer.getvalue(), "image/png"),
            },
            data={
                "prompt": prompt,
                "output_format": "png",
            }
        )
        
        if response.status_code != 200:
            raise Exception(f"Stability API error: {response.status_code}")
        
        return Image.open(io.BytesIO(response.content))


def simple_composite_edit(image: Image.Image, mask: Image.Image, prompt: str, region: tuple) -> Image.Image:
    """
    Visible fallback: Draw obvious shapes/patterns in the target region.
    Use this when API is unavailable for testing - makes changes clearly visible.
    """
    import random
    import math

    result = image.copy()
    draw = ImageDraw.Draw(result)

    x1, y1, x2, y2 = region
    region_w = x2 - x1
    region_h = y2 - y1
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2

    # Use prompt hash for deterministic but varied results
    seed = hash(prompt) % 10000
    random.seed(seed)

    # Generate a vibrant color
    hue = random.random()
    r = int(255 * (0.5 + 0.5 * math.sin(hue * 6.28)))
    g = int(255 * (0.5 + 0.5 * math.sin(hue * 6.28 + 2.09)))
    b = int(255 * (0.5 + 0.5 * math.sin(hue * 6.28 + 4.18)))
    color = (r, g, b)

    # Choose a shape type based on prompt hash
    shape_type = seed % 5

    if shape_type == 0:
        # Draw a filled circle
        radius = min(region_w, region_h) // 3
        draw.ellipse([center_x - radius, center_y - radius,
                      center_x + radius, center_y + radius], fill=color)
    elif shape_type == 1:
        # Draw a star
        points = []
        outer_r = min(region_w, region_h) // 3
        inner_r = outer_r // 2
        for i in range(10):
            angle = i * math.pi / 5 - math.pi / 2
            r = outer_r if i % 2 == 0 else inner_r
            points.append((center_x + r * math.cos(angle), center_y + r * math.sin(angle)))
        draw.polygon(points, fill=color)
    elif shape_type == 2:
        # Draw a triangle
        size = min(region_w, region_h) // 3
        points = [
            (center_x, center_y - size),
            (center_x - size, center_y + size),
            (center_x + size, center_y + size)
        ]
        draw.polygon(points, fill=color)
    elif shape_type == 3:
        # Draw concentric circles
        for i in range(3, 0, -1):
            radius = min(region_w, region_h) // 3 * i // 3
            shade = (r * i // 3, g * i // 3, b * i // 3)
            draw.ellipse([center_x - radius, center_y - radius,
                          center_x + radius, center_y + radius], fill=shade)
    else:
        # Draw a diamond/rhombus
        size = min(region_w, region_h) // 3
        points = [
            (center_x, center_y - size),
            (center_x + size, center_y),
            (center_x, center_y + size),
            (center_x - size, center_y)
        ]
        draw.polygon(points, fill=color)

    # Add a subtle border around the region to show what was modified
    draw.rectangle([x1, y1, x2, y2], outline=color, width=3)

    print(f"Fallback: drew shape {shape_type} in color {color} at region {region}")
    return result


def create_mask(image_size, region):
    """Create mask where region is white (to be inpainted)."""
    mask = Image.new("L", image_size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(region, fill=255)
    return mask


def decode_base64_image(base64_str: str) -> Image.Image:
    """Decode base64 string to PIL Image."""
    if "," in base64_str:
        base64_str = base64_str.split(",", 1)[1]
    image_data = base64.b64decode(base64_str)
    return Image.open(io.BytesIO(image_data)).convert("RGB")


def calculate_sector_region(row: int, col: int, grid_size: int,
                            img_width: int, img_height: int):
    """
    Calculate the region for a specific sector in the grid.
    Grid is row-major: row 0 = top, col 0 = left.
    """
    sector_w = img_width // grid_size
    sector_h = img_height // grid_size
    
    x1 = col * sector_w
    y1 = row * sector_h
    x2 = x1 + sector_w
    y2 = y1 + sector_h
    
    # Ensure we cover the entire image for edge sectors
    if col == grid_size - 1:
        x2 = img_width
    if row == grid_size - 1:
        y2 = img_height
    
    return (x1, y1, x2, y2)


def calculate_opposite_region(focus_x: float, focus_y: float, 
                              img_width: int, img_height: int, 
                              size_fraction: float = 0.3):
    """
    Legacy: Calculate region opposite to focus point.
    """
    opposite_x = 1.0 - focus_x
    opposite_y = 1.0 - focus_y
    
    region_w = int(img_width * size_fraction)
    region_h = int(img_height * size_fraction)
    
    center_x = int(opposite_x * img_width)
    center_y = int(opposite_y * img_height)
    
    x1 = max(0, center_x - region_w // 2)
    y1 = max(0, center_y - region_h // 2)
    x2 = min(img_width, x1 + region_w)
    y2 = min(img_height, y1 + region_h)
    
    return (x1, y1, x2, y2)


def sector_name(row: int, col: int) -> str:
    """Get human-readable sector name."""
    row_names = ["T", "M", "B"]  # Top, Middle, Bottom
    col_names = ["L", "C", "R"]  # Left, Center, Right
    if row < len(row_names) and col < len(col_names):
        return f"{row_names[row]}{col_names[col]}"
    return f"({row},{col})"


@app.on_event("startup")
def startup_event():
    load_prompts()
    load_sector_prompts()
    print(f"OpenRouter API Key: {'✓ Set' if OPENROUTER_API_KEY else '✗ Not set'}")
    print(f"Image model: {IMAGE_MODEL}")
    
    # Auto-start a session to save all generations by default
    session_id = session_manager.start_new_session()
    print(f"Auto-started recording session: {session_id}")
    print(f"All generations will be saved to: {SESSIONS_DIR}/{session_id}")


@app.get("/health")
async def health():
    return {
        "status": "ok", 
        "api_configured": bool(OPENROUTER_API_KEY),
        "image_model": IMAGE_MODEL,
        "prompts_loaded": len(prompts),
        "sector_prompts_loaded": len(sector_prompts),
        "current_prompt_index": prompt_index
    }


@app.get("/prompts")
async def get_prompts():
    """Get the list of prompts and current index."""
    return {
        "prompts": prompts,
        "current_index": prompt_index,
        "total": len(prompts)
    }


@app.post("/reset")
async def reset_prompt_index():
    """Reset prompt index to start from beginning."""
    global prompt_index
    prompt_index = 0
    return {"message": "Prompt index reset to 0"}


# Session management endpoints
@app.post("/session/start")
async def start_session(session_id: Optional[str] = None):
    """Start a new recording session."""
    sid = session_manager.start_new_session(session_id)
    return {
        "session_id": sid,
        "status": "recording"
    }


@app.get("/session/list")
async def list_sessions():
    """List all available sessions."""
    sessions = session_manager.list_sessions()
    return {"sessions": sessions}


@app.get("/session/{session_id}")
async def get_session(session_id: str):
    """Get metadata for a specific session."""
    try:
        metadata = session_manager.load_session(session_id)
        return metadata
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@app.post("/session/replay/{session_id}")
async def start_replay(session_id: str):
    """Start replaying a saved session."""
    try:
        replay_manager.start_replay(session_id)
        total = len(replay_manager.replay_metadata["sequence"]) if replay_manager.replay_metadata else 0
        return {
            "session_id": session_id,
            "status": "replaying",
            "total_generations": total
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@app.post("/session/replay/stop")
async def stop_replay():
    """Stop replay mode."""
    replay_manager.stop_replay()
    return {"status": "stopped"}


@app.get("/session/replay/next")
async def replay_next():
    """Get the next generation in replay mode."""
    if not replay_manager.is_replaying():
        raise HTTPException(status_code=400, detail="Not in replay mode")
    
    entry = replay_manager.get_next_generation()
    if not entry:
        return {"status": "complete", "image": None}
    
    # Return image as PNG
    img_byte_arr = io.BytesIO()
    entry["image"].save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return Response(
        content=img_byte_arr.getvalue(),
        media_type="image/png",
        headers={
            "X-Sector": entry["target_sector"],
            "X-Prompt": entry["prompt"][:100],
            "X-Index": str(entry["index"])
        }
    )


@app.post("/generate")
async def generate(request: GenerateRequest):
    """
    Generate modified image using OpenRouter API.
    If target_row/col provided: use precise sector
    Otherwise: use legacy opposite-region calculation
    """
    try:
        init_image = decode_base64_image(request.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to decode image: {str(e)}")
    
    try:
        # Prefer sector-based targeting if provided
        if request.target_row is not None and request.target_col is not None:
            region = calculate_sector_region(
                request.target_row,
                request.target_col,
                request.grid_size,
                init_image.width,
                init_image.height
            )
            target = sector_name(request.target_row, request.target_col)
            # Use sector-specific prompt
            prompt = get_prompt_for_sector(request.target_row, request.target_col)
            print(f"Sector-based: modifying {target} with prompt: {prompt[:50]}...")
        else:
            # Legacy: use cycling prompts
            prompt = get_next_prompt()
            target = "legacy"
            # Fallback to legacy opposite-region
            region = calculate_opposite_region(
                request.focus_x,
                request.focus_y,
                init_image.width,
                init_image.height,
                request.peripheral_size
            )
            print(f"Legacy: focus at ({request.focus_x:.2f}, {request.focus_y:.2f})")
        
        print(f"Inpainting region: {region} ({region[2]-region[0]}x{region[3]-region[1]} px)")
        
        mask_image = create_mask(init_image.size, region)
        
        # Generate using OpenRouter (no fallback - only show real generated images)
        if OPENROUTER_API_KEY:
            try:
                generated_image = await generate_with_openrouter(init_image, mask_image, prompt, region)
            except Exception as api_err:
                print(f"OpenRouter API failed: {api_err}")
                # Return original image unchanged instead of fallback shapes
                generated_image = init_image
        else:
            print("No API key set - returning original image")
            generated_image = init_image
        
        # Save to session if recording
        if session_manager.current_session_id:
            # Determine focus sector for logging (opposite of target)
            if request.target_row is not None and request.target_col is not None:
                focus_sector = sector_name(
                    (request.grid_size - 1) - request.target_row,
                    (request.grid_size - 1) - request.target_col
                )
            else:
                focus_sector = "unknown"
            
            session_manager.save_generation(
                generated_image,
                target,
                prompt,
                focus_sector
            )
        
        # Return as PNG
        img_byte_arr = io.BytesIO()
        generated_image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        return Response(
            content=img_byte_arr.getvalue(), 
            media_type="image/png",
            headers={
                "X-Prompt-Used": prompt[:100],
                "X-Prompt-Index": str(prompt_index - 1),
                "X-Target-Sector": target
            }
        )
        
    except Exception as e:
        print(f"Generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8001, help="Port to bind to")
    args = parser.parse_args()
    
    uvicorn.run(app, host=args.host, port=args.port)
