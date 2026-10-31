"""
Serato 4 & VirtualDJ YouTube Overlay — compact always-on-top search bar.

- Type -> Enter -> results drop down
- Click any result -> downloads immediately in max quality (320 kbps M4A/MP3 or Lossless)
- Drag a green DRAG row onto a Serato or VirtualDJ deck -> clears search box (keeps results)
  and hands keyboard focus back to Serato / VirtualDJ
- Gear (⚙) button opens a compact Settings & Crates drawer to create/select Crates,
  drag a Crate folder into Serato/VirtualDJ, and configure format/startup options.
"""

import ctypes
import os
import subprocess
import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes.wintypes
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
    QMimeData,
    QPoint,
    QPointF,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDrag,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from serato_crate import (
    MUSIC_DIR,
    YOUTUBE_DIR,
    clean_youtube_artist_title,
    create_crate,
    ensure_directories,
    get_crate_folder,
    list_crates,
    sync_youtube_crate,
    tag_audio_file,
)
from yt_engine import FFMPEG_EXE, download_youtube_track, sanitize_filename, search_youtube


WIDTH = 480
ROW_H = 46
MAX_ROWS = 7

HOTKEY_ID = 0x5359
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
VK_Y = 0x59
HWND_BROADCAST = 0xFFFF
ERROR_ALREADY_EXISTS = 183

WM_SHOW_OVERLAY = ctypes.windll.user32.RegisterWindowMessageW("SeratoYouTubeOverlay_Show") if IS_WIN else 0
_MUTEX_HANDLE = None

STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "SeratoYouTube"

MAC_LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.seratoyoutube.overlay.plist"
MAC_DJ_APPS = ("Serato DJ Pro", "Serato DJ Lite", "VirtualDJ")

HOTKEY_LABEL = "Ctrl+Shift+Y" if IS_WIN else "menu-bar icon"
STARTUP_LABEL = "Run at Windows Startup" if IS_WIN else "Open at Login"


def open_folder(path: Path):
    subprocess.Popen(["explorer" if IS_WIN else "open", str(path)])


# ---------------------------------------------------------------- helpers

def create_app_pixmap(size: int = 64) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor("#14161B")))
    p.setPen(QPen(QColor("#FF0033"), max(2, size // 14)))
    m = size // 12
    p.drawRoundedRect(m, m, size - 2 * m, size - 2 * m, size // 4, size // 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#FF0033")))
    cx, cy, r = size / 2.0, size / 2.0, size * 0.24
    p.drawPolygon(QPolygonF([
        QPointF(cx - r * 0.65, cy - r * 0.85),
        QPointF(cx - r * 0.65, cy + r * 0.85),
        QPointF(cx + r * 0.95, cy),
    ]))
    p.end()
    return pix


def create_app_icon() -> QIcon:
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128):
        icon.addPixmap(create_app_pixmap(sz))
    return icon


def focus_window_under_cursor() -> bool:
    """
    Bring the top-level window under the mouse (Serato / VirtualDJ) to the
    foreground so keyboard shortcuts work immediately after a deck drop.
    """
    if IS_MAC:
        # No window-under-cursor API without Accessibility permission, so
        # activate whichever DJ app is running. `open -a` needs no permission.
        for name in MAC_DJ_APPS:
            if subprocess.run(["pgrep", "-xq", name]).returncode == 0:
                subprocess.Popen(["open", "-a", name])
                return True
        return False
    if not IS_WIN:
        return False

    user32 = ctypes.windll.user32
    user32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
    user32.WindowFromPoint.restype = ctypes.wintypes.HWND
    user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.wintypes.HWND

    pt = ctypes.wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return False
    root = user32.GetAncestor(hwnd, 2)  # GA_ROOT
    if not root:
        return False

    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False

    if user32.SetForegroundWindow(root):
        return True
    VK_MENU, KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYUP, 0)
    if user32.SetForegroundWindow(root):
        return True

    user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    target_thread = user32.GetWindowThreadProcessId(root, None)
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = []
    for t in {fg_thread, target_thread} - {0, me}:
        if user32.AttachThreadInput(me, t, True):
            attached.append(t)
    try:
        user32.BringWindowToTop(root)
        ok = bool(user32.SetForegroundWindow(root))
        user32.SetFocus(root)
    finally:
        for t in attached:
            user32.AttachThreadInput(me, t, False)
    return ok


def format_duration(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def seed_existing_music_tracks(base_dir: Optional[Path] = None) -> int:
    """Copy/convert loose YouTube .m4a/.opus files in ~/Music into the YouTube folder."""
    root = ensure_directories(base_dir)
    count = 0
    for f in MUSIC_DIR.glob("*"):
        if not f.is_file() or f.suffix.lower() not in (".m4a", ".opus"):
            continue
        artist, title = clean_youtube_artist_title(f.stem)
        dest = root / (sanitize_filename(f"{artist} - {title}") + ".m4a")
        if dest.exists():
            continue
        try:
            if f.suffix.lower() == ".m4a":
                import shutil
                shutil.copy2(f, dest)
            elif FFMPEG_EXE:
                subprocess.run(
                    [FFMPEG_EXE, "-y", "-i", str(f), "-vn", "-c:a", "aac", "-b:a", "320k", str(dest)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                continue
            tag_audio_file(dest, artist=artist, title=title, album="YouTube")
            count += 1
        except Exception:
            pass
    sync_youtube_crate("YouTube", base_dir=root)
    return count


def _startup_exe_command() -> str:
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
    else:
        exe_path = Path(__file__).resolve().parent / "SeratoYouTube.exe"
    return f'"{exe_path}"'


def _mac_launch_agent_plist() -> str:
    # Inside a .app, sys.executable is Contents/MacOS/SeratoYouTube.
    exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(sys.executable)
    args = [str(exe)] if getattr(sys, "frozen", False) else [str(exe), str(Path(__file__).resolve())]
    from xml.sax.saxutils import escape
    items = "".join(f"<string>{escape(a)}</string>" for a in args + ["--tray"])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        "<key>Label</key><string>com.seratoyoutube.overlay</string>"
        f"<key>ProgramArguments</key><array>{items}</array>"
        "<key>RunAtLoad</key><true/>"
        "</dict></plist>\n"
    )


def is_startup_enabled() -> bool:
    if IS_MAC:
        return MAC_LAUNCH_AGENT.exists()
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, STARTUP_REG_NAME)
            return bool(val)
    except OSError:
        return False


def set_startup_enabled(enabled: bool = True) -> bool:
    if IS_MAC:
        try:
            if enabled:
                MAC_LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
                MAC_LAUNCH_AGENT.write_text(_mac_launch_agent_plist())
            else:
                MAC_LAUNCH_AGENT.unlink(missing_ok=True)
            return True
        except OSError:
            return False
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, _startup_exe_command())
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


class WinHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback, show_callback=None):
        super().__init__()
        self.callback = callback
        self.show_callback = show_callback or callback

    def nativeEventFilter(self, eventType, message):
        if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.callback()
                    return True, 0
                if WM_SHOW_OVERLAY and msg.message == WM_SHOW_OVERLAY:
                    self.show_callback()
                    return True, 0
            except Exception:
                pass
        return False, 0


# ---------------------------------------------------------------- workers

class SearchWorker(QThread):
    done = pyqtSignal(int, list)
    failed = pyqtSignal(int, str)

    def __init__(self, token: int, query: str, base_dir: Path):
        super().__init__()
        self.token, self.query, self.base_dir = token, query, base_dir

    def run(self):
        try:
            self.done.emit(self.token, search_youtube(self.query, max_results=12, base_dir=self.base_dir))
        except Exception as e:
            self.failed.emit(self.token, str(e))


class DownloadWorker(QThread):
    progress = pyqtSignal(str, float)
    finished_ok = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)

    def __init__(self, item: Dict, target_crate: str, audio_format: str, base_dir: Path):
        super().__init__()
        self.item = item
        self.vid = item.get("id") or item.get("title") or ""
        self.target_crate = target_crate
        self.audio_format = audio_format
        self.base_dir = base_dir

    def run(self):
        try:
            out = download_youtube_track(
                self.item,
                progress_callback=lambda pct, _m: self.progress.emit(self.vid, pct),
                target_crate=self.target_crate,
                audio_format=self.audio_format,
                base_dir=self.base_dir,
            )
            self.finished_ok.emit(self.vid, str(Path(out).resolve()))
        except Exception as e:
            self.failed.emit(self.vid, str(e))


# ---------------------------------------------------------------- widgets

class DraggableCratePill(QLabel):
    """
    A compact draggable badge that drags the currently selected Crate folder
    into Serato 4's Crate sidebar or VirtualDJ's folder list.
    """

    def __init__(self, folder_getter, parent=None):
        super().__init__("📁 Drag Crate ⣿", parent)
        self._folder_getter = folder_getter
        self._press: Optional[QPoint] = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag this badge into Serato's Crate sidebar (or VirtualDJ) to add/refresh the Crate folder")
        self.setStyleSheet(
            """
            QLabel {
                background: #1E222B;
                color: #34D399;
                border: 1px solid #059669;
                border-radius: 5px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel:hover { background: #06382C; }
            """
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.MouseButton.LeftButton) or self._press is None:
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        folder: Path = self._folder_getter()
        folder.mkdir(parents=True, exist_ok=True)

        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(folder.resolve()))])
        drag.setMimeData(mime)

        pix = QPixmap(220, 28)
        pix.fill(QColor("#0F1115"))
        p = QPainter(pix)
        p.setPen(QColor("#10B981"))
        p.drawRect(0, 0, 219, 27)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, f"📁 Crate: {folder.name}")
        p.end()
        drag.setPixmap(pix)
        drag.exec(Qt.DropAction.CopyAction)
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class ResultRow(QWidget):
    """One dropdown row: title / artist • time, plus a state badge on the right when downloading or ready."""

    def __init__(self, data: Dict):
        super().__init__()
        self.data = data
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 10, 4)
        lay.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = QLabel()
        self.title.setStyleSheet("color:#F3F4F6; font-size:13px; font-weight:600; background:transparent;")
        self.sub = QLabel()
        self.sub.setStyleSheet("color:#8B90A0; font-size:11px; background:transparent;")
        col.addWidget(self.title)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)

        self.badge = QLabel()
        self.badge.setFixedSize(62, 22)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.badge)

        text_w = WIDTH - 12 - 10 - 10 - 62 - 24
        fm_t = self.title.fontMetrics()
        self.title.setText(fm_t.elidedText(data.get("title") or "", Qt.TextElideMode.ElideRight, text_w))
        sub = " • ".join(x for x in (data.get("artist") or "", format_duration(data.get("duration") or 0)) if x)
        self.sub.setText(self.sub.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, text_w))

        self.set_state("ready" if self.is_ready() else "idle")

    def is_ready(self) -> bool:
        fp = self.data.get("file_path") or ""
        return bool(self.data.get("downloaded") and fp and Path(fp).exists())

    def set_state(self, state: str, pct: float = 0.0):
        if state == "idle":
            self.badge.hide()
            return
        styles = {
            "loading": (f"{pct:.0f}%", "#FBBF24", "#B45309", "#2A2110"),
            "ready": ("DRAG ⣿", "#34D399", "#059669", "#06281F"),
            "error": ("RETRY", "#F87171", "#B91C1C", "#2A1212"),
        }
        text, fg, border, bg = styles[state]
        self.badge.setText(text)
        self.badge.setStyleSheet(
            f"QLabel{{color:{fg}; border:1px solid {border}; background:{bg};"
            f" border-radius:4px; font-size:10px; font-weight:700;}}"
        )
        self.badge.show()


class ResultsList(QListWidget):
    """Dropdown list. Instant download on mouse-down; drag a ready row = native file drop (CF_HDROP)."""

    row_pressed = pyqtSignal(QListWidgetItem)
    track_drag_completed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press: Optional[QPoint] = None
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def row_widget(self, item: QListWidgetItem) -> Optional[ResultRow]:
        w = self.itemWidget(item)
        return w if isinstance(w, ResultRow) else None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            item = self.itemAt(self._press)
            if item is not None:
                self.row_pressed.emit(item)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.MouseButton.LeftButton) or self._press is None:
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        item = self.itemAt(self._press)
        row = self.row_widget(item) if item else None
        if not row or not row.is_ready():
            return
        self._press = None

        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(row.data["file_path"])])
        drag.setMimeData(mime)

        label = (row.data.get("title") or "")[:40]
        pix = QPixmap(280, 30)
        pix.fill(QColor("#0F1115"))
        p = QPainter(pix)
        p.setPen(QColor("#10B981"))
        p.drawRect(0, 0, 279, 29)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, f"🎵 {label}")
        p.end()
        drag.setPixmap(pix)
        drag.exec(Qt.DropAction.CopyAction)
        self.track_drag_completed.emit()


# ---------------------------------------------------------------- overlay

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(WIDTH)
        self.app_icon = create_app_icon()
        self.setWindowIcon(self.app_icon)
        self.setWindowTitle("Serato YouTube")

        self.settings = QSettings("SeratoYouTube", "Overlay")
        self._drag_offset: Optional[QPoint] = None
        self._search_token = 0
        self._search_workers: List[SearchWorker] = []
        self._downloads: Dict[str, DownloadWorker] = {}
        self._rows: Dict[str, ResultRow] = {}
        self._force_quit = False
        self._tray_hint_shown = False

        self._build_ui()
        self._setup_tray()
        self._register_hotkey()
        self._restore_position()

    # ---- settings getters
    def base_dir(self) -> Path:
        saved = self.settings.value("base_dir", "")
        if saved:
            return Path(str(saved))
        return YOUTUBE_DIR

    def active_crate(self) -> str:
        return str(self.settings.value("active_crate", "YouTube") or "YouTube")

    def audio_format(self) -> str:
        return str(self.settings.value("audio_format", "m4a") or "m4a")

    def clear_search_on_drop(self) -> bool:
        val = self.settings.value("clear_search_on_drop", "true")
        return str(val).lower() not in ("false", "0", "no")

    def focus_dj_on_drop(self) -> bool:
        val = self.settings.value("focus_dj_on_drop", "true")
        return str(val).lower() not in ("false", "0", "no")

    # ---- UI
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            """
            #card { background: rgba(17,18,22,248); border: 1px solid #2A2D36; border-radius: 10px; }
            QLineEdit { background: transparent; border: none; color: #F9FAFB; font-size: 14px;
                        font-family: 'Segoe UI'; padding: 4px 2px; selection-background-color: #FF0033; }
            QLineEdit#subInput { background: #1B1E26; border: 1px solid #2E3340; border-radius: 5px;
                                 font-size: 12px; padding: 4px 8px; }
            QLineEdit#subInput:focus { border: 1px solid #FF0033; }
            QComboBox { background: #1B1E26; color: #F3F4F6; border: 1px solid #2E3340;
                        border-radius: 5px; padding: 4px 8px; font-size: 12px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #181A20; color: #F3F4F6; selection-background-color: #FF0033; }
            QListWidget { background: transparent; border: none; border-top: 1px solid #23262E; outline: 0; }
            QListWidget::item { border-radius: 6px; margin: 1px 4px; }
            QListWidget::item:hover { background: #1E2129; }
            QListWidget::item:selected { background: #252935; }
            QPushButton#iconBtn { background: transparent; color: #8B90A0; border: none; font-size: 14px; border-radius: 4px; }
            QPushButton#iconBtn:hover { color: #FFFFFF; background: #232730; }
            QPushButton#close { background: transparent; color: #6B7080; border: none; font-size: 13px; }
            QPushButton#close:hover { color: #FF0033; }
            QPushButton#smallBtn { background: #252934; color: #E5E7EB; border: 1px solid #363B48;
                                   border-radius: 5px; padding: 4px 10px; font-size: 11px; font-weight: 600; }
            QPushButton#smallBtn:hover { background: #303543; border-color: #FF0033; }
            QCheckBox { color: #D1D5DB; font-size: 11px; spacing: 6px; }
            QScrollBar:vertical { background: transparent; width: 6px; margin: 2px; }
            QScrollBar::handle:vertical { background: #353946; border-radius: 3px; min-height: 20px; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
            """
        )
        outer.addWidget(self.card)

        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(4)

        # ---- Top Search Bar
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 0, 2, 0)
        bar.setSpacing(6)

        self.grip = QLabel("▶")
        self.grip.setToolTip("Drag to move overlay")
        self.grip.setCursor(Qt.CursorShape.SizeAllCursor)
        self.grip.setStyleSheet("color:#FF0033; font-size:13px; background:transparent;")
        bar.addWidget(self.grip)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search YouTube…  (Enter)")
        self.search.returnPressed.connect(self.start_search)
        bar.addWidget(self.search, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#8B90A0; font-size:11px; background:transparent;")
        bar.addWidget(self.status)

        self.crate_tag = QPushButton()
        self.crate_tag.setObjectName("smallBtn")
        self.crate_tag.setCursor(Qt.CursorShape.PointingHandCursor)
        self.crate_tag.setToolTip("Active download Crate (click to open Settings & Crates)")
        self.crate_tag.clicked.connect(self.toggle_settings_drawer)
        bar.addWidget(self.crate_tag)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("iconBtn")
        self.settings_btn.setFixedSize(24, 24)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setToolTip("Settings & Crates")
        self.settings_btn.clicked.connect(self.toggle_settings_drawer)
        bar.addWidget(self.settings_btn)

        min_btn = QPushButton("—")
        min_btn.setObjectName("iconBtn")
        min_btn.setFixedSize(22, 22)
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.setToolTip(f"Minimize to system tray ({HOTKEY_LABEL})")
        min_btn.clicked.connect(self.hide_to_tray)
        bar.addWidget(min_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("close")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Quit Serato YouTube")
        close_btn.clicked.connect(self.quit_app)
        bar.addWidget(close_btn)

        cl.addLayout(bar)

        # ---- Collapsible Settings & Crates Drawer
        self.settings_drawer = QFrame()
        self.settings_drawer.setStyleSheet(
            "QFrame#drawer { border-top: 1px solid #252830; padding-top: 4px; background: transparent; }"
        )
        self.settings_drawer.setObjectName("drawer")
        dl = QVBoxLayout(self.settings_drawer)
        dl.setContentsMargins(8, 6, 8, 6)
        dl.setSpacing(7)

        # Row 1: Active Crate selector + Drag Crate pill
        r_crate = QHBoxLayout()
        r_crate.setSpacing(6)
        lbl_c = QLabel("Crate:")
        lbl_c.setFixedWidth(52)
        lbl_c.setStyleSheet("color:#9CA3AF; font-size:11px; font-weight:600;")
        r_crate.addWidget(lbl_c)

        self.crate_combo = QComboBox()
        self.crate_combo.currentTextChanged.connect(self.on_crate_selected)
        r_crate.addWidget(self.crate_combo, 1)

        self.drag_crate_pill = DraggableCratePill(
            lambda: get_crate_folder(self.active_crate(), self.base_dir())
        )
        r_crate.addWidget(self.drag_crate_pill)
        dl.addLayout(r_crate)

        # Row 2: Create New Crate
        r_new_crate = QHBoxLayout()
        r_new_crate.setSpacing(6)
        lbl_nc = QLabel("New:")
        lbl_nc.setFixedWidth(52)
        lbl_nc.setStyleSheet("color:#9CA3AF; font-size:11px; font-weight:600;")
        r_new_crate.addWidget(lbl_nc)

        self.new_crate_input = QLineEdit()
        self.new_crate_input.setObjectName("subInput")
        self.new_crate_input.setPlaceholderText("New crate name (e.g. Dancehall, Afrobeats)…")
        self.new_crate_input.returnPressed.connect(self.on_create_crate)
        r_new_crate.addWidget(self.new_crate_input, 1)

        btn_create = QPushButton("+ Create Crate")
        btn_create.setObjectName("smallBtn")
        btn_create.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_create.clicked.connect(self.on_create_crate)
        r_new_crate.addWidget(btn_create)
        dl.addLayout(r_new_crate)

        # Row 3: Audio Quality / Format
        r_fmt = QHBoxLayout()
        r_fmt.setSpacing(6)
        lbl_fmt = QLabel("Quality:")
        lbl_fmt.setFixedWidth(52)
        lbl_fmt.setStyleSheet("color:#9CA3AF; font-size:11px; font-weight:600;")
        r_fmt.addWidget(lbl_fmt)

        self.format_combo = QComboBox()
        self.format_combo.addItem("M4A — 320 kbps AAC (Best for Serato & VDJ)", "m4a")
        self.format_combo.addItem("MP3 — 320 kbps CBR", "mp3")
        self.format_combo.addItem("FLAC — Lossless Compressed", "flac")
        self.format_combo.addItem("WAV — Lossless Uncompressed", "wav")
        saved_fmt = self.audio_format()
        idx_fmt = self.format_combo.findData(saved_fmt)
        if idx_fmt >= 0:
            self.format_combo.setCurrentIndex(idx_fmt)
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        r_fmt.addWidget(self.format_combo, 1)
        dl.addLayout(r_fmt)

        # Row 4: Folder Path
        r_dir = QHBoxLayout()
        r_dir.setSpacing(6)
        lbl_dir = QLabel("Folder:")
        lbl_dir.setFixedWidth(52)
        lbl_dir.setStyleSheet("color:#9CA3AF; font-size:11px; font-weight:600;")
        r_dir.addWidget(lbl_dir)

        self.dir_label = QLineEdit(str(self.base_dir()))
        self.dir_label.setObjectName("subInput")
        self.dir_label.setReadOnly(True)
        r_dir.addWidget(self.dir_label, 1)

        btn_browse = QPushButton("Browse…")
        btn_browse.setObjectName("smallBtn")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self.on_browse_folder)
        r_dir.addWidget(btn_browse)

        btn_open = QPushButton("Open")
        btn_open.setObjectName("smallBtn")
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.clicked.connect(
            lambda: open_folder(get_crate_folder(self.active_crate(), self.base_dir()))
        )
        r_dir.addWidget(btn_open)
        dl.addLayout(r_dir)

        # Row 5: Behavior Checkboxes
        r_opts = QHBoxLayout()
        r_opts.setSpacing(10)

        self.chk_clear_search = QCheckBox("Clear search box on drop")
        self.chk_clear_search.setChecked(self.clear_search_on_drop())
        self.chk_clear_search.toggled.connect(lambda v: self.settings.setValue("clear_search_on_drop", v))
        r_opts.addWidget(self.chk_clear_search)

        self.chk_focus_dj = QCheckBox("Return focus to DJ app")
        self.chk_focus_dj.setChecked(self.focus_dj_on_drop())
        self.chk_focus_dj.toggled.connect(lambda v: self.settings.setValue("focus_dj_on_drop", v))
        r_opts.addWidget(self.chk_focus_dj)

        self.chk_startup = QCheckBox("Run at Startup")
        self.chk_startup.setChecked(is_startup_enabled())
        self.chk_startup.toggled.connect(self.on_startup_toggled)
        r_opts.addWidget(self.chk_startup)
        r_opts.addStretch(1)
        dl.addLayout(r_opts)

        self.settings_drawer.hide()
        cl.addWidget(self.settings_drawer)

        # ---- Results List
        self.results = ResultsList()
        self.results.row_pressed.connect(self.on_item_clicked)
        self.results.itemClicked.connect(self.on_item_clicked)
        self.results.track_drag_completed.connect(self.on_track_drag_completed)
        self.results.hide()
        cl.addWidget(self.results)

        self._refresh_crates_combo()
        self._sync_window_height()

    def _sync_window_height(self):
        self.card.layout().activate()
        self.layout().activate()
        h = self.card.layout().sizeHint().height()
        self.setFixedHeight(max(42, h))

    def _refresh_crates_combo(self):
        crates = list_crates(self.base_dir())
        current = self.active_crate()
        if current not in crates:
            crates.append(current)
        self.crate_combo.blockSignals(True)
        self.crate_combo.clear()
        self.crate_combo.addItems(crates)
        self.crate_combo.setCurrentText(current)
        self.crate_combo.blockSignals(False)
        self.crate_tag.setText(f"📁 {current}")

    def toggle_settings_drawer(self):
        if self.settings_drawer.isVisible():
            self.settings_drawer.hide()
            if self.status.text().startswith("created "):
                self.status.setText("")
        else:
            self._refresh_crates_combo()
            self.settings_drawer.show()
        self._sync_window_height()

    def on_crate_selected(self, name: str):
        if not name:
            return
        self.settings.setValue("active_crate", name)
        self.crate_tag.setText(f"📁 {name}")
        create_crate(name, self.base_dir())

    def on_create_crate(self):
        raw = self.new_crate_input.text().strip()
        if not raw:
            return
        folder = create_crate(raw, self.base_dir())
        clean_name = folder.name if folder.resolve() != self.base_dir().resolve() else "YouTube"
        self.settings.setValue("active_crate", clean_name)
        self.new_crate_input.clear()
        self._refresh_crates_combo()
        self.status.setText(f"created '{clean_name}'")
        QTimer.singleShot(
            2500,
            lambda: self.status.setText("") if self.status.text().startswith("created ") else None,
        )

    def on_format_changed(self, _idx: int):
        fmt = self.format_combo.currentData()
        if fmt:
            self.settings.setValue("audio_format", fmt)

    def on_browse_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select YouTube Download Folder", str(self.base_dir()))
        if chosen:
            p = Path(chosen)
            self.settings.setValue("base_dir", str(p))
            self.dir_label.setText(str(p))
            self._refresh_crates_combo()

    def on_startup_toggled(self, checked: bool):
        set_startup_enabled(checked)
        if hasattr(self, "a_startup"):
            self.a_startup.blockSignals(True)
            self.a_startup.setChecked(checked)
            self.a_startup.blockSignals(False)

    def on_track_drag_completed(self):
        """
        Called right when the user releases the mouse after dragging a song onto a deck.
        Clears the search box (leaving the results dropdown visible) and returns keyboard
        focus to Serato / VirtualDJ.
        """
        if self.clear_search_on_drop():
            self.search.clear()
        if self.focus_dj_on_drop():
            QTimer.singleShot(60, focus_window_under_cursor)

    def _set_results_height(self):
        n = self.results.count()
        if n == 0:
            self.results.hide()
        else:
            self.results.setFixedHeight(min(n, MAX_ROWS) * (ROW_H + 2) + 6)
            self.results.show()
        self._sync_window_height()

    # ---- search
    def start_search(self):
        q = self.search.text().strip()
        if not q:
            self.clear_results()
            return
        self._search_token += 1
        self.status.setText("searching…")
        w = SearchWorker(self._search_token, q, self.base_dir())
        w.done.connect(self.on_results)
        w.failed.connect(self.on_search_failed)
        w.finished.connect(lambda w=w: self._search_workers.remove(w) if w in self._search_workers else None)
        self._search_workers.append(w)
        w.start()

    def on_results(self, token: int, items: List[Dict]):
        if token != self._search_token:
            return
        self.status.setText("" if items else "no results")
        self.results.clear()
        self._rows.clear()
        for data in items:
            vid = data.get("id") or data.get("title") or ""
            row = ResultRow(data)
            if vid in self._downloads and self._downloads[vid].isRunning():
                row.set_state("loading")
            li = QListWidgetItem(self.results)
            li.setSizeHint(QSize(WIDTH - 24, ROW_H))
            self.results.setItemWidget(li, row)
            self._rows[vid] = row
        self._set_results_height()

    def on_search_failed(self, token: int, err: str):
        if token == self._search_token:
            self.status.setText("search failed")
            self.status.setToolTip(err)

    def clear_results(self):
        self.results.clear()
        self._rows.clear()
        self.status.setText("")
        self._set_results_height()

    # ---- download
    def on_item_clicked(self, item: QListWidgetItem):
        row = self.results.row_widget(item)
        if not row or row.is_ready():
            return
        vid = row.data.get("id") or row.data.get("title") or ""
        if vid in self._downloads and self._downloads[vid].isRunning():
            return
        row.set_state("loading", 0)
        w = DownloadWorker(
            row.data,
            target_crate=self.active_crate(),
            audio_format=self.audio_format(),
            base_dir=self.base_dir(),
        )
        w.progress.connect(self.on_dl_progress)
        w.finished_ok.connect(self.on_dl_done)
        w.failed.connect(self.on_dl_failed)
        self._downloads[vid] = w
        w.start()

    def on_dl_progress(self, vid: str, pct: float):
        row = self._rows.get(vid)
        if row:
            row.set_state("loading", pct)

    def on_dl_done(self, vid: str, path: str):
        row = self._rows.get(vid)
        if row:
            row.data["downloaded"] = True
            row.data["file_path"] = path
            row.set_state("ready")

    def on_dl_failed(self, vid: str, err: str):
        row = self._rows.get(vid)
        if row:
            row.set_state("error")
            row.setToolTip(err)

    # ---- window behaviour
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            if self.settings_drawer.isVisible():
                self.settings_drawer.hide()
                self._sync_window_height()
            elif self.results.isVisible():
                self.search.clear()
                self.clear_results()
            else:
                self.hide_to_tray()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.settings.setValue("pos", self.pos())

    def _restore_position(self):
        pos = self.settings.value("pos")
        screen = QApplication.primaryScreen().availableGeometry()
        if isinstance(pos, QPoint) and screen.adjusted(-50, -50, 50, 50).contains(pos):
            self.move(pos)
        else:
            self.move(screen.center().x() - WIDTH // 2, screen.top() + 60)

    def show_and_focus(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
        self.search.selectAll()

    def toggle(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_and_focus()

    def hide_to_tray(self):
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(
                "Serato YouTube is in the tray",
                f"Press {HOTKEY_LABEL} or click the tray icon to bring the search bar back." if IS_WIN else "Click the menu-bar icon to bring the search bar back.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def closeEvent(self, e):
        if not self._force_quit:
            e.ignore()
            self.hide_to_tray()
        else:
            super().closeEvent(e)

    # ---- tray + hotkey
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip(f"Serato YouTube ({HOTKEY_LABEL})")
        menu = QMenu()
        a_toggle = QAction(f"Show / Hide  ({HOTKEY_LABEL})" if IS_WIN else "Show / Hide", self)
        a_toggle.triggered.connect(self.toggle)
        a_settings = QAction("Settings & Crates…", self)
        a_settings.triggered.connect(self._open_settings_from_tray)
        a_folder = QAction("Open YouTube folder", self)
        a_folder.triggered.connect(
            lambda: open_folder(get_crate_folder(self.active_crate(), self.base_dir()))
        )

        self.a_startup = QAction(STARTUP_LABEL, self)
        self.a_startup.setCheckable(True)
        self.a_startup.setChecked(is_startup_enabled())
        self.a_startup.toggled.connect(self._on_tray_startup_toggled)

        a_quit = QAction("Quit", self)
        a_quit.triggered.connect(self.quit_app)
        menu.addAction(a_toggle)
        menu.addAction(a_settings)
        menu.addAction(a_folder)
        menu.addAction(self.a_startup)
        menu.addSeparator()
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.toggle()
            if r in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
            else None
        )
        self.tray.show()

    def _open_settings_from_tray(self):
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()
        if not self.settings_drawer.isVisible():
            self.toggle_settings_drawer()

    def _on_tray_startup_toggled(self, checked: bool):
        set_startup_enabled(checked)
        self.chk_startup.blockSignals(True)
        self.chk_startup.setChecked(checked)
        self.chk_startup.blockSignals(False)

    def _register_hotkey(self):
        if not IS_WIN:
            # ponytail: no global hotkey on macOS (needs Accessibility); menu-bar icon instead
            self.hotkey_ok = False
            return
        self._hotkey_filter = WinHotkeyFilter(self.toggle, self.show_and_focus)
        QApplication.instance().installNativeEventFilter(self._hotkey_filter)
        self.hotkey_ok = bool(
            ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_Y)
        )

    def quit_app(self):
        self._force_quit = True
        self.settings.setValue("pos", self.pos())
        self.settings.sync()
        try:
            if IS_WIN:
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        except Exception:
            pass
        self.tray.hide()
        QApplication.quit()


if IS_WIN:
    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD),
            ("cntUsage", ctypes.wintypes.DWORD),
            ("th32ProcessID", ctypes.wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.wintypes.DWORD),
            ("cntThreads", ctypes.wintypes.DWORD),
            ("th32ParentProcessID", ctypes.wintypes.DWORD),
            ("pcPriClassBase", ctypes.wintypes.LONG),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]


def kill_other_instances() -> int:
    """
    Terminate any pre-existing SeratoYouTube.exe processes (excluding our own
    PID and our PyInstaller --onefile parent bootloader PID) so a stuck or
    hidden background process can never block the app from launching.
    """
    if not IS_WIN:
        return 0  # a macOS .app is kept single-instance by LaunchServices
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000

    my_pid = os.getpid()
    my_ppid = os.getppid()
    protected = {my_pid, my_ppid}

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.wintypes.HANDLE(-1).value or not snap:
        return 0

    killed_handles = []
    try:
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        has_proc = k32.Process32FirstW(snap, ctypes.byref(pe))
        while has_proc:
            exe_name = pe.szExeFile.lower()
            pid = int(pe.th32ProcessID)
            if exe_name == "seratoyoutube.exe" and pid not in protected:
                h_proc = k32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
                if h_proc:
                    k32.TerminateProcess(h_proc, 0)
                    killed_handles.append(h_proc)
            has_proc = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)

    for h_proc in killed_handles:
        k32.WaitForSingleObject(h_proc, 2000)
        k32.CloseHandle(h_proc)

    if killed_handles:
        import time
        time.sleep(0.2)

    return len(killed_handles)


def _sync_desktop_copy():
    """If the user has a direct copy of SeratoYouTube.exe on their Desktop, keep it updated."""
    try:
        if not getattr(sys, "frozen", False):
            return
        me = Path(sys.executable).resolve()
        for desk in (
            Path.home() / "OneDrive" / "Desktop" / "SeratoYouTube.exe",
            Path.home() / "Desktop" / "SeratoYouTube.exe",
        ):
            if desk.exists() and desk.resolve() != me:
                if abs(desk.stat().st_mtime - me.stat().st_mtime) > 2:
                    import shutil
                    shutil.copy2(me, desk)
    except Exception:
        pass


def already_running() -> bool:
    """
    Single-instance guard (named Win32 mutex).
    Uses ctypes.WinDLL(..., use_last_error=True) and retains the handle in
    module-level _MUTEX_HANDLE so Windows keeps the mutex alive for the life
    of the process.
    """
    global _MUTEX_HANDLE
    if not IS_WIN:
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
    k32.CreateMutexW.restype = ctypes.wintypes.HANDLE

    ctypes.set_last_error(0)
    _MUTEX_HANDLE = k32.CreateMutexW(None, False, "Local\\SeratoYouTubeOverlay")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if WM_SHOW_OVERLAY:
            ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SHOW_OVERLAY, 0, 0)
        return True
    return False


def _background_startup_tasks():
    _sync_desktop_copy()
    try:
        seed_existing_music_tracks()
    except Exception:
        pass


def main():
    start_hidden = "--tray" in sys.argv or "--minimized" in sys.argv
    if not start_hidden:
        # Always clear out any stuck/hidden background instance so launching
        # SeratoYouTube.exe guarantees the window opens and only 1 instance runs.
        kill_other_instances()

    if already_running():
        return

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    win = Overlay()
    if not start_hidden:
        win.show_and_focus()
    if IS_MAC:
        # Clicking the Dock icon while hidden in the menu bar brings it back.
        app.applicationStateChanged.connect(
            lambda st: win.show_and_focus()
            if st == Qt.ApplicationState.ApplicationActive and not win.isVisible()
            else None
        )

    import threading
    threading.Thread(target=_background_startup_tasks, daemon=True).start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
