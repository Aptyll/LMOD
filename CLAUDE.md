# Rules for Claude in LMOD

This repo holds recipes for custom League of Legends skins, built in Blender on Noah's PC
through the Blender MCP. Read `docs/SKIN_WORKFLOW.md` before any skin work. It has the
pipeline, folder layout, and iteration loop.

## Hard rules

- **Never commit Riot files or `.blend` files.** That covers `.skn`, `.skl`, `.anm`, `.tex`,
  `.dds`, `.bin`, `.wad.client`, PNGs made from Riot textures, and anything in `base/`, `work/` or
  `mods/`. Check `git status` before every commit. This repo is public.
- **Never push to `main`.** Branch, open a PR, rebase on the latest `main`, and leave it for Noah to merge.
- **The `previews` branch is pictures only** (JPEG renders). It is never merged.
- **Never write into the League install** and never run tools against it while the game is open.
- **Every skin change is a script.** Seeded, rerunnable from the rigged base, saved in
  `scripts/`. A change that only exists in a `.blend` is lost on the next patch.
- **Original skeleton only:** no new bones, at most 4 bone influences per vertex.
- **Save `work/<skin>/<skin>_vN.blend` before every pass.** Never overwrite the base files.
- **Never touch the open Blender scene or the PC's checkout for another skin's work.** Build
  headless in a background Blender and keep run copies in `work/<skin>/scripts/`.

## Working with Noah

- Show, don't tell: every result in the thread has an attached picture. A path on his PC is not a picture.
- Look at your own renders and fix problems before you send them.
- Make technical calls yourself and mention them. Ask only taste or hands-on questions, one
  or two at a time, with a picture and your recommendation.
- Using his skins in game (personal, cosmetic) is his settled decision. Don't reopen it.
- Never sell skins or unlock Riot's paid skins. If a skin is shared, it is free and credited.
