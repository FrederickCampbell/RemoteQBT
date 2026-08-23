from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
META_PATH = ROOT / "upstream" / "qbittorrent.json"
REPORT_PATH = ROOT / "upstream" / "compatibility-report.md"
UPSTREAM_REPO = "qbittorrent/qBittorrent"
LATEST_RELEASE_API = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest"
RAW_BASE = "https://raw.githubusercontent.com/qbittorrent/qBittorrent"

CONTROLLERS = {
    "app": "src/webui/api/appcontroller.h",
    "transfer": "src/webui/api/transfercontroller.h",
    "sync": "src/webui/api/synccontroller.h",
    "torrents": "src/webui/api/torrentscontroller.h",
}
UI_FILES = {
    "mainwindow.ui": "src/gui/mainwindow.ui",
    "addnewtorrentdialog.ui": "src/gui/addnewtorrentdialog.ui",
    "propertieswidget.ui": "src/gui/properties/propertieswidget.ui",
}
ICONS = [
    "configure.svg", "folder-remote.svg", "force-recheck.svg", "go-bottom.svg",
    "go-down.svg", "go-top.svg", "go-up.svg", "insert-link.svg", "list-add.svg",
    "list-remove.svg", "pause-session.svg", "qbittorrent-tray.svg", "reannounce.svg",
    "set-location.svg", "slow.svg", "torrent-start.svg", "torrent-stop.svg",
]

# These are the actions RemoteQBT actively calls. Losing any one of them is a
# compatibility break. Additions are not blindly exposed: they are reported for
# a UI/semantics review so new qBittorrent features can be added intentionally.
REQUIRED = {
    "app": {
        "version", "webapiVersion", "preferences", "setPreferences",
        "defaultSavePath", "getDirectoryContent", "shutdown",
    },
    "transfer": {
        "info", "speedLimitsMode", "setSpeedLimitsMode", "uploadLimit",
        "downloadLimit", "setUploadLimit", "setDownloadLimit", "banPeers",
    },
    "sync": {"maindata", "torrentPeers"},
    "torrents": {
        "info", "properties", "trackers", "webseeds", "files", "add", "delete",
        "start", "stop", "recheck", "reannounce", "rename", "setComment",
        "setCategory", "createCategory", "editCategory", "removeCategories",
        "createTags", "deleteTags", "addTags", "setTags", "removeTags",
        "addTrackers", "editTracker", "removeTrackers", "addWebSeeds",
        "editWebSeed", "removeWebSeeds", "addPeers", "filePrio", "setUploadLimit",
        "setDownloadLimit", "setShareLimits", "increasePrio", "decreasePrio",
        "topPrio", "bottomPrio", "setLocation", "setSavePath", "setDownloadPath",
        "setAutoManagement", "setSuperSeeding", "setForceStart",
        "toggleSequentialDownload", "toggleFirstLastPiecePrio", "renameFile",
        "renameFolder", "export",
    },
}


def headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RemoteQBT-Upstream-Watch",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch(url: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, headers=headers())
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str) -> Any:
    return json.loads(fetch(url).decode("utf-8", errors="replace"))


def fetch_raw(ref: str, path: str) -> bytes:
    return fetch(f"{RAW_BASE}/{ref}/{path}")


def parse_actions(text: str) -> list[str]:
    return sorted(set(re.findall(r"\bvoid\s+([A-Za-z0-9_]+)Action\s*\(", text)))


def parse_api_version(text: str) -> str:
    match = re.search(r"API_VERSION\s*\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\}", text)
    return ".".join(match.groups()) if match else "unknown"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def set_output(name: str, value: str | bool | int) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    text = str(value).lower() if isinstance(value, bool) else str(value)
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={text}\n")
    print(f"{name}={text}")


def version_from_tag(tag: str) -> str:
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", tag)
    return m.group(1) if m else tag


def load_meta() -> dict[str, Any]:
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def baseline_ui_hashes(meta: dict[str, Any]) -> dict[str, str]:
    hashes = dict(meta.get("ui_hashes") or {})
    if hashes:
        return hashes
    baseline = str(meta.get("last_seen_tag") or "")
    if not baseline:
        raise RuntimeError("upstream/qbittorrent.json does not define last_seen_tag")
    for name, path in UI_FILES.items():
        try:
            hashes[name] = sha256(fetch_raw(baseline, path))
        except Exception:
            hashes[name] = ""
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit latest qBittorrent release against RemoteQBT")
    parser.add_argument("--force", action="store_true", help="audit even if tag is unchanged")
    args = parser.parse_args()

    meta = load_meta()
    release = fetch_json(LATEST_RELEASE_API)
    tag = str(release.get("tag_name") or "").strip()
    if not tag:
        raise SystemExit("qBittorrent latest release did not contain a tag_name")

    previous_tag = str(meta.get("last_seen_tag") or "")
    if tag == previous_tag and not args.force:
        set_output("changed", False)
        set_output("tag", tag)
        set_output("safe", True)
        return 0

    old_controllers = {k: set(v) for k, v in (meta.get("controllers") or {}).items()}
    new_controllers: dict[str, list[str]] = {}
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    missing_required: dict[str, list[str]] = {}

    for scope, path in CONTROLLERS.items():
        text = fetch_raw(tag, path).decode("utf-8", errors="replace")
        actions = parse_actions(text)
        new_controllers[scope] = actions
        old = old_controllers.get(scope, set())
        now = set(actions)
        added[scope] = sorted(now - old)
        removed[scope] = sorted(old - now)
        missing_required[scope] = sorted(REQUIRED.get(scope, set()) - now)

    webapp = fetch_raw(tag, "src/webui/webapplication.h").decode("utf-8", errors="replace")
    api_version = parse_api_version(webapp)
    old_api_version = str(meta.get("webapi_version") or "unknown")
    api_changed = api_version != old_api_version

    old_ui_hashes = baseline_ui_hashes(meta)
    new_ui_hashes: dict[str, str] = {}
    changed_ui: list[str] = []
    for name, path in UI_FILES.items():
        data = fetch_raw(tag, path)
        new_ui_hashes[name] = sha256(data)
        if old_ui_hashes.get(name) and old_ui_hashes.get(name) != new_ui_hashes[name]:
            changed_ui.append(name)
        dest = ROOT / "upstream" / "ui" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    changed_icons: list[str] = []
    for name in ICONS:
        try:
            data = fetch_raw(tag, f"src/icons/{name}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        dest = ROOT / "assets" / "qbt" / name
        old_hash = sha256(dest.read_bytes()) if dest.exists() else ""
        new_hash = sha256(data)
        if old_hash != new_hash:
            changed_icons.append(name)
            dest.write_bytes(data)

    any_added = any(added.values())
    any_removed = any(removed.values())
    any_missing = any(missing_required.values())
    # Safe updates are intentionally conservative: icon-only/internal patch
    # changes can flow automatically. A new/removed API action, Web API version
    # change, or upstream desktop UI change gets a human/code-agent review.
    safe = not any_missing and not any_added and not any_removed and not api_changed and not changed_ui

    new_meta = {
        "repository": UPSTREAM_REPO,
        "last_seen_tag": tag,
        "webapi_version": api_version,
        "ui_hashes": new_ui_hashes,
        "controllers": new_controllers,
    }
    META_PATH.write_text(json.dumps(new_meta, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# qBittorrent upstream compatibility report",
        "",
        f"- Previous baseline: `{previous_tag or 'unknown'}`",
        f"- Latest release: `{tag}`",
        f"- Web API: `{old_api_version}` → `{api_version}`",
        f"- Classification: **{'safe automatic sync' if safe else 'review required'}**",
        "",
        "## API surface diff",
        "",
    ]
    for scope in ("app", "transfer", "sync", "torrents"):
        lines.append(f"### {scope}")
        lines.append(f"- Added: {', '.join('`'+x+'`' for x in added[scope]) if added[scope] else 'none'}")
        lines.append(f"- Removed: {', '.join('`'+x+'`' for x in removed[scope]) if removed[scope] else 'none'}")
        lines.append(f"- Required but missing: {', '.join('`'+x+'`' for x in missing_required[scope]) if missing_required[scope] else 'none'}")
        lines.append("")
    lines += [
        "## Upstream desktop UI",
        "",
        f"- Changed `.ui` files: {', '.join('`'+x+'`' for x in changed_ui) if changed_ui else 'none'}",
        f"- Synced qBittorrent icon assets: {', '.join('`'+x+'`' for x in changed_icons) if changed_icons else 'none'}",
        "",
        "## Automation decision",
        "",
    ]
    if safe:
        lines.append("No remotely-visible API or desktop UI surface changed. The bot may align RemoteQBT to this qBittorrent release as revision r1 and publish a fresh Windows build automatically.")
    else:
        lines.append("The bot will not blindly invent UI behavior. It will open/update an upstream review PR (and issue) containing this diff so the changed/new qBittorrent surface can be implemented intentionally, then normal CI/release automation takes over.")
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    set_output("changed", True)
    set_output("safe", safe)
    set_output("tag", tag)
    set_output("qbittorrent_version", version_from_tag(tag))
    set_output("api_changed", api_changed)
    set_output("ui_changed", bool(changed_ui))
    set_output("added_count", sum(len(v) for v in added.values()))
    set_output("removed_count", sum(len(v) for v in removed.values()))
    set_output("missing_required_count", sum(len(v) for v in missing_required.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
