from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .common import ASSOC_BACKUP_FILE, CONFIG_DIR


def executable_path() -> str:
    return str(Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve())


def _read_default(winreg, root, path: str):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None


def _write_default(winreg, root, path: str, value: str):
    with winreg.CreateKey(root, path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)


def _delete_tree(winreg, root, path: str):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                    _delete_tree(winreg, root, path + "\\" + child)
                except OSError:
                    break
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def register_associations() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Windows integration is only available on Windows."
    import winreg

    exe = executable_path()
    command = f'"{exe}" "%1"'
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Preserve class defaults we are about to replace. Windows UserChoice can
    # still take precedence; in that case RemoteQBT is registered as an option
    # and Settings > Default Apps can be used to select it.
    backup: dict[str, Any] = {}
    for name, path in {
        "torrent": r"Software\Classes\.torrent",
        "magnet": r"Software\Classes\magnet",
    }.items():
        backup[name] = _read_default(winreg, winreg.HKEY_CURRENT_USER, path)
    if not ASSOC_BACKUP_FILE.exists():
        ASSOC_BACKUP_FILE.write_text(json.dumps(backup, indent=2), encoding="utf-8")

    # Program IDs
    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.torrent", "BitTorrent file")
    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.torrent\DefaultIcon", exe + ",0")
    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.torrent\shell\open\command", command)

    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.magnet", "Magnet URI")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.magnet") as key:
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.magnet\DefaultIcon", exe + ",0")
    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.magnet\shell\open\command", command)

    # Register capabilities so Windows lists RemoteQBT in Default Apps.
    cap = r"Software\RemoteQBT\Capabilities"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cap) as key:
        winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, "RemoteQBT")
        winreg.SetValueEx(key, "ApplicationDescription", 0, winreg.REG_SZ, "Remote qBittorrent client")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cap + r"\FileAssociations") as key:
        winreg.SetValueEx(key, ".torrent", 0, winreg.REG_SZ, "RemoteQBT.torrent")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cap + r"\URLAssociations") as key:
        winreg.SetValueEx(key, "magnet", 0, winreg.REG_SZ, "RemoteQBT.magnet")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications") as key:
        winreg.SetValueEx(key, "RemoteQBT", 0, winreg.REG_SZ, cap)

    # Best-effort defaults when no UserChoice policy blocks registry defaults.
    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\.torrent", "RemoteQBT.torrent")
    _write_default(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\magnet", "RemoteQBT.magnet")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\magnet") as key:
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass
    return True, "RemoteQBT registered for magnet links and .torrent files."


def unregister_associations() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Windows integration is only available on Windows."
    import winreg

    backup = {}
    if ASSOC_BACKUP_FILE.exists():
        try:
            backup = json.loads(ASSOC_BACKUP_FILE.read_text(encoding="utf-8"))
        except Exception:
            backup = {}

    # Restore previous class defaults only if RemoteQBT currently owns them.
    for name, path in {
        "torrent": r"Software\Classes\.torrent",
        "magnet": r"Software\Classes\magnet",
    }.items():
        current = _read_default(winreg, winreg.HKEY_CURRENT_USER, path)
        expected = "RemoteQBT.torrent" if name == "torrent" else "RemoteQBT.magnet"
        if current == expected:
            prior = backup.get(name)
            if prior is None:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, "")
                except OSError:
                    pass
            else:
                _write_default(winreg, winreg.HKEY_CURRENT_USER, path, str(prior))

    _delete_tree(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.torrent")
    _delete_tree(winreg, winreg.HKEY_CURRENT_USER, r"Software\Classes\RemoteQBT.magnet")
    _delete_tree(winreg, winreg.HKEY_CURRENT_USER, r"Software\RemoteQBT\Capabilities")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "RemoteQBT")
    except OSError:
        pass
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass
    return True, "RemoteQBT Windows associations removed/restored."


def open_default_apps():
    if os.name == "nt":
        os.startfile("ms-settings:defaultapps")
