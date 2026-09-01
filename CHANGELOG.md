# Changelog

Every release, what shipped in it, and what was known to be wrong at the time.

Versions are the setup packages published at
<https://gitlab.com/ethan4love/psx-recomp-port/-/releases>. Every download
contains **no game data** — you supply your own Digimon World (USA) disc image
(SLUS-01032), and the game is built on your machine from it.

---

## 0.3.2

**Widescreen, rebuilt. Nothing is stretched any more.**

Until now widescreen worked by squashing: the game drew a wider view squeezed
into its normal 320-pixel frame, and the picture was stretched back out on
screen. The 3D survived that; menus, text boxes and the HUD did not, which is
why it shipped off by default and labelled experimental.

It now renders the wider view into a genuinely wider frame and shows it 1:1.
Menus, item windows, dialogue and the HUD keep their exact original shapes, and
the extra width is real extra view on both sides rather than a stretch.

Four things had to be fixed to get there, all found by surveying the game
rather than by guessing:

* **Every fade leaked.** The screen would fade to black while the strips down
  each side stayed lit, on every single screen transition in the game.
* **Widgets the game hides came back.** The clock slides off the left edge when
  you open a menu, and the enemy health bars sit off the right when there is no
  enemy. A wider screen revealed both of those hiding places, sliced by the
  frame edge.
* **The camera stopped too late at the edge of a map**, so the extra width had
  no map behind it and rendered as a black bar -- for the whole of a battle, if
  you fought there.
* **The scrolling background could not always fill the wider frame**, leaving a
  black band down one side on some screens until you moved.

Widescreen is still **off by default** -- turn it on in the Mods panel. It is no
longer labelled experimental.

**Also:** when a build fails, the launcher now shows what actually went wrong
and writes `psxrecomp-build.log` next to the game. Previously it reported only
"psxrecomp generate failed (exit 1)" and threw away the message that identified
the fault, which made every report of a build problem undiagnosable.

*Not covered:* one known oddity remains unexamined -- for about a second after
a wild battle ends, the picture drops back to 4:3 before returning to
widescreen.

---

## 0.3.1

**The build works again. 0.3.0 could not be built at all.**

If you downloaded 0.3.0, pointed the launcher at your disc and pressed Build,
it failed immediately with "psxrecomp generate failed (exit 1)", and a terminal
window flashed up carrying the real message:

    fatal error: 'generated/InstrId_enum.h' file not found

Nothing was wrong with your disc, your machine or your setup.

Two separate faults had to line up, which is why this took a while to be
noticed. The script that assembles the download excludes a folder named
`generated`, meant to keep a folder of machine-generated C out of the package —
but it matched *every* folder of that name, including one belonging to the
instruction decoder that is ordinary source code. That started with 0.1.6.
It caused no harm at the time, because those downloads also contained a
ready-built copy of the tool that needs those 21 files, so nothing on your
machine ever had to compile them.

0.3.0 was the release where the second fault hit: the packaging run stopped
early and left that ready-built tool out. With it missing, the installer builds
the tool from source — and walks straight into the 21 missing files. No build
could have succeeded.

For the record, since this deserves to be exact rather than dramatic:

| release   | 21 headers | ready-built tool | can a player build it? |
|-----------|-----------|------------------|------------------------|
| 0.1.0-0.1.5 | yes     | yes              | yes                    |
| 0.1.6, 0.1.7 | **no** | yes              | yes — the fault is dormant |
| 0.3.0      | **no**   | **no**           | **no**                 |
| 0.3.1      | yes      | yes              | yes                    |

Fixed: the folder exclusion is anchored to the one folder it was meant for; the
packaging run no longer stops early (a cleanup step aborted the entire script
whenever it found nothing to clean, which is what truncated 0.3.0); and stray
memory-card files are purged automatically instead of by hand. A new release
check now compiles the package the way your machine does, before anything can
be published — it passes on 0.1.5 and fails on every broken release.

Reported by u/WishConstant7039, who also supplied the terminal message the
launcher throws away. Without that line this would have taken far longer to
find.

Nothing else changed. This release deliberately carries no gameplay or
rendering change: the point was to get the build working again, and every
extra edit is another thing that could be wrong.

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
