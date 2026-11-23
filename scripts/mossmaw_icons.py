"""Mossmaw ability icons: Riot's five Kog'Maw spell icons, made mossy.

Run inside Blender (bpy for image IO, Aventurine's library to read the game files):

    import importlib.util
    spec = importlib.util.spec_from_file_location("mossmaw_icons", r"...\\scripts\\mossmaw_icons.py")
    mi = importlib.util.module_from_spec(spec); spec.loader.exec_module(mi)
    paths = mi.make_icons(WAD_PATH, OUT_DIR)     # {game path: .dds file}

Each icon keeps Riot's composition, so it reads the same at a glance:
  - colours move into a moss palette by brightness; the bright, saturated "energy" (acid,
    ooze, the barrage) becomes a glowing green;
  - moss creeps in from the edges in a noisy band, with a soft shadow on its inner edge,
    and the centre stays clear;
  - one small mushroom grows in a corner (a glowing one on the ultimate).
Work is done at 4x, then box-downsampled to Riot's 64 x 64 and written as an uncompressed
BGRA .dds. Ability icons are champion-wide (not per skin), so any other mod that replaces
them for Kog'Maw conflicts with this one.
"""
import importlib.util
import os

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("recolor_texture", os.path.join(_here, "recolor_texture.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

ICONS = {   # key -> (ability, Riot file name, mushroom corner, glowing mushroom)
    "passive": ("Icathian Surprise", "kogmaw_icathiansurprise.dds", "br", False),
    "q": ("Caustic Spittle", "kogmaw_causticspittle.dds", "bl", False),
    "w": ("Bio-Arcane Barrage", "kogmaw_bioarcanebarrage.dds", "br", False),
    "e": ("Void Ooze", "kogmaw_voidooze.dds", "tl", False),
    "r": ("Living Artillery", "kogmaw_livingartillery.dds", "bl", True),
}
FOLDER = "assets/characters/kogmaw/hud/icons2d/"
UP = 4                        # work at 4x
SIZE = 64                     # Riot's icon size
SEED = 5
MOSS_BAND = 0.14              # moss reaches this far in from the edge (fraction of the icon)
PALETTE = ((0.03, 0.06, 0.02), (0.12, 0.22, 0.05), (0.34, 0.46, 0.14), (0.72, 0.80, 0.52))  # dark -> pale (sRGB)
GLOW = (0.45, 1.0, 0.55)      # energy colour (sRGB)
MOSS = ((0.05, 0.10, 0.02), (0.16, 0.28, 0.06), (0.30, 0.44, 0.10))


def _ramp(t, stops):
    t = np.clip(t, 0, 1)[..., None] * (len(stops) - 1)
    i = np.minimum(t.astype(int), len(stops) - 2)
    f = t - i
    s = np.array(stops, np.float32)
    return s[i[..., 0]] * (1 - f) + s[i[..., 0] + 1] * f


def _upscale(rgb, k):
    """Bilinear upscale by k (keeps soft painted edges; the box-downsample later undoes it)."""
    h, w = rgb.shape[:2]
    ys = (np.arange(h * k) + 0.5) / k - 0.5
    xs = (np.arange(w * k) + 0.5) / k - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, h - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, w - 1)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    fy = np.clip(ys - y0, 0, 1)[:, None, None]
    fx = np.clip(xs - x0, 0, 1)[None, :, None]
    a, b = rgb[y0][:, x0], rgb[y0][:, x1]
    c, d = rgb[y1][:, x0], rgb[y1][:, x1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _down(rgb, k):
    h, w = rgb.shape[0] // k, rgb.shape[1] // k
    return rgb.reshape(h, k, w, k, -1).mean((1, 3))


def mossify(rgb, corner, glowing, seed):
    """rgb: float sRGB (n, n, 3) top-down at the working size. Returns the mossy icon."""
    n = rgb.shape[0]
    hsv = rt.rgb_to_hsv(rgb)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    # recolour by hue, keeping every pixel's own brightness (so the icon still reads):
    # purple/magenta/blue -> moss green, greens -> a deeper moss green, reds stay red
    cool = (h > 0.52) & (h < 0.97)
    green = (h >= 0.15) & (h <= 0.52)
    nh = np.where(cool, 0.24 + 0.08 * (h - 0.52) / 0.45, np.where(green, 0.25 + 0.4 * (h - 0.15) * 0.25, h))
    ns = np.where(cool | green, np.clip(s * 0.85, 0, 1), s)
    out = rt.hsv_to_rgb(np.stack([nh, ns, v], -1))
    # the bright, saturated energy glows green
    energy = np.clip((s - 0.45) * 2.5, 0, 1) * np.clip((v - 0.5) * 2.5, 0, 1) * (cool | green)
    glow = np.array(GLOW, np.float32) * (0.6 + 0.5 * v[..., None])
    out = np.clip(out * (1 - energy[..., None]) + glow * energy[..., None], 0, 1)

    # moss creeping in from two corners (the mushroom's and its neighbour along the bottom
    # or top edge) and a little along that edge; the centre stays clear
    noise = rt.value_noise(n, n, seed, octaves=(4, 10, 28), weights=(0.5, 0.3, 0.2))
    fine = rt.value_noise(n, n, seed + 1, octaves=(40, 90), weights=(0.5, 0.5))
    yy, xx = np.mgrid[0:n, 0:n] / (n - 1)
    cy0 = 1.0 if corner[0] == "b" else 0.0
    cx0 = 1.0 if corner[1] == "r" else 0.0
    d_main = np.hypot(xx - cx0, yy - cy0)
    d_side = np.hypot(xx - (1 - cx0), yy - cy0)
    d_edge = np.abs(yy - cy0)
    reach = np.maximum.reduce([0.34 - d_main, 0.2 - d_side, MOSS_BAND * 0.55 - d_edge]) + (noise - 0.5) * 0.12
    cover = np.clip(reach / 0.02, 0, 1)
    shadow = np.clip((reach + 0.035) / 0.035, 0, 1) * (1 - cover)
    edge = np.minimum.reduce([xx, yy, 1 - xx, 1 - yy])
    moss = _ramp(0.25 + 0.75 * fine * (0.6 + 0.6 * noise), MOSS)
    out = out * (1 - 0.45 * shadow[..., None])
    out = out * (1 - cover[..., None]) + moss * cover[..., None]

    # a small mushroom in one corner
    r = 0.085 * n
    cx = {"l": 0.17, "r": 0.83}[corner[1]] * n
    cy = {"t": 0.22, "b": 0.80}[corner[0]] * n
    py, px = np.mgrid[0:n, 0:n] + 0.5
    stem = (np.abs(px - cx) < r * 0.28) & (py > cy) & (py < cy + r * 1.3)
    cap = (((px - cx) / r) ** 2 + ((py - cy) / (r * 0.62)) ** 2 < 1) & (py < cy + r * 0.12)
    rim = cap & (((px - cx) / r) ** 2 + ((py - cy) / (r * 0.62)) ** 2 > 0.72)
    cap_col = np.array((0.25, 0.95, 0.65) if glowing else (0.86, 0.45, 0.14), np.float32)
    shade = np.clip(1.15 - (py - (cy - r * 0.6)) / (r * 1.4), 0.55, 1.15)[..., None]
    out = np.where(stem[..., None], np.array((0.88, 0.84, 0.70), np.float32) * 0.9, out)
    out = np.where(cap[..., None], np.clip(cap_col * shade, 0, 1), out)
    out = np.where(rim[..., None], cap_col * 0.55, out)
    if glowing:   # a soft halo around the glowing cap
        halo = np.exp(-(((px - cx) / (r * 1.8)) ** 2 + ((py - cy) / (r * 1.5)) ** 2))[..., None]
        out = np.clip(out + np.array(GLOW, np.float32) * 0.35 * halo * (~cap)[..., None], 0, 1)
    # Riot's icons have a thin dark rim; keep one
    border = (edge * n < 1.2 * UP)[..., None]
    out = np.where(border, out * 0.45, out)
    return np.clip(out, 0, 1)


def write_dds(path, rgba_top_down):
    """Uncompressed 32-bit BGRA .dds, one level."""
    img = np.clip(rgba_top_down * 255 + 0.5, 0, 255).astype(np.uint8)
    h, w = img.shape[:2]
    header = bytearray(128)
    header[0:4] = b"DDS "
    fields = [124, 0x1 | 0x2 | 0x4 | 0x8 | 0x1000, h, w, w * 4, 0, 1]
    for i, f in enumerate(fields):
        header[4 + 4 * i:8 + 4 * i] = f.to_bytes(4, "little")
    pf = [32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000]   # RGB + alpha, BGRA order
    for i, f in enumerate(pf):
        header[76 + 4 * i:80 + 4 * i] = f.to_bytes(4, "little")
    header[108:112] = (0x1000).to_bytes(4, "little")
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(img[..., [2, 1, 0, 3]].tobytes())
    return path


def make_icons(wad_path, out_dir, riot_dir=None):
    """Read Riot's icons from the WAD, mossify them, write .dds (and a copy of Riot's in
    riot_dir if given). Returns {game path: our .dds}, plus the before/after pixels."""
    rs = importlib.import_module("Aventurine.vendor.ritoshark")
    w = rs.Wad.from_path(wad_path)
    os.makedirs(out_dir, exist_ok=True)
    paths, pairs = {}, {}
    for i, (key, (name, fname, corner, glowing)) in enumerate(ICONS.items()):
        game = FOLDER + fname
        data = bytes(w.read_path(game))
        d = rs.Dds.from_bytes(data)
        src = np.frombuffer(d.rgba, np.uint8).reshape(d.height, d.width, 4).astype(np.float32) / 255
        big = _upscale(src[..., :3], UP)
        moss = _down(mossify(big, corner, glowing, SEED + i), UP)
        rgba = np.concatenate([moss, np.ones(moss.shape[:2] + (1,), np.float32)], -1)
        paths[game] = write_dds(os.path.join(out_dir, fname), rgba)
        pairs[key] = (src, rgba)
        if riot_dir:
            with open(os.path.join(riot_dir, fname), "wb") as f:
                f.write(data)
    return paths, pairs
