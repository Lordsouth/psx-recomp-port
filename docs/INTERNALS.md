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

Seven plugins live here:

| File | What it does |
|---|---|
| `dw_roster.c` | the Expansion-1 arena and the grown tables (§4.1) |
| `dw_roster_flags.c` | re-banks the new species' raised flags off the game's story flags (§4.1) |
| `dw_evolution.c` | proof of the replacement primitive; ships disabled |
| `dw_ws_backdrop.c` | fade plate, parked HUD, the clock and field gauges as single widgets, the battle bars' slide table (§5) |
| `dw_ws_camera.c` | both camera clamps, the tile-ladder shift, the load-time nudge (§5) |
| `dw_ws_sliver.c` | the fifth backdrop tile column, as a VRAM sliver (§5) |
| `dw_dw15.c` | moves the memory cards to `saves/dw15/` while Digimon World 1.5 is on (§4.4) |

`codegen_setup.c` is glue for the launcher build, not a plugin.

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
+0x00 condition (u32: 0x01 sleepy, 0x02 tired, 0x04 hungry, 0x08 poopy,
                 0x10 unhappy, 0x20 injured, 0x40 sick; dw/params.h)
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
family (`ORIGINAL_TYPE[114] = 115`), evolution branches (below), and the
raised-flag guard at `0x800FF7FC` widened from 63 types to 180.

#### 4.1.2 Only a Champion can be an evolution source

The evolution dispatch at `0x800A9150` is a level chain — Fresh (age ≥ 6),
In-Training (≥ 24), Rookie (≥ 72), Champion (≥ 144) — and it **ends there**.
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

**`HAS_USED_EVOITEM` (`0x80134E52`) had to be fixed too.** The item sets it, and
`EVL_scaleBaseStats` returns early while it is set (`evl.c:734`), skipping the
base-stat rescale. It is cleared only when a partner is initialised
(`partner_impl.c:124`) — so after one item evolution, every *later* natural
evolution silently skips its rescale for the rest of the playthrough. Vanilla
never exposed this because the items were unreachable. `dw_roster.c` clears it
from the per-VBlank callback once the sequence is fully over (partner idle,
control returned, game time running) — not mid-sequence, which would rescale
the item evolution itself, the very thing the flag exists to suppress.

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

`calculateRequirementScore` (`evolution.c:303`) offers four points and requires
three: care mistakes, a **hard ±5 weight window**, all six stats at once, and
one bonus from digimon/discipline/happiness/battles/techs. Weight is therefore
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
against the 1.5 disc. Four features cannot run with it:

| Feature | Why |
|---|---|
| `dw.vanilla.roster/roster` | same free Digimon slots; 223 of its patches also hit changed bytes |
| `dw.vanilla.gameplay/evolution-unbound` | 1.5 rewrote `EVO_REQ_DATA`; Evolution Unbound (1.5) replaces it |
| `dw.vanilla.trainer/guaranteed-drops` | 1.5 changed 138 of the `DIGIMON_DATA` drop bytes it guards; Guaranteed Item Drops (1.5) replaces it |
| `dw.vanilla.evolution/native-evolution` | 1.5 changed `getFreshEvolutionTarget`, the replaced function |

Everything else passed, widescreen included: none of its seven hooked
functions, three matched call sites, two bracketed functions or the BTL bar
slide table changed. Save Anywhere, Text Speed and Run Speed touch no byte
1.5 changed. Of the cheats, only "Shrink your own collision box" wrote a byte
1.5 changed: under 1.5, `0x8012CECC` is another table. It now writes each
game's own `DIGIMON_DATA[0].radius` behind a gate on which executable is
running (§6.1).

The `dw15` feature's `conflicts` list names all four (package format 12, see
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

**1.5-only features.** Two features in `dw.1_5` are the 1.5 editions of
features 1.5 turns off. Each has a `requires_feature dw15` constraint:
ticking one ticks 1.5, unticking 1.5 unticks it, and a `state.toml` with one
but not 1.5 refuses to launch. `tools/gen_dw15_extras.py` generates both from
the 1.5 disc, below a marker in the manifest.

| Feature | Writes |
|---|---|
| `dw15-guaranteed-drops` | 203 `main_exe` bytes: `dropChance` (`0x801F4322 + type × 0x34`) to 100 for every named record below 100. 1.5's drop roll (`0x800ED298`) is the stock code with the new base. The 159 nameless slots are all zero and no 1.5 map places one. Records with chance 0 are raised too, as in the vanilla feature, so they drop their item byte, mostly item 0 |
| `dw15-evolution-unbound` | 616 `disc_user` writes: each scaled wall (`max(1, v × pct / 100)`) of the 67 walled targets the three choosers can pick, once per copy of the block. Row 254 (all 32767, unreachable) is skipped; `-1`, flags and weight are left alone. Same `walls` option as the vanilla feature |

Checked in game: with the drops feature, 21 of 21 defeated enemies dropped
their item (control: 3 of 18). With Evolution Unbound (1.5) on relaxed,
Flamedramon's walls of 100 become 75: stats of 75 evolve, 74 do not, both from
the boot copy and after a reload from disc.

**Saves.** `save_compatibility = "isolated"` is declarative only; the runtime
does not act on it. `dw_dw15.c`'s activation callback calls
`psx_mod_set_memcard_subdir("dw15")`, so the cards become
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
cull), `dw_ws_sliver.c` (the fifth backdrop column, uploaded as a stacked
VRAM sliver whenever the game's four-column ring falls short, from a host
copy of the tile file read off the disc) and `dw_ws_world.c` (keeps a field
frame wide through a synchronous tile reload), plus `dw_ws_weather.c` (snow
and mist copied into the reveals) -- and one framework primitive
per need (`mod_function_exit_funcs`, `psx_mod_ws_native_wide_offset`,
`psx_mod_vram_write`, `psx_mod_set_world_scene_predicate`). Section 5.0c has
the 0.4.x pass and its numbers, 5.0d the plates and weather, 5.0e the
codegen culls, 5.0f why there is no per-map camera table. The
wider field of view is rendered into a genuinely wider framebuffer (320 -> 426
at 16:9, `EXTRA = 2 * round(W*(3n-4d)/(8d))`) and presented 1:1. Nothing is
stretched, so the entire "screen-space 2D renders stretched" problem class —
which is what W2 below exists to fight — does not arise.

**A player cannot select squash.** It is what shipped through 0.3.1, but the
mode is fixed at codegen (`game.toml [widescreen] native_wide`), and the mod
offers only an aspect. Sections 5.1 and 5.2 describe squash and are kept
because the code is still there and still gated, not because anyone can turn
it on.

**Only 16:9 and 16:10 are offered.** The reveal per side is
`(w*(3n-4d) + 4d) / (8d)`, so at w = 320 that is 32 px, 53 px, and 120 px for
21:9. The tile ring covers 512 px and needs the origin's leftslack in
`[m, 192-m]`, an interval that is EMPTY for `m > 96`; the 106 px sliver lifts
the practical bound to about 78. 21:9 clears neither, and it is not a tuning
constant away — it is the ring's size. The game ships its own copy of the
framework's widescreen manifest, under `mods/preloaded/packages/`, with that choice
removed. If the builtin's version is ever bumped past 1.0.0 the override stops
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

### 5.0f Painted-extent camera table: measured, no rows (0.4.x)

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
else. Highlights: freeze the game clock (one byte stops hunger, tiredness,
ageing and lifespan together), perfect condition, always carry the Training
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
| Always have Finish available | `8013D65A 0AF1` | goal and progress both `0001` | The gate is an EQUALITY (`battle_main.c:650`) against `finisherGoal = 3000 - Speed` (`battle_setup.c:100`). 2801 matches only at Speed 199; at any other Speed the code made Finish permanently **un**available |
| Partner takes no damage | `8013D666 03E8` | `8013D66E 0000` | `+0x26` is `invulnerableTimer`, the post-hit recovery countdown — pinning it meant the first hit still landed and the fighter never recovered. Damage accumulates in `hpDamageBuffer` (+0x2E) and effective HP is `currentHP - hpDamageBuffer` |
| Best Happiness / Best Discipline | `00AA` (170) | `0064` (100) | `tickConditionBoundaries` clamps both to 100 every frame, and three evolution branches test `== 100` exactly (`evolution.c:260-267`), so 170 silently blocked them |
| Have all cards | `00FF` ×33 | `0099` ×33 | Cards are packed nibbles, two per byte (`script_ops.c:416`), and the game never counts past 9 (`:374`). `FF` put 15 in both halves |
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
| `inventory.c:766-773` `moveMenuCursor`: 0x1000 moves a menu cursor **up**, 0x4000 moves it **down** | those two are the D-pad, not face buttons |
| `map.c:1272`: newly-pressed 0x40 extends the interaction box along the way the player faces | 0x40 is **Cross** |
| `script_common.c:404+`: 0x40 confirm, 0x10 cancel, 0x1000/0x4000 up/down, 0x800 Start | the PsyQ PADL/PADR layout throughout |

So: Triangle 0x10, Circle **0x20**, Cross 0x40, Square **0x80**; Up 0x1000,
Right 0x2000, Down 0x4000, Left 0x8000. A `D0` gate on the raw word inverts
that, giving `FFDF` for Circle and `FF7F` for Square — the values that were
already there.

What made the mistake possible is that the wrong reading is *self-consistent*:
under the hardware mask, `map.c:1998-2001`'s 0x1000/0x2000/0x4000/0x8000 reads
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
for Circle. Derive it from `inventory.c:766-773` and `map.c:1272`, never from
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
| `gen_dw15_extras.py` | the two 1.5-only features, below a marker in the `dw.1_5` manifest (needs the 1.5 image; `--check`) |
| `gen_cheats_mod.py` | `dw.vanilla.cheats` and `dw_cheats_data.inc` from `cheats.txt` (`--check`) |
| `gen_vice_mod.py` | a local-only package that must never be redistributed |

### 7.1b Verification tools

These drive the game and read guest memory, so a claim in this document can be
re-checked rather than believed. Each takes `--port` and runs against a
sandbox memory card seeded from `saves/`; none of them touches your own saves.

| Tool | Answers |
|---|---|
| `ws_sweep.py` | does the widescreen backdrop cover the frame **while walking**? Stops every 12 frames in four directions. Aspect-agnostic |
| `scan_painted_extent.py` | which maps' tile art goes flat black at an edge, and what clamp would hide it? Offline, reads the disc only; a proposal, not a verdict (§5.0f) |
| `verify_roster_evolution.py` | does the game's own chooser pick a roster species from requirements, and does its model actually stream? |
| `verify_roster_triggers.py` | do the roster's raised flags land clear of the game's story flags? Reads the bits either side of an evolution |
| `verify_roster_training.py` | is the relocated training table what a gym actually reads? |
| `verify_model.py` | are a model file's bytes resident in guest RAM? |
| `scan_trigger_ids.py` | which trigger ids do the disc scripts use? Run before widening any guard that writes one |
| `extract_disc_file.py` | pull a file off the disc image by its ISO path, so a probe comes from the same disc as the build |

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
`manifest.toml` files may ship, plus the Digimon World 1.5 BPS at its exact
path and SHA-256. Any other file there fails the gate.

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
user's PATH, the fail-closed packaging guard, and the
`[[widescreen.cull.relative_x_bound]]` cull kind (§5.0e).

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
* **`save_compatibility` is declared but not enforced** by the framework, so a
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
  player never owned -- which `evolution.c:401` uses to score evolution
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
  reached live. Fishing's own camera clamp (B5) was not measured. The
  partner-reappearance cull (§5.0e) is verified in the generated code only,
  and particle flashes still cull at the 4:3 edge.
* **Widescreen, Birdra Transport (207)** shows 13 columns of black art past
  its cage on the right (12-14 px at 16:10, inside the 33-35 px bar at
  16:9). A repaint failed (§5.0b) and no camera clamp can hide it (§5.0f).
* **Four inherited cheats remain suspect** (§6.3).
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
the addresses re-verified against the executable before use.
