// Declarative mods (docs/MODDING.md): <data>/mods/<id>/mod.json plus
//   files/<disc program>/<disc path>   disc files to replace or add
//   textures/**/<hash>[_<w>x<h>].png   texture replacements (any resolution)
//   font/metrics.bin                   half-width font metrics (ga2font.py pack)
// A mod with "language" in its mod.json is a language pack: it is used
// exactly when that language is chosen in Options. Other mods are switched
// on and ordered on the Mods screen (<data>/mods.ini; first = highest
// priority). Loose files in <data>/mods/<disc program>/ still work, below
// every mod.
#pragma once

#include <filesystem>
#include <string>
#include <vector>

namespace ga2
{
    struct ModInfo
    {
        std::string id; // folder name
        std::string name, version, author, description, language;
        std::filesystem::path dir;
        bool hasFiles = false, hasTextures = false, hasFontMetrics = false;
        std::string error; // mod.json could not be read
    };

    class Mods
    {
    public:
        explicit Mods(std::filesystem::path dataDir);
        void rescan();

        // In priority order (highest first).
        const std::vector<ModInfo> &list() const { return m_mods; }
        bool enabled(const std::string &id) const;
        void setEnabled(const std::string &id, bool on);
        void move(const std::string &id, int delta);
        void save() const;

        // The mods in effect for a language (enabled mods, plus the language
        // packs of that language), highest priority first.
        std::vector<const ModInfo *> active(const std::string &language) const;
        // GA2_MODS, GA2_TEXTURE_PACKS and GA2_FONT_METRICS for the engines.
        void applyToEnvironment(const std::string &language) const;

    private:
        std::filesystem::path m_dataDir;
        std::vector<ModInfo> m_mods;
        std::vector<std::string> m_enabled; // ids, in priority order
    };
}
