"""Drive the running game on a field map over the debug harness (tools/dwgame.Game).

Lifted from verify_newmap.py's closures so a second map's test can share them. A map's own
values (spec, rows, boss, host entrance) are arguments; nothing here knows the glade.
Verdicts go through the `check(cond, msg)` the caller passes in.
"""
import json
import os
import shutil
import struct
import time

UP, RIGHT, DOWN, LEFT = 0x0010, 0x0020, 0x0040, 0x0080
TAMER, NPC0 = 0x8015576C, 0x8012F344 + 8          # ENTITY_TABLE[2]: first map NPC
CURRENT_SCREEN, GAME_STATE, IS_SCRIPT_PAUSED = 0x80134DA8, 0x80134F0A, 0x80134FF4
TRI_STATE, MENU_CURSOR, CURRENT_MENU, YES_NO = 0x80134D30, 0x80123E8C, 0x8013504C, 0x801317D8
GENERAL_BUFFER, MAP_TILE_DATA, MAP_COLLISION = 0x80010000, 0x8014F084, 0x801AF398
GS_VIEWPOINT, VIEWPORT_DISTANCE, HOUR, CURRENT_FRAME = 0x80185BA8, 0x80134ED4, 0x80134EBC, 0x80134F08
CAMERA = MAP_TILE_DATA + 7092                     # cameraX, cameraY (i16), width, height (i8)
MAP_OBJECT_INSTANCE_COUNT = 0x80134D1C            # map objects (foreground sprites) loadMapObjects set up
PREVIOUS_SCREEN, CURRENT_EXIT = 0x80134DA9, 0x80134DAA
NPC_ANIM, NPC_ON_MAP, NPC_STATS = 0x80155828 + 0x2E, 0x80155828 + 0x34, 0x80155828 + 0x38   # NPC_ENTITIES[0]: animId, isOnMap, off ...
PARTNER_STATS, PARTNER_MAX_HP, MONEY = 0x801557E0, 0x801557F0, 0x80134EB8    # off, def, speed, brain; max HP, MP, current HP, MP
PARTNER_ANIM, WORLD_POOP = 0x801557A8 + 0x2E, 0x80138504
CURRENT_COMMAND, HOVERED_COMMAND = 0x8013D640 + 0x64E, 0x8013D640 + 0x652   # COMBAT_DATA.player.*[0]
BATTLE = (1, 2, 3)                                # GAME_STATE: 3 loading, 2 starting or ending, 1 fighting
END_BUFFER, END_LINES = 0x80073EA0, 0x801350B0        # the box's text; lines expected, then rows drawn
MP_BONUS = {700: 5, 800: 10, 900: 15, 999: 20}        # Brains reached: the percent its message names
PSTAT_BASE, TRIGGER_BASE = 0x801BE031, 0x801BDFCD     # docs/GAME.md 4.7, 4.8
NPC_BASE, NPC_SIZE = 0x80155828, 0x68                 # NPC_ENTITIES[8]: type i32 +0, isOnMap +0x34, currentHP +0x4C, scriptId +0x65
NPC_MODELS = 0x80137140                               # 5 x 0x1C: useCount, modelPtr, animTablePtr, mmdPtr, .., digiType i16 +0x18
ENEMY_IDS = 0x8013D640 + 0x66C                        # COMBAT_DATA.player.entityIds[4]: 1 (partner), then enemy entity ids
ENEMY_COUNT = 0x80134D6C                              # i16: how many of them are this battle's (the rest are stale)
PARTNER_LIVES = 0x801557A8 + 0x7C                     # PartnerEntity.lives, i8
INVENTORY = 0x8013D474                                # types[30], amounts[30], names[30], size u8 (docs/GAME.md 6.2)
SLIVER_NCC = 0.9      # plan 1 measured 0.991 and 1.000 for a served map's edges; a black or missing edge is far below
SPECIES, PARTNER = 0x8012CEB4, 0x801557A8             # DIGIMON_DATA (52 B each: radius, height i16 at +24); PARTNER_ENTITY
TAMER_SIZE = (100, 350)                               # the tamer's radius (docs/GAME.md 3.9) and a height over his head


class Field:
    def __init__(self, g, check, shot=None):
        self.g, self.check = g, check
        self.shot = shot or (lambda name: None)       # shot(name) -> screenshot path, for the caller's own folder

    def enter(self):
        """Title -> CONTINUE. DOWN waits for the menu to take input: an early DOWN is dropped and the crosses then
        start a NEW GAME. The intro gives a partner while still on the opening screen (238) with its script running,
        so "in control" also needs the script idle (the field menu's own test) for 2 s."""
        g = self.g
        free = lambda: (g.partner_type and g.tamer_state == 0 and self.gstate() == 0
                        and self.screen() != 238 and self.i32(IS_SCRIPT_PAUSED) == 1)
        for _ in range(12):
            g.route("6:start,150:none")
        g.advance(120)
        g.route("6:down,90:none")
        for i in range(600):
            g.route("6:cross,30:none")
            if free() and i % 5 == 0:
                g.advance(120)
                if free():
                    return
        raise RuntimeError("never reached the world from CONTINUE GAME")

    def i32(self, addr):
        return struct.unpack("<i", self.g.d.read(addr, 4))[0]

    def gstate(self):
        return struct.unpack("<b", self.g.d.read(GAME_STATE, 1))[0]

    def screen(self):
        return self.g.d.read(CURRENT_SCREEN, 1)[0]

    def pstat(self, n):
        return self.g.d.read(PSTAT_BASE + n, 1)[0]

    def trigger(self, n):
        return bool(self.g.d.read(TRIGGER_BASE + n // 8, 1)[0] >> (n % 8) & 1)

    def set_trigger(self, n):
        b = self.g.d.read(TRIGGER_BASE + n // 8, 1)[0]
        self.g.d.write_bytes(bytes([b | 1 << (n % 8)]), TRIGGER_BASE + n // 8)

    def npc(self, slot):
        """Map NPC slot `slot` as the game holds it, or None when no entity is placed there (ENTITY_TABLE[slot + 2])."""
        if struct.unpack("<I", self.g.d.read(NPC0 + 4 * slot, 4))[0] == 0:
            return None
        b = self.g.d.read(NPC_BASE + NPC_SIZE * slot, NPC_SIZE)
        return dict(type=struct.unpack_from("<i", b)[0], on_map=b[0x34], hp=struct.unpack_from("<h", b, 0x4C)[0],
                    script=b[0x65], pos=self.pos(NPC_BASE + NPC_SIZE * slot))

    def model(self, species):
        """The VRAM index k (modelId, 0..4) of the NPC model holding `species` with its MMD and model loaded, or None
        (docs/GAME.md 5.5: k 4 overlaps SBOY.TIM)."""
        for e in range(5):
            b = self.g.d.read(NPC_MODELS + 0x1C * e, 0x1C)
            use, mdl, _, mmd = struct.unpack_from("<4I", b)
            k, kind = struct.unpack_from("<hh", b, 0x16)
            if use and mdl and mmd and kind == species:
                return k
        return None

    def enemies(self):
        """The battle's enemies as map NPC slots (COMBAT_DATA.player.entityIds after the partner's 1)."""
        n = struct.unpack("<h", self.g.d.read(ENEMY_COUNT, 2))[0]
        return [e - 2 for e in self.g.d.read(ENEMY_IDS, 4)[1:1 + n]]

    def bag(self):
        """The inventory's 0x5B bytes: item types, amounts and name slots (30 each, 0xFF free), then the bag's size."""
        return self.g.d.read(INVENTORY, 0x5B)

    def set_bag(self, items):
        """The bag holds exactly `items`, [(type, amount)], in its first slots, the rest free as giveItem leaves them."""
        assert len(items) <= self.bag()[90]
        t, a, n = [0xFF] * 30, [0] * 30, [0xFF] * 30
        for i, (ty, am) in enumerate(items):
            t[i], a[i], n[i] = ty, am, i
        self.g.d.write_bytes(bytes(t + a + n), INVENTORY)

    def win_group(self, slots, bits, row):
        """A maxed partner; every enemy in `slots` on 1 HP until it falls. The fight ends on this map with the bits of
        all of them paid, and each has left the map (its run-off)."""
        g, check, gstate = self.g, self.check, self.gstate
        self.partner(999, 9999)
        money, done = self.i32(MONEY), False
        end = time.time() + 240
        while time.time() < end and not (done and gstate() == 0 and g.tamer_state == 0 and self.i32(IS_SCRIPT_PAUSED) == 1):
            if gstate() not in BATTLE:
                done = True
            else:
                for s in slots:
                    if struct.unpack("<h", g.d.read(NPC_BASE + NPC_SIZE * s + 0x4C, 2))[0] > 1:
                        g.d.write_bytes(struct.pack("<h", 1), NPC_BASE + NPC_SIZE * s + 0x4C)
            g.route("4:cross,20:none")
        check(done and gstate() == 0 and g.tamer_state == 0 and self.screen() == row,
              "won: the run-off ended, player in control, still on map %d" % row)
        check(self.i32(MONEY) - money == bits, "the bits were paid: %d -> %d (%d expected)" % (money, self.i32(MONEY), bits))
        left = [s for s in slots if self.npc(s) and self.npc(s)["on_map"]]
        check(not left, "every enemy has left the map (still on it: slots %s)" % left)
        g.advance(30)
        self.shot("won_%s" % "_".join(map(str, slots)))

    def pad(self, mask):
        self.g.d.cmd("set_input", layer="host", buttons="0x%04X" % (0xFFFF & ~mask))

    def pos(self, entity=TAMER):
        ptr = struct.unpack("<I", self.g.d.read(entity + 4, 4))[0]
        if not 0x80000000 <= ptr < 0x80200000:
            return None
        return struct.unpack("<3i", self.g.d.read(ptr + 120, 12))

    def walk(self, mask, seconds):
        """Counted in game frames, not wall time: a busy machine must not stretch the step."""
        self.g.route("%d:%s,4:none" % (seconds * 60, {UP: "up", RIGHT: "right", DOWN: "down", LEFT: "left"}[mask]), poll_s=0.02)

    def wait_for(self, pred, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.2)
        return False

    def calibrate(self):
        """Pad direction -> world (dx, dz) under this screen's camera."""
        vec = {}
        for d in (RIGHT, LEFT, UP, DOWN):
            p = self.pos(TAMER); self.walk(d, 0.35); time.sleep(0.15); q = self.pos(TAMER)
            vec[d] = (q[0] - p[0], q[2] - p[2])
        return vec

    def go(self, target, until, limit=90, vec=None):
        """Greedy walk toward world (x, z); tries the next-best direction at a wall."""
        g = self.g
        vec = vec or self.calibrate()
        for _ in range(limit):
            if until():
                return True
            p = self.pos(TAMER)
            if p is None or g.tamer_state != 0 or self.gstate() != 0:
                g.route("4:cross,20:none")        # talk or fight through whatever stopped us
                time.sleep(0.3)                   # (the spike's Piddomon respawns on entry)
                continue
            want = (target[0] - p[0], target[1] - p[2])
            order = sorted(vec, key=lambda d: -(vec[d][0] * want[0] + vec[d][1] * want[1]))[:3]
            for d in order:
                # 0.3 s runs about 300 units, wider than the targets: slow down to land on one
                need = 0.35 * (vec[d][0] * want[0] + vec[d][1] * want[1]) / max(1, vec[d][0] ** 2 + vec[d][1] ** 2)
                self.walk(d, min(0.3, max(0.1, need)) if d == order[0] else 0.3)
                q = self.pos(TAMER)
                if q is None or until() or abs(q[0] - p[0]) + abs(q[2] - p[2]) > 20:
                    break
        return until()

    def near(self, x, z, r=120):
        p = self.pos(TAMER)
        return p is not None and abs(p[0] - x) < r and abs(p[2] - z) < r

    def battle(self, row=239):
        """The spike's Piddomon in NPC slot 0: walk into it and fight through to the map."""
        g, check = self.g, self.check
        npc = struct.unpack("<I", g.d.read(NPC0, 4))[0]
        check(0x80000000 <= npc < 0x80200000, "Piddomon spawned in NPC slot 0")
        e = self.pos(npc)
        self.go((e[0], e[2]), lambda: g.tamer_state != 0 or self.gstate() != 0, limit=30)
        seen, end = set(), time.time() + 120
        while time.time() < end:
            s = self.gstate()
            if s == 3 and 3 not in seen:
                self.shot("battle")
            seen.add(s)
            if s == 0 and 3 in seen and g.tamer_state == 0:
                break
            g.route("4:cross,20:none")
            time.sleep(0.3)
        check(3 in seen, "battle started on map %d (game states %s)" % (row, sorted(seen)))
        check(self.gstate() == 0 and g.tamer_state == 0 and self.screen() == row,
              "battle over, player in control, still on map %d" % row)

    def loaded_files(self, spec, assets, name="glade"):
        """Memory and VRAM say the game runs on OUR map and tile files (<assets>/<name>.tfs, .map)."""
        g, check = self.g, self.check
        tfs = open(os.path.join(assets, name + ".tfs"), "rb").read()
        mapf = open(os.path.join(assets, name + ".map"), "rb").read()
        used = 8 + 512 * struct.unpack_from("<i", tfs, 4)[0] + 16388 * spec["camera"]["gw"] * spec["camera"]["gh"]
        ram = b"".join(g.d.read(GENERAL_BUFFER + o, min(0x4000, used - o)) for o in range(0, used, 0x4000))
        check(ram == tfs[:used], "our TFS is the one resident in GENERAL_BUFFER (%d bytes)" % used)
        nw = struct.unpack_from("<i", mapf, 0)[0] // 4
        coll = struct.unpack_from("<i", mapf, 4 * (nw - 1))[0]        # the last header word
        check(g.d.read(MAP_COLLISION, 10000) == mapf[coll:coll + 10000], "our collision grid is live")
        if "fg" in spec:                                       # foreground sprites: objects and their image
            n = struct.unpack("<h", g.d.read(MAP_OBJECT_INSTANCE_COUNT, 2))[0]
            check(n == spec["fg"]["count"], "%d foreground sprites loaded as map objects" % n)
            tim = struct.unpack_from("<i", mapf, 4)[0]          # first image: TIM, clut block, pixel block
            pix = tim + 8 + struct.unpack_from("<I", mapf, tim + 8)[0]
            px, py, pw, ph = struct.unpack_from("<4H", mapf, pix + 4)
            row = max(range(ph), key=lambda r: sum(mapf[pix + 12 + 256 * r:pix + 12 + 256 * (r + 1)]))
            hexrow = g.d.cmd("vram_peek", x=px, y=py + row, w=pw, h=1)["hex"]      # one halfword per 4 digits
            live = b"".join(bytes.fromhex(hexrow[i:i + 4])[::-1] for i in range(0, len(hexrow), 4))
            check(live == mapf[pix + 12 + 256 * row:pix + 12 + 256 * (row + 1)],
                  "our sprite texels are in VRAM at (%d,%d)" % (px, py + row))
        vp = struct.unpack("<6i", g.d.read(GS_VIEWPOINT, 24))
        cam = spec["camera"]
        check(list(vp) == [int(v) // 2 * 2 for v in cam["vp"] + cam["vr"]]
              and self.i32(VIEWPORT_DISTANCE) == cam["h"], "our camera is live (GS_VIEWPOINT %s)" % (vp,))
        w, h = g.d.read(CAMERA + 4, 2)
        check((w, h) == (cam["gw"], cam["gh"]), "tile grid %dx%d" % (w, h))
        hexrow = "".join(g.d.cmd("vram_peek", x=x, y=480, w=128, h=1)["hex"] for x in (0, 128))
        live = [int(hexrow[i:i + 4], 16) for i in range(0, 1024, 4)]
        clut0 = list(struct.unpack_from("<256H", tfs, 8))
        check(live == clut0, "our day palette is the live CLUT (VRAM row 480)")

    def figures(self):
        """Boxes (x0, y0, x1, y1) in the 4:3 view's pixels round the tamer, the partner and every NPC on the map: each
        one's radius-by-height cylinder (DIGIMON_DATA) projected through the live camera (docs/GAME.md 3.4)."""
        import numpy as np
        g = self.g
        cam = np.array(struct.unpack("<6i", g.d.read(GS_VIEWPOINT, 24)), float)
        vp, h = cam[:3], self.i32(VIEWPORT_DISTANCE)
        cx, cy = struct.unpack("<2h", g.d.read(CAMERA, 4))
        W, H = g.d.read(CAMERA + 4, 2)
        f = (cam[3:] - vp) / np.linalg.norm(cam[3:] - vp)
        r = np.array([f[2], 0.0, -f[0]]) / np.hypot(f[0], f[2])
        d = np.cross(f, r)
        size = lambda species: struct.unpack("<2h", g.d.read(SPECIES + 52 * species + 24, 4))
        who = [(self.pos(TAMER), TAMER_SIZE), (self.pos(PARTNER), size(self.i32(PARTNER)))]
        who += [(n["pos"], size(n["type"])) for n in map(self.npc, range(8)) if n and n["on_map"] and n["pos"]]
        boxes = []
        for p, (rad, ht) in who:
            if p is None:
                continue
            q = np.array([(p[0] + dx, p[1] + y, p[2] + dz) for dx in (-rad, rad) for dz in (-rad, rad)
                          for y in (0, -ht)], float) - vp
            u, v = 64 * W + h * (q @ r) / (q @ f) - cx, 64 * H + h * (q @ d) / (q @ f) - cy
            boxes.append((u.min(), v.min(), u.max(), v.max()))
        return boxes

    def slivers(self, name, backdrop, what):
        """At 21:9, the edges past the 4:3 view are our backdrop. Takes screenshot `name`. The middle of the screenshot
        (drawn by the stock tile ring) finds where the view sits on the backdrop; then each outer fifth must match the
        backdrop there (NCC at least SLIVER_NCC) and be lit. Characters may stand in the edges (that is what the wide
        view is for), so the pixels of every figure's box, read just before and after the shot, are left out; at
        least a third of each edge must remain. dw_ws_sliver.c draws those edges from a disc copy of the TFS, which
        for a served map it reads by sector: black or missing edges fail. Rows 80-195 keep the clock and the bars out."""
        import cv2
        import numpy as np
        from PIL import Image
        boxes = self.figures()
        shot = self.shot(name)
        boxes += self.figures()
        img = Image.open(shot).convert("L")
        img = np.asarray(img.resize((round(img.width * 240 / img.height), 240), Image.BOX), np.float32)
        ref = np.asarray(Image.open(backdrop).convert("L"), np.float32)
        w = img.shape[1]
        self.check(w * 3 >= 240 * 7 - 3, "%s: the screenshot is 21:9 (%d x 240)" % (what, w))
        mid = img[80:196, w * 3 // 10:w * 7 // 10]
        found = cv2.matchTemplate(ref, mid, cv2.TM_CCOEFF_NORMED)
        _, best, _, (x, y) = cv2.minMaxLoc(found)
        x0, y0 = x - w * 3 // 10, y - 80                     # the view's top-left on the backdrop
        self.check(x0 >= 0 and x0 + w <= ref.shape[1],     # characters stand in the middle: its match is only a locator
                   "%s: the 4:3 middle sits on the backdrop at (%d, %d), NCC %.3f" % (what, x0, y0, best))
        keep = np.ones(img.shape, bool)
        for bx0, by0, bx1, by1 in boxes:                   # 4 px for anti-aliasing and a step between the reads
            sx = (w - 320) / 2                             # the 4:3 view sits in the middle
            keep[max(0, int(by0) - 4):max(0, int(by1) + 5), max(0, int(bx0 + sx) - 4):max(0, int(bx1 + sx) + 5)] = False
        for side, sl in (("left", slice(0, w // 5)), ("right", slice(w * 4 // 5, w))):
            k = keep[80:196, sl]
            a = img[80:196, sl][k]
            b = ref[y0 + 80:y0 + 196, max(x0, 0):max(x0, 0) + w][:, sl]
            ncc, lit = 0.0, a.mean() if a.size else 0.0
            if b.shape == k.shape and a.size * 3 >= k.size:
                aa, bb = a - a.mean(), b[k] - b[k].mean()
                ncc = float((aa * bb).sum() / (np.sqrt((aa * aa).sum() * (bb * bb).sum()) or 1))
            self.check(lit > 20 and ncc >= SLIVER_NCC, "%s: the 21:9 %s edge is our backdrop: NCC %.3f, brightness %.0f, "
                       "%d%% left out for figures" % (what, side, ncc, lit, 100 - 100 * a.size // k.size))

    def keep_card(self, name, cards, out):
        """A copy of the card as this save left it, for the mod-off runs."""
        shutil.rmtree(os.path.join(out, "cards_" + name), ignore_errors=True)
        shutil.copytree(cards, os.path.join(out, "cards_" + name))
        shutil.copy(os.path.join(out, "saved_pos.json"), os.path.join(out, "saved_pos_%s.json" % name))

    def talk(self, vec, boss_at, centre):
        """Up the path to the boss at world (x, ?, z), via `centre`. Standing there starts nothing; pressing confirm does."""
        g, check = self.g, self.check
        x, z = boss_at[0], boss_at[2]
        self.go(centre, lambda: self.near(*centre), limit=60, vec=vec)
        check(self.go((x, z - 700), lambda: self.near(x, z - 700, r=200), limit=40, vec=vec),
              "up the path, below the boss %s" % (self.pos(TAMER),))
        north = max(vec, key=lambda d: vec[d][1])              # walk at it until it stops us: it is wide
        self.pad(north)
        last = None
        for _ in range(30):
            time.sleep(0.15)
            if self.pos(TAMER) == last:
                break
            last = self.pos(TAMER)
        self.pad(0)
        time.sleep(3)
        check(self.gstate() == 0 and g.tamer_state == 0 and self.i32(IS_SCRIPT_PAUSED) == 1,
              "next to it at %s, standing still starts nothing" % (last,))
        self.shot("boss")
        g.route("6:cross,20:none")
        time.sleep(1)
        check(self.gstate() == 0 and self.i32(IS_SCRIPT_PAUSED) != 1, "confirm opens its dialogue (the game is still on the map)")
        self.shot("talk")
        end = time.time() + 30
        while time.time() < end and self.gstate() not in BATTLE:
            g.route("4:cross,20:none")
        check(self.gstate() in BATTLE and g.d.read(0x80134D7C, 1)[0] == 0, "the battle starts, with fleeing allowed")
        # A battle goes 3 loading, 2 intro, 1 fighting, and its starting stats are written at the intro.
        # A brief 1 can show before the loading begins, and the last battle's stats stay in memory.
        check(self.wait_for(lambda: self.gstate() == 2, 30) and self.wait_for(lambda: self.gstate() == 1, 30), "the fight is on")
        self.shot("battle")

    def partner(self, stat, hp):
        self.g.d.write_bytes(struct.pack("<4h", stat, stat, stat, stat), PARTNER_STATS)
        self.g.d.write_bytes(struct.pack("<4h", hp, 9999, hp, 9999), PARTNER_MAX_HP)

    def win(self, rec, row, brains=0, end_fix=True):
        """A maxed partner. First the boss (stat record `rec`) fights for real until it has used every move (a move
        plays the animation of its slot number), both sides topped up, then on 1 HP it takes a hit and runs off.
        `brains`: the Brains the battle should have started on (judged, and its MP message after the win)."""
        g, check, gstate = self.g, self.check, self.gstate
        if brains:
            got = struct.unpack("<h", g.d.read(0x8013D610 + 10, 2))[0]
            check(got == brains, "the battle started on Brains %d (read %d, partner now %d)"
                  % (brains, got, struct.unpack("<h", g.d.read(PARTNER_STATS + 6, 2))[0]))
        self.partner(999, 9999)
        moves, seen = set(rec[22:26]), {}
        t0 = time.time()
        while time.time() - t0 < 240 and gstate() == 1 and not moves <= set(seen):
            m = g.d.read(NPC_ANIM, 1)[0]
            if m in moves and m not in seen:
                seen[m] = round(time.time() - t0)
                self.shot("move%d" % m)
            g.d.write_bytes(struct.pack("<hh", rec[11], rec[12]), NPC_STATS + 0x14)
            g.d.write_bytes(struct.pack("<hh", 9999, 9999), PARTNER_MAX_HP + 4)
            if time.time() - t0 > 12 and "menu" not in seen:
                seen["menu"] = 0
                self.shot("battle_ui")
            time.sleep(0.05)
        seen.pop("menu", None)
        check(moves <= set(seen) and gstate() == 1, "the boss used all four moves %s (move: seconds in)" % seen)
        bits, hit, box = self.i32(MONEY), False, None
        end = time.time() + 240
        while time.time() < end and not (hit and gstate() == 0 and g.tamer_state == 0 and self.i32(IS_SCRIPT_PAUSED) == 1):
            if gstate() not in BATTLE:
                hit = True
            elif struct.unpack("<h", g.d.read(NPC_STATS + 0x14, 2))[0] > 1:
                g.d.write_bytes(struct.pack("<h", 1), NPC_STATS + 0x14)
            lines, rows = struct.unpack("<hh", g.d.read(END_LINES, 4))
            if box is None and lines and rows == lines:  # the last row is up, for 60 frames or the next press
                box = g.d.read(END_BUFFER, 200).split(b"\0")[0]
                self.shot("end_box")
                print("MEASURE end box, %d rows: %r" % (rows, box), flush=True)
            g.route("4:cross,20:none")
        check(hit and gstate() == 0 and g.tamer_state == 0 and self.screen() == row,
              "won: the run-off ended, player in control, still on map %d" % row)
        if end_fix and brains and brains + 1 in MP_BONUS:
            text = b"#C1MP Cost Bonus!#Rreduced by %d pct!#W" % MP_BONUS[brains + 1]
            check(box is not None and text in box, "the end box finished on its MP bonus message")
        check(self.i32(MONEY) - bits == rec[19], "the bits were paid: %d -> %d" % (bits, self.i32(MONEY)))
        check(g.d.read(NPC_ON_MAP, 1)[0] == 0, "the boss has left the map")
        g.advance(30)
        self.shot("won")

    def measure(self, stats, heal, rec, slot, host, enter):
        """A real fight that judges nothing (docs/GAME.md 7.18). At `host` (row, exit) the partner evolves to
        MetalGreymon and takes `stats` ("hp,mp,off,def,speed,brains"); `enter()` then walks up to the fighter in NPC
        slot `slot` (its map record `rec`) and starts the battle. Nothing is poked but `heal`: up to that many +5000 HP
        when the partner falls below 2500. Presses only the finisher button and prints MEASURE lines."""
        g, check, wait_for, gstate, i32 = self.g, self.check, self.wait_for, self.gstate, self.i32
        hp, mp_, off, de, spd, brain = (int(v) for v in stats.split(","))
        g.warp(*host)
        for field, value in (("remaining_lifetime", 9999), ("poop_level", 9999), ("tiredness", 0), ("script_sync_bit", 1),
                             ("evolution_target", 12)):      # MetalGreymon
            g.set(field, value)
        g.set_atomic(("tamer_state", 6), ("tamer_substate", 0))
        g.set_atomic(("partner_state", 13), ("partner_sub_state", 0))
        check(wait_for(lambda: g.partner_type == 12 and g.evolution_target == -1
                       and (g.partner_state == 1 or g.tamer_state == 20), 240), "the partner evolved")
        for _ in range(12):
            if g.tamer_state == 0 and g.partner_state == 1:
                break
            g.route("4:cross,45:none")
        g.settle()
        wait_for(lambda: g.tamer_state == 0 and i32(IS_SCRIPT_PAUSED) == 1, 30)
        g.advance(120)                                 # the evolution's last box has closed
        g.d.write_bytes(struct.pack("<4h", off, de, spd, brain), PARTNER_STATS)
        # Buster Dive, Pulse Laser, Metal Sprinter, and its finisher: animation 46 + the slot in its move list
        g.d.write_bytes(bytes([34, 33, 33, 0, 50, 54, 56, 60]), PARTNER_STATS + 8)
        g.d.write_bytes(struct.pack("<4h", hp, mp_, hp, mp_), PARTNER_MAX_HP)
        enter()
        row = self.screen()
        npc_hp, npc_mp, npc_anim = (NPC_BASE + NPC_SIZE * slot + o for o in (0x4C, 0x4E, 0x2E))
        h = lambda addr: struct.unpack("<h", g.d.read(addr, 2))[0]
        picks, ours, last, last_p, dry, printed, boss_hp, partner_hp = {}, 0, 0, 0, None, 0, rec[11], hp
        heals = 0
        t0 = time.time()
        while time.time() - t0 < 600 and gstate() == 1:
            anim, t = g.d.read(npc_anim, 1)[0], time.time() - t0
            if anim in rec[22:26] and anim != last:
                picks[anim] = picks.get(anim, 0) + 1
            last = anim
            mine = g.d.read(PARTNER_ANIM, 1)[0]
            ours += mine == 60 and last_p != 60          # our finisher
            last_p = mine
            now = h(npc_hp), h(PARTNER_MAX_HP + 4)
            if gstate() == 1:                            # still fighting: the game restores them at the end
                boss_hp, partner_hp = now
                if heals < heal and 0 < partner_hp < 2500:
                    g.d.write_bytes(struct.pack("<h", min(hp, partner_hp + 5000)), PARTNER_MAX_HP + 4)
                    heals += 1
            if dry is None and h(npc_mp) < 96:
                dry = round(t)
            if t - printed >= 15:
                printed = t
                print("  %3ds: boss HP %d MP %d, partner HP %d MP %d, picks %s"
                      % (t, h(npc_hp), h(npc_mp), h(PARTNER_MAX_HP + 4), h(PARTNER_MAX_HP + 6), picks), flush=True)
            g.route("2:square,2:none", poll_s=0.02)   # the finisher, whenever its meter is full
        secs = round(time.time() - t0)
        self.shot("measure_end")
        print("MEASURE fight: partner %s: %ds, boss HP left %d, partner HP left %d, heals %d, boss picks %s, our finishers %d, MP ran out at %s"
              % (stats, secs, boss_hp, partner_hp, heals, picks, ours, dry), flush=True)
        end = time.time() + 300
        while time.time() < end and not (gstate() == 0 and g.tamer_state == 0 and i32(IS_SCRIPT_PAUSED) == 1):
            g.route("4:cross,20:none")
        over = gstate() == 0 and g.tamer_state == 0
        print("MEASURE outcome: %s (map %d)" % ("unfinished" if not over else "won" if self.screen() == row else "lost",
                                                self.screen()), flush=True)

    def flee(self, host_row, host_at):
        """Back to the host's side of the entrance, as any fled battle goes back where it came from."""
        g, check = self.g, self.check
        self.partner(999, 9999)
        end = time.time() + 30
        while time.time() < end and g.d.read(HOVERED_COMMAND, 1)[0] != 1:
            g.route("6:left,12:none")
        check(g.d.read(HOVERED_COMMAND, 1)[0] == 1, "cursor on Flee")
        end = time.time() + 30
        while time.time() < end and g.d.read(CURRENT_COMMAND, 1)[0] != 1:
            g.route("6:cross,12:none")
        check(g.d.read(CURRENT_COMMAND, 1)[0] == 1, "the tamer runs")
        arrived = lambda: (self.screen() == host_row and self.gstate() == 0 and g.tamer_state == 0
                           and self.i32(IS_SCRIPT_PAUSED) == 1)
        ok = self.wait_for(arrived, 120)
        g.advance(120)                                 # the arrival settles
        check(ok and arrived() and self.near(*host_at, r=400),
              "fled to the entrance on map %d %s (arrived in time %s; now tamer %d, game %d, script idle %s)"
              % (self.screen(), self.pos(TAMER), ok, g.tamer_state, self.gstate(), self.i32(IS_SCRIPT_PAUSED) == 1))
        self.shot("fled")

    def lose(self, row):
        """A feeble partner on 1 HP. Jijimon's home, as every lost battle."""
        g, check, gstate = self.g, self.check, self.gstate
        self.partner(1, 1)
        free = lambda: gstate() == 0 and g.tamer_state == 0 and self.i32(IS_SCRIPT_PAUSED) == 1 and self.screen() != row
        end = time.time() + 300
        while time.time() < end and not free():
            if gstate() in BATTLE and struct.unpack("<h", g.d.read(PARTNER_MAX_HP + 4, 2))[0] > 1:
                g.d.write_bytes(struct.pack("<h", 1), PARTNER_MAX_HP + 4)
            g.route("4:cross,20:none")
        check(free(), "lost, and back in control on map %d" % self.screen())
        check(self.screen() in (205, 218), "the loss ends at Jijimon's (map %d)" % self.screen())
        self.shot("lost")
        g.advance(120)

    def save_here(self, card_dir, out, judge, pile=None):
        """Save Anywhere from the field menu, then read slot 1 of <card_dir>/card1.mcd and hand it to
        judge(s0, main_state, (px, py, pz)), which makes the map's own checks on the saved block.
        `pile`: 4 bytes written into the poop table's last slot first (a pile dropped on the map).
        The position is kept in <out>/saved_pos.json."""
        g, check, i32 = self.g, self.check, self.i32
        # A run takes a varying amount of game time and can reach the partner's sleepy hour, which hides Save
        # Anywhere. Noon first: the game re-checks the hour against its sleepy..wake window and clears the flag.
        g.d.write_bytes(struct.pack("<hh", 12, 0), HOUR)
        self.wait_for(lambda: not g.para_condition & 1, 5)
        check((g.para_condition & 1) == 0, "partner not sleepy, so Save Anywhere is offered")
        if pile:
            g.d.write_bytes(pile, WORLD_POOP + 99 * 4)
        g.route("6:triangle,40:none")
        check(self.wait_for(lambda: i32(TRI_STATE) == -1, 10), "field menu open")
        g.route("6:left,20:none")
        check(struct.unpack("<h", g.d.read(MENU_CURSOR, 2))[0] == 6, "cursor on Save")
        g.route("6:cross,30:none")
        check(self.wait_for(lambda: i32(CURRENT_MENU) == 0x45, 10), "save prompt open")
        if g.d.read(YES_NO, 1)[0] != 0:
            g.route("6:up,20:none")
        g.route("6:cross,30:none")
        self.wait_for(lambda: i32(CURRENT_MENU) in (-1, 3, 0x42, 0x46), 20)
        check(i32(CURRENT_MENU) == -1, "save finished without a card error")
        self.wait_for(lambda: i32(TRI_STATE) == -1, 10)
        g.route("6:triangle,40:none")
        self.wait_for(lambda: g.tamer_state == 0, 10)
        card = open(os.path.join(card_dir, "card1.mcd"), "rb").read()
        blk = next(i for i in range(1, 16) if card[i * 128 + 10:i * 128 + 26] == b"BASLUS-01032DMR0")
        s0 = card[blk * 0x2000 + 0x200:]
        main_state, px, py, pz = struct.unpack_from("<4i", s0)
        judge(s0, main_state, (px, py, pz))
        with open(os.path.join(out, "saved_pos.json"), "w") as fh:
            json.dump([px, py, pz], fh)
