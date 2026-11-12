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
#include "runtime/sha256.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
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
    // The same, found whether or not the icons are on (native text reads the sprite colour of its pieces).
    uint32_t g_colourBytes = 0;

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
    // A language pack's text/<program>.msg (tools/modding/restore_text.py) maps the disc's messages to the pack's:
    // such a text is swapped for its translation when the game lays it out (the scripts themselves already come
    // from the pack). Keyed by the SHA-256 of the disc's message bytes (first 16 bytes), so a pack carries no
    // original text: GA2MSG2 stores the digests, the older GA2MSG1 the bytes themselves (hashed when loaded).
    std::unordered_map<std::string, std::string> g_restore;

    std::string messageKey(std::string_view text)
    {
        ga2_sha256::Hasher hasher;
        hasher.update(text.data(), text.size());
        const std::array<uint8_t, 32> digest = hasher.finish();
        return std::string(reinterpret_cast<const char *>(digest.data()), 16);
    }

    void loadRestore(const char *program)
    {
        const char *list = std::getenv("GA2_MODS");
        if (!list)
            return;
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
            const bool v2 = data.size() >= 12 && data.compare(0, 8, std::string("GA2MSG2\0", 8)) == 0;
            if (!v2 && (data.size() < 12 || data.compare(0, 8, std::string("GA2MSG1\0", 8)) != 0))
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
                // v2: 16-byte key; v1: u16 length + original bytes. Then u16 length + translated bytes.
                const size_t a = v2 ? 16 : u16(at);
                const size_t keyAt = v2 ? at : at + 2;
                if (keyAt + a + 2 > data.size())
                    break;
                const size_t b = u16(keyAt + a);
                if (keyAt + a + 2 + b > data.size())
                    break;
                const std::string key = v2 ? data.substr(keyAt, 16) : messageKey(std::string_view(data).substr(keyAt, a));
                g_restore.emplace(key, data.substr(keyAt + a + 2, b));
                at = keyAt + a + 2 + b;
            }
            std::cerr << "[ga2 text] " << file.string() << ": " << count << " message translation(s) for restored saves\n";
        }
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
            if (const auto it = g_restore.find(messageKey(text)); it != g_restore.end())
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

    // ---- native text (sharp text) ----------------------------------------------------------------------------
    // With sharp text on, the port draws the games' text itself (ga2_hitext.h, "native text runs"): each drawer
    // call is laid out again, Latin characters with the font's own advances and kerning instead of the bitmap
    // font's widths and 10-pixel spaces, full-width ones at the game's advance, keeping the drawer's anchor (left,
    // right edge or centre) and its line breaks. Each piece of a line in one colour becomes a "ticket" character the
    // original drawer then draws in place of the text: one full-width sprite per piece, which the screen-rect setter
    // hook moves onto the piece's rectangle, and whose 24x24 bitmap the texture replacement answers with the piece
    // rendered whole from the vector font. Button icons and characters the font lacks stay the game's own glyphs,
    // placed in the new layout. The number drawer (fixed digit slots) is left alone.
    constexpr float kInv18 = 0.055555552f; // the drawers' size / 18
    // Tickets: kernel RAM between the native SDK pools (end 0x51000) and the invocation-stack arena (0x60000). The
    // games upload a glyph by reference (a DMA REF tag to the bitmap), so a ticket must stay unchanged until the
    // frame's packets are gone: a ring much longer than the pieces of a frame.
    constexpr uint32_t kTicketRam = 0x00052000u;
    constexpr uint32_t kTicketSlots = 184u;
    static_assert(kTicketRam + kTicketSlots * 288u <= 0x00060000u);
    constexpr uint32_t kTicketLead = 0xF8u; // Shift-JIS user-defined lead; trail 0x40 + slot
    // The records (message drawer) or the string (string drawers) the original draws instead: read during the call.
    constexpr uint32_t kSynthRam = 0x00070000u;
    constexpr uint32_t kSynthBytes = 0x2000u;
    static_assert(kSynthRam + kSynthBytes <= kIconRam);
    uint32_t g_ticketNext = 0;

    // A sprite of the synthetic draw: where the original will put it (p), where it goes (t).
    struct Piece
    {
        float px0, py0, px1, py1, tx0, ty0, tx1, ty1;
        uint64_t hash = 0;                               // a ticket: its piece (for the presentation)
        std::shared_ptr<const ga2_hitext::RunSpec> spec;
    };
    struct Plan
    {
        uint32_t lo = 0, hi = 0; // the drawer's code: the setter's return address lies inside
        std::vector<Piece> pieces;
        size_t next = 0;
    };
    Plan g_plan;
    uint32_t g_drawerRange[3][2] = {}; // message drawer, string drawers 1 and 2: [start, end)

    template <size_t I>
    PS2Runtime::RecompiledFunction g_setterOriginal = nullptr;

    bool fullWidthLead(uint32_t b) { return b - 0x81u < 0x1Fu || b - 0xE0u < 0x1Du; }

    void beginPlan(int which)
    {
        g_plan.lo = g_drawerRange[which][0];
        g_plan.hi = g_drawerRange[which][1];
        g_plan.pieces.clear();
        g_plan.next = 0;
    }

    // One character of a line.
    struct Ch
    {
        uint32_t code = 0;    // a byte, or lead | trail << 8 as the records keep it
        bool full = false;    // double-byte
        bool drawn = false;   // the drawer draws something for it
        float size = 18.0f;
        uint32_t colour = 0;
        float pen = 0.0f, cellTop = 0.0f;
    };

    uint32_t unicodeOf(const Ch &c)
    {
        if (!c.full)
            return c.code == 0x5Cu ? 0xA5u : c.code; // the games' backslash cell is a yen sign
        return ga2_hitext::sjisToUnicode((c.code & 0xFFu) << 8 | c.code >> 8);
    }

    // Pens from 0 for one line: half-width advances (the font's, with kerning) times `halfScale`, full-width ones
    // `fullAdvance` (size when < 0). Returns the line's width.
    float layoutLine(std::vector<Ch> &line, const ga2_hitext::LatinLayout &L, float halfScale, float fullAdvance)
    {
        float pen = 0.0f;
        int prev = -1;
        for (Ch &c : line)
        {
            const float sc = c.size * kInv18 * halfScale;
            if (c.full)
            {
                c.pen = pen;
                pen += fullAdvance < 0.0f ? c.size : fullAdvance;
                prev = -1;
            }
            else if (c.code - 0x21u < 0x5Eu)
            {
                const int k = int(c.code) - 0x20;
                pen += prev >= 0 ? L.kern[prev][k] * sc : 0.0f;
                c.pen = pen;
                pen += L.advance[k] * sc;
                prev = k;
            }
            else
            {
                c.pen = pen;
                pen += (c.code == 0x20u ? L.advance[0] : 10.0f) * sc;
                prev = -1;
            }
        }
        return pen;
    }

    // A piece for the synthetic draw: the character to draw and its rectangle.
    struct Out
    {
        uint32_t code, colour;
        float x0, y0, x1, y1;
        uint64_t hash = 0;
        std::shared_ptr<const ga2_hitext::RunSpec> spec;
    };

    Piece pieceFor(float px0, float py0, float px1, float py1, const Out &o)
    {
        return {px0, py0, px1, py1, o.x0, o.y0, o.x1, o.y1, o.hash, o.spec};
    }

    // The drawn characters of a laid-out line as pieces: runs of one colour become tickets; button icons and
    // characters the font lacks stay the game's (their own code, at their new place).
    void piecesOf(uint8_t *rdram, const std::vector<Ch> &line, std::vector<Out> &out)
    {
        auto own = [](const Ch &c) { return c.full && !ga2_hitext::fontHasGlyph(unicodeOf(c)); };
        size_t i = 0;
        while (i < line.size())
        {
            const Ch &c = line[i];
            if (!c.drawn)
            {
                ++i;
                continue;
            }
            if (own(c))
            {
                out.push_back({c.code, c.colour, c.pen, c.cellTop, c.pen + c.size, c.cellTop + c.size});
                ++i;
                continue;
            }
            float minX = 1e9f, maxX = -1e9f, minY = 1e9f, maxY = -1e9f;
            size_t j = i;
            for (; j < line.size(); ++j)
            {
                const Ch &d = line[j];
                if (!d.drawn)
                    continue;
                if (d.colour != c.colour || own(d))
                    break;
                // Generous: ink may reach past the advance (overhangs, descenders); the rest is transparent.
                minX = std::min(minX, d.pen - 0.2f * d.size);
                maxX = std::max(maxX, d.pen + 1.1f * d.size);
                minY = std::min(minY, d.cellTop - 0.1f * d.size);
                maxY = std::max(maxY, d.cellTop + 1.25f * d.size);
            }
            // The games sample their glyph texture with u, v from half a texel in (below) and clamped at texel 23
            // of 24: an image shows cleanly from 1/48 to 23/24 of it. The content (shifted in by 1/48) stays
            // inside that, so the piece is 48/45 of it.
            const float x0 = std::floor(minX), y0 = std::floor(minY);
            const float x1 = x0 + std::min(std::ceil((std::ceil(maxX) - x0) * 48.0f / 45.0f) + 1.0f, 1024.0f);
            const float y1 = y0 + std::min(std::ceil((std::ceil(maxY) - y0) * 48.0f / 45.0f) + 1.0f, 256.0f);
            auto spec = std::make_shared<ga2_hitext::RunSpec>();
            spec->width = int(x1 - x0);
            spec->height = int(y1 - y0);
            // The games' texel setter puts u, v half a texel in (u * 16 + 8): stretched over the piece, the ticket
            // shows its image from 1/48 of it in (and 1/48 past its far edge, the empty margin). The ticket's image
            // has its glyphs that much further in, so they land where they belong.
            spec->insetX = float(spec->width) / 48.0f;
            spec->insetY = float(spec->height) / 48.0f;
            for (size_t k = i; k < j; ++k)
                if (line[k].drawn)
                    spec->glyphs.push_back({unicodeOf(line[k]), line[k].pen - x0, line[k].cellTop - y0, line[k].size,
                                            line[k].full});
            const uint32_t slot = g_ticketNext++ % kTicketSlots;
            const uint64_t hash = ga2_hitext::noteRun(*spec, rdram + kTicketRam + slot * 288u);
            out.push_back({kTicketLead | (0x40u + slot) << 8, c.colour, x0, y0, x1, y1, hash, std::move(spec)});
            i = j;
        }
    }

    // A string as the string drawers read it.
    void parseString(const uint8_t *rdram, uint32_t p, float size, uint32_t colour, std::vector<Ch> &line)
    {
        for (int n = 0; n < 1024; ++n)
        {
            const uint32_t b = rdram[p & 0x01FFFFFFu];
            if (b == 0)
                break;
            Ch c;
            c.size = size;
            c.colour = colour;
            if (fullWidthLead(b))
            {
                const uint32_t trail = rdram[(p + 1u) & 0x01FFFFFFu];
                c.code = b | trail << 8;
                c.full = true;
                c.drawn = !(b == 0x81u && trail == 0x40u);
                p += 2;
                line.push_back(c);
                if (trail == 0)
                    break;
                continue;
            }
            c.code = b;
            c.drawn = b - 0x21u < 0x5Eu;
            line.push_back(c);
            ++p;
        }
    }

    // Message glyph drawer: f12 x, f13 y, f14 line spacing, a1 records {u8 0, u8 size, u16 code, u32 colour},
    // a2 count, a3 characters revealed, t0 alignment (0 left, 1 right edge at x, 2 centred on x). The original then
    // draws the pieces as records of one full-width character each (size 24, left-aligned, one line).
    void planMessage(uint8_t *rdram, R5900Context *ctx, const ga2_hitext::LatinLayout &L)
    {
        beginPlan(0);
        const uint32_t records = getRegU32(ctx, 5);
        const int count = std::min(int32_t(getRegU32(ctx, 6)), 0x200), revealed = std::min(int32_t(getRegU32(ctx, 7)), count);
        const uint32_t align = getRegU32(ctx, 8) & 3u;
        const float x = ctx->f[12], spacing = ctx->f[14];
        float y = ctx->f[13] - spacing;
        std::vector<Out> out;
        std::vector<Ch> line;
        for (int i = 0; i < revealed;)
        {
            line.clear();
            uint8_t tallest = 0;
            int end = i;
            for (; end < count; ++end)
            {
                const uint8_t *r = rdram + ((records + uint32_t(end) * 8u) & 0x01FFFFFFu);
                const uint32_t code = uint32_t(r[2] | r[3] << 8);
                tallest = std::max(tallest, r[1]);
                if (code == 10u)
                    break;
                Ch c;
                c.code = code;
                c.size = float(r[1]);
                std::memcpy(&c.colour, r + 4, 4);
                c.full = (code & 0xFF00u) != 0;
                const bool alpha = r[7] != 0;
                c.drawn = alpha && (c.full ? fullWidthLead(code & 0xFFu) && code != 0x4081u : code - 0x21u < 0x5Eu);
                line.push_back(c);
            }
            y += spacing + float(tallest);
            const float width = layoutLine(line, L, 1.0f, -1.0f);
            const float start = align == 1 ? x - width : align == 2 ? x - width * 0.5f : x;
            for (Ch &c : line)
            {
                c.pen += start;
                c.cellTop = y - c.size; // both cells end on the line: 18 * size / 18 high, or size
            }
            line.resize(size_t(std::max(0, std::min(end, revealed) - i)));
            piecesOf(rdram, line, out);
            i = end + 1;
        }
        const size_t n = std::min<size_t>(out.size(), kSynthBytes / 8u);
        const float y0 = ctx->f[13];
        for (size_t j = 0; j < n; ++j)
        {
            uint8_t *r = rdram + kSynthRam + j * 8u;
            r[0] = 0;
            r[1] = 24;
            r[2] = uint8_t(out[j].code & 0xFFu);
            r[3] = uint8_t(out[j].code >> 8);
            std::memcpy(r + 4, &out[j].colour, 4);
            const float px = x + 24.0f * float(j);
            g_plan.pieces.push_back(pieceFor(px, y0, px + 24.0f, y0 + 24.0f, out[j]));
        }
        SET_GPR_U32(ctx, 5, kSynthRam);
        SET_GPR_U32(ctx, 6, uint32_t(n));
        SET_GPR_U32(ctx, 7, uint32_t(n));
        SET_GPR_U32(ctx, 8, 0u);
    }

    // String drawers: f12 x, f13 y, a1 size, t0 text; drawer 1: a2 colour, a3 pad half-width characters to size / 2
    // (left to the game); drawer 2: a2 advance of a full-width character (half-width ones scale with it), a3
    // colour. The original then draws the pieces as a string of full-width characters. Returns the line's width.
    template <int D>
    bool planString(uint8_t *rdram, R5900Context *ctx, const ga2_hitext::LatinLayout &L, float &width)
    {
        beginPlan(D);
        if (D == 1 && getRegU32(ctx, 7) != 0)
            return false;
        const int size = int32_t(getRegU32(ctx, 5));
        if (size <= 0 || size > 255)
            return false;
        const float advance = D == 2 ? float(int32_t(getRegU32(ctx, 6))) : float(size);
        const uint32_t colour = getRegU32(ctx, D == 2 ? 7 : 6);
        const float x = ctx->f[12], y = ctx->f[13];
        std::vector<Ch> line;
        parseString(rdram, getRegU32(ctx, 8), float(size), colour, line);
        width = layoutLine(line, L, D == 2 ? advance / float(size) : 1.0f, D == 2 ? advance : -1.0f);
        for (Ch &c : line)
        {
            c.pen += x;
            c.cellTop = y; // both cells start at y here
        }
        std::vector<Out> out;
        piecesOf(rdram, line, out);
        const size_t n = std::min<size_t>(out.size(), kSynthBytes / 2u - 1u);
        uint8_t *s = rdram + kSynthRam;
        for (size_t j = 0; j < n; ++j)
        {
            s[2 * j] = uint8_t(out[j].code & 0xFFu);
            s[2 * j + 1] = uint8_t(out[j].code >> 8);
            const float px = x + advance * float(j);
            g_plan.pieces.push_back(pieceFor(px, y, px + float(size), y + float(size), out[j]));
        }
        s[2 * n] = 0;
        SET_GPR_U32(ctx, 8, kSynthRam);
        return true;
    }

    // The screen-rect setter (f12 x0, f13 y0, f14 x1, f15 y1): a sprite of the planned drawer call goes onto its
    // piece. By difference, so a widescreen HUD placement applied before this hook (ga2_features) is kept.
    template <size_t I>
    void screenRect(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t ra = getRegU32(ctx, 31);
        if (ra >= g_plan.lo && ra < g_plan.hi && g_plan.next < g_plan.pieces.size())
        {
            const Piece &p = g_plan.pieces[g_plan.next++];
            if (std::fabs(ctx->f[13] - p.py0) < 0.5f)
            {
                ctx->f[12] += p.tx0 - p.px0;
                ctx->f[13] += p.ty0 - p.py0;
                ctx->f[14] += p.tx1 - p.px1;
                ctx->f[15] += p.ty1 - p.py1;
                // The presentation draws the port's text again at the window's resolution.
                if (p.spec && g_colourBytes)
                {
                    ga2_hitext::TextPiece piece;
                    piece.hash = p.hash;
                    piece.spec = p.spec;
                    piece.x0 = ctx->f[12], piece.y0 = ctx->f[13], piece.x1 = ctx->f[14], piece.y1 = ctx->f[15];
                    piece.r = rdram[g_colourBytes], piece.g = rdram[g_colourBytes + 1u];
                    piece.b = rdram[g_colourBytes + 2u], piece.a = rdram[g_colourBytes + 3u];
                    ga2_hitext::notePiece(piece);
                }
            }
            else
                g_plan.next = g_plan.pieces.size(); // out of step: leave the rest of this call alone
        }
        g_setterOriginal<I>(rdram, ctx, runtime);
    }

    // The aligned string drawer (drawer 1 + 0x358: names on their plates, centred or right-aligned labels):
    // f12 x, f13 y, a1 size, t0 flags (& 3: 0 left, 1 right edge at x, 2 centred on x; & 4 pad; & 8 outline),
    // t1 text. It measures the string with the bitmap font's widths, moves x by that, then calls drawer 1, which
    // lays it out natively: x moves by the difference, so the string is aligned by its native width.
    template <size_t I>
    PS2Runtime::RecompiledFunction g_alignedOriginal = nullptr;

    template <size_t I>
    void aligned(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t flags = getRegU32(ctx, 8), align = flags & 3u;
        const int size = int32_t(getRegU32(ctx, 5));
        const ga2_hitext::LatinLayout *L = ga2_hitext::nativeLayout() ? ga2_hitext::latinLayout() : nullptr;
        if (L && (align == 1u || align == 2u) && (flags & 4u) == 0 && size > 0 && size < 256)
        {
            std::vector<Ch> line;
            parseString(rdram, getRegU32(ctx, 9), float(size), 0u, line);
            float game = 0.0f;
            for (const Ch &c : line)
            {
                if (c.full)
                    game += float(size);
                else
                {
                    int32_t w = 9;
                    if (c.code - 0x21u < 0x5Eu)
                        std::memcpy(&w, rdram + kDiscs[I].metrics + (c.code - 0x20u) * 12u + 8u, 4);
                    game += float(c.code - 0x21u < 0x5Eu ? w + 1 : 10) * float(size) * kInv18;
                }
            }
            const float native = layoutLine(line, *L, 1.0f, -1.0f);
            ctx->f[12] += (game - native) * (align == 1u ? 1.0f : 0.5f);
        }
        g_alignedOriginal<I>(rdram, ctx, runtime);
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
        if constexpr (D <= 2)
            if (g_setterOriginal<I>)
            {
                const ga2_hitext::LatinLayout *layout = ga2_hitext::nativeLayout() ? ga2_hitext::latinLayout() : nullptr;
                if (!layout)
                    beginPlan(D);
                else if constexpr (D == 0)
                    planMessage(rdram, ctx, *layout);
                else
                {
                    const float x = ctx->f[12];
                    float width = 0.0f;
                    if (planString<D>(rdram, ctx, *layout, width))
                    {
                        // They return where the string ends (drawer 1: x; drawer 2: the width), and callers put
                        // what follows there (the dots after a speaker's name): the native end. Only when the
                        // drawer ran to its end here (not when it went back to the dispatcher early).
                        const uint32_t ra = getRegU32(ctx, 31);
                        g_original<I, D>(rdram, ctx, runtime);
                        if (ctx->pc == ra)
                            ctx->f[0] = D == 1 ? x + width : width;
                        return;
                    }
                }
            }
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
        // A native text piece (planMessage / planString): its ticket.
        if (lead == kTicketLead && trail >= 0x40u && trail - 0x40u < kTicketSlots)
        {
            SET_GPR_U32(ctx, 2, kTicketRam + (trail - 0x40u) * 288u);
            return;
        }
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
        // Native text: the screen-rect setter sits 0x88 past the colour setter on every disc (same code), the
        // string drawers follow one another and the message drawer is 0x528 long (up to the font textures init).
        const uint32_t setter = d.setColour + 0x88u;
        if (runtime.hasFunction(d.setColour))
        {
            const uint32_t colour = colourAddress(runtime.memory().getRDRAM(), d.setColour);
            g_colourBytes = colour && colour < 0x02000000u ? colour : 0u;
        }
        if (runtime.hasFunction(setter))
        {
            g_drawerRange[0][0] = d.drawers[0], g_drawerRange[0][1] = d.drawers[0] + 0x528u;
            g_drawerRange[1][0] = d.drawers[1], g_drawerRange[1][1] = d.drawers[2];
            g_drawerRange[2][0] = d.drawers[2], g_drawerRange[2][1] = d.drawers[3];
            hook(setter, g_setterOriginal<I>, screenRect<I>);
            if (runtime.hasFunction(d.drawers[1] + 0x358u))
                hook(d.drawers[1] + 0x358u, g_alignedOriginal<I>, aligned<I>);
        }
        else
            std::cerr << "[ga2 text] " << d.disc << ": screen-rect setter 0x" << std::hex << setter << std::dec
                      << " not in the generated table; the games' own glyphs stay\n";
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
