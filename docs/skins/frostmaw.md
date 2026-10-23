# FrostMaw (Kog'Maw)

**Concept:** a Freljord ice beast. Crystals grow from his back, icicles from his jaw.

**Status:** v2 exported, waiting for a Practice Tool test (2026-10-23).

Recipe: `scripts/frostmaw.py` (texture, props, export, game-culled previews in one run).
Working files on Noah's PC: `C:\NoahCode\LMOD\work\frostmaw\` (`frostmaw_vN.blend`, `export_vN\`,
`previews\`, run copies of the scripts in `scripts\`). Mod: `mods\frostmaw\FrostMaw.fantome`.

    blender -b base\kogmaw\kogmaw_rigged.blend --python work\frostmaw\scripts\frostmaw.py -- work\frostmaw\previews v2

## Brief

- Pillars: frosted white skull, deep-blue hide with glowing cyan veins, violet mouth, cyan
  eyes, ice crystal clusters and a shard ridge on his back, icicles under his jaw, crystals
  on his antenna tips.
- Keep untouched: eyes and mouth readable, silhouette at game zoom.
- Added geometry: about 1.6k triangles (81 shards in 32 pieces).

## Built for the game

- **Every prop is a closed, convex shard** with outward winding by construction (no cards, no
  open shells, no trimmed bottoms). The build checks it (`outward`, `open_edges`).
- **The export flips props to match Riot's body.** Aventurine mirrors positions but keeps
  winding, so in Blender the body's faces and normals both point in. The export reverses each
  prop's faces and lets its normals follow, like the body. The report's `winding` block must read
  `props_inward: 1.0` and `normals_follow_winding.props: 1.0`. bmesh's `recalc_face_normals`
  is not used, because it flipped about one shard in four on these pointed shells.
- **Props are coloured from the body texture.** Two colour ramps (crystal, icicle) are painted
  into free background strips (96x32 px) and each shard's UVs run root to tip along a ramp, so
  mips only blur the ramp's ends.
- **Previews are the export.** Every picture renders a copy of the exported mesh with all faces
  reversed and normals negated, with backface culling on: what Blender draws is what League draws,
  except for lighting. The game strip uses League's camera (about 56 degrees down, far, long lens)
  with him facing four ways.

## Champion notes

- Growth: shard ridge along the back crest (`pianomaw.crest`), clusters on `TOP_BONES` away from
  the midline, icicles under `JAW_BONES`, clusters inside the antenna tips (`ANTENNA_TIPS`).
- Skinning: each piece copies the four strongest bone weights of the skin vertex it grows from.
- Animations audited: run, idle1, attack1, spell4. Worst stretch 1.10.

## Versions

| Version | Date | What changed | Picture |
| --- | --- | --- | --- |
| v1 | 2026-10-23 | Frost texture, 33 pieces, first game-culled previews | `previews/frostmaw_v1_hero.jpg` |
| v2 | 2026-10-23 | Bigger, brighter crystals, thinner veins, every shard verified to face out after export | `previews/frostmaw_original_v2.jpg`, `previews/frostmaw_v2_game.jpg` |
| art | 2026-10-23 | Champion icon, loading screen, frost ability icons | `previews/frostmaw_art_v1.jpg`, `previews/frostmaw_ability_icons.jpg` |

## In game

- Not yet tested. Last built on patch 16.21.
- The mod also carries the art: frost ability icons (`scripts/frost_icons.py`, from the icons in
  `base\kogmaw\ui\`), the champion icon (square and round) and a loading-screen portrait
  (`scripts/frostmaw_art.py`). The game paths are the standard base-skin ones in
  `frostmaw.GAME_ART` and `frostmaw.ICON_DIR`; if one is wrong, that file is just ignored.
  Repackage without a rebuild: `blender -b --python frostmaw.py -- package EXPORT_DIR ART_DIR ICONS_DIR`.
- A W animation is planned. It needs a copy of `KogMaw.wad.client` to find W's animation first.
