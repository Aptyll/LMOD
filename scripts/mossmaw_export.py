"""Export Mossmaw for the game client: one .skn, one .tex, packed as a .fantome mod.

Run inside Blender with work/mossmaw.blend open (after mossmaw.build_moss):

    import importlib.util
    spec = importlib.util.spec_from_file_location("mossmaw_export", r"...\\scripts\\mossmaw_export.py")
    mx = importlib.util.module_from_spec(spec); spec.loader.exec_module(mx)
    mx.export_all(OUT_DIR)          # writes kogmaw.skn, kogmaw_base_tx_cm.tex/.png, the .wad and .fantome

How the moss gets into one League mesh:
  - League's base skin draws one submesh with one texture, so the moss joins the body. Each
    moss material is baked into a small colour swatch painted into an unused (background)
    area of his texture, and every moss face gets UVs inside its swatch. Glow is baked in
    as bright colour, since the game shader has no emission.
  - The hide darkening under the moss (the MossShade colour attribute) is baked into the
    texture by rasterising his triangles in UV space.
  - Everything is joined in the rest pose, keeps the original skeleton (same bones, same
    order: no .skl is exported), and every vertex keeps at most 4 bone influences.

The mod replaces only Kog'Maw's base skin mesh and texture. It is cosmetic and client-side.
Riot's files stay on this machine: never commit what this writes.
"""
import importlib
import json
import math
import os
import random
import zipfile

import numpy as np

BODY, RIG = "KogMaw", "KogMaw_Rig"
GAME = {
    "skn": "assets/characters/kogmaw/skins/base/kogmaw.skn",
    "tex": "assets/characters/kogmaw/skins/base/kogmaw_base_tx_cm.tex",
}
WAD_NAME = "KogMaw.wad.client"
BASE_TEX = r"C:\NoahCode\LMOD\base\kogmaw\kogmaw_base_tx_cm.png"   # finds the unused background
SWATCH = 32          # px per swatch block
INSET = 6            # px kept between a swatch's UVs and its edge, so mipmaps don't bleed
MAX_INFLUENCES = 4
GLOW_BAKE = 0.35     # how much of a material's emission is baked into its colour
TEX_BGRA8 = 20       # League .tex format id for uncompressed BGRA8


def _rs():
    return importlib.import_module("Aventurine.vendor.ritoshark")


# ------ colour


def _to_srgb(c):
    c = np.clip(np.asarray(c, dtype=np.float32), 0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055).clip(0, 1)


def _material_colors(mat):
    """(dark, light) linear colour pairs a material shows: one pair per random variant,
    or the noise ramp's two stops, with part of any emission added on top."""
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    ramps = [n for n in nt.nodes if n.type == 'VALTORGB']
    pick = next((r for r in ramps if r.color_ramp.interpolation == 'CONSTANT'), None)
    noise = next(r for r in ramps if r is not pick)
    if pick:
        lo, hi = noise.color_ramp.elements[0].color[0], noise.color_ramp.elements[1].color[0]
        pairs = [(np.array(e.color[:3]) * lo, np.array(e.color[:3]) * hi) for e in pick.color_ramp.elements]
    else:
        pairs = [(np.array(noise.color_ramp.elements[0].color[:3]), np.array(noise.color_ramp.elements[1].color[:3]))]
    glow = np.array(bsdf.inputs["Emission Color"].default_value[:3]) * bsdf.inputs["Emission Strength"].default_value
    return [(d + glow * GLOW_BAKE, l + glow * GLOW_BAKE) for d, l in pairs]


def _swatch(dark, light, seed):
    """A SWATCH x SWATCH sRGB patch of noise between two linear colours."""
    rng = np.random.default_rng(seed)
    coarse = rng.random((5, 5)).astype(np.float32)
    ys = np.linspace(0, 4, SWATCH)
    xs = np.linspace(0, 4, SWATCH)
    y0, x0 = np.minimum(ys.astype(int), 3), np.minimum(xs.astype(int), 3)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    n = (coarse[y0][:, x0] * (1 - fx) + coarse[y0][:, x0 + 1] * fx) * (1 - fy) + \
        (coarse[y0 + 1][:, x0] * (1 - fx) + coarse[y0 + 1][:, x0 + 1] * fx) * fy
    n = np.clip(0.7 * n + 0.3 * rng.random((SWATCH, SWATCH)), 0, 1)[..., None]
    return _to_srgb(dark + (light - dark) * n)


# ------ texture atlas


def _free_blocks(base_rgba, count):
    """Top-left corners (x, y in Blender pixel rows, bottom-up) of count SWATCH-sized
    blocks that are pure background in the original texture, with a margin around them."""
    bg = np.median(np.concatenate([base_rgba[:8, :8, :3].reshape(-1, 3), base_rgba[-8:, -8:, :3].reshape(-1, 3)]), 0)
    free = (np.abs(base_rgba[..., :3] - bg).max(-1) < 0.03)
    h, w = free.shape
    m = 4
    blocks, used = [], np.zeros_like(free)
    for y in range(m, h - SWATCH - m, SWATCH // 2):
        for x in range(m, w - SWATCH - m, SWATCH // 2):
            region = (slice(y - m, y + SWATCH + m), slice(x - m, x + SWATCH + m))
            if free[region].all() and not used[region].any():
                blocks.append((x, y))
                used[region] = True
                if len(blocks) == count:
                    return blocks
    raise RuntimeError("only %d free swatch blocks in the texture, need %d" % (len(blocks), count))


def _bake_shade(tex, body):
    """Multiply the MossShade colour attribute into the texture through his UVs. Where two
    triangles share texture space (mirrored islands), the darker value wins."""
    me = body.data
    attr = me.color_attributes.get("MossShade")
    if attr is None:
        return tex
    h, w = tex.shape[:2]
    shade = np.ones((h, w), np.float32)
    uv = me.uv_layers.active.data
    me.calc_loop_triangles()
    vals = np.array([attr.data[i].color[0] for i in range(len(me.vertices))], np.float32)
    for tri in me.loop_triangles:
        p = np.array([uv[l].uv for l in tri.loops], np.float32) * [w, h]
        k = vals[[me.loops[l].vertex_index for l in tri.loops]]
        if k.min() > 0.999:
            continue
        x0, y0 = np.floor(p.min(0)).astype(int)
        x1, y1 = np.ceil(p.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w - 1), min(y1, h - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, ay), (bx, by), (cx, cy) = p
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            continue
        l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
        l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
        l3 = 1 - l1 - l2
        inside = (l1 >= -0.01) & (l2 >= -0.01) & (l3 >= -0.01)
        val = l1 * k[0] + l2 * k[1] + l3 * k[2]
        sub = shade[y0:y1 + 1, x0:x1 + 1]
        sub[inside] = np.minimum(sub[inside], val[inside])
    out = tex.copy()
    out[..., :3] *= shade[..., None]
    return out


# ------ mesh


def _piece_swatches(pieces):
    """Every (material, variant) a piece needs -> a swatch id. Pieces with a moss-colour
    material pick a variant from their own name, so the mix stays seeded."""
    table, per_piece = {}, {}
    for ob in pieces:
        need = []
        for slot, mat in enumerate(ob.data.materials):
            pairs = _material_colors(mat)
            variant = random.Random(ob.name).randrange(len(pairs)) if len(pairs) > 1 else 0
            key = (mat.name, variant)
            if key not in table:
                table[key] = (len(table), pairs[variant])
            need.append(table[key][0])
        per_piece[ob.name] = need
    return table, per_piece


def _build_export_mesh(body, pieces, uv_of_swatch):
    """One mesh: a copy of the body plus every piece in rest-pose world space, all on the
    body's material, moss faces UV'd into their swatches."""
    import bmesh
    import bpy
    bm = bmesh.new()
    bm.from_mesh(body.data)
    uv = bm.loops.layers.uv.active
    deform = bm.verts.layers.deform.verify()
    groups = [g.name for g in body.vertex_groups]
    for ob in pieces:
        pm = bmesh.new()
        pm.from_mesh(ob.data)
        pm.transform(ob.matrix_world)
        puv = pm.loops.layers.uv.verify()
        pdef = pm.verts.layers.deform.verify()
        swatches = uv_of_swatch[ob.name]
        # planar UVs within the swatch, from the piece's own bounding box
        xs = [v.co.x for v in pm.verts]
        ys = [v.co.y for v in pm.verts]
        zs = [v.co.z for v in pm.verts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
        for f in pm.faces:
            (u0, v0), (u1, v1) = swatches[min(f.material_index, len(swatches) - 1)]
            for l in f.loops:
                a = (l.vert.co.x - min(xs) + l.vert.co.z - min(zs)) / (2 * span)
                b = (l.vert.co.y - min(ys) + l.vert.co.z - min(zs)) / (2 * span)
                l[puv].uv = (u0 + (u1 - u0) * a, v0 + (v1 - v0) * b)
        names = [g.name for g in ob.vertex_groups]
        vmap = {}
        for v in pm.verts:
            nv = bm.verts.new(v.co)
            for gi, wt in v[pdef].items():
                name = names[gi]
                if name not in groups:
                    groups.append(name)
                nv[deform][groups.index(name)] = wt
            vmap[v] = nv
        bm.verts.ensure_lookup_table()
        for f in pm.faces:
            try:
                nf = bm.faces.new([vmap[v] for v in f.verts])
            except ValueError:
                continue   # a duplicate face; skip it
            nf.material_index = 0
            nf.smooth = f.smooth
            for nl, l in zip(nf.loops, f.loops):
                nl[uv].uv = l[puv].uv
        pm.free()
    # at most MAX_INFLUENCES bones per vertex, renormalised
    for v in bm.verts:
        items = sorted(v[deform].items(), key=lambda kv: -kv[1])
        keep = items[:MAX_INFLUENCES]
        total = sum(w for _, w in keep) or 1.0
        v[deform].clear()
        for gi, wt in keep:
            v[deform][gi] = wt / total
    me = bpy.data.meshes.new("Mossmaw_Export")
    bm.to_mesh(me)
    bm.free()
    for m in body.data.materials:
        me.materials.append(m)
    ob = bpy.data.objects.new("Mossmaw_Export", me)
    for g in groups:
        ob.vertex_groups.new(name=g)
    for k in ("lol_skn_flags", "lol_skn_prefix", "lol_skn_trailing"):
        if k in body:
            ob[k] = body[k]
    ob.parent = body.parent
    bpy.context.scene.collection.objects.link(ob)
    return ob


# ------ files


def write_tex(path, rgba_bottom_up):
    """Write an uncompressed BGRA8 .tex with a full mip chain (stored smallest first)."""
    img = np.flipud(np.clip(rgba_bottom_up * 255 + 0.5, 0, 255).astype(np.uint8))   # top-down rows
    h, w = img.shape[:2]
    mips = [img]
    while mips[-1].shape[0] > 1 or mips[-1].shape[1] > 1:
        m = mips[-1].astype(np.float32)
        hh, ww = max(m.shape[0] // 2, 1), max(m.shape[1] // 2, 1)
        m = m[:hh * 2, :ww * 2].reshape(hh, m.shape[0] // hh, ww, m.shape[1] // ww, 4).mean((1, 3))
        mips.append(np.clip(m + 0.5, 0, 255).astype(np.uint8))
    header = b"TEX\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([1, TEX_BGRA8, 0, 1])
    with open(path, "wb") as f:
        f.write(header)
        for m in reversed(mips):
            f.write(m[..., [2, 1, 0, 3]].tobytes())   # RGBA -> BGRA
    return path


def write_fantome(path, wad_path, image_path=None, version="1.0.0"):
    info = {"Name": "Mossmaw", "Author": "Noah", "Version": version,
            "Description": "Kog'Maw overgrown with moss, mushrooms and ferns. Replaces the base skin; cosmetic only."}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META/info.json", json.dumps(info, indent=2))
        if image_path:
            z.write(image_path, "META/image.png")
        z.write(wad_path, "WAD/" + WAD_NAME)
    return path


def export_all(out_dir, moss_tex=r"C:\NoahCode\LMOD\work\kogmaw_base_tx_cm_moss.png", image_path=None):
    import bpy
    from Aventurine.io import export_skn
    os.makedirs(out_dir, exist_ok=True)
    body, rig = bpy.data.objects[BODY], bpy.data.objects[RIG]
    pieces = [o for o in bpy.data.collections["Moss"].objects if o.type == 'MESH']
    old = bpy.data.objects.get("Mossmaw_Export")
    if old:
        bpy.data.objects.remove(old, do_unlink=True)

    def load(p):
        im = bpy.data.images.load(p, check_existing=False)
        w, h = im.size
        a = np.array(im.pixels[:], np.float32).reshape(h, w, 4)
        bpy.data.images.remove(im)
        return a

    base, tex = load(BASE_TEX), load(moss_tex)
    tex = _bake_shade(tex, body)
    table, per_piece = _piece_swatches(pieces)
    h, w = tex.shape[:2]
    blocks = _free_blocks(base, len(table))
    uv_box = {}
    for (mat_name, variant), (sid, (dark, light)) in table.items():
        x, y = blocks[sid]
        tex[y:y + SWATCH, x:x + SWATCH, :3] = _swatch(dark, light, sid)
        tex[y:y + SWATCH, x:x + SWATCH, 3] = 1.0
        uv_box[sid] = ((x + INSET) / w, (y + INSET) / h), ((x + SWATCH - INSET) / w, (y + SWATCH - INSET) / h)
    uv_of_swatch = {name: [uv_box[s] for s in sids] for name, sids in per_piece.items()}

    keep_pose = rig.data.pose_position
    rig.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    ob = _build_export_mesh(body, pieces, uv_of_swatch)
    skn_path = os.path.join(out_dir, "kogmaw.skn")
    export_skn.write_skn_multi(skn_path, [ob], rig)
    rig.data.pose_position = keep_pose

    png_path = os.path.join(out_dir, "kogmaw_base_tx_cm.png")
    img = bpy.data.images.new("mossmaw_atlas", w, h, alpha=True)
    img.pixels = tex.ravel()
    img.filepath_raw = png_path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)
    tex_path = write_tex(os.path.join(out_dir, "kogmaw_base_tx_cm.tex"), tex)

    rs = _rs()
    wad_path = os.path.join(out_dir, WAD_NAME)
    with open(skn_path, "rb") as f1, open(tex_path, "rb") as f2:
        rs.build_wad_to_path(wad_path, {GAME["skn"]: f1.read(), GAME["tex"]: f2.read()})
    fantome = write_fantome(os.path.join(out_dir, "Mossmaw.fantome"), wad_path, image_path)
    skn = rs.Skn.from_path(skn_path)
    return {"skn": skn_path, "tex": tex_path, "png": png_path, "wad": wad_path, "fantome": fantome,
            "vertices": skn.vertex_count, "triangles": len(skn.indices) // 12,
            "swatches": len(table), "export_object": ob.name}
