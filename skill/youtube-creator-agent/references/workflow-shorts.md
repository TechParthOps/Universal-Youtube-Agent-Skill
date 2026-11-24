# Shorts workflow (draft → review → approval)

Shorts generation is a **local drafting** workflow. It never contacts YouTube and never publishes.
A Short is a new file; the source is read-only.

## 1. Prepare

```bash
creator doctor --json                 # FFmpeg/FFprobe required (exit 3 → relay hint, stop)
creator init --json                   # once per project directory
creator shorts generate raw.mp4 --project launch --target 8 --limit 2 --no-provider --json
```

- `--target` is seconds (6–60); 8–30 is a practical Short. `--limit` renders at most 5 drafts.
- `--focus auto` decodes low-resolution frames, fits a motion centroid and animates a 9:16 crop.
  Use `--focus center` for a full frame over a blurred background (no naive centre crop).
- `--transcribe` uses optional ASR for transcript-backed captions; without it, captions come from
  the generated hook and the source is recorded as template. No transcript is invented.
- Omit `--no-provider` only when the user has configured a text provider; fallback is always safe
  and names the template as the provider with `model: null`.

## 2. Interpret the output

Each entry in `shorts[]` carries `short_id`, `duration_seconds`, `width`/`height` (1080×1920),
`focus_mode`, `qa_ok`, `captions_burned`, `hook_provider`, `score` and paths to the MP4, QA report
and `timeline.json`. Read the timeline to inspect:

- `candidate.score_parts` and `signals` — transparent heuristic components with provenance.
  Scores are **not** predictions of views or virality; say so plainly.
- `reframe` — `mode` (`crop_track` / `blur_pad`), `focus_window`, `trajectory`, `model`.
- `captions.source` — `transcript` or `hook template (no transcript available)`.
- `qa.needs_approval` — objective findings; `av_sync` always needs a human watch.

## 3. Review and approve

```bash
creator shorts review launch --json
# human watches the draft, then deliberately:
creator shorts review launch --short short_001 --approve --json
```

Approval is a creator action: never pass `--approve` on the user's behalf without an explicit,
unambiguous confirmation. Missing/invalid tokens exit `5`. The audit log stores only a token
fingerprint. Approved timelines set a publish gate requiring a published parent video and a fresh
upload token; the publishing phase re-checks it immediately before upload **and** publish. Stop at
approval unless the user has explicitly asked to publish (see the publishing workflow).

## Do not

- Do not claim a model generated hooks when `hook_provider` is `template`.
- Do not present a candidate score as a virality prediction.
- Do not call a Short published — this phase only produces local drafts.
- Do not skip the human watch: QA is objective and `av_sync` is explicitly `needs_approval`.
