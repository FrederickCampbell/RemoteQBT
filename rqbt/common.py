from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon

from .version import (
    APP_NAME, DISPLAY_VERSION, QBITTORRENT_VERSION, RELEASE_ID, RELEASE_TAG,
    REMOTEQBT_REVISION,
)

ORG_NAME = APP_NAME

DEFAULT_SERVER = "http://localhost:8080"
DEFAULT_SAVE_PATH = ""
DEFAULT_SMB_PATH = ""
DEFAULT_REFRESH_MS = 1500

APPDATA = Path(os.environ.get("APPDATA", Path.home()))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home()))
CONFIG_DIR = APPDATA / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = LOCALAPPDATA / APP_NAME
LOG_FILE = LOG_DIR / "RemoteQBT.log"
ASSOC_BACKUP_FILE = CONFIG_DIR / "association-backup.json"

# qBittorrent's icon palette uses these prominently. The application layout and
# action artwork are taken from upstream qBittorrent; this stylesheet only makes
# Qt's Windows widgets coherent in dark mode.
C_BG = "#202020"
C_PANEL = "#252525"
C_PANEL_2 = "#2B2B2B"
C_PANEL_3 = "#333333"
C_BORDER = "#484848"
C_BLUE = "#1E90FF"
C_BLUE_2 = "#3F9FFF"
C_BLUE_3 = "#72B4F5"
C_TEXT = "#EAEAEA"
C_MUTED = "#A7A7A7"
C_GREEN = "#32CD32"
C_RED = "#FF4D4D"
C_ORANGE = "#FF8C00"
C_CYAN = "#59C3C3"

DARK_STYLE = f"""
QMainWindow, QWidget {{
    background: {C_BG};
    color: {C_TEXT};
    font-family: "Segoe UI";
    font-size: 9.5pt;
}}
QMenuBar {{ background: {C_PANEL}; color: {C_TEXT}; border-bottom: 1px solid {C_BORDER}; }}
QMenuBar::item {{ padding: 5px 9px; background: transparent; }}
QMenuBar::item:selected {{ background: #3A3A3A; }}
QMenu {{ background: {C_PANEL}; color: {C_TEXT}; border: 1px solid {C_BORDER}; }}
QMenu::item {{ padding: 6px 28px 6px 28px; }}
QMenu::item:selected {{ background: #315B86; }}
QMenu::separator {{ height: 1px; background: #444; margin: 4px 8px; }}
QToolBar {{ background: {C_PANEL}; border: none; border-bottom: 1px solid {C_BORDER}; spacing: 2px; padding: 3px 5px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 4px; }}
QToolButton:hover {{ background: #373737; border-color: #505050; }}
QToolButton:pressed {{ background: #292929; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #191919; color: {C_TEXT}; border: 1px solid {C_BORDER}; border-radius: 2px; padding: 5px;
    selection-background-color: #315B86;
}}
QTreeWidget, QTableWidget, QTableView, QListWidget {{
    background: {C_BG}; alternate-background-color: #242424; color: {C_TEXT};
    border: 1px solid {C_BORDER}; gridline-color: #363636;
    selection-background-color: #315B86; selection-color: white;
}}
QTreeWidget::item, QListWidget::item {{ min-height: 23px; }}
QHeaderView::section {{
    background: #303030; color: #E5E5E5; border: none; border-right: 1px solid #454545;
    border-bottom: 1px solid #454545; padding: 5px 6px;
}}
QTabWidget::pane {{ border: 1px solid {C_BORDER}; background: {C_BG}; }}
QTabBar::tab {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-bottom: none; padding: 6px 13px; margin-right: 1px; }}
QTabBar::tab:selected {{ background: #343434; color: white; }}
QPushButton {{ background: #333333; color: {C_TEXT}; border: 1px solid #505050; border-radius: 3px; padding: 6px 12px; }}
QPushButton:hover {{ background: #3C3C3C; border-color: #686868; }}
QPushButton#Primary {{ background: #2467A8; border-color: #3986CF; font-weight: 600; }}
QPushButton#Danger {{ color: #FFB1B1; }}
QStatusBar {{ background: {C_PANEL}; color: {C_TEXT}; border-top: 1px solid {C_BORDER}; }}
QSplitter::handle {{ background: #3A3A3A; }}
QScrollBar:vertical {{ background: #1D1D1D; width: 12px; }}
QScrollBar::handle:vertical {{ background: #555; min-height: 24px; }}
QScrollBar:horizontal {{ background: #1D1D1D; height: 12px; }}
QScrollBar::handle:horizontal {{ background: #555; min-width: 24px; }}
QProgressBar {{ border: 1px solid #4A4A4A; border-radius: 2px; text-align: center; background: #171717; color: white; }}
QProgressBar::chunk {{ background: #2B7CC4; }}
QGroupBox {{ border: 1px solid {C_BORDER}; margin-top: 8px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QToolTip {{ background: #2D2D2D; color: white; border: 1px solid #555; }}
"""


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def app_asset(name: str) -> str:
    return str(resource_root() / "assets" / "qbt" / name)


def qbt_icon(name: str | None) -> QIcon:
    if not name:
        return QIcon()
    path = Path(app_asset(name))
    return QIcon(str(path)) if path.exists() else QIcon()


def make_app_icon() -> QIcon:
    icon = qbt_icon("qbittorrent.ico")
    if icon.isNull():
        icon = qbt_icon("qbittorrent-tray.svg")
    return icon


def human_bytes(n: float | int | None) -> str:
    if n is None or float(n) < 0:
        return "—"
    value = float(n)
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{value:.0f} {units[idx]}"
    if idx <= 2:
        return f"{value:.1f} {units[idx]}"
    return f"{value:.2f} {units[idx]}"


def human_speed(n: float | int | None) -> str:
    if not n:
        return "0 B/s"
    return human_bytes(n) + "/s"


def human_eta(sec: int | float | None) -> str:
    if sec is None or float(sec) < 0 or float(sec) >= 8_640_000:
        return "∞"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    minutes, seconds = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def format_timestamp(ts: int | float | None) -> str:
    try:
        if not ts or float(ts) < 0:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return ""


def state_label(state: str) -> str:
    s = (state or "").lower()
    if "error" in s or "missing" in s:
        return "Errored"
    if "checking" in s:
        return "Checking"
    if s in {"forceddl"}:
        return "Downloading [F]"
    if s in {"forcedup"}:
        return "Seeding [F]"
    if "stalleddl" in s:
        return "Stalled"
    if "stalledup" in s:
        return "Stalled uploading"
    if "queueddl" in s:
        return "Queued"
    if "queuedup" in s:
        return "Queued upload"
    if "downloading" in s or "metadl" in s:
        return "Downloading"
    if "upload" in s or s.endswith("up"):
        return "Seeding"
    if "paused" in s or "stopped" in s:
        return "Stopped"
    if "moving" in s:
        return "Moving"
    if "allocating" in s:
        return "Allocating"
    return state or "Unknown"


def state_color(state: str) -> QColor:
    s = (state or "").lower()
    if "error" in s or "missing" in s:
        return QColor(C_RED)
    if "paused" in s or "stopped" in s:
        return QColor(C_MUTED)
    if "down" in s or "metadl" in s:
        return QColor(C_BLUE_3)
    if "up" in s:
        return QColor(C_GREEN)
    if "checking" in s or "queued" in s:
        return QColor(C_CYAN)
    if "stalled" in s:
        return QColor(C_ORANGE)
    return QColor(C_TEXT)


def to_bool_text(v: bool) -> str:
    return "true" if bool(v) else "false"


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}
