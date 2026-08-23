from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .version import APP_NAME, GITHUB_REPOSITORY, RELEASE_ID, RELEASE_TAG

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
LOCAL_STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
UPDATE_LOG_FILE = LOCAL_STATE_DIR / "Update-RemoteQBT.log"
UPDATE_RESULT_FILE = CONFIG_DIR / "update-result.json"
log = logging.getLogger(APP_NAME)

RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
UPDATE_STATE_FILE = CONFIG_DIR / "update-state.json"
CHECK_INTERVAL_SECONDS = 12 * 60 * 60

QBT_RELEASE_RE = re.compile(r"^qbt-(\d+)\.(\d+)\.(\d+)-r(\d+)$", re.IGNORECASE)
LEGACY_RELEASE_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class UpdateInfo:
    release_id: str
    qbittorrent_version: str
    revision: int
    tag: str
    title: str
    notes: str
    release_url: str
    asset_name: str
    asset_url: str
    sha256_name: str
    sha256_url: str


def parse_release_tag(value: str) -> tuple[int, int, int, int] | None:
    match = QBT_RELEASE_RE.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(x) for x in match.groups())


def _ordering_key(value: str) -> tuple[int, int, int, int, int] | None:
    text = value.strip()
    qbt = parse_release_tag(text)
    if qbt is not None:
        return (1, *qbt)

    legacy = LEGACY_RELEASE_RE.fullmatch(text)
    if legacy:
        major, minor, patch = (int(x) for x in legacy.groups())
        return (0, major, minor, patch, 0)
    return None


def is_newer(candidate: str, current: str = RELEASE_TAG) -> bool:
    a = _ordering_key(candidate)
    b = _ordering_key(current)
    if a is None or b is None:
        return False
    return a > b


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"RemoteQBT/{RELEASE_TAG}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_json(url: str, timeout: float = 12) -> Any:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(data: dict[str, Any]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        UPDATE_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        log.exception("Could not save update state")


def write_update_result(status: str, release_id: str, message: str) -> None:
    # Persist installer handoff/result state across the app restart.
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        UPDATE_RESULT_FILE.write_text(
            json.dumps(
                {
                    "status": str(status),
                    "release_id": str(release_id),
                    "message": str(message),
                    "time": int(time.time()),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        log.exception("Could not save update result state")


def consume_update_result() -> dict[str, Any] | None:
    # Read and clear the most recent installer result for one-time UI display.
    try:
        data = json.loads(UPDATE_RESULT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        UPDATE_RESULT_FILE.unlink(missing_ok=True)
    except Exception:
        log.exception("Could not clear update result state")
    return data if isinstance(data, dict) else None


def check_for_update(*, force: bool = False) -> UpdateInfo | None:
    state = _read_state()
    now = int(time.time())
    if not force and now - int(state.get("last_check", 0) or 0) < CHECK_INTERVAL_SECONDS:
        cached = state.get("latest") or {}
        if cached and is_newer(str(cached.get("tag", ""))):
            try:
                return UpdateInfo(**cached)
            except TypeError:
                return None
        return None

    try:
        release = _get_json(RELEASES_API)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.info("No RemoteQBT GitHub release exists yet")
            _write_state({**state, "last_check": now})
            return None
        raise RuntimeError(f"GitHub update check failed: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach GitHub for update check: {e.reason}") from e

    if bool(release.get("draft")) or bool(release.get("prerelease")):
        _write_state({**state, "last_check": now})
        return None

    tag = str(release.get("tag_name", "")).strip()
    parsed = parse_release_tag(tag)
    if parsed is None:
        _write_state({**state, "last_check": now})
        return None

    major, minor, patch, revision = parsed
    qbt_version = f"{major}.{minor}.{patch}"
    release_id = f"{qbt_version}-r{revision}"

    assets = list(release.get("assets") or [])
    wanted_suffix = "-Windows.zip"
    asset = next((a for a in assets if str(a.get("name", "")).endswith(wanted_suffix)), None)
    sha = None
    if asset:
        sha_name = str(asset.get("name", "")) + ".sha256"
        sha = next((a for a in assets if str(a.get("name", "")) == sha_name), None)

    if not asset or not sha:
        _write_state({**state, "last_check": now})
        return None

    info = UpdateInfo(
        release_id=release_id,
        qbittorrent_version=qbt_version,
        revision=revision,
        tag=tag,
        title=str(release.get("name") or tag),
        notes=str(release.get("body") or ""),
        release_url=str(release.get("html_url") or RELEASES_PAGE),
        asset_name=str(asset["name"]),
        asset_url=str(asset["browser_download_url"]),
        sha256_name=str(sha["name"]),
        sha256_url=str(sha["browser_download_url"]),
    )
    _write_state({"last_check": now, "latest": info.__dict__})
    return info if is_newer(info.tag) else None


def _download(url: str, destination: Path, timeout: float = 90) -> None:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as response, destination.open("wb") as f:
        shutil.copyfileobj(response, f, length=1024 * 1024)


def download_update(info: UpdateInfo) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"RemoteQBT-{info.release_id}-"))
    archive = root / info.asset_name
    digest_file = root / info.sha256_name
    _download(info.asset_url, archive)
    _download(info.sha256_url, digest_file)

    expected = digest_file.read_text(encoding="utf-8", errors="replace").strip().split()[0].lower()
    actual = hashlib.sha256(archive.read_bytes()).hexdigest().lower()
    if not expected or actual != expected:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError("RemoteQBT update failed SHA-256 verification.")

    extracted = root / "package"
    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        base = extracted.resolve()
        for member in zf.infolist():
            target = (extracted / member.filename).resolve()
            if target != base and base not in target.parents:
                raise RuntimeError("Unsafe path found in update archive.")
        zf.extractall(extracted)

    scripts = list(extracted.rglob("Update-RemoteQBT.ps1"))
    if len(scripts) != 1:
        raise RuntimeError("Update package does not contain exactly one updater script.")
    return scripts[0]


def launch_installer(script: Path, release_id: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Automatic installation is only supported on Windows.")
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not shell:
        raise RuntimeError("PowerShell was not found.")

    package_root = script.parent
    UPDATE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_update_result("launching", release_id, "Starting the Windows updater.")

    cmd = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script),
        "-PackageRoot", str(package_root),
        "-ParentPid", str(os.getpid()),
        "-ReleaseId", str(release_id),
        "-Relaunch",
    ]

    with UPDATE_LOG_FILE.open("a", encoding="utf-8") as output:
        output.write(f"\n=== Python updater handoff: {release_id} ===\n")
        output.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(package_root),
            stdout=output,
            stderr=subprocess.STDOUT,
            # CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP.
            creationflags=0x08000000 | 0x00000200,
            close_fds=True,
        )

    # Catch parser/startup failures while the old app is still alive.
    time.sleep(0.30)
    exit_code = proc.poll()
    if exit_code is not None:
        write_update_result("failed", release_id, f"Updater exited immediately with code {exit_code}.")
        raise RuntimeError(
            f"The Windows updater exited immediately with code {exit_code}.\n"
            f"See: {UPDATE_LOG_FILE}"
        )
    log.info("Windows updater launched: pid=%s release=%s", proc.pid, release_id)
