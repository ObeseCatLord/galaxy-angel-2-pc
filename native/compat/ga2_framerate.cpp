// High frame rate without speeding the game up (GA2_FPS=<61..240>), and the
// frame-timing oracle trace (GA2_FRAMETRACE=<file>, tools/framerate_oracle.py).
// docs/rendering/horplus-and-framerate.md has the reverse-engineering notes
// and the per-disc address table.
//
// Main loop (all five discs, same code): { Update(sys); RenderFlip(sys); }
//   Update       app->Update(dt), dt = min(sys+0x18, sys+0x1c) * sys+0x08
//                (frames elapsed, maxFrames, seconds per frame = 1/refresh)
//   RenderFlip   draws, then frames = max(int(elapsed ticks as vsyncs) + 1,
//                sys+0x20 minFrames), WaitVSync, flip (display registers).
// At GA2_FPS=N the hooks below make the game's frame N*1000/1001 Hz:
//   - sys+0x08 holds 1/(N*1000/1001) (the global refresh float is left alone:
//     it also converts vsync counts to seconds and sizes some per-frame steps);
//   - RenderFlip's elapsed-time conversion (the function turning EE timer
//     ticks into vsyncs*100) is scaled so `frames` counts N-Hz frames;
//   - setMaxFrames / setMinFrames store the values in N-Hz frames (a native
//     maxFrames m means m/59.94 s of catch-up: ceil(m*N/60) frames; a native
//     minFrames of 1 means no limit and stays 1; larger values (the battle's 30 fps
//     views) keep the native 30 fps (round(m*N/60) frames; GA2_FPS_BATTLE=full
//     renders them at N, but their dialogue then reacts sooner than natively);
//   - RenderFlip's WaitVSync (recognised by its return address; the other
//     callers keep the real VBLANK wait) sleeps on the runtime's frame clock
//     (runtime/ga2_framepace.h) instead, so the loop runs at N Hz while the
//     emulated VBLANK, IOP, sound and movies stay at 59.94 Hz;
//   - the flip function tells the runtime a new frame was set up; the GS then
//     scans out at the frame clock's ticks and the window shows every frame.
// Code that counts iterations instead of using dt (card polling, auto-repeat of the
// pad, portrait pop-ins, particle effects, random rolls, ...) is gated or held to the
// native cadence: see gate<>, effectHold<>, rng<> and DiscRate::gates. Every scene
// runs at N (nativeScenes() lists the exceptions: scene 12, the disc-change notice).
// Unset or 60: none of these is installed (the trace hooks only read).
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "runtime/ga2_framepace.h"
#include "runtime/ga2_activity.h"

#include <algorithm>
#include <chrono>
#include <ctime>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <unordered_map>
#include <utility>
#include <string>
#include <vector>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#endif

namespace
{
    struct DiscRate
    {
        const char *disc;
        uint32_t update, waitVSync, waitRa;   // main-loop Update, WaitVSync, its call from RenderFlip (return address)
        uint32_t ticks, ticksRa;              // EE timer ticks -> vsyncs*100, its call from RenderFlip
        uint32_t setMax, setMin, intervalGlobal; // maxFrames / minFrames setters; the VBLANK handler's interval global
        uint32_t flip, sysGlobal, matSet, glyph;  // display register flip, sys singleton pointer, MatSet(kind, matrix), message glyph drawer
        uint32_t itemDraw;                        // 2D layer item draw (item, list), see ga2_features.cpp
        uint32_t mapUpdate;                       // ship map scene update (zettai 0x1f6760), found on the others by signature
        uint32_t rng;                             // the game's random number generator (next value)
        uint32_t mapCamera;                       // ship map camera follow (zettai 0x1f0388), found on the others by signature
        uint32_t fill[8];                         // flat rect int / float, flat quad, gouraud quad (ga2_features.cpp), for the full-screen draw census
        uint32_t rngHold;                         // function (size 0x4a0) whose random rolls are held between native frames, 0 = none
        uint32_t gates[8];                        // per-iteration logic gated to native frames (see gate<>); bit 0 of the address marks `update(dt)` style, bit 1 a widget that must not be drawn unset (`fresh`), bit 2 the pad updater (`pad`)
        uint32_t padRaw, padOut;                  // the pad updater's raw state array (+0x10 previous, +0x18 current) and its repeat outputs
        uint32_t effect, effectState;             // a per-frame distortion effect (size 0x488): its angle word advances per call and it rolls the generator
    };

    constexpr DiscRate kDiscs[] = {
        {"zettai", 0x113210u, 0x237748u, 0x112ebcu, 0x223218u, 0x112e70u, 0x113388u, 0x1133a0u, 0x2cf774u, 0x2285e0u, 0x2d3be4u, 0x22fc88u, 0x111c70u, 0x11ce18u, 0x1f6760u, 0x107758u, 0x1f0388u, {0x2333e0u, 0x233448u, 0x2329c8u, 0x232c18u, 0x2332a0u, 0x233308u, 0x232e38u, 0x233280u}, 0x19f830u, {0x19fce3u, 0x134fa0u, 0x197209u, 0x182e09u, 0x122cdcu, 0x1229f8u, 0x12ed58u, 0}, 0x2b6850u, 0x7e9350u, 0x15aa40u, 0x2bcfc4u},
        {"mugen-1", 0x1132c0u, 0x2511a8u, 0x112f4cu, 0x23cc70u, 0x112f00u, 0x113438u, 0x113450u, 0x2ebd74u, 0x242038u, 0x2f01e4u, 0x2496e0u, 0x111cd8u, 0x11d030u, 0x20fc50u, 0x1077b8u, 0x2097b0u, {0x24ce40u, 0x24cea8u, 0x24c428u, 0x24c678u, 0x24cd00u, 0x24cd68u, 0x24c898u, 0x24cce0u}, 0x1ad580u, {0x1ada33u, 0x1359f8u, 0x1a5299u, 0, 0x122ef4u, 0x122c10u, 0x12f5d0u, 0}, 0x2d2d50u, 0x506e90u, 0x1613f8u, 0x2d9504u},
        {"mugen-2", 0x113290u, 0x2391f8u, 0x112f3cu, 0x224cc8u, 0x112ef0u, 0x113408u, 0x113420u, 0x2d1274u, 0x22a090u, 0x2d56e4u, 0x231738u, 0x111cf0u, 0x11ce98u, 0x1f8218u, 0x1077e0u, 0x1f1e40u, {0x234e90u, 0x234ef8u, 0x234478u, 0x2346c8u, 0x234d50u, 0x234db8u, 0x2348e8u, 0x234d30u}, 0x1a12d0u, {0x1a1783u, 0x135738u, 0x198ca9u, 0x1848a9u, 0x122d5cu, 0x122a78u, 0x12f418u, 0}, 0x2b8350u, 0x7eafd0u, 0x15c4e0u, 0x2beae4u},
        {"eigou-1", 0x1132c0u, 0x25dea8u, 0x112f4cu, 0x249970u, 0x112f00u, 0x113438u, 0x113450u, 0x2f9e74u, 0x24ed38u, 0x2fe2e4u, 0x2563e0u, 0x111cd8u, 0x11d030u, 0x21c278u, 0x1077b8u, 0x215dd8u, {0x259b40u, 0x259ba8u, 0x259128u, 0x259378u, 0x259a00u, 0x259a68u, 0x259598u, 0x2599e0u}, 0x1b3000u, {0x1b34b3u, 0x135b58u, 0x1aa829u, 0, 0x122ef4u, 0x122c10u, 0x12f730u, 0}, 0x2e0dd0u, 0x515710u, 0x163440u, 0x2e75a4u},
        {"eigou-2", 0x1132c0u, 0x252218u, 0x112f4cu, 0x23dce0u, 0x112f00u, 0x113438u, 0x113450u, 0x2ece34u, 0x2430a8u, 0x2f12a4u, 0x24a750u, 0x111cd8u, 0x11d030u, 0x2109b0u, 0x1077b8u, 0x20a510u, {0x24deb0u, 0x24df18u, 0x24d498u, 0x24d6e8u, 0x24dd70u, 0x24ddd8u, 0x24d908u, 0x24dd50u}, 0x1ae108u, {0x1ae5bbu, 0x135ad0u, 0x1a5e21u, 0, 0x122ef4u, 0x122c10u, 0x12f5d0u, 0}, 0x2d3dd0u, 0x508310u, 0x161e70u, 0x2da5b4u},
    };

    // The game's app->Update(dt, app) (the scene update and the common systems), reached from Update
    // through a vtable.
    constexpr uint32_t kAppUpdate[5] = {0x13a1c0u, 0x13ad60u, 0x13a9e8u, 0x13aec0u, 0x13ba78u};
    // The scene manager's update (a0 = app, a1 = manager; +4 pending request: 1 change, 2 push, 3 pop; +8 scene id):
    // it processes a request made in the previous iteration.
    constexpr uint32_t kSceneManagerUpdate[5] = {0x103550u, 0x1035b0u, 0x1035d8u, 0x1035b0u, 0x1035b0u};

    // sys object fields.
    constexpr uint32_t kSysApp = 0x04, kSysStep = 0x08, kSysFrames = 0x18, kSysMax = 0x1c, kSysMin = 0x20;

    template <size_t I, int H>
    PS2Runtime::RecompiledFunction g_original = nullptr;
    enum Hook
    {
        Update,
        WaitVSync,
        Ticks,
        Flip,
        MatSet,
        Glyph,
        ItemDraw,
        MapUpdate,
        Rng,
        MapCamera,
        AppUpdate,
        SceneManager,
        Effect,
        Fill0,
        Fill1,
        Fill2,
        Fill3,
        Fill4,
        Fill5,
        Fill6,
        Fill7,
        SetMax,
        SetMin,
        GateBase // + slot
    };
    constexpr int kGateSlots = 16;

    uint32_t word(const uint8_t *rdram, uint32_t address)
    {
        uint32_t v = 0;
        std::memcpy(&v, rdram + (address & 0x1fffffcu), sizeof(v));
        return v;
    }
    void putWord(uint8_t *rdram, uint32_t address, uint32_t v) { std::memcpy(rdram + (address & 0x1fffffcu), &v, sizeof(v)); }
    float getFloat(const uint8_t *rdram, uint32_t address)
    {
        float v = 0.0f;
        std::memcpy(&v, rdram + (address & 0x1fffffcu), sizeof(v));
        return v;
    }

    // N / 60: how many N-Hz frames fit in one native 1/60 s.
    double ratio() { return ga2_framepace::enabled() ? ga2_framepace::rate() / 60.0 : 1.0; }

    // Native values of the game's frame-count settings (the sys fields hold
    // them in N-Hz frames while GA2_FPS is on).
    uint32_t g_nativeMax = 0, g_nativeMin = 0;
    float g_nativeStep = float(1.0 / 59.94); // the game's own sys+0x08, read at the first Update
    bool g_stepKnown = false;
    bool g_fieldsKnown = false;
    uint32_t scaledMax(uint32_t native) { return std::max<uint32_t>(1u, uint32_t(std::ceil(native * ratio() - 1e-6))); }
    // The views the game limits to 30 fps (minFrames 2: the battle's camera views, some adventure scenes) render at the
    // full rate only with GA2_FPS_BATTLE=full: the whole Zettai battle through the oracle (2026-10-22, free-running
    // runs) showed the battle dialogue reacting up to 0.45 s sooner after a press at full rate (state machines that
    // step once per iteration: 30 steps a second natively, N at full rate); kept at the native 30 they match.
    bool fullRateBattle()
    {
        static const bool full = [] { const char *v = std::getenv("GA2_FPS_BATTLE"); return v && std::strcmp(v, "full") == 0; }();
        return full;
    }
    uint32_t scaledMin(uint32_t native)
    {
        if (native <= 1u || fullRateBattle())
            return 1u;
        return std::max<uint32_t>(1u, uint32_t(std::lround(native * ratio())));
    }

    // The game's RNG: how many values each iteration draws and who asks (the
    // draws of per-frame random effects grow with the frame rate).
    struct RngStat
    {
        uint32_t ra[3]; // the call chain: who called the generator, who called that, ...
        uint32_t count;
    };
    std::vector<RngStat> g_rngCallers;
    uint32_t g_rngDraws = 0;
    uint64_t g_rngTotal = 0;

    uint64_t g_iteration = 0;        // Update calls
    double g_dt = 0.0;               // dt of the current iteration
    // A native frame of game time has passed since the last iteration that was a native-frame boundary
    // (decided once per iteration in Update; used by the random-roll hold).
    double g_tickAcc = 0.0;
    bool g_nativeTick = true;
    std::vector<uint32_t> g_rngSeq;  // the held function's rolls of the last boundary iteration
    size_t g_rngCursor = 0;
    struct EffectCall
    {
        uint32_t before, after;
    };
    std::vector<EffectCall> g_effectCalls; // the effect's calls of the last boundary iteration
    size_t g_effectCursor = 0;
    uint32_t g_sceneId = 0xffffffffu; // the scene manager record's scene id (+8)
    bool g_native = false;           // this iteration runs at the native rate
    uint64_t g_mapIteration = ~0ull; // the last iteration the ship map's update ran in
    // GA2_FPS_NOFIX=1 (a diagnostic, and the oracle's negative control): none of the iteration-counting fixes (gates, effect and
    // random-roll hold, map camera, app step) is bound: what the game does by itself at the high rate.
    bool noFix()
    {
        static const bool v = std::getenv("GA2_FPS_NOFIX") != nullptr;
        return v;
    }

    // Scenes that still run at the native rate with GA2_FPS set (the safety net for what the oracle could not clear),
    // as a bit set: bit n = scene id n, kMapBit = the ship map. GA2_FPS_NATIVE=<ids and `map`, comma separated | none>
    // overrides the built-in default; GA2_FPS_SCENES=all means none (kept from before).
    constexpr uint32_t kMapBit = 1u << 31;
    // Scene 12, the disc-change notice, counts frames in its own way (the window closes 15 iterations after the button press): native.
    constexpr uint32_t kDefaultNative = 1u << 12;
    uint32_t nativeScenes()
    {
        static const uint32_t set = [] {
            if (const char *all = std::getenv("GA2_FPS_SCENES"); all && std::strcmp(all, "all") == 0)
                return 0u;
            const char *v = std::getenv("GA2_FPS_NATIVE");
            if (!v)
                return kDefaultNative;
            uint32_t bits = 0;
            for (const char *p = v; *p;)
            {
                if (std::strncmp(p, "map", 3) == 0)
                    bits |= kMapBit;
                else if (*p >= '0' && *p <= '9')
                    bits |= 1u << (std::strtoul(p, nullptr, 10) & 31u);
                const char *comma = std::strchr(p, ',');
                if (!comma)
                    break;
                p = comma + 1;
            }
            return bits;
        }();
        return set;
    }
    // Scene ids (the scene manager record's +8, same on all discs): 1 boot, 9 title/load/save, 2 adventure, 3 battle, 12 disc change.
    bool sceneAtRate(uint32_t sceneId)
    {
        const uint32_t native = nativeScenes();
        if (!native)
            return true;
        if ((native & kMapBit) && g_mapIteration != ~0ull && g_iteration - g_mapIteration <= 2)
            return false; // the ship map
        return sceneId >= 32 || !(native & (1u << sceneId));
    }
    // The sys fields as the current mode wants them.
    // The native grid of a view limited to 30 fps (minFrames 2): natively its iterations start every two native frames
    // from the iteration after the one that set the limit. At N the limit is set in an iteration that may start half a
    // frame early (the battle sets it two iterations after it starts: two ticks at 120, two frames natively), which put
    // the whole view one native frame off and every battle message started a frame early. g_minOrigin is the native
    // start of that grid: until then the iterations wait single ticks, and they end on its points.
    double g_gameTime = 0.0;    // sum of the dt given to the game
    double g_minOrigin = -1.0;
    double g_lag = 0.0;            // VBLANKs minus game time in native frames, at iterations starting at a VBLANK
    bool g_lagKnown = false;
    uint64_t g_prevIterVblank = 0;
    uint32_t g_prevWait = 0;        // the previous iteration's wait (ticks)
    // Iterations left at the native cadence after a scene switch (see sceneManager<>): a new scene counts its first
    // iterations (the battle sets up its objects and its 30 fps limit in its second iteration), so those run one
    // native frame each, as natively.
    uint32_t g_settle = 0;
    uint32_t sysMinNow(uint32_t nativeMin)
    {
        if (g_settle > 0u)
        {
            const int rate = ga2_framepace::rate();
            if (rate % 60 == 0)
                return std::max<uint32_t>(uint32_t(rate / 60), scaledMin(nativeMin));
        }
        if (nativeMin > 1u && !fullRateBattle() && g_minOrigin >= 0.0 && g_gameTime < g_minOrigin - 1e-4)
            return 1u;
        return scaledMin(nativeMin);
    }

    void applyMode(uint8_t *rdram, uint32_t sys)
    {
        // Native: the game's own step (1/59.94 in float, not 1001/60000: the native cadence stays bit-identical).
        // At N: the game's own step scaled by 60/N (exactly half at 120: 2 ticks add up to the native frame bit for bit;
        // the runtime's 1001/(1000 N) in float differed in the 7th digit, game time drifted about 25 us per second and
        // threshold timers tripped in other iterations than natively).
        const float step = g_native ? g_nativeStep : float(double(g_nativeStep) * 60.0 / double(ga2_framepace::rate()));
        std::memcpy(rdram + sys + kSysStep, &step, sizeof(step));
        putWord(rdram, sys + kSysMax, g_native ? g_nativeMax : scaledMax(g_nativeMax));
        putWord(rdram, sys + kSysMin, g_native ? g_nativeMin : sysMinNow(g_nativeMin));
    }

    // Last elapsed time RenderFlip measured, in native vsyncs (trace).
    float g_elapsed = 0.0f;
    // The message glyph drawer's call: a1 = glyph records, a2 = characters in
    // the message, a3 = characters revealed so far (the typing progress).
    uint32_t g_glyphs = 0;      // message drawer calls this iteration
    uint32_t g_revealed = 0, g_total = 0; // the largest revealed count seen and its message length
    double g_iterStartGt = 0.0, g_prevIterStartGt = 0.0; // game time at the start of this / the previous iteration

    struct MatRecord
    {
        uint32_t ra;
        float m[16];
    };
    constexpr int kMaxViews = 8, kMaxWorldsLimit = 400;
    // GA2_FRAMETRACE_WORLDS: how many world matrices per iteration the trace keeps (default 12).
    const int kMaxWorlds = [] {
        const char *v = std::getenv("GA2_FRAMETRACE_WORLDS");
        return std::clamp(v && *v ? std::atoi(v) : 12, 0, kMaxWorldsLimit);
    }();
    MatRecord g_views[kMaxViews], g_worlds[kMaxWorldsLimit];
    int g_viewCount = 0, g_worldCount = 0, g_viewsSeen = 0, g_worldsSeen = 0;

    void printMatrix(char *out, size_t size, const float *m)
    {
        int n = 0;
        for (int i = 0; i < 16; ++i)
            n += std::snprintf(out + n, size - n, i ? ",%.9g" : "%.9g", m[i]);
    }

    // GA2_FRAMETRACE_PROBE=<spec>[;<spec>...]: extra words of guest memory
    // logged each iteration as P lines (audit aid). spec = <hex address>
    // [><hex offset>]...[:<words>]: starting at the address, each `>off` loads
    // the pointer stored there and moves to pointer + off; then <words> words
    // are logged from there (so `2b6640>0>0:4` is the first four words of the
    // object the object at framework+0 points to). `*A+O` is the older spelling of `A>O`.
    struct Probe
    {
        uint32_t address;
        std::vector<uint32_t> hops;
        uint32_t words;
    };
    const std::vector<Probe> &probes()
    {
        static const std::vector<Probe> list = [] {
            std::vector<Probe> out;
            const char *v = std::getenv("GA2_FRAMETRACE_PROBE");
            while (v && *v)
            {
                Probe p{0, {}, 1};
                bool legacy = false;
                if (*v == '*')
                {
                    legacy = true;
                    ++v;
                }
                char *end = nullptr;
                p.address = uint32_t(std::strtoul(v, &end, 16));
                if (end == v)
                    break;
                if (legacy)
                    p.hops.push_back(0);
                while (*end == '>' || (legacy && *end == '+'))
                {
                    const uint32_t off = uint32_t(std::strtoul(end + 1, &end, 16));
                    if (legacy)
                        p.hops.back() = off;
                    else
                        p.hops.push_back(off);
                }
                if (*end == ':')
                    p.words = std::clamp<uint32_t>(uint32_t(std::strtoul(end + 1, &end, 10)), 1, 64);
                out.push_back(p);
                v = *end == ';' ? end + 1 : nullptr;
            }
            return out;
        }();
        return list;
    }

    // GA2_FRAMETRACE_DUMP_AT=<s>[,<s>...] with GA2_FRAMETRACE_DUMP_DIR=<dir>:
    // writes all of EE RAM at those game times after the anchor (the first
    // message reveal), for tools/framerate_ramdiff.py.
    struct Dump
    {
        std::vector<double> at;
        size_t next = 0;
        bool anchored = false;
        double anchor = 0.0;
        std::string dir;
    };
    Dump &dumps()
    {
        static Dump d = [] {
            Dump out;
            const char *dir = std::getenv("GA2_FRAMETRACE_DUMP_DIR");
            const char *at = std::getenv("GA2_FRAMETRACE_DUMP_AT");
            if (dir && *dir && at && *at)
            {
                out.dir = dir;
                for (const char *v = at; v && *v;)
                {
                    char *end = nullptr;
                    out.at.push_back(std::strtod(v, &end));
                    v = end && *end == ',' ? end + 1 : nullptr;
                }
            }
            return out;
        }();
        return d;
    }

    void maybeDump(const uint8_t *rdram, double stateTime, uint64_t n)
    {
        Dump &d = dumps();
        if (d.next >= d.at.size())
            return;
        if (!d.anchored)
        {
            static const bool absolute = [] { const char *v = std::getenv("GA2_FRAMETRACE_DUMP_ANCHOR"); return v && std::strcmp(v, "abs") == 0; }();
            if (!absolute && g_revealed == 0)
                return;
            d.anchored = true;
            d.anchor = absolute ? 0.0 : stateTime;
            ga2_trace::line("D", "anchor n=%llu", static_cast<unsigned long long>(n));
        }
        // The state closest to the target: dump when the next one would be farther.
        const double target = d.anchor + d.at[d.next];
        if (stateTime + 0.5 * (1.0 / ga2_framepace::hz()) < target)
            return;
        char name[512];
        std::snprintf(name, sizeof(name), "%s/ram-%zu.bin", d.dir.c_str(), d.next);
        if (FILE *f = std::fopen(name, "wb"))
        {
            std::fwrite(rdram, 1, 32u << 20, f);
            std::fclose(f);
        }
        ga2_trace::line("D", "file=%s target=%.6f n=%llu", name, d.at[d.next], static_cast<unsigned long long>(n));
        ++d.next;
    }

    // GA2_FRAMETRACE_WINDOW=<from>,<to> (game seconds): M, W and T lines only inside it.
    bool inWindow(double gt)
    {
        static const std::pair<double, double> w = [] {
            const char *v = std::getenv("GA2_FRAMETRACE_WINDOW");
            double a = 0.0, b = 1e30;
            if (v && std::sscanf(v, "%lf,%lf", &a, &b) < 1)
                a = 0.0;
            return std::make_pair(a, b);
        }();
        return gt >= w.first && gt <= w.second;
    }

    template <size_t I>
    void emitIteration(const uint8_t *rdram, uint32_t sys, double dt)
    {
        const uint32_t app = word(rdram, sys + kSysApp);
        // *app is the scene manager's record: +4 transition state, +8 scene id, +0xc scene argument.
        const uint32_t vtable = app >= 0x100000u && app < 0x2000000u ? word(rdram, app) : 0u;
        const uint32_t manager = vtable >= 0x100000u && vtable < 0x2000000u ? vtable : 0u;
        const uint32_t sceneId = g_sceneId;
        const uint32_t sceneArg = manager ? word(rdram, manager + 0xc) : 0xffffffffu;
        char matrix[512];
        const bool matrices = inWindow(ga2_trace::gameTime());
        for (int i = 0; matrices && i < g_viewCount; ++i)
        {
            printMatrix(matrix, sizeof(matrix), g_views[i].m);
            ga2_trace::line("M", "n=%llu i=%d ra=%x m=%s", static_cast<unsigned long long>(g_iteration), i, g_views[i].ra, matrix);
        }
        for (int i = 0; matrices && i < g_worldCount; ++i)
        {
            printMatrix(matrix, sizeof(matrix), g_worlds[i].m);
            ga2_trace::line("W", "n=%llu i=%d ra=%x m=%s", static_cast<unsigned long long>(g_iteration), i, g_worlds[i].ra, matrix);
        }
        ga2_trace::line("U",
                        "n=%llu dt=%.9g fr=%u mx=%u mn=%u el=%.4f gl=%u gc=%u gn=%u fl=%llu vseen=%d wseen=%d sc=%x vt=%x sid=%u sarg=%u rn=%u rt=%llu",
                        static_cast<unsigned long long>(g_iteration), dt, word(rdram, sys + kSysFrames),
                        word(rdram, sys + kSysMax), word(rdram, sys + kSysMin), double(g_elapsed), g_glyphs, g_revealed, g_total,
                        static_cast<unsigned long long>(ga2_framepace::flips()), g_viewsSeen, g_worldsSeen, app, vtable, sceneId, sceneArg,
                        g_rngDraws, static_cast<unsigned long long>(g_rngTotal));
        if (g_rngDraws)
        {
            char callers[256];
            int len = 0;
            for (const RngStat &c : g_rngCallers)
                len += std::snprintf(callers + len, sizeof(callers) - len, len ? ",%x<%x<%x:%u" : "%x<%x<%x:%u", c.ra[0], c.ra[1],
                                     c.ra[2], c.count);
            ga2_trace::line("G", "n=%llu draws=%u callers=%s", static_cast<unsigned long long>(g_iteration), g_rngDraws, callers);
        }
        g_rngDraws = 0;
        g_rngCallers.clear();
        for (const Probe &p : probes())
        {
            uint32_t at = p.address;
            bool valid = true;
            for (const uint32_t hop : p.hops)
            {
                at = word(rdram, at);
                if (at < 0x100000u || at >= 0x2000000u)
                {
                    valid = false;
                    break;
                }
                at += hop;
            }
            if (!valid)
            {
                ga2_trace::line("P", "n=%llu a=%x v=null", static_cast<unsigned long long>(g_iteration), p.address);
                continue;
            }
            char words[1024];
            int n = 0;
            for (uint32_t i = 0; i < p.words; ++i)
                n += std::snprintf(words + n, sizeof(words) - n, i ? ",%08x" : "%08x", word(rdram, at + 4 * i));
            ga2_trace::line("P", "n=%llu a=%x h=%zu v=%s", static_cast<unsigned long long>(g_iteration), p.address, p.hops.size(), words);
        }
        maybeDump(rdram, ga2_trace::gameTime(), g_iteration);
        g_viewCount = g_worldCount = g_viewsSeen = g_worldsSeen = 0;
        g_glyphs = g_revealed = g_total = 0;
    }

    // GA2_PAD_SCRIPT=<file>: scripted pad presses in game time, so runs at
    // different frame rates press at the same point of the game. One line per
    // press: `<anchor> <seconds> <key> [hold seconds (default 0.15)]`, anchor
    // `abs` (game time since start), `sceneN` (N-th change of the game's
    // scene object) or `reveal` (first dialogue text). Keys: c Circle, x Cross,
    // v Triangle, z Square, Return Start, Tab Select, q/e L1/R1,
    // Shift_L/Shift_R L2/R2, r/f L3/R3, Up Down Left Right.
    struct PadPress
    {
        int anchor; // 0 abs, -1 reveal, N scene N
        double at, hold;
        uint16_t bit;
    };
    std::vector<PadPress> g_padScript;
    bool g_padScriptLoaded = false;
    uint32_t g_lastScene = 0;
    int g_sceneChanges = 0;
    double g_sceneAt[64] = {};
    double g_revealAt = -1.0;
    uint16_t g_lastScriptedPad = 0;

    uint16_t padBit(const std::string &key)
    {
        using namespace ga2_framepace;
        static const std::pair<const char *, uint16_t> keys[] = {
            {"c", PadCircle}, {"x", PadCross}, {"v", PadTriangle}, {"z", PadSquare}, {"Return", PadStart},
            {"Tab", PadSelect}, {"q", PadL1}, {"e", PadR1}, {"Shift_L", PadL2}, {"Shift_R", PadR2},
            {"r", PadL3}, {"f", PadR3}, {"Up", PadUp}, {"Down", PadDown}, {"Left", PadLeft}, {"Right", PadRight}};
        for (const auto &k : keys)
            if (key == k.first)
                return k.second;
        return 0;
    }

    void loadPadScript()
    {
        g_padScriptLoaded = true;
        const char *path = std::getenv("GA2_PAD_SCRIPT");
        if (!path || !*path)
            return;
        std::ifstream in(path);
        std::string line;
        while (std::getline(in, line))
        {
            std::istringstream words(line);
            std::string anchor, key;
            double at = 0.0, hold = 0.15;
            if (line.empty() || line[0] == '#' || !(words >> anchor >> at >> key))
                continue;
            words >> hold;
            PadPress press{0, at, hold, padBit(key)};
            if (anchor == "reveal")
                press.anchor = -1;
            else if (anchor.rfind("scene", 0) == 0)
                press.anchor = std::clamp(std::atoi(anchor.c_str() + 5), 1, 63);
            else if (anchor != "abs")
                press.bit = 0;
            if (press.bit)
                g_padScript.push_back(press);
            else
                std::cerr << "[ga2 fps] GA2_PAD_SCRIPT: cannot use line \"" << line << "\"\n";
        }
        std::cerr << "[ga2 fps] pad script: " << g_padScript.size() << " presses\n";
    }

    // The scripted pad for the step the game is about to take (game time `now`).
    void scriptPad(uint32_t sceneId, double now)
    {
        if (!g_padScriptLoaded)
            loadPadScript();
        if (sceneId != g_lastScene)
        {
            if (g_lastScene != 0 && g_sceneChanges < 63)
                g_sceneAt[++g_sceneChanges] = now;
            g_lastScene = sceneId;
        }
        if (g_revealAt < 0.0 && g_revealed > 0)
            g_revealAt = now;
        uint16_t held = 0;
        for (const PadPress &p : g_padScript)
        {
            const double base = p.anchor == 0 ? 0.0 : p.anchor < 0 ? g_revealAt : (p.anchor <= g_sceneChanges ? g_sceneAt[p.anchor] : -1.0);
            if (base < 0.0 && p.anchor != 0)
                continue;
            if (now >= base + p.at - 1e-9 && now < base + p.at + p.hold - 1e-9)
                held |= p.bit;
        }
        ga2_framepace::setScriptedPad(held);
        if (held != g_lastScriptedPad)
        {
            ga2_trace::line("E", "kind=pad mask=%04x", unsigned(held));
            g_lastScriptedPad = held;
        }
    }


    // The rate governor. GA2_FPS=N is the maximum: when the guest falls behind real time (game time per wall second
    // below 97 %, or iterations below 85 % of the rate) for half a second while the game thread is saturated (more
    // than 85 % of a core), the effective rate drops at an iteration boundary to the highest rung whose estimated cost
    // fits, else to the native cadence (iterations at 59.94 Hz with the game's own limits, the 30 fps views included:
    // the code path of the scenes that run native). Rungs: N, the multiples of 60 below it, 60 (full-rate iterations),
    // native. The cost of a rung is estimated from what the game thread spent per guest second (CPU time of the thread,
    // the IOP and audio work it does while waiting included): C(R') = C * R' / (iterations per guest second), i.e.
    // every iteration costs what it did, which leans high. It climbs, at most two rungs at a time, when the
    // optimistic estimate (45 % of the cost follows the iteration count, the rest follows guest time) is under 80 % of
    // a core for 3 s at full speed; after a climb that failed within 10 s the next one is held off for 10 s, 20 s, ...
    // up to 60 s. The game's time stays exact across a change (dt follows the rate). GA2_FPS_GOVERNOR=off pins the
    // maximum; GA2_FPS_GOVERNOR_TEST=<seconds> steps through the rungs by game time instead (a deterministic
    // sequence for the oracle). Every change is logged: "[ga2 fps] governor: a -> b fps (...)".
    struct Governor
    {
        bool adaptive = true;
        double testPeriod = 0.0;
        std::vector<int> ladder; // frame rates, highest first; 0 = native
        size_t idx = 0;
        double lastEntry = -1.0;
        double accWall = 0.0, accGdt = 0.0, cpuStart = -1.0;
        int accIters = 0, behind = 0, headroom = 0;
        double holdUntil = 0.0, lastClimb = -100.0;
        int failStreak = 0;
        int cap = 0; // the top rate the player asked for in the settings menu (0: the run's own)
        bool init = false;
    } g_gov;
    bool g_govNative = false;   // the governor's lowest rung: native cadence
    bool g_sceneNative = false; // the scene policy keeps this scene at the native rate

    double seconds() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
    double threadCpuSeconds()
    {
#ifdef _WIN32
        FILETIME created, exited, kernel, user;
        if (!GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user))
            return 0.0;
        auto ticks = [](const FILETIME &t) { return (uint64_t(t.dwHighDateTime) << 32) | t.dwLowDateTime; };
        return double(ticks(kernel) + ticks(user)) * 1e-7; // 100 ns units
#else
        timespec ts{};
        clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
        return double(ts.tv_sec) + double(ts.tv_nsec) * 1e-9;
#endif
    }

    // The frame rates the governor moves between, highest first: `top`, then the multiples of 60 below it (the game's
    // frame limits scale exactly there), then 60 (full-rate iterations: what the 30 fps views of the native game do not
    // give), then the native cadence (0). A top of 60 or less is the native cadence alone.
    void govLadder(int top)
    {
        g_gov.ladder.clear();
        if (top > 60)
        {
            g_gov.ladder.push_back(top);
            for (int r = (top - 1) / 60 * 60; r >= 60; r -= 60)
                g_gov.ladder.push_back(r);
            if (g_gov.ladder.back() != 60)
                g_gov.ladder.push_back(60);
        }
        g_gov.ladder.push_back(0);
    }

    void govInit()
    {
        g_gov.init = true;
        govLadder(ga2_framepace::startCap());
        g_govNative = g_gov.ladder[0] == 0; // started at 60: the native cadence until the settings menu raises the cap
        if (const char *v = std::getenv("GA2_FPS_GOVERNOR"); v && (std::strcmp(v, "off") == 0 || std::strcmp(v, "0") == 0))
            g_gov.adaptive = false;
        if (const char *v = std::getenv("GA2_FPS_GOVERNOR_TEST"); v && *v)
        {
            g_gov.testPeriod = std::atof(v);
            g_gov.adaptive = false;
        }
    }

    void govApply(PS2Runtime &runtime, uint8_t *rdram, uint32_t sys, size_t to, const char *why, double speed, double itRate, double load,
                  int previousRate = -1)
    {
        const int from = previousRate >= 0 ? previousRate : g_gov.ladder[g_gov.idx], rate = g_gov.ladder[to];
        g_gov.idx = to;
        g_govNative = rate == 0;
        if (rate > 0)
            ga2_framepace::setRate(runtime, rate);
        putWord(rdram, sys + kSysFrames, 1u); // the frame count measured under the other rate means nothing here
        g_gov.accWall = g_gov.accGdt = 0.0;
        g_gov.accIters = g_gov.behind = g_gov.headroom = 0;
        g_gov.cpuStart = -1.0;
        auto name = [](int r, char *out) { r > 0 ? std::snprintf(out, 16, "%d", r) : std::snprintf(out, 16, "native"); };
        char a[16], b[16];
        name(from, a);
        name(rate, b);
        std::fprintf(stderr, "[ga2 fps] governor: %s -> %s fps (%s; guest speed %.0f%%, %.0f iterations/s, game thread %.0f%% of a core, game time %.1f s)\n", a, b, why,
                     speed * 100.0, itRate, load * 100.0, g_gameTime);
        ga2_trace::line("E", "kind=governor from=%s to=%s", a, b);
    }

    // Once per iteration at the top of Update: the previous iteration is over.
    void govStep(PS2Runtime &runtime, uint8_t *rdram, uint32_t sys, double previousDt, bool previousNativeScene)
    {
        if (!g_gov.init)
            govInit();
        Governor &g = g_gov;
        const double now = seconds();
        const double wall = g.lastEntry >= 0.0 ? now - g.lastEntry : 0.0;
        g.lastEntry = now;
        // The settings menu set another top rate (never above the one this run was started with): the ladder is
        // rebuilt below it and the game moves to its top at this iteration boundary.
        if (const int cap = ga2_framepace::userCap(); cap != g.cap && g.testPeriod <= 0.0)
        {
            const int previous = g.ladder[std::min(g.idx, g.ladder.size() - 1)];
            g.cap = cap;
            govLadder(cap > 0 ? std::min(cap, ga2_framepace::fps()) : ga2_framepace::startCap());
            g.holdUntil = 0.0;
            g.failStreak = 0;
            govApply(runtime, rdram, sys, 0, "frame rate chosen in the settings menu", 1.0, 0.0, 0.0, previous);
            return;
        }
        if (g.testPeriod > 0.0)
        {
            const size_t want = size_t(g_gameTime / g.testPeriod) % g.ladder.size();
            if (want != g.idx)
                govApply(runtime, rdram, sys, want, "test sequence", 1.0, 0.0, 0.0);
            return;
        }
        if (!g.adaptive || g.ladder.size() < 2)
            return;
        // Loads, movies and scene switches are not what the rate decides: start the window over.
        if (wall <= 0.0 || wall > 0.25 || previousNativeScene || previousDt <= 0.0)
        {
            g.accWall = g.accGdt = 0.0;
            g.accIters = 0;
            g.cpuStart = -1.0;
            return;
        }
        if (g.cpuStart < 0.0)
            g.cpuStart = threadCpuSeconds();
        else
        {
            g.accWall += wall;
            g.accGdt += previousDt;
            ++g.accIters;
        }
        if (g.accWall < 0.25)
            return;
        const double cpu = threadCpuSeconds() - g.cpuStart;
        const double speed = g.accGdt / g.accWall, itRate = g.accIters / g.accWall, load = cpu / g.accWall;
        const double perGuest = load / std::max(speed, 0.05);              // core seconds per guest second
        const double itsPerGuest = std::max(1.0, itRate / std::max(speed, 0.05)); // iterations per guest second
        g.accWall = g.accGdt = 0.0;
        g.accIters = 0;
        g.cpuStart = threadCpuSeconds();
        const size_t last = g.ladder.size() - 1;
        const int rate = g.ladder[g.idx];
        // core seconds per guest second at rung k: pessimistic (all of it follows the iteration count) and optimistic
        // (45 % does: the IOP, audio and the other work that follows guest time does not)
        auto ratioTo = [&](size_t k) { return (g.ladder[k] > 0 ? g.ladder[k] : 59.94) / itsPerGuest; };
        auto cost = [&](size_t k) { return perGuest * ratioTo(k); };
        auto hope = [&](size_t k) { return perGuest * (0.55 + 0.45 * ratioTo(k)); };
        if (rate > 0)
        {
            const bool behind = (speed < 0.97 || itRate < 0.85 * rate) && load > 0.85;
            g.behind = behind ? g.behind + 1 : 0;
            if (g.behind >= 2) // 0.5 s
            {
                size_t to = last;
                for (size_t k = g.idx + 1; k < last; ++k)
                    if (cost(k) <= 0.92)
                    {
                        to = k;
                        break;
                    }
                // a climb that failed within 10 s holds the next one off for longer each time
                if (now - g.lastClimb < 10.0)
                    g.failStreak = std::min(g.failStreak + 1, 4);
                g.holdUntil = now + std::min(60.0, 5.0 * double(1 << g.failStreak));
                govApply(runtime, rdram, sys, to, "behind real time", speed, itRate, load);
                return;
            }
        }
        if (g.idx > 0)
        {
            size_t up = g.idx;
            for (size_t k = 0; k < g.idx; ++k)
                if (hope(k) <= 0.8)
                {
                    up = k; // the highest rung that is hoped to fit
                    break;
                }
            up = std::max(up, g.idx >= 2 ? g.idx - 2 : size_t(0)); // at most two rungs at a time
            const bool fits = up < g.idx && speed >= 0.985 && now >= g.holdUntil;
            g.headroom = fits ? g.headroom + 1 : 0;
            if (g.headroom >= 12)
            {
                if (now - g.lastClimb > 60.0)
                    g.failStreak = 0;
                g.lastClimb = now;
                govApply(runtime, rdram, sys, up, "headroom", speed, itRate, load);
            }
        }
    }

    template <size_t I>
    void update(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t sys = GPR_U32(ctx, 4);
        if (sys >= 0x100000u && sys < 0x2000000u)
        {
            const uint32_t app = word(rdram, sys + kSysApp);
            const uint32_t manager = app >= 0x100000u && app < 0x2000000u ? word(rdram, app) : 0u;
            g_sceneId = manager >= 0x100000u && manager < 0x2000000u ? word(rdram, manager + 8) : 0xffffffffu;
            if (ga2_framepace::enabled())
            {
                if (!g_fieldsKnown)
                {
                    g_fieldsKnown = true;
                    g_nativeMax = word(rdram, sys + kSysMax);
                    g_nativeMin = word(rdram, sys + kSysMin);
                }
                if (!g_stepKnown)
                {
                    // Before anything here writes it: the step the game set itself (1/refresh in float).
                    g_stepKnown = true;
                    g_nativeStep = getFloat(rdram, sys + kSysStep);
                }
                const bool wasNative = g_native;
                govStep(*runtime, rdram, sys, g_dt, g_sceneNative);
                g_sceneNative = !sceneAtRate(g_sceneId);
                g_native = g_sceneNative || g_govNative;
                // (The first iteration is no switch: the run starts in its mode with the game's own frame count.)
                if (g_native != wasNative && g_iteration > 0)
                {
                    // A switch: the frame count measured under the other mode means nothing here.
                    putWord(rdram, sys + kSysFrames, 1u);
                    if (ga2_trace::enabled())
                        ga2_trace::line("E", "kind=rate native=%d sid=%u", int(g_native), g_sceneId);
                }
                ga2_framepace::setNative(g_native);
                applyMode(rdram, sys);
                if (g_settle > 0u)
                    --g_settle;
            }
            // Game time against the VBLANK as natively. Natively an iteration starts at a VBLANK and game time trails the
            // VBLANK count by a lag L (frames) that changes only when an iteration overruns the catch-up limit: the frames
            // beyond maxFrames are lost. At N the iterations starting at a VBLANK keep the same L (the ones between are
            // half a frame further on), and:
            //   - an overrun (a load, a slow iteration) loses exactly the native frames: with S the slow iteration (it
            //     started in the native frame of VBLANK V_S) and W the one woken at VBLANK V_W, native counts
            //     F = V_W - V_S frames and loses F - min(F, maxFrames); W gets the game time that puts the next VBLANK
            //     at the native game time. (Rounding W to whole frames instead gained half a frame per slow iteration:
            //     the battle's voice lines, timed in EE time, ended late against game time.)
            //   - a view limited to 30 fps (and the iterations settling after a scene switch) ends its iterations on
            //     its native grid (otherwise the battle ran half a frame off and threshold timers tripped one
            //     iteration apart: a particle spawn moved, the random sequence and the battle camera changed).
            if (ga2_framepace::enabled() && !g_native && g_fieldsKnown)
            {
                const int rate = ga2_framepace::rate();
                const uint32_t perNative = rate % 60 == 0 ? uint32_t(rate / 60) : 0u;
                const uint32_t measured = word(rdram, sys + kSysFrames), limit = word(rdram, sys + kSysMax);
                const uint64_t vblank = ga2_activity::vsyncs.load(std::memory_order_relaxed);
                const double nativeStep = double(g_nativeStep);
                const double step = double(getFloat(rdram, sys + kSysStep));
                bool lagSet = false;
                if (perNative > 1u && measured > limit && g_lagKnown)
                {
                    const double frames = double(std::max<uint64_t>(1u, vblank - g_prevIterVblank));
                    // Natively the lag is a whole number of frames; the iterations at a VBLANK here may sit half a frame
                    // behind (a phase left from before), which the native frame boundary absorbs.
                    // (The first iteration of the program has no step of its own: woken here, it starts the native lag.)
                    const double lag = g_iteration <= 1u ? std::round(double(vblank) - g_gameTime / nativeStep)
                                                         : std::floor(g_lag + 0.25) + frames - std::min(frames, double(std::max<uint32_t>(1u, g_nativeMax)));
                    const double target = (double(vblank) + 1.0 - lag) * nativeStep; // game time at the next VBLANK
                    // This iteration waits `wait` ticks; when that is less than a native frame, the ticks after it until
                    // the VBLANK add one tick each.
                    const uint32_t wait = std::max<uint32_t>(1u, word(rdram, sys + kSysMin));
                    const long ticks = std::lround((target - g_gameTime) / step) - long(perNative > wait ? perNative - wait : 0u);
                    if (ticks >= 1)
                    {
                        putWord(rdram, sys + kSysMax, uint32_t(ticks)); // applyMode puts the limit back next iteration
                        putWord(rdram, sys + kSysFrames, uint32_t(ticks));
                        g_lag = lag;
                        lagSet = true;
                    }
                }
                else if (perNative > 1u && ((g_nativeMin > 1u && !fullRateBattle()) || g_settle > 0u))
                {
                    const uint32_t ticks = std::min(measured, limit);
                    // The view's native grid: every minFrames native frames from g_minOrigin; before it starts, and
                    // while settling, every native frame.
                    const bool viewGrid = g_nativeMin > 1u && !fullRateBattle() && g_minOrigin >= 0.0 && g_gameTime >= g_minOrigin - 1e-4;
                    const double grid = viewGrid ? nativeStep * g_nativeMin : nativeStep;
                    const double origin = viewGrid ? g_minOrigin : 0.0;
                    const uint32_t ticksPerPoint = perNative * (viewGrid ? g_nativeMin : 1u);
                    for (uint32_t add = 0; add < ticksPerPoint; ++add)
                    {
                        const double end = (g_gameTime + double(float(ticks + add) * float(step)) - origin) / grid;
                        if (std::fabs(end - std::round(end)) < 0.25 / double(ticksPerPoint))
                        {
                            if (add)
                            {
                                putWord(rdram, sys + kSysMax, ticks + add);
                                putWord(rdram, sys + kSysFrames, ticks + add);
                            }
                            break;
                        }
                    }
                }
                // A change of cadence (a 30 fps view or the settling after a scene switch begins or ends): this
                // iteration's game time is its own wait. Its measured frames are the previous iteration's wait: leaving
                // native cadence they put game time half a frame ahead of the VBLANK, entering it half a frame behind.
                const uint32_t wait = std::max<uint32_t>(1u, word(rdram, sys + kSysMin));
                if (perNative > 1u && !lagSet && measured <= limit && g_prevWait && wait != g_prevWait && measured == g_prevWait)
                {
                    putWord(rdram, sys + kSysMax, wait);
                    putWord(rdram, sys + kSysFrames, wait);
                }
                g_prevWait = wait;
                // The lag as seen by the iterations that start at a VBLANK (the first one after the count changed).
                if (!lagSet && vblank != g_prevIterVblank)
                {
                    g_lag = double(vblank) - g_gameTime / nativeStep;
                    g_lagKnown = true;
                }
                g_prevIterVblank = vblank;
            }
            // The step the game is about to take: min(frames, maxFrames) * step.
            const uint32_t frames = std::min(word(rdram, sys + kSysFrames), word(rdram, sys + kSysMax));
            const double dt = double(float(frames) * getFloat(rdram, sys + kSysStep));
            if (ga2_trace::enabled())
                emitIteration<I>(rdram, sys, dt);
            g_prevIterStartGt = g_iterStartGt;
            g_iterStartGt = g_gameTime;
            g_gameTime += dt;
            g_dt = dt;
            {
                // One native iteration of game time: the game's native frames per iteration (minFrames: 2 in the views
                // limited to 30 fps) times 1/59.94 s.
                const double quantum = 1001.0 / 60000.0 * std::max<uint32_t>(1u, g_nativeMin);
                g_tickAcc += dt;
                g_nativeTick = g_native || g_tickAcc >= quantum * 0.999;
                if (g_nativeTick)
                    g_tickAcc = std::max(0.0, g_tickAcc - std::floor(g_tickAcc / quantum + 0.001) * quantum);
                if (g_nativeTick)
                {
                    g_rngSeq.clear();
                    g_effectCalls.clear();
                }
                g_rngCursor = 0;
                g_effectCursor = 0;
            }
            ga2_trace::setGameTime(g_gameTime);
            static const bool scripted = std::getenv("GA2_PAD_SCRIPT") != nullptr;
            if (scripted)
                scriptPad(g_sceneId, g_gameTime);
            ++g_iteration;
        }
        g_original<I, Update>(rdram, ctx, runtime);
    }

    // setMaxFrames(sys, value) -> previous value; setMinFrames(sys, value) ->
    // previous value and the VBLANK handler's interval (native units).
    template <size_t I>
    void setMax(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t sys = GPR_U32(ctx, 4), value = GPR_U32(ctx, 5);
        if (!g_fieldsKnown)
        {
            g_fieldsKnown = true;
            g_nativeMax = word(rdram, sys + kSysMax);
            g_nativeMin = word(rdram, sys + kSysMin);
        }
        if (g_native)
        {
            // Native: the game's own setter (its fields and its EE cycles, exactly as without the hooks).
            g_nativeMax = value;
            g_original<I, SetMax>(rdram, ctx, runtime);
            return;
        }
        const uint32_t previous = g_nativeMax;
        g_nativeMax = value;
        putWord(rdram, sys + kSysMax, g_native ? value : scaledMax(value));
        SET_GPR_U32(ctx, 2, previous);
        ctx->pc = GPR_U32(ctx, 31);
    }

    template <size_t I>
    void setMin(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t sys = GPR_U32(ctx, 4), value = GPR_U32(ctx, 5);
        if (!g_fieldsKnown)
        {
            g_fieldsKnown = true;
            g_nativeMax = word(rdram, sys + kSysMax);
            g_nativeMin = word(rdram, sys + kSysMin);
        }
        if (g_native)
        {
            g_nativeMin = value;
            g_original<I, SetMin>(rdram, ctx, runtime);
            return;
        }
        const uint32_t previous = g_nativeMin;
        g_nativeMin = value;
        if (value > 1u && previous <= 1u)
        {
            // Natively the limit is set in the native frame holding this iteration's start (rounded up) and the view's
            // first iteration starts one frame later.
            const double nativeStep = double(g_nativeStep);
            g_minOrigin = (std::ceil(g_iterStartGt / nativeStep - 1e-3) + 1.0) * nativeStep;
        }
        else if (value <= 1u)
            g_minOrigin = -1.0;
        putWord(rdram, sys + kSysMin, g_native ? value : sysMinNow(value));
        putWord(rdram, kDiscs[I].intervalGlobal, value); // the real VBLANK wait of the other callers
        SET_GPR_U32(ctx, 2, previous);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // WaitVSync: RenderFlip's call sleeps on the frame clock.
    template <size_t I>
    void waitVSync(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t ra = GPR_U32(ctx, 31);
        if (ra == kDiscs[I].waitRa && !g_native)
        {
            const uint32_t sys = word(rdram, kDiscs[I].sysGlobal);
            const uint32_t interval = std::max<uint32_t>(1u, word(rdram, sys + kSysMin));
            // An iteration that overran the catch-up limit (a load, a scene switch: frames > maxFrames) ends at the next
            // VBLANK, as the native game's VBLANK wait does after an overrun: the game time it loses is then exactly the
            // native loss. Ending at the next tick lost less (half frames at 120) and game time drifted ahead of the
            // IOP-timed events (voice lines, streams) with every load.
            const bool overran = word(rdram, sys + kSysFrames) > word(rdram, sys + kSysMax);
            ga2_framepace::waitTick(*runtime, interval, ra, overran);
        }
        g_original<I, WaitVSync>(rdram, ctx, runtime);
    }

    // Elapsed EE timer ticks -> vsyncs * 100 (result in f0). RenderFlip's call
    // is converted to N-Hz frames.
    template <size_t I>
    void ticks(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        g_original<I, Ticks>(rdram, ctx, runtime);
        if (GPR_U32(ctx, 31) == kDiscs[I].ticksRa)
        {
            g_elapsed = ctx->f[0] / 100.0f;
            if (ga2_framepace::enabled() && !g_native)
                ctx->f[0] = float(ctx->f[0] * ratio());
        }
    }

    // The game writes new display registers: a frame is ready to show.
    template <size_t I>
    void flip(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ga2_framepace::noteFlip();
        g_original<I, Flip>(rdram, ctx, runtime);
    }

    // MatSet(kind, matrix): kind 0 sets the view (camera), 1 the world matrix
    // of the next object. The oracle keeps the frame's first few of each.
    template <size_t I>
    void matSet(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t kind = GPR_U32(ctx, 4);
        if (kind <= 1u)
        {
            MatRecord *list = kind == 0u ? g_views : g_worlds;
            int &count = kind == 0u ? g_viewCount : g_worldCount;
            int &seen = kind == 0u ? g_viewsSeen : g_worldsSeen;
            ++seen;
            if (count < (kind == 0u ? kMaxViews : kMaxWorlds))
            {
                list[count].ra = GPR_U32(ctx, 31);
                std::memcpy(list[count].m, rdram + (GPR_U32(ctx, 5) & 0x1fffff0u), sizeof(list[count].m));
                ++count;
            }
        }
        g_original<I, MatSet>(rdram, ctx, runtime);
    }

    // GA2_FRAMETRACE_ITEMS=1: the 2D layer's items whose placement changed
    // since they were last logged, as T lines (item address, kind, x, y, w, h,
    // scale x, scale y, draw callback). Shows which UI elements animate and
    // how (audit aid; see tools/framerate_oracle.py --items).
    struct ItemState
    {
        float v[6];
    };
    std::unordered_map<uint32_t, ItemState> g_items;

    template <size_t I>
    void itemDraw(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        static const bool trace = ga2_trace::enabled() && [] { const char *v = std::getenv("GA2_FRAMETRACE_ITEMS"); return v && *v && *v != '0'; }();
        if (trace && inWindow(ga2_trace::gameTime()))
        {
            const uint32_t item = GPR_U32(ctx, 4);
            if (item >= 0x100000u && item < 0x1ffff00u)
            {
                const ItemState now{{getFloat(rdram, item + 0x3c), getFloat(rdram, item + 0x40), getFloat(rdram, item + 0x4c),
                                     getFloat(rdram, item + 0x50), getFloat(rdram, item + 0x60), getFloat(rdram, item + 0x64)}};
                auto it = g_items.find(item);
                if (it == g_items.end() || std::memcmp(it->second.v, now.v, sizeof(now.v)) != 0)
                {
                    g_items[item] = now;
                    ga2_trace::line("T", "n=%llu item=%x kind=%u v=%.6g,%.6g,%.6g,%.6g,%.6g,%.6g cb=%x",
                                    static_cast<unsigned long long>(g_iteration), item, word(rdram, item + 8), now.v[0], now.v[1],
                                    now.v[2], now.v[3], now.v[4], now.v[5], word(rdram, item + 0x78));
                }
            }
        }
        g_original<I, ItemDraw>(rdram, ctx, runtime);
    }

    // One step up the game's call stack: `ra` returns into a function whose
    // stack pointer is `sp`; find that function's prologue (the nearest
    // `addiu/daddiu sp, sp, -N` before ra) and the slot it saved its own return
    // address in (same walk as ga2_features.cpp's HUD log).
    bool callerOf(const uint8_t *rdram, uint32_t ra, uint32_t sp, uint32_t &callerRa, uint32_t &callerSp)
    {
        if (ra < 0x100000u || ra >= 0x2000000u)
            return false;
        for (uint32_t pc = ra - 8u, n = 0; n < 8192 && pc >= 0x100000u; pc -= 4u, ++n)
        {
            const uint32_t op = word(rdram, pc);
            if ((op >> 16) != 0x27bdu && (op >> 16) != 0x67bdu)
                continue;
            const int16_t frame = int16_t(op & 0xffffu);
            if (frame >= 0)
                continue;
            for (uint32_t q = pc + 4u; q < pc + 4u * 48u && q < ra; q += 4u)
            {
                const uint32_t save = word(rdram, q);
                if ((save >> 16) == 0xffbfu || (save >> 16) == 0x7fbfu || (save >> 16) == 0xafbfu)
                {
                    callerRa = word(rdram, sp + uint32_t(int16_t(save & 0xffffu)));
                    callerSp = sp + uint32_t(-int32_t(frame));
                    return true;
                }
            }
            return false;
        }
        return false;
    }

    template <size_t I>
    void rng(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t chain[3] = {GPR_U32(ctx, 31), 0, 0};
        uint32_t sp = GPR_U32(ctx, 29);
        for (int depth = 1; depth < 3 && callerOf(rdram, chain[depth - 1], sp, chain[depth], sp); ++depth)
        {
        }
        // Random rolls made by per-frame drawing (a flickering sprite) repeat the last native frame's rolls
        // between native frames, so the generator's sequence stays the native one.
        const uint32_t hold = kDiscs[I].rngHold;
        const uint32_t effect = kDiscs[I].effect;
        const bool held = ga2_framepace::enabled() && !noFix() && ((hold && chain[1] >= hold && chain[1] < hold + 0x4a0u) ||
                                                       (effect && chain[1] >= effect && chain[1] < effect + 0x490u));
        if (held && !g_native && !g_nativeTick)
        {
            // repeat the last boundary iteration's rolls in order; never draw from the generator (a call the boundary
            // iteration did not make gets the last value)
            const uint32_t value = g_rngCursor < g_rngSeq.size() ? g_rngSeq[g_rngCursor] : (g_rngSeq.empty() ? 0u : g_rngSeq.back());
            ++g_rngCursor;
            SET_GPR_U32(ctx, 2, value);
            ctx->pc = GPR_U32(ctx, 31);
            return;
        }
        if (ga2_trace::enabled())
        {
            ++g_rngDraws;
            ++g_rngTotal;
            bool found = false;
            for (RngStat &c : g_rngCallers)
                if (c.ra[0] == chain[0] && c.ra[1] == chain[1] && c.ra[2] == chain[2])
                {
                    ++c.count;
                    found = true;
                    break;
                }
            if (!found && g_rngCallers.size() < 16)
                g_rngCallers.push_back({{chain[0], chain[1], chain[2]}, 1});
        }
        g_original<I, Rng>(rdram, ctx, runtime);
        if (held && !g_native)
            g_rngSeq.push_back(GPR_U32(ctx, 2));
    }

    // The ship map's camera follows its target with `pos += (target - pos) * 0.1`
    // once per iteration (x and z while the camera is moving, y always): at N Hz
    // that eases N/60 times too fast. The step of those fields is scaled so the
    // same fraction of the way is covered per second: k' = 1 - (1 - k)^(60/N).
    // this+0x110/0x114/0x118 = x/y/z, +0x124 = moving; (this, mode): mode 1 snaps.
    template <size_t I>
    void mapCamera(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (!ga2_framepace::enabled() || g_native || GPR_U32(ctx, 5) != 0u)
        {
            g_original<I, MapCamera>(rdram, ctx, runtime);
            return;
        }
        const uint32_t self = GPR_U32(ctx, 4);
        const bool moving = word(rdram, self + 0x124) != 0u;
        float before[3] = {getFloat(rdram, self + 0x110), getFloat(rdram, self + 0x114), getFloat(rdram, self + 0x118)};
        g_original<I, MapCamera>(rdram, ctx, runtime);
        constexpr double kEase = 0.099999994;
        const double scale = (1.0 - std::pow(1.0 - kEase, 1.0 / ratio())) / kEase;
        for (int i = 0; i < 3; ++i)
        {
            if (i != 1 && !moving)
                continue; // a straight copy of the target, not an ease
            const float now = getFloat(rdram, self + 0x110 + 4u * i);
            const float scaled = float(before[i] + (now - before[i]) * scale);
            std::memcpy(rdram + self + 0x110 + 4u * i, &scaled, sizeof(scaled));
        }
    }

    // GA2_FRAMETRACE_FILL=1: full-screen flat draws (fades, flashes) as Z lines with
    // the caller chain, to find the code that counts their frames.
    uint32_t g_lastColour = 0;

    template <size_t I, int H>
    void fillDraw(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        static const bool on = ga2_trace::enabled() && [] { const char *v = std::getenv("GA2_FRAMETRACE_FILL"); return v && *v && *v != '0'; }();
        if (on)
        {
            float x0, y0, x1, y1;
            if (H == Fill7) // colour setter (r, g, b, a in a0..a3): remembered for the next draw
            {
                g_lastColour = GPR_U32(ctx, 4) | (GPR_U32(ctx, 5) << 8) | (GPR_U32(ctx, 6) << 16) | (GPR_U32(ctx, 7) << 24);
                g_original<I, H>(rdram, ctx, runtime);
                return;
            }
            if (H == Fill0 || H == Fill4) // int rect: a0 x0, a1 y0, a2 x1, a3 y1
            {
                x0 = float(int16_t(GPR_S32(ctx, 4)));
                y0 = float(int16_t(GPR_S32(ctx, 5)));
                x1 = float(int16_t(GPR_S32(ctx, 6)));
                y1 = float(int16_t(GPR_S32(ctx, 7)));
            }
            else
            {
                x0 = ctx->f[12];
                y0 = ctx->f[13];
                x1 = (H == Fill1 || H == Fill5) ? ctx->f[14] : ctx->f[16];
                y1 = (H == Fill1 || H == Fill5) ? ctx->f[15] : ctx->f[19];
            }
            if (std::fabs(x1 - x0) >= 600.0f && std::fabs(y1 - y0) >= 400.0f)
            {
                uint32_t chain[4] = {GPR_U32(ctx, 31), 0, 0, 0};
                uint32_t sp = GPR_U32(ctx, 29);
                for (int depth = 1; depth < 4 && callerOf(rdram, chain[depth - 1], sp, chain[depth], sp); ++depth)
                {
                }
                ga2_trace::line("Z", "n=%llu kind=%d ra=%x<%x<%x<%x rect=%.0f,%.0f,%.0f,%.0f rgba=%08x", static_cast<unsigned long long>(g_iteration),
                                int(H) - int(Fill0), chain[0], chain[1], chain[2], chain[3], x0, y0, x1, y1, g_lastColour);
            }
        }
        g_original<I, H>(rdram, ctx, runtime);
    }

    // A per-frame effect that advances its own state and rolls the generator on every call (an angle word,
    // random jitter): between native frames it repeats the last native frame's calls exactly: the same state
    // going in, the same rolls (see rng<>), the same state coming out, so the effect runs at the native rate
    // and the generator's sequence stays the native one.
    template <size_t I>
    void effectHold(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t state = kDiscs[I].effectState;
        if (!ga2_framepace::enabled() || g_native)
        {
            g_original<I, Effect>(rdram, ctx, runtime);
            return;
        }
        if (g_nativeTick)
        {
            const uint32_t before = word(rdram, state);
            g_original<I, Effect>(rdram, ctx, runtime);
            g_effectCalls.push_back({before, word(rdram, state)});
            return;
        }
        if (g_effectCursor < g_effectCalls.size())
        {
            const EffectCall call = g_effectCalls[g_effectCursor++];
            putWord(rdram, state, call.before);
            g_original<I, Effect>(rdram, ctx, runtime);
            putWord(rdram, state, call.after);
            return;
        }
        g_original<I, Effect>(rdram, ctx, runtime);
    }

    // Logic that counts iterations instead of using dt (fades of N frames,
    // `x = x*k + c` per call, per-iteration random rolls, ...) runs N/60 times
    // too fast. A gated function runs once per native frame of game time: the
    // first call of an iteration decides (game time since its last run reaches
    // 1/59.94 s), all calls of that iteration follow it, skipped calls return what
    // the last real call returned. An `update(dt)` style function (f12 = dt) is
    // called with dt = the whole native steps since its last run (one at 60 fps, two in the 30 fps views) and with sys+0x08 set to the same for the
    // call (some integrate in units of sys+0x08: `while (dt > 0) { ...; dt -= sys+8 }`).
    // The remainder carries, so the cadence is exactly 59.94 Hz at any N.
    // Built-in gates are in DiscRate::gates; GA2_FPS_GATES=<hex>[:u][,<hex>[:u]...]
    // adds more guest addresses for experiments (u: update(dt) style).
    struct GateState
    {
        uint32_t address = 0;
        bool update = false, tap = false, fresh = false, pad = false;
        uint64_t decided = ~0ull;
        bool run = true;
        double acc = 1.0, pass = 0.0; // the first call runs
        uint32_t lastV0 = 0;
        uint64_t runs = 0, calls = 0, skipped = 0;
        bool inflight = false; // the last real call has not come back through the wrapper
        bool noResult = false; // the callers ignore v0: a call that blocked counts as done (no re-run)
        float savedStep = 0.0f;
        // A real call returns through the gate itself: ra is pointed at the gated function's entry, so the function's
        // final `jr ra` comes back here even when it blocked in a synchronous RPC and was resumed by the scheduler
        // (its host call never returns then). That return records v0 and continues at the caller's real ra.
        bool awaiting = false;
        uint32_t callerRa = 0;
    };
    // Gated functions whose result nobody reads: the per-frame sound updates. They block in a synchronous RPC on every
    // call, so the re-run rule below would run them every iteration and the IOP would see N/60 times the native requests
    // (each runs the IOP ahead of EE time: loads and voice lines finished earlier in game time than natively).
    bool resultUnused(uint32_t address)
    {
        // per-frame sound/stream update (sound driver requests), sound manager update (voice status queries)
        for (uint32_t a : {0x1229f8u, 0x122c10u, 0x122a78u, 0x12ed58u, 0x12f5d0u, 0x12f418u, 0x12f730u})
            if (address == a)
                return true;
        return false;
    }
    GateState g_gates[kGateSlots];

    // The gated function returned (directly, or after blocking): the bookkeeping after a real call.
    template <size_t I, int K>
    void gateReturned(uint8_t *rdram, R5900Context *ctx)
    {
        GateState &g = g_gates[K];
        g.awaiting = false;
        g.inflight = false;
        g.lastV0 = GPR_U32(ctx, 2);
        if (g.update)
        {
            const uint32_t sys = word(rdram, kDiscs[I].sysGlobal);
            std::memcpy(rdram + sys + kSysStep, &g.savedStep, sizeof(g.savedStep));
        }
        SET_GPR_U32(ctx, 31, g.callerRa);
        ctx->pc = g.callerRa;
    }

    template <size_t I, int K>
    void callThroughGate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        GateState &g = g_gates[K];
        g.callerRa = GPR_U32(ctx, 31);
        g.awaiting = true;
        g.inflight = true;
        SET_GPR_U32(ctx, 31, g.address);
        g_original<I, GateBase + K>(rdram, ctx, runtime);
        // Came back without blocking: the function's `jr ra` left pc at the gate's entry.
        if (g.awaiting && ctx->pc == g.address)
            gateReturned<I, K>(rdram, ctx);
    }

    template <size_t I, int K>
    void gate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        GateState &g = g_gates[K];
        if (g.awaiting && GPR_U32(ctx, 31) == g.address)
        {
            gateReturned<I, K>(rdram, ctx);
            return;
        }
        if (g.tap)
        {
            ga2_trace::line("C", "n=%llu a=%x f12=%.5f a0=%x a1=%x ra=%x", static_cast<unsigned long long>(g_iteration), g.address, double(ctx->f[12]),
                            GPR_U32(ctx, 4), GPR_U32(ctx, 5), GPR_U32(ctx, 31));
            g_original<I, GateBase + K>(rdram, ctx, runtime);
            ga2_trace::line("C", "n=%llu a=%x ret v0=%x", static_cast<unsigned long long>(g_iteration), g.address, GPR_U32(ctx, 2));
            return;
        }
        // Before the first Update (boot, waiting loops outside the main loop) nothing is gated.
        if (!ga2_framepace::enabled() || g_native || g_iteration == 0)
        {
            g_original<I, GateBase + K>(rdram, ctx, runtime);
            return;
        }
        constexpr double kNative = 1001.0 / 60000.0;
        if (g.decided != g_iteration)
        {
            // A function that was not called in the previous iteration starts over (the first call of a new phase
            // runs at once; time spent idle is not owed to it).
            const double quantum = kNative * std::max<uint32_t>(1u, g_nativeMin); // one native iteration
            if (g.decided + 1 != g_iteration)
                g.acc = quantum;
            else
                g.acc += g_dt;
            g.decided = g_iteration;
            g.skipped = 0;
            g.run = g.acc >= quantum * 0.999;
            if (g.run)
            {
                // whole native frames of game time since the last run (a 30 fps view: two)
                const double steps = std::floor(g.acc / kNative + 0.001);
                g.pass = steps * kNative;
                g.acc = std::max(0.0, g.acc - g.pass);
            }
            g.runs += g.run;
        }
        ++g.calls;
        // A call that blocks the guest thread (a synchronous RPC) is resumed by the scheduler and returns straight to its
        // caller: the code after the original call below never ran, so the return value remembered for skipped calls is
        // stale. Such a function runs again at once (and a stuck sys+0x08 is put back).
        if (g.inflight && g.noResult)
            g.inflight = false;
        if (g.inflight)
        {
            g.inflight = false;
            g.run = true;
            if (g.update)
            {
                const uint32_t sysNow = word(rdram, kDiscs[I].sysGlobal);
                std::memcpy(rdram + sysNow + kSysStep, &g.savedStep, sizeof(g.savedStep));
            }
        }
        // A function that is polled in a loop within one iteration (waiting for it to change) must still run: after
        // a number of skipped calls in the same iteration it does.
        if (!g.run && ++g.skipped > 256)
            g.run = true;
        // A freshly created widget (scale words 0,0: `fresh` gates) is updated at once, so that it is not drawn invisible
        // for the iterations until its next native boundary.
        bool force = false;
        if (!g.run && g.fresh && g.update)
        {
            const uint32_t self = GPR_U32(ctx, 4);
            force = self && word(rdram, self + 0x74) == 0u && word(rdram, self + 0x78) == 0u;
        }
        if (!g.run && !force)
        {
            if (g.pad)
            {
                // The pad updater did not run: nothing new happened. Last call's state is the previous one and the repeat
                // outputs report no event, so a press or repeat lasts one native frame, not one iteration.
                const DiscRate &d = kDiscs[I];
                for (uint32_t port = 0; port < 2; ++port)
                {
                    putWord(rdram, d.padRaw - 8 + 4 * port, word(rdram, d.padRaw + 4 * port));
                    putWord(rdram, d.padRaw + 0x10 + 4 * port, word(rdram, d.padRaw + 0x18 + 4 * port));
                    for (uint32_t group = 0; group < 4; ++group)
                        putWord(rdram, d.padOut + 0x10 * group + 4 * port, 0xffffffffu);
                }
            }
            SET_GPR_U32(ctx, 2, g.lastV0);
            ctx->pc = GPR_U32(ctx, 31);
            return;
        }
        if (!g.update)
        {
            callThroughGate<I, K>(rdram, ctx, runtime);
            return;
        }
        const uint32_t sys = word(rdram, kDiscs[I].sysGlobal);
        // The game's own step and dt arithmetic (float(frames) * step): a step rounded differently (1001/60000 in
        // double) let a threshold like `b0 >= sys+8` trip one iteration later now and then, which moved a particle
        // spawn's random rolls and changed every later random choice (the battle camera's orbit direction).
        const float native = g_nativeStep;
        const float saved = getFloat(rdram, sys + kSysStep);
        g.savedStep = saved;
        std::memcpy(rdram + sys + kSysStep, &native, sizeof(native));
        const double steps = force || g.pass <= 0.0 ? double(std::max<uint32_t>(1u, g_nativeMin)) : std::floor(g.pass / kNative + 0.5);
        ctx->f[12] = float(steps) * g_nativeStep;
        callThroughGate<I, K>(rdram, ctx, runtime);
    }

    template <size_t I, int K>
    void bindGate(PS2Runtime &runtime, uint32_t address, bool update, bool tap, bool fresh, bool pad)
    {
        if (!address || !runtime.hasFunction(address))
        {
            if (address)
                std::cerr << "[ga2 fps] gate 0x" << std::hex << address << std::dec << " not in the generated table\n";
            return;
        }
        g_gates[K].address = address;
        g_gates[K].update = update;
        g_gates[K].tap = tap;
        g_gates[K].fresh = fresh;
        g_gates[K].pad = pad;
        g_gates[K].noResult = resultUnused(address);
        g_original<I, GateBase + K> = runtime.lookupFunction(address);
        runtime.replaceFunction(address, gate<I, K>);
    }

    template <size_t I, int... K>
    void bindGates(PS2Runtime &runtime, const uint32_t *addresses, const bool *updates, const bool *taps, const bool *fresh, const bool *pads, std::integer_sequence<int, K...>)
    {
        (bindGate<I, K>(runtime, addresses[K], updates[K], taps[K], fresh[K], pads[K]), ...);
    }

    // The scene runs with sys+0x08 at the native frame time: code that turns a
    // number of frames into seconds with it (fade and wait lengths: `12 frames *
    // sys+8`) then keeps its duration. dt itself was taken from the high-rate step
    // before this call. Update sets the step again before the next dt.
    template <size_t I>
    void appUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (ga2_framepace::enabled() && !g_native && g_fieldsKnown)
        {
            const uint32_t sys = word(rdram, kDiscs[I].sysGlobal);
            std::memcpy(rdram + sys + kSysStep, &g_nativeStep, sizeof(g_nativeStep)); // the game's own float step
        }
        g_original<I, AppUpdate>(rdram, ctx, runtime);
    }

    // Scene switches at the native time. A scene requests a switch in one iteration and the manager carries it out in
    // the next: natively one native frame after the request. At N the request comes from an iteration that may start
    // half a frame earlier (the scenes also count iterations before requesting: a load, then one more iteration), and
    // the switch came one native frame early at every scene change (Zettai: the sortie loads and the battle start).
    // A request made in the iteration starting at game time t is carried out at the first iteration starting at or
    // after (the native frame holding t, rounded up) + 1 native frame; until then the manager's update is skipped
    // (the old scene holds its picture, usually a fade to black, for those ticks).
    double g_requestDue = -1.0;
    uint64_t g_requestSeen = ~0ull;

    template <size_t I>
    void sceneManager(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t manager = GPR_U32(ctx, 5);
        if (!ga2_framepace::enabled() || g_native || g_iteration < 2 || !manager || word(rdram, manager + 4) == 0u)
        {
            g_requestSeen = ~0ull;
            g_original<I, SceneManager>(rdram, ctx, runtime);
            return;
        }
        const double nativeStep = double(g_nativeStep);
        if (g_requestSeen == ~0ull)
        {
            g_requestSeen = g_iteration;
            const double frame = std::ceil(g_prevIterStartGt / nativeStep - 1e-3);
            g_requestDue = (frame + 1.0) * nativeStep;
        }
        if (ga2_trace::enabled())
            ga2_trace::line("E", "kind=scenereq req=%u start=%.4f prev=%.4f due=%.4f", word(rdram, manager + 4), g_iterStartGt,
                            g_prevIterStartGt, g_requestDue);
        if (g_iterStartGt < g_requestDue - 1e-3 * nativeStep && g_iteration - g_requestSeen < 16u)
        {
            ctx->pc = GPR_U32(ctx, 31); // not yet: the switch waits for its native frame
            return;
        }
        g_requestSeen = ~0ull;
        g_settle = 3u;
        g_original<I, SceneManager>(rdram, ctx, runtime);
    }

    template <size_t I>
    void mapUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        g_mapIteration = g_iteration;
        g_original<I, MapUpdate>(rdram, ctx, runtime);
    }

    template <size_t I>
    void glyph(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ++g_glyphs;
        if (GPR_U32(ctx, 7) >= g_revealed)
        {
            g_revealed = GPR_U32(ctx, 7);
            g_total = GPR_U32(ctx, 6);
        }
        g_original<I, Glyph>(rdram, ctx, runtime);
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        const bool rate = ga2_framepace::enabled();
        const bool trace = ga2_trace::enabled();
        const bool script = std::getenv("GA2_PAD_SCRIPT") != nullptr;
        if (!rate && !trace && !script)
            return;
        const DiscRate &d = kDiscs[I];
        for (uint32_t address : {d.update, d.waitVSync, d.ticks, d.setMax, d.setMin, d.flip, d.matSet, d.glyph})
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 fps] " << d.disc << ": 0x" << std::hex << address << std::dec
                          << " not in the generated table; frame rate hooks off\n";
                return;
            }
        auto hook = [&](uint32_t address, PS2Runtime::RecompiledFunction &original, PS2Runtime::RecompiledFunction replacement) {
            original = runtime.lookupFunction(address);
            runtime.replaceFunction(address, replacement);
        };
        hook(d.update, g_original<I, Update>, update<I>);
        hook(d.ticks, g_original<I, Ticks>, ticks<I>);
        hook(d.matSet, g_original<I, MatSet>, matSet<I>);
        hook(d.glyph, g_original<I, Glyph>, glyph<I>);
        if (trace && std::getenv("GA2_FRAMETRACE_FILL"))
        {
            if (runtime.hasFunction(d.fill[0])) hook(d.fill[0], g_original<I, Fill0>, fillDraw<I, Fill0>);
            if (runtime.hasFunction(d.fill[1])) hook(d.fill[1], g_original<I, Fill1>, fillDraw<I, Fill1>);
            if (runtime.hasFunction(d.fill[2])) hook(d.fill[2], g_original<I, Fill2>, fillDraw<I, Fill2>);
            if (runtime.hasFunction(d.fill[3])) hook(d.fill[3], g_original<I, Fill3>, fillDraw<I, Fill3>);
            if (runtime.hasFunction(d.fill[4])) hook(d.fill[4], g_original<I, Fill4>, fillDraw<I, Fill4>);
            if (runtime.hasFunction(d.fill[5])) hook(d.fill[5], g_original<I, Fill5>, fillDraw<I, Fill5>);
            if (runtime.hasFunction(d.fill[6])) hook(d.fill[6], g_original<I, Fill6>, fillDraw<I, Fill6>);
            if (runtime.hasFunction(d.fill[7])) hook(d.fill[7], g_original<I, Fill7>, fillDraw<I, Fill7>);
        }
        if ((trace || rate) && runtime.hasFunction(d.rng))
            hook(d.rng, g_original<I, Rng>, rng<I>);
        if (std::getenv("GA2_FRAMETRACE_ITEMS") && runtime.hasFunction(d.itemDraw))
            hook(d.itemDraw, g_original<I, ItemDraw>, itemDraw<I>);
        hook(d.flip, g_original<I, Flip>, flip<I>); // the oracle's frame fingerprints are tagged by flip
        if (rate && !noFix() && runtime.hasFunction(kAppUpdate[I]) && !std::getenv("GA2_FPS_NOAPPSTEP"))
            hook(kAppUpdate[I], g_original<I, AppUpdate>, appUpdate<I>);
        if (rate && runtime.hasFunction(d.mapUpdate))
            hook(d.mapUpdate, g_original<I, MapUpdate>, mapUpdate<I>);
        if (rate && !noFix() && runtime.hasFunction(kSceneManagerUpdate[I]))
            hook(kSceneManagerUpdate[I], g_original<I, SceneManager>, sceneManager<I>);
        if (rate && !noFix() && runtime.hasFunction(d.mapCamera))
            hook(d.mapCamera, g_original<I, MapCamera>, mapCamera<I>);
        if (rate && !noFix() && d.effect && runtime.hasFunction(d.effect))
            hook(d.effect, g_original<I, Effect>, effectHold<I>);
        if (rate || (trace && std::getenv("GA2_FPS_GATES")))
        {
            uint32_t addresses[kGateSlots] = {};
            bool updates[kGateSlots] = {};
            bool taps[kGateSlots] = {};
            bool fresh[kGateSlots] = {};
            bool pads[kGateSlots] = {};
            int n = 0;
            // GA2_FPS_NOGATE=<hex>[,<hex>...] leaves single built-in gates out (a diagnostic)
            auto skipped = [](uint32_t address) {
                const char *v = std::getenv("GA2_FPS_NOGATE");
                for (; v && *v;)
                {
                    char *end = nullptr;
                    if (uint32_t(std::strtoul(v, &end, 16)) == address)
                        return true;
                    v = end && *end == ',' ? end + 1 : nullptr;
                }
                return false;
            };
            for (uint32_t spec : d.gates)
                if (spec && n < kGateSlots && !noFix() && !skipped(spec & ~7u))
                {
                    addresses[n] = spec & ~7u;
                    fresh[n] = (spec & 2u) != 0;
                    pads[n] = (spec & 4u) != 0;
                    updates[n++] = (spec & 1u) != 0;
                }
            if (const char *env = std::getenv("GA2_FPS_GATES"))
                for (const char *v = env; v && *v && n < kGateSlots;)
                {
                    char *end = nullptr;
                    const uint32_t address = uint32_t(std::strtoul(v, &end, 16));
                    if (end == v)
                        break;
                    addresses[n] = address;
                    taps[n] = end && *end == ':' && end[1] == 't';
                    updates[n++] = end && *end == ':' && end[1] == 'u';
                    v = std::strchr(end, ',');
                    if (v)
                        ++v;
                }
            bindGates<I>(runtime, addresses, updates, taps, fresh, pads, std::make_integer_sequence<int, kGateSlots>{});
        }
        if (rate)
        {
            hook(d.waitVSync, g_original<I, WaitVSync>, waitVSync<I>);
            hook(d.setMax, g_original<I, SetMax>, setMax<I>);
            hook(d.setMin, g_original<I, SetMin>, setMin<I>);
        }
        std::cerr << "[ga2 fps] " << d.disc << ": " << (rate ? "frame rate hooks bound" : "trace hooks bound")
                  << (trace ? ", GA2_FRAMETRACE on" : "") << '\n';
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 framerate zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 framerate mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 framerate mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 framerate eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 framerate eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
