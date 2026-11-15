/* Expanded Roster, phase 1: grow the partner-line tables so type 114 --
 * the game's one free slot, filled with Machinedramon -- is a legitimate,
 * raisable, evolvable partner.
 *
 * WHY A PLUGIN. EVO_REQ_DATA (63 entries), RAISE_DATA (66) and
 * EVO_GAINS_DATA (66) are fixed arrays indexed BY TYPE, wedged tight against
 * their neighbours in the EXE image -- they cannot grow in place, and a
 * declarative manifest cannot write outside the loaded image (the BSS
 * memfill would destroy it anyway). So:
 *
 *   1. This plugin reserves a 32 KB arena in the framework's Expansion-1
 *      enhancement region at activation. The first allocation is
 *      deterministically 0x9F000000, which is why the generated manifest
 *      (tools/gen_roster_mod.py) can hard-code retargeted addresses.
 *   2. The manifest's guarded main_exe patches rewrite every lui/lo16 pair
 *      that materializes a grown table's address (site list measured by the
 *      reference census; the sites are asserted in the generator) to the arena copy.
 *   3. At a function-entry hook on initializeHeap (0x800EEBDC -- runs once,
 *      after the BSS memfill, before any table consumer), this plugin copies
 *      the stock tables out of guest RAM into the arena and appends the
 *      type-114 entries.
 *
 * GATING. The hook only fills the arena if the manifest's retarget patches
 * actually applied -- detected by reading one patched lui out of guest RAM.
 * Patches applied <=> feature enabled <=> arena is live. With the feature
 * off, the game never reads the arena and behavior is stock.
 *
 * SAVESTATES. The arena is serialized with mod memory (BS_SEC_MODMEM). Its
 * tables are static per process, but its tail (0x5100+) is the evolution
 * shard scratch, live state during an evolution: a save or rewind restores it
 * together with the shard pointer in main RAM. Expansion-1 reads are wired
 * into every generated load path.
 */
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "mod_plugins.h"

/* ---- arena layout ------------------------------------------------------
 * The offsets and the arena size come from the generated include, not from
 * literals here: the manifest's retargeted addresses are computed from the
 * same constants, so duplicating them is how the game ends up reading the
 * wrong table after an edit to one file only. */
#define ROSTER_ARENA_BASE 0x9F000000u
#define ROSTER_N_TYPES 180u

#define OFF_EVO_REQ ROSTER_OFF_EVO_REQ        /* 180 * 28 = 0x13B0 */
#define OFF_RAISE ROSTER_OFF_RAISE            /* 180 * 28 = 0x13B0 */
#define OFF_EVO_GAINS ROSTER_OFF_EVO_GAINS    /* 180 * 14 = 0x9D8  */
#define OFF_ITEM_TAKE ROSTER_OFF_ITEM_TAKE    /* 180 * 1          */
#define OFF_EVO_PATHS ROSTER_OFF_EVO_PATHS    /* 180 * 11 = 0x7BC  */
#define OFF_EVOCHART ROSTER_OFF_EVOCHART      /* 180 * 8  = 0x5A0  */
#define OFF_TRAIN_ANIM ROSTER_OFF_TRAIN_ANIM  /* 180 * 2  = 0x168  */
#define OFF_TRN2_ANIM ROSTER_OFF_TRN2_ANIM    /* 180 * 2  = 0x168  */

/* ---- stock table locations in guest RAM -------------------------------- */
#define SRC_EVO_REQ 0x8012ABECu
#define N_EVO_REQ 63u
#define STRIDE_EVO_REQ 28u
#define SRC_RAISE 0x801225BCu
#define N_RAISE 66u
#define STRIDE_RAISE 28u
#define SRC_EVO_GAINS 0x8012B2D0u
#define N_EVO_GAINS 66u
#define STRIDE_EVO_GAINS 14u
#define SRC_ITEM_TAKE 0x80122D24u
#define N_ITEM_TAKE 67u
#define SRC_EVO_PATHS 0x8012B66Cu
#define N_EVO_PATHS 62u
#define STRIDE_EVO_PATHS 11u
#define SRC_EVOCHART 0x80124544u
/* 62, not the table's declared 61. renderEvoChartView's second loop draws
 * index 61 UNCONDITIONALLY, one entry past the end -- in stock that reads the
 * bytes that happen to follow, which place the frame at (-146,-67), safely
 * offscreen. Copying only 61 entries leaves the relocated slot zeroed and the
 * frame lands at (-1,-1), a visible stray box. Copying 62 keeps the vanilla
 * bytes and the vanilla appearance. */
#define N_EVOCHART 62u
#define STRIDE_EVOCHART 8u
/* TRAINING_ANIM_IDS and TRN2_D_8008DA20 have no SRC_: they live in the TRN
 * and TRN2 overlays, which are not resident at the fill hook (they load when
 * the player enters a gym). Their stock contents are baked into the include
 * instead -- see k_train_anim_stock / k_trn2_anim_stock. */
#define STRIDE_TRAIN_ANIM 2u

/* Feature-detection: the first EVO_REQ retarget site's lui. When the
 * manifest applied, its immediate reads 0x9F00. (Site from the census;
 * gen_roster_mod.py asserts this stays the first site.) */
#define DETECT_LUI_VADDR ROSTER_DETECT_LUI_VADDR

/* ---- authored type-114 entries (values researched from the game's own
 * tables; docs/GAME.md 12.2). Filled by data below. -------------- */
#include "dw_roster_data.inc"

static uint32_t roster_arena = 0;

static void roster_fill(void) {
    uint32_t i, s;
    /* Zero first. Every table here is grown to 180 entries while its stock
     * content covers 61-68, so the tail is read by any id the stock game
     * never had; "unreachable entries stay zero" should be a fact, not an
     * assumption about what the allocator hands back. */
    for (i = 0; i < ROSTER_ARENA_SIZE; i += 4)
        psx_mod_write_word(roster_arena + i, 0);
    /* copy stock entries */
    for (i = 0; i < N_EVO_REQ * STRIDE_EVO_REQ; i += 4)
        psx_mod_write_word(roster_arena + OFF_EVO_REQ + i,
                           psx_mod_read_word(SRC_EVO_REQ + i));
    for (i = 0; i < N_RAISE * STRIDE_RAISE; i += 4)
        psx_mod_write_word(roster_arena + OFF_RAISE + i,
                           psx_mod_read_word(SRC_RAISE + i));
    for (i = 0; i < N_EVO_GAINS * STRIDE_EVO_GAINS; i += 2)
        psx_mod_write_half(roster_arena + OFF_EVO_GAINS + i,
                           psx_mod_read_half(SRC_EVO_GAINS + i));
    for (i = 0; i < N_ITEM_TAKE; i++)
        psx_mod_write_byte(roster_arena + OFF_ITEM_TAKE + i,
                           psx_mod_read_byte(SRC_ITEM_TAKE + i));
    /* EVO_PATHS_DATA's tail must be 0xFF, not 0x00. The evolution scan skips
     * an entry only when it reads -1 (evolution.c:73); a zero is treated as a
     * real target and sends the game looking up type 0. Harmless while no
     * grown-range type can be a partner, live the moment one can -- so the
     * unauthored range is terminated properly rather than left zeroed. */
    for (i = 0; i < ROSTER_N_TYPES * STRIDE_EVO_PATHS; i++)
        psx_mod_write_byte(roster_arena + OFF_EVO_PATHS + i, 0xFF);
    /* Odd 11-byte stride, so copy bytewise rather than assuming alignment.
     * This copy is taken after the manifest's patches have been applied to
     * guest RAM, which is what carries the evolution wiring into the arena;
     * see the note in gen_roster_mod.py. */
    for (i = 0; i < N_EVO_PATHS * STRIDE_EVO_PATHS; i++)
        psx_mod_write_byte(roster_arena + OFF_EVO_PATHS + i,
                           psx_mod_read_byte(SRC_EVO_PATHS + i));
    for (i = 0; i < N_EVOCHART * STRIDE_EVOCHART; i += 2)
        psx_mod_write_half(roster_arena + OFF_EVOCHART + i,
                           psx_mod_read_half(SRC_EVOCHART + i));
    /* Battle-intro name banner: 112 stock rows, then the two types that used
     * to run off the end into the overlay's string pool. */
    for (i = 0; i < sizeof(k_std_glyph_stock); i++)
        psx_mod_write_byte(roster_arena + ROSTER_OFF_STD_GLYPH + i,
                           k_std_glyph_stock[i]);
    for (i = 0; i < sizeof(k_std_glyph_112); i++) {
        psx_mod_write_byte(
            roster_arena + ROSTER_OFF_STD_GLYPH + 112u * 14u + i,
            k_std_glyph_112[i]);
        psx_mod_write_byte(
            roster_arena + ROSTER_OFF_STD_GLYPH + 113u * 14u + i,
            k_std_glyph_113[i]);
        psx_mod_write_byte(
            roster_arena + ROSTER_OFF_STD_GLYPH + 114u * 14u + i,
            k_std_glyph_114[i]);
    }

    /* Versus portrait remap: type ids only, from the generator. */
    for (i = 0; i < sizeof(k_vs_remap); i++)
        psx_mod_write_byte(roster_arena + ROSTER_OFF_VS_REMAP + i, k_vs_remap[i]);

    /* The two overlay tables come from baked constants, not guest RAM. */
    for (i = 0; i < sizeof(k_train_anim_stock); i++)
        psx_mod_write_byte(roster_arena + OFF_TRAIN_ANIM + i,
                           k_train_anim_stock[i]);
    for (i = 0; i < sizeof(k_trn2_anim_stock); i++)
        psx_mod_write_byte(roster_arena + OFF_TRN2_ANIM + i,
                           k_trn2_anim_stock[i]);
    /* neutral defaults for the previously-out-of-range ids: unreachable
     * entries stay zero (EVO_REQ .digimon 0 never matches; RAISE zeros are
     * only read if a type becomes a partner, which only 114 can) */
    /* authored 114 */
    for (i = 0; i < sizeof(k_evo_req_114); i++)
        psx_mod_write_byte(roster_arena + OFF_EVO_REQ + 114u * STRIDE_EVO_REQ + i,
                           k_evo_req_114[i]);
    for (i = 0; i < sizeof(k_raise_114); i++)
        psx_mod_write_byte(roster_arena + OFF_RAISE + 114u * STRIDE_RAISE + i,
                           k_raise_114[i]);
    for (i = 0; i < sizeof(k_evo_gains_114); i++)
        psx_mod_write_byte(roster_arena + OFF_EVO_GAINS + 114u * STRIDE_EVO_GAINS + i,
                           k_evo_gains_114[i]);
    /* ITEM_TAKE_DISTANCE is read at [type - 1] (partner.c:412), unlike
     * EVO_REQ/RAISE/EVO_GAINS which are read at [type]. 0.2.0 wrote this one
     * at [114] and the game read [113] -- a zero. */
    psx_mod_write_byte(roster_arena + OFF_ITEM_TAKE + (114u - 1u),
                       k_item_take_114);
    for (i = 0; i < sizeof(k_train_anim_114); i++)
        psx_mod_write_byte(
            roster_arena + OFF_TRAIN_ANIM + 114u * STRIDE_TRAIN_ANIM + i,
            k_train_anim_114[i]);
    for (i = 0; i < sizeof(k_trn2_anim_114); i++)
        psx_mod_write_byte(
            roster_arena + OFF_TRN2_ANIM + 114u * STRIDE_TRAIN_ANIM + i,
            k_trn2_anim_114[i]);
    /* Newly raisable species (types 63-113). Same shape as slot 114 above,
     * but table-driven so the set is a generator edit. Mixed indexing: the
     * item-take distance is written at [type - 1], everything else at
     * [type]. */
    for (s = 0; s < (uint32_t)(sizeof(k_species) / sizeof(k_species[0])); s++) {
        const RosterSpecies* sp = &k_species[s];
        uint32_t t = sp->type;
        for (i = 0; i < sizeof(sp->req); i++)
            psx_mod_write_byte(
                roster_arena + OFF_EVO_REQ + t * STRIDE_EVO_REQ + i,
                sp->req[i]);
        for (i = 0; i < sizeof(sp->raise_); i++)
            psx_mod_write_byte(
                roster_arena + OFF_RAISE + t * STRIDE_RAISE + i,
                sp->raise_[i]);
        for (i = 0; i < sizeof(sp->gains); i++)
            psx_mod_write_byte(
                roster_arena + OFF_EVO_GAINS + t * STRIDE_EVO_GAINS + i,
                sp->gains[i]);
        psx_mod_write_byte(roster_arena + OFF_ITEM_TAKE + (t - 1u),
                           sp->item_take);
        for (i = 0; i < sizeof(sp->train); i++) {
            psx_mod_write_byte(
                roster_arena + OFF_TRAIN_ANIM + t * STRIDE_TRAIN_ANIM + i,
                sp->train[i]);
            psx_mod_write_byte(
                roster_arena + OFF_TRN2_ANIM + t * STRIDE_TRAIN_ANIM + i,
                sp->trn2[i]);
        }
        /* NOTE: this used to 0xFF-fill the species' own EVO_PATHS row, on the
         * assumption that every authored species was a terminal Ultimate. It is
         * removed, not moved: the arena-wide 0xFF fill above already covers
         * every row the stock copy does not reach (types 63+), so it was
         * redundant -- and now that newly-authored Rookies are themselves
         * evolution sources, it would erase their to[] and silently strip every
         * Champion hanging off them. */
    }

    /* Evolution paths whose SOURCE is newly authored. Stock sources have a row
     * in the EXE and are patched there, then copied in above; these types are
     * past the end of the stock table and have no row to patch, so their to[]
     * bytes are written here -- after the species loop, and after the stock
     * copy, so nothing can overwrite them. */
    for (s = 0; s < (uint32_t)(sizeof(k_evo_wire) / sizeof(k_evo_wire[0])); s++) {
        const RosterWire* w = &k_evo_wire[s];
        if (w->src == 0)
            continue;                    /* the empty-table placeholder */
        psx_mod_write_byte(roster_arena + OFF_EVO_PATHS
                           + (uint32_t)(w->src - 1u) * STRIDE_EVO_PATHS
                           + 5u + w->slot,
                           w->target);
    }

    /* EVOCHART_ICONS[114] is deliberately left zero. The evolution chart is a
     * fixed 61-slot layout with baked screen positions, so there is no free
     * position to give slot 114 and copying a donor entry would draw it on
     * top of that donor. Stock reads 424 bytes past the table here; zero is
     * at least deterministic. Open item: what this actually draws needs to be
     * looked at on screen before the chart is claimed correct. */
    printf("dw_roster: arena %08X filled (tables grown to %u types)\n",
           roster_arena, (unsigned)ROSTER_N_TYPES);
    fflush(stdout);
}

static int roster_live = 0;
static int roster_filled = 0;

/* Fill once per process, but only when the manifest's retarget patches are
 * actually present in guest RAM (patches applied <=> feature enabled). */
static void roster_ensure_filled(void) {
    if (roster_filled || !roster_arena)
        return;
    if ((psx_mod_read_word(DETECT_LUI_VADDR) & 0xFFFFu) != 0x9F00u)
        return;
    roster_filled = 1;
    roster_live = 1;
    roster_fill();
}

static void roster_hook(struct CPUState* cpu, uint32_t address) {
    (void)cpu;
    (void)address;
    roster_ensure_filled();
}

/* A session resumed from a savestate at boot never runs initializeHeap, so
 * the entry hook alone leaves the arena unfilled (the crash mode: retargeted
 * reads hit zeros). The framework reapplies the mod plan on restore, so the
 * patch-detect gate holds; this per-VBlank check closes the gap. */
/* HAS_USED_EVOITEM is set to 1 when an evolution item fires and is cleared
 * only when a partner is initialised (partner_impl.c:124). EVL_scaleBaseStats
 * returns early while it is set (evl.c:734), so after one item evolution every
 * LATER natural evolution silently skips its base-stat rescale for the rest of
 * the playthrough. Vanilla never exposed this because the items were
 * unreachable; un-gating them is what makes it reachable, so it is fixed here.
 *
 * Cleared only once the sequence is fully over -- partner idle, control
 * returned, game time running again. Clearing it mid-sequence would rescale
 * the item evolution itself, which is the behaviour the flag exists to
 * suppress. */
#define VA_HAS_USED_EVOITEM 0x80134E52u
#define VA_PARTNER_STATE 0x80134C5Bu
#define VA_TAMER_STATE 0x80134C91u
#define VA_EVOLUTION_TARGET 0x80134E50u
#define VA_IS_GAMETIME_RUNNING 0x80134E20u

static void roster_clear_evoitem_flag(void) {
    if (psx_mod_read_byte(VA_HAS_USED_EVOITEM) == 0)
        return;
    if (psx_mod_read_byte(VA_PARTNER_STATE) != 1)
        return;
    if (psx_mod_read_byte(VA_TAMER_STATE) != 0)
        return;
    if ((int16_t)psx_mod_read_half(VA_EVOLUTION_TARGET) != -1)
        return;
    if (psx_mod_read_word(VA_IS_GAMETIME_RUNNING) == 0)
        return;
    psx_mod_write_byte(VA_HAS_USED_EVOITEM, 0);
}

/* Cup bracket projectile icons. STD_REL.BIN is resident only during a cup, and
 * only its four references were retargeted, so its own table is intact then:
 * copy it every VBlank while the patched lui is in RAM (STD shares its address
 * window with BTL and VS, which never hold that word), then the donor rows.
 * Copying every VBlank, not once, keeps the arena right after a savestate load
 * or rewind into a cup: the arena is not part of the snapshot. */
static void roster_copy_std_icons(void) {
    uint32_t i;
    if ((psx_mod_read_word(ROSTER_STD_ICONS_LUI) & 0xFFFFu) != 0x9F00u)
        return;
    for (i = 0; i < 112u; i++)
        psx_mod_write_byte(roster_arena + ROSTER_OFF_STD_ICONS + i,
                           psx_mod_read_byte(ROSTER_STD_ICONS_SRC + i));
    for (i = 0; i < sizeof(k_std_icon_donors) / sizeof(k_std_icon_donors[0]); i++)
        psx_mod_write_byte(roster_arena + ROSTER_OFF_STD_ICONS + k_std_icon_donors[i][0],
                           psx_mod_read_byte(ROSTER_STD_ICONS_SRC + k_std_icon_donors[i][1]));
}

static void roster_vblank(void) {
    if (!roster_filled && psx_mod_game_started())
        roster_ensure_filled();
    if (roster_live) {
        roster_clear_evoitem_flag();
        roster_copy_std_icons();
    }
}

/* Model + texture remap: slot 114 has no MMD file or ALLTIM slot of its own
 * (its model code is the dev stub tAKA and its texture slot duplicates
 * Meteormon's). Both loaders take the type in $a0 and derive the model
 * filename AND the ALLTIM sector (type*9) from it, so rewriting the argument
 * at entry redirects everything at once -- the same net effect as the
 * 4-instruction entry patch the Vice hack ships, without touching a byte of
 * code. Covers map spawns, partners, evolution (EVL calls loadMMDAsync) and
 * battle (models resident from map load). */
static void roster_remap_hook(struct CPUState* cpu, uint32_t address) {
    (void)address;
    if (roster_live && cpu->gpr[4] == 114u)
        cpu->gpr[4] = 115u;
}

static void roster_activate(void) {
    roster_arena = psx_mod_alloc_guest_memory(ROSTER_ARENA_SIZE, 16);
    if (roster_arena != ROSTER_ARENA_BASE) {
        printf("dw_roster: arena at %08X, expected %08X -- manifest "
               "addresses assume first-alloc; roster disabled\n",
               roster_arena, ROSTER_ARENA_BASE);
        fflush(stdout);
        roster_arena = 0;
    }
}

/* ---- cup lists (tools/roster_cups.py) ------------------------------------
 * Roster species join the arena cups' entry and penalty lists in DG scripts
 * 213 and 102. The lists are rebuilt in the script buffer, never patched on
 * disc: every list read goes through getScript(ACTIVE_MAP_SCRIPT), whose
 * cached path returns the buffer, so this filter rebuilds the lists on the
 * load (served here, cup_load) or, failing that, on the first cached call
 * after the stock body loaded it. A probe word tells
 * stock, rebuilt and other layouts apart in O(1); the stock layout must also
 * match the generator's whole-slot hash, so a different disc is left untouched
 * (counter roster.cups.unknown) and its cups simply keep the stock lists.
 * A savestate restores whatever the buffer held: rebuilt stays rebuilt (probe),
 * stock is rebuilt on the next call. Ids are disjoint from dw_newmaps.c's own
 * getScript filter, which declines these. */
#define CUP_GET_SCRIPT 0x80106218u
#define CUP_ACTIVE_MAP_SCRIPT 0x80134FC2u   /* u16 */
#define CUP_MAP_SCRIPT_PTR 0x80134FB4u      /* u8* */
#define CUP_SCRIPT_OFFSET_PTR 0x80134FB0u  /* u32* DG.SCN offset table */
#define CUP_LOAD_STARTPOS 0x80134FA8u      /* DG.SCN start sector, cached after the first read */
#define CUP_N 23

static uint32_t cup_u16(uint32_t a) { return psx_mod_read_half(a); }

static uint32_t cup_hash(uint32_t base, uint32_t size) {
    uint32_t h = 0x811C9DC5u, i, w;
    for (i = 0; i < size; i += 4) {
        w = psx_mod_read_word(base + i);
        h = (h ^ (w & 0xFF)) * 0x01000193u;
        h = (h ^ ((w >> 8) & 0xFF)) * 0x01000193u;
        h = (h ^ ((w >> 16) & 0xFF)) * 0x01000193u;
        h = (h ^ (w >> 24)) * 0x01000193u;
    }
    return h;
}

/* The relayout of roster_cups.relayout(): lists in (cup, list 1..4) order, the
 * additions before the terminator, identical lists stored once, packed from
 * the lowest list offset, every u16 rewritten. `skip` drops an id the edition
 * cannot raise (114 on 1.5). Returns 0 if the script does not parse. */
static int cup_rebuild(uint32_t base, uint32_t size, const unsigned char* adds, unsigned skip) {
    static unsigned char lists[CUP_N * 4][256];
    static unsigned short len[CUP_N * 4], posv[CUP_N * 4];
    static unsigned char out[0x4000];
    unsigned short placed[CUP_N * 4];
    uint32_t a = 0, p, start = 0xFFFFFFFFu, at, i, j, k, n;
    for (p = 2; p + 4 <= size; p += 4) {          /* {u16 section, u16 offset}, 0xFFFF ends */
        uint32_t sec = cup_u16(base + p);
        if (sec == 0xFFFF)
            break;
        if (sec == 0xA) {
            a = cup_u16(base + p + 2);
            break;
        }
    }
    if (!a)
        return 0;
    for (i = 0; i < CUP_N; i++) {
        uint32_t sub = cup_u16(base + a + i * 4 + 2);
        for (k = 1; k <= 4; k++) {
            uint32_t slot = i * 4 + (k - 1), pos = sub + k * 4 + 2, off = cup_u16(base + pos) + 2, m = 0;
            posv[slot] = (unsigned short)pos;
            if (off < start)
                start = off;
            if (k == 1) {
                for (m = 0; m < 3; m++)
                    lists[slot][m] = psx_mod_read_byte(base + off + m);
            } else {
                do {
                    if (m >= 200 || off + m >= size)
                        return 0;
                    lists[slot][m] = psx_mod_read_byte(base + off + m);
                } while (lists[slot][m++] < 0xFE);
            }
            len[slot] = (unsigned short)m;
        }
    }
    for (p = 0; adds[p] != 0xFF; p += 3 + adds[p + 2]) {      /* [cup, list, n, ids...] */
        uint32_t slot = adds[p] * 4 + (adds[p + 1] - 1u), term;
        if (adds[p] >= CUP_N || adds[p + 1] < 2)
            return 0;
        term = lists[slot][len[slot] - 1];
        n = len[slot] - 1;
        for (j = 0; j < adds[p + 2]; j++)
            if (adds[p + 3 + j] != skip && n < 254)
                lists[slot][n++] = adds[p + 3 + j];
        lists[slot][n++] = (unsigned char)term;
        len[slot] = (unsigned short)n;
    }
    at = start;
    for (i = 0; i < CUP_N * 4; i++) {
        placed[i] = 0;
        for (j = 0; j < i; j++)
            if (len[j] == len[i] && !memcmp(lists[j], lists[i], len[i])) {
                placed[i] = placed[j];
                break;
            }
        if (j < i)
            continue;
        if (at + len[i] > size)
            return 0;
        placed[i] = (unsigned short)at;
        memcpy(out + at, lists[i], len[i]);
        at += len[i];
    }
    for (i = start; i < at; i++)
        psx_mod_write_byte(base + i, out[i]);
    for (i = 0; i < CUP_N * 4; i++)
        psx_mod_write_half(base + posv[i], (uint16_t)(placed[i] - 2));
    return 1;
}

static uint32_t cup_warned = 0xFFFFFFFFu;

/* A load of 213/102 is served here, as the stock body would read it (the
 * offset table's row, whole sectors from DG.SCN's cached start), so the lists
 * are rebuilt before anything reads them. If the read fails or the bytes are
 * not the stock ones, decline: the stock body loads the script and the cached
 * path below decides. */
static int cup_load(const RosterCupScript* s, uint32_t id, int edition, struct CPUState* cpu) {
    static unsigned char buf[0x4000];
    uint32_t table = psx_mod_read_word(CUP_SCRIPT_OFFSET_PTR), start = psx_mod_read_word(CUP_LOAD_STARTPOS);
    uint32_t off = psx_mod_read_word(table + id * 4), size = psx_mod_read_word(table + id * 4 + 4) - off;
    uint32_t h = 0x811C9DC5u, i, dst;
    if (!start || size != s->size || (off & 0x7FF) || !psx_mod_read_disc_sectors(start + (off >> 11), size >> 11, buf))
        return 0;
    for (i = 0; i < size; i++)
        h = (h ^ buf[i]) * 0x01000193u;
    if (h != s->hash[0])
        return 0;
    dst = psx_mod_read_word(CUP_MAP_SCRIPT_PTR);
    for (i = 0; i < size; i += 4)
        psx_mod_write_word(dst + i, (uint32_t)buf[i] | buf[i + 1] << 8 | buf[i + 2] << 16 | (uint32_t)buf[i + 3] << 24);
    psx_mod_write_half(CUP_ACTIVE_MAP_SCRIPT, (uint16_t)id);
    if (cup_rebuild(dst, size, s->adds, edition ? 114u : 0x100u) && cup_hash(dst, size) == s->hash[edition ? 2 : 1])
        psx_mod_counter_add("roster.cups.rebuilt", 1);
    cpu->gpr[2] = dst;
    return 1;
}

static int cup_filter(struct CPUState* cpu, int edition) {
    uint32_t i, id = cpu->gpr[4] & 0xFFFF, base, probe;
    for (i = 0; i < sizeof(k_cup_scripts) / sizeof(k_cup_scripts[0]); i++) {
        const RosterCupScript* s = &k_cup_scripts[i];
        if (s->id != id)
            continue;
        if (id != psx_mod_read_half(CUP_ACTIVE_MAP_SCRIPT))
            return cup_load(s, id, edition, cpu);
        base = psx_mod_read_word(CUP_MAP_SCRIPT_PTR);
        probe = cup_u16(base + s->probe_pos);
        if (probe == s->probe[edition ? 2 : 1])
            return 0;                             /* already rebuilt */
        if (probe == s->probe[0] && cup_hash(base, s->size) == s->hash[0]
                && cup_rebuild(base, s->size, s->adds, edition ? 114u : 0x100u)
                && cup_hash(base, s->size) == s->hash[edition ? 2 : 1]) {
            psx_mod_counter_add("roster.cups.rebuilt", 1);
            return 0;
        }
        if ((id << 16 | probe) != cup_warned) {   /* once per layout, not per call */
            cup_warned = id << 16 | probe;
            psx_mod_counter_add("roster.cups.unknown", 1);
            printf("dw_roster: script %u's cup lists are not the stock ones; left as they are\n", (unsigned)id);
            fflush(stdout);
        }
        return 0;
    }
    return 0;
}

static int roster_cup_filter(struct CPUState* cpu, uint32_t address) {
    (void)address;
    return cup_filter(cpu, 0);
}

static int roster15_cup_filter(struct CPUState* cpu, uint32_t address) {
    (void)address;
    return cup_filter(cpu, 1);
}

PSX_MOD_CONSTRUCTOR(dw_roster_register) {
    psx_mod_register_activation_plugin("dw.roster", roster_activate);
    psx_mod_register_function_entry_plugin("dw.roster", ROSTER_HOOK_VADDR,
                                           roster_hook);
    psx_mod_register_vblank_plugin("dw.roster", roster_vblank);
    psx_mod_register_function_filter_plugin("dw.roster", CUP_GET_SCRIPT, roster_cup_filter);
    psx_mod_register_function_filter_plugin("dw.roster15", CUP_GET_SCRIPT, roster15_cup_filter);
    psx_mod_register_function_entry_plugin("dw.roster", ROSTER_LOADMMD_VADDR,
                                           roster_remap_hook);
    psx_mod_register_function_entry_plugin("dw.roster",
                                           ROSTER_LOADMMDASYNC_VADDR,
                                           roster_remap_hook);
}
