# Analytics & learning workflow (fixture-first → report → inert proposal → approval)

Analytics runs **offline by default**. The bundled `examples/analytics_fixture.json` is illustrative
data, never verified channel performance. Only a live YouTube Analytics API result
(`source: youtube_analytics_api`) is labelled `verified: true`. Never present a fixture number as a
measured fact, and never describe a finding as a cause or a prediction.

## 1. Ingest a report

```bash
creator analytics refresh --json                       # bundled fixture (offline)
creator analytics refresh --from-file report.json --json
creator analytics refresh --live --start-date 2026-10-01 --end-date 2026-10-31 --json
```

- The default fixture transport requires no network, credentials or optional dependencies.
- `--from-file` accepts either an API-shaped report (`reports[].columnHeaders` + `data.rows`) or a
  simplified `{"videos": [{"video_id", "views", ...}]}` fixture.
- `--live` requires the `[youtube]` extra, stored OAuth credentials with the
  `yt-analytics.readonly` scope, and an explicit date range. It **fails closed** (exit 3/4) if any
  prerequisite is missing — it never falls back to the fixture. `youtube connect --live` requests
  this scope alongside the YouTube scopes; tokens stored before Stage 12G must reconnect once.
- The normalized snapshot is written to `.creator/analytics/snapshots/` and records `source`,
  `verified`, dates, per-video metrics and median `baseline`.

## 2. Build a cited performance report

```bash
creator analytics report --json                        # latest snapshot
creator analytics report --prior <older-snapshot.json> --json
```

- Every finding cites the **metric value, sample median, sample size, source and provenance**.
- Findings require at least 3 videos and a ≥20% difference from the sample median.
- Comparisons use an earlier snapshot (`--prior`) or the creator profile baseline, each labelled
  with its own source.
- Output is both markdown (written to `.creator/analytics/reports/`) and structured JSON.
- Interpretations are always `hypothesis` with association-only wording. The report explicitly
  disclaims causation, prediction, virality and revenue claims. Tags are not returned by the
  Analytics API; tag-based hypotheses need a separate local channel snapshot.

## 3. Propose learning (inert)

```bash
creator analytics learn --json
```

- Proposals are deterministic, bounded scorer-weight hints with attached evidence and are labelled
  `hypothesis`.
- `learn` stages the proposal into `profile.memory.pending_changes` but changes **nothing** else.
  Repeated calls reuse the pending change rather than accumulating duplicates.
- Missing tag data never penalizes audience fit. Volatility is measured from per-video dispersion,
  not from report extremes.
- Review the proposals and their evidence with the creator before applying anything.

## 4. Apply only with explicit approval

```bash
creator analytics learn --apply --approve --json
```

- `--approve` issues a fresh HMAC bound to the action, target and complete staged change, and
  consumes it once. Omitting it exits 5 (`approval_required`); a forged/expired/changed/replayed
  token exits 5 (`approval_invalid`). Nothing is applied.
- On success the approved weights are written to `research/scoring_config.json`, which
  `creator ideas` already loads — so the feedback loop is real, but only after a human authorizes it.
- The pending change is cleared, the profile memory records the applied value, and only the change
  id and weights enter the audit log (never token values).

## 5. Inspect state

```bash
creator analytics status --json
```

Lists snapshots, reports and pending learning changes. It is a local read-only view.
