"""Language metadata and provenance-aware detection for captions.

This module does not guess demographics. A declared profile language is reported as `declared`;
an ASR-returned language is an `estimated` model output. Either may be missing, in which case the
result is explicit rather than silently defaulting to English.
"""

from __future__ import annotations

from creator_core.research.provenance import ESTIMATED

# A deliberately small, explicit catalog. Unknown codes are never silently treated as known.
LANGUAGES: dict[str, dict] = {
    "en": {"name": "English", "rtl": False},
    "es": {"name": "Spanish", "rtl": False},
    "fr": {"name": "French", "rtl": False},
    "de": {"name": "German", "rtl": False},
    "it": {"name": "Italian", "rtl": False},
    "pt": {"name": "Portuguese", "rtl": False},
    "nl": {"name": "Dutch", "rtl": False},
    "ru": {"name": "Russian", "rtl": False},
    "uk": {"name": "Ukrainian", "rtl": False},
    "ar": {"name": "Arabic", "rtl": True},
    "he": {"name": "Hebrew", "rtl": True},
    "hi": {"name": "Hindi", "rtl": False},
    "ja": {"name": "Japanese", "rtl": False},
    "ko": {"name": "Korean", "rtl": False},
    "zh": {"name": "Chinese", "rtl": False},
}


def normalize_language(value: object) -> str | None:
    """Normalize `en-US`, `EN_us` or `  fr ` to a known base code; otherwise None."""
    if not isinstance(value, str):
        return None
    code = value.strip().lower().replace("_", "-").split("-", 1)[0]
    return code if code in LANGUAGES else None


def language_info(code: str | None) -> dict:
    """Return language metadata, explicitly flagging unknown or absent codes."""
    normalized = normalize_language(code)
    if normalized is None:
        return {"code": normalized, "name": None, "rtl": False, "known": False}
    info = LANGUAGES[normalized]
    return {"code": normalized, "name": info["name"], "rtl": info["rtl"], "known": True}


def list_languages() -> list[dict]:
    return [{"code": code, **info} for code, info in sorted(LANGUAGES.items())]


def detect_language(profile: dict | None = None, transcript: dict | None = None) -> dict:
    """Pick the best available language signal without inventing one.

    Precedence: ASR-reported language (an `estimated` model output) > creator-declared profile
    language (`declared`). A conflicting declared language is retained in `declared` so the
    creator can see the disagreement.
    """
    declared = normalize_language(((profile or {}).get("audience") or {}).get("language"))
    asr = None
    if isinstance(transcript, dict):
        asr = normalize_language(transcript.get("language"))
    code = asr or declared
    info = language_info(code)
    if asr:
        source, label = "asr", ESTIMATED
        note = "Language reported by the ASR model; verify before publishing captions."
    elif declared:
        source, label = "creator.profile.audience.language", "declared"
        note = "Creator-declared language, not a measured property of the audience."
    else:
        source, label = "none", "none"
        note = "No language signal available; pass --lang explicitly."
    return {
        "code": code,
        "name": info["name"],
        "rtl": info["rtl"],
        "known": info["known"],
        "source": source,
        "label": label,
        "declared": declared,
        "asr_detected": asr,
        "note": note,
    }
