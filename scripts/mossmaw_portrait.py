"""Mossmaw portrait: a loading-screen "photo" of Mossmaw in a mossy forest, plus HUD icons.

Run inside Blender with work/mossmaw.blend open (after mossmaw.build_moss):

    import importlib.util
    spec = importlib.util.spec_from_file_location("mossmaw_portrait", r"...\\scripts\\mossmaw_portrait.py")
    mp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mp)
    mp.render_portrait(OUT_PNG)                 # full-res photo, POSE frame, forest set
    mp.make_cards(OUT_PNG, UI_BASE_DIR, OUT_DIR)  # loadscreen + circle + square, game sizes

The set lives in its own scene ("Portrait") that links the same Mossmaw objects, so the main
scene's lights, ground and camera are untouched. Everything is seeded.

Game files this replaces (sizes match Riot's exactly):
  assets/characters/kogmaw/skins/base/kogmawloadscreen.tex   308 x 560, the loading-screen card
  assets/characters/kogmaw/hud/kogmaw_circle_0.tex           128 x 128, circle icon (alpha mask kept)
  assets/characters/kogmaw/hud/kogmaw_square_0.tex           128 x 128, square icon
Champ select's splash comes from the Riot client, not the game WAD, so it stays Riot's.
"""
import importlib.util
import math
import os
import random

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_here, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mm = _load("mossmaw")

SEED = 11
SCENE = "Portrait"
POSE = ("kogmaw_attack1", 14)         # head thrust forward, mouth roaring, antennae flared
CARD = (308, 560)                     # loading-screen card size in game
SCALE = 4                             # render at 4x the card, then box-downsample
CAMERA = ((1.4, -6.0, 1.15), (0.0, -1.0, 1.0), 35)     # location, target, lens (mm): low and heroic
FOREGROUND = ((-1.0, -3.4), (1.25, -3.7), (0.15, -4.1))   # ferns between him and the camera, out of focus
EXPOSURE = 0.6
ICON_CENTRE = (0.46, 0.50)            # where his face sits in the photo (x, y from top), for icons
ICON_SIZE = 0.74                      # icon crop width, as a fraction of the photo width
GAME = {
    "loadscreen": "assets/characters/kogmaw/skins/base/kogmawloadscreen.tex",
    "circle": "assets/characters/kogmaw/hud/kogmaw_circle_0.tex",
    "square": "assets/characters/kogmaw/hud/kogmaw_square_0.tex",
}
FOG = 0.012                           # world volume density
GLOW = ((0.05, 1.0, 0.45), 6.0)


# ------ set


def _scene():
    """The Portrait scene, linking Mossmaw's body, rig and moss from the main scene."""
    import bpy
    main = bpy.data.scenes[0] if bpy.data.scenes[0].name != SCENE else bpy.data.scenes[1]
    sc = bpy.data.scenes.get(SCENE) or bpy.data.scenes.new(SCENE)
    for name in (mm.BODY, mm.RIG):
        ob = bpy.data.objects[name]
        if ob.name not in sc.collection.objects:
            sc.collection.objects.link(ob)
    moss = bpy.data.collections["Moss"]
    if moss.name not in sc.collection.children:
        sc.collection.children.link(moss)
    sc.render.engine = main.render.engine
    sc.view_settings.view_transform = main.view_settings.view_transform
    sc.view_settings.exposure = EXPOSURE
    sc.render.fps = main.render.fps
    return sc


def _clear(coll):
    import bpy
    for o in list(coll.objects):
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)


def _link(coll, ob):
    coll.objects.link(ob)
    return ob


def _trunk(name, r, h, rng):
    """A leaning, tapering 8-sided trunk with a flared foot."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    rings = []
    lean = mathutils.Vector((rng.uniform(-0.15, 0.15), rng.uniform(-0.1, 0.1), 0))
    for k in range(7):
        t = k / 6
        rad = r * (1.6 - 0.6 * t if k < 2 else 1.0 - 0.3 * t)
        c = mathutils.Vector((0, 0, h * t)) + lean * h * t * t
        rings.append([bm.verts.new(c + mathutils.Vector((math.cos(a) * rad * rng.uniform(0.9, 1.1),
                                                         math.sin(a) * rad * rng.uniform(0.9, 1.1), 0)))
                      for a in [i * math.pi / 4 for i in range(8)]])
    for a, b in zip(rings, rings[1:]):
        for i in range(8):
            f = bm.faces.new((a[i], a[(i + 1) % 8], b[(i + 1) % 8], b[i]))
            f.smooth = True
    return mm._new_obj(name, bm)


def _rock(name, r, rng):
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r)
    for v in bm.verts:
        v.co *= 1 + rng.uniform(-0.2, 0.2)
        v.co.z *= 0.55
    for f in bm.faces:
        f.smooth = True
    return mm._new_obj(name, bm)


def _ground(name, radius):
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=40, y_segments=40, size=radius)
    rng = random.Random(SEED)
    for v in bm.verts:
        d = v.co.length
        v.co.z = 0.08 * math.sin(v.co.x * 1.3 + rng.random()) * math.cos(v.co.y * 1.1) * min(d / 3, 1) - 0.01
    for f in bm.faces:
        f.smooth = True
    return mm._new_obj(name, bm)


def build_set(seed=SEED):
    """Forest floor, trunks into the fog, ferns, rocks, glowing mushrooms, light shafts."""
    import bpy
    import mathutils
    sc = _scene()
    rng = random.Random(seed)
    coll = bpy.data.collections.get("PortraitSet") or bpy.data.collections.new("PortraitSet")
    if coll.name not in sc.collection.children:
        sc.collection.children.link(coll)
    _clear(coll)

    floor_mat = mm._material("ForestFloor", (0.06, 0.11, 0.025), (0.02, 0.04, 0.012), 6.0,
                             variants=None, fuzz=0.2, bump=0.3)
    bark = mm._material("Bark", (0.07, 0.06, 0.045), (0.025, 0.022, 0.018), 9.0, bump=0.6)
    moss = bpy.data.materials["Moss"]
    fern = bpy.data.materials["Fern"]
    glow_mats = [bpy.data.materials[n] for n in ("MushroomStem", "GlowCap", "GlowGills", "GlowSpot")]

    g = _link(coll, _ground("ForestFloor", 14))
    g.data.materials.append(floor_mat)

    # trunks: a ring behind him, fading into the fog
    for i in range(9):
        a = math.radians(rng.uniform(35, 145))
        d = rng.uniform(4.5, 11)
        t = _link(coll, _trunk("Trunk.%02d" % i, rng.uniform(0.35, 0.7), rng.uniform(9, 14), rng))
        t.location = (math.cos(a) * d * 1.3, math.sin(a) * d + 1.5, -0.05)
        t.data.materials.append(bark)
        t.data.materials.append(moss)

    # mossy rocks and ferns around him, more in front for depth
    for i in range(7):
        rk = _link(coll, _rock("Rock.%02d" % i, rng.uniform(0.3, 0.8), rng))
        a, d = rng.uniform(0, 2 * math.pi), rng.uniform(2.2, 5)
        rk.location = (math.cos(a) * d, math.sin(a) * d, -0.1)
        rk.data.materials.append(moss)
    for i in range(18):
        f = _link(coll, mm._fern("SetFern.%02d" % i, rng.uniform(0.6, 1.1), rng))
        a, d = rng.uniform(0, 2 * math.pi), rng.uniform(1.8, 6)
        f.location = (math.cos(a) * d, math.sin(a) * d - 0.5, 0.0)
        f.rotation_euler = (0, 0, rng.uniform(0, 6.28))
        f.data.materials.append(fern)

    for i, (x, y) in enumerate(FOREGROUND):
        f = _link(coll, mm._fern("NearFern.%02d" % i, rng.uniform(0.7, 0.95), rng))
        f.location = (x, y, 0.0)
        f.rotation_euler = (0, 0, rng.uniform(0, 6.28))
        f.data.materials.append(fern)

    # glowing mushroom clusters on the floor, each with a little light
    for i, (x, y) in enumerate([(-2.3, -1.6), (2.4, -1.0), (-2.6, 1.2), (2.7, 1.8), (-1.2, 2.6), (-0.55, -3.0)]):
        m = _link(coll, mm._mushrooms("SetGlow.%02d" % i, rng, rng.randint(3, 5), size=rng.uniform(1.6, 2.4), spots=5))
        m.location = (x, y, 0.0)
        for mat in glow_mats:
            m.data.materials.append(mat)
        lt = bpy.data.lights.new("SetGlowLight.%02d" % i, 'POINT')
        lt.energy, lt.color, lt.shadow_soft_size = 25.0, GLOW[0], 0.1
        lo = _link(coll, bpy.data.objects.new(lt.name, lt))
        lo.location = (x, y, 0.35)

    # light: a high cold moon key, a warm low rim, and three shafts through the fog
    def light(name, kind, energy, color, loc, target, size=0.2, spot=None):
        lt = bpy.data.lights.new(name, kind)
        lt.energy, lt.color = energy, color
        if kind == 'SUN':
            lt.angle = size
        else:
            lt.shadow_soft_size = size
        if spot:
            lt.spot_size, lt.spot_blend = spot
        lo = _link(coll, bpy.data.objects.new(name, lt))
        lo.location = loc
        d = mathutils.Vector(target) - mathutils.Vector(loc)
        lo.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
        return lo

    light("Moon", 'SUN', 2.5, (0.65, 0.8, 1.0), (3, -4, 10), (0, 0, 0), math.radians(4))
    light("Key", 'SPOT', 3500, (1.0, 0.92, 0.8), (3.0, -4.0, 4.0), (0, -0.8, 1.5), 0.6, (math.radians(40), 0.5))
    light("Rim", 'SPOT', 3000, (1.0, 0.85, 0.55), (-2.5, 4.5, 4.5), (0, -0.4, 1.6), 0.3, (math.radians(35), 0.4))
    for i, (x, y) in enumerate([(-1.2, 3.0), (1.6, 2.2), (0.2, 5.0)]):
        light("Shaft.%d" % i, 'SPOT', 6000, (0.85, 1.0, 0.7), (x * 2.2, y + 3, 13),
              (x * 0.6, y * 0.4, 0), 0.05, (math.radians(9), 0.15))
    light("Face", 'SPOT', 900, (0.6, 1.0, 0.75), (-1.8, -4.0, 0.9), (0, -0.9, 1.6), 0.4, (math.radians(30), 0.6))

    # world: deep green-blue with fog
    w = bpy.data.worlds.get("PortraitWorld") or bpy.data.worlds.new("PortraitWorld")
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.012, 0.022, 0.02, 1)
    bg.inputs["Strength"].default_value = 1.0
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = FOG
    vol.inputs["Color"].default_value = (0.7, 0.85, 0.75, 1)
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    sc.world = w
    ee = sc.eevee
    for attr, val in (("use_volumetric_shadows", True), ("volumetric_tile_size", '4'),
                      ("volumetric_end", 40.0), ("volumetric_samples", 96)):
        if hasattr(ee, attr):
            setattr(ee, attr, val)

    cam_data = bpy.data.cameras.get("PortraitCam") or bpy.data.cameras.new("PortraitCam")
    cam = bpy.data.objects.get("PortraitCam") or bpy.data.objects.new("PortraitCam", cam_data)
    if cam.name not in sc.collection.objects:
        sc.collection.objects.link(cam)
    loc, target, lens = CAMERA
    cam.data.lens = lens
    cam.data.dof.use_dof = True
    cam.data.dof.focus_distance = (mathutils.Vector(loc) - mathutils.Vector(target)).length
    cam.data.dof.aperture_fstop = 2.8
    mm._aim(cam, loc, target)
    sc.camera = cam
    sc.render.resolution_x, sc.render.resolution_y = CARD[0] * SCALE, CARD[1] * SCALE
    return sc


# ------ render


def _vignette(rgba, strength=0.55):
    """Darken the edges like Riot's cards: an oval falloff plus a thin dark inner border."""
    h, w = rgba.shape[:2]
    y, x = np.mgrid[0:h, 0:w]
    dx, dy = (x / (w - 1) - 0.5) / 0.5, (y / (h - 1) - 0.5) / 0.5
    oval = np.clip(1 - strength * np.clip(np.sqrt(dx * dx * 1.1 + dy * dy * 0.8) - 0.45, 0, 1) ** 1.5, 0, 1)
    edge = np.minimum.reduce([x, y, w - 1 - x, h - 1 - y]) / (0.035 * w)
    frame = np.clip(0.45 + 0.55 * edge, 0, 1)
    out = rgba.copy()
    out[..., :3] *= (oval * frame)[..., None]
    return out


def render_portrait(out_png, pose=POSE):
    """Render the photo at SCALE x the card size, with the vignette. Returns the pixels
    (Blender row order, bottom-up)."""
    import bpy
    sc = build_set()
    # Animation is only evaluated for the scene the window shows; rendering another scene
    # would show the main scene's last pose. So show the Portrait scene while rendering.
    win = bpy.context.window_manager.windows[0]
    main = win.scene
    win.scene = sc
    try:
        mm._set_action(pose[0])
        sc.frame_set(pose[1])
        sc.render.image_settings.file_format = 'PNG'
        sc.render.filepath = out_png
        bpy.ops.render.render(write_still=True)
    finally:
        win.scene = main
    img = bpy.data.images.load(out_png, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    px = _vignette(px)
    _save_png(out_png, px)
    return px


def _save_png(path, rgba):
    import bpy
    h, w = rgba.shape[:2]
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=True)
    img.pixels = np.clip(rgba, 0, 1).ravel()
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)
    return path


def _box_down(rgba, w, h):
    """Area-average downsample to exactly w x h."""
    H, W = rgba.shape[:2]
    ys = np.linspace(0, H, h + 1).astype(int)
    xs = np.linspace(0, W, w + 1).astype(int)
    out = np.zeros((h, w, rgba.shape[2]), np.float32)
    rows = np.add.reduceat(rgba, ys[:-1], axis=0) / np.diff(ys)[:, None, None]
    out = np.add.reduceat(rows, xs[:-1], axis=1) / np.diff(xs)[None, :, None]
    return out


def _read_tex(path):
    """A Riot .tex as float RGBA, Blender row order (bottom-up)."""
    rs = importlib.import_module("Aventurine.vendor.ritoshark")
    t = rs.Tex.from_path(path)
    a = np.frombuffer(t.rgba, np.uint8).reshape(t.height, t.width, 4)
    return np.flipud(a).astype(np.float32) / 255


def make_cards(photo_png, ui_dir, out_dir):
    """Cut the game images from the photo: the full card, and a face crop for the two
    icons (the circle keeps Riot's alpha mask). Writes .tex (BGRA8, no mips, like Riot's)
    and .png for each. Returns {key: (tex_path, png_path)}."""
    import bpy
    mx = _load("mossmaw_export")
    os.makedirs(out_dir, exist_ok=True)
    img = bpy.data.images.load(photo_png, check_existing=False)
    W, H = img.size
    photo = np.array(img.pixels[:], np.float32).reshape(H, W, 4)
    bpy.data.images.remove(img)
    photo[..., 3] = 1.0
    out = {}

    card = _box_down(photo, *CARD)
    out["loadscreen"] = card

    side = int(W * ICON_SIZE)
    cx, cy = int(W * ICON_CENTRE[0]), int(H * (1 - ICON_CENTRE[1]))   # bottom-up rows
    x0, y0 = max(0, min(W - side, cx - side // 2)), max(0, min(H - side, cy - side // 2))
    face = photo[y0:y0 + side, x0:x0 + side]
    sq = _box_down(face, 128, 128)
    out["square"] = sq
    mask = _read_tex(os.path.join(ui_dir, "kogmaw_circle_0.tex"))[..., 3]
    circ = sq.copy()
    circ[..., 3] = mask
    out["circle"] = circ

    paths = {}
    for key, px in out.items():
        stem = os.path.splitext(os.path.basename(GAME[key]))[0]
        tex = mx.write_tex(os.path.join(out_dir, stem + ".tex"), px, mips=False)
        png = _save_png(os.path.join(out_dir, stem + ".png"), px)
        paths[key] = (tex, png)
    return paths
