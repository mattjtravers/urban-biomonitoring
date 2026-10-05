---
parent: high-level-design
prefix: VALID
---

# Validate

## Context and Design Philosophy

Validate turns raw detector confidence into measured, per-species precision (HLD G2, §3.4).
Reviewers label a stratified sample of detections, and a logistic model per species and model
version maps confidence to P(correct). Species without enough labels are carried as
*unvalidated*, never dropped or trusted silently.

**Phase 1 scope: interfaces only.** This LLD fixes the contracts that detect and curate rely
on. Its specs are deferred until the first detections exist.

Package: `urbanbio.validate`.

## Interfaces

```python
class Sampler(Protocol):
    def draw(self, detections: Table, *, model_id: str, per_stratum: int, seed: int) -> ReviewBatch: ...
    # Strata: taxon × confidence bin (default bins: [0.10, 0.25), [0.25, 0.50), [0.50, 0.75), [0.75, 1.00]).
    # Only target taxa with in_geo_list or a minimum detection count; never detections with overlaps_mask.

class ReviewTool(Protocol):
    def export(self, batch: ReviewBatch, clips: ClipSource) -> Path: ...   # task file for the tool
    def import_labels(self, path: Path) -> list[Label]: ...

class Calibrator(Protocol):
    def fit(self, labels: Sequence[Label], detections: Table, *, taxon_key: str,
            model_id: str, target_precision: float) -> Calibration: ...
    # Logistic regression of correct ~ logit(confidence) (Wood & Kahl 2024).
    # threshold_at_target = confidence where fitted P(correct) = target_precision, or None.

class AgreementAnalyzer(Protocol):
    def compare(self, a: DeploymentRef, b: DeploymentRef, *, window_s: float) -> AgreementReport: ...
    # For co-located recorders: per-taxon agreement over matched time windows.
```

Review clips are cut from **archived masked audio** around each detection (the 3 s window plus
1.5 s of context each side) and stored privately at `clips/<detection_id>.flac`. `unsure`
labels are excluded from fitting. A calibration needs at least `min_labels` (default 50, at
least 10 of them `correct`) or the taxon stays `unvalidated`.

## T3 Resolution (HLD §10): Labeling Tool

**Evaluate Whombat first**, with Label Studio as the alternative and a minimal local UI only if
both fail. Criteria: spectrogram plus audio playback of clips, a correct/incorrect/unsure
verdict with a true-species field, file-based import/export with no cloud service, and running
locally on the reviewer's host. Review uses only masked clips from the store, so it may run on
any host configured with the `processing` profile and access to the store; it never reads the
quarantine. A review-only host is installed like any processing host
([install](../install/install-design.md)), so `config check` passes, and its quarantine
simply stays empty.

| Candidate | License | Notes |
|---|---|---|
| Whombat (v0.9.0, 2026-05) | GPL-3.0 | Built for bioacoustic annotation (spectrograms, clip review). Run as a separate tool, so its license doesn't affect this MIT code. |
| Label Studio (1.23.2, 2026-09) | Apache-2.0 | General-purpose with audio templates. Heavier; spectrogram support is less specialized. |
| Minimal local UI | MIT (ours) | Fallback. Costs build and maintenance time on a part-time project. |

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Calibration model | Logistic on logit(confidence), per taxon × model | Fixed global threshold; isotonic regression | Follows Wood & Kahl (2024) as the HLD specifies. Isotonic needs more labels per species than a small project collects. |
| Review clip source | Archived masked audio | Quarantine originals | Originals are purged within days. Masked audio is the reprocessing source (HLD §5.2). |

## Open Questions & Future Decisions

### Deferred
1. All VALID specs, until the first detections exist.
2. Target precision default (0.9 proposed) and confidence-bin edges.

## References

- HLD §3.4, G2, T3
- Wood, C. M., & Kahl, S. (2024). Guidelines for appropriate use of BirdNET scores and other
  detector outputs. *Journal of Ornithology* 165(3):777–782.
- Whombat: https://github.com/mbsantiago/whombat
- Label Studio: https://github.com/HumanSignal/label-studio
