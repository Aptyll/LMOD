"""Frost versions of a champion's ability icons (FrostMaw).

    python scripts/frost_icons.py IN_DIR OUT_DIR

IN_DIR holds the champion's ability icons as extracted from the game (.dds or .png, any size).
Each icon is gradient-mapped onto a frost palette by brightness, so Riot's shapes and shading
stay: darks go navy, mids glacier blue, lights pale cyan and the brightest white. Pink and red
parts become violet (FrostMaw's mouth colour), and green glow becomes cyan glow. Then a rime of
frost creeps in from the corners and a few ice sparkles are added.

Writes OUT_DIR/<name>.dds (game size, same name as the source, for the mod), <name>.png, and
frost_icons_preview.png (originals over frost versions, 4x). Riot-derived: never commit the
outputs. Needs numpy and Pillow (12+ for DXT1 .dds; otherwise uncompressed .dds).
"""
import glob
import os
import sys

import numpy as np
from PIL import Image

SEED = 41
FROST = ((0.015, 0.035, 0.110), (0.060, 0.220, 0.470), (0.300, 0.650, 0.930), (0.700, 0.940, 1.000),
         (1.000, 1.000, 1.000))
VIOLET = ((0.040, 0.015, 0.100), (0.250, 0.110, 0.520), (0.560, 0.420, 0.950), (0.900, 0.850, 1.000))
GLOW = ((0.020, 0.120, 0.220), (0.100, 0.600, 0.850), (0.550, 0.980, 1.000), (1.000, 1.000, 1.000))
ORDER = ("icathiansurprise", "causticspittle", "bioarcanebarrage", "voidooze", "livingartillery")


def _ramp(t, stops):
    pos = np.linspace(0, 1, len(stops))
    return np.stack([np.interp(t, pos, [s[i] for s in stops]) for i in range(3)], -1)


def _hsv(rgb):
    mx, mn = rgb.max(-1), rgb.min(-1)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    safe = np.where(d > 1e-6, d, 1)
    h = np.where(mx == r, (g - b) / safe, np.where(mx == g, 2 + (b - r) / safe, 4 + (r - g) / safe))
    return np.where(d > 1e-6, (h / 6) % 1, 0), s, mx


def _smooth_noise(h, w, rng, cells):
    g = rng.random((cells + 1, cells + 1))
    ys, xs = np.linspace(0, cells, h, endpoint=False), np.linspace(0, cells, w, endpoint=False)
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    a, b, c, d = g[y0][:, x0], g[y0][:, x0 + 1], g[y0 + 1][:, x0], g[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def frost(rgba, rng):
    rgb = rgba[..., :3]
    h, w = rgb.shape[:2]
    hue, sat, val = _hsv(rgb)
    lum = rgb @ np.array((0.299, 0.587, 0.114))
    t = np.clip((lum - 0.03) / 0.85, 0, 1) ** 0.85
    out = _ramp(t, FROST)
    magenta = ((hue > 0.75) | (hue < 0.04)) & (sat > 0.35)
    green = (hue > 0.17) & (hue < 0.45) & (sat > 0.45)
    eyes = ((hue < 0.03) | (hue > 0.985)) & (sat > 0.6)      # deep red eyes -> glowing cyan, like the skin
    out = np.where(magenta[..., None], _ramp(np.clip(val * 1.05, 0, 1), VIOLET), out)
    out = np.where((green | eyes)[..., None], _ramp(np.clip(val * 1.1 + 0.15 * eyes, 0, 1), GLOW), out)
    # rime: frost creeping in from the corners, broken up by noise
    yy, xx = np.mgrid[0:h, 0:w] / np.array([h - 1, w - 1])[:, None, None]
    corner = np.minimum.reduce([np.hypot(yy, xx), np.hypot(yy, 1 - xx), np.hypot(1 - yy, xx), np.hypot(1 - yy, 1 - xx)])
    n = _smooth_noise(h, w, rng, 6) * 0.6 + _smooth_noise(h, w, rng, 16) * 0.4
    rime = np.clip((0.17 - corner + (n - 0.5) * 0.2) / 0.08, 0, 1) ** 1.5
    out = out * (1 - 0.45 * rime[..., None]) + np.array((0.86, 0.95, 1.0)) * 0.45 * rime[..., None]
    # sparkles on the bright parts
    for _ in range(3):
        bright = np.argwhere(t > 0.75)
        if not len(bright):
            break
        cy, cx = bright[rng.integers(len(bright))]
        r = max(h // 16, 2)
        for k in range(-r, r + 1):
            f = 1 - abs(k) / (r + 1)
            for y, x in ((cy + k, cx), (cy, cx + k)):
                if 0 <= y < h and 0 <= x < w:
                    out[y, x] = out[y, x] * (1 - f) + f
    res = rgba.copy()
    res[..., :3] = np.clip(out, 0, 1)
    return res


def _load(path):
    return np.asarray(Image.open(path).convert("RGBA"), np.float32) / 255


def _save_dds(path, rgba):
    im = Image.fromarray((np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")
    try:
        im.convert("RGB").save(path, pixel_format="DXT1")   # same format as the game's icons
    except (ValueError, OSError, KeyError, TypeError):
        im.save(path)                                        # uncompressed BGRA
    return path


def main(in_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(SEED)
    found = sorted(glob.glob(os.path.join(in_dir, "*.dds")) + glob.glob(os.path.join(in_dir, "*.png")))
    names = {}
    for p in found:
        stem = os.path.splitext(os.path.basename(p))[0]
        key = next((k for k in ORDER if k in stem.lower()), None)
        if key and key not in names:
            names[key] = p
    pairs = []
    for key in ORDER:
        if key not in names:
            continue
        src = _load(names[key])
        new = frost(src, rng)
        stem = os.path.splitext(os.path.basename(names[key]))[0]
        _save_dds(os.path.join(out_dir, stem + ".dds"), new)
        Image.fromarray((new * 255 + 0.5).astype(np.uint8), "RGBA").save(os.path.join(out_dir, stem + ".png"))
        pairs.append((src, new))
    if pairs:
        s = 4
        tile = pairs[0][0].shape[0] * s
        sheet = Image.new("RGB", (tile * len(pairs) + 8 * (len(pairs) + 1), tile * 2 + 24), (16, 20, 28))
        for i, (a, b) in enumerate(pairs):
            for row, img in enumerate((a, b)):
                im = Image.fromarray((img[..., :3] * 255 + 0.5).astype(np.uint8)).resize((tile, tile), Image.NEAREST)
                sheet.paste(im, (8 + i * (tile + 8), 8 + row * (tile + 8)))
        sheet.save(os.path.join(out_dir, "frost_icons_preview.png"))
    return [k for k in ORDER if k in names]


if __name__ == "__main__":
    print(main(sys.argv[1], sys.argv[2]))
