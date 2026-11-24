"""Version-consistency and packaged-file checks for a source checkout.

A release is only coherent when every declared version source agrees and every file promised by
`MANIFEST.in` actually exists. This module is pure/read-only; it never edits or publishes.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# (label, repo-relative path, regex with one capture group for the version)
VERSION_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("pyproject", "pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"'),
    ("core", "src/creator_core/__init__.py", r'(?m)^__version__\s*=\s*"([^"]+)"'),
    ("skill", "skill/youtube-creator-agent/SKILL.md", r'(?m)^  version:\s*"([^"]+)"'),
    ("changelog", "CHANGELOG.md", r"(?m)^## \[(\d[^\]]*)\]"),
)

# Files that must exist in a source checkout and be present in an sdist (MANIFEST.in).
REQUIRED_FILES: tuple[str, ...] = (
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "ARCHITECTURE.md",
    "DEVELOPMENT_ROADMAP.md",
    "TECHNICAL_DECISIONS.md",
    "MANIFEST.in",
    "pyproject.toml",
    "skill/youtube-creator-agent/SKILL.md",
    "skill/youtube-creator-agent/references/agent-install.md",
    "skill/youtube-creator-agent/references/cli-reference.md",
    "skill/youtube-creator-agent/references/workflow-analytics.md",
    "skill/youtube-creator-agent/references/workflow-edit-and-revise.md",
    "skill/youtube-creator-agent/references/workflow-publish.md",
    "skill/youtube-creator-agent/references/workflow-shorts.md",
    "skill/youtube-creator-agent/scripts/install.py",
    "web/preview/index.html",
    "web/preview/app.js",
    "web/preview/style.css",
    "examples/analytics_fixture.json",
    "THIRD-PARTY-NOTICES.md",
    "ALPHA_RELEASE_NOTES.md",
    "VOICE_ARTIFACT_MANIFEST_SCHEMA.md",
    "VOICE_MODEL_EVALUATION.md",
)


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def read_versions(root: Path) -> list[dict]:
    """Read every declared version source; a missing/unreadable source is reported honestly."""
    entries = []
    for label, relative, pattern in VERSION_SOURCES:
        try:
            match = re.search(pattern, _read(root, relative))
            value = match.group(1) if match else None
            error = None if value else "version declaration not found"
        except (OSError, UnicodeError) as exc:
            value, error = None, f"unreadable: {type(exc).__name__}"
        entries.append({"label": label, "file": relative, "version": value, "error": error})
    return entries


def check_versions(root: Path) -> dict:
    entries = read_versions(root)
    authoritative = next((e["version"] for e in entries if e["label"] == "pyproject"), None)
    mismatches = [e["file"] for e in entries if e["version"] != authoritative]
    return {
        "ok": authoritative is not None and not mismatches,
        "version": authoritative,
        "sources": entries,
        "mismatches": mismatches,
        "note": (
            "All declared version sources agree."
            if not mismatches
            else "Version sources disagree; bump every declared location together."
        ),
    }


def check_files(root: Path) -> dict:
    """Verify every required packaged file exists in the checkout (and is a file)."""
    missing = [p for p in REQUIRED_FILES if not (root / p).is_file()]
    return {
        "ok": not missing,
        "checked": len(REQUIRED_FILES),
        "missing": missing,
        "note": (
            "All declared source files are present."
            if not missing
            else "Missing packaged files must be added to MANIFEST.in and the checkout."
        ),
    }


def file_manifest(root: Path) -> dict:
    """Hash every required packaged file so a build can be verified against source."""
    files = []
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        files.append(
            {
                "path": relative.replace("\\", "/"),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    return {
        "ok": len(files) == len(REQUIRED_FILES),
        "count": len(files),
        "files": files,
        "note": "SHA-256 hashes of required source files; no artifacts were built or uploaded.",
    }


def check_artifacts(root: Path) -> dict:
    """Compare the shipped wheel's source files against the current checkout (HIGH audit fix).

    Release metadata can be internally consistent while the built wheel is stale. This check
    detects byte-level drift between `dist/*.whl` and `src/creator_core/**/*.py`.
    """
    import zipfile

    wheels = sorted((root / "dist").glob("universal_creator_agent-*.whl"))
    if not wheels:
        return {
            "ok": False,
            "wheel": None,
            "checked": 0,
            "mismatches": [],
            "note": "no wheel found in dist/; build one before verification",
        }
    wheel = wheels[-1]
    src_root = root / "src"
    mismatches: list[dict] = []
    checked = 0
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            for path in sorted((src_root / "creator_core").rglob("*.py")):
                rel = path.relative_to(src_root).as_posix()
                checked += 1
                if rel not in names:
                    mismatches.append({"path": rel, "reason": "missing from wheel"})
                elif archive.read(rel) != path.read_bytes():
                    mismatches.append({"path": rel, "reason": "content differs from source"})
    except (OSError, zipfile.BadZipFile) as exc:
        return {
            "ok": False,
            "wheel": wheel.name,
            "checked": checked,
            "mismatches": [],
            "note": f"wheel could not be inspected: {type(exc).__name__}: {exc}",
        }
    return {
        "ok": not mismatches,
        "wheel": wheel.name,
        "checked": checked,
        "mismatches": mismatches[:50],
        "mismatch_count": len(mismatches),
        "note": (
            "Wheel source matches the current checkout."
            if not mismatches
            else "Wheel is stale: rebuild it from the current tree."
        ),
    }


def check(root: Path) -> dict:
    versions = check_versions(root)
    files = check_files(root)
    manifest_text = ""
    try:
        manifest_text = _read(root, "MANIFEST.in")
    except (OSError, UnicodeError):
        pass
    manifest_ok = all(
        token in manifest_text for token in ("skill", "web", "examples", "docs", "global-exclude")
    )
    artifacts = check_artifacts(root)
    gate_ok = versions["ok"] and files["ok"] and manifest_ok and artifacts["ok"]
    return {
        "command": "release check",
        "ok": gate_ok,
        "version": versions["version"],
        "versions": versions,
        "files": files,
        "manifest": {
            "ok": manifest_ok,
            "note": "MANIFEST.in must include skill, web, examples, docs and exclude caches.",
        },
        "artifacts": artifacts,
        "note": (
            "Release sources and shipped wheel are consistent (no upload performed)."
            if gate_ok
            else "Release checks failed; see details before building."
        ),
    }
