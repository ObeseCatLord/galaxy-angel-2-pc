#include "mods.h"
#include "process.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cstdlib>
#include <fstream>

namespace ga2
{
    namespace
    {
        void setEnv(const char *name, const std::string &value) { setEnvironment(name, value); }

        std::filesystem::path configFile(const std::filesystem::path &dataDir) { return dataDir / "mods.ini"; }
    }

    Mods::Mods(std::filesystem::path dataDir) : m_dataDir(std::move(dataDir))
    {
        std::ifstream in(configFile(m_dataDir));
        for (std::string line; std::getline(in, line);)
            if (line.rfind("enabled=", 0) == 0 && line.size() > 8)
                m_enabled.push_back(line.substr(8));
        rescan();
    }

    void Mods::rescan()
    {
        m_mods.clear();
        std::error_code ec;
        for (const auto &entry : std::filesystem::directory_iterator(m_dataDir / "mods", ec))
        {
            const auto manifest = entry.path() / "mod.json";
            if (!entry.is_directory(ec) || !std::filesystem::exists(manifest, ec))
                continue;
            ModInfo mod;
            mod.id = entry.path().filename().string();
            mod.dir = entry.path();
            mod.name = mod.id;
            try
            {
                std::ifstream in(manifest);
                const auto doc = nlohmann::json::parse(in);
                mod.name = doc.value("name", mod.id);
                mod.version = doc.value("version", "");
                mod.author = doc.value("author", "");
                mod.description = doc.value("description", "");
                mod.language = doc.value("language", "");
            }
            catch (const std::exception &error)
            {
                mod.error = error.what();
            }
            mod.hasFiles = std::filesystem::is_directory(mod.dir / "files", ec);
            mod.hasTextures = std::filesystem::is_directory(mod.dir / "textures", ec);
            mod.hasFontMetrics = std::filesystem::is_regular_file(mod.dir / "font" / "metrics.bin", ec);
            m_mods.push_back(std::move(mod));
        }
        // Enabled mods in their saved order first, then the rest by name.
        auto rank = [&](const ModInfo &m) {
            const auto it = std::find(m_enabled.begin(), m_enabled.end(), m.id);
            return it == m_enabled.end() ? m_enabled.size() : size_t(it - m_enabled.begin());
        };
        std::stable_sort(m_mods.begin(), m_mods.end(), [&](const ModInfo &a, const ModInfo &b) {
            const size_t ra = rank(a), rb = rank(b);
            return ra != rb ? ra < rb : a.name < b.name;
        });
    }

    bool Mods::enabled(const std::string &id) const
    {
        return std::find(m_enabled.begin(), m_enabled.end(), id) != m_enabled.end();
    }

    void Mods::setEnabled(const std::string &id, bool on)
    {
        m_enabled.erase(std::remove(m_enabled.begin(), m_enabled.end(), id), m_enabled.end());
        if (on)
        {
            // Keep the list order: insert where the mod sits in the list.
            std::vector<std::string> ordered;
            for (const ModInfo &m : m_mods)
                if (m.id == id || enabled(m.id))
                    ordered.push_back(m.id);
            m_enabled = ordered;
        }
    }

    void Mods::move(const std::string &id, int delta)
    {
        const auto it = std::find_if(m_mods.begin(), m_mods.end(), [&](const ModInfo &m) { return m.id == id; });
        if (it == m_mods.end())
            return;
        const long from = long(it - m_mods.begin()), to = std::clamp(from + delta, 0L, long(m_mods.size()) - 1);
        if (from == to)
            return;
        ModInfo moved = std::move(m_mods[size_t(from)]);
        m_mods.erase(m_mods.begin() + from);
        m_mods.insert(m_mods.begin() + to, std::move(moved));
        std::vector<std::string> ordered;
        for (const ModInfo &m : m_mods)
            if (enabled(m.id))
                ordered.push_back(m.id);
        m_enabled = ordered;
    }

    void Mods::save() const
    {
        std::ofstream out(configFile(m_dataDir), std::ios::trunc);
        for (const std::string &id : m_enabled)
            out << "enabled=" << id << '\n';
    }

    std::vector<const ModInfo *> Mods::active(const std::string &language) const
    {
        std::vector<const ModInfo *> out;
        for (const ModInfo &m : m_mods)
        {
            if (!m.error.empty())
                continue;
            if (!m.language.empty() ? m.language == language : enabled(m.id))
                out.push_back(&m);
        }
        return out;
    }

    void Mods::applyToEnvironment(const std::string &language) const
    {
        std::string files, textures, fontMetrics;
        for (const ModInfo *m : active(language))
        {
            if (m->hasFontMetrics && fontMetrics.empty())
                fontMetrics = pathUtf8(m->dir / "font" / "metrics.bin");
            if (m->hasFiles)
                files += (files.empty() ? "" : ";") + pathUtf8(m->dir / "files");
            if (m->hasTextures)
                textures += (textures.empty() ? "" : ";") + pathUtf8(m->dir / "textures");
        }
        // Loose files directly in <data>/mods/<disc program>/ come last.
        files += (files.empty() ? "" : ";") + pathUtf8(m_dataDir / "mods");
        setEnv("GA2_MODS", files);
        setEnv("GA2_TEXTURE_PACKS", textures);
        setEnv("GA2_FONT_METRICS", fontMetrics);
    }
}
