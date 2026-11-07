// Display features that change what the games render (all five discs share
// the engine; addresses from docs/rendering/horplus-and-framerate.md).
//
// Hor+ widescreen (GA2_WIDESCREEN_3D=1, GA2_ASPECT 16:10, 16:9, 21:9 or fit; both can be changed in the in-game
// settings menu, ga2_settings, and take effect at the next game iteration, the cameras already built included): every
// camera reads the display's pixel aspect PAR when it is constructed; PAR
// scaled by k widens the horizontal view k times (vertical view unchanged,
// the game's frustum culling widens with it). The 640-wide frame then holds
// a k-times wider view (anamorphic), which the presentation stretches back.
// 2D drawn over 3D through the Axl 2D API's screen-position setters is
// squeezed by 1/k so it keeps its shape (text included): around x = 320,
// or, for the 2D layer's items on the left or right of the 4:3 screen (the
// radar, the communication window, the unit panel), toward that edge of the
// wide frame. Primitives spanning the whole width (fades, backdrops) are
// left alone, and full-screen passes do not use these setters. The direct
// sprite function (a GIF packet built without the 2D API: the gems around a
// speaker's name) and orthographic models (radar blips) are squeezed the same
// way (docs/rendering/horplus-2d.md).
#include "game_overrides.h"
#include "ga2_hud.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "runtime/ga2_activity.h"
#include "runtime/ga2_display.h"
#include "runtime/ga2_settings.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace
{
    struct DiscFeatures
    {
        const char *disc;
        const char *elf;
        uint32_t entry, crc32;
        uint32_t cameraCtor, frameworkCtor; // read PAR
        uint32_t par;                       // display info + 0x30 (0.94)
        // Axl 2D screen-position setters: sprite rect int/float, flat rect
        // int/float, flat / gouraud / textured quads.
        uint32_t spriteInt, spriteFloat, flatInt, flatFloat, quadFlat, quadGouraud, quadTextured;
        uint32_t itemDraw;  // 2D layer item draw (item, list): sprite items and item callbacks
        uint32_t orthoDraw; // orthographic VU1 model in 2D space (record: x +0x14, sx +0x38)
        // Direct textured sprite (x0 f12, y0 f13, x1 f14, y1 f15, u0..v1 f16..f19, a0 flag):
        // a GIF packet built without the Axl 2D API (the gems around a speaker's name tag).
        uint32_t directSprite;
        uint32_t update;    // main-loop Update (the factor is taken once per iteration)
        uint32_t applyProj; // CCamera::ApplyProjection (reads ax at camera + 0xc0 every time a camera is applied)
        uint32_t sys;       // the sys singleton pointer (+4 app, app +0 scene manager, manager +8 scene id)
    };

    constexpr DiscFeatures kDiscs[] = {
        {"zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, 0x118b58u, 0x113490u, 0x7f96c0u,
         0x2332a0u, 0x233308u, 0x2333e0u, 0x233448u, 0x2329c8u, 0x232c18u, 0x232e38u, 0x11ce18u, 0x11c0e0u, 0x10bb28u, 0x113210u, 0x118e78u, 0x2d3be4u},
        {"mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, 0x118cb0u, 0x113540u, 0x517200u,
         0x24cd00u, 0x24cd68u, 0x24ce40u, 0x24cea8u, 0x24c428u, 0x24c678u, 0x24c898u, 0x11d030u, 0x11c2f8u, 0x10bb90u, 0x1132c0u, 0x118fd0u, 0x2f01e4u},
        {"mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, 0x118bd8u, 0x113510u, 0x7fb340u,
         0x234d50u, 0x234db8u, 0x234e90u, 0x234ef8u, 0x234478u, 0x2346c8u, 0x2348e8u, 0x11ce98u, 0x11c160u, 0x10bba8u, 0x113290u, 0x118ef8u, 0x2d56e4u},
        {"eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, 0x118cb0u, 0x113540u, 0x525a80u,
         0x259a00u, 0x259a68u, 0x259b40u, 0x259ba8u, 0x259128u, 0x259378u, 0x259598u, 0x11d030u, 0x11c2f8u, 0x10bb90u, 0x1132c0u, 0x118fd0u, 0x2fe2e4u},
        {"eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, 0x118cb0u, 0x113540u, 0x518680u,
         0x24dd70u, 0x24ddd8u, 0x24deb0u, 0x24df18u, 0x24d498u, 0x24d6e8u, 0x24d908u, 0x11d030u, 0x11c2f8u, 0x10bb90u, 0x1132c0u, 0x118fd0u, 0x2f12a4u},
    };

    constexpr float kNativePar = 0.94f;

    // Hor+ is available in this run: not in a VR headset (the head's field of view replaces the widened one). The
    // hooks are bound whenever it is, so the settings menu can switch it on and off while playing.
    bool horPlusPossible()
    {
        const char *vr = std::getenv("GA2_VR");
        return !(vr && std::strcmp(vr, "1") == 0);
    }

    // The player wants it now: switched on, and a play area wider than 4:3.
    bool horPlusWantedNow()
    {
        return std::string(ga2_settings::value(ga2_options::Widescreen3d)) == "1" &&
               std::string(ga2_settings::value(ga2_options::Aspect)) != "4:3";
    }

    // k = play-area width / the PS2's 4:3, from the shared play-area shape (runtime/ga2_display.h; "fit" means the
    // window's shape). The factor the game renders with is taken once per iteration (refreshK, in Update) so the
    // cameras, the squeezed 2D and the presentation (ga2_activity::horPlus) always agree within a frame.
    int sceneId(const uint8_t *rdram); // below
    std::atomic<int64_t> g_cameraVsync{-1000}; // guest vblank of the last perspective camera applied
    // The last 16 distinct vblanks a camera was applied on (game thread): a 3D scene applies one every frame, a 2D
    // scene's one-off (the title's) is not a 3D scene.
    std::array<int64_t, 16> g_cameraVsyncs = [] {
        std::array<int64_t, 16> a{};
        a.fill(-1000);
        return a;
    }();
    size_t g_cameraNext = 0;
    void noteCamera(int64_t vsync)
    {
        g_cameraVsync.store(vsync, std::memory_order_relaxed);
        if (g_cameraVsyncs[(g_cameraNext + 15) % 16] == vsync)
            return;
        g_cameraVsyncs[g_cameraNext] = vsync;
        g_cameraNext = (g_cameraNext + 1) % 16;
    }
    std::atomic<float> g_kValue{1.0f};
    bool g_everWide = false; // the projection hook leaves the cameras alone until hor+ was on once
    float k() { return g_kValue.load(std::memory_order_relaxed); }

    // Scenes shown at 4:3 even with hor+ on: Angel Select (4) is a menu over a model drawn into a picture of its
    // own, with stat polygons and a triangle drawn through cameras of its own; widened, those parted from the
    // panels they belong to, and the scene gains nothing from a wider view (docs/rendering/horplus-2d.md).
    bool narrowScene(int scene) { return scene == 4; }

    void refreshK(int scene)
    {
        const float wanted = horPlusWantedNow() && !narrowScene(scene)
                                 ? std::max(1.0f, ga2_display::wideAspect() / (4.0f / 3.0f))
                                 : 1.0f;
        if (std::fabs(wanted - g_kValue.load(std::memory_order_relaxed)) < 1e-4f)
            return;
        g_kValue.store(wanted);
        g_everWide = g_everWide || wanted > 1.0f;
        std::cerr << "[ga2 features] hor+ widescreen x" << wanted << '\n';
    }

    // The presentation (and the 2D squeeze, the pointer) show a frame wide while ga2_activity::horPlus > 1 and VU1
    // drew recently. VU1 alone is not 3D: the story scene's transition effects (the wipe into the briefing map, the
    // fade before a movie) and the title's sparkles run VU1 without a camera, and the picture went wide for about a
    // second. So the factor is published only while a perspective camera was applied on at least 10 of the last 30
    // vblanks, since the current scene began (a movie right after Angel Select must not count that scene's camera;
    // the title applies one for a single frame).
    int64_t g_sceneStartVsync = 0;

    void publishHorPlus(int scene)
    {
        static int lastScene = -2;
        const int64_t now = int64_t(ga2_activity::vsyncs.load(std::memory_order_relaxed));
        if (scene != lastScene)
        {
            lastScene = scene;
            g_sceneStartVsync = now;
        }
        int recent = 0;
        for (const int64_t v : g_cameraVsyncs)
            recent += v >= g_sceneStartVsync && now - v < 30;
        const bool camera3d = recent >= 10;
        const float shown = camera3d ? k() : 1.0f;
        if (ga2_activity::horPlus.load(std::memory_order_relaxed) != shown)
            ga2_activity::horPlus.store(shown);
    }

    // Originals of the hooked functions, per disc and per hook.
    template <size_t I, int H>
    PS2Runtime::RecompiledFunction g_original = nullptr;
    enum Hook
    {
        CameraCtor,
        FrameworkCtor,
        SpriteInt,
        SpriteFloat,
        FlatInt,
        FlatFloat,
        QuadFlat,
        QuadGouraud,
        QuadTextured,
        ItemDraw,
        OrthoDraw,
        DirectSprite,
        Update,
        ApplyProjection,
        HookCount
    };

    // Before anything reads PAR: make sure it holds the value for the factor in effect (the game's display init
    // writes the native one again whenever it runs; a change of the factor in the settings menu has to move it back).
    template <size_t I>
    void widenPar(uint8_t *rdram)
    {
        if (!g_everWide && k() <= 1.0f)
            return;
        const float par = kNativePar * k();
        std::memcpy(rdram + kDiscs[I].par, &par, sizeof(par));
    }

    // Once per game iteration, before the world is updated and rendered.
    template <size_t I>
    void update(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const int scene = sceneId(rdram);
        static const bool trace = std::getenv("GA2_WIDE_TRACE") != nullptr;
        static int lastScene = -2;
        if (trace && scene != lastScene)
        {
            std::fprintf(stderr, "[wide] scene %d at vsync %llu\n", scene, (unsigned long long)ga2_activity::vsyncs.load());
            lastScene = scene;
        }
        if (trace)
        {
            static bool lastCam = false;
            const bool cam = int64_t(ga2_activity::vsyncs.load()) - g_cameraVsync.load() < 30;
            if (cam != lastCam)
                std::fprintf(stderr, "[wide] camera %s at vsync %llu\n", cam ? "on" : "off", (unsigned long long)ga2_activity::vsyncs.load());
            lastCam = cam;
        }
        refreshK(scene);
        publishHorPlus(scene);
        g_original<I, Update>(rdram, ctx, runtime);
    }

    // Every camera that is applied takes the projection of the factor in effect: ax = 1 / PAR (the constructor's
    // value, W / W2 being 1), so cameras built before a change follow it at once.
    template <size_t I>
    void applyProjection(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        noteCamera(int64_t(ga2_activity::vsyncs.load(std::memory_order_relaxed)));
        const uint32_t camera = GPR_U32(ctx, 4);
        if ((g_everWide || k() > 1.0f) && camera >= 0x100000u && camera + 0xc4u < 0x2000000u)
        {
            const float ax = 1.0f / (kNativePar * k());
            std::memcpy(rdram + camera + 0xc0u, &ax, sizeof(ax));
        }
        g_original<I, ApplyProjection>(rdram, ctx, runtime);
    }

    template <size_t I, int H>
    void constructor(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        widenPar<I>(rdram);
        g_original<I, H>(rdram, ctx, runtime);
    }

    // 2D works in a 640-wide virtual space: x' = 320 + (x - 320) / k, unless
    // the primitive spans the whole width.
    float squeezeX(float x) { return 320.0f + (x - 320.0f) / k(); }
    bool spansWidth(float lo, float hi) { return lo <= 0.0f && hi >= 640.0f; }

    // HUD edges: a 2D layer item on the left or right of the 4:3 screen keeps
    // its distance from that edge of the wide frame (scaled by 1/k like the
    // rest, so it keeps its shape); everything else stays centred.
    enum Anchor : uint8_t
    {
        AnchorCentre,
        AnchorLeft,
        AnchorRight
    };
    Anchor g_anchor = AnchorCentre; // the item being drawn (game thread only)

    float placeXAt(Anchor anchor, float x)
    {
        switch (anchor)
        {
        case AnchorLeft:
            return x / k();
        case AnchorRight:
            return 640.0f - (640.0f - x) / k();
        default:
            return squeezeX(x);
        }
    }
    float placeX(float x) { return placeXAt(g_anchor, x); }

    // Left: reaches into the left sixth of the screen and ends before the
    // right third; right: the mirror; anything else (spanning both sides, or
    // inside the middle, like the page icon at the end of a message box) is
    // centred.
    Anchor sideOf(float x0, float x1)
    {
        constexpr float kThird = 640.0f / 3.0f, kSixth = 640.0f / 6.0f;
        if (x0 < kSixth && x1 <= 2.0f * kThird)
            return AnchorLeft;
        if (x1 > 640.0f - kSixth && x0 >= kThird)
            return AnchorRight;
        return AnchorCentre;
    }

    uint8_t *g_rdram = nullptr;

    uint32_t word(const uint8_t *rdram, uint32_t address)
    {
        uint32_t v = 0;
        std::memcpy(&v, rdram + (address & 0x1fffffcu), sizeof(v));
        return v;
    }

    // One step up the game's call stack: `ra` returns into a function whose
    // stack pointer is `sp`; find that function's prologue (the nearest
    // `addiu/daddiu sp, sp, -N` before ra) and the slot it saved its own
    // return address in (`sd/sq/sw ra, M(sp)`).
    bool callerOf(const uint8_t *rdram, uint32_t ra, uint32_t sp, uint32_t &callerRa, uint32_t &callerSp)
    {
        if (!rdram || ra < 0x100000u || ra >= 0x2000000u)
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

    // Diagnostics: GA2_HUD_LOG=<seconds> prints each 2D setter call while
    // hor+ is active (hook, caller, rectangle) from that many seconds after
    // start, for mapping UI items to screen edges.
    void hudLog(int hook, const R5900Context *ctx, float x0, float y0, float x1, float y1)
    {
        static const char *env = std::getenv("GA2_HUD_LOG");
        static const auto start = std::chrono::steady_clock::now();
        static int lines = 0;
        if (!env || lines >= 40000 ||
            std::chrono::steady_clock::now() - start < std::chrono::seconds(std::atoi(env)))
            return;
        ++lines;
        uint32_t chain[5] = {GPR_U32(ctx, 31)};
        uint32_t sp = GPR_U32(ctx, 29);
        int depth = 1;
        while (depth < 5 && callerOf(g_rdram, chain[depth - 1], sp, chain[depth], sp))
            ++depth;
        std::fprintf(stderr, "[hud] h%d ra=%06x", hook, chain[0]);
        for (int i = 1; i < depth; ++i)
            std::fprintf(stderr, "<%06x", chain[i]);
        std::fprintf(stderr, " x=%.0f..%.0f y=%.0f..%.0f\n", x0, x1, y0, y1);
    }

    float readF(const uint8_t *rdram, uint32_t address)
    {
        float v = 0.0f;
        std::memcpy(&v, rdram + (address & 0x1fffffcu), sizeof(v));
        return v;
    }

    void writeF(uint8_t *rdram, uint32_t address, float v)
    {
        std::memcpy(rdram + (address & 0x1fffffcu), &v, sizeof(v));
    }

    // 2D layer items (kind +8; x +0x3c, y +0x40, w +0x4c, h +0x50, scale
    // +0x60/+0x64, draw callback +0x78). Kind 4 is a layout (a group loaded
    // together: its record +0x28 holds the child count at +0x14, the child
    // array is at +0x2c); kind 2 is a small orthographic VU1 model placed at
    // (x, y) with scale (sx, sy), e.g. the radar blips.
    struct Rect
    {
        float x0, y0, x1, y1;
    };

    // An item's rectangle as drawn at rest: position and size times scale
    // (a scale of 0 hides it). Kind-2 models are a point.
    bool itemRect(const uint8_t *rdram, uint32_t item, Rect &r)
    {
        const float x = readF(rdram, item + 0x3c), y = readF(rdram, item + 0x40);
        const float w = readF(rdram, item + 0x4c) * std::fabs(readF(rdram, item + 0x60));
        const float h = readF(rdram, item + 0x50) * std::fabs(readF(rdram, item + 0x64));
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(w) || !std::isfinite(h))
            return false;
        if (word(rdram, item + 8) == 2u)
        {
            // A model at the origin has no place of its own to judge.
            if (x == 0.0f && y == 0.0f)
                return false;
            r = {x, y, x, y};
            return true;
        }
        if (!(w > 0.0f) || !(h > 0.0f))
            return false;
        r = {x, y, x + w, y + h};
        return true;
    }

    constexpr int kSpread = -1;
    struct LayoutSide
    {
        int side; // Anchor, or kSpread
        Rect bounds;
    };
    std::unordered_map<uint32_t, LayoutSide> g_layoutSide; // item -> its layout
    std::unordered_set<uint32_t> g_centreItems;             // 2D layer items that always stay centred (ga2_hud::keepCentred)

    // Large centred items (the cockpit reticle, pop-up panels), seen
    // recently: whatever lies inside one's inscribed ellipse (radius +5%,
    // for the tutorial's highlight around a gauge) belongs to it and stays
    // centred, even in a layout of its own (the reticle's gauges).
    struct Frame
    {
        Rect r;
        int64_t seen;
    };
    std::unordered_map<uint32_t, Frame> g_frames;

    void noteFrame(uint32_t item, const Rect &r)
    {
        if (r.x1 - r.x0 >= 240.0f && r.y1 - r.y0 >= 120.0f && !spansWidth(r.x0, r.x1) &&
            sideOf(r.x0, r.x1) == AnchorCentre)
            g_frames[item] = {r, ga2_activity::nowNanos()};
    }

    bool insideFrame(const Rect &r)
    {
        const int64_t now = ga2_activity::nowNanos();
        for (auto it = g_frames.begin(); it != g_frames.end();)
        {
            if (now - it->second.seen > 250'000'000)
            {
                it = g_frames.erase(it);
                continue;
            }
            const Rect &f = it->second.r;
            const float cx = 0.5f * (f.x0 + f.x1), cy = 0.5f * (f.y0 + f.y1);
            const float rx = 0.5f * (f.x1 - f.x0), ry = 0.5f * (f.y1 - f.y0);
            bool inside = true;
            for (const float x : {r.x0, r.x1})
                for (const float y : {r.y0, r.y1})
                    inside = inside && (x - cx) * (x - cx) / (rx * rx) + (y - cy) * (y - cy) / (ry * ry) <= 1.1f;
            if (inside)
                return true;
            ++it;
        }
        return false;
    }

    void rememberLayout(const uint8_t *rdram, uint32_t layout)
    {
        const uint32_t record = word(rdram, layout + 0x28), children = word(rdram, layout + 0x2c);
        const uint32_t count = record ? word(rdram, record + 0x14) : 0u;
        if (count == 0 || count > 256 || children < 0x100000u || children >= 0x2000000u)
            return;
        std::vector<uint32_t> items;
        std::vector<Rect> rects;
        for (uint32_t i = 0; i < count; ++i)
        {
            const uint32_t child = word(rdram, children + 4u * i);
            Rect r;
            if (child < 0x100000u || child >= 0x2000000u || word(rdram, child + 8) == 4u ||
                !itemRect(rdram, child, r))
                continue;
            items.push_back(child);
            rects.push_back(r);
        }
        if (items.empty())
            return;
        // A layout on one side moves as a whole; one gathered around the
        // middle (a pop-up panel) stays centred as a whole; one spread
        // across the screen (the cockpit, corner counters loaded together)
        // is split into groups of touching items, each placed the same way.
        constexpr float kSixth = 640.0f / 6.0f;
        auto sideOfGroup = [&](const Rect &u) {
            int side = spansWidth(u.x0, u.x1) ? int(kSpread) : int(sideOf(u.x0, u.x1));
            if (side == AnchorCentre && (u.x0 < kSixth || u.x1 > 640.0f - kSixth))
                side = kSpread;
            return side;
        };
        auto unite = [](const Rect &a, const Rect &b) {
            return Rect{std::min(a.x0, b.x0), std::min(a.y0, b.y0), std::max(a.x1, b.x1), std::max(a.y1, b.y1)};
        };
        Rect u = rects[0];
        for (const Rect &r : rects)
            u = unite(u, r);
        int side = sideOfGroup(u);
        if (side != kSpread)
        {
            for (const uint32_t child : items)
                g_layoutSide[child] = {side, u};
        }
        else
        {
            // Groups: union of overlapping rectangles, leaving out
            // screen-sized backdrops (the reticle circle) that touch all.
            const size_t n = items.size();
            std::vector<size_t> group(n);
            for (size_t i = 0; i < n; ++i)
                group[i] = i;
            auto root = [&](size_t i) {
                while (group[i] != i)
                    i = group[i] = group[group[i]];
                return i;
            };
            auto backdrop = [](const Rect &r) { return r.y1 - r.y0 >= 240.0f || r.x1 - r.x0 >= 480.0f; };
            for (size_t i = 0; i < n; ++i)
                for (size_t j = i + 1; j < n; ++j)
                    if (!backdrop(rects[i]) && !backdrop(rects[j]) && rects[i].x0 <= rects[j].x1 &&
                        rects[j].x0 <= rects[i].x1 && rects[i].y0 <= rects[j].y1 && rects[j].y0 <= rects[i].y1)
                        group[root(i)] = root(j);
            std::unordered_map<size_t, Rect> bounds;
            for (size_t i = 0; i < n; ++i)
            {
                const auto it = bounds.find(root(i));
                bounds[root(i)] = it == bounds.end() ? rects[i] : unite(it->second, rects[i]);
            }
            for (size_t i = 0; i < n; ++i)
            {
                const Rect &b = bounds[root(i)];
                g_layoutSide[items[i]] = {backdrop(rects[i]) ? int(kSpread) : sideOfGroup(b), b};
            }
        }
        if (std::getenv("GA2_HUD_LOG"))
        {
            static std::unordered_map<uint32_t, std::string> last;
            char line[128];
            std::snprintf(line, sizeof(line), "p=%06x children=%u x=%.1f..%.1f y=%.1f..%.1f side=%d", layout, count,
                          u.x0, u.x1, u.y0, u.y1, side);
            if (last[layout] != line)
            {
                last[layout] = line;
                std::fprintf(stderr, "[layout] %s\n", line);
            }
        }
    }

    // The side an item is anchored to: centred inside a frame's ellipse;
    // else its layout's side unless the layout is spread across the
    // screen; else its own rectangle's. Callback items without a rectangle
    // draw wherever their callback decides (markers on 3D positions) and
    // stay centred.
    // The scene manager's scene id: 2 story (and the ship map), 3 battle, 4 Angel Select, 13 the Mugen/Eigou
    // tutorial battle, ... (docs/cheats/battle.md); -1 when the pointers are not set up.
    uint32_t g_sys = 0;
    int sceneId(const uint8_t *rdram)
    {
        auto valid = [](uint32_t a) { return a >= 0x100000u && a < 0x2000000u; };
        const uint32_t sys = g_sys ? word(rdram, g_sys) : 0u;
        const uint32_t app = valid(sys) ? word(rdram, sys + 4) : 0u;
        const uint32_t manager = valid(app) ? word(rdram, app) : 0u;
        return valid(manager) ? int(word(rdram, manager + 8)) : -1;
    }

    // The edge rules are for HUDs over a wide 3D view: the battle (and the tutorial battle) and the ship map in the
    // story scene. Full-screen menus over a 3D model (Angel Select's radar and name plate on the left, the rings
    // around the frame on the right) stay one 4:3 picture in the middle.
    bool edgeScene(const uint8_t *rdram)
    {
        const int id = sceneId(rdram);
        return id == 2 || id == 3 || id == 13;
    }

    Anchor itemSide(const uint8_t *rdram, uint32_t item)
    {
        if (!edgeScene(rdram))
            return AnchorCentre;
        if (!g_centreItems.empty() && g_centreItems.count(item))
            return AnchorCentre;
        Rect own;
        const bool hasRect = itemRect(rdram, item, own);
        if (hasRect)
            noteFrame(item, own);
        const auto layout = g_layoutSide.find(item);
        if (layout != g_layoutSide.end() && layout->second.side != kSpread)
            return insideFrame(layout->second.bounds) ? AnchorCentre : Anchor(layout->second.side);
        if (!hasRect || spansWidth(own.x0, own.x1) || insideFrame(own))
            return AnchorCentre;
        return sideOf(own.x0, own.x1);
    }

    template <size_t I>
    void itemDraw(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        g_rdram = rdram;
        if (!ga2_activity::wideFrame())
        {
            g_original<I, ItemDraw>(rdram, ctx, runtime);
            return;
        }
        const uint32_t item = GPR_U32(ctx, 4);
        const uint32_t kind = word(rdram, item + 8);
        if (kind == 4u)
        {
            rememberLayout(rdram, item);
            g_original<I, ItemDraw>(rdram, ctx, runtime);
            return;
        }
        const Anchor outer = g_anchor;
        g_anchor = itemSide(rdram, item);
        if (std::getenv("GA2_HUD_LOG"))
        {
            static const auto start = std::chrono::steady_clock::now();
            static std::unordered_map<uint32_t, std::string> last; // print changes only
            char line[160];
            std::snprintf(line, sizeof(line), "p=%06x kind=%u x=%.1f y=%.1f w=%.1f h=%.1f cb=%06x side=%d", item, kind,
                          readF(rdram, item + 0x3c), readF(rdram, item + 0x40), readF(rdram, item + 0x4c),
                          readF(rdram, item + 0x50), word(rdram, item + 0x78), int(g_anchor));
            if (std::chrono::steady_clock::now() - start > std::chrono::seconds(std::atoi(std::getenv("GA2_HUD_LOG"))) &&
                last[item] != line)
            {
                last[item] = line;
                std::fprintf(stderr, "[item] %s\n", line);
            }
        }
        g_original<I, ItemDraw>(rdram, ctx, runtime);
        g_anchor = outer;
    }

    // Orthographic VU1 models drawn in 2D space (the radar blips, kind-2 layer
    // items) never reach the screen setters: their x and x scale are placed
    // for the draw (by the item's anchor inside an item draw, centred
    // otherwise) and restored.
    template <size_t I>
    void orthoDraw(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (!ga2_activity::wideFrame())
        {
            g_original<I, OrthoDraw>(rdram, ctx, runtime);
            return;
        }
        const uint32_t record = GPR_U32(ctx, 4);
        const float x = readF(rdram, record + 0x14), sx = readF(rdram, record + 0x38);
        writeF(rdram, record + 0x14, placeX(x));
        writeF(rdram, record + 0x38, sx / k());
        g_original<I, OrthoDraw>(rdram, ctx, runtime);
        writeF(rdram, record + 0x14, x);
        writeF(rdram, record + 0x38, sx);
    }

    // Rects: x0 / x1 in a0 / a2 (int16) or f12 / f14.
    template <size_t I, int H>
    void rectInt(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        g_rdram = rdram;
        const float x0 = float(int16_t(GPR_S32(ctx, 4))), x1 = float(int16_t(GPR_S32(ctx, 6)));
        if (ga2_activity::wideFrame())
            hudLog(H, ctx, x0, float(int16_t(GPR_S32(ctx, 5))), x1, float(int16_t(GPR_S32(ctx, 7))));
        if (ga2_activity::wideFrame() && !spansWidth(std::min(x0, x1), std::max(x0, x1)))
        {
            SET_GPR_S32(ctx, 4, int32_t(std::lround(placeX(x0))));
            SET_GPR_S32(ctx, 6, int32_t(std::lround(placeX(x1))));
        }
        g_original<I, H>(rdram, ctx, runtime);
    }

    template <size_t I, int H>
    void rectFloat(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        g_rdram = rdram;
        if (ga2_activity::wideFrame())
            hudLog(H, ctx, ctx->f[12], ctx->f[13], ctx->f[14], ctx->f[15]);
        if (ga2_activity::wideFrame() &&
            !spansWidth(std::min(ctx->f[12], ctx->f[14]), std::max(ctx->f[12], ctx->f[14])))
        {
            ctx->f[12] = placeX(ctx->f[12]);
            ctx->f[14] = placeX(ctx->f[14]);
        }
        g_original<I, H>(rdram, ctx, runtime);
    }

    // Quads: corners x0..x3 in f12, f14, f16, f18.
    template <size_t I, int H>
    void quad(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        g_rdram = rdram;
        if (ga2_activity::wideFrame())
        {
            const float xs[4] = {ctx->f[12], ctx->f[14], ctx->f[16], ctx->f[18]};
            hudLog(H, ctx, *std::min_element(xs, xs + 4), ctx->f[13], *std::max_element(xs, xs + 4), ctx->f[19]);
            if (!spansWidth(*std::min_element(xs, xs + 4), *std::max_element(xs, xs + 4)))
                for (int i = 12; i <= 18; i += 2)
                    ctx->f[i] = placeX(ctx->f[i]);
        }
        g_original<I, H>(rdram, ctx, runtime);
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        if (!horPlusPossible())
            return;
        const DiscFeatures &d = kDiscs[I];
        g_sys = d.sys;
        auto hook = [&](uint32_t address, PS2Runtime::RecompiledFunction &original, PS2Runtime::RecompiledFunction replacement) {
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 features] " << d.disc << ": 0x" << std::hex << address << std::dec
                          << " not in the generated table; hor+ off\n";
                return false;
            }
            original = runtime.lookupFunction(address);
            runtime.replaceFunction(address, replacement);
            return true;
        };
        if (!hook(d.cameraCtor, g_original<I, CameraCtor>, constructor<I, CameraCtor>) ||
            !hook(d.frameworkCtor, g_original<I, FrameworkCtor>, constructor<I, FrameworkCtor>) ||
            !hook(d.spriteInt, g_original<I, SpriteInt>, rectInt<I, SpriteInt>) ||
            !hook(d.spriteFloat, g_original<I, SpriteFloat>, rectFloat<I, SpriteFloat>) ||
            !hook(d.flatInt, g_original<I, FlatInt>, rectInt<I, FlatInt>) ||
            !hook(d.flatFloat, g_original<I, FlatFloat>, rectFloat<I, FlatFloat>) ||
            !hook(d.quadFlat, g_original<I, QuadFlat>, quad<I, QuadFlat>) ||
            !hook(d.quadGouraud, g_original<I, QuadGouraud>, quad<I, QuadGouraud>) ||
            !hook(d.quadTextured, g_original<I, QuadTextured>, quad<I, QuadTextured>) ||
            !hook(d.itemDraw, g_original<I, ItemDraw>, itemDraw<I>) ||
            !hook(d.orthoDraw, g_original<I, OrthoDraw>, orthoDraw<I>) ||
            !hook(d.directSprite, g_original<I, DirectSprite>, rectFloat<I, DirectSprite>) ||
            !hook(d.update, g_original<I, Update>, update<I>) ||
            !hook(d.applyProj, g_original<I, ApplyProjection>, applyProjection<I>))
            return;
        std::cerr << "[ga2 features] " << d.disc << ": hor+ hooks bound\n";
    }
}

// Where the 2D layer draws an item centred at frame x (see ga2_hud.h): the
// anchor the item draw hook would give a lone item of that width.
float ga2_hud::markerX(float x, float halfWidth)
{
    if (!ga2_activity::wideFrame())
        return x;
    return placeXAt(sideOf(x - halfWidth, x + halfWidth), x);
}

void ga2_hud::keepCentred(uint32_t item)
{
    g_centreItems.insert(item);
}

PS2_REGISTER_GAME_OVERRIDE("GA2 features zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 features mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 features mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 features eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 features eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
