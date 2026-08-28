# Digimon World — Recompiled

**Digimon World** running natively on your PC. Not an emulator: the game's code
is translated into a real Windows program, so you get widescreen, sharp
high-resolution rendering, save states, rewind, and a mod menu.

### Getting it

Check the [Releases](https://gitlab.com/ethan4love/psx-recomp-port/-/releases)
page for a setup package (`digimon-world-*-win-x64.zip`). If none is attached,
build one yourself — see [DEVELOPING.md](DEVELOPING.md).

Either way there is **no playable download**: the port is generated from your
own disc, on your machine. Nothing game-related is distributed here.

---

## Before you start

**A Windows PC.** 64-bit.

**Python 3.** If you don't have it: [python.org/downloads](https://www.python.org/downloads/).
Run the installer and **tick "Add Python to PATH"** on the first screen — this
matters, the setup won't find it otherwise.

**Your own copy of the game**, as a `.bin` + `.cue` pair on your PC. Nothing
here includes game data. See [DISC.md](DISC.md) for the exact
version and how to check yours is right.

---

## Installing

These steps assume you have the **setup package**
(`digimon-world-*-win-x64.zip`) from Releases, or one you built yourself per
[DEVELOPING.md](DEVELOPING.md). It contains no game data either way.

**1. Extract the zip.** Right-click → Extract All. Put it somewhere you'll keep
it, like `C:\Games\Digimon World`. Don't run it from inside the zip.

**2. Run `Digimon_World_Recompiled.exe`.**

Windows may warn you the app is unrecognised. Click **More info** →
**Run anyway**. That warning appears for any program without a paid
code-signing certificate.

**3. Follow the first-run setup.**

It will offer to download build tools (about 100 MB). Leave the box ticked and
click **Next**.

**4. Point it at your game.** Click **Browse Disc** and select your **`.cue`**
file — the small one, not the big `.bin`.

**5. Wait.** It now builds your copy of the game. **This takes 10–30 minutes**
depending on your PC, and it only ever happens once. It's normal for it to look
busy or briefly unresponsive.

**6. Press PLAY.** Every future launch skips all of the above and starts in
seconds.

---

## Why does it have to build?

Because that is what keeps this above board. The download contains none of the
game's data. The build step reads *your* disc on *your* PC and produces your own
personal copy. Nothing game-related is distributed to you, which is exactly why
you have to supply the disc yourself.

---

## Playing

**Saving works like the original.** You can only save by **sleeping**. Your
partner needs the ZZZ bubble over its head — then open the menu and choose
Sleep. The option is greyed out at any other time. There is no save point, and
no save option in the menu otherwise.

**Controller:** plug it in before launching. In the launcher, leave the Player 1
toggle on **D-Pad**. If you switch it to Analog, **every button stops working** —
this game only understands the digital controller, and that is not a bug in the
port.

**Keyboard:** Enter = Start, X = Cross, S = Circle, A = Triangle, Z = Square,
arrow keys = D-Pad. Rebind them in the launcher's Controls page.

**Handy keys:** `F7` save state, `F8` rewind, `Alt+Enter` fullscreen.

---

## Mods

Click **Mods** in the launcher. Everything is off by default; tick what you want.

| Mod | What it does |
|---|---|
| **Quality of Life** | Make your partner live longer than the stock 15 in-game days; slow the world clock so hunger, tiredness and ageing all relax together |
| **Gameplay** | Better odds of learning techniques in battle; lower the stat requirements for evolutions |
| **Trainer** | Immortality, no pooping, no lifespan loss, stat-gain multipliers |
| **Widescreen** | 16:9 (the one mod that is on by default) |
| **Fast Loading** | Shorter loads. **Off by default and best left off on this game** -- it speeds up real time whenever it thinks a load is happening, and on Digimon World it stays engaged, running the whole game at 4x. |

Turning a mod off restores the original game exactly — every change is a guarded
patch, nothing is permanent.

**Cheats:** open `cheats.txt` in Notepad and change `[ ]` to `[x]` on any code,
then restart. 44 GameShark-style codes are included. One worth knowing:
**"Skip all movies"** takes you straight to the title screen instead of sitting
through the two-minute intro.

---

## If something goes wrong

**"Python was not found"** — Python isn't installed, or you missed the "Add
Python to PATH" tickbox. Reinstall it with that box ticked.

**"Disc not recognised", or setup rejects your file** — you picked the `.bin`
instead of the `.cue`, or your copy is a different release.
[DISC.md](DISC.md) lists the file size and checksums to compare against.

**The game is running too fast?** Open the Mods panel and make sure
**Fast Loading** is off. It accelerates real time during loads, and on this game
it stays on permanently, running everything at 4x speed. It is off by default
from 0.1.5 onward.

**Controller does nothing** — the Analog/D-Pad toggle is on Analog. Switch it to
**D-Pad**. This is the single most common problem.

**The build failed** — check you have about 5 GB free, and that antivirus isn't
quarantining the compiler. Antivirus is the usual culprit: it sees a program
writing and running new executables.

**Stuck on the intro movie** — the movie runs about two minutes and only then
shows "Press Start Button" for a short window. Enable the "Skip all movies"
cheat described above to go straight to the title.

---

## Credits

Built with [psxrecomp](https://github.com/mstan/psxrecomp) by mstan — the static
recompiler doing the real work here. Game internals mapped with
[dw_decomp](https://github.com/jype0/dw_decomp) by jype0.

Digimon World is © Bandai. This project ships no game content and is not
affiliated with or endorsed by Bandai.

Building from source: [DEVELOPING.md](DEVELOPING.md).
