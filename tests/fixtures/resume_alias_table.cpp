#include <cstdint>
#include <vector>
#include <stdexcept>
#include <iostream>
#include <cassert>
struct PS2Runtime { using RecompiledFunction = void(*)(); int replacements=0; bool hasFunction(uint32_t) const; bool replaceFunction(uint32_t,RecompiledFunction); };
volatile int marker=0;
void caseD_4_0x145b98(){marker=1;} void FUN_001456b8_0x1456b8(){marker=2;}
void caseD_2_0x17e3b0(){marker=3;} void FUN_0017e330_0x17e330(){marker=4;} void unexpected(){marker=5;}
const uint32_t g_ps2RecompiledFunctionTableBase=0x145B98;
std::vector<PS2Runtime::RecompiledFunction> g_ps2RecompiledFunctionTable((0x17E400-0x145B98)/4);
bool PS2Runtime::hasFunction(uint32_t a) const {return a>=g_ps2RecompiledFunctionTableBase && a<0x17E400 && !(a&3) && g_ps2RecompiledFunctionTable[(a-g_ps2RecompiledFunctionTableBase)/4]!=nullptr;}
bool PS2Runtime::replaceFunction(uint32_t a,RecompiledFunction f){if(a<g_ps2RecompiledFunctionTableBase || a>=0x17E400 || (a&3))return false;g_ps2RecompiledFunctionTable[(a-g_ps2RecompiledFunctionTableBase)/4]=f;++replacements;return true;}
void setup(){std::fill(g_ps2RecompiledFunctionTable.begin(),g_ps2RecompiledFunctionTable.end(),nullptr);g_ps2RecompiledFunctionTable[0]=caseD_4_0x145b98;g_ps2RecompiledFunctionTable[(0x17E3B0-g_ps2RecompiledFunctionTableBase)/4]=caseD_2_0x17e3b0;for(uint32_t a=0x145B9C;a<0x145BF8;a+=4)g_ps2RecompiledFunctionTable[(a-g_ps2RecompiledFunctionTableBase)/4]=FUN_001456b8_0x1456b8;for(uint32_t a=0x17E3B4;a<0x17E400;a+=4)g_ps2RecompiledFunctionTable[(a-g_ps2RecompiledFunctionTableBase)/4]=FUN_0017e330_0x17e330;}
#include "production_resume_helper.inc"

int main(){
 setup();PS2Runtime runtime;auto initial=g_ps2RecompiledFunctionTable;preserveZettaiResumeOwners(runtime);assert(runtime.replacements==42);int changed=0;for(size_t i=0;i<initial.size();++i)if(initial[i]!=g_ps2RecompiledFunctionTable[i])++changed;assert(changed==42);auto correct=g_ps2RecompiledFunctionTable;preserveZettaiResumeOwners(runtime);assert(runtime.replacements==84 && correct==g_ps2RecompiledFunctionTable);
 for(int test=0;test<4;++test){setup();uint32_t address=test<2?0x17E3B0:0x17E3FC;g_ps2RecompiledFunctionTable[(address-g_ps2RecompiledFunctionTableBase)/4]=(test%2==0)?nullptr:unexpected;auto before=g_ps2RecompiledFunctionTable;PS2Runtime candidate;bool threw=false;try{preserveZettaiResumeOwners(candidate);}catch(const std::runtime_error&){threw=true;}assert(threw && candidate.replacements==0 && before==g_ps2RecompiledFunctionTable);}
 std::cout<<"6 host-table fixture scenarios pass:exact42changes,idempotence,missing/unexpectedowner,missing/unexpectedlastalias; no guest execution\n";
}
