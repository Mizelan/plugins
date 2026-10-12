#!/usr/bin/env python3
"""Snap an upscaled, anti-aliased pixel-art strip back onto its native pixel grid."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required. Install it with `python3 -m pip install pillow`."
    ) from exc

Color = tuple[int, int, int]
Pixel = tuple[int, int, int, int]
NEIGHBORS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Detect the native pixel grid of a generated strip, resample one color per "
            "cell, force binary alpha, quantize to the seed palette, and write "
            "integer-scaled bottom-center frames."
        )
    )
    parser.add_argument("--input", required=True, help="Path to the raw generated strip.")
    parser.add_argument(
        "--seed",
        required=True,
        help="Approved seed frame; native or an integer upscale (detected and reduced to native).",
    )
    parser.add_argument("--out-dir", required=True, help="Output directory for frames.")
    parser.add_argument("--frames", type=int, required=True, help="Number of horizontal frames.")
    parser.add_argument("--frame-size", type=int, default=64, help="Output square frame size. Default: 64.")
    parser.add_argument(
        "--scale",
        type=int,
        help="Integer upscale from native pixels to output. Default: largest that fits every frame.",
    )
    parser.add_argument(
        "--cell-size",
        type=float,
        help="Native pixel size in raw-image pixels. Default: auto-detect.",
    )
    parser.add_argument(
        "--extra-color",
        action="append",
        default=[],
        help="Extra allowed palette color as RRGGBB (for example a white hit flash). Repeatable.",
    )
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=128,
        help="Cells whose median alpha is at or above this value become opaque. Default: 128.",
    )
    parser.add_argument(
        "--keep-orphans",
        action="store_true",
        help="Skip removal of isolated pixels whose color is never isolated in the seed.",
    )
    parser.add_argument(
        "--max-cell-deviation",
        type=float,
        default=40.0,
        help=(
            "Maximum median color deviation inside a cell before the input is rejected as not pixel art "
            "(painterly or blurred strips have no grid to snap to). Default: 40."
        ),
    )
    return parser.parse_args()


def parse_hex(value: str) -> Color:
    value = value.lstrip("#")
    if len(value) != 6:
        raise SystemExit(f"--extra-color expects RRGGBB, got {value!r}.")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def edge_profile(image: Image.Image, axis: int) -> list[float]:
    """Sum of color change between neighboring pixels along one axis."""
    width, height = image.size
    pixels = image.load()
    length = width - 1 if axis == 0 else height - 1
    profile = [0.0] * length
    for y in range(height - (axis == 1)):
        for x in range(width - (axis == 0)):
            a = pixels[x, y]
            b = pixels[x + 1, y] if axis == 0 else pixels[x, y + 1]
            if a[3] == 0 and b[3] == 0:
                continue
            delta = abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3])
            profile[x if axis == 0 else y] += delta
    return profile


def grid_score(profile: list[float], cell: float, offset: float) -> float:
    positions = []
    position = offset
    while position < len(profile):
        positions.append(int(round(position)))
        position += cell
    positions = [p for p in positions if p < len(profile)]
    if not positions:
        return 0.0
    return sum(profile[p] for p in positions) / len(positions)


def best_offset(profile: list[float], cell: float) -> tuple[float, float]:
    """Best (contrast, offset): edge strength on grid lines minus strength halfway between them.

    A multiple of the true cell scores high on its lines but also halfway, so its contrast drops.
    """
    steps = max(1, int(cell * 4))
    return max(
        (
            grid_score(profile, cell, cell * i / steps) - grid_score(profile, cell, cell * i / steps + cell / 2),
            cell * i / steps,
        )
        for i in range(steps)
    )


def run_length_cell(image: Image.Image, min_run: int = 3, max_run: int = 64) -> float:
    """Estimate the cell from the most common distance between color edges along rows and columns.

    A run can span several cells but never less than one, so fractions of the cell cannot win.
    Consecutive changed pixels form one blurred edge and are measured from its center.
    """
    pixels = image.load()
    width, height = image.size
    runs: Counter[int] = Counter()
    lines = [[(x, y) for x in range(width)] for y in range(0, height, 3)]
    lines += [[(x, y) for y in range(height)] for x in range(0, width, 3)]
    for line in lines:
        previous_center: float | None = None
        span_start: int | None = None
        for i in range(1, len(line) + 1):
            changed = False
            if i < len(line):
                a, b = pixels[line[i - 1]], pixels[line[i]]
                if a[3] == 0 and b[3] == 0:
                    previous_center = None
                else:
                    changed = abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 40
            if changed and span_start is None:
                span_start = i
            elif not changed and span_start is not None:
                center = (span_start + i - 1) / 2
                if previous_center is not None and min_run <= center - previous_center <= max_run:
                    runs[int(round(center - previous_center))] += 1
                previous_center = center
                span_start = None
    if not runs:
        return 0.0
    mode = runs.most_common(1)[0][0]
    near = {length: runs[length] for length in (mode - 1, mode, mode + 1)}
    return sum(length * count for length, count in near.items()) / sum(near.values())


def detect_cell(image: Image.Image, x_profile: list[float], y_profile: list[float]) -> float:
    estimate = run_length_cell(image)
    if estimate <= 0:
        return 0.0
    # Refine to the strongest grid contrast near the estimate; multiples of the cell are excluded by the window.
    best = (float("-inf"), estimate)
    cell = estimate * 0.85
    while cell <= estimate * 1.15:
        best = max(best, (best_offset(x_profile, cell)[0] + best_offset(y_profile, cell)[0], cell))
        cell += 0.02
    return best[1]


def median(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def to_lab(color: Color) -> tuple[float, float, float]:
    """sRGB (D65) to CIELAB, so palette matching follows perceived lightness and hue."""

    def linear(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in color)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def nearest(color: Color, palette: list[Color]) -> Color:
    target = to_lab(color)
    return min(palette, key=lambda p: sum((a - b) ** 2 for a, b in zip(to_lab(p), target)))


def resample(
    image: Image.Image,
    cell: float,
    x_offset: float,
    y_offset: float,
    palette: list[Color],
    alpha_threshold: int,
) -> tuple[Image.Image, float]:
    """Return the native image and the median in-cell color deviation (low means a real pixel grid)."""
    pixels = image.load()
    columns = int((image.width - x_offset) // cell)
    rows = int((image.height - y_offset) // cell)
    inset = max(0, int(cell * 0.2))
    native = Image.new("RGBA", (columns, rows), (0, 0, 0, 0))
    out = native.load()
    deviations: list[float] = []
    for row in range(rows):
        for column in range(columns):
            left = int(round(x_offset + column * cell))
            top = int(round(y_offset + row * cell))
            right = min(image.width, int(round(x_offset + (column + 1) * cell)))
            bottom = min(image.height, int(round(y_offset + (row + 1) * cell)))
            samples = [
                pixels[x, y]
                for y in range(top + inset, max(top + inset + 1, bottom - inset))
                for x in range(left + inset, max(left + inset + 1, right - inset))
            ]
            if median([s[3] for s in samples]) < alpha_threshold:
                continue
            color = (median([s[0] for s in samples]), median([s[1] for s in samples]), median([s[2] for s in samples]))
            deviations.append(
                sum(abs(s[0] - color[0]) + abs(s[1] - color[1]) + abs(s[2] - color[2]) for s in samples) / len(samples)
            )
            out[column, row] = (*nearest(color, palette), 255)
    deviations.sort()
    return native, deviations[len(deviations) // 2] if deviations else 0.0


def orphan_colors(image: Image.Image) -> set[Color]:
    """Colors that appear as a pixel with no same-colored 4-neighbor."""
    pixels = image.load()
    found: set[Color] = set()
    for y in range(image.height):
        for x in range(image.width):
            pixel = pixels[x, y]
            if pixel[3] and is_orphan(pixels, image.size, x, y):
                found.add(pixel[:3])
    return found


def is_orphan(pixels, size: tuple[int, int], x: int, y: int) -> bool:
    pixel = pixels[x, y]
    for dx, dy in NEIGHBORS:
        nx, ny = x + dx, y + dy
        if 0 <= nx < size[0] and 0 <= ny < size[1] and pixels[nx, ny] == pixel:
            return False
    return True


def remove_orphans(image: Image.Image, allowed: set[Color]) -> int:
    """Replace isolated pixels of disallowed colors with their most common opaque neighbor."""
    pixels = image.load()
    fixes: list[tuple[int, int, Pixel]] = []
    for y in range(image.height):
        for x in range(image.width):
            pixel = pixels[x, y]
            if not pixel[3] or pixel[:3] in allowed or not is_orphan(pixels, image.size, x, y):
                continue
            neighbors = Counter(
                pixels[x + dx, y + dy]
                for dx, dy in NEIGHBORS
                if 0 <= x + dx < image.width and 0 <= y + dy < image.height and pixels[x + dx, y + dy][3]
            )
            if neighbors:
                fixes.append((x, y, neighbors.most_common(1)[0][0]))
    for x, y, replacement in fixes:
        pixels[x, y] = replacement
    return len(fixes)


def pieces_of(image: Image.Image) -> list[list[tuple[int, int]]]:
    """8-connected groups of opaque pixels."""
    pixels = image.load()
    width, height = image.size
    seen: set[tuple[int, int]] = set()
    pieces: list[list[tuple[int, int]]] = []
    for start in ((x, y) for y in range(height) for x in range(width)):
        if start in seen or not pixels[start][3]:
            continue
        seen.add(start)
        stack, piece = [start], []
        while stack:
            x, y = stack.pop()
            piece.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if 0 <= n[0] < width and 0 <= n[1] < height and n not in seen and pixels[n][3]:
                        seen.add(n)
                        stack.append(n)
        pieces.append(piece)
    return pieces


def split_frames(native: Image.Image, frames: int, min_share: float = 0.05) -> list[Image.Image] | None:
    """Give each connected piece to the slot holding its center, then crop all frames with one shared box.

    A pose may cross its slot edge (a cape or sword tip); cutting at the edge would clip it and leave a
    sliver in the neighbor, so whole pieces are assigned instead. Specks under `min_share` of the
    largest piece in a slot are dropped. The shared slot-relative box keeps each pose where the strip
    drew it: per-frame bounds would re-center the body when a sword extends to one side.
    """
    step = native.width / frames
    pixels = native.load()
    by_slot: list[list[list[tuple[int, int]]]] = [[] for _ in range(frames)]
    for piece in pieces_of(native):
        xs = [x for x, _ in piece]
        slot = min(frames - 1, int((min(xs) + max(xs) + 1) / 2 // step))
        by_slot[slot].append(piece)
    layers: list[tuple[Image.Image, int]] = []
    for index, pieces in enumerate(by_slot):
        layer = Image.new("RGBA", native.size, (0, 0, 0, 0))
        out = layer.load()
        largest = max((len(piece) for piece in pieces), default=0)
        for piece in pieces:
            if len(piece) >= largest * min_share:
                for point in piece:
                    out[point] = pixels[point]
        layers.append((layer, int(round(index * step))))
    boxes = [(box[0] - left, box[1], box[2] - left, box[3]) for layer, left in layers if (box := layer.getbbox())]
    if not boxes:
        return None
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return [layer.crop((union[0] + left, union[1], union[2] + left, union[3])) for layer, left in layers]


def seed_scale(seed: Image.Image) -> int:
    """Largest integer k such that the cropped seed is made of uniform k x k blocks."""
    pixels = seed.load()
    for k in range(min(seed.size), 1, -1):
        if seed.width % k or seed.height % k:
            continue
        if all(
            pixels[x, y] == pixels[x - x % k, y - y % k]
            for y in range(seed.height)
            for x in range(seed.width)
        ):
            return k
    return 1


def main() -> None:
    args = parse_args()
    if args.frames < 1:
        raise SystemExit("--frames must be at least 1.")
    if args.frame_size < 1:
        raise SystemExit("--frame-size must be positive.")

    strip = Image.open(args.input).convert("RGBA")
    seed = Image.open(args.seed).convert("RGBA")
    seed_bbox = seed.getbbox()
    if seed_bbox is None:
        raise SystemExit("The seed frame is fully transparent.")
    seed = seed.crop(seed_bbox)
    # A shipped frame is often an integer upscale; at that size every pixel has a same-colored
    # neighbor, so orphan detection would treat single-pixel details (eyes) as noise.
    native_scale = seed_scale(seed)
    if native_scale > 1:
        seed = seed.resize((seed.width // native_scale, seed.height // native_scale), Image.Resampling.NEAREST)

    palette = sorted({seed.getpixel((x, y))[:3] for x in range(seed.width) for y in range(seed.height) if seed.getpixel((x, y))[3]})
    palette += [c for c in (parse_hex(v) for v in args.extra_color) if c not in palette]

    x_profile = edge_profile(strip, 0)
    y_profile = edge_profile(strip, 1)
    cell = args.cell_size or detect_cell(strip, x_profile, y_profile)
    if cell < 1:
        raise SystemExit("Could not detect a pixel grid; pass --cell-size.")
    x_offset = best_offset(x_profile, cell)[1]
    y_offset = best_offset(y_profile, cell)[1]

    native, deviation = resample(strip, cell, x_offset, y_offset, palette, args.alpha_threshold)
    if deviation > args.max_cell_deviation:
        raise SystemExit(
            f"No consistent pixel grid: median in-cell color deviation is {deviation:.1f} at cell {cell:.2f}px "
            f"(limit {args.max_cell_deviation:.0f}). Regenerate the strip as hard-edged upscaled pixel art."
        )
    removed = 0
    if not args.keep_orphans:
        removed = remove_orphans(native, orphan_colors(seed))

    frames = split_frames(native, args.frames)
    if frames is None:
        raise SystemExit("No sprite content was detected in the provided strip.")
    width, height = frames[0].size
    fit = min(args.frame_size // width, args.frame_size // height)
    scale = args.scale or fit
    if scale < 1 or scale > fit:
        raise SystemExit(f"Frames do not fit {args.frame_size}px at scale {scale}; largest fitting scale is {fit}.")
    bounds = [frame.getbbox() for frame in frames]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames, start=1):
        canvas = Image.new("RGBA", (args.frame_size, args.frame_size), (0, 0, 0, 0))
        upscaled = frame.resize((width * scale, height * scale), Image.Resampling.NEAREST)
        canvas.alpha_composite(upscaled, ((args.frame_size - upscaled.width) // 2, args.frame_size - upscaled.height))
        canvas.save(out_dir / f"{index:02d}.png")

    print(
        f"cell={cell:.2f}px offset=({x_offset:.2f},{y_offset:.2f}) deviation={deviation:.1f} scale={scale} "
        f"palette={len(palette)} orphans_removed={removed} shared_box={width}x{height} frame_bounds={bounds}"
    )


if __name__ == "__main__":
    main()
