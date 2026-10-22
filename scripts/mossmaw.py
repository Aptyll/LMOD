"""Mossmaw: turn the rigged Kog'Maw into a mossy one, then render previews.

Run inside Blender with the rigged scene open (body mesh "KogMaw", armature "KogMaw_Rig"):

    import importlib.util
    spec = importlib.util.spec_from_file_location("mossmaw", r"...\\scripts\\mossmaw.py")
    mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
    mm.apply_texture(SRC_PNG, DST_PNG)     # the "moss" recipe in recolor_texture.py
    mm.build_moss()                        # everything in GROWTH, audited over the run
    mm.stage()                             # lights, ground, camera
    mm.render_hero(OUT_JPG); mm.render_run_strip(OUT_JPG)

Everything is seeded, so a rerun gives the same Mossmaw.

GROWTH lists what grows and where: moss mats and cushions on his back, tail, skull top and
shoulders; ferns, mushroom clusters (one glowing) and leaf sprouts among them; leaves out
of the antenna tips; moss strands hanging from his jaw and antenna stalks; grass on his legs.

Skinning: moss mats and cushions take their bone weights from the body with a Data Transfer
modifier (nearest face, interpolated), applied, then averaged over the piece so it moves as
one with the hide under it, plus an Armature modifier. Everything else binds fully to the
dominant bone under it, so it keeps its exact shape and swings with that bone.

build_moss audits every piece on every frame of the run and regrows any that stretch, come
loose from the hide or dip into the ground.
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

TOP_BONES = ("C_Head_b", "C_Chest", "C_Waist", "C_Root", "spine", "C_Tail", "C_Tail_b", "C_Tail_c",
             "C_Tail_d", "R_UpArm", "L_UpArm")       # back, spine, tail, skull top, shoulders
LEG_BONES = ("R_Thigh", "L_Thigh", "R_Knee", "L_Knee", "R_Foot", "L_Foot")
JAW_BONES = ("C_Head", "C_Mouth", "C_Mouth_b", "C_Mouth_c", "C_Mouth_d")   # undersides only
ANTENNA_BONES = ("R_Hair", "R_Hair_b", "R_Hair_c", "R_Hair_d", "L_Hair", "L_Hair_b", "L_Hair_c", "L_Hair_d")
ANTENNA_TIPS = ("R_Hair_b", "R_Hair_d", "L_Hair_b", "L_Hair_d")
GRID = 0.035                # metres between the rays that find his surfaces
EYE_CLEAR = 0.32            # metres kept clear around his eye bones
MIN_UP = 0.35               # a top spot's surface must face this much upward (a jaw spot: downward)


def _dominant_bone(body, v):
    if not v.groups:
        return None
    g = max(v.groups, key=lambda g: g.weight)
    return body.vertex_groups[g.group].name


def _eyes(rig):
    return [rig.matrix_world @ b.head_local for b in rig.data.bones if b.name.endswith(("_Eye", "_Eye_b"))]


def _ray_spots(body, bones, from_below=False, min_thick=0.2):
    """Rest-pose spots where a vertical ray first meets the body, on the given bones.

    Rays come straight down (tops: back, skull, legs) or straight up (undersides: the jaw).
    Only the first hit counts, so the inside of the mouth, the belly and inner faces never
    get growth. Spots must face the ray, sit clear of the eyes, and have at least min_thick
    of body behind them, so a buried root never pokes out of a thin plate."""
    import mathutils
    mw = body.matrix_world
    inv = mw.inverted()
    nm = mw.to_3x3().inverted().transposed()
    me = body.data
    corners = [mw @ mathutils.Vector(c) for c in body.bound_box]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    sign = 1.0 if from_below else -1.0
    ray = (inv.to_3x3() @ mathutils.Vector((0, 0, sign))).normalized()
    start_z = lo[2] - 1.0 if from_below else hi[2] + 1.0
    eyes = _eyes(body.parent)
    found = []
    for ix in range(int((hi[0] - lo[0]) / GRID) + 1):
        for iy in range(int((hi[1] - lo[1]) / GRID) + 1):
            o = mathutils.Vector((lo[0] + ix * GRID, lo[1] + iy * GRID, start_z))
            hit, loc, normal, face = body.ray_cast(inv @ o, ray)
            if not hit:
                continue
            n = (nm @ normal).normalized()
            if n.z * sign > 0:
                n = -n                   # flipped faces still face the ray here
            if -n.z * sign < MIN_UP:
                continue
            poly = me.polygons[face]
            vi = min(poly.vertices, key=lambda i: (me.vertices[i].co - loc).length)
            if _dominant_bone(body, me.vertices[vi]) not in bones:
                continue
            if any(((mw @ loc) - e).length < EYE_CLEAR for e in eyes):
                continue
            behind = body.ray_cast(loc + ray * 1e-3, ray)
            if not behind[0] or (behind[1] - loc).length < min_thick:
                continue
            found.append((mw @ loc, n, vi))
    return found


def _antenna_spots(body, rig, bones, tips=False):
    """Spots on the antenna stalks: their undersides (for hanging strands) or, with tips,
    the vertex furthest along each bone (for sprouts). The direction is the way out."""
    import mathutils
    mw = body.matrix_world
    out = []
    for bname in bones:
        b = rig.data.bones[bname]
        head, tail = rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local
        axis = (tail - head).normalized()
        verts = [v for v in body.data.vertices if _dominant_bone(body, v) == bname]
        if not verts:
            continue
        if tips:   # the centre of the furthest few vertices: inside the tip, not on its skin
            far = sorted(verts, key=lambda v: -(mw @ v.co - head).dot(axis))[:8]
            centre = sum((mw @ v.co for v in far), mathutils.Vector()) / len(far)
            out.append((centre, (axis + mathutils.Vector((0, 0, 0.6))).normalized(), far[0].index))
            continue
        for v in verts:
            p = mw @ v.co
            radial = p - (head + axis * (p - head).dot(axis))
            if radial.length > 1e-4 and radial.normalized().z < -0.5:
                out.append((p, mathutils.Vector((0, 0, -1)), v.index))
    return out


def _pick(cands, gap, rng):
    """Shuffle, then keep spots at least gap apart. Returns an iterator of the keepers."""
    cands = list(cands)
    rng.shuffle(cands)
    kept = []
    for c in cands:
        if all((c[0] - k[0]).length >= gap for k in kept):
            kept.append(c)
    return iter(kept)


# ------ meshes


def _material(name, base, dark, noise_scale=40.0, rough=1.0, glow=None):
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
    if glow:
        bsdf.inputs["Emission Color"].default_value = (*glow[0], 1)
        bsdf.inputs["Emission Strength"].default_value = glow[1]
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _new_obj(name, bm, anchor=None):
    """Make the object. anchor is the local point that starts buried in the hide; the
    audit checks the vertex nearest to it (default: the lowest vertex)."""
    import bpy
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    vs = me.vertices
    if anchor is None:
        ob["anchor"] = min(range(len(vs)), key=lambda i: vs[i].co.z)
    else:
        import mathutils
        a = mathutils.Vector(anchor)
        ob["anchor"] = min(range(len(vs)), key=lambda i: (vs[i].co - a).length)
    return ob


def _clump(name, radius, flat, rng):
    """A lumpy low-poly cushion of moss: two to four squashed blobs grown together."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    for k in range(rng.randint(2, 4)):
        r = radius * (1.0 if k == 0 else rng.uniform(0.5, 0.8))
        off = mathutils.Vector((0, 0, 0)) if k == 0 else mathutils.Vector(
            (rng.uniform(-1, 1), rng.uniform(-1, 1), 0)).normalized() * radius * rng.uniform(0.6, 1.0)
        geom = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=r)   # 80 faces a blob
        for v in geom["verts"]:
            v.co *= 1.0 + rng.uniform(-0.15, 0.15)
            v.co.z *= flat
            v.co += off
    for f in bm.faces:
        f.smooth = True   # smooth shading turns the low-poly blobs into soft cushions
    return _new_obj(name, bm)


ROOT = -0.06   # stalks, strands and blades start this deep in the hide


def _add_mushroom(bm, at, height, cap_r):
    """One mushroom into bm, standing at local point at. Returns the cap's faces."""
    import bmesh
    import mathutils
    base = at.z + ROOT
    bmesh.ops.create_cone(bm, cap_ends=True, segments=7, radius1=cap_r * 0.32, radius2=cap_r * 0.22,
                          depth=height, matrix=mathutils.Matrix.Translation((at.x, at.y, base + height / 2)))
    top = base + height
    cap = bmesh.ops.create_uvsphere(bm, u_segments=9, v_segments=5, radius=cap_r,
                                    matrix=mathutils.Matrix.Translation((at.x, at.y, top)))
    for v in cap["verts"]:
        dz = v.co.z - top
        v.co.z = top + (dz * 0.6 if dz > 0 else dz * 0.12)
    return {f for v in cap["verts"] for f in v.link_faces}


def _mushrooms(name, rng, count):
    """A cluster of mixed-size mushrooms; the first is the tallest."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    caps = set()
    for k in range(count):
        at = mathutils.Vector((0, 0, 0)) if k == 0 else mathutils.Vector(
            (math.cos(k * 2.4), math.sin(k * 2.4), 0)) * rng.uniform(0.05, 0.09)
        scale = 1.0 if k == 0 else rng.uniform(0.45, 0.8)
        caps |= _add_mushroom(bm, at, rng.uniform(0.17, 0.24) * scale, rng.uniform(0.08, 0.11) * scale)
    for f in bm.faces:
        f.material_index = 1 if f in caps else 0
    return _new_obj(name, bm, anchor=(0, 0, ROOT))


def _fern(name, size, rng):
    """A fern: curled, tapering fronds fanning out from one root."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    fronds = rng.randint(5, 7)
    for f in range(fronds):
        rot = mathutils.Matrix.Rotation(2 * math.pi * f / fronds + rng.uniform(-0.3, 0.3), 3, 'Z')
        lean = rng.uniform(0.35, 0.8)
        length = size * rng.uniform(0.8, 1.15)
        segs = 5
        left, right, mid = [], [], []
        for k in range(segs + 1):
            t = k / segs
            pitch = lean + t * 0.8          # curls outward and down toward the tip
            c = mathutils.Vector((math.sin(pitch) * length * t, 0, math.cos(pitch) * length * t * 0.9 + ROOT))
            w = mathutils.Vector((0, size * 0.18 * math.sin(math.pi * min(t + 0.15, 1)) + 0.004, 0))
            ridge = mathutils.Vector((0, 0, size * 0.03 * (1 - t)))
            left.append(bm.verts.new(rot @ (c + w)))
            right.append(bm.verts.new(rot @ (c - w)))
            mid.append(bm.verts.new(rot @ (c + ridge)))
        for k in range(segs):   # a folded frond: two strips meeting on a raised midrib
            bm.faces.new((left[k], mid[k], mid[k + 1], left[k + 1]))
            bm.faces.new((mid[k], right[k], right[k + 1], mid[k + 1]))
    return _new_obj(name, bm, anchor=(0, 0, ROOT))


def _strand(name, length, rng):
    """Hanging moss: a bundle of two or three wavy, tapering three-sided strands along
    local +Z (pointed at the ground), sharing one root."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    for s in range(rng.randint(2, 3)):
        ln = length * (1.0 if s == 0 else rng.uniform(0.55, 0.85))
        spread = mathutils.Vector((math.cos(s * 2.3), math.sin(s * 2.3), 0)) * (0 if s == 0 else 0.03)
        phase, amp = rng.uniform(0, 6.28), rng.uniform(0.015, 0.03)
        segs, rings = 6, []
        for k in range(segs + 1):
            t = k / segs
            wave = mathutils.Vector((math.sin(phase + t * 7) * amp * t, math.cos(phase + t * 5) * amp * t, 0))
            c = mathutils.Vector((0, 0, ROOT + (ln - ROOT) * t)) + spread * t + wave
            r = 0.03 * (1 - t) ** 0.7 + 0.005
            rings.append([bm.verts.new(c + mathutils.Vector((math.cos(a) * r, math.sin(a) * r, 0)))
                          for a in (0, 2.094, 4.189)])
        for k in range(segs):
            for j in range(3):
                bm.faces.new((rings[k][j], rings[k][(j + 1) % 3], rings[k + 1][(j + 1) % 3], rings[k + 1][j]))
        end = bm.verts.new(sum((v.co for v in rings[-1]), mathutils.Vector()) / 3 + mathutils.Vector((0, 0, 0.02)))
        for j in range(3):
            bm.faces.new((rings[-1][j], rings[-1][(j + 1) % 3], end))
    for f in bm.faces:
        f.smooth = True
    return _new_obj(name, bm, anchor=(0, 0, ROOT))


def _leaves(name, size, rng, count):
    """A sprout: folded leaves fanning up and out from one root."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    for k in range(count):
        rot = mathutils.Matrix.Rotation(2 * math.pi * k / count + rng.uniform(-0.4, 0.4), 3, 'Z')
        tilt = mathutils.Matrix.Rotation(rng.uniform(0.35, 0.8), 3, 'Y')
        ln, w = size * rng.uniform(0.8, 1.2), size * 0.32
        pts = [(0, 0, ROOT), (w, -w * 0.5, ln * 0.45), (0, 0.06 * size, ln), (-w, -w * 0.5, ln * 0.45)]
        root, a, tip, b = [bm.verts.new(rot @ tilt @ mathutils.Vector(p)) for p in pts]
        mid = bm.verts.new(rot @ tilt @ mathutils.Vector((0, w * 0.25, ln * 0.45)))
        bm.faces.new((root, a, tip, mid))
        bm.faces.new((root, mid, tip, b))
    return _new_obj(name, bm, anchor=(0, 0, ROOT))


def _tuft(name, size, rng):
    """A grass tuft: thin blades leaning out from one root."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    blades = rng.randint(6, 9)
    for k in range(blades):
        rot = mathutils.Matrix.Rotation(2 * math.pi * k / blades + rng.uniform(-0.3, 0.3), 3, 'Z')
        lean, ln, w = rng.uniform(0.15, 0.6), size * rng.uniform(0.6, 1.1), 0.012
        tip = mathutils.Vector((math.sin(lean) * ln, 0, math.cos(lean) * ln))
        a = bm.verts.new(rot @ mathutils.Vector((0, w, ROOT)))
        b = bm.verts.new(rot @ mathutils.Vector((0, -w, ROOT)))
        c = bm.verts.new(rot @ tip)
        bm.faces.new((a, b, c))
    return _new_obj(name, bm, anchor=(0, 0, ROOT))


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
    One bone means no blending, so the piece keeps its exact shape and swings with it."""
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


# ------ audit

STRETCH_MAX = 1.12          # a piece may stretch or squash this much during the action, no more
TOUCH = 0.025               # a root this close to the surface still counts as attached (thin antennae)
FLOOR = -0.01               # no vertex may dip below the ground


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
    """Play the action and return {name: (worst stretch, frames it fails)}. A frame fails
    when the piece's buried root has come out of the hide, or the piece dips into the
    ground. Stretch compares every edge to its rest length."""
    import bpy
    from mathutils.bvhtree import BVHTree
    s = bpy.context.scene
    body = bpy.data.objects[BODY]
    act = _set_action(action)
    keep = s.frame_current
    edges = {o.name: [e.vertices[:] for e in o.data.edges] for o in pieces}
    rest = {o.name: [(o.data.vertices[a].co - o.data.vertices[b].co).length for a, b in edges[o.name]]
            for o in pieces}
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
            w = worst[o.name]
            for (a, b), r0 in zip(edges[o.name], rest[o.name]):
                ln = (me.vertices[a].co - me.vertices[b].co).length
                w[0] = max(w[0], ln / max(r0, 1e-6), r0 / max(ln, 1e-6))
            root = e.matrix_world @ me.vertices[o["anchor"]].co
            attached = _inside(tree, root) or tree.find_nearest(root)[3] < TOUCH
            low = min((e.matrix_world @ v.co).z for v in me.vertices)
            if not attached or low < FLOOR:
                w[1] += 1
            e.to_mesh_clear()
    s.frame_set(keep)
    return {k: (round(a, 3), b) for k, (a, b) in worst.items()}


# ------ growth

# kind: (how many, spot finder, gap between spots of this kind in metres)
GROWTH = {
    "mat":      (36, "top", 0.13),    # big layered cushions that change his silhouette
    "cushion":  (34, "top", 0.09),    # small ones filling between and over the mats
    "fern":     (6, "top", 0.35),
    "mushrooms": (6, "top", 0.3),     # clusters of 3-5; the first one glows
    "sprout":   (8, "top", 0.25),     # leaves between his back spikes
    "tip":      (4, "tips", 0.0),     # leaves out of each antenna tip
    "beard":    (14, "jaw", 0.08),    # moss hanging from his jaw and chin
    "drape":    (6, "antenna", 0.15), # moss hanging from his antenna stalks
    "tuft":     (16, "legs", 0.1),    # grass along his legs and on his feet
}
MAT_SIZE, MAT_FLAT, MAT_SINK = (0.11, 0.24), 0.7, 0.3
CUSHION_SIZE, CUSHION_FLAT, CUSHION_SINK = (0.05, 0.1), 0.65, 0.35
UPRIGHT = 2.0               # how hard stalks lean from the surface normal toward up


def build_moss(seed=SEED, action="kogmaw_run"):
    """Grow moss, mushrooms, ferns, strands and grass on the rest pose, then audit every
    piece over the action: any that stretches past STRETCH_MAX, comes loose or dips into
    the ground on any frame is regrown on the next free spot. Reruns replace old growth."""
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

    prefixes = tuple(k.capitalize() + "." for k in GROWTH) + ("MossClump.", "Mushroom.", "Fern.")
    stale = [o for o in bpy.data.objects if o.name.startswith(prefixes) and not o.users_collection]
    for o in list(coll.objects) + stale:
        remove(o)

    mats = {
        "moss": _material("Moss", (0.085, 0.16, 0.03), (0.025, 0.06, 0.012), 45.0),
        "moss_light": _material("MossLight", (0.15, 0.25, 0.05), (0.05, 0.10, 0.02), 50.0),
        "hanging": _material("HangingMoss", (0.09, 0.14, 0.04), (0.03, 0.05, 0.015), 30.0),
        "leaf": _material("Leaf", (0.30, 0.55, 0.12), (0.12, 0.28, 0.05), 20.0, rough=0.7),
        "fern": _material("Fern", (0.30, 0.48, 0.11), (0.10, 0.22, 0.04), 25.0, rough=0.8),
        "grass": _material("Grass", (0.16, 0.26, 0.05), (0.06, 0.11, 0.02), 30.0),
        "stem": _material("MushroomStem", (0.78, 0.74, 0.60), (0.50, 0.46, 0.34), 60.0),
        "cap": _material("MushroomCap", (0.85, 0.40, 0.10), (0.42, 0.14, 0.04), 20.0, rough=0.6),
        "glow": _material("GlowCap", (0.25, 0.55, 0.35), (0.12, 0.35, 0.22), 20.0, rough=0.5,
                          glow=((0.35, 1.0, 0.55), 1.2)),
    }

    old_pose = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    finders = {
        "top": lambda: _ray_spots(body, TOP_BONES),
        "legs": lambda: _ray_spots(body, LEG_BONES, min_thick=0.08),
        "jaw": lambda: _ray_spots(body, JAW_BONES, from_below=True, min_thick=0.08),
        "antenna": lambda: _antenna_spots(body, rig, ANTENNA_BONES),
        "tips": lambda: _antenna_spots(body, rig, ANTENNA_TIPS, tips=True),
    }
    cands = {k: f() for k, f in finders.items()}
    spots = {kind: _pick(cands[where], gap, rng) for kind, (n, where, gap) in GROWTH.items()}
    up = mathutils.Vector((0, 0, UPRIGHT))

    def grow(kind, name, first):
        try:
            pos, n, vi = next(spots[kind])
        except StopIteration:
            return None
        rigid, at = True, pos
        if kind in ("mat", "cushion"):
            size, flat, sink = ((MAT_SIZE, MAT_FLAT, MAT_SINK) if kind == "mat"
                                else (CUSHION_SIZE, CUSHION_FLAT, CUSHION_SINK))
            r = rng.uniform(*size)
            ob = _clump(name, r, flat, rng)
            ob.data.materials.append(mats["moss"] if kind == "mat" or rng.random() < 0.5 else mats["moss_light"])
            at, rigid = pos - n * (r * flat * sink), False
        elif kind == "fern":
            ob = _fern(name, rng.uniform(0.28, 0.42), rng)
            ob.data.materials.append(mats["fern"])
            n = (n + up).normalized()
        elif kind == "mushrooms":
            ob = _mushrooms(name, rng, rng.randint(3, 5))
            ob.data.materials.append(mats["stem"])
            ob.data.materials.append(mats["glow"] if first else mats["cap"])
            n = (n + up).normalized()
        elif kind in ("sprout", "tip"):
            ob = _leaves(name, rng.uniform(0.10, 0.16) if kind == "sprout" else rng.uniform(0.12, 0.18),
                         rng, rng.randint(3, 4))
            ob.data.materials.append(mats["leaf"])
            if kind == "sprout":
                n = (n + up).normalized()
        elif kind in ("beard", "drape"):
            ob = _strand(name, rng.uniform(0.16, 0.34) if kind == "beard" else rng.uniform(0.18, 0.30), rng)
            ob.data.materials.append(mats["hanging"])
            n = mathutils.Vector((0, 0, -1))
        else:  # tuft
            ob = _tuft(name, rng.uniform(0.08, 0.14), rng)
            ob.data.materials.append(mats["grass"])
            n = (n + up).normalized()
        coll.objects.link(ob)
        _orient(ob, rig, at, n, rng)
        bpy.context.view_layer.update()
        if rigid:
            _skin_rigid(ob, body, rig, vi)
        else:
            _skin_transfer(ob, body, rig)
        return ob

    slots = [(kind, "%s.%02d" % (kind.capitalize(), i)) for kind, (n, _, _) in GROWTH.items() for i in range(n)]
    pieces = {name: grow(kind, name, name.endswith(".00")) for kind, name in slots}
    regrown, rounds = 0, 0
    while True:
        live = [o for o in pieces.values() if o is not None]
        rig.data.pose_position = old_pose
        report = audit(live, action)
        rig.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        bad = {k for k, (st, fails) in report.items() if st > STRETCH_MAX or fails}
        rounds += 1
        if not bad or rounds > 8:
            break
        for kind, name in slots:
            if pieces[name] is not None and pieces[name].name in bad:
                remove(pieces[name])
                pieces[name] = grow(kind, name, name.endswith(".00"))
                regrown += 1
    for name, o in list(pieces.items()):   # anything still failing after the last round goes
        if o is not None and o.name in bad:
            remove(o)
            pieces[name] = None

    rig.data.pose_position = old_pose
    bpy.context.view_layer.update()
    placed = [o for o in pieces.values() if o]
    return {"placed": len(placed), "by_kind": {k: sum(1 for o in placed if o.name.startswith(k.capitalize() + "."))
                                               for k in GROWTH},
            "dropped": [k for k, o in pieces.items() if o is None], "regrown": regrown, "rounds": rounds,
            "worst_stretch": max((v[0] for k, v in report.items() if k not in bad), default=1.0),
            "faces": sum(len(o.data.polygons) for o in placed)}


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
