// VR: tells the runtime which battle view is on screen, so the controllers'
// triggers press the right thing (docs/vr/stereo.md, "Controller bindings"):
// in the cockpit the triggers shoot (Cross held), in the unit camera view they
// are L2 / R2 (zoom, with the right stick turning the camera). The tactical map
// is flagged by ga2_battlepointer.cpp (ga2_vrpointer::wantTrigger).
//
// Each view's update runs once per frame while that view is active (the battle
// manager calls views[mode]->update, vtable +0x24); the hook only notes the
// scene and calls the original. The runtime ends a scene half a second after
// the last note. Bound only for GA2_VR=1 (a headset session); otherwise the
// functions stay the game's own.
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "runtime/ga2_activity.h"
#include "runtime/ga2_vrpointer.h"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace
{
    struct Disc
    {
        const char *disc;
        // CSLGCockpitViewManager and CSLGCameraViewManager update (the battle manager's view modes 2 and 3).
        uint32_t cockpitUpdate, cameraUpdate;
        // Test aid: the battle manager's update and its requested-view-mode field (as in ga2_battlepointer.cpp).
        uint32_t mgrUpdate, requestedMode;
    };
    // Addresses: research/battle-re/report.md section 6; Mugen 2 by signature from Zettai (+0x1aa0),
    // Mugen 1 / Eigou 1 / Eigou 2 checked by function size and the same gating ids (L3 0x16, shoulders, Start).
    constexpr Disc kDiscs[] = {
        {"zettai", 0x1a6db8u, 0x1aa6e0u, 0x1650b0u, 0x44cu},
        {"mugen-1", 0x1b4710u, 0x1b8888u, 0x16f340u, 0x454u},
        {"mugen-2", 0x1a8858u, 0x1ac180u, 0x166b50u, 0x44cu},
        {"eigou-1", 0x1ba1c8u, 0x1be2f0u, 0x172ff8u, 0x4f4u},
        {"eigou-2", 0x1b52a8u, 0x1b9420u, 0x16fe60u, 0x454u},
    };

    enum Hook
    {
        Cockpit,
        Camera,
        MgrUpdate,
    };

    template <size_t I, int H>
    PS2Runtime::RecompiledFunction g_original = nullptr;
    template <size_t I>
    int g_lastView = -1;

    template <size_t I, int H>
    void view(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ga2_vrpointer::setScene(H == Cockpit ? ga2_vrpointer::Scene::Cockpit : ga2_vrpointer::Scene::UnitCamera);
        // A line whenever the view changes (diagnostics for runs without a headset's hands).
        if (g_lastView<I> != H)
        {
            g_lastView<I> = H;
            std::cerr << "[ga2 vrscene] " << kDiscs[I].disc << ": " << (H == Cockpit ? "cockpit" : "unit camera") << " view\n";
        }
        g_original<I, H>(rdram, ctx, runtime);
    }

    // Test aid GA2_VRSCENE_REQUEST="2@600,3@640,0@680": asks for battle view mode 2 (cockpit), 3 (unit camera),
    // 0 (tactical map), ... that many game seconds after the start (60 Hz vblanks / 59.94; the first battle frame after that), for runs
    // where the story's tutorial does not allow the L3 / R3 buttons yet.
    struct Request
    {
        int mode;
        double at;
        bool done;
    };
    std::vector<Request> &requests()
    {
        static std::vector<Request> list = [] {
            std::vector<Request> out;
            if (const char *v = std::getenv("GA2_VRSCENE_REQUEST"))
                for (const char *p = v; *p;)
                {
                    char *end = nullptr;
                    const int mode = int(std::strtol(p, &end, 10));
                    if (end == p || *end != '@')
                        break;
                    const double at = std::strtod(end + 1, &end);
                    out.push_back({mode, at, false});
                    p = *end == ',' ? end + 1 : end;
                }
            return out;
        }();
        return list;
    }

    template <size_t I>
    void mgrUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const double now = ga2_activity::gameSeconds();
        const uint32_t mgr = GPR_U32(ctx, 4);
        if (mgr)
            for (Request &r : requests())
                if (!r.done && now >= r.at)
                {
                    r.done = true;
                    const uint32_t mode = uint32_t(r.mode);
                    std::memcpy(rdram + ((mgr + kDiscs[I].requestedMode) & 0x1FFFFFFu), &mode, sizeof(mode));
                    std::cerr << "[ga2 vrscene] requested battle view mode " << r.mode << " at " << now << " s\n";
                    break;
                }
        g_original<I, MgrUpdate>(rdram, ctx, runtime);
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        const char *vr = std::getenv("GA2_VR");
        if (!vr || std::string(vr) != "1")
            return;
        const Disc &d = kDiscs[I];
        for (uint32_t address : {d.cockpitUpdate, d.cameraUpdate})
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 vrscene] " << d.disc << ": 0x" << std::hex << address << std::dec
                          << " not in the generated table; trigger stays Circle in the cockpit\n";
                return;
            }
        g_original<I, Cockpit> = runtime.lookupFunction(d.cockpitUpdate);
        runtime.replaceFunction(d.cockpitUpdate, view<I, Cockpit>);
        g_original<I, Camera> = runtime.lookupFunction(d.cameraUpdate);
        runtime.replaceFunction(d.cameraUpdate, view<I, Camera>);
        if (!requests().empty() && runtime.hasFunction(d.mgrUpdate))
        {
            g_original<I, MgrUpdate> = runtime.lookupFunction(d.mgrUpdate);
            runtime.replaceFunction(d.mgrUpdate, mgrUpdate<I>);
        }
        std::cerr << "[ga2 vrscene] " << d.disc << ": cockpit and camera view hooks bound\n";
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 VR scene zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR scene mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR scene mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR scene eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 VR scene eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
