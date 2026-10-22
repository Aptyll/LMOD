# Mossmaw (Kog'Maw)

**Concept:** Kog'Maw, long asleep in a swamp, wakes up overgrown.

**Status:** polish v3 done, export to League format in progress (2026-10-22).

Recipe: `scripts/mossmaw.py` plus the `moss` recipe in `scripts/recolor_texture.py` (PR #2).
Working file on Noah's PC: `C:\NoahCode\LMOD\work\mossmaw.blend` (v1 and v2 copies beside it).

## Brief

- Pillars: moss-green hide, lichen-covered stone skull, thick moss mats that change his
  silhouette, hanging strands from the jaw and antennae, mushrooms with one glowing cluster.
- Keep untouched: eyes and mouth clear and readable.
- Added geometry: about 5.7k triangles in v3 (down from about 9.3k in v2).

## Champion notes

- Champion id 96. WAD: `DATA/FINAL/Champions/KogMaw.wad.client`, skin path
  `assets/characters/kogmaw/skins/base/`.
- Skeleton: 70 bones. Body mesh object `KogMaw`, armature `KogMaw_Rig`.
- Growth bones: head top, chest, waist, tail chain, upper arms. Jaw: `C_Mouth*` undersides.
  Antennae: `R_Hair*`, `L_Hair*`. Legs: thighs, knees, feet. Eyes kept clear by 0.32 m.
- Animations audited: run, idle1, attack1, spell4.

## Versions

| Version | Date | What changed | Picture |
| --- | --- | --- | --- |
| v1 | 2026-10-22 | Moss texture, 23 clumps, mushrooms and ferns | `previews/mossmaw_hero.jpg` |
| v2 | 2026-10-22 | Overgrown: 130 pieces, mats, strands, sprouts, grass | `previews/mossmaw_v2_hero.jpg`, `previews/mossmaw_v1_v2.jpg` |
| v3 | 2026-10-22 | Shaped mushrooms, glowing cluster, 5 moss shades, moss sits into hide, ~5.7k tris | `previews/mossmaw_v3_hero.jpg`, `previews/mossmaw_v3_turntable.jpg` |

## In game

- Not yet. Plan: bake the glow into the texture, export on the original skeleton, package
  in `mods\mossmaw\`, load with LTK Manager, test in Practice Tool.
