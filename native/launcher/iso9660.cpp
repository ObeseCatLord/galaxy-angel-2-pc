#include "iso9660.h"

#include <algorithm>
#include <cctype>
#include <cstring>

namespace ga2
{
    namespace
    {
        constexpr uint32_t kSector = 2048u;
        constexpr uint64_t kMaxDirectoryBytes = 64ull * 1024u * 1024u;
        constexpr int kMaxDepth = 64;
        constexpr size_t kMaxFiles = 100000u;
        constexpr uint64_t kMaxDirectoryTotal = 256ull * 1024u * 1024u;

        uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | uint32_t(p[3]) << 24; }

        char lowerAscii(char c) { return (c >= 'A' && c <= 'Z') ? char(c - 'A' + 'a') : c; }

        struct Record
        {
            std::string name;
            IsoExtent extent;
            bool directory = false, continues = false, special = false;
        };
    }

    int compareFolded(std::string_view a, std::string_view b)
    {
        // ASCII case folding to lower case, like the Python tools' casefold()
        // (the file order of the manifests); no allocation per comparison.
        const size_t common = std::min(a.size(), b.size());
        for (size_t i = 0; i < common; ++i)
        {
            const unsigned char x = static_cast<unsigned char>(lowerAscii(a[i])), y = static_cast<unsigned char>(lowerAscii(b[i]));
            if (x != y) return x < y ? -1 : 1;
        }
        return a.size() == b.size() ? 0 : (a.size() < b.size() ? -1 : 1);
    }

    bool equalFolded(std::string_view a, std::string_view b) { return a.size() == b.size() && compareFolded(a, b) == 0; }

    uint64_t IsoFile::size() const
    {
        uint64_t total = 0u;
        for (const auto &extent : extents) total += extent.size;
        return total;
    }

    Iso9660::Iso9660(const std::filesystem::path &image) : m_stream(image, std::ios::binary)
    {
        if (!m_stream) throw IsoError("cannot open " + image.string());
        m_size = std::filesystem::file_size(image);
        if (m_size == 0u || m_size % kSector != 0u)
            throw IsoError("image size is not a whole number of 2048-byte sectors (not a plain ISO dump?)");
        for (uint32_t n = 0u; n < 100u; ++n)
        {
            const auto descriptor = readAt((16u + n) * uint64_t(kSector), kSector);
            if (std::memcmp(descriptor.data() + 1, "CD001", 5) != 0 || descriptor[6] != 1)
                throw IsoError("not an ISO9660 disc image");
            if (descriptor[0] == 255) break;
            if (descriptor[0] != 1) continue;
            if ((descriptor[128] | descriptor[129] << 8) != kSector)
                throw IsoError("unsupported logical block size");
            m_volumeSpace = le32(descriptor.data() + 80);
            if (uint64_t(m_volumeSpace) * kSector != m_size)
                throw IsoError("image size does not match its ISO9660 volume size (padded or truncated dump)");
            const uint8_t *root = descriptor.data() + 156;
            if (root[0] < 34 || !(root[25] & 2)) throw IsoError("invalid root directory record");
            m_root.path = "/";
            m_root.extents = {{le32(root + 2), le32(root + 10)}};
            walk(m_root, "", 0);
            std::sort(m_files.begin(), m_files.end(),
                      [](const IsoFile &a, const IsoFile &b) { return compareFolded(a.path, b.path) < 0; });
            for (size_t i = 1; i < m_files.size(); ++i)
                if (equalFolded(m_files[i].path, m_files[i - 1].path))
                    throw IsoError("duplicate file on disc: " + m_files[i].path);
            return;
        }
        throw IsoError("ISO9660 primary volume descriptor not found");
    }

    std::vector<uint8_t> Iso9660::readAt(uint64_t offset, uint64_t count)
    {
        if (offset > m_size || count > m_size - offset) throw IsoError("read outside the disc image");
        std::vector<uint8_t> data(count);
        m_stream.seekg(static_cast<std::streamoff>(offset));
        m_stream.read(reinterpret_cast<char *>(data.data()), static_cast<std::streamsize>(count));
        if (!m_stream) throw IsoError("disc image ended during a read");
        return data;
    }

    void Iso9660::walk(const IsoFile &directory, const std::string &parent, int depth)
    {
        if (depth > kMaxDepth) throw IsoError("directory nesting too deep");
        const IsoExtent extent = directory.extents.front();
        if (extent.size > kMaxDirectoryBytes) throw IsoError("directory too large");
        if (!m_visited.insert((uint64_t(extent.lba) << 32) | extent.size).second)
            throw IsoError("directory graph contains a cycle");
        m_directoryBytes += extent.size;
        if (m_directoryBytes > kMaxDirectoryTotal) throw IsoError("directories too large");
        const auto data = readAt(extent.lba * uint64_t(kSector), extent.size);
        IsoFile pending;
        size_t position = 0u;
        while (position < data.size())
        {
            const uint8_t length = data[position];
            if (length == 0u)
            {
                position = (position / kSector + 1u) * kSector;
                continue;
            }
            if (length < 34u || length > kSector - position % kSector || position + length > data.size())
                throw IsoError("invalid directory record");
            const uint8_t *r = data.data() + position;
            position += length;
            const uint8_t nameLength = r[32];
            if (33u + nameLength > length) throw IsoError("truncated directory record");
            Record record;
            record.extent = {le32(r + 2), le32(r + 10)};
            if (uint64_t(record.extent.lba) * kSector > m_size ||
                record.extent.size > m_size - uint64_t(record.extent.lba) * kSector)
                throw IsoError("directory record outside the disc image");
            record.directory = r[25] & 2;
            record.continues = r[25] & 0x80;
            record.special = nameLength == 1u && (r[33] == 0 || r[33] == 1);
            if (record.special) continue;
            record.name.assign(reinterpret_cast<const char *>(r + 33), nameLength);
            // Only a trailing ";<version>" is dropped ("A;B;1" keeps "A;B").
            if (const auto semicolon = record.name.rfind(';');
                semicolon != std::string::npos && semicolon + 1 < record.name.size() &&
                record.name.find_first_not_of("0123456789", semicolon + 1) == std::string::npos)
                record.name.resize(semicolon);
            if (record.name.empty() || record.name.find_first_of("/\\") != std::string::npos ||
                !std::all_of(record.name.begin(), record.name.end(),
                             [](char c) { return static_cast<unsigned char>(c) >= 32u && static_cast<unsigned char>(c) <= 126u; }))
                throw IsoError("unsupported file name on disc");
            const std::string path = parent + "/" + record.name;
            if (!pending.path.empty())
            {
                if (pending.path != path || record.directory) throw IsoError("broken multi-extent file");
                pending.extents.push_back(record.extent);
                if (!record.continues)
                {
                    m_files.push_back(std::move(pending));
                    pending = {};
                }
                continue;
            }
            if (record.directory)
            {
                if (record.continues) throw IsoError("multi-extent directory");
                walk(IsoFile{path, {record.extent}}, path, depth + 1);
            }
            else if (record.continues)
                pending = IsoFile{path, {record.extent}};
            else
                m_files.push_back(IsoFile{path, {record.extent}});
            if (m_files.size() > kMaxFiles) throw IsoError("too many files on disc");
        }
        if (!pending.path.empty()) throw IsoError("multi-extent file missing its last record");
    }

    const IsoFile *Iso9660::find(std::string_view path) const
    {
        for (const auto &file : m_files)
            if (equalFolded(file.path, path)) return &file;
        return nullptr;
    }

    std::vector<uint8_t> Iso9660::read(const IsoFile &file, uint64_t limit)
    {
        if (file.size() > limit) throw IsoError("file too large: " + file.path);
        std::vector<uint8_t> out;
        for (const auto &extent : file.extents)
        {
            const auto part = readAt(extent.lba * uint64_t(kSector), extent.size);
            out.insert(out.end(), part.begin(), part.end());
        }
        return out;
    }

    std::string bootPathFromSystemCnf(const std::vector<uint8_t> &contents)
    {
        // Line scan (no regex: the file is untrusted input). "BOOT2 = cdrom0:\X;1".
        // A line starts with BOOT2 or BOOT (a PS1 key, used only when there is no BOOT2),
        // optional blanks, "=", then the first token.
        const std::string_view text(reinterpret_cast<const char *>(contents.data()), contents.size());
        std::string value, legacyValue;
        size_t start = 0;
        while (start < text.size())
        {
            size_t end = text.find_first_of("\r\n", start);
            if (end == std::string_view::npos) end = text.size();
            std::string_view line = text.substr(start, end - start);
            start = end + 1;
            size_t i = line.find_first_not_of(" \t");
            if (i == std::string_view::npos) continue;
            line.remove_prefix(i);
            if (line.size() < 4 || !equalFolded(line.substr(0, 4), "BOOT")) continue;
            size_t k = 4;
            const bool boot2 = k < line.size() && line[k] == '2';
            if (boot2) ++k;
            k = line.find_first_not_of(" \t", k);
            if (k == std::string_view::npos || line[k] != '=') continue;
            k = line.find_first_not_of(" \t", k + 1);
            if (k == std::string_view::npos) continue;
            std::string_view token = line.substr(k, line.find_first_of(" \t", k) - k);
            if (!token.empty() && token.front() == '"') token.remove_prefix(1);
            if (!token.empty() && token.back() == '"') token.remove_suffix(1);
            std::string &target = boot2 ? value : legacyValue;
            if (target.empty()) target = std::string(token);
        }
        if (value.empty()) value = legacyValue;
        if (value.empty())
            throw IsoError("SYSTEM.CNF has no BOOT2 line");
        if (equalFolded(std::string_view(value).substr(0, 7), "CDROM0:")) value.erase(0, 7);
        else if (equalFolded(std::string_view(value).substr(0, 6), "CDROM:")) value.erase(0, 6);
        if (const auto semicolon = value.rfind(';'); semicolon != std::string::npos &&
            value.find_first_not_of("0123456789", semicolon + 1) == std::string::npos)
            value.resize(semicolon);
        std::replace(value.begin(), value.end(), '\\', '/');
        while (!value.empty() && value.front() == '/') value.erase(value.begin());
        if (value.empty() || value.find("..") != std::string::npos) throw IsoError("unsafe BOOT2 path");
        return "/" + value;
    }
}
