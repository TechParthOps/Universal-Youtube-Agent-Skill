# VOICE_ARTIFACT_MANIFEST_SCHEMA

## Status
**DRAFT/NOT VERIFIED IN REAL RUNTIME.** Schema is documented for fail-closed validation; no verified production manifest or real OpenVoice neural inference was executed in this environment.

## Purpose
Define the artifact-level provenance and licensing manifest required before `openvoice_v2` may be treated as commercially clear or used for real neural inference.

## File location (expected)
- Workspace/configured path: `models/openvoice/manifest.json` (or owner-configured path; exact path must be validated by CLI/config).
- Example template: `models/openvoice/manifest.example.json` (to be provided by owner/runtime setup; not shipped as verified).

## Required top-level fields
| Field | Type | Required | Validation |
|---|---|---:|---|
| `schema_version` | string | yes | Must be `"1.0"` (or documented supported version). |
| `provider` | string | yes | Must be `"openvoice_v2"`. |
| `generated_at` | ISO-8601 UTC string | yes | Parseable timestamp. |
| `verified_by` | string | yes | Human/org identity responsible for verification. |
| `verification_status` | enum | yes | `verified` or `unverified`; commercial use requires `verified`. |
| `artifacts` | array | yes | Non-empty; each required runtime artifact listed. |
| `commercial_clearance` | object | yes | Explicit artifact-level commercial permission summary. |
| `evidence` | array | yes | URLs/files/notes supporting license/provenance claims. |

## Required artifact entry fields
| Field | Type | Required | Validation |
|---|---|---:|---|
| `id` | string | yes | Stable identifier/role, e.g. `openvoice_converter`, `melotts_base_en`, `base_speaker_map`. |
| `role` | string | yes | `checkpoint`, `config`, `base_speaker`, `dependency_model`, `tokenizer`, `other`. |
| `name` | string | yes | Human-readable name. |
| `source` | string | yes | Upstream repo/download/source URL or provenance record. |
| `version` | string | yes | Upstream version/tag/commit/date. |
| `path` | string | yes | Relative to manifest or configured model root; resolved safely within allowed root. |
| `sha256` | hex string | yes | Lowercase/uppercase hex; 64 chars; must match file bytes before load. |
| `size_bytes` | integer | yes | Must match file size. |
| `license_spdx` | string | yes | SPDX identifier or `UNKNOWN`; `UNKNOWN` blocks commercial use. |
| `license_evidence` | string | yes | Path/URL/reference to license text or permission evidence. |
| `commercial_use` | enum | yes | `allowed`, `denied`, `unknown`. Commercial generation requires `allowed` for every required artifact. |
| `redistribution` | enum | yes | `allowed`, `denied`, `unknown`. |
| `attribution_required` | boolean | yes | If true, attribution text/reference required. |
| `attribution_text` | string | conditional | Required when attribution_required=true. |
| `notes` | string | no | Additional constraints. |

## Required commercial_clearance fields
| Field | Type | Required | Validation |
|---|---|---:|---|
| `status` | enum | yes | `verified`, `denied`, `unknown`. |
| `scope` | string | yes | e.g. `non-commercial-only`, `commercial-allowed`, `unknown`. |
| `required_artifacts_verified` | boolean | yes | Must be true only when every required artifact hash/license/provenance verified. |
| `restrictions` | array[string] | yes | Explicit restrictions; empty only when fully cleared. |
| `acknowledgement_required_for_unverified_noncommercial` | boolean | yes | Must be true. |

## Validation rules
1. **Fail closed:** missing manifest, schema errors, missing fields, unknown enums, hash mismatch, missing files, or unknown/denied commercial clearance block the affected mode.
2. **No self-trust:** a user-supplied manifest is data, not proof. `verification_status=verified` requires documented evidence references; loader must still verify hashes and required fields.
3. **Separate code vs weights:** repository code license does not imply checkpoint/base-speaker/dataset commercial rights.
4. **Path confinement:** artifact paths must resolve within configured model root; reject traversal/symlink escape.
5. **No implicit download:** loader never downloads missing artifacts.
6. **Commercial mode:** requires `commercial_clearance.status=verified`, `required_artifacts_verified=true`, and every required artifact `commercial_use=allowed` with verified hash.
7. **Non-commercial unverified mode:** may only run with explicit owner acknowledgement and never bypasses consent, hash validation, or path safety checks.

## Current implementation status (schema 1.0 - implemented and tested)
- **New module `src/creator_core/voice/artifact_manifest.py`** enforces this schema strictly and fail-closed at the artifact level:
  - Validates top-level fields and `artifacts[]`; rejects non-object manifests, unsupported/missing `schema_version`, wrong `provider`, and empty artifact arrays.
  - Per artifact: required fields, valid `role`/`commercial_use`/`redistribution` enums, 64-hex `sha256`, non-negative integer `size_bytes`, attribution consistency.
  - Computes ACTUAL SHA-256 and file size from disk and compares them to the manifest; rejects hash/size mismatch and missing files.
  - Verifies `license_evidence` is present and resolvable within the model root (URL references accepted; local evidence files must exist).
  - Rejects duplicate artifact ids, missing `dependencies` targets, and `requires_base_speaker=true` when no `base_speaker` artifact is present.
  - Path confinement: rejects absolute paths, `..` traversal, and symlink escapes from the configured model root.
  - Computes commercial clearance itself (never trusts a caller boolean): commercial requires full artifact integrity AND every required artifact `commercial_use=allowed` AND `commercial_clearance.status=verified`.
  - Verification is always recomputed from disk; a modified/substituted artifact fails on re-verification (no stale verification reuse).
- **Wiring:** `src/creator_core/voice/providers/openvoice.py` uses this validator; commercial generation fails closed unless clearance is verified; non-commercial use still requires full artifact integrity plus explicit acknowledgement; artifacts are re-verified immediately before `torch.load` (the loader also prefers `weights_only=True`).
- **Tests:** `tests/voice/test_artifact_manifest.py` (21 tests + 1 environment-skipped symlink test) cover valid/invalid manifests, wrong hash/size, missing/modified artifacts, false commercial permission, missing license evidence, missing base-speaker/dependency, duplicate ids, path traversal, absolute/symlink paths, manifest replacement, commercial-vs-non-commercial.
- **Real neural inference (V-01): BLOCKED.** The verifier and fail-closed gates are tested, but no real OpenVoice inference has run (no torch/openvoice runtime, no owner-verified checkpoints, no authorized voice sample).
- The old weak check that trusted `commercial_use_verified` + a nonempty `checkpoint_license` string has been removed.
