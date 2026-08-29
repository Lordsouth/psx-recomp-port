/* Deep widescreen, phase 1: keep the pre-rendered backdrop registered with
 * squashed 3D.
 *
 * Digimon World's field rooms draw their "pre-rendered" art as a grid of
 * 128x128 POLY_FT4 tile quads (renderMap -> setPosDataMapTile), plus animated
 * map-object sprites (water, doors, foliage) via buildMapOverlayPrim mode 0.
 * Both scroll 1:1 with the camera in the same pre-draw-offset coordinate
 * space the GTE projects 3D into. In squash-mode widescreen the GTE
 * multiplies every projected X by xnum/xden (3/4 at 16:9) but these CPU-built
 * 2D quads are not GTE-projected, so characters slide against the backdrop --
 * the exact misalignment players reported.
 *
 * Fix: a function-entry hook on the one shared positioning helper,
 *
 *     setPosDataPolyFT4(POLY_FT4 *p, int16 x, int16 y, int16 w, int16 h)
 *         @ 0x800E56EC   ($a0 prim, $a1 x, $a2 y, $a3 w, stack h)
 *
 * filtered by return address to exactly the two world-anchored callers:
 *
 *     0x800D7200  setPosDataMapTile's call site      (backdrop tiles)
 *     0x800B507C  buildMapOverlayPrim mode-0 site    (world map objects)
 *
 * (mode 1 -- screen-space UI usage -- calls from 0x800B50A4 and is left
 * alone, as are the other 24 callers: menus, dialogue, HUD.)
 *
 * The transform mirrors the GTE exactly (gte.cpp: xterm * xnum / xden,
 * truncating about 0) and is applied per EDGE, not to x-and-width, so
 * adjacent 128-wide tiles keep shared edges and cannot seam:
 *
 *     x0' = S(x),  w' = S(x + w) - S(x)
 *
 * The hook self-gates on psx_mod_ws_squash_params(), which is true only on
 * frames where the GTE squash itself is live -- widescreen off, menus, FMV
 * and native-wide mode all leave every byte of behavior stock.
 */
#include "cpu_state.h"
#include "mod_plugins.h"

/* exported by the framework fork (gte.cpp): the exact squash gate + ratio */
extern int psx_mod_ws_squash_params(int32_t* num, int32_t* den);

#define DW_SET_POS_DATA_POLY_FT4 0x800E56ECu
#define DW_RA_MAP_TILE 0x800D7200u          /* setPosDataMapTile */
#define DW_RA_MAP_OVERLAY_WORLD 0x800B507Cu /* buildMapOverlayPrim mode 0 */

static void dw_ws_backdrop_hook(struct CPUState* cpu, uint32_t address) {
    int32_t num, den, x, w, x0, x1;
    uint32_t ra;
    (void)address;
    if (!psx_mod_ws_squash_params(&num, &den))
        return;
    ra = cpu->gpr[31];
    if (ra != DW_RA_MAP_TILE && ra != DW_RA_MAP_OVERLAY_WORLD)
        return;
    x = (int32_t)(int16_t)cpu->gpr[5]; /* $a1 */
    w = (int32_t)(int16_t)cpu->gpr[7]; /* $a3 */
    x0 = x * num / den; /* truncating, about 0 -- mirrors the GTE */
    x1 = (x + w) * num / den;
    cpu->gpr[5] = (uint32_t)x0;
    cpu->gpr[7] = (uint32_t)(x1 - x0);
}

PSX_MOD_CONSTRUCTOR(dw_ws_backdrop_register) {
    psx_mod_register_function_entry_plugin(
        "dw.ws.backdrop-align", DW_SET_POS_DATA_POLY_FT4, dw_ws_backdrop_hook);
}
