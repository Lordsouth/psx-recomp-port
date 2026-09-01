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

/* Deep widescreen, phase 2: make the full-screen fade cover the whole frame.
 *
 * renderFade (fade.c, 0x800ACF74) builds ONE semi-transparent POLY_FT4 and
 * positions it through the same shared helper:
 *
 *     setPosDataPolyFT4(prim, -160, -120, 320, 240)   @ 0x800ACFE0
 *
 * -- exactly the vanilla frame, centred on the draw offset. Under native-wide
 * the presented frame is 320 + 2*margin wide, so that quad blacks out the
 * middle and leaves both reveals showing the live scene at full brightness.
 * Measured on the shipped build: mean luma by band 29.6 / 0.6 / 64.9, with the
 * per-column step pinned exactly to the 372|373 boundary. Every screen
 * transition in the game does this, which makes it the most-seen widescreen
 * defect there is.
 *
 * Fix: widen that one quad to the presented width -- a translate and a grow,
 *
 *     x' = x - margin,  w' = w + 2*margin
 *
 * Over-covering is the safe direction: the wide compositor clips to its own
 * surface, and canonical VRAM writes are still clipped to the guest draw area,
 * so nothing outside the framebuffer is touched. Under-covering is what leaves
 * a lit sliver at the seam.
 *
 * Gating, and note it is the OPPOSITE gate to the tiles above: the tile
 * transform runs only when the GTE is squashing; this must run only when it is
 * not, because native-wide does not squash. psx_mod_widescreen_x_margin() is
 * the per-side reveal and is nonzero in BOTH wide modes, so native-wide is
 * exactly "margin > 0 and the squash is not live". At 4:3 the margin is 0 and
 * this is identity.
 */

/* exported by the framework fork (gte.cpp): the exact squash gate + ratio */
extern int psx_mod_ws_squash_params(int32_t* num, int32_t* den);
/* Per-side reveal the PRESENTED frame actually carries; 0 whenever native-wide
 * is not presenting. Deliberately not psx_mod_widescreen_x_margin(): that is the
 * cull margin, which is also nonzero in squash mode, carries the cull guard on
 * top of the reveal, and stays nonzero on native-wide FMV/menu frames that are
 * presented 4:3 -- using it here shifted 2D on frames that were never widened. */
extern int32_t psx_mod_ws_native_wide_offset(void);

#define DW_SCREEN_W 320
#define DW_SET_POS_DATA_POLY_FT4 0x800E56ECu
#define DW_RA_MAP_TILE 0x800D7200u          /* setPosDataMapTile */
#define DW_RA_MAP_OVERLAY_WORLD 0x800B507Cu /* buildMapOverlayPrim mode 0 */
#define DW_RA_FADE 0x800ACFE8u              /* renderFade's full-screen quad */
#define DW_RENDER_RECT_POLY_FT4 0x800B8928u /* shared HUD quad builder */

/* Per-side reveal when NATIVE-WIDE is actually presenting, else 0. */
static int32_t dw_ws_native_wide_margin(void) {
    const int32_t off = psx_mod_ws_native_wide_offset();
    return off > 0 ? off : 0;
}

/* Deep widescreen, phase 3: keep parked HUD parked.
 *
 * The game hides a HUD widget by moving it OUTSIDE the 320-wide frame. Opening
 * a menu slides the clock/compass off the left edge; with no enemy on screen
 * the HP/MP bars sit off the right. At 4:3 those widgets are simply gone --
 * verified against canonical VRAM: with the menu open the top-left corner
 * holds floor and a crate and no dial at all.
 *
 * Native-wide reveals `margin` columns on each side, so the parking spots are
 * now inside the visible frame and the widget reappears, sliced by the frame
 * edge. Measured: the dial at wide columns 0..103 with 20/16/12/8 lit pixels
 * in columns 0..7, against 62..125 and ZERO in 0..7 during normal play; and
 * grey bar chrome running to wide column 425, the frame's last column.
 *
 * The clock is not a rect: renderGameClock builds it from four POLY_FT4 quads
 * through the shared helper
 *
 *     renderRectPolyFT4(posX, posY, w, h, texX, texY, tpage, clut, z, flag)
 *         @ 0x800B8928   ($a0 x, $a1 y, $a2 w, $a3 h)
 *
 * so a rect-family cull in the GPU layer never sees it -- that was tried and
 * measured to change nothing.
 *
 * Fix: at that helper, when the quad is ENTIRELY outside the vanilla frame,
 * push it the rest of the way out of the wide frame too. This translates
 * something that was already invisible, which is what makes it safe: a quad
 * that straddles the edge is left alone, because it was partly visible at 4:3
 * and is legitimately more visible now.
 */
static void dw_ws_hud_park_hook(struct CPUState* cpu, uint32_t address) {
    const int32_t margin = dw_ws_native_wide_margin();
    int32_t x, w;
    (void)address;
    if (margin <= 0)
        return;
    x = (int32_t)(int16_t)cpu->gpr[4]; /* $a0 posX */
    w = (int32_t)(int16_t)cpu->gpr[6]; /* $a2 width */
    if (w <= 0)
        return;
    if (x + w <= 0)
        cpu->gpr[4] = (uint32_t)(x - margin);       /* parked left  */
    else if (x >= DW_SCREEN_W)
        cpu->gpr[4] = (uint32_t)(x + margin);       /* parked right */
}

static void dw_ws_backdrop_hook(struct CPUState* cpu, uint32_t address) {
    int32_t num, den, x, w, x0, x1;
    uint32_t ra;
    (void)address;
    ra = cpu->gpr[31];

    if (ra == DW_RA_FADE) {
        const int32_t margin = dw_ws_native_wide_margin();
        if (margin <= 0)
            return;
        x = (int32_t)(int16_t)cpu->gpr[5]; /* $a1 */
        w = (int32_t)(int16_t)cpu->gpr[7]; /* $a3 */
        cpu->gpr[5] = (uint32_t)(x - margin);
        cpu->gpr[7] = (uint32_t)(w + 2 * margin);
        return;
    }

    if (!psx_mod_ws_squash_params(&num, &den))
        return;
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
    psx_mod_register_function_entry_plugin(
        "dw.ws.hud-park", DW_RENDER_RECT_POLY_FT4, dw_ws_hud_park_hook);
}
