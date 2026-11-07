// Text inside the game executables (battle command names, memory-card
// messages, the save-slot chapter format, the extras cast list): a language
// mod ships <mod>/exe/<program>.tsv, one patch per line,
//   0xADDRESS <TAB> original bytes (hex) <TAB> new bytes (hex)
// written by tools/modding/script_xlsx.py from the Program sheet. Each patch
// is applied once the executable is loaded, before it runs, and only when the
// loaded bytes are the original string and the new one (with its NUL) fits in
// the original's room: its own bytes plus the zero padding after it. Code
// keeps pointing at the same address, so nothing else moves.
#include "game_overrides.h"
#include "ps2_runtime.h"

#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace
{
    constexpr uint32_t kRamSize = 32u * 1024u * 1024u;

    bool parseHex(std::string_view s, std::vector<uint8_t> &out)
    {
        out.clear();
        if (s.size() % 2)
            return false;
        for (size_t i = 0; i < s.size(); i += 2)
        {
            auto nibble = [](char c) -> int {
                if (c >= '0' && c <= '9') return c - '0';
                c = char(std::tolower(static_cast<unsigned char>(c)));
                if (c >= 'a' && c <= 'f') return c - 'a' + 10;
                return -1;
            };
            const int hi = nibble(s[i]), lo = nibble(s[i + 1]);
            if (hi < 0 || lo < 0)
                return false;
            out.push_back(uint8_t(hi << 4 | lo));
        }
        return true;
    }

    // <mod>/exe/<program>.tsv for every enabled mod (GA2_MODS lists the mods'
    // files/ folders, lowest priority first; later patches win).
    std::vector<std::filesystem::path> patchFiles(const char *program)
    {
        std::vector<std::filesystem::path> out;
        const char *list = std::getenv("GA2_MODS");
        if (!list)
            return out;
        for (std::string_view rest = list; !rest.empty();)
        {
            const size_t end = rest.find(';');
            const std::string_view root = rest.substr(0, end);
            if (!root.empty())
            {
                const std::filesystem::path file =
                    std::filesystem::path(std::string(root)).parent_path() / "exe" / (std::string(program) + ".tsv");
                std::error_code ec;
                if (std::filesystem::is_regular_file(file, ec))
                    out.push_back(file);
            }
            rest = end == std::string_view::npos ? std::string_view() : rest.substr(end + 1);
        }
        // GA2_MODS is highest priority first: apply the lowest first.
        return {out.rbegin(), out.rend()};
    }

    void applyFile(uint8_t *rdram, const std::filesystem::path &file)
    {
        std::ifstream in(file);
        std::string line;
        int applied = 0, skipped = 0, n = 0;
        std::vector<uint8_t> original, replacement;
        while (std::getline(in, line))
        {
            ++n;
            if (line.empty() || line[0] == '#')
                continue;
            std::istringstream fields(line);
            std::string address, a, b;
            if (!std::getline(fields, address, '\t') || !std::getline(fields, a, '\t') || !std::getline(fields, b))
            {
                ++skipped;
                continue;
            }
            if (!b.empty() && b.back() == '\r')
                b.pop_back();
            const unsigned long at = std::strtoul(address.c_str(), nullptr, 0);
            if (!parseHex(a, original) || !parseHex(b, replacement) || original.empty() ||
                at < 0x100000u || at + original.size() + replacement.size() + 1 >= kRamSize)
            {
                std::cerr << "[ga2 exe text] " << file.string() << ":" << n << ": malformed\n";
                ++skipped;
                continue;
            }
            uint8_t *p = rdram + at;
            bool ok = std::memcmp(p, original.data(), original.size()) == 0;
            // The room: the original string, then the zero bytes after it.
            size_t room = original.size();
            while (ok && at + room < kRamSize && p[room] == 0)
                ++room;
            ok = ok && replacement.size() + 1 <= room;
            if (!ok)
            {
                ++skipped;
                continue;
            }
            std::memcpy(p, replacement.data(), replacement.size());
            std::memset(p + replacement.size(), 0, original.size() > replacement.size() ? original.size() - replacement.size() : 1);
            ++applied;
        }
        std::cerr << "[ga2 exe text] " << file.string() << ": " << applied << " string(s) replaced";
        if (skipped)
            std::cerr << ", " << skipped << " skipped (original not found or no room)";
        std::cerr << '\n';
    }

    template <int I>
    void bind(PS2Runtime &runtime)
    {
        static constexpr const char *kPrograms[] = {"SLPM_662.43", "SLPM_667.79", "SLPM_667.80", "SLPM_551.18",
                                                    "SLPM_551.19"};
        for (const auto &file : patchFiles(kPrograms[I]))
            applyFile(runtime.memory().getRDRAM(), file);
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 exe text zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 exe text mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 exe text mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 exe text eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 exe text eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
