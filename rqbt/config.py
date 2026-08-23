from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import shutil
import subprocess
from typing import Any

from .common import (
    CONFIG_DIR, CONFIG_FILE, DEFAULT_REFRESH_MS, DEFAULT_SAVE_PATH,
    DEFAULT_SERVER, DEFAULT_SMB_PATH,
)

log = logging.getLogger("RemoteQBT")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob_from_bytes(data: bytes):
    buf = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buf


def dpapi_encrypt(text: str) -> str:
    if os.name != "nt":
        return "plain:" + base64.b64encode(text.encode("utf-8")).decode("ascii")
    in_blob, _buf = _blob_from_bytes(text.encode("utf-8"))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), "RemoteQBT API key", None, None, None, 0,
        ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_decrypt(encoded: str) -> str:
    if encoded.startswith("plain:"):
        return base64.b64decode(encoded[6:]).decode("utf-8")
    if os.name != "nt":
        raise RuntimeError("This API key was encrypted on Windows.")
    raw = base64.b64decode(encoded)
    in_blob, _buf = _blob_from_bytes(raw)
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _decrypt_legacy_powershell_securestring(enc: str) -> str | None:
    if os.name != "nt" or not enc:
        return None
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not shell:
        return None
    env = os.environ.copy()
    env["REMOTEQBT_LEGACY_ENC"] = enc
    cmd = (
        "$s=ConvertTo-SecureString $env:REMOTEQBT_LEGACY_ENC;"
        "$n=[System.Net.NetworkCredential]::new('', $s);"
        "[Console]::Out.Write($n.Password)"
    )
    try:
        cp = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", cmd],
            env=env, capture_output=True, text=True, timeout=8,
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        if cp.returncode == 0 and cp.stdout:
            return cp.stdout
    except Exception:
        log.exception("Legacy config migration failed")
    return None


def default_config() -> dict[str, Any]:
    return {
        "server": DEFAULT_SERVER,
        "save_path": DEFAULT_SAVE_PATH,
        "smb_path": DEFAULT_SMB_PATH,
        "refresh_ms": DEFAULT_REFRESH_MS,
        "api_key": "",
        "live_sorting": False,
        "show_properties": True,
        "show_sidebar": True,
        "integrate_windows": True,
    }


def load_config() -> dict[str, Any]:
    cfg = default_config()
    if not CONFIG_FILE.exists():
        return cfg
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if "api_key_dpapi" in raw:
            cfg.update({k: v for k, v in raw.items() if k != "api_key_dpapi"})
            try:
                cfg["api_key"] = dpapi_decrypt(raw.get("api_key_dpapi", ""))
            except Exception:
                log.exception("Could not decrypt API key")
                cfg["api_key"] = ""
            return cfg

        # Legacy PowerShell SecureString config migration.
        if "ApiKeyEncrypted" in raw:
            key = _decrypt_legacy_powershell_securestring(raw.get("ApiKeyEncrypted", ""))
            cfg.update({
                "server": raw.get("Server", DEFAULT_SERVER),
                "save_path": raw.get("SavePath", DEFAULT_SAVE_PATH),
                "smb_path": DEFAULT_SMB_PATH,
                "refresh_ms": DEFAULT_REFRESH_MS,
                "api_key": key or "",
            })
            if key:
                save_config(cfg)
            return cfg
    except Exception:
        log.exception("Failed loading config")
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "server": str(cfg.get("server", DEFAULT_SERVER)).rstrip("/"),
        "save_path": str(cfg.get("save_path", DEFAULT_SAVE_PATH)),
        "smb_path": str(cfg.get("smb_path", DEFAULT_SMB_PATH)),
        "refresh_ms": int(cfg.get("refresh_ms", DEFAULT_REFRESH_MS)),
        "live_sorting": bool(cfg.get("live_sorting", False)),
        "show_properties": bool(cfg.get("show_properties", True)),
        "show_sidebar": bool(cfg.get("show_sidebar", True)),
        "integrate_windows": bool(cfg.get("integrate_windows", True)),
        "api_key_dpapi": dpapi_encrypt(str(cfg.get("api_key", ""))),
    }
    CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
