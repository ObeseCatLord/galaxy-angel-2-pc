# This runs at the top-level project() before upstream targets exist. Defer the
# addition until they do. Capture the source path now, rather than at defer time.
get_filename_component(_ga2_native "${CMAKE_CURRENT_LIST_DIR}/../native" ABSOLUTE)
cmake_language(EVAL CODE
    "cmake_language(DEFER DIRECTORY \"${CMAKE_SOURCE_DIR}\" CALL include \"${_ga2_native}/CMakeLists.txt\")")
