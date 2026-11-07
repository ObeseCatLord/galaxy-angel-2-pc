// SDK bindings for the GA2 discs after Zettai. All five discs link the same
// SDK build; tools/sdk_match.py locates Zettai's verified functions in each
// executable (unique masked instruction match) and derives the SIF RPC state
// block, producing the rows below and configs/sdk-bindings-<disc>.json.
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ps2_stubs.h"
#include "ps2_syscalls.h"
#include "runtime/ee_scheduler.h"

#include <algorithm>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include <stdexcept>

namespace
{
    struct DiscSdk
    {
        const char *disc;
        const char *elf;
        uint32_t entry, crc32, rpcQueueRoot;
        uint32_t sifInitRpc, sifBindRpc, sifCallRpc, sifCheckStatRpc, cdInit;
        // CScene_DiscChange: vtable (RTTI "17CScene_DiscChange") and its
        // per-frame update method (vtable + 0x18). Found with Ghidra; the
        // binding checks the vtable slot still points at the method.
        uint32_t discChangeVtable, discChangeUpdate;
        // libkernel LoadExecPS2(name, argc, argv) (signature match of Mugen 2's).
        uint32_t loadExecPS2;
    };

    // Rows printed by tools/sdk_match.py.
    constexpr DiscSdk kDiscs[] = {
        {"mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, 0x0054FF68u, 0x0028E6A8u, 0x0028EE50u, 0x0028F030u, 0x0028F230u, 0x00266280u, 0x002F1204u, 0x0015A470u, 0x00294F98u},
        {"mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, 0x008340A8u, 0x00274568u, 0x00274D10u, 0x00274EF0u, 0x002750F0u, 0x0024E130u, 0x002D66B4u, 0x00156DD8u, 0x0027AE58u},
        {"eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, 0x0055E7E8u, 0x0029B428u, 0x0029BBD0u, 0x0029BDB0u, 0x0029BFB0u, 0x00272FF8u, 0x002FF394u, 0x0015C468u, 0x002A1D18u},
        {"eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, 0x005513E8u, 0x0028F728u, 0x0028FED0u, 0x002900B0u, 0x002902B0u, 0x002672F0u, 0x002F22C4u, 0x0015B310u, 0x00296018u},
    };

    template <auto Handler>
    void sdkCall(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        // Match generated runtime-known wrappers: return to the caller.
        ctx->pc = getRegU32(ctx, 31);
        Handler(rdram, ctx, runtime);
    }

    // The disc-change scene's update runs every frame while the game asks for
    // another disc; arming the changer lets the virtual drive cycle its tray and
    // insert the disc the game then looks for. The player never swaps discs.
    template <size_t Index>
    PS2Runtime::RecompiledFunction g_discChangeUpdate = nullptr;

    template <size_t Index>
    void discChangeUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        PS2Runtime::armDiscChanger();
        g_discChangeUpdate<Index>(rdram, ctx, runtime);
    }

    std::string guestString(const uint8_t *rdram, uint32_t address)
    {
        std::string out;
        for (uint32_t i = 0u; i < 256u; ++i)
        {
            const char c = static_cast<char>(rdram[(address + i) & 0x1FFFFFFu]);
            if (!c) break;
            out.push_back(c);
        }
        return out;
    }

    // Starting another disc's program hands over to that disc's native binary.
    void loadExecPS2(uint8_t *rdram, R5900Context *ctx, PS2Runtime *)
    {
        const std::string path = guestString(rdram, getRegU32(ctx, 4));
        const uint32_t argc = std::min<uint32_t>(getRegU32(ctx, 5), 16u), argv = getRegU32(ctx, 6);
        std::vector<std::string> args;
        for (uint32_t i = 0u; i < argc; ++i)
        {
            uint32_t pointer = 0u;
            std::memcpy(&pointer, rdram + ((argv + 4u * i) & 0x1FFFFFFu), sizeof(pointer));
            args.push_back(guestString(rdram, pointer));
        }
        if (!PS2Runtime::loadExecPS2(path, args))
            SET_GPR_S32(ctx, 2, -1);
    }

    template <size_t Index>
    void bindDisc(PS2Runtime &runtime)
    {
        const DiscSdk &sdk = kDiscs[Index];
        runtime.configureNativeEeRpcQueueRoot(sdk.rpcQueueRoot);
        // Low kernel memory hosts isolated stacks for native guest invocations.
        runtime.eeScheduler().configureInvocationStackArena(0x00060000u, 0x00070000u);
        const struct
        {
            uint32_t address;
            PS2Runtime::RecompiledFunction function;
            const char *name;
        } bindings[] = {
            {sdk.sifInitRpc, sdkCall<ps2_stubs::sceSifInitRpc>, "sceSifInitRpc"},
            {sdk.sifBindRpc, sdkCall<ps2_stubs::sceSifBindRpc>, "sceSifBindRpc"},
            {sdk.sifCallRpc, sdkCall<ps2_syscalls::sceSifCallRpc>, "sceSifCallRpc"},
            {sdk.sifCheckStatRpc, sdkCall<ps2_stubs::sceSifCheckStatRpc>, "sceSifCheckStatRpc"},
            {sdk.cdInit, sdkCall<ps2_stubs::sceCdInit>, "sceCdInit"},
            {sdk.loadExecPS2, sdkCall<loadExecPS2>, "LoadExecPS2"},
        };
        for (const auto &binding : bindings)
        {
            if (!runtime.hasFunction(binding.address) || !runtime.replaceFunction(binding.address, binding.function))
                throw std::runtime_error(std::string(sdk.disc) + ": SDK entry absent from generated table");
            std::cerr << "[GA2 SDK] " << sdk.disc << ' ' << binding.name << " at 0x" << std::hex << binding.address
                      << std::dec << '\n';
        }
        uint32_t slot = 0u;
        std::memcpy(&slot, runtime.memory().getRDRAM() + sdk.discChangeVtable + 0x18u, sizeof(slot));
        if (slot != sdk.discChangeUpdate || !runtime.hasFunction(sdk.discChangeUpdate))
            throw std::runtime_error(std::string(sdk.disc) + ": CScene_DiscChange vtable does not match");
        g_discChangeUpdate<Index> = runtime.lookupFunction(sdk.discChangeUpdate);
        runtime.replaceFunction(sdk.discChangeUpdate, discChangeUpdate<Index>);
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 SDK mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bindDisc<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 SDK mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bindDisc<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 SDK eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bindDisc<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 SDK eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bindDisc<3>)
