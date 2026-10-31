/* Deep widescreen, phase 6: keep the field wide while the game reloads it.
 *
 * Native-wide decides per frame whether the picture is world (wide) or a 2D
 * screen (4:3 pillarbox). For this game the decision rides on GTE activity
 * with a 45-frame tail (gpu.c, WS_GTE_GAME_MODE_HYSTERESIS). That tail is too
 * short for two synchronous loads:
 *
 *     startBattle, after a won battle:  playBGM; readMapTFS(CURRENT_SCREEN)
 *                                       (tamer.c:861-863)
 *     evolution end / death, rebirth:   loadMapSounds; readMapTFS(...)
 *                                       (partner.c:776-779, 808-809)
 *
 * During those reads no frame is drawn, the last field frame stays on screen,
 * and once the GTE stamp ages out it is presented 4:3. Measured: 74 frames
 * after a won battle and 55 after an evolution, every one of them the same lit,
 * static field image -- a visible snap to pillarbox and back.
 *
 * A "renderMap ran recently" test fails in exactly the same way, because
 * renderMap does not run during the read either. What holds is a latch on the
 * last COMPLETED frame: if it drew the map, the picture on screen is the field,
 * however long the game then stalls. The latch drops only on positive
 * evidence, a completed frame that did not draw the map (the title, FMV
 * setup), and then the normal classifier takes over again.
 *
 * Frames are delimited by the game's own double-buffer index, as in
 * dw_ws_backdrop.c: the main loop writes ACTIVE_FRAMEBUFFER at the top of each
 * frame (main.c:3936), before tickObjects/renderObjects. That ordering is why
 * the "drew the map" flag is kept per buffer. A VBlank can land after the new
 * frame's renderMap but also inside a load the new frame's tick is running,
 * so a single flag sampled at the flip would sometimes describe the frame in
 * flight rather than the one just completed -- and the load stall is exactly
 * the case where it would read 0.
 *
 * psx_mod_set_world_scene_predicate is consulted on every GPU command
 * (gpu.c:433-436), so the predicate is three host loads and nothing else. The
 * FMV veto still applies after it. Widescreen off: the predicate is never
 * consulted (ws_mode != 2) and the hooks below return before touching guest
 * memory.
 */
#include "cpu_state.h"
#include "mod_plugins.h"

#define DW_RENDER_MAP 0x800D57ECu
#define DW_ACTIVE_FRAMEBUFFER 0x80134ECCu /* 0/1, written at the top of each frame */

static int dw_drew[2];   /* frame in buffer N has run renderMap */
static int dw_last;      /* the last completed frame drew the map */
static int dw_fb = -1;   /* buffer of the frame in flight, as last seen */

static void dw_world_reset(void) {
    dw_drew[0] = dw_drew[1] = 0;
    dw_last = 0;
    dw_fb = -1;
}

static int dw_world_predicate(void) {
    return dw_last | (dw_fb >= 0 && dw_drew[dw_fb]);
}

static void dw_world_render_map_exit(struct CPUState* cpu, uint32_t address) {
    (void)cpu;
    (void)address;
    if (psx_mod_widescreen_x_margin() <= 0)
        return;
    dw_drew[psx_mod_read_byte(DW_ACTIVE_FRAMEBUFFER) & 1] = 1;
}

/* A flip completes the frame drawn into the other buffer. */
static void dw_world_vblank(void) {
    int fb;
    if (psx_mod_widescreen_x_margin() <= 0 || !psx_mod_game_started())
        return;
    fb = psx_mod_read_byte(DW_ACTIVE_FRAMEBUFFER) & 1;
    if (fb == dw_fb)
        return;
    if (dw_fb >= 0) {
        dw_last = dw_drew[dw_fb];
        dw_drew[dw_fb] = 0;
    }
    dw_fb = fb;
}

static void dw_world_activate(void) {
    dw_world_reset();
    psx_mod_set_world_scene_predicate(dw_world_predicate);
}

PSX_MOD_CONSTRUCTOR(dw_ws_world_register) {
    psx_mod_register_activation_plugin("dw.ws.world", dw_world_activate);
    psx_mod_register_vblank_plugin("dw.ws.world", dw_world_vblank);
    psx_mod_register_savestate_plugin("dw.ws.world", dw_world_reset);
    psx_mod_register_function_exit_plugin(
        "dw.ws.world", DW_RENDER_MAP, dw_world_render_map_exit);
}
