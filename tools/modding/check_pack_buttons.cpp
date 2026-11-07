// Offline check of the button prompts over texture-pack images, with the engine's own code
// (ps2xRuntime/src/lib/gs/gs_buttonpack.h): for every texture of buttons/map.json that has both a
// pack image and its original (a GA2_TEX_DUMP folder; files <hash>_<w>x<h>.png), compose the style's
// icons over the pack image and write the result and the matches.
//
//   check_pack_buttons <runtime-tree>/ps2xRuntime/src/lib/gs <buttons dir> <style> <scale> <dump dir>
//                      <pack dir> <out dir>
//
// Build (raylib from the engine build tree), for example:
//   clang++ -std=c++20 -O1 -I<runtime>/ps2xRuntime/src/lib/gs -I<build>/_deps/raylib-build/raylib/include \
//       -I<build>/_deps/nlohmann_json-src/include tools/modding/check_pack_buttons.cpp \
//       <build>/_deps/raylib-build/raylib/libraylib.a -lm -lpthread -ldl -lGL -lX11 -o check_pack_buttons
// Output: <out>/<hash>.png (the composed image), <out>/<hash>.txt (one line per icon found:
// button, rectangle in pack pixels, score).
#include "gs_buttonpack.h"

#include <nlohmann/json.hpp>

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <map>

namespace fs = std::filesystem;

static std::map<std::string, ::Image> g_icons;

int main(int argc, char **argv)
{
    if (argc < 8)
    {
        std::fprintf(stderr, "usage: %s <gs src dir (unused)> <buttons dir> <style> <scale> <dump dir> <pack dir> <out dir>\n", argv[0]);
        return 2;
    }
    const fs::path buttons = argv[2], dump = argv[5], packs = argv[6], out = argv[7];
    const std::string style = argv[3];
    const int scale = std::atoi(argv[4]);
    SetTraceLogLevel(LOG_WARNING);
    nlohmann::json map;
    {
        std::ifstream in(buttons / "map.json");
        map = nlohmann::json::parse(in);
    }
    std::map<std::string, fs::path> origs, packImages;
    for (const auto &e : fs::directory_iterator(dump))
        if (e.path().extension() == ".png")
            origs[e.path().stem().string().substr(0, 16)] = e.path();
    for (const auto &e : fs::recursive_directory_iterator(packs))
        if (e.path().extension() == ".png")
            packImages[e.path().stem().string().substr(0, 16)] = e.path();
    fs::create_directories(out);
    const ga2_buttonmatch::IconLookup lookup = [&](const std::string &button, bool wide) -> const ::Image * {
        for (const bool tryWide : {wide, false})
        {
            if (!tryWide && wide && false)
                continue;
            const std::string name = button + (tryWide ? "_wide" : "");
            const std::string key = style + "/" + name;
            auto it = g_icons.find(key);
            if (it == g_icons.end())
            {
                ::Image image = LoadImage((buttons / style / (name + ".png")).string().c_str());
                if (image.data)
                    ImageFormat(&image, PIXELFORMAT_UNCOMPRESSED_R8G8B8A8);
                it = g_icons.emplace(key, image).first;
            }
            if (it->second.data)
                return &it->second;
            if (!wide)
                break;
        }
        return nullptr;
    };
    int composed = 0, kept = 0;
    for (const auto &[hash, spotsJson] : map.at("textures").items())
    {
        if (!origs.count(hash) || !packImages.count(hash))
            continue;
        std::vector<ga2_buttonmatch::IconSpot> spots;
        for (const auto &s : spotsJson)
            spots.push_back({s.at("button").get<std::string>(), s.at("x").get<int>(), s.at("y").get<int>(),
                             s.at("w").get<int>(), s.at("h").get<int>()});
        ::Image orig = LoadImage(origs[hash].string().c_str());
        ImageFormat(&orig, PIXELFORMAT_UNCOMPRESSED_R8G8B8A8);
        // The decoded GS words: alpha 0x80 = opaque.
        std::vector<uint32_t> words(static_cast<uint32_t *>(orig.data), static_cast<uint32_t *>(orig.data) + size_t(orig.width) * size_t(orig.height));
        for (uint32_t &c : words)
            c = (c & 0x00FFFFFFu) | ((((c >> 24) + 1u) >> 1) << 24);
        ::Image pack = LoadImage(packImages[hash].string().c_str());
        ImageFormat(&pack, PIXELFORMAT_UNCOMPRESSED_R8G8B8A8);
        ::Image result{};
        std::vector<ga2_buttonmatch::Match> matches;
        const bool done = ga2_buttonmatch::composePackButtons(pack, words, orig.width, orig.height, spots, scale, lookup, result, &matches);
        std::ofstream txt(out / (hash + ".txt"));
        txt << "orig " << orig.width << "x" << orig.height << " pack " << pack.width << "x" << pack.height << "\n";
        for (const auto &m : matches)
            txt << spots[size_t(m.spot)].button << " " << m.r.x << "," << m.r.y << " " << m.r.w << "x" << m.r.h << " score " << m.score << "\n";
        if (done)
        {
            ExportImage(result, (out / (hash + ".png")).string().c_str());
            UnloadImage(result);
            ++composed;
        }
        else
            ++kept;
        std::printf("%s %s: %zu icon(s)%s\n", hash.c_str(), packImages[hash].parent_path().filename().string().c_str(), matches.size(),
                    done ? "" : " (image kept)");
        UnloadImage(orig);
        UnloadImage(pack);
    }
    std::printf("composed %d, kept as is %d\n", composed, kept);
    return 0;
}
