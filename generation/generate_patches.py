import argparse
import torch
from PIL import Image, ImageDraw
from diffusers import StableDiffusionInpaintPipeline
import os

def parse_region(region_str):
    """Parses a region string 'x,y,w,h' into a tuple of integers."""
    try:
        parts = list(map(int, region_str.split(',')))
        if len(parts) != 4:
            raise ValueError("Region must have 4 parts: x,y,w,h")
        return tuple(parts)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid region format: {region_str}. {e}")

def create_peripheral_mask(image_size, peripheral_region):
    """
    Creates a mask image where the peripheral region is white (to be inpainted)
    and everything else is black.
    """
    mask = Image.new("L", image_size, 0)  # Black background
    draw = ImageDraw.Draw(mask)
    draw.rectangle(peripheral_region, fill=255)  # White rectangle for the inpainting area
    return mask

def main():
    parser = argparse.ArgumentParser(description="Generate image patches using Stable Diffusion inpainting.")
    parser.add_argument("--input-image", type=str, required=True, help="Path to the input image.")
    parser.add_argument("--output-image", type=str, required=True, help="Path to save the generated image.")
    parser.add_argument("--peripheral-region", type=parse_region, required=True,
                        help="The region to modify, in 'x,y,w,h' format.")
    parser.add_argument("--prompt", type=str, default="photorealistic, high detail, 8k",
                        help="Prompt to guide the image generation.")
    parser.add_argument("--model-path", type=str, default="runwayml/stable-diffusion-inpainting",
                        help="Path or name of the pre-trained model to use.")
    
    args = parser.parse_args()

    print("Loading models... This may take a moment.")
    
    # Check for MPS (Apple Silicon GPU) availability, otherwise use CUDA or CPU
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    
    print(f"Using device: {device}")

    try:
        pipe = StableDiffusionInpaintPipeline.from_pretrained(
            args.model_path,
            torch_dtype=torch.float16 if device != "cpu" else torch.float32,
        ).to(device)
        print("Model loaded successfully.")
    except Exception as e:
        print(f"Error loading model: {e}")
        print("Please ensure you have a stable internet connection to download the model on first run,")
        print("and that the model path is correct.")
        return

    if not os.path.exists(args.input_image):
        print(f"Error: Input image not found at {args.input_image}")
        return

    print(f"Loading input image from {args.input_image}")
    init_image = Image.open(args.input_image).convert("RGB")
    
    print(f"Creating mask for peripheral region: {args.peripheral_region}")
    mask_image = create_peripheral_mask(init_image.size, args.peripheral_region)

    print(f"Generating new image with prompt: '{args.prompt}'")
    # The strength parameter controls how much noise is added to the image.
    # A lower value will result in an image that is more faithful to the original.
    generator = torch.Generator(device=device).manual_seed(0) # for reproducibility
    generated_image = pipe(
        prompt=args.prompt,
        image=init_image,
        mask_image=mask_image,
        strength=0.75,
        guidance_scale=7.5,
        generator=generator,
    ).images[0]

    print(f"Saving generated image to {args.output_image}")
    generated_image.save(args.output_image)
    print("Image generation complete.")

if __name__ == "__main__":
    main()