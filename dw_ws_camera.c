/* Deep widescreen, phase 4: stop the camera before the map edge leaves the
 * widened frame.
 *
 * Native-wide reveals `m` columns of extra view per side. The game stops its
 * camera so the map edge lands exactly on the 4:3 frame edge, so those extra
 * columns fall past the map and render dead black -- measured over one battle
 * fought at a clamped camera: 520 of 520 frames cut at wide column 373, 240 of
 * 240 rows agreeing, the void sampling exactly (0,0,0) in 12720 of 12720
 * pixels.
 *
 * The clamp lives in ONE place, and it is a 212-byte leaf with no calls:
 *
 *     void cameraIsAtEdge(int32_t *outX, int32_t *outY)   @ 0x800D7F68
 *         (map.c:2341-2370, fully decompiled)
 *
 * Every per-frame path reaches the clamp through it. The X arm becomes
 *
 *     lo = m                                (was 0)
 *     hi = MAP_WIDTH*128 - 320 - m          (was MAP_WIDTH*128 - 320)
 *
 * because the widened view spans [C - m, C + 320 + m) and both ends must stay
 * inside [0, MAP_WIDTH*128). The Y arm is transcribed unchanged -- native-wide
 * does not widen vertically.
 *
 * DRAWING_OFFSET_X has to move with it or the backdrop slides against the 3D,
 * which is the exact defect 0.1.7 fixed. The pair is invariant: the vanilla
 * clamp pins the offset to a limit CONSTANT at each edge, and shifting the
 * camera in by m shifts the offset out by m:
 *
 *     at lo:  DRAWING_OFFSET_X = DRAW_OFFSET_LIMIT_X_MAX - m
 *     at hi:  DRAWING_OFFSET_X = DRAW_OFFSET_LIMIT_X_MIN + m
 *
 * WHY THE GATE IS NOT THE ONE THE OTHER TWO FIXES USE. dw_ws_backdrop.c gates
 * on psx_mod_ws_native_wide_offset(), which is deliberately 0 on frames
 * presented 4:3 (FMV, menus). That is right for a STATELESS per-primitive
 * transform. It is wrong here: the clamp is STATEFUL -- it writes CAMERA_X --
 * so a single 4:3-presented frame would snap the camera back to the vanilla
 * limit and leave it m pixels off until the player next pushed against the
 * edge. The stable signal is psx_mod_widescreen_x_margin(), which carries no
 * present-classification (gpu.c: psx_ws_x_margin returns the configured
 * offset with no ws_native_wide_active() term). Its VALUE carries the cull
 * guard on top of the reveal, so it is used as a boolean only and the value is
 * latched from the exact offset the first time a wide frame is presented.
 *
 * Identity when widescreen is off: dw_margin() returns 0 without touching a
 * single byte of guest state, and the callback then returns 0 so the original
 * recompiled body runs. Nothing is read, nothing is written, no guest bus
 * access happens at all.
 */
#include "cpu_state.h"
#include "mod_plugins.h"

extern int psx_mod_ws_squash_params(int32_t* num, int32_t* den);
extern int32_t psx_mod_ws_native_wide_offset(void);
extern int32_t psx_mod_widescreen_x_margin(void);

#define DW_CAMERA_IS_AT_EDGE 0x800D7F68u

#define DW_CAMERA_X 0x80150C38u /* int16 */
#define DW_CAMERA_Y 0x80150C3Au /* int16 */
#define DW_MAP_WIDTH 0x80150C3Cu  /* int8  */
#define DW_MAP_HEIGHT 0x80150C3Du /* int8  */
#define DW_DRAWING_OFFSET_X 0x80134EC0u /* int32 */
#define DW_DRAWING_OFFSET_Y 0x80134EC4u /* int32 */
#define DW_LIMIT_X_MAX 0x80134DB6u /* int16 */
#define DW_LIMIT_X_MIN 0x80134DB8u /* int16 */
#define DW_LIMIT_Y_MAX 0x80134DBAu /* int16 */
#define DW_LIMIT_Y_MIN 0x80134DBCu /* int16 */

#define DW_SCREEN_W 320
#define DW_SCREEN_H 240
/* A 320-wide display makes any reveal past this a display-mode surprise;
 * disable rather than clamp to a number derived from the wrong width. */
#define DW_MARGIN_MAX 64

static int32_t dw_margin_latch;

/* Per-side reveal to clamp against, or 0 for "behave exactly like vanilla". */
static int32_t dw_margin(void) {
    int32_t num, den;
    int32_t off;

    if (psx_mod_ws_squash_params(&num, &den)) {
        /* Squash keeps the frame 320 wide and stretches it; the camera window
         * is unchanged, so vanilla's clamp is already correct. */
        dw_margin_latch = 0;
        return 0;
    }
    off = psx_mod_ws_native_wide_offset();
    if (off > 0 && off <= DW_MARGIN_MAX)
        dw_margin_latch = off;
    if (dw_margin_latch <= 0)
        return 0;
    /* Configured-signal check, deliberately used as a boolean: it does not
     * drop out on 4:3-presented frames the way the exact offset does. */
    if (psx_mod_widescreen_x_margin() <= 0) {
        dw_margin_latch = 0;
        return 0;
    }
    return dw_margin_latch;
}

/* Effective margin for a map of MAP_WIDTH w. A map with no scroll room to give
 * up falls back to vanilla: below this, lo would cross hi and the clamp would
 * oscillate between the two arms every frame. */
static int32_t dw_margin_for_map(int32_t w) {
    const int32_t m = dw_margin();
    if (m <= 0)
        return 0;
    return (w * 128 - DW_SCREEN_W) >= 2 * m ? m : 0;
}

static int32_t dw_read_i16(uint32_t addr) {
    return (int32_t)(int16_t)psx_mod_read_half(addr);
}

static int dw_camera_is_at_edge(struct CPUState* cpu, uint32_t address) {
    int32_t m, w, h, c, lim;
    uint32_t out_x, out_y;
    (void)address;

    w = (int32_t)(int8_t)psx_mod_read_byte(DW_MAP_WIDTH);
    m = dw_margin_for_map(w);
    if (m <= 0)
        return 0; /* vanilla: run the recompiled body untouched */

    out_x = cpu->gpr[4]; /* $a0 */
    out_y = cpu->gpr[5]; /* $a1 */
    h = (int32_t)(int8_t)psx_mod_read_byte(DW_MAP_HEIGHT);

    psx_mod_write_word(out_y, 1u);
    psx_mod_write_word(out_x, 1u);

    /* --- X: the widened arm ------------------------------------------- */
    c = dw_read_i16(DW_CAMERA_X);
    if (c < m) {
        psx_mod_write_half(DW_CAMERA_X, (uint16_t)m);
        psx_mod_write_word(DW_DRAWING_OFFSET_X,
                           (uint32_t)(dw_read_i16(DW_LIMIT_X_MAX) - m));
        psx_mod_write_word(out_x, 0u);
    } else {
        lim = w * 128 - DW_SCREEN_W - m;
        if (lim < c) {
            psx_mod_write_half(DW_CAMERA_X, (uint16_t)lim);
            psx_mod_write_word(DW_DRAWING_OFFSET_X,
                               (uint32_t)(dw_read_i16(DW_LIMIT_X_MIN) + m));
            psx_mod_write_word(out_x, 0u);
        }
    }

    /* --- Y: transcribed unchanged; native-wide does not widen vertically */
    c = dw_read_i16(DW_CAMERA_Y);
    if (c < 0) {
        psx_mod_write_half(DW_CAMERA_Y, 0u);
        psx_mod_write_word(DW_DRAWING_OFFSET_Y,
                           (uint32_t)dw_read_i16(DW_LIMIT_Y_MAX));
        psx_mod_write_word(out_y, 0u);
    } else {
        lim = h * 128 - DW_SCREEN_H;
        if (lim < c) {
            psx_mod_write_half(DW_CAMERA_Y, (uint16_t)lim);
            psx_mod_write_word(DW_DRAWING_OFFSET_Y,
                               (uint32_t)dw_read_i16(DW_LIMIT_Y_MIN));
            psx_mod_write_word(out_y, 0u);
        }
    }

    cpu->pc = cpu->gpr[31];
    return 1;
}

PSX_MOD_CONSTRUCTOR(dw_ws_camera_register) {
    psx_mod_register_function_replace_plugin(
        "dw.ws.camera-edge", DW_CAMERA_IS_AT_EDGE, dw_camera_is_at_edge);
}
