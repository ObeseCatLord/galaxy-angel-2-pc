// Galaxy Angel II PC installer and updater: the logic shared by the window (main.cpp) and the command line.
//
// Each published release carries ga2-manifest.json (tools/release_manifest.py): the release's version, date and
// notes, and per platform the archive's URL, size and SHA-256, plus the installer's own builds. The installer
// fetches the newest one (releases/latest/download/ga2-manifest.json), downloads the archive for this platform,
// checks its SHA-256, unpacks it beside the install folder and moves its files in, keeping what it replaced
// until everything is in place (an error puts the old files back). The files it installed are recorded in
// <install>/.ga2-installer/installed.json, so an update deletes the files a newer version no longer ships and
// an uninstall removes exactly what it installed. Files it did not install are never touched, and saves,
// settings, imported discs and mods live in the separate data folder (%APPDATA%\ga2, ~/.local/share/ga2).
#pragma once

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace inst
{
    namespace fs = std::filesystem;

    // windows-x86_64, linux-x86_64 or linux-arm64: the key of this build's archive in the manifest.
    const char *platformId();
    // The installer's own version (native/installer/VERSION).
    const char *installerVersion();

    struct Download
    {
        std::string file, url, sha256;
        uint64_t size = 0;
    };

    struct Manifest
    {
        std::string name, version, tag, date, notes, page;
        std::optional<Download> package;   // this platform's archive
        std::string installerVersion;      // newest installer
        std::optional<Download> installer; // this platform's installer build
    };

    // Where the manifest comes from: $GA2_INSTALLER_MANIFEST (a URL or a local file, for testing) or the
    // newest published release of the project.
    std::string manifestSource();
    Manifest parseManifest(const std::string &json);

    // -1, 0 or 1 comparing dotted version numbers ("0.10.0" > "0.9.1"); a leading "v" is ignored.
    int compareVersions(const std::string &a, const std::string &b);

    // Progress of a long operation, written by the worker thread, read by the window.
    struct Progress
    {
        std::atomic<int> stage{0}; // Stage
        std::atomic<uint64_t> done{0}, total{0};
        std::atomic<bool> cancel{false};
        std::mutex mutex;
        std::string detail; // under mutex
        void setDetail(const std::string &text)
        {
            std::lock_guard<std::mutex> lock(mutex);
            detail = text;
        }
        std::string getDetail()
        {
            std::lock_guard<std::mutex> lock(mutex);
            return detail;
        }
    };
    enum Stage
    {
        Idle,
        FetchingManifest,
        Downloading,
        Verifying,
        Extracting,
        Installing,
        Removing,
        Finished
    };

    struct Cancelled : std::exception
    {
        const char *what() const noexcept override { return "cancelled"; }
    };

    // An install folder's state.
    struct Installed
    {
        bool present = false;  // the game's launcher is there
        bool recorded = false; // installed by this installer (installed.json)
        std::string version;   // "" when unknown
        std::vector<std::string> files;
    };
    Installed readInstalled(const fs::path &dir);

    // The launcher's file name (ga2.exe / ga2) and the installer's (ga2-installer.exe / ga2-installer).
    const char *launcherName();
    const char *installerName();

    // The folder this run should offer: the installer's own folder when it holds the game, else the folder
    // remembered from the last install, else the platform's usual place for per-user programs.
    fs::path defaultInstallDir();
    fs::path dataDir(); // the game's data folder (saves, settings, discs)

    // Whether `dir` may be installed into: missing, empty (or holding only what a cancelled install left), or
    // a Galaxy Angel II PC folder already.
    bool folderUsable(const fs::path &dir);

    // Downloads `url` (https, or a local path / file:// URL for testing) to `dest`, verifying size and SHA-256.
    void download(const std::string &url, const fs::path &dest, uint64_t size, const std::string &sha256, Progress &progress);
    std::string fetchText(const std::string &url, Progress &progress);
    std::string sha256File(const fs::path &file, Progress *progress = nullptr);

    struct InstallOptions
    {
        bool shortcuts = true;
    };
    // Installs or updates to `manifest`'s package in `dir`. Throws with a message for the player.
    void install(const fs::path &dir, const Manifest &manifest, const InstallOptions &options, Progress &progress);
    // Removes what the installer installed (and its shortcuts); the data folder stays.
    void uninstall(const fs::path &dir, Progress &progress);

    // A program from `dir` (the game) is running: updating would fail on Windows and confuse it elsewhere.
    bool gameRunning(const fs::path &dir);

    // Replaces the running installer with the manifest's newer build and starts it with `arguments`.
    void selfUpdate(const Manifest &manifest, const std::vector<std::string> &arguments, Progress &progress);
    // Deletes what a previous self-update left behind.
    void cleanupAfterSelfUpdate();

    fs::path selfPath();
    void launchGame(const fs::path &dir);
    void openPath(const std::string &pathOrUrl);
    // A native folder picker (Windows; zenity or kdialog on Linux). Empty when cancelled or unavailable.
    std::optional<fs::path> pickFolder(const fs::path &start);
    bool canPickFolder();

    std::string pathUtf8(const fs::path &path);
    fs::path pathFromUtf8(const std::string &text);
    std::string getEnv(const char *name);

    // Remembers the folder of the last install (in the data folder's installer.ini).
    void rememberInstallDir(const fs::path &dir);
}
