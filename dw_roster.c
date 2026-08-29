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
 *   1. This plugin reserves a 16 KB arena in the framework's Expansion-1
 *      enhancement region at activation. The first allocation is
 *      deterministically 0x9F000000, which is why the generated manifest
 *      (tools/gen_roster_mod.py) can hard-code retargeted addresses.
 *   2. The manifest's guarded main_exe patches rewrite every lui/lo16 pair
 *      that materializes a grown table's address (site list measured by the
 *      reference census, research/roster-xref-census.md) to the arena copy.
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
 * SAVESTATES. The arena is not serialized, but its content is static per
 * process (refilled at every boot); a state save/load cannot observe a
 * mismatch. Expansion-1 reads are wired into every generated load path.
 */
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "mod_plugins.h"

/* ---- arena layout (mirrored in tools/gen_roster_mod.py) ---------------- */
#define ROSTER_ARENA_SIZE 0x4000u
#define ROSTER_ARENA_BASE 0x9F000000u
#define ROSTER_N_TYPES 180u

#define OFF_EVO_REQ 0x0000u  /* 180 * 28 = 0x13B0 */
#define OFF_RAISE 0x1400u    /* 180 * 28 = 0x13B0 */
#define OFF_EVO_GAINS 0x2800u /* 180 * 14 = 0x9D8 */
#define OFF_ITEM_TAKE 0x3200u /* 180 * 1  */

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

/* Feature-detection: the first EVO_REQ retarget site's lui. When the
 * manifest applied, its immediate reads 0x9F00. (Site from the census;
 * gen_roster_mod.py asserts this stays the first site.) */
#define DETECT_LUI_VADDR ROSTER_DETECT_LUI_VADDR

/* ---- authored type-114 entries (values researched from the game's own
 * tables; see research/roster-* docs). Filled by data below. -------------- */
#include "dw_roster_data.inc"

static uint32_t roster_arena = 0;

static void roster_fill(void) {
    uint32_t i;
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
    psx_mod_write_byte(roster_arena + OFF_ITEM_TAKE + 114u, k_item_take_114);
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
static void roster_vblank(void) {
    if (!roster_filled && psx_mod_game_started())
        roster_ensure_filled();
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

PSX_MOD_CONSTRUCTOR(dw_roster_register) {
    psx_mod_register_activation_plugin("dw.roster", roster_activate);
    psx_mod_register_function_entry_plugin("dw.roster", ROSTER_HOOK_VADDR,
                                           roster_hook);
    psx_mod_register_vblank_plugin("dw.roster", roster_vblank);
    psx_mod_register_function_entry_plugin("dw.roster", ROSTER_LOADMMD_VADDR,
                                           roster_remap_hook);
    psx_mod_register_function_entry_plugin("dw.roster",
                                           ROSTER_LOADMMDASYNC_VADDR,
                                           roster_remap_hook);
}
