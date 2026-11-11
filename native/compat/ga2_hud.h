#pragma once

#include <cstdint>

// Where the 2D layer's items end up in a hor+ frame (ga2_features.cpp), for
// game hooks that must agree with what the player sees (the battle pointer).
// Positions are 2D layer x in the 640-wide frame; markerX returns x unchanged
// when no wide 3D frame is shown.
//
// The tactical map's unit icons are projected by the game with the widened
// camera, so their x is already where the unit is in the wide frame: items
// centred on a unit's icon (its ring, face, frame and star) are drawn where
// the game put them, never squeezed or moved to an edge.
namespace ga2_hud
{
    // An item centred at frame x with the given half width, as the game
    // places it, is drawn centred at the returned x: squeezed about the screen
    // centre, or toward the left or right edge for an item near it.
    float markerX(float x, float halfWidth);
    // The unit icons' centres (frame x, y) of the tactical map, published by
    // the battle pointer on each map update; items centred on one stay put.
    void setUnitIcons(const float *xy, int count);
    // A 2D layer item (by its pointer) that always squeezes about the screen
    // centre, wherever it is put (the tactical map's cursor ring, which follows
    // the mouse; the edge rules would move it away from the pointer).
    void keepCentred(uint32_t item);
}
