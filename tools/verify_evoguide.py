#!/usr/bin/env python3
"""Evolution Guide (dw.evoguide, dw_evoguide.c) in the game.

Phases (default: all, each its own launch):

  stock   Agumon from the seed card. F2 opens the panel; the panel's model
          (debug command `evoguide`) must equal an independent computation from
          guest memory written here from the decomp (evolution.c:690-1036), not
          from the C. Screenshots of the composed window at 4:3, 16:9 and 21:9.
          Select + L3 on the host pad layer closes and reopens it. Then stats
          are shaped so the first target qualifies and the panel's "Now" pick
          must equal what the game's own chooser writes to EVOLUTION_TARGET.
  roster  Expanded Roster on: Agumon is forced to Greymon (setup), the roster's
          target must be listed, the model must match again, and a shaped pick
          must again equal the game's.
  idle    Guide ticked but closed changes nothing: the same savestate loaded
          with the guide unticked (twice: the control) and ticked, the per-frame
          write fingerprints (frame_fingerprint reset_on_load) must agree.
  dw15    Digimon World 1.5: the panel shows the "not available" line.

    $PY tools/verify_evoguide.py --seed <card dir> --port 7210 [--phase stock]

Exit 0 = every check passed.
"""
import argparse
import json
import os
import shutil
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game                                           # noqa: E402
import dwdbg                                                      # noqa: E402
from dwdbg import pad_word                                        # noqa: E402
from field_drive import Field                                     # noqa: E402
from gen_roster_mod import ARENA, OFF_EVO_REQ, OFF_EVO_GAINS, OFF_EVO_PATHS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DDATA, DSTRIDE = 0x8012CEB4, 0x34
STOCK_REQ, STOCK_GAINS, STOCK_PATHS = 0x8012ABEC, 0x8012B2D0, 0x8012B66C
DETECT_LUI = 0x800E2610            # dw_roster.c: reads 0x9F00 when the roster is on
SCRIPT_STATE_PTR = 0x80134FB8
PARTNER, BASE, MOVES, PARA = 0x801557A8, 0x801557E0, 0x80155800, 0x80138460
GATE_EXE = 0x800ED2A0              # 0x8013 stock, 0x801F on 1.5 (cheats.txt)

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)
    return cond


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


# ---- the independent model ---------------------------------------------------
class Mem:
    def __init__(self, g):
        self.g = g

    def b(self, a):
        return self.g.d.read(a, 1)[0]

    def s8(self, a):
        return struct.unpack("<b", self.g.d.read(a, 1))[0]

    def s16(self, a):
        return struct.unpack("<h", self.g.d.read(a, 2))[0]

    def u16(self, a):
        return struct.unpack("<H", self.g.d.read(a, 2))[0]

    def u32(self, a):
        return struct.unpack("<I", self.g.d.read(a, 4))[0]

    def name(self, t):
        return self.g.d.read(DDATA + t * DSTRIDE, 20).split(b"\0")[0].decode("ascii", "replace")


def py_model(g):
    m = Mem(g)
    roster = m.u16(DETECT_LUI) == 0x9F00
    req_b = ARENA + OFF_EVO_REQ if roster else STOCK_REQ
    gains_b = ARENA + OFF_EVO_GAINS if roster else STOCK_GAINS
    paths_b = ARENA + OFF_EVO_PATHS if roster else STOCK_PATHS
    cur = m.u32(PARTNER)
    lv = m.b(DDATA + cur * DSTRIDE + 29)
    st = [tdiv(m.s16(BASE + 0x10), 10), tdiv(m.s16(BASE + 0x12), 10),
          m.s16(BASE), m.s16(BASE + 2), m.s16(BASE + 4), m.s16(BASE + 6)]
    pa = {"care": m.s16(PARA + 0x52), "weight": m.s16(PARA + 0x42),
          "disc": m.s16(PARA + 0x28), "happy": m.s16(PARA + 0x2A),
          "battles": m.s16(PARA + 0x54), "timer": m.s16(PARA + 0x56)}
    techs = bin(m.u32(MOVES)).count("1") + bin(m.u32(MOVES + 4)).count("1")
    to = [m.s8(paths_b + (cur - 1) * 11 + 5 + i) for i in range(6)]

    def raised(s):
        if roster:   # dw_roster_flags.c: stock bank below 63, own bank 0x2CD.. above
            tid = 0x200 + s if 0 <= s < 63 else (0x2CD + s - 63 if 63 <= s < 146 else None)
        else:        # hasDigimonRaised -> isTriggerSet((s + 0x200) & 0xFFFF)
            tid = (s + 0x200) & 0xFFFF
        if tid is None:
            return 0
        return 1 if m.b(m.u32(SCRIPT_STATE_PTR) + 0xF5 + (tid >> 3)) & (1 << (tid & 7)) else 0

    def score(t, best):
        r = list(struct.unpack("<13h", g.d.read(req_b + t * 28, 26)))
        flags = m.b(req_b + t * 28 + 26)
        tl = m.b(DDATA + t * DSTRIDE + 29)
        c = {"t": t, "name": m.name(t), "lv": tl, "req": r, "flags": flags, "hi": -1, "mean": -1}
        c["care"] = int(pa["care"] >= r[7]) if not flags & 0x10 else int(pa["care"] <= r[7])
        c["wt"] = int(r[8] - 5 <= pa["weight"] <= r[8] + 5)
        if tl == 3:
            hi = None
            for i in range(6):
                if all(st[i] >= st[j] for j in range(6)):
                    hi = i
            c["hi"] = hi
            c["st"] = int(r[1 + hi] == 1)
            c["sm"] = [int(i == hi and c["st"]) for i in range(6)]
        else:
            c["sm"] = [int(st[i] >= r[1 + i]) for i in range(6)]
            c["st"] = int(all(c["sm"]))
        bp = bh = 0
        tests = [(0, 1, cur == r[0]), (9, 2, pa["disc"] >= r[9]), (10, 4, pa["happy"] >= r[10]),
                 (11, 8, (pa["battles"] >= r[11]) if not flags & 1 else (pa["battles"] <= r[11])),
                 (12, 16, techs >= r[12])]
        for idx, bit, ok in tests:
            if r[idx] != -1:
                bp |= bit
                if ok:
                    bh |= bit
        c["bp"], c["bh"], c["bonus"] = bp, bh, int(bh != 0)
        pts = c["care"] + c["wt"] + c["st"] + c["bonus"]
        c["pts"], c["adj"] = pts, 0
        if pts >= 3 and best != -1:
            tr = raised(m.s16(gains_b + t * 14 + 12))
            br = raised(m.s16(gains_b + best * 14 + 12))
            if tr == 1 and br == 0:
                pts, c["adj"] = 0, -1
            if tr == 0 and br == 1:
                pts, c["adj"] = pts + 1, 1
        c["score"], c["q"] = pts, int(pts >= 3)
        return c

    out = {"partner": cur, "lv": lv, "stats": st, "care": pa["care"], "weight": pa["weight"],
           "disc": pa["disc"], "happy": pa["happy"], "battles": pa["battles"], "techs": techs,
           "timer": pa["timer"], "thr": {1: 6, 2: 24, 3: 72, 4: 144}.get(lv, 0),
           "roster": roster, "cands": [], "pick": -1}
    best, best_score, total, count = -1, 0, 0, 0
    if lv not in (2, 3, 4):
        return out
    for t in to:
        if t == -1:
            continue
        c = score(t, best)
        out["cands"].append(c)
        if lv == 2:
            if best_score < c["score"] and c["score"] >= 3:
                best, best_score = t, c["score"]
            continue
        if c["score"] < 3:
            continue
        for i in range(6):
            if c["req"][1 + i] != -1:
                total += st[i]
                count += 1
        total = tdiv(total, count) if count else (-1 if total >= 0 else 1)
        c["mean"] = total
        if best_score < total:
            best, best_score = t, total
            total = count = 0
    if lv == 3 and best == -1 and pa["timer"] >= 96:
        best = 11
    out["pick"] = best
    return out


# ---- the panel ---------------------------------------------------------------
def named(g, cmd, **params):
    """A command whose own field is called `name`, which Dbg.cmd's first
    parameter shadows: send the request dict directly."""
    g.d._id += 1
    req = dict(params, id=g.d._id, cmd=cmd)
    with socket.create_connection((dwdbg.HOST, g.d.port), timeout=g.d.timeout) as s:
        f = s.makefile("rwb")
        f.write((json.dumps(req) + "\n").encode())
        f.flush()
        r = json.loads(dwdbg._frame_json(f))
    if not r.get("ok"):
        raise RuntimeError("%s: %s" % (cmd, r))
    return r


def guide(g, args):
    r = named(g, "mod_cmd", name="evoguide", args=args)["reply"]
    return r if args == "lines" else json.loads(r)


def wait_fresh_model(g, n=2, timeout=10.0):
    s0 = guide(g, "state")["seq"]
    end = time.time() + timeout
    while time.time() < end:
        s = guide(g, "state")
        if s["seq"] >= s0 + n:
            return s
        time.sleep(0.1)
    return guide(g, "state")


def text_diffs(lines, p, g):
    """The drawn text (the `lines` buffer guide_draw rasterises, built in one
    place, format_model) must state the values the memory model computes."""
    out = []
    want = ["Evolution timer %d / %d h" % (p["timer"], p["thr"]),
            "Now: %s" % (Mem(g).name(p["pick"]) if p["pick"] > 0 else "none")]
    for c in p["cands"]:
        r = c["req"]
        want.append("%s %-14s %d of 4 met, need 3" % (">" if c["t"] == p["pick"] else " ",
                                                      c["name"], c["pts"]))
        want.append("Care %d (%s %d)" % (p["care"], "max" if c["flags"] & 0x10 else "min", r[7]))
        want.append("Weight %d (%d-%d)" % (p["weight"], r[8] - 5, r[8] + 5))
        if c["lv"] != 3:
            for i, nm in enumerate(("HP", "MP", "Off", "Def", "Spd", "Brn")):
                if r[1 + i] != -1:
                    want.append("%s %d/%d" % (nm, p["stats"][i], r[1 + i]))
        if c["mean"] >= 0:
            want.append("mean %d" % c["mean"])
    flat = lines.replace("|    ", "")       # undo the panel's wrap indent
    for w in want:
        if w not in flat:
            out.append("drawn text lacks %r" % w)
    return out


def compare(g, tag):
    """Panel model vs py_model, read while the host holds the guest still."""
    diffs = []
    for _attempt in range(3):
        g.d.cmd("host_pause", on=1)
        time.sleep(0.3)
        try:
            s = guide(g, "state")
            cs = [guide(g, "t %d" % i) for i in range(s["n"])]
            lines = guide(g, "lines")
            p = py_model(g)
        finally:
            g.d.cmd("host_pause", on=0)
        diffs = []
        for k in ("partner", "lv", "timer", "thr", "pick", "stats", "care", "weight",
                  "disc", "happy", "battles", "techs"):
            if s[k] != p[k]:
                diffs.append("%s panel=%s mem=%s" % (k, s[k], p[k]))
        if s["n"] != len(p["cands"]):
            diffs.append("n panel=%d mem=%d" % (s["n"], len(p["cands"])))
        for c, q in zip(cs, p["cands"]):
            for k in ("t", "name", "lv", "req", "flags", "care", "wt", "st", "sm", "hi",
                      "bp", "bh", "bonus", "pts", "score", "adj", "q", "mean"):
                if c[k] != q[k]:
                    diffs.append("target %s %s panel=%s mem=%s" % (q["t"], k, c[k], q[k]))
        diffs += text_diffs(lines, p, g)
        if not diffs:
            break
    print("  %s: partner %d %r lv%d timer %d/%d pick %d, %d targets" % (
        tag, s["partner"], s["name"], s["lv"], s["timer"], s["thr"], s["pick"], s["n"]))
    for c in cs:
        print("    %3d %-14s pts=%d score=%d adj=%d q=%d mean=%d care=%d wt=%d st=%d bonus=%d" % (
            c["t"], c["name"], c["pts"], c["score"], c["adj"], c["q"], c["mean"],
            c["care"], c["wt"], c["st"], c["bonus"]))
    check(not diffs, "%s: every target, value and met flag matches guest memory%s"
          % (tag, "" if not diffs else ": " + "; ".join(diffs[:8])))
    return s, cs, p


def present_shot(g, path, timeout=10.0):
    seq = g.d.cmd("present_shot_seq").get("seq")
    g.d.cmd("present_shot", path=path)
    end = time.time() + timeout
    while time.time() < end:
        st = g.d.cmd("present_shot_seq")
        if st.get("seq") != seq:
            return st.get("wrote", False) and os.path.exists(path)
        time.sleep(0.1)
    return False


# ---- harness -------------------------------------------------------------
def launch(a, tag, features, card_sub=""):
    out = os.path.join(a.build, "eg_" + tag)
    shutil.rmtree(out, ignore_errors=True)
    cards = os.path.join(out, "cards")
    os.makedirs(os.path.join(cards, card_sub) if card_sub else cards)
    for n in ("card1.mcd", "card2.mcd"):
        if os.path.exists(os.path.join(a.seed, n)):
            shutil.copy(os.path.join(a.seed, n), os.path.join(cards, card_sub, n))
    with open(os.path.join(a.build, "mods", "state.toml"), "w") as fh:
        fh.write("format_version = 2\n\n" + "".join(
            '[[feature]]\npackage_id = "%s"\nid = "%s"\nenabled = true\n\n' % f for f in features))
    a.port += 1
    g = Game.launch(exe=os.path.join(a.build, "Digimon_World_Recompiled.exe"), port=a.port,
                    memcard_dir=cards, log_path=os.path.join(out, "game.log"),
                    env={"PSX_OVERLAY_CAPTURES": os.path.join(out, "cap.json")})
    return g, out


def enter(g):
    Field(g, check).enter()
    return g.partner_type


GUIDE = ("dw.evoguide", "evoguide")
ROSTER = ("dw.vanilla.roster", "roster")


def calm(g):
    g.set("remaining_lifetime", 9999)
    g.set("poop_level", 1000)
    g.set("tiredness", 0)


def force(g, target):
    """Setup only (as verify_roster_evolution.py): an ordinary forced evolution."""
    calm(g)
    if g.script_sync_bit == 0:
        g.set("script_sync_bit", 1)
    g.set("evolution_target", target)
    g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
    g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
    for _ in range(200):
        g.advance(30)
        if g.partner_type == target:
            break
    g.advance(180)
    for _ in range(60):
        if g.tamer_state == 0 and g.partner_state == 1:
            break
        g.route("4:cross,30:none")
    g.set("evolution_target", -1)
    return g.partner_type == target


def shaped_pick(g, target, tag):
    """Meet `target`'s requirements, read the panel's pick, then let the game choose."""
    p = py_model(g)
    c = next(c for c in p["cands"] if c["t"] == target)
    r = c["req"]
    stats = {"hp": max(r[1], 10) * 10, "mp": max(r[2], 10) * 10, "off": max(r[3], 5),
             "def": max(r[4], 5), "speed": max(r[5], 5), "brain": max(r[6], 5)}
    g.set("evo_timer", 0)
    for k, v in stats.items():
        g.set(k, v)
    g.set("care_mistakes", max(r[7], 0))
    g.set("weight", max(r[8], 0))
    g.set("discipline", 100)
    g.set("happiness", 100)
    g.set("battles", max(r[11], 0))
    calm(g)
    wait_fresh_model(g, n=3)
    s, cs, p = compare(g, tag + " shaped")
    pick = s["pick"]
    print("  panel says Now: %d %s (shaped for %d %s)" % (
        pick, Mem(g).name(pick) if pick > 0 else "", target, c["name"]), flush=True)
    g.set("evo_timer", p["thr"])
    chosen = None
    for _ in range(150):
        g.advance(20)
        if g.evolution_target != -1:
            chosen = g.evolution_target
            break
    check(chosen == pick, "%s: panel's pick %d equals the game's chooser %s" % (tag, pick, chosen))
    check(pick == target, "%s: the shaped target %d is the pick" % (tag, target))


def phase_stock(a):
    g, out = launch(a, "stock", [GUIDE])
    try:
        print("partner", enter(g), Mem(g).name(g.partner_type), flush=True)
        calm(g)
        s = guide(g, "state")
        check(s["enabled"] == 1 and s["open"] == 0, "guide active and closed at start")
        named(g, "host_key", name="F2")
        g.advance(30)
        s = wait_fresh_model(g)
        check(s["open"] == 1 and s["seq"] > 0, "F2 opens the panel (seq %d)" % s["seq"])
        compare(g, "stock Agumon")
        print("  lines:", guide(g, "lines"), flush=True)
        for nm, w, h in (("4x3", 960, 720), ("16x9", 1280, 720), ("21x9", 1680, 720)):
            g.d.cmd("window_size", w=w, h=h)
            g.advance(60)
            path = os.path.join(out, "panel_%s.png" % nm)
            check(present_shot(g, path), "screenshot %s %dx%d -> %s" % (nm, w, h, path))
            ww, wh, x0, y0, sx, sy, sc = guide(g, "state")["geom"]
            check((ww, wh) == (w, h) and x0 >= 0 and y0 >= 0 and x0 + sx <= ww and y0 + sy <= wh,
                  "%s: whole panel inside the window at %dx (%d,%d %dx%d in %dx%d)"
                  % (nm, sc, x0, y0, sx, sy, ww, wh))
            if nm == "16x9":
                check(sc >= 2, "16x9: drawn at %dx (2x wanted)" % sc)
        check(guide(g, "state")["draws"] > 0, "the HUD layer drew the panel")
        g.d.cmd("window_size", w=960, h=720)
        for want in (0, 1):
            g.d.cmd("set_input", layer="host", buttons="0x%04X" % pad_word("select", "l3"))
            g.advance(10)
            g.d.cmd("set_input", layer="host", buttons="0xFFFF")
            g.advance(10)
            check(guide(g, "state")["open"] == want, "Select + L3 -> open=%d" % want)
        g.d.cmd("clear_input")
        first = py_model(g)["cands"][0]["t"]
        shaped_pick(g, first, "stock")
    finally:
        g.shutdown()


def phase_roster(a):
    g, out = launch(a, "roster", [ROSTER, GUIDE])
    try:
        print("partner", enter(g), flush=True)
        check(Mem(g).u16(DETECT_LUI) == 0x9F00, "roster active")
        check(force(g, 5), "setup: forced Agumon -> Greymon (5), partner %d" % g.partner_type)
        guide(g, "open")
        wait_fresh_model(g)
        s, cs, p = compare(g, "roster Greymon")
        roster_t = [c["t"] for c in cs if c["t"] > 62]
        check(bool(roster_t), "the roster's target is listed: %s" % roster_t)
        print("  lines:", guide(g, "lines"), flush=True)
        present_shot(g, os.path.join(out, "panel_roster.png"))
        if roster_t:
            shaped_pick(g, roster_t[0], "roster")
    finally:
        g.shutdown()


def fingerprints(g, frames=360):
    g.d.cmd("frame_fingerprint", reset_on_load=1)
    g.savestate("load", 9)
    g.advance(frames + 30)
    e = g.d.cmd("frame_fingerprint", count=frames)["entries"]
    keys = ("cyc", "wc", "ws", "mmio", "mc", "sp", "sc", "qc")
    return [tuple(x.get(k) for k in keys) for x in e[:frames]]


def phase_idle(a):
    g, out = launch(a, "idle_off", [])
    try:
        enter(g)
        calm(g)
        g.advance(60)
        g.savestate("save", 9)
        a_run = fingerprints(g)
        a_ctl = fingerprints(g)
    finally:
        g.shutdown()
    src = os.path.join(out, "cards", "openbios")      # slots live beside the cards
    g, out = launch(a, "idle_on", [GUIDE])
    try:
        dst = os.path.join(out, "cards", "openbios")
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(src):
            if f.endswith("_slot09.pst"):
                shutil.copy(os.path.join(src, f), os.path.join(dst, f))
        g.advance(600)
        check(guide(g, "state")["enabled"] == 1, "guide ticked (closed)")
        b_run = fingerprints(g)
        check(guide(g, "state")["open"] == 0, "guide stayed closed")
    finally:
        g.shutdown()
    same_ctl = sum(1 for x, y in zip(a_run, a_ctl) if x == y)
    same = sum(1 for x, y in zip(a_run, b_run) if x == y)
    n = min(len(a_run), len(b_run), len(a_ctl))
    print("  frames compared %d: control %d identical, guide-closed %d identical" % (n, same_ctl, same))
    check(n > 100 and same_ctl == n, "control: the same state loaded twice is frame-identical")
    check(n > 100 and same == n, "guide ticked but closed: frame-identical to unticked")


def phase_dw15(a):
    g, out = launch(a, "dw15", [("dw.1_5", "dw15"), GUIDE], card_sub="dw15")
    try:
        end = time.time() + 120
        while time.time() < end and Mem(g).u16(GATE_EXE) != 0x801F:
            time.sleep(1)
        check(Mem(g).u16(GATE_EXE) == 0x801F, "1.5's executable is running")
        g.advance(300)
        named(g, "host_key", name="F2")
        g.advance(30)
        s = wait_fresh_model(g)
        lines = guide(g, "lines")
        print("  lines:", lines, flush=True)
        check(s["open"] == 1 and s["ok"] == 0 and "Not available on Digimon World 1.5" in lines,
              "1.5: the panel says it is not available")
        present_shot(g, os.path.join(out, "panel_dw15.png"))
    finally:
        g.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(ROOT, "build-debug"))
    ap.add_argument("--seed", default=os.path.join(ROOT, "saves"))
    ap.add_argument("--port", type=int, default=7210)
    ap.add_argument("--phase", default="all", choices=("all", "stock", "roster", "idle", "dw15"))
    a = ap.parse_args()
    for name in ("stock", "roster", "idle", "dw15"):
        if a.phase in ("all", name):
            print("\n== %s ==" % name, flush=True)
            try:
                globals()["phase_" + name](a)
            except Exception as e:      # a dead game is a failed phase, not a stop
                check(False, "%s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d failure(s)" % len(FAILS))
    for f in FAILS:
        print("  " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
