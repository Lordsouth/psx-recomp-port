/* dw.evoguide -- Evolution Guide: the partner's possible evolutions and how
 * close it is to each, drawn over the game.
 *
 * READ. Each VBlank while the panel is open, dw_evo_model() (dw_evolution.c)
 * runs the game's own chooser over the live tables and the result is formatted
 * into text lines. Nothing is written to guest memory; closed, nothing runs.
 *
 * DRAW. The framework's game HUD layer (host_overlay.h, OpenGL only) calls
 * guide_draw on the GL thread. The lines are rasterised on the CPU with the
 * public-domain font8x8 set (the one host_osd.c uses for toasts and Rewind's
 * labels), uploaded as one texture and blended in at an integer scale, top
 * left. GL state it touches is put back.
 *
 * INPUT. F2 (an SDL event watch: the input sink belongs to the in-game menu)
 * or Select + L3 (a pad transform on player 1, so remaps and keyboard-as-pad
 * count) toggles the panel. While Select + L3 is held both buttons read as
 * released to the game; otherwise the transform hands back the stock pad.
 * F9 was the suggestion, but it is the framework's fast-forward toggle.
 *
 * NO PAUSE. The panel is passive (no navigation, every target on one page), so
 * the only input it takes is its own toggle and the game can keep running
 * under it with values moving live. Pausing would also need the toggle to be
 * read inside the host pause loop, where neither the transform nor the vblank
 * runs.
 *
 * TESTS. Debug command `evoguide`: "open", "close", "state", "t <i>" (JSON),
 * "lines" (the panel text, '|' between lines).
 */
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "psx_sdl.h"
#if defined(PSX_SDL3)
#include <SDL3/SDL_opengl.h>
#else
#include <SDL_opengl.h>
#endif

#include "dw_evolution.h"
#include "host_overlay.h"
#include "mod_plugins.h"

#define PAD_SELECT 0x0001u
#define PAD_L3 0x0002u
#define PAD_ALL_TYPES 0x0Fu

#define LINE_CHARS 52
#define LINE_BYTES 160
#define MAX_LINES 48
#define GLYPH 8
#define LINE_H 10
#define PAD_PX 6

/* inline colours */
#define C_WHITE "\x01"
#define C_GREEN "\x02"
#define C_RED "\x03"
#define C_YELLOW "\x04"
#define C_GREY "\x05"
static const uint32_t COLOURS[6] = { 0xFFFFFFFFu, 0xFFFFFFFFu, 0xFF70E070u,
                                     0xFFF07070u, 0xFFF0D060u, 0xFFA0A0A0u };
#define BACKGROUND 0xD8101420u

/* Public-domain 8x8 ASCII (32..126), row bitmasks LSB = left pixel, from the
 * font8x8_basic set (https://github.com/d7samurai/font8x8), as in host_osd.c. */
static const uint8_t FONT8X8[95][8] = {
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00}, /*   */
    {0x18,0x3C,0x3C,0x18,0x18,0x00,0x18,0x00}, /* ! */
    {0x36,0x36,0x00,0x00,0x00,0x00,0x00,0x00}, /* " */
    {0x36,0x36,0x7F,0x36,0x7F,0x36,0x36,0x00}, /* # */
    {0x0C,0x3E,0x03,0x1E,0x30,0x1F,0x0C,0x00}, /* $ */
    {0x00,0x63,0x33,0x18,0x0C,0x66,0x63,0x00}, /* % */
    {0x1C,0x36,0x1C,0x6E,0x3B,0x33,0x6E,0x00}, /* & */
    {0x06,0x06,0x03,0x00,0x00,0x00,0x00,0x00}, /* ' */
    {0x18,0x0C,0x06,0x06,0x06,0x0C,0x18,0x00}, /* ( */
    {0x06,0x0C,0x18,0x18,0x18,0x0C,0x06,0x00}, /* ) */
    {0x00,0x66,0x3C,0xFF,0x3C,0x66,0x00,0x00}, /* * */
    {0x00,0x0C,0x0C,0x3F,0x0C,0x0C,0x00,0x00}, /* + */
    {0x00,0x00,0x00,0x00,0x00,0x0C,0x0C,0x06}, /* , */
    {0x00,0x00,0x00,0x3F,0x00,0x00,0x00,0x00}, /* - */
    {0x00,0x00,0x00,0x00,0x00,0x0C,0x0C,0x00}, /* . */
    {0x60,0x30,0x18,0x0C,0x06,0x03,0x01,0x00}, /* / */
    {0x3E,0x63,0x73,0x7B,0x6F,0x67,0x3E,0x00}, /* 0 */
    {0x0C,0x0E,0x0C,0x0C,0x0C,0x0C,0x3F,0x00}, /* 1 */
    {0x1E,0x33,0x30,0x1C,0x06,0x33,0x3F,0x00}, /* 2 */
    {0x1E,0x33,0x30,0x1C,0x30,0x33,0x1E,0x00}, /* 3 */
    {0x38,0x3C,0x36,0x33,0x7F,0x30,0x78,0x00}, /* 4 */
    {0x3F,0x03,0x1F,0x30,0x30,0x33,0x1E,0x00}, /* 5 */
    {0x1C,0x06,0x03,0x1F,0x33,0x33,0x1E,0x00}, /* 6 */
    {0x3F,0x33,0x30,0x18,0x0C,0x0C,0x0C,0x00}, /* 7 */
    {0x1E,0x33,0x33,0x1E,0x33,0x33,0x1E,0x00}, /* 8 */
    {0x1E,0x33,0x33,0x3E,0x30,0x18,0x0E,0x00}, /* 9 */
    {0x00,0x0C,0x0C,0x00,0x00,0x0C,0x0C,0x00}, /* : */
    {0x00,0x0C,0x0C,0x00,0x00,0x0C,0x0C,0x06}, /* ; */
    {0x18,0x0C,0x06,0x03,0x06,0x0C,0x18,0x00}, /* < */
    {0x00,0x00,0x3F,0x00,0x00,0x3F,0x00,0x00}, /* = */
    {0x06,0x0C,0x18,0x30,0x18,0x0C,0x06,0x00}, /* > */
    {0x1E,0x33,0x30,0x18,0x0C,0x00,0x0C,0x00}, /* ? */
    {0x3E,0x63,0x7B,0x7B,0x7B,0x03,0x1E,0x00}, /* @ */
    {0x0C,0x1E,0x33,0x33,0x3F,0x33,0x33,0x00}, /* A */
    {0x3F,0x66,0x66,0x3E,0x66,0x66,0x3F,0x00}, /* B */
    {0x3C,0x66,0x03,0x03,0x03,0x66,0x3C,0x00}, /* C */
    {0x1F,0x36,0x66,0x66,0x66,0x36,0x1F,0x00}, /* D */
    {0x7F,0x06,0x06,0x3E,0x06,0x06,0x7F,0x00}, /* E */
    {0x7F,0x06,0x06,0x3E,0x06,0x06,0x06,0x00}, /* F */
    {0x3C,0x66,0x03,0x03,0x73,0x66,0x7C,0x00}, /* G */
    {0x33,0x33,0x33,0x3F,0x33,0x33,0x33,0x00}, /* H */
    {0x1E,0x0C,0x0C,0x0C,0x0C,0x0C,0x1E,0x00}, /* I */
    {0x78,0x30,0x30,0x30,0x33,0x33,0x1E,0x00}, /* J */
    {0x67,0x66,0x36,0x1E,0x36,0x66,0x67,0x00}, /* K */
    {0x06,0x06,0x06,0x06,0x06,0x06,0x7F,0x00}, /* L */
    {0x63,0x77,0x7F,0x7F,0x6B,0x63,0x63,0x00}, /* M */
    {0x63,0x67,0x6F,0x7B,0x73,0x63,0x63,0x00}, /* N */
    {0x1C,0x36,0x63,0x63,0x63,0x36,0x1C,0x00}, /* O */
    {0x3F,0x66,0x66,0x3E,0x06,0x06,0x06,0x00}, /* P */
    {0x1E,0x33,0x33,0x33,0x3B,0x1E,0x38,0x00}, /* Q */
    {0x3F,0x66,0x66,0x3E,0x36,0x66,0x67,0x00}, /* R */
    {0x1E,0x33,0x07,0x0E,0x38,0x33,0x1E,0x00}, /* S */
    {0x3F,0x2D,0x0C,0x0C,0x0C,0x0C,0x1E,0x00}, /* T */
    {0x33,0x33,0x33,0x33,0x33,0x33,0x3F,0x00}, /* U */
    {0x33,0x33,0x33,0x33,0x33,0x1E,0x0C,0x00}, /* V */
    {0x63,0x63,0x63,0x6B,0x7F,0x77,0x63,0x00}, /* W */
    {0x63,0x63,0x36,0x1C,0x1C,0x36,0x63,0x00}, /* X */
    {0x33,0x33,0x33,0x1E,0x0C,0x0C,0x1E,0x00}, /* Y */
    {0x7F,0x63,0x31,0x18,0x4C,0x66,0x7F,0x00}, /* Z */
    {0x1E,0x06,0x06,0x06,0x06,0x06,0x1E,0x00}, /* [ */
    {0x03,0x06,0x0C,0x18,0x30,0x60,0x40,0x00}, /* \ */
    {0x1E,0x18,0x18,0x18,0x18,0x18,0x1E,0x00}, /* ] */
    {0x08,0x1C,0x36,0x63,0x00,0x00,0x00,0x00}, /* ^ */
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xFF}, /* _ */
    {0x0C,0x0C,0x18,0x00,0x00,0x00,0x00,0x00}, /* ` */
    {0x00,0x00,0x1E,0x30,0x3E,0x33,0x6E,0x00}, /* a */
    {0x07,0x06,0x06,0x3E,0x66,0x66,0x3B,0x00}, /* b */
    {0x00,0x00,0x1E,0x33,0x03,0x33,0x1E,0x00}, /* c */
    {0x38,0x30,0x30,0x3e,0x33,0x33,0x6E,0x00}, /* d */
    {0x00,0x00,0x1E,0x33,0x3f,0x03,0x1E,0x00}, /* e */
    {0x1C,0x36,0x06,0x0f,0x06,0x06,0x0f,0x00}, /* f */
    {0x00,0x00,0x6E,0x33,0x33,0x3E,0x30,0x1F}, /* g */
    {0x07,0x06,0x36,0x6E,0x66,0x66,0x67,0x00}, /* h */
    {0x0C,0x00,0x0E,0x0C,0x0C,0x0C,0x1E,0x00}, /* i */
    {0x30,0x00,0x30,0x30,0x30,0x33,0x33,0x1E}, /* j */
    {0x07,0x06,0x66,0x36,0x1E,0x36,0x67,0x00}, /* k */
    {0x0E,0x0C,0x0C,0x0C,0x0C,0x0C,0x1E,0x00}, /* l */
    {0x00,0x00,0x33,0x7F,0x7F,0x6B,0x63,0x00}, /* m */
    {0x00,0x00,0x1F,0x33,0x33,0x33,0x33,0x00}, /* n */
    {0x00,0x00,0x1E,0x33,0x33,0x33,0x1E,0x00}, /* o */
    {0x00,0x00,0x3B,0x66,0x66,0x3E,0x06,0x0F}, /* p */
    {0x00,0x00,0x6E,0x33,0x33,0x3E,0x30,0x78}, /* q */
    {0x00,0x00,0x3B,0x6E,0x66,0x06,0x0F,0x00}, /* r */
    {0x00,0x00,0x3E,0x03,0x1E,0x30,0x1F,0x00}, /* s */
    {0x08,0x0C,0x3E,0x0C,0x0C,0x2C,0x18,0x00}, /* t */
    {0x00,0x00,0x33,0x33,0x33,0x33,0x6E,0x00}, /* u */
    {0x00,0x00,0x33,0x33,0x33,0x1E,0x0C,0x00}, /* v */
    {0x00,0x00,0x63,0x6B,0x7F,0x7F,0x36,0x00}, /* w */
    {0x00,0x00,0x63,0x36,0x1C,0x36,0x63,0x00}, /* x */
    {0x00,0x00,0x33,0x33,0x33,0x3E,0x30,0x1F}, /* y */
    {0x00,0x00,0x3F,0x19,0x0C,0x26,0x3F,0x00}, /* z */
    {0x38,0x0C,0x0C,0x07,0x0C,0x0C,0x38,0x00}, /* { */
    {0x18,0x18,0x18,0x00,0x18,0x18,0x18,0x00}, /* | */
    {0x07,0x0C,0x0C,0x38,0x0C,0x0C,0x07,0x00}, /* } */
    {0x6E,0x3B,0x00,0x00,0x00,0x00,0x00,0x00}, /* ~ */
};

static atomic_int s_enabled, s_open;
static atomic_flag s_lock = ATOMIC_FLAG_INIT;
static DwEvoModel s_model;
static char s_lines[MAX_LINES][LINE_BYTES];
static int s_nlines;
static unsigned s_seq, s_draws;

static void lock(void) { while (atomic_flag_test_and_set(&s_lock)) {} }
static void unlock(void) { atomic_flag_clear(&s_lock); }

/* ---- formatting ---------------------------------------------------------- */
typedef struct {
    char (*lines)[LINE_BYTES];
    int n, vis, used;     /* line count, visible chars on the open line, bytes */
} Fmt;

static void newline(Fmt* f) {
    if (f->n < MAX_LINES - 1) f->n++;
    f->lines[f->n][0] = 0;
    f->vis = f->used = 0;
}

/* Append one item; wraps (indented) when it would pass the line width. */
static void put(Fmt* f, const char* colour, const char* text) {
    const int len = (int)strlen(text);
    if (f->vis && f->vis + len > LINE_CHARS) {
        newline(f);
        put(f, C_WHITE, "    ");
    }
    if (f->used + len + 2 >= LINE_BYTES) return;
    f->lines[f->n][f->used++] = colour[0];
    memcpy(&f->lines[f->n][f->used], text, (size_t)len);
    f->used += len;
    f->vis += len;
    f->lines[f->n][f->used] = 0;
}

static const char* level_name(int lv) {
    static const char* const names[] = { "?", "Fresh", "In-Training", "Rookie",
                                         "Champion", "Ultimate" };
    return (lv >= 1 && lv <= 5) ? names[lv] : names[0];
}

static void format_candidate(Fmt* f, const DwEvoModel* m, const DwEvoCandidate* c) {
    static const char* const stat_names[6] = { "HP", "MP", "Off", "Def", "Spd", "Brn" };
    char b[64];
    int i;
    const char* ok = C_GREEN;
    const char* no = C_RED;

    snprintf(b, sizeof b, "%s %-14s", c->target == m->pick ? ">" : " ", c->name);
    put(f, c->qualifies ? C_GREEN : C_WHITE, b);
    snprintf(b, sizeof b, " %d of 4 met, need 3", c->points);
    put(f, c->qualifies ? C_GREEN : C_WHITE, b);
    if (c->raised_adj) {
        snprintf(b, sizeof b, " raised:%s", c->raised_adj < 0 ? "0" : "+1");
        put(f, C_YELLOW, b);
    }
    if (c->mean >= 0) {
        snprintf(b, sizeof b, " mean %d", c->mean);
        put(f, C_GREY, b);
    }
    newline(f);

    put(f, C_WHITE, "   ");
    snprintf(b, sizeof b, "Care %d (%s %d) ", m->care, (c->flags & 0x10) ? "max" : "min",
             c->req[DW_REQ_CARE]);
    put(f, c->care_met ? ok : no, b);
    snprintf(b, sizeof b, "Weight %d (%d-%d)", m->weight, c->req[DW_REQ_WEIGHT] - 5,
             c->req[DW_REQ_WEIGHT] + 5);
    put(f, c->weight_met ? ok : no, b);
    newline(f);

    put(f, C_WHITE, "   ");
    if (c->level == 3) {
        snprintf(b, sizeof b, "Top stat %s %d ", stat_names[c->highest],
                 m->stats[c->highest]);
        put(f, c->stats_met ? ok : no, b);
        put(f, C_GREY, "wants:");
        for (i = 0; i < 6; i++)
            if (c->req[DW_REQ_HP + i] == 1) {
                snprintf(b, sizeof b, " %s", stat_names[i]);
                put(f, C_GREY, b);
            }
    } else {
        for (i = 0; i < 6; i++) {
            if (c->req[DW_REQ_HP + i] == -1) continue;
            snprintf(b, sizeof b, "%s %d/%d ", stat_names[i], m->stats[i], c->req[DW_REQ_HP + i]);
            put(f, c->stat_met[i] ? ok : no, b);
        }
    }
    newline(f);

    if (c->bonus_present) {
        put(f, c->bonus_met ? ok : no, "   One of:");
        if (c->bonus_present & DW_BONUS_FROM) {
            snprintf(b, sizeof b, " from %s", c->from_name[0] ? c->from_name : "?");
            put(f, (c->bonus_hit & DW_BONUS_FROM) ? ok : no, b);
        }
        if (c->bonus_present & DW_BONUS_DISC) {
            snprintf(b, sizeof b, " Disc %d/%d", m->discipline, c->req[DW_REQ_DISC]);
            put(f, (c->bonus_hit & DW_BONUS_DISC) ? ok : no, b);
        }
        if (c->bonus_present & DW_BONUS_HAPPY) {
            snprintf(b, sizeof b, " Happy %d/%d", m->happiness, c->req[DW_REQ_HAPPY]);
            put(f, (c->bonus_hit & DW_BONUS_HAPPY) ? ok : no, b);
        }
        if (c->bonus_present & DW_BONUS_BATTLES) {
            snprintf(b, sizeof b, " Battles %d (%s %d)", m->battles,
                     (c->flags & 1) ? "max" : "min", c->req[DW_REQ_BATTLES]);
            put(f, (c->bonus_hit & DW_BONUS_BATTLES) ? ok : no, b);
        }
        if (c->bonus_present & DW_BONUS_TECHS) {
            snprintf(b, sizeof b, " Techs %d/%d", m->techs, c->req[DW_REQ_TECHS]);
            put(f, (c->bonus_hit & DW_BONUS_TECHS) ? ok : no, b);
        }
        newline(f);
    }
}

static int format_model(const DwEvoModel* m, char (*lines)[LINE_BYTES]) {
    Fmt f = { lines, 0, 0, 0 };
    char b[96];
    int i;
    lines[0][0] = 0;
    put(&f, C_YELLOW, "EVOLUTION GUIDE");
    if (!m->ok) {
        newline(&f);
        put(&f, C_WHITE, psx_mod_read_half(0x800ED2A0u) == 0x801Fu
            ? "Not available on Digimon World 1.5."
            : "Not available: no partner, or unknown evolution code.");
        newline(&f);
        return f.n;
    }
    snprintf(b, sizeof b, "  %s, %s", m->name, level_name(m->level));
    put(&f, C_WHITE, b);
    newline(&f);
    if (!m->threshold) {
        put(&f, C_GREY, "An Ultimate does not evolve further.");
        newline(&f);
        return f.n;
    }
    snprintf(b, sizeof b, "Evolution timer %d / %d h", m->timer, m->threshold);
    put(&f, m->timer >= m->threshold ? C_GREEN : C_WHITE, b);
    if (!m->pick_defined)
        snprintf(b, sizeof b, "   Now: undefined");
    else if (m->pick == -1)
        snprintf(b, sizeof b, "   Now: none");
    else
        snprintf(b, sizeof b, "   Now: %s", m->pick_name);
    put(&f, C_YELLOW, b);
    newline(&f);
    newline(&f);
    if (m->level == 1) {
        for (i = 0; i < m->n; i++) {
            snprintf(b, sizeof b, "%s %s: no requirements", m->c[i].target == m->pick ? ">" : " ",
                     m->c[i].name);
            put(&f, C_WHITE, b);
            newline(&f);
        }
        return f.n;
    }
    for (i = 0; i < m->n; i++)
        format_candidate(&f, m, &m->c[i]);
    if (!m->n) {
        put(&f, C_GREY, "No evolution paths.");
        newline(&f);
    }
    return f.n;
}

/* ---- VBlank: model + text ------------------------------------------------- */
static void guide_vblank(void) {
    static DwEvoModel m;
    static char lines[MAX_LINES][LINE_BYTES];
    int n;
    if (!atomic_load(&s_open))
        return;
    dw_evo_model(&m);
    n = format_model(&m, lines);
    lock();
    s_model = m;
    memcpy(s_lines, lines, sizeof lines);
    s_nlines = n;
    s_seq++;
    unlock();
}

/* ---- Input ---------------------------------------------------------------- */
#if defined(PSX_SDL3)
static bool SDLCALL key_watch(void* ud, SDL_Event* e) {
    (void)ud;
    if (e->type == SDL_EVENT_KEY_DOWN && !e->key.repeat && e->key.scancode == SDL_SCANCODE_F2 &&
        atomic_load(&s_enabled) && !psx_host_ui_capture_active())
        atomic_fetch_xor(&s_open, 1);
    return true;
}
#else
static int SDLCALL key_watch(void* ud, SDL_Event* e) {
    (void)ud;
    if (e->type == SDL_KEYDOWN && !e->key.repeat && e->key.keysym.scancode == SDL_SCANCODE_F2 &&
        atomic_load(&s_enabled) && !psx_host_ui_capture_active())
        atomic_fetch_xor(&s_open, 1);
    return 1;
}
#endif

static int pad_transform(const PSXModPadFrame* frame, PSXModPadOutput* out) {
    static int watching, was_down;
    const int down = (frame->buttons & (PAD_SELECT | PAD_L3)) == 0;
    if (!watching) {          /* main thread, SDL is up: add the F2 watch once */
        SDL_AddEventWatch(key_watch, NULL);
        watching = 1;
    }
    if (down && !was_down && atomic_load(&s_enabled))
        atomic_fetch_xor(&s_open, 1);
    was_down = down;
    if (!down)
        return 0;
    out->buttons |= PAD_SELECT | PAD_L3;
    return 1;
}

/* ---- Draw (GL thread) ----------------------------------------------------- */
#define EG_FRAGMENT_SHADER 0x8B30
#define EG_VERTEX_SHADER 0x8B31
#define EG_LINK_STATUS 0x8B82
#define EG_CURRENT_PROGRAM 0x8B8D
#define EG_VERTEX_ARRAY_BINDING 0x85B5
#define EG_ACTIVE_TEXTURE 0x84E0
#define EG_TEXTURE0 0x84C0
#define EG_BLEND_EQUATION_RGB 0x8009
#define EG_BLEND_EQUATION_ALPHA 0x883D
#define EG_BLEND_DST_RGB 0x80C8
#define EG_BLEND_SRC_RGB 0x80C9
#define EG_BLEND_DST_ALPHA 0x80CA
#define EG_BLEND_SRC_ALPHA 0x80CB
#define EG_FUNC_ADD 0x8006
#define EG_PIXEL_UNPACK_BUFFER 0x88EC
#define EG_PIXEL_UNPACK_BUFFER_BINDING 0x88EF
#define EG_SAMPLER_BINDING 0x8919
#define EG_BGRA 0x80E1
#define EG_RGBA8 0x8058
#define EG_CLAMP_TO_EDGE 0x812F

typedef GLuint (APIENTRY* FnCreateShader)(GLenum);
typedef void (APIENTRY* FnShaderSource)(GLuint, GLsizei, const char* const*, const GLint*);
typedef void (APIENTRY* FnUint)(GLuint);
typedef GLuint (APIENTRY* FnCreateProgram)(void);
typedef void (APIENTRY* FnAttach)(GLuint, GLuint);
typedef void (APIENTRY* FnGetProgramiv)(GLuint, GLenum, GLint*);
typedef GLint (APIENTRY* FnGetUniformLocation)(GLuint, const char*);
typedef void (APIENTRY* FnUniform1i)(GLint, GLint);
typedef void (APIENTRY* FnUniform4f)(GLint, GLfloat, GLfloat, GLfloat, GLfloat);
typedef void (APIENTRY* FnGenVertexArrays)(GLsizei, GLuint*);
typedef void (APIENTRY* FnEnum)(GLenum);
typedef void (APIENTRY* FnEnum2)(GLenum, GLenum);
typedef void (APIENTRY* FnEnum4)(GLenum, GLenum, GLenum, GLenum);
typedef void (APIENTRY* FnBindBuffer)(GLenum, GLuint);
typedef void (APIENTRY* FnBindSampler)(GLuint, GLuint);

static struct {
    void* ctx;
    int ok;
    GLuint prog, vao, tex;
    GLint u_rect, u_tex;
    int tw, th;
    FnUint UseProgram, BindVertexArray;
    FnUniform1i Uniform1i;
    FnUniform4f Uniform4f;
    FnEnum ActiveTexture;
    FnEnum2 BlendEquationSeparate;
    FnEnum4 BlendFuncSeparate;
    FnBindBuffer BindBuffer;
    FnBindSampler BindSampler;
} G;

static const char* const VS =
    "#version 330\n"
    "uniform vec4 uRect;\n"
    "out vec2 vUv;\n"
    "void main() {\n"
    "  vec2 p = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));\n"
    "  vUv = vec2(p.x, 1.0 - p.y);\n"
    "  gl_Position = vec4(mix(uRect.xy, uRect.zw, p), 0.0, 1.0);\n"
    "}\n";
static const char* const FS =
    "#version 330\n"
    "uniform sampler2D uTex;\n"
    "in vec2 vUv;\n"
    "out vec4 o;\n"
    "void main() { o = texture(uTex, vUv); }\n";

#define PROC(type, name) ((type)SDL_GL_GetProcAddress(name))

static int gl_init(void) {
    FnCreateShader CreateShader = PROC(FnCreateShader, "glCreateShader");
    FnShaderSource ShaderSource = PROC(FnShaderSource, "glShaderSource");
    FnUint CompileShader = PROC(FnUint, "glCompileShader");
    FnCreateProgram CreateProgram = PROC(FnCreateProgram, "glCreateProgram");
    FnAttach AttachShader = PROC(FnAttach, "glAttachShader");
    FnUint LinkProgram = PROC(FnUint, "glLinkProgram");
    FnGetProgramiv GetProgramiv = PROC(FnGetProgramiv, "glGetProgramiv");
    FnGetUniformLocation GetUniformLocation = PROC(FnGetUniformLocation, "glGetUniformLocation");
    FnGenVertexArrays GenVertexArrays = PROC(FnGenVertexArrays, "glGenVertexArrays");
    GLuint vs, fs;
    GLint linked = 0;

    memset(&G, 0, sizeof G);
    G.ctx = (void*)SDL_GL_GetCurrentContext();
    G.UseProgram = PROC(FnUint, "glUseProgram");
    G.BindVertexArray = PROC(FnUint, "glBindVertexArray");
    G.Uniform1i = PROC(FnUniform1i, "glUniform1i");
    G.Uniform4f = PROC(FnUniform4f, "glUniform4f");
    G.ActiveTexture = PROC(FnEnum, "glActiveTexture");
    G.BlendEquationSeparate = PROC(FnEnum2, "glBlendEquationSeparate");
    G.BlendFuncSeparate = PROC(FnEnum4, "glBlendFuncSeparate");
    G.BindBuffer = PROC(FnBindBuffer, "glBindBuffer");
    G.BindSampler = PROC(FnBindSampler, "glBindSampler");
    if (!CreateShader || !ShaderSource || !CompileShader || !CreateProgram ||
        !AttachShader || !LinkProgram || !GetProgramiv || !GetUniformLocation ||
        !GenVertexArrays || !G.UseProgram || !G.BindVertexArray || !G.Uniform1i ||
        !G.Uniform4f || !G.ActiveTexture || !G.BlendEquationSeparate ||
        !G.BlendFuncSeparate || !G.BindBuffer || !G.BindSampler)
        return 0;
    vs = CreateShader(EG_VERTEX_SHADER);
    fs = CreateShader(EG_FRAGMENT_SHADER);
    ShaderSource(vs, 1, &VS, NULL);
    ShaderSource(fs, 1, &FS, NULL);
    CompileShader(vs);
    CompileShader(fs);
    G.prog = CreateProgram();
    AttachShader(G.prog, vs);
    AttachShader(G.prog, fs);
    LinkProgram(G.prog);
    GetProgramiv(G.prog, EG_LINK_STATUS, &linked);
    if (!linked)
        return 0;
    G.u_rect = GetUniformLocation(G.prog, "uRect");
    G.u_tex = GetUniformLocation(G.prog, "uTex");
    GenVertexArrays(1, &G.vao);
    glGenTextures(1, &G.tex);
    G.ok = 1;
    return 1;
}

static void rasterise(uint32_t* px, int w, int h, char (*lines)[LINE_BYTES], int n) {
    int i, x, y, r;
    for (i = 0; i < w * h; i++) px[i] = BACKGROUND;
    for (i = 0; i < n; i++) {
        const unsigned char* s = (const unsigned char*)lines[i];
        uint32_t colour = COLOURS[1];
        x = PAD_PX;
        y = PAD_PX + i * LINE_H;
        for (; *s; s++) {
            if (*s < 6) { colour = COLOURS[*s]; continue; }
            if (*s >= 32 && *s < 127 && x + GLYPH <= w && y + GLYPH <= h) {
                const uint8_t* g = FONT8X8[*s - 32];
                for (r = 0; r < GLYPH; r++) {
                    int c;
                    for (c = 0; c < GLYPH; c++)
                        if (g[r] & (1u << c)) px[(y + r) * w + x + c] = colour;
                }
            }
            x += GLYPH;
        }
    }
}

static void guide_draw(int ww, int wh, void* ctx) {
    static char lines[MAX_LINES][LINE_BYTES];
    static uint32_t px[(LINE_CHARS * GLYPH + 2 * PAD_PX) * (MAX_LINES * LINE_H + 2 * PAD_PX)];
    int n, pw, ph, scale, x0, y0, sx, sy;
    GLint vp[4], prog, vao, active, tex, unpack, sampler, row_len, align;
    GLint bsrc, bdst, bsrca, bdsta, beq, beqa;
    GLboolean blend, scissor, depth, stencil, cull;
    (void)ctx;
    if (!atomic_load(&s_open))
        return;
    lock();
    n = s_nlines;
    memcpy(lines, s_lines, sizeof lines);
    unlock();
    if (n <= 0)
        return;
    if (!G.ok || G.ctx != (void*)SDL_GL_GetCurrentContext()) {
        if (!gl_init())
            return;
    }
    pw = LINE_CHARS * GLYPH + 2 * PAD_PX;
    ph = n * LINE_H + 2 * PAD_PX - (LINE_H - GLYPH);
    rasterise(px, pw, ph, lines, n);
    /* Largest integer scale that keeps the panel inside 92% of the height
     * and 60% of the width, at least 1. */
    scale = (wh * 92 / 100) / ph;
    if ((ww * 60 / 100) / pw < scale) scale = (ww * 60 / 100) / pw;
    if (scale < 1) scale = 1;
    x0 = ww / 40;
    y0 = (wh - ph * scale) / 2;
    if (y0 < 0) y0 = 0;
    sx = pw * scale;
    sy = ph * scale;

    glGetIntegerv(GL_VIEWPORT, vp);
    glGetIntegerv(EG_CURRENT_PROGRAM, &prog);
    glGetIntegerv(EG_VERTEX_ARRAY_BINDING, &vao);
    glGetIntegerv(EG_ACTIVE_TEXTURE, &active);
    G.ActiveTexture(EG_TEXTURE0);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &tex);
    glGetIntegerv(EG_SAMPLER_BINDING, &sampler);
    glGetIntegerv(EG_PIXEL_UNPACK_BUFFER_BINDING, &unpack);
    glGetIntegerv(GL_UNPACK_ROW_LENGTH, &row_len);
    glGetIntegerv(GL_UNPACK_ALIGNMENT, &align);
    glGetIntegerv(EG_BLEND_SRC_RGB, &bsrc);
    glGetIntegerv(EG_BLEND_DST_RGB, &bdst);
    glGetIntegerv(EG_BLEND_SRC_ALPHA, &bsrca);
    glGetIntegerv(EG_BLEND_DST_ALPHA, &bdsta);
    glGetIntegerv(EG_BLEND_EQUATION_RGB, &beq);
    glGetIntegerv(EG_BLEND_EQUATION_ALPHA, &beqa);
    blend = glIsEnabled(GL_BLEND);
    scissor = glIsEnabled(GL_SCISSOR_TEST);
    depth = glIsEnabled(GL_DEPTH_TEST);
    stencil = glIsEnabled(GL_STENCIL_TEST);
    cull = glIsEnabled(GL_CULL_FACE);

    G.BindBuffer(EG_PIXEL_UNPACK_BUFFER, 0);
    G.BindSampler(0, 0);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glBindTexture(GL_TEXTURE_2D, G.tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, EG_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, EG_CLAMP_TO_EDGE);
    if (G.tw != pw || G.th != ph) {
        glTexImage2D(GL_TEXTURE_2D, 0, EG_RGBA8, pw, ph, 0, EG_BGRA, GL_UNSIGNED_BYTE, px);
        G.tw = pw;
        G.th = ph;
    } else {
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, pw, ph, EG_BGRA, GL_UNSIGNED_BYTE, px);
    }
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_STENCIL_TEST);
    glDisable(GL_CULL_FACE);
    glEnable(GL_BLEND);
    G.BlendEquationSeparate(EG_FUNC_ADD, EG_FUNC_ADD);
    G.BlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    glViewport(0, 0, ww, wh);
    G.UseProgram(G.prog);
    G.Uniform1i(G.u_tex, 0);
    G.Uniform4f(G.u_rect, 2.0f * x0 / ww - 1.0f, 1.0f - 2.0f * (y0 + sy) / wh,
                2.0f * (x0 + sx) / ww - 1.0f, 1.0f - 2.0f * y0 / wh);
    G.BindVertexArray(G.vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);

    G.BindVertexArray((GLuint)vao);
    G.UseProgram((GLuint)prog);
    glViewport(vp[0], vp[1], vp[2], vp[3]);
    G.BlendFuncSeparate((GLenum)bsrc, (GLenum)bdst, (GLenum)bsrca, (GLenum)bdsta);
    G.BlendEquationSeparate((GLenum)beq, (GLenum)beqa);
    if (!blend) glDisable(GL_BLEND);
    if (scissor) glEnable(GL_SCISSOR_TEST);
    if (depth) glEnable(GL_DEPTH_TEST);
    if (stencil) glEnable(GL_STENCIL_TEST);
    if (cull) glEnable(GL_CULL_FACE);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, row_len);
    glPixelStorei(GL_UNPACK_ALIGNMENT, align);
    glBindTexture(GL_TEXTURE_2D, (GLuint)tex);
    G.BindSampler(0, (GLuint)sampler);
    G.BindBuffer(EG_PIXEL_UNPACK_BUFFER, (GLuint)unpack);
    G.ActiveTexture((GLenum)active);
    lock();
    s_draws++;
    unlock();
}

/* ---- Test visibility ------------------------------------------------------ */
static int guide_command(const char* args, char* reply, int cap) {
    static DwEvoModel m;
    unsigned seq, draws;
    int i, k, at;
    if (!strcmp(args, "open") || !strcmp(args, "close")) {
        atomic_store(&s_open, args[0] == 'o');
        snprintf(reply, (size_t)cap, "{\"open\":%d}", atomic_load(&s_open));
        return 0;
    }
    if (!strcmp(args, "lines")) {      /* the panel text, colour codes dropped */
        at = 0;
        lock();
        for (i = 0; i < s_nlines && at < cap - 2; i++) {
            const char* s;
            for (s = s_lines[i]; *s && at < cap - 2; s++)
                if ((unsigned char)*s >= 6) reply[at++] = *s == '"' ? '\'' : *s;
            reply[at++] = '|';
        }
        unlock();
        reply[at] = 0;
        return 0;
    }
    lock();
    m = s_model;
    seq = s_seq;
    draws = s_draws;
    unlock();
    if (!strcmp(args, "state")) {
        snprintf(reply, (size_t)cap,
                 "{\"enabled\":%d,\"open\":%d,\"seq\":%u,\"draws\":%u,\"ok\":%d,"
                 "\"partner\":%d,\"name\":\"%s\",\"lv\":%d,\"timer\":%d,\"thr\":%d,"
                 "\"pick\":%d,\"defined\":%d,\"n\":%d,\"stats\":[%d,%d,%d,%d,%d,%d],"
                 "\"care\":%d,\"weight\":%d,\"disc\":%d,\"happy\":%d,\"battles\":%d,\"techs\":%d}",
                 atomic_load(&s_enabled), atomic_load(&s_open), seq, draws, m.ok,
                 (int)m.partner, m.name, m.level, m.timer, m.threshold, m.pick,
                 m.pick_defined, m.n, m.stats[0], m.stats[1], m.stats[2], m.stats[3],
                 m.stats[4], m.stats[5], m.care, m.weight, m.discipline, m.happiness,
                 m.battles, m.techs);
        return 0;
    }
    if (args[0] == 't' && args[1] == ' ') {
        const DwEvoCandidate* c;
        i = atoi(args + 2);
        if (i < 0 || i >= m.n) {
            snprintf(reply, (size_t)cap, "{\"error\":\"no target %d\"}", i);
            return 1;
        }
        c = &m.c[i];
        at = snprintf(reply, (size_t)cap, "{\"t\":%d,\"name\":\"%s\",\"lv\":%d,\"req\":[",
                      c->target, c->name, c->level);
        for (k = 0; k < DW_REQ_COUNT && at < cap; k++)
            at += snprintf(reply + at, (size_t)(cap - at), k ? ",%d" : "%d", c->req[k]);
        if (at < cap)
            snprintf(reply + at, (size_t)(cap - at),
                     "],\"flags\":%d,\"care\":%d,\"wt\":%d,\"st\":%d,\"sm\":[%d,%d,%d,%d,%d,%d],"
                     "\"hi\":%d,\"bp\":%d,\"bh\":%d,\"bonus\":%d,\"pts\":%d,\"score\":%d,"
                     "\"adj\":%d,\"q\":%d,\"mean\":%d}",
                     c->flags, c->care_met, c->weight_met, c->stats_met, c->stat_met[0],
                     c->stat_met[1], c->stat_met[2], c->stat_met[3], c->stat_met[4],
                     c->stat_met[5], c->highest, c->bonus_present, c->bonus_hit, c->bonus_met,
                     c->points, c->score, c->raised_adj, c->qualifies, c->mean);
        return 0;
    }
    snprintf(reply, (size_t)cap, "{\"error\":\"open|close|state|t <i>\"}");
    return 1;
}

/* ---- Lifecycle ------------------------------------------------------------ */
static void guide_activate(void) {
    PSXModPadTransform xf;
    atomic_store(&s_open, 0);
    atomic_store(&s_enabled, 1);
    psx_host_set_hud_draw_cb(guide_draw, NULL);
    memset(&xf, 0, sizeof xf);
    xf.struct_size = sizeof xf;
    xf.allowed_types = PAD_ALL_TYPES;
    xf.initial_type = PSX_MOD_PAD_DIGITAL;   /* game.toml [controller] default_mode */
    xf.transform = pad_transform;
    psx_mod_set_pad_transform(0, &xf);
    printf("dw_evoguide: active (F2 or Select + L3)\n");
    fflush(stdout);
}

/* Every plan activation (this plugin selected or not). The runtime clears the
 * HUD callback and pad transforms itself; this only quiets the key watch. */
static void guide_reset(void) {
    atomic_store(&s_enabled, 0);
    atomic_store(&s_open, 0);
}

PSX_MOD_CONSTRUCTOR(dw_evoguide_register) {
    psx_mod_register_activation_plugin("dw.evoguide", guide_activate);
    psx_mod_register_session_reset_plugin("dw.evoguide", guide_reset);
    psx_mod_register_vblank_plugin("dw.evoguide", guide_vblank);
    psx_mod_register_command("evoguide", guide_command);
}
