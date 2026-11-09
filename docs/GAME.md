# How Digimon World works

## 1. About this document

This is the reference for how Digimon World works: the USA release, serial SLUS-01032. It covers the game itself: file formats, scripts, rules, formulas, ids and addresses. How the port changes the game, and how its mods are built, is in [INTERNALS.md](INTERNALS.md). Building the port is in [DEVELOPING.md](../DEVELOPING.md).

It exists because this knowledge lived only in session notes, and those get deleted.

It describes layouts and rules and gives single values where a rule needs them. It does not reproduce stock tables or text beyond the short values and strings a rule needs; read the rest from your own disc. Addresses are USA. Code under `VERSION_IS(JP)` does not apply, and EU code is mentioned only where it shows a fix.

### 1.1 How to read a fact

| Mark | Meaning |
|---|---|
| `src/`, `include/` or `config/` path, no tag | Read in the decompilation. These paths are relative to `dw_decomp`; other paths are relative to the port's root unless marked Horizon. |
| `Disc:`, no tag | Bytes read from the stock disc image with a read-only script. |
| `[run]` | Seen in the real game by the port's tests. |
| `[Horizon]` | From dw1horizon, not rechecked. |
| `[Vice]` | From Vicen04's repo, not rechecked. |
| `[inferred]` | Worked out from other facts, not read directly. |
| `[not run]` | Read in code but never exercised in the game. |

A tag covers the sentence in front of it. A claim that a check refuted is not written as fact; section 12.4 lists the ones most likely to be repeated. Outside pages were read through a summarising model, so anything that mattered was rechecked against the decomp or the disc, and the rest carries its project's tag.

### 1.2 Sources and licences

| Project | Link | Licence shown | What that allows here |
|---|---|---|---|
| dw_decomp | https://github.com/jype0/dw_decomp | MIT (`dw_decomp/LICENSE`) | The primary source. |
| dw1horizon | https://github.com/felixcarmona/dw1horizon | MIT, 2026 felixcarmona, for its own code and content; the decomp and third-party pieces keep their own | Docs and tools can be used with the notice kept. |
| Dw1DataAndPatches (Vice) | https://github.com/Vicen04/Dw1DataAndPatches | None shown | Facts only. Nothing copied; any fix must be written from scratch. |
| DW1-Code | https://github.com/SydMontague/DW1-Code | None shown on the project page (its file list was truncated); archived 2026-10-25 | Facts only. |
| DW1-SydPatches | https://github.com/SydMontague/DW1-SydPatches | None shown on the project page (its file list was truncated) | Facts only. |
| Digimon World 1.5 | Capablemite, https://www.youtube.com/@capablemite | Not recorded by the study | The port ships the author's own BPS unchanged (INTERNALS 4.4). |

### 1.3 The decomp checkout

Commit `aaf69a4491cbf275ffd24df19fe78010151c10f2`, 2026-10-19, "Invert EFE comparison function names", 606 commits, clean tree. Every `src/...:line` here is for that commit; check with `git -C dw_decomp log -1`.

The study compared it with upstream on 2026-11-08: upstream's newest commit was the same one, so the checkout was 0 commits behind. Horizon pins `83f99c5` (2026-10-10), 8 commits behind this checkout, so its citations can be slightly off. `make compare` was not run.

No game code is left as assembly: `src/` has no `INCLUDE_ASM`, and only the PsyQ libraries stay asm (`config/us/main.yaml:64-117`). So a gap in this document means nobody has read that code yet, not that it is missing. The weak spot is names: about 1298 of the 5325 symbols in `config/us/symbols.txt` are unnamed, and some names mislead.

---

## 2. The disc and its files

Citations like `src/main/file.c:28` are relative to `dw_decomp` at commit `aaf69a4`, USA build. LBAs, sizes and
counts of stock rows or cells were read from the stock image and `SLUS_010.32`.

### 2.1 The image
`Digimon World (USA).bin` is 381,771,936 bytes: 162,318 raw Mode 2 sectors of 2352 bytes, with 2048 user bytes at
offset 24 of each. ISO 9660, root directory at LBA 22, 818 files in 36 directories. EXE file offset is
`addr - 0x80090800 + 0x800`. Besides the rows below: `/CARD`, `/DOOR`, `/ETC*` before the maps, `/MOVIE` (134 MB,
LBA 77124) after them, `/SOUND` and `/STDDAT` (LBA 142997-147702) before the overlays.

| LBA | Contents | Notes |
|---|---|---|
| 950-7320 | `/CHDAT` | `ALLTIM.TIM` at 950 (180 x 18,432 B), then 178 `.MMD` |
| 8892-77122 | `/MAP` | 242 MAP/TFS pairs, folders in the order MAP1, MAP10-17, MAP2-9 |
| 142589, 142982 | `/SCN/DG.SCN`, `/SCN/MAPHEAD.SCN` | 804,864 B ending where MAPHEAD starts; 24,686 B (0x606E) |
| 147703-148337 | 16 `*_REL.BIN` overlays | In the root directory |
| 148338 | `SLUS_010.32` | 675,840 B |
| 148668-162167 | `MMM.DAT` | 13,500 sectors, see 2.3 |
| 162168-162317 | Nothing | 150 sectors, subheader 0, no EDC: not mastered data sectors |

### 2.2 Finding a file: two routes
**The table.** `FILE_OFFSET_TABLE` (0x8011FE1C, 12 bytes a row) holds 819 rows of `{sector, size, name pointer}`
(`src/main/file_table_us.c:6`): 817 files, `PSX.EXE` as an alias of `SLUS_010.32`, and a `NULL` row of size 0 that
ends the search. `lookupFileTable` keeps the text after the last `\` and runs `strcmp` down the rows
(`src/main/file.c:16-42`), so folders are ignored and names must be unique. `readFile` reads whole sectors
(`src/main/file.c:85-93`), so a last sector's zero tail is usable. A name not found reads nothing, silently, and the
caller parses stale memory. MAP, TFS, MAPHEAD, TIMs, MMD and sync overlays (`src/main/utils2.c:382`) go this way.

**The ISO directory.** These ignore the table and call `CdSearchFile`: `DG.SCN`, start sector cached once
(`src/main/script_interp.c:2793-2810`); async overlays, `BOSS_EFE.TMD` and the evolving model through the file queue
(`src/main/file_queue.c:128-138`, `src/main/utils2.c:384`, `src/main/script_interp.c:1842`, `src/main/model.c:3635`), though
that model's size still comes from the table (`src/main/model.c:3637`); effect files (`src/main/efe.c:1030`, `src/main/efe_table.c:38`); movies (`src/mov/mov.c:225`).

So moving a file means patching its row, its ISO record, or both: `SHOP_REL` loads both ways
(`src/main/script_common.c:3076,3084`), and the port's `dw_ws_sliver.c` reads the TFS by ISO path (INTERNALS 3.2). The
table cannot grow in place: `SIN_LOOKUP_TABLE` follows it at 0x80122480. String search finds no table reader for 25
rows, which with MGEN17's two could carry 13 new map names by rewriting a name or pointer [inferred]. Scripts and DG
offsets are cached in RAM, so a savestate holds stale copies: test disc changes from a memory card [Horizon].

### 2.3 MMM.DAT: free space, not yet proven in game
**Nothing has been run in game with data placed in MMM.DAT.** The facts about the file are read from the disc; every
use of it below is a reading of code. Proof still owed: one `verify_newmap --map glade` and fight `--wide` with the
files moved.

LBA 148668, 27,648,000 B, 13,500 sectors, all user bytes zero, Mode 2, submode 0x08 (last sector 0x89). The name appears
only at `src/main/file_table_us.c:824` (row 817) and in the EXE's string pool. No shipped manifest's overlay falls there; the
`dw.1_5` BPS delta was not walked. It holds 54 pairs the size of MGEN17's 247 sectors (6 MAP at 39734, 241 TFS at 39740), the
only room proven so far [run], or 28 pairs at 241 + 241; the windows in 2.4 still apply. The route, all [not run]:

- Place sector-aligned files and repoint their table rows, with no code change (Horizon's `dwmap.py mmm` plus
  `file-entry`, which it does not use itself [Horizon]). For the glade that moves two rows, two ISO records and two
  overlay offsets; skipping the records brings back black edges at 21:9. It could then keep 30 tiles and 3 sprite pages
  [inferred].
- New DG scripts at rows 223+: nothing bounds a row (`src/main/script_interp.c:663-665,2810`), but the 0x2000 B table holds 2048 and rows 512+ are entry 0's bytes. A row is an offset
  from `DG.SCN`'s start, so `(148668-142589)*2048 = 12,449,792` plus the sector offset, in whole sectors, and needs
  both `table[N]` and `table[N+1]`. Never touch `table[222]`: it is the file's end and script 221's size.
  `table[223]` on holds stale ASCII a guard must expect. `tools/scan_trigger_ids.py` stops at row 222.

### 2.4 Where each file lands in RAM
| File | Destination | Room |
|---|---|---|
| `.MAP`, `.TFS` (`src/main/map.c:4036,4222`) | `GENERAL_BUFFER` 0x80010000 | To `TEXTURE_BUFFER` 0x80088800: 0x78800 B, 241 sectors each, one after the other. The ESALL bank (`src/main/sound.c:723-733`) overwrites the MAP between them when the bank changes. The TFS stays resident: tile pointers aim into it |
| `MAPHEAD.SCN` (`src/main/script_interp.c:2203`) | 0x801B1D30 | 0x61A8 B. The read is 0x6800 B and spills into the next buffer, harmless because the DG table is read next [inferred]. Bytes past 0x61A8 are lost |
| `DG.SCN` table (`src/main/script_interp.c:2204`) | `SCRIPT_OFFSET_TABLE` 0x801B7ED8 | 0x2000 B, u32 offsets by script id; id 0 means MAPHEAD |
| DG script (`src/main/script_interp.c:662-665`) | `MAP_SCRIPT_BUFFER` 0x801B9ED8 | 0x4000 B, then `SCRIPT_STATE`. Largest stock row after entry 0 is 0x3800 |
| Partner, tamer `.MMD` (`src/main/model.c:3437,3429`) | 0x801878DC, 0x801A1398 | 0x18800 B, 0x7800 B. Largest MMD is `ETEM.MMD`, 97,812 B. NPC models: see 3.11 |

Overlay bases (`config/us/overlay.ld`, `src/main/utils2.c:24`): MOV 0x80010000; BTL, STD, VS 0x80052AE0; KAR
0x80053800; EVL, ENDI, EAB 0x80060000; FISH, DOO2 0x80070000; MURD 0x8007C000; DOOA 0x80080000; DGET, SHOP 0x80080800;
TRN, TRN2 0x80088800. That is 16 overlays on 9 bases, most inside the map window.

So modes wipe the backdrop. A battle builds its effects at `GENERAL_BUFFER` (`src/main/btl.c:455`) with a 0x41000 B
heap (`src/btl/battle_effect.c:2144`), so a win re-reads the TFS (`src/main/tamer.c:863`) but not the MAP, whose
collision, warps and records live in their own arrays. The TFS also returns after evolution, death and the jukebox menu
(`src/main/partner.c:779,809`, `src/shop/script_menu.c:966`) and on routine 0x24 (`src/main/script_interp.c:1821`). A map that loads an overlay must keep its TFS below that
base, since the TFS arrives second [inferred]; stock does (FRZL14 460,408 B under 0x70800 for SHOP).

## 3. Maps

A screen is a `MAP_ENTRIES` row, a MAPHEAD section, a DG script, a `.MAP` and a `.TFS`. `tools/gen_newmap.py` builds one.

### 3.1 MAP_ENTRIES
0x801292D4, 255 rows of 16 bytes; the screen id is a u8 (`src/main/map.c:5397`). Rows 239-246 and 250-252 are empty.
Only MGEN17 has a file and no row. Rows 50 and 61 name files not on the disc and row 65 names `YAKA25`, which belongs
to row 232; no warp reaches those three, so they may be reusable, though two `TELEPORT` ops were not traced [inferred].
Live: `CURRENT_SCREEN` 0x80134DA8 (u8), `PREVIOUS_SCREEN` +1, `CURRENT_EXIT` +2, `PREVIOUS_EXIT` +3, `TARGET_MAP` 0x80134DE0.

| Off | Field | Meaning |
|---|---|---|
| +00 | `filename[10]` | Basename. `buildMapPath` adds `\MAP\MAP<id/15+1>\` (`src/main/map.c:4121`), which the table route ignores |
| +0A, +0B | n8, n4 | TIM counts in the MAP, which does not store them. Stock max 3 and 3 |
| +0C | `flags` | Bits 0-4 sound bank, index into `MAP_SOUND_PARA[22]` (`src/main/map.c:4214`, `src/main/sound.c:310`). Bit 5 is never read and no row sets it. 0x40 no day/night (110 rows). 0x80 read map-Digimon records (223 rows) |
| +0D | `doorsId` | 0 none, else id-1 into `DOOR_MAPDATA` 0x80122E60, 24 rows x 6 models (`src/main/map.c:4070`). All used |
| +0E | `toiletId` | id-1 into `TOILET_DATA` 0x80122E10, 10 rows (`src/main/partner.c:542`). All used. Tile 120 with id 0 would read row -1 [not run] |
| +0F | `loadingName` | Index into `MAP_NAME_PTR` 0x801291BC, 70 names (`src/main/map.c:5327`) |

### 3.2 Load order
1. A warp cell sets tamer state 5: 20 frame fade, name card at fade 10, `changeMap`
   (`src/main/tamer.c:694-702,1367-1384`). Script warps do the same, the name card optional (`src/main/map.c:5277-5319`).
2. `changeMap` unloads the map and NPC models, sets `PREVIOUS_SCREEN = CURRENT_SCREEN` (five screens override it) and
   `CURRENT_EXIT`, and runs MAPHEAD section *id* (`src/main/map.c:5331-5398`).
3. That section's op `FB` stores `CURRENT_SCRIPT_ID` and `CURRENT_SCREEN_ID`, pushes a type 3 stack entry, resets BGM
   and calls `loadMap`, reading nothing from `DG.SCN` (`src/main/script_interp.c:2844-2854`).
4. `loadMap` (`src/main/map.c:4020-4098`): read the MAP; setup; 8bpp TIMs, 4bpp TIMs, objects (only if n8 or n4 is
   nonzero); entity block (warps, tamer and partner at slot `CURRENT_EXIT`, records); doors; collision; sound bank;
   `CURRENT_SCREEN`; waypoints reset; fishing, curling, shop and arena checks.
5. The rest of the MAPHEAD section runs: music, `LOAD_DIGIMON`, `SET_DIGIMON`. Its `FE` pops the type 3 entry:
   `readMapTFS`, `setupMap`, then DG section 0xFE, the arrival section, if any (`src/main/script_interp.c:2697-2707`).
6. After the fade-in, an immediate `checkMapInteraction` (`src/main/tamer.c:1389-1391`): arriving on a trigger fires it.

Continue is the same path with exit 9 (`src/main/map.c:3937`), then saved positions overwrite the spawn
(`src/main/main.c:3463-3466,3802-3803`). No script op reads `CURRENT_EXIT`, so an arrival section cannot branch on it.
New game runs section 0xCC with exit 9 (`src/main/main.c:3455`).

### 3.3 MAP container and setup block
Little-endian. u32 offsets from file start, consumed in order by `loadMap`: setup, n8 x 8bpp TIM, n4 x 4bpp TIM,
objects (only if n8 or n4 is nonzero), entities, collision. A row's counts must match the file: wrong ones give a
missing HUD, coordinates near -32640 and dead warps [Horizon]. The setup block is all i32 (`src/main/map.c:4143-4210`):

| Word | Field | Use |
|---|---|---|
| 0-2, 3-5 | `vp`, `vr` x,y,z | Camera position and look-at point, doubled by the game into `GS_VIEWPOINT` 0x80185BA8 |
| 6-23 | 3 lights | vx, vy, vz, r, g, b each. `GsSetFlatLight`, copied to `MAP_LIGHTS` for time of day |
| 24-26 | Ambient | Dead: read and ignored, `GsSetAmbient(0x800,0x800,0x800)` is fixed |
| 27 | Projection | `GsSetProjection`, `VIEWPORT_DISTANCE` 0x80134ED4. 52 stock values, commonest 1813, 927, 772 |
| 28-31, 32-35 | Regions A, B | See below |
| 36, 37, 38+ | W, H, W*H tile ids | Grid in 128 px tiles, int8. Ids sit in `MAP_TILES`, only ever compared with -1; tiles are consumed in order |

**Regions.** `loadMapSetup` checks A0, B0, A1, B1 and so on, stopping at the first slot equal to
`RAISE_DATA[partner].favoredRegion`: A sets `partnerAreaResponse` 1 (liked), B sets 2 (disliked), no match 0
(`src/main/map.c:4187-4200`). -1 never matches, so the glade is neutral. Ids: 0 heat, lava, underground; 1 cold; 2 holy;
3 machines; 4 water, jungle, swamp; 5 plains; 6 dark; 7 insects; 8 safe, baby [Horizon]. Liked: happiness +1, tiredness
-2 per 1200 area ticks, sleep recovery x1.2. Disliked: happiness -1, tiredness +1, recovery x0.8, and sick once
`areaEffectTimer` passes 12000 (`src/main/partner_impl.c:1354-1357,1393-1401,1858-1866`).

### 3.4 Camera
The backdrop is a prerender and the 3D layer must project onto it. INTERNALS 3.2 reports this checked against the stock
art of all 242 maps; the painted glade matches in game within 1 px [run]. World +Y is down, floor y = 0 [run].

```
vp = 2*(s0,s1,s2)   vr = 2*(s3,s4,s5)   h = s27   W,H = s36,s37   (s = setup words)
f = norm(vr - vp)   r = norm((f.z, 0, -f.x))   d = f x r
q = P - vp          u = 64W + h*(q.r)/(q.f)    v = 64H + h*(q.d)/(q.f)   (backdrop pixel)
```

Camera follow never changes the view: it scrolls backdrop and 3D layer together, keeping `cameraX + DRAWING_OFFSET_X =
64W`, and likewise in Y with `64H` (`src/main/map.c:4265-4272,4374-4379`). `cameraX` starts at `64W-160`, shifts to
centre the tamer, and clamps to `0..128W-320`, `cameraY` to `0..128H-240` (`src/main/map.c:4381-4395`). It follows only
in `GAME_STATE` 0, tamer state 0, with `CAMERA_FOLLOW_PLAYER` set and a d-pad direction held (`src/main/map.c:4472-4495`).

### 3.5 TFS, tiles and time of day
Layout (`src/main/map.c:4225-4263`): a u32 the game skips (not `W | H<<16` on 13 stock maps, MAYO10 and TROP03 among them); u32 nCLUT,
truncated to i16; nCLUT x 256 u16 BGR555 palettes, uploaded to VRAM `(0, 481+i)`; then per non -1 grid slot, in order, a
skipped u32 and 128 x 128 bytes of 8bpp pixels. Size = `8 + 512*nCLUT + 16388*tiles`, true of all 241 stock pairs; a -1
slot takes no bytes. Use nCLUT 1 with flag 0x40, or 3: stock has 108 and 130 of those, while MAYO01_2, TWNA02 and
MIST02 have 4-5 and overrun the 3 pointers of `MAP_CLUTS`, and MIHA03 has 1 with no flag, so its dusk and night palettes come from stale pointers [inferred]. Colour 0x0000 is the transparent texel and 0x8000 opaque black (PS1 GPU rule [inferred]),
which is why `tools/gen_newmap.py` reserves index 0. Tile (col, row) goes to VRAM `(768 + 64*(col%4), 128*(row%3))`, drawn
through the live palette at row 480 and at OT 0xFFF, behind everything (`src/main/map.c:4006,4252-4258,4346-4347`). VRAM
holds a 4 x 3 window; `handleTileUpdate` uploads a new column or row from RAM as the camera moves
(`src/main/map.c:4659-4816`). `updateTileRow` tests -1 on the window's top row whichever row it loads, so a -1 slot can
blank a real tile or upload from NULL (`src/main/map.c:5223`): nine stock maps have them, new maps should not.

**Time of day.** `updateTimeOfDay` applies on load (`src/main/map.c:4316-4333`): hour 6-15 CLUT 0, lights x1; 16-19
CLUT 1, r x1, g and b x0.7; 20-5 CLUT 2, all x0.5. Flag 0x40 loads CLUT 0 with no scaling and no fades
(`src/main/map.c:3968,4311`). Otherwise a fade starts when `CURRENT_FRAME % 1200 == 0` at hour 16, 20 or 6, blends row
480 over 25 frames in `GAME_STATE` 0 and then switches the lights (`src/main/map.c:3969-3981,4545-4602`). 1200 frames
are one game hour at time speed 0 (`src/main/clock.c:96-146`). `verify_newmap` sees an authored map's palettes go live at 16:00 and
20:00 [run]. Sprites with clut -1 follow by sampling row 480; sprites with their own CLUT do not [inferred].

### 3.6 Map objects and sprites
TIMs upload to the rect in their own header, and all 580 stock TIMs fall inside x 384-767, y 0-255: three 8bpp pages
128 halfwords wide, or six 4bpp pages 64 wide. An 8bpp CLUT uploads unless its row is 480, which is how a sprite samples
the live palette (`src/main/overworld.c:388`). Only the first 4bpp TIM uploads its CLUT, cut into 16-colour rows at
`(16*i, 486)` (`src/main/overworld.c:405-413`). The object block is i16 with no bounds checks: nDefs, nDefs x {texX,
texY, w, h, x, y, z, clut, trans}, into 180 rows (FACT08B has 186); nInst, nInst x {frames[8], times[8], x, y, flag},
into 188 slots (TRAI00 uses all) (`src/main/overworld.c:331-374`).

| Field | Meaning | Source |
|---|---|---|
| `texX`, `texY` | `texX/256` picks the page, `texX%256`, `texY%256` are the UV | `src/main/overworld.c:753-773` |
| def `x,y,z` | World anchor for depth only. OT = GTE z >> 2 of the first real frame's anchor, minimum 0x23, 0x1000+ becomes 0xFF6, y = 0x2710 forces 0xFFE, flag 0x80 forces 0x1E. Models sort on the same scale (`src/main/model.c:2919`), so anchor where the object meets the ground [inferred] | `src/main/overworld.c:431-462` |
| `clut` | -1: 8bpp, row 480. 0-15: 4bpp, `(16*clut, 486)`. 16+: 8bpp, row `clut+468`. Stock: -1, 0-8, 16-18 | `src/main/overworld.c:762-773` |
| `trans` | 4 opaque, 0-3 GPU blend mode. Blending needs the texel's STP bit [inferred] | `src/main/overworld.c:506-510` |
| `frames`, `times` | Definition ids, -1 ends the loop, -2 a blank frame. Times are frames per step, u8 in RAM | `src/main/overworld.c:490-545`, `include/dw/map.h:41` |
| inst `x, y`, `flag` | Backdrop pixel of the top-left corner. Flag 1 hides (the cleared default), 0x80 snowflake; stock files use 0 and 1, script op 0x57 changes it | `src/main/overworld.c:326,496,737-740` |

### 3.7 Entity block: MapWarps
It comes first, is 120 B, and is **six parallel arrays**, not ten records, copied to `MAP_WARPS` 0x80138730
(`src/main/tamer.c:158-165,390`). There is no warp count. All i16[10]: `spawnX` +00, `spawnY` +14, `spawnZ` +28,
`rotation` +3C, `targetMap` +50, `targetExit` +64. -1 means no target; a live doorway at -1 can fade to black [Horizon].

Slot n is one pair: spawn n is where you appear via exit n, and cell 110+n leaves through n, setting `CURRENT_EXIT =
targetExit[n]`, `PREVIOUS_EXIT = n` (`src/main/tamer.c:694-698`). Fleeing returns to spawn `PREVIOUS_EXIT` of
`PREVIOUS_SCREEN` (`src/main/script_interp.c:1933-1935`); slot `CURRENT_EXIT` aims the tamer's flee run back along its rotation
if he is still on his start cell (`src/main/battle_tick.c:149-163`). The partner lands 200 units behind the tamer by quadrant, facing rotation + 0x800
(`src/main/tamer.c:400-410`). Stock `spawnY` is 0 on 2,402 of 2,410 slots; y 2600 puts the partner above the enemy and
the battle never ends [Horizon]. Keep arrivals 2 cells off their own exit, and wild Digimon farther from any arrival than
tracking range + 300, or a fade-in fight can start with no enemy [Horizon].

### 3.8 Entity block: map-Digimon records
Then an i16 count and the records, all i16, read only with row flag 0x80 (`src/main/overworld.c:1035-1084`); without it
the word is ignored and can be anything (up to 63 on the 18 stock maps without it). Stock has 0-8 per map, 48 maps use all 8, 989 records in
total. 84 B + 6 per waypoint. Slot n is `ENTITY_TABLE[n+2]`, `MAP_DIGIMON_TABLE` 0x8013CB50 (8 x 0xC4), `NPC_ENTITIES`
0x80155828 (8 x 0x68). Everything is re-read from the file on each map load.

| Word | Off | Field | What it really does |
|---|---|---|---|
| 0 | +00 | `typeId` | `SET_DIGIMON`'s type must match or nothing spawns, silently (`src/main/overworld.c:1095`) |
| 1 | +02 | `followMode` | See below. Stock: 12 x441, 1 x372, 2 x175, 16 x1 |
| 2-7 | +04 | pos, rot x,y,z | Spawn, home and centre of the tracking square; starting rotation, y is the facing |
| 8 | +10 | `trackingRange` | Half-width of the square on x and z (`src/main/overworld.c:1391-1394`). Drives looking and chasing only, **not fights**: 385 stock records have 0 |
| 9 | +12 | `unk2` | Dead. No reader outside the loader; 0 on all 989 |
| 10 | +14 | `scriptId` | DG section run on contact, and this NPC's id in scripts (`src/main/script_value.c:131-150`). Stock 5-12 |
| 11-14 | +16 | hp, mp, current HP, current MP | Base, then starting values applied each visit; 15 stock records differ. After any battle on that load, current is reset to base (`src/btl/battle_setup.c:184-193`, `src/btl/battle_main.c:755-762`) |
| 15-18 | +1E | off, def, speed, brain | Battle stats. Brain below 297 can go senile (`src/btl/battle_main.c:546-552`) |
| 19 | +26 | `bits` | Paid on a win, summed over enemies (`src/main/battle_ui.c:306-309`). Stock max 9999. No merit or drop field exists; drops come from `DIGIMON_DATA` |
| 20 | +28 | `chargeMode` | Dead for enemies: replaced at the first decision by random 0 or 1, or 2 off screen (`src/btl/battle_main.c:639-644`) |
| 21 | +2A | `unk1` | Live despite the name. 1: keeps the partner as target, no bonus. 0: 71% retarget at an enemy that hit it, +20 weight on range 3 moves while another enemy lives. Other: retarget, no bonus (`src/btl/battle_main.c:3697-3704,3840-3846`). No effect in a solo fight |
| 22-25 | +2C | `moves[4]` | 0x2E + index into `DIGIMON_DATA.moves`, 0xFF empty; species 60 with 0x3C gets tech 0x70 instead (`src/main/utils2.c:338-341`). Only slot 3 can fire a finisher (`src/btl/battle_main.c:617`) |
| 26-29 | +34 | `movesPrio[4]` | Pick weights (`src/btl/battle_main.c:3820-3873`). Dead on a finisher slot. An empty slot keeps its weight and means an 80 frame wait. Keep one usable slot weighted or the pick reads past the array |
| 30-32 | +3C | flee x,y,z | Not a destination: on a win with trigger 1 clear (`src/btl/battle_main.c:925`), read once as a bearing when anim 0x2C ends. The enemy walks on by root motion, no collision or stop, until all enemies are outside a 320x240 box, with no timeout (`src/btl/battle_main.c:948-961`). Only a species whose anim 0x24 does not loop or has no forward motion can hang here [inferred] |
| 33-41 | +42 | Waypoint count, `aiSections[8]` | Count unchecked, array holds 8, stock max 8 (`src/main/overworld.c:14,1078`). Steps in order: 0 wait, 1 walk (anim 2), 2 run (anim 4), -1 restart; step i uses waypoint i (`src/main/overworld.c:1363-1386`) |
| 42+ | +54 | Waypoints x,y,z | Wait: x is the frame count and the timer is int8, so over 127 never finishes (`src/main/overworld.c:36,1707-1709`); no stock wait exceeds 127. Walk, run: done on reaching the target's collision cell (`src/main/overworld.c:1770-1778`) |

Records appear only when a script places them, normally the MAPHEAD section: `LOAD_DIGIMON type`, then `SET_DIGIMON type slot autotalk`,
which also sets `isOnMap`, so a beaten enemy returns on the next load (`src/main/overworld.c:1114`). Autotalk 1 runs the
section on contact from either side; 0 needs a fresh confirm while touching; other values never fire; all need no script
running (`src/main/tamer.c:669-679`, `src/main/overworld.c:1324-1337`). Contact is a one-step-forward box test of +/-
radius from `DIGIMON_DATA`; a fresh confirm while standing adds 50 units on the facing side (`src/main/map.c:3414,3763-3779`). Contact
only runs the section: a fight comes only from `START_BATTLE` in a script. Waypoints pause while a mode is looking or chasing.

| followMode | Start test | Behaviour (`src/main/overworld.c:1239-1304`) |
|---|---|---|
| 1, any with no case | None | Waypoints only |
| 2, 11 | Square | Idle anim, turns toward the tamer at 0x71 per frame while inside |
| 3, 4, 5, 12, 13, 14 | Square | Walks, then runs, to where the tamer was seen; on arrival or contact waits 40 frames, walks home (`src/main/overworld.c:1548-1624`) |
| 6, 15 and 7, 16 | Radius: `dx + dz`, signed and unsquared, under `(range*6/10)^2`, so a half-plane, always true at range 237+ (`src/main/overworld.c:1454-1459`) | Chases with anim 2 (6, 15) or anim 4 (7, 16) while the test holds, returns home in that anim, waits 80 frames (`src/main/overworld.c:1626-1693`) |
| 8, 9, 17, 18 | Square, then radius | Same with anim 2 |

### 3.9 Collision
Last block: 10,000 bytes, 100 x 100, copied to `MAP_COLLISION_DATA` 0x801AF398, index `tx + tz*100`
(`src/main/map_collision.c:23-30,45`). Triggers need `TAMER_STATE` 0 (`src/main/tamer.c:684-712`).

| Byte | Effect | Gate |
|---|---|---|
| 0 | Floor | |
| 1-50, 121-127 | Nothing | On no stock map |
| 51-79 | Runs the DG section of that number on step | No script running. Fires again whenever idle and still on the cell |
| 80-109 | Runs it on a fresh confirm press | No script running |
| 110-119 | Warp through slot n | Warps do not check for a script |
| 120 | Runs MAPHEAD section 0x4E2 | Partner has `CONDITION_POOPY` (mask 8), no script running |
| 0x80-0xFF | Wall, no trigger | Any byte with bit 7 blocks (`src/main/map_collision.c:95,140`). Stock uses only 0x80 |

Stock values are 0, 51-73, 81-86, 110-114, 116, 120, 0x80. A section missing from the script makes `SCRIPT_POINTER` NULL;
only 0xFE is null-checked (`src/main/script_interp.c:2253-2255,2703`). Op 0x7C walls cells (`src/main/map_collision.c:190-204`).

The wall test looks a full radius ahead and half a radius sideways: moving along z it checks cells across `x +/- r/2` at
`z +/- r`, and the mirror for x (the two functions' names are swapped) (`src/main/map_collision.c:57-146`). The tamer's
radius is 100, so a one-cell lane is impassable, and his centre never stands in a row touching a wall, so a warp strip
against a wall must be two rows deep. The MAYO03 entrance shows this: it fired only with the wall row changed too [run].

### 3.10 Coordinates and rotation
```
tx = x/100 + 50 - (x<0)     tz = 50 - z/100 - (z>0)     (C truncation; src/main/map_collision.c:37-44,150-157)
cell centre = ((tx-50)*100 + 50, (50-tz)*100 - 50)
```

The world spans +/-5000 on x and z, 100 units a cell, +x toward column 99, +z toward row 0 (this project calls them east and north; the camera decides how they look). Rotation is 12-bit, 0x1000 a full
turn (`src/main/map.c:3396`, `src/main/math.c:32-77`): **0 faces -z, 0x400 -x, 0x800 +z, 0xC00 +x**. Horizon's `world.md`
(0 as +Z) and `maps.md` (partner in front) are wrong here. Tamer headings are multiples of 0x200 relative to the camera:
`_atan(vpz-vrz, vpx-vrx)` rounded to 0x200, plus 0 for down, 0x400 left, 0x800 up, 0xC00 right, +/-0x200 for diagonals
(`src/main/tamer.c:558-585,601-603`). `rotateEntity` has no branch for an exact 0x800 difference, so that turn never
finishes (`src/main/overworld.c:1513-1540`): NPC tracking recovers when the tamer moves, but a script waiting with op 0x4A
hangs, and a talk section's first `SET_DIALOG_OWNER` queues a mutual look-at by itself (`src/main/script_interp.c:2741-2747`).

### 3.11 Hard-coded screens and limits on a new map
Screens with special code, none of them a free row: 0x58-0x60, 0x84-0x87 scatter the first 0x23 instances as snow
(`src/main/overworld.c:364-372`); 115-119, 121, 163, 220 mist (`src/main/overworld.c:481-485`); 101-103 warp crystals, 0xA5
training poop (`src/main/map.c:4073-4078`); 6, 8 FISH and 136 KAR (`src/main/fish.c:123`, `src/main/kar.c:10`); 0x83, 0x85,
0x86, 0x8D, 0xCF, 0xD3, 0xD7, 0xDA SHOP and 0xD0, 0xDF DGET (`src/main/script_common.c:257,346`); 18 screens where a
blocked line keeps an NPC out of a fight (`src/main/btl.c:109`); 0x8F no drops (`src/main/battle_ui.c:195`).

| Resource | Limit | Status |
|---|---|---|
| Grid | W*H <= 35: `tiles[35]` and `MAP_TILES` (`include/dw/map.h:49`). W <= 11, H <= 8: `handleTileUpdate` has window cases only up to there (`src/main/map.c:4665-4789`). W >= 3, H >= 2 keeps the clamps positive [inferred] | Read. Stock spans 3x2 to 10x3 and 4x8, max 35 slots (7x5, with 5 -1) |
| TFS, MAP size | 241 sectors, so 30 real tiles with 3 CLUTs (493,184 B of 493,568). Soft: MAYO01_2, TWNA02, MIST02 round to 242 and spill into `TEXTURE_BUFFER`. Largest stock MAP is MIST06, 268,692 B | Read; spill harmless [inferred] |
| Sprites | 3 8bpp pages, 180 definitions, 188 instances, 8 frames each | Read. Nothing checks them |
| Map Digimon | 8 records (`src/main/overworld.c:1161`); species below 0xB4 (`src/main/model.c:3369`) | Read. Count unchecked |
| NPC models | `LOADED_DIGIMON_MODELS` holds 8 and drops the rest (`src/main/script_interp.c:2561-2571`), but `NPC_MODELS` has 5 slots (`src/main/model.c:2698`). Same species shares a slot. Index k maps to page 0x16+k/2, CLUT `((k+2)*16, 488)` (`src/main/model.c:3400-3403`) | Read. Index 4 overlaps `SBOY.TIM`'s pixels and `SYSTEM_W.TIM`'s CLUT, so 4 are clean. A sixth returns NULL [not run] |
| Heap | `_end` to stack: 197,520 B (0x801BFB70-0x801EFF00, `src/main/main.c:3537-3538`). A map needs MMDs in whole sectors, plus bones x (0x88 + 0x52) per placed Digimon (`src/main/model.c:2829-2830`), plus 0x4800 while loading (`src/main/model.c:3299`). A failed `malloc3` reads to NULL (`src/main/model.c:3410-3414`). Only `model.c` and `std_setup.c` call it | Read. Stock free heap is 40.2 KB to 192.9 KB by screen [Horizon]. Vice's randomizer re-rolls two-group areas until combined model weight is at most 152,000, unit not stated [Vice] |
| MAPHEAD tail | 0x6070-0x61A8: 312 B for all new sections; the glade's takes 20. Every row has a table entry to repoint. Free rows hold a filler binding script 180, a real cutscene: never ship one | Read. Tail conflicts with `dw.1_5` |
| DG script | 0x4000 B. No slot is free: MAPHEAD binds 1-198, cross-calls reach 199-221 | Read. Rows 223+ [not run] |

Unknown, and not to be assumed either way:
- What fails first on a busy map: heap, the 0x41000 effect heap, VRAM or SPU. Nobody has measured free memory on row 239
  with its boss loaded, or how many NPCs it can hold.
- `LOAD_DIGIMON` inside a talk: Horizon warns of hangs, but stock runs two in one dialogue section (script 43 section
  0x33) and no cause is known. Loading in MAPHEAD is the safe rule.
- Evolution and death scenes on a new row: a search of `src/evl`, `src/dooa` and `src/murd` for `CURRENT_SCREEN` and `MAP_ENTRIES` finds only a sound reload (`src/evl/evl.c:576`), the overlays were not read in full, and no test covers them.

---

## 4. Scripts and text
Who stands on a map, what they say and when a fight starts are bytecode in two files, run by
`src/main/script_interp.c`. Every stock op's name and operands are in `include/dw/script.h:21-125`; this section covers
what that list does not say. "Stock decode" means every section of both files walked read-only with the decoder in
`tools/scan_trigger_ids.py`, 0 errors (it stops at `FB`; told to carry on through row bodies it finds no new trigger
ids). A bare `:NNN` stays in the last file named.

### 4.1 MAPHEAD.SCN and DG.SCN
**MAPHEAD.SCN** is at LBA 142982, 0x606E bytes, found through `FILE_OFFSET_TABLE` and read whole at boot into
`MAPHEAD_SCRIPT_BUFFER` 0x801B1D30, 0x61A8 bytes (`src/main/script_interp.c:2203`). It holds 281 sections: rows 0..254 and
26 specials 0x4C4..0x4E5. Its room is the tail 0x6070..0x61A8: 312 bytes for all 11 free rows, of which the glade's
section uses 20 (`MH_NEW` in `tools/gen_newmap.py`). Boot reads whole sectors, 0x6800 bytes (`src/main/file.c:91`), so anything past
the buffer lands in `SCRIPT_OFFSET_TABLE` and the table read straight after overwrites it [inferred].

**DG.SCN** is at LBA 142589, 0xC4800 bytes, ending where MAPHEAD starts, so no tail slack. The ISO directory finds it; the
start sector is cached after the first read (`src/main/script_interp.c:2793-2804`). Its first 0x2000 bytes are u32 offsets, read at boot into
`SCRIPT_OFFSET_TABLE` 0x801B7ED8 (`:2204`). `getScript(id)` reads table[id]..table[id+1] in whole sectors, unchecked, into
`MAP_SCRIPT_BUFFER` 0x801B9ED8, 0x4000 bytes, and caches the id in `ACTIVE_MAP_SCRIPT` (`:647-668`, `:2815`). Past 0x4000 a
script runs into `SCRIPT_STATE` [inferred].

- Id 0 means MAPHEAD, already in RAM (`:653`). Its block at 0x800 is a redundant copy; 0x2000..0x6FFF is never read.
- 1..198 are all bound by MAPHEAD rows (stock decode). 199..221 are library scripts, reached only by file jumps (`14`, `17`).
- table[222] = 0xC4800, the file's end, sets script 221's read size: never change it. table[223..511] is stale ASCII and zeros: a guard must expect it.

Both files share one layout: u16 first-body offset (0x468 in MAPHEAD), then {u16 section, u16 offset from the script
start} pairs ended by 0xFFFF, then the bodies. Only `setMapHeadActive` reads the first word (`:671-689`, `:2181-2188`).

No slot is free. The largest are 0x3800. Slot 118 (offset 0x55000, 0x800 bytes) was the cheap room: stock it is one bare END
as section FE, and only row 122 binds it. A row N in 223..510 pointing into `MMM.DAT` should give a script its own slot on stock
code: store (148668-142589)*2048 = 12,449,792 plus the sector offset, in table[N] and table[N+1], whole sectors [not run].
The scan tool stops at row 222, so it would not audit it. Savestates hold the cached script: test edits from a card [Horizon].

### 4.2 Section numbers
| Section | In | Meaning |
|---|---|---|
| 0..254; 0x4DE; 0x4E2 | MAPHEAD | Row section; lost battle and partner death (`src/main/script_interp.c:1926`, `src/main/partner_impl.c:1912,1943`); toilet tile (`src/main/tamer.c:692`). Other specials: items, tournament, shop, menu text [Horizon] |
| 5..12 | DG | Talk sections by stock convention: the number is the record's `scriptId` (5.4) |
| 51..79, 80..109 | DG | Cell of that value: run on step and while idle on it; run on a fresh confirm (`src/main/tamer.c:703-711`). Also fires on arrival (`:1391`) |
| 0xFE | DG | Arrival, after the map loads. The one section allowed to be missing (`src/main/script_interp.c:2702-2707`) |
| 0x47..0x49; 0x0B | Scripts 165, 178; 102, 168, 209, 213 | Loss continuations on the death rows, which jump to script 212; a species byte list, not code |

Any other section named but missing sets a NULL script pointer: talk, tile, `7B`, file jumps (`:680-684`, `:2711`).

### 4.3 How a row loads
A row section is `FB 00 script:u16 row:u16`, setup ops, `FE 00`; `changeMap` starts it and ticks it once (`src/main/map.c:5397`, `src/main/script_interp.c:2235-2241`).
`FB` stores `CURRENT_SCRIPT_ID` and `CURRENT_SCREEN_ID`, pushes a type 3 stack entry and calls `loadMap`, reading nothing
from DG (`:2844-2854`). The setup ops run music, `LOAD_DIGIMON`, `SET_DIGIMON`. Stock rows branch here, which is how an NPC
appears under a condition: `19 00 01 00 tt tt 18 00 oo oo 19 00` jumps to oo when trigger tt is set. `FE` pops the entry:
`readMapTFS`, `setupMap`, DG section FE (`:2697-2707`). No op reads `CURRENT_EXIT`, so an arrival cannot tell the way in.

### 4.4 The interpreter and the stack
`IS_SCRIPT_PAUSED` == 1 means no script is running, the opposite of its name. `tickScript` runs per frame while one is: text
box, scripted movements, pending map change, wait state (`67`, `64`, `4A`, `10`, `1A`), then ops (`:395-415`, `:600-632`). A
handler ends with `longjmp` 1 for the next op now, 2 to yield a frame, 3 to change map. An opcode outside 0x10-0x3F,
0x46-0x58, 0x5A-0x5F, 0x64-0x7E, 0xFB-0xFF ends the script silently (`:604-618`), so a wrong operand width fails by desync.

`scriptPauseGame`, the first time in a run, locks tamer and partner, stops the camera following, sets pstat 0 to 3 and stops
game time (`:2727-2751`). `1B` calls it; so do most entity ops. At the end boxes close, pstat 0 is restored, and only if a
pause happened do movement, camera and time come back (`:634-641`).

The stack is 8 entries of pointer, script id, type, argument, with no overflow check (`:2368-2374`). END (`FE`, `FF`) pops in
a loop until empty, by type (`:2691-2719`):
- Type 1, pushed by `13`, `14`: discarded. Only `15` returns, so an END inside a call ends the whole script.
- Type 3, pushed by `FB`: loads the TFS, sets up the map, runs section FE.
- Type 4, pushed by `4B`, `58`, `7B`, a fled battle: runs that section of `CURRENT_SCRIPT_ID`, by then the next map's; 0xFF none.

### 4.5 Jumps and calls
| Bytes | Op | Effect (offsets from the script's start) |
|---|---|---|
| `16 00 oo oo`; `13 00 oo oo`, `15 00` | JUMP_TO; JUMP_AND_LINK, JUMP_RETURN | Jump; call and return, in this script |
| `17 00 ss ss nn nn`; `14 ..` same | JUMP_TO_FILE; _AND_LINK | Load script ss over the buffer (a disc read unless cached), enter section nn; as a call, the return re-reads the caller (`:722-737`) |
| `18 pp cc cc oo oo..` | SWITCH | Index by pstat pp; past the end takes the last entry (`:749-758`) |
| `4B ss ee nn` | WARP_TO | Map ss, exit ee, then section nn there (`:1402-1411`) |
| `58 pp`; `7B nn` | TELEPORT; SECTION_ON_EXIT | Map and exit from pstats pp, pp+1; run section nn after the next map change |

**IF** is `19 00`, entries of a condition byte, a pad 00 and operands, then the exact byte `19 00`. Each test updates one
result: bit 0x80 ANDs, 0x40 ORs, neither replaces (`:917-998`). `cond & 0x38` picks the entry:
- 0x00, u16 trigger: low bits 0 tests set, otherwise clear.
- 0x08, u8 pstat, u8 value: low bits pick ==, !=, >=, <=, >, < (`:1001-1027`).
- 0x10, u16 offset: jump if the result is true. 0x18, u16 offset: jump if false; keep the low bits 0, since 0x19 ends the block.
- 0x20, by low bits (`src/main/script_value.c:183-386`): 0 stat, 1 card, 4 item: u8 id, u8 compare, u16. 2 move, 3 condition:
  u8, u8 negate. 5 money: pad, u8 compare, s32. Stat ids are in `include/dw/script.h:128-150`.

**SET_SELECTION** is `10 nn`, nn u16 offsets, `1A 00`, text, `12 00`, cancel code. Nothing falls through: confirm jumps to
the chosen offset, and Triangle jumps to the end of the text plus 2 bytes unless trigger 0x31 is set
(`src/main/script_textbox.c:243-258,1040-1041`). Without the filler Triangle lands inside the next op.

### 4.6 Ops new content needs
Groups: 0x10-0x27 flow, text, triggers, pstats. 0x28-0x3F items, bits, dates, moves, cards, merit, stats. 0x46-0x58 entities,
camera, warp. 0x5A-0x5F sound. 0x64-0x7E routines, battle, delay, movement, spawns. Width is bytes after the op; `_` is a pad.

| Op | Name | Width | Operands |
|---|---|---|---|
| 1B, 27 | SET_DIALOG_OWNER, FADEOUT_HUD | 1 | id (4.10); box, 0 is the dialog box |
| 1A | SHOW_TEXTBOX | text | `_`, text (4.9); waits for a press |
| 1C, 1D; 1E, 1F, 20 | SET_, UNSET_TRIGGER; SET_, ADD_TO_, REDUCE_PSTAT | 3 | `_`, u16; `_`, pstat, u8 (add and reduce clamp to 0..255) |
| 28, 29; 2A, 2B | GIVE_, REMOVE_ITEM; ADD_, REDUCE_MONEY | 3; 5 | `_`, item, amount; `_`, s32 |
| 46, 79; 48, 4A | LOAD_, UNLOAD_DIGIMON; UNLOAD_ENTITY, WAIT_FOR_ENTITY | 1 | species; id, 0xFF to wait for every movement |
| 47 | SET_DIGIMON | 3 | species, slot, autotalk |
| 4E, 6C | ENTITY_WALK_TO, ENTITY_MOVE_TO | 7 | id, x, z, anim or duration in frames, u8 |
| 56, 6A | PLAY_ANIMATION, SET_AUTOTALK | 3 | `_`, id, value |
| 74, 75 | SPAWN_ITEM, SPAWN_CHEST | 5, 11 | item, x, z; item, 4 s16, u16 trigger |
| 5D, 66, 67 | SET_BGM, START_BATTLE, DELAY | 1, 1, 3 | font; u8; `_`, u16 frames |
| 64, 49 | CALL_ROUTINE, CALL_DIGIMON_ROUTINE | 1 | Two number spaces (`src/main/script_interp.c:423-520,1661-1681`) |

Softlocks: a turn of exactly 2048 never finishes, so `4A` on that slot hangs (`src/main/overworld.c:1484-1546`); `ENTITY_MOVE_TO` to a
target ahead on the positive side by less than its duration never finishes, as the delta truncates to 0 (`src/main/tamer.c:1134-1175`).

### 4.7 Pstats
256 u8 at 0x801BE031. A save holds only 0..34: 0x17C bytes of `ScriptState` are 212 + 33 + 100 + 35
(`include/dw/script.h:158-164`, `src/main/main_menu.c:5058,5120`). 35 and up and the stack are cleared only at boot
(`src/main/script_interp.c:2205-2208`), so after the ending a Continue sees old values. "Free": no decoded script, no C constant.

| Pstat | Use |
|---|---|
| 0 | Time speed: 0 normal, 1 double, 2 half, 3 a script holding control. The pause itself comes from `stopGameTime`, and a running clock resets 3 to 0, see 8.1 (`src/main/clock.c:109-131`) |
| 1..6 | Prosperity, tournament day, id, Digimon, Sukamon backup (`include/dw/pstat.h:261-266`), show map name on warp (`src/main/script_interp.c:860-877`) |
| 7..15, 20 | **Free and saved.** Horizon's list agrees |
| 16..19, 21..44 | Scripts; 0x1C..0x1F also rise daily (`src/main/script_common.c:1425-1431`) |
| 45..99 | **Free, not saved.** Raw byte scans here are false hits. Also 104, 114, 115, 118, 124..199, 201..242 |
| 0xC8; 0xF3, 0xF4, 0xF6..0xF9 | Item stacks lost on the last defeat (`src/main/script_common.c:1350-1351`); routine outputs, other C use |
| 0xF5 | Music mode, set before `5D`: 0 day and night variants, 1 fixed, 2..10 forced pairs (`src/main/script_interp.c:1584-1637`) |
| 0xFA; 0xFB..0xFD; 0xFE; 0xFF | Predefined battle; its fighters as script ids, 0xFF none (`src/main/btl.c:232-241`); routine argument; last outcome: 0xFF lost, 0 fled, 1 won (`src/main/script_interp.c:1918`) |

### 4.8 Triggers
800 bits at 0x801BDFCD: bit N is byte base + N/8, mask 1 << (N%8), all saved. `tools/scan_trigger_ids.py` sees only scripts,
so it calls the bands C owns free.

| Ids | Held by |
|---|---|
| 0..4; 15..40 | Give-item failed (`src/main/script_interp.c:1110-1112`); no-flee fight (6.1); tamerless battle (`src/btl/battle_setup.c:70`); shop modes (`src/main/script_interp.c:430-438`). Cups won, tournament registration (`include/dw/trigger.h:805-828`) |
| 41 (0x29) | Set by section 0x4DE, consumed by `checkShopMap` (`src/main/script_common.c:3070`) |
| 44, 49 (0x31), 50 | No auto-face on the next pause, one shot; blocks cancel on a selection; game beaten once |
| 200..311 | 0xC8 + species, recruited or beaten. **Arena pools and cup unlocks read this band, all of it for a cup with no species list** (`src/main/utils2.c:602-618`, `src/dget/dget.c:375-387`); prosperity reads species 3..58 (`src/main/main.c:3908-3909`) |
| 364..382; 384..511 | Medals; shop stock (`src/main/script_common.c:3026-3036,1636-1641`) |
| 512..574; 575..626 | Raised, 0x200 + species below 63 (`:3011-3024`); Jijimon's recruit lines, 0x23C + species (`dw_roster_flags.c:10-13`) |
| 640; 650..716 | Energy threshold met (`src/main/partner_impl.c:1546-1548`); chests opened. 716 is the top id any stock script names |
| 717..799 | **The Expanded Roster's raised flags**, 0x2CD + species - 63 (`dw_roster_flags.c:33-38`). Cards are shared, so a flag here marks a species raised. Horizon's allocator calls 717..788 free: not in this port |

For a new flag use **627..639 or 641..649**: no decoded script, C constant, computed path or plugin reaches them, so they stay
inert on a shared card with the mod off [not run]. `hasDigimonRaised` reaches them only for species 115 up, which stock never asks.

### 4.9 Text
`1A 00`, then pairs of bytes, high byte first: fullwidth Shift-JIS. The US font has 79 glyphs (`src/main/font.c:1391-1401`),
each with its own width (`:1406`):

| Bytes | Glyph | Width px |
|---|---|---|
| 82 60..79; 82 81..9A; 82 4F..58 | A-Z; a-z; 0-9 | 12; 8, but f and j 6, i and l 4; 8 |
| 81 40 | Space | The previous glyph's advance, not its own (`src/main/script_textbox.c:780-804`) |
| 81 42, 43, 49, 46, 47, 75 | . , ! : ; and apostrophe | 5 |
| 81 48, 76, 5F | ? and double quote (stock never uses it), backslash shape | 8 |
| 81 7B, 7C, 81; 81 9B, A0, A2, 7E | + - =; circle, square, cross, triangle | 12 |

Any other pair draws the triangle: the lookup writes it into the table's last slot and matches there (`src/main/font.c:1603-1609`).
Below 0x20 a pair is a control code with one operand byte (`src/main/script_textbox.c:1100-1238`): 01 colour, 04 pstat as a number, 05
tamer name, 06 partner name, 07/08/09 Digimon/move/item name from a pstat, 0A bits, 0B merit, 0C and 0E tabs, 0F half space; 03 takes 3.
- `0D 00` ends a row. `0D 00 00 xx` ends the text (`:1205-1223`). If the next op is `1A` the next page follows in the same
  box; otherwise the page waits for a press and the script continues.
- The dialog box is 262x59 with 4 rows, the first being the speaker's name, so 3 rows of text (`:923-926`, `:1076-1095`).
- Nothing wraps. The limit is pixels: a glyph starting at x >= 244 is not drawn (`src/main/font.c:21,1576`). By these widths
  stock's widest row is 248 px and only 2 rows pass 244 (stock decode, not measured on screen).
- `boss_script` asserts 28 characters and `[A-Za-z0-9 .!?]` (`boss_script` in `tools/gen_newmap.py`); 28 capitals are still 336 px. Its
  encode route cannot reach 81 75, since Shift-JIS has no fullwidth apostrophe. For an apostrophe or hyphen, emit the bytes.

### 4.10 The dialog owner
`1B id` names the speaker. If box 0 is not open the handler first sets the stored owner to id-1, so the first `1B` of a run
always counts as a change (`src/main/script_interp.c:771-777`). `setDialogueOwner` acts only on a change: it calls
`scriptPauseGame(owner)`, then creates box 0 on the owner (`src/main/script_common.c:2960-2969`). To reopen after `27`, send `1B`.

So `1B` must open a talk section. It arms the end of the script to give back movement, camera and time (4.4), and it creates
the box `1A` draws in; `1A` alone has no box [inferred]. 496 of the 625 stock sections 5..12 open with it, and 53 with an IF
(stock decode). A section started by talking also queues a mutual look-at on that first pause, unless trigger 44 is set
(`src/main/script_interp.c:2740-2750`). That turn can be the exact half turn of 4.6, so keep `4A FF` out of a talk.

## 5. Entities and NPCs
### 5.1 Slots and layouts
`ENTITY_TABLE` 0x8012F344 is 10 pointers, NULL until filled: 0 tamer (`TAMER_ENTITY` 0x8015576C, 0x3C bytes), 1 partner
(`PARTNER_ENTITY` 0x801557A8, 0x80 bytes), 2..9 NPC slots 0..7 (`NPC_ENTITIES` 0x80155828, 8 x 0x68, overworld state in
`MAP_DIGIMON_TABLE` 0x8013CB50, 8 x 0xC4). Layouts: `include/dw/entity.h:25-131`; the test pokes them (`tools/verify_newmap.py`).

| Offset | Field | Notes |
|---|---|---|
| +00, +04 | i32 type, posData* | World x, y, z are i32 at posData+0x78, rotation at +0x70 |
| +2E; +34, +35 | animId u8; isOnMap, isOnScreen i8 | animId is the record's move value while it plays. Collision skips an entity not both on map and on screen (`src/main/map.c:3442`, `src/main/utils2.c:367-373`) |
| +38..+3F; +40, +44 | off, def, speed, brain i16; movesPrio[4], moves[4] u8 | Partner and NPC only from here on |
| +48..+4E; +56 | hp, mp, currentHP, currentMP i16; chargeMode | chargeMode is a stamina rule, not a charge |
| NPC; partner; tamer | +58 flee, +60 bits i16, +62 unk1, +65 scriptId, +66 autotalk; +58 learnedMoves[2], +68 name[20], +7C lives; +38 tamerLevel, +39 raisedCount | |

### 5.2 The map record
After the 120 bytes of `MapWarps` and an i16 count, all i16: 42 words plus 3 per waypoint, read only with row flag 0x80
(`src/main/overworld.c:1035-1084`). Stock's maximum count is 8, unchecked. No merit or drop field. Last column: `BOSS` in `tools/gen_newmap.py`.

| Word | Offset | Field | Notes | Boss |
|---|---|---|---|---|
| 0, 1 | +00 | typeId, followMode | typeId must equal `SET_DIGIMON`'s type or nothing spawns (`src/main/overworld.c:1095`) | 60, 2 |
| 2-7 | +04 | position, rotation | | 350, 0, 1150; 0, 0, 0 |
| 8, 9 | +10 | trackingRange, unk2 | unk2 has no reader | 1200, 0 |
| 10 | +14 | scriptId | Section number and owner id at once. Stock 5..12 | 5 |
| 11-14 | +16 | hp, mp, currentHP, currentMP | Read again on every map load | 8800, 16000, 8800, 16000 |
| 15-19 | +1E | off, def, speed, brain, bits | Brain below 297 can go senile, (300 - brain)/4 percent per check (`src/btl/battle_main.c:546-552`). Bits paid on a win, stock maximum 9999 | 820, 640, 520, 400, 5000 |
| 20 | +28 | chargeMode | Dead for enemies: overwritten at each decision (`:639-643`) | 1 |
| 21 | +2A | unk1 | With a second enemy. 1: keep the partner as target, no area bonus. 0: 71% turn on an attacker, +20 weight on range 3 moves (`:3697-3704,3840-3846`) | 1 |
| 22-25 | +2C | moves | 0x2E + index into `DIGIMON_DATA.moves`, 0xFF empty; invalid ones cleared at battle load (`src/main/btl.c:464-471`) | 50, 56, 55, 60 |
| 26-29 | +34 | weights | An empty slot keeps its weight: picking it idles 80 frames. A finisher's is zeroed (`src/btl/battle_main.c:3820-3873`) | 35, 25, 40, 10 |
| 30-32 | +3C | flee x, y, z | A bearing for the run-off, not a destination | 3400, 0, 1150 |
| 33-41; 42+ | +42; +54 | waypoint count, aiSections[8]; waypoints x, y, z | 0 wait x frames, 1 walk, 2 run, -1 restart. The timer is int8 (`src/main/overworld.c:36,1707-1709`), so a wait above 127 never ends: the boss's 350 stands it still | 1; 0, -1..; 350, 0, 1150 |

### 5.3 followMode and trackingRange
| followMode | Does, while its test passes (`src/main/overworld.c:1247-1303`) | Test |
|---|---|---|
| 1, or no case | Nothing: waypoints only | None |
| 2, 11 | Stands and turns to face the tamer | Square |
| 3, 4, 5, 12, 13, 14 | Runs to the tile where it saw the tamer, stops there or on contact, waits 40 frames, walks home (`:1556-1596`) | Square |
| 8, 9, 17, 18 | Chases with anim 2, then goes home and waits 80 frames | Square, then "radius" |
| 6, 15; 7, 16 | Chases with anim 2; anim 4 | "Radius" |

Stock records use 12 x441, 1 x372, 2 x175, 16 x1. **trackingRange only feeds these tests; it starts no fight**: 60
battle-starting stock records have range 0. The square is the record's position +-range on x and z (`:1389-1398`). The "radius"
compares dx + dz, signed and unsquared, with (range*6/10)^2 (`:1454-1458`): a half-plane below range 237, always true from there.

### 5.4 How talking starts a script
Ids in scripts: 0xFD tamer, 0xFC partner, else the first NPC whose `scriptId` matches (`src/main/script_value.c:131-150`). A talk
is `callScriptSection(CURRENT_SCRIPT_ID, scriptId, 1)` from either of two places, both needing `IS_SCRIPT_PAUSED` == 1:
- Tamer side (`src/main/tamer.c:665-679`): the tamer's forward step hits entity 2..9. autotalk 1 fires; autotalk 0 needs a fresh confirm.
- NPC side (`src/main/overworld.c:1324-1337`, USA path): the NPC's own step hits the tamer, tamer state 0, autotalk 1.

autotalk comes from `SET_DIGIMON`, not the record; `6A` changes it; other values never fire. Boxes are squares of +-radius from
`DIGIMON_DATA`. A fresh confirm while idle grows the tamer's box 50 units on the facing side (`src/main/map.c:3764-3778`), so
confirm needs facing the NPC. H-Kabuterimon stops the tamer about 400 units off [run]. Contact only runs the section; the fight
is `66` in it. Stock decode: row sections hold 476 `SET_DIGIMON` sites with autotalk 1 and 224 with 0; all scripts hold 498 `START_BATTLE`,
45 sites of `1E 00 FA 01`, 47 of `1C 00 01 00`; raw byte scans overcount, because DG slots hold junk. Every map load reads the record again and
`SET_DIGIMON` sets `isOnMap` to 1, so a beaten NPC is back unless the row section skips it (`src/main/overworld.c:1026,1114`).

### 5.5 Model slots
A map has 8 entity slots but only **5 NPC model slots**. `LOAD_DIGIMON` takes one of 8 `LOADED_DIGIMON_MODELS` entries
(`src/main/script_interp.c:2561-2572`), then `loadMMD` shares or takes one of 5 `NPC_MODELS`: the same species shares, a sixth
returns 0 (`src/main/model.c:3374-3398`). **Slot 4 overlaps `SBOY.TIM`'s pixels and `SYSTEM_W`'s CLUT** (page 0x16 + k/2, CLUT x
(k+2)*16, y 488, `:3400-3403`), so 4 models are safe. The boss takes one: the glade has 3 left. The heap they allocate from is 197,520 bytes
(`src/main/main.c:3532-3540`, values from the EXE), with HKAB.MMD at 95,960 and the largest model a stock row section loads at 86,248 (one scene in script 51 loads 88,448); which limit fails first is unknown. Load in the row section: a second
`LOAD_DIGIMON` in a talk is reported to hang [Horizon], but stock loads two in one section after text boxes (scripts 43, 123, section 0x33), so no cause is known.

## 6. How to author a fight
The worked example is the glade's fight: `BOSS` and `boss_script` in `tools/gen_newmap.py`. The map row must already exist.

**1. Pick a species that can fight.** Its MMD needs field and battle animations and one per move [inferred]: the second header word is the
animation table's offset, indexed by anim id, and `startAnimation` ignores an id whose entry is 0 (`src/main/model.c:3416`, `src/main/anim.c:216-218`). The boss's check was
0, 2, 4, 0x21-0x25, 0x28-0x2C. On the stock disc 38 species fail it, listed in the `missing_anims` column of `tools/dw_tables.py`'s species.csv, and 62 and 114 have no MMD. Do not patch species 60's drop at 0x8012DB06: `dw.vanilla.trainer` writes it, and an overlap rejects the plan.

**2. Choose the moves.** A slot holds 0x2E + k into `DIGIMON_DATA[type].moves`. The boss's k = 4, 10, 9, 14 are moves 47, 33, 35, 93.
- **Trap: the finisher works only in slot 3.** Only `moves[3]` is read for it, it must map to a tech in 0x3A..0x70, and it fires when
  the meter is full, goal 3000 - speed, never by weight (`src/btl/battle_main.c:617-625,2334-2336`, `src/btl/battle_setup.c:116`).
  The boss's weight 10 does nothing. Keep one usable slot weighted, or the pick reads past `moves[]`.
- **Trap: anim to tech is species-specific.** `entityGetTechFromAnim` returns `moves[anim-0x2E]`, except that type 0x3C with anim 0x3C
  gives tech 0x70 (`src/main/utils2.c:338-341`). So the boss's finisher deals power 218, not move 93's 170: 934..2283 at neutral type.
- **Trap: EFE lists.** The effect file comes from the raw list entry, here 93 (`src/main/btl.c:469`). Each file (EFEDAT.EFE sector +
  id*10, `src/main/efe.c:1043-1052`) has a list of 10-byte entries, i16 type, bone, x, y, z, ended by a negative type. If
  `getOriginalType(type)` is not listed the script aborts (`src/btl/battle_effect.c:4660-4678`): the animation plays, MP is paid, nothing
  can hit [inferred]. Moves 47, 33, 35, 93 all list 60. The generator has no assert; add one before changing moves.

**3. Set the numbers.** All i16. Keep currentHP = HP, currentMP = MP, brain above 296. With no flag the bits repeat: an open decision.
- **Trap: fleeable fights spend the enemy's MP.** With trigger 1 clear each pick costs `mpCost*3` (`src/btl/battle_main.c:2546-2550`):
  258, 354, 96 here, 217 on average. Dry, it backs away in 80-frame waits and only the finisher fires (`:3814-3817`); no NPC MP regen
  was found. 6000 MP, the first value, buys about 27 picks, 16000 about 73 [inferred]. Trigger 1 stops the payment, but usability still
  needs currentMP >= cost (`:2364`). More MP also raises the partner's MP gain (`src/main/battle_ui.c:140-162`).

**4. Place it.** y 0. Keep it farther from every arrival than trackingRange + 300, or an encounter can start during the fade with no
enemy [Horizon]; the boss stands 4672 away. followMode 2 with range 1200 turns it to face the player and never walks.
- **Trap: a stuck run-off.** On a win with trigger 1 clear the victory loop runs, with no timeout, until every enemy has left a 320x240
  box (`src/btl/battle_main.c:943-963`; widened by `dw_ws_camera.c:448-470`). Each plays anim 0x24, turned once toward its flee point, moving by root
  motion with no collision (`src/main/anim.c:475-498`). An on-screen flee point should not hang, since it walks past; a species whose 0x24 does not loop or move forward should [inferred].

**5. Write the row section.** The stock free-row filler binds script 180 and must not ship. The glade's goes in the MAPHEAD tail and its
table entry is repointed (the `MAPHEAD section` patches in `tools/gen_newmap.py`). The row needs flag 0x80. The slot is the record's index.
```
FB 00 76 00 EF 00   SET_SCRIPT 118, row 239
1E 00 F5 00         pstat 0xF5 = 0: day and night music
5D 01  46 3C        SET_BGM 1; LOAD_DIGIMON 60
47 3C 00 00  FE 00  SET_DIGIMON 60, slot 0, autotalk 0 (press to fight); end
```

**6. Write the DG script.** `boss_script()` in `tools/gen_newmap.py`. It ships as the 124-byte overlay `assets/glade.dg`, not a `[[patch]]`, because a patch must repeat the stock bytes it replaces. Section, owner byte and record word 10 must agree. Text: 4.9.
```
0C 00  FE 00 0C 00  05 00 0E 00  FF FF    first body 12; section FE at 12; section 5 at 14
FE 00                                     +12 section FE: the arrival END
1B 05  1A 00 text 0D 00 text 0D 00 00 00  +14 section 5: owner 5, two rows, waits for confirm
27 00  67 00 01 00  66 00                 close the box, wait a frame, START_BATTLE
FE 00  FF 00                              end of section; end of file
```
- **Trap: the DG slot is shared.** Slot 118 also serves row 122, which has no map Digimon or script tiles, so only its FE runs: FE must stay
  a bare END. The generator checks the stock bytes and the 0x800 fit (`dg_new` in `tools/gen_newmap.py`). A next map needs a table row 223+ (4.1).

**7. Decide about flags.** The boss sets none, though a loss still sets trigger 0x29, as any stock battle does. To add one, put
`1C 00 tt tt` straight after `66 00`: a flee and a loss never return there (`src/main/script_interp.c:1919-1941`). To hide the NPC
afterwards, wrap `46`, `47` in the row section with the IF in 4.3, 12 bytes of MAPHEAD's tail.
- **Trap: flags that pollute cups.** Not 200..311: a flag there joins arena pools and raises cup unlock counts. The obvious 260 (200 +
  60) also joins the Ultimate lists in cup scripts. Not 717..799: those mark roster species raised. Use 627..639 or 641..649 (4.8).

**8. Generate and test.** Run `tools/gen_newmap.py`, update `MOD_PAYLOADS` in `tools/verify_setup_package.py`, rebuild to stage the package,
then `tools/verify_newmap.py fight --map glade --seed <card dir> [--wide]`. `fight` proves that standing by the boss starts nothing, confirm
opens the dialogue, it plays all four move animations, a win pays the bits and it leaves, it returns next visit, flee reaches the host side, a
loss ends on row 205 or 218, and row 122 still works [run]. It pokes HP and MP, so it cannot see the MP wall, real finisher timing or which tech
dealt the damage; `measure` fights unpoked and only prints. `BOSS`'s "between second-hardest and final" comes from a model without the auto-hit
rules. Never run on row 239: last-life and lifetime 0 losses, natural death, evolution. No test covers audio. A win crossing a Brains threshold
freezes the result box only with Battle End Freeze Fix off, see 12.1 [run]; `fight --brains` sets that up.

### 6.1 Fleeable or not: trigger 1 and predefined fights
| Trigger 1, read at battle start (`src/main/btl.c:231`) | Clear (the glade) | Set (47 stock sites) |
|---|---|---|
| Run command | Offered | Skipped (`src/main/battle_tick.c:91-103`) |
| Enemy MP | Paid | Not paid, still checked (`src/btl/battle_main.c:2547,2364`) |
| Other NPCs on screen | Roll the join table | Join, up to 3 enemies (`src/main/btl.c:323-339`). Either way those left out are off the map when the battle ends (`src/btl/battle_main.c:783-812`) |
| Enemy after a win | Runs off, no timeout | Stays, standing (`src/btl/battle_main.c:925-941`) |
| Script after a win | Camera returns first (`src/main/script_interp.c:1949-1957`) | Continues at once, movement locked (`:1943-1947`); the section must unset trigger 1 itself |
| After a loss | Section 0x4DE | Section 0x4DE, which clears it |

Press-to-fight with fleeing is new: all 9 stock autotalk 0 records whose section reaches `66` set trigger 1 first (rows 1, 2, 27, 40, 86, 95, 139, 220; stock decode). `1E 00 FA 01` with
fighters in 0xFB..0xFD makes a predefined battle: no join rolls, music track 0 (`src/main/btl.c:232-261,360-377`). Stock writes 0xFA back
to 0 after it, and every loss route reaches script 212, which clears it, so no guard against stale state is needed.

---

## 7. Battle

Battle code is the `BTL_REL` overlay (`src/btl/`) plus `src/main/btl.c`, `battle_tick.c`, `battle_ui.c` and `tamer.c`. One fight tick is one `VSync(3)`, so 20 ticks per second on NTSC (`src/btl/battle_main.c:2606`); whether a port option changes the tick was not checked. Times are ticks unless they say seconds. Arena and tournament fights (`src/vs/`, `src/std/`) have their own copies of this code, which were not read. A citation that starts with a colon is in the file last named.

`stats.moves[4]` holds **animation ids**, not techs: anim `0x2E + n` means `DIGIMON_DATA[type].moves[n]`, 0xFF is empty (`src/main/utils2.c:333-342`). A **tech** is a row of `MOVE_DATA`, 16 bytes (`include/dw/move.h`): distance i32 +0 (a squared length), power i16 +4, mpCost +6, iframes +7, range +8, special +9, status +0xA, accuracy +0xB, statusChance +0xC, flags +0xD (`unk3` in the header). Range: 1 melee, 2 ranged, 3 wide, 4 self. Flag bit 0 needs a target; bit 1, set on 16 stock techs that are all range 3, locks all bit 1 moves for 110 ticks after a cast (`src/btl/battle_main.c:2342,2469-2471,2487`, stock `MOVE_DATA`). Techs 0..0x39 are normal, 0x3A..0x70 finishers, 0x79 the flat attack.

| Symbol | USA address | Note |
|---|---|---|
| `GAME_STATE` | `0x80134F0A` | i8, see 7.1 |
| `BATTLE_FRAME_COUNT` | `0x80134D66` | i16, starts at 1, +1 per fight tick: the clock to measure with |
| `FLEE_TIMER`, `FLEE_DISABLED` | `0x80134D68`, `0x80134D7C` | Timer counts up once Run is chosen. [0] copies trigger 1; [1] is the wide-move lock, nothing to do with fleeing |
| `NO_AI_FLAG`, `FINISHING_ENTITY` | `0x80134D74`, `0x80134D60` | the finisher freeze |
| `COMBAT_DATA` | `0x8013D640` | 4 fighters of 0x168 bytes, [0] the partner, [1] the first enemy: goal +0x18, progress +0x1A, hpDamageBuffer +0x2E, speedBuffer +0x32, flags +0x34. From the base: hitCount +0x640, blockedCount +0x642, currentCommand +0x64E, hoveredCommand +0x652 |
| `INITIAL_COMBAT_STATS` | `0x8013D610` | HP, MP, Off, Def, Speed, Brains per fighter at battle start |
| `TYPE_FACTORS`, `MOVE_LEARN_CHANCES` | `0x80125F70`, `0x80125FA4` | 7x7 bytes; 58x3 bytes |

Offsets are computed from `include/dw/combat.h`; three match addresses already in use (`CURRENT_COMMAND` and `HOVERED_COMMAND` in `tools/verify_newmap.py`, the finisher cheat at `0x8013D65A`).

### 7.1 Start to end

| Step | `GAME_STATE` | What happens | Code |
|---|---|---|---|
| Field | 0 | A DG section reaches op 0x66 `START_BATTLE`: `BATTLES_STARTED` +1 (cap 9999), pstat 0xFB-0xFD script ids become entity ids if pstat 0xFA is set, BGM stops | `src/main/script_interp.c:1897-1913` |
| `startBattle` | 1, for a moment | Saves the tamer's position, stops game time, picks the fighters | `src/main/tamer.c:837-844`, `src/main/btl.c:210-344` |
| Load | 3 | Battle music and the `BTL_REL` request just before the state is set, then sound bank, enemy voices, one `EFEDAT.EFE` entry per move | `src/main/btl.c:359-500` |
| Setup, intro | 2 | `BTL_initializeCombat`, tamer walks to his start tile, non-joiners run off, start text | `src/main/btl.c:503`, `src/btl/battle_setup.c:43-327` |
| Fight | 1 | Each tick: finished?, partner AI, enemy AI, fighter states, hits, status, frame, pause | `src/btl/battle_setup.c:328`, `src/btl/battle_main.c:1838-1849` |
| Ending | 2 | Enemy HP, MP and stats restored at once; on a win the pose, end box and run-off | `src/btl/battle_main.c:748-816`, `src/main/battle_ui.c:336` |
| Back | 0 | Outcome effects, clock +20 minutes | `src/main/tamer.c:848-881` |
| Script | 0 | pstat 0xFF = outcome (-1 lost, 0 fled, 1 won), then 7.14 | `src/main/script_interp.c:1918-1957` |

States 4 and 5 belong to tournament and VS. [inferred]

A fight ends when `FLEE_TIMER` reaches 41 with the partner alive, when all enemies have finished anim 0x2B with the partner alive, or 171 ticks after the partner's anim 0x2B finishes. That countdown only runs while no item is in flight, and item 0xB (half HP) or 0xC (full) still revives (`src/btl/battle_setup.c:331-390`, `src/main/item.c:2315-2349`).

`tools/verify_newmap.py fight`, which passed at 4:3 and at 21:9 with the roster on, saw state 0 during the boss's dialogue, one of 1, 2, 3 after it, 1 while the boss used its moves, and 0 with tamer state 0 and `IS_SCRIPT_PAUSED` 1 on map 239 after the win. [run] The test polls membership and never logged the order; the order above is from the code. `BATTLE` in `tools/verify_newmap.py` has the same meanings.

### 7.2 Scripted and fleeable fights

Contact only runs the record's DG section; the fight comes from `START_BATTLE` inside it. Autotalk 1 runs it on touch from either side, autotalk 0 on a confirm press while facing and touching. `trackingRange` only drives facing and chasing (`src/main/tamer.c:665-679`, `src/main/overworld.c:1306-1343`). Of the stock records, 442 start a fight on autotalk 1 without trigger 1, and all 9 autotalk 0 fights set it (6.1; `tools/dw_tables.py`).

**Trigger 1** makes a fight scripted. It is copied to `FLEE_DISABLED[0]` at the start (`src/main/btl.c:231`) and changes all of this:

| Rule | Trigger 1 clear | Trigger 1 set | Code |
|---|---|---|---|
| Run in the menu | Offered | Cursor skips it | `src/main/battle_tick.c:91-113` |
| Nearby NPCs joining | Rolled per NPC | Roll forced to 0, so they join, up to 3 enemies, except on a blocked line on a concave screen or with a partner of species type 0xFF (stays 70 %) | `src/main/btl.c:280-339` |
| Enemy MP | Paid per cast | Not paid, but usability still needs `currentMP >= cost` | `src/btl/battle_main.c:2364,2546-2549` |
| Enemies after a win | Run off until out of view | Play anim 0x2C, go idle, stay on the map | `src/btl/battle_main.c:925-963` |
| NPCs as the fight ends | Every NPC with `isOnScreen` 1, a flag only the field tick updates, so as the fight began, gets `isOnMap` 0, the others 1 | Same, but fought enemies are left alone | `src/btl/battle_main.c:783-812`, `src/main/overworld.c:1126-1134,1352` |
| Script after a win | Camera pans back to the tamer, script waits, then continues | Movement locked, no camera return, script continues at once and must handle what follows | `src/main/script_interp.c:1943-1957` |

Trigger 1 is not cleared by a win: stock sections unset it themselves. [inferred] Section 0x4DE clears it on a loss.

**Predefined fights** set pstat 0xFA to 1 and name up to 3 fighters in pstats 0xFB-0xFD. They skip the join logic, and every other visible NPC gets the "fled" bubble (`src/main/btl.c:232-261`). A raw scan finds a `SET_TRIGGER 1` next to all 99 raw 0xFA writes, so predefined fights cannot be fled. [inferred] Raw scans overcount because DG slots hold junk; Horizon's doc counts 45 scripted battles. [Horizon] Section 0x4DE does not clear 0xFA, but every loss route reaches a section in script 212 that does (decoded from the disc), so no stale state reaches the next fight. Trigger 2 makes a tamerless fight: no commands, no items (`src/btl/battle_setup.c:70`, `src/main/battle_tick.c:66-68,249`).

**Joining** in an open fight: `FLEE_CHANCES` is misnamed, it decides who joins, and the "flee bubble" only shows the result. Roll 0..99, +20 with Enemy repel (item 0x23) in the bag, -50 with Enemy bell (0x24), which wins if both are held, and the NPC joins if the roll is below the table value for partner attribute by enemy attribute (`src/main/btl.c:104-107,305-333`). NPCs off screen are removed for the fight and come back after it; on-screen non-joiners run off in the intro (`src/main/btl.c:276-278`, `src/btl/battle_setup.c:228-245`). Music is font 0x21: track 3 if the first enemy is type 0x73, 0 if predefined, a table for types 0x43..0x6F, else 1 (`src/main/btl.c:360-377`).

### 7.3 Command menu

Built once per fight from Brains, before the injury penalty (`src/btl/battle_setup.c:31,124-182`). Run comes first and Your Call last, with the cursor and the command starting on Your Call. Under 100 those are the only two; 100 adds Attack; 200 adds Moderate; 300 adds Distance and Change; 400 adds Defense, giving Run, Change, Defense, Distance, Moderate, Attack, Your Call. From 500 the filled move slots 2, 1, 0 replace Attack and Moderate.

Bits `0x8000` and `0x2000` of `POLLED_INPUT` move the cursor, confirm commits, `0x80` fires the finisher, cancel opens the items (`src/main/battle_tick.c:87-121,177-186,251`). Those are left, right and square by PsyQ's pad layout. [inferred] Left reached Run in the `fight` test. [run] A delay from discipline is computed and then overwritten with 0, so commands act at once (`src/main/battle_tick.c:136-142`).

Commands (`src/btl/battle_main.c:373-437,486-512,1206-1248`): 1 Run, see 7.12. 2 Attack: strongest affordable of slots 0-2 by power, charge mode 0. 3 Your Call: weighted pick, charge mode as saved at battle start. 4 Moderate: cheapest by MP, charge mode 2. 5 Distance: backs out of the target's longest move, no attacks. 6 Defense: stands facing the target, no attacks. 7 Change: switches target, then carries on. 8-10: that slot when usable, otherwise Your Call. 11 finisher: not on the cursor, and it returns to Your Call. Attack or Moderate with nothing affordable waits 80 ticks.

Your Call covers slots 0-2 only. Base weight by charge mode: mode 0 ranks by power 50, 20, 5; mode 2 ranks by MP 50, 20, 10; mode 1 is a flat 20; an empty slot gets +5. Then a bonus for the tech's factor against the target's first special (20, 15, 10, 5, 2 give 10, 7, 5, 3, 1), buffs add `buffPrioTimer`, and at Brains 200 up wide moves add 10 or 15 per living enemy when 2 or more live (`src/btl/battle_main.c:300-303,3415-3584`). Target: Brains 300 up prefers a status-afflicted enemy, else the weakest with a margin of 40; 150 up keeps its target; below that the nearest (`:3686-3767`).

### 7.4 Enemy AI and move picking

Each tick, in order (`src/btl/battle_main.c:539-645`): senility roll, speed buffer, cooldown; nothing while knocked back, attacking, blocking or dead; nothing during a freeze; flat attack if flattened, nothing if stunned, a random usable move if confused; nothing while senile, charging or on cooldown; nothing with a move already queued; finisher if the meter is full; otherwise pick a move. There is no HP-triggered behaviour and there are no phases: the only low-HP effect, shared with poison, is the anims at HP/5 or less, idle 0x22 for 0x21 and walk 0x23 for 0x24 (`src/main/btl.c:189-207`, `src/btl/battle_main.c:1687-1707`).

**Weights** are the record's `movesPrio` (`src/btl/battle_main.c:3820-3866`):
- a slot that fails the usable test gets 0
- an empty slot keeps its weight, and picking it means an 80-tick wait: the knob for idle time
- a range 3 move gets +20 if another enemy lives and the record's `unk1` is 0
- a range 4 move gets +`buffPrioTimer`, which starts at Brains/10+5 and falls by 1 each 20 ticks

**Usable** (`:2318-2369`): slot set; not tech 0x2D (Counter); not a finisher, so a weight on a finisher slot does nothing; range 4 only with buffs left; flag bit 0 needs a target; `currentMP` at least `mpCost*3`. If all four weights end at 0 the pick loop runs off the end and reads past `moves[]` (`:3857-3868`), and a weighted empty slot with no weighted usable one only ever waits, so keep one usable slot weighted.

**No usable move:** charge mode 2 and a cooldown of 80, counted to below 2, so about 79 ticks of backing away, then it tries again (`:3814-3817,558-560,1113-1122`). Only the finisher can still fire. No regen was found: the only other write to enemy MP is the restore at the end (`:758`). [not run]

After a finished attack the cooldown is 40 (`:1556-1557`). Enemies target the partner; a record with `unk1` not 1 turns on another attacker 71 % of the time (`:3697-3704`), which matters only with enemy allies, and a ranged attacker holds fire while its target plays hit anim 0x28 or 0x29 (`:2458-2463`).

**Senility:** at Brains 300 or less, every (Brains/2+1)*20 ticks a roll of (300-Brains)/4 % for enemies, (70-discipline) % for the partner, gives 100 ticks of no action and no speed regen, and auto-hits against it (`:331-337,546-557,1078-1089`). A hit ends it (`:1580-1583`).

**Buffs** act when their attack object reaches the caster (`:1887-1920,2122`), as a percent of the current stat, capped at 999 (`:2229-2240`). Tech 41: Off +30. Tech 42: Off +10, Def +5, Speed +10. Tech 34: Def +20, Speed +10. Tech 21: Off +7, Def +8, Speed +7. Tech 30: Off +25, Def +15, Speed +15. Casts per fight: Brains/100+1 under 400, 4 under 600, else 5 (`src/btl/battle_setup.c:108-114`). These and the meter are the only things that escalate during a fight. [inferred]

### 7.5 Speed buffer, charge modes, MP

**Speed buffer** is a stamina gauge. It starts at 100; every second tick it gains Speed/100+1, up to 100; every executed tech subtracts its power, finishers included, floor -155 (`src/btl/battle_setup.c:105`, `src/btl/battle_main.c:1639-1649,2580-2583`). It does not move during a finisher or senility (`:329,343,544,555`). At 0 or below every hit lands (7.6), so power 100 or more fired from full opens its user, and power 255 or more bottoms out: 52 ticks open at Speed 520, because -155 + 26 steps of 6 gives +1.

**Charge mode** says when a queued tech may fire (`:1102-1153,2400-2419`):

| Mode | Waits while | Moves while waiting | During an 80-tick wait |
|---|---|---|---|
| 0 | buffer <= 0 | stands facing | keeps distance |
| 1 | buffer is not 100 and below power | keeps distance | stands facing |
| 2 | buffer < 100 | backs away | backs away |

After an attack, during the 40-tick cooldown, modes 0 and 1 keep distance and mode 2 backs away (`:1156-1178`). An enemy's record value is overwritten at its first decision and every one after: random 0 or 1, or 2 when off a 200x160 box round the screen centre (`:639-643`). The finisher and confused paths skip the rewrite. So the record's field does nothing for enemies; tune Speed and tech power instead. The partner takes it from the command (7.3).

**Reach:** melee walks to (r1+r2)^2. Ranged and wide fire when the squared distance is within 30 % of `distance + (r1+r2)^2`, or after 80 ticks of trying (`:1250-1330,1337-1376`). Self moves fire at once.

**MP:** enemy cost is `mpCost*3`, paid when the tech executes, so a move interrupted later has still paid (`:2546-2549`). Partner cost is `mpCost*3` less 5, 10, 15, 20 % at Brains 700, 800, 900, 999, and +50 % if injured or sick, paid through `mpDamageBuffer`; the check and the payment round slightly differently (`:2346-2362,2525-2544`). Finishers cost nothing. In one measured fight the boss's MP fell by exactly 4x354+258 = 1674 by the 15 s line, with picks reading 4 Bug and 1 Buster Dive. [run]

### 7.6 Hit chance

`BTL_tickAttackHits` pops one attack object per fighter per tick (`src/btl/battle_main.c:2084-2226`). Objects are dropped from `FLEE_TIMER` 19 on. A defender in a Counter animation aimed at the caster voids the attack, finishers included (`:2116-2121`). A buff applies and stops there. Anything else first ends the defender's running effect and attacking state (`:2126-2127`), then rolls `random(100) < hit`.

`BTL_calculateHitChance` (`:1922-1974`) checks in this order:

| Check | Result |
|---|---|
| Defender species level below 3 | 100 |
| Defender stunned, flattened or senile | 100 |
| The partner is sick (condition 0x40), whoever is defending, so every attack in the fight | 100 |
| Defender speed buffer 0 or below | 100 |
| Defender anim 0x2E or above, so mid-technique | 100 |
| Tech is a finisher: also clears a block | 100 |
| Defender already blocking | 0 |
| Otherwise | the formula |

```
x   = acc/2 * (defSpeed - atkSpeed/10) / 999       each division truncates
x   = x * 6/5                                      if the defender is in idle anim 0x21 or 0x22
hit = clamp(acc - x, 0, 100)
```

The auto-hit checks come before the block check, so they beat a block. A standing target dodges more than a walking one. x goes negative when `defSpeed < atkSpeed/10`, which lifts the chance above accuracy. Speed is the only stat that lowers the chance of being hit, and the attacker's Speed barely counts.

### 7.7 Damage and type factors

```
F      = sum over the defender's 3 specials of TYPE_FACTORS[tech.special][special]; an empty one counts 10
normal = F * (P + d*P/500) / 30 * (90..110) / 100      d = off - def, clamped to -500..+500
finish = (off + P) * F / 30
           partner: * charge / 40, only when charge is 41 or more
           enemy:   * (100..200) / 100
           both:    * (90..110) / 100
```

P is power, each division truncates, the result is clamped to 1..9999, and tech 0x2D uses def*3/10 (`src/btl/battle_main.c:1988-2038`, `src/main/math.c:5-11`). A gap of +500 doubles power and -500 reduces it to 0, which the clamp makes 1: that is why a Rookie's hits on a boss show as 1. Finishers ignore Defense. Damage goes into `hpDamageBuffer`, and each tick takes 900 if above 999, then 80 if above 99, 6 if above 9 and 1 if above 0 (`src/main/utils.c:93-123`). A fighter counts as beaten as soon as current HP is at or below the buffer (`src/btl/battle_main.c:3795-3802`). A hit of 20 % of max HP or more is a "big hit" for the Def gain (`src/btl/battle_main.c:2144-2148`).

`TYPE_FACTORS` is indexed by element, Fire, Battle, Air, Nature, Ice, Mech, Filth (`src/main/item.c:94-102`; names [Horizon]). Stock values are 2, 5, 10, 15 and 20, so F runs from 6 to 60 and 30 is neutral. The same element against itself is not always 10. Horizon's 2.0/1.0/0.2 chart [Horizon] and Vice's rebalance [Vice] are their mods, not stock. No package's `main_exe` patch touches `TYPE_FACTORS`, `MOVE_DATA` or `MOVE_LEARN_CHANCES`; plugins, `disc_user` patches and the 1.5 delta were not scanned. [inferred]

### 7.8 Finishers

**Meter:** goal is `3000 - Speed` from base Speed at battle start, so a later buff does not change it (`src/btl/battle_setup.c:116`). Progress gains 1 every tick, `goal*2/50` when the fighter's own attack finishes (an interrupted one gets nothing), `goal*3/50` to a defender on a block and `goal*4/50` to its attacker (`src/btl/battle_main.c:667,1551-1558,2205-2211`). It is capped at the goal, and only a fighter with a finisher tech in slot 3 gains anything (`:2614-2632`).

**Slot 3:** only `moves[3]` can fire a finisher (`:617`). A finisher in slots 0-2 never fires. The weight on a finisher in slot 3 is dead, because `BTL_isMoveUsable` rejects it (`:2334-2336`).

**Firing:** an enemy fires by itself on the first free tick with a full meter, at the partner; the meter resets at selection (`:616-626`). The partner fires when `0x80` is pressed with progress exactly at the goal (`src/main/battle_tick.c:177-186`). The user faces the target with an aura for 80 ticks, and `NO_AI_FLAG` freezes all AI, status timers, speed regen and cooldowns until its animation ends (`src/btl/battle_main.c:2436-2449,1545-1548`). An attack already in flight that reaches the user removes the aura and lifts the freeze, with the meter already spent (`:2130-2139`). [not run]

**Partner charge:** 80 ticks, starting at 0; each press of input mask 4 or 8 (L1, R1 by PsyQ's layout [inferred]) adds 2, capped at 80 (`src/btl/command_menu.c:360-419`). Below 41 there is no multiplier, so it takes 21 presses to begin and 40 for x2.

**Enemy swing:** 0.9x to 2.2x, from the 100..200 and 90..110 rolls, with no Defense term. A finisher breaks a block and never misses, unless a Counter animation voids it (7.6).

**Species-specific tech:** `entityGetTechFromAnim` has one special case: entity type 0x3C (60, H-Kabuterimon) with anim 0x3C gives tech 0x70 High Electro Shocker, power 218, where `moves[14]` says 93 Electro Shocker, power 170 (`src/main/utils2.c:338-340`, stock `MOVE_DATA`). Damage, usability, the meter and the menu name use that function (`src/btl/battle_main.c:1879,2329,2621`, `src/btl/command_menu.c:351`). The EFE file loaded at battle start uses the raw `moves[]` value, and so does the launch distance (`src/main/btl.c:469`, `src/btl/battle_main.c:1196`). So this species plays 93's effect and deals 0x70's numbers, and an assert on EFE lists has to read the raw field. Tests watching the anim byte cannot tell which tech dealt the damage.

### 7.9 Blocking and Counter

There is no block button: a block is the result of a failed roll (`src/btl/battle_main.c:2162-2224`).
1. **Counter:** if the tech is range 1, the defender is not blocking and owns tech 0x2D in a slot, has MP 165 or more, and rolls Speed/level %, it counters. This is the only way 0x2D fires.
2. If already blocking with iframes left, it only refreshes iframes to 3. No meter, no damage.
3. A range 1 tech still chips: damage * (10..30) / 100, minimum 1. A ranged tech that fails deals no damage, but steps 4 and 5 still apply.
4. Meter: `goal*3/50` to the defender, `goal*4/50` to the attacker. The partner's `blockedCount` +1.
5. The defender faces the attack, gets the blocking flag, anim 0x25 and 3 iframes.

A landed hit sets iframes from the tech (`:2157`) and plays anim 0x28 or 0x29 (`:3093-3147`).

### 7.10 Status effects

A landed hit rolls the tech's `statusChance` unless the target has `FIGHTER_FLAG_PROTECTED` (`src/btl/battle_main.c:3149-3199`). Only the Protection item (10) sets it, on the partner only, and the battle's end and the next setup clear it (`src/main/item.c:2231-2232`, `src/btl/battle_main.c:764`, `src/btl/battle_setup.c:96`). Enemies and bosses therefore have no immunity. A status already on is not refreshed. Timers pause during a freeze, except a flat timer at 65 or below (`src/btl/battle_main.c:2793-2828`).

| Id | Status | Effect | Length | `src/btl/battle_main.c` |
|---|---|---|---|---|
| 1 | Poison | `base.hp * (1..3) / 100` every 100 ticks | Until the fight ends or an item cures it | `:2792-2808` |
| 2 | Confusion | 70 % no target, else a random one; random usable move | 200..300 | `:3617-3633` |
| 3 | Stun | Stands still, auto-hit | 200..240 | `:1018-1025` |
| 4 | Flatten | Flat attack tech 0x79 every 40 ticks at range, auto-hit | 65 ticks of animation after the hit ends, then 224..304 | `:1367-1369,1577-1578,2825-2887` |

Cures on the partner: item 8, item 9 in battle, and item 0xC (`src/main/item.c:2218-2230,2315-2349`). An injured or sick partner (condition 0x20, 0x40) fights with Off, Def, Speed and Brains at -20 %, from battle start until the stats are restored (`src/btl/battle_setup.c:195-201`).

### 7.11 How a hit is delivered: not read

No study read this part, and it is the biggest hole here. What is known:
- Each move's EFE script comes from `EFEDAT.EFE`, is loaded at battle start and runs when the tech executes (`src/main/efe.c:120,1043-1049`, `src/main/btl.c:460-476`, `src/btl/battle_main.c:2572`). Script ops test a box, a radius or a line against entities, or name the target outright, set the target's `isHit` to 1 and queue an attack object (`src/btl/battle_effect.c:2263-2415,4492-4518`).
- `isHit` 1 keeps a fighter from being tested again until a reaction, block or void resets it (`src/btl/battle_main.c:1565-1567,2118,2123,2224`).
- A species missing from a move's EFE list plays the animation while the script aborts, and the MP is already paid (`src/btl/battle_effect.c:4660-4678`). That it cannot hit is inferred. [inferred]
- Horizon reports Buster Dive's hit as a line test from the attacker to its effect origin during effect frames 23-33, with no projectile, so a clip without its dash misses. [Horizon]
- Walking and hit motion go through `entityCheckCollision`, which tests the combat area, map collision and other entities, so fighters stay off blocked cells; the run-off does not (`src/main/map.c:3400-3464`).

Not known: how many attack objects each tech makes per cast, on which frames, in what shape, and whether the geometry can miss. So "one roll per attack" is an assumption, and the sim rests on it.

### 7.12 Flee

Choosing Run sets the command at once and cannot be undone (`src/main/battle_tick.c:74-85,144-167`). There is no roll: `handleFleeing` only steers the tamer back along his path (`:189-232`). `FLEE_TIMER` gains 1 per tick while the item box is closed. From 19 attack objects are dropped, though poison still ticks; at 20 the fade starts; at 41 the fight ends, about 2 s in all, if partner HP minus the damage buffer is above 0 (`src/btl/battle_main.c:2111`, `src/btl/battle_setup.c:338`). A partner already at 0 HP loses instead (`src/btl/battle_main.c:1853-1861`).

The script then adds 1 to `BATTLES_FLED` (cap 9999) and changes map to `PREVIOUS_SCREEN`, spawn `PREVIOUS_EXIT`, with no name card (`src/main/script_interp.c:1929-1941`). A fled fight always leaves the map. In the `fight` test the player reached map 2 at (1350, 0, 2350) and the boss was back at full HP on the next visit. [run]

### 7.13 Winning: run-off, gains, bits, drops, learning

`BTL_handleVictorySequence` (`src/btl/battle_main.c:868-964`): pose, end box, anim 0x2C on each enemy, then the run-off unless trigger 1 is set.

**Run-off:** when 0x2C ends the enemy starts anim 0x24, ticked twice per frame, and faces the record's flee point once. It then moves by root motion along that bearing, with no collision and no distance check, past the point if need be (`:948-954`, `src/main/battle_tick.c:308-314`). The loop has no timeout: it waits until all 8 corners of every enemy are outside a 320x240 box round the screen centre in the same pass (`src/btl/battle_main.c:955-961`, `src/main/map.c:3871-3919`). A flee point on screen does not hang it; an anim 0x24 that does not loop or has no forward motion would. [inferred] The test also never reports type 0x98 in anim 0x1C as off screen (`src/main/map.c:3880-3882`). The port's widescreen widens this test through the hooked `isOffScreen` (`dw_ws_camera.c:446-470`). Both 4:3 and 21:9 ended the run-off in the `fight` test. [run]

**Stat gains** (`src/main/battle_ui.c:127-191`), per stat:

```
E = highest starting value among the enemies, P = the partner's starting value
den = P*10 (10 if P is 0), f = 10, 12, 16 for 1, 2, 3 enemies
E >= P: gain = ceil(E*f / den)
E <  P: +1 at E*f*100/den percent
still 0: +1 at a chance from the fight
```

That last chance is HP lost % for HP; hits*10 % for MP and Off; big hits*10 % for Def; blocks*10 + HP lost %/2 for Speed; hits*5 + big hits*5 % for Brains. "Hits" counts the partner's finished attacks, landed or not (`src/btl/battle_main.c:1551-1554`). Caps are 9999 for HP and MP, 999 for the rest (`src/main/battle_ui.c:568-580`).

**Bits:** the sum of the fought NPCs' `bits`, no multiplier, wins only. It counts up 1 per tick unless confirm or cancel skips it, and `MONEY` caps at 999999 (`src/main/battle_ui.c:306-309,427-445`). 5000 bits take 250 s unskipped. **Drops:** each enemy rolls `DIGIMON_DATA.dropChance` % for `dropItem`, never on screen 0x8F; the item lands where the enemy stood (`:193-206,400-407`). The map record has no drop field.

**Learning** (`:223-276`): each tech an enemy fired, below 0x3A, up to 12 distinct (`src/btl/battle_main.c:2551-2561`), that the partner lacks, whose element is one of its 3 specials, and which is in its species list. Roll `MOVE_LEARN_CHANCES[tech][index of the matching special]`; one winner at random.

**Injury:** if the partner is not injured and any hit landed in the fight, on either side, since `HAS_TAKEN_DAMAGE` is set for every defender, chance tiredness minus HP % (`src/main/battle_ui.c:209-221,323-331`, `src/btl/battle_main.c:2143`).

**End box stall:** when a gain carries Brains to 800 or 999, the box text gains "10" or "20", the US reader returns without advancing on the character '0', and the box never reports finished, so the game hangs on the win (`src/btl/battle_hud.c:337-348,463-466,523-530`, `src/main/battle_ui.c:392-398`). Crossing 700 or 900 adds 4 to the line count for text with one row break (`src/btl/battle_hud.c:378`), so the reader runs on past the text until a stray '0' stalls it or stale row breaks make up the count: and it froze in the game at both [run]. A stock bug, with an optional fix in 12.1. The freeze symptom is reported by Vicen [Vice], and Horizon patches a soft lock from this message [Horizon].

### 7.14 What each outcome costs

| | Win | Flee | Loss |
|---|---|---|---|
| Happiness | +2 | -10 | -30 |
| Discipline | none | -6 | -20 |
| Tiredness | +5 | +7 | none |
| Further tiredness if current MP is exactly 0 (an integer division bug) | +10 | +10 | none |
| Food | -35; if hungry -15 and starvation timer -40 | same as win | -20; if hungry starvation timer -40 |
| Poop level, or if poopy the pooping timer | -2, -400 | same | same |
| Partner `battles` (feeds evolution) | +1 | none | none |
| Clock | +20 minutes, `CURRENT_FRAME` +400 | same | same |
| Bits, gains, drops, learning, injury roll | yes | no | no |
| Items | none | none | `count*30/100` filled slots, whole stacks |
| Lives | none | none | -1, and at 0 lifetime becomes 0 |
| Map | stays | `PREVIOUS_SCREEN` | a death map, 7.15 |

Code: `src/main/tamer.c:850-870,885-947`, `src/main/partner_impl.c:2040-2057`, `src/main/script_interp.c:1919-1927`, `src/main/script_common.c:1340-1384`. Lost items reach the recycle shop's stock if recyclable, capped at 99; the others are destroyed; the count goes in pstat 0xC8.

When the +20 minutes crosses an hour, lifetime -1 and the sick, injury and sleep counters tick, but `evoTimer`, age and the daily reset do not (`src/main/tamer.c:892-930`). So about one battle in three costs a lifetime hour with no evolution progress. [inferred] A hungry partner can also take a care mistake here (`:932-938`).

### 7.15 Loss routes

Outcome -1 jumps to MAPHEAD section 0x4DE (`src/main/script_interp.c:1919-1927`), decoded from `MAPHEAD.SCN` 0x5FD6-0x6033. It clears trigger 1, sets trigger 0x29 (consumed by `checkShopMap`), and routes; a routine here is op 0x49 `CALL_DIGIMON_ROUTINE` (`src/main/partner.c:1139-1214`, `src/main/tamer.c:1699-1749`):

| Test, in order | On exit | Routine | What runs |
|---|---|---|---|
| Lives are 0 | section 0x49 | 7 | Tamer state 17: MURD, then DOOA |
| Lifetime is 0 (written as <=0, compared unsigned) | section 0x47 | 3 | Partner state 8: DOOA, natural death |
| Otherwise | section 0x48 | 9 | Tamer state 19: MURD, a life lost |

Sick for 12 hours and natural death reach the same section (`src/main/partner_impl.c:1907-1944`). Sections 0x47-0x49 are not in MAPHEAD: they resolve after the map change, in scripts 165 and 178, which jump to script 212. That clears trigger 1 and pstat 0xFA again and tops up HP and MP. The map is row 205 if trigger 0xDC or 0xD6 is set or pstat 1 (prosperity) is 50 or more, else row 218, exit 1 (`src/murd/murd.c:780-786`, `src/dooa/dooa.c:1734-1740`).

From the glade, a fresh card lost and ended on row 218 at (-87, 0, -528). [run] That is the routine 9 route. [inferred] The last-life and lifetime routes were never run from row 239, and neither has a map dependency in MURD or DOOA that a search found. [inferred] Losing the last life also forgets (10-tamer level)*2*learned/100 moves, rounded, through `lostAllLives`, `CALL_ROUTINE` 0x2F in script 212's section 0x49 (`src/main/script_common.c:2456-2518`, decoded from the disc); `unlearnMove` clears bits 16-31 of the word on every call, so one forgotten move can take up to 16 others (`src/main/utils2.c:519-540`).

### 7.16 What is reloaded after a battle

A flee or a loss changes map, which reloads everything. A win re-reads only the tile set (TFS) (`src/main/tamer.c:863`), the likely reason being that the EFE engine and `BTL_REL` overwrote `GENERAL_BUFFER` (`src/main/btl.c:455`). [inferred] The MAP file, collision and warps are not read again. The map's sound bank comes back during the pose and the BGM on return (`src/btl/battle_main.c:892-902`, `src/main/tamer.c:862`). A fought enemy has HP, MP and stats restored before the pose (`src/btl/battle_main.c:755-762`), and `isOnMap` 0 with trigger 1 clear. The partner's base stats come from the start values plus gains, and its current HP and MP are kept (`src/main/battle_ui.c:488-494`).

Records are re-read on every map load, so a record's `curHP` below HP applies again each visit, while a re-fight on the same load comes back full (`src/main/overworld.c:1052-1053`). A map's `SET_DIGIMON` is what puts a beaten NPC back. After a win on the glade the TFS bytes in `GENERAL_BUFFER`, the collision grid, the 28 sprites, the camera and the day palette all matched the package's files, and the boss had left the map; on the next visit it stood at 8800/8800. [run]

### 7.17 Worked example: the secret boss

`BOSS` in `tools/gen_newmap.py`: H-Kabuterimon (species 60, level 5, radius 250, specials Nature, Battle, Fire), HP 8800, MP 16000, Off 820, Def 640, Speed 520, Brains 400, bits 5000. Trigger 1 clear, so it can be fled, it pays MP and it runs off. Autotalk 0, no flags, back every visit. Brains 400 keeps it from going senile, and its kit has no buffs.

| Anim | Tech | Name | Power | MP paid | Acc | Element | Status, chance | Weight |
|---|---|---|---|---|---|---|---|---|
| 50 | 47 | Buster Dive | 500 | 258 | 80 | Battle | Confusion 14 % | 35 |
| 56 | 33 | Bug | 500 | 354 | 80 | Nature | Flatten 35 % | 25 |
| 55 | 35 | Insect Plague | 58 | 96 | 83 | Nature | Poison 90 % | 40 |
| 60 | 0x70 | High Electro Shocker | 218 | 0 | 100 | Fire | none | 10, dead |

- **Picks** are 35:25:40 with no idle share, a mean of 217 MP. 16000 MP buys about 73 picks; Bug drops out below 354, Dive below 258, all below 96. [inferred] Earlier notes that say 6000 MP are stale.
- **Damage at F 30:** Dive or Bug on Def 600 is 648..792, on Def 999 is 288..353, and on Def 320 or less is 900..1100. Plague on Def 600 is 74..91.
- **Finisher:** (820+218)*F/30, so 934..2283 at F 30, 1245..3044 at F 40 (MetalGreymon), 1868..4567 at F 60. A partner with less HP than that can be one-shot.
- **Meter:** goal 2480, so 124 s with nothing else, +99 per finished attack, +148 when it blocks, +198 when its attack is blocked. In three `fight` runs against a maxed partner its anim first appeared at 42, 62 and 82 s by the wall clock. [run]
- **Hit rolls, acc 80:** it hits a defender of Speed 450 at 65 % moving, 62 % standing, and of Speed 999 at 43 % and 36 %. A Speed 450 attacker hits it at 61 % and 58 %. After Dive or Bug its buffer is -155, so every hit lands for 52 ticks; it takes 86 ticks to reach 100.
- **Its element sums:** Ice 50, Fire 45, Battle 35, Air 35, Nature 30, Mech 30, Filth 22. Off 600 with a 500-power Battle tech deals 482..589 to it. Poison on it is 88..264 per 100 ticks, about 35 HP/s.
- **A win** against the default partner of `measure` (5500/5000/600/600/450/450) pays HP +2, MP +4, Off +2, Def +2, Speed +2, Brains +1 at 88 %, 5000 bits and item 0 at 10 %. Learning chances by special slot: Dive 16, 7, 0; Bug 12, 8, 0; Plague 24, 16, 13.
- **Flee point** (3400, 0, 1150) is only a bearing. From wherever the fight ended it heads east, or west if it stands east of x=3400, through whatever is in the way. [inferred]
- **Repeats:** 5000 bits beats its item cost, as heals of 500, 1500, 5000, 9999 HP cost 100, 500, 1000, 2500 bits (`src/main/item.c:745-781,2064`), and a partner just below a Brains threshold can hit the end box freeze (12.1). [run]

Scale, from the disc's records as HP/MP/Off/Def/Speed/Brains/bits: Machinedramon on MGEN99 9400/9000/999/650/400/300/9999, MetalGreymon on MGEN98 8600/7800/900/620/400/300/6200. Stock no-escape bosses pay no MP, as they run with trigger 1. [inferred]

**The sim is an estimate, and an optimistic one (7.18).** `tools/fight_sim.py` reads the boss from `BOSS` and the moves from your disc. Win rates, with no items, 2 medium heals and 3 large heals:

| Partner | HP/MP/Off/Def/Speed | No items | 2 x 1500 | 3 x 5000 |
|---|---|---|---|---|
| Fresh MetalGreymon | 4500/4000/550/550/400 | 0.6 % | 2.6 % | 15.6 % |
| Average Ultimate | 5500/5000/600/600/450 | 4.6 % | 24.2 % | 81.2 % |
| Trained | 7000/6500/750/750/600 | 38.8 % | 70.2 % | 99.8 % |
| Maxed | 9999/9999/999/999/999 | 97.8 % | 99.8 % | 100 % |

Champions and Rookies never win. On the same rules, for the fresh, average, trained and maxed partners with no items, stock MetalGreymon on MGEN98 gives 1.2, 8.2, 56.4, 99.4 % and Machinedramon on MGEN99 gives 0, 0.4, 7.8, 85.6 %, so the boss falls between them in this model. Its docstring lists what it leaves out: buffs, flatten acting as stun, the partner's fixed proportions, block state, walking, and everything in 7.11. A partner that heals pays for its moves too, so the fresh partner with 3 large heals runs out of MP in 60 % of fights.

### 7.18 How to measure a fight

```
py -3.12 tools/verify_newmap.py measure --map glade --seed <card dir> --stats hp,mp,off,def,speed,brains [--heal N]
```

It evolves the partner to MetalGreymon, sets the stats and moves once, warps in, talks, then pokes nothing and presses only the finisher button, so the partner stays on Your Call with no items and no charge. `--heal N` is the one exception: it stands in for N large recovery items, adding 5000 HP when the partner falls below 2500, with none of a real item's delay. It prints boss and partner HP and MP and the picks every 15 s, then `MEASURE` lines (`measure_fight` in `tools/verify_newmap.py`). PASS only means it finished. `fight` mode is no measure: it rewrites both sides' HP and MP every poll and forces the boss to 1 HP (`win` there).

Read it with these traps in mind:
- "Boss HP left" is the last sample taken in state 1, because the game restores it as the fight ends (`src/btl/battle_main.c:748-758`). It can miss the last hit.
- Seconds are wall clock, and headless runs are not a speed measure. `BATTLE_FRAME_COUNT` / 20 is game time.
- "MP ran out" is the first time its MP read below 96, its cheapest move.
- Picks count anim changes. MP spent divided by cost is the firmer count, as in 7.5.
- Single runs vary with AI, misses and finisher timing, so take several. [Horizon]
- Tamer state 20 is the game's announcement state, and the warp the test makes cannot finish while it waits for a button. All six stats at their maximum earn medal 7 (`src/main/tamer.c:738-746`), which stopped 4 runs of 4, so use Brains 998. It can also come from other medals, a tamer level or the partner falling sick (`src/main/tamer.c:795-796`, `src/main/partner_impl.c:1898`, `src/main/item.c:3055`); one trained run stopped there for a reason not traced. Run it again.

Real fights on the shipped numbers, with only the finisher button pressed [run]:

| Partner HP/MP/Off/Def/Speed/Brains | Healing | Won | How they went |
|---|---|---|---|
| 5500/5000/600/600/450/450 | 3 x 5000 | 0 of 4 | 106-139 s, boss left on 142, 1856, 2755 and 4779 HP |
| 7000/6500/750/750/600/600 | none | 0 of 4 | 57-88 s, boss left on 3204, 3863, 4628 and 6702 HP |
| 7000/6500/750/750/600/600 | up to 2 x 5000 | 3 of 3 | 73-104 s, 1 or 2 heals used, partner left on 3050-6482 HP |
| 9999/9999/999/999/999/998 | none | 2 of 4 | Wins in 57 and 71 s; losses in 101 and 102 s, boss left on 727 and 511 HP |

So it is a hard fight that a trained partner can win with large heals, and a maxed partner with none about half the time. These are small samples with no commands given, and a real item costs time that `--heal` does not.

The sim is too kind. For these rows it gives 81.2 %, 38.8 %, 99.8 % (its column for 3 heals, the nearest) and 97.8 %. So the stock comparison in 7.17 does not show where the boss falls in the real game; no stock boss has been measured this way.

One of the maxed wins never gave control back. Its Brains of 998 can cross 999 on a win, so that is probably the end box freeze (12.1) [inferred]: the run ended before anything looked at the screen.

---

## 8. The partner

Read from `src/main/partner_impl.c` unless another file is cited; a bare `:N` is a line in that file. This is the USA path. No test in this repo exercises the care timers on purpose, so these rates are read, not measured. Horizon's 408 h lifetime, +3/-3 region tiredness and 168 h Champion wait are its own changes, not stock [Horizon]. Stock bugs named here have their detail in section 12.

### 8.1 Time

20 ticks are a game minute (1 s at 20 Hz), 1200 an hour (1 min), 28800 a day (24 min), and 30 days a year. `tickGameClock` (`src/main/clock.c:96-172`) moves `CURRENT_FRAME` only with `GAME_STATE` 0, not fishing, the map layer on and `IS_GAMETIME_RUNNING` set. Pstat 0 sets the speed: 0 normal, 1 double, 2 half. Value 3 blocks pause (`src/main/utils2.c:408`), but the clock rewrites it to 0 on its first running tick (`src/main/clock.c:109-112`), so 3 never stops time and the branch at `src/main/clock.c:115-117` is dead. Each hour: evoTimer +1, lifetime -1. Each midnight: age +1 and `dailyPStatTrigger`. Each year: tamer level +1 at 5% if happiness is 100, -1 at 10% if negative. `tickConditions` (:2059) runs the care ticks only in `PARTNER_STATE` 1, with the clock running and moved and no fade lock (:2072-2074), so "per N ticks" below means clock ticks in the field. Other activities skip the clock and pay a lump:

| Event | Clock | Food, or if hungry | Poop, or if poopy | Lifetime | evoTimer |
|---|---|---|---|---|---|
| Battle, any outcome (`src/main/tamer.c:885-965`) | +20 min | -20, starvation -40 | -2, timer -400 | -1 if the hour rolls | none |
| Training (`src/trn/trn_reward.c:721-804`) | +1 h | -60, starvation -120 | -6, timer -1200 | -1 | +1 |
| Sleep (8.4) | to wake hour | timer reset | no change, or a pile if poopy | -hours slept | +default hours |

The battle lump has no age, daily reset or year check, so three battles cost a lifetime hour with no evolution progress. That matters for any repeatable fight.

### 8.2 Meters

- `PARTNER_PARA` (`0x80138460`, 0x84 bytes, `include/dw/params.h:34-100`) is i16 after the condition word: +00 condition, +04..+0A sleepy and wake h:m, +0C/+0E hours awake/asleep, +10 timeAwakeToday (10-minute units), +12 sicknessCounter, +14 missedSleepHours, +16 tirednessSleepTimer, +18 poopLevel (1 = 200 ticks), +1E virusBar, +20 poopingTimer (ticks), +22 tiredness, +24 subTiredness, +26 tirednessHungerTimer, +28 discipline, +2A happiness, +30 timesBeingSick, +32 sicknessTries, +34 areaEffectTimer, +36 sicknessTimer (hours), +38 injuryTimer (hours), +3A nextHungerHour, +3C energyLevel, +3E foodLevel (minutes), +40 starvationTimer, +42 weight, +48 remainingLifetime (hours), +4A age (days), +4C/+4E/+50 train boost flag/value/timer, +52 careMistakes, +54 battles, +56 evoTimer (hours), +66..+72 gym counters, +76..+80 Sukamon backups.
- Bounds (:868-911): happiness -100..100, discipline and tiredness 0..100, energy 0..energyCap, weight 1..99. Discipline and happiness start at 50. Tamer level is 0..10 (`src/main/tamer.c:1309-1338`).
- `subTiredness` is read and cleared but nothing raises it on USA (only :600, :1403, :1408 in `src`), so tiredness does not grow with time. It comes from battles, training, sickness and disliked maps; food only lowers it (`src/main/item.c:2984-2990`).
- `RAISE_DATA` (`0x801225BC`, by type, 0x1C each, `include/dw/partner.h:6-22`): hungerTimes[8], energyCap +08, energyThreshold +09, energyUsage +0A, poopTimer +0C, poopSize +10, favoriteFood +11, sleepCycle +12, favoredRegion +13, trainingType +14, defaultWeight +15, view xyz +16. +0B is padding and +0E has no stock reader, so both are free per species.
- `PARTNER_STATE` (`src/main/partner.c:218-258`): 1 normal, 3 sleep, 4 scold, 15 praise, 5 feed, 6 toilet, 7 wild poop, 8 dying, 9 eat poop, 10 bubble, 11 idle, 12 scripted, 13 evolving, 14 rebirth.

### 8.3 Conditions, sickness and injury

| Bit | Name | Set when | Cleared by | While set |
|---|---|---|---|---|
| 0x01 | sleepy | HOUR inside sleepy..wake; the minute is not tested (:1021-1034) | sleep, or the wake hour passing | 8.4 |
| 0x02 | tired | tiredness >= 80 (:1415) | tiredness below 80 | happiness -2 per 100 ticks |
| 0x04 | hungry | foodLevel <= 0 (:1516) | a meal with enough energy, starvation at 0, sleep | starvation -1 per 10 ticks |
| 0x08 | poopy | poopLevel <= 0 (:1671) | toilet, wild poop, sleep, porta potty | pooping timer -1 per tick |
| 0x10 | unhappy | condition is 0, happiness < 0, every 200 ticks at chance abs(happiness) - discipline % (:1048-1059) | happiness >= 0, a scold, or another condition's bubble (:1596-1600) | happiness +1 per 15 ticks; every base stat -2% per 300 ticks, floor 1 (:1061-1100) |
| 0x20 | injured | after a won fight with damage taken, at chance tiredness - HP% (`src/main/battle_ui.c:209-221`, `:323-326`, `src/btl/battle_main.c:919`) | medicine, eating poop, or 12 h turning into sickness | battle penalty |
| 0x40 | sick | below | medicine, eating poop | happiness -10, discipline -5, tiredness +5 per hour (:1867-1873); battle penalty |

- Battle penalty for 0x60: Off, Def, Speed and Brains -20% for the fight (`src/btl/battle_setup.c:195-201`), MP cost +50% (`src/btl/battle_main.c:2527-2530`). A sick partner also makes every attack in the fight an auto-hit, in both directions (`src/btl/battle_main.c:1935-1937`).
- Routes into sickness: sicknessCounter >= 10 rolled hourly; more than 12000 ticks on a disliked map (:1858-1866); 12 h injured (:1887-1893); item chance (`src/main/item.c:3035-3055`).
- The hourly roll: tries +1, then chance = (tries-10)*5 + (tiredness-50) stored in a u8 (:1844-1847). A negative sum wraps to 161 or more (the lowest is -95, at tries 1 and tiredness 0), so a roll is certain unless the sum lands in 0..99. Becoming sick this way costs happiness -20.
- Sick for 12 h: lives -1 and MAPHEAD 0x4DE, if the tamer is idle, no script runs and lifetime is not 0 (:1907-1914).
- Item 0x0D cures injury always and sickness at 20%; item 0x0E cures both always (`src/main/item.c:2234-2243`, `:2937-2951`). Evolution and rebirth clear the condition word.

### 8.4 Hunger, energy and sleep

- `foodLevel` is minutes until the next meal time. `setFoodTimer` (:664-756) picks the next even hour at level 1, the next multiple of 3 at level 2, the next `hungerTimes` entry at level 3+. At level 3+ it can land far ahead after a long time skip (:699-711, section 12).
- Not hungry: food -1 per 20 ticks, -3 more per hour once tiredness has held 50+ for 50 ticks (:1503-1505, :1541-1544). A won or fled battle costs -15 (:2056) on top of the 8.1 lump.
- At food 0: hungry, starvation 180, so 90 game minutes (:1516-1520). At starvation 0 hunger passes and the timer restarts. It is a care mistake only if energy is below the species' threshold (:1521-1529).
- Energy: -energyUsage per hour; at 0, weight -1 per 200 ticks (:1508-1513, :1531-1536). Energy at or above the threshold sets trigger 0x280 every tick (:1546-1548).
- `handleFood` (`src/main/item.c:2685-2927`) applies the item's energy, tiredness, happiness, weight, boost, sickness chance and lifetime. Favourite food gives energy x1.4 and happiness +2. Every meal lowers poopLevel by 1. Then `partnerHandleFoodFeed`: hungry with energy at or above the threshold, happiness +5, discipline +1 and hunger clears (:1625-1632). Hungry below it, it stays hungry. Not hungry, any food costs happiness -3, discipline -2 (:1647-1653).
- Refusal (:1431-1492): items 0x21-0x25, and 0x73-0x7C except 0x79 and 0x7A, always; evolution items at the wrong level; items below 0x26 when rand(100) < 110 - (discipline + 10..19), which arms the scold flag; favourite food at 20% when not hungry. After a reasoned scold the next item skips the last two tests.
- `SLEEP_PATTERN` (`0x80122CF4`, 8 x 6 bytes, :184-193) is indexed by sleepCycle. Rows 0-5 are 15 h awake and 9 h asleep at different clock positions; rows 6 and 7 are 1 and 3 h asleep for babies. Levels 1 and 2 stay awake 3 and 7 h; `randomLimit(1)` is always 0, so the roll at :633 adds nothing (`src/main/math.c:5-11`).
- Tiredness moves bedtime: tirednessSleepTimer gains tiredness*3/10 per game minute. At 180 it resets at the next 200-tick mark and timeAwakeToday drops by 1, 10 minutes on paper (:923-949). The hour rounds up and the minute is not tested, so the first drop moves bedtime a whole hour and the next five change nothing.
- While sleepy: level 1 -1 happiness and -1 discipline per 200 ticks, level 2 per 300, level 3+ -2 and -4 per hour (:953-970). sicknessCounter and missedSleepHours +1 per hour except in the sleepy hour (:972-977). Still sleepy at wake hour: the schedule resets, care mistake, tamer level -1 at 1% (:980-1019).
- Going to bed: menu option 6 sets state 3 (`src/main/game_menu.c:2178-2186`, `src/main/partner.c:278-366`). The clock stops. At full fade come `sleepRegen`, `handleSleeping`, then the save menu if a card is known. After the fade in, `handleSpecialEvolutions(2)` rolls.
- `sleepRegen` (:1331-1386): factor = hours slept * 100 / default hours, x1.2 with item 0x22 in the bag, x1.2 on a liked map, x0.8 on a disliked one. HP and MP gain factor% of 70..79% of max. Tiredness falls by factor% of 80..99% of itself. Weight falls by defaultWeight/10. Bedtime exactly at wake hour counts 24 h and can wrap HP past i16 with a multiplier (section 12).
- `handleSleeping` (:1158-1240): the clock jumps to wake hour, with a day and age if it wraps. evoTimer gains the full default hours but lifetime pays only hours slept, so a late bedtime is cheap. Hunger clears and the food timer restarts. A poopy partner drops a pile with no care mistake. Sick costs happiness -20, discipline -10, tiredness +10; injured -10, -5, +5.
- Immortal hour: sleep and Continue set `IMMORTAL_HOUR` to the current HOUR (:1210, `src/main/main.c:3878`). While HOUR matches, the death check, the 12 h sickness checks and evolution checks do not run (:1884, :1919, :2100).

### 8.5 Poop and the pile table

- poopLevel starts at `poopTimer`, stock 18, 24, 36 by stage: 3, 4 and 6 game hours (:40-172). It falls by 1 per 200 ticks. USA doubles it after a toilet or wild poop (:1737-1739, :1766-1768); sleep and the porta potty reset it once.
- At 0: poopy, poopingTimer = (discipline+20)*12 ticks (:1671-1675), so discipline buys time to reach a toilet. These ticks need tamer state 0 (:1660).
- Toilet: collision cell 120 runs MAPHEAD 0x4E2 only when poopy (`src/main/tamer.c:688-712`), routine 0 sets state 6, and `handleToilet` gives happiness +2, discipline +2 (:1730-1743).
- Timer at 0: state 7, a pile at the partner's tile, care mistake, happiness -10, discipline -5 (:1757-1772), tamer level -1 at 1% (`src/main/partner.c:632`), and the scold flag is armed (:1684). Toilet, wild poop and porta potty, not sleep, take weight by (rand(4) + poopSize)/4 (:1745-1755). Every pile adds 1 to virusBar unless the partner is Sukamon (:1319-1320).

`WORLD_POOP` is at `0x80138504`, 100 x `{u8 map, u8 x, u8 y, i8 size}` (`include/dw/partner.h:24-29`), saved whole at payload +0x24.

- A free slot has size 0. The save default is map, x and y 0xFF (`src/main/main_menu.c:4931-4936`). An eaten pile becomes map 0xFF, size 0 (:1826-1829).
- `createPoopPile` (:1242-1329) steps one tile in the facing direction, moves at random off an occupied tile, and takes the first free slot. The scan stops once it has counted 16 piles of this map before a free slot; then, or with the table full, it overwrites from `CURRENT_POOP_ID`. Size is `poopSize`. Object 0xFA8 draws `\ETCNA\UNTI.TMD` where size is nonzero and map equals `CURRENT_SCREEN`, at tile centre, scale size/10 (:547-560).
- Numemon (0x0B) and Sukamon (0x27) walk to a pile when the tamer comes within 200 units, or 300 for size 11+ (:1697-1726). Eating heals by size, adds weight, and cures injury and sickness at 2, 7 or 20% (:1774-1830).
- **A pile saves its map byte,** so a pile dropped on the glade would store 239 on the card. `dw_newmaps.c` writes a free slot there instead (9.3) [run]. In stock every reader is an equality test (:548, :1264, :1280, :1303, :1704).

### 8.6 Discipline and happiness

| Event | Happiness | Discipline | Also | Source |
|---|---|---|---|---|
| Praise | +discipline/10+2 | -5 | | :1142-1143 |
| Scold, no reason | -10 | +2 | clears unhappy | :1130-1135 |
| Scold after a refusal or wild poop | uninitialised, acts as +3 [Vice] | +8 | next item accepted | :1126-1128 |
| Fed while hungry, energy reached | +5 | +1 | | :1627-1628 |
| Food while not hungry | -3 | -2 | | :1652-1653 |
| Toilet | +2 | +2 | | :1732-1733 |
| Wild poop | -10 | -5 | care mistake | :1761-1762 |
| Battle: win, flee, loss | +2, -10, -30 | 0, -6, -20 | section 7.14 | `src/main/tamer.c:850-870` |
| Training | -1 | | at tiredness 70+, -3 and -3 more | `src/trn/trn_reward.c:725-731` |

`handlePostBattleTiredness` (:2040-2057) adds 5 and means to add more for spent MP, but its integer division adds +10 only when current MP is exactly 0. Discipline sets the pooping timer, refusals, the unhappy chance and the partner's brain-fart chance, (70 - discipline)% (`src/btl/battle_main.c:333`). Happiness sets the lifetime drain (8.8) and the year's tamer level roll. A scold at level 3 with discipline 0 and happiness -100 arms `NANIMON_TRIGGER` (:1151-1154).

### 8.7 Liked regions

Section 3.3 has the setup words. On a liked map: happiness +1 and tiredness -2 per 1200 ticks (:1393-1402), sleep recovery x1.2 (:1354-1357). On a disliked map: happiness -1 and tiredness +1, recovery x0.8, sick past 12000 ticks (:1858-1866). The timer clears on warp (:865) and wraps at 28800 (:2084); the status bar icon flickers (:2179-2184). Listing a region on the glade (`build_map` in `tools/gen_newmap.py` writes -1) would let some partners fall sick while a player waits there [inferred].

### 8.8 Lifetime, lives and death

- Lifetime starts at 360 h and resets to 360 on rebirth (:487, :795). An Ultimate reached naturally gains +96 (`src/evl/evl.c:1415-1417`). Some food adds with no upper clamp (`src/main/item.c:2916`); it wraps at 32767 [Horizon]. Heavy chips cost -24 and tamer level -1 at 10% (`src/main/item.c:2284-2312`). Besides the hourly -1: every 4 game hours with happiness < 80, a further -((happiness-80)/-50 + 1), so 1 to 4 (:2092-2097).
- Lives start at 3 and return to 3 on rebirth (`src/main/evolution.c:1078`). A lost battle costs one, and reaching 0 there also zeroes lifetime (`src/main/script_interp.c:1919-1924`). 12 h sick costs one.
- Lifetime 0 with the tamer idle and no script running sets `IS_NATURAL_DEATH` and runs 0x4DE (:1917-1944). All three reach that MAPHEAD section (7.15): lives 0 runs routine 7, MURD then DOOA; lifetime 0 runs routine 3, DOOA natural death; otherwise routine 9, MURD. Only the last has run from row 239 [run]; the other two have not [not run].
- The next map is row 205 (0xCD) if trigger 0xDC or 0xD6 is set or pstat 1 >= 50, else row 218 (0xDA), exit 1 (`src/murd/murd.c:780-786`, `src/dooa/dooa.c:1734-1740`).
- Rebirth (`src/main/evolution.c:1054-1080`, :758-809): baby by egg slot 1, 15, 29 or 43 (`src/dooa/dooa.c:209`), base stats by egg plus `DEATH_STATS` * tamerLevel/100 on a natural death only (:823-834), age 0, evoTimer 0, condition 0, discipline and happiness 50, raisedCount +1.
- Out of lives: `lostAllLives` (`src/main/script_common.c:2456-2534`) forgets ((10-tamerLevel)*2*learned)/100 moves, rounded, at random among ids below 0x3A. `unlearnMove`'s u16 mask clears bits 16-31 of that learned-move word on every call, so one call can wipe several moves (`src/main/utils2.c:519-540`, section 12).

### 8.9 Evolution

`tickConditions` tests every moved clock tick with `PARTNER_STATE` 1, tamer state 0, no daytime fade and no immortal hour (:2098-2126). Thresholds are evoTimer, not age (:2111-2122):

| Level | evoTimer | Rule | Source |
|---|---|---|---|
| 1 | >= 6 | fixed next form, no score | `src/main/evolution.c:674-688` |
| 2 | >= 24 | best score of 3 or more | `src/main/evolution.c:690-722` |
| 3 | >= 72 | score 3+, then highest mean of the requested stats; none by evoTimer 96 means Numemon (11) | `src/main/evolution.c:724-787` |
| 4 | >= 144 | same, no fallback | `src/main/evolution.c:789-849` |

- An Ultimate has no branch. Candidates are `EVOLUTION_PATHS[current-1].to`, 6 slots, -1 empty. `calculateRequirementScore` (`src/main/evolution.c:928-1036`) scores `EVOLUTION_REQS[target]` out of 4: care mistakes (a maximum if flag 0x10, else a minimum); weight within +-5; stats; one bonus from previous form, discipline, happiness, battles (a maximum if flag 0x01) or techs.
- Stats: a level 3 (Rookie) target treats its six stat fields as flags, and the partner's highest stat (HP and MP /10, last tie wins) must have a 1. Other targets need all six at or above the value.
- A raised candidate against an unraised current best drops to 0, and an unraised one against a raised best gets +1. At Rookie and Champion the mean then decides, so a raised best with the higher mean stays. The running total also leaks between candidates (`src/main/evolution.c:773-778`, section 12), so slot order can matter.
- Special mode 2 rolls after sleep (`src/main/partner.c:359`). 30%: from 0x15 or 0x0A with discipline 100, happiness 100, tiredness 0 to 7; from 0x19 with battles >= 50, discipline 100 to 58; from 0x26 with Def >= 500, discipline 100 to 47. 50%: from 0x02, 0x10, 0x1E or 0x2C on screen 1 to 32 (`src/main/evolution.c:883-902`).
- Special mode 3 rolls after praise or scold (`src/main/partner.c:417`) and at Champion evoTimer 360 (:2123). 30%: from 0x18 or 0x23 at evoTimer exactly 200 to 49. Certain: `NANIMON_TRIGGER` to 53. 50%: any level 4 at evoTimer >= 240 to 28 (`src/main/evolution.c:903-916`).
- Sukamon: virusBar >= 16 sets the target to 0x27, stores the old type in pstat 5 and backs up the stats (:2127-2145). Routine 2 returns to pstat 5's type (`src/main/partner.c:1153-1157`). Any evolution out of Sukamon ignores the gains table (`src/evl/evl.c:1357-1360`, `:1509-1514`, section 12).
- Applying (`src/evl/evl.c:1332-1493`): per stat, if stat >= gain then +gain/10, else (gain+stat)/2; caps 9999 and 999. Targets 0x0B, 0x27, 0x35, 6 or level below 3 scale by gains.brains/10 instead. With no script running, a Champion target gives tamer level +1 at 20%, an Ultimate at 100%. Weight goes to default, care mistakes and battles to 0, evoTimer to 0 if the level changed. The new form learns the lowest-power move of its first special, and slot 3 takes its last finisher.
- Evolution items set `HAS_USED_EVOITEM` (`0x80134E52`, `src/main/item.c:2211`), and only boot clears it (:427). While set, natural evolutions skip gains, tamer levels and the +96 (`src/evl/evl.c:1362`, `:1415`, `:1497`). A stock item below 0x7D must target exactly level+1 or it returns without evolving (`src/main/item.c:2205-2208`). `tools/dwtest.py item` runs only items 0x7D-0x7F end to end, with the port's roster mod unblocking them and clearing the flag (`docs/INTERNALS.md`, "`HAS_USED_EVOITEM` had to be fixed too") [run].

### 8.10 Training

Read: `src/trn/trn_reward.c`, `src/trn/trn_slots.c`. Unread: each stat's minigame, tech learning in training, all of `src/trn2/`, and how upgrades unlock.

- A gain is base * v / 10 * payout / 10 (`src/trn/trn_reward.c:169-182`); HP training's bases are 80 HP and 2 Off, and its payout is 10 unless pstat 0xF6 is 1, which runs the slots (`src/trn/trn_hp.c:68`, `:127-130`). `TRN_calculateTrainingMultiplier` (`src/trn/trn_reward.c:594-719`) gives a neutral v of 10: trainingType gives 8..11 per stat; level x0.9 for 1-2, x1.0, x1.1, x1.2 for 5; x1.2 for the gym upgrade (trigger 0xDB for HP, Def, Speed; 0xFB for MP, Off, Brains); x1.2 for a counter of 10+, both only with flag 0 (`src/trn/trn_off.c:234` passes 6, `src/trn/trn_speed.c:110` its instance id); x1.1 with item 0x21. At tiredness 100 gains are 1.
- Boost: a set bit in trainBoostFlag replaces /10 with * value / 100 for that stat. Bits: 1 Off, 2 Def, 4 Speed, 8 Brains, 0x10 HP, 0x20 MP. The timer is duration * 1200 ticks and only clears the flag (`src/main/item.c:3025-3033`, :2087-2091).
- Gym counters rise only while the partner is level 1 or 2 (`src/trn/trn_hp.c:69-71`). Brains training asks for mode 1, the MP row (`src/trn/trn_reward.c:510`), so `upgradeBrainCounter` never reaches the multiplier in TRN.
- Slots (`src/trn/trn_slots.c:180-200`): no match 5, triple 20, triple of the trained stat 40, triple 7 100. With assist 0 (half the rolls) the third reel is pushed off a would-be triple, so a triple is impossible; assist 1 pushes off 7-7-7; only assist 2, one roll in ten, can land it (`src/trn/trn_slots.c:333-343`, `:361-362`).

## 9. Saves

### 9.1 Card file and payload

`BASLUS-01032DMR*` with the last character replaced by the slot's hex digit, one block, 0x2000 bytes (`src/main/main_menu.c:1481`, `:3750`). `SAVE_FILE` is at `0x8013192C`: a 0x200 header ('SC', icon flag 0x13, block 1, title[64], reserved 12, PocketStation 16, icon CLUT, 3 icon frames), then `saves[0]` at `0x80131B2C` and `saves[1]`, 0xF00 each (`src/main/main_menu.c:140-150`). Payload offsets are computed from the struct (`src/main/main_menu.c:88-138`); they agree with Horizon's table and `dw_newmaps.c`.

| Offset | Field | Offset | Field |
|---|---|---|---|
| +000 | mainState: 0 never saved, 1 field, 2 after the ending | +290 | scriptState, 0x17C |
| +004, +014 | tamer, partner position (VECTOR) | +40C | inventory, 0x5B |
| +024 | worldPoop[100], 0x190 | +467, +47B | tamer name, partner name, 20 each |
| +1B4, +1B8 | money, partner type | +48F..+491 | lives, tamer level, raised count |
| +1BC | learned moves (2 u32) and 2 more u32, 16 | +492 | npcMaps[8] |
| +1CC, +1CE | current frame, last handled frame | +49A..+49D | screen, previous screen, exit, previous exit |
| +1D0..+1DC | year, day, hour, minute, playtime | +49E, +4BC | tamer waypoints X[30], Y[30] |
| +1E0 | PartnerPara, 0x84 | +4DA..+4E0 | previous tile, waypoint state, start tile, active |
| +264 | merit and 5 battle counters | +4FC | checksum |
| +270 | Stats, 0x20 | +500 | RegisteredDigimon[40], 0x40 each |

- **Checksum:** `createSavegameChecksum` (`0x80112D34`, `src/main/main_menu.c:5004-5014`) is the unsigned byte sum of payload bytes 0..0x4E3 plus 0xA00 bytes from +0x500. Bytes +0x4E4..+0x4FB and the checksum itself are not covered. On load, copy 0 is tried, then copy 1, then error 2 (`src/main/main_menu.c:3751-3776`). Offline card edits must re-sum both copies.
- **Pstats:** `scriptState` covers smth[212], cards[33], triggers[100] and pstats 0-34 only (`include/dw/script.h:158-164`). Pstats 35-255 and the script stack are never saved, but Continue does not clear them either: `initializeScripts` does, once at boot (`src/main/script_interp.c:2205-2208`). After the ending a Continue keeps the old RAM values, so session state in 35+ must tolerate stale values.

### 9.2 Writing and Continue

| Menu | Reached from | Payload source (`src/main/main_menu.c`) | Return address at the checksum |
|---|---|---|---|
| 0x11 | new file | `initializeDefaultSavegame`, no `writeSavegame` (`:3580-3632`) | not filtered |
| 0x2B | sleep (`src/main/partner.c:328-338`) | `writeSavegame` (`:3971-3996`) | `0x801114A4` |
| 0x41 | `openSaveMachine` (routine 0x30), the ending's `gameClearSave` | `writeSavegame` (`:4330-4355`) | `0x80112060` |

- The order is `writeSavegame(&saves[0])`, title, checksum, copy to `saves[1]`, `MemCardWriteFile` of the whole 0x2000. `writeSavegame` (`src/main/main_menu.c:5071-5134`) first calls `recalculatePPandArena`, which rebuilds pstat 1 from recruit flags of types 3..0x3A and clears triggers 0x25-0x27 when pstat 3 is out of range (`src/main/main.c:3902-3930`). It stores pstat 0 as 0 if a script had it at 3. The save machine refuses a partner below level 3 (`src/main/main_menu.c:5279-5281`).
- Continue (`src/main/main.c:3459-3473`): `initializeMap` sets exit 9 (`src/main/map.c:3936-3937`), `runMapHeadScript(screen)` loads the map, then `initializeLoadedMap` (`src/main/main.c:3791-3899`) overwrites positions and care state from the save and sets the immortal hour. The exit only selects spawn slot 9 until then, and no script condition tests `CURRENT_EXIT`: the one script read is START_BATTLE's flee branch, which copies it to `PREVIOUS_EXIT` (`src/main/script_interp.c:1935`).
- `npcMaps` is written and loaded but never applied (`src/main/main_menu.c:5042`, `:5105`), so a beaten boss returns through MAPHEAD's `SET_DIGIMON` [inferred]. An NPC touching the tamer at load is removed, or for some species the tamer moves to the saved exit's spawn (`src/main/main.c:3821-3857`).

### 9.3 What `dw_newmaps.c` rewrites

An entry hook on `createSavegameChecksum`, filtered to the two return addresses above, runs after `writeSavegame` and before the sum and the copy, so only the buffer changes and play goes on where it is.

- Screen is 239: +49A and +49C become the host row and stock exit, read from slot 1 of the glade's own warp table. +04..+0C become that spawn, +14..+1C the partner 200 units off by rotation, as a warp places it. +4DA..+4E0 become that spawn's tiles, with waypoint count and active 0.
- Previous screen is 239: +49B and +49D become the current screen and exit. Host row with exit 4, as screen or previous screen: that exit byte becomes stock exit 0.
- It also frees any `worldPoop` slot that names the row (8.5). The pile stays in play and is gone after Continue. Not rewritten: `npcMaps` (no reader), waypoint arrays (count 0), title. `verify_newmap.py --map glade` saves on the glade and next door, continues, and in `off` mode loads both cards on the stock game [run].

## 10. Engine

### 10.1 Boot and the 20 Hz tick

- `main` (`src/main/main.c:3371-3530`): `initializeHeap`, `SsInit`, `ResetGraph`, `GsInitGraph` 320x240, `PadInit`, `CdInit`, memory cards, model and object tables, file queue, scripts (reads MAPHEAD.SCN and the DG.SCN table), view, UI, effects, inventory, clock, status objects. Then forever: movie 0 and a 600-tick landing screen until Start, `runMainMenu`, a switch on `MAIN_STATE`, and `gameLoop` until `MAIN_STATE` is 3, set only by routine 53 (`src/main/script_interp.c:516-519`), whose DG.SCN callers sit beside the ending's routines 0x34 and 0x36 [inferred]. State 0 is a new game (scene, movie 1, partner 3 or 0x11 by pstat 0xFE, MAPHEAD section 0xCC), 1 Continue, 2 Continue after the ending (movie 2 first), 3 `cleanupGame`, movie 3, back to the title.
- `gameLoop` (`src/main/main.c:3933-3963`): poll pad, clear ordering table, `processInput`, `updateTournamentRegistration`, `tickScript` if `IS_SCRIPT_PAUSED` is 0 (1 means no script is running), set view, `tickObjects`, `renderObjects`, `DrawSync(0)`, `VSync(3)`, swap, `GsDrawOt`, `handlePause`.
- `VSync(3)` waits 3 vblanks, so one tick is 1/20 s on NTSC, taking vblank as 60 Hz [inferred]. The same wait ends the battle, tournament, VS and new-game loops (`src/btl/battle_main.c:2606`, `src/std/std_main.c:6205`, `src/vs/vs_main.c:1008`, `src/main/main.c:3761`); the title and main menu use `VSync(0)` (`src/main/main.c:3656`, `:3712`). The port's Run Speed measurements fit 3-vblank logic frames [run]. Whether a port option changes the tick was not checked.
- Every timer, walk and AI step counts these ticks, so the frame rate cannot change without changing game speed [Horizon]. A battle runs nested inside a script tick: `gameLoop`, `tickScript`, op 0x66, `startBattle`, `BTL_battleMain` (`src/main/script_interp.c:1897`, `:1917`, `src/main/tamer.c:846`).
- `WORLD_OBJECTS` (`0x80137418`, 128 slots) holds everything else: `addObject(id, instance, tick, render)` takes the first free slot and ticks run in slot order (`src/main/world_object.c:19-104`). Fixed ids: 0xFA0 map, 0xFA2 clock, 0xFA8 poop, 0xFB9 playtime, 0x404 file queue. There is no per-map scheduler.

### 10.2 File loading and overlays

- Section 2.2 has the two routes. `readFile` blocks, finds the name in `FILE_OFFSET_TABLE` by basename and rounds up to whole sectors (`src/main/file.c:16-42`, `:80-96`). A name not found returns silently, and the caller parses stale data. The async queue has 32 slots, ticked by object 0x404 (`src/main/file_queue.c:99-182`); a request with size -1 uses `CdSearchFile` (`src/main/file_queue.c:127-139`).
- `loadDynamicLibrary(id, ...)` reads `REL_BIN_FILES[id-1]` to `REL_BIN_OFFSETS[id-1]`, sync or queued (`src/main/utils2.c:376-388`). There are 16 overlays on 9 bases, so a RAM write into these ranges hits whichever is resident. Sizes are file sizes without BSS (`src/main/file_table_us.c:807-822`).

| Base | Id, file, size | Loaded at |
|---|---|---|
| `0x80010000` | 7 MOV 0x73428 | `src/main/utils2.c:397` |
| `0x80052AE0` | 1 BTL 0x2349C, 2 STD 0x2D3E8, 6 VS 0x20788 | `src/main/btl.c:379`, `src/main/utils2.c:661`, `src/main/main_menu.c:4194` |
| `0x80053800` | 5 KAR 0x101EC | `src/main/kar.c:11` |
| `0x80060000` | 4 EVL 0x9008, 15 ENDI 0x1534, 16 EAB 0x2054 | `src/main/evl.c:108`, `src/main/script_interp.c:1850`, `:1844` |
| `0x80070000` | 3 FISH 0xD96C, 8 DOO2 0xB6EC | `src/main/fish.c:124`, `src/dooa/dooa.c:365` |
| `0x8007C000` | 14 MURD 0x2960 | `src/main/tamer.c:1703`, `:1738` |
| `0x80080000` | 9 DOOA 0x78AC | `src/main/partner.c:654`, `src/murd/murd.c:788` |
| `0x80080800` | 11 SHOP 0x7E10, 12 DGET 0x14F8 | `src/main/script_common.c:3076`, `:3097` |
| `0x80088800` | 10 TRN 0x6BD4, 13 TRN2 0x54C0 | `src/main/map.c:5460`, `:5466` |

SHOP has no USA yaml and its source is linked into the main EXE (`config/us/main.yaml:132`), so what the file's bytes do is unread. EVL, EAB, ENDI, DGET, DOOA and MURD were opened only at their entry functions; the ending and the Machinedramon sequence are unread.

### 10.3 Memory map and heap

Stock words read from `SLUS_010.32`: text `0x80090800` size 0xA4800, BSS `0x80134C4C` size 0x8B000, `_end` `0x801BFB64`, `_stack_addr` `0x801FFF00`, `_stack_size` 0x40 (KB).

| Range | Use |
|---|---|
| `0x80000000-0x8000FFFF` | BIOS kernel [Horizon] |
| `0x80010000-0x800887FF` | `GENERAL_BUFFER` (`include/dw/main.h:8`): MAP, sound banks, TFS, overlays, battle effects (2.4) |
| `0x80088800-0x8008FFFF` | `TEXTURE_BUFFER`, temporary TIMs; TRN or TRN2 when training |
| `0x80090000-0x800907FF` | PS-X EXE header, as linked (`config/us/main.ld:8`) |
| `0x80090800-0x80134C4B` | code, rodata, data |
| `0x80134C4C-0x801BFB63` | sbss and bss |
| `0x801BFB70-0x801EFEFF` | heap, 197,520 bytes |
| `0x801EFF00-0x801FFEFF` | stack, 64 KB |

- Fixed bss buffers (`config/us/symbols.txt`, `src/main/main.c:3306-3316`, `src/main/sound.c:341`): `SEQ_BUFFER` `0x80148574` (0x5800), `GS_OT_TAGS_0/1` `0x80155B68` (0x4000 each), `GS_WORK_BASES` `0x8015DBA8` (0x14000 each), `PARTNER_MODEL_BUFFER` `0x801878DC` (0x18800), `TAMER_MODEL_BUFFER` `0x801A1398` (0x7800), `MAP_COLLISION_DATA` `0x801AF398`, `MAPHEAD_SCRIPT_BUFFER` `0x801B1D30` (0x61A8), `SCRIPT_OFFSET_TABLE` `0x801B7ED8` (0x2000), `MAP_SCRIPT_BUFFER` `0x801B9ED8` (0x4000).
- Two stock reads overrun, so a strict bounds check would break them. BOYS.MMD is 0xDECC in a 0x7800 buffer and runs into the spare 0x6800 array after it (`src/main/main.c:3314-3315`, `src/main/file_table_us.c:16`). MAPHEAD.SCN rounds to 0x6800 in a 0x61A8 buffer and spills into the offset table, which is read straight after (`src/main/script_interp.c:2203-2204`) [inferred from symbol adjacency].
- After a mode change only some things return: section 2.4 and 7.16 list them. Whether row 239's sprite texels and the 21:9 sliver survive evolution and death scenes (`src/main/partner.c:776-779`, `:808-809`) was not checked.
- VRAM [Horizon; NPC slots and text checked in `src/main/model.c` and `src/main/font.c`]: x 0-319 frame buffers, with CLUTs at y 480-511; x 320-383 icons, tamer and partner textures; x 384-767 y 0-255 map sprites; x 384-575 y 256-511 5 NPC slots of 64x128, `SBOY.TIM` at x 512; x 576-703 y 256-511 16 battle effect slots; x 704-767 y 256-511 text; x 768-1023 y 0-383 the 4x3 tile window; below it shared effect and HUD TIMs.
- Heap: `initializeHeap` (`src/main/main.c:3532-3540`) calls `InitHeap3` from `(_end & ~15) + 16` to `_stack_addr - _stack_size*1024`. The users found are NPC models (`src/main/model.c:3410`), each placed Digimon's `posData` and `momentum` (`src/main/model.c:2829-2830`), and a temporary 0x4800 texture during a model load (`src/main/model.c:3299`). Tamer and partner use fixed buffers. Battle data does not use `malloc3` [Horizon]. Free heap across stock screens is 40.2 KB minimum (screen 48), 138 KB median [Horizon]. Nobody has measured what is free on row 239 with the boss loaded, how many NPCs a new map can hold, or which of heap, effect heap, VRAM or SPU fails first.

## 11. Sound, models and fonts

Audio has no test: `verify_newmap.py` has no BGM, sound or SPU check, so the glade's bank 0 and `SET_BGM 1` are only as good as someone listening.

### 11.1 Sound banks and music

Nine VAB slots with fixed SPU addresses and RAM headers (`src/main/sound.c:22-56`).

| Slot | File | SPU start | Loaded | Stride |
|---|---|---|---|---|
| 0, 1 | SS.VHB, SL.VHB | `0x01010`, `0x0E800` | `initializeMusic` (`sound.c:526-530`) | whole file |
| 2 | FAALL.VHB | `0x1B800` | music font (`sound.c:738-751`) | 0x27 sectors, 33 fonts |
| 3 | VLALL.VHB | `0x2A000` | partner voice (`sound.c:686`) | 15 sectors, 16 chunks |
| 4 | VBALL.VHB | `0x31800` | partner (`sound.c:703`) | 7 sectors, 71 chunks |
| 5-7 | VBALL.VHB | `0x34000`, `0x36800`, `0x39000` | one per enemy (`src/main/btl.c:411-414`) | same |
| 8 | ESALL.VHB or SB.VHB | `0x3B800` | map bank (`sound.c:729`), or `loadSB` in battle (`src/main/btl.c:408`) | `MAP_SOUND_PARA` |

- A chunk starts with u32 vhOffset and vbOffset (`include/dw/sound.h:8-11`); FAALL's third and fourth words bound its SEQ data (`src/main/sound.c:748`). `MAP_SOUND_PARA` (`0x80127FF4`, 22 rows) holds start and count in half sectors. The bank is the low 5 bits of the row's flags (`src/main/map.c:4212-4214`), and a reload of the current bank is skipped (`src/main/sound.c:723`). `DIGIMON_VLALL_SOUND_ID` and `DIGIMON_VBALL_SOUND_ID` map type to chunk (`src/main/sound.c:58`, `:694-707`). Types outside 1..65 use VLALL chunk 15.
- `playSound(vab, value)`: on slots 0, 1 and 8, program value/16 and note value%16 + 0x3C; on slots 3-7, program 0 and note value (`src/main/sound.c:641-648`). Effects take voices 10-23 (`src/main/sound.c:567-588`). An animation's sound instruction with bank 4 means the entity's own vab (`src/main/anim.c:339-345`). An oversized VAB body silently overwrites the next bank, and SPU RAM is full by design, about 27 KB slack in pieces, with reverb at `0x7B7C0` [Horizon].
- `SET_BGM` (op 0x5D) takes a font, 1..0x21 (`src/main/sound.c:755`). A font holds several tracks: its SEQ block is copied to `SEQ_BUFFER`, the first word / 4 is the track count, and each word is a track's offset; a track past the end plays track 0 (`src/main/sound.c:480-485`). Fonts 0x1D-0x20 play some tracks once; the rest loop forever (`src/main/sound.c:496-503`).
- Pstat 245, set in the opening bytes of 224 of the 239 MAPHEAD row sections on our disc, picks the track (`src/main/script_interp.c:1584-1637`), and `updateBGM` applies it every clock tick (`src/main/clock.c:171`). Mode 0: track 0 at hours 6-20, else 1. Mode 1: track 0. Modes 2-4: fonts 6, 0x0B, 0x0F, track 2. Modes 5-10: fonts 0x15, 0x1A, 0x1B, tracks 0 and 1 in turn.
- Battle music is font 0x21: track 3 if the first enemy is type 0x73; track 0 for a predefined fight (pstat 0xFA); `ENEMY_MUSIC_MAPPING[type-0x43]` for types 0x43..0x6F; else track 1 (`src/main/btl.c:95-102`, `:360-377`). The glade's boss, type 60 in an open battle, gets track 1. Which tune each track is: not checked.
- All 33 font slots hold data, so a new font must replace one or hook `playMusic`. Decoded `SET_BGM` reaches fonts 1-28 and battle uses 33; fonts 29-32 have no proven caller beyond `seqPlay`'s one-shot rule [inferred]. Limits: 39 sectors per chunk, `SEQ_BUFFER` 0x5800, largest stock SEQ 0x55F8 (font 0x0B, read from FAALL.VHB on our disc). SEQ loops are CC99=20, CC6 count, CC99=30 [Horizon].

### 11.2 MMD models

- `loadMMD(type, modelType)` (`src/main/model.c:3356-3453`): type must be below 0xB4. modelType 0 is an NPC (slots in 5.5, on the heap), 2 the tamer, 3 the partner. The path is `CHDAT\MMD<type/30>\<name>.MMD` with the name from `PTR_DIGIMON_FILE_NAMES` (`0x8011D19C`), though only the basename matters.
- A file is u32 TMD offset, u32 animation table offset, then both (`src/main/model.c:3415-3416`). The table holds u32 offsets by animation id, 0 empty [Horizon]. Texture: `ALLTIM.TIM` sector type*9, 9 sectors, 0x4800 bytes (`src/main/model.c:3301`). Largest stock files are ETEM 97,812 and HOUO 97,496 bytes; the boss's HKAB is 95,960, MUGE 78,588 (`src/main/file_table_us.c`). There are 178 MMD rows against 180 types, unexplained. Horizon's `mmd.py` reports a byte round trip on all 178, not reproduced [Horizon].
- Animation stream (`src/main/anim.c:303-347`): an instruction's high nibble is 0 keyframe, 1 loop start, 2 loop end, 3 texture copy, 4 sound. Layout: frame count, poses of 9 i16 per bone, then code<<12|frame, ending 0 [Horizon]. Skeletons are `DIGIMON_SKELETONS` (`0x8011CE60`), `{s8 object, s8 parent}` per bone [Horizon]; a partner has 34 bone slots (`src/main/main.c:3309-3310`).
- Animation ids read in code: 0, 1 idle; 2, 3, 4 walks; 8 eat; 9 sleep; 0x0A toilet (`src/main/partner.c`); 0x0E..0x13 condition emotes (`src/main/partner.c:1036-1060`); 0x21, 0x22 battle idle (`src/main/btl.c:189-207`); 0x24 run, as in the enemies' run-off after a win (`src/btl/battle_main.c:940-963`); 0x2E+n move n of the species' list. A move also needs the species on that move's EFE list, or the animation plays and costs MP with no attack (`src/btl/battle_effect.c:4660-4678`, `src/btl/battle_main.c:2526-2530`) [inferred].

### 11.3 Font and glyphs

- USA has 79 glyphs: `CHAR_TO_GLYPH_TABLE` (`0x801307E0`) maps 16-bit codes to `GLYPH_DATA` (`0x80130880`), 11 rows of u16 bitmap plus a width word, USA only, and a 0 bit is ink (`src/main/font.c:13-15`, `:34-39`). Codes cover A-Z `8260`, a-z `8281`, 0-9 `824F`, space `8140`, and a small set of punctuation (`src/main/font.c:1391-1401`).
- `drawGlyph` (`src/main/font.c:1544-1706`) writes one glyph as 4bpp through a 50-slot ring of 72-byte upload buffers into the text area at VRAM (704, 256), 256 pixels square, and returns its width. It skips x or y of 244 and above. The font CLUT is at (208, 488), 16 entries.
- Unknown code: the lookup writes the code into the last table entry and stops there, so it draws the last glyph, a triangle at width 12 (`src/main/font.c:1603-1609`). A character outside the table never errors; it just draws wrong. Section 4.9 has the text format.
- By their bitmaps (`src/main/font.c:1406`), `8175` is the apostrophe, `8176` a double quote, `817C` a hyphen, `817E` a triangle and `81A2` a cross, the last two swapped from their Shift-JIS meanings. Adding glyphs needs a hooked or rebuilt table [Horizon].

---

## 12. Known bugs in the stock game

Status says how far each one is proven: `code` is read in the decomp, `disc` is confirmed in the stock bytes, `claim` is reported elsewhere and not checked here. Only 12.1 has been reproduced in the game; every other code row is also [not run].

### 12.1 The battle end box freezes when Brains crosses 700, 800, 900 or 999

A win whose Brains gain crosses a threshold adds the MP Consumption bonus message to the end box. `BTL_appendMPBonusText`, called once from `src/main/battle_ui.c:334`, compares battle-start Brains with start plus gain and picks the highest threshold crossed (`src/btl/battle_hud.c:324-353`). It then adds 4 to the box's expected row count (`src/btl/battle_hud.c:378`).

The text has only one row break. Disc: `BTL_REL.BIN` (base 0x80052AE0) holds `#C1MP Consumption Bonus!` at 0x80072FBC and `reduced by` at 0x80072FD8, and the EXE holds `%!#W` at 0x8013475C.

The USA reader (`src/btl/battle_hud.c:441-472`) has no stop for NUL. It stops only on byte 0x30, the digit `0`, and returns without advancing (:463-466). Disc: the stock instruction at 0x80062EC8 is `addiu at,zero,0x30`, then a `beq` to the return. The box counts as finished only when rows drawn equals the expected count and the wait timer is 0 (:523-530), and `src/main/battle_ui.c:392-398` loops until it does.

| Crossing | Digits appended | What the reader does | Result |
|---|---|---|---|
| 800 | `10` | Stalls on the `0` before it reaches `#W` | Permanent hang |
| 999 | `20` | Stalls on the `0` before it reaches `#W` | Permanent hang |
| 700 | `5` | Reaches `#W` and waits for a button, then draws NULs and the rest of the buffer | Hung in its one run. Stale `#R` or `#W` bytes could still supply the 3 missing rows, so another run could differ |
| 900 | `15` | Same as 700 | Same as 700 |

Only byte 0 of the buffer is cleared between battles (`src/btl/battle_hud.c:255`), which is why stale bytes can decide the 700 and 900 cases. The hang sits inside the win sequence, before the run-off, so play since the last save is lost.

All four were run in the game, once each, with `fight --brains 699`, `799`, `899` and `998` on the glade boss. Every run paid the bits, showed the bonus text and never gave control back in 240 s [run]:

| Start Brains | On screen when it stopped |
|---|---|
| 699 | Rows of garbage triangles |
| 799 | `MP Consumption Bonu` |
| 899 | `MP Consumption Bonu`, then a row of triangles |
| 998 | `dropped sm.recovery` left from the drop text, then `MP Consumption Bonu` |

Only a battle gain fires it. That call is the only one, and battle-start Brains is captured before the injury penalty (`src/btl/battle_setup.c:184-201`), so passing a threshold by training or items skips the message, which a player can use as a workaround [inferred].

`tools/verify_newmap.py fight --brains N` sets up a crossing. Battle Stat Gain makes it likelier, and so does the repeatable glade boss: with Brains 400 against a partner at 699, the first +1 roll is (400*10*100)/(699*10) = 57 % per win (`src/main/battle_ui.c:158-161`), and a miss gets a second roll from combat counters (:183-190).

**The fix** is Battle End Freeze Fix: `end-box-fix` in `dw.vanilla.qol`, built in `tools/gen_qol_mods.py`. It is the one feature on by default, and a player can untick it. On 1.5 the `dw15` feature makes the same writes itself, because those sectors belong to its delta, so there it cannot be turned off. Five writes:

| Where | Stock | Now | Why |
|---|---|---|---|
| `BTL_REL.BIN` 0x80062D58 | `addi v0,v0,4` | `addi v0,v0,2` | The message has 2 rows |
| `BTL_REL.BIN` 0x80062EC8 | `addiu at,zero,0x30` | `addiu at,zero,0` | The reader stops at the end of the text, and digits draw |
| `BTL_REL.BIN` 0x80072FC2 | `Consumption Bonus!` | `Cost Bonus!#R` | The stock line runs past the box's 156 pixels, so it shows `MP Consumption Bonu`, and it has no row break |
| `BTL_REL.BIN` 0x80072FE2 | NUL | space | `reduced by10` needs its space |
| EXE 0x8013475C | `%!#W` | ` pct!#W` | The USA font has no `%` |

The box now reads `MP Cost Bonus!` and `reduced by 10 pct!`. Stopping on NUL cannot strand another message: on the disc, the drop, injury, command and move messages all have exactly the row breaks they count.

The USA font has 79 glyphs (`CHAR_TO_GLYPH_TABLE` 0x801307E0). `drawGlyph` draws any code it does not find with the last one, a triangle, which is what the garbage rows above are. In ASCII it can draw only letters, digits, space and `! + , - . : ; = ? \` [disc].

Tested in the game [run], with the win's text read from memory:

| Disc | Brains going in | Other messages | Result |
|---|---|---|---|
| Stock | 699, 998 | Drop message first | Finished, control back |
| Stock | 799, 899 | None | Finished, control back |
| Stock | Not near a threshold | None, and a drop alone | Finished, control back |
| 1.5, before the fix was built in | 799 | None | Froze on `MP Consumption Bonu`: the control |
| 1.5 | 699, 799, 899, 998 | None | Finished, control back |
| 1.5 | 799 | A learned move after it | Finished, control back |

Stock runs are `fight --brains N [--drops]` in `tools/verify_newmap.py`; `--no-end-fix` brings the freeze back. 1.5 runs are `tools/verify_endfix15.py N`, a win against a wild Digimon on DGHA01. Horizon lists this bug as fixed by its patch 0094 [Horizon].

### 12.2 Confirmed in code

| Bug | How it works | Status | Matters to the port's mods |
|---|---|---|---|
| **End-box freeze** | See 12.1: one row break against an expected 4, and a reader that halts on the digit `0`. | code, disc, [run] | Yes. Battle End Freeze Fix handles it, on by default. |
| **Jukebox title overflow** | `padWithSpaces` loops until its count is exactly 0 (`src/main/script_common.c:2919-2940`). At width 12 a title over 25 bytes makes the count negative, so it writes spaces until the counter wraps. Disc: 10 of 63 titles are over 25 bytes (tracks 0, 1, 14, 21, 22, 23, 24, 41, 51, 53). The freeze needs such a row to scroll into view [inferred]. | code, disc; freeze itself [Vice] | Yes: any player in that menu. A `used > 0` guard is cleaner than the EU guard at `src/main/script_common.c:2922-2925`, which skips unwritten bytes. Titles 22 and 24 (55 and 56 bytes) would still overrun the 64-byte row by about 4 bytes [inferred]. |
| **Tech wipe on losing all lives** | `unlearnMove` builds a uint16 mask, so the AND clears bits 16-31 of the 32-bit word on every call (`src/main/utils2.c:519-540`). Forgetting a move in word 0 wipes ids 16-31; one in word 1 wipes ids 48-57. Only the chosen move's equipped slot is cleared. Disc: EXE 0x800E67A0-0x800E67D8 agrees, and script 212's section 0x49 calls the routine (`64 2F`). | code, disc; [not run] | Yes: a rebirth after all lives are lost, on every species, when `lostAllLives` picks a move at all. It picks (10 - Tamer level)*2 % of the known moves, rounded, so none at Tamer level 10 (`src/main/script_common.c:2485-2494`). No Death is no guard: it patches only `tickDeathCondition`, and a last-life battle loss jumps to 0x4DE without it (`src/main/script_interp.c:1919-1926`). Moves 44/48 and 55/57 are learned in pairs, so a fix must clear both bits. |
| **Sleep heal wraps HP negative** | Sleeping at the wake hour counts 24 hours, 25 if MINUTE > 0 (`src/main/partner_impl.c:1341-1347`). The int16 sum can wrap past the `>= maxHP` clamp (:1362-1378). Level 3+ with no multiplier cannot wrap: at 9999 max HP the heal tops out at 21880. Rest pillow (item 0x22) or a liked area (`partnerAreaResponse` 1) multiply by 1.2 each (:1351-1355): one wraps from full HP at about 9046 max HP, both at about 7908. MP has the same sum. | code, computed; [not run] | Yes for partners with max HP near those values [inferred]. Save Anywhere does not remove sleep. |
| **Sticky evolution-item flag** | `HAS_USED_EVOITEM` (0x80134E52) is set by every evolution item that passes the level check, stock items 71-114 included (`src/main/item.c:2205-2211`). The only clear is in `initializeStatusObjects` (`src/main/partner_impl.c:416-427`), called once at boot (`src/main/main.c:3408`), so it survives rebirth until the game is reset. While set it blocks natural stat gains, Tamer levels, the +96 hour Ultimate bonus and `EVL_scaleBaseStats` (`src/evl/evl.c:1362,1415,1497`). | code, disc; [not run] | Yes. `dw_roster.c` clears it, so with the roster off the stock bug stays. |
| **Leaving Sukamon ignores gains** | From Sukamon every target takes the backup branch (`src/evl/evl.c:1357-1360,1508-1516`): stat = backup + (current - backup/2)/2, with no `EVOLUTION_STATS_GAINS` and no Tamer level. With `HAS_USED_EVOITEM` set nothing changes at all. Backups are written only on the virus-bar path (`src/main/partner_impl.c:2128-2143`), so other routes in use stale values [inferred]. | code; [not run] | Yes. The restored MetalEtemon gets pre-Sukamon stats plus half of what Sukamon trained, never its authored gains (`tools/gen_roster_mod.py:161-163`). The +96 hour bonus still applies. |
| **Attack that only animates** | A species missing from a move's EFE list plays the animation, then the effect script aborts (`src/btl/battle_effect.c:4660-4678`). MP is charged first and no attack object exists, so it cannot hit [inferred]. Disc: `DIGIMON_DATA[76].moves[0]` at 0x8012DE47 is Spit Fire (2), not on its list, and a new form starts on its lowest-power move (`src/evl/evl.c:1434-1455`). ToyAgumon (84) `moves[7]` is Pepper Breath (59), list [3, 31], so its finisher only animates too. | code, disc; in-game result [inferred] | Yes: both are roster species. Writing 1 at 0x8012DE47 fixes Tankmon, which then starts on Red Inferno. A generator assert must read raw `DIGIMON_DATA.moves`, the field the EFE load uses (`src/main/btl.c:469`). |
| **Evolution score leak** | The Rookie and Champion choosers reset `statTotal` and `statCount` only when a candidate wins (`src/main/evolution.c:773-778,838-843`), so a loser after a winner carries its mean and count into the next candidate. Vicen's case: Kokatorimon's 216 becomes (103+216)/4 = 79 [Vice]. | code; [not run] | Yes. Roster grafts and Evolution Unbound add qualifiers, so slot order changes results. `tools/verify_roster_evolution.py:39-44` knows this. |
| **Battles burn life without progress** | `advanceBattleTime` adds 20 minutes once per battle on every outcome and takes lifetime only when that crosses an hour (`src/main/tamer.c:885-965`). Unlike the normal clock (`src/main/clock.c:136-162`) it never adds evoTimer or age, and skips the daily reset and month-end checks. | code; [not run] | Yes for repeat fights such as the glade boss. No Lifespan Loss from Battles does not cover it: that option patches `tamerTickBattleLostLife` (0x800AAE44), not this function (0x800ABCCC). |
| **Hunger skipped after a time jump** | The non-JP branch of `setFoodTimer` takes any later table time with no upper bound (`src/main/partner_impl.c:699-711`). Hand trace: times 2, 6, 10, 14, 18, 22 with old next 18 at hour 1 give next 22, so 21 hours until hungry. Level 3+ only. | code, hand trace; [not run] | Care balance for every species; keep it in mind when testing raise rows. |
| **Selection cancel can run data** | Triangle jumps to `endPointer` unless trigger 0x31 is set (`src/main/script_textbox.c:243-258`); `endPointer` is the end of the text plus 2 bytes (:1040-1041). In a `CALL_ROUTINE` menu Triangle picks the last option instead. Disc: of 327 traced selections, 304 follow the text with `12 00` and a cancel path, and 23 have no filler, so Triangle lands 2 bytes into the next op unless 0x31 guards it. Instant tournament wins are reported [Vice]. Piximon's Training Manual sale overwrites prosperity and the tournament day and cup [Horizon]. | code, disc; exploits are claims | Yes for authoring. Rule for any new selection: text, `12 00`, cancel code. `boss_script` has no selection today. |
| **Move-to softlock** | `tickEntityMoveTo` and `tickEntityMoveToAxis` truncate delta = (target-pos)/duration (`src/main/tamer.c:1134-1175,1216-1238`). A target on the positive side by less than the duration gets delta 0 and never finishes; the negative side snaps at once. X and Z are judged separately. Duration 0 divides by zero, but the compiled code does not trap and MIPS gives -1 or +1, so it snaps [inferred]. | code, disc; [not run] | Authored scripts: keep each positive distance at least the duration. |
| **180 degree look-at softlock** | `rotateEntity` has no branch for equal differences (`src/main/overworld.c:1503-1546`), so an exact 2048 offset never finishes. A script hangs only if it waits on that slot with op 0x4A; NPC tracking self-heals. The first `SET_DIALOG_OWNER` of a talk section queues a mutual look-at (`src/main/script_interp.c:2741-2747`), so a script can meet this without writing a look-at. | code; [not run] | Authored scripts: no `4A FF` in a talk section. `boss_script` has no 0x4A wait, so it is safe as built. |
| **Chase radius is not a circle** | The radius test compares dx+dz, unsquared and signed, with (range*6/10)^2 (`src/main/overworld.c:1454-1459`). dx+dz stays near 20000 at most, so any trackingRange of 237 or more passes everywhere; below that it is a half-plane. Stock barely uses it: one record of mode 16. | code, disc; [not run] | Do not give a new NPC follow modes 6, 7, 8, 9, 15, 16, 17 or 18 expecting a circle. |
| **Cup bracket sprites** | The sheet comes from type/64 and the cell from type-1 (`src/std/std_main.c:3363-3367`), so type 64 uses sheet 1 cell 63; type 128 has the same edge. The partner's own type enters this table (:3472). `STD_DIGIMON_SPRITE_CLUT` has 112 rows, so types 112 and up read past it. Machinedramon's mini sprite shows garbage in Vice [Vice]. | code; [not run] | Yes: roster partners in cups are untested, and Gigadramon (64) is wrong for sure. |
| **Slot machine blocks triples** | At assist 0 the third reel is pushed 1 or 2 steps off a would-be triple (`src/trn/trn_slots.c:333-343`). Disc: on that reel a symbol repeats at distance 3 or more, so a triple is impossible. Assist 1 also pushes off 7-7-7 (:361-362), so triple sevens need assist 2, one roll in ten. A loss still pays 5. TRN2 was not checked. | code, disc; [not run] | Lucky Reels and Perfect Training route around it. |
| **Evolution item accepted, nothing happens** | `partnerWillRefuseItem` refuses only same level, level+2 and level-1 (`src/main/partner_impl.c:1460-1467`), so Fresh accepts Champion and Ultimate items, and In-Training accepts Ultimate items. `handleEvoItems` then returns (`src/main/item.c:2205-2208`). Vicen says the item is lost [Vice], but that return comes before `removeTamerItem` at :2212, so check first. | code; [not run] | Low. The port treats only items 125-127. |
| **Omni Disk skips the battle limit** | Case 0x12 adds a flat 20 to Off, Def and Speed without the start-plus-30 % limit (`src/main/main.c:4372-4374`); 999 still holds through `BTL_buffStats`. A later capped disk computes limit minus stat, which is negative, so it lowers the stat (:4328-4352). | code; [not run] | Low. Battle item only. |
| **Valid scold uses an unset value** | With `ITEM_SCOLD_FLAG` 1 the happiness delta is never assigned, then added (`src/main/partner_impl.c:1126-1128,1147`). The leftover register acts as +3 [Vice]. | code; value [Vice] | Low. |
| **`REDUCE_MERIT` sets, not reduces** | The op stores minus the value and clamps to 0, so merit always ends at 0 (`src/main/script_interp.c:1173-1179`). No original script uses it [Horizon]. | code; [not run] | Avoid op 0x33 in authored scripts. |
| **Post-battle tiredness division** | `(diff / base.mp) * 10` is integer division (`src/main/partner_impl.c:2053`; the decomp's comment flags it), so the extra +10 tiredness comes only at exactly 0 MP. | code; [not run] | Low. |
| **Daily card roll allows duplicates** | The duplicate check compares `smth[i]`, not `smth[j]` (`src/main/script_common.c:1418-1422`). | code; [not run] | No. |
| **Save title leftovers** | `_strncpy` stops at NUL without padding or terminating, and the USA writer rewrites only the first bytes of the title before it (`src/main/main_menu.c:3609-3617,4962-4976`), so an old name's tail stays. Horizon also lists it, as patch 0050 [Horizon]. | code; [not run] | Cosmetic. `dw_newmaps.c` rewrites the save buffer but not this title. |
| **Backdrop row test** | `updateTileRow` tests `tiles[base+i]` but loads `tiles[i+base+start]` (`src/main/map.c:5220-5227`), so a -1 tile blanks the wrong slot. | code; [not run] | INTERNALS 3.2 already says to avoid -1 slots. |
| **Move pick can run off the table** | If every usable slot has weight 0 the loop ends at i=4 and reads past `moves[]` (`src/btl/battle_main.c:3857-3873`). | code; [not run] | Authored records: keep one usable slot weighted. |
| **Missing section is a NULL script** | Only section 0xFE is null-checked; talk sections, a 0x7B section and `JUMP_TO_FILE` targets are not (`src/main/script_interp.c:2253-2255,2711`). | code; [not run] | Authored maps: ship every section a tile or record names. |
| **Stock buffers spill** | Boot rounds `MAPHEAD.SCN` up to 0x6800 B into a 0x61A8 buffer, spilling 0x658 B into `SCRIPT_OFFSET_TABLE`, harmless because that is read next (`src/main/script_interp.c:2203-2204`) [inferred]. Disc: the TFS files of MAYO01_2, TWNA02 (494,208 B) and MIST02 (493,696 B) round to 242 sectors, about 2 KB into `TEXTURE_BUFFER`. One stock map has 186 sprite definitions against 180 slots [Horizon]. | code, disc; effects [inferred] | Keep the generator's 0x78800 and 0x61A8 caps: stock getting away with this is no licence. |

### 12.3 Claims only, not checked here

| Bug | Report | Matters to the port's mods |
|---|---|---|
| **Machinedramon and All Range Beam crashes** | A partner Machinedramon can crash when a battle pulls in all four Digimon in a room (Beetle Land, first room of Mt. Infinity), All Range Beam can crash three-Digimon battles, and evolving Machinedramon to Sukamon crashes [Vice]. Vicen blames memory limits only in issue 22; issue 3 gives no cause. Nothing local root-causes it. Disc: `MUGE.MMD` is 78,588 B against the largest model, `ETEM.MMD`, at 97,812 B, so raw size alone does not explain it [inferred]. | Yes. The port keeps that move (`DIGIMON_DATA[115]` lists 25), and the virus path can send any partner to Sukamon. Never tested. Vice removes the move and blocks the evolution. |
| **BlueMeramon and ClearAgumon never fight** | Their dialogue overruns the battle command [Vice]. | No mod depends on it. The decoder in `tools/scan_trigger_ids.py` could confirm it. |
| **Digitamamon restaurant** | A failed hunger check falls into the meal check, so you can eat when not hungry [Vice]. | No. |
| **Jijimon prosperity at 100** | The medal step is nested in the Tamer-level step, so Tamer level 10 exits before the medal [Vice]. | No. |
| **Mojyamon drift** | His dance moves him forward without returning [Vice]. | Cosmetic. |
| **Missing text** | A stray `00 00` inside script text ends it early, as in Myotismon's SkullGreymon line [Vice]. | Authored text must not contain it. |
| **Bank, recycling shop, schedule and Brains-tech text** | Scrolling the bank wipes the bag text, the recycling shop uses the wrong format, schedule rows starting with a space shift, and the learned-tech box clips [Vice]. | Cosmetic. |
| **Shop and flatten odds and ends** | Shogun Gekomon shop memory leak, merit cap compare, black box after shopping, and an enemy left untouchable when the partner revives during its flatten, the last only suspected [Vice]. | None known. |
| **GPU primitive after every cup** | A self-linked primitive after each cup, patch 0023 [Horizon]. | Tournament code is untested in the port. |
| **Status page happiness label** | The label reads the wrong texture columns, patch 0220 [Horizon]. | Cosmetic. |
| **Gabumon ambush crash** | A GPU packet overflow from the banner with a fourth NPC, plus an effect-context race, reproduced under a forced layout and not proven as the original report [Horizon]. | Limit NPC counts on new maps. |
| **Care edge cases** | Lifetime has no upper clamp and wraps at 32767; the sickness roll is uint8 and wraps, by Horizon's own inference; bedtime ignores `sleepyMinute` [Horizon]. | Check against the code with item 6 in 14.1. |
| **Item-drop softlock (issue 35)** | Unresolved, and PGXP was the only lead, so it is not a known game bug [Vice]. | No. |

### 12.4 Checked and not bugs

- **Stale pstat 0xFA after a lost fight.** Two studies raised it and it does not hold. Disc: MAPHEAD 0x5FD6-0x6033 exits only to sections 0x47, 0x48 and 0x49; scripts 165 and 178 jump those to script 212, which writes pstat 0xFA = 0. Scripted fights cannot be fled, because trigger 1 is set. No guard is needed.
- **A second `LOAD_DIGIMON` in a talk always hangs.** Refuted: stock runs two in one dialogue section (scripts 43 and 123). Horizon's pages disagree with each other and the cause of its hangs is unknown, so loading in setup stays the safe rule.
- **An on-screen flee point hangs the win.** Refuted: the point is read once to set a facing (`src/btl/battle_main.c:951-953`), and the enemy walks on by root motion with no collision check until it is off screen. A hang needs an anim 0x24 with no forward motion [inferred].
- **The end box reads until memory runs out and crashes.** Not what the code does; see 12.1.
- **Omni Disk has no cap.** 999 still holds; only the per-battle limit is skipped.

## 13. What other projects know

### 13.1 The projects

| Project | What it is | How it is built | Use to a static recompile |
|---|---|---|---|
| dw_decomp | C decompilation for six disc versions (`Makefile:7`), meant to match byte for byte. | CodeWarrior and splat-style yaml. | The primary reference. Never linked in: the port translates the player's own EXE. |
| dw1horizon | Fan mod with a new story, languages, species and screens, and the most complete public format docs found: script assembler, MAP/TFS tool, model, sound and font tools, RAM, VRAM and SPU maps. | The decomp at `83f99c5` plus a linear C patch series (352 patches at v0.0.6), compiled with GCC into a new EXE. The whole disc is rebuilt; a patcher replays it on the player's own discs, checking each file by SHA-1. Story source compiles to DWS and DG.SCN [Horizon]. | Formats, offline tools and ideas, not the engine. Its engine symbols, RAM addresses and overlay layouts belong to one patch series [Horizon]. |
| Vice (Dw1DataAndPatches) | One person's NTSC-U code notes, about 30 small asm patches for stock bugs, the Vice hack on top of them, and a seeded randomizer [Vice]. | Hand asm on the stock EXE. A Godot 4 C# installer applies PPF3 files and byte writes to a raw BIN copy, with no hash and no compare before writing [Vice]. | Facts and bug reports only, since no licence is shown. |
| DW1-Code | Readable, non-compilable pseudo-C plus `memoryMap.txt`. Its README calls it outdated and points to dw_decomp. | Not built. | Superseded for code. `memoryMap.txt` was not opened. |
| DW1-SydPatches | A C++ reimplementation injected piece by piece into the US game, aimed at bug fixes and modding. Its README says it is not ready for the public. | Extract from a US ROM, then rebuild to a patched bin and cue. | Needs a rebuild. Its fixes were not read. |
| Digimon World 1.5 | Capablemite's fan mod, Test Build 1.3.1: new Digimon lines, eggs and special attacks. | A BPS delta. 20 files change; its EXE grows to 0xB9000 B of text with entry 0x80135000, and about 11 MB of new sectors belong to no file. | Already runs as a toggle (INTERNALS 4.4). Not studied for game facts. |

### 13.2 Usable with no rebuilt code

The port translates the stock EXE ahead of time. It can change data, apply guarded equal-length byte patches, overlay disc sectors, and hook or replace functions from a plugin (INTERNALS 2). So anything that is data on stock code, or an offline tool, carries over.

| Item | Where | State |
|---|---|---|
| Format facts: MAP, TFS, entities, sprites, DG, MAPHEAD, text, MMD, VAB, SEQ | Horizon `docs/reference/`, `docs/mods/` | Agree with the decomp where other sections cite them. For stock opcodes cite `include/dw/script.h`, not Horizon: its IF kind 0x28, sub-values 6 and 7, and ops 0x80-0x9F are mod-only. |
| `MMM.DAT` as free disc space | Horizon `docs/reference/disc.md`, `tools/dwmap.py` (`mmm`, `file-entry`) | Disc: LBA 148668, 13,500 all-zero Form 1 sectors, named only in table row 817. A `disc_user` overlay should apply there by code reading [not run]. Moving a map takes four location patches, not two: skip the two ISO records and 21:9 shows black edges (INTERNALS 3.2). |
| DG script rows 223 and up | `src/main/script_interp.c:647-668,2204,2783-2815` | No bounds check, so a row can point into `MMM.DAT` [not run]. Sizes must be whole sectors, a row needs `table[N]` and `table[N+1]`, `table[222]` must not change, and guards must expect stale ASCII from 223 on. `tools/scan_trigger_ids.py` stops at row 222 and would not audit them. |
| More file names | Scan of the decomp and all `*_REL.BIN` | 25 rows have no `lookupFileTable` consumer; with MGEN17's two that is 27 rows, or 13 new map names [inferred]. Overlay rows are not free (`src/main/utils2.c:382`). |
| Relayout of existing rows | Horizon `tools/build-disc.py --relayout` | Rewriting table sectors and sizes, plus ISO records for `CdSearchFile` users, is data on stock code. Only rows past 819 need a rebuilt table. |
| Script assembler | Horizon `tools/dws/bytecode/` | `dis`, `asm`, `check`, `libres`, offline. Not standalone: it imports Horizon's tools package. 105 stock ops and 32 of its own. Byte-exact on all 222 scripts plus MAPHEAD [Horizon]. |
| Story language at its `original` target | Horizon `docs/mods/dws.md` | Emits only stock opcodes; ids above 255 are an error [Horizon]. |
| Map checkers | Horizon `tools/dwmap.py`, `tools/maps/reach.py` | `dump` and `roundtrip` give an independent parse of generated maps, lossless on all 242 stock maps [Horizon]. `reach.py` finds unreachable areas and trap tiles before any run [Horizon]. |
| Model and sound tools | Horizon `tools/mmd.py`, `tools/sound.py` | Offline. MMD byte round trip 178/178 and glTF 177/178 are its reports [Horizon]. Same-size sound chunks fit `disc_user` overlays [inferred]. |
| Literal gate | Horizon `tools/patcher/literal_gate.py` | Fails on any 16-byte match with the disc [Horizon]. Not a cheap add: `glade.map` shares structural runs with all 242 stock maps, and `LIGHTS` deliberately copies MAYO03's 72 bytes. It needs a low-entropy filter and a declared exception. |
| Bug mechanisms | Vicen's wiki and `.asm` patches | Learn the mechanism, write the fix. A guarded byte patch or `mod_function_replace_funcs`, never both on one function (INTERNALS 2.2). Overlay fixes must be disc patches (INTERNALS 1). |

### 13.3 Needs a rebuilt executable

| Feature | Why stock code cannot do it |
|---|---|
| Screens 255 and up (`SCRN.DAT`) | The row table has 255 entries and the screen variable is one byte (`src/main/map.c:458,2792`). |
| File rows past 819 (`NEW_FILES`) | The table address is fixed and other data follows it (`src/main/file.c:21`). `lookupFileTable` could be replaced from a plugin instead (INTERNALS 2.2). |
| Opcodes 0x80-0x9F | Stock marks the script paused and leaves, so the section just ends (`src/main/script_interp.c:604-619,634-642`). |
| Species 180 and up (`DIGI.DAT`) | `loadMMD` rejects types of 0xB4 and above (`src/main/model.c:3369`). |
| Indexed sound and new fonts | Stock fonts run 1 to 0x21 by fixed stride (`src/main/sound.c:755`). |
| TFS streaming, more than 30 tiles | 30 tiles with 3 CLUTs is what fits the load window; Horizon reaches 35 only with its streaming patches [Horizon]. |
| Extra glyphs (F040 and up), font b, a smaller GCC build | All are compiled changes to the EXE [Horizon]. |
| DW1-SydPatches as a whole | It replaces game code with its own build. |

### 13.4 Before copying their numbers

- Horizon's combat and care pages describe the mod unless marked: its 2.0/1.0/0.2 element chart, 150 % bits, raised caps and 408 hour lifetime are not stock. Its heap and BSS figures are for its GCC build [Horizon].
- Horizon's docs disagree with each other or the disc on: where `MMM.DAT` ends (last sector is 162167), MAPHEAD sections (281 on the disc, not 282), how overlays load (only the async path uses `CdSearchFile`), which way rotation 0 faces, and the SEQ buffer (`src/main/sound.c` uses 0x5800).
- Vicen's 8-digit offsets are raw BIN positions: sector = raw/2352, byte = raw%2352-24, and for the EXE vaddr = 0x80090000 + (sector-148338)*2048 + byte. Overlays need their own LBA and base.
- His 0x14D19A84-0x14D19E6C offsets describe code Vice rewrote: on a stock disc they fall inside `getChampionEvolutionTarget`. His type lists start at type 1, so 0x14D6BEB6 is `ORIGINAL_DIGIMON_TYPES`+2.

### 13.5 Ideas worth borrowing

- **A small stock-fixes package, written from scratch:** jukebox guard, `rotateEntity` equal case, move-to zero delta, `unlearnMove` with a 32-bit mask that keeps the slot clear, sleep clamp. `mod_function_replace_funcs` suits these. Mechanisms: https://github.com/Vicen04/Dw1DataAndPatches/wiki/Bugs,-glitches,-exploits-and-improvements.
- **Clear `HAS_USED_EVOITEM` outside the roster,** so stock items are covered. Vicen's store in EVL's last delay slot at 0x80063AF0 shows a VBlank callback is not the only place [Vice].
- **A generator assert on EFE lists,** finishers included, against raw `DIGIMON_DATA.moves` and `ORIGINAL_DIGIMON_TYPES` (`src/btl/battle_effect.c:4660-4678`). A species' row must be -1 or a type on every list of its moves, and the type must be 176 or lower, because `getOriginalType` returns -1 above 0xB0.
- **A test matrix from Vicen's reports:** Machinedramon evolving out and virus bar to Sukamon, four-Digimon rooms, All Range Beam with three Digimon, a new-species partner in a cup. Issues 22 and 3.
- **Put new maps in `MMM.DAT`,** with all 30 tiles and each map's own DG row. Prove it with `tools/verify_newmap.py --map glade` and the `--wide` fight before editing INTERNALS. Horizon `docs/reference/disc.md`.
- **Generate DG bytes from source,** with a real disassembler in `tools/`. The opcode table comes from `include/dw/script.h` (MIT); Horizon's assembler can run offline with its notice.
- **A reachability check and arrival rules in the generator:** 10-unit lattice, one component, arrival not on or beside its own exit, warp y = 0, wild Digimon beyond tracking range + 300 [Horizon]. Horizon `tools/maps/reach.py`, `docs/mods/new-maps.md`.
- **Build boss text from `CHAR_TO_GLYPH_TABLE`** (`src/main/font.c:1391-1401`) and refuse anything outside it, so apostrophes and hyphens work. `boss_script` today only asserts letters, digits, space and `.!?`, which is what keeps symbols from drawing triangles.
- **Data-only map features:** animated or blended sprites and a region word (Horizon `docs/mods/maps.md`, `docs/reference/liked-regions.md`), press-to-inspect tiles 80-109 (`src/main/tamer.c:707-712`), changing walls with op 0x7C (`src/main/script_interp.c:2158-2164`).
- **Measure difficulty in real fights:** stats forced at setup, nothing else forced, repeated runs, HP lost rather than animations seen. Horizon `HARD-DIFFICULTY.md`, `docs/investigations/battle-hits.md`.
- **A one-time identity for a boss:** a Fight / Not now prompt, a win flag, a chest with its own flag. Horizon `story/mods/what-the-island-forgot/arcs/infinity/infinity-engine.py`, `arcs/seam/kennel.py`. Flags must stay outside triggers 200-311 (arena pools) and 717-799 (roster raised flags).
- **Two balance rules:** a repeatable payout must not exceed what it costs to earn, and do not scale enemies to the partner. Horizon `docs/decisions.md`, `HARD-PROPOSAL.md`.
- **More bosses from extra records on stock map files,** as Vicen's `New installer/DataCheck/Bosses2.cs` reads them; his changelog also has new maps, and whether his bosses need new art is unknown [Vice]. Not free: MGEN14 and 15 hold collision where the records go, so the files must be relaid, and rows need flag 0x80. MGEN17 is out, since the glade sits on its sectors. His randomizer caps two-group areas at a combined model weight of 152000 [Vice].
- **Per-species tech bonus** in `RAISE_DATA` +0x0B and +0x0E, which no stock code reads (`include/dw/partner.h:6-22`). Vicen's `Hack data/Digimon World moves boost.asm`.
- **An evolution requirement viewer,** as Vice's changelog describes, built on the port's own chooser in `tools/verify_roster_evolution.py`.

## 14. What is still unknown

### 14.1 Open questions, most valuable first

| # | Question | Read |
|---|---|---|
| 1 | **What the game does as script data.** For every trigger 0-799 and pstat 0-255, what sets and tests it, from a decode and not a raw scan? What are the main chain, the zone graph, the recruit and prosperity rules, and the 57 routines? Only scripts 3, 118, 0xA5, 0xB2 and 0xB9 of 222 have been decoded. Raw scans overcount: 99 raw `1E 00 FA 01` against 45 scripted battles [Horizon]. This also settles the free trigger ranges. | Horizon `docs/reference/story-engine.md` lines 110-466, `story/base/*`, `docs/reference/world.md`; `CALL_ROUTINE` in `src/main/script_interp.c`; `src/main/script_common.c`; `include/dw/trigger.h`; DG.SCN, MAPHEAD.SCN; `tools/scan_trigger_ids.py` |
| 2 | **Where `research/` is.** See 14.2. | `docs/INTERNALS.md` section 10, `DEVELOPING.md` |
| 3 | **How a hit is delivered.** Every study read the damage formula, none read delivery. Hits come from effect bytecode testing a box, radius or line. For moves 47, 33, 35, 93 and common finishers: how many attack objects, on which frame, in what shape? Can geometry miss? When is `isHit` reset? How does Your Call pick? Do fighters stay off blocked cells? The one-roll-per-attack model stands or falls on this. | `src/btl/battle_effect.c:2157-2359`, `src/main/efe.c`, `efe_table.c`, `effect_common.c`, `src/btl/battle_main.c:1324-1516`, `src/btl/command_menu.c`, `command_shout.c`, EFEDAT.EFE |
| 4 | **What fails first on a new map:** heap, battle effect heap, VRAM or SPU? How many NPCs or wild species can row 239 hold with the boss loaded? This also bears on the second-`LOAD_DIGIMON` hangs and the All Range Beam reports. | Horizon `docs/reference/memory.md` lines 233-310 and 735-830, `tools/membudget.py`; `src/main/main.c:3534-3539`; `src/main/model.c:2829-2830,3299-3304,3356-3480,3580-3700` |
| 5 | **Does the `MMM.DAT` route work in the game?** One `verify_newmap --map glade` and fight `--wide` pass with the files moved and both ISO records patched. Not one of these routes has run, and the 25 free file rows are string-based only. | `psxrecomp/runtime/src/mod_runtime.cpp`, `disc_path.cpp`; `tools/gen_newmap.py` (`iso_record` and the `file table:` patches); `dw_ws_sliver.c:210-211`; `src/main/file.c` |
| 6 | **Which care numbers are stock?** The design study took them from Horizon. What are the stock rates for food, sleep, poop, discipline, happiness and sickness, and which of Horizon's values are its own changes? | `src/main/partner_impl.c` (`setFoodTimer`, `handleSleeping`, `sleepRegen`, `tickPoopingMechanic`), `src/main/partner.c`; Horizon `docs/reference/partner.md`, `content/tables/care-rules.json` |
| 7 | **Real fights with a player in them.** 7.18 has unassisted fights; none use items, commands or a charged finisher, which is how a person plays. What are real win rates then? | `tools/verify_newmap.py` `measure`; `tools/fight_sim.py`; Horizon `docs/data/hard-difficulty/live-fights.csv`, `tools/battle_audit.py`, `HARD-PROPOSAL.md` |
| 8 | **Text boxes, shops, menus, items.** How does the text box state machine run, where does `DELAY_FRAMES` count down, what are the shop rules, how do item effects and ground pickups work? NPCs and items on new maps need this. | `src/main/script_textbox.c`, `src/shop/script_menu.c`, `src/main/game_menu.c`, `inventory.c`, `main_menu.c`, `item.c`, `world_object.c`; Horizon `docs/reference/items-and-menus.md` |
| 9 | **Which of Vicen's claims are real on this disc,** and which crash the port? All of 12.3, the tournament exploit, and the Machinedramon crashes above all, since the build keeps both triggers. | Decoded DG.SCN, `src/main/script_common.c`, `src/main/tournament.c`; Vicen's wiki bug pages, issues 3 and 22, `Digimon Code/*.asm` |
| 10 | **Cutscene overlays on an authored map.** These scenes run on whatever map the player is on, then reload only sounds and the TFS. Is row 239 intact afterwards: sprite texels at x 384+, collision, palette, the 21:9 sliver? What do the overlays occupy in RAM? Only entry functions were opened. | `src/main/partner.c:776-779,808-809`; `src/evl/evl.c`; `src/dooa/dooa.c`, `src/murd/murd.c`, `src/doo2/doo2.c`, `src/eab/eab.c`, `src/endi/endi.c`, `src/dget/dget.c`, `src/mov/mov.c`; `config/us/*.yaml` |
| 11 | **Tournaments.** The calendar, opponent pools, cup unlocks and rewards? Does the arena AI differ from the field AI? | `src/std/std_main.c`, `std_setup.c`, `std_hud.c`, `std_effect.c`, `src/main/tournament.c`, `src/vs/*.c`, `src/dget/dget.c:383-387`, `src/main/utils2.c:605-618` |
| 12 | **Training.** The gain formula, grades, gym upgrades and slots. INTERNALS says the maths is in `research/`, so it may need redoing. | `src/trn/trn_*.c`, `src/trn2/*.c`, `src/main/script_interp.c:1684-1723`, `tools/verify_roster_training.py` |
| 13 | **What DW 1.5, SydMontague and Vicen's spreadsheets know** that these studies do not. 1.5 is local and cheap and was in no study's scope. Record each licence when opened. | `dw15-release/ANALYSIS.md`, `PLAN.md`, `disc_diff.txt` (beside this repo in the workspace); DW1-Code `memoryMap.txt`; DW1-SydPatches source; Horizon `docs/decisions.md`, `docs/mods/compatibility.md` |
| 14 | **Horizon's model and sound claims.** Do the 178/178 and 222/222 round trips reproduce? What explains 178 files against 180 types, and the libsnd overrun and loop meanings? Fonts 29-32 have no proven caller. | `src/main/model.c`, `anim.c`, `sound.c`, `sound_async.c`, `font.c`; Horizon `tools/mmd.py`, `tools/sound.py`, `docs/mods/sound.md`, `docs/mods/models.md` |
| 15 | **Fishing and curling rules,** and what makes a map fishable, which decides whether the glade's pond could ever be fished. Lowest value here: these systems are self-contained. | `src/fish/fish.c`, `fish_model.c`, `src/main/fish.c`, `src/kar/kar.c`, `src/main/kar.c`, `src/main/map.c:4092`; Horizon `docs/reference/minigames.md` |

### 14.2 The `research/` archive

INTERNALS section 10, its `research/...` citations and `DEVELOPING.md` point at a `research/` tree that sits outside the repository and is not packaged. By INTERNALS' own account it holds the reference census, the free-RAM analysis, the parallel-table landmines, the widescreen deep tier and backdrop path, the training grading and gain maths, the cheat surveys and the pre-release audit.

A search of the owner's home directory to depth 5, skipping AppData, for `roster-xref-census*` and `widescreen-w2-dossier*` found nothing. So those citations may be dead for the owner too. Treat any number that rests on them as unproven until it is checked against the decomp or the EXE. If the tree turns up, move what matters somewhere durable; if not, remove the citations.

### 14.3 Work that exists only in the session scratch

The session's useful tools moved into `tools/`:

| Tool | What it does |
|---|---|
| `dis_script.py` | Disassembles a DG script or a MAPHEAD section from your disc. All 1834 stock sections decode with no errors |
| `dw_tables.py` | Writes the map records, species and moves as CSV under `build/tables/`. They are derived game data, so never commit them |
| `fight_sim.py` | The fight estimate in 7.17 |
| `verify_newmap.py measure` | A real fight with nothing poked (7.18) |

Left behind on purpose: a save decoder from one debugging session (9.1 has the full payload, and `verify_newmap.py` reads the fields it needs), and small disc probes for `MMM.DAT`, which take minutes to rewrite from 2.3.

### 14.4 Never exercised

The `fight` mode of `tools/verify_newmap.py` does not fight the glade boss at real numbers: it rewrites boss HP and MP and partner HP on every poll and forces boss HP to 1 to win. Its `measure` mode sets the partner's stats and then pokes nothing; 7.18 records what it found. Last-life loss, lifetime-0 loss, natural death and evolution have not run on row 239. `tools/verify_newmap.py` has no audio check. Whether a port option changes the 20-tick battle clock was not checked (`src/btl/battle_main.c:2606`).
