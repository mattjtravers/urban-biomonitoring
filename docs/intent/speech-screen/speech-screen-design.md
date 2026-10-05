---
parent: high-level-design
prefix: SCREEN
---

# Speech Screen

## Context and Design Philosophy

The speech screen keeps human speech out of long-term storage and publication (HLD G3, §3.2,
§6.1). It runs on quarantined originals, finds speech with two independent detectors, pads and
merges what they find, and produces the mask that the archive applies. Its guiding rule is
Tenet 1: **when uncertain, mask.** Over-masking costs a little bird audio. Under-masking is a
privacy failure that can't be undone once audio is shared.

The screen also owns how speech recall is measured, because a privacy control that isn't
measured can't be trusted.

Package: `urbanbio.screen`.

## Detectors

Both detectors run on every audio file. A segment is flagged if **either** fires (HLD §3.2).

Both detectors must complete on a file for it to count as screened. If either raises an error
or returns no result for a file, the unit ends with status `failed` (reason
`screen_detector_failed`), no SpeechEvents are written for it, and it can't be archived
(Tenet 1). A `failed` unit is retried automatically by the next `urbanbio screen` run
([runs](../runs/runs-design.md) § Stage-Unit Ledger).

### Detector A: BirdNET human classes (`birdnet-human`)

- Model: BirdNET acoustic V2.4 through `birdnet==1.1.1`, loaded with
  `birdnet.load("acoustic", "2.4", "tf", library="litert")`. The same model as
  [detect](../detect/detect-design.md), run in screening mode.
- Input: the original samples at 48 kHz (BirdNET's native rate, so no resampling), passed
  in memory with `predict_arrays`.
- Screening mode settings:
  - `custom_species_list = ["Human vocal_Human vocal"]` (from config `screen.birdnet_mask_labels`)
  - `default_confidence_threshold = 0.02` (`screen.birdnet_stats_floor`), so the per-file
    maximum score is known even below the masking threshold (see Screen File Stats)
  - masking threshold 0.10 (`screen.birdnet_threshold`), applied by the screen to the
    returned scores
  - `overlap_duration_s = 1.5` (3-second windows every 1.5 s), so speech straddling a window
    boundary is seen whole by some window
  - `top_k = None`, sigmoid sensitivity 1.0, bandpass 0–15,000 Hz (model defaults)
  - No location filter
- Each window at or above threshold becomes a SpeechEvent `[t, t + 3.0)` with its score.

BirdNET V2.4 also has `Human non-vocal` and `Human whistle` classes. They aren't speech and
aren't masked by default. The detect stage records them as noise covariates (HLD §3.3).

### Detector B: Silero VAD (`silero-vad`)

- Package: `silero-vad==6.2.3` (MIT). The model ships inside the package, so nothing is
  downloaded at runtime. It's run through ONNX Runtime with `load_silero_vad(onnx=True)`.
- Input: the original samples resampled from 48 kHz to 16 kHz (Silero supports 8 and 16 kHz)
  with `scipy.signal.resample_poly(x, 1, 3)`, converted to float32 in [-1, 1].
- Settings: `threshold = 0.30` (`screen.silero_threshold`, below the library default of 0.5,
  for recall), `min_speech_duration_ms = 250`, `min_silence_duration_ms = 100`,
  `speech_pad_ms = 30`, `window_size_samples = 512` (32 ms).
- Each returned speech segment becomes a SpeechEvent with its start and end in seconds. The
  score is the maximum frame probability within the segment.

### Manual events (`manual`)

Speech found by a human during recall labeling (below) enters as SpeechEvents with detector
`manual` and score 1.0. These go through the same padding and masking path, usually via
re-screen.

## Padding, Merging, and the Mask

For one file of duration `D` and sample rate `sr`:

1. Each event `[s, e)` is padded to `[max(0, s − p), min(D, e + p))` with
   `p = screen.padding_s` (2.0 s). The padded bounds are stored as `masked_start_s` and
   `masked_end_s`.
2. The mask is the union of all padded intervals (overlapping or touching intervals merge).
3. The mask is applied to the samples by zeroing indices
   `[floor(masked_start_s × sr), ceil(masked_end_s × sr))` on every channel. The array keeps
   its length, sample rate, and dtype, so every offset downstream stays valid (HLD §3.2).

Two functions construct a `MaskedAudio`, and nothing else can:

- `mask.apply_mask(original: QuarantinedAudio, events: list[SpeechEvent]) -> MaskedAudio` for
  first archive (mask version 1).
- `mask.remask(current: MaskedAudio, events: list[SpeechEvent]) -> MaskedAudio` for
  re-screening (mask version + 1). Its input is already masked, decoded from the archive and
  checked against `masked_pcm_sha256`.

 `MaskedAudio` carries `audio_file_id`, `mask_version`,
the event IDs applied (cumulative across versions), and `masked_seconds`. The archive's upload API accepts only
`MaskedAudio` ([archive](../archive/archive-design.md)), so unmasked samples can't reach
AWS S3 through it. `QuarantinedAudio` can only be loaded from a path under the quarantine root.

A file with no events still goes through `apply_mask` and produces a `MaskedAudio` with zero
masked seconds. Every archived file has been screened.

A file whose mask covers its whole duration is still archived, as all zeros. Its record
documents that the recorder ran during that minute (recording effort for curate), and the
masked fraction stays honest.

## Screen File Stats

For every screened file the screen writes one row to `screen_file_stats` (no audio):
`audio_file_id`, `run_id`, `birdnet_max_score` (highest `Human vocal` score in any window),
`silero_max_prob` (highest frame probability), `event_count`, `masked_seconds`. These scores
let speech sampling find near misses below the masking thresholds, and they show how close
unmasked files came to being masked.

## Speech-Event Log

SpeechEvents (fields in [data-model](../data-model/data-model-design.md)) are written to the
`speech_events` table, one file per screen run × retrieval. They contain offsets, detector,
label, and score: no audio and no derived features. Each run also reports per-retrieval
totals: events per detector, files with any event, masked seconds, and masked fraction of
recorded time. That's the cost side of HLD §5.2's accepted trade-off.

When more than `screen.max_masked_fraction` (default 0.20) of a retrieval's recorded time is
masked, the retrieval gets a `high_masked_fraction` anomaly. A rate that high more likely means
a detector is firing on non-speech sound (wind, rain, insects) than that the site is full of
conversation, and it calls for a look at the thresholds. Masking still proceeds (Tenet 1).

## Measuring Speech Recall

HLD §6.1 requires recall measured against a hand-labeled sample.

**Sampling.** `urbanbio speech sample --retrieval R --n N` (or `urbanbio process
--speech-sample N`) runs after screening and before archive, because archive purges each
original once it is verified. It chooses files and holds them in `quarantine/speech-labeling/` ([intake](../ingest/intake/intake-design.md)
§ Speech-Labeling Hold). Strata, equal thirds by default:

1. Files where at least one detector fired.
2. Near misses: no event, but `birdnet_max_score` ≥ 0.02 or `silero_max_prob` ≥ 0.15 in
   `screen_file_stats`.
3. Random files from the rest.

Selection uses a seeded RNG, and the seed is recorded in the run.

**Labeling.** The maintainer listens to held originals inside the Codespace and records speech
intervals in `quarantine/speech-labeling/labels.csv` (`audio_file_id, start_s, end_s,
reviewer_id`), one row per interval. A file with no speech gets one row with `start_s` and
`end_s` empty. Every held file must have at least one row before import. `urbanbio speech label --import` validates the file and writes
the `speech_labels` table (no audio), then releases the holds. Playback streams to the
maintainer's browser for listening only. Nothing is saved outside the quarantine.

**Metrics** (`urbanbio speech recall`), per stratum and weighted to the population with
stratum sizes:

- **Event recall:** labeled speech intervals with at least 90% of their duration inside the
  mask, divided by all labeled intervals. Reported with a Wilson 95% interval.
- **Time recall:** labeled speech seconds inside the mask, divided by labeled speech seconds.
- **Masked fraction:** masked seconds divided by recorded seconds (the cost).
- Contribution of each detector (events found by A only, B only, both).

**Target:** event recall ≥ 0.98, judged on the lower bound of the 95% interval, after the first
four retrievals. Below target, thresholds and padding are retuned (HLD T5) and affected
archives are re-screened.

**Misses.** A labeled speech interval outside the mask means speech is about to reach, or has
reached, the archive. Import turns it into a `manual` SpeechEvent. If the file isn't archived
yet, the archive stage includes the event in its first mask. If it is, `urbanbio rescreen`
re-masks it at once.

## Re-screening and Re-masking

`urbanbio rescreen --retrieval R` (HLD §5.2) runs on archived masked audio:

1. Download the current FLAC and verify it against the current `masked_sha256`.
2. Run the detectors with the current parameters, and add any `manual` events.
3. If the new events' padded union adds zeroed samples, build `remask(current, events)` and hand
   it to the archive as `mask_version + 1`. Otherwise record the run with no new version.

Re-masking can only add zeros. Earlier masked samples stay masked because the input is already
masked. Detections from earlier runs that overlap the new mask are excluded downstream by the
`overlaps_mask` logic in [curate](../curate/curate-design.md), and published clips from
re-masked intervals are withdrawn by [publish](../publish/publish-design.md).

## Compute

Per recorded minute, the screen runs one BirdNET pass at 1.5 s hop (about twice the windows of
detect's non-overlapping pass) and one Silero pass. A week from two recorders is about 67 hours
of audio. Throughput on the Codespace is **TODO: measure on the first real card**. The run
records units per second.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Second detector | Silero VAD 6.2.3 (MIT, model bundled, ONNX) | WebRTC VAD (GMM, older, high false-positive rate in noise); pyannote segmentation (gated model download, heavier); TEN VAD (newer, less field record) | Widely used, permissively licensed, CPU-fast, no runtime download, and independent of BirdNET's training data, which is the point of a second detector. |
| Combination rule | Either detector fires → mask | Both must fire; score fusion | HLD §3.2 and Tenet 1. Recall over precision. |
| BirdNET window overlap | 1.5 s | 0 s (detect setting) | Speech split across a 3 s window boundary is detected more reliably. Costs about 2× compute in this pass only. |
| Thresholds (T5) | BirdNET 0.10; Silero 0.30; padding ±2 s | Library defaults (BirdNET 0.25–0.5; Silero 0.5) | Recall-first starting points, tuned with measured recall and masked fraction. |
| Mask representation | Zeroed samples in place | Cut segments out; replace with noise | Zeros keep timing (HLD §3.2), are exactly reproducible, and compress well in FLAC. |
| Masking types | `QuarantinedAudio` → `MaskedAudio`, constructed only by `apply_mask` | A boolean "masked" flag on a shared type | Makes "unmasked audio never leaves quarantine" a property of the API rather than a convention. |
| Labeled sample storage | Labels (intervals) in a table; audio stays in quarantine and is purged after labeling | Keep a labeled speech corpus | A stored speech corpus would itself violate G3. |

## Open Questions & Future Decisions

### Deferred
1. Whether `Human whistle` should be masked as well as `Human vocal`. Default: not masked;
   revisit after the first labeled sample.
2. Reusing the screen's BirdNET outputs for detect on windows untouched by the mask (identical
   input gives identical output), to halve compute if throughput is a problem.
3. HLD §3.6's second speech check for published clips reuses these detectors. Its thresholds
   are set in [publish](../publish/publish-design.md).

## References

- HLD §3.2, §5.2, §6.1, T5, Tenet 1
- Silero VAD: https://github.com/snakers4/silero-vad (v6.2.3, 2026-09-23; MIT)
- `birdnet` library: https://github.com/birdnet-team/birdnet (v1.1.1, 2026-09-02)
- BirdNET V2.4 class labels (including `Human vocal`, `Human non-vocal`, `Human whistle`):
  `BirdNET_GLOBAL_6K_V2.4_Labels` in https://github.com/birdnet-team/BirdNET-Analyzer
- Wilson score interval: Wilson, E. B. (1927), *JASA* 22(158):209–212
