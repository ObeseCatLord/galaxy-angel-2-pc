// What differs between Windows and Linux: processes, HTTPS downloads, archives, shortcuts, dialogs.
#pragma once

#include "installer.h"

#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace inst::platform
{
    // Runs a program to completion; stdout (and stderr) into `output` when given. `keepGoing` is asked every
    // ~100 ms; false stops the program and throws Cancelled. Returns the exit code, -1 when it did not start.
    int run(const std::vector<std::string> &argv, std::string *output, const std::function<bool()> &keepGoing = {},
            const fs::path &cwd = {});
    void spawnDetached(const std::vector<std::string> &argv, const fs::path &cwd);
    std::optional<fs::path> findProgram(const std::string &name);

    // HTTPS GET of `url` into `dest`; progress.done follows the bytes written.
    void httpDownload(const std::string &url, const fs::path &dest, Progress &progress);

    // Unpacks a .zip (any platform) or, on Linux, a .tar.* with the system's tar.
    void extractZip(const fs::path &archive, const fs::path &dest, Progress &progress);
    void extractTar(const fs::path &archive, const fs::path &dest, Progress &progress);

    // Moves `from` over `to` (replacing it). False with errno-like text in `error` when it can't.
    bool replaceFile(const fs::path &from, const fs::path &to, std::string &error);

    // Start-menu / application-menu entries for the game and the updater (and on Windows, the Apps & features
    // entry). Replaced on every install; removed by uninstall.
    void createShortcuts(const fs::path &dir, const std::string &version, uint64_t sizeBytes);
    void removeShortcuts();

    bool gameRunning(const fs::path &dir);
    fs::path selfPath();
    std::optional<fs::path> pickFolder(const fs::path &start);
    bool canPickFolder();
    void openPath(const std::string &pathOrUrl);
    // Deletes `dir` (with the running installer in it) once this process has exited (Windows; elsewhere at once).
    void deleteAfterExit(const fs::path &dir, const fs::path &self);
    // The helper's side (--finish-uninstall <pid> <file> <dir>): waits for `pid`, deletes `file` and the empty `dir`.
    void finishUninstall(unsigned long pid, const fs::path &file, const fs::path &dir);

    // Held while installing or removing: a second installer in the same folder can't take it.
    class FolderLock
    {
    public:
        explicit FolderLock(const fs::path &stateDir);
        ~FolderLock();
        FolderLock(const FolderLock &) = delete;
        FolderLock &operator=(const FolderLock &) = delete;
        bool held() const
        {
#ifdef _WIN32
            return m_handle != nullptr;
#else
            return m_fd >= 0;
#endif
        }

    private:
#ifdef _WIN32
        void *m_handle = nullptr;
#else
        int m_fd = -1;
#endif
    };
}
