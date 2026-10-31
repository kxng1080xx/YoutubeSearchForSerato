import os
import re
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TIT2, TPE1, TPE2, COMM, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover


if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
    PROJECT_DIR = _exe_dir.parent if _exe_dir.name.lower() == "dist" else _exe_dir
else:
    PROJECT_DIR = Path(__file__).resolve().parent

MUSIC_DIR = Path.home() / "Music"
SERATO_DIR = MUSIC_DIR / "_Serato_"


def _resolve_writable_dirs() -> tuple[Path, Path]:
    """
    Prefer C:\\Users\\Leon\\Music\\YouTube and _Serato_\\Subcrates when writable;
    fall back to project-local YouTube / Subcrates if Windows Controlled Folder Access
    or sandbox blocks writes to Music.
    """
    pref_yt = MUSIC_DIR / "YouTube"
    pref_crates = SERATO_DIR / "Subcrates"
    local_yt = PROJECT_DIR / "YouTube"
    local_crates = PROJECT_DIR / "Subcrates"
    try:
        pref_yt.mkdir(parents=True, exist_ok=True)
        test_file = pref_yt / ".write_test"
        test_file.write_bytes(b"ok")
        test_file.unlink(missing_ok=True)
        pref_crates.mkdir(parents=True, exist_ok=True)
        if local_yt.exists() and local_yt.resolve() != pref_yt.resolve():
            import shutil
            for f in local_yt.glob("*.m4a"):
                dest = pref_yt / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
        return pref_yt, pref_crates
    except OSError:
        local_yt.mkdir(parents=True, exist_ok=True)
        local_crates.mkdir(parents=True, exist_ok=True)
        return local_yt, local_crates


YOUTUBE_DIR, SUBCRATES_DIR = _resolve_writable_dirs()


def ensure_directories(base_dir: Optional[Path] = None) -> Path:
    """Ensure the YouTube music directory and Serato Subcrates directory exist."""
    target = Path(base_dir) if base_dir else YOUTUBE_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        target = YOUTUBE_DIR
        target.mkdir(parents=True, exist_ok=True)
    SUBCRATES_DIR.mkdir(parents=True, exist_ok=True)
    return target


def sanitize_crate_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f%]', "", name).strip().rstrip(".")
    return cleaned[:80]


def list_crates(base_dir: Optional[Path] = None) -> List[str]:
    """
    Return available crate names. 'YouTube' is always first (root folder),
    followed by any subfolder crates inside base_dir.
    """
    root = ensure_directories(base_dir)
    crates = ["YouTube"]
    try:
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if p.is_dir() and not p.name.startswith("."):
                crates.append(p.name)
    except OSError:
        pass
    return crates


def get_crate_folder(crate_name: str = "YouTube", base_dir: Optional[Path] = None) -> Path:
    """Return the filesystem directory for a given crate name."""
    root = ensure_directories(base_dir)
    clean = sanitize_crate_name(crate_name)
    if not clean or clean.lower() == "youtube":
        return root
    sub = root / clean
    sub.mkdir(parents=True, exist_ok=True)
    return sub


def create_crate(crate_name: str, base_dir: Optional[Path] = None) -> Path:
    """
    Create a new crate subfolder inside YouTube (for VirtualDJ & Serato folder drag)
    and write corresponding Serato .crate files in _Serato_\\Subcrates.
    """
    folder = get_crate_folder(crate_name, base_dir)
    clean = sanitize_crate_name(crate_name)
    if clean and clean.lower() != "youtube":
        sync_youtube_crate("YouTube", subfolder=clean, base_dir=base_dir)
    else:
        sync_youtube_crate("YouTube", base_dir=base_dir)
    return folder


def to_serato_portable_path(file_path: Path) -> str:
    """
    Convert an absolute Windows path (e.g. C:\\Users\\Leon\\Music\\YouTube\\Song.m4a)
    into Serato's drive-relative portable path (Users/Leon/Music/YouTube/Song.m4a).
    On macOS the root is "/" (/Users/leon/Music/... -> Users/leon/Music/...).
    """
    resolved = file_path.resolve()
    parts = resolved.parts
    rel_parts = parts[1:] if len(parts) > 1 and (":" in parts[0] or parts[0] == "/") else parts
    return "/".join(rel_parts)


def build_crate_bytes(track_paths: List[Path]) -> bytes:
    """
    Build a binary Serato .crate file from a list of local track paths.
    """
    version_str = "1.0/Serato ScratchLive Crate".encode("utf-16-be")
    chunks = [b"vrsn" + struct.pack(">I", len(version_str)) + version_str]

    for p in track_paths:
        if not p.exists():
            continue
        serato_path = to_serato_portable_path(p)
        path_bytes = serato_path.encode("utf-16-be")
        ptrk_block = b"ptrk" + struct.pack(">I", len(path_bytes)) + path_bytes
        otrk_block = b"otrk" + struct.pack(">I", len(ptrk_block)) + ptrk_block
        chunks.append(otrk_block)

    return b"".join(chunks)


def sync_youtube_crate(
    crate_name: str = "YouTube",
    subfolder: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> Path:
    """
    Scan the YouTube folder (and any subcrate folders) and write/update Serato .crate files
    in _Serato_\\Subcrates (both top-level YouTube.crate and YouTube%%Subcrate.crate + Subcrate.crate).
    """
    root = ensure_directories(base_dir)
    target_dir = root / subfolder if subfolder else root
    target_dir.mkdir(parents=True, exist_ok=True)

    audio_exts = {".m4a", ".mp3", ".wav", ".aiff", ".flac", ".aac", ".ogg"}
    tracks = sorted(
        [
            p
            for p in target_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in audio_exts
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    crate_bytes = build_crate_bytes(tracks)

    if subfolder:
        safe_sub = sanitize_crate_name(subfolder)
        # Write both nested subcrate (YouTube%%Name.crate) and standalone crate (Name.crate)
        nested_file = SUBCRATES_DIR / f"{crate_name}%%{safe_sub}.crate"
        standalone_file = SUBCRATES_DIR / f"{safe_sub}.crate"
        nested_file.write_bytes(crate_bytes)
        standalone_file.write_bytes(crate_bytes)
        # Also keep parent YouTube.crate updated with all tracks
        all_tracks = sorted(
            [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in audio_exts],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        (SUBCRATES_DIR / f"{crate_name}.crate").write_bytes(build_crate_bytes(all_tracks))
        return standalone_file
    else:
        crate_file = SUBCRATES_DIR / f"{crate_name}.crate"
        crate_file.write_bytes(crate_bytes)
        return crate_file


def clean_youtube_artist_title(raw_title: str, uploader: str = "") -> tuple[str, str]:
    """
    Split a raw YouTube video title and channel/uploader name into clean (artist, title).
    Strips common YouTube noise like (Official Video), [Lyrics], - Topic, VEVO, etc.
    """
    title = raw_title.strip()
    channel = (uploader or "").strip()

    channel = re.sub(r"\s*-\s*Topic$", "", channel, flags=re.IGNORECASE)
    if channel.lower().endswith("vevo") and len(channel) > 4:
        channel = channel[:-4].strip()

    title = re.sub(r"\s*-\s*Topic\s*-\s*", " - ", title, flags=re.IGNORECASE)

    noise_patterns = [
        r"\s*[\(\[]\s*official\s*(music\s*)?(video|audio|lyric\s*video|visualizer|hd|4k).*?[\)\]]",
        r"\s*[\(\[]\s*(lyric\s*video|lyrics|audio|visualizer|hd|hq|4k|explicit|clean\s*version).*?[\)\]]",
        r"\s*[\(\[]\s*prod\.?\s*by.*?[\)\]]",
    ]
    for pat in noise_patterns:
        title = re.sub(pat, "", title, flags=re.IGNORECASE).strip()

    title = re.sub(r"^([A-Za-z0-9\s]+)-\s+", r"\1 - ", title)

    if " - " in title:
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        if len(parts) >= 3:
            first_clean = re.sub(r"vevo$", "", parts[0], flags=re.IGNORECASE).strip()
            if (
                first_clean.lower() == parts[1].lower()
                or parts[1].lower().startswith(first_clean.lower())
                or parts[0].lower().endswith("vevo")
            ):
                parts = parts[1:]
        if len(parts) >= 2:
            artist = parts[0]
            song_title = " - ".join(parts[1:])
            artist = re.sub(r"\s*-\s*Topic$", "", artist, flags=re.IGNORECASE)
            if artist.lower().endswith("vevo") and len(artist) > 4:
                artist = artist[:-4].strip()
            return artist, song_title

    return (channel or "YouTube"), title


def tag_audio_file(
    file_path: Path,
    artist: str,
    title: str,
    album: str = "YouTube",
    video_id: str = "",
    cover_bytes: Optional[bytes] = None,
) -> None:
    """
    Write clean metadata tags (.m4a, .mp3, .flac) so Serato DJ Pro 4 and VirtualDJ
    populate Artist, Title, Album/Crate, Grouping, Comment, and Cover Art.
    """
    if not file_path.exists():
        return
    ext = file_path.suffix.lower()
    try:
        if ext == ".m4a":
            audio = MP4(str(file_path))
            if audio.tags is None:
                audio.add_tags()
            audio.tags["\xa9nam"] = [title]
            audio.tags["\xa9ART"] = [artist]
            audio.tags["aART"] = [artist]
            audio.tags["\xa9alb"] = [album]
            audio.tags["\xa9grp"] = [album]
            if video_id:
                audio.tags["\xa9cmt"] = [f"https://youtu.be/{video_id}"]
            if cover_bytes:
                fmt = (
                    MP4Cover.FORMAT_PNG
                    if cover_bytes.startswith(b"\x89PNG")
                    else MP4Cover.FORMAT_JPEG
                )
                audio.tags["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
            audio.save()
        elif ext == ".mp3":
            try:
                tags = ID3(str(file_path))
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("TIT2")
            tags.delall("TPE1")
            tags.delall("TPE2")
            tags.delall("TALB")
            tags.add(TIT2(encoding=3, text=[title]))
            tags.add(TPE1(encoding=3, text=[artist]))
            tags.add(TPE2(encoding=3, text=[artist]))
            tags.add(TALB(encoding=3, text=[album]))
            if video_id:
                tags.delall("COMM")
                tags.add(COMM(encoding=3, lang="eng", desc="", text=[f"https://youtu.be/{video_id}"]))
            if cover_bytes:
                mime = "image/png" if cover_bytes.startswith(b"\x89PNG") else "image/jpeg"
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover_bytes))
            tags.save(str(file_path), v2_version=3)
        elif ext == ".flac":
            audio = FLAC(str(file_path))
            audio["title"] = title
            audio["artist"] = artist
            audio["album"] = album
            if video_id:
                audio["comment"] = f"https://youtu.be/{video_id}"
            if cover_bytes:
                pic = Picture()
                pic.data = cover_bytes
                pic.type = 3
                pic.mime = "image/png" if cover_bytes.startswith(b"\x89PNG") else "image/jpeg"
                audio.clear_pictures()
                audio.add_picture(pic)
            audio.save()
    except Exception:
        pass


# Backwards-compatible alias
tag_m4a_file = tag_audio_file


def read_local_youtube_tracks(base_dir: Optional[Path] = None) -> List[Dict]:
    """
    List all audio files inside the YouTube folder with their metadata.
    """
    root = ensure_directories(base_dir)
    audio_exts = {".m4a", ".mp3", ".wav", ".aiff", ".flac"}
    files = sorted(
        [
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in audio_exts
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    results = []
    for f in files:
        artist = ""
        title = f.stem
        album = "YouTube"
        duration_sec = 0
        video_id = ""
        if f.suffix.lower() == ".m4a":
            try:
                audio = MP4(str(f))
                if audio.info and audio.info.length:
                    duration_sec = int(audio.info.length)
                if audio.tags:
                    title = audio.tags.get("\xa9nam", [title])[0]
                    artist = audio.tags.get("\xa9ART", [""])[0]
                    album = audio.tags.get("\xa9alb", ["YouTube"])[0]
                    cmt = audio.tags.get("\xa9cmt", [""])[0]
                    if "youtu.be/" in cmt:
                        video_id = cmt.split("youtu.be/")[-1].strip()
            except Exception:
                pass
        if not artist and " - " in title:
            artist, title = clean_youtube_artist_title(title)

        results.append(
            {
                "id": video_id or f.stem,
                "title": title,
                "artist": artist or "YouTube",
                "album": album,
                "duration": duration_sec,
                "file_path": str(f.resolve()),
                "file_name": f.name,
                "downloaded": True,
            }
        )
    return results
