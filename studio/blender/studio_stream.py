"""Skin Studio add-on: streams the Blender viewport and model to the local studio page.

Install: Edit > Preferences > Add-ons > Install from Disk > pick this file, then enable
"Skin Studio". The panel lives in the 3D view sidebar (N) under the "Studio" tab.

It only talks to the studio server on this PC (http://127.0.0.1:7777 by default).

From a script or the Blender MCP:
    bpy.ops.skinstudio.start()        start sending viewport frames
    bpy.ops.skinstudio.send_model()   export the visible scene as .glb to the 3D panel
    bpy.ops.skinstudio.send_render(filepath=r"...jpg")   put an image in the gallery
    bpy.app.driver_namespace["studio_log"]("text")   add a line to the activity feed
"""

bl_info = {
    "name": "Skin Studio",
    "author": "LMOD",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Studio",
    "description": "Stream the viewport and model to the local Skin Studio page",
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


def prefs():
    return bpy.context.preferences.addons[__name__].preferences


class SkinStudioPrefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    server: bpy.props.StringProperty(name="Studio server", default="http://127.0.0.1:7777")
    fps: bpy.props.FloatProperty(name="Frames per second", default=2.0, min=0.2, max=10.0)
    width: bpy.props.IntProperty(name="Frame width", default=960, min=320, max=1920)
    send_on_save: bpy.props.BoolProperty(name="Send model to studio on every save", default=True)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "server")
        col.prop(self, "fps")
        col.prop(self, "width")
        col.prop(self, "send_on_save")


# ------------------------------------------------------------------ sending


_busy = threading.Lock()
_last_error = ""


def post(path, data, headers=None, wait=False):
    """POST bytes to the studio. Fire and forget unless wait=True."""

    def work():
        global _last_error
        try:
            url = prefs_server + path
            req = urllib.request.Request(url, data=data, method="POST", headers=headers or {})
            urllib.request.urlopen(req, timeout=5).read()
            _last_error = ""
        except Exception as exc:  # studio not running is normal; just note it
            _last_error = str(exc)
        finally:
            if _busy.locked():
                _busy.release()

    prefs_server = prefs().server.rstrip("/")
    if wait:
        _busy.acquire()
        work()
        return
    if not _busy.acquire(blocking=False):
        return False  # previous frame still sending; drop this one
    threading.Thread(target=work, daemon=True).start()
    return True


def png_bytes(rgba, w, h):
    """Minimal PNG encoder for an (h, w, 4) uint8 array, top row first."""
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 1))
        + chunk(b"IEND", b"")
    )


def studio_log(text, kind="note", tool="blender"):
    """Add a line to the studio's activity feed. Handy from the Blender MCP:
    bpy.app.driver_namespace["studio_log"]("Baking the moss mask")"""
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
    """Draw the viewport into an offscreen buffer. Does not need the window in front."""
    import gpu

    win, area, space, region = found
    rv3d = space.region_3d
    aspect = region.height / max(1, region.width)
    w = min(width, region.width)
    h = max(1, int(w * aspect))
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    offscreen = gpu.types.GPUOffScreen(w, h)
    try:
        with offscreen.bind():
            offscreen.draw_view3d(
                scene, view_layer, space, region, rv3d.view_matrix, rv3d.window_matrix,
                do_color_management=True,
            )
            fb = gpu.state.active_framebuffer_get()
            buf = fb.read_color(0, 0, w, h, 4, 0, "UBYTE")
    finally:
        offscreen.free()
    try:
        arr = np.asarray(buf, dtype=np.uint8)
    except Exception:
        buf.dimensions = w * h * 4
        arr = np.array(buf.to_list(), dtype=np.uint8)
    arr = arr.reshape(h, w, 4)[::-1]  # GPU rows start at the bottom
    arr = np.ascontiguousarray(arr)
    arr[:, :, 3] = 255
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


class _Stream:
    running = False
    dirty = True
    last_view = None
    last_sent = 0.0
    last_beat = 0.0
    method = "offscreen"
    frames = 0


def _mark_dirty(*_args):
    _Stream.dirty = True


def _tick():
    if not _Stream.running:
        return None
    p = prefs()
    interval = 1.0 / p.fps
    try:
        found = find_view3d()
        now = time.time()
        if found:
            rv3d = found[2].region_3d
            view = tuple(round(v, 4) for row in rv3d.view_matrix for v in row)
            if view != _Stream.last_view:
                _Stream.dirty = True
                _Stream.last_view = view
            playing = bpy.context.screen and bpy.context.screen.is_animation_playing
            if (_Stream.dirty or playing or now - _Stream.last_sent > 5) and not _busy.locked():
                if _Stream.method == "offscreen" and np is not None:
                    try:
                        img = capture_offscreen(found, p.width)
                    except Exception as exc:
                        print(f"[Skin Studio] offscreen capture failed, using screenshots: {exc}")
                        _Stream.method = "screenshot"
                        img = capture_screenshot(found)
                else:
                    img = capture_screenshot(found)
                scene = bpy.context.scene
                headers = {
                    "Content-Type": "image/png",
                    "X-Blend-File": bpy.path.basename(bpy.data.filepath) or "unsaved",
                    "X-Frame": str(scene.frame_current),
                    "X-Mode": found[2].shading.type.title(),
                }
                if post("/api/viewport", img, headers):
                    _Stream.dirty = False
                    _Stream.last_sent = now
                    _Stream.frames += 1
    except Exception as exc:
        print(f"[Skin Studio] {exc}")
    return interval


def start_stream():
    if _Stream.running:
        return
    _Stream.running = True
    _Stream.dirty = True
    for h in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        if _mark_dirty not in h:
            h.append(_mark_dirty)
    bpy.app.timers.register(_tick, first_interval=0.1)


def stop_stream():
    _Stream.running = False
    for h in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        if _mark_dirty in h:
            h.remove(_mark_dirty)


# ------------------------------------------------------------------ model


def export_glb():
    name = (bpy.path.display_name_from_filepath(bpy.data.filepath) or "scene") + ".glb"
    path = os.path.join(tempfile.gettempdir(), "skinstudio_" + name)
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_visible=True,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_apply=False,
    )
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


# ------------------------------------------------------------------ UI


class SKINSTUDIO_OT_start(bpy.types.Operator):
    bl_idname = "skinstudio.start"
    bl_label = "Start streaming"
    bl_description = "Send viewport frames to the Skin Studio page"

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
    bl_description = "Export the visible scene with its animations as .glb to the 3D panel"

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
            col.label(text=f"Streaming ({_Stream.frames} frames, {_Stream.method})")
        else:
            col.operator("skinstudio.start", icon="PLAY")
        col.separator()
        col.operator("skinstudio.send_model", icon="EXPORT")
        if _last_error:
            col.label(text="Studio not reachable", icon="ERROR")
        col.separator()
        col.prop(prefs(), "fps")
        col.prop(prefs(), "send_on_save")


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
    bpy.app.driver_namespace["studio_log"] = studio_log


def unregister():
    stop_stream()
    if _on_save in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_on_save)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
