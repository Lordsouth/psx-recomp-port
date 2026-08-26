# Which disc this needs

**Digimon World (USA)** — SLUS-01032, NTSC-U. One track, one `.bin` + `.cue`.

You must supply your own dump. Nothing game-related ships in this download:
the setup step reads your disc, generates the port on your machine, and builds
it there.

## Verifying your dump

| | |
|---|---|
| `.bin` size | 381,771,936 bytes |
| `.bin` sha1 | `5611645da66183e30d11fb6c11a2784ff47e1fdb` |
| executable  | `SLUS_010.32`, sha1 `31ec666b987d5b007dec40eecc4092d27ed9085b` |

If your dump differs, the setup step will tell you rather than producing a
broken build. A `.cue` alongside the `.bin` is required — pick the `.cue` when
the wizard asks for your disc.

Other regions (PAL, NTSC-J) are **not** supported by this project: the
addresses every mod is built against come from the USA executable above.
