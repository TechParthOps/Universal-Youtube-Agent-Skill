"""Script drafting from ideas + creator voice (template-based, no provider needed)."""

from __future__ import annotations

from creator_core.research.provenance import HYPOTHESIS, datum


def draft_script(idea: dict, profile: dict) -> dict:
    voice = profile.get("voice", {})
    style = voice.get("speaking_style") or "conversational"
    structure = idea.get("storytelling_structure") or ["Hook", "Context", "Build-up", "Payoff"]
    hook = idea.get("opening_hook", {}).get("value") or idea.get("concept", {}).get("value", "")
    title = idea.get("suggested_title", {}).get("value", "")
    phrases = list(voice.get("signature_phrases") or [])

    sections = []
    for i, beat in enumerate(structure):
        if i == 0:
            line = hook
        elif beat.lower() in ("context", "prior knowledge", "setup", "objective"):
            line = (
                f"Set up the context in {style} terms. What does the viewer need to know "
                f"before '{title}' pays off? Keep it under 30 seconds."
            )
        elif beat.lower() in ("build-up", "argument", "concept", "event", "investigation"):
            line = (
                "Present the main evidence or action; include one concrete moment from the footage."
            )
        elif beat.lower() in ("escalation", "counterpoint", "example", "revelation", "demo"):
            line = "Raise the stakes or show the decisive example. This is the retention-critical beat."
        elif beat.lower() in ("climax", "payoff", "takeaway", "recap", "reflection"):
            line = "Deliver the result, then a short takeaway that invites one specific comment."
        else:
            line = f"[{beat}] — write in a {style} voice."
        sections.append(
            {
                "beat": beat,
                "draft_line": datum(
                    line,
                    label=HYPOTHESIS,
                    source="script template",
                    method="beat-aware deterministic template",
                ),
                "fill_from_footage": i > 0,
            }
        )

    return {
        "idea_id": idea.get("idea_id"),
        "title": title,
        "voice_profile": {"style": style, "signature_phrases": phrases},
        "sections": sections,
        "note": "Template draft with explicit [fill_from_footage] cues; not a finished script. "
        "Review and edit before recording/publishing.",
    }
