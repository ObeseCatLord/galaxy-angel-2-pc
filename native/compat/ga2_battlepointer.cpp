// Pointer controls for the battle's tactical map (docs/PLAYING.md, analysis in
// research/battle-re/report.md): the map's cursor is the screen centre, and
// the game's own logic turns what is under it into selecting, moving and
// attacking. With a mouse (or a VR hand) the same logic runs on what is under
// the pointer instead:
//
//  - the two hover functions (hovered unit, target unit) pick the unit whose
//    projected icon is nearest the pointer (48 px, as for the centre), or, for
//    a VR hand, the unit nearest its aim ray;
//  - the three callees that read the camera's look-at point as the move
//    destination (ground check, move all, move one) get the map point under
//    the pointer instead, only for calls from the map's own update (stage
//    scripts call move-one too).
//
// With a mouse the map's cursor ring also follows the pointer. Outside the
// tactical map, with the pointer off the picture or untouched (keyboard and
// pad play), everything goes to the originals. All five discs share the
// engine; three layouts of the unit and manager fields.
//
// Diagnostics: GA2_BATTLEPTR_DEBUG=1 (2: also each unit's icon) logs once a
// second what the pick and the projection see and every intercepted order
// call; GA2_BATTLEPTR_SELFTEST=1 checks the mouse and hand maths on synthetic
// cameras at start; GA2_BATTLEPTR_MAP_AT=<seconds> asks for the tactical map
// that long after the start (the keyboard has no L3).
#include "game_overrides.h"
#include "ps2_mouse.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ga2_hud.h"
#include "ga2_vrview.h"
#include "runtime/ga2_activity.h"
#include "runtime/ga2_stereo.h"
#include "runtime/ga2_vrpointer.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

namespace
{
    // ---- per-disc data -----------------------------------------------------
    struct Disc
    {
        const char *disc, *elf;
        uint32_t entry, crc;
        uint32_t mgrGlobal;              // address of the battle manager pointer
        uint32_t units;                  // mgr + units + 4 * slot
        uint32_t countField;             // 0: slots 0..0x4f; else slots 0..*(mgr + field) inclusive
        uint32_t pos, alive, hp, sx;     // unit fields: world x/y/z (millimetres), alive, HP, icon x/y
        uint32_t matrix;                 // mgr + matrix: the map view-projection (unit icons use it)
        uint32_t mapUpdate, mapLeave;    // map view update, leave (the update body lies between)
        uint32_t hover, targetHover;     // (a0 = map view) -> map view + 0x28 / + 0x2c
        uint32_t ground, moveAll, moveOne;
        uint32_t setPosition;              // (f12 x, f13 y, a0 = layout holder): places a 2D layer object
        uint32_t mgrUpdate, requestedMode; // test aid: GA2_BATTLEPTR_MAP_AT (view mode request field)
        uint32_t padCtl;                   // global: the pad controller (port 0 pad at *(ctl + 0x24), its button map at pad + 0x48)
        uint32_t mapCamera;                // the map camera's update (f12 dt, a0 camera: position x, y, z at +0, +4, +8)
        uint32_t reticle, reticleX;        // the aim update of the cockpit views (f12 dt, a0 aim object; a dispatcher on the view mode,
                                           // reached through a pointer: the mode's own update is tail-jumped to, which bypasses a hook) and
                                           // the object's reticle top-left x field (y follows)
        uint32_t unitCamera;               // the unit camera's update (f12 dt, a0 camera: pitch +0x160, yaw +0x164, distance +0x170)
        uint32_t focus;                    // focus the map camera on a unit (f12 time, a0 map view, a1 unit)
        bool shifted;                      // Mugen / Eigou: logical button ids above 24 are one higher
        uint32_t flags;                    // mgr + flags: bit 2 = system menu open, bit 4 = paused
        uint32_t rear;                     // the rear (chase) view's update
        uint32_t portraitMenu;             // mgr + portraitMenu: the angel list (open while L2 is held: flag bit 0 at +0xa8, cursor at +0xa4)
        uint32_t gates;                    // mgr + gates + 4 * logical button id: the tutorial's gate (0: the button is off)
    };
    constexpr Disc kDiscs[] = {
        {"zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, 0x2cca6c, 0x174, 0, 0x180, 0x364, 0x1d0, 0x1a0, 0x4c0,
         0x1a0c30, 0x1a2138, 0x1a1da8, 0x1a1e90, 0x165410, 0x165310, 0x1816e8, 0x1ca510, 0x1650b0, 0x44c, 0x2d3be4, 0x1a2428, 0x1a9898, 0x140, 0x1aac60, 0x1a2158, false, 0x700, 0x1a48c8, 0x530, 0x71c},
        {"mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, 0x2e8fbc, 0x178, 0x2b8, 0x190, 0x3bc, 0x204, 0x1b0, 0x4d0,
         0x1ae8e0, 0x1afbc0, 0x1af678, 0x1af770, 0x16f6b8, 0x16f5a8, 0x18cc48, 0x163458, 0x16f340, 0x454, 0x2f01e4, 0x1afec0, 0x1b8740, 0x110, 0x1b8e30, 0x1afbe0, true, 0xba4, 0x1b2188, 0x540, 0xbc0},
        {"mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, 0x2ce58c, 0x174, 0, 0x180, 0x364, 0x1d0, 0x1a0, 0x4c0,
         0x1a26d0, 0x1a3bd8, 0x1a3848, 0x1a3930, 0x166eb0, 0x166db0, 0x183188, 0x1cbfb0, 0x166b50, 0x44c, 0x2d56e4, 0x1a3ec8, 0x1ab338, 0x140, 0x1ac700, 0x1a3bf8, false, 0x700, 0x1a6368, 0x530, 0x71c},
        {"eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, 0x2f7070, 0x178, 0x308, 0x190, 0x3ec, 0x208, 0x1b0, 0x570,
         0x1b4370, 0x1b5688, 0x1b5140, 0x1b5238, 0x173380, 0x173270, 0x192238, 0x1654a0, 0x172ff8, 0x4f4, 0x2fe2e4, 0x1b5988, 0x1be1a8, 0x110, 0x1be898, 0x1b56a8, true, 0xd90, 0x1b7d00, 0x5e0, 0xdac},
        {"eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, 0x2ea06c, 0x178, 0x2b8, 0x190, 0x3bc, 0x204, 0x1b0, 0x4d0,
         0x1af468, 0x1b0758, 0x1b0210, 0x1b0308, 0x1701d8, 0x1700c8, 0x18d768, 0x163ed0, 0x16fe60, 0x454, 0x2f12a4, 0x1b0a58, 0x1b92d8, 0x110, 0x1b99c8, 0x1b0778, true, 0xba4, 0x1b2d20, 0x540, 0xbc0},
    };

    enum Hook
    {
        Hover,
        TargetHover,
        Ground,
        MoveAll,
        MoveOne,
        MgrUpdate,
        SetPosition,
        MapUpdate,
        MapCamera,
        Reticle,
        UnitCamera,
        Focus,
        Rear,
    };
    template <size_t I, int H>
    PS2Runtime::RecompiledFunction g_original = nullptr;

    uint32_t word(const uint8_t *ram, uint32_t a)
    {
        uint32_t v = 0;
        std::memcpy(&v, ram + (a & 0x1FFFFFFu), sizeof(v));
        return v;
    }
    float flt(const uint8_t *ram, uint32_t a)
    {
        float v = 0;
        std::memcpy(&v, ram + (a & 0x1FFFFFFu), sizeof(v));
        return v;
    }

    // ---- math (row-vector 4x4, row-major: v' = v M) ------------------------
    // The game's GS-pixel screen position of a map point (X, 0, Z) is the
    // projector's: c = (X, 0, Z, 1) M, screen = c.xy / c.w - 2048 + (320, 240).
    constexpr float kOriginX = 2048.0f - 320.0f, kOriginY = 2048.0f - 240.0f;

    bool projectGround(const float M[16], float X, float Z, float &sx, float &sy)
    {
        const float cx = X * M[0] + Z * M[8] + M[12], cy = X * M[1] + Z * M[9] + M[13];
        const float cw = X * M[3] + Z * M[11] + M[15];
        if (!(cw > 1e-6f))
            return false;
        sx = cx / cw - kOriginX;
        sy = cy / cw - kOriginY;
        return true;
    }

    // The map point (y = 0) that projects to (sx, sy): two linear equations,
    // c.x - u c.w = 0 and c.y - v c.w = 0, in (X, Z); in front of the camera.
    bool unprojectGround(const float M[16], float sx, float sy, float &X, float &Z)
    {
        const float u = sx + kOriginX, v = sy + kOriginY;
        const float a1 = M[0] - u * M[3], b1 = M[8] - u * M[11], c1 = M[12] - u * M[15];
        const float a2 = M[1] - v * M[3], b2 = M[9] - v * M[11], c2 = M[13] - v * M[15];
        const float det = a1 * b2 - a2 * b1;
        if (std::fabs(det) < 1e-12f)
            return false;
        X = (-c1 * b2 + c2 * b1) / det;
        Z = (-a1 * c2 + a2 * c1) / det;
        return X * M[3] + Z * M[11] + M[15] > 1e-6f && std::isfinite(X) && std::isfinite(Z);
    }

    bool invert(const float m[16], float out[16])
    {
        float inv[16];
        inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
        inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
        inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
        inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
        inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
        inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
        inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
        inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
        inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
        inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
        inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
        inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
        inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
        inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
        inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
        inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];
        const float det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
        if (std::fabs(det) < 1e-20f)
            return false;
        for (int i = 0; i < 16; ++i)
            out[i] = inv[i] / det;
        return true;
    }

    // A VR hand's ray in the game's world (map units). The hand's play-space
    // frame (metres, OpenXR axes: x right, y up, -z forward) is the game
    // camera's frame scaled by units/metre and with y and z flipped
    // (ga2_vrstereo.cpp's eye transform, C S^-1): c = (u x, -u y, -u z), then
    // the inverse of the game's own view matrix V takes it to the world.
    struct WorldRay
    {
        float o[3], d[3];
    };
    bool worldRay(const float V[16], float unitsPerMetre, const float origin[3], const float direction[3], WorldRay &ray)
    {
        float inv[16];
        if (!invert(V, inv))
            return false;
        const float u = unitsPerMetre;
        const float co[4] = {u * origin[0], -u * origin[1], -u * origin[2], 1.0f};
        const float cd[4] = {u * direction[0], -u * direction[1], -u * direction[2], 0.0f};
        for (int j = 0; j < 3; ++j)
        {
            ray.o[j] = co[0] * inv[j] + co[1] * inv[4 + j] + co[2] * inv[8 + j] + co[3] * inv[12 + j];
            ray.d[j] = cd[0] * inv[j] + cd[1] * inv[4 + j] + cd[2] * inv[8 + j];
        }
        return true;
    }

    bool rayGround(const WorldRay &r, float &X, float &Z)
    {
        if (std::fabs(r.d[1]) < 1e-9f)
            return false;
        const float t = -r.o[1] / r.d[1];
        if (!(t > 0.0f))
            return false;
        X = r.o[0] + t * r.d[0];
        Z = r.o[2] + t * r.d[2];
        return std::isfinite(X) && std::isfinite(Z);
    }

    // Angle (radians) between the ray and the direction to a point.
    float rayAngle(const WorldRay &r, float px, float py, float pz)
    {
        const float to[3] = {px - r.o[0], py - r.o[1], pz - r.o[2]};
        const float dl = std::sqrt(r.d[0] * r.d[0] + r.d[1] * r.d[1] + r.d[2] * r.d[2]);
        const float tl = std::sqrt(to[0] * to[0] + to[1] * to[1] + to[2] * to[2]);
        if (dl < 1e-12f || tl < 1e-12f)
            return 3.0f;
        const float along = (r.d[0] * to[0] + r.d[1] * to[1] + r.d[2] * to[2]) / (dl * tl);
        if (along <= 0.0f)
            return 3.0f; // behind the hand
        return std::acos(std::min(1.0f, along));
    }

    // ---- what is pointed at this update --------------------------------------
    // Computed by the hover hooks and used by the destination hooks of the
    // same map update.
    enum class Source
    {
        None,
        Mouse,
        Hand
    };
    struct Pick
    {
        Source source = Source::None;
        bool ground = false; // the pointer is over a map point
        float X = 0.0f, Z = 0.0f;
        float gx = 0.0f, gy = 0.0f; // the mouse pointer in the 2D layer's 640 x 480 space
        uint32_t unit = 0;          // the unit under the pointer (0: none)
    } g_pick;

    const bool g_debug = [] {
        const char *v = std::getenv("GA2_BATTLEPTR_DEBUG");
        return v && *v && std::strcmp(v, "0") != 0;
    }();

    int64_t nowNanos() { return ga2_activity::nowNanos(); }
    // Seconds since the program started (diagnostic log lines, comparable with
    // the run harness's frame times).
    double sinceStart()
    {
        static const int64_t start = nowNanos();
        return double(nowNanos() - start) * 1e-9;
    }
    const double g_startedAt = sinceStart();

    constexpr float kIconHalfWidth = 24.0f; // the unit icon items are 48 wide

    // Mouse: over the picture and moved (or clicking) within the last seconds;
    // a pointer parked since keyboard or pad play began does not take over.
    constexpr int64_t kMouseIdle = 6'000'000'000;
    bool mouseGame(float &gx, float &gy)
    {
        static float lastX = -1e9f, lastY = -1e9f;
        static int64_t lastActive = 0;
        float px, py;
        if (!ps2_mouse::pointer(px, py))
        {
            lastX = lastY = -1e9f;
            return false;
        }
        const int64_t now = nowNanos();
        if (lastX > -1e8f && std::fabs(px - lastX) + std::fabs(py - lastY) > 0.5f)
            lastActive = now;
        lastX = px;
        lastY = py;
        if (ps2_mouse::heldButtons()) // a button held by the mouse (a click, an edge scroll)
            lastActive = now;
        if (now - lastActive > kMouseIdle)
            return false;
        // The pointer is in the 2D layer's 640 x 480 space (ps2_mouse already
        // undid the hor+ squeeze); the PS2 frame is 448 high for that.
        gx = px;
        gy = py * (480.0f / 448.0f);
        return true;
    }

    // A VR hand over the stereo battle scene: the aim ray in game units.
    bool handRay(WorldRay &ray)
    {
        const ga2_vrpointer::Aim aim = ga2_vrpointer::aim();
        if (!aim.valid)
            return false;
        const ga2_stereo::Setup setup = ga2_stereo::building();
        if (!setup.active)
            return false;
        float view[16];
        uint64_t serial = 0;
        if (!ga2_vrview::latest(view, serial))
            return false;
        // Map units: the map plane is y = 0 in the same units as the camera
        // look-at point and the projector's x 0.001 (units are millimetres).
        return worldRay(view, setup.unitsPerMetre, aim.origin, aim.direction, ray);
    }

    template <size_t I>
    uint32_t unitAt(const uint8_t *ram, uint32_t mgr, uint32_t slot)
    {
        return word(ram, mgr + kDiscs[I].units + 4u * slot);
    }

    template <size_t I>
    bool usable(const uint8_t *ram, uint32_t unit)
    {
        return unit && word(ram, unit + kDiscs[I].alive) != 0 && int32_t(word(ram, unit + kDiscs[I].hp)) > 0;
    }

    // GA2_BATTLEPTR_SELFTEST=1: the desktop and hand paths on synthetic
    // cameras. A map point under a pixel (unprojected through the view-
    // projection the icon projector uses) must be what a hand ray aimed at the
    // same point finds through the captured view and the play-space
    // conversion, from any hand position.
    void selfTest()
    {
        auto camera = [](float cx, float cy, float cz, float yaw, float pitch, float V[16]) {
            // camera axes in the world: right, down, forward (y of the world is up)
            const float cp = std::cos(pitch), sp = std::sin(pitch), cyw = std::cos(yaw), syw = std::sin(yaw);
            const float f[3] = {syw * cp, -sp, cyw * cp};
            const float r[3] = {cyw, 0.0f, -syw};
            const float d[3] = {r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]}; // r x f
            const float *axes[3] = {r, d, f};
            std::memset(V, 0, 16 * sizeof(float));
            for (int j = 0; j < 3; ++j)
            {
                for (int i = 0; i < 3; ++i)
                    V[i * 4 + j] = axes[j][i];
                V[12 + j] = -(cx * axes[j][0] + cy * axes[j][1] + cz * axes[j][2]);
            }
            V[15] = 1.0f;
        };
        auto mul = [](const float a[16], const float b[16], float out[16]) {
            float r[16];
            for (int i = 0; i < 4; ++i)
                for (int j = 0; j < 4; ++j)
                    r[i * 4 + j] = a[i * 4 + 0] * b[0 * 4 + j] + a[i * 4 + 1] * b[1 * 4 + j] + a[i * 4 + 2] * b[2 * 4 + j] +
                                   a[i * 4 + 3] * b[3 * 4 + j];
            std::memcpy(out, r, sizeof(r));
        };
        // GS-style projection: x = fx X/Z + 2048, y = fy Y/Z + 2048 (Y down), w = Z.
        const float P[16] = {600, 0, 0, 0, 0, 560, 0, 0, 2048, 2048, 1, 1, 0, 0, 0, 0};
        float worst = 0.0f, worstAngle = 0.0f;
        int cases = 0, bad = 0;
        const float cams[][5] = {{0, 14, -9, 0.0f, 0.95f}, {3, 20, 4, 2.4f, 1.1f}, {-6, 9, 7, -0.7f, 0.6f}};
        for (const auto &c : cams)
        {
            float V[16], M[16];
            camera(c[0], c[1], c[2], c[3], c[4], V);
            mul(V, P, M);
            for (const float u : {40.0f, 52.0f, 120.0f})
                for (const float sx : {40.0f, 200.0f, 320.0f, 560.0f})
                    for (const float sy : {250.0f, 330.0f, 440.0f})
                    {
                        float X, Z, rx, ry;
                        if (!unprojectGround(M, sx, sy, X, Z))
                            continue;
                        ++cases;
                        if (!projectGround(M, X, Z, rx, ry) || std::fabs(rx - sx) > 0.05f || std::fabs(ry - sy) > 0.05f)
                        {
                            ++bad;
                            std::fprintf(stderr, "[battleptr selftest] round trip off: (%.1f,%.1f) -> (%.3f,%.3f)\n", sx, sy, rx, ry);
                        }
                        // a hand 0.1 m right, 0.3 m below, 0.2 m ahead of the head, aiming at (X, 0, Z)
                        const float hand[3] = {0.1f, -0.3f, -0.2f};
                        float Vc[4];
                        for (int j = 0; j < 4; ++j)
                            Vc[j] = X * V[j] + 0.0f * V[4 + j] + Z * V[8 + j] + V[12 + j];
                        const float cam[3] = {u * hand[0], -u * hand[1], -u * hand[2]}; // the hand in game camera space
                        const float dc[3] = {Vc[0] - cam[0], Vc[1] - cam[1], Vc[2] - cam[2]};
                        float dir[3] = {dc[0] / u, -dc[1] / u, -dc[2] / u}; // back into play space
                        const float len = std::sqrt(dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2]);
                        for (float &v : dir)
                            v /= len;
                        WorldRay ray;
                        float hx, hz;
                        if (!worldRay(V, u, hand, dir, ray) || !rayGround(ray, hx, hz))
                        {
                            ++bad;
                            std::fprintf(stderr, "[battleptr selftest] hand ray missed the ground at (%.1f,%.1f)\n", sx, sy);
                            continue;
                        }
                        worst = std::max(worst, std::max(std::fabs(hx - X), std::fabs(hz - Z)));
                        worstAngle = std::max(worstAngle, rayAngle(ray, X, 0.0f, Z));
                        if (std::fabs(hx - X) > 0.02f || std::fabs(hz - Z) > 0.02f || rayAngle(ray, X, 0.0f, Z) > 1e-3f)
                            ++bad;
                    }
        }
        std::fprintf(stderr, "[battleptr selftest] %s: %d pixels, desktop and hand ground points differ by at most %.5f map units, "
                             "hand ray to point angle at most %.6f rad\n",
                     bad ? "FAIL" : "PASS", cases, worst, worstAngle);
    }

    // The unit the pointer is on (0: none) and the map point under it, for the
    // source in use; false leaves the update to the originals.
    template <size_t I>
    bool pointerPick(uint8_t *ram, uint32_t mapview, uint32_t &best)
    {
        const Disc &D = kDiscs[I];
        const uint32_t mgr = word(ram, D.mgrGlobal);
        if (!mgr)
            return false;
        const uint32_t last = D.countField ? word(ram, mgr + D.countField) : 0x4fu;
        if (D.countField && int32_t(last) < 0)
            return false;
        best = 0;
        WorldRay ray;
        float gx = 0.0f, gy = 0.0f;
        if (handRay(ray))
        {
            // The nearest unit to the ray within about four degrees (the
            // centre pick's 48 px are about as wide): the angle to its icon-
            // plane point (what the game's marker is) or its real position.
            float X = 0.0f, Z = 0.0f;
            const bool onGround = rayGround(ray, X, Z);
            g_pick = Pick{Source::Hand, onGround, X, Z};
            ga2_vrpointer::wantTrigger(true);
            float bestAngle = 0.07f;
            for (uint32_t i = 0; i <= last; ++i)
            {
                const uint32_t unit = unitAt<I>(ram, mgr, i);
                if (!usable<I>(ram, unit))
                    continue;
                const float x = flt(ram, unit + D.pos) * 0.001f, y = flt(ram, unit + D.pos + 4) * 0.001f,
                            z = flt(ram, unit + D.pos + 8) * 0.001f;
                const float angle = std::min(rayAngle(ray, x, 0.0f, z), rayAngle(ray, x, y, z));
                if (angle < bestAngle)
                {
                    bestAngle = angle;
                    best = unit;
                }
            }
            g_pick.unit = best;
            return true;
        }
        if (!mouseGame(gx, gy))
        {
            g_pick = Pick{};
            return false;
        }
        // Everything below in the 2D layer's 640 x 480 space: the icons were
        // projected in the (hor+ widened) frame, which the squeeze shows at
        // 1/k width, so their x is expanded by k like the pointer's.
        const float k = ga2_activity::wideFrame() ? ga2_activity::horPlus.load() : 1.0f;
        float matrix[16];
        std::memcpy(matrix, ram + ((mgr + D.matrix) & 0x1FFFFFFu), sizeof(matrix));
        float bestD = 2304.0f;
        for (uint32_t i = 0; i <= last; ++i)
        {
            const uint32_t unit = unitAt<I>(ram, mgr, i);
            if (!usable<I>(ram, unit))
                continue;
            // The icon is drawn by the 2D layer: in a wide frame at the position
            // the hor+ anchor rules give it (an icon near an edge keeps its
            // distance from that edge), which is what the player points at.
            const float drawnX = ga2_hud::markerX(flt(ram, unit + D.sx), kIconHalfWidth);
            const float ux = 320.0f + (drawnX - 320.0f) * k, uy = flt(ram, unit + D.sx + 4);
            const float d = (ux - gx) * (ux - gx) + (uy - gy) * (uy - gy);
            if (d < bestD)
            {
                bestD = d;
                best = unit;
            }
        }
        float X = 0.0f, Z = 0.0f;
        const float rawX = 320.0f + (gx - 320.0f) / k;
        const bool onGround = unprojectGround(matrix, rawX, gy, X, Z);
        g_pick = Pick{Source::Mouse, onGround, X, Z, gx, gy, best};
        if (g_debug)
        {
            static int64_t shown = 0;
            if (nowNanos() - shown > 1'000'000'000)
            {
                shown = nowNanos();
                float bx = 0, by = 0, rx = 0, ry = 0;
                if (best)
                {
                    bx = flt(ram, best + D.sx);
                    by = flt(ram, best + D.sx + 4);
                    projectGround(matrix, flt(ram, best + D.pos) * 0.001f, flt(ram, best + D.pos + 8) * 0.001f, rx, ry);
                }
                float px = 0, py = 0;
                if (onGround)
                    projectGround(matrix, X, Z, px, py);
                std::fprintf(stderr, "[battleptr] t=%.1f map view: sub-state %u (next %u), unit %06x, target %06x, camera focus %06x\n",
                             sinceStart(), word(ram, mapview + 8), word(ram, mapview + 0xc), word(ram, mapview + 0x28),
                             word(ram, mapview + 0x2c), word(ram, mapview + 0x20));
                std::fprintf(stderr,
                             "[battleptr] t=%.1f mouse game=(%.1f,%.1f) k=%.3f raw=(%.1f,%.1f) ground=%d (%.3f, %.3f) reproj=(%.1f,%.1f) "
                             "best=%06x icon=(%.1f,%.1f) reproj(unit)=(%.1f,%.1f)\n",
                             sinceStart(), gx, gy, k, rawX, gy, onGround, X, Z, px, py, best, bx, by, rx, ry);
                if (std::getenv("GA2_BATTLEPTR_DEBUG")[0] == '2')
                    for (uint32_t i = 0; i <= last; ++i)
                    {
                        const uint32_t unit = unitAt<I>(ram, mgr, i);
                        if (usable<I>(ram, unit))
                            std::fprintf(stderr, "[battleptr] t=%.1f   unit %06x slot %u icon (%.1f, %.1f) drawn x %.1f\n", sinceStart(), unit, i,
                                         flt(ram, unit + D.sx), flt(ram, unit + D.sx + 4),
                                         ga2_hud::markerX(flt(ram, unit + D.sx), kIconHalfWidth));
                    }
            }
        }
        return true;
    }

    template <size_t I>
    void verifyProjection(uint8_t *ram);

    // The map's cursor ring is an item (mapview + 0x14) of the 2D layer that
    // the game places once, when the map loads, at 320 / 240 (the screen
    // centre) with its position function; with a mouse it is placed at the
    // pointer by calling that function again, so what it rings is what a click
    // acts on. (The item's own position fields are not read again.)
    bool g_ringMoved = false;
    template <size_t I>
    void placeRing(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime, uint32_t mapview, float x, float y)
    {
        const uint32_t ring = word(ram, mapview + 0x14);
        if (ring < 0x100000u || ring >= 0x2000000u)
            return;
        if (g_debug)
        {
            static int64_t shown = 0;
            if (nowNanos() - shown > 1'000'000'000)
            {
                shown = nowNanos();
                std::fprintf(stderr, "[battleptr] t=%.1f ring item %06x placed at (%.1f, %.1f)\n", sinceStart(), ring, x, y);
            }
        }
        // The ring's items stay centred wherever it is put (otherwise the hor+
        // edge rules would move it away from the pointer near the sides): its
        // layer items are the vector at ring + 0x64 .. 0x68.
        const uint32_t first = word(ram, ring + 0x64u), last = word(ram, ring + 0x68u);
        if (first >= 0x100000u && last <= 0x2000000u && last > first && last - first <= 64u * 4u)
            for (uint32_t at = first; at < last; at += 4u)
                ga2_hud::keepCentred(word(ram, at));
        // A call of the game's function from inside the hook: its own register
        // state (saved and restored), the stack below the hooked call's.
        const R5900Context saved = *ctx;
        ctx->f[12] = x;
        ctx->f[13] = y;
        SET_GPR_U32(ctx, 4, ring + 0x60u);
        ctx->pc = kDiscs[I].setPosition;
        g_original<I, SetPosition>(ram, ctx, runtime);
        *ctx = saved;
        g_ringMoved = x != 320.0f || y != 240.0f;
    }

    template <size_t I, int H>
    void hover(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t mapview = GPR_U32(ctx, 4);
        uint32_t best = 0;
        if (!mapview || !pointerPick<I>(ram, mapview, best))
        {
            if (g_ringMoved && mapview)
                placeRing<I>(ram, ctx, runtime, mapview, 320.0f, 240.0f);
            g_original<I, H>(ram, ctx, runtime);
            verifyProjection<I>(ram);
            return;
        }
        if (g_pick.source == Source::Mouse)
        {
            // The ring's layouts are centred (placeRing), so the pointer's
            // 2D x is what ends up under the pointer.
            placeRing<I>(ram, ctx, runtime, mapview, g_pick.gx, g_pick.gy);
        }
        else if (g_ringMoved)
            placeRing<I>(ram, ctx, runtime, mapview, 320.0f, 240.0f);
        std::memcpy(ram + ((mapview + (H == Hover ? 0x28u : 0x2cu)) & 0x1FFFFFFu), &best, sizeof(best));
        ctx->pc = GPR_U32(ctx, 31);
    }

    // Calls from the map's own update only (the ground check and move-one are
    // also called by stage scripts and the other views).
    template <size_t I>
    bool inMapUpdate(const R5900Context *ctx)
    {
        const uint32_t ra = GPR_U32(ctx, 31);
        return ra >= kDiscs[I].mapUpdate && ra < kDiscs[I].mapLeave;
    }

    // ground(a0 = manager, f12 = x, f13 = z) -> v0: may the order go there.
    template <size_t I>
    void ground(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (g_pick.source == Source::None || !inMapUpdate<I>(ctx))
        {
            g_original<I, Ground>(ram, ctx, runtime);
            return;
        }
        if (!g_pick.ground) // the pointer is over the sky
        {
            SET_GPR_U32(ctx, 2, 0u);
            ctx->pc = GPR_U32(ctx, 31);
            return;
        }
        if (g_debug)
            std::fprintf(stderr, "[battleptr] t=%.1f ground check at (%.3f, %.3f) instead of (%.3f, %.3f)\n", sinceStart(), g_pick.X,
                         g_pick.Z, ctx->f[12], ctx->f[13]);
        ctx->f[12] = g_pick.X;
        ctx->f[13] = g_pick.Z;
        g_original<I, Ground>(ram, ctx, runtime);
        if (g_debug)
            std::fprintf(stderr, "[battleptr] t=%.1f ground check -> %u\n", sinceStart(), GPR_U32(ctx, 2));
    }

    // moveAll(f12 = x, f13 = z, a0 = manager, a1 = 1), x and z in millimetres.
    template <size_t I>
    void moveAll(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (g_pick.source != Source::None && g_pick.ground && inMapUpdate<I>(ctx))
        {
            if (g_debug)
                std::fprintf(stderr, "[battleptr] t=%.1f move all angels to (%.0f, %.0f) instead of (%.0f, %.0f)\n", sinceStart(),
                             g_pick.X * 1000.0f, g_pick.Z * 1000.0f, ctx->f[12], ctx->f[13]);
            ctx->f[12] = g_pick.X * 1000.0f;
            ctx->f[13] = g_pick.Z * 1000.0f;
        }
        g_original<I, MoveAll>(ram, ctx, runtime);
    }

    // moveOne(f12 = x, f13 = y, f14 = z, a0 = unit, a1 = 1), millimetres.
    template <size_t I>
    void moveOne(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (g_pick.source != Source::None && g_pick.ground && inMapUpdate<I>(ctx))
        {
            if (g_debug)
                std::fprintf(stderr, "[battleptr] t=%.1f move unit %06x to (%.0f, %.0f) instead of (%.0f, %.0f)\n", sinceStart(),
                             GPR_U32(ctx, 4), g_pick.X * 1000.0f, g_pick.Z * 1000.0f, ctx->f[12], ctx->f[14]);
            ctx->f[12] = g_pick.X * 1000.0f;
            ctx->f[14] = g_pick.Z * 1000.0f;
        }
        g_original<I, MoveOne>(ram, ctx, runtime);
    }

    // ---- battle views: buttons, drags and zoom ---------------------------------
    // The mouse's meaning in each battle view lives in ps2_mouse.cpp (battleHeld:
    // it presses the game's own buttons); this side tells it which view runs and
    // what the game's buttons are (its pad map), and applies the drags and the
    // wheel to the view's own camera fields before the game's update, so the
    // game's limits (map extent, height range, distance range, reticle range)
    // still clamp them.
    //
    // Logical button ids of the game; its pad map (pad + 0x48) turns them into pad
    // bits, so a remapped pad configuration is followed.
    struct Ids
    {
        uint32_t up, down, left, right, circle, cross, start, triangle, l3, r3, square, fire, charge, l1, r1, l2;
    };
    constexpr Ids idsFor(bool shifted)
    {
        const uint32_t s = shifted ? 1u : 0u;
        return {1, 2, 3, 4, 5, 6, 7, 0x15, 0x16, 0x17, 0x21 + s, 0x1a + s, 0x1b + s, 0x1f + s, 0x20 + s, 9};
    }

    bool inRam(uint32_t a) { return a >= 0x100000u && a < 0x2000000u; }

    template <size_t I>
    ps2_mouse::BattleButtons battleButtons(const uint8_t *ram)
    {
        ps2_mouse::BattleButtons b;
        const Disc &D = kDiscs[I];
        const uint32_t ctl = word(ram, D.padCtl);
        if (!inRam(ctl))
            return b;
        const uint32_t pad = word(ram, ctl + 0x24);
        if (!inRam(pad))
            return b;
        const uint32_t map = word(ram, pad + 0x48);
        if (!inRam(map))
            return b;
        const Ids k = idsFor(D.shifted);
        // The map holds scePad2's layout of button bits (L2 0x1, R2 0x2, L1 0x4, R1 0x8,
        // Triangle 0x10, Circle 0x20, Cross 0x40, Square 0x80, Select 0x100, L3 0x200,
        // R3 0x400, Start 0x800, Up 0x1000, Right 0x2000, Down 0x4000, Left 0x8000); the
        // host's pad bits are libpad's.
        auto bits = [&](uint32_t id) {
            constexpr uint16_t kToHost[16] = {0x0100, 0x0200, 0x0400, 0x0800, 0x1000, 0x2000, 0x4000, 0x8000,
                                              0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0020, 0x0040, 0x0080};
            const uint32_t pad2 = word(ram, map + 4u * id) & 0xFFFFu;
            uint16_t host = 0;
            for (int i = 0; i < 16; ++i)
                if (pad2 & (1u << i))
                    host |= kToHost[i];
            return host;
        };
        b.up = bits(k.up);
        b.down = bits(k.down);
        b.left = bits(k.left);
        b.right = bits(k.right);
        b.circle = bits(k.circle);
        b.cross = bits(k.cross);
        b.start = bits(k.start);
        b.triangle = bits(k.triangle);
        b.l3 = bits(k.l3);
        b.r3 = bits(k.r3);
        b.square = bits(k.square);
        b.fire = bits(k.fire);
        b.charge = bits(k.charge);
        b.l1 = bits(k.l1);
        b.r1 = bits(k.r1);
        b.l2 = bits(k.l2);
        if (g_debug)
        {
            static int64_t shown = 0;
            if (nowNanos() - shown > 5'000'000'000)
            {
                shown = nowNanos();
                std::fprintf(stderr,
                             "[battleptr] t=%.1f pad map: dpad %04x %04x %04x %04x circle %04x cross %04x start %04x triangle %04x "
                             "l3 %04x r3 %04x square %04x fire %04x charge %04x l1 %04x r1 %04x\n",
                             sinceStart(), b.up, b.down, b.left, b.right, b.circle, b.cross, b.start, b.triangle, b.l3, b.r3,
                             b.square, b.fire, b.charge, b.l1, b.r1);
            }
        }
        return b;
    }

    // The window mouse in the 2D layer's 640 x 480 space, also while a drag
    // leaves the picture.
    bool mouseAnywhere(float &gx, float &gy)
    {
        float px, py;
        if (!ps2_mouse::pointerAnywhere(px, py))
            return false;
        gx = px;
        gy = py * (480.0f / 448.0f);
        return true;
    }

    // The system menu (flag 2), the pause (flag 4) or a message balloon (flag 0x40:
    // seen while the tutorial's and the stage's balloons run) is up: the pointer
    // does what it does in menus and dialogue (left = confirm, right = back,
    // wheel = up / down).
    template <size_t I>
    bool menuUp(const uint8_t *ram, uint32_t mgr)
    {
        return (word(ram, mgr + kDiscs[I].flags) & 0x46u) != 0;
    }

    // The tutorial's gate of a logical button (the game's own "action enabled" test): the
    // direct camera moves below honour the gates of the buttons they stand for.
    template <size_t I>
    bool gateOpen(const uint8_t *ram, uint32_t mgr, uint32_t id)
    {
        return word(ram, mgr + kDiscs[I].gates + 4u * id) != 0;
    }

    template <size_t I>
    uint32_t currentMode(const uint8_t *ram, uint32_t mgr)
    {
        return word(ram, mgr + kDiscs[I].requestedMode - 4u);
    }

    struct MapDrive
    {
        uint32_t hoverUnit = 0; // the last unit the pointer was on, and when (a click on it moves the camera away)
        int64_t hoverAt = 0;
        uint32_t mapview = 0;
        int64_t published = 0;
        bool dragging = false;
        float zoomSteps = 0.0f; // wheel notches not yet applied, + = zoom in
        bool panning = false;
        float panX = 0.0f, panY = 0.0f; // the pointer at the last pan step (the game's own screen space)
    } g_map;

    struct UnitCameraDrive
    {
        bool turning = false;
        float x = 0.0f, y = 0.0f;
        float zoomSteps = 0.0f;
    } g_unitCam;

    // The tactical map's frame: tell the mouse the view and the game's buttons,
    // take what the mouse did.
    template <size_t I>
    void mapUpdate(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t mapview = GPR_U32(ctx, 4);
        // The update is called in every view; the map's returns at once unless it is the current one.
        const uint32_t mgrNow = word(ram, kDiscs[I].mgrGlobal);
        if (mapview && mgrNow && word(ram, mapview + 4) == 0 && currentMode<I>(ram, mgrNow) == 0)
        {
            ps2_mouse::BattleState st;
            st.view = ps2_mouse::BattleView::Map;
            st.buttons = battleButtons<I>(ram);
            st.pointerOnUnit = g_pick.source == Source::Mouse && g_pick.unit != 0;
            if (const uint32_t mgr = word(ram, kDiscs[I].mgrGlobal))
                st.modal = menuUp<I>(ram, mgr);
            ps2_mouse::setBattleState(st);
            const ps2_mouse::BattleEvents ev = ps2_mouse::takeBattleEvents();
            if (g_debug)
            {
                static int64_t shown = 0;
                if (nowNanos() - shown > 2'000'000'000)
                {
                    shown = nowNanos();
                    const uint32_t m = word(ram, kDiscs[I].mgrGlobal);
                    std::fprintf(stderr, "[battleptr] t=%.1f map: sub-state %u, flags %02x, modal %d, on unit %06x, buttons: drag %d wheel %d\n",
                                 sinceStart(), word(ram, mapview + 8), m ? word(ram, m + kDiscs[I].flags) : 0u, st.modal, g_pick.unit,
                                 ev.dragging, ev.wheel);
                }
            }
            g_map.mapview = mapview;
            g_map.published = nowNanos();
            g_map.dragging = ev.dragging;
            if (ev.listHeld)
            {
                // L2 is held by the mouse: the angel list is open; the wheel steps its cursor
                // (the game's own D-pad moves) and releasing the button jumps to that angel.
                const ps2_mouse::BattleButtons b = st.buttons;
                static int pending = 0;
                pending = std::clamp(pending + ev.wheel, -4, 4);
                if (pending != 0 && ps2_mouse::battlePulsesPending() == 0)
                {
                    ps2_mouse::battlePulse(pending > 0 ? b.up : b.down);
                    pending += pending > 0 ? -1 : 1;
                }
            }
            else
                g_map.zoomSteps = std::clamp(g_map.zoomSteps + float(ev.wheel), -6.0f, 6.0f);
            if (g_pick.source == Source::Mouse && g_pick.unit)
            {
                g_map.hoverUnit = g_pick.unit;
                g_map.hoverAt = nowNanos();
            }
            const uint32_t doubleUnit = g_map.hoverUnit;
            if (ev.doubleClick && nowNanos() - g_map.hoverAt < 800'000'000 && usable<I>(ram, doubleUnit) &&
                gateOpen<I>(ram, mgrNow, 5))
            {
                // The game's own focus: the camera glides to the unit and tracks it.
                const R5900Context saved = *ctx;
                ctx->f[12] = 0.2f;
                SET_GPR_U32(ctx, 4, mapview);
                SET_GPR_U32(ctx, 5, doubleUnit);
                SET_GPR_U32(ctx, 6, 0u);
                SET_GPR_U32(ctx, 7, 0u);
                ctx->pc = kDiscs[I].focus;
                g_original<I, Focus>(ram, ctx, runtime);
                *ctx = saved;
                if (g_debug)
                    std::fprintf(stderr, "[battleptr] t=%.1f double click: camera focus on unit %06x\n", sinceStart(), doubleUnit);
            }
        }
        g_original<I, MapUpdate>(ram, ctx, runtime);
    }

    // The map camera's update: a right drag pans (the ground under the pointer
    // follows it), the wheel moves the camera's height (what R1 / R2 do) toward
    // or away from the point under the pointer. The game's own update that
    // follows keeps both inside the map.
    template <size_t I>
    void mapCamera(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const Disc &D = kDiscs[I];
        const uint32_t cam = GPR_U32(ctx, 4);
        const uint32_t mgr = word(ram, D.mgrGlobal);
        const bool fresh = g_map.mapview && nowNanos() - g_map.published < 300'000'000;
        // Free camera only (not easing to a point or following a unit), and the
        // map view is the current one.
        if (fresh && inRam(cam) && mgr && currentMode<I>(ram, mgr) == 0 && word(ram, cam + 0x150) == 0 && word(ram, cam + 0x174) == 0)
        {
            float gx = 0.0f, gy = 0.0f;
            const bool have = mouseAnywhere(gx, gy);
            const float k = ga2_activity::wideFrame() ? ga2_activity::horPlus.load() : 1.0f;
            float matrix[16];
            std::memcpy(matrix, ram + ((mgr + D.matrix) & 0x1FFFFFFu), sizeof(matrix));
            float camX = flt(ram, cam), camY = flt(ram, cam + 4), camZ = flt(ram, cam + 8);
            bool moved = false;
            const bool panOk = gateOpen<I>(ram, mgr, 1) && gateOpen<I>(ram, mgr, 2) && gateOpen<I>(ram, mgr, 3) && gateOpen<I>(ram, mgr, 4);
            if (g_map.dragging && have && panOk)
            {
                const float rawX = 320.0f + (gx - 320.0f) / k;
                float x0, z0, x1, z1;
                if (g_map.panning && unprojectGround(matrix, g_map.panX, g_map.panY, x0, z0) &&
                    unprojectGround(matrix, rawX, gy, x1, z1))
                {
                    camX += x0 - x1;
                    camZ += z0 - z1;
                    moved = true;
                }
                g_map.panX = rawX;
                g_map.panY = gy;
                g_map.panning = true;
            }
            else
                g_map.panning = false;
            // R2 (id 0x13) zooms in, R1 (id 0x11) out; a closed gate drops that direction's steps.
            if ((g_map.zoomSteps > 0.0f && !gateOpen<I>(ram, mgr, 0x13)) || (g_map.zoomSteps < 0.0f && !gateOpen<I>(ram, mgr, 0x11)))
                g_map.zoomSteps = 0.0f;
            if (std::fabs(g_map.zoomSteps) > 0.02f)
            {
                const float dt = std::clamp(ctx->f[12], 0.001f, 0.1f);
                const float step = g_map.zoomSteps * (1.0f - std::exp(-dt * 14.0f));
                g_map.zoomSteps -= step;
                const float ratio = std::pow(0.8f, step); // < 1: closer to the ground
                camY *= ratio;
                // Keep the point under the pointer where it is on the screen.
                if (g_pick.source == Source::Mouse && g_pick.ground)
                {
                    camX += (1.0f - ratio) * (g_pick.X - camX);
                    camZ += (1.0f - ratio) * (g_pick.Z - camZ);
                    moved = true;
                }
            }
            if (g_debug && (moved || g_map.dragging || std::fabs(g_map.zoomSteps) > 0.02f))
            {
                static int64_t shown = 0;
                if (nowNanos() - shown > 300'000'000)
                {
                    shown = nowNanos();
                    std::fprintf(stderr, "[battleptr] t=%.1f map camera (%.3f, %.3f, %.3f) -> (%.3f, %.3f, %.3f), drag %d zoom steps left %.2f\n",
                                 sinceStart(), flt(ram, cam), flt(ram, cam + 4), flt(ram, cam + 8), camX, camY, camZ, g_map.dragging,
                                 g_map.zoomSteps);
                }
            }
            if (moved || camY != flt(ram, cam + 4))
            {
                std::memcpy(ram + ((cam)&0x1FFFFFFu), &camX, sizeof(camX));
                std::memcpy(ram + ((cam + 4) & 0x1FFFFFFu), &camY, sizeof(camY));
                std::memcpy(ram + ((cam + 8) & 0x1FFFFFFu), &camZ, sizeof(camZ));
                // The camera stops tracking a unit once it is moved by hand
                // (as the game's own pan does); zooming alone keeps tracking.
                if (moved && inRam(g_map.mapview))
                {
                    const uint32_t zero = 0;
                    std::memcpy(ram + ((g_map.mapview + 0x24) & 0x1FFFFFFu), &zero, sizeof(zero));
                }
            }
        }
        else
            g_map.panning = false;
        g_original<I, MapCamera>(ram, ctx, runtime);
    }

    // The cockpit's reticle follows the pointer: its top-left is written
    // before the game's update (which adds the stick and clamps it to the
    // picture), as the 64 px reticle is centred on the pointer.
    template <size_t I>
    void reticle(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const Disc &D = kDiscs[I];
        const uint32_t obj = GPR_U32(ctx, 4);
        const uint32_t mgr = word(ram, D.mgrGlobal);
        if (inRam(obj) && mgr && currentMode<I>(ram, mgr) == 2)
        {
            ps2_mouse::BattleState st;
            st.view = ps2_mouse::BattleView::Cockpit;
            st.buttons = battleButtons<I>(ram);
            st.modal = menuUp<I>(ram, mgr);
            ps2_mouse::setBattleState(st);
            ps2_mouse::takeBattleEvents();
            float gx = 0.0f, gy = 0.0f;
            const bool pointed = mouseGame(gx, gy);
            if (g_debug && !pointed)
            {
                static int64_t shown = 0;
                if (nowNanos() - shown > 2'000'000'000)
                {
                    shown = nowNanos();
                    std::fprintf(stderr, "[battleptr] t=%.1f cockpit: the pointer is idle or off the picture\n", sinceStart());
                }
            }
            if (pointed)
            {
                const float x = std::clamp(gx - 32.0f, -32.0f, 608.0f), y = std::clamp(gy - 32.0f, -32.0f, 448.0f);
                if (g_debug)
                {
                    static int64_t shown = 0;
                    if (nowNanos() - shown > 1'000'000'000)
                    {
                        shown = nowNanos();
                        std::fprintf(stderr, "[battleptr] t=%.1f cockpit: pointer (%.1f, %.1f) -> reticle top-left (%.1f, %.1f), was (%.1f, %.1f), flags %02x\n",
                                     sinceStart(), gx, gy, x, y, flt(ram, obj + D.reticleX), flt(ram, obj + D.reticleX + 4),
                                     word(ram, mgr + D.flags));
                    }
                }
                std::memcpy(ram + ((obj + D.reticleX) & 0x1FFFFFFu), &x, sizeof(x));
                std::memcpy(ram + ((obj + D.reticleX + 4) & 0x1FFFFFFu), &y, sizeof(y));
            }
        }
        g_original<I, Reticle>(ram, ctx, runtime);
    }

    // The rear (chase) view: the buttons that switch views.
    template <size_t I>
    void rearView(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t mgr = word(ram, kDiscs[I].mgrGlobal);
        if (mgr && currentMode<I>(ram, mgr) == 1)
        {
            if (g_debug)
            {
                static int64_t shown = 0;
                if (nowNanos() - shown > 3'000'000'000)
                {
                    shown = nowNanos();
                    std::fprintf(stderr, "[battleptr] t=%.1f rear view\n", sinceStart());
                }
            }
            ps2_mouse::BattleState st;
            st.view = ps2_mouse::BattleView::Rear;
            st.buttons = battleButtons<I>(ram);
            st.modal = menuUp<I>(ram, mgr);
            ps2_mouse::setBattleState(st);
            ps2_mouse::takeBattleEvents();
        }
        g_original<I, Rear>(ram, ctx, runtime);
    }

    // The unit camera: dragging turns it (yaw +0x164, pitch +0x160, what the
    // right stick moves), the wheel changes its distance (+0x170, what R1 / R2
    // move); the game's update clamps all three.
    template <size_t I>
    void unitCamera(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const Disc &D = kDiscs[I];
        const uint32_t cam = GPR_U32(ctx, 4);
        const uint32_t mgr = word(ram, D.mgrGlobal);
        if (inRam(cam) && mgr && currentMode<I>(ram, mgr) == 3)
        {
            ps2_mouse::BattleState st;
            st.view = ps2_mouse::BattleView::UnitCamera;
            st.buttons = battleButtons<I>(ram);
            st.modal = menuUp<I>(ram, mgr);
            ps2_mouse::setBattleState(st);
            const ps2_mouse::BattleEvents ev = ps2_mouse::takeBattleEvents();
            float gx = 0.0f, gy = 0.0f;
            if (ev.dragging && mouseAnywhere(gx, gy))
            {
                if (g_unitCam.turning)
                {
                    constexpr float kRadiansPerPixel = 0.006f;
                    // Like the right stick: right = yaw up, down = pitch down.
                    const float yaw = flt(ram, cam + 0x164) + (gx - g_unitCam.x) * kRadiansPerPixel;
                    const float pitch = flt(ram, cam + 0x160) - (gy - g_unitCam.y) * kRadiansPerPixel;
                    std::memcpy(ram + ((cam + 0x164) & 0x1FFFFFFu), &yaw, sizeof(yaw));
                    std::memcpy(ram + ((cam + 0x160) & 0x1FFFFFFu), &pitch, sizeof(pitch));
                }
                g_unitCam.x = gx;
                g_unitCam.y = gy;
                g_unitCam.turning = true;
            }
            else
                g_unitCam.turning = false;
            if (g_debug && (ev.dragging || ev.wheel))
                std::fprintf(stderr, "[battleptr] t=%.1f unit camera: drag %d wheel %d yaw %.3f pitch %.3f distance %.3f\n", sinceStart(),
                             ev.dragging, ev.wheel, flt(ram, cam + 0x164), flt(ram, cam + 0x160), flt(ram, cam + 0x170));
            g_unitCam.zoomSteps = std::clamp(g_unitCam.zoomSteps + float(ev.wheel), -6.0f, 6.0f);
            if (std::fabs(g_unitCam.zoomSteps) > 0.02f)
            {
                const float dt = std::clamp(ctx->f[12], 0.001f, 0.1f);
                const float step = g_unitCam.zoomSteps * (1.0f - std::exp(-dt * 14.0f));
                g_unitCam.zoomSteps -= step;
                // R2 (distance down) held at full pressure for 0.12 s per notch:
                // 359.928 * the camera's rate field (+0x158) * seconds.
                const float dist = flt(ram, cam + 0x170) - step * 0.12f * 359.928f * flt(ram, cam + 0x158);
                std::memcpy(ram + ((cam + 0x170) & 0x1FFFFFFu), &dist, sizeof(dist));
            }
        }
        else
            g_unitCam.turning = false;
        g_original<I, UnitCamera>(ram, ctx, runtime);
    }

    // GA2_BATTLEPTR_DEBUG=1: once a second while the map view runs, how well the
    // matrix at mgr + matrix projects each unit's map point (x, 0, z) onto the
    // icon the game computed (unit fields and offsets of this disc's layout).
    template <size_t I>
    void verifyProjection(uint8_t *ram)
    {
        static int64_t shown = 0;
        if (!g_debug || nowNanos() - shown < 1'000'000'000)
            return;
        const Disc &D = kDiscs[I];
        const uint32_t mgr = word(ram, D.mgrGlobal);
        if (!mgr)
            return;
        shown = nowNanos();
        float matrix[16];
        std::memcpy(matrix, ram + ((mgr + D.matrix) & 0x1FFFFFFu), sizeof(matrix));
        const uint32_t last = D.countField ? word(ram, mgr + D.countField) : 0x4fu;
        int n = 0;
        float worst = 0.0f;
        for (uint32_t i = 0; i <= last && int32_t(last) >= 0; ++i)
        {
            const uint32_t unit = unitAt<I>(ram, mgr, i);
            if (!usable<I>(ram, unit))
                continue;
            float sx, sy;
            if (!projectGround(matrix, flt(ram, unit + D.pos) * 0.001f, flt(ram, unit + D.pos + 8) * 0.001f, sx, sy))
                continue;
            ++n;
            worst = std::max(worst, std::max(std::fabs(sx - flt(ram, unit + D.sx)), std::fabs(sy - flt(ram, unit + D.sx + 4))));
            if (g_debug && std::getenv("GA2_BATTLEPTR_DEBUG") && std::getenv("GA2_BATTLEPTR_DEBUG")[0] == '2')
                std::fprintf(stderr, "[battleptr] t=%.1f   unit %06x slot %u icon (%.1f, %.1f) world (%.0f, %.0f, %.0f)\n", sinceStart(), unit, i,
                             flt(ram, unit + D.sx), flt(ram, unit + D.sx + 4), flt(ram, unit + D.pos), flt(ram, unit + D.pos + 4),
                             flt(ram, unit + D.pos + 8));
        }
        std::fprintf(stderr, "[battleptr] %s map view: %d units, projection vs icon differ by at most %.3f px\n", D.disc, n, worst);
    }

    // Test aid (GA2_BATTLEPTR_MAP_AT=<seconds>): asks for the tactical map that
    // many seconds after the program started (the first battle frame after
    // that), for runs without an L3 button (the keyboard has none).
    template <size_t I>
    void mgrUpdate(uint8_t *ram, R5900Context *ctx, PS2Runtime *runtime)
    {
        static const double at = [] {
            const char *v = std::getenv("GA2_BATTLEPTR_MAP_AT");
            return v && *v ? std::atof(v) : -1.0;
        }();
        static const char *cmdFile = std::getenv("GA2_BATTLEPTR_CMD");
        if (cmdFile && *cmdFile)
        {
            if (std::FILE *f = std::fopen(cmdFile, "r"))
            {
                char line[160];
                std::string text;
                while (std::fgets(line, sizeof(line), f))
                    text += line;
                std::fclose(f);
                if (!text.empty())
                {
                    std::FILE *clear = std::fopen(cmdFile, "w"); // empty it
                    if (clear)
                        std::fclose(clear);
                    const uint32_t mgr = GPR_U32(ctx, 4);
                    size_t at = 0;
                    while (at < text.size())
                    {
                        const size_t end = text.find('\n', at);
                        const std::string cmd = text.substr(at, end == std::string::npos ? std::string::npos : end - at);
                        at = end == std::string::npos ? text.size() : end + 1;
                        unsigned a = 0, b = 0;
                        if (mgr && std::sscanf(cmd.c_str(), "mode %u", &a) == 1)
                        {
                            std::memcpy(ram + ((mgr + kDiscs[I].requestedMode) & 0x1FFFFFFu), &a, sizeof(a));
                            std::fprintf(stderr, "[battleptr] cmd: requested view %u\n", a);
                        }
                        else if (mgr && std::sscanf(cmd.c_str(), "poke %x %x", &a, &b) == 2 && a < 0x2000)
                        {
                            std::memcpy(ram + ((mgr + a) & 0x1FFFFFFu), &b, sizeof(b));
                            std::fprintf(stderr, "[battleptr] cmd: mgr+0x%x = 0x%x\n", a, b);
                        }
                    }
                }
            }
        }
        static bool done = false;
        if (at >= 0.0 && !done && sinceStart() >= at)
        {
            const uint32_t mgr = GPR_U32(ctx, 4);
            if (mgr)
            {
                done = true;
                const uint32_t zero = 0;
                std::memcpy(ram + ((mgr + kDiscs[I].requestedMode) & 0x1FFFFFFu), &zero, sizeof(zero));
                std::fprintf(stderr, "[battleptr] requested the tactical map\n");
            }
        }
        g_original<I, MgrUpdate>(ram, ctx, runtime);
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        const Disc &d = kDiscs[I];
        if (const char *t = std::getenv("GA2_BATTLEPTR_SELFTEST"); t && *t == '1')
            selfTest();
        for (uint32_t address : {d.hover, d.targetHover, d.ground, d.moveAll, d.moveOne, d.mgrUpdate, d.setPosition, d.mapUpdate,
                                 d.mapCamera, d.reticle, d.unitCamera, d.focus, d.rear})
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 battlepointer] " << d.disc << ": 0x" << std::hex << address << std::dec
                          << " not in the generated table; pointer controls off\n";
                return;
            }
        auto hook = [&](uint32_t address, PS2Runtime::RecompiledFunction &original, PS2Runtime::RecompiledFunction replacement) {
            original = runtime.lookupFunction(address);
            runtime.replaceFunction(address, replacement);
        };
        hook(d.hover, g_original<I, Hover>, hover<I, Hover>);
        hook(d.targetHover, g_original<I, TargetHover>, hover<I, TargetHover>);
        hook(d.ground, g_original<I, Ground>, ground<I>);
        hook(d.moveAll, g_original<I, MoveAll>, moveAll<I>);
        hook(d.moveOne, g_original<I, MoveOne>, moveOne<I>);
        g_original<I, SetPosition> = runtime.lookupFunction(d.setPosition); // called, not replaced
        g_original<I, Focus> = runtime.lookupFunction(d.focus);             // likewise
        hook(d.mapUpdate, g_original<I, MapUpdate>, mapUpdate<I>);
        hook(d.mapCamera, g_original<I, MapCamera>, mapCamera<I>);
        hook(d.reticle, g_original<I, Reticle>, reticle<I>);
        hook(d.unitCamera, g_original<I, UnitCamera>, unitCamera<I>);
        hook(d.rear, g_original<I, Rear>, rearView<I>);
        if (std::getenv("GA2_BATTLEPTR_MAP_AT") || std::getenv("GA2_BATTLEPTR_CMD"))
            hook(d.mgrUpdate, g_original<I, MgrUpdate>, mgrUpdate<I>);
        std::cerr << "[ga2 battlepointer] " << d.disc << ": tactical map pointer hooks bound\n";
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 battle pointer zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 battle pointer mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 battle pointer mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 battle pointer eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 battle pointer eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
