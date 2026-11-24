"""ContentPlanner (#9): schedule ideas on preferred weekdays/hours using profile settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class ContentPlanner:
    def plan(
        self, ideas: list[dict], profile: dict, *, limit: int = 5, start: datetime | None = None
    ) -> dict:
        publishing = profile.get("publishing", {})
        allowed_days = {str(d).lower() for d in publishing.get("preferred_days", [])} or set(
            _DAY_NAMES
        )
        hours = publishing.get("preferred_hours_utc") or [16]
        cursor = start or datetime.now(UTC)
        cursor = cursor.replace(minute=0, second=0, microsecond=0)
        entries = []
        for idea in ideas[:limit]:
            # Search at most one week for a matching day/hour, then start the next
            # idea on the following day so two ideas never share the same slot.
            for _ in range(7 * 24):
                if _DAY_NAMES[cursor.weekday()] in allowed_days and cursor.hour in hours:
                    break
                cursor += timedelta(hours=1)
            else:
                raise ValueError("publishing preferences contain no matching day/hour")
            entries.append(
                {
                    "idea_id": idea["idea_id"],
                    "title": idea.get("suggested_title", {}).get("value", ""),
                    "proposed_publish_utc": cursor.isoformat(),
                    "estimated_production_minutes": idea.get(
                        "estimated_production_time_minutes", 60
                    ),
                    "opportunity_score": idea.get("opportunity", {}).get("score"),
                    "status": "proposed",
                }
            )
            cursor = cursor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return {
            "module": "ContentPlanner",
            "timezone": publishing.get("timezone", "UTC"),
            "entries": entries,
            "note": "Proposed slots use creator preferences, not measured audience activity. "
            "Analytics reports video performance but not audience-active-time; these "
            "slots remain proposals.",
        }
