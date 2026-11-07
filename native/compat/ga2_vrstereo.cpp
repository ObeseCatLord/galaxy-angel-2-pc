// VR single-pass stereo, game side (runtime/ga2_stereo.h, docs/vr/stereo.md).
//
// In a VR headset session (GA2_VR=1, GA2_VR_STEREO not 0) every 3D frame is
// rendered once from the player's head: the game camera's view matrix gets
// the head pose (relative to the recentred origin) and the perspective
// builder gets a projection covering both eyes' fields of view over the
// 640x448 frame. The runtime's GS turns that one frame into both eyes. The
// EE frustum test passes everything, since the player may look anywhere.
// Each hook acts before calling the original (recompiled functions can
// return to the dispatcher early).
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ga2_vrview.h"
#include "runtime/ga2_stereo.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

namespace ga2_vrview
{
    namespace
    {
        float g_view[16];
        uint64_t g_serial = 0;
    }
    bool latest(float view[16], uint64_t &serial)
    {
        std::memcpy(view, g_view, sizeof(g_view));
        serial = g_serial;
        return g_serial != 0;
    }
    void capture(const float v[16])
    {
        std::memcpy(g_view, v, sizeof(g_view));
        ++g_serial;
    }
}

namespace
{
    struct DiscStereo
    {
        const char *disc;
        uint32_t renderFlip, flip;       // main-loop render + flip, frame flip (DISPFB)
        uint32_t apply, applyProjection; // CCamera::Apply (calls MatSet(0, view)), the next function
        uint32_t matSet, perspective, frustum;
        uint32_t clipExtents; // globals Ex, Ey (set by ApplyProjection, read by the builder)
    };

    constexpr DiscStereo kDiscs[] = {
        {"zettai", 0x112dc0u, 0x2285e0u, 0x118d98u, 0x118e78u, 0x22fc88u, 0x22ff20u, 0x104728u, 0x2cf1a0u},
        {"mugen-1", 0x112e50u, 0x242038u, 0x118ef0u, 0x118fd0u, 0x2496e0u, 0x249980u, 0x104788u, 0x2eb7a0u},
        {"mugen-2", 0x112e40u, 0x22a090u, 0x118e18u, 0x118ef8u, 0x231738u, 0x2319d0u, 0x1047b0u, 0x2d0ca0u},
        {"eigou-1", 0x112e50u, 0x24ed38u, 0x118ef0u, 0x118fd0u, 0x2563e0u, 0x256680u, 0x104788u, 0x2f98a0u},
        {"eigou-2", 0x112e50u, 0x2430a8u, 0x118ef0u, 0x118fd0u, 0x24a750u, 0x24a9f0u, 0x104788u, 0x2ec860u},
    };

    enum Hook
    {
        RenderFlip,
        Flip,
        MatSet,
        Perspective,
        Frustum,
    };

    template <size_t I, int H>
    PS2Runtime::RecompiledFunction g_original = nullptr;

    // This frame's setup and eye transform (EE thread only).
    ga2_stereo::Setup g_frame;
    float g_eye[16];
    // GA2_VR_STEREO_DEBUG bits (diagnostics): 1 keeps the game's view, 2 its
    // projection, 4 its frustum test.
    const int g_debug = [] {
        const char *v = std::getenv("GA2_VR_STEREO_DEBUG");
        return v && *v ? std::atoi(v) : 0;
    }();

    // Row-vector 4x4 matrices, row-major (v' = v M, translation in 12..14).
    void multiply(const float *a, const float *b, float *out)
    {
        float r[16];
        for (int i = 0; i < 4; ++i)
            for (int j = 0; j < 4; ++j)
                r[i * 4 + j] = a[i * 4 + 0] * b[0 * 4 + j] + a[i * 4 + 1] * b[1 * 4 + j] + a[i * 4 + 2] * b[2 * 4 + j] +
                               a[i * 4 + 3] * b[3 * 4 + j];
        std::memcpy(out, r, sizeof(r));
    }

    // Game camera space -> eye space: to OpenXR axes and metres (C S^-1),
    // into the head frame (inverse of the head pose), back (C S).
    void eyeTransform(const ga2_stereo::Setup &s, float *out)
    {
        const float *h = s.headAnchor;
        float inv[16] = {h[0], h[4], h[8], 0, h[1], h[5], h[9], 0, h[2], h[6], h[10], 0, 0, 0, 0, 1};
        for (int j = 0; j < 3; ++j) // -t R^T
            inv[12 + j] = -(h[12] * inv[0 * 4 + j] + h[13] * inv[1 * 4 + j] + h[14] * inv[2 * 4 + j]);
        const float u = s.unitsPerMetre;
        const float there[16] = {1 / u, 0, 0, 0, 0, -1 / u, 0, 0, 0, 0, -1 / u, 0, 0, 0, 0, 1}; // C S^-1
        const float back[16] = {u, 0, 0, 0, 0, -u, 0, 0, 0, 0, -u, 0, 0, 0, 0, 1};             // C S
        multiply(there, inv, out);
        multiply(out, back, out);
    }

    template <size_t I>
    void renderFlip(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ga2_stereo::beginFrame();
        g_frame = ga2_stereo::building();
        if (g_frame.active)
            eyeTransform(g_frame, g_eye);
        g_original<I, RenderFlip>(rdram, ctx, runtime);
    }

    template <size_t I>
    void flip(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ga2_stereo::flip();
        g_original<I, Flip>(rdram, ctx, runtime);
    }

    // MatSet(0, view) from CCamera::Apply: the view becomes the head's.
    template <size_t I>
    void matSet(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t ra = GPR_U32(ctx, 31);
        if (g_frame.active && !(g_debug & 1) && GPR_U32(ctx, 4) == 0u && ra >= kDiscs[I].apply && ra < kDiscs[I].applyProjection)
        {
            const uint32_t address = GPR_U32(ctx, 5) & 0x01FFFFFFu;
            float view[16];
            std::memcpy(view, rdram + address, sizeof(view));
            ga2_vrview::capture(view);
            multiply(view, g_eye, view);
            std::memcpy(rdram + address, view, sizeof(view));
        }
        g_original<I, MatSet>(rdram, ctx, runtime);
    }

    // Perspective builder (scrz f12, ax f13, ay f14, cx f15, cy f16): the
    // shared field of view over the 640x448 frame (GS centre 2048, 2048).
    template <size_t I>
    void perspective(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const float scrz = ctx->f[12];
        if (g_debug & 8) // log the builder's arguments
        {
            static int shown = 0;
            if (shown++ < 4)
                std::cerr << "[ga2 vr] perspective f12.." << ' ' << ctx->f[12] << ' ' << ctx->f[13] << ' ' << ctx->f[14] << ' '
                          << ctx->f[15] << ' ' << ctx->f[16] << ' ' << ctx->f[17] << ' ' << ctx->f[18] << ' ' << ctx->f[19]
                          << " a0 " << std::hex << GPR_U32(ctx, 4) << " ra " << GPR_U32(ctx, 31) << std::dec << '\n';
        }
        if (g_frame.active && !(g_debug & 2) && scrz > 0.0f)
        {
            const ga2_stereo::Setup &s = g_frame;
            const float fx = ga2_stereo::kFrameWidth / (s.tanRight - s.tanLeft);
            const float fy = ga2_stereo::kFrameHeight / (s.tanUp - s.tanDown);
            ctx->f[13] = fx / scrz;
            ctx->f[14] = fy / scrz;
            const float cx = 2048.0f - 0.5f * ga2_stereo::kFrameWidth - s.tanLeft * fx;
            const float cy = 2048.0f - 0.5f * ga2_stereo::kFrameHeight + s.tanUp * fy;
            ctx->f[15] = cx;
            ctx->f[16] = cy;
            // Guard band: clipped vertices land at centre +- a*E, which must
            // stay inside the GS's 0..4095 (the game's 1856 overflows with a
            // wider projection).
            float extents[2];
            std::memcpy(extents, rdram + kDiscs[I].clipExtents, sizeof(extents));
            const float ex = (std::min(cx, 4095.0f - cx) - 32.0f) / ctx->f[13];
            const float ey = (std::min(cy, 4095.0f - cy) - 32.0f) / ctx->f[14];
            extents[0] = std::min(extents[0], ex);
            extents[1] = std::min(extents[1], ey);
            std::memcpy(rdram + kDiscs[I].clipExtents, extents, sizeof(extents));
        }
        g_original<I, Perspective>(rdram, ctx, runtime);
    }

    // Sphere vs frustum: everything is visible somewhere around the player.
    template <size_t I>
    void frustum(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (g_frame.active && !(g_debug & 4))
        {
            SET_GPR_U32(ctx, 2, 1u);
            ctx->pc = GPR_U32(ctx, 31);
            return;
        }
        g_original<I, Frustum>(rdram, ctx, runtime);
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        const char *vr = std::getenv("GA2_VR");
        const char *stereo = std::getenv("GA2_VR_STEREO");
        if (!vr || std::string(vr) != "1" || (stereo && std::string(stereo) == "0"))
            return;
        const DiscStereo &d = kDiscs[I];
        for (uint32_t address : {d.renderFlip, d.flip, d.matSet, d.perspective, d.frustum})
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 vr] " << d.disc << ": 0x" << std::hex << address << std::dec
                          << " not in the generated table; stereo off\n";
                return;
            }
        auto hook = [&](uint32_t address, PS2Runtime::RecompiledFunction &original, PS2Runtime::RecompiledFunction replacement) {
            original = runtime.lookupFunction(address);
            runtime.replaceFunction(address, replacement);
        };
        hook(d.renderFlip, g_original<I, RenderFlip>, renderFlip<I>);
        hook(d.flip, g_original<I, Flip>, flip<I>);
        hook(d.matSet, g_original<I, MatSet>, matSet<I>);
        hook(d.perspective, g_original<I, Perspective>, perspective<I>);
        hook(d.frustum, g_original<I, Frustum>, frustum<I>);
        std::cerr << "[ga2 vr] " << d.disc << ": single-pass stereo hooks bound\n";
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 VR stereo zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR stereo mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR stereo mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR stereo eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR stereo eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
