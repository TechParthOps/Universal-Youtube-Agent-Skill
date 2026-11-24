# Editing & revision workflow

This reference is loaded only for edit/review requests. Core rules from SKILL.md still apply.

## Source of truth: the timeline (EDL)

Every edit is an **immutable** versioned JSON document at
`<workspace>/.creator/projects/<project_id>/timeline_vNNN.json` (with a `timeline_latest.json`
pointer). It records sources with SHA-256 hashes, clip in/out points, effects, music, captions,
removed ranges, render settings, and provenance for each decision. Original files are read-only
inputs, never overwritten, and are re-hashed before every render.

## Standard sequence

1. **Understand**: `creator media inspect <file> --json` → format/codecs/duration;
   `creator media analyze <file> --json` → silence, scenes, audio events, optional transcript.
2. **Search** (optional): `creator media search "<natural language>" --json` → ranked timestamps +
   evidence + confidence. Verify results before using them.
3. **Draft**: `creator edit new --source <file> --strategy gaming --concept "…" --json`.
   Choose the strategy that matches the creator profile/category; do not spray arbitrary effects.
4. **Render**: `creator edit render <project> --json`. Core builds the FFmpeg graph, renders to
   `renders/edit_vNNN_rNNN.mp4`, and writes `renders/edit_vNNN_rNNN_qa.json` plus a reproducibility
   manifest. Output names are exclusive; existing renders are never overwritten.
5. **Review**: `creator edit preview <project>` opens a loopback-only local review UI with video,
   timeline lanes, captions, removed-footage list, A/B compare, QA report, and revision composer.
6. **Revise**: `creator edit revise <project> "make the first 20s more exciting" --json` → explicit
   EDL diff → new version. Use `--plan-only` to inspect first, `--queue` to store a request from
   the preview UI, and `--pending REV_ID` to apply it. Never mutate v001 in place.
7. **Export**: `creator edit exports <project> --json` lists renders and QA readiness. Publishing
   still requires fresh explicit creator approval; export manifests reference the exact version,
   render settings, and source hashes.

## Translating natural-language revisions

| Request | Expected EDL operation |
|---|---|
| "remove the unnecessary waiting sections" | add removed ranges for silent/low-energy spans; rebuild video+audio tracks |
| "make the first 20s more exciting" | tighten clip durations in 0–20s, add supported zoom/caption emphasis, shorten transitions |
| "add stronger subtitles to important moments" | caption cues on high-energy/high-confidence ranges with emphasized style |
| "reduce background music during my commentary" | lower music `gain_db` or set `duck_to_db` lower on ranges with speech |
| "restore the part I deleted at 1:20" | remove the matching entry from `removed_ranges` and reinsert its clip |

Prefer partial regeneration of affected segments. If a request is subjective or ambiguous, ask
the creator rather than silently changing their editing profile.

## QA gates

Before publishing: no black/frozen tails, audio present, no clipping, A/V sync spot-check passed,
subtitles within bounds, aspect ratio correct, output decodes cleanly, source hashes unchanged.
Auto-fixes (safe: trailing black removal, normalization) are logged; subjective changes require
approval. A failed QA report blocks export — report it plainly, do not hide it.
