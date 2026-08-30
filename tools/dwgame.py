#!/usr/bin/env python3
"""Game-aware layer over the debug transport: named fields, lifecycle, waiting.

Tests say g.partner_state, not 0x80134C5B. Every address below was verified
against dw_decomp/config/symbols.txt, and every struct offset was walked from
the type definitions in include/dw/ and then cross-checked against an
independent source (the shipped cheats.txt, whose comments carry the same
offsets: happiness +0x2A, discipline +0x28, poopLevel +0x18, tiredness +0x22).

THE RULE THIS FILE ENFORCES: a screenshot is an artifact, never a verdict.
Nothing here returns pass/fail from an image. Assertions read guest memory.
"""
import json
import os
import subprocess
import time
from dataclasses import dataclass, field as dc_field

from dwdbg import Dbg, Dead, Wedge, CmdError, parse_route

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
EXE = os.path.join(ROOT, "build-debug", "Digimon_World_Recompiled.exe")

PARTNER = 0x801557A8      # PARTNER_ENTITY; .digimonEntity.entity.type is the word AT it
PARA = 0x80138460         # PARTNER_PARA
DIGIMON_DATA = 0x8012CEB4
DIGIMON_STRIDE = 52
STATS = PARTNER + 0x38    # Stats.base -- Entity is 0x38 bytes


@dataclass(frozen=True)
class Field:
    name: str
    addr: int
    kind: str          # u8 i8 u16 i16 u32 i32 bytes:N
    write: str         # safe | unsafe | sticky | ro
    note: str = ""

    @property
    def width(self):
        if self.kind.startswith("bytes:"):
            return int(self.kind.split(":")[1])
        return {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4}[self.kind]

    def decode(self, b):
        if self.kind.startswith("bytes:"):
            return b
        signed = self.kind[0] == "i"
        return int.from_bytes(b, "little", signed=signed)

    def encode(self, v):
        if self.kind.startswith("bytes:"):
            if len(v) != self.width:
                raise ValueError("%s wants %d bytes" % (self.name, self.width))
            return bytes(v)
        signed = self.kind[0] == "i"
        return int(v).to_bytes(self.width, "little", signed=signed)


# write:
#   ro     -- never write (policy or hardware)
#   safe   -- persists; nothing recomputes it every tick
#   unsafe -- the game overwrites it next tick; writing is pointless
#   sticky -- persists AND has consequences the game will not undo
_F = [
    # --- identity ---------------------------------------------------------
    Field("partner_type", PARTNER, "u32", "ro",
          "Entity.type. NEVER WRITTEN BY THE HARNESS, on purpose: that is what "
          "makes any observed write to this address provably GUEST code, i.e. "
          "the difference between 'a value changed' and 'the game evolved it'."),
    Field("entity_table_1", 0x8012F348, "u32", "ro",
          "ENTITY_TABLE[1] -- points at PARTNER_ENTITY once a partner exists."),

    # --- state machines ---------------------------------------------------
    Field("partner_state", 0x80134C5B, "i8", "safe", "13 = tickPartnerEvolving."),
    Field("partner_sub_state", 0x80134C5C, "i8", "safe", "Write BEFORE partner_state."),
    Field("tamer_state", 0x80134C91, "i8", "safe",
          "0 = player has control. 6 = idle no-op; also suppresses the game's "
          "own evolution logic, which early-returns when getTamerState() != 0."),
    Field("tamer_substate", 0x80134C92, "i8", "safe"),
    Field("game_state", 0x80134F0A, "i8", "unsafe",
          "GATE: tickPartner reaches the state machine only at 0."),
    Field("is_in_menu", 0x80134EF0, "i32", "unsafe",
          "GATE: the test is 'if (IS_IN_MENU == 1)', so the switch runs for any "
          "value EXCEPT 1. Assert != 1; never write."),
    Field("current_screen", 0x80134DA8, "i16", "unsafe"),
    # Menu/boot progress -- these are what makes the title->Continue route
    # assertable instead of a blind button mash timed against pixels.
    Field("menu_state", 0x80134D34, "i16", "unsafe"),
    Field("main_state", 0x80134EB4, "i16", "unsafe"),
    Field("selection_menu_state", 0x80134FFA, "i16", "unsafe"),
    Field("current_menu", 0x8013504C, "i16", "unsafe"),

    # --- evolution --------------------------------------------------------
    Field("evolution_target", 0x80134E50, "i16", "safe", "-1 = idle."),
    Field("has_used_evoitem", 0x80134E52, "u8", "safe",
          "Non-zero makes the evolution skip the base-stat rescale."),
    Field("script_sync_bit", 0x80134E30, "i32", "safe",
          "SOME_SCRIPT_SYNC_BIT. partner.c:571/668 -- at completion, if this is "
          "0 the game sets it to 1 and never calls setTamerState(0), so a "
          "successful evolution would look like a hang. Pre-set it to 1."),
    Field("is_gametime_running", 0x80134E20, "i32", "safe",
          "0->1 edge is a clean 'sequence finished' signal."),
    Field("evo_seq_timer", 0x80154F90, "i32", "ro",
          "EVO_SEQUENCE_DATA. Clamps at 30000 -- pinned there means the EVL "
          "overlay never loaded."),

    # --- stats (Stats.base, PARTNER + 0x38) -------------------------------
    Field("off", STATS + 0x00, "i16", "safe"),
    Field("def", STATS + 0x02, "i16", "safe"),
    Field("speed", STATS + 0x04, "i16", "safe"),
    Field("brain", STATS + 0x06, "i16", "safe"),
    Field("hp", STATS + 0x10, "i16", "safe"),
    Field("mp", STATS + 0x12, "i16", "safe"),
    Field("current_hp", STATS + 0x14, "i16", "safe"),
    Field("current_mp", STATS + 0x16, "i16", "safe"),

    # --- raise sim (PARTNER_PARA) -- offsets cross-checked vs cheats.txt ---
    Field("para_condition", PARA + 0x00, "u32", "safe"),
    Field("poop_level", PARA + 0x18, "i16", "safe"),
    Field("virus_bar", PARA + 0x1E, "i16", "safe"),
    Field("tiredness", PARA + 0x22, "i16", "safe"),
    Field("discipline", PARA + 0x28, "i16", "safe"),
    Field("happiness", PARA + 0x2A, "i16", "safe"),
    Field("food_level", PARA + 0x3E, "i16", "safe"),
    Field("weight", PARA + 0x42, "i16", "safe"),
    Field("remaining_lifetime", PARA + 0x48, "i16", "safe",
          "0 forces PARTNER_STATE 8/14 and would steal the state machine."),
    Field("age", PARA + 0x4A, "i16", "safe"),
    Field("care_mistakes", PARA + 0x52, "i16", "safe"),
    Field("battles", PARA + 0x54, "i16", "safe"),
    Field("evo_timer", PARA + 0x56, "i16", "safe"),
]
FIELDS = {f.name: f for f in _F}


class StallRule:
    """A named signature that means 'stuck', detected by a predicate holding
    continuously for N guest frames. This exists because the failure that
    matters most here -- tickPartnerEvolving sub-state 1 spinning forever on
    tamer proximity -- keeps the frame counter advancing and the server
    responsive, so no generic liveness check can ever see it."""

    def __init__(self, name, pred, frames, remedy=""):
        self.name, self.pred, self.frames, self.remedy = name, pred, frames, remedy


@dataclass
class WaitResult:
    outcome: str            # DONE | HUNG | CRASHED | TIMEOUT
    detail: str = ""
    frames: int = 0
    samples: list = dc_field(default_factory=list)


class Game:
    """A running instance plus everything a test needs to interrogate it."""

    def __init__(self, dbg, proc=None, log_path=None):
        self.d = dbg
        self.proc = proc
        self.log_path = log_path

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def launch(cls, exe=EXE, port=4370, headless=False, memcard_dir=None,
               log_path=None, boot_timeout=120.0, extra_args=()):
        args = [exe, "--no-launcher", "--debug-port", str(port)]
        if headless:
            args.append("--headless")
        if memcard_dir:
            args += ["--memcard-dir", str(memcard_dir)]
        args += list(extra_args)
        log = open(log_path, "wb") if log_path else subprocess.DEVNULL
        proc = subprocess.Popen(args, cwd=os.path.dirname(exe),
                                stdout=log, stderr=subprocess.STDOUT)
        d = Dbg(port=port)
        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                raise Dead("process exited during boot (code %s)" % proc.returncode)
            try:
                d.frame()
                return cls(d, proc, log_path)
            except (Dead, Wedge, CmdError):
                time.sleep(0.5)
        proc.kill()
        raise Dead("debug server never answered within %.0fs" % boot_timeout)

    @classmethod
    def attach(cls, port=4370):
        d = Dbg(port=port)
        d.frame()
        return cls(d)

    def shutdown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=15)

    # ---- typed field access ---------------------------------------------
    def get(self, *names):
        """One name -> value. Several -> dict. One round trip per field."""
        if len(names) == 1:
            f = FIELDS[names[0]]
            return f.decode(self.d.read(f.addr, f.width))
        return {n: self.get(n) for n in names}

    def set(self, name, value, force=False):
        """Write a named field, applied atomically and verified server-side."""
        f = FIELDS[name]
        if f.write == "ro":
            raise PermissionError("%s is read-only: %s" % (name, f.note))
        if f.write == "unsafe" and not force:
            raise PermissionError(
                "%s is recomputed by the game every tick; writing it does "
                "nothing. Pass force=True if you really mean it." % name)
        if f.write == "sticky" and not force:
            raise PermissionError("%s has side effects the game never undoes; "
                                  "pass force=True" % name)
        self.d.write_bytes(f.encode(value), f.addr)

    def set_atomic(self, *pairs):
        """Write several ADJACENT fields in one safe point.

        Needed because the game keeps ticking between round trips: writing
        tamer_state=6 starts that state's tick, which advances tamer_substate
        before a separate write can land. Any state+substate pair therefore has
        to go down together or it is observed half-applied by the very code it
        is arming. Fields must be contiguous, and are sorted by address here so
        callers can list them in logical order.
        """
        items = sorted(((FIELDS[n], v) for n, v in pairs),
                       key=lambda t: t[0].addr)
        blob = b""
        addr = items[0][0].addr
        cursor = addr
        for f, v in items:
            if f.write == "ro":
                raise PermissionError("%s is read-only" % f.name)
            if f.addr != cursor:
                raise ValueError("%s is not contiguous with the previous field "
                                 "(0x%08X != 0x%08X)" % (f.name, f.addr, cursor))
            blob += f.encode(v)
            cursor += f.width
        self.d.write_bytes(blob, addr)

    def __getattr__(self, name):
        if name in FIELDS:
            return self.get(name)
        raise AttributeError(name)

    # ---- game knowledge --------------------------------------------------
    def digimon_record(self, t):
        b = self.d.read(DIGIMON_DATA + t * DIGIMON_STRIDE, DIGIMON_STRIDE)
        return {
            "type": t,
            "name": b[:20].split(b"\0")[0].decode("ascii", "replace"),
            "bones": int.from_bytes(b[20:24], "little", signed=True),
            "radius": int.from_bytes(b[24:26], "little", signed=True),
            "height": int.from_bytes(b[26:28], "little", signed=True),
            "attr": b[28],
            "level": b[29],
        }

    def live_species(self, level=None):
        """Species the engine can actually instantiate: named, with a real bone
        count. Chosen from RAM, never hardcoded -- type 62 reads bones=0 in this
        build and would hand the evolution sequence a degenerate skeleton."""
        out = []
        blob = self.d.read(DIGIMON_DATA, 180 * DIGIMON_STRIDE)
        for t in range(180):
            b = blob[t * DIGIMON_STRIDE:(t + 1) * DIGIMON_STRIDE]
            name = b[:20].split(b"\0")[0].decode("ascii", "replace")
            bones = int.from_bytes(b[20:24], "little", signed=True)
            lv = b[29]
            if name and bones > 0 and (level is None or lv == level):
                out.append({"type": t, "name": name, "level": lv, "bones": bones})
        return out

    def plan(self):
        return self.d.mod_plan()

    # ---- waiting ---------------------------------------------------------
    def wait_until(self, pred, timeout_frames=3600, probe=(), stall_rules=(),
                   poll_s=0.25):
        """Poll until pred(self) is true, or a stall signature holds, or the
        process dies. Returns a WaitResult; never raises on a game outcome."""
        start = self.d.frame()
        held = {r.name: None for r in stall_rules}
        samples = []
        while True:
            try:
                now = self.d.frame()
            except Dead as e:
                return WaitResult("CRASHED", str(e), 0, samples)
            except Wedge as e:
                return WaitResult("HUNG", "emu thread wedged: %s" % e, 0, samples)
            elapsed = now - start

            try:
                snap = {n: self.get(n) for n in probe} if probe else {}
                done = pred(self)
            except Dead as e:
                return WaitResult("CRASHED", str(e), elapsed, samples)
            except Wedge as e:
                return WaitResult("HUNG", "emu thread wedged: %s" % e, elapsed, samples)
            if snap:
                snap["frame"] = now
                samples.append(snap)
            if done:
                return WaitResult("DONE", "", elapsed, samples)

            for r in stall_rules:
                try:
                    hit = r.pred(self)
                except (Dead, Wedge):
                    hit = False
                if hit:
                    if held[r.name] is None:
                        held[r.name] = now
                    elif now - held[r.name] >= r.frames:
                        return WaitResult("HUNG", r.name, elapsed, samples)
                else:
                    held[r.name] = None

            if elapsed >= timeout_frames:
                return WaitResult("TIMEOUT", "%d frames" % elapsed, elapsed, samples)
            time.sleep(poll_s)

    # ---- input -----------------------------------------------------------
    def route(self, spec, wait=True, poll_s=0.2):
        """Queue and run a pad route: '60:none,4:start,60:none,4:cross'.

        frames and buttons go as BARE INTS (json_get_int), unlike addresses
        which must be hex strings -- the two commands parse differently.
        """
        steps = parse_route(spec)
        self.d.cmd("input_route_clear")
        for frames, word in steps:
            self.d.cmd("input_route_append", frames=int(frames), buttons=int(word))
        self.d.cmd("input_route_start")
        if not wait:
            return
        total = sum(f for f, _ in steps)
        deadline = self.d.frame() + total + 120
        while self.d.frame() < deadline:
            if not self.d.cmd("input_route_status").get("active", False):
                return
            time.sleep(poll_s)

    def advance(self, frames):
        target = self.d.frame() + frames
        while self.d.frame() < target:
            time.sleep(0.05)

    def settle(self, timeout_frames=3600):
        """Wait until the game is genuinely playable: partner alive, player in
        control, partner idle. A savestate load lands mid-transition and takes
        well over a second of guest time to reach this, so a fixed sleep after
        a load reads whatever transient state it happens to catch."""
        res = self.wait_until(
            lambda g: (g.partner_type != 0 and g.tamer_state == 0
                       and g.partner_state == 1),
            timeout_frames=timeout_frames)
        return res

    # ---- savestates ------------------------------------------------------
    def savestate(self, op, slot, timeout_s=30.0):
        """Save or load a slot. The request is STAGED and completes on the emu
        thread, so this polls savestate_status rather than trusting the ack."""
        before = self.d.cmd("savestate_status")
        self.d.cmd("savestate", op=op, slot=int(slot))
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.d.cmd("savestate_status")
            if st.get("generation") != before.get("generation"):
                return st
            time.sleep(0.2)
        raise TimeoutError("savestate %s slot %d did not complete" % (op, slot))

    # ---- artifacts (NEVER a verdict) ------------------------------------
    def artifact_shot(self, path):
        """Capture a screenshot FOR A HUMAN. No caller may branch on this."""
        try:
            self.d.cmd("screenshot", path=str(path))
            return str(path)
        except (CmdError, Dead, Wedge):
            return None


def main():
    """Smoke test: prove transport, lifecycle, field decode and game knowledge
    against an already-running instance."""
    import sys
    g = Game.attach(port=int(sys.argv[1]) if len(sys.argv) > 1 else 4370)
    print("frame        :", g.d.frame())
    print("mod plan     :", g.plan()[:16], "...")
    print("partner_type :", g.partner_type)
    print("partner_state:", g.partner_state, " tamer_state:", g.tamer_state)
    print("game_state   :", g.game_state, " is_in_menu:", g.is_in_menu)
    print("evo_target   :", g.evolution_target)
    ult = g.live_species(level=5)
    print("live Ultimates: %d -> %s" % (
        len(ult), ", ".join("%s(%d)" % (u["name"], u["type"]) for u in ult[:6])))
    if g.partner_type:
        print("partner rec  :", g.digimon_record(g.partner_type))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
