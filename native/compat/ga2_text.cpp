// Language packs can change the half-width (Latin) font metrics: the
// games draw characters 0x20..0x7E from the built-in "asciipfont" with a
// table of 95 {u, v, width} int32 entries in the executable's data, read on
// every character by the message glyph drawer and the three string drawers
// (docs/modding/text.md). GA2_FONT_METRICS names a 1140-byte table
// (`tools/modding/ga2font.py pack`); before the first drawer call it is
// copied over the stock table, so layout, alignment and measuring all follow.
// The font image itself is replaced through texture packs.
//
// Button placeholders in translated text ({circle}, {l1}, ...: tools/modding/
// text_buttons.py) are reserved full-width characters, Shift-JIS 0xF040..:
// the game has no glyph for them, so the Shift-JIS glyph lookup is hooked and
// answers with a monochrome 24x24 bitmap of the PlayStation symbol
// (ga2_text_icons.h) kept in kernel RAM. The game draws it like any glyph; the
// texture replacement (gs_texpacks.cpp) recognises the bitmap and draws the
// current input style's icon in its place.
//
// The games' own prompts ("○ボタン", "Ｌ２ボタン", "×：再生終了") get the same
// characters: the message parser's and the string drawers' text is swapped for
// a copy with the symbol replaced (GA2_NO_PROMPT_SWAP=1 turns that off), so the
// Japanese text shows the player's buttons too. A language pack's
// text/<program>.msg (tools/modding/restore_text.py) also translates the one
// message a save brings back in the language it was made in.
#include "game_overrides.h"
#include "runtime/ga2_hitext.h"
#include "runtime/ga2_text_icons.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace
{
    constexpr size_t kEntries = 0x7F - 0x20;
    constexpr size_t kTableBytes = kEntries * 12u;
    constexpr int kTextureW = 128, kTextureH = 144, kGlyphH = 18;
    // First entries of the stock table (space, '!', '"'): proves the address.
    constexpr int32_t kStockHead[9] = {0, 0, 9, 11, 0, 5, 20, 0, 5};

    struct DiscText
    {
        const char *disc;
        const char *program;   // the boot ELF's name (a language pack's text/<program>.msg)
        uint32_t metrics;      // the ASCII metrics table in the loaded executable
        uint32_t drawers[4];   // message glyph drawer + the three string drawers
        uint32_t glyphLookup;  // Shift-JIS glyph lookup (a0 = pointer to the 2-byte code, a1 = font)
        uint32_t setColour;    // set the sprite colour (a0..a3 = r, g, b, a): stores them in four global bytes
        uint32_t parse;        // message text -> records (a0 records, a1 text, a2 max chars, a3 size, ...)
    };

    constexpr DiscText kDiscs[] = {
        {"zettai", "SLPM_662.43", 0x2d96b8u, {0x111c70u, 0x110c78u, 0x111298u, 0x1117a8u}, 0x110a30u, 0x233280u, 0x1ce0a0u},
        {"mugen-1", "SLPM_667.79", 0x2f5ec8u, {0x111cd8u, 0x110ce0u, 0x111300u, 0x111810u}, 0x110a98u, 0x24cce0u, 0x1e5ba8u},
        {"mugen-2", "SLPM_667.80", 0x2db238u, {0x111cf0u, 0x110cf8u, 0x111318u, 0x111828u}, 0x110ab0u, 0x234d30u, 0x1cfb40u},
        {"eigou-1", "SLPM_551.18", 0x304148u, {0x111cd8u, 0x110ce0u, 0x111300u, 0x111810u}, 0x110a98u, 0x2599e0u, 0x1efae8u},
        {"eigou-2", "SLPM_551.19", 0x2f6fc8u, {0x111cd8u, 0x110ce0u, 0x111300u, 0x111810u}, 0x110a98u, 0x24dd50u, 0x1e6788u},
    };

    // Kernel RAM below the SDK pools' end and the invocation-stack arena
    // (0x60000-0x70000), which games never touch: the placeholder glyphs.
    constexpr uint32_t kIconRam = 0x00078000u;
    constexpr uint32_t kIconBytes = ga2_text_icons::kGlyphBytes * ga2_text_icons::kGlyphCount;
    static_assert(kIconRam + kIconBytes <= 0x00080000u);
    bool g_iconsWritten = false;
    bool g_noIcons = false;
    // The sprite colour's four global bytes (r, g, b, a), found in the setter's code.
    uint32_t g_colourAddress = 0;

    std::optional<std::array<uint8_t, kTableBytes>> g_table;
    bool g_applied = false;

    // The games' own button prompts in text ("○ボタンを押して", "Ｌ２ボタン", "×：再生終了"): the PlayStation
    // symbol, or the button's name, is swapped for the placeholder characters above, so the input style's icon is
    // drawn there too (the Japanese text, and any translation that kept the symbol). Only a symbol followed by ボタン
    // ("button") or by a colon (a key legend) is a prompt: "○×△□～…" mumbling stays as written. The changed text
    // is a copy in kernel RAM next to the icon bitmaps; the game reads it instead of its own (one buffer per caller
    // kind: the message parser keeps nothing of it, the string drawers draw it at once).
    constexpr uint32_t kPromptRam[2] = {0x0007C000u, 0x0007D000u};
    constexpr uint32_t kPromptBytes = 0x1000u;
    static_assert(kPromptRam[1] + kPromptBytes <= 0x00080000u && kPromptRam[0] >= kIconRam + kIconBytes);

    // The reserved characters of a button (text_buttons.py name), as Shift-JIS bytes; empty when not in the table.
    std::string placeholder(const char *button)
    {
        std::string out;
        for (const ga2_text_icons::Glyph &g : ga2_text_icons::kGlyphs)
            if (std::strcmp(g.button, button) == 0)
            {
                const uint32_t code = ga2_text_icons::kFirstCode + g.index;
                out += char(code >> 8);
                out += char(code & 0xFFu);
            }
        return out;
    }

    // `text` with the prompts swapped; false when it has none.
    bool swapPrompts(const std::string &text, std::string &out)
    {
        static const std::string kButton = "\x83\x7B\x83\x5E\x83\x93";      // ボタン
        static const std::string kColon = "\x81\x46";                       // ：
        struct Name
        {
            std::string sjis;
            const char *button;
        };
        // Full-width and ASCII spellings of the names; the symbols need ボタン or a colon after them, the names ボタン.
        static const std::vector<Name> kSymbols = {
            {"\x81\x9B", "circle"}, {"\x81\xFC", "circle"}, {"\x81\x7E", "cross"}, {"\x81\xA2", "triangle"}, {"\x81\xA0", "square"}};
        static const std::vector<Name> kNames = [] {
            std::vector<Name> v;
            const char *fwLetter[2] = {"\x82\x6B", "\x82\x71"}; // Ｌ Ｒ
            const char *fwDigit[3] = {"\x82\x50", "\x82\x51", "\x82\x52"}; // １ ２ ３
            const char *names[2][3] = {{"l1", "l2", "l3"}, {"r1", "r2", "r3"}};
            for (int s = 0; s < 2; ++s)
                for (int n = 0; n < 3; ++n)
                {
                    v.push_back({std::string(fwLetter[s]) + fwDigit[n], names[s][n]});
                    v.push_back({std::string(1, s ? 'R' : 'L') + char('1' + n), names[s][n]});
                }
            v.push_back({"\x83\x58\x83\x5E\x81\x5B\x83\x67", "start"});  // スタート
            v.push_back({"\x83\x5A\x83\x8C\x83\x4E\x83\x67", "select"}); // セレクト
            return v;
        }();
        out.clear();
        bool changed = false;
        size_t i = 0;
        while (i < text.size())
        {
            const uint8_t c = uint8_t(text[i]);
            const bool lead = (c >= 0x81 && c <= 0x9F) || (c >= 0xE0 && c <= 0xFC);
            bool swapped = false;
            auto followedBy = [&](size_t at, const std::string &what) { return text.compare(at, what.size(), what) == 0; };
            for (const Name &s : kSymbols)
                if (!swapped && followedBy(i, s.sjis) &&
                    (followedBy(i + s.sjis.size(), kButton) || followedBy(i + s.sjis.size(), kColon)))
                {
                    const std::string icon = placeholder(s.button);
                    if (!icon.empty())
                    {
                        out += icon;
                        i += s.sjis.size();
                        swapped = changed = true;
                    }
                }
            for (const Name &n : kNames)
                if (!swapped && followedBy(i, n.sjis) && followedBy(i + n.sjis.size(), kButton))
                {
                    const std::string icon = placeholder(n.button);
                    if (!icon.empty())
                    {
                        out += icon;
                        i += n.sjis.size();
                        swapped = changed = true;
                    }
                }
            if (swapped)
                continue;
            const size_t width = lead && i + 1 < text.size() ? 2 : 1;
            out.append(text, i, width);
            i += width;
        }
        return changed;
    }

    // A save keeps the message that was on screen and shows it again when loaded, in the language it was made in.
    // A language pack's text/<program>.msg (tools/modding/restore_text.py) maps the disc's message bytes to the
    // pack's: such a text is swapped for its translation when the game lays it out (the scripts themselves already
    // come from the pack).
    std::unordered_map<std::string, std::string> g_restore;

    void loadRestore(const char *program)
    {
        const char *list = std::getenv("GA2_MODS");
        if (!list)
            return;
        size_t files = 0;
        // GA2_MODS is highest priority first: the first entry for a text wins.
        for (std::string_view rest = list; !rest.empty();)
        {
            const size_t end = rest.find(';');
            const std::string_view root = rest.substr(0, end);
            rest = end == std::string_view::npos ? std::string_view() : rest.substr(end + 1);
            if (root.empty())
                continue;
            const std::filesystem::path file =
                std::filesystem::path(std::string(root)).parent_path() / "text" / (std::string(program) + ".msg");
            std::ifstream in(file, std::ios::binary);
            if (!in)
                continue;
            const std::string data{std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
            if (data.size() < 12 || data.compare(0, 8, std::string("GA2MSG1\0", 8)) != 0)
            {
                std::cerr << "[ga2 text] " << file.string() << ": not a message table\n";
                continue;
            }
            auto u16 = [&](size_t at) { return size_t(uint8_t(data[at])) | size_t(uint8_t(data[at + 1])) << 8; };
            uint32_t count = 0;
            std::memcpy(&count, data.data() + 8, 4);
            size_t at = 12;
            for (uint32_t i = 0; i < count && at + 2 <= data.size(); ++i)
            {
                const size_t a = u16(at);
                if (at + 2 + a + 2 > data.size())
                    break;
                const size_t b = u16(at + 2 + a);
                if (at + 4 + a + b > data.size())
                    break;
                g_restore.emplace(data.substr(at + 2, a), data.substr(at + 4 + a, b));
                at += 4 + a + b;
            }
            ++files;
            std::cerr << "[ga2 text] " << file.string() << ": " << count << " message translation(s) for restored saves\n";
        }
        (void)files;
    }

    // The text the game should use for the NUL-terminated text at `address`: a restored save's message in the
    // pack's language (message parser only) and/or with its button prompts swapped, written to `buffer`; `address`
    // itself when nothing changes.
    uint32_t promptText(uint8_t *rdram, uint32_t address, int buffer)
    {
        static const bool noPrompts = std::getenv("GA2_NO_TEXT_ICONS") != nullptr || std::getenv("GA2_NO_PROMPT_SWAP") != nullptr;
        address &= 0x01FFFFFFu;
        if (address < 0x100000u || address >= 0x02000000u || (noPrompts && (buffer != 0 || g_restore.empty())))
            return address;
        std::string text;
        for (uint32_t a = address; a < 0x02000000u && rdram[a] && text.size() < kPromptBytes - 1; ++a)
            text += char(rdram[a]);
        bool changed = false;
        if (buffer == 0 && !g_restore.empty())
            if (const auto it = g_restore.find(text); it != g_restore.end())
            {
                text = it->second;
                changed = true;
            }
        // Prompts: only texts with a ボ (0x83 0x7B) or a full-width colon can hold one.
        if (!noPrompts && (text.find("\x83\x7B") != std::string::npos || text.find("\x81\x46") != std::string::npos))
        {
            std::string swapped;
            if (swapPrompts(text, swapped))
            {
                static const bool trace = std::getenv("GA2_TEXT_TRACE") != nullptr;
                if (trace)
                {
                    std::string hex;
                    for (unsigned char c : swapped.substr(0, 48))
                    {
                        char b[4];
                        std::snprintf(b, sizeof(b), "%02x", c);
                        hex += b;
                    }
                    std::fprintf(stderr, "[ga2 text] button prompt swapped (%s): %s\n", buffer ? "string" : "message", hex.c_str());
                }
                text = std::move(swapped);
                changed = true;
            }
        }
        if (!changed || text.size() >= kPromptBytes)
            return address;
        std::memcpy(rdram + kPromptRam[buffer], text.data(), text.size());
        rdram[kPromptRam[buffer] + text.size()] = 0;
        return kPromptRam[buffer];
    }

    template <size_t I, int D>
    PS2Runtime::RecompiledFunction g_original = nullptr;

    // A table the drawers can use safely: every glyph cell inside the texture.
    std::optional<std::array<uint8_t, kTableBytes>> loadTable(const char *path)
    {
        std::ifstream in(path, std::ios::binary);
        const std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
        if (bytes.size() != kTableBytes)
        {
            std::cerr << "[ga2 text] " << path << ": expected " << kTableBytes << " bytes, got " << bytes.size() << '\n';
            return std::nullopt;
        }
        for (size_t i = 1; i < kEntries; ++i) // entry 0 (space) is never drawn
        {
            int32_t u, v, w;
            std::memcpy(&u, bytes.data() + i * 12u, 4);
            std::memcpy(&v, bytes.data() + i * 12u + 4u, 4);
            std::memcpy(&w, bytes.data() + i * 12u + 8u, 4);
            if (u < 0 || v < 0 || w < 0 || u + w > kTextureW || v + kGlyphH > kTextureH)
            {
                std::cerr << "[ga2 text] " << path << ": glyph 0x" << std::hex << (0x20 + i) << std::dec
                          << " cell outside the font texture\n";
                return std::nullopt;
            }
        }
        std::array<uint8_t, kTableBytes> table{};
        std::memcpy(table.data(), bytes.data(), kTableBytes);
        return table;
    }

    // A metrics table in guest memory that is plausible: every drawn character's cell lies inside the texture.
    bool plausibleTable(const uint8_t *table)
    {
        for (size_t i = 1; i < kEntries; ++i)
        {
            int32_t u, v, w;
            std::memcpy(&u, table + i * 12u, 4);
            std::memcpy(&v, table + i * 12u + 4u, 4);
            std::memcpy(&w, table + i * 12u + 8u, 4);
            if (u < 0 || v < 0 || w < 0 || u + w > kTextureW || v + kGlyphH > kTextureH)
                return false;
        }
        return true;
    }

    template <size_t I>
    void apply(uint8_t *rdram)
    {
        g_applied = true;
        uint8_t *target = rdram + kDiscs[I].metrics;
        const bool stock = std::memcmp(target, kStockHead, sizeof(kStockHead)) == 0;
        if (g_table)
        {
            if (!stock)
                std::cerr << "[ga2 text] " << kDiscs[I].disc << ": no stock metrics table at 0x" << std::hex
                          << kDiscs[I].metrics << std::dec << "; font metrics unchanged\n";
            else
            {
                std::memcpy(target, g_table->data(), kTableBytes);
                std::cerr << "[ga2 text] " << kDiscs[I].disc << ": language pack font metrics applied\n";
            }
        }
        // Sharp text redraws the half-width atlas cell by cell with the metrics in use.
        if (plausibleTable(target))
            ga2_hitext::noteAsciiMetrics(target);
    }

    // Before the original only (it may return to the dispatcher early). The
    // executable's data is loaded once, so one write per boot is enough.
    template <size_t I, int D>
    void drawer(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (!g_applied)
            apply<I>(rdram);
        // The two string drawers take the text in t0 (menus, names, the movie player's key legend).
        if (D == 1 || D == 2)
            SET_GPR_U32(ctx, 8, promptText(rdram, getRegU32(ctx, 8), 1));
        g_original<I, D>(rdram, ctx, runtime);
    }

    // The message parser (records from text: dialogue, narration, battle balloons) takes the text in a1.
    template <size_t I>
    PS2Runtime::RecompiledFunction g_parseOriginal = nullptr;

    template <size_t I>
    void parse(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        SET_GPR_U32(ctx, 5, promptText(rdram, getRegU32(ctx, 5), 0));
        g_parseOriginal<I>(rdram, ctx, runtime);
    }

    template <size_t I>
    PS2Runtime::RecompiledFunction g_lookupOriginal = nullptr;

    // The colour setter is `lui r, hi; addiu r, r, lo; sb a3, 0x1b(r); sb a0, 0x18(r); ...`:
    // the address of the red byte is hi:lo plus the offset of the `sb a0`.
    uint32_t colourAddress(const uint8_t *rdram, uint32_t function)
    {
        auto word = [&](uint32_t i) {
            uint32_t w;
            std::memcpy(&w, rdram + function + 4u * i, 4);
            return w;
        };
        int32_t base = 0;
        uint32_t reg = 0;
        for (uint32_t i = 0; i < 8; ++i)
        {
            const uint32_t w = word(i), op = w >> 26, rs = (w >> 21) & 31u, rt = (w >> 16) & 31u;
            const int32_t imm = int16_t(w & 0xFFFFu);
            if (op == 0x0Fu)                         // lui rt, imm
            {
                reg = rt;
                base = int32_t(uint32_t(w & 0xFFFFu) << 16);
            }
            else if (op == 0x09u && reg && rs == reg && rt == reg)   // addiu reg, reg, imm
                base += imm;
            else if (op == 0x28u && reg && rs == reg && rt == 4u)    // sb a0, off(reg)
                return uint32_t(base + imm);
        }
        return 0;
    }

    void writeIcons(uint8_t *rdram)
    {
        g_iconsWritten = true;
        for (unsigned i = 0; i < ga2_text_icons::kGlyphCount; ++i)
            std::memcpy(rdram + kIconRam + i * ga2_text_icons::kGlyphBytes, ga2_text_icons::kGlyphs[i].bitmap,
                        ga2_text_icons::kGlyphBytes);
    }

    // glyph = lookup(code pointer, font): reserved codes get the placeholder
    // bitmaps, everything else the game's own glyph.
    template <size_t I>
    void glyphLookup(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t code = getRegU32(ctx, 4) & 0x01FFFFFFu;
        g_lookupOriginal<I>(rdram, ctx, runtime);
        const uint8_t lead = rdram[code], trail = rdram[code + 1u];
        const bool reserved = lead == (ga2_text_icons::kFirstCode >> 8) && trail >= (ga2_text_icons::kFirstCode & 0xFFu) &&
                              trail - (ga2_text_icons::kFirstCode & 0xFFu) < ga2_text_icons::kGlyphCount;
        if (!reserved)
        {
            // Sharp text: tell the renderer which character this bitmap is (it swaps the texture for a vector one).
            const uint32_t bitmap = getRegU32(ctx, 2) & 0x01FFFFFFu;
            if (bitmap && bitmap + 288u <= 0x02000000u)
                ga2_hitext::noteGlyph(uint32_t(lead) << 8 | trail, rdram + bitmap);
            return;
        }
        if (g_noIcons)
            return;
        // The icon art has its own colours: draw it untinted (neutral 0x80), keeping the text's alpha.
        if (!g_iconsWritten)
            writeIcons(rdram);
        if (g_colourAddress && ga2_text_icons::replacementActive())
            rdram[g_colourAddress] = rdram[g_colourAddress + 1u] = rdram[g_colourAddress + 2u] = 0x80;
        SET_GPR_U32(ctx, 2, kIconRam + (trail - (ga2_text_icons::kFirstCode & 0xFFu)) * ga2_text_icons::kGlyphBytes);
    }

    template <size_t I>
    void bind(PS2Runtime &runtime)
    {
        const DiscText &d = kDiscs[I];
        g_noIcons = std::getenv("GA2_NO_TEXT_ICONS") != nullptr;
        loadRestore(d.program);
        // The glyph lookup serves two features: the button placeholder glyphs and sharp text (which only observes).
        if (runtime.hasFunction(d.glyphLookup))
        {
            if (!g_noIcons && runtime.hasFunction(d.setColour))
            {
                g_colourAddress = colourAddress(runtime.memory().getRDRAM(), d.setColour);
                if (g_colourAddress == 0 || g_colourAddress >= 0x02000000u)
                {
                    std::cerr << "[ga2 text] " << d.disc << ": colour setter not recognised; icons keep the text colour\n";
                    g_colourAddress = 0;
                }
            }
            g_lookupOriginal<I> = runtime.lookupFunction(d.glyphLookup);
            runtime.replaceFunction(d.glyphLookup, glyphLookup<I>);
            std::cerr << "[ga2 text] " << d.disc << ": glyph lookup hooked (button placeholders " << (g_noIcons ? "off" : "on")
                      << ", colour bytes at 0x" << std::hex << g_colourAddress << std::dec << ")\n";
        }
        const char *path = std::getenv("GA2_FONT_METRICS");
        if (path && *path)
            g_table = loadTable(path);
        // The drawers: they apply a language pack's metrics and report the metrics in use to sharp text.
        for (uint32_t address : d.drawers)
            if (!runtime.hasFunction(address))
            {
                std::cerr << "[ga2 text] " << d.disc << ": drawer 0x" << std::hex << address << std::dec
                          << " not in the generated table; font metrics unchanged\n";
                return;
            }
        auto hook = [&](uint32_t address, PS2Runtime::RecompiledFunction &original, PS2Runtime::RecompiledFunction replacement) {
            original = runtime.lookupFunction(address);
            runtime.replaceFunction(address, replacement);
        };
        hook(d.drawers[0], g_original<I, 0>, drawer<I, 0>);
        hook(d.drawers[1], g_original<I, 1>, drawer<I, 1>);
        hook(d.drawers[2], g_original<I, 2>, drawer<I, 2>);
        hook(d.drawers[3], g_original<I, 3>, drawer<I, 3>);
        if (runtime.hasFunction(d.parse))
            hook(d.parse, g_parseOriginal<I>, parse<I>);
        else
            std::cerr << "[ga2 text] " << d.disc << ": message parser 0x" << std::hex << d.parse << std::dec
                      << " not in the generated table; the games' own button prompts keep their symbols\n";
    }
}

PS2_REGISTER_GAME_OVERRIDE("GA2 text zettai", "SLPM_662.43", 0x00100008u, 0x5E58C415u, bind<0>)
PS2_REGISTER_GAME_OVERRIDE("GA2 text mugen-1", "SLPM_667.79", 0x00100008u, 0x15F1CB0Du, bind<1>)
PS2_REGISTER_GAME_OVERRIDE("GA2 text mugen-2", "SLPM_667.80", 0x00100008u, 0x1F83C20Cu, bind<2>)
PS2_REGISTER_GAME_OVERRIDE("GA2 text eigou-1", "SLPM_551.18", 0x00100008u, 0x73DEC964u, bind<3>)
PS2_REGISTER_GAME_OVERRIDE("GA2 text eigou-2", "SLPM_551.19", 0x00100008u, 0xAAAC1E15u, bind<4>)
