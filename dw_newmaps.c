/* dw.newmaps -- no memory card ever names a new map.
 *
 * A save is a screen, an exit and a position. With this mod off, row 239 is empty
 * and a card naming it does not load, and a save made next door can name it too:
 * previousScreen is where fleeing a battle goes. So a save made on the new map
 * resumes just outside its entrance, with the mod on or off, and the player keeps
 * one set of cards. (A separate card folder, as dw_dw15.c has, would hide their
 * existing run behind an optional side area.)
 *
 * A poop pile dropped on the new map also saves its map (partner_impl.c:1283,
 * main_menu.c:5108). The card gets a free slot in its place: the pile stays in play,
 * and is simply not there after Continue.
 *
 * Every save of a game in play reaches the card through writeSavegame (two callers,
 * both in tickMainMenu) and is summed right after by createSavegameChecksum. This hook
 * runs on that call, so the rewritten bytes are what get summed, copied to saves[1] and
 * written. The live CURRENT_SCREEN and position are not touched: play goes on where
 * it is. The load paths also sum, and so does new-file creation (menu 0x11), which
 * writes the default payload without writeSavegame, hence the return-address filter.
 *
 * Entry hooks are selected by the plan ([[plugin]] in the manifest), so this is inert
 * with the feature off. It holds no guest state, so savestates and rewind have nothing to do.
 */
#include <stdint.h>

#include "cpu_state.h"
#include "dw_newmaps_files.h"
#include "mod_plugins.h"

#define DW_CHECKSUM   0x80112D34u   /* createSavegameChecksum */
#define DW_RA_SLEEP   0x801114A4u   /* its call in menu 0x2B: the sleep save */
#define DW_RA_MACHINE 0x80112060u   /* its call in menu 0x41: save machine, Save Anywhere, game clear */
#define S0            0x80131B2Cu   /* SAVE_FILE.saves[0] */
#define DW_MAP_WARPS  0x80138730u   /* i16 spawnX[10], Y, Z, rotation, targetMap, targetExit */

/* One entry per map package: its rows, the host screen, the warp slot the host's new exit takes, and the
 * stock exit a card names instead (the glade's host slot 4 has an empty spawn on the stock map). The generators assert their own row against this table. */
typedef struct { const char* plugin; uint8_t first_row, last_row, host_row, host_exit, stock_exit; } DwMap;
static const DwMap k_maps[] = {
    {"dw.newmaps",         239, 239,  2, 4, 0},
    {"dw.newmaps.factory", 240, 243, 78, 1, 0},
};

#define SCREEN      (S0 + 0x49A)    /* currentScreen */
#define PREV_SCREEN (S0 + 0x49B)
#define EXIT        (S0 + 0x49C)    /* currentExit */
#define PREV_EXIT   (S0 + 0x49D)
#define POOP        (S0 + 0x24)     /* worldPoop[100]: u8 map, x, y; i8 size */
#define POOP_FREE   0x00FFFFFFu     /* what an eaten pile and the save default hold */

/* Slot 1 of the new map's own warp table says where a save resumes. No collision
 * cell is 111, so it never fires as a warp. gen_newmap.py writes it. */
static int32_t resume(uint32_t table) { return (int16_t)psx_mod_read_half(DW_MAP_WARPS + table * 20 + 2); }

static int8_t tile_x(int32_t x) { return (int8_t)(x / 100 + 50 - (x < 0)); }   /* getModelTile */
static int8_t tile_y(int32_t z) { return (int8_t)(50 - z / 100 - (z > 0)); }

static void move_save_to_host(void) {
    int32_t x = resume(0), y = resume(1), z = resume(2), rot = resume(3), px = x, pz = z;

    psx_mod_write_byte(SCREEN, (uint8_t)resume(4));
    psx_mod_write_byte(EXIT, (uint8_t)resume(5));

    /* The partner lands where a warp would put it (tamer.c loadMapEntities). */
    if (rot <= 0x200 || rot > 0xE00) pz += 200;
    else if (rot <= 0x600) px += 200;
    else if (rot <= 0xA00) pz -= 200;
    else px -= 200;
    psx_mod_write_word(S0 + 0x04, (uint32_t)x);     /* tamerPosition */
    psx_mod_write_word(S0 + 0x08, (uint32_t)y);
    psx_mod_write_word(S0 + 0x0C, (uint32_t)z);
    psx_mod_write_word(S0 + 0x14, (uint32_t)px);    /* partnerPosition */
    psx_mod_write_word(S0 + 0x18, (uint32_t)y);
    psx_mod_write_word(S0 + 0x1C, (uint32_t)pz);

    /* The waypoint bytes are tiles of the map the save names, and Continue restores them. */
    psx_mod_write_byte(S0 + 0x4DA, (uint8_t)tile_x(x));   /* tamerPreviousTileX, Y */
    psx_mod_write_byte(S0 + 0x4DB, (uint8_t)tile_y(z));
    psx_mod_write_byte(S0 + 0x4DC, 0);                    /* tamerWaypointCurrent, Count */
    psx_mod_write_byte(S0 + 0x4DD, 0);
    psx_mod_write_byte(S0 + 0x4DE, (uint8_t)tile_x(x));   /* tamerStartTileX, Y */
    psx_mod_write_byte(S0 + 0x4DF, (uint8_t)tile_y(z));
    psx_mod_write_byte(S0 + 0x4E0, 0);                    /* tamerWaypointActive */
}

static int in_map(const DwMap* m, uint8_t row) { return row >= m->first_row && row <= m->last_row; }

static void remap(const DwMap* m, struct CPUState* cpu) {
    if (cpu->gpr[31] != DW_RA_SLEEP && cpu->gpr[31] != DW_RA_MACHINE)
        return;

    for (uint32_t i = 0; i < 100; i++)
        if (in_map(m, psx_mod_read_byte(POOP + i * 4)))
            psx_mod_write_word(POOP + i * 4, POOP_FREE);
    if (in_map(m, psx_mod_read_byte(SCREEN)))
        move_save_to_host();
    if (in_map(m, psx_mod_read_byte(PREV_SCREEN))) {     /* walked out, then saved next door */
        psx_mod_write_byte(PREV_SCREEN, psx_mod_read_byte(SCREEN));
        psx_mod_write_byte(PREV_EXIT, psx_mod_read_byte(EXIT));
    }
    if (psx_mod_read_byte(SCREEN) == m->host_row && psx_mod_read_byte(EXIT) == m->host_exit)
        psx_mod_write_byte(EXIT, m->stock_exit);
    if (psx_mod_read_byte(PREV_SCREEN) == m->host_row && psx_mod_read_byte(PREV_EXIT) == m->host_exit)
        psx_mod_write_byte(PREV_EXIT, m->stock_exit);
}

/* A hook takes no argument and a plugin id holds one hook per address, so each map has its own entry. */
static void dw_newmaps_pre_checksum(struct CPUState* cpu, uint32_t address) { (void)address; remap(&k_maps[0], cpu); }
static void dw_newmaps_factory_pre_checksum(struct CPUState* cpu, uint32_t address) { (void)address; remap(&k_maps[1], cpu); }

/* lookupFileTable(FileLookup* a0, char* a1): answer for the names dw_newmaps_files.inc lists,
 * and decline every other name so the stock body runs. FileLookup is { CdlLOC pos; u32 size }.
 *
 * A filter hook (listed in game.toml mod_function_entry_funcs) runs only for plugins in the
 * plan, and on interpreted calls too (dirty_ram_interp.c), which matters where the 1.5 mod
 * rewrites this function. Nonzero consumes the call: the runtime then sets pc = $ra.
 *
 * Only one feature may claim a plugin id, so each map package that serves files has its own
 * id; list it in DW_FILE_PLUGINS. The glade serves no new names and has no entry here. */
#define DW_LOOKUP_FILE_TABLE 0x800A31F8u
static const char* const DW_FILE_PLUGINS[] = { "dw.newmaps.factory" };

static int dw_newmaps_lookup(struct CPUState* cpu, uint32_t address) {
    char buf[65];
    uint32_t lba, size;
    (void)address;
    if (!dw_newmaps_file(dw_newmaps_basename(cpu->gpr[5], buf), &lba, &size))
        return 0;
    psx_mod_write_word(cpu->gpr[4] + 4, size);
    psx_mod_write_word(cpu->gpr[4], dw_newmaps_cdloc(lba));
    cpu->gpr[2] = 1;
    return 1;
}

/* getScript(u16 a0) (script_interp.c:647-668): serve the script ids dw_newmaps_scripts.inc lists from MMM.DAT,
 * so no DG.SCN table row is written (1.5 holds rows 222-225 itself and its DG.SCN lies past MMM.DAT). As the
 * stock body does: an id already active is the buffer's (decline, the stock body returns it), else read the
 * script into the buffer MAP_SCRIPT_PTR names and make it active. Decline every other id. A failed read fails
 * closed: the stock body would read DG.SCN's table past its end (rows 227+ are not real) and could overrun the
 * 0x4000 B buffer, so the call is consumed and returns the buffer holding an empty section table (u16 first-body
 * offset, then the 0xFFFF end entry: getScriptSection, :671-688, returns 0 for every section), so the previous
 * screen's script never runs here. The active id stays unchanged, so the next call reads again. */
#define DW_GET_SCRIPT       0x80106218u
#define DW_ACTIVE_MAP_SCRIPT 0x80134FC2u   /* u16 */
#define DW_MAP_SCRIPT_PTR    0x80134FB4u   /* u8* */
typedef struct { uint16_t id; uint32_t lba, size; } DwScript;   /* size: whole sectors, within the 0x4000 B buffer */
static const DwScript k_scripts[] = {
#include "dw_newmaps_scripts.inc"
};

static int dw_newmaps_get_script(struct CPUState* cpu, uint32_t address) {
    uint8_t buf[0x4000];
    const uint16_t id = (uint16_t)cpu->gpr[4];
    (void)address;
    for (uint32_t i = 0; i < sizeof k_scripts / sizeof *k_scripts; i++) {
        const DwScript* s = &k_scripts[i];
        if (s->id != id)
            continue;
        if (id == psx_mod_read_half(DW_ACTIVE_MAP_SCRIPT))
            return 0;
        const uint32_t dst = psx_mod_read_word(DW_MAP_SCRIPT_PTR);
        cpu->gpr[2] = dst;
        if (s->size > sizeof buf || !psx_mod_read_disc_sectors(s->lba, s->size / 2048, buf)) {
            psx_mod_write_word(dst, 0xFFFF0004u);   /* 04 00 FF FF: an empty section table, getScriptSection -> 0 */
            return 1;
        }
        for (uint32_t o = 0; o < s->size; o += 4)
            psx_mod_write_word(dst + o, (uint32_t)buf[o] | buf[o + 1] << 8 | buf[o + 2] << 16 | (uint32_t)buf[o + 3] << 24);
        psx_mod_write_half(DW_ACTIVE_MAP_SCRIPT, id);
        return 1;
    }
    return 0;
}

PSX_MOD_CONSTRUCTOR(dw_newmaps_register) {
    for (uint32_t i = 0; i < sizeof DW_FILE_PLUGINS / sizeof *DW_FILE_PLUGINS; i++)
        psx_mod_register_function_filter_plugin(DW_FILE_PLUGINS[i], DW_LOOKUP_FILE_TABLE, dw_newmaps_lookup);
    psx_mod_register_function_filter_plugin("dw.newmaps.factory", DW_GET_SCRIPT, dw_newmaps_get_script);
    psx_mod_register_function_entry_plugin(k_maps[0].plugin, DW_CHECKSUM, dw_newmaps_pre_checksum);
    psx_mod_register_function_entry_plugin(k_maps[1].plugin, DW_CHECKSUM, dw_newmaps_factory_pre_checksum);
}
