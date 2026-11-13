#!/usr/bin/env python3
"""An ESTIMATE of the glade boss fight. Not the game, and nothing here was measured.

    py -3.12 tools/fight_sim.py [--bin disc.bin]       # win rates against the current boss and four stock bosses
    py -3.12 tools/fight_sim.py [--bin disc.bin] --check
    py -3.12 tools/fight_sim.py [--bin disc.bin] --factory     # the Abandoned Factory's fighters against the plan's bounds

The boss's stats come from BOSS in gen_newmap.py (so this needs numpy and Pillow, as that does); moves, species,
type factors and the stock bosses' records come from your disc. The partners are made-up archetypes.

Follows src/btl/battle_main.c (docs/GAME.md 7.4-7.10): damage, the hit formula with its 6/5 idle factor, the auto-hit
rules for a stunned, attacking or winded defender, the speed buffer, MP, finisher damage and meter. Species 60's
finisher is tech 0x70. Stock bosses here are trigger 1 fights, which pay no MP. A partner pays mpCost*3.

Guessed: attack, hit-stun, cooldown and approach times, so every time and rate below is soft. One roll per attack
is an assumption, since how a tech's hits are delivered was never read (GAME.md 7.11).

Left out: walking and range; a block as a state (a failed roll only chips and feeds the meters); Counter; buffs
(they run as weak attacks); flatten's own attack (it acts as stun); senility; sick and injured; species below level
3; the partner's Your Call AI, target choice and Brains discount on MP (fixed weights, charge mode 0); the freeze
stopping meter and speed regen, and a finisher's cost to the speed buffer; other enemies joining. Items are one heal
used below 35 % HP, 3 s apart. A partner finisher is charged as if by 30 button presses.
"""
import argparse
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dw_tables import DEFAULT_BIN, NUM_MOVES, NUM_SPECIES, REC, TYPE_FACTORS, Disc, is_finisher  # noqa: E402
from gen_newmap import BOSS  # noqa: E402

M, SP, TF = [], [], b""         # moves, species, type factors: set by load()
TICKS_PER_S = 20
MAX_TICKS = 600 * TICKS_PER_S
RUNS = 500
CHARGE = 60                     # partner finisher charge (0..80)
ITEMS = ((0, 0, "no items"), (2, 1500, "2 x 1500"), (3, 5000, "3 x 5000"))
STOCK = (("MGEN98", 136), ("MGEN10", 172), ("MGEN99", 115), ("TROP01", 173))    # the disc's record for map, species

# name: HP, MP, Off, Def, Speed; species for its specials (None is neutral); three techs, weighted 34:33:33, and finisher
KITS = dict(ultimate=((47, 9, 17), 77), champion=((4, 11, 1), 63), rookie=((0, 4, 2), 59))
PARTNERS = {
    "Rookie (Agumon)": (1200, 800, 140, 90, 80, 3, "rookie"),
    "Champion (Greymon, mid-game)": (2500, 2000, 300, 280, 250, 5, "champion"),
    "early Ultimate (fresh MetalGreymon)": (4500, 4000, 550, 550, 400, 12, "ultimate"),
    "average Ultimate": (5500, 5000, 600, 600, 450, None, "ultimate"),
    "trained Ultimate": (7000, 6500, 750, 750, 600, None, "ultimate"),
    "maxed Ultimate": (9999, 9999, 999, 999, 999, None, "ultimate"),
}


def load(path):
    global M, SP, TF
    disc = Disc(path)
    M = [disc.move(i) for i in range(NUM_MOVES)]
    SP = [disc.species(i) for i in range(NUM_SPECIES)]
    TF = disc.ex(TYPE_FACTORS, 49)
    return disc


def factor(tech, spec):
    return sum(TF[tech["special"] * 7 + s] if s != 255 else 10 for s in spec)


def damage(off, de, tech, spec, roll):
    d = max(-500, min(500, off - de))
    return max(1, min(9999, factor(tech, spec) * (tech["power"] + int(d * tech["power"] / 500)) // 30 * roll // 100))


def finisher_damage(off, tech, spec, mul, div, roll):
    return max(1, min(9999, (off + tech["power"]) * factor(tech, spec) // 30 * mul // div * roll // 100))


def hit_chance(tech, atk_speed, def_speed, idle):
    x = int(tech["acc"] // 2 * (def_speed - atk_speed // 10) / 999)
    if idle:
        x = int(x * 6 / 5)
    return max(0, min(100, tech["acc"] - x))


class Fighter:
    def __init__(s, name, hp, mp, off, de, spd, moves, weights, fin, spec, enemy, attack_ticks=45, free_mp=False):
        s.name, s.hp, s.maxhp, s.mp, s.off, s.de, s.spd = name, hp, hp, mp, off, de, spd
        s.moves, s.weights, s.fin, s.spec, s.enemy = moves, weights, fin, spec, enemy
        s.attack_ticks, s.free_mp = attack_ticks, free_mp
        s.sb, s.pr, s.goal = 100, 0, 3000 - spd         # speed buffer, finisher meter
        s.st, s.t, s.q, s.mode = "idle", 0, None, 0     # state, its timer, queued tech, charge mode
        s.poison, s.stun, s.conf, s.hit_done, s.dry = 0, 0, 0, False, False


def tech_of(species_id, anim):
    """entityGetTechFromAnim: a tech row, or None for an empty slot."""
    if anim == 0xFF:
        return None
    return M[0x70] if species_id == 60 and anim == 0x3C else M[SP[species_id]["moves"][anim - 0x2E]]


def enemy_from(words, attack_ticks=45, free_mp=False):
    """A fighter from a map record's words, as in BOSS or on the disc."""
    r = dict(zip(REC, words))
    techs = [tech_of(r["species_id"], r["m%d" % i] & 0xFF) for i in range(4)]
    fin = techs[3] if techs[3] and is_finisher(techs[3]["id"]) else None
    return Fighter(SP[r["species_id"]]["name"], r["hp"], r["mp"], r["off"], r["defense"], r["speed"], techs,
                   [r["p%d" % i] for i in range(4)], fin, SP[r["species_id"]]["special"], True, attack_ticks, free_mp)


def boss():
    return enemy_from(struct.unpack("<%dh" % (len(BOSS) // 2), BOSS), attack_ticks=40)


def partner(name):
    hp, mp, off, de, spd, species, kit = PARTNERS[name]
    techs, fin = KITS[kit]
    return Fighter(name, hp, mp, off, de, spd, [M[t] for t in techs], (34, 33, 33), M[fin],
                   SP[species]["special"] if species is not None else (255, 255, 255), False)


def pick(a):
    """The weighted pick: a tech, None for an empty slot's wait, or False with nothing usable."""
    opts = [(m, w) for m, w in zip(a.moves, a.weights)
            if w and (m is None or (not is_finisher(m["id"]) and m["id"] != 0x2D and a.mp >= m["mp"] * 3))]
    if not any(m for m, _ in opts):
        return False
    return random.choices([m for m, _ in opts], [w for _, w in opts])[0]


def attack_hit(a, b, m):
    if b.stun > 0 or b.sb <= 0 or b.st == "attack":     # the auto-hit rules
        p = 100
    else:
        p = hit_chance(m, a.spd, b.spd, b.st in ("idle", "wait", "cool"))
    if random.randrange(100) < p:
        b.hp -= damage(a.off, b.de, m, b.spec, random.randint(90, 110))
        if b.st in ("idle", "wait", "cool") or (b.st == "attack" and not b.hit_done):
            b.st, b.t = "react", 20
        if m["statusChance"] and random.randrange(100) < m["statusChance"]:
            if m["status"] == 1 and not b.poison:
                b.poison = 100
            elif m["status"] == 2:
                b.conf = random.randint(200, 300)
            elif m["status"] == 3:
                b.stun = random.randint(200, 240)
            elif m["status"] == 4:
                b.stun = random.randint(224, 304)
    else:
        if m["range"] == 1:     # chip through a block
            b.hp -= max(1, damage(a.off, b.de, m, b.spec, random.randint(90, 110)) * random.randint(10, 30) // 100)
        if b.fin:
            b.pr = min(b.goal, b.pr + b.goal * 3 // 50)
        if a.fin:
            a.pr = min(a.goal, a.pr + a.goal * 4 // 50)


def fire_finisher(a, b):
    a.pr = 0
    mul, div = (random.randint(100, 200), 100) if a.enemy else (CHARGE, 40)
    b.hp -= finisher_damage(a.off, a.fin, b.spec, mul, div, random.randint(90, 110))
    a.st, a.t = "cool", 85


def fight(p, e, items=0, heal=0):
    """Tick both fighters to the end. Returns 1 if the partner wins, 0 if it loses, -1 on timeout, and the ticks."""
    pause = 0
    next_item = 0
    for tick in range(1, MAX_TICKS + 1):
        for x in (p, e):
            if tick % 2 == 0 and x.sb < 100:
                x.sb = min(100, x.sb + x.spd // 100 + 1)
            if x.fin:
                x.pr = min(x.goal, x.pr + 1)
            if x.poison:
                x.poison -= 1
                if x.poison == 0:
                    x.poison = 100
                    x.hp -= x.maxhp * random.randint(1, 3) // 100
        if items and p.hp < p.maxhp * 0.35 and tick >= next_item:
            p.hp = min(p.maxhp, p.hp + heal)
            items -= 1
            next_item = tick + 3 * TICKS_PER_S
        if pause > 0:
            pause -= 1
            continue
        for a, b in ((e, p), (p, e)):
            if a.hp <= 0 or b.hp <= 0:
                continue
            if a.stun > 0:
                a.stun -= 1
                continue
            if a.conf > 0:
                a.conf -= 1
            if a.st in ("react", "cool"):
                a.t -= 1
                if a.t <= 0:
                    a.st = "idle"
                continue
            if a.st == "idle":
                if a.fin and a.pr >= a.goal:
                    fire_finisher(a, b)
                    pause = 80          # the finisher's freeze
                    continue
                m = pick(a)
                if not m:               # nothing usable, or an empty slot: an 80-tick wait
                    a.st, a.t = "cool", 80
                    continue
                a.q, a.mode = m, random.randrange(2) if a.enemy else 0
                a.st, a.t = "wait", 8
            if a.st == "wait":
                a.t -= 1
                ready = a.sb > 0 if a.mode == 0 else a.sb == 100 or a.sb >= a.q["power"]
                if ready and a.t <= 0:
                    a.sb = max(-155, a.sb - a.q["power"])
                    if not a.free_mp:
                        a.mp -= a.q["mp"] * 3
                        a.dry = all(a.mp < m["mp"] * 3 for m in a.moves if m and not is_finisher(m["id"]))
                    a.st, a.t, a.hit_done = "attack", a.attack_ticks, False
                continue
            if a.st == "attack":
                a.t -= 1
                if not a.hit_done and a.t <= a.attack_ticks * 0.4:
                    a.hit_done = True
                    if not (a.conf > 0 and random.random() < .5):
                        attack_hit(a, b, a.q)
                if a.t <= 0:
                    a.st, a.t = "cool", 40
                    if a.fin:
                        a.pr = min(a.goal, a.pr + a.goal * 2 // 50)
        if p.hp <= 0:
            return 0, tick
        if e.hp <= 0:
            return 1, tick
    return -1, MAX_TICKS


def stock_bosses(disc):
    recs = {(r["map"], r["species_id"]): r for r in disc.map_records()}
    return [("%s on %s" % (recs[k]["species"], k[0]), [recs[k][f] for f in REC]) for k in STOCK]


def table(label, make_enemy, item_sets=ITEMS):
    random.seed(1)
    e = make_enemy()
    print("%s  HP %d MP %d Off %d Def %d Speed %d%s  %s; finisher %s" % (
        label, e.hp, e.mp, e.off, e.de, e.spd, " (pays no MP)" if e.free_mp else "",
        ", ".join("%s %d" % (m["name"], w) for m, w in zip(e.moves, e.weights) if m and m is not e.fin),
        e.fin["name"] if e.fin else "none"))
    print("  %-37s" % "win %, median s, boss dry %" + "".join("%22s" % lab for _, _, lab in item_sets))
    for name in PARTNERS:
        cells = ""
        for items, heal, _ in item_sets:
            wins, dry, times = 0, 0, []
            for _ in range(RUNS):
                en = make_enemy()
                r, ticks = fight(partner(name), en, items, heal)
                wins += r == 1
                dry += en.dry
                times.append(ticks / TICKS_PER_S)
            cells += "%22s" % ("%.1f  %3.0f  %.1f" % (100 * wins / RUNS, sorted(times)[RUNS // 2], 100 * dry / RUNS))
        print("  %-37s%s" % (name, cells))


def check(disc):
    """The worked numbers of docs/GAME.md 7.6, 7.7 and 7.17, against the disc and the current BOSS."""
    b = boss()
    words = struct.unpack("<45h", BOSS)
    assert (b.hp, b.mp, b.off, b.de, b.spd) == (words[11], words[12], words[15], words[16], words[17])
    assert [m["id"] for m in b.moves] == [47, 33, 35, 0x70] and b.fin is M[0x70]
    assert [m["mp"] * 3 for m in b.moves[:3]] == [258, 354, 96] and (M[47]["power"], M[0x70]["power"]) == (500, 218)
    neutral = (255, 255, 255)
    assert (hit_chance(M[47], 520, 450, False), hit_chance(M[47], 520, 450, True)) == (65, 62)
    assert (hit_chance(M[47], 520, 999, False), hit_chance(M[47], 520, 999, True)) == (43, 36)
    assert (hit_chance(M[47], 450, 520, False), hit_chance(M[47], 450, 520, True)) == (61, 58)
    assert [damage(820, 600, M[47], neutral, r) for r in (90, 110)] == [648, 792]
    assert [damage(820, 999, M[47], neutral, r) for r in (90, 110)] == [288, 353]
    assert damage(100, 900, M[47], neutral, 110) == 1                                # the clamp at -500
    assert finisher_damage(820, b.fin, neutral, 100, 100, 90) == 934
    assert finisher_damage(820, b.fin, neutral, 200, 100, 110) == 2283
    assert b.goal == 3000 - words[17]

    # The auto-hit rules: a defender that cannot dodge always takes the hit, even from accuracy 0.
    blind = dict(M[47], acc=0, statusChance=0)
    for rule in (dict(stun=1), dict(sb=0), dict(st="attack", hit_done=True)):
        d = partner("average Ultimate")
        d.__dict__.update(rule)
        attack_hit(b, d, blind)
        assert d.hp < d.maxhp, rule
    d = partner("average Ultimate")
    attack_hit(b, d, blind)
    assert d.hp == d.maxhp

    # Who pays MP: the boss and the partner do, a free-MP stock boss does not, a dry boss only has its finisher.
    random.seed(1)
    p, e = partner("average Ultimate"), boss()
    fight(p, e)
    assert p.mp < PARTNERS["average Ultimate"][1] and e.mp < words[12]
    stock = enemy_from(stock_bosses(disc)[0][1], free_mp=True)
    start = stock.mp
    fight(partner("average Ultimate"), stock)
    assert stock.mp == start and not stock.dry
    e = boss()
    e.mp = 0
    assert pick(e) is False

    # Rates only have to run the right way round.
    def rate(name):
        random.seed(1)
        return sum(fight(partner(name), boss())[0] == 1 for _ in range(100))
    assert rate("Rookie (Agumon)") <= rate("average Ultimate") <= rate("maxed Ultimate")
    print("fight_sim check: ok")


def win_rate(make_enemy, partner_name="average Ultimate", items=3, heal=5000, runs=4 * RUNS):
    """The reference cell: the plan judges the factory's fighters against the glade boss on this one."""
    random.seed(1)
    return 100 * sum(fight(partner(partner_name), make_enemy(), items, heal)[0] == 1 for _ in range(runs)) / runs


def factory():
    """The same table for every distinct fighter of gen_factory.py's CAST (one per stat record), then the plan's
    bounds against the average Ultimate with 3 x 5000 heals: regulars 90 % or more, guardians within 10 points of
    the glade boss, the boss at least 10 points below it and above 0. Fights of a pair are judged one fighter at a
    time, as this sim has one enemy."""
    from gen_factory import CAST, NAMES, ROWS
    glade = win_rate(boss)
    seen, bad = {}, []
    for row, recs in CAST.items():
        for r in recs:
            if r.role != "worker":
                seen.setdefault((r.words[11:30], r.species, r.role), (r.words, []))[1].append(
                    "%s slot %d" % (NAMES[ROWS.index(row)], r.slot))
    for (_, _, role), (words, where) in seen.items():
        print()
        table("%s %s (%s)" % (role.capitalize(), SP[words[0]]["name"], ", ".join(where)), lambda: enemy_from(words))
        rate = win_rate(lambda: enemy_from(words))
        ok = dict(regular=rate >= 90, guardian=abs(rate - glade) <= 10, boss=0 < rate <= glade - 10)[role]
        print("  -> %.1f %% against the average Ultimate with 3 x 5000 heals (glade boss %.1f %%): %s"
              % (rate, glade, "ok" if ok else "OUT OF BOUNDS"))
        if not ok:
            bad.append(SP[words[0]]["name"])
    print("\nfactory: %s" % ("every fighter within the plan's bounds" if not bad else "out of bounds: %s" % bad))
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=DEFAULT_BIN)
    ap.add_argument("--check", action="store_true", help="assert the worked numbers and stop")
    ap.add_argument("--factory", action="store_true", help="the Abandoned Factory's fighters (gen_factory.py CAST)")
    a = ap.parse_args()
    disc = load(a.bin)
    if a.check:
        check(disc)
        return
    if a.factory:
        print("AN ESTIMATE: nothing here was measured. See this tool's docstring for what it leaves out.")
        sys.exit(0 if factory() else 1)
    print("AN ESTIMATE: nothing here was measured. See this tool's docstring for what it leaves out.\n")
    table("Current boss: " + boss().name, boss)
    for label, words in stock_bosses(disc):
        print()
        table("Stock " + label, lambda: enemy_from(words, free_mp=True), ITEMS[:1])


if __name__ == "__main__":
    main()
