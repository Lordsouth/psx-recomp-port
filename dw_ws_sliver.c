/* Deep widescreen, phase 5: the fifth backdrop tile column.
 *
 * The field backdrop streams as a ring of FOUR 128-px tile columns -- 512
 * world px, held in VRAM slots x 768..1023 -- and the widened 16:9 view is 426
 * px. The ring covers the view only when its origin 128*T lands in
 * [C-139, C-53], an 87-wide target on a 128-spaced lattice, so about a third
 * of camera positions cannot be covered by ANY four-column policy. Phase 4's
 * ladder shift (dw_ws_camera.c) makes the ring cover the wide view's LEFT
 * edge, which puts the whole shortfall on the right: the first few dozen px
 * of column T+4, the column the ring streams next. In theory at most 41; in
 * practice up to 50 measured, because the streaming ladder's hysteresis lets
 * the origin lag the ideal one by up to 15 px. Walking left never produced a
 * left-edge deficit in 387 measured stops, so only the right is handled.
 *
 * A fifth column has nowhere to live. A VRAM census (research/widescreen-
 * survey/VRAM.md, three methods, disc-wide TIM census plus runtime reads and
 * writes) found no free 64x128 slot anywhere. But the deficit is not a column,
 * it is a strip of one, and the strip the census cleared -- x 651..703,
 * between the Digi-Card image page (8bpp, 75 halfwords from 576, so it ends
 * at 650) and the text raster (starts 704), rows 256..510 -- holds it with
 * room to spare once the three tile rows are STACKED rather than placed side
 * by side: only the visible rows of each tile are needed, and the view is 240
 * rows tall, so the three strips together never exceed 240 of the 255 rows
 * available, and the full 53-halfword width (106 px) is free for the deficit.
 * Row 511 carries the card CLUT and is never touched.
 *
 * So, on every frame the ring falls short:
 *
 *   1. read the VISIBLE rows of column T+4's tiles out of a host copy of the
 *      map's tile file (8bpp, 128 bytes per row), as many leading halfwords
 *      per row as the deficit needs -- the same bytes the game's own
 *      LoadImage uploads from its tile store (see dw_tfs_load for why the
 *      copy, and not the store itself);
 *   2. upload them, stacked, with psx_mod_vram_write -- the framework
 *      primitive added for this, since a plugin cannot call LoadImage, or any
 *      guest code;
 *   3. build up to three POLY_FT4 quads in the game's own packet buffer and
 *      link them into the bucket the tiles use, ot[0xfff], exactly as
 *      renderMap does with AddPrim: same CLUT, same draw offset, same depth.
 *
 * It runs as an exit hook on renderMap (0x800D57EC): the tiles are linked,
 * the packet pointer sits just past the last prim the game built, and the
 * camera/offset pair is final for the frame. Nothing is read or written when
 * the ring already covers the view, when widescreen is off, in squash mode, or
 * on native-wide frames presented 4:3 (psx_mod_ws_native_wide_offset is 0 on
 * all of those; this is a stateless per-frame draw, so the exact offset is the
 * right gate).
 *
 * Verified by walking: at every stop where the ring fell short (deficits 5
 * to 50 px, maps 2, 5, 12) the right reveal measured the same 0-3% dark as
 * the stops beside it, and the sliver's pixels matched the ring's own
 * rendering of the same world columns at exactly zero shift (mean luma diff
 * 0.0-6.7 at shift 0 against 9-20 one pixel either side).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "mod_plugins.h"

extern int32_t psx_mod_ws_native_wide_offset(void);

#define DW_RENDER_MAP 0x800D57ECu
#define DW_READ_MAP_TFS 0x800D6308u         /* readMapTFS(int32 mapId) */

#define DW_CAMERA_X 0x80150C38u             /* int16 */
#define DW_CAMERA_Y 0x80150C3Au             /* int16 */
#define DW_MAP_WIDTH 0x80150C3Cu            /* int8  */
#define DW_MAP_HEIGHT 0x80150C3Du           /* int8  */
#define DW_MAP_TILE_X 0x80134DC2u           /* int8  */
#define DW_MAP_TILE_Y 0x80134DC4u           /* int8  */
#define DW_DRAWING_OFFSET_X 0x80134EC0u     /* int32 */
#define DW_DRAWING_OFFSET_Y 0x80134EC4u     /* int32 */
#define DW_DRAWING_OFFSET_X_COPY 0x80134EDCu /* int32, what GsSetOrign got */
#define DW_CURRENT_SCREEN 0x80134DA8u       /* uint8 */
#define DW_MAP_ENTRIES 0x801292D4u          /* MapEntry[255], 16 bytes each */
#define DW_MAP_ENTRY_BYTES 16
#define DW_MAP_ENTRY_COUNT 255
#define DW_MAP_NAME_MAX 10                  /* MapEntry.filename, char[10] */
#define DW_ACTIVE_ORDERING_TABLE 0x80134ED0u /* GsOT* */
#define DW_GSOT_ORG 4                       /* GsOT.org: GsOT_TAG* */
#define DW_GS_OUT_PACKET_P 0x80136FE4u      /* libgs work base (GsGetWorkBase) */
#define DW_GS_WORK_BASES 0x8015DBA8u        /* PACKET[2][0x14000] (btl.c:77) */
#define DW_GS_WORK_BASE_BYTES 0x28000u      /* ends at GS_VIEWPOINT 0x80185BA8 */

/* MapTileData (map.c:21-30), 20 bytes, MAP_TILE_DATA has room for 35 */
#define DW_MAP_TILE_DATA 0x8014F084u
#define DW_MAP_TILE_COUNT 35
#define DW_TILE_STRIDE 20
#define DW_TILE_IMAGE_PTR 0                 /* uint8_t*, 0 for an empty tile */
#define DW_TILE_ID 4                        /* int16, -1 = empty (drawn black) */
#define DW_TILE_CLUT 16                     /* int16, GetClut(0, 480) */

#define DW_RAM_BASE 0x80000000u
#define DW_RAM_SIZE 0x00200000u

#define DW_TILE_PX 128
#define DW_TILE_ROW_BYTES 128               /* 8bpp: 64 halfwords per row */
#define DW_TILE_BYTES (DW_TILE_PX * DW_TILE_ROW_BYTES)
/* .TFS layout (setupMap, map.c:4225-4262): int32, int32 numCluts, numCluts
 * 256-colour CLUTs, then for each painted tile in MAP_TILES order a 4-byte
 * header and the 128x128 8bpp image. */
#define DW_TFS_CLUT_BYTES 512
#define DW_TFS_CLUTS_MAX 16
#define DW_TFS_TILE_STRIDE (4 + DW_TILE_BYTES)
#define DW_TFS_MAX (8 + DW_TFS_CLUTS_MAX * DW_TFS_CLUT_BYTES + \
                    DW_MAP_TILE_COUNT * DW_TFS_TILE_STRIDE)
/* As in dw_ws_camera.c: a gap between the drawn and the stepped camera wider
 * than any one camera step is not trusted. */
#define DW_STEP_MAX 32
#define DW_RING_COLS 4
#define DW_RING_ROWS 3
#define DW_SCREEN_W 320
#define DW_SCREEN_H 240
#define DW_OT_TILE_BUCKET 0xfff             /* renderMap: AddPrim(&ot[0xfff]) */

/* The sliver column: x 651..703, rows 256..495 at most (240 of 255). */
#define DW_SLIVER_X 651
#define DW_SLIVER_Y 256
#define DW_SLIVER_MAX_HW 53                 /* halfwords per row: to 703 */
#define DW_SLIVER_MAX_PX (2 * DW_SLIVER_MAX_HW)
#define DW_SLIVER_TPAGE_X 640
#define DW_SLIVER_TPAGE_Y 256
/* GetTPage(1, 0, 640, 256): 8bpp, opaque, page x 640/64 = 10, page y 256 */
#define DW_SLIVER_TPAGE ((1u << 7) | (1u << 4) | (DW_SLIVER_TPAGE_X / 64))
#define DW_SLIVER_U0 ((DW_SLIVER_X - DW_SLIVER_TPAGE_X) * 2) /* 22 px */

#define DW_POLY_FT4_BYTES 40
#define DW_POLY_FT4_LEN 9u
#define DW_POLY_FT4_CODE 0x2Cu

/* The same cap dw_ws_camera.c applies, and it must stay the same number.
 *
 * Above it the camera tier latches OFF -- no widened map-edge clamp, no
 * tile-ladder shift -- and without the ladder shift the ring is no longer
 * aligned to the wide view's LEFT edge, which is the whole reason the deficit
 * lands on the right where this file can serve it. A sliver still drawing
 * under those conditions patches one edge of a frame that is voiding on the
 * other, which reads as a half-fixed bug rather than an unsupported setting.
 * So the tier engages and disengages as a unit.
 *
 * The bound this file could support alone is m <= 78 or so (2m - 65 <= 106
 * plus the ladder's measured hysteresis lag); the ring's own structural bound
 * is m <= 96. Neither admits 21:9's 120 px, which is why the game ships its
 * own widescreen manifest offering 16:9 and 16:10 only
 * (mods/packages/psx.enhancement.widescreen). 64 is kept because those are the
 * two aspects that are actually offered and measured; raising it would ship an
 * untested path. */
#define DW_MARGIN_MAX 64

struct dw_strip { int32_t row, vis0, vish; };

static uint16_t dw_pixels[DW_SLIVER_MAX_HW * DW_TILE_PX];

/* The sliver's pixel source: the map's tile file, read from disc.
 *
 * The tile store in RAM is not the map's art for the whole time the map is on
 * screen. During a battle it is the battle-effects heap, and after a flee or a
 * loss the game never reloads it -- tamer.c:850-861 sets
 * SKIP_DAYTIME_TRANSITION for exactly that reason, since its CLUT walk would
 * read the same garbage. Evolution and death overwrite it too. A strip
 * uploaded from it in those windows is garbage in the right reveal.
 *
 * So the strip is read from a host copy of the file instead, built ONLY from
 * psx_mod_read_disc_file and keyed by the map id the game itself asked for at
 * readMapTFS. Never copied from guest RAM: after a flee there is nothing
 * trustworthy there to copy, and the disc read is deterministic and already
 * carries every active sector mod (the repainted Item Shop and Jijimon edges
 * in this package's own manifest). Keying on readMapTFS's argument rather
 * than CURRENT_SCREEN is what keeps Toy Town right: its box script re-reads
 * with an explicit id (somethingToyTown, map_object.c:1163).
 *
 * A savestate load restores guest memory but not this copy, so it is dropped
 * and rebuilt on next use from CURRENT_SCREEN. Same when the copy is missing
 * because widescreen was off at the last read. */
static uint8_t dw_tfs[DW_TFS_MAX];
static uint32_t dw_tfs_size;
static int32_t dw_tfs_map = -1;             /* map id of the copy, -1 = none */
static int dw_tfs_tried;                    /* one disc read per invalidation */

static void dw_tfs_invalidate(void) {
    dw_tfs_map = -1;
    dw_tfs_size = 0;
    dw_tfs_tried = 0;
}

/* buildMapPath (map.c:4101): \MAP\MAP<id/15+1>\<filename><ext>. The name is
 * from MAP_ENTRIES, the executable's static map table; the bytes only ever
 * come from the disc. */
static int dw_tfs_read(int32_t map, uint8_t* buf, uint32_t cap, uint32_t* size) {
    char name[DW_MAP_NAME_MAX + 1];
    char path[48];
    int i;

    *size = 0;
    if (map < 0 || map >= DW_MAP_ENTRY_COUNT)
        return 0;
    for (i = 0; i < DW_MAP_NAME_MAX; i++) {
        name[i] = (char)psx_mod_read_byte(DW_MAP_ENTRIES +
                                          (uint32_t)map * DW_MAP_ENTRY_BYTES +
                                          (uint32_t)i);
        if (name[i] == '\0')
            break;
    }
    name[i] = '\0';
    if (i == 0)
        return 0;
    snprintf(path, sizeof path, "\\MAP\\MAP%d\\%s.TFS", (map / 15) + 1, name);
    return psx_mod_read_disc_file(path, buf, cap, size) && *size >= 8;
}

static void dw_bank_prepare(int32_t map);

static void dw_tfs_load(int32_t map) {
    uint32_t size;

    dw_tfs_invalidate();
    dw_tfs_tried = 1;
    if (!dw_tfs_read(map, dw_tfs, sizeof dw_tfs, &size))
        return;
    dw_tfs_size = size;
    dw_tfs_map = map;
    dw_bank_prepare(map);
}

static void dw_read_map_tfs_enter(struct CPUState* cpu, uint32_t address) {
    (void)address;
    if (psx_mod_widescreen_x_margin() <= 0) {
        dw_tfs_invalidate();                /* host only: nothing is read */
        return;
    }
    dw_tfs_load((int32_t)cpu->gpr[4]);      /* $a0 mapId */
}

/* Byte offset of MAP_TILE_DATA[idx]'s image in the copy, or 0. setupMap hands
 * out images in tile order, skipping empty tiles, and the tile ids it copied
 * into MAP_TILE_DATA survive a battle (the table is not in the heap). */
static uint32_t dw_tfs_tile_offset(uint32_t idx) {
    uint32_t off, k;
    int32_t ncl;

    if (dw_tfs_map < 0)
        return 0;
    ncl = (int32_t)((uint32_t)dw_tfs[4] | ((uint32_t)dw_tfs[5] << 8) |
                    ((uint32_t)dw_tfs[6] << 16) | ((uint32_t)dw_tfs[7] << 24));
    if (ncl < 1 || ncl > DW_TFS_CLUTS_MAX)
        return 0;
    off = 8u + (uint32_t)ncl * DW_TFS_CLUT_BYTES;
    for (k = 0; k < idx; k++) {
        if ((int16_t)psx_mod_read_half(DW_MAP_TILE_DATA + k * DW_TILE_STRIDE +
                                       DW_TILE_ID) != -1)
            off += DW_TFS_TILE_STRIDE;
    }
    off += 4;
    return off + DW_TILE_BYTES <= dw_tfs_size ? off : 0;
}

static int32_t dw_i8(uint32_t a) { return (int32_t)(int8_t)psx_mod_read_byte(a); }
static int32_t dw_i16(uint32_t a) { return (int32_t)(int16_t)psx_mod_read_half(a); }
static int32_t dw_i32(uint32_t a) { return (int32_t)psx_mod_read_word(a); }

static int dw_in_ram(uint32_t a, uint32_t len) {
    if (a < DW_RAM_BASE || a - DW_RAM_BASE >= DW_RAM_SIZE)
        return 0;
    return len <= DW_RAM_SIZE - (a - DW_RAM_BASE);
}

/* How far the frame's DRAWN camera leads CAMERA_X, in px (>= 0).
 *
 * The frame is scrolled by the camera it started with: GsSetOrign took
 * DRAWING_OFFSET_X before renderObjects (main.c:3947-3952), and renderMap's
 * follow step then moved CAMERA_X and the offset opposite each other. So on
 * screen the camera is C_eff = CAMERA_X + DRAWING_OFFSET_X - _COPY, one step
 * AHEAD of CAMERA_X on the right while walking left -- and a deficit computed
 * from CAMERA_X alone left exactly that step of void at the trailing edge:
 * 8 px on map 204, 4 on map 12, in runs of 3-30 frames, on 4-11% of
 * walking-left frames. Walking right, the lead is negative and the ladder
 * shift in dw_ws_camera.c covers the other edge. */
static int32_t dw_drawn_lead(void) {
    const int32_t e = dw_i32(DW_DRAWING_OFFSET_X) -
                      dw_i32(DW_DRAWING_OFFSET_X_COPY);   /* C_eff - C */
    return e > 0 && e <= DW_STEP_MAX ? e : 0;
}

static uint32_t dw_xy(int32_t x, int32_t y) {
    return ((uint32_t)x & 0xFFFFu) | (((uint32_t)y & 0xFFFFu) << 16);
}

static uint32_t dw_uv(int32_t u, int32_t v, uint32_t hi16) {
    return ((uint32_t)u & 0xFFu) | (((uint32_t)v & 0xFFu) << 8) | (hi16 << 16);
}

/* ---- The ring-free backdrop: the map drawn from texture banks (OpenGL) ----
 *
 * Everything above serves one gap of a four-column ring that cannot cover a
 * wide view at every camera position. On OpenGL there is a structural way
 * out: a texture bank, an immutable host-side image that GT3 packets from a
 * dedicated GPU-DMA arena sample instead of VRAM (mod_plugins.h). One bank
 * holds a whole tile file, so any tile of the map can be drawn on any frame
 * and the ring stops limiting the view.
 *
 * A bank is the map's painted tiles, id = map id + 1, built ONLY from the
 * tile file read from disc: the sliver's copy above when it holds that map,
 * otherwise a fresh disc read (the resolver, for a cold savestate restore that
 * meets a bank this process never built). Never from guest RAM: banks are
 * immutable for the process lifetime, so one bad copy would be permanent.
 * Painted tile k sits in its own texture-page-aligned slot, x = (k mod 16) *
 * 64 halfwords, y = 256 * (k / 16), so it is sampled from u, v = 0 just as a
 * ring slot is. The palette is NOT in the bank: GT3s read it live from VRAM
 * row 480 (psx_mod_define_texture_bank_live_clut), so the time-of-day
 * cross-fade and the palette fades land exactly as they do on the ring.
 *
 * Each frame at renderMap exit, every tile that meets the wide view plus 128
 * px on every side (wider than any camera step, so a frame drawn with the
 * previous step's camera is covered too) is emitted as two GT3 triangles that
 * copy renderMap's FT4 (map.c:3986-4004): colour 0x808080, the tile's own
 * texpage word with the slot's page, the tile's CLUT, V extent 127 for tiles
 * in ring row 1 (texV % 256 != 0: that slot starts at v = 128 and runs out of
 * 8-bit V) and 128 for the rest. Empty tiles (id -1) are a black F4, as the
 * game draws them. The chain is linked into ot[0xfff] directly BEHIND
 * renderMap's own tile quads, so it is drawn right after them: inside the
 * ring it paints the same pixels over them, outside it fills the reveal. If
 * the GPU later refuses a bank packet (an HD texture pack switched on), that
 * packet draws nothing and the ring underneath still does.
 *
 * Packets live in a 2 x 2800-byte arena allocated at activation, one half per
 * ACTIVE_FRAMEBUFFER, so a frame never rewrites the half the GPU may still be
 * walking. The arena is expansion memory, so savestates made with widescreen
 * on carry a section those made with it off do not; the banks themselves are
 * host state, rebuilt from disc, never saved.
 *
 * Where banks cannot draw -- Software renderer, HD texture packs, netplay's
 * CPU-authoritative path, a definition that failed -- the ring, the ladder
 * shift, the nudge and the sliver carry on exactly as before.
 */
#define DW_ACTIVE_FRAMEBUFFER 0x80134ECCu   /* 0/1, top of each frame */
#define DW_TILE_POS_X 6                     /* int16, col * 128 */
#define DW_TILE_POS_Y 8                     /* int16, row * 128 */
#define DW_TILE_TEX_V 12                    /* int16, ring slot y: 0/128/256 */
#define DW_TILE_TPAGE 14                    /* int16, GetTPage(1, 0, u, v) */
#define DW_TPAGE_PAGE 0x1Fu                 /* texpage x (bits 0-3), y (bit 4) */
#define DW_TPAGE_RING_MASK 0x18Cu           /* depth, and texpage x >= 12 ... */
#define DW_TPAGE_RING 0x08Cu                /* ... 8bpp at x 768-1023: a ring slot */
#define DW_TILE_CLUT_480 0x7800u            /* GetClut(0, 480) */
#define DW_BANK_WALK_MAX 128                /* prims looked at in ot[0xfff] */

#define DW_BANK_SLOT_COLS 16
#define DW_BANK_SLOT_HW 64                  /* one 128-px 8bpp tile row */
#define DW_BANK_SLOTS 32
#define DW_BANK_ROW1_Y 256                  /* second slot row, texpage y 256 */
#define DW_BANK_W_MAX (DW_BANK_SLOT_COLS * DW_BANK_SLOT_HW)
#define DW_BANK_H_MAX (DW_BANK_ROW1_Y + DW_TILE_PX)
#define DW_BANK_OVERDRAW DW_TILE_PX
#define DW_BANK_TILE_BYTES 80               /* two tagged GT3s, or a tagged F4 */
#define DW_BANK_HALF_BYTES (DW_MAP_TILE_COUNT * DW_BANK_TILE_BYTES)

#define DW_POLY_GT3_BYTES 40
#define DW_POLY_GT3_LEN 9u
#define DW_POLY_GT3_CODE 0x34u
#define DW_POLY_F4_BYTES 24
#define DW_POLY_F4_LEN 5u
#define DW_POLY_F4_CODE 0x28u
#define DW_GREY 0x808080u                   /* renderMap: setRGB0(0x80, ...) */

enum { DW_BANK_UNKNOWN, DW_BANK_READY, DW_BANK_FAILED };
static uint8_t dw_bank_state[DW_MAP_ENTRY_COUNT];
static uint32_t dw_bank_arena;
static volatile int dw_bank_off;            /* debug: mod_cmd dw.ws.banks off */
static uint16_t dw_bank_px[DW_BANK_W_MAX * DW_BANK_H_MAX];

/* Define bank map + 1 from a tile file image. */
static int dw_bank_define(int32_t map, const uint8_t* tfs, uint32_t size) {
    uint32_t first, n, k, y, x, w, h;
    int32_t ncl;

    if (size < 8)
        return 0;
    ncl = (int32_t)((uint32_t)tfs[4] | ((uint32_t)tfs[5] << 8) |
                    ((uint32_t)tfs[6] << 16) | ((uint32_t)tfs[7] << 24));
    if (ncl < 1 || ncl > DW_TFS_CLUTS_MAX)
        return 0;
    first = 8u + (uint32_t)ncl * DW_TFS_CLUT_BYTES;
    n = size > first ? (size - first) / DW_TFS_TILE_STRIDE : 0;
    if (n == 0 || n > DW_BANK_SLOTS)
        return 0;
    w = (n < DW_BANK_SLOT_COLS ? n : DW_BANK_SLOT_COLS) * DW_BANK_SLOT_HW;
    h = n > DW_BANK_SLOT_COLS ? DW_BANK_H_MAX : DW_TILE_PX;
    memset(dw_bank_px, 0, (size_t)w * h * sizeof dw_bank_px[0]);
    for (k = 0; k < n; k++) {
        const uint8_t* src = tfs + first + k * DW_TFS_TILE_STRIDE + 4;
        uint16_t* dst = dw_bank_px + (k / DW_BANK_SLOT_COLS) * DW_BANK_ROW1_Y * w +
                        (k % DW_BANK_SLOT_COLS) * DW_BANK_SLOT_HW;
        for (y = 0; y < DW_TILE_PX; y++, src += DW_TILE_ROW_BYTES, dst += w)
            for (x = 0; x < DW_BANK_SLOT_HW; x++)
                dst[x] = (uint16_t)(src[2 * x] | (src[2 * x + 1] << 8));
    }
    return psx_mod_define_texture_bank_live_clut((uint16_t)(map + 1), w, h,
                                                 dw_bank_px);
}

/* Build the bank for the map the sliver's copy holds, once per process. */
static void dw_bank_prepare(int32_t map) {
    if (!dw_bank_arena || map != dw_tfs_map ||
        dw_bank_state[map] != DW_BANK_UNKNOWN ||
        !psx_mod_texture_banks_supported())
        return;
    dw_bank_state[map] = dw_bank_define(map, dw_tfs, dw_tfs_size)
                             ? DW_BANK_READY : DW_BANK_FAILED;
    psx_mod_counter_add(dw_bank_state[map] == DW_BANK_READY ? "dw.ws.banks.defined"
                                                            : "dw.ws.banks.failed", 1);
}

/* The GPU met a bank this process has not built: a restored DMA queue after a
 * cold savestate restore. Rebuild it from disc. */
static int dw_bank_resolve(uint16_t id) {
    const int32_t map = (int32_t)id - 1;
    uint8_t* buf;
    uint32_t size;
    int ok;

    if (map < 0 || map >= DW_MAP_ENTRY_COUNT)
        return 0;
    if (map == dw_tfs_map) {
        ok = dw_bank_define(map, dw_tfs, dw_tfs_size);
    } else {
        buf = (uint8_t*)malloc(DW_TFS_MAX);
        ok = buf && dw_tfs_read(map, buf, DW_TFS_MAX, &size) &&
             dw_bank_define(map, buf, size);
        free(buf);
    }
    dw_bank_state[map] = ok ? DW_BANK_READY : DW_BANK_FAILED;
    psx_mod_counter_add(ok ? "dw.ws.banks.resolved" : "dw.ws.banks.failed", 1);
    return ok;
}

/* The bank of the map on screen, or 0 when this frame belongs to the ring. */
static uint16_t dw_bank_current(void) {
    if (!dw_bank_arena || !psx_mod_texture_banks_supported())
        return 0;
    if (dw_tfs_map < 0 && !dw_tfs_tried)
        dw_tfs_load(psx_mod_read_byte(DW_CURRENT_SCREEN));
    if (dw_tfs_map < 0)
        return 0;
    dw_bank_prepare(dw_tfs_map);
    return dw_bank_state[dw_tfs_map] == DW_BANK_READY
               ? (uint16_t)(dw_tfs_map + 1) : 0;
}

/* For dw_ws_camera.c: the ring no longer has to cover the view, so the
 * load-time nudge that exists only to make it cover stays out of the way. */
int dw_ws_banks_active(void) {
    return psx_mod_widescreen_x_margin() > 0 && dw_bank_current() != 0;
}

static void dw_put_gt3(uint32_t p, uint32_t next, uint16_t bank, int32_t x0,
                       int32_t y0, int32_t u0, int32_t v0, int32_t x1,
                       int32_t y1, int32_t u1, int32_t v1, int32_t x2,
                       int32_t y2, int32_t u2, int32_t v2, uint32_t clut,
                       uint32_t tpage) {
    psx_mod_write_word(p + 0, (DW_POLY_GT3_LEN << 24) | (next & 0xFFFFFFu));
    psx_mod_write_word(p + 4, (DW_POLY_GT3_CODE << 24) | DW_GREY);
    psx_mod_write_word(p + 8, dw_xy(x0, y0));
    psx_mod_write_word(p + 12, dw_uv(u0, v0, clut));
    psx_mod_write_word(p + 16, ((uint32_t)(bank & 0xFFu) << 24) | DW_GREY);
    psx_mod_write_word(p + 20, dw_xy(x1, y1));
    psx_mod_write_word(p + 24, dw_uv(u1, v1, tpage));
    psx_mod_write_word(p + 28, ((uint32_t)(bank >> 8) << 24) | DW_GREY);
    psx_mod_write_word(p + 32, dw_xy(x2, y2));
    psx_mod_write_word(p + 36, dw_uv(u2, v2, 0));
}

static int dw_bank_draw(int32_t m) {
    uint8_t slot[DW_MAP_TILE_COUNT];
    int32_t w, h, c, cy, dox, doy, col0, col1, row0, row1, col, row, i, n;
    uint32_t ot, org, tail, link, first, p, last, lastlen;
    uint16_t bank;

    if (dw_bank_off)
        return 0;
    bank = dw_bank_current();
    if (bank == 0)
        return 0;
    w = dw_i8(DW_MAP_WIDTH);
    h = dw_i8(DW_MAP_HEIGHT);
    if (w <= 0 || h <= 0 || w * h > DW_MAP_TILE_COUNT)
        return 0;
    ot = psx_mod_read_word(DW_ACTIVE_ORDERING_TABLE);
    if (!dw_in_ram(ot, 8))
        return 0;
    org = psx_mod_read_word(ot + DW_GSOT_ORG);
    if (!dw_in_ram(org, (DW_OT_TILE_BUCKET + 1) * 4))
        return 0;

    /* renderMap's own quads: 3 x min(width, 4) FT4s in the bucket sampling a
     * ring slot (8bpp texpage at x >= 768, the tile CLUT). AddPrim prepends,
     * so the last one met walking from the bucket head is the oldest; the
     * chain goes in behind it. Anything else short of that is skipped over. */
    n = DW_RING_ROWS * (w < DW_RING_COLS ? w : DW_RING_COLS);
    p = org + DW_OT_TILE_BUCKET * 4;
    tail = 0;
    for (i = 0, row = 0; i < DW_BANK_WALK_MAX && row < n; i++) {
        p = DW_RAM_BASE | (psx_mod_read_word(p) & 0xFFFFFFu);
        if (!dw_in_ram(p, DW_POLY_FT4_BYTES))
            return 0;
        if ((psx_mod_read_word(p) >> 24) == DW_POLY_FT4_LEN &&
            psx_mod_read_byte(p + 7) == DW_POLY_FT4_CODE &&
            psx_mod_read_half(p + 14) == DW_TILE_CLUT_480 &&
            (psx_mod_read_half(p + 22) & DW_TPAGE_RING_MASK) == DW_TPAGE_RING) {
            tail = p;
            row++;
        }
    }
    if (row != n)
        return 0;
    link = psx_mod_read_word(tail) & 0xFFFFFFu;

    /* setupMap hands out painted tiles in tile order: slot = its rank. */
    for (i = 0, n = 0; i < w * h; i++) {
        slot[i] = (uint8_t)n;
        if (dw_i16(DW_MAP_TILE_DATA + (uint32_t)i * DW_TILE_STRIDE +
                   DW_TILE_ID) != -1)
            n++;
    }

    c = dw_i16(DW_CAMERA_X);
    cy = dw_i16(DW_CAMERA_Y);
    dox = dw_i32(DW_DRAWING_OFFSET_X);
    doy = dw_i32(DW_DRAWING_OFFSET_Y);
    col0 = c - m - DW_BANK_OVERDRAW;
    col0 = col0 < 0 ? 0 : col0 / DW_TILE_PX;
    col1 = (c + DW_SCREEN_W + m + DW_BANK_OVERDRAW - 1) / DW_TILE_PX;
    col1 = col1 < w - 1 ? col1 : w - 1;
    row0 = cy - DW_BANK_OVERDRAW;
    row0 = row0 < 0 ? 0 : row0 / DW_TILE_PX;
    row1 = (cy + DW_SCREEN_H + DW_BANK_OVERDRAW - 1) / DW_TILE_PX;
    row1 = row1 < h - 1 ? row1 : h - 1;

    first = dw_bank_arena +
            (psx_mod_read_byte(DW_ACTIVE_FRAMEBUFFER) & 1u) * DW_BANK_HALF_BYTES;
    p = first;
    last = 0;
    lastlen = 0;
    for (row = row0; row <= row1; row++) {
        for (col = col0; col <= col1; col++) {
            const uint32_t idx = (uint32_t)(col + row * w);
            const uint32_t tile = DW_MAP_TILE_DATA + idx * DW_TILE_STRIDE;
            /* setPosDataMapTile: prim x = posX - camX - DRAWING_OFFSET_X */
            const int32_t x = dw_i16(tile + DW_TILE_POS_X) - c - dox;
            const int32_t y = dw_i16(tile + DW_TILE_POS_Y) - cy - doy;
            if (dw_i16(tile + DW_TILE_ID) == -1) {
                psx_mod_write_word(p + 0, (DW_POLY_F4_LEN << 24) |
                                              ((p + DW_POLY_F4_BYTES) & 0xFFFFFFu));
                psx_mod_write_word(p + 4, DW_POLY_F4_CODE << 24);
                psx_mod_write_word(p + 8, dw_xy(x, y));
                psx_mod_write_word(p + 12, dw_xy(x + DW_TILE_PX, y));
                psx_mod_write_word(p + 16, dw_xy(x, y + DW_TILE_PX));
                psx_mod_write_word(p + 20, dw_xy(x + DW_TILE_PX, y + DW_TILE_PX));
                last = p;
                lastlen = DW_POLY_F4_LEN;
                p += DW_POLY_F4_BYTES;
            } else if (slot[idx] < DW_BANK_SLOTS) {
                const uint32_t k = slot[idx];
                const uint32_t tpage =
                    ((uint32_t)psx_mod_read_half(tile + DW_TILE_TPAGE) & ~DW_TPAGE_PAGE) |
                    ((k / DW_BANK_SLOT_COLS) << 4) | (k % DW_BANK_SLOT_COLS);
                const uint32_t clut = psx_mod_read_half(tile + DW_TILE_CLUT);
                const int32_t vh = (dw_i16(tile + DW_TILE_TEX_V) % 256) != 0
                                       ? DW_TILE_PX - 1 : DW_TILE_PX;
                const int32_t x1 = x + DW_TILE_PX, y1 = y + DW_TILE_PX;
                /* the FT4's two halves, (0, 1, 2) and (2, 1, 3) */
                dw_put_gt3(p, p + DW_POLY_GT3_BYTES, bank, x, y, 0, 0, x1, y,
                           DW_TILE_PX, 0, x, y1, 0, vh, clut, tpage);
                p += DW_POLY_GT3_BYTES;
                dw_put_gt3(p, p + DW_POLY_GT3_BYTES, bank, x, y1, 0, vh, x1, y,
                           DW_TILE_PX, 0, x1, y1, DW_TILE_PX, vh, clut, tpage);
                last = p;
                lastlen = DW_POLY_GT3_LEN;
                p += DW_POLY_GT3_BYTES;
            }
        }
    }
    if (last == 0)
        return 0;
    psx_mod_write_word(last, (lastlen << 24) | link);
    psx_mod_write_word(tail, (psx_mod_read_word(tail) & 0xFF000000u) |
                                 (first & 0xFFFFFFu));
    psx_mod_counter_add("dw.ws.banks.frames", 1);
    return 1;
}

static void dw_bank_activate(void) {
    if (!dw_bank_arena)
        dw_bank_arena =
            psx_mod_alloc_texture_packet_memory(2 * DW_BANK_HALF_BYTES, 16);
    psx_mod_set_texture_bank_resolver(dw_bank_resolve);
}

/* TCP {"cmd":"mod_cmd","name":"dw.ws.banks","args":"on|off"}: draw-only A/B
 * switch for verification (the nudge gate keeps following real support). */
static int dw_bank_command(const char* args, char* reply, int cap) {
    if (args && args[0] == 'o' && args[1] == 'f')
        dw_bank_off = 1;
    else if (args && args[0] == 'o' && args[1] == 'n')
        dw_bank_off = 0;
    snprintf(reply, (size_t)cap, "banks=%s arena=%08x", dw_bank_off ? "off" : "on",
             (unsigned)dw_bank_arena);
    return 0;
}

static void dw_sliver_render_exit(struct CPUState* cpu, uint32_t address) {
    int32_t m, w, h, t, ty, c, cy, dox, doy, col, d, ws, hw, n, i, built, voff;
    uint32_t pkt, ot, org, bucket, head;
    struct dw_strip strips[DW_RING_ROWS];
    (void)cpu;
    (void)address;

    m = psx_mod_ws_native_wide_offset();
    if (m <= 0 || m > DW_MARGIN_MAX)
        return;
    if (dw_bank_draw(m))
        return;                             /* the banks drew the whole view */
    w = dw_i8(DW_MAP_WIDTH);
    h = dw_i8(DW_MAP_HEIGHT);
    if (w <= DW_RING_COLS)
        return;                             /* the ring holds the whole map */
    t = dw_i8(DW_MAP_TILE_X);
    ty = dw_i8(DW_MAP_TILE_Y);
    col = t + DW_RING_COLS;
    if (t < 0 || col >= w)
        return;                             /* nothing past the ring to show */
    c = dw_i16(DW_CAMERA_X);
    cy = dw_i16(DW_CAMERA_Y);
    /* right-edge deficit of the larger of the stepped and the drawn camera */
    d = (c + dw_drawn_lead() + DW_SCREEN_W + m) - col * DW_TILE_PX;
    if (d <= 0)
        return;
    ws = d < DW_SLIVER_MAX_PX ? d : DW_SLIVER_MAX_PX;
    hw = (ws + 1) / 2;

    /* The visible strip of each of column T+4's ring rows. Together they are
     * at most the view's 240 rows, which is what lets them stack. */
    n = 0;
    for (i = 0; i < DW_RING_ROWS; i++) {
        const int32_t row = ty + i;
        int32_t top, v0, v1;
        if (row < 0 || row >= h)
            continue;
        top = row * DW_TILE_PX;
        v0 = cy > top ? cy - top : 0;
        v1 = (cy + DW_SCREEN_H) < (top + DW_TILE_PX) ? (cy + DW_SCREEN_H) - top
                                                     : DW_TILE_PX;
        if (v1 <= v0)
            continue;
        strips[n].row = row;
        strips[n].vis0 = v0;
        strips[n].vish = v1 - v0;
        n++;
    }
    if (n == 0)
        return;
    if (dw_tfs_map < 0 && !dw_tfs_tried)
        dw_tfs_load(psx_mod_read_byte(DW_CURRENT_SCREEN));
    if (dw_tfs_map < 0)
        return;

    /* Guest pointers, validated before a single byte is written. */
    pkt = psx_mod_read_word(DW_GS_OUT_PACKET_P);
    if (pkt < DW_GS_WORK_BASES ||
        pkt - DW_GS_WORK_BASES >
            DW_GS_WORK_BASE_BYTES - (uint32_t)n * DW_POLY_FT4_BYTES)
        return;
    ot = psx_mod_read_word(DW_ACTIVE_ORDERING_TABLE);
    if (!dw_in_ram(ot, 8))
        return;
    org = psx_mod_read_word(ot + DW_GSOT_ORG);
    if (!dw_in_ram(org, (DW_OT_TILE_BUCKET + 1) * 4))
        return;
    bucket = org + DW_OT_TILE_BUCKET * 4;
    head = psx_mod_read_word(bucket);
    dox = dw_i32(DW_DRAWING_OFFSET_X);
    doy = dw_i32(DW_DRAWING_OFFSET_Y);

    built = 0;
    voff = 0;                               /* stacked V offset in the column */
    for (i = 0; i < n; i++) {
        const struct dw_strip* st = &strips[i];
        const uint32_t idx = (uint32_t)(col + st->row * w);
        uint32_t tile, img, p;
        int32_t id, vish, x0, y0, v0, rgb, yy, xx;
        const uint8_t* src;

        if (idx >= DW_MAP_TILE_COUNT)
            continue;
        vish = st->vish;
        if (voff + vish > 255 - (DW_SLIVER_Y - 256))
            vish = 255 - voff;              /* cannot happen: sum <= 240 */
        if (vish <= 0)
            continue;
        tile = DW_MAP_TILE_DATA + idx * DW_TILE_STRIDE;
        id = dw_i16(tile + DW_TILE_ID);
        v0 = voff;
        if (id != -1) {
            img = dw_tfs_tile_offset(idx);
            if (img == 0)
                continue;
            for (yy = 0; yy < vish; yy++) {
                src = dw_tfs + img + (uint32_t)(st->vis0 + yy) * DW_TILE_ROW_BYTES;
                for (xx = 0; xx < hw; xx++)
                    dw_pixels[yy * hw + xx] =
                        (uint16_t)(src[2 * xx] | (src[2 * xx + 1] << 8));
            }
            if (!psx_mod_vram_write(DW_SLIVER_X, (uint32_t)(DW_SLIVER_Y + voff),
                                    (uint32_t)hw, (uint32_t)vish, dw_pixels))
                continue;
            rgb = 0x80;                     /* renderMap: 0x80 = unmodulated */
        } else {
            rgb = 0;                        /* renderMap draws empties black */
        }
        voff += vish;

        /* setPosDataMapTile: prim x = posX - camX - DRAWING_OFFSET_X */
        x0 = col * DW_TILE_PX - c - dox;
        y0 = st->row * DW_TILE_PX + st->vis0 - cy - doy;

        p = pkt + (uint32_t)built * DW_POLY_FT4_BYTES;
        psx_mod_write_word(p + 0, (DW_POLY_FT4_LEN << 24) | (head & 0xFFFFFFu));
        psx_mod_write_word(p + 4, (DW_POLY_FT4_CODE << 24) |
                                      ((uint32_t)rgb << 16) |
                                      ((uint32_t)rgb << 8) | (uint32_t)rgb);
        psx_mod_write_word(p + 8, dw_xy(x0, y0));
        psx_mod_write_word(p + 12, dw_uv(DW_SLIVER_U0, v0,
                                         psx_mod_read_half(tile + DW_TILE_CLUT)));
        psx_mod_write_word(p + 16, dw_xy(x0 + ws, y0));
        psx_mod_write_word(p + 20, dw_uv(DW_SLIVER_U0 + ws, v0, DW_SLIVER_TPAGE));
        psx_mod_write_word(p + 24, dw_xy(x0, y0 + vish));
        psx_mod_write_word(p + 28, dw_uv(DW_SLIVER_U0, v0 + vish, 0));
        psx_mod_write_word(p + 32, dw_xy(x0 + ws, y0 + vish));
        psx_mod_write_word(p + 36, dw_uv(DW_SLIVER_U0 + ws, v0 + vish, 0));
        /* AddPrim(&ot[0xfff], p): p->next = bucket head, head = p */
        head = (head & 0xFF000000u) | (p & 0xFFFFFFu);
        built++;
    }
    if (built == 0)
        return;
    psx_mod_write_word(bucket, head);
    psx_mod_write_word(DW_GS_OUT_PACKET_P,
                       pkt + (uint32_t)built * DW_POLY_FT4_BYTES);
}

PSX_MOD_CONSTRUCTOR(dw_ws_sliver_register) {
    psx_mod_register_activation_plugin("dw.ws.sliver", dw_bank_activate);
    psx_mod_register_command("dw.ws.banks", dw_bank_command);
    psx_mod_register_function_exit_plugin(
        "dw.ws.sliver", DW_RENDER_MAP, dw_sliver_render_exit);
    psx_mod_register_function_entry_plugin(
        "dw.ws.sliver", DW_READ_MAP_TFS, dw_read_map_tfs_enter);
    psx_mod_register_savestate_plugin("dw.ws.sliver", dw_tfs_invalidate);
}
