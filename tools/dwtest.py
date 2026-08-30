#!/usr/bin/env python3
"""Game-level tests for Digimon World Recompiled.

  py -3.12 tools/dwtest.py list
  py -3.12 tools/dwtest.py evolve --target 12          # attach to a running game
  py -3.12 tools/dwtest.py evolve --target 26 --from-slot 1

Every verdict is a value read out of guest memory. Screenshots are captured as
artifacts for a human to look at; nothing here branches on one.

The load-bearing design decision is that the harness NEVER writes
PARTNER_ENTITY.type -- dwgame marks it read-only and set() refuses. So if the
field changes to the requested target, the only thing that could have written
it is guest code. That is what separates "a value changed" from "the game
evolved the partner".
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwgame import Game, StallRule, FIELDS            # noqa: E402
from dwdbg import Dead, Wedge, CmdError               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ART = os.path.join(ROOT, "build-debug", "testruns")


class Report:
    """Structured result. Records the OBSERVED value on every check, so a
    failure says what was actually there rather than just 'expected X'."""

    def __init__(self, name):
        self.name = name
        self.checks = []
        self.notes = {}
        self.artifacts = []
        self.started = datetime.now(timezone.utc).isoformat()
        self.dir = os.path.join(ART, "%s-%s" % (
            name, time.strftime("%Y%m%d-%H%M%S")))
        os.makedirs(self.dir, exist_ok=True)

    def check(self, label, observed, expected=None, pred=None, fatal=False):
        if pred is not None:
            ok = bool(pred(observed))
            want = getattr(pred, "__doc__", None) or "predicate"
        else:
            ok = observed == expected
            want = expected
        self.checks.append({"label": label, "observed": observed,
                            "expected": want, "ok": ok, "fatal": fatal})
        print("  %-4s %-28s observed=%r%s" % (
            "OK" if ok else "FAIL", label, observed,
            "" if ok else "  expected=%r" % (want,)))
        return ok

    def require(self, label, observed, expected=None, pred=None):
        ok = self.check(label, observed, expected, pred, fatal=True)
        if not ok:
            raise Precondition("%s: observed %r, wanted %r"
                               % (label, observed, expected if pred is None
                                  else "predicate"))
        return ok

    def note(self, k, v):
        self.notes[k] = v
        print("  note %-26s %r" % (k, v))

    def artifact(self, path):
        if path:
            self.artifacts.append(path)

    def emit(self, verdict):
        blob = {"test": self.name, "verdict": verdict, "started": self.started,
                "finished": datetime.now(timezone.utc).isoformat(),
                "checks": self.checks, "notes": self.notes,
                "artifacts": self.artifacts}
        p = os.path.join(self.dir, "result.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(blob, f, indent=2, default=str)
        print("\n  report: %s" % p)
        return blob


class Precondition(RuntimeError):
    """The test could not run. Distinct from the test running and failing."""


# --------------------------------------------------------------------------
def test_evolve(g, rep, target, timeout_frames=5400):
    """Force an evolution into `target` and classify what actually happens.

    The kick is five writes; partner_state is written LAST because it is the
    one the game acts on, so the others are already in place when it does.
    """
    src = g.partner_type
    rec_src = g.digimon_record(src) if src else None
    rec_tgt = g.digimon_record(target)

    # ---- preconditions: if these fail the test CANNOT run (exit 2) --------
    rep.require("partner_alive", src, pred=lambda v: v != 0)
    rep.require("game_state_playing", g.game_state, 0)
    rep.require("not_in_menu", g.is_in_menu, pred=lambda v: v != 1)
    rep.require("player_has_control", g.tamer_state, 0)
    rep.require("partner_idle", g.partner_state, 1)
    rep.require("evo_target_idle", g.evolution_target, -1)
    rep.require("target_is_instantiable", rec_tgt["bones"], pred=lambda v: v > 0)
    rep.require("target_differs", target, pred=lambda v: v != src)

    rep.note("source", rec_src)
    rep.note("target", rec_tgt)
    rep.note("source_level", rec_src["level"])
    rep.note("target_level", rec_tgt["level"])
    rep.note("mod_plan", g.plan())
    rep.artifact(g.artifact_shot(os.path.join(rep.dir, "pre_kick.png")))

    # ---- neutralise anything that would steal PARTNER_STATE ---------------
    # A lifetime of 0 forces state 8/14, and a poop timer firing forces 7 --
    # either would look like the evolution failing when in fact it never ran.
    g.set("remaining_lifetime", 9999)
    g.set("poop_level", 9999)
    g.set("tiredness", 0)
    if g.script_sync_bit == 0:
        g.set("script_sync_bit", 1)     # else success is indistinguishable from a hang

    # ---- the kick ---------------------------------------------------------
    # Only the stable values are asserted before firing. The substates are
    # game-owned the moment their state starts ticking, so each state+substate
    # pair is written ATOMICALLY (adjacent bytes, one safe point) rather than
    # written separately and read back -- the game advances them in between.
    g.set("evolution_target", target)
    staged = g.get("evolution_target", "script_sync_bit")
    rep.require("kick_staged", staged,
                {"evolution_target": target, "script_sync_bit": 1})
    kick_frame = g.d.frame()
    rep.note("kick_frame", kick_frame)
    g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
    g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))  # arms it

    # ---- wait and classify ------------------------------------------------
    def completed(gg):
        # A finished evolution parks on the "Tamer level went up!!!" textbox
        # (partner_state 11 / tamer_state 20) until it is acknowledged, so
        # waiting for partner_state==1 alone always burns the full timeout on
        # a run that actually succeeded.
        if gg.partner_type != target or gg.evolution_target != -1:
            return False
        return gg.partner_state == 1 or gg.tamer_state == 20

    res = g.wait_until(
        completed, timeout_frames=timeout_frames,
        probe=("partner_type", "partner_state", "partner_sub_state",
               "tamer_state", "evolution_target", "evo_seq_timer"),
        stall_rules=(
            # The known one: sub-state 1 spins forever on tamer proximity. The
            # frame counter keeps advancing and the server stays responsive, so
            # only a signature match can catch it.
            StallRule("SOFTLOCK_CLOSENESS",
                      lambda gg: gg.partner_state == 13 and gg.partner_sub_state == 1,
                      600, "partner was not adjacent to the tamer"),
            StallRule("SOFTLOCK_EVL_OVERLAY",
                      lambda gg: gg.partner_sub_state == 2 and gg.evo_seq_timer >= 30000,
                      300, "the EVL overlay never loaded"),
            StallRule("INERT",
                      lambda gg: gg.partner_state == 13 and gg.partner_sub_state == 0,
                      600, "the state machine never engaged"),
            StallRule("REVERTED",
                      lambda gg: gg.partner_state not in (13,) and gg.partner_type == src,
                      240, "the sequence rejected the target"),
        ))

    rep.note("wait_outcome", res.outcome)
    rep.note("wait_detail", res.detail)
    rep.note("frames_elapsed", res.frames)
    with open(os.path.join(rep.dir, "samples.json"), "w", encoding="utf-8") as f:
        json.dump(res.samples, f, indent=1)
    rep.artifact(os.path.join(rep.dir, "samples.json"))

    final = {}
    try:
        final = g.get("partner_type", "partner_state", "partner_sub_state",
                      "tamer_state", "evolution_target", "is_gametime_running")
        rep.note("final_state", final)
        rep.artifact(g.artifact_shot(os.path.join(rep.dir, "post_kick.png")))
    except (Dead, Wedge) as e:
        rep.note("final_state_unreadable", str(e))

    if res.outcome == "CRASHED":
        return "CRASHED", 5
    if res.outcome == "HUNG":
        return "HUNG(%s)" % res.detail, 4

    # partner_type is read-only to this harness, so a match is guest-written.
    got = final.get("partner_type")
    ok_type = rep.check("guest_wrote_partner_type", got, target)
    if not ok_type:
        return "NO_EVOLUTION", 3

    rep.check("evo_target_cleared", final.get("evolution_target"), -1)
    rep.check("partner_returned_to_idle", final.get("partner_state"), 1)
    after = g.digimon_record(got)
    rep.note("partner_after", after)
    rep.check("level_matches_target", after["level"], rec_tgt["level"])

    # A completed evolution ends on a dialogue ("Tamer level went up!!!"), so
    # tamer_state parks at 20 until it is acknowledged. That is a textbox, not
    # a hang -- dismiss it and then judge whether control really came back.
    if final.get("tamer_state") == 20:
        rep.note("post_evolution_dialogue", True)
        for _ in range(6):
            g.route("4:cross,45:none")
            if g.tamer_state == 0 and g.partner_state == 1:
                break
        g.settle()
        final = g.get("partner_type", "partner_state", "partner_sub_state",
                      "tamer_state", "evolution_target", "is_gametime_running")
        rep.note("final_state_after_dialogue", final)

    rep.check("partner_idle_after", final.get("partner_state"), 1)
    rep.check("gametime_resumed", final.get("is_gametime_running"), 1)
    control = rep.check("player_regained_control", final.get("tamer_state"), 0)
    return ("SUCCESS" if control else "SUCCESS_CONTROL_STUCK"), 0


# --------------------------------------------------------------------------
def cmd_evolve(args):
    rep = Report("evolve_%d" % args.target)
    g = Game.attach(port=args.port)
    if args.from_slot is not None:
        print("  loading savestate slot %d" % args.from_slot)
        g.savestate("load", args.from_slot)
        r = g.settle()
        print("  settled: %s after %d frames" % (r.outcome, r.frames))
    print("\n%s\n  target %d\n" % ("=" * 62, args.target))
    try:
        verdict, code = test_evolve(g, rep, args.target,
                                    timeout_frames=args.timeout_frames)
    except Precondition as e:
        print("\n  PRECONDITION FAILED: %s" % e)
        rep.emit("CANNOT_RUN")
        return 2
    except (Dead, Wedge) as e:
        print("\n  process died/wedged: %s" % e)
        rep.emit("CRASHED")
        return 5
    print("\n  VERDICT: %s" % verdict)
    rep.emit(verdict)
    return code


def cmd_list(args):
    print("tests:")
    print("  evolve --target N   force an evolution into type N and classify it")
    print("\nfields (%d):" % len(FIELDS))
    for n, f in sorted(FIELDS.items()):
        print("  %-22s 0x%08X %-8s %s" % (n, f.addr, f.kind, f.write))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=4370)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    e = sub.add_parser("evolve")
    e.add_argument("--target", type=int, required=True)
    e.add_argument("--from-slot", type=int, default=None,
                   help="load this savestate slot first (repeatable fixture)")
    e.add_argument("--timeout-frames", type=int, default=5400)
    e.set_defaults(fn=cmd_evolve)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
