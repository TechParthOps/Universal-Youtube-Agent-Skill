# Third-Party Notices

This file lists the third-party components that `universal-creator-agent` depends on, links to,
or bundles, and the notices that must be disclosed with the distribution.

`universal-creator-agent` itself is licensed under the MIT License — see `LICENSE`.

## 1. Runtime dependencies (declared in `pyproject.toml`)

| Package | Version constraint | License | Role |
|---|---|---|---|
| `requests` | `>=2.31` | Apache-2.0 | HTTP client for optional YouTube Data/Analytics API adapters. The core local-first workflow (research, media, editing, render, export) does **not** require network access. |

`requests` transitively depends on `urllib3`, `certifi`, `idna` and `charset-normalizer`. Each
carries its own license (`idna`/`urllib3`: MIT; `certifi`: MPL-2.0; `charset-normalizer`: MIT).
Because all constraints are open lower bounds, a fresh install resolves currently fixed upstream
versions. See `FINAL_DEPENDENCY_AUDIT.md` for the audit that was executed.

No other package is installed by the core distribution. All optional capabilities are opt-in
extras and are **not** installed by default.

## 2. Optional dependencies (extras — installed only on explicit user request)

| Extra | Packages | License notes |
|---|---|---|
| `transcribe` | `faster-whisper>=1.0` | MIT; optional/local ASR. |
| `youtube` | `google-api-python-client>=2.100`, `google-auth-oauthlib>=1.1`, `google-auth-httplib2>=0.2`, `keyring>=24` | Apache-2.0 (Google libs), MIT (keyring). Required **only** for live YouTube Data API publishing. |
| `diarize` | `pyannote.audio>=3.1` | MIT code; model weights are subject to their own upstream model licences. |
| `voice-neural` | `torch>=2.0`, `torchaudio>=2.0`, `chatterbox-tts>=0.5` | see §3. |
| `dev` | `pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.5` | MIT. Development/test only. |

## 3. Neural voice runtimes and model weights

**This project bundles no model weights, checkpoints, or audio data.** No checkpoint is downloaded
automatically at any point.

Neural voice providers (OpenVoice v2, Chatterbox, XTTS-v2, F5-TTS, Fish Speech) are **experimental
and fail closed** in this alpha. They additionally require owner-installed runtimes, verified
model checkpoints, license evidence, and explicit voice-owner consent — none of which are supplied
by this distribution. See:

- `VOICE_MODEL_COMPARISON.md` — licence review.
- `VOICE_MODEL_EVALUATION.md` — evaluation status.
- `VOICE_ARTIFACT_MANIFEST_SCHEMA.md` — the schema-1.0 manifest every commercial/verified use requires.
- `FINAL_VOICEDNA_ACCEPTANCE.md` — the current blocked status.

OpenVoice v2 code is MIT-licensed (https://github.com/myshell-ai/OpenVoice). The MIT licence covers
the **code only**; it does not by itself grant commercial rights in model weights. Repository code
licence ≠ checkpoint/base-speaker/dataset commercial rights. Fail closed until verified.

## 4. External programs invoked (not redistributed)

| Program | Licence | Role |
|---|---|---|
| FFmpeg / FFprobe | Primarily LGPL-2.1+ or GPL-2.0+ depending on build flags. Distributions of FFmpeg may require GPL compliance. | All encoding, decoding, analysis and rendering. **Installed by the user, never redistributed here.** |

Install a full FFmpeg build (e.g. `winget install Gyan.FFmpeg`, `brew install ffmpeg`, or
`apt install ffmpeg`) to get libx264/libass support.

## 5. Bundled assets

The clean-source ZIP / sdist bundle only assets owned by this project:

- `web/preview/{index.html,app.js,style.css}` — project code, MIT.
- `skill/youtube-creator-agent/**` — project code and documentation, MIT.
- `examples/**` — project sample data, MIT.

No third-party images, fonts, icon sets, or vendored libraries with incompatible licences are
included. `LICENSE` covers the project; this file covers what is *not* ours.

## 6. Data-source disclaimers

Research, trend, competitor and analytics features operate on **user-supplied or bundled fixture
snapshots** by default. Analytics findings are correlations with cited evidence, never predictions
or proof of cause. See `skill/youtube-creator-agent/references/workflow-analytics.md`.

## 7. No secrets or credentials are distributed

The distribution contains no OAuth client secrets, tokens, keys, machine-specific paths, or
personal data. Credentials are stored in the OS keyring at runtime, on the user's own machine,
and are never shipped, logged, or reported.