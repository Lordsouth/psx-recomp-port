@echo off
rem Runs the game and saves everything it prints to game-log.txt next to this
rem file. Send that file when reporting a problem -- the first ~30 lines say
rem which disc loaded, how the game is being paced, which mods applied and
rem which cheats are on, which is usually enough to identify the cause.
cd /d "%~dp0"
echo Running the game. Play until you see the problem, then close the game.
echo Output is being written to game-log.txt
echo.
if exist "build-release\Digimon_World_Recompiled.exe" (
    "build-release\Digimon_World_Recompiled.exe" --game game.toml > game-log.txt 2>&1
) else (
    "Digimon_World_Recompiled.exe" > game-log.txt 2>&1
)
echo.
echo Saved game-log.txt in this folder. Please attach it to your report.
pause
