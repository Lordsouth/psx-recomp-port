cmake_minimum_required(VERSION 3.20)
set(PSXRECOMP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/psxrecomp" CACHE PATH
    "Path to psxrecomp")
if(NOT EXISTS "${PSXRECOMP_ROOT}/runtime/runtime.cmake")
    message(FATAL_ERROR
        "PSXRecomp runtime is missing: ${PSXRECOMP_ROOT}/runtime/runtime.cmake\n"
        "This project expects the psxrecomp framework checkout at ${PSXRECOMP_ROOT}.\n"
        "Run: git submodule update --init --recursive\n"
        "If psxrecomp/ is not a submodule, set -DPSXRECOMP_ROOT=<path-to-psxrecomp>.")
endif()

project(DigimonWorldVanilla LANGUAGES C CXX)

# --- Opt-in product surfaces (OFF by default in the framework) -------------
# recomp-ui is cloned at ./recomp-ui, so PSX_RECOMP_UI stays at its default ON
# and runtime.cmake auto-detects RECOMP_UI_ROOT. This is what draws the launcher
# and, with RECOMP_UI_ENABLE_MODS (framework-seeded ON), the Mods panel that
# toggles mods/preloaded/packages/dw.vanilla.trainer.
# Netplay — single-player title, stays off:
# if(EXISTS "${PSXRECOMP_ROOT}/lib/recomp-net/CMakeLists.txt")
#     set(PSX_NETPLAY ON CACHE BOOL "…" FORCE)
# endif()
# First-run setup wizard — enable after testing:
# set(PSX_SETUP_WIZARD ON CACHE BOOL "…" FORCE)
include("${PSXRECOMP_ROOT}/runtime/runtime.cmake")

# codegen_setup.c implements psx_game_codegen_forward_if_built by calling into
# psxrecomp/host/psxrecomp_codegen_host.c, and runtime.cmake only compiles that
# host for PSX_RECOMP_UI builds. Offering it on a UI-less build (e.g. the
# TCP-drivable debug build) links a caller with no callee.
# dw_ws_backdrop.c (deep-widescreen backdrop alignment) uses only the always-
# compiled mod runtime, so it is unconditional.
set(_dw_codegen_setup
    CODEGEN_SETUP_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/dw_ws_backdrop.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_roster.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_evolution.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_ws_camera.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_ws_sliver.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_ws_world.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_ws_weather.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_roster_flags.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_cheats.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_dw15.c"
                          "${CMAKE_CURRENT_SOURCE_DIR}/dw_newmaps.c")
if(PSX_RECOMP_UI)
    list(APPEND _dw_codegen_setup "${CMAKE_CURRENT_SOURCE_DIR}/codegen_setup.c")
endif()

psxrecomp_add_game_runtime(psx-runtime
    # ENABLE_NETPLAY_IF_PRESENT
    # NETPLAY_LOBBY_URL "ws://netplay.retcomm.net:8765"
    # ENABLE_SETUP_WIZARD
    WINDOW_TITLE "Digimon World Recompiled"
    DEFAULT_GAME_CONFIG_PATH "game.toml"
    MAX_PLAYERS 1
    # LAUNCHER_BOXART "${CMAKE_CURRENT_SOURCE_DIR}/launcher_assets/img/boxart.tga"
    # APP_ICON "${CMAKE_CURRENT_SOURCE_DIR}/assets/psxrecomp.ico"
    GEN_MARKER "generated/SLUS_010.32_dispatch.c"
    GEN_FULL_GLOB "generated/SLUS_010.32_full_*.c"
    # mods/preloaded/packages is staged into <exe>/mods/bundled after the framework
    # builtins, so the game's psx.enhancement.widescreen (no 21:9) overrides
    # the builtin of the same id and version.
    PRELOADED_MODS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/mods/preloaded"
    ${_dw_codegen_setup}
)

# Stage runtime configs next to the exe for direct build/ launches. Only copy
# files that exist — never fail POST_BUILD when game_options.toml is absent.
foreach(_PSX_RUNTIME_CONFIG game.toml game_options.toml)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${_PSX_RUNTIME_CONFIG}")
        add_custom_command(TARGET psx-runtime POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/${_PSX_RUNTIME_CONFIG}"
                "$<TARGET_FILE_DIR:psx-runtime>/${_PSX_RUNTIME_CONFIG}"
            COMMENT "Staging ${_PSX_RUNTIME_CONFIG}"
            VERBATIM)
    endif()
endforeach()
