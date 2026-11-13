#include "platform.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <thread>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <tlhelp32.h>
#include <winhttp.h>
#else
#include <csignal>
#include <fcntl.h>
#include <spawn.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
#endif

// raylib's deflate decoder (external/sinfl.h, compiled into raylib with its compression API).
extern "C" int sinflate(void *out, int cap, const void *in, int size);

namespace inst::platform
{
    namespace
    {
        bool keepGoingOk(const std::function<bool()> &keepGoing) { return !keepGoing || keepGoing(); }

#ifdef _WIN32
        std::wstring wide(const std::string &text)
        {
            if (text.empty()) return {};
            const int n = MultiByteToWideChar(CP_UTF8, 0, text.data(), int(text.size()), nullptr, 0);
            std::wstring out(size_t(n), L'\0');
            MultiByteToWideChar(CP_UTF8, 0, text.data(), int(text.size()), out.data(), n);
            return out;
        }
        std::string narrow(const std::wstring &text)
        {
            if (text.empty()) return {};
            const int n = WideCharToMultiByte(CP_UTF8, 0, text.data(), int(text.size()), nullptr, 0, nullptr, nullptr);
            std::string out(size_t(n), '\0');
            WideCharToMultiByte(CP_UTF8, 0, text.data(), int(text.size()), out.data(), n, nullptr, nullptr);
            return out;
        }
        // CommandLineToArgvW's quoting rules.
        std::wstring quoteArgument(const std::wstring &arg)
        {
            if (!arg.empty() && arg.find_first_of(L" \t\"") == std::wstring::npos) return arg;
            std::wstring out = L"\"";
            size_t slashes = 0;
            for (wchar_t c : arg)
            {
                if (c == L'\\')
                    ++slashes;
                else if (c == L'"')
                {
                    out.append(slashes * 2 + 1, L'\\');
                    slashes = 0;
                }
                else
                    slashes = 0;
                out += c;
            }
            out.append(slashes, L'\\');
            out += L'"';
            return out;
        }
        std::wstring commandLine(const std::vector<std::string> &argv)
        {
            std::wstring line;
            for (const auto &arg : argv)
            {
                if (!line.empty()) line += L' ';
                line += quoteArgument(wide(arg));
            }
            return line;
        }
        std::string lastError(DWORD code = GetLastError())
        {
            wchar_t *buffer = nullptr;
            FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                           nullptr, code, 0, reinterpret_cast<LPWSTR>(&buffer), 0, nullptr);
            std::string text = buffer ? narrow(buffer) : "error " + std::to_string(code);
            if (buffer) LocalFree(buffer);
            while (!text.empty() && (text.back() == '\n' || text.back() == '\r' || text.back() == '.'))
                text.pop_back();
            return text + " (" + std::to_string(code) + ")";
        }
        fs::path knownFolder(REFKNOWNFOLDERID id)
        {
            PWSTR path = nullptr;
            fs::path out;
            if (SUCCEEDED(SHGetKnownFolderPath(id, 0, nullptr, &path))) out = path;
            CoTaskMemFree(path);
            return out;
        }
#endif
    }

    // --- processes --------------------------------------------------------------------------------------------

    int run(const std::vector<std::string> &argv, std::string *output, const std::function<bool()> &keepGoing, const fs::path &cwd)
    {
#ifdef _WIN32
        SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
        HANDLE readEnd = nullptr, writeEnd = nullptr;
        if (output)
        {
            if (!CreatePipe(&readEnd, &writeEnd, &sa, 0)) return -1;
            SetHandleInformation(readEnd, HANDLE_FLAG_INHERIT, 0);
        }
        STARTUPINFOW si{};
        si.cb = sizeof(si);
        if (output)
        {
            si.dwFlags = STARTF_USESTDHANDLES;
            si.hStdOutput = writeEnd;
            si.hStdError = writeEnd;
            si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        }
        PROCESS_INFORMATION pi{};
        std::wstring line = commandLine(argv);
        const std::wstring dir = cwd.empty() ? std::wstring() : cwd.wstring();
        const BOOL ok = CreateProcessW(nullptr, line.data(), nullptr, nullptr, output ? TRUE : FALSE, CREATE_NO_WINDOW,
                                       nullptr, dir.empty() ? nullptr : dir.c_str(), &si, &pi);
        if (writeEnd) CloseHandle(writeEnd);
        if (!ok)
        {
            if (readEnd) CloseHandle(readEnd);
            return -1;
        }
        std::thread reader;
        if (output)
            reader = std::thread([readEnd, output] {
                char buffer[4096];
                DWORD got = 0;
                while (ReadFile(readEnd, buffer, sizeof(buffer), &got, nullptr) && got > 0)
                    output->append(buffer, got);
            });
        bool cancelled = false;
        while (WaitForSingleObject(pi.hProcess, 100) == WAIT_TIMEOUT)
            if (!keepGoingOk(keepGoing))
            {
                TerminateProcess(pi.hProcess, 1);
                cancelled = true;
            }
        DWORD code = 0;
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        if (reader.joinable()) reader.join();
        if (readEnd) CloseHandle(readEnd);
        if (cancelled) throw Cancelled();
        return int(code);
#else
        int pipeFds[2] = {-1, -1};
        // Close-on-exec, so programs started meanwhile (xdg-open, a browser) don't hold the write end open.
        if (output && pipe2(pipeFds, O_CLOEXEC) != 0) return -1;
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        if (output)
        {
            posix_spawn_file_actions_adddup2(&actions, pipeFds[1], 1);
            posix_spawn_file_actions_adddup2(&actions, pipeFds[1], 2);
            posix_spawn_file_actions_addclose(&actions, pipeFds[0]);
            posix_spawn_file_actions_addclose(&actions, pipeFds[1]);
        }
        std::vector<std::string> args = argv;
        std::vector<char *> raw;
        for (auto &arg : args)
            raw.push_back(arg.data());
        raw.push_back(nullptr);
        if (!cwd.empty()) posix_spawn_file_actions_addchdir_np(&actions, cwd.c_str());
        pid_t pid = -1;
        const int error = posix_spawnp(&pid, raw[0], &actions, nullptr, raw.data(), environ);
        posix_spawn_file_actions_destroy(&actions);
        if (output) close(pipeFds[1]);
        if (error != 0)
        {
            if (output) close(pipeFds[0]);
            return -1;
        }
        std::thread reader;
        if (output)
            reader = std::thread([fd = pipeFds[0], output] {
                char buffer[4096];
                ssize_t got;
                while ((got = read(fd, buffer, sizeof(buffer))) > 0)
                    output->append(buffer, size_t(got));
                close(fd);
            });
        int status = 0;
        bool cancelled = false;
        for (;;)
        {
            const pid_t done = waitpid(pid, &status, WNOHANG);
            if (done == pid) break;
            if (done < 0)
            {
                status = 0;
                break;
            }
            if (!cancelled && !keepGoingOk(keepGoing))
            {
                kill(pid, SIGTERM);
                cancelled = true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        if (reader.joinable()) reader.join();
        if (cancelled) throw Cancelled();
        if (WIFEXITED(status)) return WEXITSTATUS(status);
        return 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
#endif
    }

    void spawnDetached(const std::vector<std::string> &argv, const fs::path &cwd)
    {
#ifdef _WIN32
        STARTUPINFOW si{};
        si.cb = sizeof(si);
        PROCESS_INFORMATION pi{};
        std::wstring line = commandLine(argv);
        const std::wstring dir = cwd.wstring();
        if (!CreateProcessW(nullptr, line.data(), nullptr, nullptr, FALSE, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, nullptr,
                            dir.empty() ? nullptr : dir.c_str(), &si, &pi))
            throw std::runtime_error("could not start " + argv[0] + ": " + lastError());
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
#else
        std::vector<std::string> args = argv;
        std::vector<char *> raw;
        for (auto &arg : args)
            raw.push_back(arg.data());
        raw.push_back(nullptr);
        posix_spawnattr_t attr;
        posix_spawnattr_init(&attr);
#ifdef POSIX_SPAWN_SETSID
        posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSID);
#else
        posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
#endif
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        if (!cwd.empty()) posix_spawn_file_actions_addchdir_np(&actions, cwd.c_str());
        pid_t pid = -1;
        const int error = posix_spawnp(&pid, raw[0], &actions, &attr, raw.data(), environ);
        posix_spawn_file_actions_destroy(&actions);
        posix_spawnattr_destroy(&attr);
        if (error != 0) throw std::runtime_error("could not start " + argv[0] + ": " + std::strerror(error));
        // Reap it when it ends (it is ours until then); a detached thread waits without holding anything up.
        std::thread([pid] {
            int status;
            waitpid(pid, &status, 0);
        }).detach();
#endif
    }

    std::optional<fs::path> findProgram(const std::string &name)
    {
#ifdef _WIN32
        wchar_t system[MAX_PATH];
        const UINT n = GetSystemDirectoryW(system, MAX_PATH);
        if (n > 0 && n < MAX_PATH)
        {
            const fs::path candidate = fs::path(system) / (wide(name) + L".exe");
            if (fs::exists(candidate)) return candidate;
        }
        return std::nullopt;
#else
        const char *path = std::getenv("PATH");
        std::string list = path ? path : "/usr/local/bin:/usr/bin:/bin";
        size_t start = 0;
        while (start <= list.size())
        {
            const size_t end = std::min(list.find(':', start), list.size());
            const std::string part = list.substr(start, end - start);
            if (!part.empty())
            {
                const fs::path candidate = fs::path(part) / name;
                if (access(candidate.c_str(), X_OK) == 0) return candidate;
            }
            start = end + 1;
        }
        return std::nullopt;
#endif
    }

    // --- downloads ----------------------------------------------------------------------------------------------

    void httpDownload(const std::string &url, const fs::path &dest, Progress &progress)
    {
        progress.done = 0;
#ifdef _WIN32
        const std::wstring wurl = wide(url);
        URL_COMPONENTS parts{};
        parts.dwStructSize = sizeof(parts);
        wchar_t host[256], path[4096];
        parts.lpszHostName = host;
        parts.dwHostNameLength = 256;
        parts.lpszUrlPath = path;
        parts.dwUrlPathLength = 4096;
        if (!WinHttpCrackUrl(wurl.c_str(), 0, 0, &parts) || parts.nScheme != INTERNET_SCHEME_HTTPS)
            throw std::runtime_error("not an https address: " + url);
        struct Handle
        {
            HINTERNET h = nullptr;
            ~Handle()
            {
                if (h) WinHttpCloseHandle(h);
            }
        } session, connection, request;
        session.h = WinHttpOpen(L"ga2-installer", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (!session.h) // before Windows 8.1
            session.h = WinHttpOpen(L"ga2-installer", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (!session.h) throw std::runtime_error("WinHTTP: " + lastError());
        DWORD protocols = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2;
#ifdef WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3
        protocols |= WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3;
        if (!WinHttpSetOption(session.h, WINHTTP_OPTION_SECURE_PROTOCOLS, &protocols, sizeof(protocols)))
            protocols = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2;
#endif
        WinHttpSetOption(session.h, WINHTTP_OPTION_SECURE_PROTOCOLS, &protocols, sizeof(protocols));
        WinHttpSetTimeouts(session.h, 30000, 30000, 30000, 60000);
        connection.h = WinHttpConnect(session.h, std::wstring(host, parts.dwHostNameLength).c_str(), parts.nPort, 0);
        if (!connection.h) throw std::runtime_error("cannot connect: " + lastError());
        request.h = WinHttpOpenRequest(connection.h, L"GET", std::wstring(path, parts.dwUrlPathLength).c_str(), nullptr,
                                       WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE);
        if (!request.h) throw std::runtime_error("request failed: " + lastError());
        if (!WinHttpSendRequest(request.h, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
            !WinHttpReceiveResponse(request.h, nullptr))
            throw std::runtime_error("download failed: " + lastError());
        DWORD status = 0, size = sizeof(status);
        WinHttpQueryHeaders(request.h, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &status,
                            &size, WINHTTP_NO_HEADER_INDEX);
        if (status != 200) throw std::runtime_error("server answered " + std::to_string(status) + " for " + url);
        std::ofstream out(dest, std::ios::binary | std::ios::trunc);
        if (!out) throw std::runtime_error("cannot write " + pathUtf8(dest));
        std::vector<char> buffer(1 << 16);
        for (;;)
        {
            if (progress.cancel) throw Cancelled();
            DWORD got = 0;
            if (!WinHttpReadData(request.h, buffer.data(), DWORD(buffer.size()), &got))
                throw std::runtime_error("download interrupted: " + lastError());
            if (got == 0) break;
            out.write(buffer.data(), got);
            if (!out) throw std::runtime_error("cannot write " + pathUtf8(dest) + " (disk full?)");
            progress.done += got;
        }
#else
        std::vector<std::string> command;
        if (auto curl = findProgram("curl"))
            command = {curl->string(), "-fsSL", "--retry", "3", "--connect-timeout", "30", "--proto", "=https",
                       "--proto-redir", "=https", "-A", "ga2-installer", "-o", dest.string(), url};
        else if (auto wget = findProgram("wget"))
            command = {wget->string(), "-q", "--tries=3", "-U", "ga2-installer", "-O", dest.string(), url};
        else
            throw std::runtime_error("downloading needs curl or wget; install one (e.g. sudo apt install curl)");
        std::string output;
        const int code = run(command, &output, [&] {
            std::error_code error;
            const auto size = fs::file_size(dest, error);
            if (!error) progress.done = size;
            return !progress.cancel.load();
        });
        if (code != 0)
        {
            while (!output.empty() && (output.back() == '\n' || output.back() == '\r'))
                output.pop_back();
            throw std::runtime_error("download failed (" + fs::path(command[0]).filename().string() + " exit " + std::to_string(code) +
                                     (output.empty() ? ")" : "): " + output));
        }
        std::error_code error;
        progress.done = fs::file_size(dest, error);
#endif
    }

    // --- archives -----------------------------------------------------------------------------------------------

    namespace
    {
        uint16_t le16(const uint8_t *p) { return uint16_t(p[0] | p[1] << 8); }
        uint32_t le32(const uint8_t *p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
        uint64_t le64(const uint8_t *p) { return uint64_t(le32(p)) | uint64_t(le32(p + 4)) << 32; }

        // A path from an archive, made safe: relative, no "..", no drive or root.
        fs::path safeRelative(const std::string &name)
        {
            fs::path out;
            size_t start = 0;
            while (start <= name.size())
            {
                size_t end = name.find_first_of("/\\", start);
                if (end == std::string::npos) end = name.size();
                const std::string part = name.substr(start, end - start);
                if (part == "..") throw std::runtime_error("archive entry leaves its folder: " + name);
                if (!part.empty() && part != ".")
                {
                    if (part.find(':') != std::string::npos) throw std::runtime_error("bad archive entry: " + name);
                    out /= pathFromUtf8(part);
                }
                start = end + 1;
            }
            return out;
        }
    }

    void extractZip(const fs::path &archive, const fs::path &dest, Progress &progress)
    {
        std::ifstream in(archive, std::ios::binary);
        if (!in) throw std::runtime_error("cannot open " + pathUtf8(archive));
        in.seekg(0, std::ios::end);
        const uint64_t fileSize = uint64_t(in.tellg());
        // End of central directory: in the last 64 KiB + 22 bytes.
        const uint64_t tail = std::min<uint64_t>(fileSize, 65557);
        std::vector<uint8_t> end(tail);
        in.seekg(std::streamoff(fileSize - tail));
        in.read(reinterpret_cast<char *>(end.data()), std::streamsize(tail));
        int64_t eocd = -1;
        for (int64_t i = int64_t(tail) - 22; i >= 0; --i)
            if (le32(&end[size_t(i)]) == 0x06054b50)
            {
                eocd = i;
                break;
            }
        if (eocd < 0) throw std::runtime_error("not a zip archive: " + pathUtf8(archive));
        uint64_t entries = le16(&end[size_t(eocd) + 10]);
        uint64_t cdSize = le32(&end[size_t(eocd) + 12]);
        uint64_t cdOffset = le32(&end[size_t(eocd) + 16]);
        if (entries == 0xFFFF || cdOffset == 0xFFFFFFFFu)
        {
            // ZIP64: locator just before the end record.
            if (eocd < 20 || le32(&end[size_t(eocd) - 20]) != 0x07064b50) throw std::runtime_error("damaged zip64 archive");
            const uint64_t at = le64(&end[size_t(eocd) - 20 + 8]);
            uint8_t record[56];
            in.seekg(std::streamoff(at));
            in.read(reinterpret_cast<char *>(record), sizeof(record));
            if (!in || le32(record) != 0x06064b50) throw std::runtime_error("damaged zip64 archive");
            entries = le64(record + 32);
            cdSize = le64(record + 40);
            cdOffset = le64(record + 48);
        }
        if (cdOffset > fileSize || cdSize > fileSize - cdOffset || entries > cdSize / 46)
            throw std::runtime_error("damaged zip archive (central directory)");
        std::vector<uint8_t> cd(static_cast<size_t>(cdSize));
        in.seekg(std::streamoff(cdOffset));
        in.read(reinterpret_cast<char *>(cd.data()), std::streamsize(cdSize));
        if (!in) throw std::runtime_error("damaged zip archive (central directory)");

        struct Entry
        {
            std::string name;
            uint16_t method;
            uint64_t compressed, size, offset;
        };
        std::vector<Entry> list;
        uint64_t total = 0;
        size_t p = 0;
        for (uint64_t i = 0; i < entries; ++i)
        {
            if (p + 46 > cd.size() || le32(&cd[p]) != 0x02014b50) throw std::runtime_error("damaged zip archive (entry)");
            Entry e;
            e.method = le16(&cd[p + 10]);
            e.compressed = le32(&cd[p + 20]);
            e.size = le32(&cd[p + 24]);
            const uint16_t nameLength = le16(&cd[p + 28]), extraLength = le16(&cd[p + 30]), commentLength = le16(&cd[p + 32]);
            e.offset = le32(&cd[p + 42]);
            if (p + 46 + nameLength + extraLength + le16(&cd[p + 32]) > cd.size()) throw std::runtime_error("damaged zip archive (entry)");
            e.name.assign(reinterpret_cast<const char *>(&cd[p + 46]), nameLength);
            // ZIP64 extra field: the 0xFFFFFFFF fields, in order.
            const size_t extraEnd = p + 46 + nameLength + extraLength;
            for (size_t x = p + 46 + nameLength; x + 4 <= extraEnd;)
            {
                const uint16_t id = le16(&cd[x]), length = le16(&cd[x + 2]);
                if (x + 4 + length > extraEnd) break;
                if (id == 0x0001)
                {
                    size_t q = x + 4;
                    if (e.size == 0xFFFFFFFFu && q + 8 <= x + 4 + length) e.size = le64(&cd[q]), q += 8;
                    if (e.compressed == 0xFFFFFFFFu && q + 8 <= x + 4 + length) e.compressed = le64(&cd[q]), q += 8;
                    if (e.offset == 0xFFFFFFFFu && q + 8 <= x + 4 + length) e.offset = le64(&cd[q]), q += 8;
                }
                x += 4 + length;
            }
            p += 46 + nameLength + extraLength + commentLength;
            total += e.size;
            list.push_back(std::move(e));
        }
        progress.total = total;
        progress.done = 0;
        std::vector<uint8_t> packed, unpacked;
        for (const Entry &e : list)
        {
            if (progress.cancel) throw Cancelled();
            const fs::path relative = safeRelative(e.name);
            if (relative.empty()) continue;
            const fs::path target = dest / relative;
            if (!e.name.empty() && (e.name.back() == '/' || e.name.back() == '\\'))
            {
                fs::create_directories(target);
                continue;
            }
            uint8_t local[30];
            in.seekg(std::streamoff(e.offset));
            in.read(reinterpret_cast<char *>(local), sizeof(local));
            if (!in || le32(local) != 0x04034b50) throw std::runtime_error("damaged zip archive at " + e.name);
            const uint64_t dataAt = e.offset + 30 + le16(local + 26) + le16(local + 28);
            if (e.compressed > 0x7FFFFFF0u || e.size > 0x7FFFFFF0u) throw std::runtime_error("archive entry too large: " + e.name);
            // Deflate can't expand more than ~1032:1; anything else is a damaged or hostile archive.
            if (dataAt > fileSize || e.compressed > fileSize - dataAt || (e.method == 8 && e.size > e.compressed * 1032 + 64))
                throw std::runtime_error("damaged zip archive at " + e.name);
            packed.resize(size_t(e.compressed));
            in.seekg(std::streamoff(dataAt));
            in.read(reinterpret_cast<char *>(packed.data()), std::streamsize(packed.size()));
            if (!in) throw std::runtime_error("damaged zip archive at " + e.name);
            const uint8_t *data = packed.data();
            if (e.method == 8)
            {
                unpacked.resize(size_t(e.size) + 65536); // sinfl's copy loops may write a little past its limit
                const int got = sinflate(unpacked.data(), int(unpacked.size()), packed.data(), int(packed.size()));
                if (got < 0 || uint64_t(got) != e.size) throw std::runtime_error("damaged zip archive (data) at " + e.name);
                data = unpacked.data();
            }
            else if (e.method != 0)
                throw std::runtime_error("unsupported zip compression in " + e.name);
            else if (e.compressed != e.size)
                throw std::runtime_error("damaged zip archive at " + e.name);
            fs::create_directories(target.parent_path());
            std::ofstream out(target, std::ios::binary | std::ios::trunc);
            out.write(reinterpret_cast<const char *>(data), std::streamsize(e.size));
            out.close();
            if (!out) throw std::runtime_error("cannot write " + pathUtf8(target) + " (disk full?)");
            progress.done += e.size;
        }
    }

    void extractTar(const fs::path &archive, const fs::path &dest, Progress &progress)
    {
#ifdef _WIN32
        (void)archive, (void)dest, (void)progress;
        throw std::runtime_error("tar archives are not used on Windows");
#else
        const auto tar = findProgram("tar");
        if (!tar) throw std::runtime_error("unpacking needs tar");
        progress.total = 0;
        std::string output;
        const int code = run({tar->string(), "-xf", archive.string(), "-C", dest.string()}, &output,
                             [&] { return !progress.cancel.load(); });
        if (code != 0)
            throw std::runtime_error("unpacking failed (tar exit " + std::to_string(code) + "): " + output +
                                     (output.find("xz") != std::string::npos ? " (install xz-utils)" : ""));
#endif
    }

    bool replaceFile(const fs::path &from, const fs::path &to, std::string &error)
    {
#ifdef _WIN32
        if (MoveFileExW(from.c_str(), to.c_str(), MOVEFILE_REPLACE_EXISTING)) return true;
        error = lastError();
        return false;
#else
        if (std::rename(from.c_str(), to.c_str()) == 0) return true;
        error = std::strerror(errno);
        return false;
#endif
    }

    // --- one installer per folder ------------------------------------------------------------------------------

    FolderLock::FolderLock(const fs::path &stateDir)
    {
        std::error_code error;
        fs::create_directories(stateDir, error);
        const fs::path file = stateDir / "lock";
#ifdef _WIN32
        HANDLE h = CreateFileW(file.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_HIDDEN, nullptr);
        if (h != INVALID_HANDLE_VALUE) m_handle = h;
#else
        const int fd = open(file.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0644);
        if (fd >= 0)
        {
            if (flock(fd, LOCK_EX | LOCK_NB) == 0)
                m_fd = fd;
            else
                close(fd);
        }
#endif
    }

    FolderLock::~FolderLock()
    {
#ifdef _WIN32
        if (m_handle) CloseHandle(static_cast<HANDLE>(m_handle));
#else
        if (m_fd >= 0) close(m_fd);
#endif
    }

    // --- shortcuts --------------------------------------------------------------------------------------------

#ifdef _WIN32
    namespace
    {
        const wchar_t *kUninstallKey = L"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\GalaxyAngel2PC";

        void writeLink(const fs::path &link, const fs::path &target, const fs::path &dir, const std::wstring &description)
        {
            IShellLinkW *shell = nullptr;
            if (FAILED(CoCreateInstance(CLSID_ShellLink, nullptr, CLSCTX_INPROC_SERVER, IID_IShellLinkW, reinterpret_cast<void **>(&shell))))
                return;
            shell->SetPath(target.c_str());
            shell->SetWorkingDirectory(dir.c_str());
            shell->SetDescription(description.c_str());
            shell->SetIconLocation((dir / "ga2.exe").c_str(), 0);
            IPersistFile *file = nullptr;
            if (SUCCEEDED(shell->QueryInterface(IID_IPersistFile, reinterpret_cast<void **>(&file))))
            {
                std::error_code ignored;
                fs::create_directories(link.parent_path(), ignored);
                file->Save(link.c_str(), TRUE);
                file->Release();
            }
            shell->Release();
        }

        struct Com
        {
            bool ok;
            Com() : ok(SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) {}
            ~Com()
            {
                if (ok) CoUninitialize();
            }
        };

        void setString(HKEY key, const wchar_t *name, const std::wstring &value)
        {
            RegSetValueExW(key, name, 0, REG_SZ, reinterpret_cast<const BYTE *>(value.c_str()), DWORD((value.size() + 1) * sizeof(wchar_t)));
        }
        void setDword(HKEY key, const wchar_t *name, DWORD value)
        {
            RegSetValueExW(key, name, 0, REG_DWORD, reinterpret_cast<const BYTE *>(&value), sizeof(value));
        }
    }
#else
    namespace
    {
        fs::path applicationsDir()
        {
            const std::string xdg = getEnv("XDG_DATA_HOME");
            if (!xdg.empty()) return fs::path(xdg) / "applications";
            return fs::path(getEnv("HOME")) / ".local" / "share" / "applications";
        }
        // The Exec key's quoting (Desktop Entry Specification).
        std::string desktopQuote(const std::string &text)
        {
            std::string out = "\"";
            for (char c : text)
            {
                if (c == '"' || c == '`' || c == '$' || c == '\\') out += '\\';
                out += c;
            }
            return out + "\"";
        }
        void writeDesktop(const fs::path &file, const std::string &name, const std::string &comment, const fs::path &exe,
                          const fs::path &dir, const std::string &icon)
        {
            std::ofstream out(file, std::ios::trunc);
            out << "[Desktop Entry]\nType=Application\nName=" << name << "\nComment=" << comment
                << "\nExec=" << desktopQuote(exe.string()) << "\nPath=" << dir.string() << "\nIcon=" << icon
                << "\nTerminal=false\nCategories=Game;\n";
            out.close();
            chmod(file.c_str(), 0755);
        }
    }
#endif

    void createShortcuts(const fs::path &dir, const std::string &version, uint64_t sizeBytes)
    {
#ifdef _WIN32
        Com com;
        const fs::path programs = knownFolder(FOLDERID_Programs);
        if (!programs.empty())
        {
            writeLink(programs / L"Galaxy Angel II PC.lnk", dir / "ga2.exe", dir, L"Galaxy Angel II PC");
            writeLink(programs / L"Galaxy Angel II PC Updater.lnk", dir / installerName(), dir,
                      L"Install updates of Galaxy Angel II PC");
        }
        const fs::path desktop = knownFolder(FOLDERID_Desktop);
        if (!desktop.empty()) writeLink(desktop / L"Galaxy Angel II PC.lnk", dir / "ga2.exe", dir, L"Galaxy Angel II PC");
        HKEY key = nullptr;
        if (RegCreateKeyExW(HKEY_CURRENT_USER, kUninstallKey, 0, nullptr, 0, KEY_WRITE, nullptr, &key, nullptr) == ERROR_SUCCESS)
        {
            setString(key, L"DisplayName", L"Galaxy Angel II PC");
            setString(key, L"DisplayVersion", wide(version));
            setString(key, L"DisplayIcon", (dir / "ga2.exe").wstring());
            setString(key, L"InstallLocation", dir.wstring());
            setString(key, L"UninstallString", quoteArgument((dir / installerName()).wstring()) + L" --uninstall");
            setString(key, L"QuietUninstallString", quoteArgument((dir / installerName()).wstring()) + L" --uninstall --yes");
            setString(key, L"URLInfoAbout", wide(std::string("https://github.com/") + GA2_INSTALLER_REPO));
            setDword(key, L"NoModify", 1);
            setDword(key, L"NoRepair", 1);
            setDword(key, L"EstimatedSize", DWORD(sizeBytes / 1024));
            RegCloseKey(key);
        }
#else
        (void)version, (void)sizeBytes;
        const fs::path apps = applicationsDir();
        std::error_code ignored;
        fs::create_directories(apps, ignored);
        writeDesktop(apps / "galaxy-angel-2-pc.desktop", "Galaxy Angel II PC", "Galaxy Angel II trilogy (your own discs)",
                     dir / "ga2", dir, "applications-games");
        writeDesktop(apps / "galaxy-angel-2-pc-updater.desktop", "Galaxy Angel II PC Updater",
                     "Install updates of Galaxy Angel II PC", dir / installerName(), dir, "system-software-update");
#endif
    }

    void removeShortcuts()
    {
        std::error_code ignored;
#ifdef _WIN32
        const fs::path programs = knownFolder(FOLDERID_Programs), desktop = knownFolder(FOLDERID_Desktop);
        if (!programs.empty())
        {
            fs::remove(programs / L"Galaxy Angel II PC.lnk", ignored);
            fs::remove(programs / L"Galaxy Angel II PC Updater.lnk", ignored);
        }
        if (!desktop.empty()) fs::remove(desktop / L"Galaxy Angel II PC.lnk", ignored);
        RegDeleteKeyW(HKEY_CURRENT_USER, kUninstallKey);
#else
        fs::remove(applicationsDir() / "galaxy-angel-2-pc.desktop", ignored);
        fs::remove(applicationsDir() / "galaxy-angel-2-pc-updater.desktop", ignored);
#endif
    }

    // --- the running game -----------------------------------------------------------------------------------

    bool gameRunning(const fs::path &dir)
    {
        std::error_code error;
        const fs::path root = fs::weakly_canonical(dir, error);
        if (error) return false;
        const fs::path self = fs::weakly_canonical(selfPath(), error);
#ifdef _WIN32
        // An executable in use can't be opened for writing.
        for (const auto &entry : fs::directory_iterator(root, error))
        {
            const fs::path file = entry.path();
            if (file.extension() != ".exe" || fs::equivalent(file, self, error)) continue;
            HANDLE h = CreateFileW(file.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
            if (h == INVALID_HANDLE_VALUE)
            {
                if (GetLastError() == ERROR_SHARING_VIOLATION) return true;
                continue;
            }
            CloseHandle(h);
        }
        return false;
#else
        const std::string prefix = root.string() + "/";
        for (const auto &entry : fs::directory_iterator("/proc", error))
        {
            const std::string name = entry.path().filename().string();
            if (name.empty() || name.find_first_not_of("0123456789") != std::string::npos || std::stoi(name) == getpid()) continue;
            std::error_code linkError;
            const fs::path exe = fs::read_symlink(entry.path() / "exe", linkError);
            if (linkError) continue;
            const std::string text = exe.string();
            if (text.rfind(prefix, 0) == 0 && exe != self) return true;
        }
        return false;
#endif
    }

    fs::path selfPath()
    {
#ifdef _WIN32
        std::wstring buffer(MAX_PATH, L'\0');
        for (;;)
        {
            const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), DWORD(buffer.size()));
            if (length == 0) return {};
            if (length < buffer.size())
            {
                buffer.resize(length);
                return fs::path(buffer);
            }
            buffer.resize(buffer.size() * 2);
        }
#else
        std::error_code error;
        return fs::read_symlink("/proc/self/exe", error);
#endif
    }

    // --- dialogs and the desktop ----------------------------------------------------------------------------

    bool canPickFolder()
    {
#ifdef _WIN32
        return true;
#else
        return findProgram("zenity") || findProgram("kdialog");
#endif
    }

    std::optional<fs::path> pickFolder(const fs::path &start)
    {
#ifdef _WIN32
        Com com;
        IFileOpenDialog *dialog = nullptr;
        if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_IFileOpenDialog,
                                    reinterpret_cast<void **>(&dialog))))
            return std::nullopt;
        DWORD options = 0;
        dialog->GetOptions(&options);
        dialog->SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM);
        dialog->SetTitle(L"Install folder");
        std::error_code error;
        fs::path existing = start;
        while (!existing.empty() && !fs::is_directory(existing, error) && existing.has_parent_path() && existing.parent_path() != existing)
            existing = existing.parent_path();
        IShellItem *folder = nullptr;
        if (!existing.empty() && SUCCEEDED(SHCreateItemFromParsingName(existing.c_str(), nullptr, IID_IShellItem, reinterpret_cast<void **>(&folder))))
        {
            dialog->SetFolder(folder);
            folder->Release();
        }
        std::optional<fs::path> result;
        if (SUCCEEDED(dialog->Show(GetActiveWindow())))
        {
            IShellItem *item = nullptr;
            if (SUCCEEDED(dialog->GetResult(&item)))
            {
                PWSTR path = nullptr;
                if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)))
                {
                    result = fs::path(path);
                    CoTaskMemFree(path);
                }
                item->Release();
            }
        }
        dialog->Release();
        return result;
#else
        std::error_code error;
        fs::path existing = start;
        while (!existing.empty() && !fs::is_directory(existing, error) && existing.has_parent_path() && existing.parent_path() != existing)
            existing = existing.parent_path();
        std::vector<std::string> command;
        if (auto zenity = findProgram("zenity"))
            command = {zenity->string(), "--file-selection", "--directory", "--title=Install folder",
                       "--filename=" + existing.string() + "/"};
        else if (auto kdialog = findProgram("kdialog"))
            command = {kdialog->string(), "--getexistingdirectory", existing.string()};
        else
            return std::nullopt;
        std::string output;
        if (run(command, &output) != 0) return std::nullopt;
        // Only the last line: GTK warnings may come first on stderr.
        while (!output.empty() && (output.back() == '\n' || output.back() == '\r'))
            output.pop_back();
        const size_t line = output.rfind('\n');
        if (line != std::string::npos) output = output.substr(line + 1);
        if (output.empty() || output[0] != '/') return std::nullopt;
        return fs::path(output);
#endif
    }

    void openPath(const std::string &pathOrUrl)
    {
#ifdef _WIN32
        ShellExecuteW(nullptr, L"open", wide(pathOrUrl).c_str(), nullptr, nullptr, SW_SHOWNORMAL);
#else
        if (auto opener = findProgram("xdg-open")) spawnDetached({opener->string(), pathOrUrl}, {});
#endif
    }

    void deleteAfterExit(const fs::path &dir, const fs::path &self)
    {
#ifdef _WIN32
        // A copy of this program in %TEMP% waits for this process to end, then deletes the installer and the
        // folder (only when empty). No command shell: paths go through as arguments, never parsed.
        wchar_t temp[MAX_PATH + 1];
        const DWORD n = GetTempPathW(MAX_PATH + 1, temp);
        if (n == 0 || n > MAX_PATH) return;
        const fs::path helper = fs::path(temp) / ("ga2-uninstall-" + std::to_string(GetCurrentProcessId()) + ".exe");
        std::error_code error;
        fs::copy_file(self, helper, fs::copy_options::overwrite_existing, error);
        if (error) return;
        try
        {
            spawnDetached({pathUtf8(helper), "--finish-uninstall", std::to_string(GetCurrentProcessId()), pathUtf8(self), pathUtf8(dir)}, fs::path(temp));
        }
        catch (...)
        {
        }
#else
        std::error_code ignored;
        fs::remove(self, ignored);
        fs::remove(dir, ignored); // only when empty
#endif
    }

    void finishUninstall(unsigned long pid, const fs::path &file, const fs::path &dir)
    {
#ifdef _WIN32
        if (HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, DWORD(pid)))
        {
            WaitForSingleObject(process, INFINITE);
            CloseHandle(process);
        }
        for (int attempt = 0; attempt < 40; ++attempt)
        {
            if (DeleteFileW(file.c_str()) || GetLastError() == ERROR_FILE_NOT_FOUND) break;
            Sleep(250);
        }
        RemoveDirectoryW((dir / ".ga2-installer").c_str());
        RemoveDirectoryW(dir.c_str()); // only when empty
        // This copy in %TEMP% goes with the next temp clean-up.
#else
        (void)pid, (void)file, (void)dir;
#endif
    }
}
