# LMOD

Noah's League of Legends side projects, starting with a custom Kog'Maw skin made in Blender.

## What lives here

Only our own work: Blender Python scripts, notes, and any parts we model ourselves.

## What never lives here

Riot's game files. That means extracted meshes (`.skn`), skeletons (`.skl`), animations (`.anm`), textures (`.tex`, `.dds`, and PNGs made from them), and `.blend` files that contain them. This repo is public and those files belong to Riot Games. `.gitignore` blocks the common ones, but check `git status` before committing anything new.

## Local setup (Windows)

- Blender 5.2 with the official Blender MCP add-on (auto-start on) and the [Aventurine League Tools](https://github.com/RitoShark/Aventurine-League-Tools) add-on.
- Kog'Maw's base files are extracted read-only from the local League install into `base/kogmaw/` (ignored by git).
- Working copies go in `work/` (also ignored).

## Workflow

Never push to `main`. Work on a branch, open a pull request, resolve conflicts on the branch, then merge.

## Risk note

Loading modded files into the game is against Riot's Terms of Service. The plan for now is preview only: the skin is built and viewed in Blender, not loaded into League.
