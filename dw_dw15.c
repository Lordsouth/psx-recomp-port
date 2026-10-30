/* dw.1_5 -- Digimon World 1.5 runs on its own memory cards.
 *
 * 1.5's README says saves from the original game may not work with it, and a
 * 1.5 save loaded by the stock game is no safer. So while the feature is on,
 * the cards live in saves/dw15/ instead of saves/. The activation callback
 * only runs when the manifest's [[plugin]] block is in the plan, and the
 * request is consumed by the next card init, so unticking 1.5 puts the stock
 * cards back on the next launch.
 */
#include "mod_plugins.h"

static void dw15_activate(void) {
    psx_mod_set_memcard_subdir("dw15");
}

PSX_MOD_CONSTRUCTOR(dw15_register) {
    psx_mod_register_activation_plugin("dw.dw15", dw15_activate);
}
