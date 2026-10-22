"""Recolor a League texture by colour group, keeping its shading and detail.

Runs inside Blender (uses bpy for image IO and numpy for the maths):

    blender --background --python scripts/recolor_texture.py -- SRC.png OUT_DIR [variant ...]

or, from Blender's Python, import this file and call recolor(src, dst, RECIPES["frost"]).

The texture is split into three groups by HSV:
  body   - saturated pixels whose hue falls in BODY_HUE (Kog'Maw's blue/cyan hide and spikes)
  accent - every other saturated pixel (mouth, warm skin)
  bone   - low-saturation pixels plus warm beige ones (skull plates, claws)
Each group gets its own hue target and saturation/value multipliers. The pixel's own
value (brightness) always carries through, so painted shading and highlights survive.

The "moss" variant (moss_pixels, tuned in MOSS) is procedural instead: layered moss greens
with fuzzy noise on the hide, new-growth green spike tips, lichen-grey stone bone with dark
moss in the crevices, a deep red mouth, and the eye left as painted.
"""
import os
import sys

import numpy as np

BODY_HUE = (0.42, 0.75)   # hue range treated as "body"
BONE_SAT = 0.25           # below this saturation a pixel counts as "bone"
BEIGE_HUE = (0.03, 0.17)  # warm beige skull plates also count as "bone"...
BEIGE_SAT = 0.6           # ...while their saturation stays under this
BODY_CENTER = 0.62        # the body's typical hue; offsets from it are kept (scaled)

RECIPES = {
    "frost": {
        "body":   {"hue": 0.53, "spread": 0.4, "sat": 0.7, "val": 1.3},
        "accent": {"hue": None, "sat": 0.7, "val": 1.0},
        "bone":   {"tint": (0.82, 0.92, 1.0), "mix": 0.7, "val": 1.15},
    },
    "toxic": {
        "body":   {"hue": 0.78, "spread": 0.4, "sat": 1.1, "val": 0.7},
        "accent": {"hue": 0.30, "sat": 1.1, "val": 1.0},
        "bone":   {"tint": (0.33, 0.30, 0.38), "mix": 0.6, "val": 0.55},
    },
    "gold": {
        "body":   {"hue": 0.985, "spread": 0.3, "sat": 1.1, "val": 0.85},
        "accent": {"hue": None, "sat": 1.0, "val": 1.0},
        "bone":   {"tint": (1.0, 0.78, 0.25), "mix": 0.85, "val": 1.1},
    },
}


def rgb_to_hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0.0)
    safe = np.where(d > 1e-6, d, 1.0)
    rc, gc, bc = (mx - r) / safe, (mx - g) / safe, (mx - b) / safe
    h = np.where(r == mx, bc - gc, np.where(g == mx, 2.0 + rc - bc, 4.0 + gc - rc))
    h = np.where(d > 1e-6, (h / 6.0) % 1.0, 0.0)
    return np.stack([h, s, mx], -1)


def hsv_to_rgb(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    i = np.floor(h * 6.0).astype(int) % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    choices = [
        np.stack([v, t, p], -1), np.stack([q, v, p], -1), np.stack([p, v, t], -1),
        np.stack([p, q, v], -1), np.stack([t, p, v], -1), np.stack([v, p, q], -1),
    ]
    out = np.zeros(hsv.shape, dtype=hsv.dtype)
    for k, c in enumerate(choices):
        out = np.where((i == k)[..., None], c, out)
    return out


def recolor_pixels(rgba, recipe):
    """rgba: float array (..., 4) in 0..1. Returns a new array."""
    rgb = rgba[..., :3]
    hsv = rgb_to_hsv(rgb)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bone = (s < BONE_SAT) | ((h >= BEIGE_HUE[0]) & (h <= BEIGE_HUE[1]) & (s < BEIGE_SAT))
    body = ~bone & (h >= BODY_HUE[0]) & (h <= BODY_HUE[1])
    accent = ~bone & ~body
    out = hsv.copy()

    b = recipe["body"]
    out[..., 0] = np.where(body, (b["hue"] + (h - BODY_CENTER) * b["spread"]) % 1.0, out[..., 0])
    out[..., 1] = np.where(body, np.clip(s * b["sat"], 0, 1), out[..., 1])
    out[..., 2] = np.where(body, np.clip(v * b["val"], 0, 1), out[..., 2])

    a = recipe["accent"]
    if a["hue"] is not None:
        out[..., 0] = np.where(accent, a["hue"], out[..., 0])
    out[..., 1] = np.where(accent, np.clip(s * a["sat"], 0, 1), out[..., 1])
    out[..., 2] = np.where(accent, np.clip(v * a["val"], 0, 1), out[..., 2])

    new_rgb = hsv_to_rgb(out)
    bn = recipe["bone"]
    tinted = np.clip(rgb * (1 - bn["mix"]) + (v[..., None] * np.array(bn["tint"])) * bn["mix"], 0, 1)
    tinted = np.clip(tinted * bn["val"], 0, 1)
    new_rgb = np.where(bone[..., None], tinted, new_rgb)

    result = rgba.copy()
    result[..., :3] = new_rgb
    return result


def value_noise(h, w, seed, octaves=(8, 32, 96), weights=(0.5, 0.3, 0.2)):
    """Smooth fractal value noise in 0..1, shape (h, w). Seeded, so reruns match."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), dtype=np.float32)
    for cells, wt in zip(octaves, weights):
        grid = rng.random((cells + 1, cells + 1)).astype(np.float32)
        ys = np.linspace(0, cells, h, endpoint=False)
        xs = np.linspace(0, cells, w, endpoint=False)
        y0, x0 = ys.astype(int), xs.astype(int)
        fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
        fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
        a, b = grid[y0][:, x0], grid[y0][:, x0 + 1]
        c, d = grid[y0 + 1][:, x0], grid[y0 + 1][:, x0 + 1]
        out += wt * ((a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy)
    return out / sum(weights)


# The "moss" recipe is procedural (noise), so it lives in moss_pixels, not in RECIPES.
MOSS = {
    "seed": 7,
    "hide_dark": (0.07, 0.13, 0.04),    # moss in shadow
    "hide_mid": (0.20, 0.32, 0.08),     # forest moss
    "hide_light": (0.42, 0.48, 0.16),   # olive, sunlit moss
    "tip": (0.66, 0.80, 0.38),          # new-growth green on the bright cyan spikes
    "tip_hue": (0.42, 0.58),            # cyan band that counts as spike tip...
    "tip_val": 0.62,                    # ...when brighter than this
    "stone": (0.40, 0.44, 0.37),        # weathered grey-green stone (bone)
    "lichen": (0.58, 0.62, 0.36),       # pale lichen blotches on stone
    "crevice": (0.08, 0.17, 0.04),      # dark moss packed into bone crevices
    "crevice_val": 0.5,                 # bone darker than this (of its max) fills with moss
    "body_band": (0.30, 0.80),          # blue/teal/purple hue counts as hide
    "mouth_band": (0.80, 0.03),         # magenta..red hue (wraps through 0) counts as mouth
    "mouth_hue": 0.985,                 # deep red flesh
    "eye_sat": 0.6,                     # saturated orange is the eye and stays as painted...
    "eye_val": 0.25,                    # ...when brighter than this
    "clean": 2,                         # majority filter radius in pixels
}


def _box_sum(a, r):
    """Sum of a over a (2r+1)^2 window, edges clamped."""
    p = np.pad(a, r, mode="edge").cumsum(0).cumsum(1)
    p = np.pad(p, ((1, 0), (1, 0)))
    k = 2 * r + 1
    return p[k:, k:] - p[:-k, k:] - p[k:, :-k] + p[:-k, :-k]


def _majority(label, n, r):
    if r <= 0:
        return label
    counts = np.stack([_box_sum((label == i).astype(np.float32), r) for i in range(n)])
    return counts.argmax(0)


def _ramp3(t, a, b, c):
    t = t[..., None]
    lo = np.array(a) + (np.array(b) - np.array(a)) * np.clip(t * 2, 0, 1)
    return np.where(t < 0.5, lo, np.array(b) + (np.array(c) - np.array(b)) * np.clip(t * 2 - 1, 0, 1))


def moss_pixels(rgba, m=MOSS):
    """Mossy recolor. rgba: float (h, w, 4) in 0..1. The pixel's brightness drives every
    colour, so the painted shading stays; noise only varies the hue of the moss."""
    hp, wp = rgba.shape[:2]
    rgb = rgba[..., :3]
    h, s, v = np.moveaxis(rgb_to_hsv(rgb), -1, 0)
    # label every pixel, then a majority filter removes stray single-pixel picks
    magenta = ((h >= m["mouth_band"][0]) | (h <= m["mouth_band"][1])) & (s > 0.4)
    eye = (h > 0.0) & (h <= BEIGE_HUE[1]) & (s > m["eye_sat"]) & (v > m["eye_val"])
    cool = (h >= m["body_band"][0]) & (h <= m["body_band"][1]) & (s >= BONE_SAT)
    label = np.where(eye, 3, np.where(magenta, 2, np.where(cool, 1, 0)))  # 0 bone 1 body 2 flesh 3 eye
    label = _majority(label, 4, m["clean"])
    bone, body, flesh, eye = (label == 0), (label == 1), (label == 2), (label == 3)
    tip = body & (h >= m["tip_hue"][0]) & (h <= m["tip_hue"][1]) & (v > m["tip_val"])
    hide = body & ~tip

    big = value_noise(hp, wp, m["seed"])
    fine = value_noise(hp, wp, m["seed"] + 1, octaves=(64, 180, 400), weights=(0.3, 0.4, 0.3))
    blot = value_noise(hp, wp, m["seed"] + 2, octaves=(12, 40), weights=(0.6, 0.4))
    fuzz = (0.82 + 0.36 * fine)[..., None]          # fuzzy moss grain

    # hide: brightness picks dark -> mid -> light moss, big noise shifts the patches
    vmax_body = max(float(v[body].max()) if body.any() else 1.0, 1e-3)
    t = np.clip(v / vmax_body * 1.1 + (big - 0.5) * 0.6, 0, 1)
    hide_rgb = _ramp3(t, m["hide_dark"], m["hide_mid"], m["hide_light"]) * fuzz
    hide_rgb *= (0.6 + 0.6 * v / vmax_body)[..., None]

    tip_rgb = np.array(m["tip"]) * (v / vmax_body)[..., None] * (0.9 + 0.2 * fine[..., None])

    # bone: stone keeps the paint's value; lichen blotches; moss in the crevices
    vmax_bone = max(float(v[bone].max()) if bone.any() else 1.0, 1e-3)
    rel = v / vmax_bone
    stone = np.array(m["stone"]) * (0.2 + 0.95 * rel)[..., None] * (0.9 + 0.2 * fine[..., None])
    lichen = np.clip((blot - 0.62) * 6, 0, 1)[..., None] * 0.7
    stone = stone * (1 - lichen) + np.array(m["lichen"]) * rel[..., None] * lichen
    crev = np.clip((m["crevice_val"] - rel) / m["crevice_val"] * 1.8 + (big - 0.5), 0, 1)[..., None]
    bone_rgb = stone * (1 - crev) + np.array(m["crevice"]) * fuzz * (0.6 + rel[..., None]) * crev

    # mouth and gums: deep red
    flesh_hsv = np.stack([np.full_like(h, m["mouth_hue"]), np.clip(s * 1.1, 0, 1), v * 0.9], -1)
    flesh_rgb = hsv_to_rgb(flesh_hsv)

    out = rgb.copy()
    for mask, col in ((hide, hide_rgb), (tip, tip_rgb), (bone, bone_rgb), (flesh, flesh_rgb)):  # eye: as painted
        out = np.where(mask[..., None], col, out)
    result = rgba.copy()
    result[..., :3] = np.clip(out, 0, 1)
    return result


def recolor(src, dst, recipe):
    import bpy
    img = bpy.data.images.load(src, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    out = moss_pixels(px) if recipe == "moss" else recolor_pixels(px, recipe)
    new = bpy.data.images.new(os.path.splitext(os.path.basename(dst))[0], w, h, alpha=True)
    new.pixels = out.ravel()
    new.filepath_raw = dst
    new.file_format = 'PNG'
    new.save()
    bpy.data.images.remove(img)
    return new


def main(argv):
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(args) < 2:
        print(__doc__)
        return
    src, out_dir, *names = args
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    for name in names or list(RECIPES) + ["moss"]:
        dst = os.path.join(out_dir, "%s_%s.png" % (stem, name))
        recolor(src, dst, "moss" if name == "moss" else RECIPES[name])
        print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv)
