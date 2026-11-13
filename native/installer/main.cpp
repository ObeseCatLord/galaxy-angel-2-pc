// Galaxy Angel II PC installer and updater: the window (raylib) and the command line. See installer.h.
#include "installer.h"
#include "platform.h"

#include "raylib.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <optional>
#include <cstdio>
#include <cstring>
#include <memory>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "installer_font.h" // kInstallerFont, kInstallerFontSize (tools/installer_font.py)

#ifdef _WIN32
extern "C" __declspec(dllimport) int __stdcall AttachConsole(unsigned long);
extern "C" __declspec(dllimport) wchar_t *__stdcall GetCommandLineW();
extern "C" __declspec(dllimport) wchar_t **__stdcall CommandLineToArgvW(const wchar_t *, int *);
extern "C" __declspec(dllimport) void *__stdcall LocalFree(void *);
extern "C" __declspec(dllimport) unsigned short __stdcall GetUserDefaultUILanguage();
#endif

namespace
{
    using inst::fs::path;

    // --- text in both languages ----------------------------------------------------------------------------

    enum class Lang
    {
        En,
        Ja
    };
    Lang g_lang = Lang::En;

    struct Text
    {
        const char *en, *ja;
    };
    const char *tr(const Text &t) { return g_lang == Lang::Ja ? t.ja : t.en; }

    // Every string the window shows (tools/installer_font.py takes the font's characters from this file).
    namespace T
    {
        const Text subtitle{"Installer and updater", "インストーラー・アップデーター"};
        const Text folder{"Install folder", "インストール先"};
        const Text browse{"Browse...", "参照..."};
        const Text installed{"Installed", "インストール済み"};
        const Text latest{"Newest release", "最新リリース"};
        const Text notInstalled{"not installed", "未インストール"};
        const Text unknown{"unknown version", "バージョン不明"};
        const Text checking{"checking...", "確認中..."};
        const Text offline{"could not check", "確認できませんでした"};
        const Text whatsNew{"What's new in", "更新内容"};
        const Text install{"Install", "インストール"};
        const Text update{"Update to", "アップデート"};
        const Text reinstall{"Reinstall", "再インストール"};
        const Text play{"Play", "プレイ"};
        const Text openFolder{"Open folder", "フォルダを開く"};
        const Text uninstall{"Uninstall", "アンインストール"};
        const Text retry{"Check again", "再確認"};
        const Text cancel{"Cancel", "キャンセル"};
        const Text shortcuts{"Create menu and desktop shortcuts", "スタートメニュー・デスクトップにショートカットを作成"};
        const Text shortcutsLinux{"Add to the application menu", "アプリケーションメニューに追加"};
        const Text upToDate{"Up to date.", "最新です。"};
        const Text updateReady{"An update is available.", "アップデートがあります。"};
        const Text readyToInstall{"Ready to install.", "インストールの準備ができました。"};
        const Text fetching{"Checking for the newest release...", "最新リリースを確認しています..."};
        const Text downloading{"Downloading", "ダウンロード中"};
        const Text verifying{"Checking the download", "ダウンロードを確認中"};
        const Text extracting{"Unpacking", "展開中"};
        const Text installing{"Installing files", "ファイルをインストール中"};
        const Text removing{"Removing files", "ファイルを削除中"};
        const Text done{"Done: Galaxy Angel II PC", "完了: Galaxy Angel II PC"};
        const Text removed{"Galaxy Angel II PC was removed. Your saves and settings were kept in", "Galaxy Angel II PC を削除しました。セーブと設定は次の場所に残っています:"};
        const Text failed{"Failed", "失敗"};
        const Text cancelled{"Cancelled.", "キャンセルしました。"};
        const Text dataNote{"Saves, settings and imported discs stay in", "セーブ・設定・取り込んだディスクの保存先:"};
        const Text dataNote2{"(never changed by the installer)", "（インストーラーは変更しません）"};
        const Text page{"Project page", "プロジェクトページ"};
        const Text selfUpdate{"A newer installer is available:", "新しいインストーラーがあります:"};
        const Text selfUpdateButton{"Update installer", "インストーラーを更新"};
        const Text confirmUninstall{"Remove Galaxy Angel II PC from this folder?", "このフォルダから Galaxy Angel II PC を削除しますか？"};
        const Text confirmUninstall2{"Saves, settings and imported discs are kept.", "セーブ・設定・取り込んだディスクは残ります。"};
        const Text yesRemove{"Remove", "削除"};
        const Text notEmpty{"This folder holds other files. Choose an empty folder.", "このフォルダには他のファイルがあります。空のフォルダを選んでください。"};
        const Text running{"The game is running from this folder. Close it to update.", "このフォルダのゲームが起動中です。アップデートするには終了してください。"};
        const Text noBuild{"The newest release has no build for this system.", "最新リリースにはこのシステム用のビルドがありません。"};
        const Text language{"日本語", "English"};
    }

    // --- drawing -------------------------------------------------------------------------------------------

    struct Palette
    {
        Color background{12, 16, 34, 255}, panel{24, 32, 64, 255}, panel2{32, 42, 82, 255}, accent{120, 180, 255, 255};
        Color text{230, 236, 250, 255}, dim{130, 140, 170, 255}, warn{255, 190, 90, 255}, ok{120, 220, 150, 255}, bad{255, 120, 120, 255};
    } P;

    struct Fonts
    {
        Font small{}, normal{}, large{};
    } F;

    std::vector<int> fontCodepoints()
    {
        std::set<int> set;
        for (int c = 32; c < 127; ++c)
            set.insert(c);
        for (int c = 0xA0; c <= 0xFF; ++c)
            set.insert(c);
        for (int c : {0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026, 0x2190, 0x2191, 0x2192, 0x2193, 0x21B5, 0x2713})
            set.insert(c);
        // The characters of the Japanese texts.
        const Lang saved = g_lang;
        g_lang = Lang::Ja;
        const Text *all[] = {&T::subtitle, &T::folder, &T::browse, &T::installed, &T::latest, &T::notInstalled, &T::unknown,
                             &T::checking, &T::offline, &T::whatsNew, &T::install, &T::update, &T::reinstall, &T::play,
                             &T::openFolder, &T::uninstall, &T::retry, &T::cancel, &T::shortcuts, &T::shortcutsLinux,
                             &T::upToDate, &T::updateReady, &T::readyToInstall, &T::fetching, &T::downloading, &T::verifying,
                             &T::extracting, &T::installing, &T::removing, &T::done, &T::removed, &T::failed, &T::cancelled,
                             &T::dataNote, &T::dataNote2, &T::page, &T::selfUpdate, &T::selfUpdateButton,
                             &T::confirmUninstall, &T::confirmUninstall2, &T::yesRemove, &T::notEmpty, &T::running,
                             &T::noBuild, &T::language};
        for (const Text *t : all)
            for (const char *s : {t->en, t->ja})
            {
                int count = 0;
                if (int *cps = LoadCodepoints(s, &count))
                {
                    for (int i = 0; i < count; ++i)
                        set.insert(cps[i]);
                    UnloadCodepoints(cps);
                }
            }
        g_lang = saved;
        return {set.begin(), set.end()};
    }

    void loadFonts()
    {
        std::vector<int> cps = fontCodepoints();
        auto load = [&](int size) {
            Font f = LoadFontFromMemory(".otf", kInstallerFont, int(kInstallerFontSize), size, cps.data(), int(cps.size()));
            SetTextureFilter(f.texture, TEXTURE_FILTER_BILINEAR);
            return f;
        };
        F.small = load(17);
        F.normal = load(21);
        F.large = load(36);
    }

    float textWidth(const Font &font, const std::string &text) { return MeasureTextEx(font, text.c_str(), float(font.baseSize), 0).x; }
    void text(const Font &font, const std::string &s, float x, float y, Color color)
    {
        DrawTextEx(font, s.c_str(), {std::round(x), std::round(y)}, float(font.baseSize), 0, color);
    }

    // Splits at spaces, and anywhere between CJK characters.
    std::vector<std::string> wrap(const Font &font, const std::string &s, float width)
    {
        std::vector<std::string> lines;
        std::string line, word;
        auto flushWord = [&] {
            if (word.empty()) return;
            const std::string candidate = line.empty() ? word : line + word;
            if (textWidth(font, candidate) > width && !line.empty())
            {
                while (!line.empty() && line.back() == ' ')
                    line.pop_back();
                lines.push_back(line);
                line = word[0] == ' ' ? word.substr(1) : word;
            }
            else
                line = candidate;
            word.clear();
        };
        for (size_t i = 0; i < s.size();)
        {
            int bytes = 1;
            const int cp = GetCodepointNext(s.c_str() + i, &bytes);
            const std::string ch = s.substr(i, size_t(bytes));
            i += size_t(bytes);
            if (cp == '\n')
            {
                flushWord();
                lines.push_back(line);
                line.clear();
                continue;
            }
            if (cp == ' ')
            {
                flushWord();
                word = " ";
                continue;
            }
            word += ch;
            if (cp >= 0x3000) flushWord(); // CJK: each character may end a line
        }
        flushWord();
        if (!line.empty()) lines.push_back(line);
        return lines;
    }

    // The release notes (Markdown) as plain lines: headings, bullets, no emphasis marks.
    std::vector<std::pair<std::string, bool>> notesLines(const std::string &markdown)
    {
        std::vector<std::pair<std::string, bool>> out;
        size_t start = 0;
        while (start <= markdown.size())
        {
            size_t end = markdown.find('\n', start);
            if (end == std::string::npos) end = markdown.size();
            std::string line = markdown.substr(start, end - start);
            start = end + 1;
            if (!line.empty() && line.back() == '\r') line.pop_back();
            bool heading = false;
            if (line.rfind("#", 0) == 0)
            {
                heading = true;
                line.erase(0, line.find_first_not_of("# "));
            }
            // The font has no return arrow.
            for (size_t at; (at = line.find("\xE2\x86\xB5")) != std::string::npos;)
                line.replace(at, 3, "Enter");
            std::string clean;
            for (size_t i = 0; i < line.size(); ++i)
            {
                if (line.compare(i, 2, "**") == 0)
                {
                    ++i;
                    continue;
                }
                if (line[i] == '`') continue;
                clean += line[i];
            }
            if (clean.rfind("- ", 0) == 0) clean = "\xE2\x80\xA2 " + clean.substr(2); // •
            else if (clean.rfind("  - ", 0) == 0) clean = "    \xE2\x80\x93 " + clean.substr(4);
            out.emplace_back(clean, heading);
        }
        while (!out.empty() && out.back().first.empty())
            out.pop_back();
        return out;
    }

    bool g_mouseUsed = false;

    bool button(Rectangle r, const std::string &label, bool primary, bool enabled = true)
    {
        const bool hover = enabled && CheckCollisionPointRec(GetMousePosition(), r);
        Color fill = primary ? Color{60, 110, 210, 255} : P.panel2;
        if (hover) fill = primary ? Color{80, 135, 235, 255} : Color{48, 60, 110, 255};
        if (!enabled) fill = Color{30, 36, 60, 255};
        DrawRectangleRounded(r, 0.25f, 6, fill);
        if (primary && enabled) DrawRectangleRoundedLinesEx(r, 0.25f, 6, 1.5f, P.accent);
        const Font &f = F.normal;
        text(f, label, r.x + (r.width - textWidth(f, label)) / 2, r.y + (r.height - float(f.baseSize)) / 2 - 1,
             enabled ? P.text : P.dim);
        if (hover) SetMouseCursor(MOUSE_CURSOR_POINTING_HAND), g_mouseUsed = true;
        return hover && IsMouseButtonReleased(MOUSE_BUTTON_LEFT);
    }

    bool checkbox(float x, float y, const std::string &label, bool &value, bool enabled)
    {
        const Rectangle box{x, y, 20, 20};
        const Rectangle hit{x, y - 2, 30 + textWidth(F.normal, label), 24};
        const bool hover = enabled && CheckCollisionPointRec(GetMousePosition(), hit);
        DrawRectangleRounded(box, 0.25f, 4, hover ? Color{48, 60, 110, 255} : P.panel2);
        if (value)
        {
            DrawLineEx({x + 4, y + 10}, {x + 8.5f, y + 15}, 2.5f, P.accent);
            DrawLineEx({x + 8.5f, y + 15}, {x + 16, y + 5}, 2.5f, P.accent);
        }
        text(F.normal, label, x + 30, y - 1, enabled ? P.text : P.dim);
        if (hover) SetMouseCursor(MOUSE_CURSOR_POINTING_HAND), g_mouseUsed = true;
        if (hover && IsMouseButtonReleased(MOUSE_BUTTON_LEFT))
        {
            value = !value;
            return true;
        }
        return false;
    }

    std::string megabytes(uint64_t bytes)
    {
        char buffer[32];
        std::snprintf(buffer, sizeof(buffer), "%.1f MB", double(bytes) / 1e6);
        return buffer;
    }

    // --- the window ---------------------------------------------------------------------------------------

    class App
    {
    public:
        App(path dir, bool confirmUninstall) : m_dirText(inst::pathUtf8(dir)), m_confirmUninstall(confirmUninstall)
        {
            m_dir = dir;
            refreshInstalled();
            startManifest();
        }
        ~App()
        {
            if (!m_worker.joinable()) return;
            m_progress->cancel = true;
            // A download may sit in a network call for a while: let it end with the process. Installing waits.
            if (m_progress->stage.load() <= inst::Verifying)
                m_worker.detach();
            else
                m_worker.join();
        }

        // Closing is refused while files are being moved (an interrupted install would need its rollback).
        bool canClose() const
        {
            if (!m_worker.joinable()) return true;
            const int stage = m_progress->stage.load();
            return stage <= inst::Verifying;
        }

        void frame()
        {
            g_mouseUsed = false;
            pollWorker();
            BeginDrawing();
            ClearBackground(P.background);
            const float W = float(GetScreenWidth());
            const float H = float(GetScreenHeight());
            const bool busy = m_worker.joinable();

            // Header.
            text(F.large, "Galaxy Angel II PC", 32, 22, P.text);
            text(F.normal, std::string(tr(T::subtitle)) + "  " + inst::installerVersion(), 34, 66, P.dim);
            if (button({W - 132, 26, 100, 34}, tr(T::language), false, true)) g_lang = g_lang == Lang::En ? Lang::Ja : Lang::En;

            float y = 104;
            // Newer installer.
            if (m_manifest && m_manifest->installer && !m_manifest->installerVersion.empty() &&
                inst::compareVersions(m_manifest->installerVersion, inst::installerVersion()) > 0)
            {
                DrawRectangleRounded({32, y, W - 64, 44}, 0.2f, 6, Color{60, 50, 20, 255});
                text(F.normal, std::string(tr(T::selfUpdate)) + " " + m_manifest->installerVersion, 48, y + 11, P.warn);
                if (button({W - 232, y + 6, 184, 32}, tr(T::selfUpdateButton), false, !busy)) startSelfUpdate();
                y += 56;
            }

            // Install folder.
            text(F.small, tr(T::folder), 34, y, P.dim);
            y += 22;
            const float browseWidth = inst::canPickFolder() ? 130.f : 0.f;
            folderField({32, y, W - 64 - (browseWidth > 0 ? browseWidth + 10 : 0), 38}, !busy);
            if (browseWidth > 0 && button({W - 32 - browseWidth, y, browseWidth, 38}, tr(T::browse), false, !busy))
                if (auto picked = inst::pickFolder(m_dir))
                {
                    path chosen = *picked;
                    std::error_code error;
                    // A non-empty folder without the game gets its own subfolder.
                    if (fs_nonEmptyForeign(chosen)) chosen /= "Galaxy Angel II PC";
                    setDir(chosen);
                }
            y += 52;

            // Versions.
            const float col = 34;
            text(F.normal, std::string(tr(T::installed)) + ":", col, y, P.dim);
            std::string installedText = !m_installed.present ? tr(T::notInstalled)
                                        : m_installed.version.empty() ? tr(T::unknown)
                                                                       : m_installed.version;
            text(F.normal, installedText, col + 190, y, m_installed.present ? P.text : P.dim);
            text(F.normal, std::string(tr(T::latest)) + ":", W / 2, y, P.dim);
            std::string latestText = m_manifest ? m_manifest->version + (m_manifest->date.empty() ? "" : "  (" + m_manifest->date + ")")
                                     : m_manifestError.empty() ? tr(T::checking)
                                                               : tr(T::offline);
            text(F.normal, latestText, W / 2 + 170, y, m_manifest ? P.ok : (m_manifestError.empty() ? P.dim : P.bad));
            y += 34;

            // Release notes.
            const float notesTop = y, notesHeight = H - y - 196;
            DrawRectangleRounded({32, notesTop, W - 64, notesHeight}, 0.03f, 6, P.panel);
            if (m_manifest)
            {
                text(F.normal, std::string(tr(T::whatsNew)) + " " + m_manifest->version, 48, notesTop + 12, P.accent);
                notes({48, notesTop + 44, W - 96, notesHeight - 54});
            }
            else if (!m_manifestError.empty())
            {
                float ly = notesTop + 14;
                for (const auto &line : wrap(F.normal, m_manifestError, W - 96))
                {
                    text(F.normal, line, 48, ly, P.bad);
                    ly += 26;
                }
                if (button({48, ly + 8, 170, 36}, tr(T::retry), true, !busy)) startManifest();
            }
            else
                text(F.normal, tr(T::fetching), 48, notesTop + 14, P.dim);
            y = notesTop + notesHeight + 14;

            // Shortcuts.
#ifdef _WIN32
            checkbox(34, y, tr(T::shortcuts), m_shortcuts, !busy);
#else
            checkbox(34, y, tr(T::shortcutsLinux), m_shortcuts, !busy);
#endif
            y += 34;

            // Status / progress.
            status({32, y, W - 64, 46});
            y += 58;

            // Buttons.
            const bool canInstall = m_manifest && m_manifest->package && !busy;
            const bool newer = m_manifest && (!m_installed.present || m_installed.version.empty() ||
                                              inst::compareVersions(m_manifest->version, m_installed.version) > 0);
            float x = 32;
            if (busy && m_progress->stage.load() == inst::Downloading)
            {
                if (button({x, y, 200, 44}, tr(T::cancel), false, true)) m_progress->cancel = true;
                x += 212;
            }
            else if (!m_installed.present || newer)
            {
                const std::string label = !m_installed.present ? std::string(tr(T::install)) + (m_manifest ? " " + m_manifest->version : "")
                                                               : std::string(tr(T::update)) + " " + m_manifest->version;
                if (button({x, y, 240, 44}, label, true, canInstall)) startInstall();
                x += 252;
            }
            if (m_installed.present)
            {
                if (button({x, y, 160, 44}, tr(T::play), !newer, !busy))
                {
                    try
                    {
                        inst::launchGame(m_dir);
                        m_quit = true;
                    }
                    catch (const std::exception &e)
                    {
                        m_message = e.what();
                        m_messageColor = P.bad;
                    }
                }
                x += 172;
                if (!newer && button({x, y, 180, 44}, tr(T::reinstall), false, canInstall)) startInstall();
                if (!newer) x += 192;
                if (button({x, y, 170, 44}, tr(T::openFolder), false, true)) inst::openPath(inst::pathUtf8(m_dir));
                x += 182;
                if (m_installed.recorded && button({W - 32 - 180, y, 180, 44}, tr(T::uninstall), false, !busy)) m_confirmUninstall = true;
            }
            y += 58;

            // Footer: where the saves are.
            const std::string data = std::string(tr(T::dataNote)) + " " + inst::pathUtf8(inst::dataDir()) + " " + tr(T::dataNote2);
            text(F.small, data, 34, H - 30, P.dim);
            const std::string link = tr(T::page);
            const float lw = textWidth(F.small, link);
            const Rectangle linkRect{W - 32 - lw, H - 30, lw, 20};
            const bool linkHover = CheckCollisionPointRec(GetMousePosition(), linkRect);
            text(F.small, link, linkRect.x, linkRect.y, linkHover ? P.text : P.accent);
            if (linkHover)
            {
                SetMouseCursor(MOUSE_CURSOR_POINTING_HAND);
                g_mouseUsed = true;
                if (IsMouseButtonReleased(MOUSE_BUTTON_LEFT))
                    inst::openPath(m_manifest && !m_manifest->page.empty() ? m_manifest->page
                                                                           : std::string("https://github.com/") + GA2_INSTALLER_REPO);
            }

            if (m_confirmUninstall) confirmDialog(W, H, busy);
            if (!g_mouseUsed && !m_editing) SetMouseCursor(MOUSE_CURSOR_DEFAULT);
            EndDrawing();
        }

        bool quit() const { return m_quit; }

    private:
        static bool fs_nonEmptyForeign(const path &dir) { return !inst::folderUsable(dir); }

        void setDir(const path &dir)
        {
            m_dir = dir;
            m_dirText = inst::pathUtf8(dir);
            refreshInstalled();
        }

        void refreshInstalled()
        {
            m_installed = inst::readInstalled(m_dir);
            m_message.clear();
            if (fs_nonEmptyForeign(m_dir))
            {
                m_message = tr(T::notEmpty);
                m_messageColor = P.warn;
            }
        }

        void folderField(Rectangle r, bool enabled)
        {
            const bool hover = enabled && CheckCollisionPointRec(GetMousePosition(), r);
            if (hover)
            {
                SetMouseCursor(MOUSE_CURSOR_IBEAM);
                g_mouseUsed = true;
            }
            if (IsMouseButtonPressed(MOUSE_BUTTON_LEFT))
            {
                if (hover && !m_editing)
                    m_editing = true;
                else if (!hover && m_editing)
                    commitDir();
            }
            DrawRectangleRounded(r, 0.2f, 6, P.panel);
            if (m_editing) DrawRectangleRoundedLinesEx(r, 0.2f, 6, 1.5f, P.accent);
            if (m_editing && enabled)
            {
                for (int c = GetCharPressed(); c > 0; c = GetCharPressed())
                {
                    int bytes = 0;
                    m_dirText += CodepointToUTF8(c, &bytes);
                }
                if ((IsKeyPressed(KEY_BACKSPACE) || IsKeyPressedRepeat(KEY_BACKSPACE)) && !m_dirText.empty())
                {
                    // One UTF-8 character.
                    size_t cut = m_dirText.size() - 1;
                    while (cut > 0 && (static_cast<unsigned char>(m_dirText[cut]) & 0xC0) == 0x80)
                        --cut;
                    m_dirText.erase(cut);
                }
                const bool ctrl = IsKeyDown(KEY_LEFT_CONTROL) || IsKeyDown(KEY_RIGHT_CONTROL);
                if (ctrl && IsKeyPressed(KEY_V))
                    if (const char *clip = GetClipboardText()) m_dirText += clip;
                if (ctrl && IsKeyPressed(KEY_A)) m_dirText.clear();
                if (IsKeyPressed(KEY_ENTER) || IsKeyPressed(KEY_KP_ENTER)) commitDir();
                if (IsKeyPressed(KEY_ESCAPE))
                {
                    m_dirText = inst::pathUtf8(m_dir);
                    m_editing = false;
                }
            }
            // The end of a long path stays visible.
            std::string shown = m_dirText;
            while (shown.size() > 4 && textWidth(F.normal, "..." + shown) > r.width - 24)
            {
                int bytes = 1;
                GetCodepointNext(shown.c_str(), &bytes);
                shown.erase(0, size_t(bytes));
            }
            if (shown.size() != m_dirText.size()) shown = "..." + shown;
            BeginScissorMode(int(r.x), int(r.y), int(r.width), int(r.height));
            text(F.normal, shown, r.x + 12, r.y + 8, enabled ? P.text : P.dim);
            if (m_editing && std::fmod(GetTime(), 1.0) < 0.55)
            {
                const float cx = r.x + 12 + textWidth(F.normal, shown) + 1;
                DrawRectangle(int(cx), int(r.y + 8), 2, 22, P.accent);
            }
            EndScissorMode();
        }

        void commitDir()
        {
            m_editing = false;
            while (!m_dirText.empty() && (m_dirText.back() == ' ' || m_dirText.back() == '\n')) m_dirText.pop_back();
            if (m_dirText.empty())
            {
                m_dirText = inst::pathUtf8(m_dir);
                return;
            }
            setDir(inst::pathFromUtf8(m_dirText));
        }

        void notes(Rectangle r)
        {
            if (m_notesFor != m_manifest->version + (g_lang == Lang::Ja ? "j" : "e") + std::to_string(int(r.width)))
            {
                m_notesFor = m_manifest->version + (g_lang == Lang::Ja ? "j" : "e") + std::to_string(int(r.width));
                m_noteLines.clear();
                for (const auto &[line, heading] : notesLines(m_manifest->notes))
                {
                    if (line.empty())
                    {
                        m_noteLines.push_back({"", false, 10});
                        continue;
                    }
                    const bool bullet = line.rfind("\xE2\x80\xA2 ", 0) == 0;
                    bool first = true;
                    for (const auto &part : wrap(F.small, line, r.width - (bullet ? 18 : 0) - 14))
                    {
                        m_noteLines.push_back({(bullet && !first ? "   " : "") + part, heading, 22});
                        first = false;
                    }
                }
            }
            float total = 0;
            for (const auto &l : m_noteLines)
                total += l.height;
            if (CheckCollisionPointRec(GetMousePosition(), r)) m_scroll -= GetMouseWheelMove() * 44;
            m_scroll = std::clamp(m_scroll, 0.f, std::max(0.f, total - r.height));
            BeginScissorMode(int(r.x), int(r.y), int(r.width), int(r.height));
            float y = r.y - m_scroll;
            for (const auto &l : m_noteLines)
            {
                if (y + l.height >= r.y && y <= r.y + r.height) text(F.small, l.text, r.x, y, l.heading ? P.accent : P.text);
                y += l.height;
            }
            EndScissorMode();
            if (total > r.height)
            {
                const float barHeight = std::max(30.f, r.height * r.height / total);
                const float barY = r.y + (r.height - barHeight) * (m_scroll / (total - r.height));
                DrawRectangleRounded({r.x + r.width + 4, barY, 5, barHeight}, 1.f, 4, P.panel2);
            }
        }

        void status(Rectangle r)
        {
            if (m_worker.joinable())
            {
                const int stage = m_progress->stage.load();
                const uint64_t done = m_progress->done.load(), total = m_progress->total.load();
                std::string label;
                switch (stage)
                {
                case inst::Downloading:
                    label = std::string(tr(T::downloading)) + "  " + megabytes(done) + (total ? " / " + megabytes(total) : "");
                    if (m_rateTime > 0)
                    {
                        const double now = GetTime();
                        if (now - m_rateTime > 1.0)
                        {
                            m_rate = double(done - m_rateBytes) / (now - m_rateTime);
                            m_rateTime = now;
                            m_rateBytes = done;
                        }
                        if (m_rate > 0) label += "   " + megabytes(uint64_t(m_rate)) + "/s";
                    }
                    else
                    {
                        m_rateTime = GetTime();
                        m_rateBytes = done;
                    }
                    break;
                case inst::Verifying: label = tr(T::verifying); break;
                case inst::Extracting: label = tr(T::extracting); break;
                case inst::Installing: label = tr(T::installing); break;
                case inst::Removing: label = tr(T::removing); break;
                default: label = "..."; break;
                }
                text(F.normal, label, r.x + 2, r.y, P.text);
                const Rectangle bar{r.x, r.y + 30, r.width, 12};
                DrawRectangleRounded(bar, 1.f, 6, P.panel);
                if (total > 0)
                    DrawRectangleRounded({bar.x, bar.y, bar.width * float(std::min<double>(1.0, double(done) / double(total))), bar.height}, 1.f, 6, P.accent);
                else
                {
                    // Unknown length: a sliding block.
                    const float t = float(std::fmod(GetTime() * 0.6, 1.0));
                    DrawRectangleRounded({bar.x + (bar.width - 120) * t, bar.y, 120, bar.height}, 1.f, 6, P.accent);
                }
                return;
            }
            std::string line = m_message;
            Color color = m_messageColor;
            if (line.empty() && m_manifest)
            {
                if (!m_manifest->package)
                    line = tr(T::noBuild), color = P.bad;
                else if (!m_installed.present)
                    line = tr(T::readyToInstall), color = P.dim;
                else if (!m_installed.version.empty() && inst::compareVersions(m_manifest->version, m_installed.version) <= 0)
                    line = tr(T::upToDate), color = P.ok;
                else
                    line = tr(T::updateReady), color = P.warn;
            }
            float y = r.y;
            for (const auto &part : wrap(F.normal, line, r.width))
            {
                text(F.normal, part, r.x + 2, y, color);
                y += 24;
            }
        }

        void confirmDialog(float W, float H, bool busy)
        {
            DrawRectangle(0, 0, int(W), int(H), Color{0, 0, 0, 160});
            const Rectangle box{W / 2 - 300, H / 2 - 100, 600, 200};
            DrawRectangleRounded(box, 0.08f, 8, P.panel);
            DrawRectangleRoundedLinesEx(box, 0.08f, 8, 1.5f, P.accent);
            text(F.normal, tr(T::confirmUninstall), box.x + 28, box.y + 30, P.text);
            text(F.small, tr(T::confirmUninstall2), box.x + 28, box.y + 66, P.dim);
            if (button({box.x + box.width - 28 - 150, box.y + box.height - 64, 150, 42}, tr(T::yesRemove), true, !busy))
            {
                m_confirmUninstall = false;
                startUninstall();
            }
            if (button({box.x + box.width - 28 - 150 - 12 - 150, box.y + box.height - 64, 150, 42}, tr(T::cancel), false, true))
                m_confirmUninstall = false;
        }

        // --- background work ---

        void joinWorker()
        {
            if (m_worker.joinable())
            {
                m_progress->cancel = true;
                m_worker.join();
            }
        }

        template <class Fn> void startWorker(Fn fn)
        {
            joinWorker();
            m_progress = std::make_shared<inst::Progress>();
            m_result = std::make_shared<Result>();
            m_rateTime = 0;
            m_rate = 0;
            auto progress = m_progress;
            auto result = m_result;
            m_worker = std::thread([fn, progress, result] {
                try
                {
                    fn(*progress, *result);
                }
                catch (const inst::Cancelled &)
                {
                    result->cancelled = true;
                }
                catch (const std::exception &e)
                {
                    result->error = e.what();
                }
                result->finished = true;
            });
        }

        struct Result
        {
            std::atomic<bool> finished{false};
            bool cancelled = false;
            std::string error;
            std::optional<inst::Manifest> manifest;
            enum Kind
            {
                Manifest,
                Install,
                Uninstall,
                SelfUpdate
            } kind = Manifest;
        };

        void startManifest()
        {
            m_manifestError.clear();
            startWorker([](inst::Progress &p, Result &r) {
                r.kind = Result::Manifest;
                p.stage = inst::FetchingManifest;
                r.manifest = inst::parseManifest(inst::fetchText(inst::manifestSource(), p));
            });
        }

        void startInstall()
        {
            if (inst::gameRunning(m_dir))
            {
                m_message = tr(T::running);
                m_messageColor = P.bad;
                return;
            }
            const inst::Manifest manifest = *m_manifest;
            const path dir = m_dir;
            const bool shortcuts = m_shortcuts;
            m_message.clear();
            startWorker([manifest, dir, shortcuts](inst::Progress &p, Result &r) {
                r.kind = Result::Install;
                inst::InstallOptions options;
                options.shortcuts = shortcuts;
                inst::install(dir, manifest, options, p);
            });
        }

        void startUninstall()
        {
            const path dir = m_dir;
            m_message.clear();
            startWorker([dir](inst::Progress &p, Result &r) {
                r.kind = Result::Uninstall;
                inst::uninstall(dir, p);
            });
        }

        void startSelfUpdate()
        {
            const inst::Manifest manifest = *m_manifest;
            const std::vector<std::string> args{"--dir", inst::pathUtf8(m_dir)};
            startWorker([manifest, args](inst::Progress &p, Result &r) {
                r.kind = Result::SelfUpdate;
                inst::selfUpdate(manifest, args, p);
            });
        }

        void pollWorker()
        {
            if (!m_worker.joinable() || !m_result->finished) return;
            m_worker.join();
            Result &r = *m_result;
            if (r.kind == Result::Manifest)
            {
                if (r.manifest)
                    m_manifest = r.manifest;
                else
                    m_manifestError = std::string(tr(T::offline)) + ": " + (r.error.empty() ? "?" : r.error);
                return;
            }
            if (r.cancelled)
            {
                m_message = tr(T::cancelled);
                m_messageColor = P.dim;
            }
            else if (!r.error.empty())
            {
                m_message = std::string(tr(T::failed)) + ": " + r.error;
                m_messageColor = P.bad;
            }
            else if (r.kind == Result::SelfUpdate)
                m_quit = true; // the new installer has started
            else if (r.kind == Result::Uninstall)
            {
                m_installed = inst::readInstalled(m_dir);
                m_message = std::string(tr(T::removed)) + " " + inst::pathUtf8(inst::dataDir());
                m_messageColor = P.ok;
                return;
            }
            else
            {
                m_installed = inst::readInstalled(m_dir);
                m_message = std::string(tr(T::done)) + " " + m_installed.version;
                m_messageColor = P.ok;
                return;
            }
            const std::string keep = m_message;
            const Color keepColor = m_messageColor;
            m_installed = inst::readInstalled(m_dir);
            m_message = keep;
            m_messageColor = keepColor;
        }

        struct NoteLine
        {
            std::string text;
            bool heading;
            float height;
        };

        path m_dir;
        std::string m_dirText;
        bool m_editing = false;
        inst::Installed m_installed;
        std::optional<inst::Manifest> m_manifest;
        std::string m_manifestError;
        std::thread m_worker;
        std::shared_ptr<inst::Progress> m_progress = std::make_shared<inst::Progress>();
        std::shared_ptr<Result> m_result;
        std::string m_message;
        Color m_messageColor = P.dim;
        bool m_shortcuts = true;
        bool m_confirmUninstall = false;
        bool m_quit = false;
        std::string m_notesFor;
        std::vector<NoteLine> m_noteLines;
        float m_scroll = 0;
        double m_rateTime = 0, m_rate = 0;
        uint64_t m_rateBytes = 0;
    };

    // --- the command line ---------------------------------------------------------------------------------

    void usage()
    {
        std::printf("Galaxy Angel II PC installer %s (%s)\n\n"
                    "Without options it opens its window. Options:\n"
                    "  --dir <folder>   install folder (default: this program's folder when it holds the game,\n"
                    "                   else the last install, else %s)\n"
                    "  --check          print the installed and the newest version; exit 0 when up to date,\n"
                    "                   10 when an update is available, 1 on errors\n"
                    "  --update         install the newest release when it is newer (or nothing is installed)\n"
                    "  --install        install the newest release even when it is installed already\n"
                    "  --uninstall      remove what the installer installed (asks in a window unless --yes)\n"
                    "  --yes            no questions (with --uninstall)\n"
                    "  --no-shortcuts   no menu/desktop shortcuts\n"
                    "  --version        print the installer's version\n"
                    "GA2_INSTALLER_MANIFEST=<url or file> uses another release manifest (testing).\n",
                    inst::installerVersion(), inst::platformId(), inst::pathUtf8(inst::defaultInstallDir()).c_str());
    }

    int headless(const path &dir, bool check, bool update, bool force, bool remove, bool shortcuts)
    {
        inst::Progress progress;
        try
        {
            if (remove)
            {
                inst::uninstall(dir, progress);
                std::printf("removed Galaxy Angel II PC from %s; saves and settings stay in %s\n", inst::pathUtf8(dir).c_str(),
                            inst::pathUtf8(inst::dataDir()).c_str());
                return 0;
            }
            const inst::Installed installed = inst::readInstalled(dir);
            std::printf("install folder: %s\ninstalled: %s\n", inst::pathUtf8(dir).c_str(),
                        !installed.present ? "no" : installed.version.empty() ? "unknown version" : installed.version.c_str());
            const inst::Manifest manifest = inst::parseManifest(inst::fetchText(inst::manifestSource(), progress));
            std::printf("newest release: %s%s\n", manifest.version.c_str(), manifest.date.empty() ? "" : (" (" + manifest.date + ")").c_str());
            if (!manifest.installerVersion.empty() && inst::compareVersions(manifest.installerVersion, inst::installerVersion()) > 0)
                std::printf("a newer installer is available: %s (this one is %s)\n", manifest.installerVersion.c_str(), inst::installerVersion());
            const bool newer = !installed.present || installed.version.empty() || inst::compareVersions(manifest.version, installed.version) > 0;
            if (check) return newer ? 10 : 0;
            if (!newer && !force)
            {
                std::printf("up to date\n");
                return 0;
            }
            std::thread reporter;
            std::atomic<bool> stop{false};
            reporter = std::thread([&] {
                int last = -1;
                uint64_t lastPercent = 101;
                while (!stop)
                {
                    const int stage = progress.stage.load();
                    const uint64_t total = progress.total.load(), done = progress.done.load();
                    const uint64_t percent = total ? done * 100 / total : 0;
                    if (stage != last || (total && percent / 10 != lastPercent / 10))
                    {
                        static const char *names[] = {"", "checking", "downloading", "checking the download", "unpacking",
                                                      "installing files", "removing files", "done"};
                        if (stage > 0 && stage < 7) std::printf("%s%s\n", names[stage], total ? (" " + std::to_string(percent) + "%").c_str() : "");
                        std::fflush(stdout);
                        last = stage;
                        lastPercent = percent;
                    }
                    std::this_thread::sleep_for(std::chrono::milliseconds(200));
                }
            });
            inst::InstallOptions options;
            options.shortcuts = shortcuts;
            try
            {
                inst::install(dir, manifest, options, progress);
            }
            catch (...)
            {
                stop = true;
                reporter.join();
                throw;
            }
            stop = true;
            reporter.join();
            std::printf("installed Galaxy Angel II PC %s in %s\n", manifest.version.c_str(), inst::pathUtf8(dir).c_str());
            return 0;
        }
        catch (const std::exception &e)
        {
            std::fprintf(stderr, "error: %s\n", e.what());
            return 1;
        }
    }

    std::vector<std::string> arguments(int argc, char **argv)
    {
        std::vector<std::string> out;
#ifdef _WIN32
        (void)argc, (void)argv;
        int count = 0;
        wchar_t **wide = CommandLineToArgvW(GetCommandLineW(), &count);
        for (int i = 1; i < count; ++i)
            out.push_back(inst::pathUtf8(path(wide[i])));
        LocalFree(wide);
#else
        for (int i = 1; i < argc; ++i)
            out.push_back(argv[i]);
#endif
        return out;
    }
}

int main(int argc, char **argv)
{
    const std::vector<std::string> args = arguments(argc, argv);
    // The uninstall helper (a copy in the temp folder, see platform::deleteAfterExit).
    if (args.size() == 4 && args[0] == "--finish-uninstall")
    {
        inst::platform::finishUninstall(std::stoul(args[1]), inst::pathFromUtf8(args[2]), inst::pathFromUtf8(args[3]));
        return 0;
    }
    bool check = false, update = false, force = false, remove = false, yes = false, shortcuts = true, help = false, version = false;
    std::optional<path> dir;
    for (size_t i = 0; i < args.size(); ++i)
    {
        const std::string &a = args[i];
        if (a == "--check") check = true;
        else if (a == "--update") update = true;
        else if (a == "--install") update = force = true;
        else if (a == "--uninstall") remove = true;
        else if (a == "--yes" || a == "-y") yes = true;
        else if (a == "--no-shortcuts") shortcuts = false;
        else if (a == "--help" || a == "-h") help = true;
        else if (a == "--version") version = true;
        else if (a == "--dir" && i + 1 < args.size()) dir = inst::pathFromUtf8(args[++i]);
        else
        {
            std::fprintf(stderr, "unknown option %s (see --help)\n", a.c_str());
            return 2;
        }
    }
    const bool console = help || version || check || update || (remove && yes);
#ifdef _WIN32
    if (console && AttachConsole(static_cast<unsigned long>(-1))) // the console the command was typed in
    {
        std::freopen("CONOUT$", "w", stdout);
        std::freopen("CONOUT$", "w", stderr);
    }
#endif
    if (help)
    {
        usage();
        return 0;
    }
    if (version)
    {
        std::printf("%s\n", inst::installerVersion());
        return 0;
    }
    inst::cleanupAfterSelfUpdate();
    const path target = dir ? *dir : inst::defaultInstallDir();
    if (console) return headless(target, check, update, force, remove, shortcuts);

#ifdef _WIN32
    if (const std::string lang = inst::getEnv("GA2_LANG"); lang == "ja") g_lang = Lang::Ja;
    else
    {
        if ((GetUserDefaultUILanguage() & 0x3FF) == 0x11) g_lang = Lang::Ja;
    }
#else
    for (const char *name : {"GA2_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"})
        if (const std::string value = inst::getEnv(name); !value.empty())
        {
            if (value.rfind("ja", 0) == 0) g_lang = Lang::Ja;
            break;
        }
#endif

    SetTraceLogLevel(LOG_WARNING);
    SetConfigFlags(FLAG_MSAA_4X_HINT | FLAG_VSYNC_HINT);
    InitWindow(1000, 700, "Galaxy Angel II PC Installer");
    if (!IsWindowReady())
    {
        std::fprintf(stderr, "no display; use the command line (--help)\n");
        return 1;
    }
    SetExitKey(KEY_NULL);
    SetTargetFPS(60);
    loadFonts();
    {
        App app(target, remove);
        while (!app.quit())
        {
            if (WindowShouldClose() && app.canClose()) break;
            app.frame();
        }
    }
    CloseWindow();
    return 0;
}
