# Custom skin workflow

How we make custom League of Legends skins in this repo, for any champion. Written for the
Claude sessions that do the work and for Noah, who directs it. The first skin made this way
is [Mossmaw](skins/mossmaw.md), a mossy Kog'Maw.

## The idea in one paragraph

Noah is the art director: he says what the skin should feel like and reacts to pictures.
Claude is the hands: it drives Blender on Noah's PC through the Blender MCP, and every change
is a **seeded Python script** in `scripts/`, not hand edits saved only in a `.blend`. That one
rule is what makes the rest work. A skin is a recipe that runs on top of Riot's base files,
so it can be rebuilt on any patch, tweaked one pass at a time, and reused for the next
champion. Riot's files and our `.blend` files stay on Noah's PC. The repo holds only our
recipes, notes and docs.

## Who runs where

| Session | Where it runs | What it can do |
| --- | --- | --- |
| Project thread (cloud) | Claude's sandbox | Plan, research, write docs, open PRs, post pictures in the thread. |
| Project thread with the PC linked | Claude's sandbox, reaching Noah's PC | Also drives Blender through the PC's Blender MCP and moves files with folder access to `C:\NoahCode\LMOD` (copy to the PC, pull renders back). No Remote Control session needed. |
| Remote Control session | Noah's PC (`DESKTOP-OB7EUVQ`), in `C:\NoahCode\LMOD` | Everything in Blender through the MCP, git on the PC, export, packaging. |

Either way: the recipe is written and committed from git, Blender runs on the PC, preview
JPEGs go to the `previews` branch, and the thread posts them for Noah. Piano'Maw was built
entirely from a linked cloud thread.

## Two skins at once (one PC, one Blender)

Two threads can work on two skins at the same time without touching each other:

- **Build headless.** Never open, save or switch files in the Blender window: another session
  may be using its scene. Run the recipe in a separate background Blender instead:
  `blender -b base\<champion>\<champion>_rigged.blend --python <recipe> -- <out dir> vN`.
  The MCP's `execute_blender_code_for_cli` does this if `BLENDER_PATH` is set on the PC
  (it is not yet). Until then, start it from the open Blender with
  `subprocess.run([bpy.app.binary_path, "-b", ...])`: a new process, the open scene is untouched.
- **Don't switch the PC's checkout.** `C:\NoahCode\LMOD` is on whatever branch the other skin
  needs. Put the copies you run in `work\<skin>\scripts\` (ignored), with the shared scripts
  they import, and commit from git elsewhere (the cloud clone, or a `git worktree`).
- **`previews` is shared.** Pull with rebase before pushing, never force-push, and prefix every
  picture with the skin name.
- **One mod per champion in game.** Two mods that replace the same base skin conflict, so only
  one Kog'Maw skin can be enabled in LTK Manager at a time.

## One-time setup (already done on Noah's PC)

- Blender 5.2 with the official Blender Lab MCP add-on (bridge on `127.0.0.1:9876`, auto-start on).
  Use only this MCP. Don't install a second one, because two would fight over the same Blender.
- [Aventurine League Tools](https://github.com/RitoShark/Aventurine-League-Tools) 4.0.1 in Blender:
  imports and exports `.skn`, `.skl` and `.anm`, and reads WAD files directly.
- Claude Code on the PC allows `mcp__Blender__execute_blender_code` (otherwise auto mode blocks it).
- Remote Control registered **in `C:\NoahCode\LMOD`** (not `C:\NoahCode\bon\smallscope`).
- LTK Manager (official release from `LeagueToolkit/ltk-manager`) for loading mods in game.

## Folder layout

```
C:\NoahCode\LMOD\                  (this repo, public)
  CLAUDE.md                        rules for agents (short)
  docs\SKIN_WORKFLOW.md            this file
  docs\skins\<skin>.md             one card per skin: concept, versions, status, pictures
  scripts\recolor_texture.py       shared: texture recolor by colour group, with recipes
  scripts\<skin>.py                one recipe per skin (mossmaw.py)
  base\<champion>\                 IGNORED. Riot files pulled from the game, read-only
  work\<skin>\                     IGNORED. .blend working files, vN copies, exports
  work\<skin>\scripts\             IGNORED. the copies a headless run uses (see "Two skins at once")
  work\<skin>\previews\            IGNORED. renders and the audit report, before they go to `previews`
  mods\<skin>\                     IGNORED. Packaged .fantome / .modpkg
  studio\                          Skin Studio: local live page (see studio\README.md)
  studio\data\                     IGNORED. models, gallery images and viewport frames it shows

C:\NoahCode\LMOD_previews\         git worktree on the orphan `previews` branch
  previews\<skin>_v<N>_<view>.jpg  rendered pictures only, never merged
```

Mossmaw predates the per-skin subfolders and lives at `work\mossmaw.blend`. Use subfolders for
new skins.

## What is committed and what never is

**Committed (through a PR):** scripts, docs, skin cards, `.gitignore` changes.

**Never committed:** anything extracted from the game (`.skn`, `.skl`, `.anm`, `.tex`, `.dds`,
`.bin`, `.wad.client`, PNGs made from Riot textures), `.blend` files (they embed Riot meshes),
exported or packaged mods. `.gitignore` blocks the common ones. Run `git status` before
every commit anyway, and stop if anything from `base/`, `work/` or `mods/` shows up.

**The `previews` branch** holds JPEG renders only. It is how pictures get from the PC into
the chat. Never merge it and never put game files in it.

**Git rules:** never push to `main`. Branch, open a PR, rebase onto the latest `main` and fix
conflicts before it merges. Noah merges.

## The pipeline, stage by stage

Each stage has a done check and a picture. Don't start a stage until the one before it has
its picture.

### 1. Pull the base champion (read-only)

- Open `<League>\Game\DATA\FINAL\Champions\<Champion>.wad.client` with Aventurine's WAD
  library. Copy out `assets/characters/<champion>/skins/base/`: the `.skn` mesh, the `.skl`
  skeleton, the main texture, and a few animations (idle, run, attacks, spells, dance).
  Also copy `data/characters/<champion>/skins/skin0.bin`, which the mod will need later.
- Never write into the League folder. Never run anything while the game is open.
- CommunityDragon has meshes and textures but not skeletons or animations, so use the game files.
- **Done when:** `base\<champion>\<champion>_rigged.blend` plays the run animation with the
  right texture. That file holds only the run; recipes import the other `.anm` files
  (idle, attack, main spell) with Aventurine before the audit (`pianomaw.load_actions`). (Aventurine imports flip the UVs correctly. A raw `.obj` import needed a V flip.)
- **Picture:** a 6-frame run strip plus idle strip.

### 2. Write the brief

Write the brief in the skin card (`docs/skins/<skin>.md`) before any Blender work: the concept
in one line, three to five visual pillars (for Mossmaw: moss hide, lichen stone skull,
hanging strands, mushrooms with one glowing cluster), what stays untouched (eyes, mouth
readability, silhouette at game zoom), and a triangle budget.

### 3. Texture pass

- Add a recipe to `scripts/recolor_texture.py` (or a skin-specific function). Split the
  texture into colour groups (body, accent, bone), shift each group's hue, saturation and value,
  and always keep the original pixel brightness so Riot's painted shading survives.
- Write the new texture to `work\<skin>\`. Never overwrite the base texture.
- **Picture:** original and variant side by side, same pose and camera.

### 4. Geometry pass

- Build new pieces procedurally in `scripts/<skin>.py` from a `SEED`, so a rerun gives the
  same skin. Keep a table (like Mossmaw's `GROWTH`) that says what grows on which bones.
- **Skinning:** pieces that sit on the surface take weights from the body (Data Transfer,
  nearest face, then averaged per piece). Pieces that stick out copy the weights of the one
  skin vertex they grow from. Use only the original bones. No new bones, and at most 4
  influences per vertex, because the game uses the original `.skl`.
- **Audit every piece on every frame** of run, idle, an attack and the main spell. Regrow
  any piece that stretches, comes off the skin, dips into the ground or clips into the body.
- Keep the eyes and mouth clear. Check the silhouette at game zoom, not only close up: render
  the standard game-camera shot (`pianomaw.render_game_view`), small and from above.
- **Pieces that must hug the body** (a keyboard, plates, trim) can't be regrown elsewhere like
  moss. Drape them with rays onto the rest-pose surface, smooth the bed over more than one
  carapace band so they don't tilt into shingles, and give each piece one shared set of
  weights from the skin under it. Per-vertex weights look right at rest but tear: Kog'Maw's
  carapace squashes so much that Piano'Maw's keys stretched up to 7x. Measure how deep they
  sink on the worst frame (`pianomaw.sink_audit`) instead of only counting clipped frames.
- **The audit's "collar" must be a point on the skin**, moving with the piece's weights. A
  point on a smoothed bed or a floating ornament reads as "came loose" on every frame.
- **Picture:** hero 3/4 shot, run strip, game-zoom shot, and vN-1 vs vN side by side.

### 5. Polish passes (repeat)

Before each pass, save the current file as `work\<skin>\<skin>_vN.blend`. Then change the
script, rebuild from the rigged base, audit, and render. Each pass ends with a turntable
(front, side, back, 3/4) and a "before vs after" image.

### 6. Export to League format

This stage is being proven on Mossmaw right now. Update it with what worked.

- Join the new pieces into the body as one `.skn` on the original `.skl` (same bone names and
  order, at most 4 influences per vertex). New pieces need UVs: either pack them into the
  body texture or export a second submesh with its own texture and material in `skin0.bin`.
- League has no live glow on champion meshes, so paint the glow into the texture.
- Convert textures to `.tex` (Aventurine or ltk-tex-utils).
- **Done when:** the exported `.skn` re-imported with Aventurine on the original `.skl` plays
  run and idle cleanly.
- **Picture:** the re-imported export, running.

### 7. Package and load

- Package a `.fantome` / `.modpkg` that replaces only that champion's base skin files.
  Keep it in `mods\<skin>\`.
- Add it in LTK Manager, then test in **Practice Tool** first.
- **Picture:** LTK Manager with the mod listed, then an in-game screenshot.

## The iteration loop

1. **Brief.** The cloud thread turns Noah's words into a short brief for the PC session:
   the goal, design notes, which file to save to, what to render, and the picture names.
2. **Build.** The PC session changes the script and rebuilds.
3. **Self-review.** Look at your own renders before sending anything. Fix floating bits,
   stretching, clipping, and anything that reads badly. Say what you fixed and what still looks weak.
4. **Show.** Push the JPEGs to `previews`, then post them in the thread with two or three
   lines on what changed and one recommendation for the next pass.
5. **React.** Noah answers in a few words ("more moss", "keep polishing"). That is the next brief.

**Live view.** If Skin Studio is running (`studio\start.bat`, then http://localhost:7777),
Noah can watch the work as it happens. During a PC session: run `bpy.ops.skinstudio.start()`
once, call `bpy.ops.skinstudio.send_model()` after each rebuild, and post the current step to
`/api/status`. Renders saved in `work\<skin>\previews\` show up in its gallery on their own.
The thread pictures above are still required, because Noah isn't always at the PC.

### Visual proof rules

- Every result comes with a picture attached in the thread. A file path on Noah's PC is not a picture.
- Picture names: `<skin>_v<N>_hero.jpg`, `<skin>_v<N>_run_strip.jpg`,
  `<skin>_v<N>_turntable.jpg`, `<skin>_v<N>_game.jpg`, `<skin>_v<N-1>_v<N>.jpg`, and
  `<skin>_original_v<N>.jpg` (base Kog'Maw next to the skin). JPEG, quality about 85.
  Strips may be wider than 1600 px; single shots should not be.
- Same camera, light and frame between versions, so a comparison shows the change and nothing else.
- Preview only what the game can show: no gloss, metal or live glow on added pieces. Paint
  sheen and glow into colours (the export bakes material colours into the texture).

### When to ask Noah, and when to decide

- **Decide yourself:** tooling, file layout, how to build something, fixing your own mistakes,
  the order of the work. Mention the call in one line.
- **Ask Noah:** true taste questions (which of these three looks?), and anything that needs his
  hands or his words. One or two questions at a time, always with a picture and your pick.
- **Settled, don't reopen:** Noah plays his skins in game for personal, cosmetic use and accepts
  the ToS risk (decided 2026-10-22).

## After every League patch

League patches roughly every two weeks. Two things can break:

1. **The loader.** A Vanguard or game update can stop LTK Manager from patching. Fix: update
   LTK Manager. If it still fails, wait a day or two for a fix and play without mods meanwhile.
2. **The champion's files.** If Riot changes the champion's mesh, skeleton, animations or
   `skin0.bin`, an old mod can show the wrong model, crash, or loop on the loading screen.

Our recipes make the second case cheap. Re-extract `base\<champion>\`, compare it with the
last copy, and if anything changed, rerun the skin's script, export, package, and test in
Practice Tool again. If nothing changed, the old mod is fine.

## Known gotchas

- Remote Control must be started from `C:\NoahCode\LMOD`. A session started from another
  folder works in the wrong place.
- An idle Remote Control session sleeps and only wakes on a message typed in its own thread,
  not in the project chat.
- The PC's safety check blocks steps that were approved second-hand (relayed from the project
  chat). Ask Noah to say it in the thread itself.
- Aventurine 4.0.1 works on Blender 5.2, even though it was built for 4.x.
- modelviewer.lol has no downloads and streams scrambled files. Use the game files.
- A file copied to the PC from the project folder can arrive stale: the shared folder lags a few
  seconds. Check the PC copy (size or a known line) before running it, or you render the old recipe.
- Several of Kog'Maw's faces are flipped. Ray hits must flip a downward normal before testing
  whether a surface faces up.

## Starting a new skin (checklist)

1. Copy `docs/skins/_TEMPLATE.md` to `docs/skins/<skin>.md` and write the brief.
2. Stage 1 for the champion if `base\<champion>\` doesn't exist yet. Record the champion's
   bone names in the skin card, because they differ per champion.
3. Branch `<skin>` from `main`. Write `scripts/<skin>.py` and import the shared helpers
   (skinning, audit, materials, renders) from `mossmaw.py` rather than copying them. Once
   Mossmaw is merged, those helpers should move to one shared `scripts/skinlib.py`.
4. Run stages 3 to 7 with a picture at every stage, then open a PR with the script and card.
