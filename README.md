# Serato YouTube

A compact always-on-top search bar for YouTube. Search, click a result to
download it as tagged audio into `~/Music/YouTube`, then drag it onto a
Serato or VirtualDJ deck. Downloads are added to a `YouTube` crate in
`~/Music/_Serato_/Subcrates`.

## Windows

Run `SeratoYouTube.exe`, or `Launch Serato YouTube.bat` from source.
`Ctrl+Shift+Y` shows and hides the bar from anywhere.

Build: `pyinstaller SeratoYouTube.spec`

## macOS

Download `SeratoYouTube-macOS-arm64.zip` from the latest
[Actions run](../../actions) (or a release), unzip, and move
`SeratoYouTube.app` to Applications.

The app is not signed, so the first launch needs one of:

- Right-click the app → **Open** → **Open**
- or in Terminal: `xattr -cr /Applications/SeratoYouTube.app`

Differences from Windows:

- No global hotkey. Use the menu-bar icon or the Dock icon instead.
- After a deck drop it refocuses Serato DJ Pro / Lite or VirtualDJ,
  whichever is running.
- "Open at Login" writes `~/Library/LaunchAgents/com.seratoyoutube.overlay.plist`.

The build runs on GitHub Actions (`.github/workflows/build-macos.yml`) on
every push to `main`. It targets Apple Silicon.
