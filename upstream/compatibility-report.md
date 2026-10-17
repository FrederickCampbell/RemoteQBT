# qBittorrent upstream compatibility report

- Previous baseline: `release-5.2.3`
- Latest release: `release-5.2.4`
- Web API: `2.15.1` → `2.15.1`
- Classification: **review required**

## API surface diff

### app
- Added: none
- Removed: none
- Required but missing: none

### transfer
- Added: none
- Removed: none
- Required but missing: none

### sync
- Added: none
- Removed: none
- Required but missing: none

### torrents
- Added: none
- Removed: none
- Required but missing: none

## Upstream desktop UI

- Changed `.ui` files: `addnewtorrentdialog.ui`
- Synced qBittorrent icon assets: none

## Automation decision

The bot will not blindly invent UI behavior. It will open/update an upstream review PR (and issue) containing this diff so the changed/new qBittorrent surface can be implemented intentionally, then normal CI/release automation takes over.
