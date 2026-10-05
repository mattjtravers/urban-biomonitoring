# Runbook: ChromeOS Processing Host (example deployment)

This is the maintainer's processing host: a Chromebook running the ChromeOS Linux development
environment (Crostini), with the `processing` profile, the default `s3` store, and private
values in an env file loaded by the shell. It is one example of a deployment; nothing in the
software depends on it. The generic steps are in the [README](../../README.md), and the design
in [install](../intent/install/install-design.md),
[config-cli](../intent/config-cli/config-cli-design.md), and
[store](../intent/store/store-design.md).

Run it once to set up a new host, and again whenever the host is rebuilt. Every step is safe to
repeat.

## Host

| Property | Value |
|---|---|
| Host | Chromebook, ChromeOS Linux development environment (Crostini) |
| Linux distribution | Debian 13 "trixie" |
| Architecture | x86_64 |
| CPU cores | 8 |
| RAM available to Linux | 6.4 GB, no swap |
| Linux disk | 50 GB |
| Network | Wired Ethernet, about 20 Mbit/s upstream |

The host has less RAM than the 8 GB the defaults are sized for
([install](../intent/install/install-design.md) § Sizing guidance). Keep the default of 2
workers until the first card's run records show peak memory, and don't raise
`resources.workers` without that evidence. If memory runs short, adding swap inside Crostini is
an option to evaluate then.

To confirm the values on the host:

```bash
. /etc/os-release && echo "$PRETTY_NAME"; uname -m; nproc
free -h; swapon --show; df -h ~
```

## 1. Enable Linux and size its disk

1. *Settings → About ChromeOS → Developers → Linux development environment → Set up*.
2. When asked for the disk size, choose **50 GB**. To resize an existing environment, use
   *Settings → About ChromeOS → Developers → Linux development environment → Disk size*.
3. Open the *Terminal* app and confirm the Debian release with `cat /etc/os-release`. Debian 13
   is one of the releases CI tests the installer on.

## 2. Power and sleep settings

Linux suspends when the Chromebook sleeps, which pauses a run until the host wakes.

1. *Settings → Device → Power*: while charging, set *When idle* to **Turn off display** or
   **Keep display on**, not *Sleep*.
2. Turn off **Sleep when cover is closed**.
3. Process cards with the charger connected and the Ethernet cable plugged in.

## 3. Share the SD card reader with Linux

The card is read through ChromeOS and shared into Linux. Nothing is copied through ChromeOS's
own folders.

1. Insert a card. ChromeOS shows it in the *Files* app.
2. In *Files*, right-click the card → **Share with Linux**.
3. In the terminal, check it is visible: `ls /mnt/chromeos/removable/`. The card's mount point,
   `<card mount>` in the weekly runbook, is `/mnt/chromeos/removable/<card name>`.

If the card isn't listed after you insert it again later, share it again. Don't pass the card
reader through as a USB device (*Manage USB devices*); the file share is the supported path.

## 4. Clone the repository and run the installer

```bash
git clone https://github.com/mattjtravers/urban-biomonitoring.git ~/urban-biomonitoring
cd ~/urban-biomonitoring
bash scripts/install.sh --profile processing
sudo apt-get install -y tmux      # For long runs; not part of the installer
```

The installer installs the system packages, the pinned uv, and the locked Python dependencies,
and creates the quarantine, work, and state directories with mode `0700`. It ends with
`urbanbio config check`, which fails on a new host because the private values aren't set yet.
Continue with step 5.

The installer asks for your password once, for `sudo apt-get`.

## 5. Create the secrets file and load it in every shell

On this host, private values live in `~/.config/urbanbio/env`, outside the repository, and the
shell exports them. The values are in the maintainer's password manager. They never go into the
repository, into *My files*, or into any file other than this one.

1. Create the directory and an empty file with private modes:

   ```bash
   install -d -m 0700 ~/.config/urbanbio
   [ -f ~/.config/urbanbio/env ] || install -m 0600 /dev/null ~/.config/urbanbio/env
   ```

2. Open it in a terminal editor (`nano ~/.config/urbanbio/env`) and fill it in.
   `URBANBIO_SITES` is TOML inside single quotes, spanning several lines (format in
   [config-cli](../intent/config-cli/config-cli-design.md) § Private values):

   ```bash
   URBANBIO_PROFILE=processing
   URBANBIO_BUCKET=...
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   URBANBIO_SITES='
   [[site]]
   site_id = "site1"
   ...
   '
   ```

3. Check the mode is still `0600`: `stat -c '%a' ~/.config/urbanbio/env`. If it isn't, run
   `chmod 600 ~/.config/urbanbio/env`.
4. Add the loader to `~/.bashrc` once:

   ```bash
   grep -qxF 'set -a; [ -f ~/.config/urbanbio/env ] && . ~/.config/urbanbio/env; set +a' ~/.bashrc \
     || echo 'set -a; [ -f ~/.config/urbanbio/env ] && . ~/.config/urbanbio/env; set +a' >> ~/.bashrc
   ```

5. Close the terminal and open a new one, so the shell loads the values. A new terminal is
   needed after every edit.

## 6. Initialize the store, if it's new

A new bucket needs its marker once (see the AWS setup runbook). With the values loaded:

```bash
cd ~/urban-biomonitoring
uv run urbanbio store init        # Only for a new, empty bucket; refuses otherwise
```

Skip this when rebuilding the host against an existing bucket.

## 7. Re-run the installer until the check passes

```bash
bash scripts/install.sh --profile processing
```

It finishes with `urbanbio config check` passing. If the check fails, it prints the failing
check and its fix. It never prints a secret value.

## Folders that must never hold the quarantine or a card copy

ChromeOS syncs or backs up several folders outside Linux: *My files*, *Downloads*, and Google
Drive (also visible in Linux under `/mnt/chromeos/` once shared). Never copy a card, or point
`paths.quarantine`, at any of them. Google Drive can hold a `filesystem` store only under the
conditions in [filesystem-store.md](filesystem-store.md); this deployment uses the `s3` store.

## Backups of the Linux environment

ChromeOS's *Back up Linux* writes the whole Linux disk, quarantine included, to a file in *My
files*. Take one only when the quarantine holds no audio: every retrieval's `urbanbio retrieval
status` reports safe to wipe and `ls ~/urbanbio/quarantine/incoming/` is empty. Nothing on the
host needs a backup. Results are in the store, the code is in git, and the secrets are in the
password manager, so a rebuild from this runbook replaces a restore.

## Rotating the AWS key

This deployment rotates the pipeline key every 90 days with the administrator identity
([store](../intent/store/store-design.md) § IAM). After a rotation, update
`~/.config/urbanbio/env` (step 5) and the password manager, then open a new terminal and run
`uv run urbanbio config check`.
