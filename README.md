# Serato YouTube

A compact always-on-top search bar for YouTube. Search, click a result to
download it as tagged audio into `~/Music/YouTube`, then drag it onto a
Serato or VirtualDJ deck. Downloads are added to a `YouTube` crate in
`~/Music/_Serato_/Subcrates`.

## What to play next (Next ▸)

Click **Next ▸** in the bar to see what to play after the track you last dragged onto a deck (or, if you have not dragged one yet, the highlighted result):

- Candidates come from YouTube's own Mix for that track, plus your
  downloaded tracks.
- Tracks Serato has analysed are checked against the one you played.
  Same tempo (±6%, or half/double time) or a compatible key on the
  Camelot wheel go to the top, marked like `✓ 100 BPM · 5A`.
- Ranking only uses BPM and key that Serato has already written into the
  files, so it gets better the more of your library Serato has analysed.

## Windows

Run `SeratoYouTube.exe`, or `Launch Serato YouTube.bat` from source.
`Ctrl+Shift+Y` shows and hides the bar from anywhere.

Build: `pyinstaller SeratoYouTube.spec`

## macOS

Download the zip for your Mac from the latest
[Actions run](../../actions) (or a release): `SeratoYouTube-macOS-intel.zip`
for Intel Macs, `SeratoYouTube-macOS-arm64.zip` for Apple Silicon (M1 and
later). Unzip and move
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
every push to `main`, for both Intel and Apple Silicon.
