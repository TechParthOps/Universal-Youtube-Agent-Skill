# Publishing workflow (stub-first → private → verified → public)

Publishing is **private-first** and approval-gated. The default transport is an offline stub: no
command contacts YouTube unless the user explicitly opts into live mode. Never present a stub
result (`live: false`) as a real YouTube upload.

## 1. Choose the transport

```bash
creator youtube connect --json                 # offline stub; no network (default)
creator youtube connect --live --client-secrets client_secret.json --json
```

- Stub mode is deterministic and writes its state under `.creator/publishing/stub/`. It is what all
  tests use. Its payload says `live: false` and notes `LOCAL STUB — not connected to YouTube`.
- Live mode requires the `[youtube]` extra (`pip install 'universal-creator-agent[youtube]'`), an
  OAuth client JSON from Google Cloud Console, and a consent-screen approval. Google libs are
  imported lazily; if missing, exit 3 with the install hint. Never claim a live connection that
  did not happen.

## 2. Upload (private, never public)

```bash
creator youtube upload --project launch --source render.mp4 --title "My video" \
  --description "…" --tags gaming test --approve --json
```

- `--approve` issues a fresh HMAC bound to the action, target and full request (title, privacy,
  tags, media hash, …) and consumes it once. A forged/expired/changed/replayed token exits 5
  (`approval_invalid`); omitting it exits 5 (`approval_required`). Nothing is uploaded on failure.
- The result is `state: processing`, `privacy: private`, `live: false/true`. `--privacy public` is
  rejected: public release is always a separate step.
- Scheduling: add `--publish-at 2026-12-01T17:00:00Z` (ISO-8601, UTC). This stores a private video
  with YouTube's `publishAt`; state becomes `scheduled` after verification.
- Interrupted uploads resume from the last reported byte offset automatically; the source file is
  never modified and its SHA-256 is recorded in `.creator/publishing/videos/<id>.json`.

## 3. Verify, then publish

```bash
creator youtube verify --video-id stub_abc123 --json
creator youtube publish --video-id stub_abc123 --approve --json
```

- `verify` polls the remote status (a `videos.list` quota charge) and advances `processing →
  private`/`scheduled`. Publishing while still `processing` is rejected with a hint to verify.
- `publish` needs its **own** fresh approval token and charges `videos.update`. Only then does state
  become `public`. Confirm playback and monetization in YouTube Studio afterwards.

## 4. Shorts parent gate

An approved Short (`creator shorts review <project> --short short_001 --approve`) additionally
requires `--parent <video_id>` at upload, and the parent must already be in state `public`. The gate
is re-checked immediately before upload **and** publish: if the parent was demoted in between,
release is blocked and no state changes.

## 5. Thumbnail and quota

```bash
creator youtube thumbnail --video-id stub_abc123 --image cover.jpg --approve --json
creator youtube status --json
creator youtube quota --json
```

Thumbnails need their own approval, must be ≤2 MB and charge `thumbnails.set`. The quota ledger
uses documented Data API v3 units (insert 1600, update/thumbnail 50, list calls 1), checks before
each call and refuses an operation that would exceed the daily ceiling (exit 7). Usage is UTC and
resets automatically.

## Do not

- Do not pass `--approve` without an explicit, unambiguous instruction from the user.
- Do not call a stub run a live upload, or a scheduled video "public" before verification.
- Do not upload a Short without its approved review and a public parent.
- Do not fabricate quota numbers: read them from `creator youtube quota --json`.
