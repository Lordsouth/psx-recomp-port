#!/usr/bin/env python3
"""Fail if two co-enablable mod features patch the same byte differently.

WHY THIS EXISTS. The runtime rejects the ENTIRE mod plan when two applied
patches disagree over a byte (mod_packages.cpp write_overlap_mismatch), so one
overlap between two independently-toggleable features does not degrade
gracefully -- the player loses every mod, including widescreen, with a message
naming two features they cannot be expected to connect. That is exactly what
happened when "Guaranteed Item Drops" swept all 180 DIGIMON_DATA records while
"Expanded Roster" rewrote record 114 wholesale.

Patches gated on different values of the same option are mutually exclusive by
construction and are not collisions. Packages that are never shipped (the
generated Vice manifest, which the packager purges) are skipped by default
because a collision there cannot reach a player.

  py -3.12 tools/check_mod_collisions.py           # shipped packages only
  py -3.12 tools/check_mod_collisions.py --all     # include non-shipped ones
"""
import glob
import os
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SEARCH = [
    os.path.join(ROOT, "mods", "preloaded", "packages"),
    os.path.normpath(os.path.join(ROOT, "psxrecomp", "mods", "builtin",
                                  "packages")),
]
# Packages the setup packager purges (their manifests embed another work's
# bytes and must never be redistributed) -- a collision there is a local-only
# concern, not a release blocker.
NOT_SHIPPED = {"dw.vanilla.vice"}


EDITIONS = set()      # {frozenset(("pkg/edition", "pkg/original"))}: the runtime never runs both
NEEDS = {}            # "pkg/feature" -> what it requires: requires_feature, unconditional [[requirement]]
GIVES_WAY = {}        # "pkg/original" -> what its edition requires: while that is on, the original is off


def load_patches(include_all):
    out = []
    for base in SEARCH:
        for mf in glob.glob(os.path.join(base, "*", "*", "manifest.toml")):
            with open(mf, "rb") as f:
                m = tomllib.load(f)
            pkg = m.get("id") or os.path.basename(mf)
            if not include_all and pkg in NOT_SHIPPED:
                continue
            for c in m.get("constraint", []):
                if c.get("kind") == "requires_feature":
                    NEEDS.setdefault("%s/%s" % (pkg, c["feature"]), set()).add("%s/%s" % (pkg, c["requires_feature"]))
            for r in m.get("requirement", []):
                if not r.get("when"):
                    NEEDS.setdefault("%s/%s" % (pkg, r["feature"]), set()).add(
                        "%s/%s" % (r["package"], r["requires_feature"]))
            for feat in m.get("feature", []):
                if feat.get("edition_of"):
                    EDITIONS.add(frozenset(("%s/%s" % (pkg, feat["id"]), feat["edition_of"])))
                    GIVES_WAY.setdefault(feat["edition_of"], set()).add("%s/%s" % (pkg, feat["id"]))
            for p in m.get("patch", []):
                exp = (p.get("expected") or "").replace(" ", "")
                start = p.get("address", p.get("offset"))
                if not exp or start is None:
                    continue
                out.append({
                    "target": p.get("target"), "lo": start,
                    "hi": start + len(exp) // 2, "pkg": pkg,
                    "feature": p.get("feature"),
                    "when": tuple(sorted((p.get("when") or {}).items())),
                    "replace": (p.get("replace") or "").replace(" ", ""),
                })
    return out


def mutually_exclusive(a, b):
    """Two patches of the same feature gated on different values of one option
    can never both be live; nor can a feature and its edition (edition_of)."""
    ka, kb = "%s/%s" % (a["pkg"], a["feature"]), "%s/%s" % (b["pkg"], b["feature"])
    if frozenset((ka, kb)) in EDITIONS:
        return True
    # An original is off whenever what its edition needs is on, so it never runs
    # beside a feature that needs the same thing (say, anything that needs 1.5).
    for orig, other in ((ka, kb), (kb, ka)):
        need = set().union(*(NEEDS.get(e, set()) for e in GIVES_WAY.get(orig, ())))
        if need and (other in need or NEEDS.get(other, set()) & need):
            return True
    if a["pkg"] != b["pkg"] or a["feature"] != b["feature"]:
        return False
    wa, wb = dict(a["when"]), dict(b["when"])
    return any(wa[k] != wb[k] for k in set(wa) & set(wb))


def main():
    include_all = "--all" in sys.argv
    patches = load_patches(include_all)
    by_target = {}
    for p in patches:
        by_target.setdefault(p["target"], []).append(p)

    blockers, benign = [], []
    for items in by_target.values():
        items.sort(key=lambda p: p["lo"])
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if b["lo"] >= a["hi"]:
                    break
                if mutually_exclusive(a, b):
                    continue
                lo, hi = max(a["lo"], b["lo"]), min(a["hi"], b["hi"])
                ao, bo, ln = (lo - a["lo"]) * 2, (lo - b["lo"]) * 2, (hi - lo) * 2
                same = a["replace"][ao:ao + ln] == b["replace"][bo:bo + ln]
                (benign if same else blockers).append((lo, hi, a, b))

    scope = "all" if include_all else "shipped"
    print("checked %d patches across %d %s package(s)"
          % (len(patches), len({p["pkg"] for p in patches}), scope))
    if benign:
        print("%d overlapping pair(s) write identical bytes (harmless)"
              % len(benign))
    if not blockers:
        print("OK: no co-enablable feature writes the same byte differently.")
        return 0
    print("\n%d COLLISION(S) -- enabling both features rejects the WHOLE mod "
          "plan:" % len(blockers))
    for lo, hi, a, b in blockers:
        print("  %s 0x%08X..0x%08X" % (a["target"], lo, hi - 1))
        print("      %s / %s" % (a["pkg"], a["feature"]))
        print("      %s / %s" % (b["pkg"], b["feature"]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
