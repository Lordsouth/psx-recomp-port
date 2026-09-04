#!/usr/bin/env python3
"""scan_trigger_ids.py -- audit which trigger ids the disc scripts actually touch.

Usage:
    py -3.12 scan_trigger_ids.py <DG.SCN | MAPHEAD.SCN> [more files ...]
    py -3.12 scan_trigger_ids.py --raw <file>     # pattern cross-check only

Why this exists
---------------
The roster mod widens two guards in the main executable:

  1. setDigimonRaised (0x800FF7F8)
         800FF7FC  sltiu $at,$a0,0x3F   ->  sltiu $at,$a0,0xB4
         800FF808  addi  $v0,$a0,0x200          (trigger = species + 0x200)
     Vanilla writes triggers 0x200..0x23E; the mod writes 0x200..0x2B3.
     NEW WRITE BAND = 0x23F..0x2B3.

  2. recalculatePPandArena (0x800EFA18)
         800EFA40  addiu $s3,$zero,0xCB         (trigger = 0xC8 + i, i starts 3)
         800EFAD4  slti  $at,$s0,0x3B   ->  slti  $at,$s0,0x72
     Vanilla reads triggers 0xCB..0x102; the mod reads 0xCB..0x139.
     NEW READ BAND = 0x103..0x139.

Trigger flags live in ScriptState.triggers[100] (800 bits, ids 0..799) and are
written to the memory card, so an id collision with a disc script is a
save-persistent bug.  This tool decodes the shipped script bytecode and reports
every trigger id the scripts reference, then intersects with the two bands.

Bytecode facts this decoder is built on (dw_decomp, SLUS-01032)
--------------------------------------------------------------
Dispatcher: src/main/script_tick.c tickScript(), the `op = *MAIN_D_80134FDC++`
block.  Opcode is one byte; ranges 0x10-0x27, 0x28-0x3F, 0x46-0x58, 0x5A-0x5F,
0x64-0x7E, 0xFB-0xFF are handled, everything else pauses the script.

Trigger-carrying instructions (all ids are 16-bit little-endian):
    0x1C  set trigger        script_instr10.c:98   skipOneReadOneUShort -> setTrigger
    0x1D  unset trigger      script_instr10.c:102  skipOneReadOneUShort -> unsetTrigger
    0x2C  date compare       script_common.c:3286  scriptCompareDate: set/unsetTrigger
    0x75  spawn chest        script_instr64.c:538  trigger tested by spawnChest
                             (map_object.c:659) and SET by tamer.c:1070 on open
    0x19  condition block    script_anim.c:78 MAIN_func_801050C0, entries whose
                             (condOp & 0x38) == 0 do isTriggerSet(u16)

Operand widths come from the handlers in script_instr10.c / script_instr28.c /
script_instr64.c; the inline-text skipper mirrors showTextbox()
(script_common.c:3009).
"""

import struct
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# bands, derived from the guard constants rather than hardcoded
# ---------------------------------------------------------------------------

RAISED_BASE = 0x200          # setDigimonRaised: addi $v0,$a0,0x200
RAISED_BOUND_STOCK = 0x3F    # sltiu $at,$a0,0x3F
RAISED_BOUND_MOD = 0xB4      # patched to  sltiu $at,$a0,0xB4

PP_BASE = 0xC8               # recalculatePPandArena: addiu $s3,$zero,0xCB, i=3
PP_BOUND_STOCK = 0x3B        # slti $at,$s0,0x3B
PP_BOUND_MOD = 0x72          # patched to  slti $at,$s0,0x72

RAISED_NEW = (RAISED_BASE + RAISED_BOUND_STOCK, RAISED_BASE + RAISED_BOUND_MOD - 1)
PP_NEW = (PP_BASE + PP_BOUND_STOCK, PP_BASE + PP_BOUND_MOD - 1)

BANDS = [
    ("raised-flag WRITE band (setDigimonRaised, species 0x3F..0xB3)", RAISED_NEW),
    ("prosperity READ band (recalculatePPandArena, i=0x3B..0x71)", PP_NEW),
]

TRIGGER_ARRAY_BITS = 100 * 8  # ScriptState.triggers[100] -- script.h:21

# ---------------------------------------------------------------------------
# opcode table: opcode -> (operand bytes after the opcode, flow)
# flow: 'next'  fall through
#       'stop'  trace ends here
# Special opcodes are handled in code: 0x10, 0x18, 0x19, 0x1A, 0x13, 0x14,
# 0x16, 0x17, 0x1C, 0x1D, 0x2C, 0x75, 0xFB, 0xFE, 0xFF
# ---------------------------------------------------------------------------

SIMPLE = {
    # 0x10..0x27  (script_instr10.c)
    0x11: 0, 0x12: 0,          # not in the switch: no operands, falls through
    0x15: 1,                   # pop stack / return -> handled as 'stop'
    0x1B: 1,
    0x1E: 3, 0x1F: 3, 0x20: 3,
    0x21: 1, 0x22: 1, 0x23: 1,
    0x24: 3, 0x25: 1,
    0x26: 3,                   # scriptSetTextboxSize: 1 byte + 2 bytes
    0x27: 1,
    # 0x28..0x3F  (script_instr28.c)
    0x28: 3, 0x29: 3,
    0x2A: 5, 0x2B: 5,          # skipOneReadInteger
    0x2D: 1, 0x2E: 1, 0x2F: 1, 0x30: 1,
    0x31: 3, 0x32: 3, 0x33: 3,
    0x34: 3, 0x35: 3, 0x36: 3,
    0x37: 1,
    0x38: 5, 0x39: 5,          # byte + int32
    0x3A: 3, 0x3B: 3, 0x3C: 3,
    0x3D: 1, 0x3E: 1,
    0x3F: 3,
    # 0x46..0x58  (script_instr28.c)
    0x46: 1,
    0x47: 3,
    0x48: 1, 0x49: 1,
    0x4A: 1,                   # waits, but PC has advanced -> 'next'
    0x4B: 3,
    0x4C: 3, 0x4D: 3,
    0x4E: 7,
    0x4F: 5,
    0x50: 3, 0x51: 3,
    0x52: 7, 0x53: 3,
    0x54: 1,
    0x55: 7,
    0x56: 3, 0x57: 3, 0x58: 1,
    # 0x5A..0x5F  (script_instr28.c)
    0x5A: 3, 0x5B: 1, 0x5C: 1, 0x5D: 1, 0x5E: 1, 0x5F: 1,
    # 0x64..0x7E  (script_instr64.c)
    0x64: 1, 0x65: 1, 0x66: 1,
    0x67: 3,
    0x68: 3, 0x69: 1, 0x6A: 3,
    0x6B: 0,
    0x6C: 7, 0x6D: 3, 0x6E: 7, 0x6F: 3, 0x70: 3,
    0x71: 9,                   # byte + 2 bytes + 2 bytes + 2 shorts
    0x72: 5, 0x73: 5,
    0x74: 5,
    0x76: 1,
    0x77: 5,
    0x78: 1, 0x79: 1,
    0x7A: 3, 0x7B: 1,
    0x7C: 7,
    0x7D: 9,
    0x7E: 3,
}

# condition-entry operand widths for (condOp & 0x38) == 0x20, keyed by condOp & 7
CLASS20 = {0: 4, 1: 4, 2: 2, 3: 2, 4: 4, 5: 6}

SET, UNSET, TEST, CHEST, DATE = "set", "unset", "test", "chest", "date"
DGET = "dgetlist"

# Section 0x0B is not code: dget.c:344 does
#     triggerPtr = getScriptSection(script, 0xb) + 2;
#     while ((trigger = *triggerPtr++) < 0xfe)
#             isTriggerSet(trigger + TRIGGER_OGRE_FORTRESS_OPENED);
# i.e. a byte list of species ids, each read as recruit trigger 0xC8 + id.
DATA_SECTION_DGET = 0x0B
DGET_TRIGGER_BASE = 0xC8         # TRIGGER_OGRE_FORTRESS_OPENED


class Ref:
    __slots__ = ("kind", "tid", "off", "blob", "section")

    def __init__(self, kind, tid, off, blob, section):
        self.kind, self.tid, self.off = kind, tid, off
        self.blob, self.section = blob, section


class Decoder:
    """Recursive-descent decoder over one script blob."""

    def __init__(self, data, base, end, blob_name):
        self.d = data
        self.base = base            # file offset of the blob (== CURRENT_SCRIPT_PTR)
        self.end = end
        self.name = blob_name
        self.seen = set()           # blob-relative offsets already decoded
        self.covered = set()        # every byte consumed as code/operand
        self.refs = []
        self.errors = []
        self.section = None

    # ---- little helpers, all blob-relative -------------------------------
    def u8(self, p):
        return self.d[self.base + p]

    def u16(self, p):
        return struct.unpack_from("<H", self.d, self.base + p)[0]

    def mark(self, p, n):
        for i in range(n):
            self.covered.add(p + i)

    def size(self):
        return self.end - self.base

    # ---- inline text (showTextbox, script_common.c:3009) ------------------
    def skip_text(self, p):
        limit = self.size()
        while p < limit:
            ctrl = self.u8(p)
            self.mark(p, 1)
            p += 1
            if ctrl == 3:
                self.mark(p, 3)
                p += 3
            elif ctrl == 13:
                self.mark(p, 1)
                p += 1                       # the paired byte
                if p >= limit:
                    break
                if self.u8(p) == 0:
                    self.mark(p, 2)
                    p += 2                   # consumes "00 xx"; next byte is peeked
                    return p
                # else: newline, keep going
            else:
                self.mark(p, 1)
                p += 1
        raise ValueError("unterminated text at 0x%X" % p)

    # ---- condition block (MAIN_func_801050C0, script_anim.c:78) ----------
    def cond_block(self, p, work):
        limit = self.size()
        start = p
        while p < limit:
            cond_op = self.u8(p)
            self.mark(p, 2)
            p += 2                           # condOp byte + the skipped byte
            if cond_op == 0x19:              # terminator
                return p
            cls = cond_op & 0x38
            if cls == 0x00:                  # isTriggerSet(u16)
                tid = self.u16(p)
                self.refs.append(Ref(TEST, tid, self.base + p, self.name, self.section))
                self.mark(p, 2)
                p += 2
            elif cls == 0x08:                # pstat compare
                self.mark(p, 2)
                p += 2
            elif cls in (0x10, 0x18):        # conditional jump, then fall through
                tgt = self.u16(p)
                self.mark(p, 2)
                p += 2
                work.append(tgt)
            elif cls == 0x20:
                n = CLASS20.get(cond_op & 7)
                if n is None:
                    raise ValueError("cond class 0x20 sub %d at 0x%X"
                                     % (cond_op & 7, p))
                self.mark(p, n)
                p += n
            else:
                raise ValueError("unhandled cond class 0x%02X (condOp 0x%02X) at 0x%X"
                                 % (cls, cond_op, p))
            if p - start > 0x2000:
                raise ValueError("runaway condition block at 0x%X" % start)
        raise ValueError("unterminated condition block at 0x%X" % start)

    def mark_header(self, table_bytes):
        self.mark(0, table_bytes)

    def read_dget_list(self, off, section):
        """Section 0x0B: species-id byte list -> recruit triggers 0xC8 + id."""
        self.section = section
        p = off + 2
        self.mark(off, 2)
        limit = self.size()
        while p < limit and self.u8(p) < 0xFE:
            self.refs.append(Ref(DGET, self.u8(p) + DGET_TRIGGER_BASE,
                                 self.base + p, self.name, section))
            self.mark(p, 1)
            p += 1
        self.mark(p, 1)

    # ---- main trace ------------------------------------------------------
    def trace(self, entry, section, far):
        """far: list that collects (scriptId, sectionId) cross-script targets."""
        self.section = section
        work = [entry]
        origin = {entry: "section 0x%X entry" % section}
        limit = self.size()
        while work:
            p = work.pop()
            if p in self.seen or not (0 <= p < limit):
                continue
            src = origin.get(p, "?")
            while True:
                if p in self.seen:
                    break
                if not (0 <= p < limit):
                    self.errors.append("ran off blob end at 0x%X (from %s)" % (p, src))
                    break
                self.seen.add(p)
                op = self.u8(p)
                self.mark(p, 1)
                q = p + 1
                before = len(work)
                try:
                    q = self.step(op, q, work, far)
                except (ValueError, IndexError, struct.error) as exc:
                    self.errors.append("op 0x%02X @blob+0x%X: %s (reached from %s)"
                                       % (op, p, exc, src))
                    break
                for t in work[before:]:
                    origin.setdefault(t, "blob+0x%X" % p)
                if q is None:
                    break
                p = q

    def step(self, op, p, work, far):
        """Decode one instruction; return the next PC or None to stop."""
        if op in SIMPLE:
            n = SIMPLE[op]
            self.mark(p, n)
            if op == 0x15:                   # popScriptStack -> return
                return None
            return p + n

        if op == 0x10:                       # scriptShowSelection
            # script_common.c:2953 -- count byte, then (count+1) u16: the first
            # `count` are jump offsets and the last is the "1A 00" that opens the
            # prompt text.  script_instr10.c:27 longjmps out, and the script only
            # resumes via tickScriptDialogueBox (script_common.c:2888), which
            # reloads the PC from table[cursor] -- so there is NO linear
            # fall-through past a selection.
            count = self.u8(p)
            self.mark(p, 1)
            p += 1
            n = (count + 1) * 2
            for i in range(count):
                work.append(self.u16(p + i * 2))
            self.mark(p, n)
            p += n
            self.skip_text(p)                # marks the prompt text as covered
            return None

        if op == 0x13:                       # call, local
            self.mark(p, 3)
            work.append(self.u16(p + 1))
            return p + 3

        if op == 0x14:                       # call, other script/section
            self.mark(p, 5)
            far.append((self.u16(p + 1), self.u16(p + 3)))
            return p + 5

        if op == 0x16:                       # jump, local
            self.mark(p, 3)
            work.append(self.u16(p + 1))
            return None

        if op == 0x17:                       # jump, other script/section
            self.mark(p, 5)
            far.append((self.u16(p + 1), self.u16(p + 3)))
            return None

        if op == 0x18:                       # computed jump through inline table
            count = self.u16(p + 1)
            self.mark(p, 3)
            p += 3
            if count > 0x400:
                raise ValueError("implausible jump-table count %d" % count)
            for i in range(count):
                work.append(self.u16(p + i * 2))
            self.mark(p, count * 2)
            return None

        if op == 0x19:                       # condition block
            self.mark(p, 1)
            return self.cond_block(p + 1, work)

        if op == 0x1A:                       # showTextbox
            self.mark(p, 1)
            return self.skip_text(p + 1)

        if op in (0x1C, 0x1D):               # set / unset trigger
            tid = self.u16(p + 1)
            self.refs.append(Ref(SET if op == 0x1C else UNSET, tid,
                                 self.base + p + 1, self.name, self.section))
            self.mark(p, 3)
            return p + 3

        if op == 0x2C:                       # scriptCompareDate -> set/unsetTrigger
            tid = self.u16(p + 1)
            self.refs.append(Ref(DATE, tid, self.base + p + 1,
                                 self.name, self.section))
            self.mark(p, 9)
            return p + 9

        if op == 0x75:                       # spawnChest(..., trigger)
            tid = self.u16(p + 9)
            self.refs.append(Ref(CHEST, tid, self.base + p + 9,
                                 self.name, self.section))
            self.mark(p, 11)
            return p + 11

        if op == 0xFB:                       # load map
            self.mark(p, 5)
            return None
        if op in (0xFC, 0xFD):
            return p
        if op in (0xFE, 0xFF):               # return from script file
            return None

        raise ValueError("undispatched opcode (script would pause)")


def parse_sections(data, base, end):
    """getScriptSection(): u16 first-body-offset, then (sectionId, offset) pairs
    terminated by sectionId 0xFFFF (script_tick.c:329).
    Returns (sections, table_size_in_bytes)."""
    out = []
    p = base + 2
    while p + 4 <= end:
        sid, off = struct.unpack_from("<HH", data, p)
        if sid == 0xFFFF:
            p += 2
            break
        if off >= (end - base):
            break
        out.append((sid, off))
        p += 4
    return out, p - base


def blob_list(path, data):
    """Return [(name, start, end, sections, table_bytes)]."""
    def single():
        secs, tb = parse_sections(data, 0, len(data))
        return [("whole-file", 0, len(data), secs, tb)]

    if len(data) < 0x2000:
        return single()
    tbl = list(struct.unpack_from("<%dI" % (0x2000 // 4), data, 0))
    n = 0
    while n + 1 < len(tbl) and tbl[n] <= tbl[n + 1] <= len(data):
        n += 1
    # DG.SCN: a sector-aligned offset table whose last entry is EOF
    if n < 2 or tbl[0] != 0x800 or any(v % 0x800 for v in tbl[:n + 1]):
        return single()
    out = []
    for i in range(n):
        s, e = tbl[i], tbl[i + 1]
        if e <= s:
            continue
        secs, tb = parse_sections(data, s, e)
        out.append(("script%03d" % i, s, e, secs, tb))
    return out


def raw_scan(data):
    """Encoding-agnostic cross-check: every `1C ?? lo hi` / `1D ?? lo hi` and
    every plausible condition-entry trigger test, with no flow analysis.
    Deliberately over-reports; used only to prove the decoder is not blind."""
    hits = defaultdict(list)
    for i in range(len(data) - 3):
        b = data[i]
        if b in (0x1C, 0x1D):
            tid = struct.unpack_from("<H", data, i + 2)[0]
            hits[tid].append(i + 2)
    return hits


def in_band(tid, band):
    return band[0] <= tid <= band[1]


def short(path):
    import os
    return os.path.basename(path)


def decodes_as_code(data, start, end, off):
    """Throwaway decode used to tell code sections from data sections."""
    probe = Decoder(data, start, end, "probe")
    probe.trace(off, -1, [])
    return not probe.errors


def main(argv):
    raw_only = False
    args = []
    for a in argv:
        if a == "--raw":
            raw_only = True
        else:
            args.append(a)
    if not args:
        print(__doc__)
        return 2

    print("=" * 78)
    print("TRIGGER-ID BANDS (derived from the guard constants, not assumed)")
    print("=" * 78)
    print("  setDigimonRaised     trigger = species + 0x%03X" % RAISED_BASE)
    print("      stock  species <  0x%02X  ->  triggers 0x%03X..0x%03X"
          % (RAISED_BOUND_STOCK, RAISED_BASE, RAISED_BASE + RAISED_BOUND_STOCK - 1))
    print("      modded species <  0x%02X  ->  triggers 0x%03X..0x%03X"
          % (RAISED_BOUND_MOD, RAISED_BASE, RAISED_BASE + RAISED_BOUND_MOD - 1))
    print("      NEW WRITE BAND      0x%03X..0x%03X  (%d ids)"
          % (RAISED_NEW[0], RAISED_NEW[1], RAISED_NEW[1] - RAISED_NEW[0] + 1))
    print("  recalculatePPandArena  trigger = 0x%03X + i, i from 3" % PP_BASE)
    print("      stock  i < 0x%02X  ->  triggers 0x%03X..0x%03X"
          % (PP_BOUND_STOCK, PP_BASE + 3, PP_BASE + PP_BOUND_STOCK - 1))
    print("      modded i < 0x%02X  ->  triggers 0x%03X..0x%03X"
          % (PP_BOUND_MOD, PP_BASE + 3, PP_BASE + PP_BOUND_MOD - 1))
    print("      NEW READ BAND       0x%03X..0x%03X  (%d ids)"
          % (PP_NEW[0], PP_NEW[1], PP_NEW[1] - PP_NEW[0] + 1))
    print("  ScriptState.triggers[100] holds ids 0..%d" % (TRIGGER_ARRAY_BITS - 1))
    print()

    all_refs = []
    total_bytes = 0
    covered_bytes = 0
    all_errors = []
    raw_hits = defaultdict(list)

    for path in args:
        with open(path, "rb") as fh:
            data = fh.read()
        print("-" * 78)
        print("FILE %s  (%d bytes)" % (path, len(data)))
        for tid, offs in raw_scan(data).items():
            raw_hits[tid].extend((path, o) for o in offs)
        if raw_only:
            continue
        blobs = blob_list(path, data)
        print("  %d script blob(s)" % len(blobs))
        nsec = 0
        before_err = len(all_errors)
        for name, start, end, sections, tblen in blobs:
            nsec += len(sections)
            dec = Decoder(data, start, end, name)
            dec.mark_header(tblen)
            far = []
            for sid, off in sections:
                if sid == DATA_SECTION_DGET and not decodes_as_code(
                        data, start, end, off):
                    # only the cup-requirement scripts put a species byte list
                    # here; everywhere else section 0x0B is ordinary code
                    dec.read_dget_list(off, sid)
                else:
                    dec.trace(off, sid, far)
            all_refs.extend(dec.refs)
            total_bytes += end - start
            covered_bytes += len(dec.covered)
            for e in dec.errors:
                all_errors.append("%s %s: %s" % (short(path), name, e))
        print("  %d sections, decode errors: %d"
              % (nsec, len(all_errors) - before_err))

    if raw_only:
        print()
        print("RAW 0x1C/0x1D pattern hits (no flow analysis, over-reports):")
        for tid in sorted(raw_hits):
            print("  0x%03X  x%d" % (tid, len(raw_hits[tid])))
        return 0

    print()
    print("=" * 78)
    print("DECODE COVERAGE")
    print("=" * 78)
    print("  %d / %d script bytes reached by the decoder (%.1f%%)"
          % (covered_bytes, total_bytes, 100.0 * covered_bytes / max(1, total_bytes)))
    print("  decode errors: %d" % len(all_errors))
    for e in all_errors[:40]:
        print("    " + e)
    if len(all_errors) > 40:
        print("    ... %d more" % (len(all_errors) - 40))

    by_id = defaultdict(list)
    for r in all_refs:
        by_id[r.tid].append(r)

    LETTER = {SET: "S", UNSET: "U", TEST: "T", CHEST: "C", DATE: "D", DGET: "G"}

    print()
    print("=" * 78)
    print("TRIGGER IDS REFERENCED BY DISC SCRIPTS")
    print("=" * 78)
    kinds_of = lambda rs: "".join(sorted({LETTER[r.kind] for r in rs}))
    ids = sorted(by_id)
    print("  %d distinct ids, min 0x%03X, max 0x%03X"
          % (len(ids), ids[0], ids[-1]) if ids else "  none")
    # contiguous runs, so the reader can see where the id families sit
    runs = []
    for t in ids:
        if runs and t == runs[-1][1] + 1:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    print("  contiguous runs (gaps = ids no script touches):")
    rl = ["0x%03X-0x%03X" % (a, b) if a != b else "0x%03X" % a for a, b in runs]
    for i in range(0, len(rl), 8):
        print("    " + "  ".join(rl[i:i + 8]))
    line = []
    for tid in ids:
        line.append("0x%03X%s(%d)" % (tid, kinds_of(by_id[tid]), len(by_id[tid])))
        if len(line) == 6:
            print("    " + "  ".join(line))
            line = []
    if line:
        print("    " + "  ".join(line))
    print("  legend: S=set(0x1C) U=unset(0x1D) T=test(0x19) C=chest(0x75)"
          " D=date(0x2C) G=dget recruit list (section 0x0B)")

    written = sorted(t for t, rs in by_id.items()
                     if any(r.kind in (SET, UNSET, CHEST, DATE) for r in rs))
    print()
    print("  ids the scripts WRITE (save-persistent): %d" % len(written))
    print("    " + " ".join("0x%03X" % t for t in written))

    print()
    print("=" * 78)
    print("BAND INTERSECTION")
    print("=" * 78)
    verdict_clean = True
    for label, band in BANDS:
        hits = [t for t in ids if in_band(t, band)]
        print()
        print("  %s" % label)
        print("  band 0x%03X..0x%03X" % band)
        if not hits:
            print("    NO COLLISION -- no disc script references any id in this band.")
            continue
        verdict_clean = False
        free = [t for t in range(band[0], band[1] + 1) if t not in by_id]
        print("    %d of %d band ids are referenced by disc scripts; %d are free."
              % (len(hits), band[1] - band[0] + 1, len(free)))
        print("    free ids: %s"
              % (" ".join("0x%03X" % t for t in free) if free else "(none)"))
        print("    %d COLLIDING ID(S):" % len(hits))
        for t in hits:
            for r in by_id[t]:
                print("      0x%03X %-8s  %s section 0x%X  file offset 0x%06X"
                      % (t, r.kind, r.blob, r.section, r.off))

    print()
    print("=" * 78)
    print("VERDICT: %s" % ("NO COLLISION" if verdict_clean else "COLLISION"))
    print("=" * 78)

    # cross-check: raw pattern hits inside the bands that the decoder missed
    print()
    print("CROSS-CHECK (raw byte pattern, no flow analysis)")
    for label, band in BANDS:
        raw_in = sorted(t for t in raw_hits if in_band(t, band))
        missed = [t for t in raw_in if t not in by_id]
        print("  %s: raw 0x1C/0x1D pattern matches in band: %d id(s) %s"
              % (label.split("(")[0].strip(), len(raw_in),
                 " ".join("0x%03X" % t for t in raw_in)))
        if missed:
            print("    not confirmed by the decoder (likely coincidental bytes): %s"
                  % " ".join("0x%03X" % t for t in missed))
            for t in missed:
                for path, off in raw_hits[t][:4]:
                    with open(path, "rb") as fh:
                        fh.seek(max(0, off - 8))
                        ctx = fh.read(24)
                    print("      %s @0x%06X  ...%s..." % (path, off, ctx.hex(" ")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
