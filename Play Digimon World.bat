@echo off
rem Launch Digimon World Recompiled from the project root so game.toml,
rem saves\ and the disc path all resolve. Double-click me.
rem
rem MinGW is prepended to PATH so the runtime can find gcc and compile captured
rem overlays to native DLLs in the background as you play (see [runtime]
rem overlay_autocompile_cmd in game.toml). Without gcc the game still runs and
rem is still correct -- overlay code just stays in the interpreter.
cd /d "%~dp0"
set "PATH=C:\msys64\mingw64\bin;%PATH%"
rem The freeze detector writes a ~109 MB state snapshot per suspected wedge.
rem On this title its "spin_freeze" heuristic false-positives while the game
rem idles in attract mode: a 24-minute soak produced five dumps (~500 MB) while
rem the game ran at a steady 60 fps and stayed fully responsive. Off for normal
rem play; raise or remove it if you ever need the diagnostics.
set "PSX_FREEZE_DUMP_MAX=0"

start "" "build-release\Digimon_World_Recompiled.exe" --game game.toml
