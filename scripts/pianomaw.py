"""Piano'Maw: Kog'Maw as a living grand piano.

Concept: his banded carapace is black lacquer, his skull is ivory, his antennae are piano
hammers with red felt heads, and a keyboard runs down the crest of his back.

Run headless (this is how the Piano'Maw thread builds it, so the open Blender is untouched):

    blender -b base/kogmaw/kogmaw_rigged.blend --python scripts/pianomaw.py -- OUT_DIR v1

or import it in Blender and call the steps yourself (see main()). mossmaw.py and
recolor_texture.py must sit beside this file: the skinning, audit, material and render
helpers come from mossmaw.py.

Pieces (all seeded or deterministic, so a rerun gives the same skin):
  - texture: the "piano" recolor (piano_pixels), Riot's painted shading kept.
  - keyboard: white keys laid side by side along the crest of his back, each one draped
    over the carapace (rays from above), with black keys in the 2-3 pattern straddling the
    crest. Keys take the averaged weights of the hide under them, like Mossmaw's mats.
  - a gold eighth note on the tip of his tail.
Everything is audited over run, idle, attack and ult like Mossmaw, and the report says
which pieces fail. Only the original bones are used, at most 4 influences per vertex.
"""
import importlib.util
import json
import math
import os
import sys

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_here, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mm = _load("mossmaw")   # skinning, audit, materials and renders are shared with Mossmaw
rt = mm.rt

SEED = 11
BODY, RIG = mm.BODY, mm.RIG
ACTIONS = mm.ACTIONS
EXTRA_ANMS = ("kogmaw_idle1", "kogmaw_attack1", "kogmaw_spell4")   # the rigged base only has run

# ------ texture

PIANO = {
    "seed": 11,
    "teal_band": (0.40, 0.575),      # Kog'Maw's teal stripes and belly -> gold harp plate
    "felt_seed": 0.75,               # bright cyan seeds the felt (only the antenna tips are this bright)...
    "felt_val": 0.42,                # ...which then spreads over connected blue/teal brighter than this
    "felt_grow": 40,                 # ...for at most this many px                # teal brighter than this (the antenna tips) -> red felt hammers
    "felt_sat": 0.40,
    "blue_band": (0.575, 0.84),      # blue/purple hide -> black lacquer
    "mouth_band": (0.84, 0.03),      # magenta..red -> crimson velvet
    "eye_sat": 0.6, "eye_val": 0.25, # saturated bright orange is the eye: left as painted
    "clean": 2,                      # majority filter radius (px)
    "ebony": (0.030, 0.026, 0.030),  # lacquer base
    "ebony_lift": (0.20, 0.18, 0.22),# lacquer in full light
    "sheen": (0.62, 0.60, 0.66),     # the polished highlight
    "sheen_from": 0.55,              # relative brightness where the highlight starts
    "gold_dark": (0.22, 0.12, 0.03),
    "gold_mid": (0.70, 0.47, 0.14),
    "gold_light": (1.00, 0.86, 0.48),
    "string": (1.00, 0.95, 0.80),    # bright string lines on the gold
    "string_gap": 9,                 # px between strings
    "ivory": (0.95, 0.90, 0.78),
    "ivory_shadow": (0.36, 0.28, 0.20),
    "felt": (0.62, 0.05, 0.07),
    "velvet": 0.985,                 # hue of the mouth
    "trim": (0.95, 0.74, 0.30),      # gold pinstripe where lacquer meets ivory
    "trim_px": 2,
}


def _dilate(mask, r):
    return rt._box_sum(mask.astype(np.float32), r) > 0


def piano_pixels(rgba, m=PIANO):
    """Piano recolor. rgba: float (h, w, 4) in 0..1, sRGB. The painted brightness drives
    every colour, so Riot's shading survives:
      blue hide       -> black lacquer that lifts with the light, plus a tight polished sheen
      teal stripes    -> gold inlay; the teal belly becomes a gold harp plate with strings
      cyan antenna tips (seeded by the brightest cyan, grown over connected bright pixels)
                      -> red felt, so each antenna reads as a piano hammer
      bone            -> ivory, with a gold pinstripe where it meets the lacquer
      mouth           -> crimson velvet; the eye stays as painted"""
    hp, wp = rgba.shape[:2]
    rgb = rgba[..., :3]
    h, s, v = np.moveaxis(rt.rgb_to_hsv(rgb), -1, 0)
    teal = (h >= m["teal_band"][0]) & (h < m["teal_band"][1]) & (s >= 0.18)
    blue = (h >= m["blue_band"][0]) & (h < m["blue_band"][1]) & (s >= rt.BONE_SAT)
    mag = ((h >= m["mouth_band"][0]) | (h <= m["mouth_band"][1])) & (s > 0.4)
    eye = (h > 0.0) & (h <= rt.BEIGE_HUE[1]) & (s > m["eye_sat"]) & (v > m["eye_val"])
    felt = teal & (v > m["felt_seed"]) & (s > m["felt_sat"])
    label = np.select([eye, mag, felt, teal, blue], [5, 4, 3, 2, 1], 0)   # 0 bone 1 hide 2 gold 3 felt 4 mouth 5 eye
    label = rt._majority(label, 6, m["clean"])
    bone, hide, gold, felt, mouth, eye = [(label == i) for i in range(6)]
    # felt grows a little so the whole hammer head (not only its brightest core) turns red
    room = (gold | hide | felt) & (v > m["felt_val"])
    for _ in range(m["felt_grow"]):
        felt = _dilate(felt, 1) & room
    gold &= ~felt
    hide &= ~felt

    fine = rt.value_noise(hp, wp, m["seed"], octaves=(64, 180, 400), weights=(0.3, 0.4, 0.3))
    big = rt.value_noise(hp, wp, m["seed"] + 1)

    def rel(mask):
        return np.clip(v / max(float(v[mask].max()) if mask.any() else 1.0, 1e-3), 0, 1)

    # lacquer: deep black that lifts with the painted light, then a tight bright sheen
    t = rel(hide)[..., None]
    lac = np.array(m["ebony"]) + (np.array(m["ebony_lift"]) - np.array(m["ebony"])) * t
    sheen = np.clip((t - m["sheen_from"]) / (1 - m["sheen_from"]), 0, 1) ** 2.2
    lac = lac + np.array(m["sheen"]) * sheen * 0.85

    # gold harp plate with strings
    t = rel(gold)
    g = rt._ramp3(np.clip(t * 1.15 + (big - 0.5) * 0.25, 0, 1), m["gold_dark"], m["gold_mid"], m["gold_light"])
    yy, xx = np.mgrid[0:hp, 0:wp]
    lines = (((xx + yy * 0.35) % m["string_gap"]) < 1.2)[..., None]
    g = np.where(lines, g * 0.55 + np.array(m["string"]) * 0.45 * t[..., None], g)

    # ivory keeps its painted shading, with a warm shadow and a faint grain
    t = rel(bone)[..., None]
    iv = np.array(m["ivory_shadow"]) + (np.array(m["ivory"]) - np.array(m["ivory_shadow"])) * np.clip(t * 1.15, 0, 1)
    iv = iv * (0.96 + 0.08 * fine[..., None])

    # felt: soft, fuzzy, matte red
    t = rel(felt)[..., None]
    fe = np.array(m["felt"]) * (0.45 + 0.75 * t) * (0.85 + 0.3 * fine[..., None])

    mo = rt.hsv_to_rgb(np.stack([np.full_like(h, m["velvet"]), np.clip(s * 1.15, 0, 1), v * 0.8], -1))

    out = rgb.copy()
    for mask, col in ((hide, lac), (gold, g), (bone, iv), (felt, fe), (mouth, mo)):
        out = np.where(mask[..., None], col, out)
    # gold pinstripe on the ivory side of every lacquer/ivory edge
    trim = bone & _dilate(hide, m["trim_px"])
    out = np.where(trim[..., None], np.array(m["trim"]) * (0.55 + 0.6 * rel(bone)[..., None]), out)
    res = rgba.copy()
    res[..., :3] = np.clip(out, 0, 1)
    return res


def apply_texture(src, dst):
    """Write the piano texture to dst and plug it into the body's image node."""
    import bpy
    img = bpy.data.images.load(src, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    out = piano_pixels(px)
    new = bpy.data.images.new(os.path.splitext(os.path.basename(dst))[0], w, h, alpha=True)
    new.pixels = out.ravel()
    new.filepath_raw = dst
    new.file_format = 'PNG'
    new.save()
    for mat in bpy.data.objects[BODY].data.materials:
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                node.image = new
    return new


# ------ animations


def load_actions(anm_dir, names=EXTRA_ANMS):
    """Import .anm files onto the rig with Aventurine, one action each, named after the file."""
    import bpy
    rig = bpy.data.objects[RIG]
    keep = rig.animation_data.action if rig.animation_data else None
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    loaded = []
    for n in names:
        path = os.path.join(anm_dir, n + ".anm")
        if n in bpy.data.actions or not os.path.exists(path):
            continue
        before = set(bpy.data.actions.keys())
        bpy.ops.import_scene.anm(filepath=path, files=[{"name": n + ".anm"}], directory=anm_dir)
        new = [a for a in bpy.data.actions.keys() if a not in before]
        if new and n not in bpy.data.actions:
            bpy.data.actions[new[0]].name = n
        if n in bpy.data.actions:
            bpy.data.actions[n].use_fake_user = True
            loaded.append(n)
    if keep:
        mm._set_action(keep.name)
    return loaded


# ------ keyboard

KEY_FROM_Y, KEY_TO_Y = -0.72, 0.70   # where along his back (rest pose, metres) the crest search runs
CREST_HALF = 0.35          # search this far either side of his midline for the crest
CREST_MIN_UP = 0.5         # a crest point's surface must face at least this much upward
CREST_MIN_Z = 0.95         # the keyboard ends where his back drops below this height (lower keys slid on the tail)
SMOOTH = 8                 # crest points averaged either side: wider than a carapace band, so keys don't shingle
PITCH = 0.085              # white key spacing along the crest: big enough to read at game zoom
WHITE = {"half_len": 0.26, "half_w": 0.038, "height": 0.045, "sink": 0.04}
BLACK = {"len": 0.55, "half_w": 0.022, "height": 0.095, "sink": 0.04}
SEGMENTS = 6               # facets across a key, so it drapes over his rounded back
BED_SMOOTH = 3             # keys either side averaged into each key's bed
MAX_DROP = 0.26            # a key ends where his body falls away more than this below the crest
BLACK_AFTER = (0, 1, 3, 4, 5)   # C D _ F G A _: white keys followed by a black key, per octave
MAX_INFLUENCES = 4         # the game's limit per vertex
FOLLOW = float(os.environ.get("PIANO_FOLLOW", 0.0))   # how much each part of a key follows the skin right under it
SKIP_BONES = ("R_Hair", "L_Hair", "R_Eye", "L_Eye")   # rays ignore antennae and eyes


def _surface_tree(body, skip=SKIP_BONES):
    """Rest-pose BVH of the body without antennae and eyes, so rays from above find his back.
    Returns (tree, ids): ids maps a tree face index back to the mesh polygon."""
    from mathutils.bvhtree import BVHTree
    me = body.data
    mw = body.matrix_world
    dom = [mm._dominant_bone(body, v) or "" for v in me.vertices]
    ids = [p.index for p in me.polygons if not any(dom[i].startswith(skip) for i in p.vertices)]
    tree = BVHTree.FromPolygons([mw @ v.co for v in me.vertices], [me.polygons[i].vertices[:] for i in ids])
    return tree, ids


def _midline_x(rig, y):
    """His midline x at y, from the spine and tail bones (the tail curls to his right)."""
    pts = sorted(((rig.matrix_world @ rig.data.bones[b].head_local) for b in
                  ("C_Chest", "C_Waist", "C_Root", "C_Tail", "C_Tail_b", "C_Tail_c", "C_Tail_d")),
                 key=lambda p: p.y)
    ys = [p.y for p in pts]
    return float(np.interp(y, ys, [p.x for p in pts]))


def crest(body, rig, tree):
    """Points along the top of his back, front to back: (location, normal), smoothed."""
    import mathutils
    raw, misses = [], 0
    for y in np.arange(KEY_FROM_Y, KEY_TO_Y, 0.02):
        x0 = _midline_x(rig, y)
        best = None
        for dx in np.linspace(-CREST_HALF, CREST_HALF, 29):
            loc, nrm, _, _ = tree.ray_cast(mathutils.Vector((x0 + dx, y, 5.0)), mathutils.Vector((0, 0, -1)))
            if loc is None:
                continue
            nrm = nrm if nrm.z >= 0 else -nrm    # some of his faces are flipped
            if nrm.z < CREST_MIN_UP:
                continue                         # spike flanks and steep sides are not the crest
            if best is None or loc.z > best[0].z:
                best = (loc, nrm)
        if best is None or best[0].z < CREST_MIN_Z:
            misses += 1
            if raw and misses > 4:
                break
            continue
        misses = 0
        raw.append(best)
    pts = []
    for i in range(len(raw)):
        win = raw[max(0, i - SMOOTH): i + SMOOTH + 1]
        pts.append((sum((p for p, _ in win), mathutils.Vector()) / len(win),
                    sum((n for _, n in win), mathutils.Vector()).normalized()))
    return pts


def _resample(pts, pitch):
    """Points every pitch metres of arc along the crest, with tangent and normal."""
    locs = [p for p, _ in pts]
    seg = [(b - a).length for a, b in zip(locs, locs[1:])]
    total = sum(seg)
    out, s, i, acc = [], pitch / 2, 0, 0.0
    while s < total and i < len(seg):
        while i < len(seg) and acc + seg[i] < s:
            acc += seg[i]
            i += 1
        if i >= len(seg):
            break
        f = (s - acc) / max(seg[i], 1e-6)
        loc = locs[i].lerp(locs[i + 1], f)
        nrm = pts[i][1].lerp(pts[i + 1][1], f).normalized()
        tan = (locs[i + 1] - locs[i]).normalized()
        nrm = (nrm - tan * nrm.dot(tan)).normalized()
        out.append((loc, tan, nrm))
        s += pitch
    return out, total


def _frame(loc, tan, nrm):
    import mathutils
    side = nrm.cross(tan).normalized()   # x along the crest, y across, z up: right-handed
    m = mathutils.Matrix((tan, side, nrm)).transposed().to_4x4()
    m.translation = loc
    return m


def _probe(frame, surf, body, half_len, half_w):
    """Where his surface is under a key: rays along the frame's -z at each facet corner.
    Returns (us, rows, skin): the facet positions across his back, the surface height under
    each corner (frame z, -1 for a miss), and the skin weights there."""
    import mathutils
    tree, ids = surf
    inv = frame.inverted()
    up = frame.to_3x3() @ mathutils.Vector((0, 0, 1))
    us = np.linspace(-half_len, half_len, SEGMENTS + 1)
    rows, skin = [], []
    for u in us:
        row, srow = [], []
        for e in (-half_w, half_w):
            o = frame @ mathutils.Vector((e, u, 0.6))
            loc, _, face, _ = tree.ray_cast(o, -up, 1.2)
            row.append((inv @ loc).z if loc is not None else -1.0)
            srow.append(_skin_weights(body, ids[face], loc) if loc is not None else {})
        rows.append(row)
        skin.append(srow)
    return us, rows, skin


def _draped_key(name, probe, half_w, height, sink, caps=False):
    """A key as a draped box in its frame's local space: x along the crest (width), y across
    his back (length), z up, sitting on the surface heights in probe. The key stops early on
    a side where his body drops away more than MAX_DROP.
    Returns (object, weights): one {bone: weight} per vertex, from the skin right under it."""
    import bmesh
    us, rows, skin = probe
    centre = SEGMENTS // 2
    keep = [centre]
    for step in (1, -1):    # walk out from the crest, stop where the body falls away
        i = centre + step
        while 0 <= i <= SEGMENTS and min(rows[i]) > -MAX_DROP:
            keep.append(i)
            i += step
    keep.sort()
    bm = bmesh.new()
    top, bot = [], []
    for i in keep:
        top.append([bm.verts.new((e, us[i], rows[i][j] + height)) for j, e in enumerate((-half_w, half_w))])
        bot.append([bm.verts.new((e, us[i], rows[i][j] - sink)) for j, e in enumerate((-half_w, half_w))])
    for k in range(len(keep) - 1):
        bm.faces.new((top[k][0], top[k][1], top[k + 1][1], top[k + 1][0]))
        bm.faces.new((bot[k][0], bot[k + 1][0], bot[k + 1][1], bot[k][1]))
        for j in (0, 1):
            bm.faces.new((top[k][j], top[k + 1][j], bot[k + 1][j], bot[k][j]))
    for k in (0, len(keep) - 1):
        bm.faces.new((top[k][0], top[k][1], bot[k][1], bot[k][0]))
    if caps:   # the outer facet at each end is gold: from afar the keyboard gets gold rails
        ends = {0, len(keep) - 2}
        for f in bm.faces:
            ks = {k for k, (t, b) in enumerate(zip(top, bot)) if any(v in f.verts for v in t + b)}
            if len(ks) == 2 and min(ks) in ends or len(ks) == 1:
                f.material_index = 1
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    ob = mm._new_obj(name, bm)
    ob["collar_local"] = (-half_w, float(us[keep[0]]), rows[keep[0]][0])   # the skin under vertex 0
    weights = []
    for i in keep:   # same order as the vertices: top left, top right, bottom left, bottom right
        weights += [skin[i][0], skin[i][1], skin[i][0], skin[i][1]]
    return ob, weights


TRUNK = ("C_", "spine", "root", "head")   # keys only follow trunk, head and tail bones, never limbs


def _skin_weights(body, poly, loc):
    """Bone weights of the skin at loc on a polygon: its corners' weights, nearer corners
    counting more. Limb bones are dropped, so a key end near his shoulder never follows the arm."""
    me = body.data
    mw = body.matrix_world
    acc = {}
    for vi in me.polygons[poly].vertices:
        v = me.vertices[vi]
        k = 1.0 / (((mw @ v.co) - loc).length + 1e-3)
        for g in v.groups:
            name = body.vertex_groups[g.group].name
            if name.startswith(TRUNK):
                acc[name] = acc.get(name, 0.0) + g.weight * k
    t = sum(acc.values()) or 1.0
    return {b: w / t for b, w in acc.items()}


def _skin_follow(ob, raw, rig):
    """Give each vertex the weights of the skin under it (raw, from _draped_key), blended
    FOLLOW of the way from the key's average, pruned to the 4 strongest bones. FOLLOW 0 is
    Mossmaw's rigid mats (keys slide and clip as his back bends), 1 is full per-vertex (keys
    bend with every fold); in between keeps them on the carapace without shearing."""
    avg = {}
    for w in raw:
        for b, x in w.items():
            avg[b] = avg.get(b, 0.0) + x / len(raw)
    groups = {}
    for i, w in enumerate(raw):
        mix = {b: FOLLOW * w.get(b, 0.0) + (1 - FOLLOW) * avg.get(b, 0.0) for b in set(w) | set(avg)}
        top = sorted(mix.items(), key=lambda kv: -kv[1])[:MAX_INFLUENCES]
        s = sum(x for _, x in top) or 1.0
        for b, x in top:
            if b not in groups:
                groups[b] = ob.vertex_groups.new(name=b)
            groups[b].add([i], x / s, 'REPLACE')
    ob.modifiers.new("Armature", 'ARMATURE').object = rig


def _place(ob, rig, frame, coll):
    import bpy
    coll.objects.link(ob)
    ob.parent = rig
    ob.matrix_parent_inverse = rig.matrix_world.inverted()
    ob.matrix_world = frame
    bpy.context.view_layer.update()


def build_keyboard(body, rig, coll, mats):
    """Lay the keyboard along the crest. Returns the key objects, front (head) to back."""
    import mathutils
    surf = _surface_tree(body)
    pts = crest(body, rig, surf[0])
    samples, length = _resample(pts, PITCH)
    frames = [_frame(loc, tan, nrm) for loc, tan, nrm in samples]
    tapers = [1.0 - 0.45 * (i / max(len(samples) - 1, 1)) for i in range(len(samples))]   # narrower toward his tail
    probes = [_probe(f, surf, body, WHITE["half_len"] * t, WHITE["half_w"]) for f, t in zip(frames, tapers)]
    # one smooth key bed: each key sits on the average of its own and its neighbours' surface,
    # so the carapace bands under them don't tilt the keys into shingles
    beds = []
    for i, (us, rows, skin) in enumerate(probes):
        near = [probes[j][1] for j in range(max(0, i - BED_SMOOTH), min(len(probes), i + BED_SMOOTH + 1))]
        bed = []
        for r in range(len(rows)):
            row = []
            for c in range(2):
                vals = [n[r][c] for n in near if n[r][c] > -1.0]
                row.append(sum(vals) / len(vals) if rows[r][c] > -1.0 and vals else rows[r][c])
            bed.append(row)
        beds.append((us, bed, skin))
    keys = []
    for i, (loc, tan, nrm) in enumerate(samples):
        frame = frames[i]
        ob, raw = _draped_key("WhiteKey.%02d" % i, beds[i], WHITE["half_w"], WHITE["height"], WHITE["sink"], caps=True)
        ob.data.materials.append(mats["ivory"])
        ob.data.materials.append(mats["gold"])
        _place(ob, rig, frame, coll)
        # the audit's collar must be a point on his skin: the smoothed bed and crest are not,
        # so with shared weights (FOLLOW 0) use the skin under the key's middle
        mid = probes[i][1][SEGMENTS // 2]
        on_skin = frame @ mathutils.Vector((0, 0, sum(mid) / 2))
        ob["collar"] = list(on_skin if FOLLOW == 0 else frame @ mathutils.Vector(ob["collar_local"]))
        ob["clip_z"] = WHITE["height"] * 0.5
        _skin_follow(ob, raw, rig)
        keys.append(ob)
        if i % 7 in BLACK_AFTER and i + 1 < len(samples):
            nloc, ntan, nnrm = samples[i + 1]
            bl = loc.lerp(nloc, 0.5)
            bf = _frame(bl, (tan + ntan).normalized(), (nrm + nnrm).normalized())
            probe = _probe(bf, surf, body, WHITE["half_len"] * tapers[i] * BLACK["len"], BLACK["half_w"])
            ob, raw = _draped_key("BlackKey.%02d" % i, probe, BLACK["half_w"], BLACK["height"], BLACK["sink"])
            ob.data.materials.append(mats["ebony"])
            _place(ob, rig, bf, coll)
            ob["collar"] = list(bf @ mathutils.Vector(ob["collar_local"]))
            ob["clip_z"] = BLACK["height"] * 0.5
            _skin_follow(ob, raw, rig)
            keys.append(ob)
    return keys, {"crest_points": len(pts), "white": len(samples), "arc_m": round(length, 3),
                  "from": [round(c, 3) for c in pts[0][0]] if pts else None,
                  "to": [round(c, 3) for c in pts[-1][0]] if pts else None}


def sink_audit(keys, actions=ACTIONS):
    """For each key, the deepest its top surface sinks into his body on any frame (metres).
    A key that dips a little reads as a pressed key; one that disappears does not."""
    import bpy
    from mathutils.bvhtree import BVHTree
    s = bpy.context.scene
    body, rig = bpy.data.objects[BODY], bpy.data.objects[RIG]
    keep_frame = s.frame_current
    keep_act = rig.animation_data.action if rig.animation_data else None
    tops = {o.name: [v.index for v in o.data.vertices if v.index % 4 < 2] for o in keys}   # see _draped_key
    worst = {o.name: 0.0 for o in keys}
    for action in actions:
        if action not in bpy.data.actions:
            continue
        act = mm._set_action(action)
        f0, f1 = (int(x) for x in act.frame_range)
        for f in range(f0, f1 + 1, 2):
            s.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            be = body.evaluated_get(dg)
            bm = be.to_mesh()
            tree = BVHTree.FromPolygons([be.matrix_world @ v.co for v in bm.vertices],
                                        [p.vertices[:] for p in bm.polygons])
            be.to_mesh_clear()
            for o in keys:
                e = o.evaluated_get(dg)
                me = e.to_mesh()
                mw = e.matrix_world
                for i in tops[o.name]:
                    p = mw @ me.vertices[i].co
                    if mm._inside(tree, p):
                        worst[o.name] = max(worst[o.name], tree.find_nearest(p)[3])
                e.to_mesh_clear()
    if keep_act:
        mm._set_action(keep_act.name)
    s.frame_set(keep_frame)
    return {k: round(v, 3) for k, v in worst.items()}


# ------ tail note

def _rigid_to_bone(ob, rig, bone):
    ob.vertex_groups.new(name=bone).add([v.index for v in ob.data.vertices], 1.0, 'REPLACE')
    ob.modifiers.new("Armature", 'ARMATURE').object = rig


def _note(name, rig, body, coll, mat, bone="C_Tail_d", size=0.40):
    """A chunky gold eighth note standing on the tip of his tail, rigid on the last tail bone."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    th = size * 0.07                      # thickness of the glyph
    # note head: a tilted, flattened ellipse
    head = bmesh.ops.create_uvsphere(bm, u_segments=10, v_segments=6, radius=1.0)["verts"]
    tilt = mathutils.Matrix.Rotation(math.radians(-25), 3, 'Y')
    for v in head:
        v.co = tilt @ mathutils.Vector((v.co.x * size * 0.24, v.co.y * th * 1.4, v.co.z * size * 0.17))
    # stem: a box up from the right of the head
    x0 = size * 0.2
    sx, sz = size * 0.045, size
    bmesh.ops.create_cube(bm, size=1.0, matrix=mathutils.Matrix.LocRotScale(
        mathutils.Vector((x0, 0, sz / 2)), None, mathutils.Vector((sx, th, sz))))
    # flag: a curved, tapering ribbon from the stem top
    pts = []
    for k in range(6):
        t = k / 5
        pts.append(mathutils.Vector((x0 + size * 0.42 * t, 0, sz - size * 0.55 * t * t - size * 0.08 * t)))
    rows = []
    for k, p in enumerate(pts):
        w = size * 0.16 * (1 - k / 6)
        rows.append([bm.verts.new(p + mathutils.Vector((0, s * th / 2, dz))) for s, dz in
                     ((-1, 0), (1, 0), (1, -w), (-1, -w))])
    for a, c in zip(rows, rows[1:]):
        for j in range(4):
            bm.faces.new((a[j], c[j], c[(j + 1) % 4], a[(j + 1) % 4]))
    bm.faces.new(rows[0][::-1])
    bm.faces.new(rows[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    ob = mm._new_obj(name, bm)
    ob.data.materials.append(mat)
    b = rig.data.bones[bone]
    tip = rig.matrix_world @ b.tail_local
    mw = body.matrix_world
    own = [v for v in body.data.vertices if mm._dominant_bone(body, v) == bone] or list(body.data.vertices)
    near = min(own, key=lambda v: ((mw @ v.co) - tip).length)
    base = mw @ near.co
    frame = mathutils.Matrix.Rotation(math.radians(-135), 4, 'Z')   # faces the front-right, where the cameras are
    frame.translation = base + mathutils.Vector((0, 0, size * 0.3))
    _place(ob, rig, frame, coll)
    ob["collar"] = list(base)
    _rigid_to_bone(ob, rig, bone)
    return ob


# ------ build


def materials():
    """Colours are linear (shader space). The game has no gloss or metal, so none is used:
    what the preview shows is what the export can bake."""
    return {
        "ivory": mm._material("KeyIvory", (0.92, 0.88, 0.74), (0.80, 0.75, 0.60), 30.0, rough=0.45),
        "ebony": mm._material("KeyEbony", (0.045, 0.042, 0.050), (0.025, 0.023, 0.028), 30.0, rough=0.35),
        "gold": mm._material("Gold", (0.85, 0.58, 0.18), (0.45, 0.27, 0.05), 25.0, rough=0.4),
    }


def build_piano(actions=ACTIONS):
    """Build every piece on the rest pose, audit them over the actions, return a report."""
    import bpy
    body, rig = bpy.data.objects[BODY], bpy.data.objects[RIG]
    coll = bpy.data.collections.get("Piano") or bpy.data.collections.new("Piano")
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    for o in list(coll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    mats = materials()
    old_pose = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    keys, kb = build_keyboard(body, rig, coll, mats)
    note = _note("Note.Tail", rig, body, coll, mats["gold"])
    rig.data.pose_position = old_pose
    bpy.context.view_layer.update()
    pieces = keys + [note]
    report = mm.audit(pieces, actions)
    sink = sink_audit(keys, actions)
    bad = sorted(mm.failing(report))
    tris = sum(len(p.vertices) - 2 for o in pieces for p in o.data.polygons)
    nl = note.matrix_world.translation
    return {"note_at": [round(nl.x, 3), round(nl.y, 3), round(nl.z, 3)], "keyboard": kb, "follow": FOLLOW, "key_sink": sink, "pieces": len(pieces), "triangles": tris, "failing": bad,
            "audit": {k: v for k, v in report.items() if k in bad or v[0] > 1.05},
            "actions": [a for a in actions if a in bpy.data.actions]}


# ------ previews

BG = (0.022, 0.020, 0.026)


def stage():
    """A recital stage: warm key light, cool fill, a strong white rim so black lacquer keeps
    its outline, and a dark floor for contact shadows."""
    import bmesh
    import bpy
    s = bpy.context.scene
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

    light("Key", 4.0, (1.0, 0.92, 0.80), (math.radians(50), 0, math.radians(35)), math.radians(8))
    light("Fill", 1.0, (0.72, 0.80, 1.0), (math.radians(65), 0, math.radians(-120)), math.radians(30))
    light("Rim", 4.5, (0.95, 0.95, 1.0), (math.radians(-60), 0, math.radians(170)), math.radians(6))
    ground = bpy.data.objects.get("Ground")
    if ground is None:
        bm = bmesh.new()
        bmesh.ops.create_circle(bm, cap_ends=True, segments=48, radius=8.0)
        ground = mm._new_obj("Ground", bm)
        s.collection.objects.link(ground)
        ground.data.materials.append(mm._material("Stage", (0.05, 0.032, 0.022), (0.03, 0.019, 0.013), 4.0))
    ground.location = (0, 0, -0.005)
    s.camera.data.type = 'PERSP'
    s.camera.data.lens = 70
    s.render.film_transparent = False


GAME_CAM = ((5.6, -11.0, 12.5), (0.0, -0.2, 1.0), 120)   # roughly League's camera pitch, far and long-lens


def render_game_view(out_jpg, size=420, frame=mm.HERO_FRAME, action=mm.HERO_ACTION):
    """A small shot from about League's camera angle and zoom, to check he reads in game."""
    import bpy
    s = bpy.context.scene
    mm._set_action(action)
    s.frame_set(frame)
    s.render.resolution_x = s.render.resolution_y = size
    lens = s.camera.data.lens
    s.camera.data.lens = GAME_CAM[2]
    mm._aim(s.camera, GAME_CAM[0], GAME_CAM[1])
    px = mm._render_rgba(os.path.splitext(out_jpg)[0] + "_tmp.png")
    s.camera.data.lens = lens
    return mm._save_jpg(out_jpg, px)


KEYBOARD_CAM = ((2.2, 1.8, 3.4), (0.0, -0.1, 1.5))   # behind and above his right shoulder


def render_keyboard(out_jpg, size=700, frame=20, action="kogmaw_idle1"):
    """A close look at the keyboard down his back."""
    import bpy
    s = bpy.context.scene
    mm._set_action(action if action in bpy.data.actions else mm.HERO_ACTION)
    s.frame_set(frame)
    s.render.resolution_x = s.render.resolution_y = size
    mm._aim(s.camera, *KEYBOARD_CAM)
    return mm._save_jpg(out_jpg, mm._render_rgba(os.path.splitext(out_jpg)[0] + "_tmp.png"))


def hero_rgba(tmp, size=900, frame=mm.HERO_FRAME, action=mm.HERO_ACTION):
    import bpy
    s = bpy.context.scene
    mm._set_action(action)
    s.frame_set(frame)
    s.render.resolution_x = s.render.resolution_y = size
    mm._aim(s.camera, *mm.HERO_CAM)
    return mm._render_rgba(tmp)


# ------ headless entry


def main(argv):
    """blender -b kogmaw_rigged.blend --python pianomaw.py -- OUT_DIR VERSION"""
    import bpy
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    out_dir = args[0]
    ver = args[1] if len(args) > 1 else "v1"
    root = os.environ.get("LMOD_ROOT", r"C:\NoahCode\LMOD")
    base = os.path.join(root, "base", "kogmaw")
    work = os.path.join(root, "work", "pianomaw")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    report = {"version": ver, "loaded_actions": load_actions(os.path.join(base, "animations"))}
    stage()
    before = hero_rgba(os.path.join(out_dir, "_before.png"))
    apply_texture(os.path.join(base, "kogmaw_base_tx_cm.png"), os.path.join(work, "pianomaw_tx_cm.png"))
    report.update(build_piano())
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(work, "pianomaw_%s.blend" % ver))
    after = hero_rgba(os.path.join(out_dir, "_after.png"))
    mm._save_jpg(os.path.join(out_dir, "pianomaw_%s_hero.jpg" % ver), after)
    mm._save_jpg(os.path.join(out_dir, "pianomaw_original_%s.jpg" % ver), np.concatenate([before, after], axis=1))
    mm.render_run_strip(os.path.join(out_dir, "pianomaw_%s_run_strip.jpg" % ver))
    mm.render_turntable(os.path.join(out_dir, "pianomaw_%s_turntable.jpg" % ver))
    render_game_view(os.path.join(out_dir, "pianomaw_%s_game.jpg" % ver))
    render_keyboard(os.path.join(out_dir, "pianomaw_%s_keyboard.jpg" % ver))
    with open(os.path.join(out_dir, "pianomaw_%s_report.json" % ver), "w") as f:
        json.dump(report, f, indent=1)
    return report


if __name__ == "__main__" and "--" in sys.argv:
    main(sys.argv)
