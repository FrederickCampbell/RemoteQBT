# RemoteQBT for qBittorrent 5.2.3

**Release identity:** `5.2.3-r3` · Git tag `qbt-5.2.3-r3`

**Remote qBittorrent, controlled locally.**

RemoteQBT is an unofficial Windows desktop client for a qBittorrent instance running on another Windows/Linux host. qBittorrent remains the torrent engine: downloads, files, tracker sessions, long-term seeding, and queue state stay on the remote host. RemoteQBT renders a native desktop UI and talks to qBittorrent through its Web API.

## Release identity

RemoteQBT deliberately has **no independent major/minor/patch product version**.

- qBittorrent `5.2.3` → RemoteQBT `5.2.3-r1`
- a RemoteQBT-only follow-up while still targeting qBittorrent `5.2.3` → `5.2.3-r2`, then `r3`, and so on
- when qBittorrent advances, RemoteQBT moves to that qBittorrent version and resets to `r1`

GitHub release tags use `qbt-X.Y.Z-rN`. The build manifest, Windows package name, Qt application version, updater comparison logic, README, installer banner, GitHub release title, and automation all derive from that same identity.

Current audited upstream baseline: qBittorrent **5.2.3** (`release-5.2.3`), Web API **2.15.1**.

## Live transfer list

RemoteQBT uses qBittorrent's `/api/v2/sync/maindata` incremental-sync endpoint instead of rebuilding the whole table every refresh. Existing rows update in place, new/removed torrents do not yank the viewport, selection/scroll position are preserved, and Live Sorting is off by default.

## qBittorrent-shaped interface

The main window follows qBittorrent's desktop structure: menu bar, compact toolbar, filters sidebar, transfer list, torrent property tabs, and status bar. Supported upstream qBittorrent artwork is used for the app/toolbar icons.

Remote transfer controls include adding files/links, start/stop, session pause/resume, remove with or without content, Force Start, recheck/reannounce, queue controls, destination opening through the configured SMB mirror, export/rename/comment/location controls, Auto TMM, sequential/first-last-piece modes, Super Seeding, rate/share limits, categories, tags, trackers, peers, web seeds, and file priorities/rename.

## Native remote-host folder picker

Torrent destinations are browsed on the **qBittorrent host**, not guessed from laptop paths. RemoteQBT uses qBittorrent's `app/getDirectoryContent` API for the remote folder tree.

## Windows magnet + `.torrent` integration

The installer registers RemoteQBT as a per-user handler for `magnet:` links and `.torrent` files. A single-instance local channel forwards new magnets/torrent files to an already-open RemoteQBT window.

## Security

The qBittorrent API key is protected with Windows DPAPI for the current Windows account in `%APPDATA%\RemoteQBT\config.json`. Private tracker passkeys are not built into RemoteQBT.

## Install / update

`Install.ps1` builds a windowed PyInstaller `onedir` app and installs it under `%LOCALAPPDATA%\Programs\RemoteQBT`.

Editable source uses a stable generation-free project location:

`C:\_Hub\Projects\RemoteQBT\current`

Other PCs fall back to `%LOCALAPPDATA%\RemoteQBT\Source`. Existing `%APPDATA%\RemoteQBT` configuration is preserved.

The installed app checks GitHub Releases in the background at most twice per day. Only `qbt-X.Y.Z-rN` releases with both a Windows ZIP and its SHA-256 sidecar are offered.

Self-updates are transactional: RemoteQBT downloads and verifies the package before closing, the Windows updater stages the replacement, keeps a rollback copy until the installed release identity marker matches the expected release, then relaunches and confirms success. Failures restore the previous installation and are recorded in `%LOCALAPPDATA%\RemoteQBT\Update-RemoteQBT.log`.

## Automatic qBittorrent tracking

The daily `qBittorrent Upstream Watch` compares the latest stable qBittorrent release with the stored API/UI baseline.

If the change is mechanically safe, automation aligns the RemoteQBT identity to the new qBittorrent release, opens a bot PR, explicitly runs Windows CI, performs syntax/tests plus a real PyInstaller Windows build, merges only if green, then tags/builds/publishes the verified release.

If qBittorrent adds/removes Web API actions, changes the Web API version, removes a required endpoint, or changes mirrored desktop `.ui` files, automation stops at a review PR + issue instead of inventing behavior. Once the adaptation is reviewed and merged, the qBittorrent-aligned release is built automatically.

See `AUTOMATION.md`.

## Scope

RemoteQBT implements the remotely meaningful desktop transfer-management surface. Separate qBittorrent applications such as the RSS reader, Search Engine, Torrent Creator, and plugin manager are not duplicated into fake desktop panes; the official Web UI remains available for those subsystems.

## Licensing

qBittorrent code and visual assets are GPL-licensed. See `LICENSE-GPLv3.txt` and `THIRD_PARTY_NOTICES.md`. RemoteQBT is an unofficial companion and is not affiliated with or endorsed by the qBittorrent project.
