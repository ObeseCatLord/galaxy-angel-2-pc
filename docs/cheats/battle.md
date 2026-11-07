# Battle engine and cheat hooks

Reverse-engineering notes for a cheat menu, mainly "skip this fight with the
best result". All addresses are guest (EE) addresses in the stock ELFs; the
recompiled function for guest function `X` is `FUN_00X_0xX` in
`generated52/<disc>`. Found 2026-10-14 with Ghidra decompilation of the five
ELFs (headless, read-only copies of the private projects) plus live RAM
inspection of the Mugen tutorial battle. A machine-readable copy of every
table below is `<private>/research/cheats/battle-addresses.json`.

Status legend: **verified** = observed in a running game; **static** = read
from decompiled code, consistent across discs, not exercised at runtime;
**inferred** = a reasonable reading that needs a test before shipping.

## 1. How a battle works

### Scenes

`CGalaxyAngel2SceneFactory::Create(id)` (Zettai `0x13a290`) maps scene ids to
classes. The scene controller is `*(*0x2b6640)` (Zettai) and switches scenes
through its vtable slot at `+0x14`: `ChangeScene(id, param)`.

| id | class | notes |
| --- | --- | --- |
| 1 | CScene_Title | |
| 2 | CScene_Adv | story; `param 3` = resume after a won battle |
| 3 | CScene_Slg | the battle |
| 4 | CScene_AngelSelect | sortie selection, tension display |
| 5 | CScene_Config | |
| 6 | CScene_GameOver | |
| 7 | CScene_Result | Score Attack result (score breakdown) |
| 8 / 9 / 10 / 11 | Movie / SaveLoad / Omake / SlgNowLoading | |

Mugen and Eigou add `CScene_DiscChange` and `CScene_Tutorial` (id `0xd`).

### Objects

- `CScene_Slg` (12 bytes) owns a 0x28-byte block whose word 0 is the
  **`CSLGManager`** (0x830 bytes in Zettai). A global also points at the live
  manager (`mgrGlobal` below, Zettai `0x2cca6c`); it is null outside battles.
  **Verified** on Mugen 1 (`*0x2e8fbc` = the manager whose vtable is
  `CSLGManager`'s).
- Units: `mgr + units + 4*i` (i < 0x50) point at `CUnit*` objects
  (`CUnitCrestPC` = the player's Emblem Frame, `CUnitCrest` = other angels,
  `CUnitFriend`, `CUnitEnemy`). `mgr + playerIdx` is the player's unit index.
  The vtable pointer is at unit `+8`.
- Game state: a singleton (`gameGlobal`, Zettai `0x2d3be8`) whose `+0x1c`
  points at the game-state block (Zettai 0x8b60 bytes). The save file is the
  block from `+0x134` up to the mode field (Zettai `0x134..0x8b4c` = 0x8a18
  bytes, exactly `ga2_1_NN.sav`). It holds the script variable array
  (`vars`, ints), the chapter flow progress, the current chapter/stage, the
  battle mode and the last battle's score.

### Battle state machine

`CSLGManager` vtable slot 1 (`mgrUpdate`, Zettai `0x1650b0`) runs every frame
and switches on `mgr+state`:

| state | meaning |
| --- | --- |
| 0 | load stage (`slg/stage/CCSS00/stage.tbl`), build units, then 2 |
| 2 | battle (`battleUpdate`, Zettai `0x161890`) |
| 3 | variant update |
| 5 | quit to title from the pause menu (fade, outcome 3), then 6 |
| 6 | finished; `CScene_Slg` changes scene |

In state 2, while `mgr+result == 0`, `battleUpdate` calls
**`winCheck(mgr)`** (Zettai `0x163260`) every frame (Mugen/Eigou only when
no pause/message flag is set, `FUN_0016fa20(mgr, -1) == 0` on Mugen 1):

```
r = winCheck(mgr);                 // 0 = continue, 1 = win, 2 = lose
if (r && mgr.result != 2) mgr.result = r;
if (mgr.result == 1 || mgr.result == 2) mgr.outcome = mgr.result;
if (sequencerFinished(mgr.sequencer)) mgr.state = 6;
```

`winCheck` also writes `mgr+reason`:

| reason | result | condition |
| --- | --- | --- |
| 1 | win | every unit in the target list (`targetUnits`) destroyed |
| 2 | win | no target list and no enemies left |
| 3 | win | a unit in `arriveUnits` reached the goal area |
| 4 | lose | the player's unit was destroyed (also key units) |
| 5 / 6 | lose | a key unit (`keyUnits`, stage key `KEYUNIT`) destroyed |
| 7 | lose | time limit (`ENABLE_TIMELIMIT`/`TIMELIMIT`) ran out |
| 8 | lose | an enemy in `reachUnits` reached its area |

The stage's sequence script (`OPDEMO`/`SEQFILENAME`) tests `mgr+outcome`
and `mgr+reason` (condition callbacks at Zettai `0x1bd108`/`0x1bd158`/
`0x1bd1a8`...), plays the victory or defeat demo and then reports finished,
which moves the manager to state 6.

### End of battle and return to the story

`CScene_Slg` slot 1 (`slgUpdate`, Zettai `0x148148`) waits for state 6 and
picks the next scene from `mgr+outcome` (1 win, 2 lose, 3 quit) and the battle
mode (`game+mode`: 0 story, 1/2 Score Attack and extra battles, 3 tutorial in
Mugen/Eigou):

| mode | win | lose | quit |
| --- | --- | --- | --- |
| story | Adv, param 3 | GameOver (Mugen/Eigou: Adv param 3 when `var[0xc]` != 0, i.e. a battle you may lose) | Title |
| score attack | Result | Result | Omake |
| tutorial (Mugen/Eigou) | scene 0xd | 0xd | 0xd |

The `CScene_Slg` destructor (`slgDtor`) calls **`recordResult`** (Zettai
`0x15d380`), which stores the battle score in the game state:

| field | value |
| --- | --- |
| `scoreTime` | `(timeLimit - elapsed + 0.999) * 100`, 0 without a time limit |
| `scoreHp` | `(maxHp - dmgTaken) / maxHp * 100000` of the player's unit |
| `scoreDealt` | damage the player dealt (counted only when `IS_EXBATTLE`) |
| `scoreKills` | sum of the `SCORE` values of enemy parts the player destroyed |

The total (`sum of the four`, Zettai `0x159490`) is what `CScene_Result`
shows. Mugen and Eigou also store `elapsed` and write the outcome to
`var[0x1a]`, so their scripts can branch on win/lose; Eigou 1 has battle types
selected by `var[0x31]` that pick other score sources.

`CScene_Adv` with param 3 (`0x148e40` -> `0x1fd548`) looks up the chapter flow
table (`ChapFlow`, built from the `SLG_MAX`/`R:`/`E:`/`S:` lists) for the entry
after battle `(chapter, stage)` and runs the next script. There is no rank or
experience in the story: the story only needs the win (plus `var[0x1a]` in
Mugen/Eigou). The score matters only for Score Attack / extra battles.

**Static**: everything in this section except the manager/unit layout, which
is verified on the Mugen tutorial.

## 2. Shared engine

All five discs ship the same battle engine (same classes, same functions in
the same order, same state machine and win/lose codes); addresses move and
three field layouts exist:

- **Z**: Zettai, Mugen disc 2 (the Zettai Extra Route build)
- **M**: Mugen disc 1, Eigou disc 2
- **E**: Eigou disc 1

Mugen/Eigou have six angels instead of five and two extra scenes. Matching
was done by vtable/RTTI (`__tf` type-info functions, gcc 2.9x vtables with
8-byte entries, first method at `vtable+0xc`), call graphs and masked
instruction signatures, and each function below was decompiled on its disc.

### Functions (static; every row decompiled)

| | Zettai | Mugen 1 | Mugen 2 | Eigou 1 | Eigou 2 |
| --- | --- | --- | --- | --- | --- |
| layout | Z | M | Z | E | M |
| `winCheck(mgr)` | 163260 | 16d330 | 164d00 | 170148 | 16de50 |
| `battleUpdate(dt, mgr)` | 161890 | 16b398 | 163330 | 16e0c0 | 16beb8 |
| `mgrUpdate(dt, mgr)` (vt slot 1) | 1650b0 | 16f340 | 166b50 | 172ff8 | 16fe60 |
| `applyDamage(mgr, hit*)` | 162cc8 | 16cd38 | 164768 | 16fb48 | 16d858 |
| `recordResult(mgr)` | 15d380 | 1645d0 | 15ee20 | 166c30 | 165048 |
| `CScene_Slg` ctor | 147e70 | 14ac18 | 1486d8 | 14bef0 | 14b7a0 |
| `CScene_Slg` dtor | 147f68 | 14ad58 | 1487d0 | 14c030 | 14b8e0 |
| `CScene_Slg` update (slot 1) | 148148 | 14af38 | 1489b0 | 14c210 | 14bac0 |
| scene factory | 13a290 | 13ae30 | 13aab8 | 13af90 | 13bb48 |
| `CSLGManager` vtable | 2d4df0 | 2f14e8 | 2d6938 | 2ff678 | 2f25a8 |
| `CScene_Slg` vtable | 2d47c8 | 2f0e18 | 2d62c8 | 2fefa8 | 2f1ed8 |
| `CUnitEnemy` vtable | 2d5730 | 2f1ee8 | 2d7278 | 300118 | 2f2fa8 |
| `CUnitCrestPC` vtable | 2d5278 | 2f19a0 | 2d6dc0 | 2ffb50 | 2f2a60 |
| `mgrGlobal` (CSLGManager*) | 2cca6c | 2e8fbc | 2ce58c | 2f7070 | 2ea06c |
| `gameGlobal` (+0x1c = state) | 2d3be8 | 2f01e8 | 2d56e8 | 2fe2e8 | 2f12a8 |

### CSLGManager fields

| field | Z | M | E |
| --- | --- | --- | --- |
| outcome (1 win, 2 lose, 3 quit) | 160 | 164 | 164 |
| state | 16c | 170 | 170 |
| units[0x50] | 174 | 178 | 178 |
| playerIdx | 544 | 554 | 5fc |
| result (winCheck latch) | 614 | aa4 | bb0 |
| reason | 618 | aa8 | bb4 |
| time limit enabled | 5fc | a88 | b94 |
| elapsed / limit (float s) | 604 / 608 | a90 / a94 | b9c / ba0 |
| keyUnits (vector begin/end) | 620 | ab0 | bbc |
| targetUnits | 630 | ac0 | bcc |
| arriveUnits | 640 | ad0 | bdc |
| reachUnits | 650 | ae0 | bec |

### Unit fields

| field | Z | M | E |
| --- | --- | --- | --- |
| id | 168 | 168 | 168 |
| max HP / HP / HP enabled | 1cc / 1d0 / 1d4 | 200 / 204 / 208 | 200 / 208 / 214 |
| max EN / EN / EN enabled | 1d8 / 1dc / 1e0 | 20c / 210 / 214 | 218 / 21c / 220 |
| max SP / SP / SP enabled | 1e4 / 1e8 / 1ec | 218 / 21c / 220 | 224 / 228 / 22c |
| incoming damage multiplier (float) | 284 | 2d0 | 2e4 |
| alive | 364 | 3bc | 3ec |
| damage taken / dealt / kill score | 614 / 618 / 61c | 674 / 678 / 67c | 754 / 758 / 75c |

HP is the "H·POINT" gauge (stage key `INIT_SHIELD`), EN is "ENERGY",
SP is "SPIRIT" (special attacks). Layout M was **verified** in Mugen 1's
tutorial dumps (player unit `8250/8250` HP, EN draining `10000 -> 9928 ->
9808`, SP `0 -> 100`). Eigou 1's `+0x200`/`+0x204`/`+0x20c`/`+0x210` are
**inferred** (`recordResult` uses `+0x200` as max HP, the damage code uses
`+0x208` as HP).

`applyDamage(mgr, hit)` is the single choke point for unit HP loss (called
from every weapon/projectile hit path): `hit[0]` victim index, `hit[1]` raw
damage, `hit[2]` attacker index, `hit[3]` SP gained by the attacker, `hit[4]`
hit type. It does `victim.hp -= max(1, hit[1] * victim.dmgMult)`,
`victim.dmgTaken += ...`, `attacker.dmgDealt += ...` (score attack only) and
adds SP. Large enemy ships also have destructible parts with their own HP at
part `+0x8c` and `SCORE` at `+0xe8` (Zettai `0x18f700`).

### Game state (`*(gameGlobal)+0x1c`)

| field | Zettai | Mugen 2 | Mugen 1 | Eigou 1 | Eigou 2 |
| --- | --- | --- | --- | --- | --- |
| vars (int array) | 5c54 | 5c5c | 7324 | 769c | 7328 |
| stage / chapter (u16) | 8b48 / 8b4a | 8b50 / 8b52 | 9d94 / 9d96 | aea8 / aeaa | 9d98 / 9d9a |
| battle mode | 8b4c | 8b54 | 9d9c | aeb0 | 9da0 |
| score time / HP / dealt / kills | 8b50.. | 8b58.. | 9dac.. | aec0.. | 9db0.. |

Script variables (index into `vars`), **static** from the code that reads
them:

| meaning | Zettai, Mugen 2 (5 angels) | Mugen 1, Eigou (6 angels) |
| --- | --- | --- |
| tension per angel, clamped 0..5 (AngelSelect, battle voices) | 0..4 | 0..5 |
| affection per angel | 5..9 | 6..11 |
| affection threshold per angel | 10..14 | 12..17 |
| tie-break order | 15..19 | 18..23 |
| route/variant (low byte of the stage id) | 0x14 | 0x18 |
| loss allowed in this battle | - | 0xc |
| last battle outcome | - | 0x1a |

The affection reading comes from the script helper at Zettai `0x1ec170`
(Mugen 1 `0x204f30`, Eigou 1 `0x210eb0`): it ranks angels by
`affection * 10 + tiebreak` and counts those with `affection >= threshold`.
The Zettai chapter-1 save holds affection `0,10,10,0,0`, thresholds `46` x5,
tie-break `5,4,3,2,1`, tension `2,3,3,2,2`. Which angel is index 1..5 and
the real maximum the scripts use are **not verified**.

## 3. Skip fight with the best result

Hook `winCheck` (replace the recompiled function by address, as
`native/compat/ga2_sdk.cpp` does for SDK calls). When the cheat is armed for
the current manager, return 1 (win) with the stage's natural win reason and
do not run the original; otherwise forward to the original. The game then
runs its own path: `battleUpdate` latches `result`/`outcome`, the stage
sequence plays the victory demo, state 6, `CScene_Slg` picks the story/Result
scene, the destructor runs `recordResult` (score, Mugen/Eigou `var[0x1a]`)
and the chapter flow continues. Nothing is written mid-frame from another
thread: everything happens inside the game's own call on the EE thread.

Why not post-process the original's return value: generated functions may
return to the dispatcher early at any guest call (`dispatchGuestBranch`
returns false when an EE checkpoint is due, and the call is resumed by pc
later), so hooks must do their work before forwarding, never after.

For "best result", the hook also clears the player's `dmgTaken` and refills
HP (HP bonus 100000; the time bonus is automatically the full remaining time
because the battle ends at once). Kill and damage-dealt scores only count in
Score Attack/extra battles; skipping gives 0 there. For the best Score
Attack score use invincibility + one-hit kills instead and play it out.

```cpp
// native/compat/ga2_cheats.cpp (proposal)
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include <atomic>
#include <cstring>

namespace
{
    struct Layout { uint32_t outcome, state, units, playerIdx, result, reason,
                    keyUnits, targetUnits, arriveUnits, reachUnits;
                    uint32_t uMaxHp, uHp, uMaxEn, uEn, uMaxSp, uSp, uDmgMult, uAlive, uDmgTaken; };
    constexpr Layout kZ{0x160, 0x16c, 0x174, 0x544, 0x614, 0x618, 0x620, 0x630, 0x640, 0x650,
                        0x1cc, 0x1d0, 0x1d8, 0x1dc, 0x1e4, 0x1e8, 0x284, 0x364, 0x614};
    constexpr Layout kM{0x164, 0x170, 0x178, 0x554, 0xaa4, 0xaa8, 0xab0, 0xac0, 0xad0, 0xae0,
                        0x200, 0x204, 0x20c, 0x210, 0x218, 0x21c, 0x2d0, 0x3bc, 0x674};
    constexpr Layout kE{0x164, 0x170, 0x178, 0x5fc, 0xbb0, 0xbb4, 0xbbc, 0xbcc, 0xbdc, 0xbec,
                        0x200, 0x208, 0x218, 0x21c, 0x224, 0x228, 0x2e4, 0x3ec, 0x754};

    struct Disc { const char *elf; uint32_t crc; const Layout *L;
                  uint32_t winCheck, applyDamage, mgrUpdate, slgCtor, mgrGlobal, enemyVtable; };
    constexpr Disc kDiscs[] = {
        {"SLPM_662.43", 0x5E58C415u, &kZ, 0x163260, 0x162cc8, 0x1650b0, 0x147e70, 0x2cca6c, 0x2d5730},
        {"SLPM_667.79", 0x15F1CB0Du, &kM, 0x16d330, 0x16cd38, 0x16f340, 0x14ac18, 0x2e8fbc, 0x2f1ee8},
        {"SLPM_667.80", 0x1F83C20Cu, &kZ, 0x164d00, 0x164768, 0x166b50, 0x1486d8, 0x2ce58c, 0x2d7278},
        {"SLPM_551.18", 0x73DEC964u, &kE, 0x170148, 0x16fb48, 0x172ff8, 0x14bef0, 0x2f7070, 0x300118},
        {"SLPM_551.19", 0xAAAC1E15u, &kM, 0x16de50, 0x16d858, 0x16fe60, 0x14b7a0, 0x2ea06c, 0x2f2fa8},
    };

    // Set by the cheat menu (any thread); consumed on the EE thread.
    std::atomic<bool> g_skipRequested{false};
    std::atomic<bool> g_invincible{false}, g_infiniteEnergy{false}, g_oneHitKill{false};
    uint32_t g_skipMgr = 0;          // manager the skip applies to (EE thread only)

    inline uint32_t rd32(uint8_t *ram, uint32_t a) { uint32_t v; std::memcpy(&v, ram + (a & 0x1FFFFFFu), 4); return v; }
    inline void wr32(uint8_t *ram, uint32_t a, uint32_t v) { std::memcpy(ram + (a & 0x1FFFFFFu), &v, 4); }
    inline float rdf(uint8_t *ram, uint32_t a) { float v; std::memcpy(&v, ram + (a & 0x1FFFFFFu), 4); return v; }
    inline bool vecEmpty(uint8_t *ram, uint32_t mgr, uint32_t off) { return rd32(ram, mgr + off) == rd32(ram, mgr + off + 4); }
    inline uint32_t unitAt(uint8_t *ram, const Layout &L, uint32_t mgr, uint32_t idx)
    { return idx < 0x50u ? rd32(ram, mgr + L.units + 4u * idx) : 0u; }

    template <size_t I> PS2Runtime::RecompiledFunction g_winCheck, g_applyDamage, g_mgrUpdate, g_slgCtor;

    // winCheck(a0 = CSLGManager*) -> v0: 0 continue, 1 win, 2 lose.
    template <size_t I>
    void winCheckHook(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    {
        const Layout &L = *kDiscs[I].L;
        const uint32_t mgr = getRegU32(ctx, 4);
        if (g_skipRequested.exchange(false)) g_skipMgr = mgr;
        if (mgr == 0 || mgr != g_skipMgr) { g_winCheck<I>(ram, ctx, rt); return; }   // tail call
        // The win the stage would give by itself: escort/arrival, target kill, annihilation.
        const uint32_t reason = !vecEmpty(ram, mgr, L.arriveUnits) ? 3u
                              : !vecEmpty(ram, mgr, L.targetUnits) ? 1u : 2u;
        wr32(ram, mgr + L.reason, reason);
        if (uint32_t pc = unitAt(ram, L, mgr, rd32(ram, mgr + L.playerIdx)))
        {   // best HP bonus: no damage taken, full gauges
            wr32(ram, pc + L.uDmgTaken, 0u);
            wr32(ram, pc + L.uHp, rd32(ram, pc + L.uMaxHp));
        }
        SET_GPR_U32(ctx, 2, 1u);
        ctx->pc = getRegU32(ctx, 31);          // return to the caller
    }

    // A new CScene_Slg ends any pending skip (pre-hook, then tail call).
    template <size_t I>
    void slgCtorHook(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    { g_skipMgr = 0; g_skipRequested = false; g_slgCtor<I>(ram, ctx, rt); }

    // applyDamage(a0 = mgr, a1 = hit*): edit the hit record before the game applies it.
    template <size_t I>
    void applyDamageHook(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    {
        const Disc &D = kDiscs[I]; const Layout &L = *D.L;
        const uint32_t mgr = getRegU32(ctx, 4), hit = getRegU32(ctx, 5);
        const uint32_t victim = unitAt(ram, L, mgr, rd32(ram, hit));
        if (victim && static_cast<int32_t>(rd32(ram, hit + 4)) > 0)
        {
            const bool enemy = rd32(ram, victim + 8) == D.enemyVtable;
            if (!enemy && g_invincible) wr32(ram, hit + 4, 0u);            // < 1: no HP change
            else if (enemy && g_oneHitKill)
            {
                const float mult = rdf(ram, victim + L.uDmgMult);
                const int32_t hp = static_cast<int32_t>(rd32(ram, victim + L.uHp));
                if (mult > 0.0f && hp > 0) wr32(ram, hit + 4, static_cast<uint32_t>(hp / mult) + 1u);
            }
        }
        g_applyDamage<I>(ram, ctx, rt);                                    // tail call
    }

    // mgrUpdate(f12 = dt, a0 = mgr): refill the player's gauges once per frame.
    template <size_t I>
    void mgrUpdateHook(uint8_t *ram, R5900Context *ctx, PS2Runtime *rt)
    {
        const Layout &L = *kDiscs[I].L;
        const uint32_t mgr = getRegU32(ctx, 4);
        if (g_infiniteEnergy)
            if (uint32_t pc = unitAt(ram, L, mgr, rd32(ram, mgr + L.playerIdx)))
            {
                wr32(ram, pc + L.uEn, rd32(ram, pc + L.uMaxEn));
                wr32(ram, pc + L.uSp, rd32(ram, pc + L.uMaxSp));
            }
        g_mgrUpdate<I>(ram, ctx, rt);                                      // tail call
    }

    template <size_t I>
    void bind(PS2Runtime &rt)
    {
        const Disc &D = kDiscs[I];
        g_winCheck<I> = rt.lookupFunction(D.winCheck);       rt.replaceFunction(D.winCheck, winCheckHook<I>);
        g_applyDamage<I> = rt.lookupFunction(D.applyDamage); rt.replaceFunction(D.applyDamage, applyDamageHook<I>);
        g_mgrUpdate<I> = rt.lookupFunction(D.mgrUpdate);     rt.replaceFunction(D.mgrUpdate, mgrUpdateHook<I>);
        g_slgCtor<I> = rt.lookupFunction(D.slgCtor);         rt.replaceFunction(D.slgCtor, slgCtorHook<I>);
    }
}
// PS2_REGISTER_GAME_OVERRIDE("GA2 cheats <disc>", elf, 0x00100008u, crc, bind<I>) per disc,
// or call bind<I>() from the existing bindDisc<I>() in ga2_sdk.cpp.
```

Notes for the implementation:

- Check that each address is a generated function (`rt.hasFunction`) and,
  like `bindDisc` does for the disc changer, that a vtable slot still points
  at the method (`mgrUpdate` = `CSLGManager` vtable `+0x14`), so a wrong disc
  build fails loudly instead of patching random code.
- In Mugen/Eigou `winCheck` is skipped while a pause/message flag is set, so
  "Skip" pressed then takes effect as soon as control returns. Zettai calls
  it on every `battleUpdate`; arming during the opening demo is untested
  (the sequence script may only test the outcome after the demo). The menu entry should only be enabled while
  `*mgrGlobal != 0` and `mgr+state == 2`.
- Do not arm the skip in mode 3 (Mugen/Eigou tutorial): the tutorial's
  sequence script ignores the outcome (see verification).
- The Score Attack result could also be maxed by writing the score fields in
  the game state after `recordResult`, but that needs a post-hook on the
  destructor; prefer playing it out with invincibility + one-hit kills.
- Zettai's SDK bindings live in `native/compat/zettai_sif.cpp` (same
  entry/CRC pair as above); the other four discs can reuse `bindDisc<I>`.

## 4. Other cheats

| cheat | how | status |
| --- | --- | --- |
| invincible (all friendly units) | `applyDamage` pre-hook: zero `hit[1]` when the victim's vtable is not `CUnitEnemy` | static |
| infinite EN / SP | `mgrUpdate` pre-hook: `EN = maxEN`, `SP = maxSP` of the player unit (or all friendly units) | layout verified (M), hook static |
| one-hit kills | `applyDamage` pre-hook for enemy victims: `hit[1] = HP / mult + 1`; ship parts (`+0x8c`) take damage through a separate path (Zettai `0x18f700`), so bosses with parts need that too | inferred |
| stop the time limit | write `mgr+elapsed` = 0 each frame (or `timeEnabled` = 0) | static |
| max tension | `vars[tension]` = 5 for every angel before AngelSelect | inferred (range from the clamp in AngelSelect; effect on battle not traced) |
| max affection | `vars[affection i]` >= `vars[threshold i]` (thresholds are 46 in the chapter-1 save); raising all can change which route/ending the scripts pick, so offer per angel | inferred |
| unlock all (gallery, Score Attack stages) | lives in `ga2_1_sys.sav` (312 bytes); fields not mapped | not done |

Writing `vars` is safe at any time the ADV scene is idle (they are plain ints
saved with the game); apply them from the menu on the EE thread (e.g. inside
one of the hooks or a per-frame runtime callback), not from the UI thread.

## 5. Verification

Evidence and tools are in `<private>/research/cheats/`: `decomp/` (Ghidra
C for every function of Zettai, Mugen 1 and Eigou 1), `tools/` (ELF/RTTI
helpers, cross-disc signature matcher `xmatch.py`, `dat.sh` + `DecompAt.java`
for decompiling single addresses headlessly from a copy of a Ghidra project),
`runs/` (ramtool logs, one Mugen tutorial RAM image `mt1/ram-270.bin`).
`ramtool.py` wraps a run (`GA2_WRAP='python3
.../ramtool.py --'`), finds guest RAM inside the engine process (the parent
may read and write `/proc/<child>/mem` under Yama scope 1), and runs timed or
live commands: dumps, writes, freezes, watches, and triggers relative to a
guest pointer (e.g. "45 s after `*0x2e8fbc` has state 2, write the result
fields"). It only stands in for the proposed hooks; nothing in the port uses
it.

- Mugen 1 tutorial (`keys-mtut3`, runs `~/ga2-cheats-re/runs/mt1`, `mt3`):
  manager at `0xf3ed70` = `*0x2e8fbc`, state 2, units 0 (`CUnitFriend`,
  flagship 60000 HP), 1 (`CUnitCrestPC`, the player, playerIdx 1), 2-5
  (`CUnitCrest`), 10 (`CUnitEnemy`, 60000 HP); gauges as in section 2.
  Writing `result = 1, reason = 2, outcome = 1` (what the hook produces)
  during the tutorial did **not** end it: the tutorial (mode 3) runs its own
  lesson script that never checks the outcome, and the battle continued.
  The tutorial ends with state 6 and outcome 0 at ~330 s, after which this
  build stops drawing (0 GS packets, EE thread waiting at `0x134b38`); the
  same happens without any cheat (`research/run56/mtutk`), so it is an
  existing port issue on the tutorial exit, not caused by the cheat.
- Story battles: not reached. Mugen 1 New Game with Circle every 1.2 s
  plus manual ship-map navigation reached the end of chapter 1 ("to be
  Continued", ~28 min) without a battle; the following movie stopped the
  engine with `Error during program execution: IPU pump did not settle`
  (an existing movie/IPU issue at the chapter 1 -> 2 transition, no cheat
  write had happened). Eigou 1 New Game ran ~35 min of story (ceremony
  scenes) without a battle before the run ended. Zettai's first battle is
  after the ship-map section (needs navigation from the `mc-zettai-map`
  save). A save placed at a battle on each disc is the next step.

### In-game check of the hooks (Zettai Stage 1-1, 2026-10-15)

Build of 2026-10-14 13:33 (all discs regenerated) with
`native/compat/ga2_cheats.cpp` and the F1 cheat menu, started from the
`mc-zettai-prebattle` fixture (section 6). Evidence (screenshots and
ramtool logs) in `<private>/research/cheats/verify/`.

| cheat | result | evidence |
| --- | --- | --- |
| menu | opens with F1 over the battle, toggles show ON/OFF | `zettai-menu.png`, `zettai-menu-toggles.png` |
| skip battle | **works**: `result = 1, reason = 1` (target destroyed), `outcome = 1` on the next frame; the victory demo plays, state 6 after ~37 s, story resumes in the hangar (`a010_0012`). Recorded score: time 100, HP 100000 (maximum), dealt 0, kills 0; stage 1-1, mode 0 | `zettai-skip-1351.png`, `zettai-after-skip-hangar.png`, `zettai-cz1-ramtool.log` (t=1350-1425) |
| infinite energy and spirit | **works**: EN back to max 12000 and SP to 10000 within a frame of enabling, stays there | `zettai-inv-en-on.png`, `zettai-cz2-ramtool.log` (t=1091) |
| invincible | **works**: with it on from the battle start, the player was hit 7 times (SP +55 per hit, `watch psp`) and HP stayed 7200; without it the same hits cost 45 HP each (cz2: 7200 -> 7092) | `zettai-invincible-on.png`, `zettai-invincible-fullhp-after-hits.png`, `zettai-cz3-ramtool.log` (t=557-562) |
| one-hit kills | **not verified**: in three runs the player never damaged the pirate ship (enemy HP 18000 constant; the scripted cockpit view of Stage 1-1 is hard to aim with scripted input) | - |
| stop the time limit | **not verified**: Stage 1-1 has no time limit (`timeEnabled = 0`; the elapsed counter is also paused, `mgr+0x600 = 1`) | - |

Issue found: activating a menu row with **Enter** also reaches the game as
Start on the frame the menu closes: Zettai showed its PAUSE label after
"Skip" (`zettai-skip-1351.png`, top right). The skip still completed. The
menu should swallow keys/buttons that are down when it closes until they
are released (runtime `ga2_cheat_menu.cpp`).

Fixed in runtime 0076 and re-checked 2026-10-18 (`research/run57/zskip2`):
F1 + Enter on Skip at the battle's opening tutorial question shows
"Battle skipped" and no PAUSE label. The skip waits while the tutorial
question is up (the battle is paused there); after answering it (いいえ),
the battle ran ~25 s, then the victory scene played and the story resumed
in the hangar (カズヤ: ふう。やっぱり実戦は違うな…).

Re-verified 2026-10-19 with the cheat snapshot binary and a fresh copy of
the `mc-zettai-prebattle` fixture (`research/run57/cheatskip`): battle HUD
at ~425 s, F1 at 434 s, Enter on "Skip this battle" at 444 s ("Battle
skipped", no PAUSE label). The opening tutorial lines kept playing; after
the question was answered (大体わかりました) the battle started at ~515 s,
the victory scene (Luxiole and the pirate ship) played at ~545-551 s, and
the story resumed in the hangar at ~563 s (アプリコット: シラナミさん、お疲れさまで…,
then the ナノナノ/カルーア/タクト dialogue about the first victory); the
run was left going to ~866 s at 100% speed with Circle every 2 s, no errors
beyond the boot-time unimplemented-syscall and SCREAM lines that every run
prints.

No address or hook fix was needed for Zettai. Enemy detection by the
`CUnitEnemy` vtable is right: units from `ENEMY` stage sections are always
created as `CUnitEnemy` (Zettai `0x162000`), and friendly special units
(ids 0x33-0x38) use other classes.

Not verified yet: Mugen and Eigou in a story battle (no pre-battle fixture
yet), Score Attack, affection/tension effects.

## 6. Fixtures

Memory-card fixtures that stop just before a story battle (copy, then run
with `GA2_MC_ROOT=<copy>`; never run on the fixture itself).

### Zettai: `<private>/research/fixtures/mc-zettai-prebattle`

- Slot 3 ("1章 入隊、ルーンエンジェル ACT.4"): the briefing room just before
  Stage 1-1 (海賊船来襲, pilot Apricot). Slots 1-2 are the old ship-map
  saves (slot 2 = after visiting every room of the ship map).
- Battle starts ~7.8 min after boot with
  `<private>/research/cheats/fixtures/keys-zettai-prebattle.json`:
  `[[26,"c"],[36,"Down"],[37,"Down"],[39,"c"],[42,"Left"],[43,"c"],[100,"v"],[400,"c"],[470,"c"]]`
  (title -> つづきから -> slot 3 -> はい; Triangle turns on Auto so the
  briefing and the hangar launch movie play without input; c at 400 s picks
  Apricot at ANGEL SELECT, c at 470 s starts from the STAGE1-1 screen).
  Verified three times (`*0x2cca6c` state 2 at ~471 s). Pressing Circle
  during the launch movie aborted the engine once with `IPU pump did not
  settle` (old build); the script avoids input there.
- How it was made: from `mc-zettai-map` the story continues only after every
  red room of the ship map has been visited (Block A briefing room; Block C
  tea lounge, convenience store, training room, recreation lounge, cafeteria,
  galaxy park; Block D simulator, hangar, lab, medical room, engine room,
  storage; Block B Apricot's room, Kazuya's room), then the bridge (Block A)
  starts the briefing script `a010_0009`. R1/R2 change blocks, the d-pad
  moves the cursor, Circle enters.
- First battle facts: manager at `0x1f9cce0`, player unit 1 (`0xf3ee40`,
  7200 HP, 12000 EN), Luxiole unit 0 (60000 HP), pirate ship unit 10
  (`0xfd7850`, 18000 HP); target list `[10]`, key units `[0, 1]`, no time
  limit. The battle opens with a tutorial question (はい/いいえ).

### Mugen 1, Eigou 1

Not made yet (see `<private>/research/cheats/fixtures.md` for progress).

### Reproducing the two port problems

- **IPU pump did not settle** (engine exits): seen at the Mugen 1 chapter
  1 -> 2 movie (~28 min into New Game, research run `mnew2`, frame 1702)
  and at Zettai's hangar launch movie when Circle was pressed during it
  (run `zt1`, ~250 s with the Zettai fixture and Circle every second from
  65 s). Shortest repro: Zettai fixture, keys
  `[[26,"c"],[36,"Down"],[37,"Down"],[39,"c"],[42,"Left"],[43,"c"]]` plus
  Circle every 1 s from 65 s to 260 s (old build of 2026-10-14 12:13; not
  retried on the rebuilt engines).
- **Mugen tutorial stops drawing after it ends**: `research/run56/keys-mtut3.json`
  on Mugen 1 (title -> チュートリアル); the tutorial battle ends at ~330 s
  (state 6, outcome 0) and the picture freezes (0 GS packets, EE waiting at
  `0x134b38`). Not retried on the rebuilt engines.
