"""Mossmaw: turn the rigged Kog'Maw into a mossy one, then render previews.

Run inside Blender with the rigged scene open (body mesh "KogMaw", armature "KogMaw_Rig"):

    import importlib.util
    spec = importlib.util.spec_from_file_location("mossmaw", r"...\\scripts\\mossmaw.py")
    mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
    mm.apply_texture(SRC_PNG, DST_PNG)     # the "moss" recipe in recolor_texture.py
    mm.build_moss()                        # everything in GROWTH, audited over ACTIONS
    mm.stage()                             # lights, ground, camera
    mm.render_hero(OUT_JPG); mm.render_run_strip(OUT_JPG); mm.render_turntable(OUT_JPG)

Everything is seeded, so a rerun gives the same Mossmaw. The actions in ACTIONS must be in
the file (Aventurine imports them from the .anm files); missing ones are skipped.

GROWTH lists what grows and where: moss mats and cushions on his back, tail, skull top and
shoulders (each picks a moss colour, and the hide under it darkens); ferns, leaf sprouts,
mushroom clusters, two hero mushrooms and one glowing cluster (the mushroom kinds take spots
the hero camera sees first); leaves out of the antenna tips; moss strands hanging from his
jaw and antenna stalks; grass on his legs. Faces buried in his body are trimmed away.

Skinning: moss mats and cushions take their bone weights from the body with a Data Transfer
modifier (nearest face, interpolated), applied, then averaged over the piece so it moves as
one with the hide under it, plus an Armature modifier. Everything else copies the weights
of the one skin vertex it grows from, so its root stays on that spot of skin.

build_moss audits every piece on every frame of every action and regrows any that stretch,
come loose from the skin, dip into the ground, or brush into him on more than a few frames.
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


def _material(name, base, dark, noise_scale=40.0, rough=1.0, glow=None, variants=None, fuzz=0.0,
              bump=0.0):
    """A noise-mottled material. variants: colours one of which each object picks at random
    (Object Info > Random), replacing base. fuzz: sheen that lights the rim like fine moss
    hairs. bump: strength of a fine noise bump. glow: ((r, g, b), strength) emission."""
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
    color = ramp.outputs["Color"]
    if variants:
        info = nt.nodes.new("ShaderNodeObjectInfo")
        pick = nt.nodes.new("ShaderNodeValToRGB")
        pick.color_ramp.interpolation = 'CONSTANT'
        els = pick.color_ramp.elements
        while len(els) < len(variants):
            els.new(0.5)
        for i, c in enumerate(variants):
            els[i].position = i / len(variants)
            els[i].color = (*c, 1)
        nt.links.new(info.outputs["Random"], pick.inputs["Fac"])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        mix.inputs["Factor"].default_value = 1.0
        ramp.color_ramp.elements[0].color = (0.45, 0.45, 0.45, 1)   # noise now only shades
        ramp.color_ramp.elements[1].color = (1.1, 1.1, 1.1, 1)
        nt.links.new(pick.outputs["Color"], mix.inputs["A"])
        nt.links.new(ramp.outputs["Color"], mix.inputs["B"])
        color = mix.outputs["Result"]
    nt.links.new(color, bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    if fuzz:
        bsdf.inputs["Sheen Weight"].default_value = fuzz
        bsdf.inputs["Sheen Tint"].default_value = (0.45, 0.7, 0.25, 1)
        bsdf.inputs["Sheen Roughness"].default_value = 0.6
    if bump:
        fine = nt.nodes.new("ShaderNodeTexNoise")
        fine.inputs["Scale"].default_value = noise_scale * 4
        fine.inputs["Detail"].default_value = 8.0
        bn = nt.nodes.new("ShaderNodeBump")
        bn.inputs["Strength"].default_value = bump
        bn.inputs["Distance"].default_value = 0.02
        nt.links.new(fine.outputs["Fac"], bn.inputs["Height"])
        nt.links.new(bn.outputs["Normal"], bsdf.inputs["Normal"])
    if glow:
        bsdf.inputs["Emission Color"].default_value = (*glow[0], 1)
        bsdf.inputs["Emission Strength"].default_value = glow[1]
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _new_obj(name, bm):
    import bpy
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return bpy.data.objects.new(name, me)


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


STEM, CAP, GILLS, SPOTS = range(4)   # mushroom material slots


def _add_mushroom(bm, at, height, cap_r, lean, rng, spots=0):
    """One mushroom into bm, its foot at local point at: a curved five-sided stem, a domed
    cap with a curled rim, gills underneath, and optional raised spots on the cap.
    lean is the (x, y) the stem bends toward. Faces carry their material slot."""
    import mathutils
    sides, cap_seg = 5, 8

    def ring(c, r, n, z_off=0.0, twist=0.0):
        return [bm.verts.new(c + mathutils.Vector((math.cos(twist + 2 * math.pi * i / n) * r,
                                                   math.sin(twist + 2 * math.pi * i / n) * r, z_off)))
                for i in range(n)]

    def band(a, b, slot):
        n = len(a)
        for i in range(n):
            f = bm.faces.new((a[i], a[(i + 1) % n], b[(i + 1) % n], b[i]))
            f.material_index = slot
            f.smooth = True

    bend = mathutils.Vector((lean[0], lean[1], 0))
    stem_r = cap_r * 0.26
    rings, centres = [], []
    for k in range(4):           # foot, two bends, neck
        t = k / 3
        c = mathutils.Vector((at.x, at.y, at.z + ROOT + height * t)) + bend * height * t * t
        r = stem_r * (1.35 if k == 0 else 1.0 - 0.2 * t)   # a little bulb at the foot
        rings.append(ring(c, r, sides))
        centres.append(c)
    for a, b in zip(rings, rings[1:]):
        band(a, b, STEM)
    top = centres[-1]
    # cap: rim (curled down) -> shoulder -> crown -> top
    profile = [(1.0, -0.18), (1.02, 0.05), (0.82, 0.38), (0.45, 0.62)]
    cap_rings = [ring(top, cap_r * rr, cap_seg, cap_r * zz, twist=0.3) for rr, zz in profile]
    for a, b in zip(cap_rings, cap_rings[1:]):
        band(a, b, CAP)
    tip = bm.verts.new(top + mathutils.Vector((0, 0, cap_r * 0.7)))
    for i in range(cap_seg):
        f = bm.faces.new((cap_rings[-1][i], cap_rings[-1][(i + 1) % cap_seg], tip))
        f.material_index = CAP
        f.smooth = True
    # gills: from the rim in to the neck, a shallow cone under the cap
    inner = ring(top, stem_r * 0.9, cap_seg, cap_r * 0.02, twist=0.3)
    for i in range(cap_seg):
        f = bm.faces.new((cap_rings[0][(i + 1) % cap_seg], cap_rings[0][i], inner[i], inner[(i + 1) % cap_seg]))
        f.material_index = GILLS
    for _ in range(spots):       # small raised dots on the crown
        a, d = rng.uniform(0, 2 * math.pi), rng.uniform(0.25, 0.75)
        p = top + mathutils.Vector((math.cos(a) * cap_r * d, math.sin(a) * cap_r * d, cap_r * (0.65 - 0.4 * d * d)))
        s = cap_r * rng.uniform(0.1, 0.16)
        dot = [bm.verts.new(p + mathutils.Vector(o) * s) for o in ((1, 0, 0), (-0.5, 0.87, 0), (-0.5, -0.87, 0))]
        peak = bm.verts.new(p + mathutils.Vector((0, 0, s * 0.8)))
        for i in range(3):
            f = bm.faces.new((dot[i], dot[(i + 1) % 3], peak))
            f.material_index = SPOTS


def _mushrooms(name, rng, count, size=1.0, spots=0):
    """A cluster of mixed-size mushrooms leaning out from a shared root; the first is the
    tallest. size scales the whole cluster (hero mushrooms use about 1.8)."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    for k in range(count):
        ang = k * 2.4 + rng.uniform(-0.3, 0.3)
        at = mathutils.Vector((0, 0, 0)) if k == 0 else mathutils.Vector(
            (math.cos(ang), math.sin(ang), 0)) * rng.uniform(0.06, 0.10) * size
        scale = size * (1.0 if k == 0 else rng.uniform(0.4, 0.75))
        lean = (0.0, 0.0) if k == 0 else (math.cos(ang) * 0.35, math.sin(ang) * 0.35)
        _add_mushroom(bm, at, rng.uniform(0.20, 0.26) * scale, rng.uniform(0.09, 0.12) * scale, lean, rng,
                      spots=spots if k < 2 else 0)
    return _new_obj(name, bm)


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
    return _new_obj(name, bm)


def _strand(name, length, rng, thick=0.03, bundle=(2, 3)):
    """Hanging moss: a bundle of wavy, tapering three-sided strands along local +Z
    (pointed at the ground), sharing one root. thick is the root radius."""
    import bmesh
    import mathutils
    bm = bmesh.new()
    for s in range(rng.randint(*bundle)):
        ln = length * (1.0 if s == 0 else rng.uniform(0.55, 0.85))
        spread = mathutils.Vector((math.cos(s * 2.3), math.sin(s * 2.3), 0)) * (0 if s == 0 else 0.03)
        phase, amp = rng.uniform(0, 6.28), rng.uniform(0.015, 0.03)
        segs, rings = 5, []
        for k in range(segs + 1):
            t = k / segs
            wave = mathutils.Vector((math.sin(phase + t * 7) * amp * t, math.cos(phase + t * 5) * amp * t, 0))
            c = mathutils.Vector((0, 0, ROOT + (ln - ROOT) * t)) + spread * t + wave
            r = thick * (1 - t) ** 0.7 + 0.005
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
    return _new_obj(name, bm)


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
    return _new_obj(name, bm)


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
    return _new_obj(name, bm)


# ------ skinning


def _trim_buried(ob, tree):
    """Delete the faces of ob that sit wholly inside the body (rest pose): nobody sees them.
    A vertex only counts as inside when it is also a little below the skin, so a misread
    on an open part of his mesh never eats a visible face."""
    import bmesh
    mw = ob.matrix_world
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    inside = {v: _inside(tree, mw @ v.co) and tree.find_nearest(mw @ v.co)[3] > 0.01 for v in bm.verts}
    dead = [f for f in bm.faces if all(inside[v] for v in f.verts)]
    if len(dead) < len(bm.faces):
        bmesh.ops.delete(bm, geom=dead, context='FACES_ONLY')
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bm.to_mesh(ob.data)
    bm.free()
    return len(dead)


def _orient(ob, rig, pos, normal, rng):
    import mathutils
    ob.parent = rig
    ob.matrix_parent_inverse = rig.matrix_world.inverted()
    rot = normal.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    spin = mathutils.Matrix.Rotation(rng.uniform(0, 2 * math.pi), 4, 'Z')
    ob.matrix_world = mathutils.Matrix.Translation(pos) @ rot @ spin


def _skin_rigid(ob, body, rig, vert_index):
    """Give every vertex the weights of the one body vertex the piece grows from (its four
    strongest bones). The whole piece then moves like that spot of skin: its root stays put
    and it swings with the bones under it. The audit's stretch check catches any shear."""
    src = sorted(body.data.vertices[vert_index].groups, key=lambda g: -g.weight)[:4]
    total = sum(g.weight for g in src) or 1.0
    idx = [v.index for v in ob.data.vertices]
    for g in src:
        ob.vertex_groups.new(name=body.vertex_groups[g.group].name).add(idx, g.weight / total, 'REPLACE')
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
TOUCH = 0.025               # a piece's collar must stay this close to his surface
FLOOR = -0.01               # no vertex may dip below the ground
CLIP_DEPTH = 0.02           # a strand, stalk or blade tip sunk deeper than this into him is clipping
CLIP_FRAMES = 3             # a piece may brush into him on this many frames, over all actions


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


ACTIONS = ("kogmaw_run", "kogmaw_idle1", "kogmaw_attack1", "kogmaw_spell4")   # every one is audited


def audit(pieces, actions=ACTIONS):
    """Play each action and return {name: (worst stretch, hard fails, clip frames)}.
    A hard fail is a frame where the skin point the piece grew from (its "collar") has
    drifted more than TOUCH off his surface, or the piece dips into the ground. A clip frame is one where (for pieces with a "clip_z") a
    vertex above clip_z has sunk into his body. Stretch compares every edge to its rest
    length. Actions missing from the file are skipped."""
    import bpy
    import mathutils
    from mathutils.bvhtree import BVHTree
    s = bpy.context.scene
    body = bpy.data.objects[BODY]
    rig = bpy.data.objects[RIG]
    keep_frame, keep_act = s.frame_current, rig.animation_data.action if rig.animation_data else None
    edges = {o.name: [e.vertices[:] for e in o.data.edges] for o in pieces}
    rest = {o.name: [(o.data.vertices[a].co - o.data.vertices[b].co).length for a, b in edges[o.name]]
            for o in pieces}
    tips = {o.name: [v.index for v in o.data.vertices if "clip_z" in o and v.co.z > o["clip_z"]] for o in pieces}
    # every vertex of a piece shares one set of bone weights, so one blended bone matrix
    # moves the skin point the piece grew from (its "collar") exactly as the piece moves
    weights = {o.name: [(o.vertex_groups[g.group].name, g.weight) for g in o.data.vertices[0].groups]
               for o in pieces}
    rmw, rmw_inv = rig.matrix_world, rig.matrix_world.inverted()
    worst = {o.name: [1.0, 0, 0] for o in pieces}
    for action in actions:
        if action not in bpy.data.actions:
            continue
        act = _set_action(action)
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
                mw = e.matrix_world
                w = worst[o.name]
                for (a, b), r0 in zip(edges[o.name], rest[o.name]):
                    ln = (me.vertices[a].co - me.vertices[b].co).length
                    w[0] = max(w[0], ln / max(r0, 1e-6), r0 / max(ln, 1e-6))
                collar = None
                if weights[o.name]:
                    skin = mathutils.Matrix.Diagonal((0, 0, 0, 0))
                    for b, wt in weights[o.name]:
                        skin = skin + (rmw @ rig.pose.bones[b].matrix @ rig.data.bones[b].matrix_local.inverted()
                                       @ rmw_inv) * wt
                    collar = skin @ mathutils.Vector(o["collar"])
                attached = collar is not None and tree.find_nearest(collar)[3] < TOUCH
                low = min((mw @ v.co).z for v in me.vertices)
                depth = o.get("clip_depth", CLIP_DEPTH)
                clipped = any(_inside(tree, mw @ me.vertices[i].co) and
                              tree.find_nearest(mw @ me.vertices[i].co)[3] > depth for i in tips[o.name])
                if not attached or low < FLOOR:
                    w[1] += 1
                elif clipped:
                    w[2] += 1
                e.to_mesh_clear()
    if keep_act:
        _set_action(keep_act.name)
    s.frame_set(keep_frame)
    return {k: (round(a, 3), b, c) for k, (a, b, c) in worst.items()}


def failing(report):
    """Names the audit rejects: too much stretch, ever loose or in the ground, or brushing
    into him on more than CLIP_FRAMES frames."""
    return {k for k, (st, hard, clip) in report.items() if st > STRETCH_MAX or hard or clip > CLIP_FRAMES}


# ------ growth

# kind: (how many, spot finder, gap between spots of this kind in metres)
GROWTH = {
    "mat":       (30, "top", 0.14),     # big layered cushions that change his silhouette
    "cushion":   (12, "top", 0.10),     # small ones filling between and over the mats
    "fern":      (6, "top", 0.35),
    "glow":      (1, "top", 0.0),       # one glowing cluster, first pick of the in-view spots
    "hero":      (2, "top", 0.0),       # two big mushrooms, also where the hero camera sees them
    "mushrooms": (5, "top", 0.3),       # clusters of 3-5
    "sprout":    (8, "top", 0.25),      # leaves between his back spikes
    "tip":       (4, "tips", 0.0),      # leaves out of each antenna tip
    "beard":     (16, "jaw", 0.07),     # moss hanging from his jaw and chin; a few long ones
    "drape":     (6, "antenna", 0.15),  # moss hanging from his antenna stalks
    "tuft":      (14, "legs", 0.1),     # grass along his legs and on his feet
}
IN_VIEW = ("glow", "hero", "mushrooms")   # mushroom kinds share one pool: in-view spots first
ANTENNA_ROOTS = ("R_Hair", "L_Hair", "R_Hair_c", "L_Hair_c")
ANTENNA_CLEAR = 0.4         # metres kept clear around the antenna roots for in-view pieces
VIEW_GAP = 0.3              # metres between hero mushrooms and the glow cluster
CAP_HEIGHT = 0.18           # where a cap sits above its spot, for the visibility test
HERO_ACTION, HERO_FRAME = "kogmaw_run", 6   # the pose of the hero shot
MAT_SIZE, MAT_FLAT, MAT_SINK = (0.12, 0.25), 0.7, 0.3
CUSHION_SIZE, CUSHION_FLAT, CUSHION_SINK = (0.06, 0.11), 0.65, 0.35
UPRIGHT = 2.0               # how hard stalks lean from the surface normal toward up
LONG_BEARDS = 4             # this many jaw strands hang long
BEARD_CLEARANCE = 0.45      # a jaw strand ends at least this high off the ground (rest pose)
STRAND_CLIP = 0.04          # strands may brush this deep into him before it counts as clipping
MOSS_COLORS = ((0.11, 0.20, 0.035),   # moss green
               (0.17, 0.25, 0.04),    # yellow-green new growth
               (0.10, 0.13, 0.04),    # olive
               (0.045, 0.085, 0.02),  # dark wet moss
               (0.13, 0.22, 0.05))
SHADE = 0.55                # how much the hide darkens right under a cushion
GLOW = ((0.05, 1.0, 0.45), 6.0)   # green-cyan; kept deep, as bright emission washes out to white


def _in_view(body, cands, cam_loc):
    """Spots the hero camera sees in the hero pose, best facing first: the spot's skin
    faces the camera and nothing of his body (antennae included) blocks the line from a
    point CAP_HEIGHT above the spot, where a mushroom cap would sit."""
    import bpy
    from mathutils.bvhtree import BVHTree
    rig = body.parent
    s = bpy.context.scene
    keep_pose, keep_frame = rig.data.pose_position, s.frame_current
    roots = [rig.matrix_world @ b.head_local for b in rig.data.bones if b.name in ANTENNA_ROOTS]
    rig.data.pose_position = 'POSE'
    _set_action(HERO_ACTION)
    s.frame_set(HERO_FRAME)
    dg = bpy.context.evaluated_depsgraph_get()
    be = body.evaluated_get(dg)
    em = be.to_mesh()
    mw = be.matrix_world
    pts = [mw @ v.co for v in em.vertices]
    nrm = [(mw.to_3x3() @ v.normal).normalized() for v in em.vertices]
    tree = BVHTree.FromPolygons(pts, [p.vertices[:] for p in em.polygons])
    rest_n = [(body.matrix_world.to_3x3() @ v.normal).normalized() for v in body.data.vertices]
    be.to_mesh_clear()
    scored = []
    for p, n, vi in cands:
        if any((p - r).length < ANTENNA_CLEAR for r in roots):
            continue   # his antennae sweep through anything big growing at their roots
        pn = nrm[vi] if rest_n[vi].dot(n) >= 0 else -nrm[vi]   # follow the spot's facing
        cap = pts[vi] + pn * CAP_HEIGHT
        to_cam = (cam_loc - cap).normalized()
        face = pn.dot(to_cam)
        if face < 0.35:
            continue
        if tree.ray_cast(cap + to_cam * 0.02, to_cam)[0] is not None:
            continue
        scored.append((face, (p, n, vi)))
    rig.data.pose_position = keep_pose
    s.frame_set(keep_frame)
    bpy.context.view_layer.update()
    scored.sort(key=lambda s: -s[0])
    return [c for _, c in scored]


def shade_hide(body, clumps, strength=SHADE):
    """Darken the hide under each cushion, so the moss sits in it, not on it: a colour
    attribute on the body ("MossShade") multiplied into its texture. Rest-pose distances."""
    me = body.data
    attr = me.color_attributes.get("MossShade") or me.color_attributes.new("MossShade", 'FLOAT_COLOR', 'POINT')
    mw = body.matrix_world
    centres = [(o.matrix_world.translation.copy(), o["radius"]) for o in clumps]
    for v in me.vertices:
        p = mw @ v.co
        dark = 0.0
        for c, r in centres:
            d = (p - c).length
            if d < r * 1.7:
                t = max(0.0, min(1.0, (r * 1.7 - d) / (r * 0.9)))
                dark = max(dark, t * t * (3 - 2 * t))
        k = 1.0 - strength * dark
        attr.data[v.index].color = (k, k, k, 1.0)
    for mat in me.materials:
        nt = mat.node_tree
        if "MossShade" in nt.nodes:
            continue
        tex = next(n for n in nt.nodes if n.type == 'TEX_IMAGE')
        link = next(l for l in nt.links if l.from_node == tex and l.from_socket.name == "Color")
        dst = link.to_socket
        col = nt.nodes.new("ShaderNodeVertexColor")
        col.name = "MossShade"
        col.layer_name = "MossShade"
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        mix.inputs["Factor"].default_value = 1.0
        nt.links.remove(link)
        nt.links.new(tex.outputs["Color"], mix.inputs["A"])
        nt.links.new(col.outputs["Color"], mix.inputs["B"])
        nt.links.new(mix.outputs["Result"], dst)


def _glow_light(rig, ob, bone):
    """A small light riding the glow cluster's bone, so its glow spills onto the moss."""
    import bpy
    import mathutils
    name = "GlowLight"
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    lt = bpy.data.lights.get(name) or bpy.data.lights.new(name, 'POINT')
    lt.energy, lt.color, lt.shadow_soft_size = 6.0, GLOW[0], 0.05
    lo = bpy.data.objects.new(name, lt)
    ob.users_collection[0].objects.link(lo)
    vs = ob.data.vertices
    top = ob.matrix_world @ vs[max(range(len(vs)), key=lambda i: vs[i].co.z)].co
    lo.parent, lo.parent_type, lo.parent_bone = rig, 'BONE', bone
    bpy.context.view_layer.update()
    lo.matrix_world = mathutils.Matrix.Translation(top + mathutils.Vector((0, 0, 0.08)))
    return lo


def build_moss(seed=SEED, actions=ACTIONS):
    """Grow everything in GROWTH on the rest pose, then audit every piece over the actions:
    any that stretches past STRETCH_MAX, comes loose, clips into him or dips into the
    ground on any frame is regrown on the next free spot. Reruns replace old growth."""
    import bpy
    import mathutils
    from mathutils.bvhtree import BVHTree
    body, rig = bpy.data.objects[BODY], bpy.data.objects[RIG]
    rng = random.Random(seed)
    coll = bpy.data.collections.get("Moss") or bpy.data.collections.new("Moss")
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)

    def remove(o):
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)

    prefixes = tuple(k.capitalize() + "." for k in GROWTH) + ("MossClump.", "Mushroom.", "Fern.")
    stale = [o for o in bpy.data.objects if o.name.startswith(prefixes) and not o.users_collection]
    for o in list(coll.objects) + stale:
        remove(o)

    mats = {
        "moss": _material("Moss", (0.1, 0.18, 0.03), (0.03, 0.06, 0.01), 45.0, variants=MOSS_COLORS,
                          fuzz=0.25, bump=0.25),
        "hanging": _material("HangingMoss", (0.09, 0.14, 0.04), (0.03, 0.05, 0.015), 30.0, fuzz=0.2),
        "leaf": _material("Leaf", (0.30, 0.55, 0.12), (0.12, 0.28, 0.05), 20.0, rough=0.7),
        "fern": _material("Fern", (0.30, 0.48, 0.11), (0.10, 0.22, 0.04), 25.0, rough=0.8),
        "grass": _material("Grass", (0.16, 0.26, 0.05), (0.06, 0.11, 0.02), 30.0, fuzz=0.15),
        "stem": _material("MushroomStem", (0.80, 0.76, 0.62), (0.52, 0.47, 0.35), 60.0),
        "cap": _material("MushroomCap", (0.80, 0.36, 0.09), (0.40, 0.13, 0.04), 18.0, rough=0.55,
                         variants=((0.80, 0.36, 0.09), (0.65, 0.25, 0.07), (0.85, 0.50, 0.16))),
        "gills": _material("MushroomGills", (0.62, 0.52, 0.38), (0.36, 0.28, 0.18), 120.0),
        "dots": _material("MushroomSpots", (0.92, 0.88, 0.74), (0.75, 0.70, 0.56), 40.0),
        "glow_cap": _material("GlowCap", (0.012, 0.05, 0.04), (0.004, 0.02, 0.016), 18.0, rough=0.5,
                              glow=(GLOW[0], 0.15)),   # dark cap: the spots and gills carry the glow
        "glow_spot": _material("GlowSpot", (0.2, 0.9, 0.5), (0.1, 0.7, 0.4), 20.0,
                               glow=((0.3, 1.0, 0.6), 6.0)),
        "glow_gills": _material("GlowGills", (0.05, 0.30, 0.20), (0.03, 0.18, 0.12), 120.0,
                                glow=(GLOW[0], 3.0)),
    }

    old_pose = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    me = body.data
    rest_tree = BVHTree.FromPolygons([body.matrix_world @ v.co for v in me.vertices],
                                     [p.vertices[:] for p in me.polygons])
    finders = {
        "top": lambda: _ray_spots(body, TOP_BONES),
        "legs": lambda: _ray_spots(body, LEG_BONES, min_thick=0.08),
        "jaw": lambda: _ray_spots(body, JAW_BONES, from_below=True, min_thick=0.08),
        "antenna": lambda: _antenna_spots(body, rig, ANTENNA_BONES),
        "tips": lambda: _antenna_spots(body, rig, ANTENNA_TIPS, tips=True),
    }
    cands = {k: f() for k, f in finders.items()}
    cam = mathutils.Vector(HERO_CAM[0])
    def view_pool(where):
        """In-view spots first (best facing first), then any other spot, all VIEW_GAP apart.
        Heroes and the glow cluster draw from this one pool, so they never share a spot."""
        rest = list(cands[where])
        rng.shuffle(rest)
        taken = []
        for c in _in_view(body, cands[where], cam) + rest:
            if all((c[0] - t).length >= VIEW_GAP for t in taken):
                taken.append(c[0])
                yield c

    spots = {}
    shared = view_pool("top")
    for kind, (n, where, gap) in GROWTH.items():
        spots[kind] = shared if kind in IN_VIEW else _pick(cands[where], gap, rng)
    up = mathutils.Vector((0, 0, UPRIGHT))
    beards = [0]

    def grow(kind, name):
        try:
            pos, n, vi = next(spots[kind])
        except StopIteration:
            return None
        rigid, at, clip = True, pos, 0.03
        if kind in ("mat", "cushion"):
            size, flat, sink = ((MAT_SIZE, MAT_FLAT, MAT_SINK) if kind == "mat"
                                else (CUSHION_SIZE, CUSHION_FLAT, CUSHION_SINK))
            r = rng.uniform(*size)
            ob = _clump(name, r, flat, rng)
            ob.data.materials.append(mats["moss"])
            ob["radius"] = r
            at, rigid, clip = pos - n * (r * flat * sink), False, None
        elif kind == "fern":
            ob = _fern(name, rng.uniform(0.28, 0.42), rng)
            ob.data.materials.append(mats["fern"])
            n = (n + up).normalized()
            clip = None   # fronds curl down onto the moss by design
        elif kind in ("mushrooms", "hero", "glow"):
            if kind == "hero":
                ob = _mushrooms(name, rng, rng.randint(2, 3), size=1.55)
            elif kind == "glow":
                ob = _mushrooms(name, rng, rng.randint(4, 5), size=1.2, spots=5)
            else:
                ob = _mushrooms(name, rng, rng.randint(3, 5))
            slots_ = (("stem", "glow_cap", "glow_gills", "glow_spot") if kind == "glow"
                      else ("stem", "cap", "gills", "dots"))
            for m in slots_:
                ob.data.materials.append(mats[m])
            n = (n + up).normalized()
            # side stems start in the curve of the hide by design; only caps may not sink in
            clip = {"hero": 0.17, "glow": 0.14}.get(kind, 0.11)
        elif kind in ("sprout", "tip"):
            ob = _leaves(name, rng.uniform(0.10, 0.16) if kind == "sprout" else rng.uniform(0.12, 0.18),
                         rng, rng.randint(3, 4))
            ob.data.materials.append(mats["leaf"])
            if kind == "sprout":
                n = (n + up).normalized()
        elif kind in ("beard", "drape"):
            if kind == "beard":
                beards[0] += 1
                long = beards[0] <= LONG_BEARDS
                ln = rng.uniform(0.38, 0.5) if long else rng.uniform(0.12, 0.28)
                ln = min(ln, pos.z - BEARD_CLEARANCE)   # his head dips low when he runs and spits
                if ln < 0.1:
                    return grow(kind, name)
                ob = _strand(name, ln, rng,
                             thick=rng.uniform(0.03, 0.042) if long else rng.uniform(0.016, 0.03),
                             bundle=(1, 2) if long else (1, 3))
            else:
                ob = _strand(name, rng.uniform(0.18, 0.32), rng, thick=rng.uniform(0.014, 0.024), bundle=(1, 2))
            ob.data.materials.append(mats["hanging"])
            n = mathutils.Vector((0, 0, -1))
            clip = 0.06
            ob["clip_depth"] = STRAND_CLIP   # a soft strand brushing his chest is fine
        else:  # tuft
            ob = _tuft(name, rng.uniform(0.08, 0.14), rng)
            ob.data.materials.append(mats["grass"])
            n = (n + up).normalized()
        coll.objects.link(ob)
        _orient(ob, rig, at, n, rng)
        bpy.context.view_layer.update()
        # the skin point it grows from; a tip sprout's spot is inside the tip, so use its vertex
        ob["collar"] = list(body.matrix_world @ me.vertices[vi].co if kind == "tip" else pos)
        if clip is not None:
            ob["clip_z"] = clip
        if rigid:
            _skin_rigid(ob, body, rig, vi)
        else:
            _trim_buried(ob, rest_tree)
            _skin_transfer(ob, body, rig)
        return ob

    slots = [(kind, "%s.%02d" % (kind.capitalize(), i)) for kind, (n, _, _) in GROWTH.items() for i in range(n)]
    pieces = {name: grow(kind, name) for kind, name in slots}
    regrown, rounds = 0, 0
    while True:
        live = [o for o in pieces.values() if o is not None]
        rig.data.pose_position = old_pose
        report = audit(live, actions)
        rig.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        bad = failing(report)
        rounds += 1
        if not bad or rounds > 12:
            break
        for kind, name in slots:
            if pieces[name] is not None and pieces[name].name in bad:
                remove(pieces[name])
                pieces[name] = grow(kind, name)
                regrown += 1
    for name, o in list(pieces.items()):   # anything still failing after the last round goes
        if o is not None and o.name in bad:
            remove(o)
            pieces[name] = None

    placed = [o for o in pieces.values() if o]
    shade_hide(body, [o for o in placed if "radius" in o])
    glow = pieces.get("Glow.00")
    if glow:
        _glow_light(rig, glow, glow.vertex_groups[0].name)
    rig.data.pose_position = old_pose
    bpy.context.view_layer.update()
    tris = sum(len(p.vertices) - 2 for o in placed for p in o.data.polygons)
    return {"placed": len(placed), "by_kind": {k: sum(1 for o in placed if o.name.startswith(k.capitalize() + "."))
                                               for k in GROWTH},
            "dropped": [k for k, o in pieces.items() if o is None], "regrown": regrown, "rounds": rounds,
            "worst_stretch": max((v[0] for k, v in report.items() if k not in bad), default=1.0),
            "triangles": tris}


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


def render_hero(out_jpg, frame=HERO_FRAME, action=HERO_ACTION, size=1200):
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


TURNTABLE = (("front", -90), ("side", 0), ("back", 90), ("3/4", -51.5))   # camera angle around Z, degrees


def render_turntable(out_jpg, size=600, frame=HERO_FRAME, action=HERO_ACTION):
    """The hero pose from front, side, back and 3/4, at the hero camera's distance and height."""
    import bpy
    s = bpy.context.scene
    _set_action(action)
    s.frame_set(frame)
    s.render.resolution_x = s.render.resolution_y = size
    (cx, cy, cz), target = HERO_CAM
    dist = math.hypot(cx - target[0], cy - target[1])
    tmp = os.path.splitext(out_jpg)[0] + "_tmp.png"
    tiles = []
    for _, deg in TURNTABLE:
        a = math.radians(deg)
        _aim(s.camera, (target[0] + dist * math.cos(a), target[1] + dist * math.sin(a), cz), target)
        tiles.append(_render_rgba(tmp))
    return _save_jpg(out_jpg, np.concatenate(tiles, axis=1))
