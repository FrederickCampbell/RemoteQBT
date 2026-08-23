from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "rqbt" / "version.py"
UPSTREAM_FILE = ROOT / "upstream" / "qbittorrent.json"
MANIFEST_FILE = ROOT / "BUILD-MANIFEST.json"
README_FILE = ROOT / "README.md"
AUTOMATION_FILE = ROOT / "AUTOMATION.md"


def parse_upstream_tag(tag: str) -> str:
    match = re.fullmatch(r"release-(\d+\.\d+\.\d+)", tag.strip())
    if not match:
        raise ValueError(f"Unsupported qBittorrent release tag: {tag!r}")
    return match.group(1)


def make_release_id(qbt_version: str, revision: int) -> str:
    if revision < 1:
        raise ValueError("RemoteQBT revision must be >= 1")
    return f"{qbt_version}-r{revision}"


def make_release_tag(qbt_version: str, revision: int) -> str:
    return f"qbt-{make_release_id(qbt_version, revision)}"


def read_current() -> tuple[str, int]:
    text = VERSION_FILE.read_text(encoding="utf-8")
    qbt = re.search(r'^QBITTORRENT_VERSION\s*=\s*"([^"]+)"', text, re.M)
    rev = re.search(r"^REMOTEQBT_REVISION\s*=\s*(\d+)", text, re.M)
    if not qbt or not rev:
        raise RuntimeError("Could not read qBittorrent-aligned release identity")
    return qbt.group(1), int(rev.group(1))


def read_upstream() -> tuple[str, str, str]:
    meta = json.loads(UPSTREAM_FILE.read_text(encoding="utf-8"))
    upstream_tag = str(meta.get("last_seen_tag") or "")
    qbt_version = parse_upstream_tag(upstream_tag)
    webapi = str(meta.get("webapi_version") or "unknown")
    return qbt_version, upstream_tag, webapi


def _update_docs(qbt_version: str, release_id: str, release_tag: str, upstream_tag: str, webapi: str) -> None:
    if README_FILE.exists():
        text = README_FILE.read_text(encoding="utf-8")
        text = re.sub(r"^# RemoteQBT for qBittorrent .+$", f"# RemoteQBT for qBittorrent {qbt_version}", text, count=1, flags=re.M)
        text = re.sub(r"^\*\*Release identity:\*\* .+$", f"**Release identity:** `{release_id}` · Git tag `{release_tag}`", text, count=1, flags=re.M)
        text = re.sub(
            r"^Current audited upstream baseline:.*$",
            f"Current audited upstream baseline: qBittorrent **{qbt_version}** (`{upstream_tag}`), Web API **{webapi}**.",
            text,
            count=1,
            flags=re.M,
        )
        README_FILE.write_text(text, encoding="utf-8")

    if AUTOMATION_FILE.exists():
        text = AUTOMATION_FILE.read_text(encoding="utf-8")
        replacement = f"**Current compatibility baseline:** qBittorrent `{qbt_version}` / Web API `{webapi}`."
        if re.search(r"^\*\*Current compatibility baseline:\*\*.*$", text, re.M):
            text = re.sub(r"^\*\*Current compatibility baseline:\*\*.*$", replacement, text, count=1, flags=re.M)
        elif re.search(r"^Current migration baseline:.*$", text, re.M):
            text = re.sub(r"^Current migration baseline:.*$", replacement, text, count=1, flags=re.M)
        else:
            text = text.replace(
                "The single source of shipped identity is `rqbt/version.py`.",
                "The single source of shipped identity is `rqbt/version.py`.\n\n" + replacement,
                1,
            )
        AUTOMATION_FILE.write_text(text, encoding="utf-8")


def write_identity(qbt_version: str, revision: int) -> str:
    release_id = make_release_id(qbt_version, revision)
    release_tag = make_release_tag(qbt_version, revision)

    VERSION_FILE.write_text(
        'APP_NAME = "RemoteQBT"\n'
        f'QBITTORRENT_VERSION = "{qbt_version}"\n'
        f"REMOTEQBT_REVISION = {revision}\n"
        'GITHUB_REPOSITORY = "FrederickCampbell/RemoteQBT"\n\n'
        'RELEASE_ID = f"{QBITTORRENT_VERSION}-r{REMOTEQBT_REVISION}"\n'
        'RELEASE_TAG = f"qbt-{RELEASE_ID}"\n'
        'DISPLAY_VERSION = f"for qBittorrent {QBITTORRENT_VERSION} (r{REMOTEQBT_REVISION})"\n',
        encoding="utf-8",
    )

    upstream_qbt, upstream_tag, webapi = read_upstream()
    if upstream_qbt != qbt_version:
        upstream_tag = f"release-{qbt_version}"
        webapi = "review-pending"

    manifest = {
        "name": "RemoteQBT",
        "release_id": release_id,
        "release_tag": release_tag,
        "qbittorrent_compatibility": {
            "release": qbt_version,
            "upstream_tag": upstream_tag,
            "webapi_version": webapi,
        },
        "remoteqbt_revision": revision,
        "target": f"Windows / qBittorrent {qbt_version} compatibility baseline",
        "validated": {
            "python_syntax": True,
            "private_api_key_embedded": False,
            "private_tracker_url_embedded": False,
            "package_mode": "PyInstaller windowed onedir EXE",
        },
        "github_repository": "FrederickCampbell/RemoteQBT",
        "self_update": True,
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _update_docs(qbt_version, release_id, release_tag, upstream_tag, webapi)
    return release_tag


def sync_to_upstream() -> str:
    current_qbt, current_revision = read_current()
    upstream_qbt, _tag, _webapi = read_upstream()
    if current_qbt != upstream_qbt:
        return write_identity(upstream_qbt, 1)
    return write_identity(current_qbt, current_revision + 1)


def check_consistency() -> None:
    qbt_version, revision = read_current()
    expected_id = make_release_id(qbt_version, revision)
    expected_tag = make_release_tag(qbt_version, revision)

    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("release_id") != expected_id:
        errors.append("BUILD-MANIFEST release_id does not match rqbt/version.py")
    if manifest.get("release_tag") != expected_tag:
        errors.append("BUILD-MANIFEST release_tag does not match rqbt/version.py")
    if manifest.get("remoteqbt_revision") != revision:
        errors.append("BUILD-MANIFEST remoteqbt_revision does not match rqbt/version.py")

    compat = manifest.get("qbittorrent_compatibility") or {}
    if compat.get("release") != qbt_version:
        errors.append("BUILD-MANIFEST qBittorrent release does not match rqbt/version.py")

    compat_tag = str(compat.get("upstream_tag") or f"release-{qbt_version}")
    compat_api = str(compat.get("webapi_version") or "unknown")
    baseline = f"qBittorrent **{qbt_version}** (`{compat_tag}`), Web API **{compat_api}**."

    readme = README_FILE.read_text(encoding="utf-8")
    if not readme.startswith(f"# RemoteQBT for qBittorrent {qbt_version}\n"):
        errors.append("README heading does not match rqbt/version.py")
    if f"`{expected_id}`" not in readme or f"`{expected_tag}`" not in readme:
        errors.append("README release identity does not match rqbt/version.py")
    if baseline not in readme:
        errors.append("README compatibility baseline is stale")

    automation = AUTOMATION_FILE.read_text(encoding="utf-8")
    auto_baseline = f"**Current compatibility baseline:** qBittorrent `{qbt_version}` / Web API `{compat_api}`."
    if auto_baseline not in automation:
        errors.append("AUTOMATION.md compatibility baseline is stale")

    if errors:
        raise SystemExit("\n".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain qBittorrent-aligned RemoteQBT release identity")
    parser.add_argument("command", choices=["sync", "increment", "check", "show"])
    args = parser.parse_args()

    if args.command == "sync":
        print(sync_to_upstream())
    elif args.command == "increment":
        qbt_version, revision = read_current()
        print(write_identity(qbt_version, revision + 1))
    elif args.command == "check":
        check_consistency()
        qbt_version, revision = read_current()
        print(make_release_tag(qbt_version, revision))
    else:
        qbt_version, revision = read_current()
        print(make_release_tag(qbt_version, revision))


if __name__ == "__main__":
    main()
