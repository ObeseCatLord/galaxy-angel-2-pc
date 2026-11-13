# Galaxy Angel II PC installer and updater: a standalone program (one file per platform, no runtime libraries
# beyond the system's), built on its own:
#   cmake -S native/installer -B build/installer -DCMAKE_BUILD_TYPE=Release && cmake --build build/installer
# Windows: cross-compile with -DCMAKE_TOOLCHAIN_FILE=tools/mingw-w64-toolchain.cmake (or any MinGW-w64).
# raylib and nlohmann/json come with FetchContent (FETCHCONTENT_SOURCE_DIR_RAYLIB / _NLOHMANN_JSON point at local
# copies). GA2_INSTALLER_REPO is the GitHub project whose releases it installs.
cmake_minimum_required(VERSION 3.16)
project(ga2_installer C CXX)

set(GA2_INSTALLER_REPO "ObeseCatLord/galaxy-angel-2-pc" CACHE STRING "GitHub owner/name of the releases")
file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/VERSION" GA2_INSTALLER_VERSION LIMIT_COUNT 1)

include(FetchContent)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_GAMES OFF CACHE BOOL "" FORCE)
set(CUSTOMIZE_BUILD ON CACHE BOOL "" FORCE)
set(SUPPORT_MODULE_RAUDIO OFF CACHE BOOL "" FORCE)
set(SUPPORT_MODULE_RMODELS OFF CACHE BOOL "" FORCE)
set(SUPPORT_COMPRESSION_API ON CACHE BOOL "" FORCE) # its deflate decoder unpacks the Windows zip
FetchContent_Declare(raylib GIT_REPOSITORY https://github.com/raysan5/raylib.git GIT_TAG 5.5 GIT_SHALLOW TRUE)
FetchContent_Declare(nlohmann_json GIT_REPOSITORY https://github.com/nlohmann/json.git GIT_TAG v3.11.3 GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(raylib nlohmann_json)

# The font (a cut of the UI font, tools/installer_font.py) inside the program.
set(_font "${CMAKE_CURRENT_LIST_DIR}/installer-font.otf")
file(READ "${_font}" _font_hex HEX)
file(SIZE "${_font}" _font_size)
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," _font_bytes "${_font_hex}")
file(CONFIGURE OUTPUT "${CMAKE_BINARY_DIR}/generated/installer_font.h"
     CONTENT "#pragma once\nstatic const unsigned char kInstallerFont[] = {${_font_bytes}};\nstatic const unsigned long kInstallerFontSize = ${_font_size}UL;\n")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_font}" "${CMAKE_CURRENT_LIST_DIR}/VERSION")

add_executable(ga2-installer main.cpp installer.cpp platform.cpp sha256.cpp)
target_compile_features(ga2-installer PRIVATE cxx_std_20)
target_compile_definitions(ga2-installer PRIVATE "GA2_INSTALLER_VERSION=\"${GA2_INSTALLER_VERSION}\""
                                                 "GA2_INSTALLER_REPO=\"${GA2_INSTALLER_REPO}\"")
target_include_directories(ga2-installer PRIVATE "${CMAKE_BINARY_DIR}/generated")
target_link_libraries(ga2-installer PRIVATE raylib nlohmann_json::nlohmann_json)
if(WIN32)
    string(REPLACE "." "," _version_commas "${GA2_INSTALLER_VERSION},0")
    configure_file(installer.rc.in "${CMAKE_BINARY_DIR}/generated/installer.rc" @ONLY)
    target_sources(ga2-installer PRIVATE "${CMAKE_BINARY_DIR}/generated/installer.rc")
    set_target_properties(ga2-installer PROPERTIES WIN32_EXECUTABLE ON)
    target_compile_definitions(ga2-installer PRIVATE UNICODE _UNICODE)
    target_link_libraries(ga2-installer PRIVATE winhttp ole32 shell32 uuid advapi32 -static)
else()
    find_package(Threads REQUIRED)
    target_link_libraries(ga2-installer PRIVATE Threads::Threads -static-libstdc++ -static-libgcc)
endif()
