# Skin Studio

A local web page for watching skin work happen live. Claude works in Blender and the terminal;
Noah watches one page at **http://localhost:7777**.

| Panel | Where it comes from |
| --- | --- |
| Claude activity | The newest Claude Code session log on this PC (`~/.claude/projects/*LMOD*/*.jsonl`): what Claude says, each tool call, and a ✓ or ✗ when it finishes. |
| Live 3D model | The newest `.glb` in `studio/data/models/`. Spin it, zoom it, pick an animation, or switch to an approximate League camera. |
| Blender viewport | Frames sent by the Blender add-on (about 2 per second, only when something changes). |
| Gallery | Images in `studio/data/gallery/`, `..\LMOD_previews\previews\`, `renders\` and `work\<skin>\previews\`, newest first. |

## Start it

1. Double-click `studio\start.bat` (or run `python studio/server.py`). The page opens by itself.
2. In Blender: Edit > Preferences > Add-ons > Install from Disk > `studio\blender\studio_stream.py`,
   enable **Skin Studio**. Then in the 3D view press N, open the **Studio** tab, and press
   **Start streaming**. **Send model** puts the current scene, with its animations, in the 3D panel.
   While streaming, every save sends the model again.

## For Claude (Blender MCP)

```python
bpy.ops.skinstudio.start()                               # stream the viewport
bpy.ops.skinstudio.send_model()                          # refresh the 3D panel after a change
bpy.ops.skinstudio.send_render(filepath=r"...\x.jpg")    # put a render in the gallery
bpy.app.driver_namespace["studio_log"]("Baking the moss mask")  # a line in the activity feed
```

The feed reads Claude Code logs on this PC. A cloud thread that drives Blender through the
device link has no log here, so it should call `studio_log` at each step instead (or POST
`{"kind": "note"|"tool", "text": ..., "tool": ...}` to `/api/events`).

Show the current step in the header:
`curl -X POST localhost:7777/api/status -d "{\"step\": \"Step 3/5: moss texture pass\"}"`

## Safety

- Listens on 127.0.0.1 only and refuses requests addressed to any other host name, so other
  devices and other websites can't read the feed.
- Nothing is uploaded anywhere. `studio/data/` is in `.gitignore` because its renders and models
  come from Riot files.
- Standard library Python only. three.js r160 is vendored in `web/vendor/` (MIT) so the page
  works offline.

## Options

`python studio/server.py --help`: `--port`, `--match` (which Claude project logs to follow,
default `LMOD`, `""` for any), `--gallery <folder>` (extra image folder, repeatable),
`--no-browser`.
