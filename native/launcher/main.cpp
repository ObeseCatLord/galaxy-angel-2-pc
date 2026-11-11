// Galaxy Angel II launcher: imports the player's disc images once, then starts
// the games. Each disc's recompiled program is its own engine binary next to
// this one; the engines switch discs (and programs) by themselves.
//
//   ga2                         menu (drop .iso files on the window to import)
//   ga2 --import <iso>...       import from the command line
//   ga2 --list                  show imported discs and start entries
//   ga2 --start <entry>         start a game without the menu
//   --data-dir <dir>            library, memory cards and logs (default: user data dir)
//   --engine-dir <dir>          where the ga2_<disc> engines are (default: next to ga2)
#include "library.h"
#include "runtime/ga2_menu_select.h"

#include <raylib.h>
#include "runtime/ga2_ui_font.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <cstring>
#include <atomic>
#include <iostream>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>
#include "process.h"
#include "settings.h"
#include "assets.h"
#include "mods.h"

namespace
{
    using ga2::Settings;
    namespace ui = ga2_ui_font;

    // The UI font (native/fonts, next to the launcher); GA2_UI_FONT overrides it. Missing: raylib's default font.
    void chooseFont()
    {
        const std::string chosen = ga2::getEnvironment("GA2_UI_FONT");
        ui::setFile(chosen.empty() ? ga2::executableDirectory() / "fonts" / "NotoSansJP-Medium.otf"
                                   : ga2::pathFromUtf8(chosen));
    }
    std::filesystem::path settingsFile(const ga2::Library &library) { return library.dataDir() / "settings.ini"; }

    // <data>/mods/<disc program>/ holds files that replace the disc's own
    // (same path as on the disc). The engines read $GA2_MODS.
    // <data>/mods: loose per-disc folders and installed mods (mods.h); sets
    // GA2_MODS / GA2_TEXTURE_PACKS for the chosen language.
    void prepareMods(const ga2::Library &library, const std::string &language)
    {
        const auto mods = library.dataDir() / "mods";
        std::error_code error;
        std::filesystem::create_directories(mods, error);
        for (const auto &known : ga2::knownDiscs())
            std::filesystem::create_directories(mods / (known.bootPath + 1), error);
        std::ofstream readme(mods / "README.txt", std::ios::trunc);
        readme << "Galaxy Angel II mods (full guide: docs/MODDING.md)\n\n"
                  "Installed mods: one folder per mod, holding\n"
                  "  mod.json      {\"name\": ..., \"version\": ..., \"author\": ..., \"description\": ...,\n"
                  "                 \"language\": \"en\"   <- only for language packs}\n"
                  "  files/<disc program>/<disc path>   disc files to replace or add\n"
                  "  textures/.../<hash>.png            texture replacements, any resolution\n"
                  "Turn mods on and order them on the launcher's Mods screen (M).\n"
                  "A language pack is used whenever its language is chosen in Options.\n\n"
                  "Loose files: the folders below replace disc files of the same path, under every mod:\n";
        for (const auto &known : ga2::knownDiscs())
            readme << "  " << (known.bootPath + 1) << "  " << known.label << '\n';
        readme << "Example: SLPM_662.43/ADV.DAT replaces ADV.DAT on the Zettai disc.\n";
        ga2::Mods(library.dataDir()).applyToEnvironment(language);
    }

    // The options as the file has them now (the engines save changes made in game there), handed to the next engine.
    void refreshEnvironment(const ga2::Library &library)
    {
        const Settings settings = Settings::load(settingsFile(library));
        settings.applyToEnvironment(library.dataDir());
        prepareMods(library, settings.value("language"));
    }

    // Exit status of an engine that left the next command in the handoff file
    // (LoadExecPS2 to another disc's program); matches the runtime.
    constexpr int kHandoffExitCode = 75;

    // Runs the engine to completion, following program handoffs between the
    // per-disc engines; output goes to <data>/logs/<entry>.log.
    // `refresh` runs before a handoff's next engine starts: the player may have changed options in game (settings.ini).
    int runEngine(std::vector<std::string> command, const std::filesystem::path &log, const std::function<void()> &refresh)
    {
        std::filesystem::create_directories(log.parent_path());
        const auto handoff = log.parent_path() / "handoff.txt";
        ga2::setEnvironment("GA2_HANDOFF_FILE", ga2::pathUtf8(handoff));
#ifndef _WIN32
        // A packaged build keeps libps2_runtime.so next to the engines.
        const std::string engineDir = ga2::pathUtf8(ga2::pathFromUtf8(command.front()).parent_path());
        const std::string previous = ga2::getEnvironment("LD_LIBRARY_PATH");
        ga2::setEnvironment("LD_LIBRARY_PATH", previous.empty() ? engineDir : engineDir + ":" + previous);
#endif
        for (bool append = false;; append = true)
        {
            std::error_code ignored;
            std::filesystem::remove(handoff, ignored);
            const int code = ga2::spawnAndWait(command, log, append);
            if (code != kHandoffExitCode || !std::filesystem::exists(handoff))
                return code;
            std::ifstream in(handoff);
            std::vector<std::string> next;
            for (std::string line; std::getline(in, line);)
            {
                if (!line.empty() && line.back() == '\r') line.pop_back();
                next.push_back(line);
            }
            if (next.empty())
                return code;
            command = std::move(next);
            if (refresh) refresh();
        }
    }

    std::filesystem::path logFor(const ga2::Library &library, const ga2::StartEntry &entry)
    {
        return library.dataDir() / "logs" / (std::string(entry.id) + ".log");
    }

    const ga2::StartEntry *entryById(std::string_view id)
    {
        for (const auto &entry : ga2::startEntries())
            if (id == entry.id) return &entry;
        return nullptr;
    }

    // --- menu ---------------------------------------------------------------

    struct Palette
    {
        Color background{12, 16, 34, 255}, panel{24, 32, 64, 255}, accent{120, 180, 255, 255};
        Color text{230, 236, 250, 255}, dim{130, 140, 170, 255}, warn{255, 190, 90, 255}, ok{120, 220, 150, 255};
    };

    class Menu
    {
    public:
        Menu(ga2::Library &library, std::filesystem::path engineDir)
            : m_library(library), m_engineDir(std::move(engineDir)), m_settings(Settings::load(settingsFile(library))) {}

        ~Menu()
        {
            if (m_worker.joinable())
            {
                m_progress.cancel = true;
                m_worker.join();
            }
        }

        void run()
        {
            chooseFont();
            for (;;)
            {
                openWindow();
                const ga2::StartEntry *start = loop();
                // Largest whole multiple of 640x448 that fits the monitor.
                const int monitor = GetCurrentMonitor();
                m_settings.windowScale = std::max(1, std::min(GetMonitorWidth(monitor) * 9 / 10 / 640,
                                                              GetMonitorHeight(monitor) * 9 / 10 / 448));
                m_settings.save(settingsFile(m_library));
                ui::shutdown(); // the atlases are textures of this window
                CloseWindow();
                if (!start) return;
                try
                {
                    m_settings.applyToEnvironment(m_library.dataDir());
                    prepareMods(m_library, m_settings.value("language"));
                    const int code = runEngine(m_library.engineCommand(*start, m_engineDir), logFor(m_library, *start),
                                               [this] { refreshEnvironment(m_library); });
                    m_status = code == 0 ? std::string("Returned from ") + start->title
                                         : std::string(start->title) + " stopped (code " + std::to_string(code) +
                                               "); see " + logFor(m_library, *start).string();
                }
                catch (const std::exception &error)
                {
                    m_status = std::string("Could not start ") + start->title + ": " + error.what();
                }
            }
        }

    private:
        void openWindow()
        {
            SetConfigFlags(FLAG_WINDOW_RESIZABLE | FLAG_MSAA_4X_HINT);
            InitWindow(1280, 720, "Galaxy Angel II PC " GA2_VERSION);
            SetTargetFPS(60);
            SetExitKey(KEY_NULL);
        }

        bool importing() const { return m_worker.joinable() && !m_workerDone; }
        // Disc import (not just skybox rendering) in progress: games wait for it.
        bool importingDiscs() const { return importing() && m_discImport; }

        void startImport(std::vector<std::filesystem::path> images)
        {
            if (m_worker.joinable()) m_worker.join();
            m_workerDone = false;
            m_discImport = true;
            m_worker = std::thread([this, images = std::move(images)] {
                for (const auto &image : images)
                {
                    {
                        std::lock_guard lock(m_mutex);
                        m_importing = image.filename().string();
                    }
                    try
                    {
                        const std::string id = m_library.import(image, &m_progress);
                        const ga2::KnownDisc *known = ga2::knownDisc(id);
                        {
                            std::lock_guard lock(m_mutex);
                            m_status = "Added " + std::string(known->label) + "; rendering its space skybox...";
                        }
                        std::string why;
                        const bool sky = ga2::ensureSkybox(m_library, *known, m_engineDir, why);
                        std::lock_guard lock(m_mutex);
                        m_status = "Added " + std::string(known->label) + (sky ? "" : " (no skybox: " + why + ")");
                    }
                    catch (const std::exception &error)
                    {
                        std::lock_guard lock(m_mutex);
                        m_status = error.what();
                    }
                }
                m_workerDone = true;
            });
        }

        // Disc images in the isos folders (<data folder>/isos and isos beside the
        // launcher) that are not added yet. For systems without drag and drop: the
        // Steam Frame, Steam Deck game mode, a TV with a pad.
        std::vector<std::filesystem::path> folderImages() const
        {
            std::set<std::filesystem::path> known;
            std::error_code ec;
            for (const auto &disc : m_library.discs())
                known.insert(std::filesystem::weakly_canonical(disc.image, ec));
            std::vector<std::filesystem::path> out;
            for (const auto &dir : {m_library.dataDir() / "isos", m_engineDir / "isos"})
                for (const auto &entry : std::filesystem::directory_iterator(dir, ec))
                {
                    std::string ext = entry.path().extension().string();
                    std::transform(ext.begin(), ext.end(), ext.begin(), [](unsigned char c) { return char(std::tolower(c)); });
                    if (ext == ".iso" && entry.is_regular_file(ec) &&
                        !known.count(std::filesystem::weakly_canonical(entry.path(), ec)))
                        out.push_back(entry.path());
                }
            return out;
        }

        void importFolderImages()
        {
            if (importing())
                return;
            std::vector<std::filesystem::path> images = folderImages();
            if (images.empty())
            {
                std::lock_guard lock(m_mutex);
                m_status = "No new disc images in " + ga2::pathUtf8(m_library.dataDir() / "isos");
                return;
            }
            startImport(std::move(images));
        }

        // Skyboxes for discs imported before the launcher rendered them.
        void startMissingSkyboxes()
        {
            std::vector<const ga2::KnownDisc *> missing;
            for (const auto &known : ga2::knownDiscs())
                if (const auto disc = m_library.disc(known.id); disc && disc->available &&
                                                               !std::filesystem::exists(ga2::skyboxFile(m_library, known)))
                    missing.push_back(&known);
            if (missing.empty() || importing())
                return;
            if (m_worker.joinable()) m_worker.join();
            m_workerDone = false;
            m_discImport = false;
            m_worker = std::thread([this, missing] {
                for (const ga2::KnownDisc *known : missing)
                {
                    {
                        std::lock_guard lock(m_mutex);
                        m_importing = std::string("space skybox for ") + known->label;
                    }
                    std::string why;
                    if (!ga2::ensureSkybox(m_library, *known, m_engineDir, why))
                    {
                        std::lock_guard lock(m_mutex);
                        m_status = std::string("No space skybox: ") + why;
                        break;
                    }
                }
                m_workerDone = true;
            });
        }

        const ga2::StartEntry *loop()
        {
            const auto &entries = ga2::startEntries();
            {
                std::error_code ec;
                std::filesystem::create_directories(m_library.dataDir() / "isos", ec);
            }
            if (!folderImages().empty())
                importFolderImages();
            startMissingSkyboxes();
            while (!WindowShouldClose())
            {
                if (IsFileDropped())
                {
                    FilePathList dropped = LoadDroppedFiles();
                    std::vector<std::filesystem::path> images;
                    for (unsigned i = 0; i < dropped.count; ++i) images.emplace_back(dropped.paths[i]);
                    UnloadDroppedFiles(dropped);
                    if (!importing()) startImport(std::move(images));
                }
                // R or Triangle / Y: add the images in the isos folders.
                if (IsKeyPressed(KEY_R) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_UP))
                    importFolderImages();
                const bool up = IsKeyPressed(KEY_UP) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_UP);
                const bool down = IsKeyPressed(KEY_DOWN) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_DOWN);
                const bool confirm = IsKeyPressed(KEY_ENTER) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_RIGHT) ||
                                     IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN);
                // A row is highlighted while the pointer is over it, or after Up / Down until the mouse moves again;
                // a click starts the hovered row, Enter / Circle the one the keys chose (and nothing otherwise).
                const Vector2 mouse = GetMousePosition();
                const Vector2 moved = GetMouseDelta();
                const int hovered = entryAt(mouse);
                const bool clicked = hovered >= 0 && IsMouseButtonPressed(MOUSE_BUTTON_LEFT);
                m_select.begin(hovered, moved.x != 0.0f || moved.y != 0.0f, IsMouseButtonPressed(MOUSE_BUTTON_LEFT));
                if (up) m_select.move(-1, int(entries.size()));
                if (down) m_select.move(1, int(entries.size()));
                const int chosen = clicked ? hovered : confirm ? m_select.keySelection() : -1;
                // Options screen: O key or the Options button.
                // Pads: Select / View opens Mods, Start / Menu Options.
                if (IsKeyPressed(KEY_M) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_MIDDLE_LEFT) ||
                    (CheckCollisionPointRec(mouse, modsButton()) && IsMouseButtonPressed(MOUSE_BUTTON_LEFT)))
                {
                    modsScreen();
                    continue;
                }
                if (IsKeyPressed(KEY_O) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_MIDDLE_RIGHT) ||
                    (CheckCollisionPointRec(mouse, optionsButton()) && IsMouseButtonPressed(MOUSE_BUTTON_LEFT)))
                {
                    optionsScreen();
                    continue;
                }
                if (IsKeyPressed(KEY_ESCAPE) && !importing()) break;
                if (chosen >= 0 && !importingDiscs())
                {
                    const auto &entry = entries[size_t(chosen)];
                    if (m_library.canStart(entry)) return &entry;
                    m_status = std::string(entry.title) + " needs discs that are not added yet";
                }
                draw();
            }
            if (m_worker.joinable())
            {
                m_progress.cancel = true;
                m_worker.join();
                m_progress.cancel = false;
            }
            return nullptr;
        }

        static constexpr int kEntryTop = 140, kEntryPitch = 74, kEntryHeight = 66;

        // Start-entry row under a window point, or -1 (the rows draw() lays out).
        static int entryAt(Vector2 point)
        {
            const int w = GetScreenWidth();
            const int count = int(ga2::startEntries().size());
            for (int i = 0; i < count; ++i)
            {
                const int top = kEntryTop + i * kEntryPitch - 8;
                if (point.x >= 48 && point.x < w - 48 && point.y >= top && point.y < top + kEntryHeight)
                    return i;
            }
            return -1;
        }

        static Rectangle optionsButton() { return Rectangle{float(GetScreenWidth() - 220), 40.0f, 170.0f, 38.0f}; }
        static Rectangle modsButton() { return Rectangle{float(GetScreenWidth() - 410), 40.0f, 170.0f, 38.0f}; }

        // Installed mods: Enter / click toggles, [ and ] (or Page Up/Down)
        // change the priority, Esc returns.
        void modsScreen()
        {
            ga2::Mods mods(m_library.dataDir());
            ga2_menu::Selector select; // highlighted row: hovered, or chosen with the keys
            const std::string language = m_settings.value("language");
            while (!WindowShouldClose())
            {
                const auto &list = mods.list();
                const Vector2 mouse = GetMousePosition();
                const Rectangle back{48.0f, float(GetScreenHeight() - 80), 160.0f, 40.0f};
                if (IsKeyPressed(KEY_ESCAPE) || IsKeyPressed(KEY_BACKSPACE) ||
                    IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_RIGHT) ||
                    IsGamepadButtonPressed(0, GAMEPAD_BUTTON_MIDDLE_LEFT) ||
                    (CheckCollisionPointRec(mouse, back) && IsMouseButtonPressed(MOUSE_BUTTON_LEFT)))
                    break;
                auto rowRect = [&](size_t i) {
                    return Rectangle{48.0f, 140.0f + 64.0f * float(i), float(GetScreenWidth() - 96), 58.0f};
                };
                bool changed = false;
                if (!list.empty())
                {
                    int hovered = -1;
                    for (size_t i = 0; i < list.size(); ++i)
                        if (CheckCollisionPointRec(mouse, rowRect(i))) hovered = int(i);
                    select.begin(hovered, GetMouseDelta().x != 0.0f || GetMouseDelta().y != 0.0f, IsMouseButtonPressed(MOUSE_BUTTON_LEFT));
                    if (IsKeyPressed(KEY_UP) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_UP)) select.move(-1, int(list.size()));
                    if (IsKeyPressed(KEY_DOWN) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_DOWN)) select.move(1, int(list.size()));
                    // A click acts on the hovered row; Enter, Space and the priority keys on the row the keys chose.
                    if (hovered >= 0 && IsMouseButtonPressed(MOUSE_BUTTON_LEFT) && list[size_t(hovered)].language.empty())
                    {
                        mods.setEnabled(list[size_t(hovered)].id, !mods.enabled(list[size_t(hovered)].id));
                        changed = true;
                    }
                    const int chosen = select.keySelection();
                    if (chosen >= 0)
                    {
                        const ga2::ModInfo &mod = list[size_t(chosen)];
                        if ((IsKeyPressed(KEY_ENTER) || IsKeyPressed(KEY_SPACE) ||
                             IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN)) && mod.language.empty())
                        {
                            mods.setEnabled(mod.id, !mods.enabled(mod.id));
                            changed = true;
                        }
                        const int delta = (IsKeyPressed(KEY_LEFT_BRACKET) || IsKeyPressed(KEY_PAGE_UP) ||
                                           IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_TRIGGER_1)) ? -1
                                          : (IsKeyPressed(KEY_RIGHT_BRACKET) || IsKeyPressed(KEY_PAGE_DOWN) ||
                                             IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_TRIGGER_1)) ? 1 : 0;
                        if (delta)
                        {
                            const std::string id = mod.id;
                            mods.move(id, delta);
                            for (size_t i = 0; i < mods.list().size(); ++i)
                                if (mods.list()[i].id == id) select.adopt(int(i));
                            changed = true;
                        }
                    }
                }
                if (changed) mods.save();

                const Palette p;
                BeginDrawing();
                ClearBackground(p.background);
                ui::draw("MODS", 60, 40, 44, p.text);
                ui::draw(("Folder: " + (m_library.dataDir() / "mods").string()).c_str(), 64, 92, 18, p.dim);
                if (list.empty())
                    ui::draw("No mods installed. Put mod folders (with a mod.json) in the folder above.", 60, 150, 22, p.dim);
                for (size_t i = 0; i < list.size(); ++i)
                {
                    const ga2::ModInfo &mod = list[i];
                    const Rectangle r = rowRect(i);
                    if (int(i) == select.shown()) DrawRectangleRec(r, p.panel);
                    const bool active = mod.language.empty() ? mods.enabled(mod.id) : mod.language == language;
                    DrawRectangleLines(int(r.x) + 14, int(r.y) + 16, 24, 24, active ? p.ok : p.dim);
                    if (active) DrawRectangle(int(r.x) + 19, int(r.y) + 21, 14, 14, p.ok);
                    std::string title = mod.name + (mod.version.empty() ? "" : "  " + mod.version);
                    if (!mod.language.empty()) title += "   [language pack: " + mod.language + ", follows Options > Language]";
                    ui::draw(title.c_str(), int(r.x) + 54, int(r.y) + 6, 22, mod.error.empty() ? p.text : p.warn);
                    const std::string detail = !mod.error.empty() ? "mod.json: " + mod.error
                                               : mod.description + (mod.author.empty() ? "" : "  -  " + mod.author);
                    ui::draw(detail.c_str(), int(r.x) + 54, int(r.y) + 34, 16, mod.error.empty() ? p.dim : p.warn);
                }
                ui::draw("Enter / A / click: on or off    [ ] or LB / RB: priority (top wins)    Esc / B: back", 60, GetScreenHeight() - 130, 20, p.dim);
                DrawRectangleRec(back, CheckCollisionPointRec(mouse, back) ? p.panel : p.background);
                DrawRectangleLinesEx(back, 1.0f, p.dim);
                ui::draw("Back (Esc)", int(back.x) + 22, int(back.y) + 10, 20, p.text);
                EndDrawing();
            }
        }

        static constexpr int kOptionTop = 130, kOptionPitch = 50;

        // Rows that fit above the help line; the list scrolls past them.
        static size_t visibleOptions() { return size_t(std::max(1, (GetScreenHeight() - kOptionTop - 150) / kOptionPitch)); }

        static Rectangle optionRow(size_t index, size_t first)
        {
            if (index < first || index >= first + visibleOptions()) return Rectangle{0, 0, 0, 0};
            return Rectangle{48.0f, float(kOptionTop + int(index - first) * kOptionPitch), float(GetScreenWidth() - 96), 44.0f};
        }

        // The editable number of a row (the frame rate's Custom): its entry box in the row, or an empty rectangle.
        // The box is drawn after "< Custom:" in the value column.
        static Rectangle numberBox(const Rectangle &row)
        {
            return Rectangle{row.x + row.width * 0.45f + float(ui::measure("< Custom: ", 22)), row.y + 5.0f, 86.0f, row.height - 10.0f};
        }

        // Up/Down choose an option, Left/Right (or clicks: left = next, right = previous) change it; Esc or Back
        // returns. An option with an editable number (Custom) takes typed digits (Enter or leaving the row keeps
        // them, a number in range applies as you type); the mouse wheel over the box steps by 1 (Shift: 10).
        // Nothing is highlighted unless the pointer is over a row or the keys chose one.
        void optionsScreen()
        {
            const auto &table = ga2::options();
            ga2_menu::Selector select;
            size_t first = 0;
            std::string edit; // digits typed into the number of row `editRow`
            int editRow = -1;
            while (GetCharPressed() != 0) {} // nothing typed before this screen counts
            // The typed number, brought into range and kept.
            // `force` (Enter) brings an out-of-range number into range; leaving the row drops it.
            auto commitEdit = [&](bool force = false) {
                int typed = 0;
                if (editRow >= 0 && ga2_options::parseWhole(edit, typed) &&
                    (force || (typed >= table[size_t(editRow)].customMin && typed <= table[size_t(editRow)].customMax)))
                {
                    m_settings.setNumber(size_t(editRow), typed);
                    m_settings.save(settingsFile(m_library));
                }
                edit.clear();
                editRow = -1;
            };
            while (!WindowShouldClose())
            {
                const Vector2 mouse = GetMousePosition();
                const Rectangle back{48.0f, float(GetScreenHeight() - 80), 160.0f, 40.0f};
                const int chosenBefore = select.keySelection();
                const bool customChosen = chosenBefore >= 0 && ga2_options::hasCustom(table[size_t(chosenBefore)]) &&
                                          m_settings.choice(size_t(chosenBefore)) == table[size_t(chosenBefore)].customChoice;
                // Backspace deletes a digit while a number is chosen (Esc leaves).
                if (IsKeyPressed(KEY_ESCAPE) || (IsKeyPressed(KEY_BACKSPACE) && !customChosen) ||
                    IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_RIGHT) ||
                    (CheckCollisionPointRec(mouse, back) && IsMouseButtonPressed(MOUSE_BUTTON_LEFT)))
                {
                    commitEdit();
                    break;
                }
                const float wheel = GetMouseWheelMove();
                const bool shift = IsKeyDown(KEY_LEFT_SHIFT) || IsKeyDown(KEY_RIGHT_SHIFT);
                const size_t visible = visibleOptions();
                int hovered = -1;
                if (wheel != 0.0f)
                {
                    // Over a number's box the wheel steps it; elsewhere it scrolls the list.
                    bool stepped = false;
                    for (size_t i = first; i < std::min(table.size(), first + visible); ++i)
                        if (ga2_options::hasCustom(table[i]) && m_settings.choice(i) == table[i].customChoice &&
                            CheckCollisionPointRec(mouse, numberBox(optionRow(i, first))))
                        {
                            commitEdit();
                            m_settings.setNumber(i, m_settings.number(i) + (wheel > 0.0f ? 1 : -1) * (shift ? 10 : 1));
                            m_settings.save(settingsFile(m_library));
                            stepped = true;
                        }
                    if (!stepped && table.size() > visible)
                        first = size_t(std::clamp(int(first) - int(wheel), 0, int(table.size() - visible)));
                }
                for (size_t i = 0; i < table.size(); ++i)
                    if (CheckCollisionPointRec(mouse, optionRow(i, first))) hovered = int(i);
                const bool pressed = IsMouseButtonPressed(MOUSE_BUTTON_LEFT), pressedRight = IsMouseButtonPressed(MOUSE_BUTTON_RIGHT);
                select.begin(hovered, GetMouseDelta().x != 0.0f || GetMouseDelta().y != 0.0f, pressed || pressedRight);
                const bool up = IsKeyPressed(KEY_UP) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_UP);
                const bool down = IsKeyPressed(KEY_DOWN) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_DOWN);
                if (up) select.move(-1, int(table.size()));
                if (down) select.move(1, int(table.size()));
                // Digits typed over a row with a number choose that row.
                std::string typed;
                for (int ch = GetCharPressed(); ch != 0; ch = GetCharPressed())
                    if (ch >= '0' && ch <= '9') typed += char(ch);
                if (!typed.empty() && select.keySelection() < 0 && hovered >= 0 && ga2_options::hasCustom(table[size_t(hovered)]))
                    select.adopt(hovered);
                // Clicking the box of the Custom value puts the keys in it.
                if (pressed && hovered >= 0 && ga2_options::hasCustom(table[size_t(hovered)]) &&
                    m_settings.choice(size_t(hovered)) == table[size_t(hovered)].customChoice &&
                    CheckCollisionPointRec(mouse, numberBox(optionRow(size_t(hovered), first))))
                    select.adopt(hovered);
                const int row = select.keySelection();
                if (row != editRow) commitEdit(); // the keys left the row (or the mouse took over)
                int delta = 0, target = -1;
                if (row >= 0)
                {
                    target = row;
                    const ga2_options::Option &option = table[size_t(row)];
                    if (ga2_options::hasCustom(option))
                    {
                        for (char ch : typed)
                            if (edit.size() < 3) { edit += ch; editRow = row; }
                        if ((IsKeyPressed(KEY_BACKSPACE) || IsKeyPressedRepeat(KEY_BACKSPACE)) && !edit.empty()) edit.pop_back();
                        int number = 0;
                        // A number in range applies at once; the rest waits for Enter or leaving the row.
                        if (!typed.empty() && ga2_options::parseWhole(edit, number) && number >= option.customMin && number <= option.customMax)
                        {
                            m_settings.setNumber(size_t(row), number);
                            m_settings.save(settingsFile(m_library));
                        }
                    }
                    if (IsKeyPressed(KEY_ENTER) || IsKeyPressed(KEY_KP_ENTER))
                    {
                        if (!edit.empty()) { commitEdit(true); target = -1; }
                        else delta = 1;
                    }
                    if (IsKeyPressed(KEY_RIGHT) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_RIGHT) ||
                        IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN))
                        delta = 1;
                    if (IsKeyPressed(KEY_LEFT) || IsGamepadButtonPressed(0, GAMEPAD_BUTTON_LEFT_FACE_LEFT))
                        delta = -1;
                    if (delta) commitEdit();
                    // The list scrolls to show the row the keys moved to.
                    if (up || down)
                    {
                        if (row >= int(first) + int(visible)) first = size_t(row) + 1 - visible;
                        if (row < int(first)) first = size_t(row);
                    }
                }
                // A click acts on the hovered row (left: next, right: previous), except inside a number's box.
                if (hovered >= 0 && (pressed || pressedRight))
                {
                    const bool inBox = ga2_options::hasCustom(table[size_t(hovered)]) &&
                                       m_settings.choice(size_t(hovered)) == table[size_t(hovered)].customChoice &&
                                       CheckCollisionPointRec(mouse, numberBox(optionRow(size_t(hovered), first)));
                    if (!inBox) { target = hovered; delta = pressed ? 1 : -1; }
                }
                if (delta && target >= 0)
                {
                    m_settings.step(size_t(target), delta);
                    m_settings.save(settingsFile(m_library));
                }

                const Palette p;
                BeginDrawing();
                ClearBackground(p.background);
                ui::draw("OPTIONS", 60, 40, 44, p.text);
                ui::draw("Saved automatically; used the next time a game starts. In a game, F2 opens these options.", 64, 90, 18, p.dim);
                const int shown = select.shown();
                for (size_t i = first; i < std::min(table.size(), first + visibleOptions()); ++i)
                {
                    const Rectangle r = optionRow(i, first);
                    const bool highlighted = int(i) == shown;
                    if (highlighted)
                    {
                        DrawRectangleRec(r, p.panel);
                        DrawRectangle(int(r.x), int(r.y), 6, int(r.height), p.accent);
                    }
                    ui::draw(table[i].label, int(r.x) + 24, int(r.y) + 12, 22, p.text);
                    const bool custom = ga2_options::hasCustom(table[i]) && m_settings.choice(i) == table[i].customChoice;
                    const Color valueColor = highlighted ? p.accent : p.text;
                    if (custom)
                    {
                        // "< Custom: [173] >": the number sits in an entry box.
                        ui::draw("< Custom: ", int(r.x + r.width * 0.45f), int(r.y) + 12, 22, valueColor);
                        const Rectangle box = numberBox(r);
                        const bool editing = editRow == int(i) && !edit.empty();
                        DrawRectangleRec(box, p.background);
                        DrawRectangleLinesEx(box, 1.0f, highlighted ? p.accent : p.dim);
                        const std::string number = editing ? edit + (int(GetTime() * 2.0) % 2 ? "" : "_") : std::to_string(m_settings.number(i));
                        ui::draw(number.c_str(), int(box.x) + 10, int(r.y) + 12, 22, editing ? p.warn : valueColor);
                        ui::draw(">", int(box.x + box.width) + 12, int(r.y) + 12, 22, valueColor);
                    }
                    else
                    {
                        const std::string value = "< " + m_settings.label(i) + " >";
                        ui::draw(value.c_str(), int(r.x + r.width * 0.45f), int(r.y) + 12, 22, valueColor);
                    }
                }
                if (first > 0) ui::draw("^", GetScreenWidth() - 80, kOptionTop - 26, 22, p.dim);
                if (first + visibleOptions() < table.size())
                    ui::draw("v", GetScreenWidth() - 80, kOptionTop + int(visibleOptions()) * kOptionPitch, 22, p.dim);
                if (shown >= 0)
                {
                    std::string help = table[size_t(shown)].help;
                    if (std::string(table[size_t(shown)].key) == "sides" && m_settings.value("sides") == "skybox" &&
                            !std::filesystem::exists(m_library.dataDir() / "assets" / "skybox"))
                        help = "No skybox rendered yet (done from your discs after import; needs Python 3 + Pillow).";
                    else if (ga2_options::hasCustom(table[size_t(shown)]) && m_settings.choice(size_t(shown)) == table[size_t(shown)].customChoice)
                        help += " Type a rate, or use the wheel on it.";
                    drawWrapped(help, 60, GetScreenHeight() - 150, GetScreenWidth() - 120, 20, p.dim);
                }
                DrawRectangleRec(back, CheckCollisionPointRec(mouse, back) ? p.panel : p.background);
                DrawRectangleLinesEx(back, 1.0f, p.dim);
                ui::draw("Back (Esc)", int(back.x) + 22, int(back.y) + 10, 20, p.text);
                EndDrawing();
            }
        }

        // Words wrapped to `width`, one line under the other.
        static void drawWrapped(const std::string &text, int x, int y, int width, int size, Color color)
        {
            std::string line, word;
            auto flush = [&] {
                if (word.empty()) return;
                const std::string trial = line.empty() ? word : line + " " + word;
                if (!line.empty() && ui::measure(trial.c_str(), size) > width)
                {
                    ui::draw(line.c_str(), x, y, size, color);
                    y += size + 4;
                    line = word;
                }
                else
                    line = trial;
                word.clear();
            };
            for (char ch : text)
            {
                if (ch == ' ') flush(); else word += ch;
            }
            flush();
            if (!line.empty()) ui::draw(line.c_str(), x, y, size, color);
        }

        void draw()
        {
            const Palette p;
            const int w = GetScreenWidth(), h = GetScreenHeight();
            BeginDrawing();
            ClearBackground(p.background);
            ui::draw("GALAXY ANGEL II", 60, 40, 48, p.text);
            ui::draw("PC port", 64, 94, 20, p.dim);

            const Rectangle modsRect = modsButton();
            DrawRectangleRec(modsRect, CheckCollisionPointRec(GetMousePosition(), modsRect) ? p.panel : p.background);
            DrawRectangleLinesEx(modsRect, 1.0f, p.dim);
            ui::draw("Mods (M)", int(modsRect.x) + 32, int(modsRect.y) + 9, 20, p.text);
            const Rectangle button = optionsButton();
            DrawRectangleRec(button, CheckCollisionPointRec(GetMousePosition(), button) ? p.panel : p.background);
            DrawRectangleLinesEx(button, 1.0f, p.dim);
            ui::draw("Options (O)", int(button.x) + 18, int(button.y) + 9, 20, p.text);

            const auto &entries = ga2::startEntries();
            int y = kEntryTop;
            for (size_t i = 0; i < entries.size(); ++i)
            {
                const auto &entry = entries[i];
                const bool selected = int(i) == m_select.shown();
                const bool ready = m_library.canStart(entry);
                if (selected) DrawRectangle(48, y - 8, w - 96, kEntryHeight, p.panel);
                if (selected) DrawRectangle(48, y - 8, 6, kEntryHeight, p.accent);
                ui::draw(entry.title, 72, y, 28, ready ? p.text : p.dim);
                {
                    // The title as on the box, at the right when there is room beside the romanised one.
                    const float width = ui::measure(entry.titleJa, 26);
                    const float left = float(w - 72) - width;
                    if (left > 96.0f + ui::measure(entry.title, 28))
                        ui::draw(entry.titleJa, left, float(y), 26, ready ? p.accent : p.dim);
                }
                std::string detail = entry.detail;
                if (!ready)
                {
                    detail += "  -  needs:";
                    for (const char *id : entry.required)
                        if (!m_library.disc(id)) detail += std::string(" ") + ga2::knownDisc(id)->label + ";";
                }
                else
                    for (const char *id : entry.later)
                        if (!m_library.disc(id))
                            detail += std::string("  -  add ") + ga2::knownDisc(id)->label + " before the story reaches it";
                ui::draw(detail.c_str(), 72, y + 32, 18, ready ? p.dim : p.warn);
                y += kEntryPitch;
            }

            // Disc shelf.
            int x = 60;
            const int shelfY = h - 120;
            ui::draw("Discs", x, shelfY + 12, 20, p.dim);
            x += 80;
            for (const auto &known : ga2::knownDiscs())
            {
                const bool have = m_library.disc(known.id).has_value();
                DrawRectangleLines(x, shelfY, 140, 44, have ? p.ok : p.dim);
                ui::draw(known.shortLabel, x + 14, shelfY + 12, 20, have ? p.text : p.dim);
                ui::draw(have ? "added" : "missing", x + 14, shelfY + 46, 14, have ? p.ok : p.warn);
                x += 152;
            }

            std::string footer;
            if (importing())
            {
                const double total = double(std::max<uint64_t>(m_progress.total, 1u));
                const float fraction = float(double(m_progress.done) / total);
                std::lock_guard lock(m_mutex);
                if (m_discImport)
                {
                    footer = "Checking " + m_importing + " ...";
                    DrawRectangle(60, h - 70, w - 120, 12, p.panel);
                    DrawRectangle(60, h - 70, int(float(w - 120) * fraction), 12, p.accent);
                }
                else
                    footer = "Rendering the " + m_importing + " (games can start meanwhile) ...";
            }
            else
            {
                std::lock_guard lock(m_mutex);
                footer = m_status.empty() ? "Drop disc images (.iso) here, or put them in " +
                                                ga2::pathUtf8(m_library.dataDir() / "isos") + " and press R / Triangle (Y)"
                                          : m_status;
            }
            ui::draw(footer.c_str(), 60, h - 44, 20, p.dim);
            EndDrawing();
        }

        ga2::Library &m_library;
        std::filesystem::path m_engineDir;
        Settings m_settings;
        ga2_menu::Selector m_select; // the highlighted start entry
        std::mutex m_mutex;
        std::string m_status, m_importing;
        std::thread m_worker;
        std::atomic<bool> m_workerDone{true};
        std::atomic<bool> m_discImport{false};
        ga2::ImportProgress m_progress;
    };
}

int main(int argc, char **rawArgv)
{
    const std::vector<std::string> args = ga2::commandLineArguments(argc, rawArgv);
    std::filesystem::path dataDir = ga2::Library::defaultDataDir();
    std::filesystem::path engineDir = ga2::executableDirectory();
    std::vector<std::filesystem::path> imports;
    std::string start;
    bool list = false;
    const int count = int(args.size());
    for (int i = 1; i < count; ++i)
    {
        const std::string_view arg = args[size_t(i)];
        if (arg == "--data-dir" && i + 1 < count) dataDir = ga2::pathFromUtf8(args[size_t(++i)]);
        else if (arg == "--engine-dir" && i + 1 < count) engineDir = ga2::pathFromUtf8(args[size_t(++i)]);
        else if (arg == "--start" && i + 1 < count) start = args[size_t(++i)];
        else if (arg == "--list") list = true;
        else if (arg == "--version")
        {
            std::cout << "Galaxy Angel II PC " GA2_VERSION "\n";
            return 0;
        }
        else if (arg == "--import")
            while (i + 1 < count && std::string_view(args[size_t(i + 1)]).substr(0, 2) != "--")
                imports.push_back(ga2::pathFromUtf8(args[size_t(++i)]));
        else
        {
            std::cerr << "unknown option " << arg << "\n";
            return 2;
        }
    }
    try
    {
        ga2::Library library(dataDir);
        int failures = 0;
        for (const auto &image : imports)
        {
            try
            {
                const std::string id = library.import(image);
                std::cout << image.filename().string() << ": " << ga2::knownDisc(id)->label << '\n';
                std::string why;
                if (!ga2::ensureSkybox(library, *ga2::knownDisc(id), engineDir, why))
                    std::cerr << "  no space skybox: " << why << '\n';
            }
            catch (const std::exception &error)
            {
                std::cerr << image.filename().string() << ": " << error.what() << '\n';
                ++failures;
            }
        }
        if (list)
        {
            for (const auto &disc : library.discs())
                std::cout << "disc  " << disc.id << "  " << disc.image.string() << '\n';
            for (const auto &entry : ga2::startEntries())
                std::cout << "start " << entry.id << (library.canStart(entry) ? "  ready" : "  missing discs") << '\n';
        }
        if (!start.empty())
        {
            const ga2::StartEntry *entry = entryById(start);
            if (!entry || !library.canStart(*entry))
            {
                std::cerr << "cannot start " << start << '\n';
                return 2;
            }
            const Settings settings = Settings::load(settingsFile(library));
            settings.applyToEnvironment(library.dataDir());
            prepareMods(library, settings.value("language"));
            return runEngine(library.engineCommand(*entry, engineDir), logFor(library, *entry),
                             [&library] { refreshEnvironment(library); });
        }
        if (!imports.empty() || list) return failures ? 1 : 0;
        Menu(library, engineDir).run();
        return 0;
    }
    catch (const std::exception &error)
    {
        std::cerr << "ga2: " << error.what() << '\n';
        return 1;
    }
}
