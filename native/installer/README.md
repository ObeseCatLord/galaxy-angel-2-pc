# Installer and updater

`ga2-installer`: installs the newest release of Galaxy Angel II PC, keeps it
up to date and uninstalls it, on Windows x86-64 and Linux x86-64/ARM64. The
player-facing description is in [docs/PLAYING.md](../../docs/PLAYING.md#installing-and-updating);
how it works is at the top of `installer.h`.

- Build: `tools/ci/build-installer.sh linux|windows <out-file>` (a standalone
  CMake project; raylib and nlohmann/json come with FetchContent).
- Releases: `.github/workflows/installer.yml` builds it for the three
  platforms, puts it into each release archive and attaches it with
  `ga2-manifest.json` (`tools/release_manifest.py`), the file the installer
  reads from the newest release.
- Testing against local files: `GA2_INSTALLER_MANIFEST=<file>` with a manifest
  from `tools/release_manifest.py <dir> --tag vX.Y.Z --base-url <dir>`.
- `VERSION` is the installer's own version: raise it when the installer
  changes, so older copies offer to update themselves.
- `installer-font.otf` is a cut of the UI font (Noto Sans JP, SIL Open Font
  License 1.1, `native/fonts/OFL.txt`) made by `tools/installer_font.py`;
  regenerate it after changing the window's texts.
