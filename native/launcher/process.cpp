// Engine processes for the launcher (kept apart from raylib, whose names
// clash with <windows.h>).
#include "process.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#else
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
extern char **environ;
#endif

namespace ga2
{
    namespace
    {
        // One argument quoted the way CommandLineToArgvW / the MSVCRT split it
        // (also used to print commands readably on every platform). Only
        // ASCII characters matter here, so UTF-8 bytes pass through.
        std::string quoteArgument(const std::string &arg)
        {
            if (!arg.empty() && arg.find_first_of(" \t\n\v\"") == std::string::npos)
                return arg;
            std::string out = "\"";
            size_t slashes = 0;
            for (char c : arg)
            {
                if (c == '\\') { ++slashes; continue; }
                // Backslashes before a quote (or the closing quote) are doubled; the quote is escaped.
                out.append(c == '"' ? slashes * 2 + 1 : slashes, '\\');
                slashes = 0;
                out.push_back(c);
            }
            out.append(slashes * 2, '\\');
            out.push_back('"');
            return out;
        }

        void logLine(const std::filesystem::path &log, const std::string &text, bool truncate)
        {
            std::ofstream out(log, truncate ? std::ios::trunc : std::ios::app);
            out << text << '\n';
        }

#ifdef _WIN32
        std::wstring toWide(const std::string &text)
        {
            if (text.empty()) return {};
            for (const DWORD flags : {DWORD(MB_ERR_INVALID_CHARS), DWORD(0)})
            {
                const UINT page = flags ? CP_UTF8 : CP_ACP; // invalid UTF-8: treat as ANSI
                const int size = MultiByteToWideChar(page, flags, text.data(), int(text.size()), nullptr, 0);
                if (size <= 0) continue;
                std::wstring out(size_t(size), L'\0');
                MultiByteToWideChar(page, flags, text.data(), int(text.size()), out.data(), size);
                return out;
            }
            return {};
        }

        std::string toUtf8(const wchar_t *text, size_t length)
        {
            if (length == 0) return {};
            const int size = WideCharToMultiByte(CP_UTF8, 0, text, int(length), nullptr, 0, nullptr, nullptr);
            if (size <= 0) return {};
            std::string out(size_t(size), '\0');
            WideCharToMultiByte(CP_UTF8, 0, text, int(length), out.data(), size, nullptr, nullptr);
            return out;
        }
#endif
    }

    std::string commandLineText(const std::vector<std::string> &command)
    {
        std::string line;
        for (const auto &arg : command)
        {
            if (!line.empty()) line.push_back(' ');
            line += quoteArgument(arg);
        }
        return line;
    }

    std::string pathUtf8(const std::filesystem::path &path)
    {
        const auto text = path.u8string();
        return std::string(text.begin(), text.end());
    }

    std::filesystem::path pathFromUtf8(const std::string &text)
    {
        return std::filesystem::path(std::u8string(text.begin(), text.end()));
    }

    std::vector<std::string> commandLineArguments(int argc, char **argv)
    {
#ifdef _WIN32
        int count = 0;
        if (wchar_t **wide = CommandLineToArgvW(GetCommandLineW(), &count))
        {
            std::vector<std::string> out;
            for (int i = 0; i < count; ++i) out.push_back(toUtf8(wide[i], std::wcslen(wide[i])));
            LocalFree(wide);
            return out;
        }
#endif
        return std::vector<std::string>(argv, argv + argc);
    }

    std::string getEnvironment(const char *name)
    {
#ifdef _WIN32
        const std::wstring wideName = toWide(name);
        const DWORD size = GetEnvironmentVariableW(wideName.c_str(), nullptr, 0);
        if (size == 0) return {};
        std::wstring value(size, L'\0');
        const DWORD length = GetEnvironmentVariableW(wideName.c_str(), value.data(), size);
        return toUtf8(value.data(), length);
#else
        const char *value = std::getenv(name);
        return value ? value : "";
#endif
    }

    void setEnvironment(const char *name, const std::string &value)
    {
#ifdef _WIN32
        // An empty value removes the variable on Windows, like an unset one.
        SetEnvironmentVariableW(toWide(name).c_str(), value.empty() ? nullptr : toWide(value).c_str());
#else
        setenv(name, value.c_str(), 1);
#endif
    }

    // Runs one engine process to completion, output appended to `log`.
    int spawnAndWait(const std::vector<std::string> &command, const std::filesystem::path &log, bool append)
    {
        if (command.empty()) return -1;
        std::error_code ignored;
        std::filesystem::create_directories(log.parent_path(), ignored);
        const std::string text = commandLineText(command);
        // The log opens with the command that produced it and ends with the status.
        logLine(log, "[ga2] command: " + text, !append);
        std::cerr << "[ga2] command: " << text << std::endl;
        int result = -1;
#ifdef _WIN32
        std::wstring line = toWide(text);
        SECURITY_ATTRIBUTES inherit{sizeof(inherit), nullptr, TRUE};
        HANDLE out = CreateFileW(log.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, &inherit,
                                 OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        if (out != INVALID_HANDLE_VALUE)
        {
            startup.dwFlags = STARTF_USESTDHANDLES;
            startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
            startup.hStdOutput = out;
            startup.hStdError = out;
        }
        PROCESS_INFORMATION process{};
        const std::wstring program = toWide(command.front());
        const BOOL started = CreateProcessW(program.c_str(), line.data(), nullptr, nullptr, TRUE,
                                            CREATE_UNICODE_ENVIRONMENT, nullptr, nullptr, &startup, &process);
        const DWORD startError = started ? 0 : GetLastError();
        if (out != INVALID_HANDLE_VALUE) CloseHandle(out);
        if (!started)
        {
            const std::string message = "[ga2] cannot start " + command.front() + " (Windows error " +
                                        std::to_string(startError) + ")";
            std::cerr << message << '\n';
            logLine(log, message, false);
            return -1;
        }
        WaitForSingleObject(process.hProcess, INFINITE);
        DWORD code = 0;
        GetExitCodeProcess(process.hProcess, &code);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        result = int(code);
        logLine(log, "[ga2] exit code " + std::to_string(result), false);
#else
        std::vector<char *> argv;
        for (const auto &arg : command) argv.push_back(const_cast<char *>(arg.c_str()));
        argv.push_back(nullptr);
        posix_spawn_file_actions_t actions;
        int error = posix_spawn_file_actions_init(&actions);
        if (error == 0)
        {
            // The header above already created the file, so the child only appends.
            error = posix_spawn_file_actions_addopen(&actions, 1, log.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
            if (error == 0) error = posix_spawn_file_actions_adddup2(&actions, 1, 2);
        }
        pid_t pid = 0;
        if (error == 0) error = posix_spawn(&pid, argv[0], &actions, nullptr, argv.data(), environ);
        posix_spawn_file_actions_destroy(&actions);
        if (error != 0)
        {
            const std::string message = std::string("[ga2] cannot start ") + argv[0] + ": " + std::strerror(error);
            std::cerr << message << '\n';
            logLine(log, message, false);
            return -1;
        }
        int status = 0;
        pid_t waited;
        while ((waited = waitpid(pid, &status, 0)) < 0 && errno == EINTR) {}
        std::string outcome;
        if (waited < 0)
        {
            outcome = std::string("[ga2] waiting for the engine failed: ") + std::strerror(errno);
            result = -1;
        }
        else if (WIFEXITED(status))
        {
            result = WEXITSTATUS(status);
            outcome = "[ga2] exit code " + std::to_string(result);
        }
        else
        {
            const int signal = WIFSIGNALED(status) ? WTERMSIG(status) : 0;
            result = 128 + signal;
            outcome = "[ga2] terminated by signal " + std::to_string(signal) + " (reported as exit code " +
                      std::to_string(result) + ")";
        }
        logLine(log, outcome, false);
#endif
        std::cerr << "[ga2] " << command.front() << " finished with code " << result << std::endl;
        return result;
    }
}
