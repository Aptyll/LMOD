# Mossmaw (Kog'Maw)

**Concept:** Kog'Maw, long asleep in a swamp, wakes up overgrown.

**Status:** v3 in game as mod v1.1.0 with a custom loading-screen portrait (2026-10-23).

Recipe: `scripts/mossmaw.py` plus the `moss` recipe in `scripts/recolor_texture.py`; export in
`scripts/mossmaw_export.py`, portrait in `scripts/mossmaw_portrait.py`.
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

- `mods\Mossmaw.fantome`, v1.1.0, loaded in LTK Manager (healthy on 16.21). It replaces five
  files in `KogMaw.wad.client`: `kogmaw.skn`, `kogmaw_base_tx_cm.tex`,
  `kogmawloadscreen.tex` (308 x 560), `hud/kogmaw_circle_0.tex` and `hud/kogmaw_square_0.tex` (128 x 128).
- v1.0.0 looked see-through in game: Aventurine keeps winding while mirroring positions, so
  the moss came out inside-out and the game culled its outer faces. v1.1.0 fixes the winding,
  closes the cushion shells and makes cards double-sided (about 10.9k triangles in all).
  Picture: `previews/mossmaw_bug_before_after.jpg`.
- Loading-screen portrait: attack1 frame 14 in a fogged forest set
  (`previews/mossmaw_portrait.jpg`, `previews/mossmaw_loadscreen.jpg`, `previews/mossmaw_icons.jpg`).
