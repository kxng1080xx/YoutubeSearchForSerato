import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional
import requests
import yt_dlp

try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = shutil.which("ffmpeg")

from serato_crate import (
    YOUTUBE_DIR,
    clean_youtube_artist_title,
    ensure_directories,
    get_crate_folder,
    read_local_youtube_tracks,
    sync_youtube_crate,
    tag_audio_file,
)


def sanitize_filename(name: str) -> str:
    """Create a safe Windows filename without illegal characters."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned[:160] or "YouTube_Track"


def get_existing_video_map(base_dir: Optional[Path] = None) -> Dict[str, str]:
    """Map YouTube video_id (and filename stems) to local file paths."""
    mapping: Dict[str, str] = {}
    for item in read_local_youtube_tracks(base_dir):
        vid = item.get("id")
        fpath = item.get("file_path")
        if vid and fpath:
            mapping[vid] = fpath
        m = re.search(r"\[([A-Za-z0-9_-]{11})\]$", Path(fpath).stem)
        if m:
            mapping[m.group(1)] = fpath
    return mapping


def search_youtube(
    query: str,
    max_results: int = 15,
    base_dir: Optional[Path] = None,
) -> List[Dict]:
    """
    Search YouTube or resolve a pasted YouTube Video / Playlist URL.
    Returns a list of track metadata dicts ready for the UI.
    """
    query = query.strip()
    if not query:
        return []

    is_url = query.startswith(("http://", "https://", "www.youtube.com", "youtu.be", "music.youtube.com"))
    target = query if is_url else f"ytsearch{max_results}:{query}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist" if is_url else True,
        "skip_download": True,
        "ignoreerrors": True,
    }

    existing_map = get_existing_video_map(base_dir)
    results: List[Dict] = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target, download=False)
        if not info:
            return []

        entries = info.get("entries") if "entries" in info else [info]
        playlist_title = info.get("title") if "entries" in info and is_url else None

        for entry in entries or []:
            if not entry:
                continue
            vid = entry.get("id") or ""
            raw_title = entry.get("title") or "Unknown Title"
            if raw_title in ("[Deleted video]", "[Private video]"):
                continue
            uploader = entry.get("uploader") or entry.get("channel") or ""
            artist, clean_title = clean_youtube_artist_title(raw_title, uploader)
            duration = int(entry.get("duration") or 0)

            thumbnails = entry.get("thumbnails") or []
            thumb_url = entry.get("thumbnail") or (thumbnails[-1].get("url") if thumbnails else "")
            if not thumb_url and len(vid) == 11:
                thumb_url = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"

            url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
            if not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={vid}"

            local_path = existing_map.get(vid)

            results.append(
                {
                    "id": vid,
                    "raw_title": raw_title,
                    "title": clean_title,
                    "artist": artist,
                    "uploader": uploader,
                    "album": playlist_title or "YouTube",
                    "duration": duration,
                    "thumbnail": thumb_url,
                    "url": url,
                    "downloaded": bool(local_path and Path(local_path).exists()),
                    "file_path": local_path or "",
                }
            )

    return results


def download_youtube_track(
    item: Dict,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    target_crate: str = "YouTube",
    audio_format: str = "m4a",
    base_dir: Optional[Path] = None,
) -> Path:
    """
    Download a YouTube track in maximum quality (320kbps M4A/MP3 or lossless FLAC/WAV)
    into the selected crate folder, write clean Artist / Title / Album / Cover Art tags,
    and sync the Serato .crate files.
    """
    codec = (audio_format or "m4a").lower()
    if codec not in ("m4a", "mp3", "flac", "wav"):
        codec = "m4a"

    vid = item.get("id") or ""
    raw_title = item.get("raw_title") or item.get("title") or "YouTube Track"
    uploader = item.get("uploader") or item.get("artist") or ""
    artist, clean_title = clean_youtube_artist_title(raw_title, uploader)
    if item.get("artist") and item.get("title"):
        artist = item["artist"]
        clean_title = item["title"]

    crate_label = (target_crate or "YouTube").strip() or "YouTube"
    out_dir = get_crate_folder(crate_label, base_dir=base_dir)
    safe_base = sanitize_filename(f"{artist} - {clean_title}")
    final_file = out_dir / f"{safe_base}.{codec}"

    subcrate_arg = None if crate_label.lower() == "youtube" else crate_label

    if final_file.exists() and final_file.stat().st_size > 10_000:
        if progress_callback:
            progress_callback(100.0, "Ready")
        sync_youtube_crate("YouTube", subfolder=subcrate_arg, base_dir=base_dir)
        return final_file

    temp_template = str(out_dir / f"{safe_base}.%(ext)s")

    def _hook(d: dict):
        if not progress_callback:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100.0) if total > 0 else 50.0
            progress_callback(min(95.0, pct), f"Downloading {pct:.0f}%")
        elif status == "finished":
            progress_callback(96.0, "Tagging...")

    ydl_opts = {
        "format": "bestaudio/best",
        "format_sort": ["abr", "asr", "acodec"],
        "outtmpl": temp_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 8,
        "progress_hooks": [_hook],
    }

    if FFMPEG_EXE:
        ydl_opts["ffmpeg_location"] = FFMPEG_EXE
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": codec,
                "preferredquality": "320" if codec in ("m4a", "mp3") else "0",
            }
        ]
        if codec in ("m4a", "mp3"):
            ydl_opts["postprocessor_args"] = {
                "extractaudio": ["-b:a", "320k"]
            }

    url = item.get("url") or f"https://www.youtube.com/watch?v={vid}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    if not final_file.exists():
        candidates = sorted(
            out_dir.glob(f"{safe_base}.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            final_file = candidates[0]

    cover_bytes = None
    thumb_url = item.get("thumbnail") or (
        f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if len(vid) == 11 else ""
    )
    if thumb_url:
        try:
            resp = requests.get(thumb_url, timeout=4)
            if resp.status_code == 200:
                cover_bytes = resp.content
        except Exception:
            pass

    tag_audio_file(
        file_path=final_file,
        artist=artist,
        title=clean_title,
        album=crate_label,
        video_id=vid,
        cover_bytes=cover_bytes,
    )

    sync_youtube_crate("YouTube", subfolder=subcrate_arg, base_dir=base_dir)
    if progress_callback:
        progress_callback(100.0, "Ready")

    return final_file
