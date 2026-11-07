// Diagnostic only: actual native host/media boundaries without game execution.
#include "ps2_runtime.h"
#include "ps2_stubs.h"
#include "ps2_iop_host.h"
#include "ps2_disc_mount.h"
#include "emulator/core/iop_cpu.h"
#include "emulator/core/iop_kernel.h"
#include "emulator/core/iop_memory.h"
#include "emulator/imports/iop_cdvd.h"
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace
{
    void require(bool condition, const char *message)
    { if (!condition) throw std::runtime_error(message); }
    std::vector<uint8_t> read(const std::filesystem::path &path)
    {
        const auto size = std::filesystem::file_size(path);
        require(size != 0u && size <= 16u*1024u*1024u, "expected payload outside probe bounds");
        std::vector<uint8_t> data(size); std::ifstream file(path, std::ios::binary);
        require(bool(file.read(reinterpret_cast<char *>(data.data()), data.size())), "short expected payload");
        return data;
    }
}

int main(int argc, char **argv)
{
    try
    {
        require(argc == 7, "usage: ga2_disc_probe ISO MANIFEST ISO_SHA256 MANIFEST_SHA256 GUEST_FILE EXPECTED_FILE");
        PS2Runtime runtime;
        PS2Runtime::IoPaths paths; paths.cdImage=std::filesystem::absolute(argv[1]).lexically_normal();
        PS2Runtime::setIoPaths(paths);
        std::string error;
        const bool mounted = ps2_disc::mount(paths.cdImage, argv[2], argv[3], argv[4], error);
        require(mounted, error.c_str());
        PS2IopHostAdapter host(runtime); ps2_disc::File entry;
        require(ps2_disc::findFile(argv[5],entry) && entry.extents.size()==1u,"original file is not a single extent");
        const auto expected=read(argv[6]); require(entry.size==expected.size(),"file size differs from imported source");
        const auto handle=host.openGuestFile(argv[5]); require(handle!=0u,"native host could not open original ISO file");
        std::vector<uint8_t> actual(expected.size(),0xCCu); size_t bytes=0u;
        const bool fileRead=host.readHostFile(handle,0u,actual.data(),actual.size(),bytes);
        host.closeHostFile(handle);
        require(fileRead && bytes==expected.size() && actual==expected,"native logical file does not match original import");

        std::vector<uint8_t> ram(PS2_RAM_SIZE,0xCCu); R5900Context ee{};
        require(std::strlen(argv[5])<1024u,"guest path too long"); std::strcpy(reinterpret_cast<char *>(ram.data()+0x1000u),argv[5]);
        ee.r[4]=_mm_set_epi64x(0,0x2000u); ee.r[5]=_mm_set_epi64x(0,0x1000u);
        ps2_stubs::sceCdSearchFile(ram.data(),&ee,&runtime); require(getRegU32(&ee,2)==1u,"EE original file search failed");
        uint32_t eeLba=0u; std::memcpy(&eeLba,ram.data()+0x2000u,4u);
        require(eeLba==entry.extents.front().lba,"EE file search disagrees with imported LBA");

        using namespace ps2x::iop::detail;
        IopMemory memory; IopKernel kernel(memory); IopCdvd cdvd(host,memory,kernel);
        const uint32_t name=memory.allocate(1024u),result=memory.allocate(32u),destination=memory.allocate(2048u);
        require(name && result && destination,"diagnostic IOP allocation failed");
        require(memory.writeRam(name,argv[5],std::strlen(argv[5])+1u),"diagnostic path write failed");
        IopCpuState cpu{}; cpu.gpr[4]=result; cpu.gpr[5]=name;
        require(cdvd.dispatchImport(10u,cpu) && cpu.gpr[2]==1u,"IOP original file search failed");
        require(memory.read32(result)==eeLba,"IOP and EE real LBAs disagree");
        cpu.gpr[4]=eeLba; cpu.gpr[5]=1u; cpu.gpr[6]=destination; cpu.gpr[7]=0u;
        require(cdvd.dispatchImport(6u,cpu) && cpu.gpr[2]==1u,"IOP physical sector read failed");
        std::array<uint8_t,2048> sector{};
        require(memory.readRam(destination,sector.data(),sector.size()),"IOP result read failed");
        std::ifstream image(paths.cdImage,std::ios::binary); image.seekg(uint64_t(eeLba)*2048u);
        std::array<uint8_t,2048> source{}; require(bool(image.read(reinterpret_cast<char *>(source.data()),source.size())),"source sector short");
        require(sector==source,"IOP search/read extent disagrees with raw ISO sector");
        std::cout << "verified_mount=1 logical_file_match=1 ee_iop_lba_match=1 iop_raw_sector_match=1 lba=" << eeLba << " size=" << entry.size << '\n';
        return 0;
    }
    catch(const std::exception &error) { std::cerr << error.what() << '\n'; return 1; }
}
