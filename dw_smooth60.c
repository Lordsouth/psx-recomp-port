/*
 * Smooth 60 FPS (package dw.smooth60): field and battle in-between frames with
 * render passes (psxrecomp docs/RENDER_PASSES.md). Logic stays at the stock
 * 20 Hz; each pass redraws the frame with the game's own renderObjects at the
 * camera, scroll and bone matrices blended between the last two logic ticks.
 * Study: docs/investigations/2026-11-13-60fps.md.
 *
 * Pass point: VSync entry from gameLoop or BTL_battleTickFrame. Both call
 * renderObjects, DrawSync and VSync(3), then swap and GsDrawOt the new OT. So at
 * VSync entry tick N's frame (drawn last iteration) is complete and waits for the
 * swap, and tick N+1's logic is done: the PENDING flip mode, no added delay.
 *
 * Each pass draws from the RAM as it was at renderObjects entry (some render
 * callbacks advance state: fades, damage numbers), with the blended values
 * written over it. 2D emitters (renderSprite, addFXPrim, renderParticleFlash,
 * drawEntityText(Icon), renderFade) get their screen arguments blended between
 * the two stock frames' calls, matched by call site, key and order.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "mod_memory.h"
#include "mod_plugins.h"
#include "pgxp.h"
#include "render_pass_motion.h"

#define PLUGIN_ID   "dw.smooth60"
#define PKG_ID      "dw.smooth60"
#define FEATURE_ID  "smooth60"

/* dw_decomp config/us/symbols.txt */
#define VSYNC                   0x80091CA8u
#define GS_DRAW_OT              0x80098808u
#define GS_CLEAR_OT             0x80098838u
#define GS_SET_WORK_BASE        0x80098C28u
#define GS_SET_ORIGN            0x80097D48u
#define GS_SET_REF_VIEW2        0x80098E18u
#define GS_SET_PROJECTION       0x800981C8u
#define DRAW_SYNC               0x800947B0u
#define RENDER_OBJECTS          0x800A315Cu
#define APPLY_DRAW_OFFSET       0x800EFD30u
#define TICK_CAMERA_FOLLOW      0x800D6F64u
#define RENDER_SPRITE           0x800DA36Cu      /* (GsSPRITE*, x, y, distance, w, h) */
#define ADD_FX_PRIM             0x800DA3FCu      /* (POLY_FT4*, x, y, w, h, distance) */
#define RENDER_PARTICLE_FLASH   0x800AF030u      /* (ParticleFlashData*) */
#define DRAW_ENTITY_TEXT        0x800E5724u      /* (color, digits, x, y, value, depth) */
#define DRAW_ENTITY_TEXT_ICON   0x800E60E0u      /* (x, y, u, depth) */
#define RENDER_FADE             0x800ACF74u      /* (progress) */
#define START_SOUND             0x800C61D4u      /* (vabId, prog, note) -> voice mask */
#define LOAD_IMAGE              0x800948A8u      /* (RECT*, u_long*) */
#define CLEAR_IMAGE             0x80094818u      /* (RECT*, r, g, b) */
#define GAME_LOOP               0x800EFB68u
#define GAME_LOOP_END           0x800EFD30u      /* applyDrawOffset follows it */
/* BTL_REL overlay (base 0x80052AE0, shared with STD/VS: GAME_STATE tells them apart) */
#define BTL_TICK_FRAME          0x8005DEC4u      /* symbols_btl.txt */
#define BTL_TICK_FRAME_END      0x8005DFC8u      /* BTL_addFinisherProgress */

#define ACTIVE_FRAMEBUFFER      0x80134ECCu
#define ACTIVE_ORDERING_TABLE   0x80134ED0u
#define VIEWPORT_DISTANCE_COPY  0x80134ED8u
#define DRAWING_OFFSET_X        0x80134EC0u
#define DRAWING_OFFSET_Y        0x80134EC4u
#define DRAWING_OFFSET_X_COPY   0x80134EDCu
#define DRAWING_OFFSET_Y_COPY   0x80134EE0u
#define GAME_STATE              0x80134F0Au
#define CURRENT_SCREEN          0x80134DA8u
#define CLOCK_OFFSET_X          0x80134E1Eu      /* i16 slide: -135 shown, -220 hidden */
#define STATUS_UI_OFFSET_X      0x80134C50u      /* i16 slide: 75 shown, 240 hidden */
#define GS_ORDERING_TABLE       0x8015DB68u      /* GsOT[2], 0x14 each */
#define DR_OFFSETS              0x8015DB90u      /* DR_OFFSET[2], 12 each */
#define GS_WORK_BASES           0x8015DBA8u      /* 2 x 0x14000 */
#define GS_VIEWPOINT_COPY       0x80185BC8u      /* GsRVIEW2 */
#define MAP_CAMERA              (0x8014F084u + 7092u) /* cameraX, cameraY i16, w, h i8 */
#define ENTITY_TABLE            0x8012F344u      /* Entity*[10] */
#define DIGIMON_DATA            0x8012CEB4u      /* 52 B; boneCount i32 at +20 */
/* startAnimation's `lui v0,%hi(DIGIMON_DATA+20); addiu v0,v0,%lo(...)`: Digimon
 * World 1.5 moves the table and patches this pair, so read it from the code. */
#define BONE_COUNT_SITE         0x800C1A6Cu

#define BONE_SIZE   0x88u   /* PositionData: GsDOBJ2 +0, GsCOORDINATE2 +0x10 (flg, coord +0x14), location +0x78 */
#define MAX_BONES   48u
#define MAX_TRACK   1024u
#define MAX_REC     768u
#define FLIP_DELAY  1u      /* at VSync entry the next VBlank still shows the previous frame */

extern int g_psx_render_pass_active;   /* psx_cycle_freeze.h (runtime) */
extern uint8_t* memory_get_ram_ptr(void);
extern uint8_t* memory_get_scratchpad_ptr(void);
extern uint32_t memory_get_ram_bytes(void);

static PSXMotionSet* s_set;
static uint32_t s_frame;
static int s_in_pass;
static double s_alpha;
static int s_last_screen = -1;
static uint32_t s_flg[MAX_TRACK];
static uint32_t s_nflg;
static uint32_t s_buf;          /* the buffer tick N is in (1 - ACTIVE) */
static uint32_t s_ret;          /* the loop's return point after VSync */
enum { MODE_NONE, MODE_FIELD, MODE_BATTLE };
static int s_mode, s_last_mode;

/* RAM, scratchpad and mod arenas at the last stock renderObjects entry. */
static uint8_t* s_ram;
static uint32_t s_ram_size;
static uint8_t s_scratch[1024];
static uint8_t* s_arena;
static uint32_t s_arena_size, s_arena_cap;
static int s_snap_ok;
static int16_t s_hud[2][2];     /* [prev/cur][clock, status] at renderObjects entry */
static int s_hud_n;

/* 2D emitter calls of the last two stock frames. */
enum { FN_SPRITE, FN_FXPRIM, FN_FLASH, FN_TEXT, FN_ICON, FN_FADE };
typedef struct { uint8_t fn; uint32_t key; int32_t v[5]; } Rec;
static Rec s_rec[2][MAX_REC];
static uint32_t s_nrec[2];
static int s_cur;               /* index of the frame being recorded */
static int s_recording;
static Rec s_pass_calls[MAX_REC];
static uint32_t s_npass;


/* Inside one of this plugin's passes. A pass the sandbox rolls back leaves by
 * longjmp, past the end of pass(), so the flag is only trusted while a pass is
 * actually running (render_pass.c: plugin state must tolerate the abort). */
static int in_pass(void) {
    if (!g_psx_render_pass_active) s_in_pass = 0;
    return s_in_pass;
}

static int32_t rd32(uint32_t a) { return (int32_t)psx_mod_read_word(a); }
static int16_t rd16(uint32_t a) { return (int16_t)psx_mod_read_half(a); }

/* Origin of the VRAM buffer OT[b] draws into. DR_OFFSETS[b] is the game's HUD
 * offset for that OT: the buffer origin plus the stock centre (160, 120). */
static void buffer_origin(uint32_t b, int32_t* x, int32_t* y) {
    const uint32_t dro = psx_mod_read_word(DR_OFFSETS + 12u * b + 4u);
    *x = (int32_t)(dro & 0x7FFu) - 160;
    *y = (int32_t)((dro >> 11) & 0x7FFu) - 120;
}

static uint32_t bone_count_table(void) {
    const uint32_t hi = psx_mod_read_word(BONE_COUNT_SITE), lo = psx_mod_read_word(BONE_COUNT_SITE + 4);
    if ((hi >> 16) != 0x3C02u || (lo >> 16) != 0x2442u) return DIGIMON_DATA + 20;
    return ((hi & 0xFFFFu) << 16) + (uint32_t)(int32_t)(int16_t)(lo & 0xFFFFu);
}

static void capture(void) {
    const uint32_t bone_counts = bone_count_table();
    s_nflg = 0;
    psx_motion_begin(s_set, ++s_frame);
    psx_motion_track(s_set, PSX_MOTION_SCALAR, DRAWING_OFFSET_X, 1);
    psx_motion_track(s_set, PSX_MOTION_SCALAR, DRAWING_OFFSET_Y, 2);
    psx_motion_track(s_set, PSX_MOTION_SCALAR, DRAWING_OFFSET_X_COPY, 3);
    psx_motion_track(s_set, PSX_MOTION_SCALAR, DRAWING_OFFSET_Y_COPY, 4);
    psx_motion_track(s_set, PSX_MOTION_SVECTOR, MAP_CAMERA, 5);  /* z = w|h<<8, constant */
    psx_motion_track(s_set, PSX_MOTION_VECTOR, GS_VIEWPOINT_COPY, 6);
    psx_motion_track(s_set, PSX_MOTION_VECTOR, GS_VIEWPOINT_COPY + 12, 7);
    for (uint32_t e = 0; e < 10; e++) {
        const uint32_t ent = psx_mod_read_word(ENTITY_TABLE + 4 * e);
        if (ent < 0x80000000u || ent >= 0x80200000u) continue;
        if (psx_mod_read_byte(ent + 0x34) == 0) continue;            /* isOnMap */
        const uint32_t type = psx_mod_read_word(ent);
        const uint32_t pos = psx_mod_read_word(ent + 4);
        if (type > 200 || pos < 0x80000000u || pos >= 0x80200000u) continue;
        uint32_t bones = psx_mod_read_word(bone_counts + 52 * type);
        if (bones > MAX_BONES) bones = MAX_BONES;
        for (uint32_t b = 0; b < bones; b++) {
            const uint32_t p = pos + b * BONE_SIZE;
            const uint32_t id = psx_motion_identity(ent, type, b);
            psx_motion_track(s_set, PSX_MOTION_MATRIX, p + 0x14, id);
            if (b == 0) psx_motion_track(s_set, PSX_MOTION_VECTOR, p + 0x78, id ^ 0x5A5A);
            if (s_nflg < MAX_TRACK) s_flg[s_nflg++] = p + 0x10;
        }
    }
    PSXMotionLimits lim = { 400.0, 0.0 };   /* run 50/tick, Run Speed 2x 100; warps snap */
    PSXMotionStats st;
    psx_motion_prepare(s_set, &lim, &st);
    psx_mod_counter_add("dw.smooth60.tracked", st.tracked);
    psx_mod_counter_add("dw.smooth60.blended", st.blended);
    psx_mod_counter_add("dw.smooth60.placed", st.placed);
}

static uint32_t call(struct CPUState* cpu, uint32_t fn, uint32_t a0, uint32_t a1, uint32_t a2) {
    return psx_mod_call_guest(cpu, fn, s_ret, a0, a1, a2, 0);
}

/* ---- pre-render snapshot ------------------------------------------------ */

static void snapshot(void) {
    const uint32_t n = memory_get_ram_bytes(), an = psx_mod_memory_snapshot_bytes();
    s_snap_ok = 0;
    if (n != s_ram_size) {
        free(s_ram);
        s_ram = (uint8_t*)malloc(n);
        s_ram_size = s_ram ? n : 0;
    }
    if (an > s_arena_cap) {
        free(s_arena);
        s_arena = (uint8_t*)malloc(an);
        s_arena_cap = s_arena ? an : 0;
    }
    if (!s_ram || (an && !s_arena)) return;
    memcpy(s_ram, memory_get_ram_ptr(), n);
    memcpy(s_scratch, memory_get_scratchpad_ptr(), sizeof s_scratch);
    s_arena_size = an;
    if (an) psx_mod_memory_snapshot_write(s_arena);
    s_snap_ok = 1;
}

static int restore_snapshot(void) {
    if (!s_snap_ok || s_ram_size != memory_get_ram_bytes() ||
        s_arena_size != psx_mod_memory_snapshot_bytes()) return 0;
    if (s_arena_size && !psx_mod_memory_snapshot_read(s_arena, s_arena_size)) return 0;
    memcpy(memory_get_ram_ptr(), s_ram, s_ram_size);
    memcpy(memory_get_scratchpad_ptr(), s_scratch, sizeof s_scratch);
    pgxp_invalidate_all();   /* the pass checkpoint restores the live shadows afterwards */
    return 1;
}

/* A stock frame's render starts: keep its RAM, start a new 2D record. */
static void on_render_objects(struct CPUState* cpu, uint32_t address) {
    (void)cpu; (void)address;
    if (in_pass() || g_psx_render_pass_active) return;
    const int state = (int8_t)psx_mod_read_byte(GAME_STATE);
    s_recording = 0;
    if (state != 0 && state != 1) { s_snap_ok = 0; return; }
    snapshot();
    s_hud[0][0] = s_hud[1][0]; s_hud[0][1] = s_hud[1][1];
    s_hud[1][0] = rd16(CLOCK_OFFSET_X); s_hud[1][1] = rd16(STATUS_UI_OFFSET_X);
    if (s_hud_n < 2) s_hud_n++;
    s_cur ^= 1;
    s_nrec[s_cur] = 0;
    s_recording = 1;
}

/* ---- 2D emitter blending ------------------------------------------------- */

static int32_t sx16(uint32_t v) { return (int32_t)(int16_t)(v & 0xFFFFu); }

/* Read an emitter's blendable values; returns the number of values. */
static int emitter_read(struct CPUState* cpu, int fn, uint32_t* key, int32_t* v) {
    const uint32_t* a = &cpu->gpr[4];
    const uint32_t sp = cpu->gpr[29], ra = cpu->gpr[31];
    switch (fn) {
    case FN_SPRITE:   /* x, y, distance, w, h */
        *key = a[0] ^ ra;
        v[0] = sx16(a[1]); v[1] = sx16(a[2]); v[2] = (int32_t)a[3];
        v[3] = rd32(sp + 16); v[4] = rd32(sp + 20);
        return 5;
    case FN_FXPRIM:   /* x, y, w, h, distance */
        *key = ra;
        v[0] = sx16(a[1]); v[1] = sx16(a[2]); v[2] = sx16(a[3]);
        v[3] = sx16(psx_mod_read_word(sp + 16)); v[4] = rd32(sp + 20);
        return 5;
    case FN_FLASH:    /* screen x, y, depth, scale, colorScale */
        *key = ra;
        v[0] = rd16(a[0]); v[1] = rd16(a[0] + 2); v[2] = rd16(a[0] + 4);
        v[3] = rd32(a[0] + 8); v[4] = psx_mod_read_byte(a[0] + 0x15);
        return 5;
    case FN_TEXT:     /* x, y; keyed by value, depth, colour */
        *key = psx_mod_read_word(sp + 16) * 2654435761u ^ (psx_mod_read_word(sp + 20) << 24) ^ (a[0] << 16);
        v[0] = (int32_t)a[2]; v[1] = (int32_t)a[3];
        return 2;
    case FN_ICON:     /* x, y; keyed by icon u and depth */
        *key = (a[2] & 0xFFu) | (a[3] << 8);
        v[0] = (int32_t)a[0]; v[1] = (int32_t)a[1];
        return 2;
    case FN_FADE:     /* progress */
        *key = ra;
        v[0] = (int32_t)(a[0] & 0xFFu);
        return 1;
    }
    return 0;
}

static void emitter_write(struct CPUState* cpu, int fn, const int32_t* v) {
    uint32_t* a = &cpu->gpr[4];
    const uint32_t sp = cpu->gpr[29];
    switch (fn) {
    case FN_SPRITE:
        a[1] = (uint32_t)v[0]; a[2] = (uint32_t)v[1]; a[3] = (uint32_t)v[2];
        psx_mod_write_word(sp + 16, (uint32_t)v[3]); psx_mod_write_word(sp + 20, (uint32_t)v[4]);
        break;
    case FN_FXPRIM:
        a[1] = (uint32_t)v[0]; a[2] = (uint32_t)v[1]; a[3] = (uint32_t)v[2];
        psx_mod_write_word(sp + 16, (uint32_t)v[3]); psx_mod_write_word(sp + 20, (uint32_t)v[4]);
        break;
    case FN_FLASH:
        psx_mod_write_half(a[0], (uint16_t)v[0]); psx_mod_write_half(a[0] + 2, (uint16_t)v[1]);
        psx_mod_write_half(a[0] + 4, (uint16_t)v[2]); psx_mod_write_word(a[0] + 8, (uint32_t)v[3]);
        psx_mod_write_byte(a[0] + 0x15, (uint8_t)v[4]);
        break;
    case FN_TEXT:
        a[2] = (uint32_t)v[0]; a[3] = (uint32_t)v[1];
        break;
    case FN_ICON:
        a[0] = (uint32_t)v[0]; a[1] = (uint32_t)v[1];
        break;
    case FN_FADE:
        a[0] = (uint32_t)v[0];
        break;
    }
}

static const Rec* find_rec(int frame, int fn, uint32_t key, uint32_t ordinal) {
    for (uint32_t i = 0; i < s_nrec[frame]; i++) {
        const Rec* r = &s_rec[frame][i];
        if (r->fn == fn && r->key == key && ordinal-- == 0) return r;
    }
    return 0;
}

/* Values 0 and 1 are screen x and y for every emitter but the fade: farther apart
 * than this between the two frames, it is a different sprite (respawned, reordered)
 * and is drawn as the newer frame has it. */
#define MAX_STEP_PX 96

static void emitter(struct CPUState* cpu, int fn) {
    uint32_t key;
    int32_t v[5];
    const int n = emitter_read(cpu, fn, &key, v);
    if (!n) return;
    if (!in_pass()) {
        if (s_recording && s_nrec[s_cur] < MAX_REC) {
            Rec* r = &s_rec[s_cur][s_nrec[s_cur]++];
            r->fn = (uint8_t)fn; r->key = key;
            memcpy(r->v, v, sizeof v);
        }
        return;
    }
    uint32_t ordinal = 0;
    for (uint32_t i = 0; i < s_npass; i++)
        if (s_pass_calls[i].fn == fn && s_pass_calls[i].key == key) ordinal++;
    if (s_npass < MAX_REC) { s_pass_calls[s_npass].fn = (uint8_t)fn; s_pass_calls[s_npass++].key = key; }
    const Rec* p = find_rec(s_cur ^ 1, fn, key, ordinal);
    const Rec* c = find_rec(s_cur, fn, key, ordinal);
    if (!p || !c) { psx_mod_counter_add("dw.smooth60.2d_unmatched", 1); return; }
    if (fn != FN_FADE && (abs(c->v[0] - p->v[0]) > MAX_STEP_PX || abs(c->v[1] - p->v[1]) > MAX_STEP_PX)) {
        psx_mod_counter_add("dw.smooth60.2d_snapped", 1);
        return;
    }
    for (int i = 0; i < n; i++)
        v[i] = p->v[i] + (int32_t)((double)(c->v[i] - p->v[i]) * s_alpha + (c->v[i] >= p->v[i] ? 0.5 : -0.5));
    emitter_write(cpu, fn, v);
    psx_mod_counter_add("dw.smooth60.2d_blended", 1);
}

static void on_sprite(struct CPUState* cpu, uint32_t a) { (void)a; emitter(cpu, FN_SPRITE); }
static void on_fxprim(struct CPUState* cpu, uint32_t a) { (void)a; emitter(cpu, FN_FXPRIM); }
static void on_flash(struct CPUState* cpu, uint32_t a)  { (void)a; emitter(cpu, FN_FLASH); }
static void on_text(struct CPUState* cpu, uint32_t a)   { (void)a; emitter(cpu, FN_TEXT); }
static void on_icon(struct CPUState* cpu, uint32_t a)   { (void)a; emitter(cpu, FN_ICON); }
static void on_fade(struct CPUState* cpu, uint32_t a)   { (void)a; emitter(cpu, FN_FADE); }

/* ---- the pass ------------------------------------------------------------- */

static int16_t lerp16(int16_t a, int16_t b, double t, int limit) {
    if (abs(b - a) > limit) return b;
    return (int16_t)(a + (int)((b - a) * t + (b >= a ? 0.5 : -0.5)));
}

static int pass(struct CPUState* cpu, void* user, uint32_t alpha_q16) {
    (void)user;
    const uint32_t b = s_buf;
    const uint32_t ot = GS_ORDERING_TABLE + 0x14u * b;
    if (!restore_snapshot()) return 0;   /* draw from where the stock render started */
    s_in_pass = 1;
    s_alpha = alpha_q16 / 65536.0;
    s_npass = 0;
    psx_motion_apply(s_set, s_alpha);
    for (uint32_t i = 0; i < s_nflg; i++) psx_mod_write_word(s_flg[i], 0);   /* GsGetLws recomputes */
    if (s_hud_n == 2) {
        psx_mod_write_half(CLOCK_OFFSET_X, (uint16_t)lerp16(s_hud[0][0], s_hud[1][0], s_alpha, 200));
        psx_mod_write_half(STATUS_UI_OFFSET_X, (uint16_t)lerp16(s_hud[0][1], s_hud[1][1], s_alpha, 200));
    }
    psx_mod_write_word(ACTIVE_FRAMEBUFFER, b);
    psx_mod_write_word(ACTIVE_ORDERING_TABLE, ot);
    call(cpu, GS_SET_WORK_BASE, GS_WORK_BASES + 0x14000u * b, 0, 0);
    call(cpu, GS_CLEAR_OT, 0, 0, ot);
    /* Field: gameLoop sets origin and view before its tick (the _COPY values).
     * Battle: BTL_battleTickFrame sets no view, and the origin after its tick
     * from DRAWING_OFFSET; the HUD and effects restore GS_VIEWPOINT themselves. */
    const int field = s_mode == MODE_FIELD;
    const int32_t dox = rd32(field ? DRAWING_OFFSET_X_COPY : DRAWING_OFFSET_X);
    const int32_t doy = rd32(field ? DRAWING_OFFSET_Y_COPY : DRAWING_OFFSET_Y);
    call(cpu, GS_SET_ORIGN, (uint32_t)dox, (uint32_t)doy, 0);
    if (field) {
        call(cpu, GS_SET_REF_VIEW2, GS_VIEWPOINT_COPY, 0, 0);
        call(cpu, GS_SET_PROJECTION, (uint32_t)rd32(VIEWPORT_DISTANCE_COPY), 0, 0);
    }
    call(cpu, RENDER_OBJECTS, 0, 0, 0);
    if (field) {
        call(cpu, APPLY_DRAW_OFFSET, b, 0, 0);
    } else {          /* AddPrim(org + 0x20, &DR_OFFSETS[b]): 0x20 GsOT_TAGs = 0x80 bytes */
        const uint32_t slot = psx_mod_read_word(ot + 4) + 0x80u, dr = DR_OFFSETS + 12u * b;
        const uint32_t sv = psx_mod_read_word(slot), tag = psx_mod_read_word(dr);
        psx_mod_write_word(dr, (tag & 0xFF000000u) | (sv & 0xFFFFFFu));
        psx_mod_write_word(slot, (sv & 0xFF000000u) | (dr & 0xFFFFFFu));
    }

    /* Draw environment for buffer b, drawn first: fill, area, world offset.
     * World offset: buffer origin + the GsSetOrign origin, as GsSwapDispBuff
     * sets it (GsOFSGPU); the HUD gets DR_OFFSETS' own from slot 32 on. A
     * stock battle frame drew with E5 (384, 282) and DR_OFFSET (160, 120). */
    int32_t bx, by;
    buffer_origin(b, &bx, &by);
    const int32_t ox = bx + dox, oy = by + doy;
    const uint32_t prim = GS_WORK_BASES + 0x14000u * b + 0x13F00u;
    const uint32_t head = psx_mod_read_word(ot + 0x10);              /* GsOT.tag */
    const uint32_t link = psx_mod_read_word(head);
    psx_mod_write_word(prim + 4, 0x02000000u);                          /* fill black */
    psx_mod_write_word(prim + 8, ((uint32_t)by << 16) | (uint32_t)bx);
    psx_mod_write_word(prim + 12, (240u << 16) | 320u);
    psx_mod_write_word(prim + 16, 0xE3000000u | ((uint32_t)by << 10) | (uint32_t)bx);
    psx_mod_write_word(prim + 20, 0xE4000000u | ((uint32_t)(by + 239) << 10) | (uint32_t)(bx + 319));
    psx_mod_write_word(prim + 24, 0xE5000000u | (((uint32_t)oy & 0x7FFu) << 11) | ((uint32_t)ox & 0x7FFu));
    psx_mod_write_word(prim, (6u << 24) | (link & 0xFFFFFFu));
    psx_mod_write_word(head, (link & 0xFF000000u) | (prim & 0xFFFFFFu));

    call(cpu, GS_DRAW_OT, ot, 0, 0);
    call(cpu, DRAW_SYNC, 0, 0, 0);
    s_in_pass = 0;
    return 1;
}

static void on_vsync(struct CPUState* cpu, uint32_t address) {
    (void)address;
    if (in_pass() || g_psx_render_pass_active) return;
    const uint32_t ra = cpu->gpr[31];
    const int state = (int8_t)psx_mod_read_byte(GAME_STATE);
    int mode = MODE_NONE;
    if (ra >= GAME_LOOP && ra < GAME_LOOP_END && state == 0) mode = MODE_FIELD;
    else if (ra >= BTL_TICK_FRAME && ra < BTL_TICK_FRAME_END && state == 1) mode = MODE_BATTLE;
    if (mode == MODE_NONE) return;                    /* other VSync callers (timing, other loops) */
    const int recorded = s_recording;
    s_recording = 0;
    const int screen = psx_mod_read_byte(CURRENT_SCREEN);
    if (mode != s_last_mode || screen != s_last_screen || !recorded || !s_snap_ok) {
        psx_motion_invalidate(s_set);                 /* no blending across a cut */
        s_last_screen = screen;
        s_last_mode = mode;
        s_hud_n = 0;
        s_nrec[s_cur ^ 1] = 0;
        psx_mod_counter_add(!recorded ? "dw.smooth60.skipped_no_render" :
                            !s_snap_ok ? "dw.smooth60.skipped_no_snapshot" : "dw.smooth60.skipped", 1);
        return;
    }
    s_mode = mode;
    s_buf = (psx_mod_read_word(ACTIVE_FRAMEBUFFER) & 1u) ^ 1u;
    s_ret = ra;
    capture();
    int32_t bx, by;
    buffer_origin(s_buf, &bx, &by);
    if (bx < 0 || by < 0 || bx > 1024 - 320 || by > 512 - 240) {
        psx_mod_counter_add("dw.smooth60.bad_rect", 1);
        return;
    }
    PSXModRenderPassFrame f = { sizeof f, 3, FLIP_DELAY, (uint16_t)bx, (uint16_t)by, 320, 240 };
    const uint32_t kept = psx_mod_render_pass_frame(cpu, &f, pass, 0);
    psx_mod_counter_add(mode == MODE_FIELD ? "dw.smooth60.frames" : "dw.smooth60.battle_frames", 1);
    psx_mod_counter_add(mode == MODE_FIELD ? "dw.smooth60.passes" : "dw.smooth60.battle_passes", kept);
}

/* renderMap's camera follow is logic: inside a pass the blended camera stands in for it. */
static int on_camera_follow(struct CPUState* cpu, uint32_t address) {
    (void)cpu; (void)address;
    return in_pass();
}

/* A pass redraws from the pre-render RAM, so a render callback that starts a
 * sound effect would start it again (the stock frame already did): skip it,
 * reporting no voices. The caller's RAM is rolled back with the pass. */
static int on_start_sound(struct CPUState* cpu, uint32_t address) {
    (void)address;
    if (!in_pass()) return 0;
    cpu->gpr[2] = 0;
    psx_mod_counter_add("dw.smooth60.sound_skipped", 1);
    return 1;
}

/* A pass replays the stock render from the same RAM, so a CPU->VRAM upload or
 * clear outside the rect it draws (glyphs, map tiles, CLUT fades) writes what the
 * stock frame already left there, and the sandbox then rolls it back: skip it.
 * One that overlaps the rect runs as usual. */
static int on_vram_upload(struct CPUState* cpu, uint32_t address) {
    (void)address;
    if (!in_pass()) return 0;
    const uint32_t r = cpu->gpr[4];
    const int32_t x = rd16(r), y = rd16(r + 2), w = rd16(r + 4), h = rd16(r + 6);
    int32_t bx, by;
    buffer_origin(s_buf, &bx, &by);
    if (x < bx + 320 && bx < x + w && y < by + 240 && by < y + h) return 0;
    cpu->gpr[2] = 0;
    psx_mod_counter_add("dw.smooth60.upload_skipped", 1);
    return 1;
}

/* A savestate load or rewind step jumps the game: never blend across it. */
static void reset(void) {
    if (s_set) psx_motion_invalidate(s_set);
    s_last_screen = -1;
    s_last_mode = MODE_NONE;
    s_snap_ok = 0;
    s_hud_n = 0;
    s_recording = 0;
    s_nrec[0] = s_nrec[1] = 0;
}

static void activate(void) {
    if (!s_set) s_set = psx_motion_set_create(MAX_TRACK + 16);
    reset();
    psx_mod_activate_render_pass_rate(PKG_ID, FEATURE_ID, "rate", PSX_MOD_RENDER_PASS_FLIP_PENDING);
}

PSX_MOD_CONSTRUCTOR(dw_smooth60_register) {
    psx_mod_register_activation_plugin(PLUGIN_ID, activate);
    psx_mod_register_savestate_plugin(PLUGIN_ID, reset);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, VSYNC, on_vsync);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, RENDER_OBJECTS, on_render_objects);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, RENDER_SPRITE, on_sprite);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, ADD_FX_PRIM, on_fxprim);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, RENDER_PARTICLE_FLASH, on_flash);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, DRAW_ENTITY_TEXT, on_text);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, DRAW_ENTITY_TEXT_ICON, on_icon);
    psx_mod_register_function_entry_plugin(PLUGIN_ID, RENDER_FADE, on_fade);
    psx_mod_register_function_filter_plugin(PLUGIN_ID, TICK_CAMERA_FOLLOW, on_camera_follow);
    psx_mod_register_function_filter_plugin(PLUGIN_ID, START_SOUND, on_start_sound);
    psx_mod_register_function_filter_plugin(PLUGIN_ID, LOAD_IMAGE, on_vram_upload);
    psx_mod_register_function_filter_plugin(PLUGIN_ID, CLEAR_IMAGE, on_vram_upload);
}
