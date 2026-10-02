"""Sector geometry and image helpers shared across the generation service."""
from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw, ImageFilter

# Aspect ratios accepted by the OpenRouter image-config knob. We pick the
# closest match to the input image so the model output keeps the input's
# proportions; otherwise sector compositing maps the wrong pixels.
SUPPORTED_ASPECTS: tuple[str, ...] = ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3")


def sector_name(row: int, col: int) -> str:
    row_names = ["T", "M", "B"]
    col_names = ["L", "C", "R"]
    if row < len(row_names) and col < len(col_names):
        return f"{row_names[row]}{col_names[col]}"
    return f"({row},{col})"


def calculate_sector_region(
    row: int, col: int, grid_size: int, img_width: int, img_height: int
) -> tuple[int, int, int, int]:
    """Pixel bounds of a grid cell. Row 0 is top, col 0 is left. Edge cells
    extend to the image boundary so no pixels are dropped from rounding."""
    sector_w = img_width // grid_size
    sector_h = img_height // grid_size

    x1 = col * sector_w
    y1 = row * sector_h
    x2 = img_width if col == grid_size - 1 else x1 + sector_w
    y2 = img_height if row == grid_size - 1 else y1 + sector_h
    return (x1, y1, x2, y2)


def calculate_opposite_region(
    focus_x: float,
    focus_y: float,
    img_width: int,
    img_height: int,
    size_fraction: float = 0.3,
) -> tuple[int, int, int, int]:
    """Legacy non-grid modifier: a region opposite the gaze point."""
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


def create_mask(image_size: tuple[int, int], region: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", image_size, 0)
    ImageDraw.Draw(mask).rectangle(region, fill=255)
    return mask


def decode_base64_image(base64_str: str) -> Image.Image:
    if "," in base64_str:
        base64_str = base64_str.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(base64_str))).convert("RGB")


def _aspect_value(label: str) -> float:
    w, h = label.split(":")
    return int(w) / int(h)


def infer_aspect_ratio(width: int, height: int) -> str:
    """Snap an arbitrary image's aspect ratio to the closest supported label."""
    target = width / height
    return min(SUPPORTED_ASPECTS, key=lambda s: abs(_aspect_value(s) - target))


def composite_sector(
    base: Image.Image,
    edit: Image.Image,
    region: tuple[int, int, int, int],
    feather: int = 8,
) -> Image.Image:
    """Paste only the target sector of `edit` onto a copy of `base`.

    Foundation image models re-render the entire canvas even when asked to
    modify a single region, so using the model's full output as the next state
    introduces compounding drift. By taking only the target sector and pasting
    it onto the previous state, non-target pixels stay byte-identical across
    iterations.

    `feather` (px) softens the patch border with an alpha ramp, hiding the
    inevitable tone/exposure mismatch between the model's output and the base.
    Pass `feather=0` for hard-edge compositing (mostly useful in tests).
    """
    sx1, sy1, sx2, sy2 = region
    bw, bh = base.size
    ew, eh = edit.size
    mx1 = sx1 * ew // bw
    my1 = sy1 * eh // bh
    mx2 = sx2 * ew // bw
    my2 = sy2 * eh // bh
    pw, ph = sx2 - sx1, sy2 - sy1
    patch = edit.crop((mx1, my1, mx2, my2)).resize((pw, ph), Image.LANCZOS)

    out = base.copy()
    if feather <= 0:
        out.paste(patch, (sx1, sy1))
        return out

    # Cap feather to a third of the smaller dimension so the inner mask still
    # has area; otherwise tiny patches turn fully transparent.
    f = max(1, min(feather, min(pw, ph) // 3))
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rectangle((f, f, pw - f, ph - f), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(f))
    out.paste(patch, (sx1, sy1), mask=mask)
    return out


def shrink_for_api(img: Image.Image, max_edge: int = 1024) -> str:
    """Downscale + PNG-encode a PIL image as a data URL, bounding payload size.

    OpenRouter rejects requests where the combined image content exceeds ~30MB.
    Semantic mode sends two images per call, so each one needs to fit comfortably
    within ~12MB. 1024 px is also the native generation resolution for current
    foundation image models — pixels above that get downsampled internally
    before generation, so we don't gain quality by sending more.
    """
    work = img.copy()
    if max(work.size) > max_edge:
        work.thumbnail((max_edge, max_edge), Image.LANCZOS)
    buf = io.BytesIO()
    work.convert("RGB").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
