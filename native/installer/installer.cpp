#include "installer.h"
#include "platform.h"
#include "sha256.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace inst
{
    namespace
    {
        const char *kStateDir = ".ga2-installer";
        const char *kRecord = "installed.json";

        fs::path stateDir(const fs::path &dir) { return dir / kStateDir; }

        std::string readFile(const fs::path &file)
        {
            std::ifstream in(file, std::ios::binary);
            std::ostringstream text;
            text << in.rdbuf();
            return text.str();
        }

        bool isLocal(const std::string &url) { return url.find("://") == std::string::npos || url.rfind("file://", 0) == 0; }
        fs::path localPath(const std::string &url)
        {
            return pathFromUtf8(url.rfind("file://", 0) == 0 ? url.substr(7) : url);
        }

        std::string generic(const fs::path &relative)
        {
            std::string text = pathUtf8(relative);
            std::replace(text.begin(), text.end(), '\\', '/');
            return text;
        }

        // Deletes `file`'s now empty parent folders up to (not including) `root`.
        void pruneEmpty(fs::path folder, const fs::path &root)
        {
            std::error_code error;
            while (!folder.empty() && folder != root && folder.native().size() > root.native().size())
            {
                if (!fs::is_directory(folder, error) || !fs::is_empty(folder, error)) return;
                fs::remove(folder, error);
                folder = folder.parent_path();
            }
        }

        void writeRecord(const fs::path &dir, const std::string &version, const std::vector<std::string> &files)
        {
            nlohmann::json record;
            record["version"] = version;
            record["platform"] = platformId();
            record["installer"] = installerVersion();
            const std::time_t now = std::time(nullptr);
            char date[32];
            std::strftime(date, sizeof(date), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&now));
            record["installedAt"] = date;
            record["files"] = files;
            fs::create_directories(stateDir(dir));
            const fs::path file = stateDir(dir) / kRecord, temp = stateDir(dir) / "installed.json.new";
            {
                std::ofstream out(temp, std::ios::trunc);
                out << record.dump(1) << "\n";
                out.close();
                if (!out) throw std::runtime_error("cannot write " + pathUtf8(temp));
            }
            std::string error;
            if (!platform::replaceFile(temp, file, error)) throw std::runtime_error("cannot write " + pathUtf8(file) + ": " + error);
        }

        std::string versionOfLauncher(const fs::path &launcher)
        {
            std::string output;
            try
            {
                if (platform::run({pathUtf8(launcher), "--version"}, &output) != 0) return {};
            }
            catch (...)
            {
                return {};
            }
            // "Galaxy Angel II PC 0.10.0"
            std::istringstream lines(output);
            for (std::string line; std::getline(lines, line);)
            {
                if (!line.empty() && line.back() == '\r') line.pop_back();
                const std::string prefix = "Galaxy Angel II PC ";
                if (line.rfind(prefix, 0) == 0) return line.substr(prefix.size());
            }
            return {};
        }

        uint64_t folderSize(const fs::path &dir)
        {
            uint64_t total = 0;
            std::error_code error;
            for (auto it = fs::recursive_directory_iterator(dir, error); !error && it != fs::recursive_directory_iterator(); it.increment(error))
                if (it->is_regular_file(error)) total += it->file_size(error);
            return total;
        }

        fs::path settingsIni() { return dataDir() / "installer.ini"; }

        // A path from installed.json that stays inside the install folder (the file is ours, but may be damaged).
        bool safeRecorded(const std::string &rel)
        {
            if (rel.empty() || rel[0] == '/' || rel[0] == '\\' || rel.find(':') != std::string::npos) return false;
            for (size_t start = 0; start <= rel.size();)
            {
                size_t end = rel.find_first_of("/\\", start);
                if (end == std::string::npos) end = rel.size();
                if (rel.compare(start, end - start, "..") == 0 && end - start == 2) return false;
                start = end + 1;
            }
            return !pathFromUtf8(rel).is_absolute() && !pathFromUtf8(rel).has_root_name();
        }
    }

    const char *platformId()
    {
#if defined(_WIN32)
        return "windows-x86_64";
#elif defined(__aarch64__)
        return "linux-arm64";
#else
        return "linux-x86_64";
#endif
    }

    const char *installerVersion() { return GA2_INSTALLER_VERSION; }

#ifdef _WIN32
    const char *launcherName() { return "ga2.exe"; }
    const char *installerName() { return "ga2-installer.exe"; }
#else
    const char *launcherName() { return "ga2"; }
    const char *installerName() { return "ga2-installer"; }
#endif

    std::string pathUtf8(const fs::path &path)
    {
        const auto text = path.u8string();
        return std::string(reinterpret_cast<const char *>(text.data()), text.size());
    }

    fs::path pathFromUtf8(const std::string &text)
    {
        return fs::path(std::u8string(reinterpret_cast<const char8_t *>(text.data()), text.size()));
    }

    std::string getEnv(const char *name)
    {
#ifdef _WIN32
        const std::wstring wname(name, name + std::strlen(name));
        const wchar_t *value = _wgetenv(wname.c_str());
        return value ? pathUtf8(fs::path(value)) : std::string();
#else
        const char *value = std::getenv(name);
        return value ? value : "";
#endif
    }

    std::string manifestSource()
    {
        const std::string custom = getEnv("GA2_INSTALLER_MANIFEST");
        if (!custom.empty()) return custom;
        return std::string("https://github.com/") + GA2_INSTALLER_REPO + "/releases/latest/download/ga2-manifest.json";
    }

    Manifest parseManifest(const std::string &text)
    {
        const auto json = nlohmann::json::parse(text);
        if (json.value("schemaVersion", 0) != 1) throw std::runtime_error("this installer is too old for the release; download the newest installer");
        Manifest m;
        m.name = json.value("name", "Galaxy Angel II PC");
        m.version = json.at("version").get<std::string>();
        m.tag = json.value("tag", "v" + m.version);
        m.date = json.value("date", "");
        m.notes = json.value("notes", "");
        m.page = json.value("page", "");
        auto read = [](const nlohmann::json &entry) {
            Download d;
            d.file = entry.at("file").get<std::string>();
            d.url = entry.at("url").get<std::string>();
            d.sha256 = entry.at("sha256").get<std::string>();
            d.size = entry.value("size", uint64_t(0));
            if (d.file.empty() || d.file.find_first_of("/\\:") != std::string::npos || d.file == "." || d.file == "..")
                throw std::runtime_error("bad file name in the release manifest");
            if (d.sha256.size() != 64 || d.sha256.find_first_not_of("0123456789abcdef") != std::string::npos)
                throw std::runtime_error("bad SHA-256 in the release manifest");
            return d;
        };
        if (json.contains("packages") && json["packages"].contains(platformId())) m.package = read(json["packages"][platformId()]);
        if (json.contains("installer"))
        {
            const auto &installer = json["installer"];
            m.installerVersion = installer.value("version", "");
            if (installer.contains("files") && installer["files"].contains(platformId())) m.installer = read(installer["files"][platformId()]);
        }
        return m;
    }

    int compareVersions(const std::string &a, const std::string &b)
    {
        auto parts = [](std::string text) {
            if (!text.empty() && (text[0] == 'v' || text[0] == 'V')) text.erase(0, 1);
            std::vector<long> out;
            std::istringstream in(text);
            for (std::string part; std::getline(in, part, '.');)
                out.push_back(std::strtol(part.c_str(), nullptr, 10));
            return out;
        };
        const auto x = parts(a), y = parts(b);
        for (size_t i = 0; i < std::max(x.size(), y.size()); ++i)
        {
            const long p = i < x.size() ? x[i] : 0, q = i < y.size() ? y[i] : 0;
            if (p != q) return p < q ? -1 : 1;
        }
        return 0;
    }

    Installed readInstalled(const fs::path &dir)
    {
        Installed out;
        std::error_code error;
        out.present = fs::exists(dir / launcherName(), error);
        const fs::path record = stateDir(dir) / kRecord;
        if (fs::exists(record, error))
        {
            try
            {
                const auto json = nlohmann::json::parse(readFile(record));
                out.recorded = true;
                out.version = json.value("version", "");
                out.files = json.value("files", std::vector<std::string>());
            }
            catch (...)
            {
                out.recorded = false;
            }
        }
        if (out.present && out.version.empty()) out.version = versionOfLauncher(dir / launcherName());
        return out;
    }

    fs::path dataDir()
    {
#ifdef _WIN32
        if (const std::string appdata = getEnv("APPDATA"); !appdata.empty()) return pathFromUtf8(appdata) / "ga2";
#else
        if (const std::string xdg = getEnv("XDG_DATA_HOME"); !xdg.empty()) return pathFromUtf8(xdg) / "ga2";
        if (const std::string home = getEnv("HOME"); !home.empty()) return pathFromUtf8(home) / ".local" / "share" / "ga2";
#endif
        return fs::current_path() / "ga2-data";
    }

    fs::path defaultInstallDir()
    {
        std::error_code error;
        const fs::path self = platform::selfPath().parent_path();
        if (!self.empty() && (fs::exists(self / launcherName(), error) || fs::exists(stateDir(self), error))) return self;
        std::ifstream in(settingsIni());
        for (std::string line; std::getline(in, line);)
        {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.rfind("install_dir=", 0) == 0)
            {
                const fs::path remembered = pathFromUtf8(line.substr(12));
                if (fs::exists(remembered / launcherName(), error)) return remembered;
            }
        }
#ifdef _WIN32
        if (const std::string local = getEnv("LOCALAPPDATA"); !local.empty())
            return pathFromUtf8(local) / "Programs" / "Galaxy Angel II PC";
        return fs::current_path() / "Galaxy Angel II PC";
#else
        if (const std::string xdg = getEnv("XDG_DATA_HOME"); !xdg.empty()) return pathFromUtf8(xdg) / "galaxy-angel-2-pc";
        return pathFromUtf8(getEnv("HOME")) / ".local" / "share" / "galaxy-angel-2-pc";
#endif
    }

    void rememberInstallDir(const fs::path &dir)
    {
        std::error_code error;
        fs::create_directories(dataDir(), error);
        std::ofstream out(settingsIni(), std::ios::trunc);
        out << "install_dir=" << pathUtf8(dir) << "\n";
    }

    std::string sha256File(const fs::path &file, Progress *progress)
    {
        std::ifstream in(file, std::ios::binary);
        if (!in) throw std::runtime_error("cannot read " + pathUtf8(file));
        Sha256 hash;
        std::vector<char> buffer(1 << 20);
        if (progress) progress->done = 0;
        while (in)
        {
            if (progress && progress->cancel) throw Cancelled();
            in.read(buffer.data(), std::streamsize(buffer.size()));
            const auto got = in.gcount();
            if (got <= 0) break;
            hash.update(buffer.data(), size_t(got));
            if (progress) progress->done += uint64_t(got);
        }
        return hash.hex();
    }

    bool folderUsable(const fs::path &dir)
    {
        std::error_code error;
        if (!fs::exists(dir, error)) return true;
        if (!fs::is_directory(dir, error)) return false;
        for (const auto &entry : fs::directory_iterator(dir, error))
            if (entry.path().filename() != kStateDir) // what a cancelled first install leaves
            {
                const Installed installed = readInstalled(dir);
                return installed.present || installed.recorded;
            }
        return true;
    }

    void download(const std::string &url, const fs::path &dest, uint64_t size, const std::string &sha256, Progress &progress)
    {
        fs::create_directories(dest.parent_path());
        const fs::path part = fs::path(dest).concat(".part");
        std::error_code error;
        fs::remove(part, error);
        progress.total = size;
        progress.done = 0;
        if (isLocal(url))
        {
            const fs::path source = localPath(url);
            std::ifstream in(source, std::ios::binary);
            if (!in) throw std::runtime_error("cannot read " + pathUtf8(source));
            std::ofstream out(part, std::ios::binary | std::ios::trunc);
            std::vector<char> buffer(1 << 20);
            while (in)
            {
                if (progress.cancel) throw Cancelled();
                in.read(buffer.data(), std::streamsize(buffer.size()));
                const auto got = in.gcount();
                if (got <= 0) break;
                out.write(buffer.data(), got);
                progress.done += uint64_t(got);
            }
            out.close();
            if (!out) throw std::runtime_error("cannot write " + pathUtf8(part) + " (disk full?)");
        }
        else
            platform::httpDownload(url, part, progress);
        if (size != 0 && fs::file_size(part, error) != size)
        {
            fs::remove(part, error);
            throw std::runtime_error("the download is incomplete (" + std::to_string(progress.done.load()) + " of " + std::to_string(size) +
                                     " bytes); try again");
        }
        if (!sha256.empty())
        {
            progress.stage = Verifying;
            progress.total = size;
            const std::string actual = sha256File(part, &progress);
            if (actual != sha256)
            {
                fs::remove(part, error);
                throw std::runtime_error("the download is damaged (SHA-256 " + actual + ", expected " + sha256 + "); try again");
            }
        }
        std::string failure;
        if (!platform::replaceFile(part, dest, failure)) throw std::runtime_error("cannot write " + pathUtf8(dest) + ": " + failure);
    }

    std::string fetchText(const std::string &url, Progress &progress)
    {
        if (isLocal(url)) return readFile(localPath(url));
        // In the player's own data folder, not the shared temp folder.
        std::error_code made;
        fs::create_directories(dataDir(), made);
        const fs::path temp = dataDir() / ("installer-manifest-" + std::to_string(
#ifdef _WIN32
                                                                       GetCurrentProcessId()
#else
                                                                       getpid()
#endif
                                                                           ) +
                                           ".json");
        platform::httpDownload(url, temp, progress);
        std::string text = readFile(temp);
        std::error_code ignored;
        fs::remove(temp, ignored);
        return text;
    }

    bool gameRunning(const fs::path &dir) { return platform::gameRunning(dir); }
    fs::path selfPath() { return platform::selfPath(); }
    std::optional<fs::path> pickFolder(const fs::path &start) { return platform::pickFolder(start); }
    bool canPickFolder() { return platform::canPickFolder(); }
    void openPath(const std::string &pathOrUrl) { platform::openPath(pathOrUrl); }

    void launchGame(const fs::path &dir) { platform::spawnDetached({pathUtf8(dir / launcherName())}, dir); }

    void install(const fs::path &dirIn, const Manifest &manifest, const InstallOptions &options, Progress &progress)
    {
        if (!manifest.package) throw std::runtime_error(std::string("this release has no build for ") + platformId());
        std::error_code error;
        fs::path dir = fs::absolute(dirIn, error);
        if (error) dir = dirIn;
        const Installed before = readInstalled(dir);
        if (fs::exists(dir, error) && !fs::is_directory(dir, error)) throw std::runtime_error(pathUtf8(dir) + " is a file, not a folder");
        if (!folderUsable(dir))
            throw std::runtime_error(pathUtf8(dir) + " already holds other files; choose an empty folder or a Galaxy Angel II PC folder");
        if (gameRunning(dir)) throw std::runtime_error("the game is running from this folder; close it first");
        fs::create_directories(stateDir(dir));
        platform::FolderLock lock(stateDir(dir));
        if (!lock.held()) throw std::runtime_error("another installer is working on this folder");
#ifdef _WIN32
        SetFileAttributesW(stateDir(dir).c_str(), FILE_ATTRIBUTE_HIDDEN);
#endif
        const fs::path downloads = stateDir(dir) / "downloads", staging = stateDir(dir) / "staging", backup = stateDir(dir) / "backup";
        fs::remove_all(staging, error);
        fs::remove_all(backup, error);
        // The unpacked copy goes whatever happens (a failure has put everything back by then).
        struct Cleanup
        {
            fs::path staging, backup;
            bool keepBackup = false;
            ~Cleanup()
            {
                std::error_code ignored;
                fs::remove_all(staging, ignored);
                if (!keepBackup) fs::remove_all(backup, ignored);
            }
        } cleanup{staging, backup};

        // 1. The archive (a verified earlier download of the same file is reused).
        const Download &package = *manifest.package;
        const fs::path archive = downloads / package.file;
        bool cached = false;
        if (fs::exists(archive, error) && fs::file_size(archive, error) == package.size)
        {
            progress.stage = Verifying;
            progress.total = package.size;
            cached = sha256File(archive, &progress) == package.sha256;
        }
        if (!cached)
        {
            fs::remove_all(downloads, error);
            progress.stage = Downloading;
            progress.setDetail(package.file);
            download(package.url, archive, package.size, package.sha256, progress);
        }

        // 2. Unpacked beside the install (same drive, so moving the files in is a rename).
        progress.stage = Extracting;
        progress.done = 0;
        progress.total = 0;
        fs::create_directories(staging);
        if (package.file.size() > 4 && package.file.substr(package.file.size() - 4) == ".zip")
            platform::extractZip(archive, staging, progress);
        else
            platform::extractTar(archive, staging, progress);
        fs::path root = staging;
        {
            std::vector<fs::path> top;
            for (const auto &entry : fs::directory_iterator(staging))
                top.push_back(entry.path());
            if (top.size() == 1 && fs::is_directory(top[0])) root = top[0];
        }
        std::vector<std::string> files;
        for (auto it = fs::recursive_directory_iterator(root); it != fs::recursive_directory_iterator(); ++it)
            if (!it->is_directory() || it->is_symlink()) files.push_back(generic(fs::relative(it->path(), root)));
        std::sort(files.begin(), files.end());
        if (std::find(files.begin(), files.end(), launcherName()) == files.end())
            throw std::runtime_error("the downloaded archive holds no game launcher; nothing was changed");

        // 3. Moving the files in; what they replace goes to backup/ until the end, and back on failure.
        if (progress.cancel) throw Cancelled();
        if (gameRunning(dir)) throw std::runtime_error("the game is running from this folder; close it first");
        progress.stage = Installing;
        progress.total = files.size();
        progress.done = 0;
        std::vector<std::string> placed, saved;
        // Puts the replaced files back; false (and backup/ kept) when one can't be.
        auto rollback = [&] {
            bool complete = true;
            std::string failure;
            for (const auto &rel : placed)
                fs::remove(dir / pathFromUtf8(rel), error);
            for (const auto &rel : saved)
                if (!platform::replaceFile(backup / pathFromUtf8(rel), dir / pathFromUtf8(rel), failure)) complete = false;
            if (!complete) cleanup.keepBackup = true;
            return complete;
        };
        try
        {
            for (const auto &rel : files)
            {
                const fs::path from = root / pathFromUtf8(rel), to = dir / pathFromUtf8(rel);
                std::string failure;
                if (fs::is_directory(to, error)) throw std::runtime_error(pathUtf8(to) + " is a folder; the update needs a file there");
                if (fs::exists(to, error) || fs::is_symlink(to, error))
                {
                    const fs::path saveTo = backup / pathFromUtf8(rel);
                    fs::create_directories(saveTo.parent_path());
                    if (!platform::replaceFile(to, saveTo, failure))
                        throw std::runtime_error("cannot replace " + pathUtf8(to) + ": " + failure + " (is the game or a file from it open?)");
                    saved.push_back(rel);
                }
                fs::create_directories(to.parent_path());
                if (!platform::replaceFile(from, to, failure)) throw std::runtime_error("cannot install " + pathUtf8(to) + ": " + failure);
                placed.push_back(rel);
                ++progress.done;
            }
        }
        catch (const std::exception &e)
        {
            if (!rollback())
                throw std::runtime_error(std::string(e.what()) + "; some replaced files could not be put back: they are in " +
                                         pathUtf8(backup) + " (reinstall to repair)");
            throw;
        }

        // 4. Files the previous version had and this one dropped.
        auto comparable = [](std::string rel) {
#ifdef _WIN32
            // NTFS names ignore case: Foo.dll -> foo.dll is the same file, not a dropped one.
            for (char &c : rel)
                if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
#endif
            return rel;
        };
        std::set<std::string> current;
        for (const auto &rel : files)
            current.insert(comparable(rel));
        for (const auto &rel : before.files)
            if (!current.count(comparable(rel)) && safeRecorded(rel))
            {
                const fs::path stale = dir / pathFromUtf8(rel);
                fs::remove(stale, error);
                pruneEmpty(stale.parent_path(), dir);
            }

        // 5. This installer, when the package has none (the running one stays where it is).
        const fs::path self = platform::selfPath();
        if (!fs::exists(dir / installerName(), error) && !self.empty())
        {
            fs::copy_file(self, dir / installerName(), fs::copy_options::overwrite_existing, error);
            if (!error) files.push_back(installerName());
        }

        writeRecord(dir, manifest.version, files);
        progress.stage = Finished;
        fs::remove_all(staging, error);
        fs::remove_all(backup, error); // a replaced running installer stays until the next run cleans it up
        fs::remove_all(downloads, error);
        if (options.shortcuts) platform::createShortcuts(dir, manifest.version, folderSize(dir));
        rememberInstallDir(dir);
    }

    void uninstall(const fs::path &dir, Progress &progress)
    {
        const Installed installed = readInstalled(dir);
        if (!installed.recorded)
            throw std::runtime_error("this folder was not set up by the installer; delete it yourself (your saves are in " +
                                     pathUtf8(dataDir()) + ")");
        if (gameRunning(dir)) throw std::runtime_error("the game is running from this folder; close it first");
        progress.stage = Removing;
        progress.total = installed.files.size();
        progress.done = 0;
        std::error_code error;
        const fs::path self = platform::selfPath();
        bool selfInside = false;
        {
        platform::FolderLock lock(stateDir(dir));
        if (!lock.held()) throw std::runtime_error("another installer is working on this folder");
        for (const auto &rel : installed.files)
        {
            if (!safeRecorded(rel)) continue;
            const fs::path file = dir / pathFromUtf8(rel);
            if (!self.empty() && fs::equivalent(file, self, error))
            {
                selfInside = true;
                continue;
            }
            fs::remove(file, error);
            pruneEmpty(file.parent_path(), dir);
            ++progress.done;
        }
        }
        platform::removeShortcuts();
        fs::remove_all(stateDir(dir), error);
        fs::remove(settingsIni(), error);
        if (selfInside)
            platform::deleteAfterExit(dir, self);
        else
            fs::remove(dir, error); // only when empty
        progress.stage = Finished;
    }

    void selfUpdate(const Manifest &manifest, const std::vector<std::string> &arguments, Progress &progress)
    {
        if (!manifest.installer) throw std::runtime_error("no installer build for this platform in the release");
        const fs::path self = platform::selfPath();
        const fs::path fresh = fs::path(self).concat(".new"), old = fs::path(self).concat(".old");
        progress.stage = Downloading;
        progress.setDetail(manifest.installer->file);
        download(manifest.installer->url, fresh, manifest.installer->size, manifest.installer->sha256, progress);
#ifndef _WIN32
        chmod(fresh.c_str(), 0755);
#endif
        std::error_code error;
        fs::remove(old, error);
        std::string failure;
        // A running program can be renamed (not overwritten) on Windows.
        if (!platform::replaceFile(self, old, failure)) throw std::runtime_error("cannot replace the installer: " + failure);
        if (!platform::replaceFile(fresh, self, failure))
        {
            platform::replaceFile(old, self, failure);
            throw std::runtime_error("cannot replace the installer: " + failure);
        }
        std::vector<std::string> command{pathUtf8(self)};
        command.insert(command.end(), arguments.begin(), arguments.end());
        platform::spawnDetached(command, self.parent_path());
        progress.stage = Finished;
    }

    void cleanupAfterSelfUpdate()
    {
        const fs::path self = platform::selfPath();
        if (self.empty()) return;
        std::error_code error;
        // An update of this installer's own folder leaves the replaced (then running) copy of it in backup/.
        if (fs::exists(stateDir(self.parent_path()) / "backup", error))
        {
            platform::FolderLock lock(stateDir(self.parent_path()));
            if (lock.held()) fs::remove_all(stateDir(self.parent_path()) / "backup", error);
        }
        const fs::path old = fs::path(self).concat(".old");
        if (!fs::exists(old, error)) return;
        // The previous process may still be exiting.
        std::thread([old] {
            for (int attempt = 0; attempt < 20; ++attempt)
            {
                std::error_code e;
                if (fs::remove(old, e) || !fs::exists(old, e)) return;
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            }
        }).detach();
    }
}
