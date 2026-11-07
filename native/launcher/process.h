#pragma once

#include <filesystem>
#include <string>
#include <vector>

namespace ga2
{
    // Runs one engine process to completion; stdout and stderr go to `log`
    // (appended when `append`). The log starts with the quoted command line
    // and ends with the exit status. Returns the exit status (128 + signal
    // for a signalled exit), or -1 if the process did not start.
    int spawnAndWait(const std::vector<std::string> &command, const std::filesystem::path &log, bool append);

    // The text of `command` as one line, arguments quoted for reading back.
    std::string commandLineText(const std::vector<std::string> &command);

    // Paths cross process boundaries (arguments, environment, files) as UTF-8
    // on every platform.
    std::string pathUtf8(const std::filesystem::path &path);
    std::filesystem::path pathFromUtf8(const std::string &text);

    // main()'s arguments as UTF-8 (Windows decodes the wide command line; its
    // argv is in the ANSI code page and loses non-ASCII characters).
    std::vector<std::string> commandLineArguments(int argc, char **argv);

    // Environment variables as UTF-8 (the narrow Windows environment is in the
    // ANSI code page). getEnvironment returns "" when the variable is unset.
    std::string getEnvironment(const char *name);
    void setEnvironment(const char *name, const std::string &value);
}
