// Decodes an intra-only MPEG-2 elementary stream through IpuDevice the way
// Sony's libmpeg drives the hardware (FDEC for headers, VDEC for macroblock
// address/type, BDEC per macroblock, or IDEC per slice) and compares the result
// with a raw yuv420p reference produced by FFmpeg.
//
// usage: ipu_stream_test <stream.m2v> <reference.yuv> <width> <height> [bdec|idec] [alternate_scan]
#include "ps2_ipu.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    // MPEG-2 default intra matrix in zigzag (bitstream) order.
    constexpr uint8_t kDefaultIntra[64] = {
        8, 16, 16, 19, 16, 19, 22, 22, 22, 22, 22, 22, 26, 24, 26, 27,
        27, 27, 26, 26, 26, 26, 27, 27, 27, 29, 29, 29, 34, 34, 34, 29,
        29, 29, 27, 27, 29, 29, 32, 32, 34, 34, 37, 38, 37, 35, 35, 34,
        35, 38, 38, 40, 40, 40, 48, 48, 46, 46, 56, 56, 58, 69, 69, 83};
    constexpr uint8_t kZigzag[64] = {
        0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
        12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
        35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
        58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63};
    constexpr uint8_t kAlternate[64] = {
        0, 8, 16, 24, 1, 9, 2, 10, 17, 25, 32, 40, 48, 56, 57, 49,
        41, 33, 26, 18, 3, 11, 4, 12, 19, 27, 34, 42, 50, 58, 35, 43,
        51, 59, 20, 28, 5, 13, 6, 14, 21, 29, 36, 44, 52, 60, 37, 45,
        53, 61, 22, 30, 7, 15, 23, 31, 38, 46, 54, 62, 39, 47, 55, 63};

    struct Driver
    {
        IpuDevice ipu;
        std::vector<uint8_t> stream;
        size_t fed = 0;
        uint32_t skip = 0; // bits still to discard, passed as the next FB

        bool starve = std::getenv("IPU_STARVE") != nullptr; // feed one qword per step to force mid-block stalls
        void feed()
        {
            int budget = starve ? 1 : 1 << 30;
            while (budget-- > 0 && ipu.needsData() && fed < stream.size())
            {
                uint8_t q[16]{};
                std::memcpy(q, stream.data() + fed, std::min<size_t>(16, stream.size() - fed));
                if (!ipu.writeInput(q))
                    break;
                fed += 16;
            }
        }

        void exec(uint32_t command, std::vector<uint8_t> *out = nullptr)
        {
            ipu.writeCommand(command);
            for (int guard = 0;; ++guard)
            {
                const uint64_t before = ipu.activity();
                feed();
                ipu.run();
                if (out)
                {
                    while (ipu.outputCount())
                    {
                        uint8_t q[16];
                        ipu.readOutput(q, 1);
                        out->insert(out->end(), q, q + 16);
                    }
                    ipu.run();
                }
                if (!ipu.busy())
                    return;
                if (ipu.activity() == before || guard > 1000000)
                    throw std::runtime_error("IPU stalled on command " + std::to_string(command >> 28) + " ctrl=" + std::to_string(ipu.readControl()) + " bp=" + std::to_string(ipu.readBitPointer()) + " fed=" + std::to_string(fed) + "/" + std::to_string(stream.size()) + " req=" + std::to_string(ipu.needsData()));
            }
        }

        // Peek 32 bits after discarding the pending skip (and `extra` bits).
        uint32_t peek(uint32_t extra = 0)
        {
            uint32_t total = skip + extra;
            while (total > 32)
            {
                exec(0x40000000u | 32u);
                total -= 32;
            }
            exec(0x40000000u | total);
            skip = 0;
            return ipu.readCommand();
        }
        uint32_t bits(uint32_t n)
        {
            const uint32_t v = peek();
            skip = n;
            return n ? v >> (32 - n) : 0;
        }
        void byteAlign()
        {
            const uint32_t bp = (ipu.readBitPointer() & 0x7f) + skip;
            skip += (8 - (bp & 7)) & 7;
        }
        uint32_t nextStartCode()
        {
            byteAlign();
            uint32_t v = peek();
            while ((v >> 8) != 1)
            {
                skip = 8;
                v = peek();
            }
            skip = 32;
            return v & 0xff;
        }
    };
}

int main(int argc, char **argv)
{
    if (argc < 5)
    {
        std::fprintf(stderr, "usage: %s stream.m2v ref.yuv width height [bdec|idec]\n", argv[0]);
        return 2;
    }
    const bool useIdec = argc > 5 && std::string(argv[5]) == "idec";
    std::ifstream sf(argv[1], std::ios::binary), rf(argv[2], std::ios::binary);
    Driver drv;
    drv.stream.assign(std::istreambuf_iterator<char>(sf), {});
    // Pad so lookahead past the final macroblock (IPU_TOP) has data, as a
    // streaming source would.
    for (uint8_t b : {0x00, 0x00, 0x01, 0xb7})
        drv.stream.push_back(b);
    drv.stream.insert(drv.stream.end(), 64, 0);
    while (drv.stream.size() % 16)
        drv.stream.push_back(0);
    const std::vector<uint8_t> ref(std::istreambuf_iterator<char>(rf), {});
    const int width = std::atoi(argv[3]), height = std::atoi(argv[4]);
    const int mbw = (width + 15) / 16;
    int mbh = (height + 15) / 16;
    const size_t frameBytes = static_cast<size_t>(width) * height * 3 / 2;

    drv.ipu.setOutputDmaReady([] { return true; });

    uint8_t intra[64], nonIntra[64];
    std::memcpy(intra, kDefaultIntra, 64);
    std::memset(nonIntra, 16, 64);
    int intraDc = 0, qScaleType = 0, intraVlc = 0, altScan = 0, framePredFrameDct = 1, pictureType = 0;
    int frame = 0;
    std::vector<uint8_t> picY(static_cast<size_t>(mbw) * 16 * (mbh + 1) * 16), picCb(picY.size() / 4), picCr(picY.size() / 4);
    std::vector<uint8_t> rgb(static_cast<size_t>(mbw) * 16 * (mbh + 1) * 16 * 4);
    long long sumSq = 0, worst = 0, samples = 0;
    long long rgbSumSq = 0, rgbWorst = 0, rgbSamples = 0;

    // libmpeg uploads quantizer matrices with SETIQ from FIFO data. This
    // harness prepends them (ordered for the active scan) to the stream.
    {
        const bool alt = argc > 6 && std::atoi(argv[6]) != 0;
        std::vector<uint8_t> prefix;
        for (int pass = 0; pass < 2; ++pass)
        {
            uint8_t raster[64];
            const uint8_t *zz = pass ? nonIntra : intra;
            for (int k = 0; k < 64; ++k)
                raster[kZigzag[k]] = zz[k];
            for (int k = 0; k < 64; ++k)
                prefix.push_back(raster[alt ? kAlternate[k] : kZigzag[k]]);
        }
        drv.stream.insert(drv.stream.begin(), prefix.begin(), prefix.end());
        drv.exec(0x00000000u);           // BCLR
        drv.exec(0x50000000u);           // SETIQ intra
        drv.exec(0x50000000u | 1u << 27); // SETIQ non-intra
    }

    while (drv.fed < drv.stream.size() || (drv.ipu.readBitPointer() >> 8 & 0xf) != 0)
    {
        uint32_t code;
        try
        {
            code = drv.nextStartCode();
        }
        catch (const std::exception &)
        {
            break;
        }
        if (code == 0xb7)
            break;
        if (code == 0xb3)
        {
            drv.bits(32);
            drv.bits(31); // bit_rate .. constrained_parameters_flag
            if (drv.bits(1))
                for (int k = 0; k < 64; ++k)
                    intra[k] = static_cast<uint8_t>(drv.bits(8));
            if (drv.bits(1))
                for (int k = 0; k < 64; ++k)
                    nonIntra[k] = static_cast<uint8_t>(drv.bits(8));
            drv.bits(0);
        }
        else if (code == 0xb5)
        {
            const uint32_t id = drv.bits(4);
            if (id == 1)
            {
                drv.bits(8); // profile_and_level_indication
                if (!drv.bits(1)) // progressive_sequence == 0: rows in 32-line pairs
                    mbh = 2 * ((height + 31) / 32);
                drv.bits(0);
            }
            else if (id == 8)
            {
                drv.bits(16); // f_codes
                intraDc = static_cast<int>(drv.bits(2));
                const uint32_t structure = drv.bits(2);
                if (structure != 3)
                    throw std::runtime_error("field pictures not covered by this test");
                drv.bits(1);
                framePredFrameDct = static_cast<int>(drv.bits(1));
                drv.bits(1);
                qScaleType = static_cast<int>(drv.bits(1));
                intraVlc = static_cast<int>(drv.bits(1));
                altScan = static_cast<int>(drv.bits(1));
                drv.bits(0);
            }
        }
        else if (code == 0x00)
        {
            drv.bits(10);
            pictureType = static_cast<int>(drv.bits(3));
            if (pictureType != 1)
                throw std::runtime_error("only I pictures are covered by this test");
            drv.bits(0);
        }
        else if (code >= 0x01 && code <= 0xaf)
        {
            const uint32_t ctrl = (static_cast<uint32_t>(intraDc) << 16) | (static_cast<uint32_t>(altScan) << 20) |
                                  (static_cast<uint32_t>(intraVlc) << 21) | (static_cast<uint32_t>(qScaleType) << 22) |
                                  (1u << 24);
            drv.ipu.writeControl(ctrl);
            const int row = static_cast<int>(code) - 1;
            int qsc = static_cast<int>(drv.bits(5));
            while (drv.bits(1)) // extra_bit_slice
                drv.bits(8);
            drv.bits(0);
            if (useIdec)
            {
                // IDEC decodes the whole slice and emits RGB32 per macroblock.
                drv.exec(0x30000000u | drv.skip); // first macroblock_address_increment
                drv.skip = 0;
                std::vector<uint8_t> out;
                drv.exec(0x10000000u | (static_cast<uint32_t>(qsc) << 16) |
                             (framePredFrameDct ? 0u : 1u << 24),
                         &out);
                drv.skip = 0;
                // First macroblock address increment was consumed by IDEC itself
                // (it decodes macroblock_type first); assume full-width slices.
                const size_t mbs = out.size() / 1024;
                for (size_t m = 0; m < mbs; ++m)
                {
                    const int col = static_cast<int>(m);
                    for (int y = 0; y < 16; ++y)
                        std::memcpy(&rgb[((static_cast<size_t>(row) * 16 + y) * mbw * 16 + col * 16) * 4],
                                    &out[m * 1024 + y * 64], 64);
                }
                if (row == mbh - 1)
                    goto compare_idec;
                continue;
            }
            int col = -1;
            bool first = true;
            while (true)
            {
                // macroblock_address_increment
                drv.exec(0x30000000u | drv.skip);
                drv.skip = 0;
                uint32_t inc = drv.ipu.readCommand() & 0xffff;
                int add = 0;
                while (inc == 0x23) // escape
                {
                    add += 33;
                    drv.exec(0x30000000u);
                    inc = drv.ipu.readCommand() & 0xffff;
                }
                col += add + static_cast<int>(inc);
                drv.exec(0x34000000u); // macroblock_type
                const uint32_t type = drv.ipu.readCommand() & 0xffff;
                uint32_t dt = 0;
                if (!framePredFrameDct)
                    dt = drv.bits(1);
                if (type & 0x10)
                    qsc = static_cast<int>(drv.bits(5));
                std::vector<uint8_t> out;
                // BDEC: QSC[20:16] DT[25] DCR[26] MBI[27]
                drv.exec(0x20000000u | drv.skip | (static_cast<uint32_t>(qsc) << 16) | (dt << 25) |
                             ((first || add || inc > 1) ? 1u << 26 : 0u) | (1u << 27),
                         &out);
                drv.skip = 0;
                first = false;
                if (std::getenv("IPU_TRACE")) std::fprintf(stderr, "row %d col %d inc %u type %x qsc %d top %08x ctrl %08x\n", row, col, inc, type, qsc, drv.ipu.readTop(), drv.ipu.readControl());
                if (out.size() != 768)
                    throw std::runtime_error("BDEC produced " + std::to_string(out.size()) + " bytes");
                const int16_t *mb = reinterpret_cast<const int16_t *>(out.data());
                for (int y = 0; y < 16; ++y)
                    for (int x = 0; x < 16; ++x)
                        picY[(static_cast<size_t>(row) * 16 + y) * mbw * 16 + col * 16 + x] = static_cast<uint8_t>(mb[y * 16 + x]);
                for (int y = 0; y < 8; ++y)
                    for (int x = 0; x < 8; ++x)
                    {
                        picCb[(static_cast<size_t>(row) * 8 + y) * mbw * 8 + col * 8 + x] = static_cast<uint8_t>(mb[256 + y * 8 + x]);
                        picCr[(static_cast<size_t>(row) * 8 + y) * mbw * 8 + col * 8 + x] = static_cast<uint8_t>(mb[320 + y * 8 + x]);
                    }
                // BDEC leaves SCD set when a start code follows the macroblock.
                if (drv.ipu.readControl() & (1u << 15))
                    break;
                if ((drv.ipu.readTop() >> 9) == 0)
                    break;
            }
            if (row == mbh - 1)
            {
                // Picture complete: compare with the reference frame.
                if (static_cast<size_t>(frame + 1) * frameBytes > ref.size())
                    break;
                const uint8_t *r = ref.data() + static_cast<size_t>(frame) * frameBytes;
                for (int y = 0; y < height; ++y)
                    for (int x = 0; x < width; ++x)
                    {
                        const long long d = static_cast<long long>(picY[static_cast<size_t>(y) * mbw * 16 + x]) - r[y * width + x];
                        sumSq += d * d;
                        worst = std::max(worst, std::llabs(d));
                        ++samples;
                    }
                const uint8_t *rc = r + width * height, *rr = rc + width * height / 4;
                for (int y = 0; y < height / 2; ++y)
                    for (int x = 0; x < width / 2; ++x)
                    {
                        long long d = static_cast<long long>(picCb[static_cast<size_t>(y) * mbw * 8 + x]) - rc[y * width / 2 + x];
                        sumSq += d * d;
                        worst = std::max(worst, std::llabs(d));
                        d = static_cast<long long>(picCr[static_cast<size_t>(y) * mbw * 8 + x]) - rr[y * width / 2 + x];
                        sumSq += d * d;
                        worst = std::max(worst, std::llabs(d));
                        samples += 2;
                    }
                ++frame;
                std::fprintf(stderr, "frame %d cumulative yuv max_abs=%lld rmse=%.4f\n", frame, worst,
                             std::sqrt(static_cast<double>(sumSq) / static_cast<double>(samples)));
            }
        }
        if (useIdec && code == 0x00 && false)
            break;
        // Frame completion for IDEC: compare RGB against the reference converted
        // with the IPU's documented integer CSC.
        continue;
    compare_idec:
        {
            if (static_cast<size_t>(frame + 1) * frameBytes > ref.size())
                break;
            const uint8_t *r = ref.data() + static_cast<size_t>(frame) * frameBytes;
            const uint8_t *rc = r + width * height, *rr = rc + width * height / 4;
            for (int y = 0; y < height; ++y)
                for (int x = 0; x < width; ++x)
                {
                    const int Y = r[y * width + x], cb = rc[(y / 2) * width / 2 + x / 2] - 128, cr = rr[(y / 2) * width / 2 + x / 2] - 128;
                    const int lum = (0x95 * std::max(0, Y - 16)) >> 6;
                    const int exp[3] = {
                        std::clamp((lum + ((0xcc * cr) >> 6) + 1) >> 1, 0, 255),
                        std::clamp((lum + ((-0x68 * cr) >> 6) + ((-0x32 * cb) >> 6) + 1) >> 1, 0, 255),
                        std::clamp((lum + ((0x102 * cb) >> 6) + 1) >> 1, 0, 255)};
                    for (int c = 0; c < 3; ++c)
                    {
                        const long long d = static_cast<long long>(rgb[(static_cast<size_t>(y) * mbw * 16 + x) * 4 + c]) - exp[c];
                        rgbSumSq += d * d;
                        rgbWorst = std::max(rgbWorst, std::llabs(d));
                        ++rgbSamples;
                    }
                }
            ++frame;
        }
    }

    if (useIdec)
    {
        if (!rgbSamples)
        {
            std::printf("FAIL: no frames decoded\n");
            return 1;
        }
        const double rmse = std::sqrt(static_cast<double>(rgbSumSq) / static_cast<double>(rgbSamples));
        std::printf("IDEC frames=%d rgb_rmse=%.4f max_abs=%lld\n", frame, rmse, rgbWorst);
        return (rmse < 1.0 && rgbWorst <= 8) ? 0 : 1;
    }
    if (!samples)
    {
        std::printf("FAIL: no frames decoded\n");
        return 1;
    }
    const double rmse = std::sqrt(static_cast<double>(sumSq) / static_cast<double>(samples));
    std::printf("BDEC frames=%d yuv_rmse=%.4f max_abs=%lld\n", frame, rmse, worst);
    return (rmse < 0.5 && worst <= 4) ? 0 : 1;
}
