"""Smooth 60 FPS run: dw.smooth60 with other packages; pass counters, dumps, fingerprints.

python tools/verify_smooth60.py --port N --tag T [--build build-debug] [--off] [--verify] [--ws 21:9]
       [--run 2x] [--hd DIR] [--hd-dump DIR] [--dw15] [--map NAME] [--dir right] [--battle] [--fp]
       [--menu] [--talk] [--shout]
Results: <build>/s60/<tag>/result.json. Method: docs/investigations/2026-11-13-60fps.md.
"""
import argparse, json, os, shutil, struct, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dwgame import Game                                              # noqa: E402
from field_drive import Field, UP, DOWN, LEFT, RIGHT                 # noqa: E402

SEED = os.environ.get("DW_SEED", r"C:\Users\boura\hdb-rewind")
DIRS = {"right": RIGHT, "left": LEFT, "up": UP, "down": DOWN}

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=6540)
ap.add_argument("--build", default="build-debug")
ap.add_argument("--exe", default="Digimon_World_Recompiled.exe", help="another exe in the build dir (an A/B build)")
ap.add_argument("--idle-fp-save", action="store_true", help="save a state in the field, fingerprint it twice (control)")
ap.add_argument("--idle-fp-load", help="tag of an --idle-fp-save run: load its state here and compare fingerprints")
ap.add_argument("--talk", action="store_true", help="talk to the nearest NPC and dump the text box opening")
ap.add_argument("--shout", action="store_true", help="battle: dump the command menu slide-in and a command shout")
ap.add_argument("--win", action="store_true", help="battle: win it and dump the ending (menu slide-out, result boxes)")
ap.add_argument("--tag", required=True)
ap.add_argument("--off", action="store_true")
ap.add_argument("--rate", default="60")
ap.add_argument("--verify", action="store_true")
ap.add_argument("--ws")
ap.add_argument("--run")
ap.add_argument("--hd")
ap.add_argument("--hd-dump")
ap.add_argument("--dw15", action="store_true")
ap.add_argument("--map")
ap.add_argument("--dir", default="right")
ap.add_argument("--secs", type=float, default=4)
ap.add_argument("--battle", action="store_true")
ap.add_argument("--fp", action="store_true")
ap.add_argument("--fp-frames", type=int, default=400)
ap.add_argument("--fpdiff", type=int, default=-1, help="record every write of this frame after the load, passes on vs refused")
ap.add_argument("--menu", action="store_true")
ap.add_argument("--fight", type=int, default=0, help="battle: N more rounds of attack + dump")
ap.add_argument("--fade", action="store_true", help="warp and dump the fade-in")
ap.add_argument("--dump", type=int, default=3)
ap.add_argument("--dump-delay", type=float, default=0.8)
ap.add_argument("--visible", action="store_true")
ap.add_argument("--env", action="append", default=[])
ap.add_argument("--hold", type=float, default=0, help="seconds to leave the window up at the end")
a = ap.parse_args()

BUILD = os.path.join(ROOT, a.build)
out = os.path.join(BUILD, "s60", a.tag)
cards = os.path.join(out, "cards")
shutil.rmtree(out, ignore_errors=True)
os.makedirs(cards)
os.makedirs(os.path.join(cards, "dw15"))             # 1.5 keeps its own cards (dw_dw15.c)
for n in ("card1.mcd", "card2.mcd"):
    shutil.copy(os.path.join(SEED, n), cards)
    shutil.copy(os.path.join(SEED, n), os.path.join(cards, "dw15"))


def feat(pkg, fid, values=None, resources=None):
    s = '\n[[feature]]\npackage_id = "%s"\nid = "%s"\nenabled = true\n' % (pkg, fid)
    if values:
        s += "[feature.values]\n" + "".join('%s = "%s"\n' % kv for kv in values.items())
    if resources:
        s += "[feature.resources]\n" + "".join('%s = "%s"\n' % kv for kv in resources.items())
    return s


pkgs, feats = [], []
if not a.off:
    pkgs.append("dw.smooth60"); feats.append(feat("dw.smooth60", "smooth60", {"rate": a.rate}))
if a.ws:
    pkgs.append("psx.enhancement.widescreen")
    feats.append(feat("psx.enhancement.widescreen", "widescreen", {"aspect": a.ws}))
if a.run:
    pkgs.append("dw.vanilla.qol"); feats.append(feat("dw.vanilla.qol", "walk-speed", {"speed": a.run}))
if a.hd or a.hd_dump:
    pkgs.append("psx.enhancement.hd-textures")
    feats.append(feat("psx.enhancement.hd-textures", "hd-textures",
                      {"replacements": "true" if a.hd else "false", "dump": "true" if a.hd_dump else "false"},
                      {"pack": (a.hd or a.hd_dump).replace("\\", "/")}))
if a.dw15:
    pkgs.append("dw.1_5"); feats.append(feat("dw.1_5", "dw15"))
state = "format_version = 2\n" + "".join('\n[[package]]\nid = "%s"\nversion = "1.0.0"\n' % p for p in pkgs) + "".join(feats)
open(os.path.join(BUILD, "mods", "state.toml"), "w").write(state)

env = {"SDL_AUDIODRIVER": "dummy", "PSX_DEBUG_FMV_QUIET": "0"}
if a.verify:
    env["PSX_RENDER_PASS_VERIFY"] = "1"
for kv in a.env:
    k, v = kv.split("=", 1)
    env[k] = v
res = {"args": vars(a)}
g = Game.launch(exe=os.path.join(BUILD, a.exe), port=a.port, memcard_dir=cards,
                extra_args=[] if a.visible else ["--hidden-window"], env=env,
                log_path=os.path.join(out, "run.log"))
d = g.d
KEYS = ("plans", "planned", "wanted", "passes", "aborted", "watchdog", "vram_leaks", "device_reads",
        "verify_checks", "verify_mismatch")


def cmd(name, **kw):
    try:
        return d.cmd(name, **kw)
    except Exception as e:          # noqa: BLE001
        return {"error": str(e)}


def counters():
    return {c["name"]: c["count"] for c in cmd("mod_counters").get("counters", []) if "smooth" in c["name"]}


class Stage:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.s0, self.c0 = cmd("render_pass_stats"), counters()
        res.setdefault("gl_interp_before", {})[self.name] = cmd("gl_interp")
        return self

    def __exit__(self, *exc):
        s1, c1 = cmd("render_pass_stats"), counters()
        r = {k: s1.get(k, 0) - self.s0.get(k, 0) for k in KEYS if isinstance(s1.get(k), (int, float))}
        r.update(status=s1.get("status"), avg_pass_ms=s1.get("avg_pass_ms"),
                 counters={k: v - self.c0.get(k, 0) for k, v in c1.items() if v != self.c0.get(k, 0)})
        dn = s1.get("frame_delay_n", 0) - self.s0.get("frame_delay_n", 0)
        if dn:
            r["frame_delay_ms"] = round((s1["frame_delay_sum_ms"] - self.s0["frame_delay_sum_ms"]) / dn, 2)
            r["frame_delay_n"] = dn
        r["gl_interp"] = cmd("gl_interp")
        res.setdefault("stages", {})[self.name] = r
        print(self.name, json.dumps(r), flush=True)
        return False


def dump(name, count=None):
    p = os.path.join(out, name)
    os.makedirs(p)
    cmd("render_pass_dump", path=p, count=a.dump if count is None else count)


CURRENT_MENU, MENU_POS = 0x8013504C, 0x801316B8      # i16; MENU_HIGHLIGHTS[0].pos (main_menu.c)


def enter(f):
    """Title -> CONTINUE, checked: the main menu (CURRENT_MENU 0) must be up and its cursor on 1."""
    rd = lambda adr, n, fmt: struct.unpack(fmt, d.read(adr, n))[0]
    # CURRENT_MENU reads 0 from boot (zeroed RAM); the main menu's setup writes -1, then the first
    # tick shows menu 0 (main.c:3690, main_menu.c tickMainMenu). START is inert on menu 0.
    # drawMainMenuStrings zeroes MAIN_MENU_TICKS and tickMainMenu counts it up (main_menu.c:2303, :3346).
    ticks = lambda: rd(0x80135048, 4, "<i")
    for _ in range(200):
        t0 = ticks()
        g.advance(10)
        if rd(CURRENT_MENU, 2, "<h") == 0 and ticks() > t0:
            break
        g.route("6:start,20:none")
    else:
        raise RuntimeError("title menu never came up")
    g.advance(30)
    if rd(0x80135030, 4, "<I") == 0:                  # CONNECTED_CARDS: CONTINUE does nothing without
        raise RuntimeError("no memory card detected")
    for _ in range(6):
        if rd(MENU_POS, 1, "<b") == 1:
            break
        g.route("6:down,30:none")
    if rd(MENU_POS, 1, "<b") != 1:
        raise RuntimeError("cursor never reached CONTINUE")
    free = lambda: (g.partner_type and g.tamer_state == 0 and f.gstate() == 0 and f.screen() != 238
                    and f.i32(0x80134FF4) == 1)
    for i in range(400):                              # (the seed save is in the opening scene, 238)
        g.route("6:cross,30:none")
        if free() and i % 5 == 0:
            g.advance(120)
            if free():
                return
    raise RuntimeError("never reached the world from CONTINUE")


try:
    f = Field(g, lambda c, m: print(("PASS " if c else "FAIL ") + m, flush=True), dw15=a.dw15)
    enter(f)
    if a.map:
        mid = g.maps_named(a.map)[0]
        g.warp(mid, 0)
        time.sleep(1.5)
    res["screen"] = f.screen()
    w = struct.unpack("<2I", d.read(0x800C1A6C, 8))
    res["bone_site"] = [hex(x) for x in w]
    base = ((w[0] & 0xFFFF) << 16) + struct.unpack("<h", struct.pack("<H", w[1] & 0xFFFF))[0]
    ptype = g.partner_type
    res["partner_bones"] = (hex(base), ptype, struct.unpack("<i", d.read(base + 52 * ptype, 4))[0])
    print("bones", res["bone_site"], res["partner_bones"], flush=True)
    res["plan"] = cmd("mod_plan")
    if a.idle_fp_save or a.idle_fp_load:
        # The verify_evoguide.py idle method: one state, saved in the field by one launch, fingerprinted
        # there twice (control) and again in another launch (another exe or package set).
        def fps(frames=360):
            cmd("frame_fingerprint", reset_on_load=1)
            g.savestate("load", 9)
            g.advance(frames + 30)
            e = cmd("frame_fingerprint", count=frames).get("entries", [])
            return [[x.get(k) for k in ("cyc", "wc", "ws", "mmio", "mc", "sp", "sc", "qc")] for x in e[:frames]]
        slots = os.path.join(cards, "openbios")
        if a.idle_fp_save:
            g.advance(60)
            g.savestate("save", 9)
            runs = [fps(), fps()]
        else:
            src = os.path.join(BUILD, "s60", a.idle_fp_load)
            os.makedirs(slots, exist_ok=True)
            for fn in os.listdir(os.path.join(src, "cards", "openbios")):
                if fn.endswith("_slot09.pst"):
                    shutil.copy(os.path.join(src, "cards", "openbios", fn), os.path.join(slots, fn))
            runs = json.load(open(os.path.join(src, "idlefp.json")))
            runs.append(fps())
            n = min(len(r) for r in runs)
            res["idle_fp"] = dict(frames=n, control_same=sum(runs[0][i] == runs[1][i] for i in range(n)),
                                  this_same=sum(runs[0][i] == runs[2][i] for i in range(n)))
            print("idle_fp", res["idle_fp"], flush=True)
        json.dump(runs, open(os.path.join(out, "idlefp.json"), "w"))
    with Stage("idle"):
        time.sleep(2)
        dump("dump_idle")
        time.sleep(1)
    if a.menu:
        tri = lambda: f.i32(0x80134D30)              # TRI_STATE: -1 while the field menu is open
        with Stage("menu"):
            for i in range(4):
                if i == 0:
                    dump("dump_menu_open", 14)       # the box zoom: 5 ticks from the press
                g.route("6:triangle,40:none")
                if f.wait_for(lambda: tri() == -1, 4):
                    break
            res["menu_open"] = tri() == -1
            time.sleep(1)
            dump("dump_menu")
            g.artifact_shot(os.path.join(out, "menu.png"))
            time.sleep(1)
            for i in range(4):
                if i == 0:
                    dump("dump_menu_close", 14)
                g.route("6:triangle,40:none")
                if f.wait_for(lambda: tri() != -1, 4):
                    break
            res["menu_closed"] = tri() != -1
            g.artifact_shot(os.path.join(out, "menu_after.png"))
            time.sleep(1)
    samples = res.setdefault("scroll", [])

    def sample(t_end):
        while time.time() < t_end:
            dox, doy = struct.unpack("<2i", d.read(0x80134EC0, 8))
            cx, cy = struct.unpack("<2h", d.read(0x8014F084 + 7092, 4))
            samples.append((d.frame(), dox, doy, cx, cy, struct.unpack("<b", d.read(0x80134DC2, 1))[0],
                            struct.unpack("<b", d.read(0x80150C3C, 1))[0]))   # MAP_TILE_X, MAP_WIDTH
            time.sleep(0.05)

    with Stage("move"):
        f.pad(DIRS[a.dir])
        sample(time.time() + a.dump_delay)
        res["dump_frame"] = d.frame()
        dump("dump_move")
        sample(time.time() + max(0, a.secs - a.dump_delay))
        f.pad(DIRS[{"right": "left", "left": "right", "up": "down", "down": "up"}[a.dir]])
        sample(time.time() + a.secs)
        f.pad(0)
        time.sleep(0.5)
    g.artifact_shot(os.path.join(out, "field.png"))
    if a.fade:
        with Stage("fade"):
            mid = g.maps_named("native forest")[0]
            g.set("target_map", mid)
            g.set("current_exit", 0)
            g.set_atomic(("tamer_state", 5), ("tamer_substate", 0))
            t_end = time.time() + 20
            while time.time() < t_end and not (g.current_screen == mid and g.tamer_state == 0):
                time.sleep(0.02)
            dump("dump_fade")
            time.sleep(4)
    if a.fp:
        g.savestate("save", 7)
        runs = []
        for label, refuse in (("pass_a", 0), ("stock", 1), ("pass_b", 0)):
            cmd("render_pass_refuse", on=refuse)
            f.pad(DIRS[a.dir])
            cmd("frame_fingerprint", reset_on_load=1)
            g.savestate("load", 7)
            f0 = d.frame()
            while d.frame() < f0 + a.fp_frames:
                time.sleep(0.2)
            fp = cmd("frame_fingerprint", count=a.fp_frames + 200)
            f.pad(0)
            st = cmd("render_pass_stats")
            runs.append((label, fp, st.get("passes")))
        cmd("render_pass_refuse", on=0)
        json.dump(runs, open(os.path.join(out, "fp.json"), "w"))
        cols = ("cyc", "wc", "ws", "mmio", "mc", "sp", "sc")

        def rows(fp):        # aligned by position since the load (absolute frame numbers differ)
            return [tuple(e.get(c) for c in cols) for e in fp.get("entries", [])]
        base = rows(runs[1][1])
        res["fp"] = {}
        prev = None
        for label, fp, passes in runs:
            r = rows(fp)
            n = min(len(r), len(base))
            diff = [i for i in range(n) if r[i] != base[i]]
            res["fp"][label] = dict(frames=n, differ=len(diff), first=diff[:3], passes_total=passes)
            prev = passes
        print("fp", res["fp"], flush=True)
    if a.fpdiff == -2 and res.get("fp", {}).get("pass_a", {}).get("first"):
        a.fpdiff = res["fp"]["pass_a"]["first"][0]
    if a.fpdiff >= 0:
        slot = 7 if a.fp else 6                     # the fingerprint runs' own state
        if not a.fp:
            g.savestate("save", slot)
        logs = {}
        for label, refuse in (("on", 0), ("refused", 1)):
            cmd("render_pass_refuse", on=refuse)
            f.pad(DIRS[a.dir])
            cmd("frame_fingerprint", reset_on_load=1)
            g.savestate("load", slot)
            e = []
            while not e:
                e = cmd("frame_fingerprint", count=4).get("entries", [])
            target = e[0]["frame"] + a.fpdiff
            cmd("record_frame", frame=target)
            while d.frame() < target + 3:
                time.sleep(0.05)
            entries, off = [], 0
            while True:
                r = cmd("record_frame_dump", offset=off, count=4000)
                chunk = r.get("entries", [])
                entries += chunk
                off += len(chunk)
                if not chunk or off >= r.get("total", 0):
                    break
            logs[label] = dict(frame=target, total=r.get("total"), overflow=r.get("overflow"), entries=entries)
            f.pad(0)
        cmd("render_pass_refuse", on=0)
        json.dump(logs, open(os.path.join(out, "fpdiff.json"), "w"))
        from collections import Counter
        ca = Counter((x["kind"], x["addr"], x["val"]) for x in logs["on"]["entries"])
        cb = Counter((x["kind"], x["addr"], x["val"]) for x in logs["refused"]["entries"])
        only_a, only_b = ca - cb, cb - ca
        res["fpdiff"] = dict(on=len(logs["on"]["entries"]), refused=len(logs["refused"]["entries"]),
                             overflow=(logs["on"]["overflow"], logs["refused"]["overflow"]),
                             only_on=sorted(only_a)[:20], only_refused=sorted(only_b)[:20])
        pcs = {}
        for x in logs["on"]["entries"] + logs["refused"]["entries"]:
            if (x["kind"], x["addr"], x["val"]) in only_a or (x["kind"], x["addr"], x["val"]) in only_b:
                pcs.setdefault(x["addr"], set()).add((x["pc"], x["ra"]))
        res["fpdiff"]["pcs"] = {k: sorted(v) for k, v in list(pcs.items())[:20]}
        print("fpdiff", json.dumps(res["fpdiff"])[:3000], flush=True)
    if a.battle:
        for mid in g.maps_named("native forest")[:3]:
            g.warp(mid, 0)
            time.sleep(1.5)
            for slot in range(4):
                n = f.npc(slot)
                if not n or not n["on_map"]:
                    continue
                f.go((n["pos"][0], n["pos"][2]), lambda: f.gstate() != 0, limit=25)
                end = time.time() + 30
                intro = False
                while time.time() < end and f.gstate() != 1:
                    if a.shout and not intro and f.gstate() == 2:   # intro: FIGHT!, the command menu slides in
                        dump("dump_intro", 24)
                        intro = True
                    time.sleep(0.02)
                if f.gstate() == 1:
                    res["battle_map"] = mid
                    break
            if "battle_map" in res:
                break
        if "battle_map" in res:
            time.sleep(2)
            res["interp_before"] = cmd("dirty_ram_stats")
            with Stage("battle"):
                dump("dump_battle")
                if a.shout:
                    time.sleep(1)
                    dump("dump_shout", 12)           # the command shout hops for 20 ticks
                g.route("4:cross,20:none")
                time.sleep(1)
                dump("dump_battle2")
                time.sleep(4)
                for k in range(a.fight):
                    if f.gstate() != 1:
                        break
                    g.route("4:cross,20:none")
                    time.sleep(1.2)
                    dump("dump_fight%d" % k)
                    time.sleep(1.5)
            res["interp_after"] = cmd("dirty_ram_stats")
            g.artifact_shot(os.path.join(out, "battle.png"))
            if a.win and f.gstate() == 1:
                # A maxed partner against 1-HP enemies; the ending runs in GAME_STATE 2: the command menu
                # slides out, then the result boxes zoom open (UI_BOX_DATA[0].state 2).
                with Stage("win"):
                    f.partner(999, 9999)
                    box_state = lambda: struct.unpack("<h", d.read(0x8013D390 + 18, 2))[0]
                    end, seen = time.time() + 90, set()
                    while time.time() < end and f.gstate() == 1:
                        for slot in range(8):
                            n = f.npc(slot)
                            if n and n["on_map"] and n["hp"] > 1:
                                d.write_bytes(struct.pack("<h", 1), 0x80155828 + 0x68 * slot + 0x4C)
                        g.route("4:cross,10:none")
                        time.sleep(0.05)
                    if f.gstate() == 2:
                        dump("dump_end_slide", 20)
                        seen.add("slide")
                        end = time.time() + 30
                        while time.time() < end and box_state() != 2 and f.gstate() == 2:
                            time.sleep(0.01)
                        if box_state() == 2:
                            dump("dump_end_box", 14)
                            seen.add("box")
                        time.sleep(3)
                        g.artifact_shot(os.path.join(out, "end_box.png"))
                    end = time.time() + 60
                    while time.time() < end and not (f.gstate() == 0 and g.tamer_state == 0):
                        g.route("4:cross,20:none")
                    res["win"] = sorted(seen) + ["field" if f.gstate() == 0 else "state %d" % f.gstate()]
        else:
            res["battle"] = "no fight started"
    if a.hold:
        time.sleep(a.hold)
    res["stats_end"] = cmd("render_pass_stats")
    res["counters_end"] = counters()
finally:
    json.dump(res, open(os.path.join(out, "result.json"), "w"), indent=1, default=str)
    if a.hd_dump:                                   # let the capture writer finish
        cmd("quit")
        try:
            g.proc.wait(timeout=60)
        except Exception:                           # noqa: BLE001
            pass
    g.shutdown()
    print("screen", res.get("screen"), "battle", res.get("battle_map", res.get("battle")),
          "status_end", res.get("stats_end", {}).get("status"), flush=True)
