import argparse
import io
import os
import base64
import torch
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from diffusers import StableDiffusionInpaintPipeline
from PIL import Image, ImageDraw
import uvicorn
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Generation Server", version="0.2.0")

# CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

import json

# Global state
pipe = None
device = "cpu"
prompts = []
prompt_index = 0
sector_prompts = {}  # Sector-specific prompts
default_prompt = "add more detail, photorealistic, seamless blend"
PROMPTS_FILE = Path(__file__).parent / "prompts.txt"
SECTOR_PROMPTS_FILE = Path(__file__).parent / "sector_prompts.json"


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
    """Load sector-specific prompts from JSON file."""
    global sector_prompts, default_prompt
    
    if SECTOR_PROMPTS_FILE.exists():
        try:
            with open(SECTOR_PROMPTS_FILE, 'r') as f:
                data = json.load(f)
            sector_prompts = data.get("sectors", {})
            default_prompt = data.get("default", default_prompt)
            print(f"Loaded {len(sector_prompts)} sector-specific prompts from {SECTOR_PROMPTS_FILE}")
            for sector, prompt in sector_prompts.items():
                print(f"  [{sector}] {prompt[:50]}...")
        except Exception as e:
            print(f"Error loading sector prompts: {e}")
            sector_prompts = {}
    else:
        print(f"No sector prompts file found at {SECTOR_PROMPTS_FILE}")


def get_prompt_for_sector(row: int, col: int) -> str:
    """Get the prompt for a specific sector. Falls back to cycling prompts if not found."""
    name = sector_name(row, col)
    
    # First try sector-specific prompt
    if name in sector_prompts:
        return sector_prompts[name]
    
    # Then try cycling prompts
    if prompts:
        return get_next_prompt()
    
    # Finally fall back to default
    return default_prompt


def get_next_prompt():
    """Get the next prompt in the cycle (for non-sector requests)."""
    global prompt_index
    if not prompts:
        return "photorealistic, high detail"
    
    prompt = prompts[prompt_index]
    prompt_index = (prompt_index + 1) % len(prompts)
    return prompt


def load_model():
    global pipe, device
    print("Loading Stable Diffusion Inpainting model...")
    
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    print(f"Using device: {device}")
    
    try:
        model_path = "runwayml/stable-diffusion-inpainting"
        pipe = StableDiffusionInpaintPipeline.from_pretrained(
            model_path,
            torch_dtype=torch.float16 if device != "cpu" else torch.float32,
        ).to(device)
        print("Model loaded successfully.")
    except Exception as e:
        print(f"Error loading model: {e}")
        pipe = None


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
    load_model()


@app.get("/health")
async def health():
    return {
        "status": "ok", 
        "model_loaded": pipe is not None, 
        "device": device,
        "prompts_loaded": len(prompts),
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


@app.post("/generate")
async def generate(request: GenerateRequest):
    """
    Generate modified image using sector-based inpainting.
    If target_row/col provided: use precise sector
    Otherwise: use legacy opposite-region calculation
    """
    if pipe is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
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
        
        # Run generation
        generated_image = pipe(
            prompt=prompt,
            image=init_image,
            mask_image=mask_image,
            strength=request.strength,
            guidance_scale=7.5,
        ).images[0]
        
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
                "X-Target-Sector": sector_name(request.target_row or 0, request.target_col or 0)
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
