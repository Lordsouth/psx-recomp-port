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
#define DW_GS_SORT_SPRITE 0x80096F90u       /* libgs GsSortSprite */
#define DW_RENDER_GAME_CLOCK 0x800DD2A4u    /* clock.c, not decompiled */
#define DW_RENDER_GAME_CLOCK_END 0x800DD5E0u /* updatePlaytime follows it */
#define DW_CLOCK_SPRITE 0x80150C8Cu         /* GsSPRITE: the needle; x int16 @+4 */
#define DW_CLOCK_OFFSET_X 0x80134E1Eu       /* int16 slide: -135 shown, -220 hidden */
#define DW_ACTIVE_FRAMEBUFFER 0x80134ECCu   /* flips every presented frame */
#define DW_RENDER_STATUS_BARS 0x800A936Cu   /* discipline/happiness gauges */
#define DW_RENDER_STATUS_BARS_END 0x800A97E0u /* setImmortalHour follows it */
#define DW_STATUS_UI_OFFSET_X 0x80134C50u   /* int16 slide: 75 shown, 240 hidden */
#define DW_BTL_SLIDE_TABLE 0x80073280u      /* BTL_D_80073280, int16[8] */
#define DW_BTL_SLIDE_N 8
#define DW_BTL_SLIDE_MAX_SHIFT 128          /* sanity bound when restoring */

/* Per-side reveal when NATIVE-WIDE is actually presenting, else 0.
 *
 * DELIBERATELY UNCAPPED, unlike dw_ws_camera.c and dw_ws_sliver.c, which stop
 * at DW_MARGIN_MAX. Those two depend on the backdrop tile ring's geometry and
 * become incoherent past a reveal the ring can cover. Everything in THIS file
 * -- widening the fade plate, pushing parked HUD out of the wider frame --
 * depends only on how wide the presented frame is, and is right at any width.
 * Capping them would reintroduce the fade leak and the sliced clock at exactly
 * the aspects that need them most. */
static int32_t dw_ws_native_wide_margin(void) {
    const int32_t off = psx_mod_ws_native_wide_offset();
    return off > 0 ? off : 0;
}

static int32_t dw_read_i16(uint32_t addr) {
    return (int32_t)(int16_t)psx_mod_read_half(addr);
}

/* The clock is one widget drawn as SEVEN pieces -- four dial quads, the
 * needle (a GsSPRITE, sorted through GsSortSprite, not the quad helper), and
 * two hour-marker quads -- every one of them positioned from the same slide
 * offset CLOCK_OFFSET_X, which renderGameClock steps by 50 px a frame between
 * -135 (shown) and -220 (hidden). Two lessons from shipping the per-quad rule
 * on it:
 *
 *   * The needle went through a different draw path and was never moved, so
 *     it stayed behind in the reveal when the dial was pushed out -- a player
 *     reported the hand sitting alone on the left of the screen.
 *   * Mid-slide, the pieces reach "fully outside the 4:3 frame" on different
 *     frames, so a per-piece rule pushes the leftmost sliver out a frame
 *     before the rest and the dial visibly comes apart on every map change.
 *
 * So the clock is parked as a UNIT: the union of its pieces, measured relative
 * to the slide offset on each frame (the geometry is constant, so the previous
 * frame's union is exact), decides once per frame, and every piece -- quads
 * and needle alike -- gets the same shift. renderGameClock rewrites the
 * needle's x from the slide offset every frame (sh @0x800DD524), so shifting
 * the sprite in place cannot accumulate.
 */
static int dw_in_clock(uint32_t ra) {
    return ra >= DW_RENDER_GAME_CLOCK && ra < DW_RENDER_GAME_CLOCK_END;
}

static int32_t dw_clock_union_lo, dw_clock_union_hi; /* x - s .. x + w - s */
static int dw_clock_union_known;
static int32_t dw_clock_acc_lo, dw_clock_acc_hi;
static int dw_clock_acc_any;
static int dw_clock_decided;
static int32_t dw_clock_shift;
static int32_t dw_clock_fb = -1;

/* Frame boundary = the game's own double-buffer flip. ACTIVE_FRAMEBUFFER
 * toggles once per presented frame, and every clock piece of one frame is
 * drawn between two flips. This is observed from guest state rather than an
 * entry hook on renderGameClock because the recompiler's boundary for that
 * function is 0x800DD2A8, not 0x800DD2A4: the compiler hoisted a gp-relative
 * load above the prologue, so a hook keyed on the symbol's address is emitted
 * nowhere and silently never fires. Measured, not guessed -- the first cut of
 * this did exactly that. */
static void dw_clock_frame_check(void) {
    const int32_t fb = (int32_t)psx_mod_read_byte(DW_ACTIVE_FRAMEBUFFER);
    if (fb == dw_clock_fb)
        return;
    dw_clock_fb = fb;
    if (dw_clock_acc_any) {
        dw_clock_union_lo = dw_clock_acc_lo;
        dw_clock_union_hi = dw_clock_acc_hi;
        dw_clock_union_known = 1;
    }
    dw_clock_acc_any = 0;
    dw_clock_decided = 0;
    dw_clock_shift = 0;
}

/* Decide once per frame, from the slide offset the frame's pieces will use. */
static int32_t dw_clock_decide(int32_t margin) {
    dw_clock_frame_check();
    if (!dw_clock_decided) {
        const int32_t s = dw_read_i16(DW_CLOCK_OFFSET_X);
        dw_clock_shift = 0;
        if (dw_clock_union_known) {
            const int32_t lo = DW_SCREEN_W / 2 + s + dw_clock_union_lo;
            const int32_t hi = DW_SCREEN_W / 2 + s + dw_clock_union_hi;
            if (hi <= 0)
                dw_clock_shift = -margin;         /* parked off the left  */
            else if (lo >= DW_SCREEN_W)
                dw_clock_shift = margin;          /* parked off the right */
        }
        dw_clock_decided = 1;
    }
    return dw_clock_shift;
}

static void dw_clock_note_piece(int32_t x, int32_t w) {
    const int32_t s = dw_read_i16(DW_CLOCK_OFFSET_X);
    const int32_t lo = x - s, hi = x + w - s;
    dw_clock_frame_check();
    if (!dw_clock_acc_any || lo < dw_clock_acc_lo)
        dw_clock_acc_lo = lo;
    if (!dw_clock_acc_any || hi > dw_clock_acc_hi)
        dw_clock_acc_hi = hi;
    dw_clock_acc_any = 1;
}

/* The field status cluster: the discipline and happiness gauges, bottom right.
 *
 * renderStatusBars (0x800A936C) slides them out on STATUS_UI_OFFSET_X, which
 * steps by 50 a frame between 75 (shown) and 240 (hidden), and builds them
 * from EIGHT quads through the same helper as the clock. Their union relative
 * to the slide is a constant [0, 75], so unlike the clock this needs no
 * accumulator -- the decision is arithmetic on the offset alone.
 *
 * The per-piece rule tears it at exactly two offsets. At S = 125 the two frame
 * quads sit at screen 285 and stay, while the 16 gauge pips from index 9 on
 * sit at 321 or beyond and jump 53 px; at S = 140 that is 24 pips of 25. One
 * frame on every hide and one on every show, which is the same defect that
 * came off the clock -- pieces of one widget crossing the frame edge on
 * different frames.
 *
 * Note renderGameClock TAIL-CALLS this function (jal at 0x800DD5C0), so its
 * quads carry return addresses inside renderStatusBars while the call chain
 * began in the clock. That is why the clock's window test uses the return
 * address of the quad rather than anything about the call stack: the two
 * widgets share a render callback but not a slide offset, and each must be
 * judged on its own. */
static int dw_in_status_bars(uint32_t ra) {
    return ra >= DW_RENDER_STATUS_BARS && ra < DW_RENDER_STATUS_BARS_END;
}

static int32_t dw_status_shift(int32_t margin) {
    /* screen span is [160 + S, 235 + S]; parked once the near edge clears the
     * vanilla frame. */
    return (DW_SCREEN_W / 2 + dw_read_i16(DW_STATUS_UI_OFFSET_X)) >= DW_SCREEN_W
               ? margin : 0;
}

/* The battle partner's HP and MP bars.
 *
 * These are the widget the scene survey filed as "grey HUD chrome running to
 * wide column 425, the frame's last column", and nothing in this file was ever
 * touching them: BTL_renderPartnerStatusBars builds its quads inline with
 * AddPrim and its digits through BTL_renderNumber, so renderRectPolyFT4 -- the
 * helper both rules above hook -- is called ZERO times anywhere in the battle
 * overlay. The bars are simply revealed, for most of every fight.
 *
 * They also cannot be hooked directly. Nothing below 0x80090800 is statically
 * recompiled (generated/SLUS_010.32_full.ranges), so a function-entry hook on
 * an overlay address is emitted nowhere and silently never fires -- the same
 * trap as renderGameClock's 0x800DD2A4 boundary, one address range further
 * out.
 *
 * What makes a fix possible without touching the overlay's code at all: the
 * two bars take their X from ONE eight-entry table of slide positions,
 * BTL_D_80073280 = {160, 129, 104, 83, 66, 55, 48, 46}, indexed by a per-bar
 * phase counter, and entry 0 is the parked position. The table is read from
 * exactly two instructions in the whole game, both inside that one function.
 * So moving the parked position moves every piece of a parked bar coherently,
 * by construction -- quads, icon, fill and digits alike, with no per-piece rule
 * to get wrong and nothing to split mid-slide, because only the parked entry
 * changes and the seven visible ones are untouched.
 *
 * Writing it from a VBlank callback rather than once per battle is what avoids
 * a one-frame flash: the table is already patched whenever the renderer reads
 * it, instead of being patched a frame after the bar first parks.
 *
 * The signature guard is not decoration. 0x80073280 is overlay RAM: when BTL
 * is not resident those bytes belong to whatever else was loaded there, so the
 * seven entries this does NOT write must all match before the one it does is
 * touched. Idempotent, and it puts the stock value back when widescreen goes
 * off. */
static const int16_t dw_btl_slide[DW_BTL_SLIDE_N] = {
    160, 129, 104, 83, 66, 55, 48, 46
};

static void dw_btl_bars_vblank(void) {
    const int32_t margin = dw_ws_native_wide_margin();
    int32_t parked;
    int i;

    if (!psx_mod_game_started())
        return;
    for (i = 1; i < DW_BTL_SLIDE_N; i++) {
        if (dw_read_i16(DW_BTL_SLIDE_TABLE + (uint32_t)i * 2) != dw_btl_slide[i])
            return;                       /* not the battle overlay's table */
    }
    parked = dw_read_i16(DW_BTL_SLIDE_TABLE);
    if (margin > 0) {
        if (parked == dw_btl_slide[0])
            psx_mod_write_half(DW_BTL_SLIDE_TABLE,
                               (uint16_t)(dw_btl_slide[0] + margin));
    } else if (parked != dw_btl_slide[0] &&
               parked > dw_btl_slide[0] &&
               parked <= dw_btl_slide[0] + DW_BTL_SLIDE_MAX_SHIFT) {
        psx_mod_write_half(DW_BTL_SLIDE_TABLE, (uint16_t)dw_btl_slide[0]);
    }
}

static void dw_ws_needle_park_hook(struct CPUState* cpu, uint32_t address) {
    const int32_t margin = dw_ws_native_wide_margin();
    int32_t shift;
    (void)address;
    if (margin <= 0 || cpu->gpr[4] != DW_CLOCK_SPRITE) /* $a0 = GsSPRITE* */
        return;
    shift = dw_clock_decide(margin);
    if (shift == 0)
        return;
    psx_mod_write_half(DW_CLOCK_SPRITE + 4,
                       (uint16_t)(dw_read_i16(DW_CLOCK_SPRITE + 4) + shift));
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
    int32_t x, w, sx;
    (void)address;
    if (margin <= 0)
        return;
    x = (int32_t)(int16_t)cpu->gpr[4]; /* $a0 posX */
    w = (int32_t)(int16_t)cpu->gpr[6]; /* $a2 width */
    if (w <= 0)
        return;
    /* posX is CENTRE-RELATIVE, not 0-based. Measured at the call sites inside
     * renderGameClock: the dial arrives as x=-137 w=16, x=-127 w=32,
     * x=-135 w=48 -- all comfortably on screen at 320/2 + x. Testing those
     * against 0 as if they were screen coordinates makes EVERY widget left of
     * centre look parked, and shoves it 53 px left. That is exactly what it
     * did: it moved the clock face while the needle -- drawn by GsSortSprite,
     * not this helper -- stayed where it was, so the hand came off the clock.
     * A player reported it before this was caught. */
    if (dw_in_clock(cpu->gpr[31])) {
        /* The clock moves as one widget: see dw_clock_decide. */
        int32_t shift;
        dw_clock_note_piece(x, w);
        shift = dw_clock_decide(margin);
        if (shift != 0)
            cpu->gpr[4] = (uint32_t)(x + shift);
        return;
    }
    if (dw_in_status_bars(cpu->gpr[31])) {
        const int32_t shift = dw_status_shift(margin);
        if (shift != 0)
            cpu->gpr[4] = (uint32_t)(x + shift);
        return;
    }
    sx = x + DW_SCREEN_W / 2;
    if (sx + w <= 0)
        cpu->gpr[4] = (uint32_t)(x - margin);       /* parked off the left  */
    else if (sx >= DW_SCREEN_W)
        cpu->gpr[4] = (uint32_t)(x + margin);       /* parked off the right */
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
    psx_mod_register_function_entry_plugin(
        "dw.ws.hud-park", DW_GS_SORT_SPRITE, dw_ws_needle_park_hook);
    psx_mod_register_vblank_plugin("dw.ws.btl-bars", dw_btl_bars_vblank);
}
