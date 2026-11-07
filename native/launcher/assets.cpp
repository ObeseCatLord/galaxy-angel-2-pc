#include "assets.h"
#include "process.h"

#include <cstdlib>
#include <string_view>
#include <vector>

namespace ga2
{
    namespace
    {
        // The sky each game shows behind its own opening (docs/assets/skybox.md).
        const char *defaultSky(std::string_view id)
        {
            if (id == "mugen-1") return "adv_op_sbg_0002+0003";
            if (id == "eigou-2") return "adv_op_sbg_9998+9999";
            return "adv_op_sbg_0000+0001";
        }

        std::filesystem::path findInPath(std::initializer_list<const char *> names)
        {
            const std::string path = getEnvironment("PATH");
            if (path.empty())
                return {};
#ifdef _WIN32
            const char separator = ';';
            const std::vector<std::string> suffixes = {".exe", ""};
#else
            const char separator = ':';
            const std::vector<std::string> suffixes = {""};
#endif
            std::string_view rest = path;
            while (!rest.empty())
            {
                const size_t end = rest.find(separator);
                const std::filesystem::path dir = pathFromUtf8(std::string(rest.substr(0, end)));
                rest = end == std::string_view::npos ? std::string_view() : rest.substr(end + 1);
                for (const char *name : names)
                    for (const std::string &suffix : suffixes)
                    {
                        std::error_code ec;
                        const auto candidate = dir / (std::string(name) + suffix);
                        if (std::filesystem::is_regular_file(candidate, ec))
                            return candidate;
                    }
            }
            return {};
        }
    }

    std::filesystem::path skyboxFile(const Library &library, const KnownDisc &disc)
    {
        return library.dataDir() / "assets" / "skybox" / (std::string(disc.bootPath + 1) + ".png");
    }

    bool ensureSkybox(const Library &library, const KnownDisc &disc, const std::filesystem::path &engineDir,
                      std::string &message)
    {
        const auto target = skyboxFile(library, disc);
        if (std::filesystem::exists(target))
            return true;
        const auto imported = library.disc(disc.id);
        if (!imported || !imported->available)
        {
            message = "disc not available";
            return false;
        }
        const auto tool = engineDir / "tools" / "extract_skybox.py";
        if (!std::filesystem::exists(tool))
        {
            message = "skybox tool missing (" + tool.string() + ")";
            return false;
        }
        const auto python = findInPath({"python3", "python", "py"});
        if (python.empty())
        {
            message = "Python 3 (with Pillow) is needed to render the space skybox";
            return false;
        }
        const auto work = library.dataDir() / "assets" / "skybox" / "work";
        std::error_code ec;
        std::filesystem::create_directories(work, ec);
        const std::string sky = defaultSky(disc.id);
        const int code = spawnAndWait({pathUtf8(python), pathUtf8(tool), pathUtf8(imported->image), "-o", pathUtf8(work), "--sky",
                                       sky, "--cube", "0", "--no-backdrops"},
                                      library.dataDir() / "logs" / "skybox.log", true);
        const auto rendered = work / (disc.bootPath + 1) / sky / (sky + "_equirect.png");
        if (code != 0 || !std::filesystem::exists(rendered))
        {
            message = "the skybox tool failed (see logs/skybox.log)";
            return false;
        }
        std::filesystem::rename(rendered, target, ec);
        if (ec)
            std::filesystem::copy_file(rendered, target, std::filesystem::copy_options::overwrite_existing, ec);
        std::filesystem::remove_all(work / (disc.bootPath + 1), ec);
        return std::filesystem::exists(target);
    }
}
