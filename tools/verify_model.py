#!/usr/bin/env python3
"""Byte-proof model verification -- no vision, no interpretation.

Dumps the full 2 MB of guest RAM over the debug server and searches it for
byte probes taken from a model file as it exists on the disc. Either the
model's bytes are resident in RAM or they are not.

  python tools/verify_model.py <file.MMD> [name]

Probes: 32 bytes of header (contains per-model size fields) and 64 bytes of
interior geometry at +0x1000, which cannot collide between different models.
Exit code 0 = both probes found; 3 = not found.
"""
import json
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 4370


def cmd(name, **params):
    s = socket.create_connection((HOST, PORT), timeout=120)
    try:
        f = s.makefile("rwb")
        f.write((json.dumps({"id": 1, "cmd": name, **params}) + "\n").encode())
        f.flush()
        # the 2 MB dump is a single ~4 MB response line -- read until newline
        buf = bytearray()
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            buf += b
            if b.endswith(b"\n"):
                break
        return json.loads(buf.decode())
    finally:
        s.close()


def main():
    path = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else path
    blob = open(path, "rb").read()
    probes = [("header", blob[:32])]
    if len(blob) > 0x1040:
        probes.append(("interior+0x1000", blob[0x1000:0x1040]))

    t0 = time.time()
    r = cmd("read_ram", addr="0x80000000", len=0x200000)
    ram = bytes.fromhex(r["hex"])
    print("dumped %d bytes of guest RAM in %.1fs" % (len(ram), time.time() - t0))

    ok = True
    for pname, sig in probes:
        hits = []
        i = ram.find(sig)
        while i >= 0 and len(hits) < 8:
            hits.append(0x80000000 + i)
            i = ram.find(sig, i + 1)
        if hits:
            print("  %-16s %-18s FOUND at %s"
                  % (label, pname, ", ".join("0x%08X" % h for h in hits)))
        else:
            print("  %-16s %-18s not present" % (label, pname))
            ok = False
    print("VERDICT: %s bytes %s resident in guest RAM"
          % (label, "ARE" if ok else "are NOT"))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
