#!/usr/bin/env python3
"""Drive Digimon World Vice Recompiled through its TCP debug server.

Needs a debug-tools build (RelWithDebInfo / -DPSX_DEBUG_TOOLS=ON), which
serves JSON-over-newline on localhost:4370.

  python tools/drive.py status
  python tools/drive.py shot out.png
  python tools/drive.py tap start 4
  python tools/drive.py route "60:none,4:start,60:none,4:cross"
  python tools/drive.py overlays
"""
import json
import socket
import sys
import time

PORT = 4370
HOST = "127.0.0.1"

# PS1 digital pad word: inverted (bit clear = pressed). 0xFFFF = nothing held.
BITS = {
    "select": 1 << 0, "l3": 1 << 1, "r3": 1 << 2, "start": 1 << 3,
    "up": 1 << 4, "right": 1 << 5, "down": 1 << 6, "left": 1 << 7,
    "l2": 1 << 8, "r2": 1 << 9, "l1": 1 << 10, "r1": 1 << 11,
    "triangle": 1 << 12, "circle": 1 << 13, "cross": 1 << 14, "square": 1 << 15,
}
IDLE = 0xFFFF


def word(*names):
    """Pad word with the named buttons held down."""
    w = IDLE
    for n in names:
        n = n.strip().lower()
        if n in ("none", "", "idle"):
            continue
        if n not in BITS:
            raise SystemExit(f"unknown button: {n} (have {', '.join(sorted(BITS))})")
        w &= ~BITS[n]
    return w & 0xFFFF


class Conn:
    """One TCP connection per command.

    The runtime's debug server (runtime/src/debug_server.c io_thread_main) is
    one-shot by design: accept -> recv_line -> respond -> sock_close. Reusing a
    socket for a second request gets it aborted, so connect per command.
    """

    def __init__(self, port=PORT, timeout=35.0):
        self.port = port
        self.timeout = timeout
        self.id = 0

    def cmd(self, name, **params):
        self.id += 1
        req = {"id": self.id, "cmd": name}
        req.update(params)
        s = socket.create_connection((HOST, self.port), timeout=self.timeout)
        try:
            f = s.makefile("rwb")
            f.write((json.dumps(req) + "\n").encode())
            f.flush()
            line = f.readline()
        finally:
            s.close()
        if not line:
            raise SystemExit(f"no response to {name}")
        return json.loads(line.decode())

    def frame(self):
        # The `ping` handler is an IO-thread fast-path with no frame field;
        # get_registers is serviced by the emu thread and carries the count.
        return self.cmd("get_registers").get("frame", 0)

    def wait_frames(self, n, poll=0.05):
        """Block until the guest has advanced n frames (or it stops advancing)."""
        start = self.frame()
        target = start + n
        stalls = 0
        last = start
        while True:
            cur = self.frame()
            if cur >= target:
                return cur
            if cur == last:
                stalls += 1
                if stalls > 400:  # ~20s with no progress
                    raise SystemExit(f"guest stalled at frame {cur}")
            else:
                stalls = 0
                last = cur
            time.sleep(poll)

    def run_route(self, steps):
        """steps: [(frames, [buttons...]), ...] — deterministic vblank-exact input."""
        self.cmd("input_route_clear")
        total = 0
        for frames, buttons in steps:
            r = self.cmd("input_route_append", frames=int(frames), buttons=word(*buttons))
            if not r.get("ok"):
                raise SystemExit(f"route append failed: {r}")
            total += int(frames)
        r = self.cmd("input_route_start")
        if not r.get("ok"):
            raise SystemExit(f"route start failed: {r}")
        self.wait_frames(total + 2)
        return total

    def shot(self, path):
        return self.cmd("screenshot", path=path)


def parse_route(spec):
    """'60:none,4:start,30:none' -> [(60,['none']), (4,['start']), ...]"""
    steps = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        frames, _, btns = chunk.partition(":")
        steps.append((int(frames), btns.split("+") if btns else ["none"]))
    return steps


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    op = sys.argv[1]
    c = Conn()

    if op == "status":
        out = {"frame": c.frame()}
        for probe in ("overlay_loader_status", "gpu_state", "irq_state"):
            r = c.cmd(probe)
            if r.get("ok"):
                out[probe] = {k: v for k, v in r.items() if k not in ("id", "ok")}
        print(json.dumps(out, indent=2)[:4000])
    elif op == "shot":
        print(json.dumps(c.shot(sys.argv[2] if len(sys.argv) > 2 else "psx_screenshot.png")))
    elif op == "tap":
        btn = sys.argv[2]
        hold = int(sys.argv[3]) if len(sys.argv) > 3 else 4
        c.run_route([(2, ["none"]), (hold, [btn]), (8, ["none"])])
        print(json.dumps({"tapped": btn, "frame": c.frame()}))
    elif op == "route":
        n = c.run_route(parse_route(sys.argv[2]))
        print(json.dumps({"frames": n, "frame": c.frame()}))
    elif op == "wait":
        print(json.dumps({"frame": c.wait_frames(int(sys.argv[2]))}))
    elif op == "overlays":
        for probe in ("overlay_loader_status", "overlay_candidates",
                      "overlay_capture_dump", "autocompile_status"):
            r = c.cmd(probe)
            print(probe, "->", json.dumps({k: v for k, v in r.items() if k != "id"})[:600])
    elif op == "cmd":
        params = {}
        for kv in sys.argv[3:]:
            k, _, v = kv.partition("=")
            params[k] = int(v, 0) if v.lstrip("-").isdigit() else v
        print(json.dumps(c.cmd(sys.argv[2], **params), indent=2)[:4000])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
