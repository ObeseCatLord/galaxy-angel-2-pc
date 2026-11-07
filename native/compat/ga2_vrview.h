#pragma once

#include <cstdint>

// The game camera's own view matrix (before the VR head pose is multiplied
// in), captured by the stereo hook (ga2_vrstereo.cpp) and read by the battle
// pointer (ga2_battlepointer.cpp). EE thread only.
namespace ga2_vrview
{
    // Row-vector convention, row-major (v' = v M, translation in 12..14):
    // game world (map units) -> game camera space. False until a stereo frame
    // has set one; `serial` counts captures.
    bool latest(float view[16], uint64_t &serial);
    void capture(const float view[16]);
}
