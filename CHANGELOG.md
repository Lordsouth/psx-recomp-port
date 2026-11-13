# Changelog

Every release, what shipped in it, and what was known to be wrong at the time.

Versions are the setup packages published at
<https://github.com/Lordsouth/psx-recomp-port/releases>. Every download
contains **no game data** — you supply your own Digimon World (USA) disc image
(SLUS-01032), and the game is built on your machine from it.

---

## Unreleased

**Widescreen: a recruit's "X joins the city!" no longer freezes the game.**
A player reported "Agumon joins the city!" staying on screen for good: the world
kept moving, but they could not get past it. After that box the game moves the
camera to your tamer. Widescreen stops the camera a little further from each map
edge than the original game does, so the camera can come to rest a few pixels short
of that spot, and the game waited for it forever. This happened only with widescreen
on, with the tamer standing near the left or right edge of the screen, and it could
hit any scene that moves the camera, not only recruits. A camera move now counts as
finished when it reaches widescreen's edge. With widescreen off nothing changes.

**A second new map: the Abandoned Factory, on both discs.** Tick it under
**Mods → New maps**, go to the dead end with the signs on Gear Savanna and walk
east through the gap past the camp. Four screens lead into an old factory: an entry
hall with workers, two floors with machine Digimon and a guardian each who holds the
way until beaten, and a chamber with Machinedramon. Beating Machinedramon pays out a
Metalbanana and six chips once, all or nothing: with a full bag it takes nothing and
says so. Saving inside the factory works, and the card loads on the stock game. It works
with Digimon World 1.5 too: with 1.5 ticked it switches to a 1.5 edition by itself,
the same four screens and fights, and the boss's reward box names item 127 as 1.5
does, Glimpup Gem.

**Every other mod now works with Digimon World 1.5.** Expanded Roster, Hidden Glade,
Guaranteed Item Drops and Evolution Unbound switch to versions made for 1.5 while
it is on, the new Digimon included, and back when you untick 1.5. On 1.5 the roster
makes the same 51 Digimon raisable, written into 1.5's own tables; Machinedramon is
left out, because 1.5 uses its slot for Veemon. The glade is the same map and fight,
written where 1.5 keeps the files it moved. The separate "(1.5)"
entries are gone from the Mods list, and ticking 1.5 no longer turns any of these
off. The Native Evolution Engine no longer turns 1.5 off either: on 1.5 it steps
aside for 1.5's own evolution chooser.

## 0.6.0

**A new map: the Hidden Glade.** Tick it under **Mods → New maps**, go to the
Native Forest screen where Palmon waits, and walk north through the gap in the
bushes at the top right. It is a small forest glade with a pond and a dirt path,
and you can walk behind its bushes. It is the first map in this port that the
original game never had: the art, the walkable ground and the sprites are all
this project's own, and no game data is in the mod.

Something strong waits at the end of the path. It is a hard secret fight: bring a
fully grown, well-trained partner and strong healing items. It starts only when
you press confirm next to it, you can run, and it is back on your next visit.

* A save made inside resumes just outside it, in Native Forest. No memory card
  depends on the mod, so turning it off is always safe.
* It needs the exact disc image in `DISC.md` and cannot run with Digimon World 1.5.
* It has no other Digimon, and no items of its own yet.

**A fix for a freeze in the original game.** A battle hung on its results when the
win lifted your partner's Brains past 700, 800, 900 or 999, and you lost everything
since your last save. The game now shows its MP bonus message and carries on, on
the original game and on Digimon World 1.5. **Battle End Freeze Fix** is the first
mod that is on by default; you can untick it under **Mods → Quality of Life**. The
message reads "MP Cost Bonus! reduced by 10 pct!", because the original line ran off
the box and the game's font has no % sign. Savestates made before this update belong
to the old mod settings, so they appear only with the fix unticked.

## 0.5.0

**Rewind is in.** Press **F8** (or **Select + R3** on a pad) and a strip of
thumbnails from the last ~12 seconds appears. Pick a moment with Left/Right and
press Cross to go back to it: a fumbled battle, a wasted feeding, a missed
evolution. Turn it on under **Mods → Quality of Life → Rewind**; it is off by
default.

* The README has listed F8 rewind for a while, but no release ever had it. The
  framework component that does the work was never included in the package,
  so every build quietly left it out, 0.4.0 included. The release gate now
  refuses a package without it.
* It keeps 50 snapshots, one every 15 frames, and uses about 185 MB of memory
  at those defaults; both numbers are options. Taking a snapshot costs about
  2 ms, and jumping back about a tenth of a second.
* Tested in town (across a map change), leaving and inside a battle, and in
  the middle of a map load: position, clock, HP and the battle all come back,
  and the audio keeps playing.
* The history survives a savestate load, so going far enough back returns to
  before the load.

**Widescreen at 21:9 fills the narrow rooms.** The 36 smallest rooms are
narrower than a 21:9 view and showed a black bar on each side (88 px on the
three-tile rooms, 24 px on the four-tile ones; the three-tile rooms also had a
21 px bar at 16:9). Those bars now show the room's own edge art, mirrored and
fading to black at the screen edge. Where a room's edge is mostly black anyway
(17 sides), and on two rooms where the mirror looked worse than the bar, the
bar stays.

**The clock and the mood gauges sit in the corners of the wide screen.** In
widescreen they used to stay where the 4:3 picture had them, inset from the
edges; the clock, its needle and the gauges now move out to the wide frame's
corners, and still hide and return as before.

**The launcher's memory card panel follows Digimon World 1.5.** With 1.5
ticked it shows 1.5's own cards (`saves/dw15/`), and switches back when you
untick it. In 0.4.0 it always showed the normal cards.

**HD Texture Packs work with Digimon World 1.5.** With both ticked, the game
closed at start-up. The pack folder was being treated as part of the game, and
1.5's separate savestates require everything in the game to be checksummed,
which a folder of images cannot be. The pack is now treated as presentation
only, like the CRT filter, so it boots, 1.5's savestates work, and in netplay
the pack is your own choice rather than the host's.

**HD Backdrops**, a free optional download: all 242 pre-rendered backgrounds
redrawn at 4x, including the extra width in widescreen. See the README's "HD
Backdrops" section or the
[release page](https://github.com/Lordsouth/psx-recomp-port/releases/tag/hd-backdrops-1.0).

Also:

* Netplay: changing a display option during a match no longer saves the host's
  mod list as your own.

Known at the time:

* The narrow-room fill is a mirror. At normal distance it reads as the room
  continuing; looked at closely, faint symmetric shapes show. It is not drawn
  on Vulkan, in netplay, or on Software with an HD texture pack; those keep
  the black bars.
* Rewind with Digimon World 1.5 has only had a short test (rewinding
  dialogue), not a play session.
* Widescreen: in Birdra Transport a thin black strip still shows right of
  the cage, because the original art stops there. Rooms drawn floating in
  black (the mansion, the caves, the igloo) show more of that black in the
  wider view; that is the game's own art and is left as it is.

---

## 0.4.0

**Digimon World 1.5 is in the Mods panel.**

Capablemite's fan mod, Test Build 1.3.1, is one tick away: Veemon, Wormmon,
Renamon and Guilmon lines, new eggs, new special attacks, a four-page
Digivolution Chart and a second Tech page. There is no patcher and no second
disc. You keep your normal disc; with the mod on, the launcher checks it,
applies the author's own patch file to it in memory and plays the result. The
disc file is never written. Untick it and you are back to the original game.

* **Start a new game.** 1.5's own notes say old saves may not work.
* **Separate saves.** While 1.5 is on, the memory cards are `saves/dw15/`
  instead of `saves/`, so a 1.5 save and a normal save can never overwrite each
  other.
* **Mods that cannot run with it are turned off**: Expanded Roster (both use
  the same free Digimon slots), Evolution Unbound, Guaranteed Item Drops and
  the Native Evolution Engine. Each edits bytes 1.5 changed, and one guard
  failing would have thrown out every mod. The list comes from
  `tools/check_dw15_compat.py`, which compares every patch and code hook
  against the 1.5 disc; the rest of the Trainer, Quality of Life, Tech Mastery,
  the cheats and Widescreen passed.
* **Two of them have 1.5 editions**, in the same Mods panel. Ticking one
  ticks 1.5 as well:
  * **Guaranteed Item Drops (1.5).** Every Digimon you defeat in 1.5 drops
    its item, the new species included. Tested in real 1.5 battles: 21
    drops from 21 defeated enemies, against 3 from 18 without it.
  * **Evolution Unbound (1.5).** Lowers the six stat requirements of 1.5's
    evolutions to 75% (Relaxed) or 50% (Open), the new lines included. 1.5
    reloads that table from the disc after every training session, so the
    change is made on the disc as well and survives the reload.

**Three new Quality of Life options**, all off by default and all working with
Digimon World 1.5:

* **Save Anywhere.** Pick Sleep in the field menu while your partner is not
  sleepy and you get the game's own "Do you want to save?" prompt. No time
  passes, nothing about your partner changes, and the menu comes back after
  the save. When your partner is sleepy, Sleep works as before. Sleep no
  longer looks greyed out or buzzes, so the ZZZ bubble is what tells you your
  partner is sleepy.
* **Text Speed.** Fast draws each dialogue page at once instead of one line
  per frame; Instant also opens the box without its zoom-in. A new message
  box appears in 18 frames (Fast) or 3 (Instant) instead of 21-27, with the
  same text. Instant drops the box-open sound, and mashing X can skip a page.
* **Run Speed.** The tamer runs 1.5x or 2x faster in the field. Collisions,
  map exits, doors and talking work as normal; walking with R1, menus and
  cutscenes are unchanged. Your partner keeps its own pace: on a long
  straight run it falls well behind and catches up a few seconds after you
  stop. A map exit puts it back beside you.

**Overlays now compile to native code for everyone.** Parts of the game that
load from disc mid-play (battles, training, menus) are compiled to native code
in the background. That step used to need the `py` launcher with exactly
Python 3.12 installed; without it, every attempt failed silently and those
parts stayed in the slower interpreter. It now uses the Python and compiler
bundled with the setup package, so it works on any machine.

**The framework is updated** to the latest upstream psxrecomp (two months of
work: 60 fps rendering, HD movie replacement, audio-sync and widescreen
fixes, and more). Speed in normal play is unchanged, measured side by side.

Also:

* Widescreen fixes that had silently stopped working: fades dim the whole
  screen again (the side strips stayed bright), the hidden clock no longer
  shows sliced at the left edge, and the backdrop fills the left edge on wide
  maps. The updated framework only runs function-entry hooks a mod names, and
  Widescreen did not name these three.
* Widescreen now fills two rooms whose pre-rendered art stopped where the
  original 4:3 camera did, so the widened view showed a flat black strip: the
  Item Shop's right wall and the left side of Jijimon's house (the larger
  room). The new art uses each room's
  own colours and is loaded by the game itself. It comes with widescreen;
  there is nothing extra to turn on, and with widescreen off the rooms are the
  original ones.
* Widescreen no longer drops to 4:3 for about a second after a won battle or
  an evolution; the field stays wide while the game reloads it. Characters,
  your partner, chests and doors stay drawn in the extra width instead of
  vanishing at the old 4:3 edge, and an enemy you can see there can join the
  fight. The smallest rooms now sit centred with an even 21 px bar on each
  side instead of up to 53 px on one side. The thin black strip that flickered
  at the edge behind you while walking is gone, maps that could open with a
  black edge (seen after loading a savestate) open clean, and the backdrop's
  widest column is read from the disc, so it can no longer show garbage after
  a battle.
* Widescreen: fades, flashes and the closing circle after a lost battle now
  cover the whole wide screen; before, the side strips stayed bright. Snow
  falls across the full width, and mist on the misty maps no longer stops
  short of the edges. Animated scenery (water, torches, doors) no longer pops
  in at the outer edge of the wide view. Fishing near either shore of
  Dragon Eye Lake no longer swings the camera to where a black strip shows
  past the edge of the lake.
* Widescreen on OpenGL now draws the room backdrop from the room's own tile
  file instead of the game's four-column tile ring, so every column of the
  wide view is the real art: no void at the edges after a battle, a flee, an
  evolution or a load, and the middle of the screen is pixel-for-pixel what
  it was. The Software renderer keeps the previous method at 16:9 and 16:10,
  which stays clean there and costs nothing extra, and uses the tile file only
  at 21:9 (see below). Netplay keeps the previous method too; HD texture
  packs now use the tile-file backdrop like everything else, and their
  replacements apply to it. **Savestates
  made with widescreen on and with it off are no longer interchangeable**
  (memory-card saves are unaffected), and savestates from earlier builds will
  not load.
* Widescreen offers **21:9 (ultrawide)**, on OpenGL and Software (on
  Software at a price: the backdrop is drawn twice, about 12 ms of CPU per
  frame at the default 4x supersampling). The 36
  smallest rooms are narrower than a 21:9 view and show an even black bar on
  each side (88 px on the three-tile rooms, 24 px on the four-tile ones). HD texture packs work at 21:9: their
  tile replacements apply in the extra width as well as the middle. Nothing
  wider is offered.
* "Shrink your own collision box" now works with 1.5. 1.5 moved the Digimon
  table, so the cheat checks which game is running and writes that game's own
  copy; before, under 1.5 it wrote into an unrelated table.
* Savestates work with 1.5. With any mod that changes the game's code, the
  savestate file name carried a 64-character fingerprint, and in a deeper
  install folder the path passed Windows' 260-character limit, so every save
  failed. The name now carries 16 characters; existing slots are renamed.
* Cheats no longer write to memory before the game has started. 1.5 parks its
  new code where the game later keeps its variables, so a cheat poking that
  spot while the disc was still loading (as "Skip all movies" does by default)
  could have damaged it.
* The setup package is now named `digimon-world-*-windows-x64.zip`.
* The game's mods moved to `mods/preloaded/packages` and are staged by the
  framework. Done the old way, the updated framework replaced this game's
  Widescreen with its generic one (which lacks this game's own plugins and
  repainted rooms) and, on first launch, would have deleted the game's mod sources that
  the setup wizard rebuilds from.
* 1.5's patch contains glyphs from two SIL OFL fonts (Ark Pixel, Cubic 11);
  their licence texts now ship beside it, as the OFL requires.
* The release gate `tools/verify_setup_package.py` fails if anything other than
  a manifest ships under `mods/preloaded/packages`, except the 1.5 patch and
  its two licence files, each checked by hash.

Known at the time:

* With 1.5 ticked, the launcher's memory card preview still shows the normal
  cards. The game itself uses `saves/dw15/`.
* Widescreen: in Birdra Transport a thin black strip still shows right of
  the cage, because the original art stops there. Rooms drawn floating in
  black (the mansion, the caves, the igloo) show more of that black in the
  wider view; that is the game's own art and is left as it is.

---

## 0.3.5

**The cheats are in the Mods panel.**

A player asked where the cheat list was in the launcher. It was not there:
the 57 codes lived in `cheats.txt`, and picking one meant finding that file,
editing it in Notepad and restarting. Nothing on screen said the cheats
existed at all.

Tick **Cheats** in the Mods panel and the whole list appears as checkboxes,
grouped by what they affect -- Battle, Stats, Items, Training, Care, City,
Partner, Movies, Raise sim, Misc. Every code that was in the file is there,
including the one that fills the Item Keeper's bank with 99 of every item.

`cheats.txt` still works and still decides while that feature is off, so
nothing anyone already set up has changed. Turning it on hands the whole list
to the launcher instead, and then the file's own `[x]` marks stop mattering --
including the one on "Skip all movies", which is why that cheat can now be
switched off from the panel like any other.

**The file stays the single source of truth for the codes.** The mod carries
cheat *names*, never addresses: its option list is generated from `cheats.txt`
by a tool that the release process re-runs to prove the two still agree. This
project has shipped a wrong cheat address more than once, and the one thing
worth guaranteeing here was that there is still only one place where an
address is written down.

Worth knowing before ticking it: these are per-frame memory pokes, exactly
like a real GameShark. Several write things the game keeps on the memory
card, so their effects outlive switching the cheat off -- back up your `saves`
folder first. And where a cheat and another mod touch the same value, the
cheat wins, because it rewrites that value every frame.

No other behaviour changed in this release. Widescreen, the expanded roster
and everything else are exactly as they were in 0.3.4.

---

## 0.3.4

**Widescreen: the black band on the right is gone, and the clock hides in one
piece.**

0.3.3 was not the finished article it was described as. Two things were wrong,
both mine, both found by playing rather than by the arrival-frame measurement
the 0.3.2 notes were based on:

* **Walking right opened a black band in the right reveal.** The game keeps
  only four columns of backdrop in video memory, and four columns cannot cover
  a 16:9 view at every camera position -- roughly a third of positions fall
  short by up to 50 pixels. 0.3.2 moved that shortfall from the left edge to
  the right edge and then measured only the left. Now the missing strip is
  drawn: whenever the four columns fall short, the first pixels of the fifth
  column are read from the map data already in memory, uploaded to a spare
  strip of video memory the earlier survey had cleared, and drawn as part of
  the backdrop. Measured on a fine camera sweep: at all 44 stops where the ring
  fell short (deficits of 5 to 50 px, three maps) the right reveal read the
  same 0-3% dark as the stops beside it, and the strip's pixels matched the
  game's own rendering of the same columns to the pixel.
* **The clock hand stayed behind when the clock hid.** Opening a menu or
  changing map slides the clock off the left edge; the fix in 0.3.3 moved the
  dial pieces but not the hand, which is drawn by a different routine, so the
  hand sat alone in the reveal -- and mid-slide the dial pieces came apart.
  The clock now parks as a single widget: one decision per frame, applied to
  every piece, hand included.

The framework grew one primitive for this, `psx_mod_vram_write`, so a game
plugin can upload texture data the game never had a slot for.

**The rest of the HUD got the same treatment.** Having found the clock's hand
by having it reported, we went looking for every other widget the game hides by
sliding it off-screen, rather than waiting to be told about the next one. An
exhaustive scan of the whole game found two more, and cleared everything else:

* **The battle HP and MP bars sat in the right reveal for most of every
  fight.** They are drawn by the battle overlay, which never calls the routine
  the clock fix hooks, so nothing was touching them at all. Their position
  comes from a table of slide positions read by exactly two instructions in the
  game, so moving the parked entry moves the whole bar coherently.
* **The discipline and happiness gauges came apart for one frame** each time a
  menu opened or closed: the frame of the gauge stayed while most of its pips
  jumped. Same cause as the clock, so the same fix.

The enemy's health bars, the finisher gauge and the battle command menu were
checked and are fine. They are noted as fine so nobody re-checks them.

**21:9 is no longer offered for this game.** It was in the list because the
widescreen mod is shared by every game the framework runs, and this one cannot
honour it: 21:9 asks for 120 pixels of extra view per side, and the game's
backdrop streams four tile columns that top out around 96 even with the new
fifth-column patch. Picking it did not give you a wider game, it gave you the
old black edges back. 16:9 and 16:10 both work and 16:10 is now measured too.

**The game no longer fills your drive with crash reports.** A freeze detector
was writing a 106 MB diagnostic file whenever it thought the game had hung —
and on this game it thinks that while the title screen sits idle, so a normal
session could shed half a gigabyte describing a game that was running
perfectly. It had quietly accumulated 24 GB on one machine. The detector still
watches and still reports, it just no longer writes the file unless you ask it
to.

**Linux builds work again.** Two fixes a player sent in and that had never
been applied, despite a reply saying they had: a missing include that only
broke on Linux, and a hard build failure when an optional rewind component is
absent. That one is now a warning — you get a game without the rewind feature
instead of no game at all.
Known and left open: for about 1.2 s after a wild battle the frame is shown
4:3 before widening again. Cosmetic; it is a hysteresis timeout in the
framework's gameplay detector.

Widescreen remains off by default. Turn it on in the launcher's Mods panel.

**The cheat file had codes that did the opposite of what they said.** Every one
was re-checked against the decompiled game, and this time against the code that
READS each value rather than just the address it writes. Seven were wrong and
three were deleted:

* **"Press Square to lay a pile of poop" set every condition flag at once** —
  hungry, injured, sick and more — in a value the game saves to your memory
  card. It now sets only the one it should.
* **"Always have Finish available" made Finish permanently unavailable**, unless
  your Speed was exactly 199. The check is an equality against a target derived
  from Speed, and the code pinned one side of it.
* **"Partner takes no damage" let the first hit through** and then left your
  partner stuck in its hit reaction. It was pinning the recovery timer rather
  than the damage.
* **"Best Happiness" and "Best Discipline" wrote 170** where the game clamps to
  100, and three evolutions test for exactly 100 — so the cheat quietly blocked
  them. **"Have all cards"** wrote 15 of each where the maximum is 9.
* **"All Digimon in city" corrupted your partner's animation** every frame and
  had nothing to do with recruitment. Removed; "Everyone moves to File City"
  does the real thing. **"Speed up the game"** did nothing at all. **"Monochromon
  shop points"** wrote an address nobody had identified, and "Max Merit Points"
  already covers it.
* **"Smaller aggro radius" was renamed.** It shrinks your own collision box,
  which does make enemies notice you later, but also removes your shadow.

The file also now says to back up your saves first, and explains that where a
cheat and a mod touch the same thing, the cheat wins.
**The expanded roster was quietly overwriting story flags. That is fixed.**

The mod recorded "you have raised this Digimon" by setting a numbered flag, and
for the new Digimon those numbers landed on flags the game was already using —
mostly the ones that remember which recruitment announcements Jijimon has given
you. The flags are saved to your memory card, so it stuck, and it went both
ways: raising one of the new Digimon could silently cost you a scene, and
watching a recruitment scene could make the game think you had already raised a
Digimon you had never seen, which nudged your partner away from evolving into
it.

The new Digimon now have flags of their own, in a range nothing else touches.
Found by decoding the game's own scripts and checking every number the mod could
write against every number the scripts use, rather than by assuming the space
above the original range was free. It was not: 88 of the 117 numbers were in
use.

If you have already raised one of the new Digimon on an older version, the
flags it set stay set. Nothing is broken, but those scenes do not come back.

**Two long-standing claims about the expanded roster are now proven rather than
argued.** Neither changes the game; both were listed as unverified since 0.3.0,
and one of them was load-bearing for a soft-lock nobody had ruled out.

* Machinedramon's body really does stream for the new slot. Guest memory is
  searched for fingerprints of the model file taken off the disc, with a
  control beforehand so the check can fail.
* You can reach him by raising, not just by a test harness forcing it. With the
  partner's stats meeting the requirements, the game's own chooser picks him.
  Worth knowing if you go looking: Agumon cannot reach any of the new species.
  Twelve stock Digimon can, Greymon among them.

A third claim is only half closed, and is listed here rather than quietly
carried: the training tables the new Digimon need really are relocated and the
gym really does read them from the new location, checked with the training code
loaded and a new Digimon as the partner. A full training session with one has
still not been driven end to end. Nothing suggests it is broken; it is simply
not proven, and the difference matters.

---

## 0.3.3

**Fixes the clock hand coming off the clock in widescreen.**

In 0.3.2 the needle floated away from the clock face and sat by itself to the
right of it. Reported by two players within hours of release.

The cause was mine, and it was worse than the needle: the code that keeps
hidden HUD pieces hidden was reading positions as if they were measured from
the left edge of the screen, when the game measures them from the centre. So
anything on the left half of the screen looked to it like it had been hidden
off-screen, and got shoved 53 pixels left. The clock face moved; the needle is
drawn by a different routine and stayed where it was.

Nothing else in 0.3.2 changes. Widescreen coverage is unaffected — still
measured at 20 of 20 maps fully covered.

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
slot. Twenty-one Rookies, twenty-four Champions and seven Ultimates; six of
them have no evolution path in, and one of those, Panjyamon, is reached with
the Noble Mane item instead.

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

## Shipped but doing nothing

Work that is in the package and deliberately inert, so nothing here is a
surprise if you go looking:

* **Native evolution engine** (`dw_evolution.c`, `dw.vanilla.evolution`).
  Groundwork for removing the evolution system's structural limits — six paths
  per Digimon, ids below 128, the 62-row table — by replacing the chooser
  functions with native code. One function is wired and the mechanism is built,
  but that hook has not yet been observed running. The package ships in the
  zip, off by default, and changes nothing when enabled either.
* **Widescreen UI counter-squash.** Built and dormant; it does not work, and
  under native-wide it has nothing to do anyway.

## What is not verified

**Closed in 0.3.4**, and left here because the doubt was published: every
roster test used to *force* an evolution directly, bypassing the game's own
requirement scoring, so all 52 species were proven to instantiate and stay
stable while nothing proved that raising a partner a particular way actually
yields the Digimon the tables promise. `tools/verify_roster_evolution.py` now
shows the game's own chooser selecting a roster species from requirements,
with the harness never writing the target.

What is still open is narrower and is listed in the 0.3.4 entry: a full
training session with one of the new species has not been driven end to end,
and balance across all 52 is playtesting nobody has done.
