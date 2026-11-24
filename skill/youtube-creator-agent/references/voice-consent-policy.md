# Voice Consent Policy — Creator Agent / VoiceDNA

Version: 1.0 (contract 1.0). Effective 2026-11-21.

## 1. Who may register a voice

Only the creator who owns the workspace, or a person holding explicit written authorisation from
the voice owner, may register a reference recording. The registration flow requires confirmation of
this exact statement:

> "I confirm that I own this voice or hold explicit written authorisation to use it, and I
> authorise Creator Agent to generate synthetic narration for the purposes recorded below. I
> understand I may revoke this consent and delete the profile at any time."

A custom statement is accepted only if it retains the first sentence verbatim. Consent is recorded
with a UTC timestamp, owner id, profile id and permitted purposes.

## 2. Purpose scope

Consent is not blanket. Each grant lists one or more purposes:

| Purpose | Covers |
|---|---|
| `narration` | Long-form video narration and scripted voice-over |
| `shorts` | Shorts generated from the same registered voice |
| `dubbing` | Replacing or adding voice in existing footage |
| `localization` | Generating the same creator voice in another supported language |

A generation request for a purpose not in the active grant is refused with `auth_required`
(exit 4). Unknown purposes are impossible — the API rejects them at grant time.

## 3. Owner isolation

A consent record is bound to exactly one `(owner_id, profile_id)` pair. Verification checks both
values. A creator cannot list, load, generate with, or regenerate another creator's profile. With no
owner filter, `list_profiles()` returns redacted summaries only.

## 4. Revocation

`creator voice consent revoke --owner ID --profile ID` immediately sets `status = "revoked"`,
records `revoked_at` and a reason, and audit-logs the event. Revocation is irreversible without a
new explicit grant. All subsequent generation attempts fail before any provider is called.

## 5. Deletion

`creator voice delete --owner ID --profile ID` permanently removes, from local workspace storage:

- every immutable profile version and the latest pointer,
- the consent record,
- the stored reference audio,
- all generation history, segment WAVs and voice exports.

The deletion itself is audit-logged with a profile fingerprint. Third-party/hosted data deletion
requires the creator to exercise the provider's own retention controls; this tool never claims to
have deleted remote copies it does not control.

## 6. Prohibited uses

- Cloning a third party without their explicit, informed, recorded authorisation.
- Impersonation, deception, or presenting synthetic speech as a real person's live statement.
- Applying one creator's profile to another creator's workspace or project.
- Using a licence-gated model for commercial output without a compliant weight licence and
  recorded acknowledgement.
- Sending reference audio to an external provider without `allow_external=True` and an audit entry.

## 7. Creator responsibilities

The creator is responsible for the accuracy of the ownership attestation, for reviewing generated
speech before publication, for deciding whether YouTube synthetic-content disclosure applies, and
for complying with local law and platform policy regarding synthetic voice content.
