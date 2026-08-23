from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable

from .common import RELEASE_TAG, to_bool_text


class QbtApiError(RuntimeError):
    pass


class QbtClient:
    def __init__(self, cfg: dict[str, Any]):
        self.server = str(cfg.get("server", "")).rstrip("/")
        self.key = str(cfg.get("api_key", ""))
        self.save_path = str(cfg.get("save_path", ""))
        self.user_agent = f"RemoteQBT/{RELEASE_TAG}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key}",
            "User-Agent": self.user_agent,
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15,
    ) -> bytes:
        if not self.key:
            raise QbtApiError("No qBittorrent API key configured.")
        h = self._headers()
        if headers:
            h.update(headers)
        req = urllib.request.Request(self.server + endpoint, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace").strip()
            if e.code == 403:
                raise QbtApiError("Forbidden (403): check the qBittorrent API key.")
            raise QbtApiError(f"HTTP {e.code}: {body or e.reason}")
        except urllib.error.URLError as e:
            raise QbtApiError(f"Cannot reach {self.server}: {e.reason}")
        except TimeoutError:
            raise QbtApiError(f"Timed out connecting to {self.server}.")

    def _get_json(self, endpoint: str) -> Any:
        raw = self._request("GET", endpoint)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8", errors="replace"))

    def _get_text(self, endpoint: str) -> str:
        return self._request("GET", endpoint).decode("utf-8", errors="replace").strip()

    def _post_form(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        data = urllib.parse.urlencode({k: str(v) for k, v in params.items()}).encode("utf-8")
        raw = self._request(
            "POST", endpoint, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace").strip()
        try:
            return json.loads(text)
        except Exception:
            return text

    @staticmethod
    def _multipart(fields: dict[str, Any], file_path: str | None = None) -> tuple[bytes, str]:
        boundary = "----RemoteQBT" + uuid.uuid4().hex
        chunks: list[bytes] = []
        for name, value in fields.items():
            if value is None:
                continue
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        if file_path:
            p = Path(file_path)
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="torrents"; filename="{p.name}"\r\n'.encode(),
                b"Content-Type: application/x-bittorrent\r\n\r\n",
                p.read_bytes(),
                b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    # ---------- app ----------
    def version(self) -> str:
        return self._get_text("/api/v2/app/version")

    def webapi_version(self) -> str:
        return self._get_text("/api/v2/app/webapiVersion")

    def build_info(self) -> dict[str, Any]:
        return self._get_json("/api/v2/app/buildInfo") or {}

    def process_info(self) -> dict[str, Any]:
        return self._get_json("/api/v2/app/processInfo") or {}

    def preferences(self) -> dict[str, Any]:
        return self._get_json("/api/v2/app/preferences") or {}

    def set_preferences(self, updates: dict[str, Any]) -> Any:
        return self._post_form("/api/v2/app/setPreferences", {"json": json.dumps(updates, separators=(",", ":"))})

    def default_save_path(self) -> str:
        return self._get_text("/api/v2/app/defaultSavePath")

    def shutdown(self) -> Any:
        return self._post_form("/api/v2/app/shutdown")

    def directory_content(self, path: str, mode: str = "dirs", with_metadata: bool = False) -> list[Any]:
        q = urllib.parse.urlencode({
            "dirPath": path,
            "mode": mode,
            "withMetadata": to_bool_text(with_metadata),
        })
        return self._get_json(f"/api/v2/app/getDirectoryContent?{q}") or []

    def free_space(self, path: str) -> int:
        # Added after qBittorrent 5.2.1. Keep the folder picker compatible with
        # 5.2.1 by treating an unsupported endpoint as "unknown free space".
        q = urllib.parse.urlencode({"path": path})
        try:
            data = self._get_json(f"/api/v2/app/getFreeSpaceAtPath?{q}")
            return int(data)
        except Exception:
            return -1

    # ---------- sync / transfer ----------
    def main_sync(self, rid: int) -> dict[str, Any]:
        return self._get_json(f"/api/v2/sync/maindata?rid={int(rid)}") or {}

    def transfer_info(self) -> dict[str, Any]:
        return self._get_json("/api/v2/transfer/info") or {}

    def speed_limits(self) -> dict[str, int]:
        # qBittorrent 5.2.1 (Web API 2.15.1) exposes normal limits through the
        # individual transfer endpoints and alternative limits through app prefs.
        # Newer qBittorrent builds have a combined endpoint, but this form works on
        # the user's 5.2.1 server and remains forward-compatible.
        try:
            up = int(self._get_text("/api/v2/transfer/uploadLimit") or 0)
        except Exception:
            up = 0
        try:
            down = int(self._get_text("/api/v2/transfer/downloadLimit") or 0)
        except Exception:
            down = 0
        prefs = self.preferences()
        return {
            "up_limit": up,
            "dl_limit": down,
            "alt_up_limit": int(prefs.get("alt_up_limit", 0) or 0),
            "alt_dl_limit": int(prefs.get("alt_dl_limit", 0) or 0),
        }

    def set_speed_limits(self, up_limit: int, dl_limit: int, alt_up_limit: int, alt_dl_limit: int) -> Any:
        self._post_form("/api/v2/transfer/setUploadLimit", {"limit": up_limit})
        self._post_form("/api/v2/transfer/setDownloadLimit", {"limit": dl_limit})
        return self.set_preferences({"alt_up_limit": alt_up_limit, "alt_dl_limit": alt_dl_limit})

    def speed_limits_mode(self) -> bool:
        return self._get_text("/api/v2/transfer/speedLimitsMode").strip() not in {"0", "false", "False", ""}

    def set_speed_limits_mode(self, enabled: bool) -> Any:
        return self._post_form("/api/v2/transfer/setSpeedLimitsMode", {"mode": 1 if enabled else 0})

    def ban_peers(self, peers: Iterable[str]) -> Any:
        return self._post_form("/api/v2/transfer/banPeers", {"peers": "|".join(peers)})

    # ---------- torrent listing/details ----------
    def torrents_info(self) -> list[dict[str, Any]]:
        return self._get_json("/api/v2/torrents/info") or []

    def torrent_properties(self, torrent_hash: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({"hash": torrent_hash})
        return self._get_json(f"/api/v2/torrents/properties?{q}") or {}

    def torrent_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"hash": torrent_hash})
        return self._get_json(f"/api/v2/torrents/trackers?{q}") or []

    def torrent_webseeds(self, torrent_hash: str) -> list[str]:
        q = urllib.parse.urlencode({"hash": torrent_hash})
        return self._get_json(f"/api/v2/torrents/webseeds?{q}") or []

    def torrent_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"hash": torrent_hash})
        return self._get_json(f"/api/v2/torrents/files?{q}") or []

    def torrent_peers(self, torrent_hash: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({"hash": torrent_hash, "rid": 0})
        data = self._get_json(f"/api/v2/sync/torrentPeers?{q}") or {}
        return data.get("peers", {}) or {}

    def torrent_details(self, torrent_hash: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "properties": {}, "trackers": [], "webseeds": [], "peers": {}, "files": [], "errors": []
        }
        calls = [
            ("properties", self.torrent_properties),
            ("trackers", self.torrent_trackers),
            ("webseeds", self.torrent_webseeds),
            ("peers", self.torrent_peers),
            ("files", self.torrent_files),
        ]
        for key, fn in calls:
            try:
                result[key] = fn(torrent_hash)
            except Exception as e:
                result["errors"].append(f"{key}: {e}")
        return result

    # ---------- generic torrent actions ----------
    @staticmethod
    def _hashes(hashes: Iterable[str]) -> str:
        values = [str(x) for x in hashes if str(x)]
        if not values:
            raise QbtApiError("No torrents selected.")
        return "|".join(values)

    def torrent_action(self, action: str, hashes: Iterable[str], **extra: Any) -> Any:
        params: dict[str, Any] = {"hashes": self._hashes(hashes)}
        params.update(extra)
        return self._post_form(f"/api/v2/torrents/{action}", params)

    def start(self, hashes: Iterable[str]) -> Any:
        return self.torrent_action("start", hashes)

    def stop(self, hashes: Iterable[str]) -> Any:
        return self.torrent_action("stop", hashes)

    def delete(self, hashes: Iterable[str], delete_files: bool) -> Any:
        return self.torrent_action("delete", hashes, deleteFiles=to_bool_text(delete_files))

    def recheck(self, hashes: Iterable[str]) -> Any:
        return self.torrent_action("recheck", hashes)

    def reannounce(self, hashes: Iterable[str]) -> Any:
        return self.torrent_action("reannounce", hashes)

    def queue(self, hashes: Iterable[str], where: str) -> Any:
        action = {
            "top": "topPrio",
            "up": "increasePrio",
            "down": "decreasePrio",
            "bottom": "bottomPrio",
        }[where]
        return self.torrent_action(action, hashes)

    def set_force_start(self, hashes: Iterable[str], value: bool) -> Any:
        return self.torrent_action("setForceStart", hashes, value=to_bool_text(value))

    def set_super_seeding(self, hashes: Iterable[str], value: bool) -> Any:
        return self.torrent_action("setSuperSeeding", hashes, value=to_bool_text(value))

    def toggle_sequential(self, hashes: Iterable[str]) -> Any:
        return self.torrent_action("toggleSequentialDownload", hashes)

    def toggle_first_last(self, hashes: Iterable[str]) -> Any:
        return self.torrent_action("toggleFirstLastPiecePrio", hashes)

    def set_auto_management(self, hashes: Iterable[str], enabled: bool) -> Any:
        return self.torrent_action("setAutoManagement", hashes, enable=to_bool_text(enabled))

    def set_location(self, hashes: Iterable[str], path: str) -> Any:
        return self.torrent_action("setLocation", hashes, location=path)

    def set_save_path(self, hashes: Iterable[str], path: str) -> Any:
        # Current qBittorrent API names this parameter `id`, rather than `hashes`.
        return self._post_form("/api/v2/torrents/setSavePath", {"id": self._hashes(hashes), "path": path})

    def set_download_path(self, hashes: Iterable[str], path: str) -> Any:
        return self._post_form("/api/v2/torrents/setDownloadPath", {"id": self._hashes(hashes), "path": path})

    def rename(self, torrent_hash: str, name: str) -> Any:
        return self._post_form("/api/v2/torrents/rename", {"hash": torrent_hash, "name": name})

    def set_comment(self, hashes: Iterable[str], comment: str) -> Any:
        return self.torrent_action("setComment", hashes, comment=comment)

    def set_category(self, hashes: Iterable[str], category: str) -> Any:
        return self.torrent_action("setCategory", hashes, category=category)

    def add_tags(self, hashes: Iterable[str], tags: list[str]) -> Any:
        return self.torrent_action("addTags", hashes, tags=",".join(tags))

    def set_tags(self, hashes: Iterable[str], tags: list[str]) -> Any:
        return self.torrent_action("setTags", hashes, tags=",".join(tags))

    def remove_tags(self, hashes: Iterable[str], tags: list[str]) -> Any:
        return self.torrent_action("removeTags", hashes, tags=",".join(tags))

    def set_upload_limit(self, hashes: Iterable[str], limit: int) -> Any:
        return self.torrent_action("setUploadLimit", hashes, limit=limit)

    def set_download_limit(self, hashes: Iterable[str], limit: int) -> Any:
        return self.torrent_action("setDownloadLimit", hashes, limit=limit)

    def set_share_limits(
        self,
        hashes: Iterable[str],
        ratio_limit: float,
        seeding_time_limit: int,
        inactive_seeding_time_limit: int,
        action: str,
    ) -> Any:
        return self.torrent_action(
            "setShareLimits", hashes,
            ratioLimit=ratio_limit,
            seedingTimeLimit=seeding_time_limit,
            inactiveSeedingTimeLimit=inactive_seeding_time_limit,
            shareLimitAction=action,
        )

    # ---------- categories/tags ----------
    def create_category(self, name: str, save_path: str = "") -> Any:
        return self._post_form("/api/v2/torrents/createCategory", {"category": name, "savePath": save_path})

    def edit_category(self, name: str, save_path: str = "") -> Any:
        return self._post_form("/api/v2/torrents/editCategory", {"category": name, "savePath": save_path})

    def remove_categories(self, names: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/removeCategories", {"categories": "\n".join(names)})

    def create_tags(self, tags: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/createTags", {"tags": ",".join(tags)})

    def delete_tags(self, tags: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/deleteTags", {"tags": ",".join(tags)})

    # ---------- trackers/web seeds/peers ----------
    def add_trackers(self, torrent_hash: str, urls: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/addTrackers", {"hash": torrent_hash, "urls": "\n".join(urls)})

    def edit_tracker(self, torrent_hash: str, orig_url: str, new_url: str) -> Any:
        return self._post_form("/api/v2/torrents/editTracker", {"hash": torrent_hash, "origUrl": orig_url, "newUrl": new_url})

    def remove_trackers(self, torrent_hash: str, urls: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/removeTrackers", {"hash": torrent_hash, "urls": "|".join(urls)})

    def add_webseeds(self, torrent_hash: str, urls: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/addWebSeeds", {"hash": torrent_hash, "urls": "\n".join(urls)})

    def edit_webseed(self, torrent_hash: str, orig_url: str, new_url: str) -> Any:
        return self._post_form("/api/v2/torrents/editWebSeed", {"hash": torrent_hash, "origUrl": orig_url, "newUrl": new_url})

    def remove_webseeds(self, torrent_hash: str, urls: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/removeWebSeeds", {"hash": torrent_hash, "urls": "|".join(urls)})

    def add_peers(self, torrent_hashes: Iterable[str], peers: list[str]) -> Any:
        return self._post_form("/api/v2/torrents/addPeers", {
            "hashes": self._hashes(torrent_hashes),
            "peers": "|".join(peers),
        })

    def file_priority(self, torrent_hash: str, file_ids: list[int], priority: int) -> Any:
        return self._post_form("/api/v2/torrents/filePrio", {
            "hash": torrent_hash,
            "id": "|".join(str(i) for i in file_ids),
            "priority": priority,
        })

    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> Any:
        return self._post_form("/api/v2/torrents/renameFile", {"hash": torrent_hash, "oldPath": old_path, "newPath": new_path})

    def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> Any:
        return self._post_form("/api/v2/torrents/renameFolder", {"hash": torrent_hash, "oldPath": old_path, "newPath": new_path})

    def export_torrent(self, torrent_hash: str) -> bytes:
        q = urllib.parse.urlencode({"hash": torrent_hash})
        return self._request("GET", f"/api/v2/torrents/export?{q}")

    # ---------- add ----------
    @staticmethod
    def parse_sources(text: str) -> list[str]:
        items: list[str] = []
        for line in text.replace("\r", "").split("\n"):
            line = line.strip()
            if not line:
                continue
            if os.path.isfile(line):
                items.append(line)
                continue
            found = re.findall(r'(?:magnet:\?[^\s]+|https?://[^\s]+)', line)
            items.extend(found or [line])
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def add_sources(self, sources: list[str], options: dict[str, Any]) -> list[dict[str, Any]]:
        if not sources:
            raise QbtApiError("Nothing to add.")
        results: list[dict[str, Any]] = []
        fields = dict(options)
        fields.setdefault("savepath", self.save_path)
        # qBittorrent's current API accepts boolean strings for these fields.
        for key in [
            "seedMode", "skip_checking", "sequentialDownload", "firstLastPiecePrio", "forced",
            "addToTopOfQueue", "stopped", "useDownloadPath", "autoTMM",
        ]:
            if key in fields and isinstance(fields[key], bool):
                fields[key] = to_bool_text(fields[key])

        for source in sources:
            try:
                file_path = source if os.path.isfile(source) else None
                if file_path and Path(file_path).suffix.lower() != ".torrent":
                    raise QbtApiError("Local files must end in .torrent")
                local_fields = dict(fields)
                if not file_path:
                    if not (source.startswith("http://") or source.startswith("https://") or source.startswith("magnet:?")):
                        raise QbtApiError("Not a URL, magnet, or local .torrent file.")
                    local_fields["urls"] = source
                payload, ctype = self._multipart(local_fields, file_path=file_path)
                raw = self._request(
                    "POST", "/api/v2/torrents/add", data=payload,
                    headers={"Content-Type": ctype}, timeout=40,
                )
                text = raw.decode("utf-8", errors="replace").strip()
                try:
                    detail: Any = json.loads(text) if text else {"success": True}
                except Exception:
                    detail = text or "OK"
                # qBittorrent 5.2 can return async add bookkeeping. A zero failure_count
                # with pending_count=1 is an accepted URL, not a failure.
                ok = not (isinstance(detail, dict) and int(detail.get("failure_count", 0) or 0) > 0)
                results.append({"item": source, "ok": ok, "detail": detail})
            except Exception as e:
                results.append({"item": source, "ok": False, "detail": str(e)})
        return results

    # ---------- logs ----------
    def main_log(self, last_known_id: int = -1) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({
            "normal": "true", "info": "true", "warning": "true", "critical": "true",
            "last_known_id": last_known_id,
        })
        return self._get_json(f"/api/v2/log/main?{q}") or []
