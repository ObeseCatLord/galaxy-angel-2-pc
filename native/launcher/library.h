// The player's imported discs and how to start each game from them.
#pragma once

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace ga2
{
    // A disc this port has recompiled code for, identified by its program.
    struct KnownDisc
    {
        const char *id;       // "mugen-1"
        const char *bootPath; // "/SLPM_667.79"
        const char *elfSha256;
        const char *engine;   // native binary next to the launcher
        const char *label;    // "Mugen Kairou no Kagi, disc 1"
        const char *shortLabel; // "Mugen 1"
    };
    const std::vector<KnownDisc> &knownDiscs();
    const KnownDisc *knownDisc(std::string_view id);

    // A way to start playing: which disc boots, which discs it cannot start without.
    struct StartEntry
    {
        const char *id;
        const char *title;
        const char *titleJa; // the title as printed on the box (UTF-8)
        const char *detail;
        const char *bootDisc;
        std::vector<const char *> required;
        std::vector<const char *> later; // needed further into the game (disc change)
    };
    const std::vector<StartEntry> &startEntries();

    struct ImportedDisc
    {
        std::string id;
        std::filesystem::path image, manifest, elf;
        std::string imageSha256, manifestSha256;
        bool available = true; // image, manifest and program present right now
    };

    struct ImportProgress
    {
        std::atomic<uint64_t> done{0u}, total{0u};
        std::atomic<bool> cancel{false};
    };

    class Library
    {
    public:
        explicit Library(std::filesystem::path dataDir);
        static std::filesystem::path defaultDataDir();

        const std::filesystem::path &dataDir() const { return m_dataDir; }
        // Thread-safe: imports run on a worker while the menu draws.
        std::vector<ImportedDisc> discs() const;
        std::optional<ImportedDisc> disc(std::string_view id) const;
        bool canStart(const StartEntry &entry) const;

        // Identifies the image, hashes it once, writes its manifest and
        // program into the data directory and records it. Returns the disc id.
        std::string import(const std::filesystem::path &image, ImportProgress *progress = nullptr);

        // Command line for the engine binary that plays `entry`.
        std::vector<std::string> engineCommand(const StartEntry &entry, const std::filesystem::path &engineDir) const;

    private:
        void load();
        void save() const;

        std::filesystem::path m_dataDir;
        mutable std::mutex m_mutex;
        std::vector<ImportedDisc> m_discs;
    };

    std::filesystem::path executableDirectory();
    // The engine binary for a disc (with the platform's executable suffix).
    std::filesystem::path engineBinary(const std::filesystem::path &engineDir, const KnownDisc &disc);
}
