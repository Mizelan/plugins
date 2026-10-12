# Sprite Pipeline

This is the default 2D animation workflow for the plugin.

## Principles

- Start from one approved in-game frame.
- Generate the animation as one strip, not isolated frames.
- Normalize the whole strip with one shared scale.
- Use one shared anchor, typically bottom-center.
- Preview before approving the asset.

## Why this works

- The approved seed frame preserves identity.
- Strip-first generation reduces frame-to-frame drift.
- Shared-scale normalization prevents one tall pose from making the character feel smaller.
- Locking frame 01 back to the shipped sprite preserves continuity for idle-to-action transitions.

## Prompt template

```text
Intended use: candidate production spritesheet for a 2D browser game animation review.
Edit the provided transparent reference canvas into a single horizontal <N>-frame spritesheet.

The existing sprite in the leftmost slot is the anchor frame and must remain the same character:
- same facing direction
- same silhouette family
- same palette family
- same proportions
- same readable face or key features
- same outfit details

Composition:
- transparent canvas
- exactly one row of <N> equal frame slots
- no extra characters
- no labels
- no scenery
- no poster layout

Action:
- describe the specific animation beat from frame 1 through frame N

Style:
- authentic pixel-art production asset
- crisp pixel clusters
- restrained palette
- not concept art
```

## Normalization notes

- Use the union of detected sprite bounds per slot.
- Compute one scale from the largest detected frame and anchor.
- Bottom-align frames into the target canvas.
- Reuse the exact shipped frame for frame 01 when `--lock-frame1` is appropriate. The anchor must be exactly `--frame-size` square; other sizes are rejected because the strip-space scale would shrink it.
- Pixel art: run `snap_pixel_grid.py` instead. It estimates the cell from the most common distance between color edges (a run never spans less than one cell, so half cells cannot win), refines it to the strongest grid contrast within ±15%, samples each cell's inner area, matches colors in CIELAB, and replaces isolated pixels whose color is never isolated in the seed (so seed eyes survive, stray shading pixels do not).
- It gives each connected piece of the strip to the slot holding its center, so a cape or sword tip that crosses a slot edge stays whole with its own frame, and drops specks under 5% of the largest piece in a slot. All frames are cropped with one shared slot-relative box, so a pose keeps its place (an extended sword does not re-center the body).
- It exits with an error when the median in-cell color deviation exceeds `--max-cell-deviation` (default 40). Painterly or small, blurred strips have no grid; regenerate them as hard-edged upscaled pixel art with each frame filling its slot.
- Keep the seed's block size visible in the edit canvas (about 8–12 px per seed pixel). The image tool may resize the canvas (2240×740 came back as 2172×724), and the drawn block size scaled with it. Observed once per setting with a 58×89 seed: at 2 px per seed pixel the strip came back at 40×51 sprite pixels; at 8 px it came back at 58×88. A 40×51 seed at 12 px also kept 51 rows.
- A new view (for example the back view) drawn from the front seed lost scale even at 8 px (observed: 60 and 69 rows against an 89-row seed). Upscaling that frame to the target height with nearest-neighbor and using it as the 8 px reference brought the redrawn strip back to 89 rows (observed once). Generate every animation of that view from the same corrected reference so they match.
- Add `--extra-color` only for colors the seed palette lacks; an added color near an existing one steals its pixels.
