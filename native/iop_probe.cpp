#include "ps2x/iop/iop_subsystem.h"

#include <algorithm>
#include <array>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <vector>

using namespace ps2x::iop;

// A bounded diagnostic host, not a replacement game runtime. Unsupported
// peripherals fail; module IDs alone are never treated as service readiness.
class ProbeHost final : public IopHost
{
public:
    explicit ProbeHost(std::string image) : image_(std::move(image)), guest_(32u << 20u) {}
    IopSubsystem *iop = nullptr;
    void serveModule(std::string guestPath, const std::filesystem::path &file)
    {
        if (guestPath.empty() || guestPath.size() >= 252u)
            throw std::runtime_error("served guest module path outside RPC bounds");
        const auto path = std::filesystem::canonical(file);
        const auto size = std::filesystem::file_size(path);
        if (size < 52u || size > (16u << 20u))
            throw std::runtime_error("served IRX outside probe bounds");
        modules_.emplace(std::move(guestPath), path.string());
    }
    bool normalizeGuestAddress(uint32_t address, uint32_t &normalized) const override
    {
        const auto segment = address & 0xE0000000u;
        if (segment != 0 && segment != 0x80000000u && segment != 0xA0000000u)
            return false;
        normalized = address & 0x1FFFFFFFu;
        return normalized < guest_.size();
    }
    bool readGuest(uint32_t address, void *dst, size_t size) const override
    {
        uint32_t offset;
        if (!range(address, size, offset) || (size && !dst)) return false;
        if (size) std::memcpy(dst, guest_.data() + offset, size);
        return true;
    }
    bool writeGuest(uint32_t address, const void *src, size_t size) override
    {
        uint32_t offset;
        if (!range(address, size, offset) || (size && !src)) return false;
        if (size) std::memcpy(guest_.data() + offset, src, size);
        return true;
    }
    bool zeroGuest(uint32_t address, size_t size) override
    {
        uint32_t offset;
        if (!range(address, size, offset)) return false;
        std::fill_n(guest_.begin() + offset, size, uint8_t{});
        return true;
    }
    bool readIopMemory(uint32_t a, void *p, size_t n) const override
    { return iop && iop->readMemory(a, p, n); }
    bool writeIopMemory(uint32_t a, const void *p, size_t n) override
    { return iop && iop->writeMemory(a, p, n); }
    bool zeroIopMemory(uint32_t a, size_t n) override
    { return iop && iop->zeroMemory(a, n); }
    bool normalizeIopAddress(uint32_t a, uint32_t &out) const override
    { out = a & 0x1FFFFFFFu; return iop && iop->isMemoryRange(out, 1); }
    uint32_t allocateIopHandle(IopHandleKind) override
    { return iop ? iop->allocateMemory(128, 16) : 0; }
    uint32_t allocateGuest(uint32_t, uint32_t) override
    { log(LogLevel::Warning, "probe: EE allocation unsupported"); return 0; }
    void freeGuest(uint32_t) override {}
    void audioCommand(uint32_t, uint32_t, GuestBuffer, GuestBuffer) override
    { log(LogLevel::Warning, "probe: audio unsupported"); }
    std::string hostPath(HostPathKind kind) const override
    { return kind == HostPathKind::CdImage ? image_ : std::string{}; }
    std::string translateGuestPath(std::string_view path) const override
    {
        const auto module = modules_.find(std::string(path));
        return module == modules_.end() ? std::string{} : module->second;
    }
    uint64_t openHostFile(std::string_view path) override
    {
        // Only explicitly supplied image/module paths can be opened.
        const bool module = std::any_of(modules_.begin(), modules_.end(),
                                      [&](const auto &item) { return item.second == path; });
        if (!module && (image_.empty() || path != image_)) return 0;
        auto stream = std::make_unique<std::ifstream>(std::string(path), std::ios::binary);
        if (!*stream) return 0;
        const auto handle = ++nextHandle_;
        files_.emplace(handle, std::move(stream));
        paths_.emplace(handle, std::string(path));
        return handle;
    }
    bool hostFileSize(uint64_t handle, uint64_t &size) const override
    {
        if (!files_.contains(handle)) return false;
        std::error_code error;
        size = std::filesystem::file_size(paths_.at(handle), error);
        return !error;
    }
    bool readHostFile(uint64_t handle, uint64_t offset, void *dst,
                      size_t size, size_t &bytesRead) override
    {
        bytesRead = 0;
        uint64_t length;
        if ((size && !dst) || !hostFileSize(handle, length) ||
            offset > length || size > length - offset) return false;
        auto &stream = *files_.at(handle);
        stream.clear();
        stream.seekg(static_cast<std::streamoff>(offset));
        stream.read(static_cast<char *>(dst), static_cast<std::streamsize>(size));
        bytesRead = static_cast<size_t>(stream.gcount());
        return bytesRead == size;
    }
    void closeHostFile(uint64_t handle) override { files_.erase(handle); paths_.erase(handle); }
    int32_t memoryCard(const MemoryCardRequest &) override { return -1; }
    bool hasGuestFunction(uint32_t) const override { return false; }
    bool sendSifCommand(uint32_t id, const void *, size_t size) override
    {
        std::cout << "undelivered_sif id=0x" << std::hex << id << std::dec
                  << " size=" << size << '\n';
        return false;
    }
    void log(LogLevel level, std::string_view message) override
    { std::cout << "log level=" << unsigned(level) << ' ' << message << '\n'; }
    bool readSifRegister(SifRegister reg, uint32_t &value) const override
    {
        const uint32_t slot = uint32_t(reg);
        if (slot != 0u && slot != 1u && slot != 2u && slot != 4u) return false;
        value = sifRegisters_[slot];
        return true;
    }
    bool writeSifRegister(SifRegister reg, uint32_t value) override
    {
        if (reg == SifRegister::MsFlag) sifRegisters_[2] &= ~value;
        else if (reg == SifRegister::SmFlag) sifRegisters_[4] |= value;
        else if (reg == SifRegister::SubAddress) sifRegisters_[1] = value;
        else return false;
        return true;
    }
    void sifSnapshot() const
    {
        std::cout << "sif_registers msflag=0x" << std::hex << sifRegisters_[2]
                  << " smflag=0x" << sifRegisters_[4] << " subaddr=0x" << sifRegisters_[1]
                  << std::dec << '\n';
    }
    void resetIopSifRegisters() override
    {
        sifRegisters_[4] &= ~0x70000u;
        sifRegisters_[1] = 0u;
    }
private:
    bool range(uint32_t address, size_t size, uint32_t &offset) const
    { return normalizeGuestAddress(address, offset) && size <= guest_.size() - offset; }
    std::string image_;
    std::vector<uint8_t> guest_;
    uint64_t nextHandle_ = 0;
    std::map<uint64_t, std::unique_ptr<std::ifstream>> files_;
    std::map<uint64_t, std::string> paths_;
    std::map<std::string, std::string> modules_;
    std::array<uint32_t, 5> sifRegisters_{};
};

static void snapshot(const IopSubsystem &iop)
{
    const auto s = iop.debugSnapshot();
    std::cout << "snapshot instructions=" << s.emulatorInstructions
              << " cycles=" << s.emulatorCycles << " modules=" << s.emulatorLoadedModules
              << " threads=" << s.emulatorThreads << " rpc_servers=" << s.emulatorRpcServers
              << " cdvd_bind=" << iop.canBindRpc(0x80000592u)
              << " loadfile_bind=" << iop.canBindRpc(0x80000006u) << '\n';
    for (const auto &line : s.diagnostics) std::cout << "diagnostic " << line << '\n';
}

static void cdInit(ProbeHost &host, IopSubsystem &iop)
{
    RpcRequest request{};
    request.sid = 0x80000592u;
    request.send = {0x2000, 4};
    request.receive = {0x3000, 16};
    const uint32_t mode = 0;
    const uint32_t poison[4] = {0xCCCCCCCCu, 0xCCCCCCCCu, 0xCCCCCCCCu, 0xCCCCCCCCu};
    if (!host.writeGuest(request.send.address, &mode, sizeof(mode)) ||
        !host.writeGuest(request.receive.address, poison, sizeof(poison)))
        throw std::runtime_error("invalid probe RPC buffers");
    const auto result = iop.handleRpc(request);
    uint32_t reply[4]{};
    if (!host.readGuest(request.receive.address, reply, sizeof(reply)))
        throw std::runtime_error("invalid probe reply address");
    std::cout << "cdinit_rpc handled=" << result.handled
              << " completion=" << result.signalCompletion << " reply=" << std::hex;
    for (const auto word : reply) std::cout << ' ' << word;
    std::cout << std::dec << '\n';
}

static void readPvd(ProbeHost &host, IopSubsystem &iop)
{
    // libcdvd-rpc.h RPC5 ReadEE packet. Aligned destination avoids fragment
    // copy policy; compare every byte against the actual mounted sector.
    const uint32_t packet[7] = {16u, 1u, 0x4000u, 0x10u, 0x5100u, 0x5000u, 0u};
    std::array<uint8_t, 2048> bytes{};
    bytes.fill(0xCCu);
    if (!host.writeGuest(0x4000, bytes.data(), bytes.size()) ||
        !host.writeGuest(0x2000, packet, sizeof(packet)) ||
        !host.zeroGuest(0x5000, 0x200))
        throw std::runtime_error("invalid probe read buffers");
    RpcRequest request{};
    request.sid = 0x80000595u;
    request.function = 1u;
    request.send = {0x2000, sizeof(packet)};
    request.receive = {0x3000, 4};
    const auto result = iop.handleRpc(request);
    uint32_t reply = 0;
    if (!host.readGuest(0x4000, bytes.data(), bytes.size()) ||
        !host.readGuest(0x3000, &reply, sizeof(reply)))
        throw std::runtime_error("invalid probe read result");
    std::array<uint8_t, 2048> expected{};
    std::ifstream image(host.hostPath(HostPathKind::CdImage), std::ios::binary);
    image.seekg(16u * 2048u);
    const bool exact = bool(image.read(reinterpret_cast<char *>(expected.data()), expected.size()));
    std::cout << "read_pvd_rpc handled=" << result.handled << " reply=" << reply
              << " image_sector_available=" << exact
              << " sector_matches=" << (exact && bytes == expected)
              << " destination_untouched="
              << std::all_of(bytes.begin(), bytes.end(), [](uint8_t b) { return b == 0xCCu; })
              << '\n';
}

static void requestModule(ProbeHost &host, IopSubsystem &iop, bool positive,
                          const std::string &positivePath = {}, bool testArguments = false)
{
    // loadfile-common.h: argument length, module result, 252-byte path/args.
    std::array<uint8_t, 512> packet{};
    const char *path = positive ? positivePath.c_str() : "host0:__ga2_probe_missing__.irx";
    std::memcpy(packet.data() + 8u, path, std::strlen(path) + 1u);
    if (positive && testArguments)
    {
        constexpr char args[] = "alpha\0"; // alpha and one empty argument.
        const uint32_t length = sizeof(args);
        std::memcpy(packet.data(), &length, sizeof(length));
        std::memcpy(packet.data() + 260u, args, sizeof(args));
    }
    const uint32_t poison[2] = {0xCCCCCCCCu, 0xCCCCCCCCu};
    if (!host.writeGuest(0x2000, packet.data(), packet.size()) ||
        !host.writeGuest(0x3000, poison, sizeof(poison)))
        throw std::runtime_error("invalid LOADFILE diagnostic buffers");
    RpcRequest request{};
    request.sid = 0x80000006u;
    request.function = 0u;
    request.send = {0x2000, packet.size()};
    request.receive = {0x3000, sizeof(poison)};
    const auto before = iop.debugSnapshot().emulatorLoadedModules;
    const auto result = iop.handleRpc(request);
    int32_t reply[2]{};
    if (!host.readGuest(0x3000, reply, sizeof(reply)))
        throw std::runtime_error("invalid LOADFILE reply buffer");
    std::cout << (positive ? "positive_module_rpc" : "missing_module_rpc") << " handled=" << result.handled
              << " id=" << reply[0] << " start=" << reply[1]
              << " negative_error=" << (reply[0] < 0 && uint32_t(reply[0]) != 0xCCCCCCCCu)
              << " modules_before=" << before
              << " modules_after=" << iop.debugSnapshot().emulatorLoadedModules << '\n';
}

static void fixtureMarker(const IopSubsystem &iop)
{
    // The original-tooling test emitter uses ET_EXEC at40000, safely outside
    // the original service images. This is diagnostic fixture data only.
    std::array<uint32_t, 6> marker{};
    const bool readable = iop.readMemory(0x40700u, marker.data(), sizeof(marker));
    std::array<uint8_t, 32> arguments{};
    const bool snapshotRead = iop.readMemory(0x40740u, arguments.data(), arguments.size());
    constexpr char path[] = "host0:ga2-test.irx";
    constexpr char alpha[] = "alpha";
    uint32_t terminator = 1u;
    std::memcpy(&terminator, arguments.data() + 28u, sizeof(terminator));
    const bool argumentMatch = snapshotRead &&
        std::memcmp(arguments.data(), path, sizeof(path)) == 0 && arguments[19] == 0u &&
        std::memcmp(arguments.data() + 20u, alpha, sizeof(alpha)) == 0 &&
        arguments[26] == 0u && terminator == 0u;
    std::array<uint32_t, 12> metadata{};
    const bool metadataRead = marker[3] != 0u && iop.readMemory(marker[3], metadata.data(), sizeof(metadata));
    const bool metadataMatch = metadataRead && (metadata[2] & 0xFFFFu) == 0x0201u &&
        metadata[4] == 0x40000u && metadata[5] == 0x40500u && metadata[6] == 0x40000u &&
        metadata[7] == 0x80u && metadata[8] == 0x10u && metadata[9] == 0x20u;
    std::cout << "fixture_marker readable=" << readable << " argc=" << marker[0]
              << " gp=0x" << std::hex << marker[2] << " metadata=0x" << marker[3]
              << " a2=0x" << marker[4] << std::dec
              << " argument_snapshot_matches=" << argumentMatch
              << " metadata_matches=" << metadataMatch << '\n';
}

int main(int argc, char **argv)
{
    try
    {
        if (argc < 3) throw std::runtime_error("usage: ga2_iop_probe ISO-or-- IRX [IRX ...] [--serve-test-irx FILE | --serve-module GUESTPATH FILE]");
        ProbeHost host(std::string(argv[1]) == "-" ? "" : argv[1]);
        IopSubsystem iop(host);
        host.iop = &iop;
        bool positive = false;
        bool testArguments = false;
        std::string positivePath;
        std::vector<std::string> providers;
        bool bootNotifications = false;
        for (int n = 2; n < argc; ++n)
        {
            if (std::string_view(argv[n]) == "--boot-notifications")
                bootNotifications = true;
            else if (std::string_view(argv[n]) == "--serve-test-irx")
            {
                if (positive) throw std::runtime_error("only one positive module per probe");
                if (++n == argc) throw std::runtime_error("--serve-test-irx needs a file");
                positivePath = "host0:ga2-test.irx";
                host.serveModule(positivePath, argv[n]);
                positive = true;
                testArguments = true;
            }
            else if (std::string_view(argv[n]) == "--serve-module")
            {
                if (positive || n + 2 >= argc) throw std::runtime_error("--serve-module needs one guest path and file");
                positivePath = argv[++n];
                host.serveModule(positivePath, argv[++n]);
                positive = true;
            }
            else providers.emplace_back(argv[n]);
        }
        snapshot(iop);
        cdInit(host, iop); // Negative control: no provider is loaded yet.
        if (bootNotifications && !iop.beginBootNotifications(uint32_t(providers.size())))
            throw std::runtime_error("boot notification collection rejected");
        for (const auto &provider : providers)
        {
            const auto size = std::filesystem::file_size(provider);
            if (size < 52 || size > (16u << 20u)) throw std::runtime_error("IRX size outside probe bounds");
            std::vector<uint8_t> bytes(size);
            std::ifstream file(provider, std::ios::binary);
            if (!file.read(reinterpret_cast<char *>(bytes.data()), bytes.size()))
                throw std::runtime_error("short IRX read");
            if (!host.writeGuest(0x100000, bytes.data(), bytes.size()))
                throw std::runtime_error("IRX does not fit diagnostic EE buffer");
            const auto result = iop.loadModuleBuffer(0x100000);
            std::cout << "load file=" << std::filesystem::path(provider).filename()
                      << " handled=" << result.handled << " id=" << result.moduleId
                      << " start=" << result.startResult << '\n';
            // Finite execution allowance; readiness is observed, never injected.
            iop.runEeCycles(3000000);
            snapshot(iop);
            host.sifSnapshot();
        }
        if (bootNotifications)
        {
            std::cout << "boot_notifications_complete=" << iop.finishBootNotifications() << '\n';
            host.sifSnapshot();
        }
        if (iop.canBindRpc(0x80000592u)) cdInit(host, iop);
        if (iop.canBindRpc(0x80000595u)) readPvd(host, iop);
        if (iop.canBindRpc(0x80000006u))
        {
            if (positive)
            {
                requestModule(host, iop, true, positivePath, testArguments);
                if (testArguments) fixtureMarker(iop);
                // Loading a child must preserve the original controller/service.
                if (iop.canBindRpc(0x80000592u)) cdInit(host, iop);
                if (iop.canBindRpc(0x80000595u)) readPvd(host, iop);
            }
            requestModule(host, iop, false);
        }
        snapshot(iop);
        iop.reset();
        std::cout << "after_reset ";
        snapshot(iop);
        cdInit(host, iop); // Reset must revoke the provider and preserve poison.
        host.sifSnapshot(); // Observe hardware-state retirement, not provider counts alone.
        return 0; // Probe completion does not assert successful firmware boot.
    }
    catch (const std::exception &error)
    { std::cerr << error.what() << '\n'; return 1; }
}
