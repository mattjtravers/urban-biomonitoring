---
parent: high-level-design
prefix: INGEST
---

# Ingest

## Problem

Recorders write media in device-specific formats: file naming, header metadata, logs, and
clock conventions all differ by vendor. Downstream stages need one contract (HLD §4) regardless
of device (HLD G5), and the first thing that touches the media must fix its identity with a
checksum (HLD §3.1).

## Approach

Ingest has two parts, each a child LLD:

- **[intake](intake/intake-design.md)** (`INGEST-INTAKE`): device-independent. It owns the
  quarantine, hashes every file, assigns IDs, normalizes time, records the MediaRetrieval, and
  later purges verified originals.
- **Recorder adapters**, one per device family, behind the interface below. The first is
  **[songmeter-micro2](songmeter-micro2/songmeter-micro2-design.md)** (`INGEST-SM2`).
  AudioMoth will be a sibling folder.

Intake calls the adapter. The adapter never touches AWS S3, the ledger, or other files.

## Recorder Adapter Interface

```python
class RecorderAdapter(Protocol):
    key: ClassVar[str]                  # e.g. "songmeter-micro2"
    version: ClassVar[int]              # bumps when parsing output changes (feeds stage_version)

    def list_media(self, root: Path) -> MediaListing:
        """Classify every file under root as audio, device log, diagnostic, or unknown."""

    def read_audio_metadata(self, path: Path) -> AudioFileMeta:
        """Parse one audio file's name and header. Raises AdapterError on inconsistency."""

    def read_device_log(self, listing: MediaListing) -> DeviceLog | None:
        """Parse the device's status log, if the device writes one."""

    def header_chunks(self, path: Path) -> list[RawChunk]:
        """Every non-audio container chunk, verbatim, for the archive sidecar."""
```

Data passed across the interface (Pydantic models in `urbanbio.ingest.adapters.base`):

- `MediaListing`: `audio: list[Path]`, `logs: list[Path]`, `diagnostics: list[Path]`,
  `unknown: list[Path]`.
- `AudioFileMeta`: `original_filename`, `start_local: datetime` (naive), `utc_offset_minutes:
  int | None`, `sample_rate_hz`, `channels`, `bit_depth`, `frames`, `device_serial: str | None`,
  `device_make`, `device_model`, `firmware_version: str | None`, `recorder_name: str | None`,
  `temperature_c: float | None`, `device_position: tuple[float, float] | None`,
  `vendor_fields: dict[str, str]`.
- `DeviceLog`: `sessions: list[LogSession]` (one per power-on), each with `rows:
  list[LogRow]` (`time_local`, `battery_v`, `files_completed`, `extra: dict[str, str]`).
- `RawChunk`: `chunk_id: str` (four characters), `offset: int`, `size: int`, `data: bytes`.

`device_position` exists so intake can check it against the deployment's site. It is exact,
so intake uses it only for that comparison and never persists it outside the private archive
sidecar (HLD §6.2).

Adapters are pure parsers: no clock correction and no UTC conversion. Those are intake's job,
so every device follows the same normalization rules.

## Image Branch Seam

Camera traps (HLD §7) reuse Site, Deployment, MediaRetrieval, Run, and intake's quarantine,
hashing, time normalization, and purge. They differ only in the media contract:

```python
class CameraAdapter(Protocol):
    key: ClassVar[str]
    version: ClassVar[int]
    def list_media(self, root: Path) -> MediaListing: ...           # images/videos instead of audio
    def read_image_metadata(self, path: Path) -> ImageFileMeta: ...  # EXIF time, offset, serial, trigger
    def header_chunks(self, path: Path) -> list[RawChunk]: ...       # EXIF/maker notes verbatim
```

The image counterpart of the speech screen is a person filter (MegaDetector or SpeciesNet
person class) that blurs or withholds images before anything leaves the quarantine. Its output
maps to Camtrap DP `media` and `observations` through the field alignment in
[data-model](../data-model/data-model-design.md). The branch gets its own LLDs when it starts.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Adapter selection | Deployment declares its adapter | Auto-detect from card contents | Explicit is unambiguous. A Song Meter Micro and Micro 2 share firmware and formats, so detection could guess wrong silently. |
| Time normalization owner | Intake | Each adapter | One implementation of DST, offset, and drift rules for every device. |
| Sub-HLD split | Intake + one leaf per adapter | One ingest LLD with adapter sections | Adapters are distinct intents that grow independently (AudioMoth next); each owns its own specs and TODO list. |

## References

- HLD §3.1, §4, §7
