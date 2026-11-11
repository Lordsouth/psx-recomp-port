# Internals

How this port's enhancements actually work: what was built, where it lives in
the game, and how to verify or regenerate any of it.

If you only want to play, read [README.md](../README.md).
If you want to build the port, read [DEVELOPING.md](../DEVELOPING.md).

---

## 1. What this project is

`psxrecomp` translates the game's MIPS code into C ahead of time and compiles
it into a native Windows program. That gives three execution tiers — statically
recompiled functions, background-compiled native overlay shards, and an
interpreter as the safety net — and it is why enhancements here can do things a
patch on an emulator cannot: we can hook a guest function by address, allocate
memory the console never had, and change data the game reads, all while the
rest of the game runs natively.

The project ships **no game content**. Every enhancement is a guarded byte patch
expressed in a text manifest, native C in this repository that runs against the
player's own disc, or a map file built from our own scene and art (§3.2).

**Two golden rules, learned the hard way:**

1. **Never ship a patch or code whose target has not been identified in the
   decompilation.** Guessing an address produces a cheat that silently does
   nothing, or corrupts a save. Several inherited codes were wrong exactly this
   way (§6.3).
2. **Overlay RAM is shared.** The game loads sixteen overlays into nine reused
   memory slots, so a per-frame write into an overlay's address range will
   corrupt whichever overlay happens to be resident. Overlay changes must be
   **disc patches**, never RAM pokes.

---

## 2. The two enhancement mechanisms

### 2.1 Declarative patches (`.psxmod` manifests)

A manifest is TOML listing features and guarded byte writes:

```toml
[[patch]]
feature = "no-hunger"
target  = "main_exe"          # or disc_user / disc_raw
address = 0x800A75DC
expected = "e8 ff bd 27 10 00 bf af"    # must match, or nothing applies
replace  = "08 00 e0 03 00 00 00 00"    # jr $ra ; nop
```

Every write is equal-length and guarded: if `expected` does not match the
player's disc, the patch is refused. Turning a feature off restores the
original bytes exactly.

Three target types matter here:

| Target | Meaning | Used for |
|---|---|---|
| `main_exe` | the loaded PS-EXE image in guest RAM | code and data in `SLUS_010.32` |
| `disc_user` | a byte offset into the disc's user data | overlay files (`*_REL.BIN`) |
| `disc_raw` | raw sector bytes | unused here |

**Disc offset arithmetic** (verified against the physical image):

```
disc_user_offset = lba * 2048 + (vaddr - overlay_load_base)
```

For the training overlays: `TRN_REL.BIN` LBA **148248**, `TRN2_REL.BIN` LBA
**148262**, both loading flat at base **0x80088800**. A disc patch may not
cross a 2048-byte sector boundary — the generators check this.

**Failure mode worth knowing:** if two *enabled* features write the same byte
with different values, the runtime discards the **entire** mod plan — the
player loses every mod including widescreen, with an error naming two features
they cannot be expected to connect. `tools/check_mod_collisions.py` exists
because this happened (§7.2).

### 2.2 Native plugins (C in this repository)

A manifest can also select a statically-linked plugin by id. The plugin gets:

* an **activation callback** before the game boots (host configuration, memory
  reservation),
* a **per-VBlank callback** with full guest-RAM access,
* **function-entry hooks** on any guest address listed in `game.toml`'s
  `[recompiler] mod_function_entry_funcs`, receiving the CPU state — so we can
  read and rewrite argument registers as a guest function is called,
* **function-exit hooks** (`mod_function_exit_funcs`), which fire at the
  function's own `jr $ra`. For state a plugin must perturb for exactly one
  call and then put back,
* **whole-function replacement** (`mod_function_replace_funcs`): the callback
  returns nonzero to claim the call, having written `$v0` and published `$ra`
  into `cpu->pc`. Never combine this with a code patch on the same function —
  patching marks the range executable-dirty, dispatch then refuses the
  compiled body, and the hook lives inside the body that was refused,
* an **Expansion-1 arena**: up to 1 MB of memory the real console never had,
  wired into every generated load/store path. The first allocation is
  deterministically `0x9F000000`, which is what lets a manifest hard-code
  addresses into it.

**A plugin's activation and VBlank callbacks only run if a manifest selects it**
with a `[[plugin]]` block. So do function-entry hooks registered under a plugin
id (`build_function_entry_hooks` only walks the plan's plugins); exit and
replacement hooks fire regardless. Entry hooks used to fire regardless too;
since the framework update (upstream `199cfe01`) they are plan-selected, and
widescreen's `dw.ws.backdrop-align`, `dw.ws.hud-park` and `dw.ws.tile-ladder`
silently stopped running until its manifest named them (0.4.0). That
asymmetry has now cost three evenings: the first time an
activation callback silently did nothing, the second time a VBlank callback
did, and the only symptom was a table in guest RAM still reading its stock
value in a live battle. If a callback appears not to exist, check the manifest
before the code.

Eleven plugins live here:

| File | What it does |
|---|---|
| `dw_roster.c` | the Expansion-1 arena and the grown tables (§4.1) |
| `dw_roster_flags.c` | re-banks the new species' raised flags off the game's story flags (§4.1) |
| `dw_evolution.c` | proof of the replacement primitive; ships disabled |
| `dw_ws_backdrop.c` | fade plate, parked HUD, the clock and field gauges as single widgets, the battle bars' slide table (§5) |
| `dw_ws_camera.c` | both camera clamps, the tile-ladder shift, the load-time nudge (§5) |
| `dw_ws_sliver.c` | the fifth backdrop tile column, as a VRAM sliver (§5) |
| `dw_ws_world.c` | keeps a field frame wide through a synchronous tile reload (§5.0c) |
| `dw_ws_weather.c` | copies snow and mist into the reveals (§5.0d) |
| `dw_cheats.c` | the cheat list as launcher toggles; the codes stay in `cheats.txt` (§6) |
| `dw_dw15.c` | moves the memory cards to `saves/dw15/` while Digimon World 1.5 is on (§4.4) |
| `dw_newmaps.c` | keeps a new map out of a memory card's screen bytes: a save made there names its host map (§3.2) |

`codegen_setup.c` is glue for the launcher build, not a plugin.

---

## 3. Map of the game

[GAME.md](GAME.md) is the reference for how the game works. This document is about
the port.

Addresses verified against `dw_decomp` symbols and recomputed from the struct
definitions. The EXE loads at `0x80090800`; `file_offset = vaddr - 0x80090800 + 0x800`.
Citations like `map.c:4265` in this document are `dw_decomp` commit `aaf69a4`.

### Core tables

| Symbol | Address | Shape |
|---|---|---|
| `DIGIMON_DATA` | `0x8012CEB4` | 180 × 0x34 — name, bones, radius/height, attribute, level, drop item/chance, 16 moves |
| `EVOLUTION_REQS` (`EVO_REQ_DATA` in the port's tools and below) | `0x8012ABEC` | 63 × 0x1C — evolution requirements, **indexed by target type** |
| `RAISE_DATA` | `0x801225BC` | 66 × 0x1C — meal times, energy, poop, favourite food, camera |
| `EVOLUTION_STATS_GAINS` (`EVO_GAINS_DATA`) | `0x8012B2D0` | 66 × 0x0E — stat gains on evolving |
| `EVOLUTION_PATHS` (`EVO_PATHS_DATA`) | `0x8012B66C` | 62 × 0x0B — `int8 from[5], to[6]`, `-1` terminated |
| `DIGIMON_SKELETONS` | `0x8011CE60` | 180 pointers |
| `PTR_DIGIMON_FILE_NAMES` | `0x8011D19C` | 180 pointers into the model-code string pool `0x80133B44` |
| `ORIGINAL_DIGIMON_TYPES` (`ORIGINAL_TYPE`) | `0x8012A84C` | 180 × int16 — battle-aura family, and the key every tech's EFE list is searched with: a row must be -1 (the species itself) or a type on the lists of its moves, or those moves abort their effect script |
| `ITEM_PARA` | `0x801269DC` | 128 × 0x20 |
| `MOVE_LEARN_CHANCES` | `0x80125FA4` | 58 × 3 — per **move** and per partner special slot, not per type |

### Live state

| Symbol | Address | Notes |
|---|---|---|
| `PARTNER_PARA` | `0x80138460` | struct size 0x84 (confirmed: the next symbol sits exactly 0x84 later) |
| `PARTNER_ENTITY` | `0x801557A8` | `.type` is an int32 at +0x00 |
| `SCRIPT_STATE` | `0x801BDED8` | `smth[212]`, `cards[33]`, `triggers[100]`, `pstats[256]` |
| `COMBAT_DATA` | `0x8013D640` | fighter stride 0x168; invuln timer +0x26, cooldown +0x28 |
| `NPC_ENTITIES` | `0x80155828` | 8 × 0x68; `bits` at +0x60 |
| `IS_GAMETIME_RUNNING` | `0x80134E20` | one byte; zero stops the hunger, tiredness, ageing and lifespan ticks. Battles and training apply their own lumps of time, food, poop and lifetime regardless (`tamer.c:885-965`, `trn_reward.c:721-804`) |

Selected `PARTNER_PARA` offsets, each recomputed by walking the struct:

```
+0x00 condition (u32: 0x01 sleepy, 0x02 tired, 0x04 hungry, 0x08 poopy,
                 0x10 unhappy, 0x20 injured, 0x40 sick; dw/params.h)
+0x22 tiredness      +0x3C energyLevel     +0x48 remainingLifetime
+0x4C trainBoostFlag +0x4E trainBoostValue +0x50 trainBoostTimer
+0x5E fishCaught     +0x72 upgradeBrainCounter
```

`SCRIPT_STATE` sub-arrays: recycle-shop stock `0x801BDEDE` (`smth[6..83]`, 78
bytes, by `RECYCLABLE_ITEMS` index; items lost on defeat land there), cards
`0x801BDFAC`, triggers `0x801BDFCD` (bit N is byte `base + N/8`, mask
`1 << (N%8)`), pstats `0x801BE031`. Recruitment uses trigger `0xC8 + digimonId`
— and two of those flags *are* the gym upgrades (Kabuterimon → HP/Def/Speed,
Kuwagamon → Off/MP/Brain).

### 3.1 New maps on free rows (M0 spike, verified in game)

`MAP_ENTRIES` (`0x801292D4`, 255 × 16: `filename[10]`, n8bpp, n4bpp, flags,
doorsId, toiletId, loadingName) has 11 empty rows: 239–246 and 250–252. A
screen id is a u8, so these 11 are all the empty rows the stock engine offers
without a hook layer. Rows 50 and 61 name files not on the disc and row 65 names
row 232's file. No warp reaches those three, so they may be reusable, though two
`TELEPORT` ops were not traced. `gen_newmap_spike.py` fills row 239 with the unused Mt. Infinity floor
MGEN17. `verify_newmap.py --map spike` then walks in through a stock exit, wins
a battle, saves on 239, walks out, relaunches, and continues onto 239. Both
runs pass. §3.2 puts an authored map on the same row.

What a new row needs, each verified in that run:

- **The row.** The MAP and TFS, like every read through `readFile`, resolve by basename
  only (`lookupFileTable`), so the `\MAP\MAPnn\` folder `buildMapPath` builds never
  matters for them. `DG.SCN`, async overlays, `BOSS_EFE.TMD`, EFE files and movies
  use `CdSearchFile` on the ISO directory instead, and `SHOP_REL` loads both ways.
  n8/n4 must match the
  MAP header word count (3 words means 0/0). Keep flag `0x40` when the TFS has a
  single CLUT, because the day/night swap reads `MAP_CLUTS[1..2]`. Set `0x80` for
  map Digimon. The low 5 bits are the sound bank (< 22). loadingName must be < 70.
- **Its MAPHEAD.SCN section.** `changeMap` runs MAPHEAD section *mapId*, and
  its `FB` op picks the DG script and loads the map. All free rows already have
  a 14-byte filler that binds **script 0xB4, the Factorial Town sewer**. Its
  arrival section plays the fog cutscene and sets T340 (so the real sewer's
  intro never plays). With T341 set and T211 clear it starts the Numemon
  battle. Never ship a free row on the filler. The spike moves the section
  into the zero tail of the file's last sector (MAPHEAD ends at `0x606E`, and
  the buffer is `0x61A8`) and repoints its table entry.
- **Exits.** A MAP's entity block starts with `MapWarps`: six parallel `i16[10]`
  arrays (spawn x, y, z, rotation, targetMap, targetExit), so slot n's fields are
  10 entries apart. Collision is 100×100 bytes. 0 is floor, any byte with bit 7
  is wall, and 110+n fires warp n. 51–79 run a DG section on step and 80–109 only
  on a fresh confirm press; a missing section makes the script pointer NULL. 120
  runs MAPHEAD section `0x4E2` when the partner needs the toilet, and 1–50 and
  121–127 do nothing. MGEN17's spawn 2 is inside a wall.
- **Wild battles** need three parts:
  - map-Digimon records after the 120 bytes of `MapWarps` and an i16 Digimon
    count, which is read only with flag `0x80` (84 bytes plus 6 per waypoint,
    layout in `loadMapDigimon`)
  - `LOAD_DIGIMON` and `SET_DIGIMON type slot autotalk` in the MAPHEAD section
  - a DG section numbered by the record's scriptId that runs `START_BATTLE`

  Contact with autotalk 1 runs that section (autotalk 0 needs a confirm press).
  trackingRange only drives looking and chasing, and starts no fight: 385 stock
  records have 0.

  The spike borrows MGEN11's script `0xB9` and a weakened copy of its Piddomon
  record. That script spawns MGEN11's chest from its arrival section and sets
  trigger 0x11D after the fight. That is fine for a spike, but an authored map
  needs its own DG script.
- **Room to grow.** `readFile` loads whole sectors, so the zero tail of a
  file's last sector is free space. The spike puts MGEN17's new entity block
  there and changes only the header offset. Nothing in `FILE_OFFSET_TABLE`
  changes.
- **Saves** store the screen, the exit and the exact position. Continue reruns
  the MAPHEAD section. A save made on a new row cannot load once the mod is
  off, so a shipped map must keep the row out of the cards' screen bytes (§3.2).

The generated manifest copies stock bytes (MGEN17's warps, MGEN11's record).
It is written into `build/mods/installed/` and never committed. An authored
map ships its own MAP/TFS, MAPHEAD section and DG script, and needs none of those
bytes.

### 3.2 An authored map (M1, verified in game)

`gen_newmap.py` builds the "Hidden Glade" from a seeded scene described in
code: a wobbly clearing walled in by foliage masses, trees at its back edge, a
bush island, rocks, a stump, a fallen log, a three-lobed pond and a dirt path,
all in world units. The scene drives every output, so they cannot drift apart:

- **The guide and label map.** The scene is ray-traced from the map's own
  camera with the engine's projection, in flat material colours with sun, sky
  and cast shadows (`build/newmap/guide.png`), plus a per-pixel material map
  (grass, earth, water, foliage, rock, wood).
- **The backdrop.** `paint_newmap.py` paints the guide into finished art with
  local models and writes `art/newmaps/glade.png` (committed, with its seeds
  and prompts in `glade.json`). `gen_newmap.py` checks the art against the
  label map: a per-material Gaussian classifier, fitted on the painting itself
  where the scene's label is unambiguous, must find ground where the scene has
  ground (IoU at least 0.75). The art is then quantised to 255 15-bit colours
  (k-means in Lab over a sample stratified by material, so the rocks and pond
  get colours too) and tinted into the dusk and night palettes. Index 0 is
  reserved for `0x0000`, the transparent texel. The art is painted in a 6×5
  frame (768×640) and the backdrop is its 6×4 centre crop (768×512): six fewer
  tiles leave room for the foreground sprites' image (below). Without painted
  art the flat guide is used.
- **The collision grid.** Floor follows the scene: the clearing and the path,
  minus the pond, foliage and tree footprints and anything outside the painted
  frame. Then the painting decides: a cell stays floor only if the art shows
  ground at 7 of 9 points of a character's footprint. Painted non-ground
  unprojected onto the floor covers a prop's base and the floor hidden behind
  it, so this keeps characters from walking behind anything without a sprite,
  because backdrops always draw behind characters. Foliage and trunks have
  sprites: the next bullet lets characters behind them. The small props (rocks,
  stump, log) have none, and take their collision from the painting alone,
  because the painter may nudge them.
- **Foreground sprites** let characters walk behind the foliage, as on the
  stock maps. Scene foliage and trunks that the painting shows (grown 6 px into
  the painter's leaves) are cut into sprites per clump, but only where they
  overlap a character's box on some walkable cell. Each sprite is drawn over
  characters by depth. Behind a bush, a character may stand under its overhang,
  down to the patch where it touches the floor, and the sprite hides their
  legs. In front, the bush's whole width stays blocked. Each walkable cell is
  sampled over a character's box (378 points, about pixel pitch) and must order
  cleanly against every sprite it overlaps: a point the sprite's surface hides
  needs the sprite's anchor in front (depth − 100), and a visible point on
  sprite pixels needs it behind (depth + 100). Each clump's anchor is the depth
  that satisfies the most cells, and cells that no anchor satisfies stay
  blocked. After the sprites are cut, a dense check (5×10 units, finer than a
  pixel) blocks any cell that would still be drawn wrong. An independent
  review found the thin sprite edges the coarser sampling missed.
- **The MAP.** It holds the setup block (camera, the MAYO maps' character
  lights, a 6×4 grid), the sprites' 8bpp images and object block, and the warp
  table.

**The MAP object section** (`loadMap`, `loadMapObjects`, `renderMapOverlays`)
is read only when `MAP_ENTRIES` n8 or n4 is nonzero. The header words are then:

```
setup, n8 x 8bpp TIM, n4 x 4bpp TIM, objects, entities, collision
```

- Each 8bpp TIM is uploaded at its pixel rect. The stock maps' commonest shape is
  `(384 + 128k, 0)`, 128 halfwords wide (k = 0..2). Some are 64, 256, 320 or 384
  wide, all inside x 384-767, y 0-255 (map tiles stream at x 768 and up).
- A CLUT rect at row 480 (`0x1E0`) is not uploaded. The sprite then samples the
  live palette, so dusk and night apply to it.
- The object block is `i16 nDefs`, then `nDefs` × 9 `i16` (texX, texY, w, h,
  anchor x, y, z, clut, trans), then `i16 nInst`, then `nInst` × 19 `i16`
  (8 sprite ids, 8 frame times, x, y, flag; at most 188). Flag 1 hides an
  instance and is the cleared default.
- texX/256 picks the texture page.
- clut −1 means 8bpp on row 480, 0–15 means 4bpp at `(16*clut, 486)`, and 16 and
  up means 8bpp on row `clut + 468`. trans 4 means opaque.
- x, y is the backdrop pixel of the sprite's top-left corner.
- The OT slot is the anchor's GTE screen z >> 2, the same scale
  `GsSortObject4J` uses for models (shift 2). That is what puts a sprite in
  front of or behind a character. Its minimum is `0x23`, `0x1000` and up becomes
  `0xFF6`, an anchor y of `0x2710` forces `0xFFE`, and instance flag `0x80` forces
  `0x1E` (`overworld.c:449-462`).

The painter (`paint_newmap.py`, ComfyUI through the studio's `comfy.py`
client):
1. **Qwen-Image-Edit 2511** (Q4 GGUF, Lightning 4-step) reads the guide as a
   blockout and renders a detailed 3D scene with every outline kept.
2. **Z-Image-Turbo img2img** enters at step 4 of 8 and adds real materials.
   Step 5 adds too little and step 3 redesigns the layout.
3. **Masked Z-Image** repaints only the scene's foliage (eroded) at step 2,
   with a photographic prompt at 1920×1600, so the leaves land at the stock
   maps' 3–6 px. The clearing is untouched by the mask.
4. **Ground textures.** At backdrop size, a generated grass and a generated
   dirt texture are laid onto the painted ground in the camera's perspective as
   luminance detail, the way the 1999 pre-renders were textured.
5. **Grade and tone.** A grade moves the colour statistics half way toward the
   stock Native Forest maps' Lab mean and standard deviation. A per-material
   tone pass sets turf to the stock olive at half the blade contrast, with
   single-pixel rust and dark-green flecks, sets earth to a darker brown, and
   makes the foliage calmer.

No pixel of the game is read; the targets are a few numbers measured on the
stock maps. Seeds reproduce bit-exactly on the same models and GPU. Plain
prompting alone gives modern stylised art (glossy rosette bushes, smooth
painted grass): blind judges scored those 3–5/10 for style match before steps
3–5 were added.

The camera model, checked against the stock art of all 242 maps:

```
vp = 2*(s0,s1,s2)   vr = 2*(s3,s4,s5)   h = s27   W,H = s36,s37   (s = setup i32 words)
f = norm(vr - vp)   r = norm((f.z, 0, -f.x))   d = f x r          (world +Y down, floor y = 0)
q = P - vp          u = 64W + h*(q.r)/(q.f)    v = 64H + h*(q.d)/(q.f)   (stitched backdrop pixel)
```

Camera follow never changes the view. It scrolls the backdrop and the 3D
layer together, keeping `cameraX + DRAWING_OFFSET_X = 64W`.

The TFS layout is a `u32` the game skips (`W|H<<16` on most stock maps, but not on
13, MAYO10 and TROP03 among them), `u32 nCLUT`, the CLUTs, then for each grid slot
that is not -1 a `u32` the game skips and 128×128 8bpp pixels. A -1 slot takes no
bytes. The file must stay within
241 sectors (`0x78800`), the window from `GENERAL_BUFFER` up to
`TEXTURE_BUFFER`, which allows 30 tiles with 3 CLUTs. Flag `0x40` is needed
when there is only 1 CLUT. Avoid -1 grid slots: `updateTileRow` checks the
wrong row and can blank real tiles.

**Delivery** needs no runtime change. Two file-backed `disc_user` `[[overlay]]`s
lay our MAP and TFS across MGEN17's 247 adjacent sectors (MAP, then TFS, then
zero padding), and a third lays the DG script over the start of slot 118. Two guarded `main_exe` patches move the files'
`FILE_OFFSET_TABLE` entries (`0x8011FE1C`, 12 bytes each: sector, size, name
pointer; `lookupFileTable` resolves names there) to match. Overlays need
`disc_sha256` on the `[[target]]`. Past the end of the disc a `disc_user`
overlay reads as zeros. The disc does hold free space: `MMM.DAT` (LBA 148668) is
13,500 all-zero sectors that nothing reads. But that route has never been run in
game (`docs/GAME.md` 2.3), so the glade stays on MGEN17's own sectors: a 30-tile
TFS leaves 2 KB, and the 24-tile backdrop leaves room for 2 texture pages.
The row, the MAPHEAD section and the entrance are small guarded patches. The DG
script is a file because a patch must repeat the stock bytes it replaces, and those
are live script bytecode; `disc_sha256` and the generator's asserts guard it.

**On 1.5** the glade runs its edition `glade-15` (§4.4, 1.5 editions), written by
`tools/gen_newmap_dw15.py` below a marker in the manifest; `gen_newmap.py` keeps that
block when it rewrites the part above. 1.5 leaves row 239, MGEN17's sectors, DG slot
118 and MAYO03's warp slot 4 free but moves three things: its stub copies the file
table to `0x801F1000` (MGEN17's entries at `0x801F268C`), DG.SCN grew and sits past
the stock end at LBA 162388 (slot 118 is rewritten there as a guarded `[[patch]]`,
since the runtime refuses an overlay on a delta sector), and 1.5's own new MAPHEAD
sections fill the tail to `0x60DE`, so the glade's section goes at `0x60DE`. The
other writes are the glade's own, their guards checked against 1.5. The edition sits
in `dw.newmaps` and reaches `dw15` through a `[[requirement]]`, which the runtime
accepts for writes on delta sectors. Checked in game (2026-11-11): `run1 --dw15`
passed every check, the save on a 1.5 card included.

**The secret fight.** The MAP's entity block has one record, H-Kabuterimon, which
the game places nowhere, at the end of the dirt path: HP 8800, MP 16000, Off 820,
Def 640, Speed 520, Brains 400 (`BOSS` in `gen_newmap.py`). `tools/fight_sim.py` puts
it between the game's second-hardest fight and its final boss, and real unassisted
fights agree that it is hard (`docs/GAME.md` 7.17, 7.18).
`followMode` 2 turns it to face
the player without walking, and autotalk 0 means only a confirm press next to it
starts the fight, so no one walks into it by accident. Its hit radius is wide: the
tamer stops about 400 units from its centre.

DG script slot 118 has room for its script. Stock, the slot holds one bare END
section, and the rest of its 2 KB is junk nothing reads. Row 122 (TUNN07_2) binds
the same slot, but has no map Digimon and no script tiles, so it only ever runs
the END. `boss_script()` keeps that END as the arrival section and adds section 5:
pause, the boss's two lines, close the box, wait a frame, `START_BATTLE`. Fleeing
stays allowed, which no stock press-to-fight battle does, but it shares the code
path of any other fled battle.

The fight itself sets no triggers. A loss writes what any stock loss writes:
`MAPHEAD` section 0x4DE sets trigger 0x29 (the next map load clears it), and lives
and the battle counters change. The boss is back on every visit: winning only
clears `isOnMap` until the map next loads. The outcomes:

| Outcome | What happens |
|---|---|
| Win | It runs off along the bearing to (3400, 0, 1150), read once: east into the foliage, unless the fight ended east of that. It pays 5000 bits, and the map's art and collision are as they were. Happiness +2, tiredness +5 |
| Flee | `PREVIOUS_SCREEN`/`PREVIOUS_EXIT` send the player to the host's side of the entrance. Happiness -10, discipline -6, tiredness +7 |
| Lose | Happiness -30, discipline -20, `count*30/100` of the filled item slots and one life, then Jijimon's house, as `MAPHEAD` section 0x4DE decides |

Every outcome also moves the clock 20 minutes and runs down food and the poop timer.
`docs/GAME.md` 7.14 has the full table.

`verify_newmap.py --map glade` proves the game loaded our files, from memory:
- our TFS is resident in `GENERAL_BUFFER`
- our collision grid is in `MAP_COLLISION_DATA`
- our camera is in `GS_VIEWPOINT`
- our palette is in VRAM row 480, and our dusk and night palettes go live at
  16:00 and 20:00
- our foreground sprites are loaded (`MAP_OBJECT_INSTANCE_COUNT`) and their
  texels are in VRAM
- standing behind the bush island, the screenshot shows the bush, not the
  tamer, under the sprite, and the tamer above it

It also enters through the new exit, walks the meadow (targets the generator
picks from the fitted grid, in `spec.json`), pushes into the pond (the tamer stays
ashore), saves on the map, and walks out. Run 2 continues from that save, which
lands just outside the entrance, walks back in and out, and saves next door. The
`off` mode then loads both cards with the mod off, on the stock game. Screenshots
taken against our own backdrop under the live camera match best at offset (0,0) or
within 1 px. It tests the bundled package, so rebuild after regenerating: it stops
if the build's copy differs from `mods/preloaded/packages`.

The `fight` mode plays the secret fight on a fresh card, with a maxed partner so
the outcome is the one under test: standing next to the boss starts nothing,
confirm opens its dialogue, the boss uses all four moves in a real battle (the test
rewrites both sides' HP and MP on every poll, so it is not an unassisted fight), and
then it is beaten (forced to 1 HP first), fled from and lost to in turn. After the win it repeats the map
checks above and saves, and it comes back at full strength on the next visit. It
last visits row 122. `--wide` runs the same at 21:9 with the Expanded Roster on.
It does not judge the difficulty.

**The entrance** adds an exit and takes none away. On MAYO03 (row 2, Native
Forest), floor reaches a notch in the north edge under a lit lane between the
bushes. MapWarps slot 4 (free in stock) gets the return spawn and targets row 239,
and collision rows 22-23, columns 62-67 become warp 4 (`0x72`). Row 22 is stock
wall and must change too: the wall test runs a radius (100) ahead of the tamer, so
his centre never stands in a row touching a wall, and row 23 alone would not fire.
That is why stock warp strips are two rows deep. MAYO03 is the only row using its
file, so no story variant needs the same edit. The `_2` map files are a trap:
MAYO01_2 and MAYO02_2 (rows 110/111) are variants of each other, the screen between
MAYO01 and MAYO00, picked by trigger 106.

**Saves.** `dw_newmaps.c` keeps row 239 out of every card's screen bytes, so with
the mod off, every card still loads. A poop pile dropped on the glade would also save
its map in the card's pile table (`partner_impl.c:1283`, `main_menu.c:5108`), so the
hook writes a free slot there instead: the pile stays in play and is gone after
Continue. Every save of a game in play goes through `writeSavegame`
and is then summed by `createSavegameChecksum` (`0x80112D34`, a function entry
hook filtered by return address, because the load paths sum too, and so does
new-file creation, which writes the default payload without `writeSavegame`). The
hook runs between the two and rewrites only the save buffer, so play goes on where
it is:
- A save made on 239 names the host, a stock exit and the spawn just outside the
  entrance, read from slot 1 of the map's own warp table (no cell is 111, so it
  never fires as a warp). The position, partner position and waypoint tiles move
  with it, because Continue takes the position from the save, not from the exit.
- `previousScreen` is the second way to brick a card: fleeing a battle goes there,
  so walking out of the glade and saving next door would store 239. It gets the
  current screen instead.
- Exit 4 on the host becomes stock exit 0: slot 4's spawn is empty on the stock map.

A separate card folder (`dw_dw15.c`) was the simpler choice and the wrong one: it
would hide a player's existing run behind an optional side area.
`save_compatibility = "isolated"` is still set, for savestates: a state taken on
239 holds the patched row and file table in RAM, and must not load without the plan
that serves the overlays.

**The directory records move too.** The game finds the MAP and TFS through
`FILE_OFFSET_TABLE`, but `dw_ws_sliver.c` reads the TFS through the ISO directory
(`psx_mod_read_disc_file`). With only the table patched it read our MAP as a TFS,
its bank failed, and 21:9 showed black at the map's edges. So both records are
patched, as `mod_runtime.cpp` documents for relocating a file. New rows beyond 239
would hit a second trap there: it builds `\MAP\MAP<id/15+1>\`, and MGEN17 only
happens to live in MAP16.

**It ships** in `mods/preloaded/packages/dw.newmaps/1.0.0/`, with `glade.map`,
`glade.tfs` and `glade.dg` on `verify_setup_package.py`'s allowlist. It is the first package with
a `disc_sha256` on its `[[target]]`, so on any other image ticking it refuses the
plan. A stale `build*/mods/installed/dw.newmaps` silently shadows the bundled copy
on a dev machine: delete it. None of the release gates reads `[[overlay]]`, so an
overlap with one only surfaces when the runtime resolves the plan.

Still to do:
- NPCs and items. Anything the DG script sets persists on shared cards and must
  be inert with the mod off. Audit trigger ids with `tools/scan_trigger_ids.py`, but
  it sees only scripts, so it calls the bands C owns free (`docs/GAME.md` 4.8). Ids
  627-639 and 641-649 are free of scripts, C and plugins
- foreground sprites for the rocks, stump and log (their collision comes from
  the painting, so a sprite would need the painted footprint)

---

## 4. Mod packages

### 4.1 `dw.vanilla.roster` — 52 raisable Digimon

**1 feature, 245 patches, 1 manifest-declared plugin** (`dw.roster`) **plus a
second plugin source** (`dw_roster_flags.c`) that reaches the game through
`mod_function_replace_funcs` and therefore needs no manifest block.

This began as one Digimon and grew into the whole enemy roster. Slot **114** is
the game's only free type id — a developer stub named `tAKA` — and it becomes
Machinedramon, the complete-but-unobtainable Ultimate; that came first and is
why the table-relocation machinery below is described around it. Types 63-113
are the other 51: finished Digimon the game shipped as enemies only.

Three obstacles, three mechanisms:

**Table relocation.** Eight tables are indexed by type and packed tight against
their neighbours, so they cannot grow in place — and a manifest cannot write
outside the loaded image anyway (the game's own BSS clear would erase it).
Instead `dw_roster.c` reserves a 32 KB Expansion-1 arena at `0x9F000000`,
copies the stock tables in at a function-entry hook on `initializeHeap`
(`0x800EEBDC`, which runs once after the BSS clear and before any consumer),
and appends authored entries for type 114. Guarded patches retarget every
instruction pair that materialises one of those table addresses.

| Table | Stock | Sites | Target |
|---|---|---|---|
| `EVO_REQ_DATA` `0x8012ABEC` | 63 | 16 | `main_exe` |
| `RAISE_DATA` `0x801225BC` | 66 | 44 | `main_exe` |
| `EVO_GAINS_DATA` `0x8012B2D0` | 66 | 4 | `main_exe` |
| `ITEM_TAKE_DISTANCE` `0x80122D24` | 67 | 3 | `main_exe` |
| `EVO_PATHS_DATA` `0x8012B66C` | 62 | 9 | `main_exe` |
| `EVOCHART_ICONS` `0x80124544` | 61 | 7 | `main_exe` |
| `TRAINING_ANIM_IDS` `0x8008F050` | 68 | 5 | `disc_user` (TRN) |
| `TRN2_D_8008DA20` `0x8008DA20` | 68 | 3 | `disc_user` (TRN2) |

All eight are grown to **180 entries**, the engine's hard ceiling (`loadMMD`
rejects type ≥ `0xB4`). That is 91 `lui` + 91 `lo16` retargets. Site counts are
asserted in the generator, because silently patching a different number of
sites is how you ship a half-relocated table.

#### 4.1.1 Patching overlay code

The two training tables live in overlay images, which makes them the only case
here where **executable** bytes are patched on disc rather than data. That is
safe by construction rather than by luck: the runtime keys each compiled
overlay entry by a content hash of its own code ranges plus a page-generation
snapshot (`overlay_loader.c`, the "Inc3" design), so a patched overlay misses
its cache entirely and is recompiled. Executing stale native code for a range
that changed is not possible. The cost is that those functions interpret until
the background compiler rebuilds them.

Disc offsets follow `lba*2048 + (vaddr - <that overlay's load base>)`; the
runtime maps that onto a 2352-byte raw image as `(off/2048)*2352 + 24 +
off%2048`. A patch may not straddle a 2048-byte sector boundary — the runtime
applies writes inside a single sector buffer — and the generator asserts it.

**Overlays do not share a load base.** TRN and TRN2 are `0x80088800`, but EVL,
EAB and ENDI are `0x80060000`, BTL/STD/VS are `0x80052AE0`, and so on;
`OVL_BASES` in the generator has all sixteen. Using one base for all of them
still finds the right table references (the target address is absolute) but
computes wrong site addresses, which is a silent way to patch the wrong bytes.

#### 4.1.1a A relocation must census *every* overlay

This one shipped broken and is worth recording precisely.

The first version of the relocation censused reference sites **in the EXE
only**, plus TRN/TRN2 for the two tables that live there. But overlays
reference the EXE's tables too, and one of them mattered enormously:
**`EVL_REL.BIN` — the evolution overlay — reads `EVO_GAINS_DATA`** at
`lui 0x80063390`. It was still pointing at the stock table, which has **66
entries**. So the moment a partner evolved into any type above 65, EVL computed
the new species from out-of-bounds memory:

| Target | Became |
|---|---|
| 12, 26, 63, 64, 65 | correct |
| 66 | 511 |
| 113 | 12543 |
| 115 | 7730 |
| 149 | 514 |

Five of the seven authored species were affected, Machinedramon included — and
the bug was invisible because every early test target happened to fall below
66. Seven more sites read `RAISE_DATA` (EVL ×1, TRN ×3, TRN2 ×3) and would have
corrupted training the same way.

The census is now per **(overlay, table)** and asserted as an exact set, so a
newly-appearing site is a hard generator failure rather than a silent omission.
The EXE-resident tables are scanned in every overlay; the two overlay-resident
tables are scanned only in the overlay that owns them, because TRN and TRN2
share an address window and would otherwise match each other's unrelated data.

The reference census that found those sites is in `research/roster-xref-census.md`.
Two properties made the relocation safe, both verified mechanically: every
reference is a plain `lui`/`lo16` pair (no gp-relative access, no mid-table
constants beyond entry-0 field offsets), and **no patched `lui` feeds any
consumer outside the relocated tables**.

**Model and texture.** Slot 114 has no model file and its texture slot merely
duplicates Meteormon's. Both loaders (`loadMMD` `0x800A1F68`, `loadMMDAsync`
`0x800A2810`) derive the model filename *and* the texture sector from the type
in `$a0`, so function-entry hooks rewrite `114 → 115` on entry — redirecting
model, skeleton-compatible animations and texture together, with zero code
bytes patched. This is the same net effect as the Vice hack's four-instruction
entry patch.

**Slot completion.** Guarded data patches: the `DIGIMON_DATA` record (the
game's own Machinedramon data, attribute set to Virus), the skeleton pointer
(`NULL → 0x8011CBC0`), the model-code pointer (`tAKA → MUGE`), the battle-aura
family (`ORIGINAL_TYPE[114] = 115`) and evolution branches (below). The raised-flag
guard at `0x800FF7FC` is not patched: `dw_roster_flags.c` replaces the function (§9).

#### 4.1.2 Only a Champion can be an evolution source

The evolution dispatch at `0x800A9150` is a level chain on `evoTimer` in hours,
not `age`, which counts days — Fresh (≥ 6), In-Training (≥ 24), Rookie (≥ 72),
Champion (≥ 144, plus a mode 3 special roll at exactly 360) — and it **ends there**.
There is no Ultimate branch, so an Ultimate partner never evolves again.

0.2.0 shipped Machinedramon hung off **Megadramon** and **MetalGreymon**, both
level 5. The route the README described could never fire; only the cheat could
reach slot 114, which is also why its model was never seen on screen. Its
sources are now **Tyrannomon** and **Greymon** — the two Champions that already
lead to the Megadramon/MetalGreymon line, Tyrannomon to both. The generator
asserts every source is level 4, so this cannot be authored again.

#### 4.1.3 The three evolution items

The game ships three finished evolution items that nothing could ever use:

| Item | Id | Description string | Writes `EVOLUTION_TARGET` |
|---|---|---|---|
| Giga Hand | 125 `0x7D` | "Digivolve to Gigadramon!" | `0x40` = 64 |
| Noble Mane | 126 `0x7E` | "Digivolve to Panjyamon!" | `0x3F` = 63 |
| Metalbanana | 127 `0x7F` | "Digivolve to MetalEtemon!" | `0x41` = 65 |

They target three of the unreachable species, and were dead for two reasons —
both now removed. Types 63/64/65 had no partner-line rows (all three are
authored above), and `partnerWillRefuseItem` gates them on the partner's level:
item 126 demands level 3, items 125/127 demand level 4.

`handleEvoItems` (`0x800C3864`) is clean: `slti $at,$a0,0x7D` routes everything
below `0x7D` to the table path, and ids `0x7D`–`0x7F` fall down a straight line
that writes the hardcoded target and drops into the kick
(`jal 0x800AA188` with `$a0 = 6`, i.e. `setTamerState(6)`). **No level check on
that path.**

So the whole fix is one instruction. Two `slti`s at `0x800A7368`/`0x800A7374`
form a range check whose fall-through is reached by exactly those three ids;
turning the second branch unconditional sends them to the same exit as every
other item and skips only the level tests:

```
0x800A7378   beqz $at,0x800A74B8   ->   b 0x800A74B8
             4f 00 20 10                4f 00 00 10
```

Only the `rs` field changes (`$at` → `$zero`) — one byte of the four. Items
below `0x7D` left two instructions earlier and items `0x80`+ take this very
branch already, so nothing else is affected.

**`HAS_USED_EVOITEM` (`0x80134E52`) had to be fixed too.** Every evolution item
that passes its level check sets it (`item.c:2205-2211`), stock items 71-114
included. While it is set, `EVL_scaleBaseStats` returns early (`evl.c:1497`),
skipping the base-stat rescale, and natural evolutions also get no stat gains,
Tamer levels or +96 hour Ultimate bonus (`evl.c:1362`, `1415`). The only clear is
in `initializeStatusObjects` (`partner_impl.c:427`), called once at boot
(`main.c:3408`) — so after one item evolution, every *later* natural evolution
silently skips all of that until the game is reset, in the stock game too.
`dw_roster.c` clears it
from the per-VBlank callback once the sequence is fully over (partner idle,
control returned, game time running) — not mid-sequence, which would rescale
the item evolution itself, the very thing the flag exists to suppress. That fix
is absent when the roster is off.

Verified end to end by `tools/dwtest.py item`, which drives the real inventory
UI rather than poking evolution state: all three items fed to a level-5
MetalGreymon, each producing its documented target, with the item consumed,
control returned and the flag cleared.

#### 4.1.4 The six restored Ultimates

| Species | Type | Source Champion | Weight window |
|---|---|---|---|
| Gigadramon | 64 | Airdramon | 45 |
| MetalEtemon | 65 | Sukamon | 50 |
| Myotismon | 66 | Devimon | 20 |
| WaruMonzaemon | 70 | Numemon | 25 |
| Tekkamon | 90 | Kokatorimon | 35 |
| WaruSeadramon | 112 | Seadramon | 40 |

`calculateRequirementScore` (`evolution.c:928`) offers four points and requires
three: care mistakes, a **hard ±5 weight window**, all six stats at once (a level
3 target instead treats its stat fields as flags and tests the partner's highest
stat), and one bonus from digimon/discipline/happiness/battles/techs. Weight is therefore
the game's own steering mechanic, and each new species gets a window distinct
from the ones its source already offers. The four dark variants require care
mistakes as a *minimum* (flags bit4 clear) — a neglect path no stock Ultimate
uses. Everything else is donor-copied from a species sharing family and body
plan, so animation, camera pull-back and appetite suit the model.

**Indexing is not uniform**, and getting it wrong is silent:

| `[type]` | `[type - 1]` |
|---|---|
| `EVO_REQ_DATA`, `RAISE_DATA`, `EVO_GAINS_DATA`, `TRAINING_ANIM_IDS`, `TRN2_D_8008DA20` | `ITEM_TAKE_DISTANCE`, `EVO_PATHS_DATA`, `EVOCHART_ICONS` |

0.2.0 wrote Machinedramon's `ITEM_TAKE_DISTANCE` at `[114]`; the game reads
`[113]`, a zero. Fixed.

Types 64–66 land *inside* the shorter tables' stock extents (`RAISE_DATA` has
66 entries, `ITEM_TAKE_DISTANCE` 67, the training tables 68). Those slots are
dead padding — the highest evolution target the disc ships is **61** — so
authoring them is safe, and the verifier excludes them from its stock diff
rather than reporting them as regressions.

**The training tables** (`TRAINING_ANIM_IDS` `0x8008F050` in `TRN_REL.BIN`,
`TRN2_D_8008DA20` `0x8008DA20` in `TRN2_REL.BIN`) are `s8[68][2]` indexed by
raw type, so any type past 68 reads off the end. Without a fix, offense
training picks an invalid animation and **MP training soft-locks** (its
threshold reads zero).

0.2.0 fixed this in place: index 114 lands 92 bytes past the table, inside the
string `"Give it all you "`, and two disc patches rewrote those bytes. Correct
for one slot, unworkable for 51 — and it carried a bug. The patch rewrote only
byte `[0]` of `"e "`, so byte `[1]` silently inherited the string's space
(`0x20`). That byte is the frame the training impact lands on: `trn_off.c:137`
increments a counter per frame and compares it with `==` before firing the hit
FX. Machinedramon's wind-up therefore ran 32 frames where every other user of
animation 46 uses 10, and outside the `0x08..0x1E` range of the whole stock
table. Both tables are relocated now (§4.1.1) and the entry is authored as
`2E 0A`, leaving the string alone.

Requirements as authored: 5000 HP/MP, 500/500/400/400 stats, ≤5 care mistakes,
100 discipline, 50 battles, 35 techniques.

**Savestate hazard, fixed.** A session resumed from a savestate at boot never
runs `initializeHeap`, so the arena stayed empty and retargeted reads hit
zeros — the game died at PC 0 after one to four minutes. A per-VBlank check now
fills the arena on those boots too. Related: a savestate taken under a
*different* mod plan is poisonous on resume, because the old plan's patched code
persists in restored RAM.

Regenerate: `py -3.12 tools/gen_roster_mod.py` (`--check` verifies only).

### 4.2 `dw.vanilla.trainer` — 15 features

Ten features neuter one raise-sim tick function each by rewriting its first two
instructions to `jr $ra ; nop` (every target returns `void`, so no caller reads
a result): hunger, tiredness, sickness, unhappiness, death, two lifespan
drains, post-battle tiredness, pooping, poop weight loss.

**Battle Stat Gain** — three integer sliders over `STAT_GAIN_FACTORS`
(`0x801344D0`, indexed by enemy count − 1; vanilla ships `{10, 12, 16}`).

**Perfect Training.** The training slot machine *is* the entire grading system
— there is no timing or accuracy check. `TRN_tickSlotMachine` case 5 scores the
three reels into a payout, and the award functions apply it as a straight
percentage: `gain = base × payout / 10`.

| Reels | Payout | Effect |
|---|---|---|
| no match | 5 | **0.5×** |
| any triple | 20 | 2× |
| triple matching the trained stat | 40 | 4× |
| triple 7 | 100 | **10×** |

Rewriting the three lower immediates to 100 makes every session pay the jackpot
rate while reels and sounds behave normally — six `disc_user` patches (three per
overlay). Caveats, both in the feature description: it only affects sessions
that actually run the minigame, and a fully exhausted partner still earns almost
nothing.

**Training Multiplier** (2×/3×/5×/10×). `PARTNER_PARA` carries the game's own
boost trio — flag, value, timer — that boost foods set. The **flag** is a per-stat
bit mask (1 Off, 2 Def, 4 Speed, 8 Brains, 0x10 HP, 0x20 MP), and a stat's gain
scales by `value / 10` while its bit is set (the timer is not consulted).
`tickPartnerNormal`'s expiry block at `0x800A9034` clears flag and timer when
the timer runs out; four patched instructions make it *write* the boost
instead, with all six bits set. The base register is established four instructions earlier and is not
clobbered — verified by disassembly. A real boost item still wins while its own
timer runs.

**Lucky Reels.** Before each spin the machine rolls an assist level from a
ten-byte table (`00 00 00 00 00 01 01 01 01 02`). Half the time (level 0) the third
reel is pushed off a would-be triple, so no triple lands. Level 1 helps the reels
match but pushes the third off 7-7-7. Only one roll in ten (level 2) makes it
actively scan for a seven, and only that can land triple sevens. Filling the table with
the highest level makes it hunt every spin, so the reels genuinely land on
jackpots and the player still stops them. A fair-play alternative to Perfect
Training. Two `disc_user` patches.

**Guaranteed Item Drops.** `dropChance` (+0x22 in each `DIGIMON_DATA` record)
raised to 100 for every species. 157 needed patching; slot 114 is deliberately
skipped because the roster package owns that record (§7.2). The 1.5 edition is
in §4.4.

Regenerate: `py -3.12 tools/gen_trainer.py`.

### 4.3 `dw.vanilla.qol` and `dw.vanilla.gameplay`

**QoL** — partner lifespan (four `addiu $v0,$zero,360` initialisers; the
immediate is sign-extended, so 9999 is the ceiling) and world-clock speed
(`tickGameClock`'s speed read), plus the three features below. Each has its own
generator (`gen_save_anywhere.py`, `gen_text_speed.py`, `gen_walk_speed.py`);
`gen_qol_mods.py` imports them and owns their player-facing descriptions. All
three are byte-identical in 1.5's executable and run with `dw.1_5/dw15`.

**Save Anywhere** (4 `main_exe` writes). The stock game saves only through
sleep, and the sleep path (`partnerTickSleep` case 5: `sleepRegen`,
`handleSleeping`) skips to the wake hour, ages the partner and resets its timers
before the save menu opens; case 7 also rolls `handleSpecialEvolutions(2)`. The
game already has a save without sleep: `openSaveMachine` (`0x8011381C`, script
op 0x30) and `tickSaveMachine` (`0x801138B0`). Its menu 0x45 "Do you want to
save?" runs 0x3F/0x40/0x41, the same `writeSavegame` + `MemCardWriteFile` path,
card checks and retry screens as the sleep save.

| Address | Function | Change |
|---|---|---|
| `0x800BA2A4` (80 B) | `handleGameMenuSelection` case 6 (Sleep) | not sleepy (`MENU_OPTIONS[6].disabled & 3`, `0x80123ED6`): `openSaveMachine`, `TARGET_MENU` 0x45 unless it chose 0x44 (no card), `TRIANGLE_MENU_STATE` 6. Sleepy: the stock sleep code, moved up three words onto case 5's identical tail |
| `0x800B7FB4` (52 B) | `tickTriangleMenu` state 6 | close UI box 0 as states 2/3/5 do, then tick `tickSaveMachine` each frame; when it returns 1 the stock store sets state 0 and the menu reopens |
| `0x800B95A4` (4 B) | `tickPlayerMenu` exit | stores state 4, not 6; stock state 4 is the same code, so state 6 is free |
| `0x800B7CB8` (4 B) | `addGameMenu` | not sleepy stores 2, not 1, in `MENU_OPTIONS[6].disabled`. `renderGameMenu` greys only on `== 1` and `tickGameMenu` buzzes only on `& 1`, so Sleep draws and sounds enabled |

Every `TRIANGLE_MENU_STATE` writer in both executables and all 16 overlays of
both discs was enumerated: nothing else stores 6, and no branch lands inside a
patched range. The menu stays open, so the clock stays stopped. The gate is the
sleepy bit (`PARTNER_PARA.condition & 1`), not tiredness. The `disabled` byte
has three readers (those two and the gate) and three writers: `addGameMenu`,
`tickGameMenu` (0 when sleepy) and `setSleepDisabled`, which only runs while
game time runs, so never with the menu open. `addGameMenu`'s other caller,
`tickInventoryUI` on Items cancel, runs with the clock stopped and stores the
same values. With the feature on, Sleep no longer looks greyed out when the
partner is not sleepy; the ZZZ bubble is the cue.

**Text Speed** (4 `main_exe` words). There is no typewriter effect.
`tickTextboxHandling` (`0x80100258`) calls `drawTextboxStrings` (`0x800FFA4C`)
once per game tick, which draws one line of a full-width box, and X is ignored
until the page is done. A box built with flag 0x80 then zooms in for five ticks
(`createAnimatedUIBox`) before its first page flips in.

| Address | Function | Change | Option |
|---|---|---|---|
| `0x800FFB10` | `drawTextboxStrings` | lines per tick, full-width boxes: 1 → 16 (the loop still stops at the page end) | Fast, Instant |
| `0x80100FFC` | `setupDialogueBox` | `ori $v0,$s0,0x80` → `0`: no zoom flag | Instant |
| `0x80101E20` | `scriptSetTextboxSize` | same | Instant |
| `0x80109C98` | `setupNewGameDialogueBox` | flags 0x81 → 0x01 | Instant |

Without 0x80 the box takes the `createStaticUIBox` path stock already uses for
dialogue with no owner. The flag is dropped only where dialogue boxes (0x21/0x81)
are built, because they close through `removeStaticUIBox`; menu boxes
(0xE1/0xF1) close through `removeAnimatedUIBox`, which a static box cannot
finish. Measured in VBlanks, vanilla and 1.5 alike: a new message box appears in
21–27 stock, 18 Fast, 3 Instant; an interview page after X in 9–12 stock, 6–9
with either. The text in VRAM is byte-identical to stock. What does move:
non-blocking text (`TEXTBOX_CLOSE_MODE` 1) returns to its script up to ~18
VBlanks sooner, auto-closing text stays up longer (its countdown starts at the
script op), Instant drops the box-open sound, and an X pressed a few frames
after a box opens counts. A whole page per tick pushes more than 50 glyph
uploads through `drawGlyph`'s 50-slot ring at once: fine in this port,
unverified on hardware. A shop (the Market Manager in Gear Savanna: his
question, Buy/Sell, the item list, the amount box, a purchase), the field Item
menu and widescreen dialogue were run with each option on both games: every
page, menu text, branch and the purchase match stock. The shop's 6-row list
draws in 1 call instead of 6. The `scriptSetTextboxSize` site has not been
reached in a test; no caller was found.

**Battle End Freeze Fix** (`end-box-fix`, 4 `disc_user` writes and 1 `main_exe` write). A win whose Brains gain crosses 700, 800, 900 or 999 froze the end-of-battle box, on stock and on 1.5. It is the only feature on by default, and `dw15` makes the same writes. The message's row count was wrong, the reader stopped on the digit 0, and the font has no `%`. `GAME.md` 12.1 has the mechanism, every write and what was tested.

**Run Speed** (`walk-speed`, 3 `main_exe` writes, 2 live). The game already
runs by default: a held direction plays anim 3 (run, 50 units per logic frame),
R1 plays anim 2 (walk, 20). There is no speed variable; movement is root motion
applied by `tickAnimation` (`0x800C1C64`). The feature gives the tamer extra
whole frames of movement while running.

| Address | What |
|---|---|
| `0x80114334` (84 B) | `tamerTickOverworld`'s jump table: the 21 case labels become the callees (tail-call stubs for states 1, 9 and 13, whose delay slot sets an argument) |
| `0x800A9CAC` (240 B, one per speed) | `tamerTickOverworld`: `jr` → `jalr` dispatch, which frees the case blocks. State 0 calls `tamerTickWalkingState`, then at most once, if `TAMER_STATE` 0, `IS_SCRIPT_PAUSED` 1 and the tamer's anim is 3: `tickAnimation`, `tickCameraFollowPlayer`, `tamerTickWalkingState`. 1.5x takes the step only on odd libetc VSync counts (`0x80116B30`) |

Each extra step is a full stock frame, so collision is probed before it and
exits, talk, chests and pickups are checked after it. Only the function itself
and `tamerTick`'s `jal` reference the function and its table (both executables,
every overlay on both discs). Measured: 2x is 99.7 units per logic frame (stock
49.5), 1.5x alternates 100/50, R1 walking stays 19.8, and every 2x position is
every other stock position, wall slides included. With widescreen on, the
tile streaming after every camera call is the same sequence as at 1x. The
partner keeps its own pace: 310–600 units behind after a short run (stock
~230); after a ~7500-unit straight run it is ~3300 behind at 1.5x and ~4400 at
2x. Closing to ~320 took ~3.5 s at 1.5x and ~4.6 s at 2x; in another 2x run
it was still over 350 behind after 5 s. It never gets stuck, and an exit
places it next to you. Scripted walks (`tickEntityWalkTo`, which stops on reaching a tile) are
untouched, since a doubled step could overshoot. The parity trick assumes
3-VBlank logic frames; lagging frames lock 1.5x at 1x or 2x for a while.

**Gameplay** — Tech Mastery (the `random(100)` immediate in the move-learning
roll) and Evolution Unbound (scales the six stat requirements across all 63
`EVO_REQ_DATA` entries; `-1` means "not required" and is preserved, and weight
is deliberately untouched because it is an exact target, not a floor). The 1.5
edition is in §4.4.

### 4.4 `dw.1_5` — Digimon World 1.5 (Test Build 1.3.1)

**3 features (1.5 and two 1.5-only editions), 1 `[[disc_delta]]`, 1 plugin**
(`dw.dw15`). The fan mod by Capablemite, shipped as the author's own BPS
(`assets/digimon_world_1_5_test1.bps`, unchanged).

**How it applies.** With the feature on, the framework checks the player's
stock disc against `source_sha256`, applies the BPS in memory, checks the
result against `target_sha256`, and serves the changed and added sectors in
place of the stock ones. 1.5 changes 20 files: ten overlays and five data files
in place, and five files that grow and move past the old end of the disc (the
executable among them), plus about 11 MB of new sectors no file points at. The
player's disc file is never written.

**The executable.** 1.5's `SLUS_010.32` is 0xB9000 bytes of text against
0xA4800, with 25,189 bytes changed inside the stock range. Its entry point
`0x80135000` is a stub in what the stock game uses as BSS. It copies three
blocks out of that staging area and jumps to the stock entry `0x800A4240`:

| From | To | Bytes |
|---|---|---|
| `0x80135100` | `0x801F0000` | 40960 |
| `0x8013F100` | `0x801FA000` | 12032 |
| `0x80142000` | `0x80089000` | 30720 |

After entry the staging area is ordinary BSS again, so the game's variables
there (`MOVIE_COMPLETED`, `PARTNER_PARA`) keep their stock addresses. Before
entry it is code, which is why the cheat engine now waits for the game's entry
point (`psx_cheats.c`): "Skip all movies" pokes `0x80135304` every frame, and
during the BIOS load that byte is part of the block copied to `0x801F0204`.

**Conflicts.** `tools/check_dw15_compat.py` (§7.5) compares every guarded
write, every `game.toml` hook and the ranges the widescreen plugins depend on
against the 1.5 disc. Six features cannot run on it as they are. Five of them have
1.5 editions that run in their place by themselves; the sixth, Battle End Freeze Fix,
is built into `dw15` and gives way by itself:

| Feature | Why |
|---|---|
| `dw.vanilla.roster/roster` | 1.5 grew and moved every table it relocates, and slot 114 is Veemon. Not a conflict: `roster-15` runs in its place (below) |
| `dw.vanilla.gameplay/evolution-unbound` | 1.5 rewrote `EVO_REQ_DATA`. Not a conflict: `dw15-evolution-unbound` runs in its place |
| `dw.vanilla.trainer/guaranteed-drops` | 1.5 changed 138 of the `DIGIMON_DATA` drop bytes it guards. Not a conflict: `dw15-guaranteed-drops` runs in its place |
| `dw.vanilla.evolution/native-evolution` | 1.5 changed `getFreshEvolutionTarget`, the replaced function: its first instruction jumps to 1.5's own chooser at `0x801F9940`. Not a conflict: `dw15-native-evolution`, which writes nothing and hooks nothing, runs in its place, so 1.5's chooser runs. The engine changes no behaviour yet, so nothing is lost |
| `dw.newmaps/glade` | 1.5 moved the file table, DG.SCN and the MAPHEAD tail it writes. Not a conflict: `glade-15` runs in its place (§3.2) |
| `dw.vanilla.qol/end-box-fix` | 1.5's delta owns its `BTL_REL.BIN` sectors, though not these bytes. The `dw15` feature makes the same writes itself. This one is default-on, so it gives way without being unticked (`MOD_PACKAGES.md`, Feature conflicts) |

Everything else passed, widescreen included: none of its seven hooked
functions, three matched call sites, two bracketed functions or the BTL bar
slide table changed. Save Anywhere, Text Speed and Run Speed touch no byte
1.5 changed. Of the cheats, only "Shrink your own collision box" wrote a byte
1.5 changed: under 1.5, `0x8012CECC` is another table. It now writes each
game's own `DIGIMON_DATA[0].radius` behind a gate on which executable is
running (§6.1).

The `dw15` feature's `conflicts` list names all five (package format 12, see
"Feature conflicts" in `psxrecomp/docs/MOD_PACKAGES.md`): ticking 1.5 unticks
them, ticking one of them unticks 1.5. `check_dw15_compat.py` fails when that
list and its own result disagree.

**Patching 1.5's data.** `main_exe` patches are applied when the game first
reaches the stock entry `0x800A4240`, after the stub has run. So a patch on
data 1.5 relocated targets the live copy, never the staging area, and is
guarded with 1.5's bytes. Where the tables live in 1.5, read off its code:

| Table | Stock | 1.5 |
|---|---|---|
| `DIGIMON_DATA` | `0x8012CEB4`, 180 × 0x34 | `0x801F4300`, 384 × 0x34 |
| `EVO_REQ_DATA` | `0x8012ABEC`, 63 × 0x1C | `0x80089020`, 0x1C per row, indexed by target |
| `EVO_PATHS_DATA` | `0x8012B66C`, 0x0B per row | `0x8008CF20`, 0x18 per row |
| `EVO_GAINS_DATA` | `0x8012B2D0` | `0x8008BA20` |
| `RAISE_DATA` | `0x801225BC` | `0x801FA01C` |
| `FILE_OFFSET_TABLE` | `0x8011FE1C` | `0x801F1000` |

**The `0x80089000` block is re-read from disc.** It is a 30720-byte block
starting "DTBL". 1.5's executable carries one copy at file offset 0xB2000
(LBA 170423 on the 1.5 disc), which the stub copies at boot. The disc carries
another at LBA 168481, past the stock end and in no ISO9660 directory. The
training overlays load at `0x80088800` and overwrite it; 1.5's guard at
`0x801F9C00` then reloads it with `readFile("MODTBL.BIN")` and bumps a
counter at `0x801F9ED0`. So a change to a table there is two `disc_user`
writes, one per copy, and no `main_exe` write: `main_exe` writes are
re-applied unguarded after a savestate load or rewind, which would poke
whatever overlay is resident.

**Writes on 1.5's sectors.** Both copies sit on sectors the delta owns. The
framework allows a disc write there only from the delta's own feature or one
in the same package that reaches it through `requires_feature` (chains
count). Such a write is guarded against 1.5's bytes and applied after the
delta on every read: raw and 2048-byte drive reads, host reads, sectors past
the stock end. Any other write there, and every `[[overlay]]`, fails before
launch with "collides at disc LBA ... with disc delta ...". A cross-package
`[[requirement]]` does not count. Details: "Disc deltas" in
`psxrecomp/docs/MOD_PACKAGES.md`.

**1.5 editions.** Three features in `dw.1_5` are the 1.5 editions of vanilla
features (the third, `dw15-native-evolution`, is hand-written above the marker
and empty). Each is `hidden`, has a `requires_feature dw15` constraint and names
its original with `edition_of` (format 13, "Feature editions" in
`psxrecomp/docs/MOD_PACKAGES.md`). While 1.5 and the original are both on, the
session runs the edition instead, with the original's option values, and saves
nothing: the player keeps one checkbox, and the runtime logs the edition as
"activated implicitly (required by ...)". The originals are not in `dw15`'s
`conflicts`. `tools/gen_dw15_extras.py` generates both from the 1.5 disc, below a
marker in the manifest, and `tools/check_dw15_compat.py` reports an original with
an edition as `edition`, not `CONFLICT`. Checked in game (2026-11-10): with 1.5,
plain Guaranteed Drops and Evolution Unbound on Open, both editions activated and
1.5's live drop table read 100 for species 1-11 (1.5 alone: 0, 0, 50, 30, ...).

**The roster on 1.5** (`roster-15`, `tools/gen_roster_dw15.py`, in `dw.vanilla.roster`
behind a `[[requirement]]` on `dw15`). 1.5 already grew every table the roster relocates
to 384 types and moved it, and fills the rows for the roster's 51 species with
placeholders, so nothing is relocated: the roster's rows are written into 1.5's tables,
found by decoding 1.5's instructions at the stock reference sites. Requirement rows are
the roster's own; raise, gains, item-take and training-animation rows come from the same
donors, read from 1.5's tables; each species is wired into its source's path row (seven
targets per row on 1.5, so Koromon, Tokomon, Tsunomon and Tanemon reach six).

| Table | 1.5 address | Index | Written as |
|---|---|---|---|
| `EVO_REQ` / `EVO_GAINS` / `EVO_PATHS` | `0x80089020` / `0x8008BA20` / `0x8008CF20` (MODTBL) | type / type / type-1 | `disc_user`, both copies |
| `RAISE_DATA` | `0x801FA01C` | type | `main_exe` |
| `ITEM_TAKE_DISTANCE` | `0x801FCA40` | type-1 | `main_exe` |
| `TRAINING_ANIM_IDS` / TRN2 | `0x8012ED14` / `0x8012F014` (inside the stock `DIGIMON_DATA` range) | type | `main_exe` |
| `EVOCHART_ICONS` | `0x801215D4` | | not written: 1.5 rewrote the chart |

Raised flags: 1.5's `setDigimonRaised` (`0x801F0D40`) banks species 63-179 at
`0x262 + species`, which for 63-107 lands on treasure-chest ids its scripts use
(`0x28A`-`0x2CC`, `tools/scan_trigger_ids.py` on 1.5's DG.SCN and MAPHEAD.SCN). Plain 1.5
never raises those species; the roster does, so `roster-15` moves 63-113 to
`0x296 + species` (`0x2D5`-`0x307`). The stock roster's replacement hook would not fire on a
function 1.5 rewrote, so this is guarded patches on 1.5's own `setDigimonRaised`, which
grows into the padding after it, and two words of its `hasDigimonRaised` (`0x801F9B80`).
1.5's only raisable species in 115-179 is Veemon (114, `0x2D4`). The evolution-item level
test and the Prosperity bound are the stock roster's patches, unchanged on 1.5.

Not carried over: Machinedramon (slot 114 is Veemon); the battle-intro name glyphs, which
1.5 still reads from the stock 112-row table, so WaruSeadramon and Meteormon show garbage
on the arena banner, as they do without the roster; and the chart cursor fix.

Checked in game (2026-11-11), `tools/verify_roster15.py`, 1.5's own choosers picking from
shaped stats: Airdramon to Gigadramon (Champion), Meteormon to MudFrigimon (a roster source)
and Koromon to ToyAgumon (1.5's Rookie chooser). Each evolution finished with control back,
the flag set in the roster bank and the treasure-chest id untouched.

All five editions together (2026-11-11, `tools/verify_dw15_states.py`): with 1.5 and every
mod ticked, standing in the Hidden Glade, a savestate load and a Rewind jump both restored
the game with the glade, 1.5's drop table, the roster's rows and wiring intact, and
Evolution Unbound on Open read Flamedramon's walls as 50. Netplay is covered by
`test_mod_packages`: a peer that adopts the host's selection derives the same editions and
plan fingerprint. A real online room was not run.

| Feature | Writes |
|---|---|
| `dw15-guaranteed-drops` | 203 `main_exe` bytes: `dropChance` (`0x801F4322 + type × 0x34`) to 100 for every named record below 100. 1.5's drop roll (`0x800ED298`) is the stock code with the new base. The 159 nameless slots are all zero and no 1.5 map places one. Records with chance 0 are raised too, as in the vanilla feature, so they drop their item byte, mostly item 0 |
| `dw15-evolution-unbound` | 616 `disc_user` writes: each scaled wall (`max(1, v × pct / 100)`) of the 67 walled targets the three choosers can pick, once per copy of the block. Row 254 (all 32767, unreachable) is skipped; `-1`, flags and weight are left alone. Same `walls` option as the vanilla feature |

Checked in game: with the drops feature, 21 of 21 defeated enemies dropped
their item (control: 3 of 18). With Evolution Unbound (1.5) on relaxed,
Flamedramon's walls of 100 become 75: stats of 75 evolve, 74 do not, both from
the boot copy and after a reload from disc.

**Saves.** `save_compatibility = "isolated"` scopes savestate slots to the plan
(§3.2) and leaves memory cards alone. `dw_dw15.c`'s static constructor calls
`psx_mod_register_memcard_subdir("dw.dw15", "dw15")`, and activation applies that
while the plan selects the plugin, so the cards become
`<memcard_dir>/dw15/card1.mcd` and `card2.mcd` (an explicit card path keeps its
file name inside a `dw15/` folder next to it). Activation runs after every
launcher session and before the cards are opened, and the request is cleared
once used, so a session with 1.5 off opens the stock cards.

---

## 5. Widescreen

**Native-wide is what ships since 0.3.2** (`[widescreen] native_wide = true`).
The defects catalogued in the widescreen survey are fixed by three game
plugins -- `dw_ws_backdrop.c` (fade plate, parked HUD, the clock and the field
gauges each as ONE widget, the battle bars' slide table, and every other
full-screen plate or mask band via one ordering-table walk),
`dw_ws_camera.c` (both camera clamps, the tile-ladder shift, the load-time
nudge, the per-frame clamp, narrow-map centring, the widened visibility
cull, fishing's camera limits), `dw_ws_sliver.c` (on OpenGL, and on
Software at 21:9, the whole backdrop drawn from texture banks built from the tile file read off the disc; elsewhere the fifth
backdrop column, uploaded as a stacked VRAM sliver whenever the game's
four-column ring falls short, from the same host copy) and `dw_ws_world.c` (keeps a field
frame wide through a synchronous tile reload), plus `dw_ws_weather.c` (snow
and mist copied into the reveals) -- and one framework primitive
per need (`mod_function_exit_funcs`, `psx_mod_ws_native_wide_offset`,
`psx_mod_vram_write`, `psx_mod_set_world_scene_predicate`, live-palette
texture banks). Section 5.0c has the 0.4.x pass and its numbers, 5.0d the
plates and weather, 5.0e the codegen culls, 5.0f the bank backdrop, 5.0g why there is no per-map camera table, 5.0h 21:9, 5.0i fishing. The
wider field of view is rendered into a genuinely wider framebuffer (320 -> 426
at 16:9, `EXTRA = 2 * round(W*(3n-4d)/(8d))`) and presented 1:1. Nothing is
stretched, so the entire "screen-space 2D renders stretched" problem class —
which is what W2 below exists to fight — does not arise.

**A player cannot select squash.** It is what shipped through 0.3.1, but the
mode is fixed at codegen (`game.toml [widescreen] native_wide`), and the mod
offers only an aspect. The untitled text at the end of 5.0g and section 5.2
describe squash and are kept
because the code is still there and still gated, not because anyone can turn
it on.

**16:9, 16:10 and 21:9 are offered; 21:9 only through texture banks.** The
reveal per side is `(w*(3n-4d) + 4d) / (8d)`, so at w = 320 that is 32 px,
53 px, and 120 px for 21:9. The tile ring covers 512 px and needs the
origin's leftslack in `[m, 192-m]`, an interval that is EMPTY for `m > 96`;
the 106 px sliver lifts the practical bound to about 78. 21:9 clears neither,
so it is served by the bank backdrop (5.0f) alone: `dw_ws_camera.c` lifts its
margin cap from 64 to 120 (`DW_MARGIN_BANKS_MAX`) only while banks draw the
map on screen (`dw_ws_banks_active(m)`, asked every frame). Where banks are
refused (Vulkan, netplay's CPU-authoritative path) the
framework still presents 21:9 -- there is no runtime fallback to a narrower
aspect -- and the ring tier runs at 64: the inner 64 px of each reveal are
covered as at 16:9 and the outer 56 are not (5.0h). Nothing wider than 21:9 is
offered. The game ships its own copy of the
framework's widescreen manifest, under `mods/preloaded/packages/`, which
declares the game's plugins and repaint patches and carries that text. If the builtin's version is ever bumped past 1.0.0 the override stops
applying silently, because the loader takes the highest semver.

### 5.0 The native-wide measurement (what actually got checked)

Native-wide was tried on this game once before and rejected for "stale side
strips, boundary void and torn menus". Re-measured with `tools/dwgame.py`
(`enter_game()` plus a fixed 14-step walk plan, so the survey reproduces), on
both renderer backends:

* OpenGL and software agree to **0.1%** per scene on every measure below, so
  none of this is a backend artefact. Both present `path=wide, present_w=426,
  wide_fellback=0` for every sampled frame.
* The 4:3 image aligns to wide columns **53..372** (best-match search over
  offsets 0..109; runner-up 52 at a clearly worse score). Both 53-column
  reveals are therefore real new view.
* **Not stale.** Walking with a fixed camera, per-frame pixel delta in the left
  reveal is the same order as in the centre (1.2/0.4/1.1 vs 3.3/2.6/2.7). The
  reveal renders live and crosses the old 4:3 edge with no seam.
* **Menus are not torn and not squashed.** The item window measures 301 px in
  4:3 (columns 9..310) and 303 px in wide (columns 61..364) — same size, same
  centre, native pixel shapes.
* **Boundary void is real, and is missing art.** 3 of the 14 camera setups show
  black in the right reveal, cut flat along the old frustum plane, mid-object.
  It is not a cull: other cameras in the same rooms draw content out to column
  404 (canonical x 351), which any screen-space clip at 320 would forbid. Those
  interiors are simply only modelled as far as the fixed camera could see.

Two knobs were tried and rejected, both recorded in `game.toml`:

* `[widescreen.cull] auto_screen_x` — inert here. It pairs a `sltiu ..,0x140`
  width compare with a `sltiu ..,0xE0` height compare; this game has **neither
  immediate anywhere** (zero in the main EXE and in all 16 overlays; one signed
  `slti ..,320` in `EAB_REL.BIN`). Enabling it emitted zero `psx_ws_x_margin()`
  call sites, i.e. byte-identical codegen.
* `[widescreen] nw_hud_corners` — actively harmful here. It re-anchors
  outer-third 2D prims to the true wide corners, but on this game it shifts
  **world** polygons: two black vertical slabs tear through the scene and the
  HP/MP bars separate from their icons.

### 5.0b Repainted room edges (0.4.0)

Two rooms were pre-rendered only as far as the original camera looked, so
the reveal showed flat black cut through painted objects: ROOM06 (Item Shop,
right; colour index 0 on all 256 rows of its last 37 columns) and ROOM08
(Jijimon's house, the 5x3 one; a diagonal cut down the left). ROOM12 (Birdra
Transport, right of the cage) was tried and dropped: every generated wall fell
off into the room's own shadow, and in game 13 columns still measured void at
16:10. A scan of all 242 maps found 16 with a dark edge run of 6 px or more;
the other 13 are rooms drawn floating in black
(the mansion, caves, the igloo, the curling rink, the Secret Item Shop's
shadowed wall) and are left as designed.

* **Art.** `art/backdrops/<MAP>.png` is the whole map as an 8-bit PNG in the
  map's own CLUT indices. It was made with a local Qwen-Image-Edit 2511
  (ComfyUI, latent noise mask over the dark region connected to the edge,
  Lightning LoRA at 4 steps, full denoise, smooth Telea pre-fill), downscaled,
  feathered into the original and Floyd-Steinberg quantised to the CLUT with
  the transparent entry 0x0000 excluded. Every pixel outside the repainted
  region is the original byte. Asking the model to "keep everything else
  identical" with no mask kept the black too, and 8 steps with the 4-step
  LoRA streaked.
* **Delivery.** `tools/gen_extended_backdrops.py` diffs the PNG against the
  TFS on the disc and writes guarded `disc_user` patches, split per sector,
  into a marked block at the end of the widescreen manifest, under feature
  `widescreen`. The game's own loader and VRAM ring then carry the art, on
  every renderer, and only while widescreen is on. None of the sectors are
  owned by the 1.5 delta (`check_dw15_compat.py`).
* **4:3 reach.** With widescreen off, the Item Shop camera reaches CAMERA_X 318
  (view to column 638), so the black strip is not strictly widescreen-only
  there; widescreen just shows it far more often. That is why the art rides
  on the widescreen feature rather than being applied always.
* **HD texture packs** key replacements on the uploaded bytes, so a pack's
  replacement for one of these tiles stops matching while widescreen is on.

### 5.0c Reloads, culls, narrow rooms, panning edges (0.4.x)

Phase 1 of the widescreen plan. Every number below is a live run at 16:9 on a
build-debug copy, widescreen only unless stated, against the same harness
that measured the "before".

* **No 4:3 snap after a won battle or an evolution** (`dw_ws_world.c`). Both
  end with a synchronous `readMapTFS` during which nothing is drawn, and the
  runtime's 45-frame GTE tail ran out. A world-scene predicate now latches
  "the last completed frame drew the map" (renderMap exit, per buffer,
  committed on the ACTIVE_FRAMEBUFFER flip). Battle won: 74 lit 4:3 frames ->
  0 (the 128-frame static reload run stays wide). Evolution: 55 -> 0. The
  title stays 4:3 (2686 of 2686 menu frames) and FMV is still vetoed after the
  predicate.
* **Characters and boxes stay drawn in the reveal** (`dw_ws_camera.c`,
  `dw.ws.cull`). isOffScreen's width argument is widened by the cull envelope
  for its two callers only (entityIsOffScreen, isBoxOffScreen, matched by
  return address). In-reveal entities with isOnScreen = 1: 29 of 32 samples
  (was 1 of 6); of the 3 off, two are below the frame and one is at the
  widened edge. 12 of the 13 disagreements with the host model sit within
  12 px of that edge. Under 1.5, where entityIsOffScreen runs interpreted,
  28 of 28 vertically visible in-reveal samples were on, so the hook in the
  compiled isOffScreen still fires. Battle membership follows the flag
  (owner decision D1, option A).
* **Narrow rooms are centred**: the 19 three-tile maps pin CAMERA_X at 32,
  21 px of map-edge black per side at 16:9 (was up to 53 px on one side).
  Measured on 206, 18, 29, 45, 207, 32, 84, 136; 207 shows 33-35 px on the
  right because 12-14 px of its own art there is painted black.
* **Arrivals** (`dw.ws.camera-follow`). The widened clamp and the load-time
  nudge are re-applied at tickCameraFollowPlayer, gated to the plain field
  camera (and off on 6/8/136 while FISH/KAR are resident). The 7-warp chain
  after a savestate load: 5 of 7 bad arrivals -> 0 of 7.
* **No void on the trailing edge while panning.** The frame is drawn with the
  camera it started with, `C_eff = CAMERA_X + DRAWING_OFFSET_X -
  DRAWING_OFFSET_X_COPY`, one step off the camera the ladder and the sliver
  used. The sliver takes the larger deficit and the ladder shift adds the
  lag. gapwalk, 4 walks per map: map 204 36+36 right (+9 arrival left) -> 0,
  map 12 30+30 -> 0, map 69 15+15 right and 3+3 left -> 0. Pairing each
  presented image with the snapshot one game frame earlier (double
  buffering) is what makes the old voids line up with C_eff on 12 and 204;
  69 never fitted that model, but measured 0 after the change too.
* **The sliver reads the tile file from disc, never the tile store**
  (`dw.ws.sliver`). readMapTFS's map id keys a host copy built with
  `psx_mod_read_disc_file` (sector mods included); a savestate load drops it
  and it is rebuilt from CURRENT_SCREEN. With column T+4's tiles overwritten
  in guest RAM, the way a battle's effect heap overwrites them: sliver vs map
  art 78.6 luma before, 0.3 after (0.2 after a savestate load with no warp).

### 5.0d Plates, masks, snow and mist (0.4.x)

Phase 2 of the widescreen plan, same harness and build setup as 5.0c.

* **Full-screen plates and mask bands** (`dw_ws_backdrop.c`, `dw.ws.plates`).
  Almost every producer lives in an overlay no hook reaches, so a GsDrawOt
  (0x80098808) entry hook walks the ordering table once, tracking the draw
  offset (libgs's GsSetOrign origin, then each E5), and moves an edge lying
  EXACTLY on screen X 0 or 320 out by the reveal, for untextured F4/G4/TILE
  and solid-patch FT4 (UV span <= 4) rectangles at least 240 tall. Exact
  equality keeps it idempotent. Measured on a lost battle: the fade-in at
  Jijimon's darkened the centre only (plate strength 180 -> 32 in the centre,
  none in the reveals); after, left reveal = centre within 0-3 at every
  sampled frame, right reveal = centre once its dark art stops clipping
  (F <= 54). The MURD iris left both reveals lit; after, its bands reach the
  wide edge. Under 1.5 the same fade measured 123/123, 69/69, 16/16 (reveal /
  centre) and the iris covers the reveals too.
* **The pause plate needed nothing**: the framework already stretches an
  untextured TILE that covers the display. Reveal/centre dimming ratio 0.99-1.0
  on maps 204, 0 and 69, before and after.
* **Snow** (`dw_ws_weather.c`). buildSnowflakePrim keeps flakes in [0, 320];
  a flake within m of an edge gets a copy 320 px over (renderMap exit), no
  RNG call. Map 88, flakes per column (8 spawns averaged): reveals 0 / 0.10 x
  centre before; 0.99 (left) and 0.76 (right) after. The right reveal equals
  the 4:3 frame's own leftmost 53 columns exactly (13.36/col), which are
  sparser than the centre in vanilla as well; the copies are an exact tiling.
* **Mist** (`dw_ws_weather.c`). Each 320-wide mist quad gets a copy 640 px
  over wherever that copy reaches the wide window (renderMist exit). Maps
  115-119, 20 phases each, the wide window fully covered in 32 of 100 samples
  before (worst layer 0.876), 100 of 100 after; map 116 reveal/centre luma
  0.74 -> 1.00, 119 0.84 -> 0.98.

### 5.0e Codegen culls (0.4.x)

Phase 3: two screen-space culls that still stopped at the 4:3 edge are
widened in the generated code by the live margin `psx_ws_x_margin()` (53 at
16:9; 0 at 4:3, so identity). Config in `game.toml [widescreen.cull]`.

* **Map sprites** (water, torches, doors; renderMapOverlays). Its bounds
  `screenX - 40` / `screenX + 360` are `ADDI` from the camera register, which
  no existing kind accepted, so the framework fork gained
  `[[widescreen.cull.relative_x_bound]]` (full-word guarded, lower/upper edge,
  same rule in native code, overlays and the interpreter). Map 204, 120
  stops of a left/right/left walk: sprites visible in the outer reveal band
  but outside the vanilla window drawn 0 of 32 before, 52 of 54 after (the
  inner band matches at the same ~95%, the limit of the prim-to-instance
  match).
* **Partner reappearance** (isTileOffScreen, used by tickPartnerWaypoints):
  `slti_sites` / `slti_lower_sites`, so the partner is placed beyond
  |u| > 200 + m instead of 200, outside the 213-px half view. The generated
  code calls `psx_ws_cull_slti(.., 201)` and `psx_ws_cull_slti_lower(.., -200)`
  at 0x800D3138 / 0x800D312C. Not reproduced live: relocating the partner
  before a battle left its isOnScreen at 1, so the teleport never ran.
* **Not done**: renderParticleFlash's cell cull (upper bounds ADDI, lower
  bounds against -DRAWING_OFFSET_X with no immediate), never seen cut.

### 5.0f The backdrop from texture banks (0.4.x)

Phase 4 (OpenGL), Phase 5 (Software, through the fork's software bank
sampler, 5.0h). The field backdrop no longer depends on the game's
four-column VRAM tile ring (`dw_ws_sliver.c`, `dw.ws.sliver`).

* **One bank per tile file**, id = map id + 1, built only from
  `psx_mod_read_disc_file` (sector mods included, so the repainted rooms
  follow): the sliver's host copy when it holds that map, a fresh disc read
  when the GPU meets a bank the process never built (a cold savestate
  restore; `psx_mod_set_texture_bank_resolver`). Never from guest RAM: banks
  are immutable for the process. Painted tile k sits in its own texpage slot
  (x = (k mod 16) * 64 halfwords, y = 256 * (k / 16)), so it samples from
  u, v = 0 like a ring slot. The palette is read live from VRAM row 480
  through the fork's new `psx_mod_define_texture_bank_live_clut`, so the
  time-of-day cross-fade and the evolution fades land as on the ring.
* **Emission at renderMap exit**: every tile meeting the wide view plus 128 px
  on every side, two GT3s copying renderMap's FT4 (colour 0x808080, the
  tile's texpage word with the slot's page, the tile's CLUT, V extent 127 for
  ring-row-1 tiles and 128 for the rest), a black F4 for an empty tile. The
  chain goes into ot[0xfff] right behind renderMap's own tile quads (found by
  their ring-slot texpage and CLUT), from a 2 x 2800-byte GPU-DMA packet
  arena allocated at activation, one half per ACTIVE_FRAMEBUFFER. A packet
  the GPU refuses (HD pack switched on later) draws nothing and the ring
  under it still does.
* **The sliver and the load-time nudge stand down while banks draw**; both
  stay, unchanged, for Vulkan and netplay's
  CPU-authoritative path (`psx_mod_texture_banks_supported() == 0`), and for
  the Software renderer at 16:9 and 16:10, where a bank backdrop costs more
  than the ring it replaces (`psx_mod_renderer_accelerated() == 0`, 5.0h). The
  arena makes savestates taken with widescreen on carry an expansion-memory
  section (state format v8): states made with it on and off are not
  interchangeable. Memory-card saves are unaffected.
* **Debug switch**: `{"cmd":"mod_cmd","name":"dw.ws.banks","args":"off"}`
  stops the bank draw only (the nudge keeps following real support), for
  A/B; counters `dw.ws.banks.frames/defined/resolved/failed` in
  `mod_counters`.

Measured at 16:9, OpenGL headless (`--headless-opengl`) unless stated:

* **Inside the 4:3 frame nothing changes.** 29-map walking sweep (934 stops,
  30 steps per direction; map 205 unreachable by warp, Robot House 146 added),
  banks on/off A/B from one savestate every 12th stop, frames paired by guest
  frame counter and displayed buffer: 114 stops paired, 110 with the centre
  320 columns bit-identical in every pair. Of the 4 others, 2 are dialogue
  scenes whose game state diverged between the two replays (different
  character poses; the extra GPU work moves timing), and 2 are on map 25
  where the ring itself shows the wrong tile in one column (banks off 28.9
  luma from the art there, banks on 1.7). Where the old sliver had drawn the
  reveal, its top strip differed from the bank by the ring-row-1 127/128
  stretch the sliver never copied; the bank matches the ring.
* **No uncovered edges.** Sweep: 4613 solid-void reveal columns, every one
  explained: 4452 beyond the map edge (narrow rooms centred at 21/21 px, the
  clamp ends), 142 painted black, 19 on 146 (no RAM dump; the decode below
  shows its reveals equal the art), 0 lit art. 130 stops had the ring short
  on the right: 0 voids. gapwalk 204/12/69, 4 walks each, every presented
  frame: 0 void frames. 7-warp arrival chain: 0/7 bad, with the camera left
  where the game put it (C = 53, 292, 429, 153, 53, 139, 53: no nudge).
* **Reveal equals the art** (independent decode of the vanilla tile file
  with the live palette, aligned on the centre; mean / median abs luma):
  battle on map 69 started at a right-deficit camera (deficit 31-34): left
  2.05 / 0.59, right 0.7-4.7 / 0.0 over 4 battle frames (the same as the
  standing frame before it, 2.02 / 0.59 and 4.5 / 0.0: the means are
  characters in the reveal). After a real flee (FLEE_TIMER 41; the game moved
  the tamer to 204): 0.8-7.0 / 0.0. Evolution on 204: 0.03 / 0.0 and
  0.08 / 0.0 on every field frame of the sequence. Robot House 146 (the id
  `readMapTFS(0x92)` re-reads), 14 stops: medians 0-1.
* **Savestates**: save/load mid-field and mid-battle on 69, and a cold restore
  of both in a new process: 0 void columns, reveal medians 0-0.59, and the
  cold restore rebuilt the bank through the resolver (`resolved` = 1).
* **Fallback = Phase 1.** With an (empty) HD texture pack on, OpenGL:
  `dw.ws.banks.frames` stays 0, gapwalk 0 void frames, the 7-warp chain
  0/7 with exactly the Phase 1 cameras (53, 309, 437, 181, 53, 139, 53), a
  421-stop sweep of 8 maps 0 unexplained voids (50 ring-short stops, all
  covered by the sliver; 206 and 18 at 21/21 px). Software renderer, before
  Phase 5 gave it banks: 48 stops on 204/69/12, 8 ring-short, 0 voids.
* **Cost**: windowed (not headless), process CPU per vblank at one standing
  spot on 204, 8 alternating 8-second windows each: banks on 7.75 ms median,
  off 8.01; paired difference -0.26 ms mean (SD 0.44), so any cost is below
  the measurement noise, upper bound about +0.05 ms at 2 SE. Game frames per
  window identical (160-161 both).
* **With Digimon World 1.5** (widescreen + dw.1_5, NEW GAME): plan loads
  with no refusal lines, banks drew (5 defined, 5455 frames), reveals on maps
  0, 69 and 204 at 9 stops: median 0 luma from the art. Savestates could not
  be taken under 1.5 in this build with or without widescreen, so the A/B
  there is not done. That was a Windows path-length limit, fixed since in the
  framework (`fec50795`); the A/B has not been rerun.
* **After a real flee** the shots are the first three field frames on 204;
  walking on from there took the tamer through a door.

### 5.0g Painted-extent camera table: measured, no rows (0.4.x)

Phase 6a planned a hand-verified per-map table that pulls the widened clamp
in to the map's painted extent wherever a hard cut through painted objects
still shows. The safety rule decides it: an entry may move a clamp end by at
most m past the camera position the VANILLA game actually reaches, or the
wide view stops showing something the 4:3 game showed (|C - C_vanilla| <= m).
Today's clamp [m, scroll - m] already sits exactly m inside [0, scroll], so
on any map whose vanilla camera reaches the map edge an entry has no room at
all; the black there is in the 4:3 game too.

`tools/scan_painted_extent.py` (stdlib, reads the disc, `--png` dumps the
candidates) proposes the extents: 16 maps have a dark edge run of 6 px or
more. Classified by eye and then swept (`ws_research/p6a`, 8 walks x 12
stops per map, widescreen off, 16:9, 16:10; door-fade frames excluded):

| map | scan | kind | vanilla C | edge void at the wide clamp ends, 16:9 / 16:10 | entry possible |
|---|---|---|---|---|---|
| 216 Item Shop | R 39 | cut, repainted (5.0b) | 0..270 | R 0 / 0; L 41-43 / 32 is the shadowed pillar, shown at C = 0 in 4:3 | no, and not needed |
| 218 Jijimon (5x3) | L 24 | cut, repainted | 0..275 | 0 / 0 both sides | not needed |
| 207 Birdra Transport | R 13 | cut, not repainted | 0..46 | L 21 / 0, R 33-35 / 12-14 | no: three tiles, the whole map is already in view, re-centring only moves black from one side to the other |
| 205 Jijimon (4x3) | (L 0) | room floats in black | 0..192 | L 11 / 7, R 53 / 32 (below the floor) | no: vanilla reaches both ends |
| 217 Secret Item Shop | L 21 | shadowed wall | 0..192 | L 5 / 5 | no: vanilla reaches C = 0 |
| 26, 54-64, 108, 126, 137, 235 | 6-88 | mansion, caves, igloo, curling | - | - | left as designed (owner decision D2) |

So the table would have no rows and is not in the code. The two hard cuts the
camera could not hide were repainted instead (5.0b), and Birdra Transport's
13 columns stay. Re-run the scanner and a widescreen-off sweep before adding
an entry for a room this table does not list.



The "pre-rendered" room art is a grid of 128×128 tile quads drawn every frame,
scrolling 1:1 with the camera, in the same pre-draw-offset coordinate space the
GTE projects 3D into. In squash mode the 3D was compressed and the tiles were
not, so characters slid against the background — the misalignment players
reported.

`dw_ws_backdrop.c` hooks the shared quad positioner (`setPosDataPolyFT4`,
`0x800E56EC`), filters by **return address** to the two world-anchored callers
(the tile writer and the mode-0 map-object builder), and applies the exact GTE
transform per edge so adjacent tiles cannot seam. It is gated on a fork export
that reports whether the GTE is squashing this frame, so with widescreen off
every byte of behaviour is stock.

Verified numerically: 574 tile quads measure 96 px wide with the squash live
and 128 px with it disabled mid-session, while every UI primitive population is
identical.

### 5.0h 21:9 (0.4.x)

Phase 5. Two pieces, plus the manifest choice.

* **Fork: Software draws texture banks** (F2). The renderer vtable gains
  `texture_banks_supported` / `select_texture_bank`; `gpu.c`'s GT3 and BG2D
  bank paths and `psx_mod_texture_banks_supported()` ask `gr_*` instead of
  testing for OpenGL. The software rasterizer takes a texel source
  (`sw_set_texture_source`): texel words, and palette words unless the bank
  is live-palette, come from the bank, addressed like VRAM and 0 outside it
  (the GL shader's rule), point-sampled whatever the filter setting. It refuses
  under the netplay CPU-authoritative raster (`sw_faithful_authority`) and
  while an HD pack replaces or dumps. Unit test
  `runtime/tests/test_sw_texture_banks.c` (bank draw byte-identical to the
  same words drawn from VRAM, live palette recolours, extent, filter,
  refusals). Vulkan has no hook and still refuses.
* **DW: the camera cap follows the banks.** `dw_margin()` latches any reveal
  up to 120 (`DW_MARGIN_BANKS_MAX`) and serves it while `dw_ws_banks_active()`;
  above 64 without banks it returns 64, so the clamp, nudge, ladder shift and
  sliver run exactly as at 16:9 on the inner 64 px. The tile-ladder shift is
  skipped above 64 (the banks cover the view; the ring keeps the game's own
  origin). The sliver tries the banks first and clamps its own reveal to 64.
* **Software keeps the ring at 16:9 and 16:10** (owner decision, after the
  cost below). Fork: `psx_mod_renderer_accelerated()` (1 on OpenGL and Vulkan,
  0 on Software; asked per frame, since netplay can switch the backend).
  `dw_bank_current(m)` returns no bank when that is 0 and m <= 64, so on
  Software the bank draw, and with it `dw_ws_banks_active(m)`, is on only at
  21:9; there banks are also defined at their first draw rather than at tile
  file load, so Software at 16:9 and 16:10 builds none. OpenGL is unchanged:
  banks at every aspect. Everything else (plates, parked HUD, battle bars, weather,
  culls) already scaled with the presented width.

Measured at 21:9 (m = 120), headless, OpenGL (`--headless-opengl`) and
Software (`--headless --renderer software`, internal scale 4):

* **Broad sweep**, 30 steps per direction, banks on/off A/B from one
  savestate every 12th (GL) / 24th (Software) stop. GL: 33 maps, 1,064 stops
  (238 unreachable by warp this time). Software: 32 maps, 983 stops (209
  unreachable). Every measured stop had the ring short (it cannot cover 560
  px), so every reveal pixel came from the banks. Solid-void reveal columns:
  GL 28,347, of which 27,655 beyond the map edge, 331 painted black, 361 lit
  art; Software 28,057 / 27,214 / 293 / 550. **Every lit-art void is the Item
  Shop (216)'s left strip**, world columns 0-42, which the game itself draws
  black: with widescreen off the 4:3 frame at C = 17 shows world 20-40 at
  luma 8 (max 25) where the tile art is lit (5.0b's "shadowed pillar"). No
  other map has one: **unexplained edge voids = 0** on both renderers.
* **Inside the 4:3 frame** (A/B): GL 130 stops paired, 128 with the centre
  320 columns bit-identical in every pair; the other two are the dialogue
  scenes that diverged in 5.0f too (36, 208). Software at native resolution:
  84 paired, 60 identical, the other 24 differ only on animated sprites, the
  clock needle, speech bubbles and water (checked as a diff montage). At
  internal scale the bank's two triangles and the game's rect-drawn FT4 differ
  in 0.33 % (median) of the centre's sub-samples: native VRAM is identical.
* **Narrow maps centre**: every three-tile map (18, 29, 45, 49, 206, 207) at
  C = 32 with 88 px beyond the map edge on each side; every four-tile map
  reached (157, 205, 209, 211, 213, 214, 215, 222) at C = 96 with 24 px each
  side, on both renderers.
* **gapwalk 204/12/69**, 4 walks each, every presented frame: 0 void frames
  (GL 4,182 images, Software 4,222). **7-warp arrival chain**: 0 unexplained
  voids on both, cameras C = 120, 292, 429, 153, 120, 96, 96 (no nudge); the
  205 and 211 arrivals' dark edges classify as 24-px map-edge bars plus 205's
  own black art.
* **Battle at a ring-deficit camera** (map 69, ring short by 5-13 px, plus the
  whole 120-px left): reveal vs an independent decode of the tile file, mean /
  median abs luma, GL L 2.1 / 0.59, R 2.2-2.4 / 0.0; Software L 4.4 / 3.5,
  R 4.3 / 2.9, the same as its own centre (3.5: Software's colour path against
  the decode). HP/MP bars and the "Your Call" HUD in place, enemy bars parked
  out of frame.
* **Characters in the reveal** (isOnScreen against the widened projection,
  NPCs steered into each reveal): GL 71 of 77 in-reveal samples drawn,
  Software 31 of 33. The ones not drawn that were checked (4 GL, 2 Software)
  are below the frame (v > 120); the game's flag agreed with the widened
  projection in 330 of 335 samples overall.
* **Plates**: pause dims every band alike (median drop L/C/R GL 45/48/45,
  56/53/45, 62/56/51 on maps 0/204/69; Software 50/52/48, 61/58/50,
  67/60/55); the warp fade-out likewise (GL 91/96/91, 112/107/91,
  124/112/102). With the menu open the parked clock leaves no piece in the
  left reveal.
* **Weather**: snow on 88, flakes per column L/C/R 18.3/17.5/17.6 (GL),
  17.9/17.5/16.1 (Software); mist on 115-119 fully covers the 560-px window
  in 100/100 samples on both.
* **Without banks** (empty HD texture pack on, GL): the ring tier at 64. A
  4-map sweep (204, 69, 12, 0; 149 stops): contiguous black at an edge 0-56 px
  per side, never more, changing with the camera; 115 stops had some. That is
  what the manifest text tells the player.
* **16:9 and 16:10 unchanged**: gapwalk 204 (4 walks, every frame) 0 void
  frames at 16:9 GL (1,392 images), 16:10 GL (1,399) and 16:9 Software (1,417,
  on banks at the time; on the ring again since, see below); a battle on 69 at each: reveal medians 0-0.59 on GL, 2.8-3.5
  on Software (equal to its centre).
* **Cost**, windowed, process CPU per vblank, standing on 204, alternating
  8-second windows, banks on vs off (off = the game's own ring only): OpenGL
  at 21:9 10.33 vs 9.60 ms median, paired difference +0.85 ms (8 pairs).
  **Software is expensive**: at supersampling 4 (the game's default) a bank
  backdrop is a second full-screen textured raster on top of the ring it
  covers, +12.2 ms per vblank at 21:9 (30.1 vs 17.5) and +10 ms at 16:9
  (33 vs 23); on this machine the game fell from about 15 to 9 vblanks per
  second at 21:9 and from 17 to 13 at 16:9. So Software draws banks only at
  21:9 now. Measured after the change, same method, Software 16:9 windowed
  on 204: 16.46 ms per vblank median with the debug switch on and 16.66 off
  (4 + 4 windows), against 25.33 on / 16.02 off before it (this run; the
  machine was faster than in the run above); game frames per 8-s window
  150-161 against 89-91 with banks. The camera at the 204 arrival is again
  Phase 1's nudged C = 309 (was 257 with banks). Software 16:9: gapwalk 204
  0 void frames (1,430 images), a 4-map sweep (204, 69, 12, 0; 116 stops) 0
  void columns, `banks.frames` 0. Software 21:9: the same sweep plus 2 and 5
  (143 stops) 0 void columns with `banks.frames` 5,002, gapwalk 204 0 void
  frames. OpenGL 16:9 and 21:9: gapwalk 204 0 void frames, 4-map sweeps 0
  void columns, banks drawing (3,455 / 3,254 frames), A/B centre identical
  8/8 at both.

### 5.0i Fishing camera (0.4.x)

FISH (maps 6 and 8) moves the camera itself. `FISH_scrollCameraTo` clamps
its target to the screen positions the `DRAW_OFFSET_LIMIT_X_MAX/MIN` pair
(0x80134DB6/B8) stands for (fish.c:939-947, 4673-4690) and moves CAMERA_X
and DRAWING_OFFSET_X together; it is overlay code, so no hook reaches it.
Measured before: fishing started at the right shore of map 8 centred the
camera on the tamer at the vanilla 448, leaving all 53 px of the right reveal
past the map edge, and it stayed there until the tamer walked; the left shore
gave 27 (26 px).

* **While FISH is loaded (`FISHING_DATA_PTR` 0x80134E6C non-zero) the pair is
  widened** to the camera clamp range (`dw_fish_limits`, at the existing
  tickCameraFollowPlayer entry hook, before its field-camera gates): MAX - lo
  and MAX - hi in place of MAX and MAX - scroll. FISH's own clamp, the
  circle + D-pad pan and the bobber pan then stay inside [m, scroll - m].
  Nothing moves the camera. setupMap writes the vanilla pair on every map
  load; the plugin puts it back itself only if widescreen is switched off on
  the map.
* **The field clamp no longer reads the pair.** setupMap is its only writer
  (map.c:4265-4268; the decomp has no undecompiled function) and sets
  MAX = w*64, MIN = 320 - w*64, i.e. CAMERA_X + DRAWING_OFFSET_X = w*64 at
  every camera position. `dw_clamp_x` uses that (`dw_offset_limit_max`), so
  the widened values never reach it, and a savestate taken mid-fishing
  restores to the same answer (a host-side cache of the originals would not).
  The pair read live matched w*64 / 320 - w*64 on maps 0, 69, 136 and 204,
  and on 8 with widescreen off.
* The per-frame clamp still skips 6/8/136 (the nudge would fight the
  overlays' pans). Curling (KAR, 136) moves the camera through
  moveCameraByOffset / updateDrawingOffsets / tickCameraMovement, all of
  which end in cameraIsAtEdge, the widened clamp; 136 is a three-tile map, so
  the camera is pinned at 32 with 21 px of map edge per side at 16:9. Walking
  the rink kept C = 32; a curling match itself was not reached (it starts
  from a story script).

Measured (`--headless-opengl`, map 8: right shore = the arrival spot, left
shore walked to; triangle -> FISH -> bait, aim, 600-frame circle + D-pad pans
both ways, a cast and the same pans on the bobber, exit, walk both ways, warp
to 69), 27 states per run, read host-paused:

| | right shore: start, aim, pans, bobber, exit | left shore | limit pair on 8 | reveal voids |
|---|---|---|---|---|
| 16:9 | C = 395 (was 448), pans stop at 395 | C = 53 (was 27), pans stop at 53 | (331, -11) | 0 edge columns, 0 past the map edge, every state |
| 21:9 | C = 328 | C = 120 | (264, 56) | 0 |
| widescreen off | C = 448 (vanilla) | C = 14, pans reach 0 | (384, -64), untouched | (4:3) |

CAMERA_X + DRAWING_OFFSET_X = 384 in every state on map 8, walking resumes
normally after the exit, and the pair read (576, -256) after the warp to 69,
its vanilla values. States read mid-pan (the camera springing back toward
the bobber) score high on the art compare because the frame is drawn one step
behind CAMERA_X; their edge voids are 0 as well.

### 5.0j Narrow maps: the edge fill (0.4.x)

36 maps are narrower than the widened view, read off the disc (`MAP` header
width, every map in `MAP_ENTRIES`): 19 three-tile maps (384 px: 18, 29, 32,
45, 49, 84, 86, 92, 102, 136, 137, 148, 150, 151, 156, 206, 207, 224, 235) and
17 four-tile ones (512 px: 82, 91, 98, 121, 147, 149, 157, 164, 205, 209, 211,
213, 214, 215, 217, 222, 231). No map is narrower than three tiles. The camera
centres them (5.0c), so each side shows an even gap past the map edge: 88 px
(three tiles) and 24 px (four) at 21:9, 21 px (three tiles) at 16:9, none at
16:10. On all but a handful the art is lit right up to the edge (the outer
4 px are lit on 34 of the 36 maps on at least one side), so the gap read as a
hard black bar cutting the room off.

**The fill** (`dw_ws_sliver.c`, edge fill): the bank backdrop draws, after the
map's tiles, the gap from the map's own edge tile in each tile row, mirrored
(world column -1-t shows column t; W+t shows W-1-t) and Gouraud-shaded down
to black at the view edge. Gaps of 32 px or less (16:9, and four tiles at
21:9) take one ramp from 0x80 to 0 over a plain mirror. The 88-px gaps take a
mirror stretched 2x (u = t/2: the gap shows the inner 44 px of the edge,
smeared) and two ramps, 0x80 to 0x38 at 6/16 of the gap, then to 0. The right
side samples u = 127 - t, not 128 - t: the rasteriser samples at the pixel's
left edge, and u = 128 at the seam read the NEXT bank slot, a 1-px line of
another tile's art down the seam (seen on every map before the fix). The
arena half (2,800 bytes) holds the worst case exactly: 15 three-tile tiles x
80 + 5 rows x 320 bytes of edge prims.

**Sides left black.** A side whose own outer 8 columns are half or more dark
(luma under 3 of 31 under the map's first palette, every row, measured once
per map from the disc copy) belongs to a room drawn floating in black: the
black past it is the scene's frame, and a mirror only copied a wall standing
a few dozen px inside out into the void as a detached ghost (the igloo, 137,
grew a second dome). That skips 17 of the 72 sides: 84 L, 137 both, 157 L,
205 both, 206 R, 207 R, 213 both, 214 both, 215 both, 217 L, 231 both, 235 L.
Two more are listed by hand: 32's left, where the game draws a dark rock over
the backdrop's edge as a separate object, so the tile art under it is a lit
cave mouth no player sees and its mirror stood beside the rock with a hard
seam (no other map's visible edge differs from its tiles: mirror residual
0.3-2 on all the rest); and 148, whose high-contrast striped toy walls any
mirror turns into bold zig-zags.

**How the look was chosen.** Every narrow map was shot before and after at
21:9 (36) and 16:9 (19) and reviewed by three independent reviewers, one per
set, each followed by a skeptic re-checking the verdicts. The 24-px and
21-px gaps came out a clear improvement nearly everywhere: on the four-tile
maps the fill was better on 12 of 17, about the same on 4 and worse on 213
(now black); at 16:9 it was better on all but 32 and 137, both now black. The 88-px gaps under a plain mirror at 0x28 by 5/16 were
about the same as the bars on half the rooms: symmetric blots (a face in the
cave wall of 29, a two-headed gargoyle chair in 224, an X of roof beams in
206, the serial number on 206's wall read backwards). Four variants were then
shot on the 12 worst rooms and ranked by two judges, one at normal viewing
distance, one zoomed in hunting artifacts. Dropping faster hides the blots
but the bend in the ramp reads as a vertical pillar on bright rooms (92,
102), the sharper the bend the plainer (slope change 12x at 0x20 by 3/16,
4.8x at 0x28 by 5/16, 2.1x at 0x38 by 6/16). The 2x stretch with the gentle
knee ranked first on 9 of 11 filled rooms for the viewing-distance judge;
the zoomed judge preferred the bars overall but ranked the gentle-knee
variants best among the fills. The skip rule and the two exceptions came out
of the same reviews.

The fill draws wherever texture banks are supported, including Software at
16:9 and 16:10, where the backdrop itself stays on the ring: a few narrow quads
per row are not the full-screen raster that made banks too dear there (5.0h).
Without banks (Vulkan, netplay's CPU raster, Software with an HD pack) the gap
stays black. It is pure presentation: no texture, VRAM or guest state changes,
so an HD texture pack's replacement of an edge tile is mirrored with it, and
the HD Backdrops pack needs no new or changed tiles. `dw.ws.banks edges-off` /
`edges-on` switches the fill alone for A/B, and `dw.ws.edges.frames` counts
the frames the edge path ran.

Measured on the final build (`--headless-opengl`, the framework at its
committed state), arrival at every map by warp, fill off then on, black band
at each screen edge over the HUD-free middle rows (luma under 6):

* **21:9**: 238 of 241 maps measured (110, 165 and 220 did not finish the
  warp: 165 and 220 sit in an arrival event, tamer state 10, with the fill
  off too; 110 passed at 16:9). Every filled side of the 36 narrow maps went
  from its 88 or 24 px bar to the last dark pixels of the fade, 1-14 px on
  most rooms and up to 34 on dark ones (45); the sides left black are exactly
  the ones listed above. The 205 wide maps measured the same with the fill
  on and off; the 7 with an edge band (33, 54, 57, 60, 66, 124, 125) are their
  own black art, as before (5.0g).
* **16:9**: 237 of 241 (165, 220, 229, 230 in the same arrival event; 229
  and 230 passed at 21:9). Every filled side of the 19 three-tile maps went
  from 21 px to 0.5-7 px.
* `dw.ws.edges.frames` over 120 frames standing still: 0 on every wide map
  checked (0, 2, 12, 69, 88, 115, 204, 216, 218) at both aspects while
  `dw.ws.banks.frames` kept counting; on 206 at both, on 98 at 21:9 only.
* The menu still hides the clock and gauges on narrow maps with the fill
  (206, 29, 98 at 21:9; 206, 29 at 16:9). With a Beetle-format pack holding
  six of the maps' HD tiles, the fill mirrors the 4x art. Software draws the
  fill at 21:9 (with its bank backdrop) and at 16:9 (fill alone, on the ring).

**Why not generated art.** Outpainting the 72 strips was tried on the local
ComfyUI before settling on the mirror: Z-Image-Turbo with a latent noise mask
(full denoise invented whole neighbouring rooms beside a room drawn floating
in black; entering at step 2-5 over a stretched edge left streaks), with the
whole map as context and a per-map scene prompt (plausible on toy bricks and
cave rock, wrong on ice, where it drew cartoon snowflakes), and
Qwen-Image-Edit 2511 (kept the grey bands as content and moved the picture).
None was better than the mirror across the set. A generated strip would also
be new tiles that every HD pack lacks, and data the repo would have to ship.

### 5.2 W2 — UI counter-squash (built, **dormant**, not working)

The machinery is committed to the framework fork behind
`[widescreen] ui_offset_squash`, which ships `false`.

What is proven: the game's UI layer begins at its own draw-offset reset packet,
identified by DMA source address (`DRAW_OFFSETS`, `0x8015DB90`) — ordinal
counting does not work because a frame's list carries both buffers' world
passes; the phase must persist across the frame boundary; the transform reaches
the rasteriser (an unconditional test shift visibly moved the dialogue box); and
steady-state submissions are correctly squashed.

What is not working: the UI does not visibly counter-squash. The leading
hypothesis — that the paused-cutscene test scene was to blame — was **tested in
live gameplay and refuted**: a field menu's borders sit on identical columns in
both aspects.

Full evidence, the refutation, and the recommended next step (expose an
engagement counter through the existing debug snapshot instead of continuing
with printf archaeology) are in `research/widescreen-w2-dossier.md`.

---

## 6. The cheat library

`cheats.txt` holds **57** GameShark-style codes applied per frame by the
runtime's cheat engine. Change `[ ]` to `[x]` and restart.

### 6.1 Engine

Supported: 8-bit (`30`) and 16-bit (`80`) writes, `D0`–`D3`/`E0`–`E3`
conditionals gating the next line, and the `50` repeat block —
`5000CCSS 0000` repeats the **next** line CC times stepping SS bytes, with the
operand as the value step. Writes are restricted to main RAM.

A conditional on a code word the two executables hold differently tells the
games apart. "Shrink your own collision box" uses `0x800ED2A0`, the drop
roll's `lui` of the `DIGIMON_DATA` address (`0x8013` stock, `0x801F` in 1.5,
patched by no mod): `D00ED2A0 8013` gates the stock write to `0x8012CECC`,
`D00ED2A0 801F` the 1.5 write to `0x801F4318`. The engine only runs after the
game's entry point, so the gate word is always the loaded code.

### 6.2 What is new here

The published corpus for this game (36 CMGSCCC codes and everything derived
from it) contains **no training codes at all** — no perfect training, no
multiplier, no gym or boost codes. Everything in the training and city sections
was derived from the decompilation and, as far as we can tell, exists nowhere
else. Highlights: freeze the game clock (one byte stops the hunger, tiredness,
ageing and lifespan ticks; battles and training still apply theirs), perfect condition, always carry the Training
Manual (+10% to every training result), everyone moves to File City (which also
grants both gym upgrades), item shop stocks 99 of everything.

### 6.3 Inherited codes that were wrong

Verifying every address against the decomp found four real defects in codes
this project had been shipping. A second sweep in 0.3.4 went further and
checked the code that READS each field, which found seven more — see 6.3b.
The first four:

* **The standalone Digipine-effect code set the boost flag with value 0** —
  and because gains are multiplied by `value / 10`, that **zeroed all training
  gains**. Replaced with three self-contained 2×/3×/5× codes that set flag and
  value together.
* **"All gyms upgraded" was missing the Brain gym.** `PartnerPara` contains
  both a bugged, never-read brains counter (+0x6C) and the real one (+0x72).
* **"Max Bits" wrote 99,999,999** — overflowing the six-digit renderer and
  never equalling the 999,999 the money medal checks for, so that medal could
  never unlock.
* **"Max Merit Points" wrote 999** where the clamp is 9999.

### 6.3b The 0.3.4 sweep — right address, wrong value

The four above were wrong addresses. These were the opposite, which is why the
first pass missed them: the address resolved to the right field and the value
was still wrong, because nothing had checked what READS it.

| Code | Was | Now | Why |
|---|---|---|---|
| Press Square to lay a pile of poop | `80138460 FFFF` | `30138460 0008` | The write set every condition bit at once — hungry, injured, sick, needs-toilet and nine undefined bits — in a field saved to the memory card. Only 0x08 is "needs the toilet". The pad gate was left alone; see below |
| Always have Finish available | `8013D65A 0AF1` | goal and progress both `0001` | The gate is an EQUALITY (`battle_tick.c:178`, for enemies `battle_main.c:620`) against `finisherGoal = 3000 - Speed` (`battle_setup.c:116`). 2801 matches only at Speed 199; at any other Speed the code made Finish permanently **un**available |
| Partner takes no damage | `8013D666 03E8` | `8013D66E 0000` | `+0x26` is `invulnerableTimer`, the post-hit recovery countdown — pinning it meant the first hit still landed and the fighter never recovered. Damage accumulates in `hpDamageBuffer` (+0x2E) and effective HP is `currentHP - hpDamageBuffer` |
| Best Happiness / Best Discipline | `00AA` (170) | `0064` (100) | `tickConditionBoundaries` clamps both to 100 every frame, and three evolution branches test `== 100` exactly (`evolution.c:885-892`), so 170 silently blocked them |
| Have all cards | `00FF` ×33 | `0099` ×33 | Cards are packed nibbles, two per byte (`script_interp.c:2489`), and the game never counts past 9 (`:1146`). `FF` put 15 in both halves |
| Smaller aggro radius | (name) | renamed | It writes `DIGIMON_DATA[0].radius`, and record 0 is named "main character" — the player's own radius, which is also the drop-shadow size and half the interaction box |

And three were deleted outright rather than fixed:

* **All Digimon in city** wrote `0x801557C5` and `0x801557C6` — the high byte
  of the partner's `anim.animFrame` and its `frameCount`. It corrupted the
  partner's animation every frame and had nothing to do with recruitment;
  "Everyone moves to File City" does the real thing.
* **Speed up the game** wrote libapi's `_vsync_last`, which VSync rewrites in
  its own epilogue (0x80091DE0). A pure no-op.
* **Monochromon shop points** wrote an address with no symbol, written from
  nineteen places. `MERIT` is at `0x80134FC4` and "Max Merit Points" already
  sets it.

### 6.3c The one this sweep got wrong

Recorded because it is the most instructive thing in this section.

The sweep also "corrected" the two pad-gated codes — `D0135450 FFDF` (Circle)
and `FF7F` (Square) — to `DFFF` and `7FFF`, on the reasoning that the pad word
is active low and Circle is bit 0x2000. The first half is right and the second
is wrong, and the result would have shipped a code that ends the player's run
the first time they walk right. It was caught by an independent re-derivation
and reverted the same day.

**`POLLED_INPUT` does not use the raw hardware mask.** The pad buffer is
byte-swapped relative to it, so the game sees the PsyQ layout. Read it off the
game's own code rather than assuming:

| Evidence | Conclusion |
|---|---|
| `inventory.c:1026-1033` `tickInventoryOptionSelector`: 0x1000 moves a menu cursor **up**, 0x4000 moves it **down** | those two are the D-pad, not face buttons |
| `map.c:3764-3778`: newly-pressed 0x40 extends the interaction box along the way the player faces | 0x40 is **Cross** |
| `script_common.c:830-869`: 0x40 confirm, 0x10 cancel, 0x1000/0x4000 up/down, 0x800 Start | the PsyQ PADL/PADR layout throughout |

So: Triangle 0x10, Circle **0x20**, Cross 0x40, Square **0x80**; Up 0x1000,
Right 0x2000, Down 0x4000, Left 0x8000. A `D0` gate on the raw word inverts
that, giving `FFDF` for Circle and `FF7F` for Square — the values that were
already there.

What made the mistake possible is that the wrong reading is *self-consistent*:
under the hardware mask, `map.c:4475-4478`'s 0x1000/0x2000/0x4000/0x8000 reads
as "the camera follows while a face button is held", which is odd but not
obviously absurd. Under the right one it reads as "while a direction is held",
which is obviously correct. **A convention that merely fits is not evidence.**

The lesson generalises past this one code: **an address being right does not
make a value right, and a plausible convention is not a derivation.** Resolve
the symbol, then read the code that consumes the field — and prefer the reading
under which the game makes sense.

### 6.4 Verifying a code before shipping it

Resolve the address to its `dw_decomp` symbol and offset, cross-check the
containing struct's size against where the *next* symbol starts, and check the
value against the game's own clamp — then, separately, check the code that
READS the field, because an address can be right and a value still wrong.
That last step is what the 0.3.4 sweep added, and it is where the remaining
defects were: a finisher code that only worked at one exact Speed, care values
above a clamp the game applies every frame, and two pad-gated codes whose
button masks were the wrong buttons entirely.

The pad buffer is worth knowing because this sweep got it wrong (§6.3c).
`0x80135450` holds the RAW pad word, which is ACTIVE LOW — `PadRead`
(`0x80091BF8`) reads it and `nor`s it to produce `POLLED_INPUT`
(`0x80134EE4`), the active-high value the game's own code tests — **and that
value uses the PsyQ layout, not the raw hardware mask.** Circle is `0x20`,
Square `0x80`, Cross `0x40`, Triangle `0x10`; the D-pad is `0x1000`/`0x2000`/
`0x4000`/`0x8000`. So a `D0` gate on the raw word is the inverse: `0xFFDF`
for Circle. Derive it from `inventory.c:1026-1033` and `map.c:3764-3778`, never from
the hardware convention.

---

## 7. Tooling

### 7.1 Generators

`tools/gen_*.py` produce the mod manifests. **Regenerate rather than
hand-editing** — every `expected` guard is read from this project's own
extracted executable and overlays at generation time, so a drifted constant
fails loudly instead of corrupting. Never copy a manifest between builds of
different executables.

| Tool | Produces |
|---|---|
| `gen_roster_mod.py` | `dw.vanilla.roster` + the plugin's data include |
| `gen_trainer.py` | `dw.vanilla.trainer` |
| `gen_qol_mods.py`, `gen_gameplay_mods.py` | the QoL and gameplay packages |
| `gen_save_anywhere.py`, `gen_text_speed.py`, `gen_walk_speed.py` | Save Anywhere, Text Speed and Run Speed, imported by `gen_qol_mods.py`. Run alone they only verify; `--out` writes a standalone test manifest |
| `gen_dw15_extras.py` | the two generated 1.5 editions and 1.5's own end-box writes, below a marker in the `dw.1_5` manifest (needs the 1.5 image; `--check`) |
| `gen_newmap_dw15.py` | `glade-15`, Hidden Glade's 1.5 edition, below a marker in the `dw.newmaps` manifest (needs the 1.5 image; `--check`) |
| `gen_roster_dw15.py` | `roster-15`, Expanded Roster's 1.5 edition, below a marker in the `dw.vanilla.roster` manifest (needs the 1.5 image; `--check`) |
| `verify_dw15_states.py` | on 1.5 with every mod ticked, in the Hidden Glade: a savestate load and a Rewind jump (driven through the debug host pad layer, the only input Rewind reads headless) must bring the game back with the editions' data intact; also checks Evolution Unbound's setting reaches its 1.5 edition |
| `verify_roster15.py` | on 1.5, a roster evolution 1.5's own chooser picks from shaped stats: `--source S --target T`, default Airdramon to Gigadramon; checks the raised flag's bank |
| `gen_cheats_mod.py` | `dw.vanilla.cheats` and `dw_cheats_data.inc` from `cheats.txt` (`--check`) |
| `gen_vice_mod.py` | a local-only package that must never be redistributed |
| `gen_newmap_spike.py` | the local-only `dw.newmaps.spike` dev package (§3.1) in `build/mods/installed/`; never commit its output |
| `gen_newmap.py` | the authored glade (§3.2): renders the guide, checks `art/newmaps/glade.png` against the scene, fits collision to it, and writes the shipped `dw.newmaps` package (MAP, TFS, manifest) into `mods/preloaded/packages/`, plus previews and a collision overlay in `build/newmap/` (`--preview` renders only) |
| `paint_newmap.py` | paints the glade's guide into `art/newmaps/glade.png` with local ComfyUI models (§3.2); `--sweep-foliage` writes candidates to `build/newmap/paint/` |

### 7.1b Verification tools

These drive the game and read guest memory, so a claim in this document can be
re-checked rather than believed. Each takes `--port` and runs against a
sandbox memory card seeded from `saves/`; none of them touches your own saves.

| Tool | Answers |
|---|---|
| `ws_sweep.py` | does the widescreen backdrop cover the frame **while walking**? Stops every 12 frames in four directions. Aspect-agnostic |
| `scan_painted_extent.py` | which maps' tile art goes flat black at an edge, and what clamp would hide it? Offline, reads the disc only; a proposal, not a verdict (§5.0g) |
| `verify_roster_evolution.py` | does the game's own chooser pick a roster species from requirements, and does its model actually stream? |
| `verify_roster_triggers.py` | do the roster's raised flags land clear of the game's story flags? Reads the bits either side of an evolution |
| `verify_roster_training.py` | is the relocated training table what a gym actually reads? |
| `verify_model.py` | are a model file's bytes resident in guest RAM? |
| `scan_trigger_ids.py` | which trigger ids do the disc scripts use? Run before widening any guard that writes one |
| `extract_disc_file.py` | pull a file off the disc image by its ISO path, so a probe comes from the same disc as the build |
| `verify_newmap.py` | does a map on row 239 work like a real map? `--map spike` or `glade`; `run1` walks in, runs the map's checks, saves and walks out; `run2` relaunches and continues (the glade: just outside it, then walks in and out and saves next door); `off --map glade --card glade\|host` loads those cards with the mod off; `fight --map glade [--wide] [--brains N] [--no-end-fix] [--drops]` wins, flees and loses the secret fight, and `--brains` crosses a stock end-box threshold, which freezes only with `--no-end-fix` (`GAME.md` 12.1); `measure --map glade --stats ... [--heal N]` plays a real fight, with nothing poked but `--heal`'s stand-in for recovery items, and only prints; `--dw15` runs a mode on Digimon World 1.5, through `glade-15` (§3.1, §3.2, `GAME.md` 7.18) |
| `verify_endfix15.py` | Battle End Freeze Fix on 1.5: `N` (699, 799, 899 or 998) wins a fight on DGHA01 with Brains at N, and checks the end box finishes on its MP bonus message and control comes back (`GAME.md` 12.1) |
| `dis_script.py` | disassembles a DG script or a MAPHEAD section from your disc; prints only (`GAME.md` 4) |
| `dw_tables.py` | writes the map records, species and moves as CSV under `build/tables/`. Derived game data, so it refuses a tracked folder |
| `fight_sim.py` | an estimate of a fight against `BOSS`, from your disc's move data, and how the stock bosses compare (`GAME.md` 7.17) |

The pattern they all follow: **a verdict is a guest-memory read, never a**
**screenshot**, and a probe that cannot fail is not a probe. Each one takes a
control measurement first where a control is possible — the model check is
meaningless unless the bytes are absent beforehand, and it says so and stops
rather than reporting a pass.

### 7.2 `check_mod_collisions.py` — run before every release

Two co-enablable features writing the same byte differently makes the runtime
reject the **whole** mod plan. This tool parses every manifest, understands that
patches gated on different values of one option are mutually exclusive, ignores
packages the packager purges, and exits non-zero on a real collision.

It exists because **Machinedramon + Guaranteed Item Drops** did exactly this:
the drop sweep covered all 180 records while the roster rewrites record 114
wholesale, so enabling both cost the player every mod including widescreen.
Both defaulted off and had never been booted together.

```
py -3.12 tools/check_mod_collisions.py
```

### 7.3 `verify_disc_patches.py` — run before every release

`main_exe` patches are guarded when the mod plan is applied, so a bad one fails
loudly at boot. **`disc_user` patches are not.** The runtime checks their
expected bytes lazily, the first time the containing sector is actually read
(`mod_runtime_patch_disc_sector`). A wrong offset therefore survives startup,
survives the menus, and only trips when a player reaches the content — and the
failure is not local: the runtime prints one line to stderr and sets
`disc_guard_failed`, which disables **every** disc patch in the plan, not just
the bad one. Widescreen and the roster would quietly stop working together.

This gate reads the real disc image, reproduces the runtime's physical mapping
exactly, and proves every guard up front. Features that reach a `[[disc_delta]]`
through `requires_feature` (Evolution Unbound (1.5)) are checked against the
patched image instead, hash-checked against the manifest's `target_sha256`:
by default `../dw15-release/patched/digimon_world_1_5_test1.bin`, or the
second argument. Without it those patches fail; they are never skipped.

```
py -3.12 tools/verify_disc_patches.py [stock.bin [patched.bin]]
```

### 7.4 Packaging safety

The setup packager purges any staged mod package whose manifest marks itself
non-redistributable, then **asserts** afterwards that no marker and no
never-ship package survived — failing the build rather than silently
redistributing another work's bytes.

`verify_setup_package.py` adds the positive half: under `mods/preloaded/packages` only
`manifest.toml` files may ship, plus the Digimon World 1.5 BPS and its font
licences and the Hidden Glade's MAP, TFS and DG script, each at its exact path and SHA-256.
Any other file there fails the gate.

### 7.5 `check_dw15_compat.py` — run when a manifest, hook or the BPS changes

Lists the features that cannot run with Digimon World 1.5 (§4.4). Needs the
stock disc and the 1.5 image, and checks both against the manifest's hashes.
A feature conflicts when a guarded write covers a byte 1.5 changed, a
`disc_user` write lands in a file 1.5 moved, or a hook or plugin-read range it
owns sits in changed code. Hook owners are a table in the tool; a new
`game.toml` hook without an owner is an error.

`main_exe` bytes are compared as guest RAM at the stock entry point, after
1.5's stub has copied its blocks (§4.4). 1.5-only features (a
`requires_feature dw15` constraint, or a `[[requirement]]` on
`dw.1_5/dw15`) are expected to differ from vanilla: their guards are checked
against 1.5 instead. It exits 1 when a 1.5-only guard does not match, a
`dw.1_5` extra lacks the constraint, or the declared conflicts differ from
the computed list. Cheats are reported, not disabled; a write behind a game
gate (§6.1) is listed as stock-only or 1.5-only, and a gate a mod patches or
that passes on neither game is an error.

```
py -3.12 tools/check_dw15_compat.py
```

---

## 8. Framework fork

`github.com/Lordsouth/psxrecomp`, branch `feat/dw15-mod`. Upstream
cannot build this game at all — it rejects the executable because the header's
BSS clear overlaps the sector-rounded code tail. On top of that parser fix the
branch carries: SDL joystick-hint ordering (without which controllers die when
the launcher opens), a Windows `os.utime` crash that killed every rebuild, a
`Path.write_text` call that broke setup on older Python, a
dllexport-on-redeclaration fix without which no overlay compiles under clang, a
frame-pacing guard for monitors where vsync does not actually throttle, the
GameShark cheat engine, the widescreen plugin, the squash-gate export W1 needs,
the dormant W2 machinery, a hash-pinned build toolchain that no longer edits the
user's PATH, the fail-closed packaging guard, and the
`[[widescreen.cull.relative_x_bound]]` cull kind (§5.0e) and live-palette
texture banks (`psx_mod_define_texture_bank_live_clut`, §5.0f), Software
texture banks and `psx_mod_renderer_accelerated` (§5.0h).

---

## 9. Known issues and open work

* **W2 UI counter-squash does not work** (§5.2). Dormant.
* ~~Widescreen's entry hooks never ran.~~ **CLOSED (0.4.0)**: the framework
  update made entry hooks plan-selected (§2.2), and the widescreen manifest
  now names `dw.ws.backdrop-align`, `dw.ws.hud-park` and `dw.ws.tile-ladder`.
  With the tile ladder live the tile switches at camera X 195 instead of 141,
  and Run Speed still matches 1x.
* **`main_exe` writes are re-applied unguarded after a savestate load or
  rewind.** A table in overlay-shared RAM must therefore be patched on disc
  (§4.4, `MOD_PACKAGES.md`).
* ~~Machinedramon's model has never been confirmed on screen.~~ **CLOSED
  2026-09-04.** `tools/verify_roster_evolution.py` dumps guest RAM and searches
  it for three byte probes taken from `MUGE.MMD` on the vanilla disc, with a
  control taken while the partner is still the setup species: absent on all
  three before, found at 0x80024800 / 0x80025800 / 0x80028800 after — the file
  loaded contiguously. The body is also on screen in the run's frames.

  Take the probe file off **your own** disc (`tools/extract_disc_file.py`,
  `/CHDAT/MMD3/MUGE.MMD`, 78588 bytes). A copy of the same name from a modified
  disc measured 79774 bytes here and shares no prefix; probing with it would
  have failed for a reason unrelated to the roster.
* ~~Requirement-driven evolution has never been confirmed by play.~~ **CLOSED
  2026-09-04.** Same tool, and it never writes `EVOLUTION_TARGET`. Greymon is
  one of twelve stock species the roster wires to a new target
  (`to[] = [114, -1, 12, 26, -1, -1]`); with the partner's stats shaped to meet
  Machinedramon's requirements and `evoTimer` past the Champion threshold of
  144, the game's own `getChampionEvolutionTarget` wrote 114, and the sequence
  ran to a guest-written `partner_type` of 114. A Python transcription of the
  chooser predicted 114 independently, so the requirement arithmetic is
  understood rather than merely observed.

  Worth knowing for anyone testing this by hand: **Agumon cannot reach any
  roster species.** Its `to[]` is entirely stock. The twelve stock sources that
  can are types 2, 5, 6, 7, 8, 10, 11, 16, 30, 39, 44 and 50.
* **`save_compatibility` does not reach memory cards.** `isolated` scopes
  savestate slots only (§3.2), so a card
  save made with the roster enabled and loaded with it disabled reads a stale
  table set. Documented in `cheats.txt`; a framework-level fix is the real
  answer.
* ~~The widened raised-flag guard's trigger ids were never audited against the
  disc scripts.~~ **AUDITED 2026-09-04, and it was a real collision. Fixed.**

  `research/roster-band-authoring.md` carried a "VERIFY BEFORE SHIPPING" note
  to check that trigger id 0x272 was script-unused. It is not.
  `tools/scan_trigger_ids.py` decodes the shipped bytecode in `DG.SCN` and
  `MAPHEAD.SCN` -- 1834 sections, zero decode errors -- and finds **88 of the
  117 widened ids referenced by disc scripts**, 45 of them in the range this
  roster's 52 species actually reach. They are the "Jijimon announced that X
  joined the city" family at `0x23C + species`, plus treasure-chest flags
  higher up.

  Because trigger flags are memory-card persistent the aliasing ran both ways:
  raising a new species marked a recruitment announcement as already seen, and
  seeing an announcement made `hasDigimonRaised()` true for a species the
  player never owned -- which `evolution.c:1024` uses to score evolution
  targets, so story progress biased what the partner became.

  `dw_roster_flags.c` replaces `setDigimonRaised` and `hasDigimonRaised` and
  re-banks the new species onto ids `0x2CD..`, above the highest id any script
  references (0x2CC) and inside `triggers[100]`'s 800-bit range. The manifest's
  byte patch at 0x800FF7FC is gone and must stay gone -- a patched function is
  refused by native dispatch, and the replacement hook lives inside the body
  that would be refused.

  **Migration:** a save made before this fix keeps whatever the old aliasing
  set. Those announcements stay skipped and species raised under the old scheme
  read as un-raised. Nothing is corrupted; nothing is recovered either.
* **The overlay table retarget is now seen live in a gym — but a full training
  session with a new species is still not proven.** Partly closed 2026-09-04 by
  `tools/verify_roster_training.py`; be precise about which part.

  *Proven.* With a type-114 partner in Green Gym (map 112) and TRN actually
  resident, all five `TRAINING_ANIM_IDS` read sites in the LOADED overlay carry
  the arena's high half-word — `0x8008B91C`, `0x8008B9FC`, `0x8008BC30`,
  `0x8008BE38`, `0x8008BF50` all read 0x9F00. The relocated entry for type 114
  reads anim 0x2E, frame 0x0A. That matters because the failure mode is
  specifically a frame byte of **zero**: offense training's state 2 advances
  only when its counter EQUALS the table's frame byte, and that state has no
  timeout and no input escape, so a zero parks the session until an int16
  counter wraps. With the stock 68-entry table, type 114 reads 92 bytes past
  the end — in TRN into the string "Technique mastered." (anim 101, frame 32),
  in TRN2 into anim 0, **frame 0**. The relocation is what stops that, and the
  relocation is live.

  *Not proven.* An offense session run to completion. One attempt started a
  session and parked in state 2 for 845 ticks with no stat gain, but the
  stock-partner control never reached a machine, so that observation cannot be
  attributed: the walk that finds a machine is a blind search (the trigger
  coordinates live in the on-disc map script, not in the decompilation), and a
  blind CROSS may start any exercise — HP training is a button-mash minigame
  that will sit waiting for input a scripted route never supplies. **Do not
  cite that run as evidence of a soft-lock, and do not cite the passing checks
  as evidence there is none.** Settling it needs the machine's coordinates, or
  the memory-arm route in the tool's docstring.

  Note the overlay does NOT load on arrival: probing found the region all
  zeroes through 900 frames of standing on map 112, and populated immediately
  after walking into the machine. A test that warps and reads is reading
  nothing — which is what made the first run of this report a "retarget
  failure" that was really an absence.
* **Widescreen, not covered by the plate walk** (§5.0d): for the ~10-20
  frames of an iris in which the circle's edge sits inside a reveal, the game
  draws no band on that side, so a lit strip shows there; the STD lose wipe
  (32 narrow columns) and the scrolling star field stay 4:3-wide. Battle
  flashes (BTL POLY_F4), STD/VS and EAB plates match the rule but were not
  reached live. The
  partner-reappearance cull (§5.0e) is verified in the generated code only,
  and particle flashes still cull at the 4:3 edge.
* ~~Widescreen bank backdrop on Software costs a second backdrop raster.~~
  **CLOSED (0.4.0)**: Software keeps the ring at 16:9 and 16:10 and draws
  banks only at 21:9, where it still pays the +12 ms (§5.0h).
* ~~Fishing's camera clamp (B5) shows map-edge black.~~ **CLOSED (0.4.0)**:
  the limit pair is widened while FISH is loaded (§5.0i). A curling match was
  not reached live.
* **Widescreen bank backdrop** (§5.0f, §5.0h): OpenGL, and Software at 21:9;
  Vulkan and netplay's CPU-authoritative path keep the ring and the sliver,
  so at 21:9 they leave the outer 56 px of each reveal uncovered (there is no
  runtime fallback to 16:9). With an HD texture pack on (OpenGL), banks draw
  and use the pack: each bank slot is declared as the 64x128-word tile upload
  it holds (`psx_mod_texture_bank_add_upload`), and the framework matches bank
  draws against that residency. Verified at 21:9 on map 204 with a 30-tile
  Beetle-format test pack (every tile flipped): the reveals changed with the
  pack on/off (mean luma diff 30/38) as the centre did (17), banks drew at
  m = 120. DuckStation-format packs share the code path but were not run.
  Rewind ships since 0.5.0, but the bank path's rewind behaviour is unmeasured;
  save/load and a cold restore are. The ring itself can show a wrong tile in
  a column at some map-25 stops (banks off: 28.9 luma from the art over that
  column, banks on: 1.7); not chased, since the banks paint over it on GL.
  The field walk route never reached map 205 by warp.
* **Widescreen, Birdra Transport (207)** shows 13 columns of black art past
  its cage on the right (12-14 px at 16:10, inside the 33-35 px bar at
  16:9). A repaint failed (§5.0b) and no camera clamp can hide it (§5.0g).
* ~~Four inherited cheats remain suspect.~~ **Stale**: §6.3 names four defects,
  and all four are fixed.
* ~~Prosperity Points ignore new recruits.~~ **Stale — they have counted since
  0.3.0.** The recruit loop's bound WAS widened
  (`tools/gen_roster_mod.py:640`, `0x800EFAD4` `slti 0x3B -> 0x72`), and the
  trigger-id worry that this text records was checked in 0.3.4 and is
  unfounded for THIS band: ids 0x103..0x139 are the same `0xC8 + species`
  recruit family the loop already reads. The band that really did collide was
  the raised-flags one, which is a different guard and is fixed separately
  (above).

### The flagship, as shipped

**This section described future work until 0.3.0, when it was done.** It is
kept because the reasoning still explains the shape of the roster mod, but read
it as history: the 51 species below are raisable today, and what remains is the
short list at the end.

**Fifty-one finished, unreachable Digimon were already on the disc.** Types
63–113 — Panjyamon, Gigadramon, MetalEtemon, Myotismon, Guardromon, Tentomon,
BlueMeramon, Meteormon and more — have complete records, their own models,
textures, skeletons, voices and battle auras. They were enemy-only purely
because the partner-line tables stopped short.

**Every table wall is down.** All eight type-indexed tables that capped the
partner line are relocated and grown to 180 entries (§4.1), verified
byte-identical to the stock images over their stock ranges. The authoring is
done too: per species, an `EVO_REQ_DATA` row (14 fields),
a `RAISE_DATA` row (28 bytes), `EVO_GAINS_DATA` (7 fields), a training-animation
pair in each of the two overlay tables, and evolution branches grafted onto
existing Champions. It came from a donor-copy pass keyed on level, attribute
and family; balance is still playtesting.

Two things are not solved by the relocation:

* **Evo-chart icons — for all 52, not just slot 114.** The chart is a fixed
  61-slot layout with baked screen positions, so there is no free position for
  a new species and copying a donor entry draws the newcomer on top of that
  donor. `EVOCHART_ICONS` is relocated and grown with the other tables, but no
  icon row is authored for **any** of the 52 — the generator has no
  evochart-authoring path, only the relocation and the cursor fix. Slot 114's
  entry is deliberately left zero and what that draws has not been looked at.
  The player-facing consequence is stated in the changelog: the chart still
  shows 62 cells, so the new Digimon do not all appear on it.
* ~~Prosperity Points still ignore new recruits.~~ Stale; see above.

Full analysis in `research/rel_flagship.txt`. Tier-3 (type ids beyond 179) was
costed and **rejected**: 304 relocation sites, ALLTIM exactly full, and no
models for the new slots.

---

## 10. Research archive

**Every `research/...` path in this document is one of these, and none of them
ships in the package.** They are cited so the reasoning can be traced by anyone
working on the port, not because a player can open them. If you have only the
released zip, the citation is a dead end and that is expected.

`research/` (outside this repository, not packaged) holds the raw investigation
output behind everything above: the reference census of every table reference
site, the free-RAM analysis, the parallel-table landmines, the widescreen deep
tier and backdrop path, the training grading and gain math, the cheat surveys,
and the pre-release audit. Numbers in this document come from those files, with
the addresses re-verified against the executable before use. A search of the
owner's home directory to depth 6, skipping AppData, for `roster-xref-census*`,
`widescreen-w2-dossier*`, `rel_flagship*` and `roster-band-authoring*` finds none of
them, so the citations may be dead for the owner too. Treat a number that rests on
one as unproven until it is checked against the decomp or the executable.
