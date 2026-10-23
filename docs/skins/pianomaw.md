# Piano'Maw (Kog'Maw)

**Concept:** Kog'Maw as a living grand piano: black lacquer, ivory, gold and red felt.

**Status:** polish v2 (2026-10-22). Not exported yet.

Recipe: `scripts/pianomaw.py` (texture recipe `piano_pixels` inside it; skinning, audit and
render helpers imported from `scripts/mossmaw.py`). Built headless, so it never touches the
Blender window another session is using:

    blender -b base\kogmaw\kogmaw_rigged.blend --python scripts\pianomaw.py -- work\pianomaw\previews v2

Working files on Noah's PC: `work\pianomaw\` (texture, `pianomaw_vN.blend`, previews).

## Brief

- Pillars:
  1. **Black lacquer** carapace with a tight polished sheen (the blue hide).
  2. **Gold**: the teal stripes become gold inlay, the teal belly a gold harp plate with strings.
  3. **Ivory** skull and bone plates, edged with a thin gold pinstripe.
  4. **Hammers**: the bright antenna tips turn red felt, so each antenna reads as a piano hammer.
  5. **Keyboard spine**: white and black keys draped down the crest of his back, the first
     thing the game camera sees. Plus a gold eighth note on his tail.
- Keep untouched: eyes (as painted), mouth readable (crimson velvet), his silhouette.
- Triangle budget for added geometry: under 3k (v2 uses about 1.7k).

## Champion notes

- Same base as [Mossmaw](mossmaw.md): champion id 96, 70 bones, body `KogMaw`, armature `KogMaw_Rig`.
- `kogmaw_rigged.blend` only holds `kogmaw_run`. The recipe imports idle1, attack1 and spell4
  from `base\kogmaw\animations\` with Aventurine before the audit.
- Facing: his front is -Y. His back is a banded dome; the crest search runs from y -0.72
  (behind the antenna roots) toward the tail and stops where the crest gets steep.
- Rays from above ignore the antennae (`R_Hair*`, `L_Hair*`) and eyes.
- Animations audited: run, idle1, attack1, spell4.

## Versions

| Version | Date | What changed | Picture |
| --- | --- | --- | --- |
| v1 | 2026-10-22 | Piano texture, 23-key keyboard down his back with gold rails, tail note (~2.2k tris) | `previews/pianomaw_original_v1.jpg`, `previews/pianomaw_v1_turntable.jpg`, `previews/pianomaw_v1_keyboard.jpg` |
| v2 | 2026-10-22 | Smoother key bed (crest and key heights averaged over a carapace band), keyboard ends before the tail where keys slid; 17 white + 12 black keys, ~1.7k tris | `previews/pianomaw_v1_v2.jpg`, `previews/pianomaw_v2_turntable.jpg`, `previews/pianomaw_v2_game.jpg` |

Dropped: gold ankle cuffs (v1 draft). Sized to the leg they read as loose hoops, not piano brass.

## Audit (v2)

- Stretch at most 1.22 (keys share one set of weights per key, `FOLLOW = 0`).
- Keys sink into the carapace on some frames of attack and ult: worst white key 6 cm, two
  black keys 9 cm (they are 9.5 cm tall). Reads as a pressed key; watch it in game.
- Per-vertex weights (`FOLLOW > 0`) were tried and rejected: his carapace squashes so much
  that keys stretched up to 7x.

## In game

- Not yet. Export follows Mossmaw's `mossmaw_export.py` once the look is settled.
