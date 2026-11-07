#include "library.h"
#include "iso9660.h"
#include "process.h"

#include <nlohmann/json.hpp>
#include "runtime/sha256.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdlib>
#include <fstream>
#include <list>
#include <memory>
#include <stdexcept>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#endif

namespace ga2
{
    namespace
    {
        // Program identities come from configs/verified-builds.json (CMake writes
        // them into ga2_verified_builds.inc), the same file the tools use.
        struct VerifiedBuild
        {
            const char *id, *serial, *elfSha256;
        };
        constexpr VerifiedBuild kVerifiedBuilds[] = {
#define GA2_VERIFIED_BUILD(id, serial, sha) {id, serial, sha},
#include "ga2_verified_builds.inc"
#undef GA2_VERIFIED_BUILD
        };
    }

    const std::vector<KnownDisc> &knownDiscs()
    {
        struct Metadata
        {
            const char *id, *engine, *label, *shortLabel;
        };
        static const Metadata metadata[] = {
            {"zettai", "ga2_zettai", "Zettai Ryouiki no Tobira", "Zettai"},
            {"mugen-1", "ga2_mugen_1", "Mugen Kairou no Kagi, disc 1", "Mugen 1"},
            {"mugen-2", "ga2_mugen_2", "Mugen Kairou no Kagi, disc 2", "Mugen 2"},
            {"eigou-1", "ga2_eigou_1", "Eigou Kaiki no Toki, disc 1", "Eigou 1"},
            {"eigou-2", "ga2_eigou_2", "Eigou Kaiki no Toki, disc 2", "Eigou 2"},
        };
        static const std::vector<KnownDisc> discs = [] {
            static std::list<std::string> paths; // stable storage for bootPath
            std::vector<KnownDisc> out;
            for (const auto &entry : metadata)
            {
                const VerifiedBuild *build = nullptr;
                for (const auto &candidate : kVerifiedBuilds)
                    if (std::string_view(candidate.id) == entry.id) build = &candidate;
                if (!build)
                    throw std::logic_error(std::string("configs/verified-builds.json has no entry for ") + entry.id);
                paths.push_back(std::string("/") + build->serial);
                out.push_back({entry.id, paths.back().c_str(), build->elfSha256, entry.engine, entry.label, entry.shortLabel});
            }
            return out;
        }();
        return discs;
    }

    const KnownDisc *knownDisc(std::string_view id)
    {
        for (const auto &disc : knownDiscs())
            if (id == disc.id) return &disc;
        return nullptr;
    }

    const std::vector<StartEntry> &startEntries()
    {
        static const std::vector<StartEntry> entries = {
            {"zettai", "Zettai Ryouiki no Tobira", "絶対領域の扉", "Galaxy Angel II, part 1", "zettai", {"zettai"}, {}},
            {"mugen", "Mugen Kairou no Kagi", "無限回廊の鍵", "Part 2", "mugen-1", {"mugen-1"}, {"mugen-2"}},
            {"mugen-extra", "Zettai Ryouiki no Tobira: Extra Route", "絶対領域の扉 エクストラルート", "Starts from Mugen disc 2; the Zettai disc is its key",
             "mugen-2", {"mugen-2", "zettai"}, {}},
            {"eigou", "Eigou Kaiki no Toki", "永劫回帰の刻", "Part 3", "eigou-1", {"eigou-1"}, {"eigou-2"}},
            {"eigou-2", "Eigou Kaiki no Toki: disc 2 start", "永劫回帰の刻 ディスク2から", "Starts from Eigou disc 2; Mugen disc 1 is its key",
             "eigou-2", {"eigou-2", "mugen-1"}, {}},
        };
        return entries;
    }

    namespace
    {
        // Disc identity digests (shared with the engines' mount check).
        class Sha256
        {
        public:
            void update(const void *data, size_t size) { m_hash.update(data, size); }
            std::string final() { return m_hash.finishHex(); }

        private:
            ga2_sha256::Hasher m_hash;
        };

        std::string sha256(const std::vector<uint8_t> &data)
        {
            Sha256 hash;
            hash.update(data.data(), data.size());
            return hash.final();
        }

        std::string sha256File(const std::filesystem::path &path, ImportProgress *progress)
        {
            std::ifstream in(path, std::ios::binary);
            if (!in) throw std::runtime_error("cannot read " + path.string());
            Sha256 hash;
            std::vector<char> buffer(4u << 20);
            while (in)
            {
                if (progress && progress->cancel) throw std::runtime_error("import cancelled");
                in.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
                const auto count = in.gcount();
                hash.update(buffer.data(), size_t(count));
                if (progress) progress->done += uint64_t(count);
            }
            return hash.final();
        }

        unsigned long processId()
        {
#ifdef _WIN32
            return GetCurrentProcessId();
#else
            return static_cast<unsigned long>(getpid());
#endif
        }

        // Writes through a temporary next to the target and renames it over,
        // so readers see the old or the new file. The temporary is removed
        // when anything fails. `unique` names it per process and call, for
        // files other processes (the engines) also write.
        void writeFile(const std::filesystem::path &path, const void *data, size_t size, bool unique = false)
        {
            std::filesystem::create_directories(path.parent_path());
            static std::atomic<unsigned> counter{0u};
            std::filesystem::path temp = path;
            temp += unique ? ".tmp" + std::to_string(processId()) + "." + std::to_string(counter++) : std::string(".tmp");
            try
            {
                {
                    std::ofstream out(temp, std::ios::binary | std::ios::trunc);
                    out.write(static_cast<const char *>(data), static_cast<std::streamsize>(size));
                    out.flush();
                    if (!out) throw std::runtime_error("cannot write " + path.string());
                }
                std::filesystem::rename(temp, path);
            }
            catch (...)
            {
                std::error_code ignored;
                std::filesystem::remove(temp, ignored);
                throw;
            }
        }

        // The file identity the engines' digest cache keys on (runtime
        // ps2_disc_mount.cpp identity()): device, file, size, mtime, ctime and
        // their sub-second parts.
        bool fileIdentity(const std::filesystem::path &path, nlohmann::json &identity)
        {
#ifdef _WIN32
            const HANDLE handle = CreateFileW(path.c_str(), FILE_READ_ATTRIBUTES,
                                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (handle == INVALID_HANDLE_VALUE) return false;
            BY_HANDLE_FILE_INFORMATION info{};
            FILE_BASIC_INFO basic{};
            const bool ok = GetFileInformationByHandle(handle, &info) &&
                            GetFileInformationByHandleEx(handle, FileBasicInfo, &basic, sizeof(basic));
            CloseHandle(handle);
            if (!ok) return false;
            identity = nlohmann::json::array({uint64_t(info.dwVolumeSerialNumber),
                                              uint64_t(info.nFileIndexHigh) << 32u | info.nFileIndexLow,
                                              uint64_t(info.nFileSizeHigh) << 32u | info.nFileSizeLow,
                                              int64_t(basic.LastWriteTime.QuadPart), int64_t(basic.ChangeTime.QuadPart),
                                              int64_t(0), int64_t(0)});
#else
            struct stat info{};
            if (::stat(path.c_str(), &info) != 0 || !S_ISREG(info.st_mode) || info.st_size < 0) return false;
#ifdef __APPLE__
            const auto modified = info.st_mtimespec, changed = info.st_ctimespec;
#else
            const auto modified = info.st_mtim, changed = info.st_ctim;
#endif
            identity = nlohmann::json::array({uint64_t(info.st_dev), uint64_t(info.st_ino), uint64_t(info.st_size),
                                              int64_t(modified.tv_sec), int64_t(changed.tv_sec),
                                              int64_t(modified.tv_nsec), int64_t(changed.tv_nsec)});
#endif
            return true;
        }

        std::filesystem::path digestCachePath()
        {
#ifdef _WIN32
            const std::string base = getEnvironment("LOCALAPPDATA");
            return base.empty() ? std::filesystem::path{} : pathFromUtf8(base) / "ga2" / "disc-verify.json";
#else
            if (const std::string xdg = getEnvironment("XDG_CACHE_HOME"); !xdg.empty())
                return pathFromUtf8(xdg) / "ga2" / "disc-verify.json";
            const std::string home = getEnvironment("HOME");
            return home.empty() ? std::filesystem::path{} : pathFromUtf8(home) / ".cache" / "ga2" / "disc-verify.json";
#endif
        }

        // The engines skip re-hashing an image whose file identity matches an
        // earlier verification (runtime ps2_disc_mount.cpp keeps the same cache
        // at the same place, so importing seeds it: the first launch does not
        // hash a 4 GB image behind a black window). The cache is a convenience
        // for the player's own machine, not a security boundary: it is a
        // user-writable file keyed by path and file identity (see HANDOFF).
        // Failure to seed only costs that first hash.
        void rememberVerifiedImage(const std::filesystem::path &image, const std::string &digest)
        {
            try
            {
                nlohmann::json identity;
                const auto cache = digestCachePath();
                if (cache.empty() || !fileIdentity(image, identity)) return;
                nlohmann::json json = nlohmann::json::object();
                if (std::ifstream in(cache); in) json = nlohmann::json::parse(in, nullptr, false);
                if (!json.is_object()) json = nlohmann::json::object();
                // Same key as the engines' (image.string() of the absolute, normalised path).
                json[image.string()] = {{"identity", identity}, {"sha256", digest}};
                const std::string text = json.dump(1) + "\n";
                // Another process may rewrite the file between our read and
                // rename; the loser of that race loses one cache entry (the
                // next launch hashes once), never a torn file.
                writeFile(cache, text.data(), text.size(), true);
            }
            catch (const std::exception &)
            {
            }
        }
    }

    std::filesystem::path engineBinary(const std::filesystem::path &engineDir, const KnownDisc &disc)
    {
#ifdef _WIN32
        return engineDir / (std::string(disc.engine) + ".exe");
#else
        return engineDir / disc.engine;
#endif
    }

    std::filesystem::path Library::defaultDataDir()
    {
#ifdef _WIN32
        if (const std::string appdata = getEnvironment("APPDATA"); !appdata.empty())
            return pathFromUtf8(appdata) / "ga2";
        return std::filesystem::current_path() / "ga2-data";
#else
        if (const std::string xdg = getEnvironment("XDG_DATA_HOME"); !xdg.empty()) return pathFromUtf8(xdg) / "ga2";
        if (const std::string home = getEnvironment("HOME"); !home.empty())
            return pathFromUtf8(home) / ".local" / "share" / "ga2";
        return std::filesystem::current_path() / "ga2-data";
#endif
    }

    std::filesystem::path executableDirectory()
    {
#ifdef _WIN32
        std::wstring buffer(MAX_PATH, L'\0');
        for (;;)
        {
            const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), DWORD(buffer.size()));
            if (length == 0) return std::filesystem::current_path();
            if (length < buffer.size())
            {
                buffer.resize(length);
                return std::filesystem::path(buffer).parent_path();
            }
            buffer.resize(buffer.size() * 2); // truncated: a long path
        }
#elif defined(__APPLE__)
        uint32_t size = 0;
        _NSGetExecutablePath(nullptr, &size);
        std::string buffer(size, '\0');
        if (_NSGetExecutablePath(buffer.data(), &size) != 0) return std::filesystem::current_path();
        std::error_code error;
        const auto self = std::filesystem::canonical(buffer.c_str(), error);
        return error ? std::filesystem::current_path() : self.parent_path();
#else
        std::error_code error;
        const auto self = std::filesystem::read_symlink("/proc/self/exe", error);
        return error ? std::filesystem::current_path() : self.parent_path();
#endif
    }

    Library::Library(std::filesystem::path dataDir) : m_dataDir(std::move(dataDir))
    {
        std::filesystem::create_directories(m_dataDir / "memcards" / "mc0");
        std::filesystem::create_directories(m_dataDir / "memcards" / "mc1");
        load();
    }

    void Library::load()
    {
        m_discs.clear();
        const auto path = m_dataDir / "library.json";
        std::ifstream in(path);
        if (!in) return;
        try
        {
            const auto json = nlohmann::json::parse(in);
            if (!json.is_object() || json.value("schema_version", 0) != 1 || !json.at("discs").is_array())
                throw std::runtime_error("unexpected library layout");
            for (const auto &item : json["discs"])
            {
                ImportedDisc disc{item.at("id").get<std::string>(), pathFromUtf8(item.at("image").get<std::string>()),
                                  pathFromUtf8(item.at("manifest").get<std::string>()),
                                  pathFromUtf8(item.at("elf").get<std::string>()),
                                  item.at("image_sha256").get<std::string>(), item.at("manifest_sha256").get<std::string>()};
                if (!knownDisc(disc.id))
                    continue;
                // A disc on a drive that is not attached right now stays in the
                // library; it is only unavailable until the image is back.
                disc.available = std::filesystem::exists(disc.image) && std::filesystem::exists(disc.manifest) &&
                                 std::filesystem::exists(disc.elf);
                m_discs.push_back(std::move(disc));
            }
        }
        catch (const std::exception &)
        {
            // Keep the unreadable file for inspection and start empty.
            in.close();
            std::error_code ignored;
            std::filesystem::rename(path, path.string() + ".bad", ignored);
            m_discs.clear();
        }
    }

    void Library::save() const
    {
        nlohmann::json discs = nlohmann::json::array();
        for (const auto &disc : m_discs)
            discs.push_back({{"id", disc.id}, {"image", pathUtf8(disc.image)}, {"manifest", pathUtf8(disc.manifest)},
                             {"elf", pathUtf8(disc.elf)}, {"image_sha256", disc.imageSha256},
                             {"manifest_sha256", disc.manifestSha256}});
        const std::string text = nlohmann::json{{"schema_version", 1}, {"discs", discs}}.dump(2) + "\n";
        writeFile(m_dataDir / "library.json", text.data(), text.size());
    }

    std::vector<ImportedDisc> Library::discs() const
    {
        std::lock_guard lock(m_mutex);
        std::vector<ImportedDisc> available;
        for (const auto &disc : m_discs)
            if (disc.available) available.push_back(disc);
        return available;
    }

    std::optional<ImportedDisc> Library::disc(std::string_view id) const
    {
        std::lock_guard lock(m_mutex);
        for (const auto &disc : m_discs)
            if (disc.id == id && disc.available) return disc;
        return std::nullopt;
    }

    bool Library::canStart(const StartEntry &entry) const
    {
        return std::all_of(entry.required.begin(), entry.required.end(),
                           [&](const char *id) { return disc(id).has_value(); });
    }

    std::string Library::import(const std::filesystem::path &imagePath, ImportProgress *progress)
    {
        const auto image = std::filesystem::absolute(imagePath).lexically_normal();
        Iso9660 iso(image);
        const IsoFile *cnf = iso.find("/SYSTEM.CNF");
        if (!cnf) throw std::runtime_error(image.filename().string() + " is not a PS2 game disc (no SYSTEM.CNF)");
        const std::string bootPath = bootPathFromSystemCnf(iso.read(*cnf, 64u << 10));
        const IsoFile *boot = iso.find(bootPath);
        if (!boot) throw std::runtime_error("the disc's program " + bootPath + " is missing");
        const auto elf = iso.read(*boot, 128u << 20);
        const std::string elfDigest = sha256(elf);
        const KnownDisc *known = nullptr;
        for (const auto &candidate : knownDiscs())
            if (equalFolded(bootPath, candidate.bootPath) && elfDigest == candidate.elfSha256) known = &candidate;
        if (!known)
            throw std::runtime_error(image.filename().string() + " (" + bootPath.substr(1) +
                                     ") is not a supported Galaxy Angel II disc");

        if (progress)
        {
            progress->done = 0u;
            progress->total = std::filesystem::file_size(image);
        }
        const std::string imageDigest = sha256File(image, progress);

        const auto folder = m_dataDir / "discs" / known->id;
        // Named like the verified program whatever case SYSTEM.CNF used (the
        // engines match exec targets by this file name).
        const auto elfPath = folder / (known->bootPath + 1);
        writeFile(elfPath, elf.data(), elf.size());

        nlohmann::json files = nlohmann::json::array();
        for (const auto &file : iso.files())
        {
            nlohmann::json extents = nlohmann::json::array();
            for (const auto &extent : file.extents) extents.push_back({{"lba", extent.lba}, {"size", extent.size}});
            files.push_back({{"path", file.path}, {"lba", file.extents.front().lba}, {"size", file.size()},
                             {"extents", extents}});
        }
        const nlohmann::json manifest = {
            {"schema_version", 1},
            {"disc_id", known->id},
            {"iso9660", {{"logical_block_size", 2048}, {"volume_space_size", iso.volumeSpaceSize()},
                         {"root_lba", iso.rootLba()}, {"root_size", iso.rootSize()}}},
            {"files", files},
            {"boot", {{"path", boot->path}, {"sha256", elfDigest}}},
        };
        const std::string manifestText = manifest.dump(2) + "\n";
        const auto manifestPath = folder / "manifest.json";
        writeFile(manifestPath, manifestText.data(), manifestText.size());
        rememberVerifiedImage(image, imageDigest);

        ImportedDisc record{known->id, image, manifestPath, elfPath, imageDigest,
                            sha256(std::vector<uint8_t>(manifestText.begin(), manifestText.end()))};
        std::lock_guard lock(m_mutex);
        std::erase_if(m_discs, [&](const ImportedDisc &d) { return d.id == record.id; });
        m_discs.push_back(std::move(record));
        std::sort(m_discs.begin(), m_discs.end(), [](const ImportedDisc &a, const ImportedDisc &b) {
            return std::string_view(a.id) < std::string_view(b.id);
        });
        save();
        return known->id;
    }

    std::vector<std::string> Library::engineCommand(const StartEntry &entry, const std::filesystem::path &engineDir) const
    {
        const std::optional<ImportedDisc> boot = disc(entry.bootDisc);
        const std::vector<ImportedDisc> all = discs();
        const KnownDisc *known = knownDisc(entry.bootDisc);
        if (!boot || !known) throw std::runtime_error(std::string("disc not imported: ") + entry.bootDisc);
        std::vector<std::string> command{pathUtf8(engineBinary(engineDir, *known)), pathUtf8(boot->elf),
                                         "--disc-image", pathUtf8(boot->image),
                                         "--disc-manifest", pathUtf8(boot->manifest),
                                         "--disc-image-sha256", boot->imageSha256,
                                         "--disc-manifest-sha256", boot->manifestSha256};
        // Every other disc rides along; the engine inserts it when the game asks.
        for (const auto &other : all)
            if (other.id != boot->id)
                command.insert(command.end(), {"--extra-disc", pathUtf8(other.image), pathUtf8(other.manifest),
                                               other.imageSha256, other.manifestSha256});
        // Program handoffs (LoadExecPS2): every installed engine next to this one.
        for (const auto &other : all)
        {
            const auto engine = engineBinary(engineDir, *knownDisc(other.id));
            if (std::filesystem::exists(engine))
                command.insert(command.end(), {"--exec-target", pathUtf8(other.elf.filename()), pathUtf8(engine),
                                               pathUtf8(other.elf)});
        }
        command.insert(command.end(), {"--mc-root", pathUtf8(m_dataDir / "memcards" / "mc0")});
        return command;
    }
}
