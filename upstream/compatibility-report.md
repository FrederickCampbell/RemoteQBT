# qBittorrent upstream compatibility report

- Previous baseline: `release-5.2.3`
- Latest release: `release-5.3.0`
- Web API: `2.15.1` → `2.16.2`
- Classification: **review required**

## API surface diff

### app
- Added: `getFreeSpaceAtPath`
- Removed: none
- Required but missing: none

### transfer
- Added: `getSpeedLimits`, `pauseSession`, `resumeSession`, `setSpeedLimits`
- Removed: none
- Required but missing: none

### sync
- Added: none
- Removed: none
- Required but missing: none

### torrents
- Added: `downloadFile`
- Removed: none
- Required but missing: none

## Upstream desktop UI

- Changed `.ui` files: `mainwindow.ui`, `addnewtorrentdialog.ui`
- Synced qBittorrent icon assets: none

## Automation decision

The bot will not blindly invent UI behavior. It will open/update an upstream review PR (and issue) containing this diff so the changed/new qBittorrent surface can be implemented intentionally, then normal CI/release automation takes over.
