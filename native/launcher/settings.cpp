#include "settings.h"
#include "process.h"

#include <algorithm>
#include <cstdlib>
#include <fstream>

namespace ga2
{
    namespace
    {
        void setEnv(const char *name, const std::string &value) { setEnvironment(name, value); }
    }

    void Settings::set(size_t option, int choice)
    {
        const int count = int(options()[option].choices.size());
        choice = std::clamp(choice, 0, count - 1);
        m_numbers[option] = ga2_options::carryNumber(options()[option], m_choices[option], choice, m_numbers[option]);
        m_choices[option] = choice;
    }

    void Settings::setNumber(size_t option, int number)
    {
        const Option &o = options()[option];
        if (!ga2_options::hasCustom(o)) return;
        m_numbers[option] = ga2_options::clampCustom(o, number);
        m_choices[option] = o.customChoice;
    }

    void Settings::step(size_t option, int delta)
    {
        const int count = int(options()[option].choices.size());
        set(option, (m_choices[option] + delta % count + count) % count);
    }

    std::string Settings::value(const char *key) const
    {
        const auto &table = options();
        for (size_t i = 0; i < table.size(); ++i)
            if (std::string(table[i].key) == key)
                return table[i].choices[size_t(m_choices[i])].value;
        return {};
    }

    Settings Settings::load(const std::filesystem::path &file)
    {
        Settings settings;
        std::ifstream in(file);
        const auto &table = options();
        for (std::string line; std::getline(in, line);)
        {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            const auto equals = line.find('=');
            if (equals == std::string::npos) continue;
            const std::string key = line.substr(0, equals), value = line.substr(equals + 1);
            if (key == "window_scale")
            {
                settings.windowScale = std::clamp(std::atoi(value.c_str()), 1, 8);
                continue;
            }
            for (size_t i = 0; i < table.size(); ++i)
                if (key == table[i].key)
                {
                    // Old files (fps=120), a custom number (fps=173) and "monitor" all read here; a value that is
                    // none of them leaves the default.
                    int choice = settings.m_choices[i], number = settings.m_numbers[i];
                    if (ga2_options::parseStored(i, value, choice, number))
                    {
                        settings.m_choices[i] = choice;
                        settings.m_numbers[i] = number;
                    }
                }
        }
        return settings;
    }

    void Settings::save(const std::filesystem::path &file) const
    {
        std::ofstream out(file, std::ios::trunc);
        const auto &table = options();
        for (size_t i = 0; i < table.size(); ++i)
            out << table[i].key << '=' << ga2_options::storedValue(i, m_choices[i], m_numbers[i]) << '\n';
        out << "window_scale=" << windowScale << '\n';
    }

    void Settings::applyToEnvironment(const std::filesystem::path &dataDir) const
    {
        const std::string dumpDir = pathUtf8(dataDir / "dumps" / "textures");
        const auto &table = options();
        for (size_t i = 0; i < table.size(); ++i)
            setEnv(table[i].env, ga2_options::envValue(i, m_choices[i], dumpDir, m_numbers[i]));
        setEnv("GA2_WINDOW_SCALE", std::to_string(windowScale));
        // Where the engines read and save settings.ini (the in-game settings menu).
        setEnv("GA2_DATA_DIR", pathUtf8(dataDir));
        // Rendered from the player's discs after import (assets.cpp): one
        // <program>.png per game.
        setEnv("GA2_SKYBOX", pathUtf8(dataDir / "assets" / "skybox"));
    }
}
