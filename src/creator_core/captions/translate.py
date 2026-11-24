"""Optional translation for captions with an honest no-translation default.

The core never depends on a translation model. With no configured provider the result contains
**zero** translated cues and says so; a failed provider degrades to that same honest result with a
redacted `fallback_reason`. File-supplied translations provide a fully offline path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from creator_core.cli.errors import ValidationError
from creator_core.editing.graph import sanitize_untrusted_plain
from creator_core.research.provenance import ESTIMATED
from creator_core.security.secrets import redact_text

_PROVIDER_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "openai_compatible": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "anthropic": "https://api.anthropic.com/v1",
}
_ALLOWED_BASE_URLS = frozenset(_PROVIDER_DEFAULTS.values())


def _validate_base_url(url: str, config: dict) -> str:
    """Reject arbitrary translation endpoints unless explicitly opted in (S-07).

    A hostile or mistaken `base_url` would otherwise send the API key to an untrusted host.
    Custom gateways require `allow_custom_base_url=true` plus https (or localhost http) and no
    embedded credentials.
    """
    url = url.rstrip("/")
    if url in _ALLOWED_BASE_URLS:
        return url
    if not config.get("allow_custom_base_url"):
        raise ValidationError(
            f"unsupported translation base_url {url!r}; use a documented provider endpoint "
            "or explicitly set providers.translation.allow_custom_base_url=true",
        )
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValidationError("custom translation base_url must be an http(s) URL with a host")
    if parts.username or parts.password:
        raise ValidationError("custom translation base_url must not embed credentials")
    if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValidationError("custom translation base_url must use https unless it is localhost")
    if parts.query or parts.fragment:
        raise ValidationError("custom translation base_url must not include a query or fragment")
    return url


class TranslationProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def translate(
        self, cues: list[dict], *, target_language: str, source_language: str | None = None
    ) -> dict: ...


def _empty(reason: str | None = None) -> dict:
    return {
        "provider": "none",
        "model": None,
        "available": False,
        "cues": [],
        "label": None,
        "fallback_reason": redact_text(reason)[:300] if reason else None,
        "note": "No translated captions were produced; source captions are unchanged.",
    }


class NullTranslator:
    """Always available as a *decision*: it deliberately produces no translation."""

    name = "none"

    def available(self) -> bool:
        return False

    def translate(
        self, cues: list[dict], *, target_language: str, source_language: str | None = None
    ) -> dict:
        return _empty()


class FileTranslator:
    """Translate from a creator-supplied JSON file (fully offline, deterministic)."""

    name = "file"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def available(self) -> bool:
        return self.path.is_file()

    def _load(self) -> list[object]:
        if not self.path.is_file():
            raise ValidationError(
                f"translations file not found: {self.path}",
                hint="pass a JSON array of strings or {index, cue_id, text} objects",
            )
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationError(f"translations file is unreadable: {exc}") from exc
        if not isinstance(data, list):
            raise ValidationError("translations file must be a JSON array")
        return data

    def translate(
        self, cues: list[dict], *, target_language: str, source_language: str | None = None
    ) -> dict:
        entries = self._load()
        by_cue_id = {
            str(e.get("cue_id")): e.get("text")
            for e in entries
            if isinstance(e, dict) and e.get("cue_id") is not None
        }
        by_index = {
            int(e["index"]): e.get("text")
            for e in entries
            if isinstance(e, dict) and str(e.get("index", "")).lstrip("-").isdigit()
        }
        out: list[dict] = []
        for i, cue in enumerate(cues):
            value = None
            cue_id = cue.get("cue_id")
            if cue_id and str(cue_id) in by_cue_id:
                value = by_cue_id[str(cue_id)]
            elif i in by_index:
                value = by_index[i]
            elif i < len(entries) and isinstance(entries[i], str):
                value = entries[i]
            text = sanitize_untrusted_plain(str(value or "")).strip()
            if not text:
                continue
            out.append(
                {
                    "start": cue["start"],
                    "end": cue["end"],
                    "text": text,
                    "cue_id": cue.get("cue_id"),
                }
            )
        return {
            "provider": self.name,
            "model": None,
            "available": True,
            "cues": out,
            "label": ESTIMATED,
            "fallback_reason": None,
            "source": f"file:{self.path.name}",
            "unmatched": len(cues) - len(out),
            "target_language": target_language,
            "note": "Translations were supplied by the creator in a local file; review before publishing.",
        }


class HttpTranslator:
    """OpenAI-compatible chat translator; degrades to the honest empty result on any failure."""

    def __init__(self, config: dict, kind: str):
        self.cfg = config
        self.kind = kind
        self.name = str(config.get("name") or kind)
        self.model = str(config.get("model") or "")
        self.base_url = _validate_base_url(
            str(config.get("base_url") or _PROVIDER_DEFAULTS.get(kind, "")), config
        )
        self.key_env = config.get("api_key_env", "OPENAI_API_KEY" if kind == "openai" else None)
        self.timeout = float(config.get("timeout_s", 20))

    def available(self) -> bool:
        api_key = os.environ.get(str(self.key_env), "") if self.key_env else True
        return bool(self.base_url and self.model and api_key)

    def translate(
        self, cues: list[dict], *, target_language: str, source_language: str | None = None
    ) -> dict:
        if not self.available():
            return _empty("translation provider is not configured (model/base_url/api key missing)")
        if not cues:
            return _empty("no source cues to translate")
        numbered = "\n".join(f"{i}: {c['text']}" for i, c in enumerate(cues))
        prompt = (
            f"Translate each numbered caption line into {target_language}."
            f"{' The source language is ' + source_language + '.' if source_language else ''} "
            "Return ONLY a JSON array of strings in the same order and with the same number "
            "of entries. Do not add commentary.\n" + numbered
        )
        try:
            import requests

            headers = {"Content-Type": "application/json"}
            api_key = os.environ.get(str(self.key_env), "") if self.key_env else ""
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - network/SDK failures must never fabricate text
            return _empty(f"translation provider failed: {type(exc).__name__}: {exc}")
        try:
            if "```" in content:
                content = content.split("```", 2)[1].split("\n", 1)[-1].rsplit("```", 1)[0]
            values = json.loads(content.strip())
            if (
                not isinstance(values, list)
                or len(values) != len(cues)
                or not all(isinstance(v, str) and v.strip() for v in values)
            ):
                return _empty("translation provider returned an unusable response")
        except (TypeError, ValueError):
            return _empty("translation provider returned an unusable response")
        out = []
        for cue, value in zip(cues, values):
            text = sanitize_untrusted_plain(value).strip()
            if text:
                out.append(
                    {
                        "start": cue["start"],
                        "end": cue["end"],
                        "text": text,
                        "cue_id": cue.get("cue_id"),
                    }
                )
        if not out:
            return _empty("translation provider returned no usable lines")
        return {
            "provider": self.name,
            "model": self.model,
            "available": True,
            "cues": out,
            "label": ESTIMATED,
            "fallback_reason": None,
            "source": self.name,
            "unmatched": len(cues) - len(out),
            "target_language": target_language,
            "note": "Machine translation is an estimate; a human should review before publishing.",
        }


def get_provider(config: dict | None, *, enabled: bool = True) -> TranslationProvider:
    cfg = ((config or {}).get("providers") or {}).get("translation")
    if not enabled or not cfg or not isinstance(cfg, dict):
        return NullTranslator()
    kind = str(cfg.get("kind") or "").strip().lower()
    if kind == "none":
        return NullTranslator()
    if kind == "file":
        if not cfg.get("path"):
            return NullTranslator()
        return FileTranslator(cfg["path"])
    if kind in _PROVIDER_DEFAULTS:
        return HttpTranslator(cfg, kind)
    return NullTranslator()
