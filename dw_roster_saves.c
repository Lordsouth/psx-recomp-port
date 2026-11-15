/* dw.roster -- memory cards that load with the Expanded Roster on or off.
 *
 * A card names its partner by type. With the roster off, a type 63-114 indexes past the end of every stock
 * partner-line table (RAISE_DATA has 66 rows, EVO_PATHS 62, ...), and 114 has no model at all. So a card never
 * names a roster species: it names a stock stand-in of the same level (k_save_stand_in, chosen by
 * tools/gen_roster_mod.py save_stand_ins), and keeps the true species in bytes the stock game neither reads nor
 * writes. With the roster on, Continue puts it back.
 *
 * WHERE. SavegamePayload.unknown4E1 (+0x4E1..+0x4FB, 27 bytes, docs/GAME.md 9.1): writeSavegame does not write it,
 * loadSavegame does not read it, and nothing else in the stock code names it (main_menu.c:135, :5016-5134). A stock
 * save therefore carries it over unchanged from the card it loaded (saves[1] = saves[0] copies it whole).
 *
 *   +4E1 'R' 'S' 1       magic, version
 *   +4E4 u32             FNV-1a of the stock-visible payload as this hook wrote it (see covered())
 *   +4E8 u8              the true partner type, or 0 when the partner was not remapped
 *   +4E9 u8[4]           its equipped move slots (Stats.base.moves)
 *   +4ED u8              the true pstat 5, or 0 when it was not remapped
 *
 * THE RULE. Continue with the roster on restores exactly when the card's stock-visible payload hashes to the stored
 * value, i.e. it is byte for byte what this hook wrote. A stock save after that rewrites the payload from the live
 * game (playtime, clock, the partner...), so the hash no longer matches and the stock partner the player then had
 * stays; the stale block is never applied, and the next roster save overwrites or clears it. If the payload is
 * unchanged, restoring is exactly right, whatever happened in between. The location fields dw_newmaps.c rewrites at
 * the same checksum call (positions, poop piles, screens, waypoint tail) are left out of the hash, so the order of
 * the two entry hooks does not matter.
 *
 * WHAT IS REMAPPED, and only for a roster partner:
 *   - partnerType (+1B8) to the stand-in;
 *   - Stats.base.moves (+27C): a slot indexes the species' own move list (DIGIMON_DATA.moves[slot - 0x2E],
 *     src/evl/evl.c:1450-1476), so each equipped move is looked up by move id in the stand-in's list; moves it lacks
 *     are dropped (the rest move up), an empty set gets what EVL would give a fresh stand-in (the lowest-power move of
 *     its first special group), and the finisher slot takes the stand-in's own finisher if it lacks the roster one;
 *   - pstat 5 (+3EE) when the partner is Sukamon: it holds the type routine 2 turns Sukamon back into
 *     (docs/GAME.md 8.9), which would bring the roster type back with the roster off.
 * Everything else (stats, PartnerPara, learned moves, name, lives, the raised flags dw_roster_flags.c banks at
 * trigger 0x2CD+) is generic data the stock game reads correctly or never reads, and is left alone.
 * A Battle Mode registration (+500, 40 records, main_menu.c:5230-5245) of a 114 partner is written as 115, the
 * stock Machinedramon whose record 114 copies and whose model 114 draws: stock has no model for 114. It is not
 * restored; it only differs in the attribute byte the roster sets on 114 (Virus).
 *
 * WHEN. Save: an entry hook on createSavegameChecksum filtered to the two save calls (as dw_newmaps.c), so the
 * rewritten bytes are summed, copied to saves[1] and written, and play goes on with the live roster partner. Load:
 * an entry hook on loadSavegame (0x80112D84, a0 = the payload), after the caller has verified the checksum and before
 * any field is copied out; it rewrites the payload in SAVE_FILE, which every later save rewrites from the live game.
 * No guest or host state is held, so savestates and Rewind have nothing to do. The plugin is dw.roster, so all of
 * this runs only with the roster's feature; Digimon World 1.5 runs roster-15 in its place, whose species have rows
 * in 1.5's own tables (docs/INTERNALS.md 4.1 "Saves").
 */
#include <stdint.h>

#include "cpu_state.h"
#include "mod_plugins.h"

#include "dw_roster_saves.inc"

#define DW_CHECKSUM      0x80112D34u   /* createSavegameChecksum */
#define DW_LOAD_SAVEGAME 0x80112D84u   /* loadSavegame(SavegamePayload*) */
#define DW_RA_SLEEP      0x801114A4u   /* its call in menu 0x2B: the sleep save */
#define DW_RA_MACHINE    0x80112060u   /* its call in menu 0x41: save machine, Save Anywhere, game clear */
#define S0               0x80131B2Cu   /* SAVE_FILE.saves[0] */
#define DIGIMON_DATA     0x8012CEB4u   /* 52 B each: special[3] +30, moves[16] +35 */
#define MOVE_DATA        0x8012623Cu   /* 16 B each: power i16 +4 */

#define P_TYPE   0x1B8u
#define P_MOVES  0x27Cu                /* Stats (+270) .base.moves[4] */
#define P_PSTAT5 0x3EEu                /* scriptState (+290): smth[212], cards[33], triggers[100], then pstats */
#define P_REGS   0x500u                /* RegisteredDigimon[40], 0x40 each, digimonId +0x1C */
#define P_SPARE  0x4E1u
#define N_SPARE  27u
#define SUKAMON  0x27u
#define SLOT0    0x2Eu                 /* a move slot value is 0x2E + index into DIGIMON_DATA.moves */

static uint8_t stand_in(uint32_t type) {
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

static uint8_t move_id(uint32_t type, uint8_t slot) {
    return (slot >= SLOT0 && slot < SLOT0 + 16) ? psx_mod_read_byte(DIGIMON_DATA + type * 52 + 35 + slot - SLOT0) : 0xFF;
}

static uint8_t slot_of(uint32_t type, uint8_t id) {
    if (id == 0xFF)
        return 0xFF;
    for (uint8_t i = 0; i < 16; i++)
        if (psx_mod_read_byte(DIGIMON_DATA + type * 52 + 35 + i) == id)
            return SLOT0 + i;
    return 0xFF;
}

/* EVL_applyEvolution's starting move (src/evl/evl.c:1418-1455) and finisher (:1461-1476) for `type`. */
static uint8_t fresh_move(uint32_t type) {
    static const uint8_t base[7] = {0, 40, 8, 32, 16, 24, 49};
    uint8_t special = psx_mod_read_byte(DIGIMON_DATA + type * 52 + 30), lo = special < 7 ? base[special] : 0;
    uint8_t best = 0xFF;
    for (uint8_t i = 0; i < 16; i++) {
        uint8_t m = psx_mod_read_byte(DIGIMON_DATA + type * 52 + 35 + i);
        if (m < lo || m > lo + 8)
            continue;
        int16_t pm = (int16_t)psx_mod_read_half(MOVE_DATA + m * 16u + 4);
        if (best == 0xFF || (pm != 0 && (int16_t)psx_mod_read_half(MOVE_DATA + best * 16u + 4) > pm))
            best = m;
    }
    return slot_of(type, best);
}

static uint8_t fresh_finisher(uint32_t type) {
    for (int i = 15; i >= 0; i--) {
        uint8_t m = psx_mod_read_byte(DIGIMON_DATA + type * 52 + 35 + (uint32_t)i);
        if (m != 0xFF && m >= 0x3A && m < 0x71)
            return SLOT0 + (uint8_t)i;
    }
    return 0xFF;
}

static void remap_moves(uint32_t from, uint32_t to) {
    uint8_t out[4] = {0xFF, 0xFF, 0xFF, 0xFF}, n = 0;
    for (uint32_t i = 0; i < 3; i++) {
        uint8_t s = slot_of(to, move_id(from, psx_mod_read_byte(S0 + P_MOVES + i)));
        if (s != 0xFF)
            out[n++] = s;
    }
    if (n == 0)
        out[0] = fresh_move(to);
    out[3] = slot_of(to, move_id(from, psx_mod_read_byte(S0 + P_MOVES + 3)));
    if (out[3] == 0xFF || out[3] < SLOT0 || move_id(to, out[3]) < 0x3A || move_id(to, out[3]) >= 0x71)
        out[3] = fresh_finisher(to);
    for (uint32_t i = 0; i < 4; i++)
        psx_mod_write_byte(S0 + P_MOVES + i, out[i]);
}

static int has_block(uint32_t p) {
    return psx_mod_read_byte(p + P_SPARE) == 'R' && psx_mod_read_byte(p + P_SPARE + 1) == 'S'
        && psx_mod_read_byte(p + P_SPARE + 2) == 1;
}

static void save_hook(struct CPUState* cpu, uint32_t address) {
    (void)address;
    if (cpu->gpr[31] != DW_RA_SLEEP && cpu->gpr[31] != DW_RA_MACHINE)
        return;
    for (uint32_t i = 0; i < 40; i++)
        if (psx_mod_read_byte(S0 + P_REGS + i * 0x40 + 0x1C) == 114)
            psx_mod_write_byte(S0 + P_REGS + i * 0x40 + 0x1C, 115);

    uint32_t type = psx_mod_read_word(S0 + P_TYPE);
    uint8_t pstat5 = psx_mod_read_byte(S0 + P_PSTAT5);
    uint8_t to = stand_in(type), to5 = type == SUKAMON ? stand_in(pstat5) : 0;
    if (!to && !to5) {
        if (has_block(S0))                       /* a stock partner now: nothing to bring back */
            for (uint32_t i = 0; i < N_SPARE; i++)
                psx_mod_write_byte(S0 + P_SPARE + i, 0);
        return;
    }
    for (uint32_t i = 0; i < N_SPARE; i++)
        psx_mod_write_byte(S0 + P_SPARE + i, 0);
    psx_mod_write_byte(S0 + P_SPARE + 0, 'R');
    psx_mod_write_byte(S0 + P_SPARE + 1, 'S');
    psx_mod_write_byte(S0 + P_SPARE + 2, 1);
    if (to) {
        psx_mod_write_byte(S0 + 0x4E8, (uint8_t)type);
        for (uint32_t i = 0; i < 4; i++)
            psx_mod_write_byte(S0 + 0x4E9 + i, psx_mod_read_byte(S0 + P_MOVES + i));
        remap_moves(type, to);
        psx_mod_write_word(S0 + P_TYPE, to);
    }
    if (to5) {
        psx_mod_write_byte(S0 + 0x4ED, pstat5);
        psx_mod_write_byte(S0 + P_PSTAT5, to5);
    }
    psx_mod_write_word(S0 + 0x4E4, payload_hash(S0));
    psx_mod_counter_add("dw.roster.saves.remapped", 1);
}

static void load_hook(struct CPUState* cpu, uint32_t address) {
    const uint32_t p = cpu->gpr[4];
    (void)address;
    if (!has_block(p))
        return;
    if (psx_mod_read_word(p + 0x4E4) != payload_hash(p)) {   /* saved again without the roster since */
        psx_mod_counter_add("dw.roster.saves.stale", 1);
        return;
    }
    uint8_t type = psx_mod_read_byte(p + 0x4E8), pstat5 = psx_mod_read_byte(p + 0x4ED);
    if (type) {
        psx_mod_write_word(p + P_TYPE, type);
        for (uint32_t i = 0; i < 4; i++)
            psx_mod_write_byte(p + P_MOVES + i, psx_mod_read_byte(p + 0x4E9 + i));
    }
    if (pstat5)
        psx_mod_write_byte(p + P_PSTAT5, pstat5);
    psx_mod_counter_add("dw.roster.saves.restored", 1);
}

PSX_MOD_CONSTRUCTOR(dw_roster_saves_register) {
    psx_mod_register_function_entry_plugin("dw.roster", DW_CHECKSUM, save_hook);
    psx_mod_register_function_entry_plugin("dw.roster", DW_LOAD_SAVEGAME, load_hook);
}
