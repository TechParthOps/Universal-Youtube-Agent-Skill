# VoiceDNA Security & Privacy

Scope: `src/creator_core/voice/*`, provider adapters and their CLI/API surfaces. Version 0.10.0a0.

## 1. Sensitive asset model

Reference recordings, voice profiles, consent records, generation history and narration exports are
treated as sensitive personal data. They live only inside the creator's workspace:

```
<workspace>/.creator/voice/<owner_id>/
  profiles/<profile_id>/voice_vNNN.json, voice_latest.json, consent/<profile_id>.json
  references/<profile_id>/reference.<ext>
  generation/<profile_id>/versions/...
  exports/<profile_id>/...
```

- Owner ids are strictly validated; all paths are re-confined with `resolve_within()`.
- The original reference is copied **once**; re-registering identical bytes is hash-verified and
  idempotent. Deleting a profile removes every one of these trees plus its consent record.
- Raw audio never appears in JSON profiles, history entries, audit logs or CLI output — only the
  confined relative path, SHA-256 and derived measurements.
- Deletion is recorded in the append-only audit log with a profile fingerprint, never raw audio.

## 2. Consent as an authorization boundary

`consent.verify_consent()` is invoked inside the synthesis layer, not only by the CLI. It requires:
an existing record, `status == "active"`, matching `owner_id` and `profile_id`, and the exact
requested purpose (`narration`, `shorts`, `dubbing`, `localization`). Revocation takes effect
immediately; deleted consent records cannot be resurrected without a new explicit grant.

Granting consent requires confirmation of the exact ownership/authorisation statement. The grant
and revocation are audit-logged. Complete profile deletion also deletes the consent record.

## 3. No impersonation

VoiceDNA only synthesises for the verified owner of a profile. There is no UI, CLI flag or adapter
for cloning a named third party, uploading someone else's sample under another owner, or applying a
profile across owners. Provider adapters declare `clones_voice`; `system_sapi` explicitly sets it to
`false` and cannot reproduce the creator's voice. Licence-gated adapters refuse commercial use
unless the creator supplies compliant weights and acknowledges the licence.

## 4. External provider permission

Neural adapters are local by default. A hosted provider requires an explicit `allow_external=True`
argument on the generation call; without it, selection fails closed. When allowed, the audit log
records the provider id, profile id and a redacted model identifier — never reference bytes or
credentials. API keys are read from the existing secret store / environment and are redacted by the
shared `security/secrets.py` helpers before any JSON or log emission.

## 5. Untrusted text and media

Scripts, transcripts, captions, video metadata and creator text are treated as untrusted data:
- Sanitised with `media.injection.sanitize_untrusted()` before use in captions, prompts or logs.
- Never executed, evaluated or interpreted as instructions.
- EDL/JSON input is validated against allowlists; generated FFmpeg commands use list arguments via
  `security/subprocess.py` (`shell=False`, timeouts, output caps).
- Reference audio decoding is bounded to 60 seconds at 16 kHz, preventing memory exhaustion from
  hostile long files.

## 6. Published-output disclosure

`disclosure.assess()` records whether the final video contains synthetic voice, which provider
generated it, and whether the creator reviewed the assessment. It never automatically asserts that
disclosure is required or exempt — the creator decides in the existing private-first publish flow.
No publish occurs without the existing HMAC approval token bound to the full canonical request and
consumed once (Stage 12I). Registration and consent grant also require explicit
`--confirm-voice-ownership` against the displayed statement.

## 7. Known limitations

- Local JSON storage is not encrypted at rest. It is owner-scoped and audit-logged, which is
  appropriate for the single-user local tool but not for shared/multi-user hosting. Encrypt before
  deploying in a multi-user environment.
- The speaker-count estimate is heuristic and unverified; the creator must review a sample.
- No neural inference was executed in this environment; provider security is assessed from code and
  licence evidence, not from runtime behaviour.
