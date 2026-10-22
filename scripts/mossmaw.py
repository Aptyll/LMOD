"""Mossmaw: turn the rigged Kog'Maw into a mossy one, then render previews.

Run inside Blender with the rigged scene open (body mesh "KogMaw", armature "KogMaw_Rig"):

    import importlib.util
    spec = importlib.util.spec_from_file_location("mossmaw", r"...\\scripts\\mossmaw.py")
    mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
    mm.apply_texture(SRC_PNG, DST_PNG)     # the "moss" recipe in recolor_texture.py
    mm.build_moss()                        # clumps, mushrooms and ferns on back and skull
    mm.stage()                             # lights, ground, camera
    mm.render_hero(OUT_JPG); mm.render_run_strip(OUT_JPG)

Everything is seeded, so a rerun gives the same Mossmaw.

Skinning: moss clumps take their bone weights from the body with a Data Transfer modifier
(nearest face, interpolated), applied, then averaged over the clump so it moves as one piece
with the hide under it, plus an Armature modifier. Mushrooms and ferns bind fully to the
dominant bone under them, so they keep their exact shape.
"""
import importlib.util
import math
import os
import random

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("recolor_texture", os.path.join(_here, "recolor_texture.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

SEED = 7
BODY, RIG = "KogMaw", "KogMaw_Rig"

# ------ texture


def apply_texture(src, dst):
    """Write the moss texture to dst and plug it into the body's image node."""
    import bpy
    new = rt.recolor(src, dst, "moss")
    new.name = os.path.splitext(os.path.basename(dst))[0]
    for mat in bpy.data.objects[BODY].data.materials:
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                node.image = new
    return new


# ------ placement

MOSS_BONES = ("C_Head_b", "C_Chest", "C_Waist", "C_Root", "spine",
              "C_Tail", "C_Tail_b", "C_Tail_c")   # back and skull, never mouth, eyes or limbs
CLUMPS = 18
MUSHROOMS = 4
FERNS = 3
CLUMP_SIZE = (0.09, 0.18)   # radius, metres (he stands about 2.8 m tall)
CLUMP_FLAT = 0.55           # squash along the surface normal
SINK = 0.45                 # fraction of a clump's height pushed into the hide, so it never floats
MIN_UP = 0.35               # surface normal must point this much upward
MIN_GAP = 0.20              # metres between growth spots
GRID = 0.04                 # metres between the downward rays that find his top surface
EYE_CLEAR = 0.32            # metres kept clear around his eye bones
MIN_THICK = 0.2             # the body under a spot must be at least this deep


def _dominant_bone(body, v):
    if not v.groups:
        return None
    g = max(v.groups, key=lambda g: g.weight)
    return body.vertex_groups[g.group].name


def _spots(body, rng):
    """Rest-pose spots on top of his back and skull, spaced apart. World space.

    Rays drop straight down on a grid; only the first hit counts, so the mouth, the belly
    and inner faces can never get moss. Hits on bones outside MOSS_BONES (antennae, eyes,
    limbs) are skipped, and so are slopes steeper than MIN_UP."""
    import mathutils
    mw = body.matrix_world
    inv = mw.inverted()
    nm = mw.to_3x3().inverted().transposed()
    me = body.data
    corners = [mw @ mathutils.Vector(c) for c in body.bound_box]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    down = (inv.to_3x3() @ mathutils.Vector((0, 0, -1))).normalized()
    rig = body.parent
    eyes = [rig.matrix_world @ b.head_local for b in rig.data.bones if b.name.endswith(("_Eye", "_Eye_b"))]
    found = []
    steps_x = int((hi[0] - lo[0]) / GRID) + 1
    steps_y = int((hi[1] - lo[1]) / GRID) + 1
    for ix in range(steps_x):
        for iy in range(steps_y):
            top = mathutils.Vector((lo[0] + ix * GRID, lo[1] + iy * GRID, hi[2] + 1.0))
            hit, loc, normal, face = body.ray_cast(inv @ top, down)
            if not hit:
                continue
            n_world = (nm @ normal).normalized()
            if n_world.z < 0:
                n_world = -n_world       # flipped faces still face the sky here
            if n_world.z < MIN_UP:
                continue
            poly = me.polygons[face]
            vi = min(poly.vertices, key=lambda i: (me.vertices[i].co - loc).length)
            if _dominant_bone(body, me.vertices[vi]) not in MOSS_BONES:
                continue
            if any(((mw @ loc) - e).length < EYE_CLEAR for e in eyes):
                continue
            under = body.ray_cast(loc + down * 1e-3, down)
            if not under[0] or (under[1] - loc).length < MIN_THICK:
                continue   # a thin plate: a buried base would poke out the far side
            found.append((mw @ loc, n_world, vi, face))
    rng.shuffle(found)
    chosen = []
    for p in found:
        if all((p[0] - q[0]).length >= MIN_GAP for q in chosen):
            chosen.append(p)
    return chosen


# ------ meshes


def _material(name, base, dark, noise_scale=40.0, rough=1.0):
    import bpy
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 6.0
    ramp.color_ramp.elements[0].color = (*dark, 1)
    ramp.color_ramp.elements[1].color = (*base, 1)
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _new_obj(name, bm):
    import bpy
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return bpy.data.objects.new(name, me)


def _clump(name, radius, rng):
    """A lumpy low-poly cushion of moss: two or three squashed blobs grown together."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    for k in range(rng.randint(2, 3)):
        r = radius * (1.0 if k == 0 else rng.uniform(0.55, 0.8))
        off = mathutils.Vector((0, 0, 0)) if k == 0 else mathutils.Vector(
            (rng.uniform(-1, 1), rng.uniform(-1, 1), 0)).normalized() * radius * rng.uniform(0.6, 0.9)
        geom = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r)
        for v in geom["verts"]:
            v.co *= 1.0 + rng.uniform(-0.12, 0.12)
            v.co.z *= CLUMP_FLAT
            v.co += off
    return _new_obj(name, bm)   # flat shaded, to sit with his hand-painted low-poly look


def _mushroom(name, height, cap_r):
    import bmesh
    import mathutils
    base = -0.08   # the stem starts deep in the hide, so it never lifts off as he runs
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=7, radius1=cap_r * 0.32, radius2=cap_r * 0.22,
                          depth=height, matrix=mathutils.Matrix.Translation((0, 0, base + height / 2)))
    stem_faces = len(bm.faces)
    top = base + height
    cap = bmesh.ops.create_uvsphere(bm, u_segments=9, v_segments=5, radius=cap_r,
                                    matrix=mathutils.Matrix.Translation((0, 0, top)))
    for v in cap["verts"]:
        dz = v.co.z - top
        v.co.z = top + (dz * 0.6 if dz > 0 else dz * 0.12)
    ob = _new_obj(name, bm)
    for i, p in enumerate(ob.data.polygons):
        p.material_index = 0 if i < stem_faces else 1
    return ob


def _fern(name, size, rng):
    """A tiny fern: a few curled, tapering fronds fanning out from one root."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    fronds = rng.randint(4, 6)
    for f in range(fronds):
        rot = mathutils.Matrix.Rotation(2 * math.pi * f / fronds + rng.uniform(-0.3, 0.3), 3, 'Z')
        lean = rng.uniform(0.5, 0.9)
        length = size * rng.uniform(0.8, 1.15)
        segs = 4
        left, right = [], []
        for k in range(segs + 1):
            t = k / segs
            pitch = lean + t * 0.9          # curls outward and down toward the tip
            c = mathutils.Vector((math.sin(pitch) * length * t, 0, math.cos(pitch) * length * t * 0.9 - 0.05))
            w = mathutils.Vector((0, size * 0.16 * (1 - t) + 0.004, 0))
            left.append(bm.verts.new(rot @ (c + w)))
            right.append(bm.verts.new(rot @ (c - w)))
        for k in range(segs):
            bm.faces.new((left[k], right[k], right[k + 1], left[k + 1]))
    return _new_obj(name, bm)


# ------ skinning


def _orient(ob, rig, pos, normal, rng):
    import mathutils
    ob.parent = rig
    ob.matrix_parent_inverse = rig.matrix_world.inverted()
    rot = normal.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    spin = mathutils.Matrix.Rotation(rng.uniform(0, 2 * math.pi), 4, 'Z')
    ob.matrix_world = mathutils.Matrix.Translation(pos) @ rot @ spin


def _skin_rigid(ob, body, rig, vert_index):
    """Bind every vertex fully to the dominant bone of the body vertex it grows from.
    One bone means no blending, so the piece keeps its exact shape."""
    name = _dominant_bone(body, body.data.vertices[vert_index])
    ob.vertex_groups.new(name=name).add([v.index for v in ob.data.vertices], 1.0, 'REPLACE')
    ob.modifiers.new("Armature", 'ARMATURE').object = rig


def _skin_transfer(ob, body, rig):
    import bpy
    for g in body.vertex_groups:
        ob.vertex_groups.new(name=g.name)
    dt = ob.modifiers.new("Weights", 'DATA_TRANSFER')
    dt.object = body
    dt.use_object_transform = True
    dt.use_vert_data = True
    dt.data_types_verts = {'VGROUP_WEIGHTS'}
    dt.vert_mapping = 'POLYINTERP_NEAREST'
    dt.layers_vgroup_select_src = 'ALL'
    dt.layers_vgroup_select_dst = 'NAME'
    with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
        bpy.ops.object.modifier_apply(modifier=dt.name)
    # Per-vertex weights would stretch a clump that straddles a joint (measured up to 11x
    # on the run). Every vertex gets the clump's average weights instead, so it moves as
    # one piece with the patch of hide it grows on.
    n = len(ob.data.vertices)
    avg = {}
    for v in ob.data.vertices:
        for g in v.groups:
            avg[g.group] = avg.get(g.group, 0.0) + g.weight / n
    total = sum(avg.values()) or 1.0
    keep = {ob.vertex_groups[i].name: w / total for i, w in avg.items() if w / total > 0.02}
    norm = sum(keep.values())
    for g in list(ob.vertex_groups):
        ob.vertex_groups.remove(g)
    idx = list(range(n))
    for name, w in keep.items():
        ob.vertex_groups.new(name=name).add(idx, w / norm, 'REPLACE')
    ob.modifiers.new("Armature", 'ARMATURE').object = rig


def _inside(tree, p):
    """Ray parity: an odd number of crossings means p is inside the body."""
    import mathutils
    hits, o, d = 0, p.copy(), mathutils.Vector((0.31, 0.17, 0.93)).normalized()
    for _ in range(64):
        loc = tree.ray_cast(o, d)[0]
        if loc is None:
            break
        hits += 1
        o = loc + d * 1e-4
    return hits % 2 == 1


def audit(pieces, action="kogmaw_run"):
    """Play the action and return {name: (worst stretch, frames its buried base shows)}.
    Stretch compares every edge to its rest length; the buried base is the piece's lowest
    vertex, which starts inside the hide."""
    import bpy
    from mathutils.bvhtree import BVHTree
    s = bpy.context.scene
    body = bpy.data.objects[BODY]
    act = _set_action(action)
    keep = s.frame_current
    edges = {o.name: [e.vertices[:] for e in o.data.edges] for o in pieces}
    rest = {o.name: [(o.data.vertices[a].co - o.data.vertices[b].co).length for a, b in edges[o.name]]
            for o in pieces}
    base = {o.name: min(range(len(o.data.vertices)), key=lambda i: o.data.vertices[i].co.z) for o in pieces}
    worst = {o.name: [1.0, 0] for o in pieces}
    f0, f1 = (int(x) for x in act.frame_range)
    for f in range(f0, f1 + 1):
        s.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        be = body.evaluated_get(dg)
        bm = be.to_mesh()
        tree = BVHTree.FromPolygons([be.matrix_world @ v.co for v in bm.vertices],
                                    [p.vertices[:] for p in bm.polygons])
        be.to_mesh_clear()
        for o in pieces:
            e = o.evaluated_get(dg)
            me = e.to_mesh()
            for (a, b), r0 in zip(edges[o.name], rest[o.name]):
                ln = (me.vertices[a].co - me.vertices[b].co).length
                worst[o.name][0] = max(worst[o.name][0], ln / max(r0, 1e-6), r0 / max(ln, 1e-6))
            if not _inside(tree, e.matrix_world @ me.vertices[base[o.name]].co):
                worst[o.name][1] += 1
            e.to_mesh_clear()
    s.frame_set(keep)
    return {k: (round(a, 3), b) for k, (a, b) in worst.items()}


STRETCH_MAX = 1.12          # a piece may stretch or squash this much during the action, no more
UPRIGHT = 2.0               # how hard mushrooms and ferns lean from the surface normal toward up


def build_moss(seed=SEED, action="kogmaw_run"):
    """Grow moss, mushrooms and ferns on the rest pose, then audit them over the action:
    any piece that stretches past STRETCH_MAX or whose buried base surfaces on any frame
    is regrown on the next free spot. Reruns replace the old growth."""
    import bpy
    import mathutils
    body, rig = bpy.data.objects[BODY], bpy.data.objects[RIG]
    rng = random.Random(seed)
    coll = bpy.data.collections.get("Moss") or bpy.data.collections.new("Moss")
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)

    def remove(o):
        me = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if me and me.users == 0:
            bpy.data.meshes.remove(me)

    stale = [o for o in bpy.data.objects
             if o.name.startswith(("MossClump.", "Mushroom.", "Fern.")) and not o.users_collection]
    for o in list(coll.objects) + stale:
        remove(o)

    moss_mat = _material("Moss", (0.085, 0.16, 0.03), (0.025, 0.06, 0.012), 45.0)
    stem_mat = _material("MushroomStem", (0.78, 0.74, 0.60), (0.50, 0.46, 0.34), 60.0)
    cap_mat = _material("MushroomCap", (0.85, 0.40, 0.10), (0.42, 0.14, 0.04), 20.0, rough=0.6)
    fern_mat = _material("Fern", (0.36, 0.55, 0.14), (0.12, 0.26, 0.05), 25.0, rough=0.8)
    fern_mat.use_backface_culling = False

    old_pose = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    spots = iter(_spots(body, rng))

    def grow(kind, name):
        try:
            pos, n, vi, _ = next(spots)
        except StopIteration:
            return None
        if kind == "clump":
            r = rng.uniform(*CLUMP_SIZE)
            ob, mats, at = _clump(name, r, rng), [moss_mat], pos - n * (r * CLUMP_FLAT * SINK)
        elif kind == "mushroom":
            ob = _mushroom(name, rng.uniform(0.16, 0.24), rng.uniform(0.08, 0.12))
            mats, at = [stem_mat, cap_mat], pos
        else:
            ob, mats, at = _fern(name, rng.uniform(0.18, 0.26), rng), [fern_mat], pos
        for m in mats:
            ob.data.materials.append(m)
        coll.objects.link(ob)
        if kind != "clump":   # stalks grow toward the sky, not straight out of a slope
            n = (n + mathutils.Vector((0, 0, UPRIGHT))).normalized()
        _orient(ob, rig, at, n, rng)
        bpy.context.view_layer.update()
        if kind == "clump":
            _skin_transfer(ob, body, rig)
        else:
            _skin_rigid(ob, body, rig, vi)
        return ob

    slots = ([("clump", "MossClump.%02d" % i) for i in range(CLUMPS)]
             + [("mushroom", "Mushroom.%02d" % i) for i in range(MUSHROOMS)]
             + [("fern", "Fern.%02d" % i) for i in range(FERNS)])
    pieces = {name: grow(kind, name) for kind, name in slots}
    regrown = 0
    while True:
        live = [o for o in pieces.values() if o is not None]
        rig.data.pose_position = old_pose
        report = audit(live, action)
        rig.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        bad = [k for k, (st, shows) in report.items() if st > STRETCH_MAX or shows]
        if not bad:
            break
        for kind, name in slots:
            if pieces[name] is not None and pieces[name].name in bad:
                remove(pieces[name])
                pieces[name] = grow(kind, name)
                regrown += 1

    rig.data.pose_position = old_pose
    bpy.context.view_layer.update()
    return {"placed": [o.name for o in pieces.values() if o], "dropped": [k for k, o in pieces.items() if o is None],
            "regrown": regrown, "audit": report}


# ------ previews


def stage():
    """Warm key, cool fill, green rim, a dark ground for contact shadows, a perspective camera."""
    import bmesh
    import bpy
    s = bpy.context.scene
    world = s.world or bpy.data.worlds.new("World")
    s.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.055, 0.065, 0.06, 1)
    bg.inputs["Strength"].default_value = 1.0

    def light(name, energy, color, rot, angle):
        ob = bpy.data.objects.get(name)
        if ob is None:
            ob = bpy.data.objects.new(name, bpy.data.lights.new(name, 'SUN'))
            s.collection.objects.link(ob)
        ob.data.energy, ob.data.color, ob.data.angle = energy, color, angle
        ob.rotation_euler = rot

    light("Sun", 4.0, (1.0, 0.93, 0.80), (math.radians(50), 0, math.radians(35)), math.radians(8))
    light("Fill", 1.2, (0.70, 0.82, 1.0), (math.radians(65), 0, math.radians(-120)), math.radians(30))
    light("Rim", 2.5, (0.85, 1.0, 0.75), (math.radians(-60), 0, math.radians(170)), math.radians(10))

    ground = bpy.data.objects.get("Ground")
    if ground is None:
        bm = bmesh.new()
        bmesh.ops.create_circle(bm, cap_ends=True, segments=48, radius=8.0)
        ground = _new_obj("Ground", bm)
        s.collection.objects.link(ground)
        ground.data.materials.append(_material("GroundMat", (0.022, 0.027, 0.022), (0.014, 0.017, 0.014), 6.0))
    ground.location = (0, 0, -0.005)

    s.camera.data.type = 'PERSP'
    s.camera.data.lens = 70
    s.render.film_transparent = False


def _aim(cam, loc, target):
    import mathutils
    cam.location = loc
    cam.rotation_euler = (mathutils.Vector(target) - mathutils.Vector(loc)).to_track_quat('-Z', 'Y').to_euler()


def _set_action(name):
    import bpy
    rig = bpy.data.objects[RIG]
    act = bpy.data.actions[name]
    rig.animation_data_create().action = act
    if getattr(act, "slots", None):
        rig.animation_data.action_slot = act.slots[0]
    return act


def _render_rgba(path):
    import bpy
    s = bpy.context.scene
    s.render.image_settings.file_format = 'PNG'
    s.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    os.remove(path)
    return px


def _save_jpg(path, rgba, quality=90):
    import bpy
    h, w = rgba.shape[:2]
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=False)
    img.pixels = rgba.ravel()
    img.filepath_raw = path
    img.file_format = 'JPEG'
    bpy.context.scene.render.image_settings.quality = quality
    img.save()
    bpy.data.images.remove(img)
    return path


HERO_CAM = ((4.7, -5.9, 2.9), (0.0, -0.2, 1.3))
STRIP_CAM = ((7.4, -3.4, 2.6), (0.0, 0.0, 1.2))


def render_hero(out_jpg, frame=6, action="kogmaw_run", size=1200):
    """A 3/4 front shot from slightly above."""
    import bpy
    s = bpy.context.scene
    _set_action(action)
    s.frame_set(frame)
    s.render.resolution_x = s.render.resolution_y = size
    _aim(s.camera, *HERO_CAM)
    return _save_jpg(out_jpg, _render_rgba(os.path.splitext(out_jpg)[0] + "_tmp.png"))


def render_run_strip(out_jpg, action="kogmaw_run", count=6, size=480):
    """count evenly spaced frames of one loop, side by side, from a side 3/4 view."""
    import bpy
    s = bpy.context.scene
    act = _set_action(action)
    f0, f1 = act.frame_range
    s.render.resolution_x = s.render.resolution_y = size
    _aim(s.camera, *STRIP_CAM)
    tmp = os.path.splitext(out_jpg)[0] + "_tmp.png"
    tiles = []
    for k in range(count):
        s.frame_set(int(round(f0 + (f1 - f0 + 1) * k / count)))
        tiles.append(_render_rgba(tmp))
    return _save_jpg(out_jpg, np.concatenate(tiles, axis=1))
