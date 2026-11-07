#pragma once

#include <cstdint>

// Where the 2D layer's items end up in a hor+ frame (ga2_features.cpp), for
// game hooks that must agree with what the player sees (the battle pointer).
// Positions are 2D layer x in the 640-wide frame; markerX returns x unchanged
// when no wide 3D frame is shown.
namespace ga2_hud
{
    // An item centred at frame x with the given half width, as the game
    // places it, is drawn centred at the returned x: squeezed about the screen
    // centre, or toward the left or right edge for an item near it.
    float markerX(float x, float halfWidth);
    // A 2D layer item (by its pointer) that always squeezes about the screen
    // centre, wherever it is put (the tactical map's cursor ring, which follows
    // the mouse; the edge rules would move it away from the pointer).
    void keepCentred(uint32_t item);
}
