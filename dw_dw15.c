/* dw.1_5 -- Digimon World 1.5 runs on its own memory cards.
 *
 * 1.5's README says saves from the original game may not work with it, and a
 * 1.5 save loaded by the stock game is no safer. So while the feature is on,
 * the cards live in saves/dw15/ instead of saves/. The folder is registered
 * statically, so the launcher's card panel switches to those cards as soon
 * as 1.5 is ticked, and activation applies it only while the manifest's
 * [[plugin]] block is in the plan: unticking 1.5 puts the stock cards back.
 */
#include "mod_plugins.h"

PSX_MOD_CONSTRUCTOR(dw15_register) {
    psx_mod_register_memcard_subdir("dw.dw15", "dw15");
}
