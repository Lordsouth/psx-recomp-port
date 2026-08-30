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

The project ships **no game content**. Every enhancement is either a guarded
byte patch expressed in a text manifest, or native C in this repository that
runs against the player's own disc.

**Two golden rules, learned the hard way:**

1. **Never ship a patch or code whose target has not been identified in the
   decompilation.** Guessing an address produces a cheat that silently does
   nothing, or corrupts a save. Several inherited codes were wrong exactly this
   way (§6.3).
2. **Overlay RAM is shared.** The game loads sixteen overlays into eight reused
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
* an **Expansion-1 arena**: up to 1 MB of memory the real console never had,
  wired into every generated load/store path. The first allocation is
  deterministically `0x9F000000`, which is what lets a manifest hard-code
  addresses into it.

**A plugin's activation callback only runs if a manifest selects it** with a
`[[plugin]]` block. Function-entry hooks fire regardless — that asymmetry cost
an evening.

Two plugins live here: `dw_ws_backdrop.c` (§5.1) and `dw_roster.c` (§4.1).

---

## 3. Map of the game

Addresses verified against `dw_decomp` symbols and recomputed from the struct
definitions. The EXE loads at `0x80090800`; `file_offset = vaddr - 0x80090800 + 0x800`.

### Core tables

| Symbol | Address | Shape |
|---|---|---|
| `DIGIMON_DATA` | `0x8012CEB4` | 180 × 0x34 — name, bones, radius/height, attribute, level, drop item/chance, 16 moves |
| `EVO_REQ_DATA` | `0x8012ABEC` | 63 × 0x1C — evolution requirements, **indexed by target type** |
| `RAISE_DATA` | `0x801225BC` | 66 × 0x1C — meal times, energy, poop, favourite food, camera |
| `EVO_GAINS_DATA` | `0x8012B2D0` | 66 × 0x0E — stat gains on evolving |
| `EVO_PATHS_DATA` | `0x8012B66C` | 62 × 0x0B — `int8 from[5], to[6]`, `-1` terminated |
| `DIGIMON_SKELETONS` | `0x8011CE60` | 180 pointers |
| `PTR_DIGIMON_FILE_NAMES` | `0x8011D19C` | 180 pointers into the model-code string pool `0x80133B44` |
| `ORIGINAL_TYPE` | `0x8012A84C` | 180 × int16 — battle-aura family |
| `ITEM_PARA` | `0x801269DC` | 128 × 0x20 |
| `MOVE_LEARN_CHANCES` | `0x80125FA4` | per **move**, not per type |

### Live state

| Symbol | Address | Notes |
|---|---|---|
| `PARTNER_PARA` | `0x80138460` | struct size 0x84 (confirmed: the next symbol sits exactly 0x84 later) |
| `PARTNER_ENTITY` | `0x801557A8` | `.type` is an int32 at +0x00 |
| `SCRIPT_STATE` | `0x801BDED8` | `smth[212]`, `cards[33]`, `triggers[100]`, `pstats[256]` |
| `COMBAT_DATA` | `0x8013D640` | fighter stride 0x168; invuln timer +0x26, cooldown +0x28 |
| `NPC_ENTITIES` | `0x80155828` | 8 × 0x68; `bits` at +0x60 |
| `IS_GAMETIME_RUNNING` | `0x80134E20` | one byte; zero freezes hunger, tiredness, ageing and lifespan together |

Selected `PARTNER_PARA` offsets, each recomputed by walking the struct:

```
+0x00 condition (u32: 0x04 sleepy, 0x08 poop, 0x20 injured, 0x40 sick)
+0x22 tiredness      +0x3C energyLevel     +0x48 remainingLifetime
+0x4C trainBoostFlag +0x4E trainBoostValue +0x50 trainBoostTimer
+0x5E fishCaught     +0x72 upgradeBrainCounter
```

`SCRIPT_STATE` sub-arrays: item-shop stock `0x801BDEDE` (78 bytes), cards
`0x801BDFAC`, triggers `0x801BDFCD` (bit N is byte `base + N/8`, mask
`1 << (N%8)`), pstats `0x801BE031`. Recruitment uses trigger `0xC8 + digimonId`
— and two of those flags *are* the gym upgrades (Kabuterimon → HP/Def/Speed,
Kuwagamon → Off/MP/Brain).

---

## 4. Mod packages

### 4.1 `dw.vanilla.roster` — Machinedramon as a raisable partner

**1 feature, 189 patches, 1 plugin.** The headline feature: the game contains
exactly one free type id — slot **114**, a developer stub named `tAKA` — and
this makes it Machinedramon, the complete-but-unobtainable Ultimate.

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

Disc offsets follow `lba*2048 + (vaddr - 0x80088800)`; the runtime maps that
onto a 2352-byte raw image as `(off/2048)*2352 + 24 + off%2048`. A patch may
not straddle a 2048-byte sector boundary — the runtime applies writes inside a
single sector buffer — and the generator asserts it.

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
family (`ORIGINAL_TYPE[114] = 115`), evolution branches from **Megadramon**
and **MetalGreymon**, and the raised-flag guard at `0x800FF7FC` widened from
63 types to 180.

**The training tables** (`TRAINING_ANIM_IDS` `0x8008F050` in `TRN_REL.BIN`,
`TRN2_D_8008DA20` `0x8008DA20` in `TRN2_REL.BIN`) are `s8[68][2]` indexed by
raw type, so any type past 68 reads off the end. Without a fix, offense
training picks an invalid animation and **MP training soft-locks** (its
threshold reads zero).

0.2.0 fixed this in place: index 114 lands 92 bytes past the table, inside the
string `"Give it all you "`, and two disc patches rewrote those bytes. Correct
for one slot, unworkable for 51 — and it carried a bug. The patch rewrote only
byte `[0]` of `"e "`, so byte `[1]` silently inherited the string's space
(`0x20`). That byte is the frame the training impact lands on: `trn_off.c:145`
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
boost trio — flag, value, timer — that boost foods set; gains scale by
`value / 10` while the **flag** is set (the timer is not consulted).
`tickPartnerNormal`'s expiry block at `0x800A9034` clears flag and timer when
the timer runs out; four patched instructions make it *write* the boost
instead. The base register is established four instructions earlier and is not
clobbered — verified by disassembly. A real boost item still wins while its own
timer runs.

**Lucky Reels.** Before each spin the machine rolls an assist level from a
ten-byte table (`00 00 00 00 00 01 01 01 01 02`): half the time no help, and
only one roll in ten makes it actively scan for a seven. Filling the table with
the highest level makes it hunt every spin, so the reels genuinely land on
jackpots and the player still stops them. A fair-play alternative to Perfect
Training. Two `disc_user` patches.

**Guaranteed Item Drops.** `dropChance` (+0x22 in each `DIGIMON_DATA` record)
raised to 100 for every species. 157 needed patching; slot 114 is deliberately
skipped because the roster package owns that record (§7.2).

Regenerate: `py -3.12 tools/gen_trainer.py`.

### 4.3 `dw.vanilla.qol` and `dw.vanilla.gameplay`

**QoL** — partner lifespan (four `addiu $v0,$zero,360` initialisers; the
immediate is sign-extended, so 9999 is the ceiling) and world-clock speed
(`tickGameClock`'s speed read).

**Gameplay** — Tech Mastery (the `random(100)` immediate in the move-learning
roll) and Evolution Unbound (scales the six stat requirements across all 63
`EVO_REQ_DATA` entries; `-1` means "not required" and is preserved, and weight
is deliberately untouched because it is an exact target, not a floor).

---

## 5. Widescreen

Squash mode is what ships: the GTE compresses projected X and the presenter
stretches the frame back out. It is **off by default and labelled experimental**
because screen-space 2D still renders stretched.

### 5.1 W1 — backdrop alignment (shipped in 0.1.7)

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

`cheats.txt` holds **60** GameShark-style codes applied per frame by the
runtime's cheat engine. Change `[ ]` to `[x]` and restart.

### 6.1 Engine

Supported: 8-bit (`30`) and 16-bit (`80`) writes, `D0`–`D3`/`E0`–`E3`
conditionals gating the next line, and the `50` repeat block —
`5000CCSS 0000` repeats the **next** line CC times stepping SS bytes, with the
operand as the value step. Writes are restricted to main RAM.

### 6.2 What is new here

The published corpus for this game (36 CMGSCCC codes and everything derived
from it) contains **no training codes at all** — no perfect training, no
multiplier, no gym or boost codes. Everything in the training and city sections
was derived from the decompilation and, as far as we can tell, exists nowhere
else. Highlights: freeze the game clock (one byte stops hunger, tiredness,
ageing and lifespan together), perfect condition, always carry the Training
Manual (+10% to every training result), everyone moves to File City (which also
grants both gym upgrades), item shop stocks 99 of everything.

### 6.3 Inherited codes that were wrong

Verifying every address against the decomp found four real defects in codes
this project had been shipping:

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

A further four codes are flagged as suspect and still under review; see
`research/rel_cheats.txt`.

### 6.4 Verifying a code before shipping it

Resolve the address to its `dw_decomp` symbol and offset, cross-check the
containing struct's size against where the *next* symbol starts, and check the
value against the game's own clamp. The pattern is in
`scratchpad/verify_cheat_addrs.py`; all 24 base addresses in the current library
were verified this way.

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
| `gen_vice_mod.py` | a local-only package that must never be redistributed |

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
exactly, and proves every guard up front.

```
py -3.12 tools/verify_disc_patches.py
```

### 7.4 Packaging safety

The setup packager purges any staged mod package whose manifest marks itself
non-redistributable, then **asserts** afterwards that no marker and no
never-ship package survived — failing the build rather than silently
redistributing another work's bytes.

---

## 8. Framework fork

`gitlab.com/ethan4love/psxrecomp`, branch `fix/bss-memfill-overlap`. Upstream
cannot build this game at all — it rejects the executable because the header's
BSS clear overlaps the sector-rounded code tail. On top of that parser fix the
branch carries: SDL joystick-hint ordering (without which controllers die when
the launcher opens), a Windows `os.utime` crash that killed every rebuild, a
`Path.write_text` call that broke setup on older Python, a
dllexport-on-redeclaration fix without which no overlay compiles under clang, a
frame-pacing guard for monitors where vsync does not actually throttle, the
GameShark cheat engine, the widescreen plugin, the squash-gate export W1 needs,
the dormant W2 machinery, a hash-pinned build toolchain that no longer edits the
user's PATH, and the fail-closed packaging guard.

---

## 9. Known issues and open work

* **W2 UI counter-squash does not work** (§5.2). Dormant.
* **Machinedramon's model has never been confirmed on screen.** The loader
  remap is implemented and the data is verified in RAM, but nobody has watched
  the body stream in. Treat any claim otherwise as unverified.
* **`save_compatibility` is declared but not enforced** by the framework, so a
  save made with the roster enabled and loaded with it disabled reads a stale
  table set. Documented in `cheats.txt`; a framework-level fix is the real
  answer.
* **The overlay table retarget has not been seen working in a gym.** The
  expected-byte guards are proven against the real disc image, every rewritten
  instruction pair decodes to the intended arena address, and the arena content
  is verified byte-exact — but nothing has yet driven the game into a training
  gym with a relocated `TRAINING_ANIM_IDS`. Proven on paper and at the guard,
  not on screen.
* **Four inherited cheats remain suspect** (§6.3).
* **Prosperity Points ignore new recruits** — the recruit loop's bound was
  deliberately not widened, since widening it sweeps trigger ids other events
  use.

### The next flagship

**Fifty-one finished, unreachable Digimon are already on the disc.** Types
63–113 — Panjyamon, Gigadramon, MetalEtemon, Myotismon, Guardromon, Tentomon,
BlueMeramon, Meteormon and more — have complete records, their own models,
textures, skeletons, voices and battle auras. They are enemy-only purely
because the partner-line tables stopped short.

**Every table wall is now down.** All eight type-indexed tables that capped the
partner line are relocated and grown to 180 entries (§4.1), verified
byte-identical to the stock images over their stock ranges. What remains is
**authoring**, not engineering: per species, an `EVO_REQ_DATA` row (14 fields),
a `RAISE_DATA` row (28 bytes), `EVO_GAINS_DATA` (7 fields), a training-animation
pair in each of the two overlay tables, and evolution branches grafted onto
existing Champions. A donor-copy pass keyed on level, attribute and family gets
a working first cut; balance is playtesting.

Two things are not solved by the relocation:

* **Evo-chart icons.** The chart is a fixed 61-slot layout with baked screen
  positions, so there is no free position for a new species and copying a donor
  entry draws the newcomer on top of that donor. Slot 114's entry is
  deliberately left zero. What that actually draws has not been looked at.
* **Prosperity Points** still ignore new recruits (above).

Full analysis in `research/rel_flagship.txt`. Tier-3 (type ids beyond 179) was
costed and **rejected**: 304 relocation sites, ALLTIM exactly full, and no
models for the new slots.

---

## 10. Research archive

`research/` (outside this repository, not packaged) holds the raw investigation
output behind everything above: the reference census of every table reference
site, the free-RAM analysis, the parallel-table landmines, the widescreen deep
tier and backdrop path, the training grading and gain math, the cheat surveys,
and the pre-release audit. Numbers in this document come from those files, with
the addresses re-verified against the executable before use.
