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
#define DW_HANDLE_TILE_UPDATE 0x800D78BCu  /* the backdrop streaming ladder */
#define DW_CAMERA_SETTLE 0x800D6A4Cu      /* MAIN_func_800D6A4C: 2nd X clamp */

#define DW_CAMERA_X 0x80150C38u /* int16 */
#define DW_CAMERA_Y 0x80150C3Au /* int16 */
#define DW_CAMERA_X_PREVIOUS 0x80134DCCu /* int16 */
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

static int32_t dw_read_i32(uint32_t addr) {
    return (int32_t)psx_mod_read_word(addr);
}

/* ---- Wide-aware backdrop tile origin -------------------------------------
 *
 * The backdrop streams as a ring of FOUR tile columns of 128 world px = 512 px.
 * The widened view spans [C - m, C + 320 + m) = 426 px at 16:9, and coverage
 * needs the ring origin 128*T to land in [C - 139, C - 53] -- an 87-wide target
 * on a 128-spaced lattice, so ~32% of camera positions cannot be covered at
 * all. Shifting the origin one tile left whenever the left edge would fail is
 * what fixes the left half of that, and it makes the right deficit at most 41
 * world px instead of a whole column.
 *
 * handleTileUpdate (0x800D78BC, map.c:2177-2339) picks the origin with a
 * hand-tuned ladder of CAMERA_X bands and then calls updateTileColumn /
 * updateTileRow to actually stream. It cannot be replaced natively -- a plugin
 * has no way to call those guest functions, so a replacement could set
 * MAP_TILE_X but never stream, showing the wrong tiles.
 *
 * It can, however, be made to SEE a shifted camera. Verified: handleTileUpdate
 * READS CAMERA_X and CAMERA_X_PREVIOUS and never writes them (no `CAMERA_X[0] =`
 * anywhere in its body). So subtract the reveal on the way in and put it back
 * on the way out, and the ladder -- bands, hysteresis and all -- selects the
 * origin for the wide view's left edge with no band edits at all. At m = 0 the
 * subtraction is zero and every band behaves exactly as vanilla.
 *
 * The restore is why [recompiler] mod_function_exit_funcs exists. An entry hook
 * alone would leave CAMERA_X 53 px low for every later reader in the frame.
 */
static int32_t dw_tile_shift_applied;

static void dw_tile_ladder_enter(struct CPUState* cpu, uint32_t address) {
    int32_t m, c, p;
    (void)cpu;
    (void)address;
    dw_tile_shift_applied = 0;
    m = dw_margin_for_map((int32_t)(int8_t)psx_mod_read_byte(DW_MAP_WIDTH));
    if (m <= 0)
        return;
    c = dw_read_i16(DW_CAMERA_X);
    p = dw_read_i16(DW_CAMERA_X_PREVIOUS);
    /* Never push either below 0: the ladder's lowest band starts at +10 and a
     * negative camera would fall off the bottom of the chain entirely, leaving
     * MAP_TILE_X at whatever it was. */
    if (c < m || p < m)
        return;
    psx_mod_write_half(DW_CAMERA_X, (uint16_t)(c - m));
    psx_mod_write_half(DW_CAMERA_X_PREVIOUS, (uint16_t)(p - m));
    dw_tile_shift_applied = m;
}

static void dw_tile_ladder_exit(struct CPUState* cpu, uint32_t address) {
    const int32_t m = dw_tile_shift_applied;
    (void)cpu;
    (void)address;
    if (m <= 0)
        return;
    psx_mod_write_half(DW_CAMERA_X,
                       (uint16_t)(dw_read_i16(DW_CAMERA_X) + m));
    psx_mod_write_half(DW_CAMERA_X_PREVIOUS,
                       (uint16_t)(dw_read_i16(DW_CAMERA_X_PREVIOUS) + m));
    dw_tile_shift_applied = 0;
}

/* The widened X clamp. Returns 1 if it fired.
 *
 * Idempotent, which is what lets the SAME logic serve both a replacement (where
 * it runs instead of the vanilla clamp) and an exit hook (where it runs after
 * one, correcting its result). Either way the post-condition is the same:
 * CAMERA_X in [m, MAP_WIDTH*128 - 320 - m], with DRAWING_OFFSET_X moved by the
 * matching amount so CAMERA_X + DRAWING_OFFSET_X stays invariant.
 */
static int dw_clamp_x(int32_t w, int32_t m) {
    const int32_t c = dw_read_i16(DW_CAMERA_X);
    const int32_t hi = w * 128 - DW_SCREEN_W - m;
    if (c < m) {
        psx_mod_write_half(DW_CAMERA_X, (uint16_t)m);
        psx_mod_write_word(DW_DRAWING_OFFSET_X,
                           (uint32_t)(dw_read_i16(DW_LIMIT_X_MAX) - m));
        return 1;
    }
    if (c > hi) {
        psx_mod_write_half(DW_CAMERA_X, (uint16_t)hi);
        psx_mod_write_word(DW_DRAWING_OFFSET_X,
                           (uint32_t)(dw_read_i16(DW_LIMIT_X_MIN) + m));
        return 1;
    }
    return 0;
}

/* The SECOND copy of the clamp. map.c has two, byte-for-byte the same logic:
 * cameraIsAtEdge (replaced above) and MAIN_func_800D6A4C (map.c:1892-1907),
 * where it is the last thing the function does. Measuring across 20 maps caught
 * this: every map whose camera settled through this path clamped to the VANILLA
 * bound -- CAMERA_X 0 instead of m, and a 88-91% left void -- because only the
 * other copy had been widened.
 *
 * This one cannot be replaced: the function runs GTE transforms and calls out.
 * But the clamp is its tail, so re-applying the widened bound on exit corrects
 * whatever the vanilla clamp just decided. That is what the exit hook is for.
 */
/* Nudge the camera out of the tile window's blind band.
 *
 * The ring covers [128T, 128T+512) and the widened view needs
 * leftslack = C - 128T in [m, 192-m]. For a given C only two leftslacks are
 * reachable -- r = C mod 128, with T = floor(C/128), or r + 128 with T one
 * lower -- so C is coverable iff
 *
 *     r >= m            (use T)        ... at m=53: r in [53,127]
 *  or r <= 64 - m       (use T-1)      ... at m=53: r in [0,11]
 *
 * leaving a blind band r in [12,52] at 16:9 that NO choice of origin covers.
 * That is the whole of the remaining defect, and it only bites at map load:
 * once the player walks, the streaming ladder moves the camera out of the band
 * on its own. Measured over 20 maps, four landed in it, with left voids of
 * 33-73% that went to 0.0% after a single step.
 *
 * Rather than add a fifth tile column -- which needs VRAM that a three-method
 * census proved does not exist -- move the camera to the nearer edge of the
 * band. The cost is bounded by half the band, 21 px out of 320, applied once
 * when a map loads and erased the moment the player moves. A view that starts
 * 21 px off centre is not a thing anyone can see; a 40-column black bar is.
 *
 * CAMERA_X and DRAWING_OFFSET_X move opposite each other, exactly as the
 * camera-follow code does (map.c: `CAMERA_X += d; DRAWING_OFFSET_X -= d;`),
 * so the backdrop stays registered with the 3D.
 */
static void dw_align_x(int32_t w, int32_t m) {
    const int32_t lo = m;
    const int32_t hi = w * 128 - DW_SCREEN_W - m;
    const int32_t c = dw_read_i16(DW_CAMERA_X);
    const int32_t r = ((c % 128) + 128) % 128;
    int32_t d;

    /* Only UP. In principle leftslack r + 128 also covers when r <= 64 - m,
     * by taking the origin one tile lower -- but that choice is not available
     * where it matters. At map load MAP_TILE_X is set by the plain formula
     * CAMERA_X / 128 (map.c:1785), never T-1, so the low window is unreachable
     * and nudging down lands on an origin the loader will not pick. Measured:
     * aiming at r = 11 left maps 12, 20 and 30 with 54-73% left voids at
     * exactly leftslack 11. The reachable condition is the simple one:
     *
     *     C mod 128 >= m
     *
     * and the right edge needs no test, because r <= 127 < 192 - m always. */
    if (r >= m)
        return;
    d = m - r;
    if (c + d < lo || c + d > hi)
        return;                       /* no room: leave it to the ladder */
    psx_mod_write_half(DW_CAMERA_X, (uint16_t)(c + d));
    psx_mod_write_word(DW_DRAWING_OFFSET_X,
                       (uint32_t)(dw_read_i32(DW_DRAWING_OFFSET_X) - d));
}

static void dw_camera_settle_exit(struct CPUState* cpu, uint32_t address) {
    int32_t w, m;
    (void)cpu;
    (void)address;
    w = (int32_t)(int8_t)psx_mod_read_byte(DW_MAP_WIDTH);
    m = dw_margin_for_map(w);
    if (m <= 0)
        return;
    dw_clamp_x(w, m);
    dw_align_x(w, m);
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
    if (dw_clamp_x(w, m))
        psx_mod_write_word(out_x, 0u);

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
    psx_mod_register_function_entry_plugin(
        "dw.ws.tile-ladder", DW_HANDLE_TILE_UPDATE, dw_tile_ladder_enter);
    psx_mod_register_function_exit_plugin(
        "dw.ws.tile-ladder", DW_HANDLE_TILE_UPDATE, dw_tile_ladder_exit);
    psx_mod_register_function_exit_plugin(
        "dw.ws.camera-settle", DW_CAMERA_SETTLE, dw_camera_settle_exit);
}
