/* Deep widescreen, phase 8: snow and mist fill the widened frame.
 *
 * Both are screen-space layers drawn in the UI part of the ordering table
 * (after the DR_OFFSET at bucket 0x20, origin 160), so they are laid out for
 * the 4:3 frame [-160, 160) and leave the reveals bare:
 *
 *   SNOW. buildSnowflakePrim (overworld.c:665) keeps each flake's x in
 *   [0, 320], wrapping, and draws it at x - 160. Nothing falls in either
 *   reveal; 13 maps snow. The layer is periodic in 320 by construction, so a
 *   flake within m of one edge gets a copy one period over, in the opposite
 *   reveal. That keeps the density -- rescaling x would thin it by 25% -- and
 *   it makes no RNG call, so the game's randomness and everything downstream
 *   of it is untouched.
 *
 *   MIST. renderMist (overworld.c:550) draws two layers, each two 320-wide
 *   quads that tile with period 640 (offsets wrapped to [-480, 160) and
 *   [-160, 480)), times two rows. The union always covers [-160, 160) but can
 *   stop up to m short of the wide window on one side, so each quad gets a
 *   copy 640 over wherever that copy reaches into [-160 - m, 160 + m).
 *
 * Copies are 40-byte POLY_FT4s in the game's own packet buffer, linked in
 * right behind their original, so they draw at the same depth with the same
 * texture, CLUT and blend.
 *
 * Where they are made. Mist: at renderMist's exit -- it built its eight quads
 * back to back from the work base, and its caller re-reads the work base for
 * every later prim, so copies can be allocated there. Snow: a flake is built
 * into a prim its caller allocated and advances past from a register
 * (renderMapOverlays: prim++; GsSetWorkBase(prim)), so a copy allocated at
 * buildSnowflakePrim's exit would be overwritten by the next prim. Its exit
 * only records the flake; the copies are made at renderMap's exit, after
 * renderMapOverlays -- renderMap's last call -- has linked every flake.
 *
 * Gated on the presented native-wide offset, like every per-frame draw in
 * dw_ws_backdrop.c and dw_ws_sliver.c: identity at 4:3, in squash mode and on
 * native-wide frames presented 4:3.
 */
#include "cpu_state.h"
#include "mod_plugins.h"

extern int32_t psx_mod_ws_native_wide_offset(void);

#define DW_RENDER_MAP 0x800D57ECu
#define DW_RENDER_MIST 0x800B4780u
#define DW_BUILD_SNOWFLAKE_PRIM 0x800B4CF4u
#define DW_GS_OUT_PACKET_P 0x80136FE4u      /* libgs work base (GsGetWorkBase) */
#define DW_GS_WORK_BASES 0x8015DBA8u        /* PACKET[2][0x14000] (btl.c:77) */
#define DW_GS_WORK_BASE_BYTES 0x14000u

#define DW_POLY_FT4_BYTES 40u
#define DW_POLY_FT4_LEN 9u
#define DW_HALF_W 160                       /* the 4:3 frame is [-160, 160) */
#define DW_SNOW_PERIOD 320
#define DW_MIST_PERIOD 640
#define DW_MIST_QUADS 8
#define DW_MIST_TPAGE_X 11                  /* GetTPage(0, n, 0x2c0, 0) */
#define DW_SNOW_MAX 188                     /* renderMapOverlays' instance count */

static int32_t dw_wide_margin(void) {
    const int32_t off = psx_mod_ws_native_wide_offset();
    return off > 0 ? off : 0;
}

/* A POLY_FT4 still holding SetPolyFT4's length and code. */
static int dw_is_ft4(uint32_t p) {
    const uint32_t tag = psx_mod_read_word(p);
    const uint32_t code = psx_mod_read_word(p + 4) >> 24;
    return (tag >> 24) == DW_POLY_FT4_LEN && (code & 0xFCu) == 0x2Cu;
}

static int dw_in_work_base(uint32_t p) {
    return p >= DW_GS_WORK_BASES &&
           p - DW_GS_WORK_BASES <= 2 * DW_GS_WORK_BASE_BYTES - DW_POLY_FT4_BYTES;
}

/* X span [lo, hi] of an FT4's four vertex words (body words 2, 4, 6, 8). */
static void dw_ft4_span(uint32_t p, int32_t* lo, int32_t* hi, int32_t* h) {
    int32_t ylo = 0x7FFF, yhi = -0x8000;
    int k;
    *lo = 0x7FFF;
    *hi = -0x8000;
    for (k = 0; k < 4; k++) {
        const uint32_t w = psx_mod_read_word(p + 8 + (uint32_t)k * 8);
        const int32_t x = (int32_t)(int16_t)(w & 0xFFFFu);
        const int32_t y = (int32_t)(int16_t)(w >> 16);
        *lo = x < *lo ? x : *lo;
        *hi = x > *hi ? x : *hi;
        ylo = y < ylo ? y : ylo;
        yhi = y > yhi ? y : yhi;
    }
    *h = yhi - ylo;
}

/* Copy the FT4 at src shifted by dx into the work base and link it right
 * behind src. Never crosses the end of the current work buffer. */
static void dw_link_copy(uint32_t src, int32_t dx) {
    const uint32_t dst = psx_mod_read_word(DW_GS_OUT_PACKET_P);
    uint32_t tag, k;
    if (!dw_in_work_base(dst) ||
        (dst - DW_GS_WORK_BASES) % DW_GS_WORK_BASE_BYTES >
            DW_GS_WORK_BASE_BYTES - DW_POLY_FT4_BYTES)
        return;
    for (k = 1; k < 10; k++) {
        uint32_t w = psx_mod_read_word(src + k * 4);
        if (k % 2 == 0)                       /* xy words */
            w = (w & 0xFFFF0000u) |
                ((uint32_t)((int32_t)(int16_t)(w & 0xFFFFu) + dx) & 0xFFFFu);
        psx_mod_write_word(dst + k * 4, w);
    }
    tag = psx_mod_read_word(src);
    psx_mod_write_word(dst, tag);             /* same length, src's old next */
    psx_mod_write_word(src, (tag & 0xFF000000u) | (dst & 0x00FFFFFFu));
    psx_mod_write_word(DW_GS_OUT_PACKET_P, dst + DW_POLY_FT4_BYTES);
}

/* ---- snow ---------------------------------------------------------------- */
static uint32_t dw_flakes[DW_SNOW_MAX];
static int dw_nflakes;

static void dw_snow_flake_exit(struct CPUState* cpu, uint32_t address) {
    uint32_t p;
    (void)cpu;
    (void)address;
    if (dw_wide_margin() <= 0 || dw_nflakes >= DW_SNOW_MAX)
        return;
    /* The caller built it at the work base and has not advanced it yet. */
    p = psx_mod_read_word(DW_GS_OUT_PACKET_P);
    if (dw_in_work_base(p))
        dw_flakes[dw_nflakes++] = p;
}

static void dw_snow_render_map_exit(struct CPUState* cpu, uint32_t address) {
    const int32_t m = dw_wide_margin();
    int i;
    (void)cpu;
    (void)address;
    for (i = 0; m > 0 && i < dw_nflakes; i++) {
        int32_t lo, hi, h;
        if (!dw_is_ft4(dw_flakes[i]))
            continue;
        dw_ft4_span(dw_flakes[i], &lo, &hi, &h);
        if (lo < -DW_HALF_W + m)
            dw_link_copy(dw_flakes[i], DW_SNOW_PERIOD);   /* into the right reveal */
        else if (hi > DW_HALF_W - m)
            dw_link_copy(dw_flakes[i], -DW_SNOW_PERIOD);  /* into the left one */
    }
    dw_nflakes = 0;
}

static void dw_snow_reset(void) {
    dw_nflakes = 0;
}

/* ---- mist ---------------------------------------------------------------- */
static void dw_mist_exit(struct CPUState* cpu, uint32_t address) {
    const int32_t m = dw_wide_margin();
    const uint32_t end = psx_mod_read_word(DW_GS_OUT_PACKET_P);
    uint32_t quads[DW_MIST_QUADS];
    int n = 0, k;
    (void)cpu;
    (void)address;
    if (m <= 0)
        return;
    /* renderMist's quads sit back to back just below the work base; when it
     * returned early these are other prims, which the signature rejects. */
    for (k = 1; k <= DW_MIST_QUADS; k++) {
        const uint32_t p = end - (uint32_t)k * DW_POLY_FT4_BYTES;
        int32_t lo, hi, h;
        if (!dw_in_work_base(p) || !dw_is_ft4(p) ||
            ((psx_mod_read_word(p + 20) >> 16) & 0xFu) != DW_MIST_TPAGE_X)
            continue;
        dw_ft4_span(p, &lo, &hi, &h);
        if (hi - lo == 2 * DW_HALF_W && h == 240)
            quads[n++] = p;
    }
    for (k = 0; k < n; k++) {
        int32_t lo, hi, h;
        dw_ft4_span(quads[k], &lo, &hi, &h);
        if (lo + DW_MIST_PERIOD < DW_HALF_W + m)
            dw_link_copy(quads[k], DW_MIST_PERIOD);
        if (hi - DW_MIST_PERIOD > -DW_HALF_W - m)
            dw_link_copy(quads[k], -DW_MIST_PERIOD);
    }
}

PSX_MOD_CONSTRUCTOR(dw_ws_weather_register) {
    psx_mod_register_function_exit_plugin(
        "dw.ws.weather", DW_BUILD_SNOWFLAKE_PRIM, dw_snow_flake_exit);
    psx_mod_register_function_exit_plugin(
        "dw.ws.weather", DW_RENDER_MAP, dw_snow_render_map_exit);
    psx_mod_register_function_exit_plugin(
        "dw.ws.weather", DW_RENDER_MIST, dw_mist_exit);
    psx_mod_register_savestate_plugin("dw.ws.weather", dw_snow_reset);
}
