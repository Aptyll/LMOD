"""FrostMaw art: champion icon and loading-screen portrait, rendered from the built skin.

Run headless on a built FrostMaw file (frostmaw.py saves work/frostmaw/frostmaw_vN.blend):

    blender -b work/frostmaw/frostmaw_v2.blend --python scripts/frostmaw_art.py -- OUT_DIR [EXPORT_DIR]

With EXPORT_DIR (e.g. work/frostmaw/export_v2) the mod there is repackaged with the art.

Writes transparent renders (icon_raw.png, loadscreen_raw.png) and then composites them with
numpy into the finished pieces at preview size and at the game's sizes:
  frostmaw_icon_square.png / _circle.png    128 x 128 (in game: kogmaw_square_0 / kogmaw_circle_0)
  frostmaw_loadscreen.png                    308 x 560 (in game: kogmawloadscreen)
plus a .tex of each (uncompressed BGRA8, like the skin texture). The render is the game-culled
export mesh, so the art shows the skin as it is in game.
"""
import math
import os
import sys

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("frostmaw", os.path.join(_here, "frostmaw.py"))
fm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fm)
mm, mx = fm.mm, fm.mx

SEED = 31
NAVY = np.array((0.010, 0.030, 0.085))
DEEP = np.array((0.035, 0.140, 0.300))
ICE = np.array((0.560, 0.940, 1.000))


def _bone_head(rig, name):
    return rig.matrix_world @ rig.data.bones[name].head_local


def _render(path, size, cam_loc, target, lens, action, frame):
    import bpy
    s = bpy.context.scene
    mm._set_action(action)
    s.frame_set(frame)
    s.render.resolution_x, s.render.resolution_y = size
    s.render.film_transparent = True
    s.camera.data.lens = lens
    mm._aim(s.camera, cam_loc, target)
    s.render.image_settings.file_format = 'PNG'
    s.render.image_settings.color_mode = 'RGBA'
    s.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    return px[::-1]   # top row first


def _lights():
    """Brighter, colder light than the turntable: a white key, an icy rim from behind."""
    import bpy
    s = bpy.context.scene
    for name, energy, color in (("Key", 3.0, (0.92, 0.97, 1.0)), ("Fill", 1.3, (0.65, 0.78, 1.0)),
                                ("Rim", 6.0, (0.45, 0.88, 1.0))):
        ob = s.objects.get(name)
        if ob:
            ob.data.energy, ob.data.color = energy, color
    g = s.objects.get("Ground")
    if g:
        g.hide_render = True


# ------ 2D compositing (numpy only; images are float RGB(A), top row first)

def _grid(h, w):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return y / max(h - 1, 1), x / max(w - 1, 1)


def _blur(a, r):
    """Box blur (three passes ~ gaussian), any channel count."""
    if r < 1:
        return a
    out = a.astype(np.float32)
    for _ in range(3):
        for axis in (0, 1):
            c = np.cumsum(np.pad(out, [(r + 1, r) if i == axis else (0, 0) for i in range(out.ndim)], mode="edge"),
                          axis=axis)
            hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
            lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
            out = (hi - lo) / (2 * r + 1)
    return out


def _background(h, w, rng, focus=(0.42, 0.5)):
    yy, xx = _grid(h, w)
    d = np.sqrt(((yy - focus[0]) * 1.0) ** 2 + ((xx - focus[1]) * w / h) ** 2)
    t = np.clip(1 - d / 0.9, 0, 1) ** 1.6
    bg = NAVY + (DEEP - NAVY) * t[..., None]
    # aurora: two soft wavy bands across the top
    for k, (amp, y0, col) in enumerate(((0.05, 0.18, (0.20, 0.85, 0.80)), (0.04, 0.28, (0.40, 0.55, 1.0)))):
        phase = rng.uniform(0, 6.28)
        wave = y0 + amp * np.sin(xx * 6.0 + phase) + 0.02 * np.sin(xx * 17.0 + phase * 2)
        band = np.exp(-((yy - wave) / 0.035) ** 2) * (0.35 - 0.1 * k)
        bg = bg + band[..., None] * np.array(col)
    # cold glow on the ground behind his feet
    glow = np.exp(-(((yy - 0.86) / 0.07) ** 2 + ((xx - 0.5) / 0.45) ** 2))
    return np.clip(bg + glow[..., None] * ICE * 0.22, 0, 1)


def _snow(h, w, rng, count, big=6):
    """Soft snow dots plus a few six-armed sparkles. Returns an additive RGB layer."""
    layer = np.zeros((h, w), np.float32)
    ys, xs = rng.integers(0, h, count), rng.integers(0, w, count)
    layer[ys, xs] = rng.uniform(0.4, 1.0, count)
    layer = _blur(layer, 1) * 4
    star = np.zeros((h, w), np.float32)
    for _ in range(big):
        cy, cx = rng.integers(int(h * 0.05), int(h * 0.6)), rng.integers(0, w)
        r = rng.integers(max(h // 90, 3), max(h // 45, 5))
        for a in range(6):
            ang = a * math.pi / 3
            for t in np.linspace(0, 1, 3 * r):
                y, x = int(cy + math.sin(ang) * r * t), int(cx + math.cos(ang) * r * t)
                if 0 <= y < h and 0 <= x < w:
                    star[y, x] = 1.0 - 0.6 * t
    star = _blur(star, 1) * 2.5
    return np.clip(layer + star, 0, 1)[..., None] * np.array((0.85, 0.95, 1.0))


def _over(bg, fg):
    a = fg[..., 3:4]
    return bg * (1 - a) + fg[..., :3] * a


def _rim_glow(fg, r, strength, color=ICE):
    """An icy glow around the silhouette, behind him."""
    a = _blur(fg[..., 3], r)
    return a[..., None] * np.array(color) * strength


def _frame(img, width, color=(0.60, 0.90, 1.0), inner=(0.10, 0.25, 0.45)):
    h, w = img.shape[:2]
    out = img.copy()
    out[:width], out[-width:], out[:, :width], out[:, -width:] = color, color, color, color
    k = max(width // 2, 1)
    out[width:width + k, width:-width] = inner
    out[-width - k:-width, width:-width] = inner
    out[width:-width, width:width + k] = inner
    out[width:-width, -width - k:-width] = inner
    return out


def _resize(img, size):
    """Area resize with numpy (integer factors work best)."""
    h, w = img.shape[:2]
    W, H = size
    ys = (np.arange(H + 1) * h / H).astype(int)
    xs = (np.arange(W + 1) * w / W).astype(int)
    c = np.pad(img, ((1, 0), (1, 0), (0, 0))).cumsum(0).cumsum(1)
    tot = c[ys[1:]][:, xs[1:]] - c[ys[:-1]][:, xs[1:]] - c[ys[1:]][:, xs[:-1]] + c[ys[:-1]][:, xs[:-1]]
    area = np.outer(ys[1:] - ys[:-1], xs[1:] - xs[:-1])[..., None]
    return tot / np.maximum(area, 1)


def _save_png(path, rgba_top_first):
    import bpy
    a = rgba_top_first
    if a.shape[2] == 3:
        a = np.concatenate([a, np.ones(a.shape[:2] + (1,), np.float32)], -1)
    h, w = a.shape[:2]
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=True)
    img.pixels = np.ascontiguousarray(a[::-1]).ravel()
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)
    return path


def make_icon(raw, rng):
    """Square and round icons from the head close-up render (raw: RGBA, sRGB colours)."""
    h, w = raw.shape[:2]
    bg = _background(h, w, rng, focus=(0.45, 0.5))
    bg = bg + _rim_glow(raw, max(h // 40, 2), 0.55)
    img = _over(np.clip(bg + _snow(h, w, rng, h // 6, big=2) * 0.6, 0, 1), raw)
    yy, xx = _grid(h, w)
    vign = 1 - 0.35 * np.clip(np.sqrt((yy - 0.5) ** 2 + (xx - 0.5) ** 2) / 0.7, 0, 1) ** 2
    square = _frame(np.clip(img * vign[..., None], 0, 1), max(h // 64, 2))
    d = np.sqrt((yy - 0.5) ** 2 + (xx - 0.5) ** 2)
    ring = ((d > 0.47) & (d <= 0.5)).astype(np.float32)[..., None]
    circle = np.concatenate([img * (1 - ring) + np.array((0.60, 0.90, 1.0)) * ring,
                             np.clip((0.5 - d) * w, 0, 1)[..., None]], -1)
    return square, circle


def _ground(h, w, raw):
    """An icy floor under his feet: a soft contact shadow inside a cold glow, placed from the
    bottom of his silhouette, so he stands instead of floating."""
    rows = np.where(raw[..., 3].max(1) > 0.5)[0]
    cols = np.where(raw[..., 3].max(0) > 0.5)[0]
    if not len(rows):
        return np.zeros((h, w, 3)), np.ones((h, w))
    fy = rows[-1] / h
    cx = (cols[0] + cols[-1]) / 2 / w
    half = (cols[-1] - cols[0]) / 2 / w
    yy, xx = _grid(h, w)
    e = ((yy - fy) / 0.035) ** 2 + ((xx - cx) / (half * 1.05)) ** 2
    shadow = 1 - 0.6 * np.exp(-e * 1.5)
    floor = np.exp(-(((yy - fy - 0.01) / 0.09) ** 2 + ((xx - cx) / (half * 1.9)) ** 2))
    below = np.clip((yy - fy + 0.03) / 0.25, 0, 1) * np.exp(-((xx - cx) / 0.8) ** 2)
    return floor[..., None] * ICE * 0.35 + below[..., None] * np.array((0.10, 0.22, 0.35)) * 0.6, shadow


def make_loadscreen(raw, rng):
    h, w = raw.shape[:2]
    bg = _background(h, w, rng, focus=(0.55, 0.5))
    glow, shadow = _ground(h, w, raw)
    bg = np.clip(bg + glow, 0, 1) * shadow[..., None]
    bg = np.clip(bg + _snow(h, w, rng, h // 2, big=7) * 0.8, 0, 1)
    bg = bg + _rim_glow(raw, max(h // 60, 2), 0.7)
    img = _over(np.clip(bg, 0, 1), raw)
    # snow in front of him too, sparser and softer
    img = np.clip(img + _blur(_snow(h, w, rng, h // 6, big=0), 1) * 0.5, 0, 1)
    yy, xx = _grid(h, w)
    vign = 1 - 0.45 * np.clip(np.sqrt(((yy - 0.55) * 0.8) ** 2 + (xx - 0.5) ** 2) / 0.7, 0, 1) ** 2
    return _frame(np.clip(img * vign[..., None], 0, 1), max(w // 100, 2))


def main(argv):
    import bpy
    out = argv[argv.index("--") + 1]
    os.makedirs(out, exist_ok=True)
    rng = np.random.default_rng(SEED)
    rig = bpy.data.objects[fm.RIG]
    s = bpy.context.scene
    _lights()
    s.view_settings.view_transform = 'Standard'
    head = _bone_head(rig, "C_Head")
    eye = _bone_head(rig, "R_Eye") if "R_Eye" in rig.data.bones else head
    # icon: head and back crystals, from the front-right, a little above
    tgt = (head + eye) / 2
    icon_cam = (tgt.x + 2.0, tgt.y - 3.9, tgt.z + 1.0)
    raw_icon = _render(os.path.join(out, "icon_raw.png"), (512, 512), icon_cam,
                       (tgt.x + 0.05, tgt.y + 0.15, tgt.z + 0.05), 85, "kogmaw_idle1", 20)
    # loading screen: whole body, low and to the left, looking up at him mid-spit
    act = "kogmaw_attack1" if "kogmaw_attack1" in bpy.data.actions else mm.HERO_ACTION
    raw_load = _render(os.path.join(out, "loadscreen_raw.png"), (616, 1120), (-3.0, -5.6, 0.9),
                       (0.0, -0.15, 1.55), 40, act, 18)
    square, circle = make_icon(raw_icon, rng)
    load = make_loadscreen(raw_load, rng)
    files = {}
    for name, img, size in (("frostmaw_icon_square", square, (128, 128)),
                            ("frostmaw_icon_circle", circle, (128, 128)),
                            ("frostmaw_loadscreen", load, (308, 560))):
        files[name + "_preview"] = _save_png(os.path.join(out, name + "_preview.png"), img)
        small = _resize(img, size)
        files[name] = _save_png(os.path.join(out, name + ".png"), small)
        if small.shape[2] == 3:
            small = np.concatenate([small, np.ones(small.shape[:2] + (1,), np.float32)], -1)
        files[name + "_tex"] = mx.write_tex(os.path.join(out, name + ".tex"), np.ascontiguousarray(small[::-1]))
    args = argv[argv.index("--") + 1:]
    if len(args) > 1:   # an export dir: repackage the mod with the art in it
        files["mod"] = fm.package_mod(args[1], out)
    print("ART", files)
    return files


if __name__ == "__main__" and "--" in sys.argv:
    main(sys.argv)
