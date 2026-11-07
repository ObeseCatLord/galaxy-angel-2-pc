// Exact-build SDK identities verified against analyzer signatures and guest ABI.
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_stubs.h"
#include "ps2_syscalls.h"
#include "runtime/ee_scheduler.h"
#include <ps2_recompiled_functions.h>

#include <iostream>
#include <stdexcept>

namespace
{
    template <auto Handler>
    void sdkCall(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        // Match generated runtime-known wrappers, including tail-call returns
        // and host handlers which suspend through EeDispatcherTransfer.
        ctx->pc = getRegU32(ctx, 31);
        Handler(rdram, ctx, runtime);
    }

    void preserveZettaiResumeOwners(PS2Runtime &runtime)
    {
        struct Range
        {
            uint32_t first, end, ownerEntry;
            PS2Runtime::RecompiledFunction owner, generatedAlternative;
        };
        const Range ranges[] = {
            {0x00145B9Cu, 0x00145BF8u, 0x00145B98u,
             caseD_4_0x145b98, FUN_001456b8_0x1456b8},
            {0x0017E3B4u, 0x0017E400u, 0x0017E3B0u,
             caseD_2_0x17e3b0, FUN_0017e330_0x17e330},
        };
        // Preflight both complete ranges before changing any alias.
        for (const Range &range : ranges)
        {
            if (!runtime.hasFunction(range.ownerEntry) ||
                g_ps2RecompiledFunctionTable[(range.ownerEntry - g_ps2RecompiledFunctionTableBase) >> 2] != range.owner)
                throw std::runtime_error("original Zettai resume owner absent from generated table");
            for (uint32_t address = range.first; address < range.end; address += 4u)
            {
                if (!runtime.hasFunction(address))
                    throw std::runtime_error("original Zettai resume alias absent from generated table");
                const auto current = g_ps2RecompiledFunctionTable[(address - g_ps2RecompiledFunctionTableBase) >> 2];
                if (current != range.owner && current != range.generatedAlternative)
                    throw std::runtime_error("unexpected Zettai resume alias owner");
            }
        }
        for (const Range &range : ranges)
            for (uint32_t address = range.first; address < range.end; address += 4u)
                if (!runtime.replaceFunction(address, range.owner))
                    throw std::runtime_error("could not preserve original Zettai resume alias");
        std::cerr << "[GA2 resume] preserved 42 original continuation owners\n";
    }

    void bindZettaiSif(PS2Runtime &runtime)
    {
        // Original SDK Set/RemoveRpcQueue owns this live root. Interpret its
        // existing lists at the missing IOP bind boundary; retain SDK code.
        runtime.configureNativeEeRpcQueueRoot(0x00832428u);
        // Native HLE owns this range for this exact executable. Original EE
        // kernel payloads are absent; retained SDK installation claims are
        // excluded by the reviewed profile and guarded at runtime boundaries.
        runtime.eeScheduler().configureInvocationStackArena(0x00060000u, 0x00070000u);
        struct Binding
        {
            uint32_t address;
            PS2Runtime::RecompiledFunction function;
            const char *name;
        };
        const Binding bindings[] = {
            {0x00272AA8u, sdkCall<ps2_stubs::sceSifInitRpc>, "sceSifInitRpc"},
            {0x00273250u, sdkCall<ps2_stubs::sceSifBindRpc>, "sceSifBindRpc"},
            {0x00273430u, sdkCall<ps2_syscalls::sceSifCallRpc>, "sceSifCallRpc"},
            {0x00273630u, sdkCall<ps2_stubs::sceSifCheckStatRpc>, "sceSifCheckStatRpc"},
            {0x0024C680u, sdkCall<ps2_stubs::sceCdInit>, "sceCdInit"},
        };
        for (const Binding &binding : bindings)
        {
            if (!runtime.hasFunction(binding.address) ||
                !runtime.replaceFunction(binding.address, binding.function))
                throw std::runtime_error("verified Zettai SDK entry absent from generated table");
            std::cerr << "[GA2 SDK] " << binding.name << " at 0x" << std::hex
                      << binding.address << std::dec << '\n';
        }
        preserveZettaiResumeOwners(runtime);
    }
}

// SHA-256 is checked at import and launch by ga2.py/capture_boot.py. The runtime
// descriptor additionally requires this filename, entry and whole-file CRC32.
PS2_REGISTER_GAME_OVERRIDE("Zettai verified SDK", "SLPM_662.43",
                           0x00100008u, 0x5E58C415u, bindZettaiSif)
