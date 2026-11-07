// Message skipping: hold Ctrl (docs/PLAYING.md, "Skipping messages").
//
// The engines have a hold-to-skip of their own (R1, pad action 0x10), but it
// stalls at some Mugen 1 transitions (research/run58/keys), while ordinary
// Circle presses never do. So Ctrl presses Circle, the confirm button, every
// other pad frame: it completes the typing and turns the page, like a player
// tapping as fast as the game takes presses. The pad side (ps2_pad.cpp,
// ga2_framepace.h) presses it when Ctrl is down and this file says it is safe:
//
//   - the story scene (the ADV scene's update, scene id 2 in the scene
//     manager's record) is running: "a story script is on";
//   - and no choice or menu is up (the `menu` / `choice` script builtin, one
//     handler, is called every frame while a selection is shown) and the ship
//     map (which is also scene 2) is not updating.
//
// So it can never pick a choice, move around the ship map or touch the system
// menu, the save and load screens, the title or a battle (other scenes).
// Only state seen before the original runs is used: a guest function that
// blocks (a synchronous RPC) does not return into the hook.
//
// The pad side adds two more stops (ps2_pad.cpp): any other button pressed
// while Ctrl is held (the system menu, the backlog, Auto, ...) ends the
// skipping until Ctrl is released, and the picture itself is checked for the
// system menu and the backlog (strict colour signatures).
//
// Addresses: Zettai's from docs/modding/scripts.md and the ADV/framerate
// notes, the other discs by signature matches of Zettai's functions
// (research/rendering/tools/sigmatch.py), see kDiscs.
//
// Esc skips movies (the deployment before a battle, story movies): see actionQuery below.
//
// GA2_NO_SKIP=1 turns the hooks off. GA2_SKIP_TRACE=1 logs message starts
// (with a millisecond stamp), calls per second and the presses the pad side
// makes.
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "runtime/ga2_framepace.h"

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <utility>

namespace
{
    struct DiscSkip
    {
        const char *disc;
        uint32_t msg;  // script builtin 0x00 `msg`: shows a message and waits for the button (trace only)
        uint32_t menu; // script builtins 0x01 / 0x02 `menu` and `choice`
        uint32_t adv;  // the ADV scene's update (dt, adv): runs every frame while a story script is on
        uint32_t map;  // the ship map scene's update (ga2_framerate.cpp mapUpdate)
        uint32_t sys;  // the sys singleton pointer (+4 app, app +0 scene manager, manager +8 scene id; pad objects at +0x24)
        uint32_t action; // the pad action query (input, action): true when a button mapped to the action is pressed
                         // (callers with action 0xA, the skip: CScene_Movie's update 0x154128, the story's movie command 0x1ec548)
    };
    // Zettai: docs/modding/scripts.md (builtin handlers 00: 0x1e9ef0, 01/02: 0x1ea330); the ADV scene update is
    // FUN_001e6d90, the ship map update ga2_framerate.cpp's mapUpdate. The other discs: the callers of the pad-action
    // query (zettai 0x1cc610) are the script builtin handlers that wait for the button, which finds msg and
    // menu/choice (the first two by position and shape); the ADV update by sigmatch.py 0x1e6d90.
    constexpr DiscSkip kDiscs[] = {
        {"zettai", 0x1e9ef0u, 0x1ea330u, 0x1e6d90u, 0x1f6760u, 0x2d3be4u, 0x114828u},
        {"mugen-1", 0x2026d0u, 0x202b70u, 0x1ff470u, 0x20fc50u, 0x2f01e4u, 0x1148f0u},
        {"mugen-2", 0x1eb998u, 0x1ebdd8u, 0x1e8838u, 0x1f8218u, 0x2d56e4u, 0x1148a8u},
        {"eigou-1", 0x20dc50u, 0x20e310u, 0x209d88u, 0x21c278u, 0x2fe2e4u, 0x1148f0u},
        {"eigou-2", 0x203338u, 0x2037d8u, 0x200070u, 0x2109b0u, 0x2f12a4u, 0x1148f0u},
    };
    enum Hook { Msg, Menu, Adv, Map, Action };
    template <size_t I, int H>
    PS2Runtime::RecompiledFunction g_original = nullptr;

    constexpr uint32_t kAdventureScene = 2;
    bool g_trace = false;
    uint32_t g_scene = 0xffffffffu; // trace: the scene id seen last

    uint32_t word(const uint8_t *rdram, uint32_t address)
    {
        uint32_t v = 0;
        std::memcpy(&v, rdram + (address & 0x1fffffcu), sizeof(v));
        return v;
    }

    long long stamp()
    {
        return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count() % 1000000;
    }

    // The scene manager's scene id (1 boot, 9 title/load/save, 2 adventure and ship map, 3 battle, 12 disc change).
    template <size_t I>
    uint32_t sceneId(const uint8_t *rdram)
    {
        const uint32_t sys = word(rdram, kDiscs[I].sys);
        if (sys < 0x100000u || sys >= 0x2000000u)
            return 0xffffffffu;
        const uint32_t app = word(rdram, sys + 4);
        const uint32_t manager = app >= 0x100000u && app < 0x2000000u ? word(rdram, app) : 0u;
        g_scene = manager >= 0x100000u && manager < 0x2000000u ? word(rdram, manager + 8) : 0xffffffffu;
        return g_scene;
    }

    // Trace: calls per second of each hook.
    uint64_t g_counts[8] = {};
    long long g_countAt = 0;
    void count(int which)
    {
        if (!g_trace)
            return;
        ++g_counts[which];
        const long long now = stamp();
        if (now - g_countAt >= 1000)
        {
            std::fprintf(stderr, "[skip] %lld ms calls/s: msg %llu (first %llu) menu %llu adv-update %llu scene %u\n", now, (unsigned long long)g_counts[0],
                         (unsigned long long)g_counts[1], (unsigned long long)g_counts[2], (unsigned long long)g_counts[3], g_scene);
            for (auto &c : g_counts)
                c = 0;
            g_countAt = now;
        }
    }

    template <size_t I>
    void msg(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        count(GPR_U32(ctx, 6) == 0 ? 1 : 0);
        if (g_trace && GPR_U32(ctx, 6) == 0) // a2 = 0: the first call of a message
            std::fprintf(stderr, "[skip] %lld ms message start, scene %u\n", stamp(), sceneId<I>(rdram));
        g_original<I, Msg>(rdram, ctx, runtime);
    }

    // The ADV scene's per-frame update: a story script is on. The ship map's update means the map is up.
    template <size_t I>
    void advUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        count(3);
        if (sceneId<I>(rdram) == kAdventureScene)
            ga2_framepace::skipSeenMessage();
        g_original<I, Adv>(rdram, ctx, runtime);
    }

    template <size_t I>
    void mapUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ga2_framepace::skipSeenBlock();
        g_original<I, Map>(rdram, ctx, runtime);
    }

    template <size_t I>
    void menu(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ga2_framepace::skipSeenBlock();
        count(2);
        g_original<I, Menu>(rdram, ctx, runtime);
    }

    // Esc skips a movie. The games' movie players (the movie scene and the story's movie command) ask the pad for
    // action 0xA every frame while a movie plays and end it when it is pressed, the same way as when it is over; the
    // action is the Start button (on a gamepad, Start already skips). Right after Esc that question is answered with
    // yes, once. The only other places that ask for action 0xA (screens that go on with Circle or Start) already
    // take Esc as Circle.
    template <size_t I>
    void actionQuery(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t action = GPR_U32(ctx, 5);
        g_original<I, Action>(rdram, ctx, runtime);
        if (action == 0xAu && GPR_U32(ctx, 2) == 0u && ga2_framepace::takeCutsceneSkip())
        {
            SET_GPR_U32(ctx, 2, 1u);
            if (g_trace)
                std::fprintf(stderr, "[skip] %lld ms Esc: skip (pad action 0xA) answered yes\n", stamp());
        }
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        if (std::getenv("GA2_NO_SKIP"))
            return;
        g_trace = std::getenv("GA2_SKIP_TRACE") != nullptr;
        const DiscSkip &d = kDiscs[I];
        for (uint32_t address : {d.menu, d.adv, d.map})
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 skip] " << d.disc << ": 0x" << std::hex << address << std::dec << " not in the generated table; Ctrl skipping off\n";
                return;
            }
        auto hook = [&](uint32_t address, PS2Runtime::RecompiledFunction &original, PS2Runtime::RecompiledFunction replacement) {
            original = runtime.lookupFunction(address);
            runtime.replaceFunction(address, replacement);
        };
        if (g_trace && runtime.hasFunction(d.msg))
            hook(d.msg, g_original<I, Msg>, msg<I>);
        hook(d.menu, g_original<I, Menu>, menu<I>);
        hook(d.adv, g_original<I, Adv>, advUpdate<I>);
        hook(d.map, g_original<I, Map>, mapUpdate<I>);
        std::cerr << "[ga2 skip] " << d.disc << ": Ctrl message skipping hooks bound\n";
        if (runtime.hasFunction(d.action))
            hook(d.action, g_original<I, Action>, actionQuery<I>);
        else
            std::cerr << "[ga2 skip] " << d.disc << ": pad action query not in the generated table; Esc does not skip movies\n";
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 skip zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 skip mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 skip mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 skip eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 skip eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
