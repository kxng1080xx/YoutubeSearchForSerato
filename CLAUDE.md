# SeratoYouTube — project log and status

Compact always-on-top PyQt6 overlay: search YouTube, click to download tagged
audio into `~/Music/YouTube`, drag the row onto a Serato / VirtualDJ deck.
Downloads are written into a Serato `YouTube` crate in `~/Music/_Serato_/Subcrates`.

Last updated: 2026-10-31

## Layout

| File | What it does |
|---|---|
| `app.py` | UI (overlay, results list, settings drawer, tray), workers, platform glue |
| `yt_engine.py` | yt-dlp search / download, "what to play next" suggestions |
| `serato_crate.py` | Music/Serato folders, `.crate` writer, audio tagging (mutagen) |
| `SeratoYouTube.spec` | PyInstaller spec for the Windows one-file exe |
| `.github/workflows/build-macos.yml` | macOS builds on GitHub Actions |

- Repo: https://github.com/kxng1080xx/YoutubeSearchForSerato (public, `main`)
- Songs (`YouTube/`, `Subcrates/`), `.venv`, `build/`, `dist/`, `*.exe` are gitignored.

## Run and build

- From source: `.venv\Scripts\python.exe app.py` (or `Launch Serato YouTube.bat`)
- Windows exe: `.venv\Scripts\pyinstaller.exe --noconfirm SeratoYouTube.spec`,
  then copy `dist\SeratoYouTube.exe` to the project root. On launch the app
  refreshes a copy on the Desktop (`_sync_desktop_copy`).
- macOS: push to `main` → Actions builds `SeratoYouTube-macOS-intel.zip` and
  `SeratoYouTube-macOS-arm64.zip` (artifacts). Tag `v*` also attaches them to a release.
- `.venv` uses Python 3.14 (`C:\Python314`). It was recreated after the move;
  rebuild it with `python -m venv .venv` + `pip install -r requirements.txt` if broken.

## Done (2026-10-31)

1. **macOS port.** Windows-only code is guarded by `IS_WIN` / `IS_MAC` in `app.py`:
   - global hotkey Ctrl+Shift+Y: Windows only (macOS would need Accessibility
     permission); menu-bar icon / Dock icon instead
   - single-instance mutex + process cleanup: Windows only (LaunchServices
     keeps a `.app` single-instance)
   - Run at Startup: registry on Windows, LaunchAgent
     `~/Library/LaunchAgents/com.seratoyoutube.overlay.plist` on macOS
   - refocus DJ app after a deck drop: `WindowFromPoint` on Windows,
     `open -a "Serato DJ Pro" | "Serato DJ Lite" | VirtualDJ` on macOS
   - open folder: `explorer` / `open`
   - fixed `to_serato_portable_path`: macOS paths became `//Users/...`; now `Users/...`
2. **macOS builds** on GitHub Actions for Intel (`macos-15-intel`, the main
   target) and Apple Silicon. Both pass; arch verified with `file`; ffmpeg
   (imageio-ffmpeg) is bundled. Minimum macOS is **13 Ventura**, set by PyQt6 6.11.
3. **"What to play next" (⏭ button).** Enabled after a track is dragged onto a deck.
   - Candidates: YouTube's Mix for that video (`watch?v=ID&list=RDID`) plus local tracks.
   - Ranking: BPM and key that Serato has written into the files
     (`tmpo`, `----:com.apple.iTunes:initialkey`; ID3 `TBPM`/`TKEY`),
     compatible = BPM ±6% or half/double, or Camelot neighbour / relative key.
     Rows show `✓ 100 BPM · 5A`.
   - Mix fetch is capped (`playlistend` + `lazy_playlist`): 23s → ~2.6s.
4. Rebuilt the Windows exe with all of the above.
5. Moved the project from `C:\Users\Leon\.gemini\antigravity\scratch\serato-youtube`
   to `D:\Vs Code Projects\SeratoYoutube`.

## Not yet verified / next steps

- [ ] Run the macOS build on a real Intel Mac (first launch: right-click → Open,
      or `xattr -cr`). Check drag-to-deck into Serato and that the crate shows up.
- [ ] If the target Mac is older than Ventura (macOS 12/11), build the Intel
      job with an older PyQt6 (Qt 6.7 or earlier).
- [ ] Suggestion ranking only covers files Serato has analysed (3 of 86 at
      the time). Running "Analyze Files" in Serato on the YouTube folder fixes that.
      Undownloaded YouTube candidates are unranked; ranking them needs audio analysis.
- [ ] Optional: global hotkey on macOS (needs Accessibility permission).
- [ ] Optional: sign / notarize for macOS (needs an Apple Developer account).
