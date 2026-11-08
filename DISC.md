# The disc you need

This project needs **Digimon World (USA)** — the North American PlayStation 1
release, serial `SLUS-01032`.

You have to supply it yourself. Nothing game-related is included in the
download, and none is available here.

## What the files should look like

You need **two files that sit next to each other**:

| File | What it is | Size |
|---|---|---|
| `Digimon World (USA).bin` | the disc's data | about 364 MB |
| `Digimon World (USA).cue` | a small text file describing the `.bin` | under 1 KB |

When setup asks for your disc, **pick the `.cue`** — the tiny one. It tells the
game where the `.bin` is. If you only have a `.bin` and no `.cue`, setup can't
read it.

Some copies come as a `.7z` or `.zip`. Extract it first; you need the actual
`.bin` and `.cue` on disk.

## Making these from your own disc

If you own the disc and want to read it yourself, you need a PC with a CD/DVD
drive and disc-imaging software that can output **BIN/CUE**. That format
matters: a plain `.iso` drops information PlayStation discs rely on and will not
work here.

## Checking your copy is the right one

Setup verifies this for you and will say so if something is off, rather than
building something broken. To check it yourself:

| | |
|---|---|
| `.bin` size | exactly 381,771,936 bytes |
| `.bin` SHA-1 | `5611645da66183e30d11fb6c11a2784ff47e1fdb` |
| game executable | `SLUS_010.32`, SHA-1 `31ec666b987d5b007dec40eecc4092d27ed9085b` |

To get a SHA-1 on Windows, open PowerShell in the folder holding the file and
run:

```
Get-FileHash "Digimon World (USA).bin" -Algorithm SHA1
```

A different size or hash usually means a different regional release or a
re-dump. It may still work, but it has not been tested.

**Hidden Glade** and **Digimon World 1.5** run only on exactly this `.bin`: on any
other image the game will not start with them ticked. Untick them to get back in.

## Other versions

**PAL (Europe) and NTSC-J (Japan) are not supported.** Every mod and patch in
this project is built against exact addresses inside the USA executable above,
and those do not line up in other releases.
