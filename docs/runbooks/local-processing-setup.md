# Runbook: Local Processing Machine Setup

Sets up the processing machine, the local Linux machine that handles all real recorder media
(HLD §2). The repository's setup script does everything it can. This runbook covers the rest.
Design: [environments](../intent/environments/environments-design.md) and
[config-cli](../intent/config-cli/config-cli-design.md) § Private values.

Run it once to set up a new machine, and again whenever the machine is rebuilt. Every step is
safe to repeat.

## Reference machine

| Property | Value |
|---|---|
| Host | Chromebook, ChromeOS Linux development environment (Crostini) |
| Linux distribution | Debian 13 "trixie" |
| Architecture | x86_64 |
| CPU cores | 8 |
| RAM available to Linux | 6.4 GB, no swap |
| Linux disk | 50 GB |
| Network | Wired Ethernet, about 20 Mbit/s upstream |

The disk budget ([intake](../intent/ingest/intake/intake-design.md) § Disk Budget) and the
worker default ([runs](../intent/runs/runs-design.md) § Resource Limits) are sized for this
machine. On a different machine, update this table first, then re-check those two sections and
`config/processing.toml`.

To confirm the values on the machine:

```bash
. /etc/os-release && echo "$PRETTY_NAME"; uname -m; nproc
free -h; swapon --show; df -h ~
```

## 1. Enable Linux and size its disk

1. *Settings → About ChromeOS → Developers → Linux development environment → Set up*.
2. When asked for the disk size, choose **50 GB**. To resize an existing environment, use
   *Settings → About ChromeOS → Developers → Linux development environment → Disk size*.
3. Open the *Terminal* app and confirm the Debian release with `cat /etc/os-release`. If it is
   not the release in the table above, update the table and the `setup-local` CI job's image
   ([environments](../intent/environments/environments-design.md) § CI: setup-local job).

## 2. Power and sleep settings

Linux suspends when the Chromebook sleeps, which pauses a run until the machine wakes.

1. *Settings → Device → Power*: while charging, set *When idle* to **Turn off display** or
   **Keep display on**, not *Sleep*.
2. Turn off **Sleep when cover is closed**.
3. Process cards with the charger connected and the Ethernet cable plugged in.

## 3. Share the SD card reader with Linux

The card is read through ChromeOS and shared into Linux. Nothing is copied through ChromeOS's
own folders.

1. Insert a card. ChromeOS shows it in the *Files* app.
2. In *Files*, right-click the card → **Share with Linux**.
3. In the terminal, check it is visible: `ls /mnt/chromeos/removable/`.

If the card isn't listed after you insert it again later, share it again. Don't pass the card
reader through as a USB device (*Manage USB devices*); the file share is the supported path.

## 4. Clone the repository and run the setup script

```bash
git clone https://github.com/mattjtravers/urban-biomonitoring.git ~/urban-biomonitoring
cd ~/urban-biomonitoring
bash scripts/setup-local.sh
```

The script installs the system packages, the pinned uv, and the locked Python dependencies.
It creates the quarantine, work, and state directories with mode `0700`, creates an empty
`~/.config/urbanbio/env` with mode `0600`, and adds the loader line to `~/.bashrc`. It ends with
`urbanbio config check`. On a new machine that check fails because the secrets are empty, which
is expected. Continue with step 5.

The script asks for your password once, for `sudo apt-get`.

## 5. Fill in the secrets file

The values are in the maintainer's password manager. They never go into the repository, into
*My files*, or into any file other than this one.

1. Open the file in a terminal editor: `nano ~/.config/urbanbio/env` (or `vi`).
2. Fill in `URBANBIO_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and
   `URBANBIO_SITES`. `URBANBIO_ENV=processing` and `AWS_DEFAULT_REGION` are already set.
   `URBANBIO_SITES` is TOML inside single quotes, spanning several lines (format in
   [config-cli](../intent/config-cli/config-cli-design.md) § Private values).
3. Save, then check the mode is still `0600`: `stat -c '%a' ~/.config/urbanbio/env`. If it
   isn't, run `chmod 600 ~/.config/urbanbio/env`.
4. Close the terminal and open a new one, so the shell loads the values.

## 6. Re-run the setup script until the check passes

```bash
cd ~/urban-biomonitoring
bash scripts/setup-local.sh
```

It finishes with `urbanbio config check` passing. If the check fails, it prints the failing
check and its fix. It never prints a secret value.

## Backups of the Linux environment

ChromeOS's *Back up Linux* writes the whole Linux disk, quarantine included, to a file in *My
files*. Take one only when the quarantine holds no audio: every retrieval's `urbanbio retrieval
status` reports safe to wipe and `ls ~/urbanbio/quarantine/incoming/` is empty. Nothing on the
machine needs a backup. Results are in AWS S3, the code is in git, and the secrets are in the
password manager, so a rebuild from this runbook replaces a restore.

## Rotating the AWS key

The archive runbook rotates the pipeline key every 90 days
([archive](../intent/archive/archive-design.md) § IAM). After a rotation, update
`~/.config/urbanbio/env` (step 5), the Codespaces secrets, and the password manager, then
open a new terminal and run `uv run urbanbio config check`.
