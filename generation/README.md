# Patch Generation via OpenRouter

This folder hosts utilities for precomputing peripheral stimuli with Google’s "Nano Banana" Imagen variant (or any other OpenRouter model that can generate images via the chat/completions API).

## Prerequisites
1. Create an OpenRouter account and grab an API key.
2. Export it before running the script (the generator pings OpenRouter once to validate the key before any batch is started):
   ```bash
   export OPENROUTER_API_KEY="sk-or-..."
   ```
3. Ensure `requests` is installed in your virtualenv (`pip install requests`).

## Prompt Lists
Create a text file where each line is either:
- `prompt only` (slug will be auto-generated), or
- `label|prompt` to control filenames.

Example `banana_prompts.txt`:
```
shards|Ultra-high-contrast banana-themed texture made of angular shards, dominant colors #F7E018 and #111111, glowing rim lighting, seamless edges, no text.
swirls|Ultra-high-contrast banana palette abstract organic swirls, soft volumetric haze, seamless edges, no text.
```

## Generate Images
```bash
cd generation
python generate_patches.py \
  --model google/nano-banana \
  --prompt-file still_prompts.txt \
  --per-prompt 4 \
  --size 512x512 \
  --output-dir ../assets/patches/generated
```

Flags of note:
- `--prompt` – render a single prompt without a file.
- `--guidance` – pass a guidance scale if the model supports it (e.g., 6.5).
- `--seed` + `--seed-increment` – deterministic batches per prompt.
- `--dry-run` – inspect payloads without hitting the API.
 - `--positions-mode corners` – instead of one batch per prompt, generate a *series* of variants per prompt, starting with a single object in the upper-left corner, then adding objects in the upper-right, lower-right, and lower-left corners.
 - `--object-description` – short description of the repeated object (default: `small banana`).
 - `--max-positions` – cap how many positions are used when `--positions-mode` is not `none`.

Note: the generator validates your API key via `/api/v1/models`, but it does not
pre-validate the specific `--model` id. It sends the id you provide to the
`/api/v1/chat/completions` endpoint with `modalities: ["image"]` and adapts the returned
data URLs into PNG files. If the model slug is invalid or your key does not have access
to it, the OpenRouter API will return an error message and the script will surface it.

Each PNG is written alongside a `.json` metadata file capturing the prompt, label, seed, and timestamp for reproducibility.
