#!/usr/bin/env python3
"""Measure how much wall-clock time the game spends loading.

The runtime's `load_transitions` command reports state edges with host
timestamps: `load` flips to 1 when a load is detected and back to 0 when it
ends. Summing the 1-intervals gives real load time, which is the only honest
way to judge a load accelerator -- "feels faster" is not a measurement.

Note this command answers with MANY lines, unlike every other debug command,
so it needs its own reader rather than drive.py's one-line client.

  python tools/measure_loads.py            # summary
  python tools/measure_loads.py --detail   # per-load breakdown
"""
import json
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 4370


def fetch():
    s = socket.create_connection((HOST, PORT), timeout=20)
    try:
        f = s.makefile("rwb")
        f.write((json.dumps({"id": 1, "cmd": "load_transitions"}) + "\n").encode())
        f.flush()
        time.sleep(1.2)          # let the multi-line reply land
        chunks = []
        s.settimeout(2.0)
        try:
            while True:
                b = s.recv(1 << 16)
                if not b:
                    break
                chunks.append(b)
        except socket.timeout:
            pass
    finally:
        s.close()
    return b"".join(chunks).decode(errors="replace")


def parse(text):
    edges = []
    for line in text.splitlines():
        line = line.strip().lstrip(",")
        i = line.find('{"seq"')
        if i < 0:
            continue
        j = line.rfind("}")
        try:
            edges.append(json.loads(line[i:j + 1]))
        except ValueError:
            continue
    return edges


def main():
    detail = "--detail" in sys.argv
    edges = parse(fetch())
    if not edges:
        print("no load edges reported (has the game loaded anything yet?)")
        return 1

    spans, start = [], None
    for e in edges:
        if e.get("load") == 1 and start is None:
            start = e
        elif e.get("load") == 0 and start is not None:
            spans.append((start, e))
            start = None

    first, last = edges[0]["host_ms"], edges[-1]["host_ms"]
    total_load = sum(b["host_ms"] - a["host_ms"] for a, b in spans)
    window = max(1, last - first)

    if detail:
        print("%-5s %-10s %-10s %s" % ("#", "start_ms", "ms", "frames"))
        for n, (a, b) in enumerate(spans, 1):
            print("%-5d %-10d %-10d %d"
                  % (n, a["host_ms"], b["host_ms"] - a["host_ms"],
                     b["frame"] - a["frame"]))
        print()

    print("edges          : %d" % len(edges))
    print("loads          : %d" % len(spans))
    print("total load time: %.2f s" % (total_load / 1000.0))
    print("observed window: %.2f s" % (window / 1000.0))
    print("loading         : %.1f%% of the window" % (100.0 * total_load / window))
    if spans:
        longest = max(b["host_ms"] - a["host_ms"] for a, b in spans)
        print("longest single : %.2f s" % (longest / 1000.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
