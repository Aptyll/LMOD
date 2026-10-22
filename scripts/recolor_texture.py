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


def recolor(src, dst, recipe):
    import bpy
    img = bpy.data.images.load(src, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    out = recolor_pixels(px, recipe)
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
    for name in names or list(RECIPES):
        dst = os.path.join(out_dir, "%s_%s.png" % (stem, name))
        recolor(src, dst, RECIPES[name])
        print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv)
