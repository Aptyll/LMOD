"""Skin Studio add-on: sends the Blender viewport and model to the local studio page.

Install: Edit > Preferences > Add-ons > Install from Disk > pick this file, then enable
"Skin Studio". The panel lives in the 3D view sidebar (N) under the "Studio" tab.

It only talks to the studio server on this PC (http://127.0.0.1:7777 by default).

Built to stay light:
  * A frame is captured only after the scene or view changes and then holds still for a
    moment, so orbiting or editing doesn't capture every tick. Nothing changes, nothing is sent.
  * No captures while nobody has the studio page open and visible, or while the page's
    "Live viewport" switch is off. Then the add-on only sends a tiny heartbeat every 3 s.
  * No captures during animation playback unless "Stream playback" is on.
  * Frames are 640 px wide at most and capped at 1 per second by default.

From a script or the Blender MCP:
    bpy.ops.skinstudio.start()        start sending viewport frames
    bpy.ops.skinstudio.send_model()   export the rig and its meshes as .glb to the 3D panel
    bpy.ops.skinstudio.send_render(filepath=r"...jpg")   put an image in the gallery
    import studio_stream; studio_stream.studio_log("text")   add a line to the activity feed
"""

bl_info = {
    "name": "Skin Studio",
    "author": "LMOD",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Studio",
    "description": "Send the viewport and model to the local Skin Studio page",
    "category": "3D View",
}

import json
import os
import struct
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zlib

import bpy

try:
    import numpy as np
except ImportError:  # Blender ships numpy, but be safe
    np = None

TICK = 0.25  # seconds between cheap checks
SETTLE = 0.4  # wait this long after the last change before capturing
HEARTBEAT = 3.0


def prefs():
    return bpy.context.preferences.addons[__name__].preferences


class SkinStudioPrefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    server: bpy.props.StringProperty(name="Studio server", default="http://127.0.0.1:7777")
    fps: bpy.props.FloatProperty(name="Max frames per second", default=1.0, min=0.2, max=5.0)
    width: bpy.props.IntProperty(name="Frame width", default=640, min=320, max=1600)
    stream_playback: bpy.props.BoolProperty(
        name="Stream playback", default=False,
        description="Also send frames while an animation plays (uses more of the PC)")
    send_on_save: bpy.props.BoolProperty(name="Send model to studio on every save", default=True)

    def draw(self, context):
        col = self.layout.column()
        for p in ("server", "fps", "width", "stream_playback", "send_on_save"):
            col.prop(self, p)


# ------------------------------------------------------------------ sending


class _Stream:
    running = False
    dirty = True
    dirty_since = 0.0
    last_view = None
    last_sent = 0.0
    last_beat = 0.0
    frames = 0
    capture_ms = 0.0
    method = "offscreen"
    viewers = 1  # until the server says otherwise
    enabled = True


_busy = threading.Lock()
_last_error = ""


def post(path, data, headers=None, wait=False):
    """POST bytes to the studio. Fire and forget unless wait=True.
    Replies from the studio tell us whether anyone is watching."""

    def work():
        global _last_error
        try:
            req = urllib.request.Request(server + path, data=data, method="POST", headers=headers or {})
            reply = urllib.request.urlopen(req, timeout=5).read()
            _last_error = ""
            try:
                info = json.loads(reply)
                if "viewers" in info:
                    _Stream.viewers = int(info["viewers"])
                if "viewport_enabled" in info:
                    _Stream.enabled = bool(info["viewport_enabled"])
            except (ValueError, TypeError):
                pass
        except Exception as exc:  # studio not running is normal; just note it
            _last_error = str(exc)
            _Stream.viewers = 0
        finally:
            _busy.release()

    server = prefs().server.rstrip("/")
    if wait:
        _busy.acquire()
        work()
        return True
    if not _busy.acquire(blocking=False):
        return False  # previous request still going; skip this one
    threading.Thread(target=work, daemon=True).start()
    return True


def png_bytes(rgb, w, h):
    """Minimal PNG encoder for an (h, w, 3) uint8 array, top row first."""
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 1))
        + chunk(b"IEND", b"")
    )


def studio_log(text, kind="note", tool="blender"):
    """Add a line to the studio's activity feed."""
    body = json.dumps({"kind": kind, "text": text, "tool": tool}).encode()
    post("/api/events", body, {"Content-Type": "application/json"}, wait=True)
    return not _last_error


# ------------------------------------------------------------------ capture


def find_view3d():
    """Biggest 3D viewport in any open window."""
    best = None
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region and (best is None or region.width * region.height > best[3].width * best[3].height):
                best = (win, area, area.spaces.active, region)
    return best


def capture_offscreen(found, width):
    """Draw the viewport into a small offscreen buffer. Does not need the window in front."""
    import gpu

    win, area, space, region = found
    rv3d = space.region_3d
    w = min(width, region.width)
    h = max(1, int(w * region.height / max(1, region.width)))
    offscreen = gpu.types.GPUOffScreen(w, h)
    try:
        with offscreen.bind():
            offscreen.draw_view3d(
                bpy.context.scene, bpy.context.view_layer, space, region,
                rv3d.view_matrix, rv3d.window_matrix, do_color_management=True,
            )
            buf = gpu.state.active_framebuffer_get().read_color(0, 0, w, h, 4, 0, "UBYTE")
    finally:
        offscreen.free()
    try:
        arr = np.asarray(buf, dtype=np.uint8)
    except Exception:
        buf.dimensions = w * h * 4
        arr = np.array(buf.to_list(), dtype=np.uint8)
    arr = np.ascontiguousarray(arr.reshape(h, w, 4)[::-1, :, :3])  # GPU rows start at the bottom
    return png_bytes(arr, w, h)


def capture_screenshot(found):
    """Fallback: Blender's own area screenshot (includes overlays and gizmos)."""
    win, area, space, region = found
    path = os.path.join(tempfile.gettempdir(), "skinstudio_viewport.png")
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=path, check_existing=False)
    with open(path, "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------ loop


def _mark_dirty(*_args):
    _Stream.dirty = True
    _Stream.dirty_since = time.time()


def _heartbeat(now):
    _Stream.last_beat = now
    scene = bpy.context.scene
    body = json.dumps({"blender": {
        "file": bpy.path.basename(bpy.data.filepath) or "unsaved",
        "frame": scene.frame_current,
    }}).encode()
    post("/api/status", body, {"Content-Type": "application/json"})


def _tick():
    if not _Stream.running:
        return None
    try:
        now = time.time()
        if now - _Stream.last_beat > HEARTBEAT:
            _heartbeat(now)
        if not (_Stream.viewers > 0 and _Stream.enabled):
            return TICK * 2  # nobody watching: do nothing else

        found = find_view3d()
        if not found:
            return TICK
        space = found[2]
        view = tuple(round(v, 4) for row in space.region_3d.view_matrix for v in row)
        if view != _Stream.last_view:
            _Stream.last_view = view
            _mark_dirty()

        p = prefs()
        screen = bpy.context.window_manager.windows[0].screen
        playing = bool(screen and screen.is_animation_playing)
        if playing and not p.stream_playback:
            return TICK  # wait for playback to stop; the last frame is sent then
        settled = playing or now - _Stream.dirty_since >= SETTLE
        if not (_Stream.dirty and settled and now - _Stream.last_sent >= 1.0 / p.fps):
            return TICK
        if _busy.locked():
            return TICK

        t = time.perf_counter()
        if _Stream.method == "offscreen" and np is not None:
            try:
                img = capture_offscreen(found, p.width)
            except Exception as exc:
                print(f"[Skin Studio] offscreen capture failed, using screenshots: {exc}")
                _Stream.method = "screenshot"
                img = capture_screenshot(found)
        else:
            img = capture_screenshot(found)
        _Stream.capture_ms = (time.perf_counter() - t) * 1000
        headers = {
            "Content-Type": "image/png",
            "X-Blend-File": bpy.path.basename(bpy.data.filepath) or "unsaved",
            "X-Frame": str(bpy.context.scene.frame_current),
            "X-Mode": space.shading.type.title(),
        }
        if post("/api/viewport", img, headers):
            _Stream.dirty = False
            _Stream.last_sent = now
            _Stream.last_beat = now
            _Stream.frames += 1
    except Exception as exc:
        print(f"[Skin Studio] {exc}")
    return TICK


HANDLERS = ("depsgraph_update_post", "frame_change_post")


def start_stream():
    if _Stream.running:
        return
    _Stream.running = True
    _mark_dirty()
    for name in HANDLERS:
        h = getattr(bpy.app.handlers, name)
        if _mark_dirty not in h:
            h.append(_mark_dirty)
    bpy.app.timers.register(_tick, first_interval=0.1)


def stop_stream():
    _Stream.running = False
    for name in HANDLERS:
        h = getattr(bpy.app.handlers, name)
        if _mark_dirty in h:
            h.remove(_mark_dirty)


# ------------------------------------------------------------------ model


def rig_objects():
    """Visible armatures plus the meshes they drive. Leaves out props like a ground plane."""
    vis = [o for o in bpy.context.view_layer.objects if o.visible_get()]
    arms = {o for o in vis if o.type == "ARMATURE"}
    meshes = [
        o for o in vis if o.type == "MESH" and (
            (o.parent in arms)
            or any(m.type == "ARMATURE" and m.object in arms for m in o.modifiers)
        )
    ]
    return list(arms), meshes


def export_glb():
    """Export the rig and its meshes with every animation of the rig, then send it.
    Each of the rig's actions is put on a temporary NLA track so the exporter writes all of
    them (Blender 5's slotted actions are otherwise skipped). The tracks are removed after."""
    name = (bpy.path.display_name_from_filepath(bpy.data.filepath) or "scene") + ".glb"
    path = os.path.join(tempfile.gettempdir(), "skinstudio_" + name)
    arms, meshes = rig_objects()
    selected = [o for o in bpy.context.view_layer.objects if o.select_get()]
    active = bpy.context.view_layer.objects.active
    temp_tracks = []
    try:
        for arm in arms:
            ad = arm.animation_data or arm.animation_data_create()
            for act in bpy.data.actions:
                slots = getattr(act, "slots", None)
                if slots is not None and len(slots) and not any(s.identifier == "OB" + arm.name for s in slots):
                    continue
                if slots is None and act.id_root not in ("OBJECT", None):
                    continue
                track = ad.nla_tracks.new()
                track.name = act.name
                strip = track.strips.new(act.name, int(act.frame_range[0]), act)
                if hasattr(strip, "action_slot") and slots:
                    strip.action_slot = next(s for s in slots if s.identifier == "OB" + arm.name)
                temp_tracks.append((ad, track))
        kwargs = dict(filepath=path, export_format="GLB", export_animations=True, export_apply=False)
        if arms:
            for o in bpy.context.view_layer.objects:
                o.select_set(o in arms or o in meshes)
            kwargs.update(use_selection=True, export_animation_mode="NLA_TRACKS" if temp_tracks else "ACTIONS")
        else:
            kwargs.update(use_visible=True, export_animation_mode="ACTIONS")
        bpy.ops.export_scene.gltf(**kwargs)
    finally:
        for ad, track in temp_tracks:
            ad.nla_tracks.remove(track)
        for o in bpy.context.view_layer.objects:
            o.select_set(o in selected)
        bpy.context.view_layer.objects.active = active
    with open(path, "rb") as fh:
        data = fh.read()
    post("/api/model?" + urllib.parse.urlencode({"name": name}), data,
         {"Content-Type": "model/gltf-binary"}, wait=True)
    return name, len(data)


@bpy.app.handlers.persistent
def _on_save(*_args):
    try:
        if prefs().send_on_save and _Stream.running:
            export_glb()
    except Exception as exc:
        print(f"[Skin Studio] model export on save failed: {exc}")


@bpy.app.handlers.persistent
def _on_load(*_args):
    bpy.app.driver_namespace["studio_log"] = studio_log  # cleared by every file load
    _Stream.last_view = None
    _mark_dirty()


# ------------------------------------------------------------------ UI


class SKINSTUDIO_OT_start(bpy.types.Operator):
    bl_idname = "skinstudio.start"
    bl_label = "Start streaming"
    bl_description = "Send viewport frames to the Skin Studio page while it is open"

    def execute(self, context):
        start_stream()
        return {"FINISHED"}


class SKINSTUDIO_OT_stop(bpy.types.Operator):
    bl_idname = "skinstudio.stop"
    bl_label = "Stop streaming"

    def execute(self, context):
        stop_stream()
        return {"FINISHED"}


class SKINSTUDIO_OT_send_model(bpy.types.Operator):
    bl_idname = "skinstudio.send_model"
    bl_label = "Send model"
    bl_description = "Export the rig, its meshes and its animations as .glb to the 3D panel"

    def execute(self, context):
        name, size = export_glb()
        if _last_error:
            self.report({"WARNING"}, f"Studio not reachable: {_last_error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Sent {name} ({size // 1024} KB)")
        return {"FINISHED"}


class SKINSTUDIO_OT_send_render(bpy.types.Operator):
    bl_idname = "skinstudio.send_render"
    bl_label = "Send image to gallery"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        with open(bpy.path.abspath(self.filepath), "rb") as fh:
            data = fh.read()
        name = os.path.basename(self.filepath)
        post("/api/gallery?" + urllib.parse.urlencode({"name": name}), data, wait=True)
        return {"FINISHED"} if not _last_error else {"CANCELLED"}


class SKINSTUDIO_PT_panel(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Studio"
    bl_label = "Skin Studio"

    def draw(self, context):
        col = self.layout.column(align=True)
        if _Stream.running:
            col.operator("skinstudio.stop", icon="PAUSE")
            if _last_error:
                col.label(text="Studio not reachable", icon="ERROR")
            elif not _Stream.enabled:
                col.label(text="Paused from the page")
            elif _Stream.viewers == 0:
                col.label(text="Idle: page not open")
            else:
                col.label(text=f"{_Stream.frames} frames, last {_Stream.capture_ms:.0f} ms")
        else:
            col.operator("skinstudio.start", icon="PLAY")
        col.separator()
        col.operator("skinstudio.send_model", icon="EXPORT")
        col.separator()
        p = prefs()
        col.prop(p, "fps")
        col.prop(p, "stream_playback")
        col.prop(p, "send_on_save")


CLASSES = (
    SkinStudioPrefs,
    SKINSTUDIO_OT_start,
    SKINSTUDIO_OT_stop,
    SKINSTUDIO_OT_send_model,
    SKINSTUDIO_OT_send_render,
    SKINSTUDIO_PT_panel,
)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    if _on_save not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(_on_save)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    bpy.app.driver_namespace["studio_log"] = studio_log


def unregister():
    stop_stream()
    for h, f in ((bpy.app.handlers.save_post, _on_save), (bpy.app.handlers.load_post, _on_load)):
        if f in h:
            h.remove(f)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
