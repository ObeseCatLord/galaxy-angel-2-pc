// Assets the launcher derives from the player's own discs once, after import.
#pragma once

#include "library.h"

#include <filesystem>
#include <string>

namespace ga2
{
    // <data>/assets/skybox/<program>.png: the game's space sky as an
    // equirectangular panorama (VN side fill, VR), rendered from the disc's
    // sky models by tools/extract_skybox.py (needs Python 3 with Pillow).
    std::filesystem::path skyboxFile(const Library &library, const KnownDisc &disc);
    // Renders it if missing; false (with a reason) when it cannot.
    bool ensureSkybox(const Library &library, const KnownDisc &disc, const std::filesystem::path &engineDir,
                      std::string &message);
}
