---
parent: ingest
prefix: INGEST-SM2
---

# Song Meter Micro 2 Adapter

## Context and Design Philosophy

The first recorder adapter (adapter key `songmeter-micro2`) parses Wildlife Acoustics Song
Meter Micro 2 cards into the ingest contract ([ingest](../ingest-design.md)). It implements only
what Wildlife Acoustics documents in the Song Meter Micro 2 User Guide (2025-08-22 edition,
cited as **[UG]** with page numbers). Anything the guide doesn't specify is marked
**TODO: confirm from first real card** and the adapter treats it strictly: an unexpected value
flags the file rather than being guessed at (Tenet 3).

The Song Meter Micro and Micro 2 run the same firmware versions [UG p.13 "Configuration and
Firmware Files are Cross-Compatible"], so this adapter is expected to read Micro cards too.
That is untested until a Micro card is available.

## Card Layout [UG p.93]

```
<card root>/
  <RECORDER NAME>_Summary.txt                  # Summary (status) log
  <RECORDER NAME>_<YYYYMMDD>_<hhmmss>.minidiags   # Diagnostics, after unexpected reboots or on request
  Data/
    <RECORDER NAME>_<YYYYMMDD>_<hhmmss>.wav    # Audio
```

- "All non-audio files" are at the card's top level; "all audio files are saved to a folder
  named `Data`" [UG p.93].
- Configuration and firmware files may also be on the card if the maintainer saved them there
  [UG p.27]. `list_media` classifies them as `unknown`, and they're archived verbatim with the
  device logs.
- The card's volume label is the first 11 characters of the recorder name (firmware 4.4+)
  [UG p.27]. The adapter doesn't depend on it.

## File Naming [UG p.93]

- The recorder name prefixes every audio, summary, and diagnostics file. It defaults to the
  serial number and can be changed in the Configurator.
- The date and time in audio filenames mark when the recording began, "according to the time
  zone that was set on the recorder for that deployment," in the format `YYYYMMDD_hhmmss`.
- Parsing regex (applied to the basename):
  `^(?P<name>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.(?P<ext>wav)$`. The name may itself contain
  underscores, so date and time are anchored at the end.
- **TODO: confirm from first real card:** extension case (`.wav` vs `.WAV`); the regex matches
  case-insensitively until confirmed.

**Recorder name rule.** The recorder name ends up in filenames, which are stored in the
private archive. Because it can be any text, the runbook sets it to an opaque value derived from
the deployment (for example `site1A`). The adapter flags `recorder_name_mismatch` when the
filename prefix doesn't equal the deployment's `recorder_name`.

## Audio Format

- Container: RIFF/WAVE, uncompressed ("full-spectrum") [UG p.93].
- Sample rates offered: 8,000; 12,000; 16,000; 22,050; 24,000; 32,000; 44,100; 48,000; 96,000;
  192,000; 256,000 Hz. The default is 24,000 Hz [UG p.45]. Project deployments use 48,000 Hz.
- Maximum recording length: 1–60 minutes in 1-minute steps (default 60) [UG p.45]. Project
  deployments use 1 minute with a 1 minute on / 4 minutes off duty cycle, so each duty "on"
  phase is one file.
- Gain: 6, 12, 18, or 24 dB (default 18) [UG p.45].
- **TODO: confirm from first real card:** bit depth (expected 16) and channel count (expected
  1); the `fmt ` chunk is authoritative. Intake flags anything else.

## Header Metadata

### GUANO [UG pp.93–95]

The recorder writes GUANO metadata (spec version 1.0; a `guan` RIFF chunk holding UTF-8
`key: value` lines, with namespaces separated by `|`). Fields documented for the Song Meter
Micro 2:

| GUANO field | Meaning [UG p.94–95] | Mapped to |
|---|---|---|
| `Firmware Version` | Firmware at time of recording | `firmware_version` |
| `Length` | Duration in seconds | checked against frames / sample rate (tolerance 1 frame) |
| `Loc Position` | Latitude and longitude saved to the recorder | `device_position` (exact; position check only) |
| `Make` | "Wildlife Acoustics, Inc." | `device_make` |
| `Model` | Model name | `device_model` |
| `Original Filename` | Name as first saved to the card | checked against the actual basename |
| `Samplerate` | Hz | checked against the `fmt ` chunk |
| `Serial` | Serial number | `device_serial` |
| `Temperature Int` | Internal temperature, °C | `temperature_c` |
| `Timestamp` | Start date and time, with the UTC offset set in the recorder's Location & Time Zone screen | `start_local`, `utc_offset_minutes` |
| `WA\|Song Meter\|Audio settings` | Array with `rate` and `gain` (dB) | `vendor_fields` |
| `WA\|Song Meter\|Prefix` | Recorder name | `recorder_name` |

GUANO's `Timestamp` is ISO 8601 and may include a UTC offset (GUANO spec). The adapter parses it
with `datetime.fromisoformat`. A timestamp without an offset sets `utc_offset_minutes = None`,
and intake falls back to the deployment offset.

**TODO: confirm from first real card:**

- The exact GUANO key spellings and order (the guide lists display names, e.g. "Firmware
  version"; the GUANO standard keys are `Firmware Version`, `Loc Position`, `Original
  Filename`, `Samplerate`, `Temperature Int`). The parser matches keys case-insensitively and
  records unmatched keys in `vendor_fields`.
- The exact `Timestamp` string format and how the offset is written.
- The `WA|Song Meter|Audio settings` value syntax.

### Additional vendor metadata [UG p.94]

"Full-spectrum .wav files include additional metadata not shown in the GUANO fields". The
desktop Mini/Micro Configurator displays the recorder's settings and schedule from a recording.
The guide doesn't say where this is stored. **TODO: confirm from first real card:** the RIFF
chunk ID(s) carrying it. The adapter doesn't interpret it. `header_chunks` returns every
non-`data` chunk verbatim, so it's preserved in the archive sidecar whatever its format.

### RIFF parsing

`urbanbio.ingest.riff` walks the RIFF chunk list itself (chunk ID, little-endian size, data,
pad byte for odd sizes) and returns each chunk with its byte offset. It doesn't rely on
libsndfile for metadata, because libsndfile drops unknown chunks. Samples are read with
`soundfile`.

## Summary File [UG pp.95–96]

`<RECORDER NAME>_Summary.txt`: comma-separated text. A line is written "for every minute the
Song Meter Micro 2 is awake and recording". Column headers are on the first line, and the
header row repeats each time the recorder powers on and starts its schedule (after a manual
restart, a momentary battery failure, or a reboot). Documented columns:

| Column | Meaning | Parsed as |
|---|---|---|
| `DATE` | Date the line was written | part of `time_local` |
| `TIME` | Time of day the line was written | part of `time_local` |
| `LAT`, `NS` | Latitude value and hemisphere | exact position; position check only |
| `LON`, `EW` | Longitude value and hemisphere | exact position; position check only |
| `POWER(V)` | Battery voltage, volts | `battery_v` |
| `#FILES` | Full-spectrum `.wav` files that finished during the preceding minute | `files_completed` |

Parsing rules:

- Split the file into sessions at each header row. Each session after the first counts as a
  reboot.
- Map columns by header name, not position. Unknown columns go to `extra` verbatim.
- `DATE` and `TIME` are device-local with no offset in the file. The adapter returns them as
  naive local times, and intake converts them using the offset of the audio files from the same
  power-on session ([intake](../intake/intake-design.md) § Ingest Flow, step 5).
- **TODO: confirm from first real card:** `DATE` and `TIME` formats, whether `LAT`/`LON` carry
  signs as well as `NS`/`EW`, and line endings. Until confirmed, a row that doesn't parse
  flags the device log (not the audio files) with `summary_unparseable`.
- Low-battery anomaly threshold: 4.2 V (configurable). The recorder shuts down at about 3.7 V
  with alkaline cells [UG p.112], and lithium cells may fail at higher voltages.

The summary file includes the deployment's exact coordinates, so it's archived only to the
private bucket and never parsed into a persisted table beyond battery and file counts.

## Diagnostics Files [UG p.93]

`<RECORDER NAME>_<DATE>_<TIME>.minidiags` files are written after unexpected reboots or on
request. They are read by the desktop Configurator. The adapter records their presence and
timestamps as `reboot` anomalies and archives them verbatim without parsing.

## Clock and Time Zone [UG pp.30–31, 74]

- The internal clock is set from the phone when the recorder pairs with the Song Meter
  Configurator app. A backup battery keeps it running during battery swaps.
- The recorder's time zone is a fixed offset from UTC. "The Song Meter Micro 2 cannot update
  its own Time Zone setting mid-deployment, so you should choose to use either standard or
  daylight time for the duration of the deployment" [UG p.30].
- Consequence: filenames and GUANO timestamps use one offset for the whole deployment, so a
  deployment spanning a daylight-saving change (e.g., US DST ends 2026-11-01) has no repeated or
  missing local hour. Intake converts using the recorded offset and never applies DST rules
  ([intake](../intake/intake-design.md) § Time Normalization).
- Project preference: set the recorder to UTC (offset 0) if the Configurator's time-zone list
  offers it. Otherwise use the phone's zone (America/New_York) and rely on the recorded offset.
- **TODO: confirm from first real card:** whether the Configurator offers UTC; how a
  deployment configured in daylight time and retrieved after the change is recorded (the
  offset in GUANO for files on both sides of 2026-11-01); and whether re-pairing at a
  retrieval changes the offset for subsequent files. Each finding becomes a synthetic fixture
  test.

## Interface Implementation Summary

| Method | Behavior |
|---|---|
| `list_media(root)` | `Data/*.wav` → audio; `*_Summary.txt` at root → logs; `*.minidiags` → diagnostics; everything else → unknown |
| `read_audio_metadata(path)` | Filename regex + RIFF/GUANO parse + cross-checks: filename vs `Original Filename`, filename time vs GUANO time, `Samplerate` vs `fmt `, `Length` vs frames, filename prefix vs `WA\|Song Meter\|Prefix` |
| `read_device_log(listing)` | Parse the summary file into sessions and rows |
| `header_chunks(path)` | All non-`data` RIFF chunks, verbatim, with offsets |

Cross-check failures raise `AdapterError` with reason codes `filename_unparseable`,
`guano_missing`, `guano_missing_timestamp`, `original_filename_mismatch`, `timestamp_mismatch`,
`samplerate_mismatch`, `length_mismatch`, or `recorder_name_mismatch`.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Metadata source of truth | GUANO header, cross-checked with the filename | Filename only | GUANO carries the UTC offset, serial, and firmware. The filename is a second, independent witness. |
| GUANO parsing | Own parser over raw RIFF chunks | The `guano` PyPI package | The adapter already walks RIFF chunks for verbatim preservation. GUANO's text format is small, and one parser avoids a second reading path. |
| Unknown vendor metadata | Preserve verbatim, don't interpret | Reverse-engineer the format | Undocumented. Verbatim preservation meets HLD §5.2 without inventing a format. |
| Strictness on undocumented details | Flag on anything unexpected | Lenient parsing with defaults | Tenet 3. The first real cards settle the TODOs, then the checks become exact. |

## Open Questions & Future Decisions

### Deferred (all resolved from the first real cards)
1. Bit depth, channel count, and extension case of audio files.
2. Exact GUANO key spellings, `Timestamp` format, and `Audio settings` syntax.
3. RIFF chunk ID(s) holding the additional settings and schedule metadata.
4. Summary file `DATE`/`TIME` formats and coordinate sign conventions.
5. Whether UTC is selectable as the recorder time zone; offset behavior across the 2026-11-01
   DST change; offset behavior after re-pairing.
6. Whether a file in progress when the batteries fail is truncated, and how it appears in
   GUANO `Length`.

## References

- Wildlife Acoustics, *Song Meter Micro 2 User Guide*, 2025-08-22 edition:
  https://www.wildlifeacoustics.com/uploads/user-guides/Song_Meter_Micro_2_User_Guide_en.pdf
  (HTML version: https://www.wildlifeacoustics.com/uploads/user-guides/html/Micro2-HTML5/en/sd-card-contents.html)
- GUANO specification v1.0: https://github.com/riggsd/guano-spec/blob/master/guano_specification.md
