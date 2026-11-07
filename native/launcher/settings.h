// Player options: <data>/settings.ini, shown on the launcher's Options
// screen and handed to the engines as environment variables. Every option
// here changes something the engines do; options whose feature needs data the
// player does not have yet (the skybox) say so instead of pretending.
#pragma once

#include "runtime/ga2_options.h"

#include <filesystem>
#include <string>
#include <vector>

namespace ga2
{
    // The option table is shared with the runtime's in-game settings menu (ps2xRuntime/include/runtime/ga2_options.h).
    using OptionChoice = ga2_options::Choice;
    using Option = ga2_options::Option;

    inline const std::vector<Option> &options() { return ga2_options::options(); }

    class Settings
    {
    public:
        static Settings load(const std::filesystem::path &file);
        void save(const std::filesystem::path &file) const;

        // Index into options()[option].choices.
        int choice(size_t option) const { return m_choices[option]; }
        void set(size_t option, int choice);
        void step(size_t option, int delta);
        // The editable number of an option that has one (the frame rate's Custom); used while Custom is chosen.
        int number(size_t option) const { return m_numbers[option]; }
        // Chooses the custom choice with this number (clamped to its range).
        void setNumber(size_t option, int number);
        // What the menus show for the option's state ("Custom: 173").
        std::string label(size_t option) const { return ga2_options::choiceLabel(option, m_choices[option], m_numbers[option]); }
        std::string value(const char *key) const;

        int windowScale = 1; // windowed size as a multiple of 640x448 (from the monitor)

        // Environment for the engines (dataDir: where assets such as the
        // extracted skybox live, and where the engines save settings.ini
        // when the player changes options in game).
        void applyToEnvironment(const std::filesystem::path &dataDir) const;

    private:
        std::vector<int> m_choices = std::vector<int>(options().size(), 0);
        std::vector<int> m_numbers = defaultNumbers();
        static std::vector<int> defaultNumbers()
        {
            std::vector<int> numbers;
            for (const Option &o : options())
                numbers.push_back(o.customDefault);
            return numbers;
        }
    };
}
