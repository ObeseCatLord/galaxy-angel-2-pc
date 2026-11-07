# Cross-compile the Windows build: clang (the same compiler as the Linux build,
# so floating-point code generation matches) targeting mingw-w64, linked fully
# static so the executables need no MinGW runtime DLLs.
#   GA2_RUNTIME_BUILD=<dir> tools/configure_runtime.sh \
#     -DCMAKE_TOOLCHAIN_FILE=$PWD/tools/mingw-w64-toolchain.cmake -DPS2X_RUNTIME_SHARED=OFF \
#     -DPS2X_BUILD_TEST=OFF -DPS2X_IOP_BUILD_TESTS=OFF
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(GA2_MINGW_TRIPLE x86_64-w64-mingw32)
set(CMAKE_C_COMPILER clang)
set(CMAKE_C_COMPILER_TARGET ${GA2_MINGW_TRIPLE})
set(CMAKE_CXX_COMPILER clang++)
set(CMAKE_CXX_COMPILER_TARGET ${GA2_MINGW_TRIPLE})
set(CMAKE_RC_COMPILER ${GA2_MINGW_TRIPLE}-windres)
set(CMAKE_AR llvm-ar)
set(CMAKE_RANLIB llvm-ranlib)
set(CMAKE_FIND_ROOT_PATH /usr/${GA2_MINGW_TRIPLE})
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_EXE_LINKER_FLAGS_INIT "-fuse-ld=lld -static")
# The usual Windows system libraries, then a static winpthread for libstdc++.
set(GA2_MINGW_LIBS "-lkernel32 -luser32 -lgdi32 -lwinspool -lshell32 -lole32 -loleaut32 -luuid -lcomdlg32 -ladvapi32 -Wl,-Bstatic -lpthread")
set(CMAKE_CXX_STANDARD_LIBRARIES "${GA2_MINGW_LIBS}")
set(CMAKE_C_STANDARD_LIBRARIES "${GA2_MINGW_LIBS}")
