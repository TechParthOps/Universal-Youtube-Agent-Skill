# YouTube Integration & Agent Compatibility Validation — Stages 12G–12H

**Status:** Offline integration paths verified; all live paths remain **BLOCKED BY ENVIRONMENT**.
**Version under test:** `0.10.0a0` (contract `1.0`) — unchanged.
**Date:** 2026-11-22. **Branch:** `stage12/final-remediation` (HEAD `82017ea` at time of test edits).
**Host:** Python 3.11.9, Windows; FFmpeg/FFprobe 9.1.

---

## 1. Classification legend

| Label | Meaning in this document |
|---|---|
| **UNIT** | Pure-function / in-memory assertion. |
| **INTEGRATION** | Real CLI subprocesses, filesystem, state machine and local JSON. |
| **END-TO-END (offline)** | Full command chain through the CLI against the explicit stub transport; `live: false` in every payload. |
| **MOCKED** | Fake Google modules / stub transport. Never evidence of a real API call. |
| **REAL-PROVIDER** | Actual Google/YouTube API call with real credentials — **none performed**. |
| **BLOCKED BY ENVIRONMENT** | Library/credential/channel/host absent; reported explicitly, never skipped silently. |
| **NOT IMPLEMENTED** | Capability absent from source, not merely untested. |

---

## 2. What was actually exercised

| Area | Classification | Evidence |
|---|---|---|
| Stub transport: connect, upload, resume, status, update, thumbnail | **INTEGRATION + MOCKED (explicit stub)** | `tests/youtube/test_transport.py`, `tests/youtube/test_publish.py`, `tests/youtube/test_state.py`, `tests/youtube/test_quota.py` — 67 tests in `tests/youtube/` |
| Full CLI chain `connect → upload → verify → publish → thumbnail → quota → status` | **END-TO-END (offline), MOCKED** | `tests/test_phase8_cli.py` (7 tests); stub payloads carry `live: false` |
| Live adapter contract with fake `google*` modules | **UNIT + MOCKED** | `tests/youtube/test_transport.py::fake_google` — injects fake `google`, `googleapiclient`, `google_auth_oauthlib`; no network |
| Approval payload binding / forged / wrong-target / one-time consumption | **INTEGRATION** | `tests/security/test_audit_fixes.py`, `tests/youtube/test_publish.py` |
| Channel snapshot analysis and provenance labels | **UNIT + fixture** | `tests/youtube/test_channel.py`, `examples/channel_snapshot_fixture.json` |
| Analytics fixture ingest/report/learning; live fail-closed on missing credentials | **UNIT + MOCKED** | `tests/analytics/` 13 tests + `tests/test_phase9_cli.py` (7 tests); 128 youtube+analytics tests passed together |
| Skill structure, documented commands, installer compilation/safety | **UNIT / static** | `tests/test_skill.py` (5), `tests/test_install_security.py` (3), `tests/security/test_audit_fixes.py` (Windows symlink case) |

Result: `python -m pytest tests/youtube/ tests/analytics/ -o addopts="" -q` → **128 passed**;
`python -m pytest tests/test_phase8_cli.py tests/test_phase9_cli.py tests/test_skill.py tests/test_install_security.py -o addopts="" -q`
→ **21 passed, 1 skipped** (the skip is the documented non-Windows symlink privilege gate).
Ruff clean on all changed YouTube files.

**No test in this repository performs a real OAuth consent, Google API call, keyring write, token
refresh against Google, or real publication.** Fake Google modules are test doubles, not evidence.

---

## 3. Source defects found and fixed in Stage 12G

| ID | Defect | Fix | Verification |
|---|---|---|---|
| C-02 | `youtube.transport.SCOPES` requested only `youtube.upload` + `youtube`, while live Analytics requires `yt-analytics.readonly`; every token obtainable via `youtube connect --live` was rejected by Analytics | `src/creator_core/youtube/transport.py:21-25` now imports `ANALYTICS_SCOPE` from `analytics/transport.py` and includes it in `SCOPES` | **UNIT**: `test_connect_scopes_include_analytics_scope`; fake-module connect test asserts scopes returned. Users with old tokens must reconnect once to grant the new scope |
| L-01 | Expired token with no refresh token produced an unhandled vendor error; refresh failure was not converted into a structured auth error | `transport.py:199-212` fails closed with `AuthRequiredError` and a reconnect hint | **INTEGRATION/MOCKED**: `test_live_refresh_failure_fails_closed`, `test_live_expired_token_without_refresh_token_rejected` |
| L-02 | Retry loop passed `resume_at` but the real adapter created a fresh `MediaFileUpload`, so an interrupted live upload restarted (extra 1600 quota units, possible duplicate draft); docs claimed resume | `transport.py:250-277` captures and reuses `resumable_uri` + `resumable_progress`, refuses an offset without a session URI; `publish.py:166-180` propagates the URI | **INTEGRATION/MOCKED**: `test_live_upload_resumes_the_same_session`, `test_live_upload_refuses_resume_without_session_uri`, updated interruption test; stub-flow resume test still passes |
| L-03 | `--publish-at` accepted arbitrary strings despite documented ISO-8601 UTC requirement | `publish.py:107-117` parses with `datetime.fromisoformat`, requires an explicit zero UTC offset, and canonicalises to `...Z` | **UNIT**: 4 invalid inputs rejected, valid `+00:00` normalized |

---

## 4. Residual limitations (not silently fixed)

- **Live API verification is impossible here.** `googleapiclient`, `google`, `google_auth_oauthlib` and
  `keyring` are absent; no OAuth client-secret JSON, no test channel, no network consent flow. Live
  connect/upload/verify/publish/thumbnail and live Analytics fetch are **BLOCKED BY ENVIRONMENT**.
- **Quota accounting is incomplete for direct calls.** The ledger charges orchestrated operations
  (`videos.insert`, `videos.list`, `videos.update`, `thumbnails.set`); `channels.list` has a declared
  cost but `GoogleApiTransport.channel_info()` is never called by the publish orchestration, and
  three failed upload attempts exit before any charge. `check()` and `charge()` are separate, so the
  ledger is advisory, not an exact Google bill. Gate: **FAIL** (source-verified), real bill **BLOCKED
  BY ENVIRONMENT**.
- **Shorts parent gate is local-state-only.** `youtube/state.py:99-115` checks the parent's local JSON
  state; no remote confirmation that the parent is public on YouTube exists. This is by design in
  this release, not a claim of remote verification. Gate: **NOT APPLICABLE** for remote validation.
- **Approvals are consumed before downstream validation.** A rejected `publish` or a Short/quota gate
  failure burns the one-time token; the operator must request a fresh one. This is fail-closed, not a
  bypass, but it is a usability defect. Gate: **PASS** for safety, documented limitation.
- **Analytics scope change requires re-consent.** Existing stored tokens issued before Stage 12G do
  not contain `yt-analytics.readonly`; the live Analytics adapter will continue to reject them until
  `youtube connect --live` is rerun. Gate: **PASS** for the code path, **BLOCKED BY ENVIRONMENT**
  for actual re-consent.
- **Plaintext credential fallback** requires explicit `CREATOR_ALLOW_PLAINTEXT_CREDENTIALS=1`
  (`security/secrets.py`); it is not configured here. Gate: **NOT APPLICABLE**.

---

## 5. Agent-runtime compatibility matrix

| Runtime | Claimed in | Tested here | Gate |
|---|---|---|---|
| Generic skill structure / installer | `SKILL.md`, `scripts/install.py`, `references/agent-install.md` | `tests/test_skill.py` (frontmatter, links, documented voice commands vs real CLI help), `tests/test_install_security.py` (generic copy + symlink safety), `py_compile` | **PASS** (static/local) |
| Codex | `install.py` target | static installer contract only; `codex` binary absent | **BLOCKED BY ENVIRONMENT** |
| Claude Code | `install.py` target | static installer contract only; `claude` binary absent | **BLOCKED BY ENVIRONMENT** |
| Cursor | `install.py` target | static installer contract only; `cursor` binary absent | **BLOCKED BY ENVIRONMENT** |
| Windsurf | `install.py` target | static installer contract only; `windsurf` binary absent | **BLOCKED BY ENVIRONMENT** |
| `opencode` CLI | `install.py` target | static/symlink installer test only; `opencode` CLI binary absent (this session is not a CLI harness test) | **BLOCKED BY ENVIRONMENT** |
| `gh` publishing helper | docs | binary absent; no push/publish attempted by design | **NOT APPLICABLE** |

The repository must not claim live compatibility with any of those harnesses from these tests.

---

## 6. Gate summary

| Gate | Status |
|---|---|
| Offline stub publishing state machine, approvals, quota ledger, Shorts local gate | **PASS** |
| CLI end-to-end chain against stub (`live: false`) | **PASS** |
| Live adapter contracts with fake Google modules (auth, scope, upload, resume, refresh, error mapping) | **PASS** |
| Analytics fixture ingest/report/learning and fail-closed live CLI | **PASS** |
| C-02 consistent OAuth scope set | **PASS** (source-verified) |
| Resumable session reuse / publishAt validation | **PASS** (source-verified + fake-module tests) |
| Quota accounting completeness | **FAIL** |
| Real OAuth connect / keyring storage / token refresh against Google | **BLOCKED BY ENVIRONMENT** |
| Real Data API upload, verify, publish, thumbnail against a test channel | **BLOCKED BY ENVIRONMENT** |
| Real YouTube Analytics query with stored token | **BLOCKED BY ENVIRONMENT** |
| Shorts parent public status against remote channel | **NOT APPLICABLE** (local-only state) |
| Skill installer static contract | **PASS** |
| Live Codex / Claude Code / Cursor / Windsurf / `opencode` harness compatibility | **BLOCKED BY ENVIRONMENT** |
| Claim that current tests prove a real YouTube publication | **NOT APPLICABLE** — never claimed |

### Manual reproduction when credentials are available

```bash
python -m pytest tests/youtube/ tests/analytics/ -o addopts="" -q
python -m pytest tests/test_phase8_cli.py tests/test_phase9_cli.py -o addopts="" -q
# Only in a licensed, explicitly authorized test channel:
# creator youtube connect --live --client-secrets CLIENT.json
# creator youtube upload ... --live   # then verify, then publish with fresh approval
```

Live steps are intentionally left as manual instructions: no credentials exist in this environment
and a mocked result must never be recorded as a live PASS.
