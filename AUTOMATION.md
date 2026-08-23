# RemoteQBT automation

RemoteQBT uses qBittorrent itself as its release/version axis.

## Release identity

There is no independent RemoteQBT semantic-version line.

- qBittorrent `X.Y.Z` → RemoteQBT `X.Y.Z-r1`
- RemoteQBT-only fixes while targeting the same qBittorrent release → `X.Y.Z-r2`, `r3`, …
- next qBittorrent release → reset to that qBittorrent version at `r1`
- Git tags → `qbt-X.Y.Z-rN`

The single source of shipped identity is `rqbt/version.py`.

**Current compatibility baseline:** qBittorrent `5.2.3` / Web API `2.15.1`.

`scripts/release_identity.py` synchronizes the README, automation documentation, and `BUILD-MANIFEST.json`, and CI refuses stale identity/baseline documentation.

## CI gate

Every PR/build path validates release identity, compiles Python, runs unit tests, and performs a real Windows PyInstaller smoke build.

Protected `main` blocks direct/force pushes and deletion. Automatic changes land through PRs and must satisfy the Windows `test` check before merge.

## Safe upstream update

The daily watcher audits the latest stable qBittorrent release against the stored controller/UI baseline. A safe change becomes a bot PR, explicit Windows CI, protected merge, a `qbt-X.Y.Z-r1` tag, a verified Windows release, then an in-app update.

Safe updates do not retain transient compatibility-report files on `main`.

## Review-required upstream change

New/removed Web API actions, Web API version changes, missing required endpoints, or mirrored desktop `.ui` changes create a bot PR + issue and stop.

The compatibility report exists only while that review is needed. After the reviewed adaptation is merged, release preparation removes the transient report, runs the same Windows CI gate, and publishes the qBittorrent-aligned release.

## Client-only revision

If RemoteQBT itself needs another release without qBittorrent changing, `scripts/release_identity.py increment` advances only the `rN` suffix.

## Immutable releases

Windows releases are built through explicit `workflow_dispatch` only. Creating a tag cannot start a second competing build.

Release jobs are serialized per tag. Once a published tag has its Windows ZIP and SHA-256 sidecar, reruns are a no-op: published assets are never replaced. New releases are assembled as drafts and become public only after both required assets exist.

## Dependencies

Runtime/build inputs in `requirements.txt` are exact pins. Dependency updates are intentionally explicit rather than automated by Dependabot, keeping the public repository free of maintenance-branch noise. Any dependency change must still pass the same protected Windows CI gate before release.

## Self-update

The installed app accepts only `qbt-X.Y.Z-rN` releases with a Windows ZIP plus its exact SHA-256 sidecar. User configuration under `%APPDATA%\RemoteQBT` is never part of a release package.
