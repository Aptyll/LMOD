"""FrostMaw: Kog'Maw as a Freljord ice beast.

Concept: a frosted white skull, deep-blue hide with glowing cyan veins, a violet mouth,
crystal clusters growing from his back, a ridge of ice shards down his spine, icicles
hanging from his jaw and crystals on his antenna tips.

Run headless (the open Blender is untouched):

    blender -b base/kogmaw/kogmaw_rigged.blend --python scripts/frostmaw.py -- OUT_DIR v1

mossmaw.py, pianomaw.py, mossmaw_export.py and recolor_texture.py must sit beside this file.

Built for the game first. Lessons from Mossmaw:
  - Aventurine mirrors positions on import and export but keeps triangle winding, and turns
    normals with the winding. So in Blender Riot's body is inside-out: its faces and normals
    both point in (a culled render of the plain body shows his insides). A prop built the
    normal Blender way exports inside-out, and League (which culls back faces) shows only
    its inner side. Every prop here is a closed, outward shell, and the export reverses its
    faces so it matches the body: winding and normals both flipped. The report checks it
    ("winding").
  - No moss-style cards or open shells: every prop is a closed solid.
  - Props are UV'd onto colour ramps painted into the body texture, so the preview is the
    exported mesh with the exported texture, culled the way the game culls. What the
    previews show is what the game draws, minus its lighting.

Only the original bones are used, at most 4 influences per vertex.
"""
import importlib.util
import json
import math
import os
import random
import sys
import zipfile

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_here, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mm = _load("mossmaw")          # spots, skinning, audit, renders
pm = _load("pianomaw")         # loading actions, back crest, game view
mx = _load("mossmaw_export")   # .tex writer, WAD helper
rt = mm.rt

SEED = 23
BODY, RIG = mm.BODY, mm.RIG
ACTIONS = mm.ACTIONS
NAME = "FrostMaw"

# ------ texture (pixel values are sRGB, like the texture itself)

FROST = {
    "seed": 23,
    "hide_dark": (0.020, 0.055, 0.150),   # navy in shadow
    "hide_mid": (0.075, 0.250, 0.500),    # glacier blue
    "hide_light": (0.300, 0.580, 0.860),  # sunlit ice blue
    "vein": (0.560, 0.960, 1.000),        # glowing cyan veins
    "vein_width": 32.0,                   # bigger = thinner veins
    "vein_glow": 0.22,                    # soft halo around each vein
    "tip": (0.820, 0.960, 1.000),         # pale ice spike tips
    "frost": (0.880, 0.930, 0.985),       # frosted bone
    "frost_shadow": (0.360, 0.500, 0.700),
    "crevice": (0.140, 0.290, 0.580),     # blue ice packed into bone crevices
    "crevice_val": 0.45,
    "mouth_hue": 0.74,                    # violet
    "eye": (0.450, 0.930, 1.000),         # glowing cyan eyes
}

# colour ramps painted into the texture for the props: root -> tip
RAMPS = {
    "crystal": ((0.100, 0.320, 0.620), (0.350, 0.720, 0.980), (0.700, 0.950, 1.000), (0.950, 1.000, 1.000)),
    "icicle": ((0.280, 0.520, 0.800), (0.640, 0.860, 0.990), (0.930, 0.990, 1.000), (1.000, 1.000, 1.000)),
}
STRIP_W, STRIP_H = 96, 32     # px per ramp strip (ramp runs along u, so mips only blur its ends)
STRIP_INSET = (10, 14)        # px kept clear inside a strip: (along the ramp, across it)


def _labels(rgba, clean=2):
    """0 bone, 1 hide, 2 mouth, 3 eye; the same split Mossmaw uses."""
    h, s, v = np.moveaxis(rt.rgb_to_hsv(rgba[..., :3]), -1, 0)
    magenta = ((h >= 0.80) | (h <= 0.03)) & (s > 0.4)
    eye = (h > 0.0) & (h <= rt.BEIGE_HUE[1]) & (s > 0.6) & (v > 0.25)
    cool = (h >= 0.30) & (h <= 0.80) & (s >= rt.BONE_SAT)
    label = np.where(eye, 3, np.where(magenta, 2, np.where(cool, 1, 0)))
    return rt._majority(label, 4, clean), h, s, v


def frost_pixels(rgba, f=FROST):
    """Frost recolor. The pixel's brightness drives every colour, so Riot's painted shading
    stays; noise adds veins in the hide and sparkle on the bone."""
    hp, wp = rgba.shape[:2]
    label, h, s, v = _labels(rgba)
    bone, body, flesh, eye = (label == 0), (label == 1), (label == 2), (label == 3)
    tip = body & (h >= 0.42) & (h <= 0.58) & (v > 0.62)
    hide = body & ~tip

    big = rt.value_noise(hp, wp, f["seed"])
    fine = rt.value_noise(hp, wp, f["seed"] + 1, octaves=(64, 180, 400), weights=(0.3, 0.4, 0.3))
    veinn = rt.value_noise(hp, wp, f["seed"] + 3, octaves=(10, 26), weights=(0.65, 0.35))

    vmax_body = max(float(v[body].max()) if body.any() else 1.0, 1e-3)
    rel_b = v / vmax_body
    t = np.clip(rel_b * 1.1 + (big - 0.5) * 0.35, 0, 1)
    hide_rgb = rt._ramp3(t, f["hide_dark"], f["hide_mid"], f["hide_light"])
    hide_rgb *= (0.7 + 0.45 * rel_b)[..., None] * (0.93 + 0.14 * fine)[..., None]
    d = np.abs(veinn - 0.5)
    vein = np.clip(1 - d * f["vein_width"], 0, 1) + f["vein_glow"] * np.clip(1 - d * f["vein_width"] / 3.5, 0, 1)
    vein = np.clip(vein, 0, 1)[..., None]
    hide_rgb = hide_rgb * (1 - vein) + np.array(f["vein"]) * vein

    tip_rgb = np.array(f["tip"]) * np.clip(0.55 + 0.5 * rel_b, 0, 1)[..., None]

    vmax_bone = max(float(v[bone].max()) if bone.any() else 1.0, 1e-3)
    rel = v / vmax_bone
    frost = rt._ramp3(np.clip(rel * 1.15, 0, 1), f["crevice"], f["frost_shadow"], f["frost"])
    sparkle = np.clip((fine - 0.68) * 6, 0, 1)[..., None] * 0.35
    frost = frost * (1 - sparkle) + sparkle
    crev = np.clip((f["crevice_val"] - rel) / f["crevice_val"] * 1.6 + (big - 0.5) * 0.6, 0, 1)[..., None]
    bone_rgb = frost * (1 - crev) + np.array(f["crevice"]) * (0.6 + rel[..., None]) * crev

    flesh_rgb = rt.hsv_to_rgb(np.stack([np.full_like(h, f["mouth_hue"]), np.clip(s * 0.95, 0, 1),
                                        np.clip(v * 1.05, 0, 1)], -1))

    vmax_eye = max(float(v[eye].max()) if eye.any() else 1.0, 1e-3)
    er = (v / vmax_eye)[..., None]
    eye_rgb = np.array(f["eye"]) * (0.55 + 0.6 * er) + np.clip(er - 0.75, 0, 1) * 2.0   # white-hot core

    out = rgba[..., :3].copy()
    for mask, col in ((hide, hide_rgb), (tip, tip_rgb), (bone, bone_rgb), (flesh, flesh_rgb), (eye, eye_rgb)):
        out = np.where(mask[..., None], col, out)
    res = rgba.copy()
    res[..., :3] = np.clip(out, 0, 1)
    res[..., 3] = 1.0
    return res


def _free_strips(base_rgba, count, w=STRIP_W, h=STRIP_H, margin=4):
    """Bottom-left corners (x, y, Blender rows bottom-up) of count w x h rectangles that are
    plain background in the original texture."""
    bg = np.median(np.concatenate([base_rgba[:8, :8, :3].reshape(-1, 3), base_rgba[-8:, -8:, :3].reshape(-1, 3)]), 0)
    free = np.abs(base_rgba[..., :3] - bg).max(-1) < 0.03
    H, W = free.shape
    used = np.zeros_like(free)
    out = []
    for y in range(margin, H - h - margin, 8):
        for x in range(margin, W - w - margin, 8):
            reg = (slice(y - margin, y + h + margin), slice(x - margin, x + w + margin))
            if free[reg].all() and not used[reg].any():
                out.append((x, y))
                used[reg] = True
                if len(out) == count:
                    return out
    raise RuntimeError("only %d free %dx%d strips in the texture, need %d" % (len(out), w, h, count))


def _ramp_strip(stops, w=STRIP_W, h=STRIP_H):
    xs = np.linspace(0, 1, w)
    pos = np.linspace(0, 1, len(stops))
    row = np.stack([np.interp(xs, pos, [c[i] for c in stops]) for i in range(3)], -1)
    return np.repeat(row[None], h, 0)


def build_texture(src, dst):
    """Frost recolor plus the prop ramps. Writes dst, plugs it into the body's image node and
    returns {ramp: (u0, u1, v)}: the UV span each prop ramp occupies."""
    import bpy
    img = bpy.data.images.load(src, check_existing=False)
    w, h = img.size
    base = np.array(img.pixels[:], np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    tex = frost_pixels(base)
    strips = {}
    for (name, stops), (x, y) in zip(RAMPS.items(), _free_strips(base, len(RAMPS))):
        tex[y:y + STRIP_H, x:x + STRIP_W, :3] = _ramp_strip(stops)
        tex[y:y + STRIP_H, x:x + STRIP_W, 3] = 1.0
        a, b = STRIP_INSET
        strips[name] = ((x + a) / w, (x + STRIP_W - a) / w, (y + STRIP_H / 2) / h)
    new = bpy.data.images.new(os.path.splitext(os.path.basename(dst))[0], w, h, alpha=True)
    new.pixels = tex.ravel()
    new.filepath_raw = dst
    new.file_format = 'PNG'
    new.save()
    for mat in bpy.data.objects[BODY].data.materials:
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                node.image = new
    return strips, tex


# ------ props: closed, outward shells only

SINK = 0.05   # metres a shard's base sits under the skin


def _shard(bm, uvl, axis, length, radius, strip, sides=6, mid=0.68, base_r=0.85, tip_off=(0, 0), twist=0.0):
    """One closed crystal from the local origin along axis: a sunk base ring, a shoulder ring
    at mid, and a point. Flat faces (ice facets), outward winding. UVs walk the ramp from
    root (u0) to tip (u1)."""
    import mathutils
    axis = mathutils.Vector(axis).normalized()
    side = axis.orthogonal().normalized()
    other = axis.cross(side)
    u0, u1, vv = strip

    def ring(c, r):
        return [bm.verts.new(c + (side * math.cos(twist + 2 * math.pi * i / sides) +
                                  other * math.sin(twist + 2 * math.pi * i / sides)) * r) for i in range(sides)]

    root = -axis * SINK
    r0 = ring(root, radius * base_r)
    r1 = ring(axis * length * mid, radius)
    tip = bm.verts.new(axis * length + side * tip_off[0] * radius + other * tip_off[1] * radius)
    t_of = {}
    for v in r0:
        t_of[v] = 0.0
    for v in r1:
        t_of[v] = mid * 0.9
    t_of[tip] = 1.0
    faces = []
    for i in range(sides):
        j = (i + 1) % sides
        faces.append(bm.faces.new((r0[i], r0[j], r1[j], r1[i])))
        faces.append(bm.faces.new((r1[i], r1[j], tip)))
    faces.append(bm.faces.new(r0[::-1]))
    for k, f in enumerate(faces):
        f.smooth = False
        shade = 0.9 if k % 4 == 0 else 1.0          # every other facet a touch darker
        for loop in f.loops:
            t = t_of[loop.vert] * shade
            loop[uvl].uv = (u0 + (u1 - u0) * t, vv)
    return faces


def _outward_share(bm):
    """Share of faces pointing away from their own shard's centre. Shards are convex and
    never share vertices, so this is exact. _shard builds them outward; this proves it.
    (bmesh's recalc_face_normals is not used: on these pointed shells it flipped about one
    shard in four the wrong way.)"""
    import mathutils
    seen, good, total = set(), 0, 0
    for f in bm.faces:
        if f in seen:
            continue
        stack, island = [f], set()
        while stack:
            g = stack.pop()
            if g in island:
                continue
            island.add(g)
            stack.extend(h for e in g.edges for h in e.link_faces if h not in island)
        seen |= island
        verts = {v for g in island for v in g.verts}
        c = sum((v.co for v in verts), mathutils.Vector()) / len(verts)
        good += sum(1 for g in island if g.normal.dot(g.calc_center_median() - c) > 0)
        total += len(island)
    return good / max(total, 1)


def _finish(bm, name):
    """Check the shell is closed and points out, then make the object."""
    bm.normal_update()
    open_edges = sum(1 for e in bm.edges if e.is_boundary)
    outward = _outward_share(bm)
    ob = mm._new_obj(name, bm)
    ob["open_edges"] = open_edges
    ob["outward"] = outward
    return ob


def _cluster(name, rng, strip, size, count, spread=0.55):
    """A crystal cluster: one big shard along local +Z, smaller ones leaning out around it."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    _shard(bm, uvl, (0, 0, 1), size, size * 0.2, strip, twist=rng.uniform(0, 1))
    for k in range(count - 1):
        a = 2 * math.pi * k / max(count - 1, 1) + rng.uniform(-0.4, 0.4)
        lean = rng.uniform(0.6, 1.0) * spread
        ax = mathutils.Vector((math.cos(a) * lean, math.sin(a) * lean, 1.0))
        ln = size * rng.uniform(0.45, 0.75)
        off = mathutils.Vector((math.cos(a), math.sin(a), 0)) * size * 0.08
        before = set(bm.verts)
        _shard(bm, uvl, ax, ln, ln * 0.22, strip, twist=rng.uniform(0, 1))
        for v in set(bm.verts) - before:
            v.co += off
    return _finish(bm, name)


def _ridge(name, rng, strip, size):
    """A spine shard leaning back (local +Y is toward his tail, +Z out of the skin), with two
    small ones at its foot, one either side."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    _shard(bm, uvl, (0, 0.55, 1), size, size * 0.2, strip, sides=5, base_r=1.0, twist=rng.uniform(0, 1))
    for s in (-1, 1):
        before = set(bm.verts)
        ln = size * rng.uniform(0.3, 0.45)
        _shard(bm, uvl, (0.6 * s, 0.3, 1), ln, ln * 0.25, strip, sides=5, twist=rng.uniform(0, 1))
        for v in set(bm.verts) - before:
            v.co += mathutils.Vector((s * size * 0.07, 0, 0))
    return _finish(bm, name)


def _icicle(name, rng, strip, length):
    """A hanging icicle along local +Z (oriented downward): slim, slightly kinked."""
    import bmesh
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    r = length * rng.uniform(0.11, 0.15)
    _shard(bm, uvl, (rng.uniform(-0.08, 0.08), rng.uniform(-0.08, 0.08), 1), length, r, strip, sides=5,
           mid=0.3, base_r=1.15, tip_off=(rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3)), twist=rng.uniform(0, 1))
    return _finish(bm, name)


# ------ placement

GROWTH = {
    # kind: (how many, gap in metres)
    "ridge": (11, 0.0),      # shards down the crest of his back, front to back
    "cluster": (8, 0.30),    # crystal clusters on his back, shoulders and skull top
    "icicle": (12, 0.075),   # icicles under his jaw
    "antenna": (4, 0.0),     # a small cluster on each antenna tip
}
RIDGE_PITCH = 0.13
RIDGE_SIZE = (0.24, 0.52)    # shard length at the ends and in the middle of the ridge
RIDGE_CLEAR = 0.16           # clusters keep this far from his midline, so they don't crowd the ridge
CLUSTER_SIZE = (0.28, 0.42)
ICICLE_LEN = (0.12, 0.30)
ICICLE_CLEARANCE = 0.45      # an icicle ends at least this high off the ground (rest pose)
ANTENNA_SIZE = 0.16
CLIP_FRAC = 0.4              # the top 60% of a shard may not sink into him


def _nearest_vertex(body, p, bones):
    mw = body.matrix_world
    best, bd = None, 1e9
    for v in body.data.vertices:
        if mm._dominant_bone(body, v) not in bones:
            continue
        d = ((mw @ v.co) - p).length
        if d < bd:
            best, bd = v.index, d
    return best


def _ridge_spots(body, rig):
    """Spots down the crest of his back, front to back: (skin point, normal, vertex, tangent
    toward the tail, shard size). The crest is smoothed, so each spot drops back onto the skin."""
    import mathutils
    tree, _ = pm._surface_tree(body)
    pts, total = pm._resample(pm.crest(body, rig, tree), RIDGE_PITCH)
    out = []
    for k, (loc, tan, nrm) in enumerate(pts):
        t = k / max(len(pts) - 1, 1)
        size = RIDGE_SIZE[0] + (RIDGE_SIZE[1] - RIDGE_SIZE[0]) * math.sin(math.pi * min(t * 1.3, 1.0))
        hit = tree.ray_cast(mathutils.Vector((loc.x, loc.y, 5.0)), mathutils.Vector((0, 0, -1)))[0]
        if hit is None:
            continue
        vi = _nearest_vertex(body, hit, mm.TOP_BONES)
        if vi is not None:
            out.append((hit, nrm, vi, tan, size))
    return out


def materials_for_props():
    """Props draw with the body's own material: the same texture the game uses."""
    import bpy
    return bpy.data.objects[BODY].data.materials[0]


def build_frost(strips, seed=SEED, actions=ACTIONS):
    """Grow every prop on the rest pose, audit over the actions, regrow failures on the next
    spot of their kind, drop what still fails. Returns a report."""
    import bpy
    import mathutils
    body, rig = bpy.data.objects[BODY], bpy.data.objects[RIG]
    rng = random.Random(seed)
    coll = bpy.data.collections.get("Frost") or bpy.data.collections.new("Frost")
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    for o in list(coll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    mat = materials_for_props()
    old_pose = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()

    ridge = _ridge_spots(body, rig)
    mid_x = lambda p: pm._midline_x(rig, p.y)
    top = [c for c in mm._ray_spots(body, mm.TOP_BONES) if abs(c[0].x - mid_x(c[0])) > RIDGE_CLEAR]
    cands = {
        "ridge": iter(ridge),
        "cluster": mm._pick(top, GROWTH["cluster"][1], rng),
        "icicle": mm._pick(mm._ray_spots(body, mm.JAW_BONES, from_below=True, min_thick=0.08),
                           GROWTH["icicle"][1], rng),
        "antenna": iter(mm._antenna_spots(body, rig, mm.ANTENNA_TIPS, tips=True)),
    }
    up = mathutils.Vector((0, 0, 1))

    def grow(kind, name):
        try:
            spot = next(cands[kind])
        except StopIteration:
            return None
        pos, n, vi = spot[:3]
        at = pos
        collar = body.matrix_world @ body.data.vertices[vi].co   # always a point on the skin
        if kind == "ridge":
            tan, size = spot[3], spot[4]
            frame_n = n
            ob = _ridge(name, rng, strips["crystal"], size)
        elif kind == "cluster":
            size = rng.uniform(*CLUSTER_SIZE)
            ob = _cluster(name, rng, strips["crystal"], size, rng.randint(3, 5))
            frame_n = (n + up * 0.8).normalized()
        elif kind == "icicle":
            ln = min(rng.uniform(*ICICLE_LEN), pos.z - ICICLE_CLEARANCE)
            if ln < 0.07:
                return grow(kind, name)
            ob = _icicle(name, rng, strips["icicle"], ln)
            frame_n = mathutils.Vector((0, 0, -1))
        else:   # antenna tip: the spot is inside the tip, the collar is its vertex
            ob = _cluster(name, rng, strips["crystal"], ANTENNA_SIZE, 3, spread=0.35)
            frame_n = n
        ob.data.materials.append(mat)
        coll.objects.link(ob)
        mm._orient(ob, rig, at, frame_n, rng)
        if kind == "ridge":
            # local +Z out of the skin, local +Y toward the tail
            z = frame_n.normalized()
            y = (tan - z * tan.dot(z)).normalized()
            rot = mathutils.Matrix((y.cross(z), y, z)).transposed().to_4x4()
            ob.matrix_world = mathutils.Matrix.Translation(at) @ rot
        bpy.context.view_layer.update()
        ob["collar"] = list(collar)
        ob["clip_z"] = ob.dimensions.z * CLIP_FRAC if kind != "icicle" else 0.05
        mm._skin_rigid(ob, body, rig, vi)
        return ob

    slots = [(k, "%s.%02d" % (k.capitalize(), i)) for k, (n, _) in GROWTH.items() for i in range(n)]
    pieces = {name: grow(kind, name) for kind, name in slots}
    regrown, rounds, bad, report = 0, 0, set(), {}

    def remove(o):
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)

    while True:
        live = [o for o in pieces.values() if o is not None]
        rig.data.pose_position = old_pose
        report = mm.audit(live, actions)
        rig.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        bad = mm.failing(report)
        rounds += 1
        if not bad or rounds > 8:
            break
        for kind, name in slots:
            if pieces[name] is not None and pieces[name].name in bad:
                remove(pieces[name])
                pieces[name] = grow(kind, name)
                regrown += 1
    for name, o in list(pieces.items()):
        if o is not None and o.name in bad:
            remove(o)
            pieces[name] = None
    rig.data.pose_position = old_pose
    bpy.context.view_layer.update()
    placed = [o for o in pieces.values() if o]
    return {"placed": len(placed),
            "by_kind": {k: sum(1 for o in placed if o.name.startswith(k.capitalize() + ".")) for k in GROWTH},
            "dropped": [k for k, o in pieces.items() if o is None], "regrown": regrown, "rounds": rounds,
            "open_edges": sum(o["open_edges"] for o in placed),
            "outward": round(min((o["outward"] for o in placed), default=1.0), 3),
            "worst_stretch": max((v[0] for k, v in report.items() if k not in bad), default=1.0),
            "triangles": sum(len(p.vertices) - 2 for o in placed for p in o.data.polygons)}


# ------ export: one .skn on the original .skl, one .tex

MAX_INFLUENCES = 4
GAME = mx.GAME


def _corner_normals(me):
    return [tuple(n.vector) for n in me.corner_normals]


def build_export_mesh(body, pieces, name=NAME + "_Export"):
    """The body plus every prop as one mesh in the rest pose. Props get their faces reversed,
    so their winding and normals point in like the body's do in Blender (Riot's convention
    after Aventurine's mirror). The body keeps its own corner normals. Returns the object."""
    import bmesh
    import bpy
    body_normals = _corner_normals(body.data)
    bm = bmesh.new()
    bm.from_mesh(body.data)
    uv = bm.loops.layers.uv.active
    deform = bm.verts.layers.deform.verify()
    groups = [g.name for g in body.vertex_groups]
    n_body_faces = len(bm.faces)
    for ob in pieces:
        pmesh = bmesh.new()
        pmesh.from_mesh(ob.data)
        to_body = body.matrix_world.inverted() @ ob.matrix_world
        pmesh.transform(to_body)
        if to_body.to_3x3().determinant() < 0:           # a mirrored piece: undo the mirror's flip
            bmesh.ops.reverse_faces(pmesh, faces=pmesh.faces[:])
        puv = pmesh.loops.layers.uv.active
        pdef = pmesh.verts.layers.deform.verify()
        names = [g.name for g in ob.vertex_groups]
        vmap = {}
        for v in pmesh.verts:
            nv = bm.verts.new(v.co)
            for gi, wt in v[pdef].items():
                gname = names[gi]
                if gname not in groups:
                    groups.append(gname)
                nv[deform][groups.index(gname)] = wt
            vmap[v] = nv
        for f in pmesh.faces:
            loops = list(f.loops)[::-1]                  # reversed winding
            nf = bm.faces.new([vmap[l.vert] for l in loops])
            nf.material_index = 0
            nf.smooth = False       # flat ice facets
            for nl, l in zip(nf.loops, loops):
                nl[uv].uv = l[puv].uv
        pmesh.free()
    for v in bm.verts:
        items = sorted(v[deform].items(), key=lambda kv: -kv[1])[:MAX_INFLUENCES]
        total = sum(w for _, w in items) or 1.0
        v[deform].clear()
        for gi, wt in items:
            v[deform][gi] = wt / total
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    normals = list(body_normals)
    for p in me.polygons[n_body_faces:]:
        normals.extend([tuple(p.normal)] * p.loop_total)   # flat, following the reversed winding
    assert len(normals) == len(me.loops), (len(normals), len(me.loops))
    me.normals_split_custom_set(normals)
    for m in body.data.materials:
        me.materials.append(m)
    ob = bpy.data.objects.new(name, me)
    for g in groups:
        ob.vertex_groups.new(name=g)
    for k in ("lol_skn_flags", "lol_skn_prefix", "lol_skn_trailing"):
        if k in body:
            ob[k] = body[k]
    bpy.context.scene.collection.objects.link(ob)
    ob.parent = body.parent
    ob.matrix_world = body.matrix_world
    ob["n_body_faces"] = n_body_faces
    return ob


def winding_check(ob, pieces):
    """normals_follow_winding: share of faces whose shading normal is on the same side as
    their winding normal (body ~1.0 is Riot's convention; props must be 1.0).
    props_inward: share of prop faces whose winding points into their own piece, like the
    body's does in Blender (must be 1.0; each piece is convex enough for a centre test)."""
    import mathutils
    me = ob.data
    nb = ob["n_body_faces"]
    cn = me.corner_normals
    agree, total = [0, 0], [0, 0]
    for p in me.polygons:
        avg = sum((cn[i].vector for i in range(p.loop_start, p.loop_start + p.loop_total)), mathutils.Vector())
        k = 0 if p.index < nb else 1
        total[k] += 1
        agree[k] += 1 if p.normal.dot(avg) > 0 else 0
    # props_inward: every shard is convex, so group prop faces into shards (faces that share
    # vertices) and test each face's winding normal against its shard's centre
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    props = list(me.polygons[nb:])
    for p in props:
        for v in p.vertices[1:]:
            parent[find(v)] = find(p.vertices[0])
    centre, count = {}, {}
    for v in {v for p in props for v in p.vertices}:
        r = find(v)
        centre[r] = centre.get(r, mathutils.Vector()) + me.vertices[v].co
        count[r] = count.get(r, 0) + 1
    inward = sum(1 for p in props
                 if p.normal.dot(p.center - centre[find(p.vertices[0])] / count[find(p.vertices[0])]) < 0)
    return {"normals_follow_winding": {"body": round(agree[0] / max(total[0], 1), 3),
                                       "props": round(agree[1] / max(total[1], 1), 3)},
            "props_inward": round(inward / max(len(props), 1), 3), "prop_faces": total[1],
            "shards": len(centre)}


def write_fantome(path, wad_path, image_path=None, version="1.0.0"):
    info = {"Name": NAME, "Author": "Noah", "Version": version,
            "Description": "Kog'Maw as a Freljord ice beast. Replaces the base skin; cosmetic only."}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META/info.json", json.dumps(info, indent=2))
        if image_path:
            z.write(image_path, "META/image.png")
        z.write(wad_path, "WAD/" + mx.WAD_NAME)
    return path


def export_all(out_dir, tex):
    """Write kogmaw.skn, the .tex/.png, a .wad.client with just those two, and FrostMaw.fantome.
    Riot-derived files: they stay on this machine, never commit them."""
    import bpy
    from Aventurine.io import export_skn
    os.makedirs(out_dir, exist_ok=True)
    body, rig = bpy.data.objects[BODY], bpy.data.objects[RIG]
    pieces = [o for o in bpy.data.collections["Frost"].objects if o.type == 'MESH']
    old = bpy.data.objects.get(NAME + "_Export")
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    keep = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    ob = build_export_mesh(body, pieces)
    skn_path = os.path.join(out_dir, "kogmaw.skn")
    export_skn.write_skn_multi(skn_path, [ob], rig)
    rig.data.pose_position = keep
    bpy.context.view_layer.update()
    h, w = tex.shape[:2]
    png_path = os.path.join(out_dir, "kogmaw_base_tx_cm.png")
    img = bpy.data.images.new("frostmaw_atlas", w, h, alpha=True)
    img.pixels = tex.ravel()
    img.filepath_raw = png_path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)
    tex_path = mx.write_tex(os.path.join(out_dir, "kogmaw_base_tx_cm.tex"), tex)
    rs = mx._rs()
    wad_path = os.path.join(out_dir, mx.WAD_NAME)
    with open(skn_path, "rb") as f1, open(tex_path, "rb") as f2:
        rs.build_wad_to_path(wad_path, {GAME["skn"]: f1.read(), GAME["tex"]: f2.read()})
    fantome = write_fantome(os.path.join(out_dir, NAME + ".fantome"), wad_path)
    skn = rs.Skn.from_path(skn_path)
    return ob, {"skn": skn_path, "tex": tex_path, "fantome": fantome, "vertices": skn.vertex_count,
                "triangles": len(skn.indices) // 12, "winding": winding_check(ob, pieces)}


# game paths for the art from frostmaw_art.py (champion icon, round icon, loading screen)
GAME_ART = {
    "frostmaw_icon_square.tex": "assets/characters/kogmaw/hud/kogmaw_square_0.tex",
    "frostmaw_icon_circle.tex": "assets/characters/kogmaw/hud/kogmaw_circle_0.tex",
    "frostmaw_loadscreen.tex": "assets/characters/kogmaw/skins/base/kogmawloadscreen.tex",
}


def package_mod(export_dir, extra_dir=None):
    """Rebuild the .wad.client and .fantome in export_dir from its kogmaw.skn and texture, plus
    any GAME_ART files found in extra_dir. A wrong game path only means that file is ignored."""
    files = {GAME["skn"]: os.path.join(export_dir, "kogmaw.skn"),
             GAME["tex"]: os.path.join(export_dir, "kogmaw_base_tx_cm.tex")}
    for name, game_path in GAME_ART.items():
        p = os.path.join(extra_dir or "", name)
        if extra_dir and os.path.exists(p):
            files[game_path] = p
    data = {}
    for game_path, p in files.items():
        with open(p, "rb") as f:
            data[game_path] = f.read()
    wad_path = os.path.join(export_dir, mx.WAD_NAME)
    mx._rs().build_wad_to_path(wad_path, data)
    image = os.path.join(extra_dir or "", "frostmaw_loadscreen.png")
    fantome = write_fantome(os.path.join(export_dir, NAME + ".fantome"), wad_path,
                            image if extra_dir and os.path.exists(image) else None)
    return {"fantome": fantome, "files": sorted(files)}


# ------ previews: the exported mesh, culled like the game

def game_preview(export_ob):
    """A copy of the export mesh with every face reversed and every normal negated, so Blender's
    front faces and normals are the game's, skinned to the rig. Its material culls back faces.
    The body and the build-time props are hidden from renders."""
    import bmesh
    import bpy
    src = export_ob.data
    want = {}
    cn = src.corner_normals
    for p in src.polygons:
        for li in range(p.loop_start, p.loop_start + p.loop_total):
            want[(p.index, src.loops[li].vertex_index)] = tuple(-cn[li].vector)
    me = src.copy()
    me.name = NAME + "_Game"
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    me.normals_split_custom_set([want[(p.index, me.loops[li].vertex_index)] for p in me.polygons
                                 for li in range(p.loop_start, p.loop_start + p.loop_total)])
    mat = export_ob.data.materials[0].copy()
    mat.name = NAME + "_GameCull"
    mat.use_backface_culling = True
    me.materials.clear()
    me.materials.append(mat)
    ob = bpy.data.objects.new(NAME + "_Game", me)
    for g in export_ob.vertex_groups:
        ob.vertex_groups.new(name=g.name)
    rig = bpy.data.objects[RIG]
    ob.parent = rig
    ob.matrix_world = export_ob.matrix_world
    ob.modifiers.new("Armature", 'ARMATURE').object = rig
    bpy.context.scene.collection.objects.link(ob)
    for o in [bpy.data.objects[BODY], export_ob] + list(bpy.data.collections["Frost"].objects):
        o.hide_render = True
        o.hide_viewport = True
    return ob


BG = (0.010, 0.022, 0.050)


def stage():
    """Night snow: cool key, pale fill, cyan rim, dark navy floor and sky."""
    import bmesh
    import bpy
    s = bpy.context.scene
    for eng in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
        try:
            s.render.engine = eng
            break
        except TypeError:
            continue
    world = s.world or bpy.data.worlds.new("World")
    s.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (*BG, 1)
    bg.inputs["Strength"].default_value = 1.0
    for o in [o for o in s.objects if o.type == 'LIGHT']:
        bpy.data.objects.remove(o, do_unlink=True)

    def light(name, energy, color, rot, angle):
        ob = bpy.data.objects.new(name, bpy.data.lights.new(name, 'SUN'))
        s.collection.objects.link(ob)
        ob.data.energy, ob.data.color, ob.data.angle = energy, color, angle
        ob.rotation_euler = rot

    light("Key", 3.6, (0.92, 0.96, 1.0), (math.radians(50), 0, math.radians(35)), math.radians(8))
    light("Fill", 1.3, (0.70, 0.80, 1.0), (math.radians(65), 0, math.radians(-120)), math.radians(30))
    light("Rim", 4.0, (0.55, 0.90, 1.0), (math.radians(-60), 0, math.radians(170)), math.radians(6))
    ground = bpy.data.objects.get("Ground")
    if ground is None:
        bm = bmesh.new()
        bmesh.ops.create_circle(bm, cap_ends=True, segments=48, radius=8.0)
        ground = mm._new_obj("Ground", bm)
        s.collection.objects.link(ground)
        ground.data.materials.append(mm._material("Snowfield", (0.05, 0.075, 0.12), (0.03, 0.045, 0.08), 6.0))
    ground.location = (0, 0, -0.005)
    s.camera.data.type = 'PERSP'
    s.camera.data.lens = 70
    s.render.film_transparent = False


# League's camera: about 56 degrees down, looking north, far and long-lens.
LEAGUE_PITCH, LEAGUE_DIST, LEAGUE_LENS = 56.0, 14.0, 126.0
LEAGUE_YAWS = (("toward camera", 0), ("right", 90), ("away", 180), ("left", 270))


def render_league_strip(out_jpg, action="kogmaw_run", frame=6, size=320):
    """Him at game zoom from League's camera, facing four ways (the camera never turns, he
    does). Faces are culled like the game culls them."""
    import bpy
    s = bpy.context.scene
    rig = bpy.data.objects[RIG]
    mm._set_action(action if action in bpy.data.actions else mm.HERO_ACTION)
    s.frame_set(frame)
    s.render.resolution_x = s.render.resolution_y = size
    keep_rot, keep_lens = rig.rotation_euler.z, s.camera.data.lens
    a = math.radians(LEAGUE_PITCH)
    target = (0.0, 0.0, 0.8)
    loc = (0.0, -LEAGUE_DIST * math.cos(a), target[2] + LEAGUE_DIST * math.sin(a))
    s.camera.data.lens = LEAGUE_LENS
    mm._aim(s.camera, loc, target)
    tiles = []
    tmp = os.path.splitext(out_jpg)[0] + "_tmp.png"
    for _, yaw in LEAGUE_YAWS:
        rig.rotation_euler.z = keep_rot + math.radians(yaw)
        bpy.context.view_layer.update()
        tiles.append(mm._render_rgba(tmp))
    rig.rotation_euler.z = keep_rot
    s.camera.data.lens = keep_lens
    return mm._save_jpg(out_jpg, np.concatenate(tiles, axis=1))


# ------ headless entry


def main(argv):
    """blender -b kogmaw_rigged.blend --python frostmaw.py -- OUT_DIR VERSION"""
    import bpy
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    out_dir = args[0]
    ver = args[1] if len(args) > 1 else "v1"
    root = os.environ.get("LMOD_ROOT", r"C:\NoahCode\LMOD")
    base = os.path.join(root, "base", "kogmaw")
    work = os.path.join(root, "work", "frostmaw")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    report = {"version": ver, "loaded_actions": pm.load_actions(os.path.join(base, "animations"))}
    stage()
    before = pm.hero_rgba(os.path.join(out_dir, "_before.png"))
    strips, tex = build_texture(os.path.join(base, "kogmaw_base_tx_cm.png"), os.path.join(work, "frostmaw_tx_cm.png"))
    report["strips"] = strips
    report.update(build_frost(strips))
    export_ob, exp = export_all(os.path.join(work, "export_" + ver), tex)
    report["export"] = exp
    game_preview(export_ob)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(work, "frostmaw_%s.blend" % ver))
    after = pm.hero_rgba(os.path.join(out_dir, "_after.png"))
    mm._save_jpg(os.path.join(out_dir, "frostmaw_%s_hero.jpg" % ver), after)
    mm._save_jpg(os.path.join(out_dir, "frostmaw_original_%s.jpg" % ver), np.concatenate([before, after], axis=1))
    mm.render_run_strip(os.path.join(out_dir, "frostmaw_%s_run_strip.jpg" % ver))
    mm.render_turntable(os.path.join(out_dir, "frostmaw_%s_turntable.jpg" % ver))
    render_league_strip(os.path.join(out_dir, "frostmaw_%s_game.jpg" % ver))
    with open(os.path.join(out_dir, "frostmaw_%s_report.json" % ver), "w") as f:
        json.dump(report, f, indent=1, default=str)
    return report


if __name__ == "__main__" and "--" in sys.argv:
    main(sys.argv)
