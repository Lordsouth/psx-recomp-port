/* dw.roster / dw.roster15 -- memory cards that load with the Expanded Roster on or off, on the stock game and on
 * Digimon World 1.5.
 *
 * A card names its partner by type. With the roster off, a type 63-114 indexes past the end of every stock
 * partner-line table (RAISE_DATA has 66 rows, EVO_PATHS 62, ...), and 114 has no model at all. On plain 1.5 the
 * rows exist (placeholders), but 1.5's own setDigimonRaised banks species 63-107 on treasure-chest triggers, so a
 * roster-15 species the partner evolves back into marks a chest as taken. So a card never names a roster species:
 * it names a stock stand-in of the same level (k_save_stand_in, tools/gen_roster_mod.py save_stand_ins: never one of
 * the special forms Numemon, Sukamon, Nanimon or Devimon, whose stock code reads more state), and keeps the true
 * values in bytes the stock game neither reads nor writes. With the roster on, Continue puts them back.
 *
 * WHERE. SavegamePayload.unknown4E1 (+0x4E1..+0x4FB, 27 bytes, docs/GAME.md 9.1): writeSavegame does not write it,
 * loadSavegame does not read it, and nothing else in the stock code names it (main_menu.c:135, :5016-5134). A stock
 * save therefore carries it over unchanged from the card it loaded (saves[1] = saves[0] copies it whole). 1.5 keeps
 * the payload layout (its writeSavegame and loadSavegame differ only in DIGIMON_DATA's address).
 *
 *   +4E1 'R' 'S' 2       magic, version
 *   +4E4 u32             FNV-1a of the stock-visible payload as this hook wrote it (see covered())
 *   +4E8 u8              the true partner type                    (flag 1)
 *   +4E9 u8[4]           its equipped move slots (Stats.base.moves) (flag 1)
 *   +4ED u8              the true pstat 5                          (flag 2)
 *   +4EE u8              the true pstat 4                          (flag 4)
 *   +4EF u8              flags
 *   +4F0 u8[5]           40-bit mask: Battle Mode registrations that are 114 (stock edition only)
 *
 * THE RULE. Continue with the roster on restores exactly when the card's stock-visible payload hashes to the stored
 * value, i.e. it is byte for byte what this hook wrote. A stock save after that rewrites the payload from the live
 * game (playtime, clock, the partner...), so the hash no longer matches and what the player then had stays; the
 * stale block is never applied, and the next roster save overwrites or clears it. If the payload is unchanged,
 * restoring is exactly right, whatever happened in between. The location fields dw_newmaps.c rewrites at the same
 * checksum call (positions, poop piles, screens, waypoint tail) are left out of the hash, so the order of the two
 * entry hooks does not matter. Any new rewrite of the save buffer at that call must stay inside covered()'s
 * excluded ranges, or update it.
 *
 * WHAT IS REMAPPED, each only when it names a roster species:
 *   - partnerType (+1B8) to the stand-in;
 *   - Stats.base.moves (+27C): a slot indexes the species' own move list (DIGIMON_DATA.moves[slot - 0x2E],
 *     src/evl/evl.c:1450-1476), so each equipped move is looked up by move id in the stand-in's list; moves it lacks
 *     are dropped (the rest move up), an empty set gets what EVL would give a fresh stand-in (the lowest-power move of
 *     its first special group; the generator asserts every stand-in has one), and the finisher slot takes the
 *     stand-in's own finisher if it lacks the roster one;
 *   - pstat 5 (+3EE), whatever the partner: the type Sukamon's cure (partner routine 2, partner.c:1153-1157) evolves
 *     into; the game writes it when the virus strikes (partner_impl.c:2130) and one disc script stores the partner's
 *     type there (DG script 209), so it can name a roster species long after;
 *   - pstat 4 (+3ED), when it equals the true partner type: the species a tournament entry was made with
 *     (script_interp.c:1811); updateTournamentRegistration drops the entry when it no longer matches the partner,
 *     so it follows the partner to its stand-in. The disc scripts store a type in no other saved pstat
 *     (STORE_DIGIMON_TYPE, op 0x22, targets only pstats 5, 103, 200; 103 and 200 are not saved);
 *   - a Battle Mode registration (+500, 40 records of 0x40, main_menu.c:5230-5245): on the stock edition 114 is
 *     written as 115, the stock Machinedramon whose record and model 114 uses (stock has no model for 114); every
 *     other roster species (63-113, both editions; on 1.5, 114 is Veemon, its own species) as its stand-in, its
 *     three move slots translated into the stand-in's list as for the partner. Versus mode, the only reader, indexes
 *     68-entry portrait palette tables by the id and the species' move list by the slots (vs_select.c:797,
 *     vs_scene.c:1344, vs_combat.c:477), so a roster id there reads past the tables with the roster off. The true
 *     id and slots go into the record's own unused bytes (RegisteredDigimon.unk20, +0x20..+0x3F: nothing reads
 *     them, every copy moves the whole record), with a hash of the record's first 0x20 bytes as written:
 *       rec+0x20 'R' 'V', +0x22 true id, +0x23 its three slots, +0x26 u32 FNV-1a of rec+0x00..+0x1F
 *     A record is restored exactly when that hash still matches: a registration made again on the stock game
 *     rewrites the stats and name, so it stays as made. Each record stands alone (no payload hash), so a card
 *     saved again without the roster keeps its untouched registrations restorable.
 * Everything else (stats, PartnerPara, learned moves, name, lives, the raised flags dw_roster_flags.c banks at
 * trigger 0x2CD+) is generic data the stock game reads correctly or never reads, and is left alone.
 *
 * WHEN.
 *   Save: an entry hook on createSavegameChecksum filtered to the two save calls (as dw_newmaps.c), so the rewritten
 *   bytes are summed, copied to saves[1] and written. Play goes on with the live roster partner.
 *   After the write: an exit hook on MemCardSync, filtered to the sync after each save's MemCardWriteFile, puts the
 *   114 registrations back in both copies in SAVE_FILE. Registrations live only there (writeSavegame never writes
 *   them), so the roster's live data is changed only for the length of the write.
 *   Load: an entry hook on loadSavegame (a0 = the payload), after the caller has verified the checksum and before any
 *   field is copied out; it restores both copies in SAVE_FILE (the save machine later copies saves[1] over saves[0]).
 *   Versus mode reads the card without loadSavegame (main_menu.c:4147-4174): an entry hook on countRegisteredDigimon,
 *   called right after the registrations are copied into the VS buffer, puts the 114s back there when the buffer
 *   matches a validated copy.
 * No guest or host state is held, so savestates and Rewind have nothing to do. dw.roster runs only with the roster's
 * feature and dw.roster15 only with roster-15 (Digimon World 1.5), so with the feature off everything here is inert.
 */
#include <stdint.h>

#include "cpu_state.h"
#include "mod_plugins.h"

#include "dw_roster_saves.inc"

#define DW_CHECKSUM       0x80112D34u   /* createSavegameChecksum */
#define DW_LOAD_SAVEGAME  0x80112D84u   /* loadSavegame(SavegamePayload*) */
#define DW_MEMCARD_SYNC   0x800EB980u   /* MemCardSync: exit hook */
#define DW_COUNT_REGS     0x8011341Cu   /* countRegisteredDigimon(RegisteredDigimon*) */
#define DW_RA_SLEEP       0x801114A4u   /* createSavegameChecksum's call in menu 0x2B: the sleep save */
#define DW_RA_MACHINE     0x80112060u   /* its call in menu 0x41: save machine, Save Anywhere, game clear */
#define DW_RA_SLEEP_SYNC  0x80111528u   /* MemCardSync after menu 0x2B's MemCardWriteFile */
#define DW_RA_MACHINE_SYNC 0x801120E4u  /* MemCardSync after menu 0x41's MemCardWriteFile */
#define S0                0x80131B2Cu   /* SAVE_FILE.saves[0] */
#define S1                (S0 + 0xF00u) /* SAVE_FILE.saves[1] */

#define P_TYPE   0x1B8u
#define P_MOVES  0x27Cu                 /* Stats (+270) .base.moves[4] */
#define P_PSTAT4 0x3EDu                 /* scriptState (+290): smth[212], cards[33], triggers[100], then pstats */
#define P_PSTAT5 0x3EEu
#define P_REGS   0x500u                 /* RegisteredDigimon[40], 0x40 each, digimonId +0x1C */
#define P_SPARE  0x4E1u
#define N_SPARE  27u
#define B_HASH   0x4E4u
#define B_TYPE   0x4E8u
#define B_MOVES  0x4E9u
#define B_PSTAT5 0x4EDu
#define B_PSTAT4 0x4EEu
#define B_FLAGS  0x4EFu
#define B_REGS   0x4F0u
#define F_PARTNER 1u
#define F_PSTAT5  2u
#define F_PSTAT4  4u
#define VERSION   2u
#define SLOT0    0x2Eu                  /* a move slot value is 0x2E + index into DIGIMON_DATA.moves */
#define R_ID     0x1Cu                  /* RegisteredDigimon.digimonId; moves[3] at +0x1D */
#define R_TAG    0x20u                  /* unk20: 'R' 'V', true id, its three slots, u32 hash of +0x00..+0x1F */

typedef struct {
    uint32_t digimon_data;  /* 52 B each: special[3] +30, moves[16] +35 */
    uint32_t move_data;     /* 16 B each: power i16 +4 */
    int stock;              /* the stock edition: 114 is Machinedramon and Battle Mode registrations are remapped */
} Edition;
static const Edition k_stock = {0x8012CEB4u, 0x8012623Cu, 1};
static const Edition k_dw15  = {0x801F4300u, 0x8012ABECu, 0};   /* 1.5 moved both tables (docs/INTERNALS.md 4.4) */

static uint8_t stand_in(const Edition* e, uint32_t type) {
    if (type == 114 && !e->stock)                                 /* 1.5's 114 is Veemon, its own species */
        return 0;
    for (uint32_t i = 0; i < sizeof k_save_stand_in / sizeof *k_save_stand_in; i++)
        if (k_save_stand_in[i][0] == type)
            return k_save_stand_in[i][1];
    return 0;
}

/* The stock-visible bytes the hash covers: all of the payload but the spare block, the checksum, and what
 * dw_newmaps.c may rewrite (tamer/partner position and poop piles +04..+1B3, screens and exits +49A..+49D,
 * waypoint tail +4DA..+4E0). */
static int covered(uint32_t o) {
    return !((o >= 0x04 && o < 0x1B4) || (o >= 0x49A && o < 0x49E) || (o >= 0x4DA && o < 0x500));
}

static uint32_t payload_hash(uint32_t p) {
    uint32_t h = 2166136261u;
    for (uint32_t o = 0; o < 0xF00; o++)
        if (covered(o))
            h = (h ^ psx_mod_read_byte(p + o)) * 16777619u;
    return h;
}

static uint8_t move_at(const Edition* e, uint32_t type, uint32_t i) {
    return psx_mod_read_byte(e->digimon_data + type * 52 + 35 + i);
}

static uint8_t move_id(const Edition* e, uint32_t type, uint8_t slot) {
    return (slot >= SLOT0 && slot < SLOT0 + 16) ? move_at(e, type, slot - SLOT0) : 0xFF;
}

static uint8_t slot_of(const Edition* e, uint32_t type, uint8_t id) {
    if (id == 0xFF)
        return 0xFF;
    for (uint8_t i = 0; i < 16; i++)
        if (move_at(e, type, i) == id)
            return SLOT0 + i;
    return 0xFF;
}

/* EVL_applyEvolution's starting move (src/evl/evl.c:1418-1455) and finisher (:1461-1476) for `type`. */
static uint8_t fresh_move(const Edition* e, uint32_t type) {
    static const uint8_t base[7] = {0, 40, 8, 32, 16, 24, 49};
    uint8_t special = psx_mod_read_byte(e->digimon_data + type * 52 + 30), lo = special < 7 ? base[special] : 0;
    uint8_t best = 0xFF;
    for (uint8_t i = 0; i < 16; i++) {
        uint8_t m = move_at(e, type, i);
        if (m < lo || m > lo + 8)
            continue;
        int16_t pm = (int16_t)psx_mod_read_half(e->move_data + m * 16u + 4);
        if (best == 0xFF || (pm != 0 && (int16_t)psx_mod_read_half(e->move_data + best * 16u + 4) > pm))
            best = m;
    }
    return slot_of(e, type, best);
}

static uint8_t fresh_finisher(const Edition* e, uint32_t type) {
    for (int i = 15; i >= 0; i--) {
        uint8_t m = move_at(e, type, (uint32_t)i);
        if (m != 0xFF && m >= 0x3A && m < 0x71)
            return SLOT0 + (uint8_t)i;
    }
    return 0xFF;
}

/* Three equipped slots at `at` (the partner's Stats.base.moves, or a registration's moves) from `from`'s move list
 * into `to`'s; with fin_too, the finisher slot at at+3 as well. */
static void remap_moves_at(const Edition* e, uint32_t at, uint32_t from, uint32_t to, int fin_too) {
    uint8_t out[4] = {0xFF, 0xFF, 0xFF, 0xFF}, n = 0;
    for (uint32_t i = 0; i < 3; i++) {
        uint8_t s = slot_of(e, to, move_id(e, from, psx_mod_read_byte(at + i)));
        if (s != 0xFF)
            out[n++] = s;
    }
    if (n == 0)
        out[0] = fresh_move(e, to);
    if (fin_too) {
        uint8_t fin = slot_of(e, to, move_id(e, from, psx_mod_read_byte(at + 3)));
        out[3] = (fin != 0xFF && move_id(e, to, fin) >= 0x3A && move_id(e, to, fin) < 0x71) ? fin
                                                                                          : fresh_finisher(e, to);
    }
    for (uint32_t i = 0; i < (fin_too ? 4u : 3u); i++)
        psx_mod_write_byte(at + i, out[i]);
}

static void remap_moves(const Edition* e, uint32_t from, uint32_t to) {
    remap_moves_at(e, S0 + P_MOVES, from, to, 1);
}

static uint32_t rec_hash(uint32_t r) {
    uint32_t h = 2166136261u;
    for (uint32_t o = 0; o < R_TAG; o++)
        h = (h ^ psx_mod_read_byte(r + o)) * 16777619u;
    return h;
}

static int rec_tagged(uint32_t r) {
    return psx_mod_read_byte(r + R_TAG) == 'R' && psx_mod_read_byte(r + R_TAG + 1) == 'V';
}

/* One registration to the card: a roster id becomes its stand-in (114 -> 115 on the stock edition), the truth goes
 * into the record's unused tail with a hash of what is written. A tag that no longer matches (the record was made
 * again since) is cleared. Returns 1 when the record was remapped. */
static int save_reg(const Edition* e, uint32_t r) {
    uint8_t id = psx_mod_read_byte(r + R_ID);
    uint8_t to = (e->stock && id == 114) ? 115 : stand_in(e, id);
    if (!to) {
        if (rec_tagged(r) && psx_mod_read_word(r + R_TAG + 6) != rec_hash(r))
            for (uint32_t i = 0; i < 10; i++)
                psx_mod_write_byte(r + R_TAG + i, 0);
        return 0;
    }
    psx_mod_write_byte(r + R_TAG, 'R');
    psx_mod_write_byte(r + R_TAG + 1, 'V');
    psx_mod_write_byte(r + R_TAG + 2, id);
    for (uint32_t i = 0; i < 3; i++)
        psx_mod_write_byte(r + R_TAG + 3 + i, psx_mod_read_byte(r + R_ID + 1 + i));
    if (to != 115)                                  /* 114 has 115's record and move list */
        remap_moves_at(e, r + R_ID + 1, id, to, 0);
    psx_mod_write_byte(r + R_ID, to);
    psx_mod_write_word(r + R_TAG + 6, rec_hash(r));
    return 1;
}

/* A true value written by the stock edition that this edition must not bring back: 114 is the stock Machinedramon,
 * and on 1.5 it is Veemon. Only the stock edition ever stores a 114 (1.5 leaves its own 114 alone), so on 1.5 the
 * stand-in (115 for a registration, MetalGreymon for a partner) stays in play, and the card keeps the tag or block
 * for the stock edition: restoring it as Veemon would let the next 1.5 save write a raw 114, which the stock game
 * cannot load. */
static int foreign(const Edition* e, uint8_t v) {
    return !e->stock && v == 114;
}

/* The roster registration back from its tag, if the record is still as written. */
static int restore_reg(const Edition* e, uint32_t r) {
    if (!rec_tagged(r) || psx_mod_read_word(r + R_TAG + 6) != rec_hash(r) || foreign(e, psx_mod_read_byte(r + R_TAG + 2)))
        return 0;
    psx_mod_write_byte(r + R_ID, psx_mod_read_byte(r + R_TAG + 2));
    for (uint32_t i = 0; i < 3; i++)
        psx_mod_write_byte(r + R_ID + 1 + i, psx_mod_read_byte(r + R_TAG + 3 + i));
    return 1;
}

static uint32_t restore_regs_tagged(const Edition* e, uint32_t base) {
    uint32_t n = 0;
    for (uint32_t i = 0; i < 40; i++)
        n += (uint32_t)restore_reg(e, base + i * 0x40);
    return n;
}

static int has_block(uint32_t p) {
    return psx_mod_read_byte(p + P_SPARE) == 'R' && psx_mod_read_byte(p + P_SPARE + 1) == 'S'
        && psx_mod_read_byte(p + P_SPARE + 2) == VERSION;
}

static int valid_block(uint32_t p) {
    return has_block(p) && psx_mod_read_word(p + B_HASH) == payload_hash(p);
}

/* Put the 114 registrations back in payload p, as the block's mask says (stock edition only: see foreign()). */
static void restore_regs(const Edition* e, uint32_t p) {
    if (!e->stock)
        return;
    for (uint32_t i = 0; i < 40; i++)
        if (psx_mod_read_byte(p + B_REGS + i / 8) >> (i % 8) & 1)
            psx_mod_write_byte(p + P_REGS + i * 0x40 + 0x1C, 114);
}

/* The edition whose save hook ran for the card write now in progress, or NULL. Exit hooks fire whether or not
 * their plugin's feature is selected (mod_runtime.cpp, psx_mod_function_exit), so the MemCardSync exit acts only
 * after this edition's own checksum entry hook (which the plan does gate) ran in the same save: with the roster
 * off, a stock save would otherwise have the true ids put back in SAVE_FILE, and the next stock save would write
 * them raw. Held from the checksum call to the sync of one menu tick; a savestate taken in between resumes inside
 * the same call chain. */
static const Edition* g_pending = 0;

static void save(const Edition* e, struct CPUState* cpu) {
    if (cpu->gpr[31] != DW_RA_SLEEP && cpu->gpr[31] != DW_RA_MACHINE)
        return;
    g_pending = e;
    uint8_t regs[5] = {0, 0, 0, 0, 0}, any_reg = 0;
    uint32_t n_regs = 0;
    for (uint32_t i = 0; i < 40; i++) {
        uint32_t r = S0 + P_REGS + i * 0x40;
        if (e->stock && psx_mod_read_byte(r + R_ID) == 114) {   /* the version-2 mask too, as before */
            regs[i / 8] |= (uint8_t)(1u << (i % 8));
            any_reg = 1;
        }
        n_regs += (uint32_t)save_reg(e, r);
    }
    if (n_regs)
        psx_mod_counter_add("dw.roster.saves.regs", n_regs);

    uint32_t type = psx_mod_read_word(S0 + P_TYPE);
    uint8_t pstat4 = psx_mod_read_byte(S0 + P_PSTAT4), pstat5 = psx_mod_read_byte(S0 + P_PSTAT5);
    uint8_t to = stand_in(e, type), to5 = stand_in(e, pstat5), to4 = pstat4 == type ? stand_in(e, pstat4) : 0;
    uint8_t flags = (uint8_t)((to ? F_PARTNER : 0) | (to5 ? F_PSTAT5 : 0) | (to4 ? F_PSTAT4 : 0));
    if (!flags && !any_reg) {
        if (has_block(S0) || psx_mod_read_byte(S0 + P_SPARE) == 'R')   /* a stock partner now: nothing to bring back */
            for (uint32_t i = 0; i < N_SPARE; i++)
                psx_mod_write_byte(S0 + P_SPARE + i, 0);
        return;
    }
    for (uint32_t i = 0; i < N_SPARE; i++)
        psx_mod_write_byte(S0 + P_SPARE + i, 0);
    psx_mod_write_byte(S0 + P_SPARE + 0, 'R');
    psx_mod_write_byte(S0 + P_SPARE + 1, 'S');
    psx_mod_write_byte(S0 + P_SPARE + 2, VERSION);
    psx_mod_write_byte(S0 + B_FLAGS, flags);
    for (uint32_t i = 0; i < 5; i++)
        psx_mod_write_byte(S0 + B_REGS + i, regs[i]);
    if (to) {
        psx_mod_write_byte(S0 + B_TYPE, (uint8_t)type);
        for (uint32_t i = 0; i < 4; i++)
            psx_mod_write_byte(S0 + B_MOVES + i, psx_mod_read_byte(S0 + P_MOVES + i));
        remap_moves(e, type, to);
        psx_mod_write_word(S0 + P_TYPE, to);
    }
    if (to5) {
        psx_mod_write_byte(S0 + B_PSTAT5, pstat5);
        psx_mod_write_byte(S0 + P_PSTAT5, to5);
    }
    if (to4) {
        psx_mod_write_byte(S0 + B_PSTAT4, pstat4);
        psx_mod_write_byte(S0 + P_PSTAT4, to4);
    }
    psx_mod_write_word(S0 + B_HASH, payload_hash(S0));
    psx_mod_counter_add("dw.roster.saves.remapped", 1);
}

/* Both copies: the save machine copies saves[1] over saves[0] before its next save (main_menu.c:4211). */
static void after_write(struct CPUState* cpu) {
    if (cpu->gpr[31] != DW_RA_SLEEP_SYNC && cpu->gpr[31] != DW_RA_MACHINE_SYNC)
        return;
    const Edition* e = g_pending;
    g_pending = 0;
    if (!e)                                                  /* no roster save ran: the roster is off */
        return;
    if (has_block(S0))
        restore_regs(e, S0);
    if (has_block(S1))
        restore_regs(e, S1);
    restore_regs_tagged(e, S0 + P_REGS);
    restore_regs_tagged(e, S1 + P_REGS);
}

static void restore(const Edition* e, uint32_t p) {
    uint8_t flags = psx_mod_read_byte(p + B_FLAGS);
    if (foreign(e, psx_mod_read_byte(p + B_TYPE)))
        flags &= (uint8_t)~F_PARTNER;
    if (foreign(e, psx_mod_read_byte(p + B_PSTAT5)))
        flags &= (uint8_t)~F_PSTAT5;
    if (foreign(e, psx_mod_read_byte(p + B_PSTAT4)))
        flags &= (uint8_t)~F_PSTAT4;
    if (flags & F_PARTNER) {
        psx_mod_write_word(p + P_TYPE, psx_mod_read_byte(p + B_TYPE));
        for (uint32_t i = 0; i < 4; i++)
            psx_mod_write_byte(p + P_MOVES + i, psx_mod_read_byte(p + B_MOVES + i));
    }
    if (flags & F_PSTAT5)
        psx_mod_write_byte(p + P_PSTAT5, psx_mod_read_byte(p + B_PSTAT5));
    if (flags & F_PSTAT4)
        psx_mod_write_byte(p + P_PSTAT4, psx_mod_read_byte(p + B_PSTAT4));
    restore_regs(e, p);
}

static void load(const Edition* e, struct CPUState* cpu) {
    const uint32_t p = cpu->gpr[4];
    /* Both blocks are judged on the payloads as read, before anything changes them; registrations stand alone. */
    const int blk = has_block(p), valid = blk && valid_block(p);
    const uint32_t other = p == S0 ? S1 : p == S1 ? S0 : 0;
    const int other_valid = other && valid_block(other);
    const uint32_t n = restore_regs_tagged(e, p + P_REGS) + (other ? restore_regs_tagged(e, other + P_REGS) : 0);
    if (n)
        psx_mod_counter_add("dw.roster.saves.regs_restored", n);
    if (!blk)
        return;
    if (!valid) {                                            /* saved again without the roster since */
        psx_mod_counter_add("dw.roster.saves.stale", 1);
        return;
    }
    if (other_valid)                                         /* judged before p changes, which it may equal */
        restore(e, other);
    restore(e, p);
    psx_mod_counter_add("dw.roster.saves.restored", 1);
}

/* countRegisteredDigimon(a0 = VS__REGISTERED_DIGIMON_BUFFER + player * 40 records), called right after Versus mode
 * copied the card's registrations there from whichever copy passed its checksum. */
static void versus(const Edition* e, struct CPUState* cpu) {
    const uint32_t buf = cpu->gpr[4];
    const uint32_t copies[2] = {S0, S1};
    if (restore_regs_tagged(e, buf)) {                          /* tagged records carry their own proof */
        psx_mod_counter_add("dw.roster.saves.versus", 1);
        return;
    }
    for (uint32_t c = 0; c < 2 && e->stock; c++) {          /* the 114 mask: stock edition only */
        uint32_t p = copies[c], o = 0;
        if (!valid_block(p))
            continue;
        while (o < 0xA00 && psx_mod_read_byte(buf + o) == psx_mod_read_byte(p + P_REGS + o))
            o++;
        if (o < 0xA00)
            continue;
        for (uint32_t i = 0; i < 40; i++)
            if (psx_mod_read_byte(p + B_REGS + i / 8) >> (i % 8) & 1)
                psx_mod_write_byte(buf + i * 0x40 + 0x1C, 114);
        psx_mod_counter_add("dw.roster.saves.versus", 1);
        return;
    }
}

/* A hook takes no argument and a plugin id holds one hook per address, so each edition has its own entries. */
static void save_stock(struct CPUState* cpu, uint32_t address) { (void)address; save(&k_stock, cpu); }
static void save_dw15(struct CPUState* cpu, uint32_t address) { (void)address; save(&k_dw15, cpu); }
static void load_stock(struct CPUState* cpu, uint32_t address) { (void)address; load(&k_stock, cpu); }
static void load_dw15(struct CPUState* cpu, uint32_t address) { (void)address; load(&k_dw15, cpu); }
static void sync_any(struct CPUState* cpu, uint32_t address) { (void)address; after_write(cpu); }
static void versus_stock(struct CPUState* cpu, uint32_t address) { (void)address; versus(&k_stock, cpu); }
static void versus_dw15(struct CPUState* cpu, uint32_t address) { (void)address; versus(&k_dw15, cpu); }

PSX_MOD_CONSTRUCTOR(dw_roster_saves_register) {
    psx_mod_register_function_entry_plugin("dw.roster", DW_CHECKSUM, save_stock);
    psx_mod_register_function_entry_plugin("dw.roster", DW_LOAD_SAVEGAME, load_stock);
    /* One exit registration serves both editions: exit hooks fire for every plan, and after_write acts only after
     * a roster save hook of either edition ran (g_pending). */
    psx_mod_register_function_exit_plugin("dw.roster", DW_MEMCARD_SYNC, sync_any);
    psx_mod_register_function_entry_plugin("dw.roster", DW_COUNT_REGS, versus_stock);
    psx_mod_register_function_entry_plugin("dw.roster15", DW_CHECKSUM, save_dw15);
    psx_mod_register_function_entry_plugin("dw.roster15", DW_LOAD_SAVEGAME, load_dw15);
    psx_mod_register_function_entry_plugin("dw.roster15", DW_COUNT_REGS, versus_dw15);
}
