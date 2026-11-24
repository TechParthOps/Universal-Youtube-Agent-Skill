"""Strict, versioned VoiceDNA artifact manifest verification (fail-closed).

A manifest is DATA, not proof. This module independently verifies every required
artifact (existence, SHA-256, size) and its licensing evidence *before* any model
is loaded, and computes commercial clearance itself. A caller-supplied
``commercial_use_verified`` boolean is never accepted as proof. Verification is
always recomputed from disk, so a modified/substituted artifact fails on the next
check — stale verification is never reused.

Commercial generation must fail closed here unless every required artifact is
present with a matching hash/size and verified commercial permission. Non-commercial
use still requires full artifact integrity verification (and the provider enforces
its own explicit acknowledgement + consent).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from creator_core.cli.errors import ValidationError
from creator_core.security.paths import resolve_within

SUPPORTED_SCHEMA_VERSIONS = ("1.0",)
VALID_ROLES = {
    "checkpoint",
    "config",
    "base_speaker",
    "dependency_model",
    "tokenizer",
    "other",
}
VALID_PERMISSIONS = {"allowed", "denied", "unknown"}
VALID_CLEARANCE_STATUS = {"verified", "denied", "unknown"}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

_REQUIRED_ARTIFACT_FIELDS = (
    "id",
    "role",
    "name",
    "source",
    "version",
    "path",
    "sha256",
    "size_bytes",
    "license_spdx",
    "license_evidence",
    "commercial_use",
    "redistribution",
)


@dataclass
class ArtifactResult:
    id: str
    role: str = ""
    path: str = ""
    ok: bool = False
    exists: bool = False
    hash_ok: bool = False
    size_ok: bool = False
    license_evidence_ok: bool = False
    expected_sha256: str = ""
    actual_sha256: str = ""
    expected_size: int | None = None
    actual_size: int | None = None
    license_id: str = ""
    commercial_use: str = "unknown"
    redistribution: str = "unknown"
    errors: list[str] = field(default_factory=list)


@dataclass
class ManifestVerification:
    ok: bool = False
    schema_version: str = ""
    provider: str = ""
    integrity_ok: bool = False
    commercial_ok: bool = False
    artifacts: list[ArtifactResult] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    missing_base_speaker: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_root: str = ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_contained(resolved: PurePath, root: PurePath) -> bool:
    """Platform-independent containment: component-based, never a string prefix.

    ``Path.is_relative_to`` compares path components (so ``/models-safe`` is NOT
    inside ``/models``, and different Windows drives never match) and works for
    both POSIX and Windows semantics. Accepts pure paths so the predicate can be
    exercised with either path flavour on any host platform. Requires Python
    >= 3.9; this project requires >= 3.11.
    """
    return resolved.is_relative_to(root)


def _resolve_artifact_path(model_root: Path, rel: str) -> tuple[Path | None, str]:
    """Resolve an artifact path strictly within model_root.

    Rejects absolute paths, ``..`` traversal, and symlink escapes. Containment is
    checked on fully resolved (symlink-followed) paths using platform-independent
    component comparison, so valid artifacts verify on both Windows and POSIX.
    Returns (resolved_path, error). error is "" on success.
    """
    raw = str(rel or "")
    if not raw:
        return None, "empty artifact path"
    if Path(raw).is_absolute() or raw.startswith(("/", "\\")):
        return None, f"artifact path must be relative, got absolute: {raw!r}"
    parts = Path(raw).parts
    if ".." in parts:
        return None, f"artifact path traversal rejected: {raw!r}"
    try:
        resolved = resolve_within(model_root, raw)
    except ValidationError as exc:
        return None, f"artifact path escapes model root ({raw!r}): {exc.message}"
    root_real = Path(model_root).resolve()
    resolved_real = Path(resolved).resolve()
    if not _is_contained(resolved_real, root_real):
        return None, f"artifact path escapes model root via symlink: {raw!r}"
    return Path(resolved), ""


def _check_license_evidence(model_root: Path, evidence: str) -> tuple[bool, str]:
    """License evidence is required and resolvable. A URL is an accepted reference;
    a local path must exist within the model root. Returns (ok, error)."""
    ref = str(evidence or "").strip()
    if not ref:
        return False, "missing license evidence reference"
    if _URL_RE.match(ref):
        return True, ""
    path, err = _resolve_artifact_path(model_root, ref)
    if err:
        return False, f"license evidence not resolvable: {err}"
    if path is None or not path.is_file():
        return False, f"license evidence file missing: {ref!r}"
    return True, ""


def verify_artifact(
    entry: dict,
    *,
    model_root: Path,
    artifact_ids: set[str] | None = None,
    base_speaker_ids: set[str] | None = None,
) -> ArtifactResult:
    """Verify a single required artifact: fields, path confinement, existence,
    SHA-256, size, license evidence, enum validity, and dependency/base-speaker
    references. Fresh on every call (never cached)."""
    res = ArtifactResult(id=str(entry.get("id") or ""))
    missing_fields = [f for f in _REQUIRED_ARTIFACT_FIELDS if f not in entry]
    if missing_fields:
        res.errors.append(f"missing required fields: {missing_fields}")
        return res
    res.role = str(entry.get("role"))
    res.license_id = str(entry.get("license_spdx") or "")
    res.commercial_use = str(entry.get("commercial_use") or "unknown")
    res.redistribution = str(entry.get("redistribution") or "unknown")
    if res.role not in VALID_ROLES:
        res.errors.append(f"unknown artifact role: {res.role!r}")
    if res.commercial_use not in VALID_PERMISSIONS:
        res.errors.append(f"unknown commercial_use: {res.commercial_use!r}")
    if res.redistribution not in VALID_PERMISSIONS:
        res.errors.append(f"unknown redistribution: {res.redistribution!r}")
    if not res.id:
        res.errors.append("empty artifact id")

    expected_sha = str(entry.get("sha256") or "")
    if not _SHA256_RE.match(expected_sha):
        res.errors.append("sha256 must be 64 hex characters")
    else:
        res.expected_sha256 = expected_sha.lower()

    expected_size = entry.get("size_bytes")
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
        res.errors.append("size_bytes must be a non-negative integer")
        expected_size = None
    res.expected_size = expected_size

    # Attribution requirement consistency.
    if (
        bool(entry.get("attribution_required"))
        and not str(entry.get("attribution_text") or "").strip()
    ):
        res.errors.append("attribution_required is true but attribution_text is empty")

    # Path confinement + existence + hash + size.
    resolved, err = _resolve_artifact_path(model_root, str(entry.get("path") or ""))
    if err:
        res.errors.append(err)
        return res
    if resolved is None:  # defensive: _resolve_artifact_path only returns None on error
        res.errors.append("artifact path could not be resolved")
        return res
    res.path = str(resolved)
    if not resolved.is_file():
        res.exists = False
        res.errors.append(f"artifact file missing: {res.path}")
    else:
        res.exists = True
        res.actual_size = resolved.stat().st_size
        if expected_size is not None and res.actual_size != expected_size:
            res.size_ok = False
            res.errors.append(
                f"size mismatch: expected {expected_size} bytes, got {res.actual_size}"
            )
        else:
            res.size_ok = expected_size is None or res.actual_size == expected_size
        try:
            res.actual_sha256 = sha256_file(resolved)
        except OSError as exc:
            res.errors.append(f"could not hash artifact: {exc}")
        if res.actual_sha256 and res.expected_sha256:
            res.hash_ok = res.actual_sha256 == res.expected_sha256
            if not res.hash_ok:
                res.errors.append(
                    f"sha256 mismatch for {entry.get('id')}: expected {res.expected_sha256[:12]}…, "
                    f"got {res.actual_sha256[:12]}…"
                )

    evidence_ok, ev_err = _check_license_evidence(
        model_root, str(entry.get("license_evidence") or "")
    )
    res.license_evidence_ok = evidence_ok
    if ev_err:
        res.errors.append(ev_err)

    # Dependency + base-speaker references.
    if artifact_ids is not None:
        for dep in entry.get("dependencies", []) or []:
            if str(dep) not in artifact_ids:
                res.errors.append(f"missing dependency artifact: {dep!r}")
        if bool(entry.get("requires_base_speaker")) and not (base_speaker_ids or set()):
            res.errors.append("requires_base_speaker=true but no base_speaker artifact in manifest")

    res.ok = (
        res.exists and res.hash_ok and res.size_ok and res.license_evidence_ok and not res.errors
    )
    return res


def verify_manifest(
    data: object, *, model_root: Path, provider: str = "openvoice_v2"
) -> ManifestVerification:
    """Validate a manifest document and verify every required artifact.

    Computes commercial clearance itself (never trusting caller booleans). Always
    reads bytes from disk, so re-calling after any change reflects current state.
    """
    ver = ManifestVerification(model_root=str(model_root))
    if not isinstance(data, dict):
        ver.errors.append("manifest is not a JSON object")
        return ver

    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        ver.errors.append(
            f"unsupported/unknown schema_version: {schema_version!r} "
            f"(supported: {', '.join(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    ver.schema_version = str(schema_version)

    if str(data.get("provider") or "") != provider:
        ver.errors.append(f"provider must be {provider!r}, got {data.get('provider')!r}")
    ver.provider = str(data.get("provider") or "")

    artifacts_raw = data.get("artifacts")
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        ver.errors.append("artifacts must be a non-empty array")
        ver.errors.append("fail-closed: no artifacts to verify")
        ver.integrity_ok = False
        ver.commercial_ok = False
        return ver

    # Duplicate id check.
    seen: set[str] = set()
    for entry in artifacts_raw:
        if not isinstance(entry, dict):
            ver.errors.append("artifact entry is not an object")
            continue
        aid = str(entry.get("id") or "")
        if aid:
            if aid in seen:
                ver.errors.append(f"duplicate artifact id: {aid}")
            seen.add(aid)
    artifact_ids = set(seen)
    base_speaker_ids = {
        str(e.get("id"))
        for e in artifacts_raw
        if isinstance(e, dict) and e.get("role") == "base_speaker"
    }

    results: list[ArtifactResult] = []
    for entry in artifacts_raw:
        if not isinstance(entry, dict):
            continue
        res = verify_artifact(
            entry,
            model_root=model_root,
            artifact_ids=artifact_ids,
            base_speaker_ids=base_speaker_ids,
        )
        results.append(res)
        ver.errors.extend(f"[{res.id or '?'}] {e}" for e in res.errors)
    ver.artifacts = results

    ver.integrity_ok = bool(results) and all(r.ok for r in results)

    # Compute commercial clearance independently of any caller boolean.
    all_allowed = bool(results) and all(r.commercial_use == "allowed" for r in results)
    clear_ok, clear_err = _check_commercial_clearance(data, all_allowed)
    ver.commercial_ok = bool(ver.integrity_ok and all_allowed and clear_ok)
    ver.errors.extend(clear_err)

    ver.ok = ver.integrity_ok and not ver.errors
    return ver


def _check_commercial_clearance(data: dict, all_artifacts_allowed: bool) -> tuple[bool, list[str]]:
    errs: list[str] = []
    cc = data.get("commercial_clearance")
    if not isinstance(cc, dict):
        return False, ["commercial_clearance object is required"]
    status = str(cc.get("status") or "")
    if status not in VALID_CLEARANCE_STATUS:
        errs.append(f"unknown commercial_clearance.status: {status!r}")
    if status != "verified":
        errs.append(
            f"commercial_clearance.status must be 'verified' for commercial use (got {status!r})"
        )
    if not all_artifacts_allowed:
        errs.append("every required artifact must have commercial_use=allowed for commercial use")
    # Evidence array is required even though individual artifact evidence is checked too.
    if not isinstance(data.get("evidence"), list) or not data.get("evidence"):
        errs.append("manifest evidence array is required and must be non-empty")
    return (not errs), errs


def load_manifest_file(path: Path) -> dict:
    """Read and parse a manifest JSON file; returns {} on failure with stderr-safe errors."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
