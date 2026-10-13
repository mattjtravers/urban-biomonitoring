# Song Meter Micro 2 Adapter Specs

EARS requirements for [songmeter-micro2](songmeter-micro2-design.md). Markers: `[x]`
implemented, `[ ]` active gap, `[D]` deferred.

## Media Listing

- [ ] **INGEST-SM2-LIST-001**: When the `songmeter-micro2` adapter's `list_media(root)` runs, the system shall classify `Data/*.wav` (extension matched case-insensitively) as audio, `*_Summary.txt` at the root as logs, `*.minidiags` as diagnostics, and every other file as unknown.

## Audio Metadata

- [ ] **INGEST-SM2-META-001**: When the `songmeter-micro2` adapter reads an audio file, the system shall parse its basename with `^(?P<name>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.(?P<ext>wav)$`, case-insensitively, and raise `AdapterError` (`filename_unparseable`) when it doesn't match.
- [ ] **INGEST-SM2-META-002**: When the `songmeter-micro2` adapter reads an audio file, the system shall walk the RIFF chunk list itself (chunk ID, little-endian size, data, pad byte for odd sizes), reading samples only through `soundfile`.
- [ ] **INGEST-SM2-META-003**: When the `songmeter-micro2` adapter reads an audio file, the system shall parse the `guan` chunk as UTF-8 `key: value` lines, matching keys case-insensitively, mapping the GUANO fields listed in songmeter-micro2 § GUANO, and recording unmatched keys in `vendor_fields`.
- [ ] **INGEST-SM2-META-004**: If an audio file has no `guan` chunk, then the `songmeter-micro2` adapter shall raise `AdapterError` (`guano_missing`), and if the GUANO has no `Timestamp`, `AdapterError` (`guano_missing_timestamp`).
- [ ] **INGEST-SM2-META-005**: When the `songmeter-micro2` adapter parses GUANO `Timestamp` with `datetime.fromisoformat`, the system shall return the naive local time as `start_local` and the offset in minutes as `utc_offset_minutes`, or `None` when the timestamp has no offset.
- [ ] **INGEST-SM2-META-006**: If GUANO `Original Filename` differs from the actual basename, then the `songmeter-micro2` adapter shall raise `AdapterError` (`original_filename_mismatch`).
- [ ] **INGEST-SM2-META-007**: If the filename's date and time differ from GUANO `Timestamp`'s local time by more than 1 s, then the `songmeter-micro2` adapter shall raise `AdapterError` (`timestamp_mismatch`).
- [ ] **INGEST-SM2-META-008**: If GUANO `Samplerate` differs from the `fmt ` chunk's sample rate, then the `songmeter-micro2` adapter shall raise `AdapterError` (`samplerate_mismatch`).
- [ ] **INGEST-SM2-META-009**: If GUANO `Length` differs from frames divided by sample rate by more than one frame's duration, then the `songmeter-micro2` adapter shall raise `AdapterError` (`length_mismatch`).
- [ ] **INGEST-SM2-META-010**: If the filename's recorder-name prefix differs from GUANO `WA|Song Meter|Prefix`, then the `songmeter-micro2` adapter shall raise `AdapterError` (`recorder_name_mismatch`).
- [ ] **INGEST-SM2-META-011**: The `songmeter-micro2` adapter shall take sample rate, channel count, bit depth, and frames from the `fmt ` and `data` chunks.
- [ ] **INGEST-SM2-META-012**: When `header_chunks(path)` runs on a Song Meter Micro 2 file, the system shall return every non-`data` RIFF chunk verbatim with its byte offset and size.

## Summary File

- [ ] **INGEST-SM2-LOG-001**: When the `songmeter-micro2` adapter parses a `_Summary.txt` file, the system shall start a new session at each header row and map columns by header name, putting unknown columns in `extra` verbatim.
- [ ] **INGEST-SM2-LOG-002**: When the `songmeter-micro2` adapter parses a summary row, the system shall return `DATE` and `TIME` as a naive local `time_local`, `POWER(V)` as `battery_v`, and `#FILES` as `files_completed`, and shall not return `LAT`, `NS`, `LON`, or `EW` values in any field other than `extra`.
- [ ] **INGEST-SM2-LOG-003**: If a summary row doesn't parse, then the `songmeter-micro2` adapter shall skip the row and return its line number in `DeviceLog.unparsed_rows`.
- [ ] **INGEST-SM2-LOG-004**: The `songmeter-micro2` adapter shall return `low_battery_v` from its `[ingest.songmeter_micro2]` table as `DeviceLog.low_battery_v`.

## Diagnostics Files

- [ ] **INGEST-SM2-DIAG-001**: When the `songmeter-micro2` adapter reads the device log, the system shall return each `.minidiags` file in `DeviceLog.diagnostics` with the local time from its filename (`None` when the name doesn't parse), without parsing its contents.

## Deferred

- [D] **INGEST-SM2-META-013**: When the first real cards confirm extension case, bit depth, channel count, GUANO key spellings, `Timestamp` format, and `Audio settings` syntax, the `songmeter-micro2` adapter shall match them exactly.
- [D] **INGEST-SM2-LOG-005**: When the first real cards confirm the summary file's `DATE`/`TIME` formats and coordinate sign conventions, the `songmeter-micro2` adapter shall parse them exactly.
