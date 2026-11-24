#!/usr/bin/env python3
"""Build the local release artifact set with one authoritative SHA-256 manifest.

    python scripts/pack_release.py [--out dist] [--json]

Produces, from the current intended release source (no network, nothing uploaded):

  1. Wheel + sdist via ``python -m build``              -> ``dist/*.whl`` / ``dist/*.tar.gz``
  2. Clean source ZIP from a filesystem-verified inventory -> ``dist/clean-source/*.zip``
  3. A single authoritative checksum manifest           -> ``dist/SHA256SUMS.txt``
  4. Removes the stale duplicate ``dist/SHA256SUMS_RELEASE.txt`` if present.

The clean source ZIP inventory is the git-tracked files that exist in the working
tree UNION every ``.py`` discovered on disk under ``src/``, ``scripts/``, ``tests/``
UNION required runtime-referenced documents (``_REQUIRED_DOCS``), minus a defensive
denylist. Scanning the working tree (not git alone) means required-but-untracked
source can never silently disappear from the release ZIP; the packer fails closed
if any required source file or required document is missing from the archive.
This already excludes ``.git/``, caches, ``dist/``, ``build/`` and any untracked
local checkpoints/credentials. Every packaged file is scanned for committed
secrets and machine-specific absolute paths before the manifest is written; a
finding aborts the packaging.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from creator_core.release.build import build_artifacts

VERSION = "0.10.0a0"
PROJECT = "universal-creator-agent"

# Defensive denylist: never ship these even if somehow git-tracked.
_DENY_NAME = re.compile(
    r"(^|/)(tests|\.git|\.mypy_cache|\.pytest_cache|\.ruff_cache|__pycache__|build|dist)(/|$)"
    r"|\.(pyc|pyo|pyd|db|sqlite3?|coverage)$"
    r"|(^|/)\.env($|\.)|(^|/)(id_rsa|id_ed25519|credentials\.json|client_secret\.json)$"
    r"|\.(safetensors|ckpt|pt|pth)$",
    re.IGNORECASE,
)

# Secret markers. A hit in a shipped (non-test) file is FATAL and aborts packaging;
# the same shapes in tests/ are the project's own redaction fixtures -> warning.
_HIGH_CONFIDENCE = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS secret key", re.compile(r"aws_secret_access_key\s*[:=]", re.IGNORECASE)),
    ("Google OAuth token", re.compile(r"ya29\.[0-9A-Za-z\-_]{20,}")),
]
_LOW_CONFIDENCE = [
    (
        "key/secret assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"][^'\"\s]{8,}"
        ),
    ),
]

# Text-like extensions we scan for secrets/absolute paths.
_TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".yml",
    ".yaml",
    ".html",
    ".js",
    ".css",
    ".sh",
    ".in",
}


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git ls-files failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


_SOURCE_ROOTS = ("src", "scripts")

# Required non-.py documents: either referenced at runtime (provider error hints,
# pyproject comments) or referenced by the shipped README/alpha release notes.
# They must ship in the clean-source ZIP even while untracked; the packer fails
# closed if any is missing from disk or from the archive.
_REQUIRED_DOCS = (
    "VOICE_ARTIFACT_MANIFEST_SCHEMA.md",
    "THIRD-PARTY-NOTICES.md",
    "ALPHA_RELEASE_NOTES.md",
)


def _all_source_py() -> list[str]:
    """Every .py actually present under the source roots (posix relpaths).

    This deliberately scans the WORKING TREE, not git, so required source that is
    untracked (e.g. ``src/creator_core/voice/aligners/``) is never silently omitted.
    """
    found: list[str] = []
    for root in _SOURCE_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if _DENY_NAME.search(rel):
                continue
            found.append(rel)
    return found


def _build_inventory() -> dict:
    """Assemble the release inventory = tracked files (present on disk) UNION all
    .py source found on disk UNION required runtime-referenced documents, minus
    the denylist.

    Returns the (relpath, abspath) items to package plus diagnostics used to fail
    closed: which untracked source was pulled in, whether any required source file
    or required document is missing from the package, and which tracked files are
    missing from disk.
    """
    tracked = _tracked_files()
    tracked_set = {t.replace("\\", "/") for t in tracked}
    source_py = set(_all_source_py())
    candidates = tracked_set | source_py | set(_REQUIRED_DOCS)
    items: list[tuple[str, Path]] = []
    denied: list[str] = []
    for rel in sorted(candidates):
        if _DENY_NAME.search(rel):
            denied.append(rel)
            continue
        abspath = REPO_ROOT / rel
        if abspath.is_file():
            items.append((rel, abspath))
    packaged = {rel for rel, _ in items}
    return {
        "items": items,
        "denied": denied,
        "all_source_py": sorted(source_py),
        "untracked_src_included": sorted(source_py - tracked_set),
        "missing_required": sorted(source_py - packaged),
        "missing_required_docs": sorted(r for r in _REQUIRED_DOCS if r not in packaged),
        "tracked_missing_on_disk": sorted(r for r in tracked_set if not (REPO_ROOT / r).is_file()),
    }


def _is_test(rel: str) -> bool:
    return rel.replace("\\", "/").startswith("tests/")


def _scan_for_secrets(items: list[tuple[str, Path]]) -> tuple[list[str], list[str]]:
    """Return (fatal, warnings).

    Machine-specific absolute paths and secret-shaped content in shipped files
    are FATAL. The same shapes under tests/ are the project's redaction fixtures
    and are reported as warnings only.
    """
    # Matches a Windows user-home absolute path without embedding the literal search
    # string in this file (which would make the packer flag itself).
    abs_home_re = re.compile(r"[a-z]:[\\/](?:users|documents and settings)[\\/]", re.IGNORECASE)
    repo_literal = str(REPO_ROOT).lower()
    fatal: list[str] = []
    warnings: list[str] = []

    def report(rel: str, reason: str) -> None:
        (warnings if _is_test(rel) else fatal).append(f"{rel}: {reason}")

    for rel, abspath in items:
        if abspath.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = abspath.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        low = text.lower()
        if repo_literal in low:
            fatal.append(f"{rel}: machine-specific path ({repo_literal!r})")
        elif abs_home_re.search(low):
            fatal.append(f"{rel}: machine-specific user-home path")
        for label, pattern in _HIGH_CONFIDENCE + _LOW_CONFIDENCE:
            if pattern.search(text):
                report(rel, f"possible {label}")
    return fatal, warnings


def _write_clean_zip(items: list[tuple[str, Path]], out_dir: Path) -> Path:
    zip_dir = out_dir / "clean-source"
    zip_dir.mkdir(parents=True, exist_ok=True)
    for stale in zip_dir.glob("*.zip"):
        stale.unlink()
    archive = zip_dir / f"{PROJECT}-{VERSION}-clean-source.zip"
    top = f"{PROJECT}-{VERSION}-source/"
    # Deterministic: fixed timestamp, fixed order, sorted entries.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, abspath in items:
            info = zipfile.ZipInfo(top + rel.replace("\\", "/"), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, abspath.read_bytes())
    return archive


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(out_dir: Path, artifacts: list[Path], manifest: Path) -> bool:
    """Write the SINGLE authoritative manifest, then remove the stale duplicate.

    Returns True if a duplicate ``SHA256SUMS_RELEASE.txt`` was present and removed.
    """
    lines = [f"{_sha256(p)}  {p.name}" for p in sorted(artifacts, key=lambda x: x.name)]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dup = out_dir / "SHA256SUMS_RELEASE.txt"
    if dup.exists():
        dup.unlink()
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist", help="output directory inside the checkout")
    parser.add_argument("--json", action="store_true", help="emit a JSON result")
    args = parser.parse_args(argv)

    # 1) Wheel + sdist (real python -m build, no upload). Also seeds dist/SHA256SUMS.txt.
    build_artifacts(REPO_ROOT, args.out)
    out_dir = (REPO_ROOT / args.out).resolve()

    # 2) Clean source ZIP from the explicit release inventory (tracked files
    #    present on disk UNION every .py on disk UNION required runtime-referenced
    #    documents, minus the denylist), so untracked-but-required source is never
    #    silently omitted.
    inv = _build_inventory()
    items, denied = inv["items"], inv["denied"]
    all_source_py = inv["all_source_py"]
    fatal, warnings = _scan_for_secrets(items)
    archive = _write_clean_zip(items, out_dir)

    # Verify archive entries round-trip, contain no absolute/denied paths, and
    # actually contain every required .py source file on disk plus every required
    # runtime-referenced document (fail closed if not).
    top = f"{PROJECT}-{VERSION}-source/"
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        name_set = set(names)
        bad = [n for n in names if n.startswith("/") or ".." in n.split("/")]
        denied_in_zip = [n for n in names if _DENY_NAME.search(n.replace("\\", "/"))]
        missing_in_zip = [rel for rel in all_source_py if top + rel not in name_set]
        missing_docs_in_zip = [rel for rel in _REQUIRED_DOCS if top + rel not in name_set]
    inventory_gaps = list(
        dict.fromkeys(
            inv["missing_required"]
            + missing_in_zip
            + inv["missing_required_docs"]
            + missing_docs_in_zip
        )
    )

    # 3) Single authoritative manifest over wheel + sdist + clean-source zip.
    release_set = sorted(set(out_dir.glob("*.whl")) | set(out_dir.glob("*.tar.gz")))
    release_set.append(archive)
    manifest = out_dir / "SHA256SUMS.txt"
    duplicate_removed = _write_manifest(out_dir, release_set, manifest)

    result = {
        "ok": True,
        "out": str(out_dir),
        "version": VERSION,
        "clean_source_zip": {
            "path": str(archive),
            "files": len(items),
            "bytes": archive.stat().st_size,
            "entries": len(names),
            "denied_excluded": denied,
        },
        "artifacts": [
            {"name": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in release_set
        ],
        "manifest": str(manifest),
        "duplicate_removed": duplicate_removed,
        "inventory": {
            "all_source_py": len(all_source_py),
            "untracked_src_included": inv["untracked_src_included"],
            "tracked_missing_on_disk": inv["tracked_missing_on_disk"],
            "missing_from_zip": missing_in_zip,
            "required_docs": list(_REQUIRED_DOCS),
            "missing_required_docs": inv["missing_required_docs"],
            "missing_docs_in_zip": missing_docs_in_zip,
            "missing_required": inventory_gaps,
        },
        "security_scan": {
            "fatal": fatal,
            "warnings": warnings,
            "bad_entries": bad,
            "denied_in_zip": denied_in_zip,
        },
        "note": "Local artifacts only. Nothing was uploaded or published.",
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Packed {len(release_set)} release artifact(s) in {out_dir}")
        for item in result["artifacts"]:
            print(f"  {item['name']}  {item['bytes']}B  sha256={item['sha256'][:16]}...")
        inv_info = result["inventory"]
        print(
            f"  clean-source.zip: {result['clean_source_zip']['files']} files "
            f"({len(denied)} denied excluded); {inv_info['all_source_py']} source .py, "
            f"{len(inv_info['untracked_src_included'])} untracked-source included"
        )
        for rel in inv_info["untracked_src_included"]:
            print(f"    untracked-source required: {rel}")
        if warnings:
            print(
                f"  security scan: {len(warnings)} warning(s) (test fixtures), {len(fatal)} fatal"
            )
        print(f"  manifest: {manifest} (single authoritative; duplicate removed)")
        print("  " + result["note"])
    if fatal or bad or denied_in_zip or inventory_gaps:
        print("\nPackaging aborted: release set failed verification.", file=sys.stderr)
        for f in fatal + bad + denied_in_zip + inventory_gaps:
            print(f"  FATAL {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
