#!/usr/bin/env python3
"""Transport for the Digimon World debug server. No game knowledge lives here.

Three things this exists to get right, each of which silently corrupts results
if you get it wrong:

1. FRAMING. The server answers one request per TCP connection, but a handler is
   free to emit more than one line (fn_filter ends its format string with a
   newline; several *_dump handlers emit a line per record). readline() returns
   the first fragment and the client happily parses a truncated object. This
   reads with a brace-balanced, string-aware framer instead.

2. ENCODING. json_get_str hands a QUOTED token straight to hex_to_u32, which is
   always base 16 -- so {"val":"13"} writes 19, not 13. Bare decimals go through
   a different path. The rule is enforced here, inside poke/write_bytes, so a
   caller cannot get it wrong: addresses and values are always emitted as
   explicit 0x-prefixed strings, lengths always as bare ints.

3. ERRORS ARE NOT DATA. drive.py does .get("frame", 0), so a wedged server
   reads as frame 0 and a wait loop returns instantly. Here a failed reply
   raises. A test that cannot run must never look like a test that passed.
"""
import json
import socket

HOST = "127.0.0.1"
DEFAULT_PORT = 4370
RECV_BUF_SIZE = 8192          # debug_server.c: requests longer than this are cut

# PS1 digital pad word: active low, so a clear bit means pressed.
BITS = {
    "select": 1 << 0, "l3": 1 << 1, "r3": 1 << 2, "start": 1 << 3,
    "up": 1 << 4, "right": 1 << 5, "down": 1 << 6, "left": 1 << 7,
    "l2": 1 << 8, "r2": 1 << 9, "l1": 1 << 10, "r1": 1 << 11,
    "triangle": 1 << 12, "circle": 1 << 13, "cross": 1 << 14, "square": 1 << 15,
}
IDLE = 0xFFFF


class Dead(RuntimeError):
    """The process is gone -- connection refused or reset."""


class Wedge(RuntimeError):
    """The emu thread did not reach a safe point. A HANG verdict, not noise."""


class CmdError(RuntimeError):
    """The server replied ok:false."""

    def __init__(self, cmd, err, reply):
        super().__init__("%s: %s" % (cmd, err))
        self.cmd, self.err, self.reply = cmd, err, reply


def pad_word(*names):
    """Pad word with the named buttons held. pad_word() == 0xFFFF (nothing)."""
    w = IDLE
    for n in names:
        n = n.strip().lower()
        if n in ("none", "", "idle"):
            continue
        if n not in BITS:
            raise ValueError("unknown button %r" % n)
        w &= ~BITS[n] & 0xFFFF
    return w


def parse_route(spec):
    """'60:none,4:start' -> [(60, 0xFFFF), (4, 0xFFF7)]"""
    out = []
    for step in spec.split(","):
        step = step.strip()
        if not step:
            continue
        frames, _, buttons = step.partition(":")
        out.append((int(frames), pad_word(*buttons.split("+"))))
    return out


def _frame_json(sock_file):
    """Read one complete JSON object, tolerating extra lines after it.

    Brace-balanced and string-aware: a brace inside a string literal (an error
    message, a file path) must not end the object.
    """
    buf = []
    depth = 0
    in_str = False
    esc = False
    started = False
    while True:
        ch = sock_file.read(1)
        if not ch:
            if started:
                raise Dead("connection closed mid-object")
            raise Dead("connection closed with no reply")
        c = ch.decode("utf-8", "replace")
        if not started:
            if c != "{":
                continue            # skip leading whitespace/newlines
            started = True
        buf.append(c)
        if esc:
            esc = False
            continue
        if in_str:
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return "".join(buf)


class Dbg:
    """One connection per request -- the server closes after replying."""

    def __init__(self, port=DEFAULT_PORT, timeout=35.0):
        self.port = port
        self.timeout = timeout
        self._id = 0

    # ---- core ------------------------------------------------------------
    def raw(self, name, **params):
        """Send one command. Returns the parsed reply even when ok is false."""
        self._id += 1
        req = {"id": self._id, "cmd": name}
        req.update(params)
        line = json.dumps(req)
        # The IO thread short-circuits on the literal substring "ping" anywhere
        # in the request and answers from the wrong handler.
        if name != "ping" and '"ping"' in line:
            raise ValueError("request contains the token \"ping\"; the server "
                             "would hijack it: %s" % line)
        if len(line) + 1 > RECV_BUF_SIZE:
            raise ValueError("request is %d bytes, over the server's %d-byte "
                             "receive buffer" % (len(line) + 1, RECV_BUF_SIZE))
        try:
            s = socket.create_connection((HOST, self.port), timeout=self.timeout)
        except (ConnectionRefusedError, OSError) as e:
            raise Dead("cannot connect to %s:%d (%s)" % (HOST, self.port, e))
        try:
            f = s.makefile("rwb")
            f.write((line + "\n").encode())
            f.flush()
            text = _frame_json(f)
        except (ConnectionResetError, ConnectionAbortedError) as e:
            raise Dead("connection reset during %s (%s)" % (name, e))
        except socket.timeout:
            raise Wedge("no reply to %s within %.0fs" % (name, self.timeout))
        finally:
            try:
                s.close()
            except OSError:
                pass
        reply = json.loads(text)
        # The wedge reply uses 'err' and carries no id, unlike a normal error.
        if reply.get("ok") is False and "err" in reply and "id" not in reply:
            raise Wedge("emu thread busy or frozen (during %s)" % name)
        return reply

    def cmd(self, name, **params):
        """raw() but ok:false raises."""
        r = self.raw(name, **params)
        if not r.get("ok"):
            raise CmdError(name, r.get("error") or r.get("err") or "?", r)
        return r

    # ---- primitives, with the encoding rule baked in ---------------------
    def ping(self):
        """IO-thread fast path: answers even while the emu thread is frozen.
        Carries no frame field -- use it for liveness, never for progress."""
        return self.raw("ping")

    def alive(self):
        try:
            return bool(self.ping().get("ok"))
        except (Dead, Wedge):
            return False

    def frame(self):
        """Guest frame counter. Raises rather than returning a sentinel."""
        r = self.cmd("get_registers")
        if "frame" not in r:
            raise CmdError("get_registers", "reply has no frame field", r)
        return int(r["frame"])

    def read(self, addr, n):
        """Read n bytes. addr as a hex STRING, len as a bare INT."""
        if n < 1:
            raise ValueError("len must be >= 1")
        r = self.cmd("read_ram", addr="0x%08X" % addr, len=int(n))
        b = bytes.fromhex(r["hex"])
        if len(b) != n:
            raise CmdError("read_ram", "asked %d bytes, got %d" % (n, len(b)), r)
        return b

    def poke(self, addr, val):
        """Write one byte. Both operands go as 0x-prefixed strings."""
        self.cmd("write_ram", addr="0x%08X" % addr, val="0x%02X" % (val & 0xFF))

    def write_bytes(self, data, addr):
        """Bulk write, applied inside ONE emulator safe point and verified
        server-side (see handle_write_ram_bytes). Use this for anything wider
        than a byte, and for any multi-field state change that must not be
        observed half-applied."""
        if not data:
            raise ValueError("empty payload")
        return self.cmd("write_ram_bytes", addr="0x%08X" % addr,
                        hex=bytes(data).hex())

    def mod_plan(self):
        """Fingerprint of the mod plan actually loaded in this process."""
        return self.cmd("mod_plan")["fingerprint"]


def main():
    """Self-test: proves the framer, the encoding rule and the verified write.

    The 13-vs-0x13 round trip is the important one. A quoted decimal is parsed
    base 16 by the server, so a naive client writes 19 where it meant 13 and
    every later assertion is measured against a value that was never set.
    """
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    d = Dbg(port=port)
    print("ping        :", d.ping().get("ok"))
    print("frame       :", d.frame())
    print("mod_plan    :", d.mod_plan()[:16], "...")

    scratch = 0x801FFF00          # high RAM, well clear of anything the game uses
    keep = d.read(scratch, 4)
    try:
        d.poke(scratch, 13)
        got = d.read(scratch, 1)[0]
        print("poke 13     : read back %d %s" % (got, "OK" if got == 13 else "*** WRONG"))
        d.write_bytes(bytes([0xDE, 0xAD, 0xBE, 0xEF]), scratch)
        got4 = d.read(scratch, 4)
        print("bulk write  : %s %s" % (got4.hex(" "),
                                       "OK" if got4 == b"\xDE\xAD\xBE\xEF" else "*** WRONG"))
    finally:
        d.write_bytes(keep, scratch)
        print("restored    :", d.read(scratch, 4).hex(" "))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
