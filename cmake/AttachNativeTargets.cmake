# This runs at the top-level project() before upstream targets exist. Defer the
# addition until they do. Capture the source path now, rather than at defer time.
get_filename_component(_ga2_native "${CMAKE_CURRENT_LIST_DIR}/../native" ABSOLUTE)
cmake_language(EVAL CODE
    "cmake_language(DEFER DIRECTORY \"${CMAKE_SOURCE_DIR}\" CALL include \"${_ga2_native}/CMakeLists.txt\")")

# x86: the runtime and the generated code use SSE4.1 intrinsics (PS2 128-bit
# registers). Some compilers enable it by default and some do not (GCC, older
# clang), so ask for it for every target, before the upstream ones exist.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64)$" AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    add_compile_options(-msse4.1)
endif()
