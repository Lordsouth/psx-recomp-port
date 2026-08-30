# Changelog

Every release, what shipped in it, and what was known to be wrong at the time.

Versions are the setup packages published at
<https://gitlab.com/ethan4love/psx-recomp-port/-/releases>. Every download
contains **no game data** — you supply your own Digimon World (USA) disc image
(SLUS-01032), and the game is built on your machine from it.

---

## 0.3.0

**Fifty-two raisable Digimon.**

Fifty-one of them shipped on the original disc as enemies only: Tentomon,
Myotismon, Guardromon, Panjyamon, Gigadramon, BlueMeramon, Otamamon, Gotsumon,
SnowAgumon, Saberdramon and more all have finished models, animations,
textures, voices and battle auras, and the game simply never let you raise
them. The fifty-second, Machinedramon, fills the game's one genuinely unused
slot. Sixteen new Rookies, twenty-three new Champions, six new Ultimates.

* **The three dead evolution items work.** Giga Hand ("Digivolve to
  Gigadramon!"), Noble Mane ("Digivolve to Panjyamon!") and Metalbanana
  ("Digivolve to MetalEtemon!") have always been in the game, named, priced and
  described, and always did nothing. They now work — including on an Ultimate,
  which the game otherwise never allows.
* **Prosperity Points count the new recruits.**
* **Fixed:** two of the new Digimon drew garbage where their name should be in
  the arena battle intro. The name table stopped at 112 entries and they were
  reading the bytes of a file path.
* **Fixed:** empty cells on the evolution chart opened a page for a Digimon you
  had not selected.
* **Fixed:** the "every item in storage" cheat only ever filled 56 of the
  game's 128 items, starting 44 items too far into the list.
* **Fixed:** Machinedramon could never actually be evolved into. It had been
  wired to two Ultimates, and Ultimates never evolve again, so the route the
  documentation described could not fire.

**Known limits, unchanged by this release**

* Five of the new Digimon — Sharmamon, ClearAgumon, Darkrizamon, SandYanmamon,
  SnowGoburimon — are fully built but have no way in. The game has exactly four
  In-Training Digimon with four free evolution slots each, sixteen in total,
  and twenty-one new Rookies wanted them. That is a limit of the original
  design. Each is a same-size, same-attribute twin of one that is reachable.
* The evolution chart still shows 62 cells, so the new Digimon do not all
  appear on it.
* Widescreen still renders menus and HUD stretched, and stays off by default.

---

## 0.2.0 — built, never published

Superseded by 0.3.0 before release. Its content is in 0.3.0, with the
substantial difference that **0.2.0's headline feature did not work**:
Machinedramon was reachable only by cheat, because its evolution route was
wired to two Ultimates. It also carried three defects found later — five of the
seven species it advertised were broken by an unrelocated table read, the
training animation for slot 114 used a stray value, and its item-take distance
was written to the wrong index.

Kept in the history because the work is real and the release notes were
written; it is simply not a version anyone should install.

---

## 0.1.7

* Widescreen keeps pre-rendered backdrops aligned with the 3D. Previously the
  backdrops stayed 4:3 while the world squashed, so they slid out of register.
* Widescreen now **off by default** and labelled experimental: 3D proportions
  are correct, but menus and HUD still render stretched.

## 0.1.6

* Removed a machine-local path from the mod generator that made the tool
  unusable on anyone else's machine.
* Hardened setup.
* Restored a missing packaging section in the docs and corrected stale claims.
* Moved project links to GitLab.

## 0.1.5

* Verified fix for the game running at double speed on 60 Hz panels.
* Cleaned the shipped mod state.

## 0.1.4

* **Stopped shipping Fast Loading enabled.** On this game it stays permanently
  engaged and ran the whole game at 4x.

## 0.1.3

* Overlays compile under the bundled clang toolchain.

## 0.1.2

* Fixed setup on Python 3.8/3.9.
* Corrected the disc-picker wording.

## 0.1.1

* Picked up the Windows rebuild fix.

## 0.1.0

First release. Digimon World running natively on PC through psxrecomp static
recompilation, with a setup package that builds the game from the player's own
disc.

---

## Not in any release

Work that exists in the repository but has never shipped in a package:

* **Native evolution engine** (`dw_evolution.c`, `dw.vanilla.evolution`).
  Groundwork for removing the evolution system's structural limits — six paths
  per Digimon, ids below 128, the 62-row table — by replacing the chooser
  functions with native code. One function is wired and the mechanism is built,
  but it has not yet been observed running, so the package ships disabled and
  changes nothing.
* **Widescreen UI counter-squash.** Built and dormant; it does not work.

## What is not verified

Stated plainly, because the automated tests cannot reach it: every roster test
*forces* an evolution directly, which bypasses the game's own requirement
scoring. So all 52 species are proven to instantiate correctly and remain
stable — but whether raising a partner to a particular weight actually yields
the Digimon the tables say it should has not been confirmed by playing.
