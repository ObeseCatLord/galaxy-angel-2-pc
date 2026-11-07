// Minimal ISO9660 reader for importing the player's disc images: the file
// table (with multi-extent files) and reading one file. Bounds-checked like
// tools/import_discs.py, which it mirrors.
#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_set>
#include <vector>

namespace ga2
{
    struct IsoExtent
    {
        uint32_t lba = 0u;
        uint32_t size = 0u;
    };

    struct IsoFile
    {
        std::string path; // "/DIR/NAME.EXT", version suffix removed
        std::vector<IsoExtent> extents;
        uint64_t size() const;
    };

    class IsoError : public std::runtime_error
    {
    public:
        using std::runtime_error::runtime_error;
    };

    class Iso9660
    {
    public:
        explicit Iso9660(const std::filesystem::path &image);

        uint32_t volumeSpaceSize() const { return m_volumeSpace; }
        uint32_t rootLba() const { return m_root.extents.front().lba; }
        uint32_t rootSize() const { return m_root.extents.front().size; }
        // Every file (not directory), sorted case-insensitively by path.
        const std::vector<IsoFile> &files() const { return m_files; }
        const IsoFile *find(std::string_view path) const; // case-insensitive
        std::vector<uint8_t> read(const IsoFile &file, uint64_t limit);

    private:
        std::vector<uint8_t> readAt(uint64_t offset, uint64_t count);
        void walk(const IsoFile &directory, const std::string &parent, int depth);

        std::ifstream m_stream;
        uint64_t m_size = 0u;
        uint32_t m_volumeSpace = 0u;
        IsoFile m_root;
        std::vector<IsoFile> m_files;
        std::unordered_set<uint64_t> m_visited;
        uint64_t m_directoryBytes = 0u;
    };

    // ASCII case-insensitive comparison without allocation (negative, 0, positive).
    int compareFolded(std::string_view a, std::string_view b);
    bool equalFolded(std::string_view a, std::string_view b);

    // BOOT2 path from SYSTEM.CNF ("cdrom0:\SLPM_662.43;1" -> "/SLPM_662.43").
    std::string bootPathFromSystemCnf(const std::vector<uint8_t> &contents);
}
