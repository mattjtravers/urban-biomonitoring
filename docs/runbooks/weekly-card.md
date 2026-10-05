# Runbook: Weekly Card Processing

Processes a recorder's microSD card on the processing machine: copy it into the quarantine,
run the pipeline, and wipe the card only once every file is archived. Design:
[intake](../intent/ingest/intake/intake-design.md), [runs](../intent/runs/runs-design.md)
§ Long Runs on the Processing Machine.

Requires a processing machine set up with
[local-processing-setup.md](local-processing-setup.md).

The card is the only backup of its recordings until `urbanbio retrieval status` says **safe to
wipe**. Don't wipe, reformat, or reuse it before then.

## 1. Prepare the machine

1. Plug in the charger and the Ethernet cable.
2. Check the power settings from the setup runbook (no sleep while charging, no sleep when the
   cover closes). The machine stays awake for the whole run, which takes hours.

## 2. Update to the tested code

```bash
cd ~/urban-biomonitoring
git status --short        # Must print nothing
git pull && uv sync --locked
uv run urbanbio config check
```

This runs exactly the code and locked dependencies that CI tested on `main`. Pipeline commands
on the processing machine refuse to start with uncommitted changes
([runs](../intent/runs/runs-design.md) § Run Record).

## 3. Start a tmux session

Closing the terminal window would stop a run started in it. Run everything inside `tmux`:

```bash
tmux new -s card          # Or `tmux attach -t card` if it already exists
```

Detach with `Ctrl-b d`, and reattach later with `tmux attach -t card`.

## 4. Copy the card into the quarantine

1. Insert the card and share it with Linux (*Files* → right-click the card → **Share with
   Linux**). Check it is visible: `ls /mnt/chromeos/removable/`.
2. Check there is room. The card's size must fit within the budget
   ([intake](../intent/ingest/intake/intake-design.md) § Disk Budget):

   ```bash
   du -sh /mnt/chromeos/removable/<card name>/
   du -sh ~/urbanbio/quarantine ~/urbanbio/work; df -h ~
   ```

3. Copy straight from the card into `incoming/`. `<label>` is any short name, such as the
   recorder name and date.

   ```bash
   mkdir -p ~/urbanbio/quarantine/incoming/<label>
   cp -r --preserve=timestamps /mnt/chromeos/removable/<card name>/. \
     ~/urbanbio/quarantine/incoming/<label>/
   ```

4. Compare the copy with the card. Any output means the copy is incomplete: delete
   `incoming/<label>/` and copy again.

   ```bash
   diff -rq /mnt/chromeos/removable/<card name>/ ~/urbanbio/quarantine/incoming/<label>/
   ```

5. Eject the card in *Files* and keep it safe. Don't wipe it.

Never copy the card into *My files*, *Downloads*, or Google Drive.

## 5. Run the pipeline

```bash
uv run urbanbio process ~/urbanbio/quarantine/incoming/<label> \
  --deployment <deployment_id> --retrieved <ISO 8601 time with offset> \
  [--clock-offset-s <seconds>] [--speech-sample <N>]
```

`process` runs ingest, the speech screen, the optional speech sample, archive (purging each
original once it is verified), and detect. It prints the `retrieval_id`. Detach and let it run.

When it ends, check the exit code (`echo $?`):

| Code | Meaning | Next |
|---|---|---|
| `0` | Succeeded | Step 6 |
| `2` | Partial: some units flagged or failed | `uv run urbanbio runs show <run_id>`; failed units retry when the same command is re-run; flagged units need a decision (`runs unflag`, or `purge --discard`) |
| `3` | Configuration or environment error | Fix what it reports; `uv run urbanbio config check` |
| `4` | Lock held | Another command is running. Wait for it; if its PID is gone, re-run with `--force-unlock` |

If the machine slept, lost power, or the terminal closed, re-run the same `process` command. It
resumes and skips completed units.

## 6. Check retrieval status and wipe the card

```bash
uv run urbanbio retrieval status <retrieval_id>
```

Wipe the card **only** when this prints **safe to wipe**. Then format it as the recorder's user
guide describes, and it is ready for the next deployment. If it doesn't print safe to wipe, it
lists the files that aren't archived. Resolve them (step 5's table) and check again.

Speech-labeling holds older than 30 days are listed here too. Label them and run
`urbanbio speech label --import` so they can be purged.

## 7. The second card

With two recorders, repeat steps 4–6 for the other card. Both cards can be copied in one
sitting when step 4's space check shows room for both. Start the second `process` only after
the first has finished. Only one pipeline command runs at a time.

## First-card measurements

The first real card settles open questions in the design docs. Record each finding in the doc
that owns it. Use synthetic fixtures for anything the tests need, and never copy real files,
real positions, or real recordings into the repository. Keep the machine awake throughout, so
the timings don't include time spent suspended.

| Measurement | Where it is recorded |
|---|---|
| File naming, extension case, bit depth, channel count, GUANO fields and formats, vendor chunk location | [songmeter-micro2](../intent/ingest/songmeter-micro2/songmeter-micro2-design.md) |
| Whether the Configurator offers UTC; how the device behaves across a daylight-saving change | [songmeter-micro2](../intent/ingest/songmeter-micro2/songmeter-micro2-design.md) |
| Whether the Configurator shows the device clock before resynchronizing (`--clock-offset-s`) | [intake](../intent/ingest/intake/intake-design.md) § Deferred |
| Installed dependency footprint and uv cache size (settles `quarantine.budget_gb`) | [intake](../intent/ingest/intake/intake-design.md) § Disk Budget |
| FLAC size as a fraction of WAV; upload throughput | [archive](../intent/archive/archive-design.md) § Cost |
| Peak memory and wall-clock time per stage for one card; whether 3–4 workers fit | [runs](../intent/runs/runs-design.md) § Resource Limits |
| Speech screen and detect throughput | [speech-screen](../intent/speech-screen/speech-screen-design.md) § Compute, [detect](../intent/detect/detect-design.md) § Detect Flow |

Peak memory, wall-clock time, and the worker count are in each run's record:
`uv run urbanbio runs show <run_id>` (`resources`).
