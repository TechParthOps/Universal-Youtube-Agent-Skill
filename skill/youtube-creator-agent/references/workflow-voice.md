# VoiceDNA narration workflow

This workflow adds consented voice narration to the existing edit pipeline. It is **additive**:
`WorkspacePaths` is unchanged, EDL v1 tracks are never rewritten, and the base render stays on disk.
For design and licence detail see `voice-architecture.md`, `voice-model-comparison.md`, `voice-consent-policy.md`,
`voice-security.md` and `voice-api-contract.md` in this skill's `references/` directory.

## Preconditions

1. `creator doctor --json` passes (Python, FFmpeg, FFprobe).
2. The user explicitly authorises use of their own voice (or a voice they hold written rights to).
   Never proceed for a third party, a celebrity or an impersonation request.
3. A clean reference WAV is inside the workspace (or a registered media root).
4. A project with a real `creator media analyze` report exists. `voice generate` refuses reports
   without measured source duration and detector signals — a concept alone is not enough.

## Step-by-step

```bash
# 0. Workspace + real footage analysis
creator init --json
creator media analyze CLIP --project PRJ --json
creator edit new --source CLIP --project PRJ --strategy gaming --concept "..." --json
creator edit render PRJ --json

# 1. Provider survey (never downloads or runs a model)
creator voice providers --json
creator voice languages --json

# 2. Register the creator's own voice (approval + explicit consent)
creator voice setup --owner OWNER --reference REF.wav --display-name "Creator" --confirm-voice-ownership --approve --json
creator voice register --owner OWNER --reference REF.wav --display-name "Creator" --purpose narration --confirm-voice-ownership --approve --json
creator voice inspect --owner OWNER --profile PROFILE --json

# 3. Generate narration (footage-grounded, HMAC-approved)
creator voice generate --owner OWNER --profile PROFILE --project PRJ \
  --concept "Explain the level transition" --approve --json

# 4. Align + compose additive EDL metadata and captions
creator voice align --owner OWNER --profile PROFILE --json
creator voice compose --owner OWNER --profile PROFILE --project PRJ --json

# 5. Non-destructive mix (real FFmpeg delay + ducking; base render untouched)
creator voice mix --project PRJ --dry-run --json    # inspect the plan first
creator voice mix --project PRJ --json

# 6. Preview, targeted regeneration, revision
creator voice preview --project PRJ --json
creator voice preview --project PRJ --segment seg_001 --json
creator voice regenerate --owner OWNER --profile PROFILE --segment seg_001 \
  --text "A revised line" --approve --json
creator edit revise PRJ "shorten the intro" --approve --json   # existing revision path
creator voice align --owner OWNER --profile PROFILE --json
creator voice compose --owner OWNER --profile PROFILE --project PRJ --json
creator voice mix --project PRJ --json

# 7. QA + disclosure — both must be clear before publishing
creator voice qa --project PRJ --json
creator voice disclosure --project PRJ --json
creator voice disclosure --project PRJ --approve --json

# 8. Only then use the normal private-first publishing workflow.
```

## Hybrid narration

For footage that mixes creator speech, gameplay and silent reactions:

```bash
creator voice hybrid --project PRJ --window 2.0 --json
creator voice hybrid --project PRJ \
  --actions '{"sec_001":"preserve","sec_002":"insert"}' --json
```

Actions are `preserve` (keep original audio), `insert` (add narration), `replace` or `silence`.
Original sections are preserved by default. Every action is stored in the additive EDL `voice.mix`
object and applied in the non-destructive mix pass; no source track is overwritten.

## Failure and honesty rules

| Situation | Expected result | What to tell the user |
|---|---|---|
| No consent or consent revoked | exit 4, no provider called | Ask for explicit consent; rerun `voice consent grant --confirm-voice-ownership --approve` |
| Neural provider unavailable | exit 3, no download | Relay the licence/install hint; do not claim inference ran |
| Disallowed licence | exit 2 | Explain the licence gate; do not add `--allow-noncommercial` without the user's explicit acknowledgement |
| Missing approval | exit 5 | Explain the exact request (shown in the error) and ask for confirmation |
| `mocked=true` in QA | `needs_approval`, publication blocked | `test_tone` is tests-only tone, not speech — never publish it |
| Narration timeline longer than render | QA `needs_approval` | Shorten the concept/script or choose faster speech; do not suppress the check |
| `label: estimated` in alignment | normal | FFmpeg measures speech/pause windows from the WAV; word boundaries are still allocated by character count inside those windows, not measured word timing |

## Revocation and deletion

```bash
creator voice consent revoke --owner OWNER --profile PROFILE --purpose narration --approve --json
creator voice delete --owner OWNER --profile PROFILE --approve --json
```

Revocation is immediate and irreversible without a new explicit grant. Deletion removes the owner's
profile versions, consent record, reference copy, generation history and all generated audio. Existing
renders are not modified; a project's additive `voice` metadata is a historical record, not a live
capability. Confirm the user understands this is permanent before running `delete --approve`.

## Full acceptance demo

`python scripts/voice_acceptance_demo.py --json` runs every stage above on locally generated media and
prints a JSON trace with measured hashes, durations and QA results. It uses a real Windows SAPI voice
when installed; otherwise it falls back to `test_tone` and labels the output `mocked=true`. It never
uploads anything.
