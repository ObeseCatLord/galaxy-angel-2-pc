# Added to the upstream build by a deferred project include; upstream stays clean.
if(NOT TARGET ps2_runtime)
    message(FATAL_ERROR "Use tools/ga2.py build to configure with PS2Recomp")
endif()
set(GA2_PRIVATE_ROOT "" CACHE PATH "Local game inputs and generated recomp code")
set(GA2_GENERATED_ROOT "${GA2_PRIVATE_ROOT}/generated" CACHE PATH "Generated recomp code root")
# The release version (VERSION at the top of the repository).
file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/../VERSION" GA2_VERSION LIMIT_COUNT 1)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_LIST_DIR}/../VERSION")
set(GA2_DISC_IDS "zettai;mugen-1;mugen-2;eigou-1;eigou-2" CACHE STRING "Disc builds to compile")
# Generated VU1 programs (ga2_vu1_gen), shared by every engine: the GA2 discs
# share their microcode, and programs are selected by code, not disc. An
# OBJECT library, since each program registers itself from a static
# initializer. ga2_vu1_check (the replay harness with them) checks them against GA2_VU1CAP captures.
file(GLOB ga2_vu1_sources CONFIGURE_DEPENDS "${GA2_GENERATED_ROOT}/vu1/vu1*.cpp")
if(ga2_vu1_sources)
    add_library(ga2_vu1_programs OBJECT ${ga2_vu1_sources})
    target_compile_features(ga2_vu1_programs PRIVATE cxx_std_20)
    target_link_libraries(ga2_vu1_programs PRIVATE ps2_runtime)
    add_executable(ga2_vu1_check "${CMAKE_SOURCE_DIR}/ps2xTest/src/ga2_vu1_replay.cpp")
    target_compile_features(ga2_vu1_check PRIVATE cxx_std_20)
    target_link_libraries(ga2_vu1_check PRIVATE ps2_runtime ga2_vu1_programs)
    set_target_properties(ga2_vu1_check PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/ga2/bin")
endif()

foreach(disc IN LISTS GA2_DISC_IDS)
    set(generated "${GA2_GENERATED_ROOT}/${disc}")
    if(NOT EXISTS "${generated}/register_functions.cpp")
        message(STATUS "GA2: ${disc} not generated yet")
        continue()
    endif()
    # sources.txt (written by ga2.py generate) lists the files: re-globbing
    # ~35k generated files on every build costs seconds per ninja run.
    if(EXISTS "${generated}/sources.txt")
        file(STRINGS "${generated}/sources.txt" generated_names)
        list(TRANSFORM generated_names PREPEND "${generated}/" OUTPUT_VARIABLE generated_sources)
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${generated}/sources.txt")
    else()
        file(GLOB generated_sources CONFIGURE_DEPENDS "${generated}/*.cpp")
    endif()
    string(REPLACE "-" "_" target_id "${disc}")
    add_executable(ga2_${target_id}
        "${CMAKE_SOURCE_DIR}/ps2xRuntime/src/main.cpp"
        ${generated_sources}
    )
    # Per-disc SDK bindings: compat/<target_id>_*.cpp (zettai_*, mugen_1_*, ...).
    file(GLOB _ga2_compat CONFIGURE_DEPENDS "${CMAKE_CURRENT_LIST_DIR}/compat/${target_id}_*.cpp")
    if(NOT disc STREQUAL "zettai")
        list(APPEND _ga2_compat "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_sdk.cpp")
    endif()
    # Rendering features shared by all discs (hor+ widescreen, ...).
    list(APPEND _ga2_compat "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_features.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_framerate.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_battlepointer.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_cheats.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_text.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_exetext.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_skip.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_vrstereo.cpp"
                            "${CMAKE_CURRENT_LIST_DIR}/compat/ga2_vrscene.cpp")
    if(_ga2_compat)
        target_sources(ga2_${target_id} PRIVATE ${_ga2_compat})
        target_include_directories(ga2_${target_id} PRIVATE "${CMAKE_CURRENT_LIST_DIR}/compat"
            "${CMAKE_SOURCE_DIR}/ps2xRuntime/src/lib/Kernel")
        set_source_files_properties(${_ga2_compat} PROPERTIES SKIP_UNITY_BUILD_INCLUSION ON)
        # The battle pointer reads the window mouse (ps2_mouse.h is a runtime-internal header).
        set_source_files_properties("${CMAKE_CURRENT_LIST_DIR}/compat/ga2_battlepointer.cpp" PROPERTIES
            INCLUDE_DIRECTORIES "${CMAKE_SOURCE_DIR}/ps2xRuntime/src/lib")
    endif()
    target_compile_features(ga2_${target_id} PRIVATE cxx_std_20)
    # Line tables only for recompiled code: full DWARF for ~35k functions makes
    # links slow and binaries huge; profiles still resolve function and line.
    # (Target-wide: per-source options would drop files out of unity batches.)
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        target_compile_options(ga2_${target_id} PRIVATE -gline-tables-only)
    endif()
    # Shared runtime: the runtime reads the function table from the executable.
    ps2x_export_program_table(ga2_${target_id})
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64|i.86)$" AND
       CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(ga2_${target_id} PRIVATE -msse4.1)
    endif()
    target_include_directories(ga2_${target_id} BEFORE PRIVATE "${generated}")
    target_link_libraries(ga2_${target_id} PRIVATE ps2_runtime)
    # Small bounded batches amortize runtime headers without one giant TU.
    set_target_properties(ga2_${target_id} PROPERTIES UNITY_BUILD ON UNITY_BUILD_BATCH_SIZE 64)
    target_precompile_headers(ga2_${target_id} PRIVATE
        "${CMAKE_SOURCE_DIR}/ps2xRuntime/include/ps2_runtime_macros.h"
        "${CMAKE_SOURCE_DIR}/ps2xRuntime/include/ps2_runtime.h"
        "${generated}/ps2_recompiled_functions.h"
        "${generated}/ps2_recompiled_stubs.h"
    )
    # The runtime library is found next to the engine (a packaged folder) or,
    # in the build tree, by a path relative to it: no absolute build paths.
    set_target_properties(ga2_${target_id} PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/ga2/bin"
        BUILD_RPATH "$ORIGIN"
        BUILD_RPATH_USE_ORIGIN ON
    )
    if(COMMAND ps2x_stage_ffmpeg_runtime_dlls)
        ps2x_stage_ffmpeg_runtime_dlls(ga2_${target_id})
    endif()

    if(TARGET ga2_vu1_programs)
        target_link_libraries(ga2_${target_id} PRIVATE ga2_vu1_programs)
    endif()
endforeach()

# Diagnostic only: original IRX execution without the EE recompiled program.
add_executable(ga2_iop_probe "${CMAKE_CURRENT_LIST_DIR}/iop_probe.cpp")
target_link_libraries(ga2_iop_probe PRIVATE ps2_iop)
set_target_properties(ga2_iop_probe PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/ga2/bin")

# Diagnostic only: original ISO file/extent parity through the native adapter.
add_executable(ga2_disc_probe "${CMAKE_CURRENT_LIST_DIR}/disc_probe.cpp"
    "${CMAKE_SOURCE_DIR}/ps2xTest/src/test_function_table.cpp")
target_include_directories(ga2_disc_probe PRIVATE
    "${CMAKE_SOURCE_DIR}/ps2xRuntime/src/lib"
    "${CMAKE_SOURCE_DIR}/ps2xIOP/src")
target_link_libraries(ga2_disc_probe PRIVATE ps2_runtime)
set_target_properties(ga2_disc_probe PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/ga2/bin")

# Player-facing launcher: imports disc images, starts the per-disc engines.
# Filter shaders (Anime4K etc.) next to the engines, as in a packaged folder.
file(COPY "${CMAKE_CURRENT_LIST_DIR}/shaders/" DESTINATION "${CMAKE_BINARY_DIR}/ga2/bin/shaders")
# Button-prompt icons (the port's own art) and where the games show buttons.
file(COPY "${CMAKE_CURRENT_LIST_DIR}/buttons/" DESTINATION "${CMAKE_BINARY_DIR}/ga2/bin/buttons")
# The UI font (menus, launcher): the launcher and the engines look in <app>/fonts.
file(COPY "${CMAKE_CURRENT_LIST_DIR}/fonts/" DESTINATION "${CMAKE_BINARY_DIR}/ga2/bin/fonts")
# The launcher renders the space skybox from the player's discs with this tool.
file(COPY "${CMAKE_CURRENT_LIST_DIR}/../tools/assets/extract_skybox.py" DESTINATION "${CMAKE_BINARY_DIR}/ga2/bin/tools")

# The launcher's program identities (disc id, serial, program SHA-256) come from
# configs/verified-builds.json, the file the tools use, not a second copy.
set(_ga2_builds_json "${CMAKE_CURRENT_LIST_DIR}/../configs/verified-builds.json")
file(READ "${_ga2_builds_json}" _ga2_builds)
string(JSON _ga2_build_count LENGTH "${_ga2_builds}" builds)
set(_ga2_build_rows "")
math(EXPR _ga2_build_last "${_ga2_build_count} - 1")
foreach(_i RANGE ${_ga2_build_last})
    string(JSON _id GET "${_ga2_builds}" builds ${_i} disc_id)
    string(JSON _serial GET "${_ga2_builds}" builds ${_i} serial)
    string(JSON _sha GET "${_ga2_builds}" builds ${_i} elf_sha256)
    string(APPEND _ga2_build_rows "GA2_VERIFIED_BUILD(\"${_id}\", \"${_serial}\", \"${_sha}\")\n")
endforeach()
file(CONFIGURE OUTPUT "${CMAKE_BINARY_DIR}/ga2/generated/ga2_verified_builds.inc" CONTENT "${_ga2_build_rows}" @ONLY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_ga2_builds_json}")

add_executable(ga2
    "${CMAKE_CURRENT_LIST_DIR}/launcher/main.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/launcher/library.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/launcher/iso9660.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/launcher/process.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/launcher/settings.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/launcher/assets.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/launcher/mods.cpp")
target_compile_features(ga2 PRIVATE cxx_std_20)
target_compile_definitions(ga2 PRIVATE "GA2_VERSION=\"${GA2_VERSION}\"")
target_link_libraries(ga2 PRIVATE raylib nlohmann_json::nlohmann_json)
target_include_directories(ga2 PRIVATE "${CMAKE_SOURCE_DIR}/ps2xRuntime/include" "${CMAKE_BINARY_DIR}/ga2/generated")
set_target_properties(ga2 PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/ga2/bin")
if(PS2X_RUNTIME_SHARED)
    ps2x_export_program_table(ga2_disc_probe)
endif()
