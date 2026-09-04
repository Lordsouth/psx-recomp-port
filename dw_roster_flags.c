/* Roster fix: give the new species their own trigger flags instead of
 * borrowing the game's story flags.
 *
 * THE BUG THIS REPLACES. Since 0.3.0 the roster mod widened one immediate:
 *
 *     setDigimonRaised  @0x800FF7F8
 *         800FF7FC  sltiu $at,$a0,0x3F      ->  0xB4      (63 -> 180 species)
 *         800FF808  addi  $v0,$a0,0x200               trigger = species + 0x200
 *
 * so raising a new species recorded trigger id `species + 0x200`. Vanilla only
 * ever writes 0x200..0x23E, and the id space directly above that is NOT free:
 * 0x23C + species is the "Jijimon announced that X joined the city" family, and
 * 0x28A upward are treasure-chest "already opened" flags. Decoding the shipped
 * bytecode in DG.SCN and MAPHEAD.SCN (tools/scan_trigger_ids.py, 1834 sections,
 * zero decode errors) finds 88 of the 117 widened ids referenced by disc
 * scripts, 45 of them inside the range this roster's 52 species actually use.
 *
 * Trigger flags live in ScriptState.triggers[100] and go to the memory card
 * (writeSavegame memcpys the whole struct), so the aliasing is persistent and
 * runs both ways:
 *
 *   - Raise Panjyamon (63) and the game marks Agumon's recruitment
 *     announcement as already seen. The player never gets that scene.
 *   - See a recruitment announcement and hasDigimonRaised(63) starts returning
 *     true, which is not cosmetic: evolution.c:401 uses it to score evolution
 *     targets, zeroing the requirement points of anything "already raised". So
 *     ordinary story progress quietly biases which Digimon the partner becomes.
 *
 * THE FIX. Both functions are tiny, and the framework can replace a guest
 * function outright, so the mapping moves into C where it can be an actual
 * mapping rather than one immediate:
 *
 *     species <  63          ->  0x200 + species        (stock, unchanged)
 *     63 <= species < 146    ->  0x2CD + (species - 63)  (a bank nothing uses)
 *
 * 0x2CD is where the scan's highest referenced id (0x2CC) ends, and
 * triggers[100] runs to id 799 (0x31F), so 0x2CD..0x31F is 83 ids of genuinely
 * unclaimed space -- enough for the 52 species here with room to grow. The
 * bound is expressed in terms of that space rather than the species count, so
 * adding species cannot silently walk off the end of the array: past 145 the
 * flag is simply not recorded, exactly as vanilla ignores species past 62.
 *
 * The bit arithmetic is transcribed from the guest's own helper at 0x80106CA8,
 * which both setTrigger and isTriggerSet call:
 *
 *     byte = *SCRIPT_STATE_PTR + 0xF5 + (id >> 3)
 *     mask = 1 << (id & 7)
 *
 * GATING. This must be inert unless the roster feature is actually in the
 * plan, or a vanilla player's save would use a different flag layout from the
 * one the game writes. Function replacements fire regardless of the mod plan
 * -- only activation and vblank callbacks are plan-selected -- so the gate is
 * explicit: the roster's own retarget site is read back, and unless it carries
 * the arena's high half-word the callback returns 0 and the original
 * recompiled body runs, untouched.
 *
 * NOT A CODE PATCH. The manifest no longer widens 0x800FF7FC, and it must not:
 * patching a function's bytes marks its range executable-dirty, dispatch then
 * refuses the compiled body, and the replacement hook -- which lives inside
 * that body -- would never run.
 *
 * KNOWN MIGRATION WRINKLE, worth stating plainly. A save made before this fix
 * has story flags already set by the old aliasing. This does not clear them:
 * those announcements stay skipped, and the species raised under the old
 * scheme read as un-raised now. Nothing is corrupted, but an existing roster
 * save does not gain back what the old scheme consumed.
 */
#include "cpu_state.h"
#include "mod_plugins.h"

#define DW_SET_DIGIMON_RAISED 0x800FF7F8u
#define DW_HAS_DIGIMON_RAISED 0x800FF824u

#define DW_SCRIPT_STATE_PTR 0x80134FB8u   /* pointer, not the struct */
#define DW_TRIGGERS_OFFSET 0xF5u          /* helper @0x80106CE0: addiu v0,v0,245 */

#define DW_STOCK_SPECIES 63u              /* vanilla sltiu bound */
#define DW_STOCK_BASE 0x200u
#define DW_BANK_BASE 0x2CDu               /* first id no disc script references */
#define DW_TRIGGER_MAX 799u               /* triggers[100] = 800 bits */
#define DW_BANK_SPECIES_MAX \
    (DW_STOCK_SPECIES + (DW_TRIGGER_MAX - DW_BANK_BASE) + 1u)   /* 146 */

/* dw_roster.c's feature-detection site: the first EVO_REQ retarget lui, whose
 * immediate reads 0x9F00 once the roster manifest has applied. */
#define DW_ROSTER_DETECT_LUI 0x800E2610u
#define DW_ROSTER_ARENA_HI 0x9F00u

#define DW_RAM_BASE 0x80000000u
#define DW_RAM_SIZE 0x00200000u

static int dw_roster_active(void) {
    return psx_mod_read_half(DW_ROSTER_DETECT_LUI) == DW_ROSTER_ARENA_HI;
}

/* Trigger id for a species, or -1 when the species has no flag. */
static int32_t dw_raised_trigger(uint32_t species) {
    if (species < DW_STOCK_SPECIES)
        return (int32_t)(DW_STOCK_BASE + species);
    if (species < DW_BANK_SPECIES_MAX)
        return (int32_t)(DW_BANK_BASE + (species - DW_STOCK_SPECIES));
    return -1;
}

/* Byte address of a trigger bit, or 0 if the script state is not live yet. */
static uint32_t dw_trigger_byte(int32_t id, uint8_t* mask) {
    const uint32_t state = psx_mod_read_word(DW_SCRIPT_STATE_PTR);
    uint32_t addr;
    if (id < 0 || (uint32_t)id > DW_TRIGGER_MAX)
        return 0;
    if (state < DW_RAM_BASE || state - DW_RAM_BASE >= DW_RAM_SIZE)
        return 0;
    addr = state + DW_TRIGGERS_OFFSET + ((uint32_t)id >> 3);
    if (addr < DW_RAM_BASE || addr - DW_RAM_BASE >= DW_RAM_SIZE)
        return 0;
    *mask = (uint8_t)(1u << ((uint32_t)id & 7u));
    return addr;
}

static int dw_set_digimon_raised(struct CPUState* cpu, uint32_t address) {
    int32_t id;
    uint32_t addr;
    uint8_t mask = 0;
    (void)address;
    if (!dw_roster_active())
        return 0;                          /* vanilla: run the stock body */
    id = dw_raised_trigger(cpu->gpr[4]);   /* $a0 = species */
    addr = dw_trigger_byte(id, &mask);
    if (addr)
        psx_mod_write_byte(addr, (uint8_t)(psx_mod_read_byte(addr) | mask));
    cpu->gpr[2] = 0;                       /* void in the decompilation */
    cpu->pc = cpu->gpr[31];
    return 1;
}

static int dw_has_digimon_raised(struct CPUState* cpu, uint32_t address) {
    int32_t id;
    uint32_t addr;
    uint8_t mask = 0;
    (void)address;
    if (!dw_roster_active())
        return 0;
    id = dw_raised_trigger(cpu->gpr[4]);
    addr = dw_trigger_byte(id, &mask);
    cpu->gpr[2] = (addr && (psx_mod_read_byte(addr) & mask)) ? 1u : 0u;
    cpu->pc = cpu->gpr[31];
    return 1;
}

PSX_MOD_CONSTRUCTOR(dw_roster_flags_register) {
    psx_mod_register_function_replace_plugin(
        "dw.roster.raised-set", DW_SET_DIGIMON_RAISED, dw_set_digimon_raised);
    psx_mod_register_function_replace_plugin(
        "dw.roster.raised-has", DW_HAS_DIGIMON_RAISED, dw_has_digimon_raised);
}
