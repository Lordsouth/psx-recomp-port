/* dw.vanilla.cheats -- the cheat list as launcher toggles.
 *
 * Cheats in this project live in `cheats.txt` and are applied per frame by the
 * runtime's GameShark engine. That is the right mechanism and it is not the
 * problem: the problem is that a player has to find a text file, edit it, and
 * restart, and nothing in the launcher hints that the cheats exist at all. A
 * player asked where the cheat list was in the Mods panel, which is a fair
 * question with an unhelpful answer.
 *
 * WHAT THIS DOES NOT DO. It does not carry the codes. A manifest patch cannot
 * express a GameShark code -- a `[[patch]]` is a one-time guarded write to the
 * executable image, while these codes pin values every frame, and many target
 * heap addresses that do not exist in the image before boot. Duplicating the
 * codes into a manifest would also create two places to get an address wrong,
 * and this project has already shipped one wrong address more than once.
 *
 * So the file stays the single source of truth for what every code writes, and
 * this only overrides the `[x]`. `tools/gen_cheats_mod.py` derives the manifest
 * FROM `cheats.txt`, so the option list cannot drift from the cheat list, and
 * the release gate re-runs it with --check.
 *
 * ORDERING, which is the one subtle part. Activation callbacks run during
 * startup; the cheat file is parsed lazily on the FIRST VBlank, well after.
 * Writing the engine's enabled flags here directly would therefore be undone a
 * moment later by the load. `psx_mod_cheat_set_enabled()` records the choice by
 * name instead, and the engine applies it after every parse -- so this is
 * order-independent and survives a reload.
 *
 * WHEN THE FEATURE IS OFF this file does nothing at all: the activation
 * callback only runs for a plan-selected plugin, so `cheats.txt` keeps deciding
 * exactly as it did before, including its own `[x]` defaults.
 */
#include "mod_plugins.h"

#include "dw_cheats_data.inc"

#define DW_CHEATS_PKG "dw.vanilla.cheats"
#define DW_CHEATS_FEATURE "cheats"

static void dw_cheats_activate(void) {
    const unsigned n = (unsigned)(sizeof(k_cheat_options) /
                                  sizeof(k_cheat_options[0]));
    unsigned i;
    for (i = 0; i < n; i++) {
        char value[16];
        /* A read that fails is NOT the same as "false": it means the plan is
         * not committed or the ids do not resolve, and forcing every cheat off
         * on that basis would silently override the file for a reason the
         * player never chose. Leave those alone and let cheats.txt stand. */
        if (!psx_mod_option_value(DW_CHEATS_PKG, DW_CHEATS_FEATURE,
                                  k_cheat_options[i].option,
                                  value, sizeof value))
            continue;
        if (value[0] == '\0')
            continue;
        psx_mod_cheat_set_enabled(k_cheat_options[i].cheat,
                                  value[0] == 't');   /* "true" / "false" */
    }
}

PSX_MOD_CONSTRUCTOR(dw_cheats_register) {
    psx_mod_register_activation_plugin("dw.cheats", dw_cheats_activate);
}
