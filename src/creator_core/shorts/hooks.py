"""Hook/title generation with a deterministic default and optional text providers.

The core never depends on an LLM: `template` is always available and is used whenever a
configured provider is disabled, misconfigured, unreachable or returns unusable output. A
fallback result names the template as the provider and records why, so no run ever claims that
an unavailable model produced its text.
"""

from __future__ import annotations

import json
import os

from creator_core.media.injection import analyze_untrusted, sanitize_untrusted, wrap_for_prompt
from creator_core.research.provenance import HYPOTHESIS, datum
from creator_core.security.secrets import redact_text

MAX_HOOK_CHARS = 140
DEFAULT_COUNT = 3
_PROVIDER_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "openai_compatible": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "anthropic": "https://api.anthropic.com/v1",
}


def _mmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _clean(text: str, *, truncate: bool = True) -> str:
    value = sanitize_untrusted(str(text)).replace("\r", " ").replace("\n", " ").strip()
    value = " ".join(value.split())
    return value[:MAX_HOOK_CHARS] if truncate else value


def _hook(text: str, provider: str, *, truncated: bool = False) -> dict:
    return datum(
        _clean(text),
        label=HYPOTHESIS,
        source=provider,
        method="generated hook copy",
        retrieved_at=None,
    ) | {"truncated": truncated}


def _template_lines(candidate: dict, profile: dict | None, count: int) -> list[str]:
    signals = {s.get("value") for s in candidate.get("signals", []) if isinstance(s, dict)}
    stamp = _mmss(candidate.get("start", 0.0))
    personality = " ".join(
        str(v).lower() for v in ((profile or {}).get("voice") or {}).get("personality", [])
    )
    if "audio_spike" in signals:
        lines = [
            f"That energy jump at {stamp} is the whole story",
            f"Wait for the audio peak at {stamp}",
            f"The loudest moment at {stamp}, in one take",
        ]
    elif "scene_cut" in signals:
        lines = [
            f"One cut at {stamp} changes the pace completely",
            f"The frame switch at {stamp} is worth a replay",
            f"Three setups, one sharp turn at {stamp}",
        ]
    elif "transcript" in signals:
        lines = [
            f"The line at {stamp} that lands best",
            f"Start at {stamp}: the setup pays off here",
            f"This exchange at {stamp} says a lot in a few seconds",
        ]
    else:
        lines = [
            f"A closer look at the moment around {stamp}",
            f"Short take from {stamp}",
            f"One slice of the original at {stamp}",
        ]
    if "humor" in personality or "funny" in personality:
        lines = [f"Quick one: {lines[0].lower()}", *lines[1:]]
    return lines[:count]


def _fallback(candidate: dict, profile: dict | None, count: int, reason: str) -> dict:
    result = TemplateProvider().generate(candidate, profile, count)
    result["fallback_reason"] = redact_text(reason)[:300]
    return result


class TemplateProvider:
    """Always-available deterministic generator; no network and no model claims."""

    name = "template"

    def available(self) -> bool:
        return True

    def generate(self, candidate: dict, profile: dict | None, count: int = DEFAULT_COUNT) -> dict:
        lines = _template_lines(candidate, profile, max(1, min(5, int(count))))
        hooks = [_hook(line, self.name) for line in lines]
        stamp = _mmss(candidate.get("start", 0.0))
        return {
            "provider": self.name,
            "model": None,
            "available": True,
            "hooks": hooks,
            "title": _hook(f"{lines[0]} #shorts", self.name)["value"][:100],
            "description": _hook(
                f"Drafted from measured signals near {stamp}. Review before publishing. #shorts",
                self.name,
            )["value"],
            "label": HYPOTHESIS,
            "fallback_reason": None,
        }


class _HttpProvider:
    """OpenAI-compatible chat or Anthropic messages API; degrades to templates on failure."""

    api_style = "chat"

    def __init__(self, config: dict, kind: str, *, provider_name: str | None = None):
        self.cfg = config
        self.kind = kind
        self.name = provider_name or kind
        self.model = str(config.get("model") or "")
        self.base_url = str(config.get("base_url") or _PROVIDER_DEFAULTS.get(kind, "")).rstrip("/")
        self.key_env = config.get("api_key_env", "OPENAI_API_KEY" if kind == "openai" else None)
        self.timeout = float(config.get("timeout_s", 20))

    def available(self) -> bool:
        if not self.base_url or not self.model:
            return False
        if self.key_env and not os.environ.get(str(self.key_env)):
            return False
        try:
            import requests  # noqa: F401 - optional dependency probe
        except ImportError:
            return False
        return True

    def generate(self, candidate: dict, profile: dict | None, count: int = DEFAULT_COUNT) -> dict:
        if not self.available():
            return _fallback(
                candidate,
                profile,
                count,
                f"{self.name} unavailable (missing config, key or requests package)",
            )
        try:
            import requests

            lines = self._request(candidate, profile, count, requests)
        except Exception as exc:  # provider failures must degrade, never fabricate success  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
            return _fallback(candidate, profile, count, f"{self.name} request failed: {exc}")
        if not lines:
            return _fallback(candidate, profile, count, f"{self.name} returned no usable hooks")
        hooks = [_hook(line, self.name, truncated=len(line) > MAX_HOOK_CHARS) for line in lines]
        stamp = _mmss(candidate.get("start", 0.0))
        return {
            "provider": self.name,
            "model": self.model,
            "available": True,
            "hooks": hooks,
            "title": _hook(f"{lines[0]} #shorts", self.name)["value"][:100],
            "description": _hook(
                f"Drafted from measured signals near {stamp}. Review before publishing. #shorts",
                self.name,
            )["value"],
            "label": HYPOTHESIS,
            "fallback_reason": None,
        }

    def _request(self, candidate: dict, profile: dict | None, count: int, requests) -> list[str]:
        system = (
            "You write short, honest YouTube Shorts hooks. Never claim guaranteed views or "
            "virality. Reply with a JSON array of plain text lines only."
        )
        evidence = wrap_for_prompt(
            "candidate",
            json.dumps(
                {
                    "start_seconds": candidate.get("start"),
                    "signals": [s.get("value") for s in candidate.get("signals", [])],
                    "reason": candidate.get("reason", ""),
                    "profile_voice": (profile or {}).get("voice", {}),
                },
                ensure_ascii=False,
                default=str,
            ),
        )
        user = f"Write {count} hook lines for this measured candidate:\n{evidence}"
        headers = {"Content-Type": "application/json"}
        if self.key_env:
            headers["Authorization"] = f"Bearer {os.environ[str(self.key_env)]}"
        if self.api_style == "anthropic":
            headers["x-api-key"] = os.environ.get(str(self.key_env), "")
            headers["anthropic-version"] = "2023-06-01"
            url = f"{self.base_url}/messages"
            body = {
                "model": self.model,
                "max_tokens": 256,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        else:
            url = f"{self.base_url}/chat/completions"
            body = {
                "model": self.model,
                "temperature": 0.4,
                "max_tokens": 256,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        response = requests.post(url, json=body, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if self.api_style == "anthropic":
            raw = "".join(
                part.get("text", "")
                for part in payload.get("content", [])
                if isinstance(part, dict)
            )
        else:
            raw = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _parse_lines(raw, count)


class AnthropicProvider(_HttpProvider):
    api_style = "anthropic"


def _parse_lines(raw: str, count: int) -> list[str]:
    text = str(raw or "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            parsed = parsed.get("hooks") or parsed.get("lines") or []
        if isinstance(parsed, list):
            lines = [str(v).strip() for v in parsed if str(v).strip()]
            return [_clean(v, truncate=False) for v in lines[:count] if _clean(v, truncate=False)]
    except (json.JSONDecodeError, TypeError):
        pass
    lines = [line.lstrip("-•0123456789. ").strip() for line in text.splitlines()]
    # Keep the full sanitized line: the caller flags truncation before `_hook` clips it.
    return [_clean(v, truncate=False) for v in lines if _clean(v, truncate=False)][:count]


def get_provider(config: dict | None, *, enabled: bool = True) -> TemplateProvider | _HttpProvider:
    """Select the configured text provider; always fall back to templates safely."""
    cfg = ((config or {}).get("providers") or {}).get("text")
    if not enabled or not cfg or not isinstance(cfg, dict):
        return TemplateProvider()
    kind = str(cfg.get("kind") or "template").lower()
    if kind == "template":
        return TemplateProvider()
    if kind not in _PROVIDER_DEFAULTS:

        class _InvalidProvider(TemplateProvider):
            def generate(self, candidate, profile, count=DEFAULT_COUNT):
                result = super().generate(candidate, profile, count)
                result["fallback_reason"] = f"unknown text provider kind: {kind}"
                return result

        return _InvalidProvider()
    if kind == "anthropic":
        return AnthropicProvider(cfg, kind)
    return _HttpProvider(cfg, kind)


def generate_hooks(
    candidate: dict,
    profile: dict | None,
    config: dict | None,
    *,
    enabled: bool = True,
    count: int = DEFAULT_COUNT,
) -> dict:
    """Generate hooks with the configured provider, transparently falling back to templates."""
    provider = get_provider(config, enabled=enabled)
    if not provider.available():
        return _fallback(candidate, profile, count, f"{provider.name} provider unavailable")
    return provider.generate(candidate, profile, count)


def analyze_hook_text(hook: dict) -> dict:
    """Re-check generated copy before display (defence in depth for optional providers)."""
    return analyze_untrusted(hook.get("value", ""))
