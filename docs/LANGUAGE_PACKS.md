# Language packs and the English translation

The games are Japanese. A translation is a **language pack**: a mod folder
that the port loads when its language is chosen in the options. The disc
images are never modified. Switching the language back to Japanese gives you
the original text.

## The English translation

Releases from 0.9.0 on include the English translation, in the `mods/ga2-en`
folder next to `ga2`. Nothing needs installing:

1. Start `ga2` and open **Options** (O, or Start on a pad).
2. Set **Language** to **English**.
3. Start a game.

The rest of this page is for other language packs, or for installing a
newer version of the English pack than the one a release shipped with.

## Installing a language pack

1. Open the data folder:
   - Linux: `~/.local/share/ga2`
   - Windows: `%APPDATA%\ga2` (paste it into the Explorer address bar)
2. Create a `mods` folder there if there isn't one already.
3. Put the pack's folder in `mods`. A downloaded `.zip` has to be extracted
   first. The pack's `mod.json` must end up one level down, like this:

   ```
   ga2/
     mods/
       ga2-en/
         mod.json        contains "language": "en"
         files/          translated scripts and data
         textures/       translated pictures
         exe/            translated text inside the game programs (optional)
         text/           translated save-file messages (optional)
   ```

4. Start `ga2` and open **Options** (O). Set **Language** to **English**.
5. Start a game. The language applies from the next game start.

The launcher's **Mods** screen (M) shows the pack with its language. You
don't switch a language pack on there: it follows the Language option.
Other mods, such as HD art or fixes, can be switched on alongside it and
stack on top of it.

You can also change the language in a game (F2 > Language). The change is
marked "after restart" and applies the next time a game starts.

## What switches with the language

- The story scripts and all other replaced disc files.
- The translated pictures (menus, battle HUD text, title screens), which
  replace the game's textures at any resolution.
- The game names in the window title.
- Button prompts in the translated text: the pack writes them as
  placeholders such as `{circle}`, and the port draws the keyboard,
  PlayStation or Xbox icon for the device you're using.

Saves work in either language. A save stores the message that was on
screen. If the pack has a `text/` folder, that message is shown in the
pack's language when you load the save, even if the save was made in
Japanese.

## Updating or removing a pack

To update a pack, replace its folder in `mods` with the new version. To
remove it, delete the folder, or set Language back to Japanese.

## Making a language pack

How a pack is built from a translation spreadsheet, and the rules for
text, fonts and pictures:

- [MODDING.md](MODDING.md): the mod format, textures, and language packs.
- [modding/translation.md](modding/translation.md): the translation workbook
  (every line of the three games in one spreadsheet) and the build tool
  that turns it into a pack.
- [modding/text.md](modding/text.md) and [modding/scripts.md](modding/scripts.md):
  how the games lay out text, and the script format.
