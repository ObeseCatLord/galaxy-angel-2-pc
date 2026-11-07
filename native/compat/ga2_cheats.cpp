// Battle cheats (docs/cheats/battle.md): the in-game cheat menu sets the
// switches in runtime/ga2_cheats.h; these pre-hooks on the battle engine
// (shared by all five discs, three field layouts) act on them on the EE
// thread, inside the game's own calls. Hooks only work before forwarding to
// the original: recompiled functions may return to the dispatcher early.
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "runtime/ga2_cheats.h"

#include <cstring>
#include <iostream>

namespace
{
    struct Layout
    {
        uint32_t outcome, state, units, playerIdx, result, reason, keyUnits, targetUnits, arriveUnits, reachUnits;
        uint32_t timeEnabled, elapsed;
        uint32_t uMaxHp, uHp, uMaxEn, uEn, uMaxSp, uSp, uDmgMult, uAlive, uDmgTaken;
    };
    constexpr Layout kZ{0x160, 0x16c, 0x174, 0x544, 0x614, 0x618, 0x620, 0x630, 0x640, 0x650, 0x5fc, 0x604,
                        0x1cc, 0x1d0, 0x1d8, 0x1dc, 0x1e4, 0x1e8, 0x284, 0x364, 0x614};
    constexpr Layout kM{0x164, 0x170, 0x178, 0x554, 0xaa4, 0xaa8, 0xab0, 0xac0, 0xad0, 0xae0, 0xa88, 0xa90,
                        0x200, 0x204, 0x20c, 0x210, 0x218, 0x21c, 0x2d0, 0x3bc, 0x674};
    constexpr Layout kE{0x164, 0x170, 0x178, 0x5fc, 0xbb0, 0xbb4, 0xbbc, 0xbcc, 0xbdc, 0xbec, 0xb94, 0xb9c,
                        0x200, 0x208, 0x218, 0x21c, 0x224, 0x228, 0x2e4, 0x3ec, 0x754};

    struct Disc
    {
        const char *disc, *elf;
        uint32_t entry, crc;
        const Layout *layout;
        uint32_t winCheck, applyDamage, mgrUpdate, slgCtor, managerGlobal, enemyVtable, managerVtable;
    };
    constexpr Disc kDiscs[] = {
        {"zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, &kZ, 0x163260, 0x162cc8, 0x1650b0, 0x147e70, 0x2cca6c, 0x2d5730, 0x2d4df0},
        {"mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, &kM, 0x16d330, 0x16cd38, 0x16f340, 0x14ac18, 0x2e8fbc, 0x2f1ee8, 0x2f14e8},
        {"mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, &kZ, 0x164d00, 0x164768, 0x166b50, 0x1486d8, 0x2ce58c, 0x2d7278, 0x2d6938},
        {"eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, &kE, 0x170148, 0x16fb48, 0x172ff8, 0x14bef0, 0x2f7070, 0x300118, 0x2ff678},
        {"eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, &kM, 0x16de50, 0x16d858, 0x16fe60, 0x14b7a0, 0x2ea06c, 0x2f2fa8, 0x2f25a8},
    };

    uint32_t rd32(const uint8_t *ram, uint32_t a)
    {
        uint32_t v = 0;
        std::memcpy(&v, ram + (a & 0x1FFFFFFu), 4);
        return v;
    }
    void wr32(uint8_t *ram, uint32_t a, uint32_t v) { std::memcpy(ram + (a & 0x1FFFFFFu), &v, 4); }
    float rdf(const uint8_t *ram, uint32_t a)
    {
        float v = 0.0f;
        std::memcpy(&v, ram + (a & 0x1FFFFFFu), 4);
        return v;
    }
    bool vecEmpty(const uint8_t *ram, uint32_t mgr, uint32_t off) { return rd32(ram, mgr + off) == rd32(ram, mgr + off + 4); }
    uint32_t unitAt(const uint8_t *ram, const Layout &L, uint32_t mgr, uint32_t idx)
    {
        return idx < 0x50u ? rd32(ram, mgr + L.units + 4u * idx) : 0u;
    }

    enum Hook { WinCheck, ApplyDamage, MgrUpdate, SlgCtor };
    template <size_t I, int H>
    PS2Runtime::RecompiledFunction g_original = nullptr;
    template <size_t I>
    uint32_t g_skipManager = 0; // the battle a skip applies to (EE thread)

    // winCheck(a0 = manager) -> v0: 0 continue, 1 win, 2 lose.
    template <size_t I>
    void winCheck(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    {
        const Layout &L = *kDiscs[I].layout;
        const uint32_t mgr = getRegU32(ctx, 4);
        if (ga2_cheats::state().skipRequested.exchange(false))
            g_skipManager<I> = mgr;
        if (!mgr || mgr != g_skipManager<I>)
        {
            g_original<I, WinCheck>(ram, ctx, rt);
            return;
        }
        // The win the stage would give by itself: arrival, target kill or annihilation.
        const uint32_t reason = !vecEmpty(ram, mgr, L.arriveUnits) ? 3u : !vecEmpty(ram, mgr, L.targetUnits) ? 1u : 2u;
        wr32(ram, mgr + L.reason, reason);
        if (const uint32_t player = unitAt(ram, L, mgr, rd32(ram, mgr + L.playerIdx)))
        {
            // Best result: no damage taken (full HP bonus); the time bonus is
            // the whole remaining time because the battle ends now.
            wr32(ram, player + L.uDmgTaken, 0u);
            wr32(ram, player + L.uHp, rd32(ram, player + L.uMaxHp));
        }
        SET_GPR_U32(ctx, 2, 1u);
        ctx->pc = getRegU32(ctx, 31);
    }

    // A new battle scene drops any pending skip.
    template <size_t I>
    void slgCtor(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    {
        g_skipManager<I> = 0;
        ga2_cheats::state().skipRequested = false;
        g_original<I, SlgCtor>(ram, ctx, rt);
    }

    // applyDamage(a0 = manager, a1 = hit {victim, damage, attacker, sp, type}).
    template <size_t I>
    void applyDamage(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    {
        const Disc &D = kDiscs[I];
        const Layout &L = *D.layout;
        ga2_cheats::State &s = ga2_cheats::state();
        if (s.invincible || s.oneHitKill)
        {
            const uint32_t mgr = getRegU32(ctx, 4), hit = getRegU32(ctx, 5);
            const uint32_t victim = unitAt(ram, L, mgr, rd32(ram, hit));
            if (victim && int32_t(rd32(ram, hit + 4)) > 0)
            {
                const bool enemy = rd32(ram, victim + 8u) == D.enemyVtable; // vtable pointer at +8
                if (!enemy && s.invincible)
                    wr32(ram, hit + 4, 0u); // below 1: no HP change
                else if (enemy && s.oneHitKill)
                {
                    const float mult = rdf(ram, victim + L.uDmgMult);
                    const int32_t hp = int32_t(rd32(ram, victim + L.uHp));
                    if (mult > 0.0f && hp > 0)
                        wr32(ram, hit + 4, uint32_t(float(hp) / mult) + 1u);
                }
            }
        }
        g_original<I, ApplyDamage>(ram, ctx, rt);
    }

    // mgrUpdate(f12 = dt, a0 = manager): per-frame refills.
    template <size_t I>
    void mgrUpdate(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    {
        const Layout &L = *kDiscs[I].layout;
        ga2_cheats::State &s = ga2_cheats::state();
        const uint32_t mgr = getRegU32(ctx, 4);
        if (mgr && s.infiniteEnergy)
            if (const uint32_t player = unitAt(ram, L, mgr, rd32(ram, mgr + L.playerIdx)))
            {
                wr32(ram, player + L.uEn, rd32(ram, player + L.uMaxEn));
                wr32(ram, player + L.uSp, rd32(ram, player + L.uMaxSp));
            }
        if (mgr && s.stopTimer && rd32(ram, mgr + L.timeEnabled))
            wr32(ram, mgr + L.elapsed, 0u);
        g_original<I, MgrUpdate>(ram, ctx, rt);
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        const Disc &D = kDiscs[I];
        for (uint32_t address : {D.winCheck, D.applyDamage, D.mgrUpdate, D.slgCtor})
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 cheats] " << D.disc << ": 0x" << std::hex << address << std::dec
                          << " not in the generated table; cheats off\n";
                return;
            }
        // The battle manager's vtable must still hold its update method.
        bool vtableMatches = false;
        for (uint32_t slot = 0; slot < 8u; ++slot)
            vtableMatches = vtableMatches || rd32(runtime.memory().getRDRAM(), D.managerVtable + 4u * slot) == D.mgrUpdate;
        if (!vtableMatches)
        {
            std::cerr << "[ga2 cheats] " << D.disc << ": battle manager vtable does not match; cheats off\n";
            return;
        }
        auto hook = [&](uint32_t address, PS2Runtime::RecompiledFunction &original, PS2Runtime::RecompiledFunction replacement) {
            original = runtime.lookupFunction(address);
            runtime.replaceFunction(address, replacement);
        };
        hook(D.winCheck, g_original<I, WinCheck>, winCheck<I>);
        hook(D.applyDamage, g_original<I, ApplyDamage>, applyDamage<I>);
        hook(D.mgrUpdate, g_original<I, MgrUpdate>, mgrUpdate<I>);
        hook(D.slgCtor, g_original<I, SlgCtor>, slgCtor<I>);
        ga2_cheats::State &s = ga2_cheats::state();
        s.managerGlobal = D.managerGlobal;
        s.stateOffset = D.layout->state;
        s.available = true;
        std::cerr << "[ga2 cheats] " << D.disc << ": battle cheats ready\n";
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 cheats zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 cheats mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 cheats mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 cheats eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 cheats eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
