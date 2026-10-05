# Runbook: Using a `filesystem` Store

How to point the pipeline at a directory on a mounted filesystem, such as a lab NFS or SMB
share or an external disk, instead of the default AWS S3 bucket. Design:
[store](../intent/store/store-design.md) § `filesystem` Backend.

## Requirements for the filesystem

The pipeline confirms every write by reading the file back, and it purges unmasked originals
from the quarantine on the strength of that confirmation. The filesystem must make that
confirmation mean something:

- **Durable when `fsync` returns.** Local disks, NFS, and SMB shares mounted with default
  (synchronous-on-fsync) options qualify. Sync clients and FUSE mounts that cache writes locally
  and upload later (cloud-drive clients, `rclone mount` with a write cache) only confirm the
  local copy. If you use one, don't wipe a card until the client reports that its uploads have
  finished.
- **On a different filesystem from the quarantine.** `store init` and `config check` refuse a
  store root on the quarantine's filesystem in the `processing` profile, and `retrieval status`
  won't call a card safe to wipe.
- **Private.** The store holds header sidecars and device logs, which include the recorder's
  exact position. Only you, or your lab group, should be able to read it. Never share the
  folder publicly or by link.
- **Snapshots and backups expire after a re-mask.** When a re-screen re-masks a file, the
  previous version still contains speech the new mask removes. If the filesystem keeps
  snapshots or you back it up, expire copies older than the re-mask.
- **Mounted before every run.** An unmounted share shows as an empty directory. The store marker
  check catches that and stops the command; mount the share and run it again.

## Setup

1. Create the store directory on the mounted filesystem, readable only by you or your group:

   ```bash
   mkdir -p /mnt/lab-share/urbanbio
   chmod 2750 /mnt/lab-share/urbanbio      # setgid: new files take the directory's group
   ```

2. Select the backend in your operator config file, outside the repository (for example,
   `~/.config/urbanbio/config.toml`), and set `URBANBIO_CONFIG` to its path in your shell
   environment:

   ```toml
   [store]
   backend = "filesystem"
   root = "/mnt/lab-share/urbanbio"
   ```

3. With `URBANBIO_PROFILE` and `URBANBIO_CONFIG` set, initialize and check:

   ```bash
   uv run urbanbio store init
   uv run urbanbio config check
   ```

`URBANBIO_BUCKET` and the AWS credentials aren't needed with this backend.
